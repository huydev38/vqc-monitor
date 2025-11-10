# app/api/ws_alerts.py
import time, asyncio
from typing import Optional, Any, Dict, Iterable
from fastapi import APIRouter, WebSocket, Query
from fastapi.websockets import WebSocketDisconnect
from sqlalchemy import text
from vqc_monitor.db.base import SessionLocal
from vqc_monitor.db import repo

router = APIRouter()

def to_jsonable_alert(a: Any) -> Dict[str, Any]:
    """
    Chuyển ORM/Row -> dict. Điều chỉnh fields theo schema alerts của bạn.
    """
    # Nếu repo.get_alerts đã trả dict thì trả thẳng:
    if isinstance(a, dict):
        return a

    # Nếu là SQLAlchemy model:
    # return {"id": a.id, "app_id": a.app_id, "metric": a.metric, "ts_ms": a.ts_ms, "value": a.value}

    # Nếu là Row (result from text query):
    try:
        d = dict(a._mapping)
        return d
    except Exception:
        # fallback tối thiểu
        return {
            "app_id": getattr(a, "app_id", None),
            "alert_type": getattr(a, "alert_type", None),
            "ts_ms": getattr(a, "ts_ms", None),
            "value": getattr(a, "value", None),
        }
    
def to_jsonable_container_alert(a: Any) -> Dict[str, Any]:
    """
    Chuyển ORM/Row -> dict. Điều chỉnh fields theo schema alerts của bạn.
    """
    # Nếu repo.get_alerts đã trả dict thì trả thẳng:
    if isinstance(a, dict):
        return a

    # Nếu là SQLAlchemy model:
    # return {"id": a.id, "app_id": a.app_id, "metric": a.metric, "ts_ms": a.ts_ms, "value": a.value}

    # Nếu là Row (result from text query):
    try:
        d = dict(a._mapping)
        return d
    except Exception:
        # fallback tối thiểu
        return {
            "container_name": getattr(a, "container_name", None),
            "alert_type": getattr(a, "alert_type", None),
            "ts_ms": getattr(a, "ts_ms", None),
            "value": getattr(a, "value", None),
        }

@router.websocket("/ws/alerts")
async def alerts_ws(
    ws: WebSocket,
    app_id: Optional[str] = Query(None, description="Lọc theo app_id"),
    limit: int = Query(10, description="Số lượng alert tối đa trả về")
):
    alerts_list_prev = []
    alerts_list = []
    await ws.accept()



    try:
        # 1) Gửi lịch sử trong [ts_from, ts_to]
        with SessionLocal() as db:
            while True:
                if alerts_list_prev == alerts_list and alerts_list:
                    await asyncio.sleep(5)
                    continue

                alerts_list = repo.get_alerts(db, app_id=app_id, limit=limit)  # đảm bảo hàm này trả list
                alerts_jsonable = [to_jsonable_alert(a) for a in alerts_list]
                await ws.send_json({"alerts": alerts_jsonable})
                alerts_list_prev = alerts_list
                await asyncio.sleep(10)

    except WebSocketDisconnect:
        return
    finally:
        db.close()

@router.websocket("/ws/container/alerts")
async def alerts_ws(
    ws: WebSocket,
    container_name: Optional[str] = Query(None, description="Lọc theo container_name"),
    limit: int = Query(10, description="Số lượng alert tối đa trả về")
):
    alerts_list_prev = []
    alerts_list = []
    await ws.accept()



    try:
        # 1) Gửi lịch sử trong [ts_from, ts_to]
        with SessionLocal() as db:
            while True:
                if alerts_list_prev == alerts_list and alerts_list:
                    await asyncio.sleep(5)
                    continue

                alerts_list = repo.get_container_alerts(db, limit=limit, container_name=container_name)  # đảm bảo hàm này trả list
                alerts_jsonable = [to_jsonable_container_alert(a) for a in alerts_list]
                await ws.send_json({"alerts": alerts_jsonable})
                alerts_list_prev = alerts_list
                await asyncio.sleep(10)

    except WebSocketDisconnect:
        return
    finally:
        db.close()