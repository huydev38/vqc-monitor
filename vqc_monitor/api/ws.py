# app/api/ws.py
import json, time, asyncio
from typing import List, Dict, Tuple, Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from vqc_monitor.core.logs import _validate_service, LogHub
from vqc_monitor.metrics.cgroup import snapshot as cg_snapshot, compute_rates as cg_rates
from vqc_monitor.metrics.system import snapshot as sys_snapshot, compute_rates as sys_rates
from vqc_monitor.core.config import resolve_service_to_cgroup, settings, list_services
from vqc_monitor.metrics.cgroup import get_service_uptime
from vqc_monitor.metrics.collector import get_metrics_from_containers

TAIL_DEFAULT = 200
hub = LogHub()

router = APIRouter()

@router.websocket("/ws/live")
async def ws_live(
    ws: WebSocket,
    mode: str = Query("service", pattern="^(service|system|combined)$"),
    app_id: str | None = Query(None, description="Khi mode=service"),
    services: str | None = Query(None, description="CSV app_ids; nếu bỏ trống sẽ lấy tất cả trackable"),
    interval_ms: int = Query(1000, ge=50, le=60000),
):
    """
    - mode=service  : số liệu realtime cho 1 service (giữ nguyên hành vi cũ)
    - mode=system   : số liệu realtime cho system (giữ nguyên hành vi cũ)
    - mode=combined : gộp system + nhiều services trong 1 payload
      + query ?services=nginx,postgres  (CSV); nếu None: lấy all trackable từ settings.APPS
    """
    await ws.accept()

    if mode == "service":
        cgroup_path = resolve_service_to_cgroup(app_id) if app_id else None
        if not app_id or not cgroup_path:
            await ws.close(code=1002)
            return
        prev = cg_snapshot(cgroup_path)
        t0 = time.time()
        try:
            while True:
                await asyncio.sleep(max(0.05, interval_ms / 1000))
                curr = cg_snapshot(cgroup_path)
                t1 = time.time()
                rates = cg_rates(prev, curr, max(1e-6, t1 - t0))
                rates["ts_ms"] = int(t1 * 1000)
                rates["app_id"] = app_id
                await ws.send_text(json.dumps(rates))
                prev, t0 = curr, t1
        except WebSocketDisconnect:
            return

    # elif mode == "system":
    #     prev = sys_snapshot()
    #     t0 = time.time()
    #     try:
    #         while True:
    #             await asyncio.sleep(max(0.05, interval_ms / 1000))
    #             curr = sys_snapshot()
    #             t1 = time.time()
    #             rates = sys_rates(prev, curr, max(1e-6, t1 - t0))
    #             # đính kèm disk usage tức thời (đã thêm trong system.snapshot())
    #             rates["disk_used_bytes"]   = curr.get("disk_used_bytes")
    #             rates["disk_total_bytes"]  = curr.get("disk_total_bytes")
    #             rates["disk_used_percent"] = curr.get("disk_used_percent")
    #             rates["ts_ms"] = int(t1 * 1000)
    #             rates["app_id"] = "__system__"
    #             await ws.send_text(json.dumps(rates))
    #             prev, t0 = curr, t1
    #     except WebSocketDisconnect:
    #         return

    # mode == "combined" Chi dung loai nay
        # Chuẩn bị danh sách service theo query hoặc tất cả trackable
    if services:
        req_ids = [s.strip() for s in services.split(",") if s.strip()]
    else:
        # chọn tất cả service trackable từ config
        req_ids = [sid for sid, info in settings.APPS.items() if getattr(info, "trackable", True)]

    # Tạo map app_id -> cgroup_path (lọc ra cái hợp lệ)
    svc_map: Dict[str, str] = {}
    for sid in req_ids:
        cg = resolve_service_to_cgroup(sid)
        if cg:
            svc_map[sid] = cg

    # prev snapshots per service trong vòng đời kết nối
    prev_svc: Dict[str, Tuple[dict, float]] = {}
    # prev system
    prev_sys = sys_snapshot()
    t0_sys = time.time()

    try:
        while True:
            await asyncio.sleep(max(0.05, interval_ms / 1000))
            now = time.time()

            # --- system ---
            curr_sys = sys_snapshot()
            sys_rates_now = sys_rates(prev_sys, curr_sys, max(1e-6, now - t0_sys))
            sys_payload = {
                "cpu_percent": sys_rates_now["cpu_percent"],
                "mem_bytes":   sys_rates_now["mem_bytes"],
                "read_Bps":    sys_rates_now["read_Bps"],
                "write_Bps":   sys_rates_now["write_Bps"],
                "net_rx_Bps":  sys_rates_now.get("net_rx_Bps", 0.0),
                "net_tx_Bps":  sys_rates_now.get("net_tx_Bps", 0.0),
                # usage tức thời
                "disk_used_bytes":   curr_sys.get("disk_used_bytes"),
                "disk_total_bytes":  curr_sys.get("disk_total_bytes"),
                "disk_used_percent": curr_sys.get("disk_used_percent"),
                "total_ram": settings.TOTAL_RAM_BYTES,
                "cpu_threshold": settings.CPU_THRESHOLD,
                "memory_threshold": settings.MEMORY_THRESHOLD,
            }
            prev_sys, t0_sys = curr_sys, now

            # --- services ---
            services_payload = []
            for sid, cgpath in list(svc_map.items()):
                try:
                    
                    curr = cg_snapshot(cgpath)
                except FileNotFoundError:
                    # cgroup biến mất (service dừng) -> loại khỏi vòng lặp
                    prev_svc.pop(sid, None)
                    svc_map.pop(sid, None)
                    continue

                if sid in prev_svc:
                    prev_snap, t0 = prev_svc[sid]
                    dt = max(1e-6, now - t0)
                    r = cg_rates(prev_snap, curr, dt)

                    services_payload.append({
                        "app_id": sid,
                        "cpu_percent": r["cpu_percent"],
                        "mem_bytes":   r["mem_bytes"],
                        # Nếu muốn thêm IO:
                        # "read_Bps": r["read_Bps"], "write_Bps": r["write_Bps"]
                        "cpu_threshold": getattr(settings.APPS.get(sid), "cpu_threshold", None),
                        "memory_threshold_mb": getattr(settings.APPS.get(sid), "memory_threshold_mb", None),
                        "uptime": get_service_uptime(sid),
                    })
                prev_svc[sid] = (curr, now)

            payload = {
                "ts_ms": int(now * 1000),
                "system": sys_payload,
                "services": services_payload,
            }

            await ws.send_text(json.dumps(payload))
    except WebSocketDisconnect:
        return


@router.websocket("/ws/logs")
async def logs_ws(
    ws: WebSocket,
    service: str = Query(..., description="e.g. nginx or nginx.service"),
    tail: int = Query(TAIL_DEFAULT, ge=0, le=5000),
):
    await ws.accept()
    svc = _validate_service(service)
    try:
        await hub.subscribe(svc, ws, tail)
        while True:
            await asyncio.sleep(60)
    except WebSocketDisconnect:
        pass
    finally:
        # fix tên hàm (trước đây có khoảng trắng)
        await hub.unsubscribe(svc, ws)

@router.websocket("/ws/containers")
async def ws_containers(
    ws: WebSocket,
    container: str = Query(None, description="Tên container"),
    interval_ms: int = Query(5000, ge=50, le=60000),
):
    """
    WebSocket để gửi số liệu realtime của container.
    """
    
    await ws.accept()
    try:
        while True:
            if not container:
                metrics = get_metrics_from_containers(settings.CONTAINERS.keys())
            else:
                metrics = get_metrics_from_containers([container])
            container_payload = []
            if container is not None:
                payload = {}
                data = metrics.get(container)
                if data:
                    payload = {
                        "ts_ms": int(time.time() * 1000),
                        "container_name": container,
                        "cpu_percent": data["cpu_percent"],
                        "mem_bytes": data["mem_bytes"],
                        "mem_limit": data["mem_limit"],
                    }
                    await ws.send_text(json.dumps(payload))

            else:
                for name, data in metrics.items():

                    container_payload.append({
                        "container_name": name,
                        "cpu_percent": data["cpu_percent"],
                        "mem_bytes": data["mem_bytes"],
                        "mem_limit": data["mem_limit"],
                    })
                payload = {
                    "ts_ms": int(time.time() * 1000),
                    "containers": container_payload,
                }
                await ws.send_text(json.dumps(payload))
            await asyncio.sleep(max(0.05, interval_ms / 1000))
    except WebSocketDisconnect:
        return
    