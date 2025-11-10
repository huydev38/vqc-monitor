from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from vqc_monitor.core.config import settings
from sqlalchemy.pool import NullPool
DB_URL = f"sqlite:///{settings.DB_PATH}"

class Base(DeclarativeBase): ...
engine = create_engine(DB_URL, connect_args={"check_same_thread": False}, poolclass=NullPool)

# Bật WAL + tuning
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_conn, conn_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA synchronous=NORMAL;")
    # cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

def create_all():
    from vqc_monitor.db import models  # ensure models imported
    Base.metadata.create_all(bind=engine)
