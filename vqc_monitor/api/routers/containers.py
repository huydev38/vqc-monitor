from fastapi import APIRouter, Query, Depends
from vqc_monitor.db import repo
from vqc_monitor.core.config import settings
from vqc_monitor.db.repo import get_container_stats
from vqc_monitor.api.deps import get_db, db_context
from vqc_monitor.core import container_control


router = APIRouter(prefix="/containers", tags=["containers"])


@router.get("")
def list_containers():
    return settings.CONTAINERS

@router.get("/{container_name}/stats")
def get_container_stats(
    container_name: str,
    start: int = Query(..., description="epoch ms"),
    end:   int = Query(..., description="epoch ms"),
    max_points: int = Query(1000, ge=10, le=1000),
    bucket_ms: int | None = Query(None, ge=5000),
    db = Depends(get_db),

):
    return repo.get_container_stats(db, container_name, start, end, bucket_ms=bucket_ms, max_points=max_points)

@router.post("/{container_name}/control/{action}")
def control_container(container_name: str, action: str):
    if action in ["start", "stop", "restart"]:
        return container_control.control_container(container_name, action)
    else:
        return {"status": "error", "message": "Invalid action"}