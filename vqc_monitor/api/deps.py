from contextlib import contextmanager
from vqc_monitor.db.base import SessionLocal

@contextmanager
def db_context():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_db():
    with db_context() as db:
        yield db