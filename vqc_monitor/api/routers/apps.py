from fastapi import APIRouter, Depends, HTTPException
from vqc_monitor.api.deps import get_db
from vqc_monitor.db import repo
from vqc_monitor.core.config import reload_list_services
from vqc_monitor.core import app_control


router = APIRouter(tags=["apps"])

@router.get("/apps")
def list_apps():
    return repo.list_apps()

@router.post("/apps/{app_id}/control/{action}")
def control_app(app_id: str, action: str):
    if app_control.control_service(app_id, action):
        reload_list_services()  # cập nhật lại config
        return {"status": "success"}
    else:
        return {"status": "error"}
    
@router.get("/system/thresholds")
def get_system_thresholds():
    from vqc_monitor.core.config import settings
    return {
        "cpu_threshold": settings.CPU_THRESHOLD,
        "memory_threshold": settings.MEMORY_THRESHOLD,
        "disk_threshold": settings.DISK_THRESHOLD
    }