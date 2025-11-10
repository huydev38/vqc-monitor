from fastapi import FastAPI
from vqc_monitor.core.daily_cleanup import daily_cleanup
from vqc_monitor.db.base import create_all
from vqc_monitor.api.routers import apps, stats, containers
from vqc_monitor.api import ws
from vqc_monitor.api.routers import alert
from vqc_monitor.metrics.collector import Collector
from vqc_monitor.db import repo
from vqc_monitor.core.config import settings    
from fastapi.middleware.cors import CORSMiddleware           
from vqc_monitor.metrics.collector import update_timeline_when_system_start      


import asyncio
from vqc_monitor.db.base import create_all, SessionLocal


collector = Collector()

def create_app():
    create_all()  # <-- TỰ SINH BẢNG NẾU CHƯA CÓ

    with SessionLocal() as db:
        repo.ensure_system_app(db)         # tạo apps.id="__system__" nếu chưa có
        repo.upsert_apps(db, settings.APPS)  # tạo/cập nhật rows cho mọi service
        repo.upsert_containers(db, settings.CONTAINERS)
        update_timeline_when_system_start()  # Cập nhật timeline khi khởi động
        db.commit()
    app = FastAPI(title="App Monitor")




# Add CORS middleware
    app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins (use specific domains in production)
    allow_credentials=True,
    allow_methods=["*"],  # Allow all HTTP methods
    allow_headers=["*"],  # Allow all headers
    )

    app.include_router(apps.router)
    app.include_router(stats.router)
    app.include_router(ws.router)
    app.include_router(alert.router)
    app.include_router(containers.router)
    
    

    @app.on_event("startup")
    async def _start():
        asyncio.create_task(collector.run())
        asyncio.create_task(daily_cleanup())
    return app

app = create_app()