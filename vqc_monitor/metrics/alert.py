from vqc_monitor.db import repo
from vqc_monitor.core.config import settings
import math
from typing import Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import text
from vqc_monitor.metrics.system import _root_disk_usage

# === CONFIG ===
WINDOW_MS = settings.ALERT_WINDOW_MS          # đảm bảo đúng 5' => 300000 nếu bạn muốn 5 phút thật
COOLDOWN_MS = settings.ALERT_COOLDOWN_MS
CPU_ALERT_THRESHOLD = settings.CPU_THRESHOLD 
MEMORY_ALERT_THRESHOLD = settings.MEMORY_THRESHOLD
COVERAGE = getattr(settings, "ALERT_COVERAGE", 0.8)  # cho phép override qua config, mặc định 0.8


# =========================
#   THRESHOLDS (unchanged)
# =========================
def get_apps_thresholds() -> dict[str, dict[str, float]]:
    thresholds = {}
    for app_id, app_info in settings.APPS.items():
        thresholds[app_id] = {
            "cpu": (app_info.cpu_threshold),
            "memory": (app_info.memory_threshold_mb),
        }
    thresholds["__system__"] = {"cpu": CPU_ALERT_THRESHOLD, "memory": MEMORY_ALERT_THRESHOLD}
    return thresholds

def get_container_thresholds() -> dict[str, dict[str, float]]:
    thresholds = {}
    for container_name, container_info in settings.CONTAINERS.items():
        thresholds[container_name] = {
            "cpu": (container_info.cpu_threshold),
            "memory": (container_info.memory_threshold_mb),
        }
    return thresholds


# =========================
#   TIME/UNIT HELPERS
# =========================
def _infer_ts_unit(db: Session, table: str, id_col: str, id_val: str) -> str:
    """
    Suy luận đơn vị ts_ms trong DB: 'ms' nếu giá trị lớn ~1e12, 's' nếu ~1e9.
    """
    row = db.execute(
        text(f"SELECT MAX(ts_ms) FROM {table} WHERE {id_col} = :id"),
        {"id": id_val},
    ).fetchone()
    max_ts = row[0] if row else None
    if not max_ts:
        return "ms"  # không có dữ liệu, giữ ms theo tên cột
    return "s" if max_ts < 1_000_000_000_000 else "ms"

def _norm_window(since_ms: int, now_ms: int, unit: str) -> Tuple[int, int]:
    if unit == "ms":
        return since_ms, now_ms
    # unit == 's'
    return since_ms // 1000, now_ms // 1000


# =========================
#   COVERAGE by OBSERVED
# =========================
def _compute_min_samples(observed_first: Optional[int], observed_last: Optional[int],
                         observed_n: int, window_ms: int,
                         fallback_interval_ms: int, coverage: float) -> int:
    """
    Tính min_samples dựa trên cadence quan sát được trong chính cửa sổ query.
    - Nếu có >=2 mẫu: interval_est = (last-first) / (n-1)
    - Ngược lại: dùng fallback từ settings.SAMPLE_INTERVAL_MS
    """
    if observed_n and observed_n >= 2 and observed_first is not None and observed_last is not None:
        span = max(1, observed_last - observed_first)
        interval_est = max(1.0, span / (observed_n - 1))
    else:
        interval_est = max(1.0, float(fallback_interval_ms))
    expected = float(window_ms) / interval_est
    return max(1, math.floor(expected * coverage))


# =========================
#   APP COVERAGE + QUERIES
# =========================
def _enough_coverage(db: Session, app_id: str, since_ms: int, now_ms: int,
                     include_current_sample: bool = True) -> bool:
    """
    Kiểm tra coverage trong cửa sổ [since, now] (unit-aware).
    - include_current_sample=True: cộng bù 1 mẫu (trường hợp check xảy ra trước khi insert).
    """
    # unit = _infer_ts_unit(db, "samples", "app_id", app_id)
    since, now = _norm_window(since_ms, now_ms, "ms")

    row = db.execute(
        text("""
            SELECT COUNT(*) AS n, MIN(ts_ms) AS first_ts, MAX(ts_ms) AS last_ts
            FROM samples
            WHERE app_id = :app AND ts_ms BETWEEN :since AND :now
        """),
        {"app": app_id, "since": since, "now": now},
    ).fetchone()

    n = int(row[0] or 0)
    first_ts = row[1]
    last_ts = row[2]

    # min_samples dựa trên quan sát + fallback settings.SAMPLE_INTERVAL_MS
    min_samples = _compute_min_samples(
        first_ts, last_ts, n, WINDOW_MS, settings.SAMPLE_INTERVAL_MS, COVERAGE
    )

    n_effective = n + (1 if include_current_sample else 0)
    # Debug (tùy): print(f"[app cov] app={app_id} unit={unit} n={n} eff={n_effective} min={min_samples} span={last_ts and first_ts and (last_ts-first_ts)}")
    return n_effective >= min_samples


def _no_sample_below_or_equal(db: Session, app_id: str, since_ms: int, now_ms: int,
                              metric: str, threshold: float) -> bool:
    # unit = _infer_ts_unit(db, "samples", "app_id", app_id)
    since, now = _norm_window(since_ms, now_ms, "ms")

    if metric == "cpu":
        row = db.execute(
            text("""
                SELECT COUNT(*) FROM samples
                WHERE app_id = :app AND ts_ms BETWEEN :since AND :now
                  AND cpu_percent <= :thr
            """),
            {"app": app_id, "since": since, "now": now, "thr": threshold},
        ).fetchone()
    else:
        row = db.execute(
            text("""
                SELECT COUNT(*) FROM samples
                WHERE app_id = :app AND ts_ms BETWEEN :since AND :now
                  AND mem_bytes <= :thr
            """),
            {"app": app_id, "since": since, "now": now, "thr": threshold},
        ).fetchone()
    return (row[0] or 0) == 0


def _passed_cooldown(db: Session, app_id: str, metric: str, now_ms: int) -> bool:
    row = db.execute(text("""
        SELECT ts_ms FROM alerts
        WHERE app_id = :app AND alert_type = :m
        ORDER BY ts_ms DESC LIMIT 1
    """), {"app": app_id, "m": metric}).fetchone()
    if not row:
        return True
    last_ts = row[0]
    return (now_ms - last_ts) >= COOLDOWN_MS


def monitor_alerts_db_backed(db, app_id: str, ts_ms: int, cpu_usage: float, mem_usage: float):
    th = get_apps_thresholds().get(app_id)
    mem_usage_mb = mem_usage / (1024 * 1024)

    if app_id == "__system__":
        th = {
            "cpu": CPU_ALERT_THRESHOLD,
            # memory threshold của system là % RAM tổng → chuyển sang MB
            "memory": MEMORY_ALERT_THRESHOLD * (settings.TOTAL_RAM_BYTES / (1024 * 1024)) / 100.0
        }
    if not th:
        return

    since_ms = ts_ms - WINDOW_MS

    # ---- CPU ----
    if cpu_usage > th["cpu"]:
        if _enough_coverage(db, app_id, since_ms, ts_ms, include_current_sample=True) \
           and _no_sample_below_or_equal(db, app_id, since_ms, ts_ms, "cpu", th["cpu"]) \
           and _passed_cooldown(db, app_id, "cpu", ts_ms):
            repo.save_alert(db, app_id, "cpu", ts_ms, cpu_usage)

    # ---- Memory ----
    if mem_usage_mb > th["memory"]:
        if _enough_coverage(db, app_id, since_ms, ts_ms, include_current_sample=True) \
           and _no_sample_below_or_equal(db, app_id, since_ms, ts_ms, "memory", th["memory"]) \
           and _passed_cooldown(db, app_id, "memory", ts_ms):
            repo.save_alert(db, app_id, "memory", ts_ms, mem_usage_mb)

    # ---- Disk ----   
    disk_usage_pct = _root_disk_usage()[2]
    DISK_ALERT_THRESHOLD = settings.DISK_THRESHOLD
    if disk_usage_pct > DISK_ALERT_THRESHOLD:
        if _passed_cooldown(db, "__system__", "disk", ts_ms):
            repo.save_alert(db, "__system__", "disk", ts_ms, disk_usage_pct)



# =========================
#   CONTAINER COVERAGE
# =========================
def _container_enough_coverage(db: Session, container_name: str, since_ms: int, now_ms: int,
                               include_current_sample: bool = True) -> bool:
    # unit = _infer_ts_unit(db, "container_metrics", "container_name", container_name)
    since, now = _norm_window(since_ms, now_ms, "ms")

    row = db.execute(
        text("""
            SELECT COUNT(*) AS n, MIN(ts_ms) AS first_ts, MAX(ts_ms) AS last_ts
            FROM container_metrics
            WHERE container_name = :container AND ts_ms BETWEEN :since AND :now
        """),
        {"container": container_name, "since": since, "now": now},
    ).fetchone()

    n = int(row[0] or 0)
    first_ts = row[1]
    last_ts = row[2]

    min_samples = _compute_min_samples(
        first_ts, last_ts, n, WINDOW_MS, settings.SAMPLE_INTERVAL_MS, COVERAGE
    )
    n_effective = n + (1 if include_current_sample else 0)
    # Debug (tùy): print(f"[ctr cov] name={container_name} unit={unit} n={n} eff={n_effective} min={min_samples}")
    return n_effective >= min_samples


def _container_no_sample_below_or_equal(db: Session, container_name: str, since_ms: int, now_ms: int,
                                        metric: str, threshold: float) -> bool:
    # unit = _infer_ts_unit(db, "container_metrics", "container_name", container_name)
    since, now = _norm_window(since_ms, now_ms, "ms")

    if metric == "cpu":
        row = db.execute(
            text("""
                SELECT COUNT(*) FROM container_metrics
                WHERE container_name = :container AND ts_ms BETWEEN :since AND :now
                  AND cpu_percent <= :thr
            """),
            {"container": container_name, "since": since, "now": now, "thr": threshold},
        ).fetchone()
    else:
        row = db.execute(
            text("""
                SELECT COUNT(*) FROM container_metrics
                WHERE container_name = :container AND ts_ms BETWEEN :since AND :now
                  AND mem_bytes <= :thr
            """),
            {"container": container_name, "since": since, "now": now, "thr": threshold},
        ).fetchone()
    return (row[0] or 0) == 0


def _container_passed_cooldown(db: Session, container_name: str, metric: str, now_ms: int) -> bool:
    row = db.execute(text("""
        SELECT ts_ms FROM container_alerts
        WHERE container_name = :container AND alert_type = :m
        ORDER BY ts_ms DESC LIMIT 1
    """), {"container": container_name, "m": metric}).fetchone()
    if not row:
        return True
    last_ts = row[0]
    return (now_ms - last_ts) >= COOLDOWN_MS


def monitor_container_alerts_db_backed(db, container_name: str, ts_ms: int, cpu_usage: float, mem_usage: float):
    th = get_container_thresholds().get(container_name)
    mem_usage_mb = mem_usage / (1024 * 1024)
    if not th:
        return

    since_ms = ts_ms - WINDOW_MS

    # ---- CPU ----
    if cpu_usage > th["cpu"]:
        if _container_enough_coverage(db, container_name, since_ms, ts_ms, include_current_sample=True) \
           and _container_no_sample_below_or_equal(db, container_name, since_ms, ts_ms, "cpu", th["cpu"]) \
           and _container_passed_cooldown(db, container_name, "cpu", ts_ms):
            repo.save_container_alert(db, container_name, "cpu", ts_ms, cpu_usage)

    # ---- Memory ----
    if mem_usage_mb > th["memory"]:
        if _container_enough_coverage(db, container_name, since_ms, ts_ms, include_current_sample=True) \
           and _container_no_sample_below_or_equal(db, container_name, since_ms, ts_ms, "memory", th["memory"]) \
           and _container_passed_cooldown(db, container_name, "memory", ts_ms):
            repo.save_container_alert(db, container_name, "memory", ts_ms, mem_usage_mb)
    