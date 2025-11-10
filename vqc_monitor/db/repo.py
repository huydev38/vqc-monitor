from math import ceil
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import select, text
from vqc_monitor.db.models import Container, ContainerAlert, ContainerStateTimeline, Sample
from vqc_monitor.db.base import SessionLocal
from vqc_monitor.core.config import ContainerInfo, reload_list_services
from vqc_monitor.db.models import App
from vqc_monitor.core.config import AppInfo
from vqc_monitor.db.models import Alert, StateTimeline
from vqc_monitor.metrics.alert import monitor_alerts_db_backed, monitor_container_alerts_db_backed
from datetime import datetime
from vqc_monitor.db.models import ContainerMetric

def ensure_system_app(db: Session):
    row = db.get(App, "__system__")
    if not row:
        db.add(App(id="__system__", name="System", cgroup_path="__system__", version=0))
        db.flush()

def upsert_apps(db: Session, items: dict[str, AppInfo]):
    ensure_system_app(db)
    for app_id, app_info in items.items():
        row = db.get(App, app_id)
        if row:
            row.cgroup_path = app_info.cgroup
        else:
            db.add(App(id=app_id, name=app_id.capitalize(), cgroup_path=app_info.cgroup, version=app_info.version))



def insert_sample(db: Session, app_id: str, ts_ms: int, cpu: float, mem: int, r: float, w: float):
    s = Sample(app_id=app_id, ts_ms=ts_ms, cpu_percent=cpu, mem_bytes=mem, io_read_Bps=r, io_write_Bps=w)
    db.merge(s)  # upsert theo (app_id, ts_ms)
    db.flush()
    monitor_alerts_db_backed(db, app_id, ts_ms, cpu, mem)


def list_apps():
    return reload_list_services()

def get_stats(db: Session, app_id: str, ts_from: int, ts_to: int, max_points: int = 1000, bucket_ms: Optional[int] = 5000):

    # Tính bucket_ms nếu không truyền
    if bucket_ms is None:
        bucket_ms = max(1, ceil((ts_to - ts_from) / max(1, min(max_points, 1000))))

    sql = text("""
      SELECT
        ((ts_ms / :bucket_ms) * :bucket_ms) AS t,
        AVG(cpu_percent) AS cpu_avg,
        MIN(cpu_percent) AS cpu_min,
        MAX(cpu_percent) AS cpu_max,
        AVG(mem_bytes)   AS mem_avg,
        MIN(mem_bytes)   AS mem_min,
        MAX(mem_bytes)   AS mem_max,
        AVG(io_read_Bps)  AS io_r_avg,
        AVG(io_write_Bps) AS io_w_avg
      FROM samples
      WHERE app_id = :app_id
        AND ts_ms BETWEEN :start AND :end
      GROUP BY t
      ORDER BY t ASC
    """)
    rows = db.execute(sql, {
        "bucket_ms": bucket_ms,
        "app_id": app_id,
        "start": ts_from,
        "end": ts_to
    }).mappings().all()

    return {
        "app_id": app_id,
        "start": ts_from,
        "end": ts_to,
        "bucket_ms": bucket_ms,
        "points": [
            {
                "t": int(r["t"]),
                "cpu_avg": float(r["cpu_avg"]) if r["cpu_avg"] is not None else None,
                "cpu_min": float(r["cpu_min"]) if r["cpu_min"] is not None else None,
                "cpu_max": float(r["cpu_max"]) if r["cpu_max"] is not None else None,
                "mem_avg": int(r["mem_avg"]) if r["mem_avg"] is not None else None,
                "mem_min": int(r["mem_min"]) if r["mem_min"] is not None else None,
                "mem_max": int(r["mem_max"]) if r["mem_max"] is not None else None,
                "io_r_avg": float(r["io_r_avg"]) if r["io_r_avg"] is not None else None,
                "io_w_avg": float(r["io_w_avg"]) if r["io_w_avg"] is not None else None,
            } for r in rows
        ]
    }


def save_alert(db: Session, app_id: str, alert_type: str, ts_ms: int, value: float):
    alert = Alert(app_id=app_id, alert_type=alert_type, ts_ms=ts_ms, value=value)
    db.add(alert)
    db.flush()

def get_alerts(db: Session, limit: int, app_id: Optional[str] = None):
    stmt = None
    if app_id:
        stmt = (select(Alert)
            .where(Alert.app_id == app_id)
            .order_by(Alert.ts_ms.desc())
            .limit(limit))
    else:
        stmt = (select(Alert)
            .order_by(Alert.ts_ms.desc())
            .limit(limit))

    return db.scalars(stmt).all()

def open_or_close_state_timeline(db: Session, app_id: str, state: str):
    ts_ms = datetime.now().timestamp() * 1000
    # Kiểm tra trạng thái hiện tại
    stmt = (select(StateTimeline)
            .where(StateTimeline.app_id == app_id)
            .order_by(StateTimeline.start_time.desc())
            .limit(1))
    current = db.scalars(stmt).first()

    if current and current.state == state and current.end_time is None:
        # Đang ở trạng thái này, không làm gì
        return
    else:
        # Đóng trạng thái cũ nếu cần
        if current and current.end_time is None:
            current.end_time = ts_ms
            db.flush()
        # Mở trạng thái mới
        new_timeline = StateTimeline(app_id=app_id, state=state, start_time=ts_ms, end_time=None)
        db.add(new_timeline)
        db.flush()

def update_state_timeline_end(db: Session, app_id: str, end_time: int):
    # Cập nhật end_time của trạng thái hiện tại
    stmt = (select(StateTimeline)
            .where(StateTimeline.app_id == app_id)
            .order_by(StateTimeline.start_time.desc())
            .limit(1))
    current = db.scalars(stmt).first()

    if current and current.end_time is None:
        print(f" Cập nhật end_time cho {app_id} thành {end_time}")
        current.end_time = end_time
        db.flush()


def get_state_timelines(db: Session, app_id: str, ts_from: int, ts_to: int):
    stmt = (select(StateTimeline)
            .where(StateTimeline.app_id == app_id,
                   StateTimeline.start_time <= ts_to,
                   (StateTimeline.end_time == None) | (StateTimeline.end_time >= ts_from))
            .order_by(StateTimeline.start_time.asc()))
    return db.scalars(stmt).all()

def get_last_state(db: Session, app_id: str) -> Optional[StateTimeline]:
    stmt = (select(StateTimeline)
            .where(StateTimeline.app_id == app_id)
            .order_by(StateTimeline.start_time.desc())
            .limit(1))
    return db.scalars(stmt).first()

def upsert_containers(db: Session, items: dict[str, ContainerInfo]):
    for container_name, ctr_info in items.items():
        row = db.get(Container, container_name)
        if row:
            row.image = ctr_info.image
            row.version = ctr_info.version
        else:
            db.add(Container(name=container_name, image=ctr_info.image, version=ctr_info.version))

def insert_container_sample(db: Session, container_name: str, ts_ms: int, cpu: float, mem: int):
    s = ContainerMetric(container_name=container_name, ts_ms=ts_ms, cpu_percent=cpu, mem_bytes=mem)
    db.merge(s)  # upsert theo (container_name, ts_ms)
    db.flush()
    monitor_container_alerts_db_backed(db, container_name, ts_ms, cpu, mem)


def get_container_stats(db: Session, container_name: str, ts_from: int, ts_to: int, max_points: int = 1000, bucket_ms: Optional[int] = 5000):

    # Tính bucket_ms nếu không truyền
    if bucket_ms is None:
        bucket_ms = max(1, ceil((ts_to - ts_from) / max(1, min(max_points, 1000))))

    sql = text("""
      SELECT
        ((ts_ms / :bucket_ms) * :bucket_ms) AS t,
        AVG(cpu_percent) AS cpu_avg,
        MIN(cpu_percent) AS cpu_min,
        MAX(cpu_percent) AS cpu_max,
        AVG(mem_bytes)   AS mem_avg,
        MIN(mem_bytes)   AS mem_min,
        MAX(mem_bytes)   AS mem_max
      FROM container_metrics
      WHERE container_name = :container_name
        AND ts_ms BETWEEN :start AND :end
      GROUP BY t
      ORDER BY t ASC
    """)
    rows = db.execute(sql, {
        "bucket_ms": bucket_ms,
        "container_name": container_name,
        "start": ts_from,
        "end": ts_to
    }).mappings().all()

    return {
        "container_name": container_name,
        "start": ts_from,
        "end": ts_to,
        "bucket_ms": bucket_ms,
        "points": [
            {
                "t": int(r["t"]),
                "cpu_avg": float(r["cpu_avg"]) if r["cpu_avg"] is not None else None,
                "cpu_min": float(r["cpu_min"]) if r["cpu_min"] is not None else None,
                "cpu_max": float(r["cpu_max"]) if r["cpu_max"] is not None else None,
                "mem_avg": int(r["mem_avg"]) if r["mem_avg"] is not None else None,
                "mem_min": int(r["mem_min"]) if r["mem_min"] is not None else None,
                "mem_max": int(r["mem_max"]) if r["mem_max"] is not None else None,
            } for r in rows
        ]
    }


def save_container_alert(db: Session, container_name: str, alert_type: str, ts_ms: int, value: float):
    alert = ContainerAlert(container_name=container_name, alert_type=alert_type, ts_ms=ts_ms, value=value)
    db.add(alert)
    db.flush()

def get_container_alerts(db: Session, limit: int, container_name: Optional[str] = None):
    stmt = None
    if container_name:
        stmt = (select(ContainerAlert)
            .where(ContainerAlert.container_name == container_name)
            .order_by(ContainerAlert.ts_ms.desc())
            .limit(limit))
    else:
        stmt = (select(ContainerAlert)
            .order_by(ContainerAlert.ts_ms.desc())
            .limit(limit))

    return db.scalars(stmt).all()

##CONTAINER STATE TIMELINE

def open_or_close_state_timeline_container(db: Session, container_name: str, state: str):
    ts_ms = datetime.now().timestamp() * 1000
    # Kiểm tra trạng thái hiện tại
    stmt = (select(ContainerStateTimeline)
            .where(ContainerStateTimeline.container_name == container_name)
            .order_by(ContainerStateTimeline.start_time.desc())
            .limit(1))
    current = db.scalars(stmt).first()

    if current and current.state == state and current.end_time is None:
        # Đang ở trạng thái này, không làm gì
        return
    else:
        # Đóng trạng thái cũ nếu cần
        if current and current.end_time is None:
            current.end_time = ts_ms
            db.flush()
        # Mở trạng thái mới
        new_timeline = ContainerStateTimeline(container_name=container_name, state=state, start_time=ts_ms, end_time=None)
        db.add(new_timeline)
        db.flush()

def update_state_timeline_end_container(db: Session, container_name: str, end_time: int):
    # Cập nhật end_time của trạng thái hiện tại
    stmt = (select(ContainerStateTimeline)
            .where(ContainerStateTimeline.container_name == container_name)
            .order_by(ContainerStateTimeline.start_time.desc())
            .limit(1))
    current = db.scalars(stmt).first()

    if current and current.end_time is None:
        print(f" Cập nhật end_time cho {container_name} thành {end_time}")
        current.end_time = end_time
        db.flush()


def get_state_timelines_container(db: Session, container_name: str, ts_from: int, ts_to: int):
    stmt = (select(ContainerStateTimeline)
            .where(ContainerStateTimeline.container_name == container_name,
                   ContainerStateTimeline.start_time <= ts_to,
                   (ContainerStateTimeline.end_time == None) | (ContainerStateTimeline.end_time >= ts_from))
            .order_by(ContainerStateTimeline.start_time.asc()))
    return db.scalars(stmt).all()

def get_last_state_container(db: Session, container_name: str) -> Optional[ContainerStateTimeline]:
    stmt = (select(ContainerStateTimeline)
            .where(ContainerStateTimeline.container_name == container_name)
            .order_by(ContainerStateTimeline.start_time.desc())
            .limit(1))
    return db.scalars(stmt).first()

def clean_old_records(db: Session, retention_days: int):
    cutoff_ts = int((datetime.now().timestamp() - retention_days * 86400) * 1000)

    # Xoá samples cũ
    db.execute(
        text("DELETE FROM samples WHERE ts_ms < :cutoff_ts"),
        {"cutoff_ts": cutoff_ts}
    )

    # Xoá alerts cũ
    db.execute(
        text("DELETE FROM alerts WHERE ts_ms < :cutoff_ts"),
        {"cutoff_ts": cutoff_ts}
    )

    # Xoá state timelines cũ
    db.execute(
        text("DELETE FROM state_timelines WHERE end_time IS NOT NULL AND end_time < :cutoff_ts"),
        {"cutoff_ts": cutoff_ts}
    )

    # Xoá container_metrics cũ
    db.execute(
        text("DELETE FROM container_metrics WHERE ts_ms < :cutoff_ts"),
        {"cutoff_ts": cutoff_ts}
    )

    # Xoá container_alerts cũ
    db.execute(
        text("DELETE FROM container_alerts WHERE ts_ms < :cutoff_ts"),
        {"cutoff_ts": cutoff_ts}
    )

    # Xoá container_state_timelines cũ
    db.execute(
        text("DELETE FROM container_state_timelines WHERE end_time IS NOT NULL AND end_time < :cutoff_ts"),
        {"cutoff_ts": cutoff_ts}
    )

    db.commit()