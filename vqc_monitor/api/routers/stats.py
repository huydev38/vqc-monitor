
from fastapi import APIRouter, Depends, Query, HTTPException
from vqc_monitor.api.deps import get_db, db_context
from vqc_monitor.db import repo
from datetime import datetime, timedelta
from vqc_monitor.db.repo import get_stats

router = APIRouter()

# @router.get("/{app_id}/stats")
# def get_stats(app_id: str,
#               ts_from: int = Query(..., description="epoch ms"),
#               ts_to:   int = Query(..., description="epoch ms"),
#               limit:   int = 2000,
#               db = Depends(get_db)):
#     rows = repo.get_stats(db, app_id, ts_from, ts_to, limit)
#     return [dict(
#         ts_ms=r.ts_ms,
#         cpu_percent=r.cpu_percent,
#         mem_bytes=r.mem_bytes,
#         io_read_Bps=r.io_read_Bps,
#         io_write_Bps=r.io_write_Bps
#     ) for r in rows]


DEFAULT_MAX_POINTS = 1000

@router.get("/apps/{app_id}/stats")
def get_stats_bucketed(
    app_id: str,
    start: int = Query(..., description="epoch ms"),
    end:   int = Query(..., description="epoch ms"),
    max_points: int = Query(DEFAULT_MAX_POINTS, ge=10, le=1000),
    bucket_ms: int | None = Query(None, ge=5000) ,  # tối thiểu 5000ms để tránh 0
    db = Depends(get_db)
):
    stats = repo.get_stats(db, app_id, start, end, max_points, bucket_ms)
    return stats

    

@router.get("/apps/{app_id}/state_timelines")
def get_state_timelines(
    app_id: str,
    ts_from: int = Query(int((datetime.now() - timedelta(hours=24)).timestamp() * 1000), description="epoch ms"),
    ts_to:   int = Query(int(datetime.now().timestamp() * 1000), description="epoch ms"),
    db = Depends(get_db)
):
    rows = repo.get_state_timelines(db, app_id, ts_from, ts_to)
    return [
        {
            "id": r.id,
            "app_id": r.app_id,
            "state": r.state,
            "start_time": r.start_time,
            "end_time": r.end_time,
        } for r in rows
    ]

@router.get("/containers/{container_name}/state_timelines")
def get_state_timelines(
    container_name: str,
    ts_from: int = Query(int((datetime.now() - timedelta(hours=24)).timestamp() * 1000), description="epoch ms"),
    ts_to:   int = Query(int(datetime.now().timestamp() * 1000), description="epoch ms"),
    db = Depends(get_db)
):
    rows = repo.get_state_timelines_container(db, container_name, ts_from, ts_to)
    return [
        {
            "id": r.id,
            "container_name": r.container_name,
            "state": r.state,
            "start_time": r.start_time,
            "end_time": r.end_time,
        } for r in rows
    ]
