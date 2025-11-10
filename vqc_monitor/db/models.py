from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import Integer, String, BigInteger, Float, ForeignKey, PrimaryKeyConstraint
from sqlalchemy.sql import func
from vqc_monitor.db.base import Base

class App(Base):
    __tablename__ = "apps"
    id: Mapped[str] = mapped_column(String, primary_key=True)       # "nginx"
    name: Mapped[str] = mapped_column(String, nullable=False)       # "Nginx"
    cgroup_path: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, server_default=func.strftime("%s","now"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

class Sample(Base):
    __tablename__ = "samples"
    ts_ms: Mapped[int] = mapped_column(BigInteger)                  # epoch ms
    app_id: Mapped[str] = mapped_column(String, ForeignKey("apps.id", ondelete="CASCADE"))
    cpu_percent: Mapped[float] = mapped_column(Float)
    mem_bytes: Mapped[int] = mapped_column(BigInteger)
    io_read_Bps: Mapped[float] = mapped_column(Float)
    io_write_Bps: Mapped[float] = mapped_column(Float)
    __table_args__ = (PrimaryKeyConstraint("app_id", "ts_ms"), )

class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    app_id: Mapped[str] = mapped_column(String, ForeignKey("apps.id", ondelete="CASCADE"))
    alert_type: Mapped[str] = mapped_column(String)                  # "cpu", "memory", "io_read", "io_write"
    ts_ms: Mapped[int] = mapped_column(BigInteger)                  # epoch ms
    value: Mapped[float] = mapped_column(Float)                      # giá trị tại thời điểm cảnh báo

class ContainerAlert(Base):
    __tablename__ = "container_alerts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    container_name: Mapped[str] = mapped_column(String, ForeignKey("containers.name", ondelete="CASCADE"))
    alert_type: Mapped[str] = mapped_column(String)                  # "cpu", "memory"
    ts_ms: Mapped[int] = mapped_column(BigInteger)                  # epoch ms
    value: Mapped[float] = mapped_column(Float)                      # giá trị tại thời điểm cảnh báo

class StateTimeline(Base):
    __tablename__ = "state_timelines"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    app_id: Mapped[str] = mapped_column(String, ForeignKey("apps.id", ondelete="CASCADE"))
    state: Mapped[str] = mapped_column(String)                       # "running", "stopped", etc.
    start_time: Mapped[int] = mapped_column(BigInteger)
    end_time: Mapped[int] = mapped_column(BigInteger, nullable=True)  # null nếu đang diễn ra

class ContainerStateTimeline(Base):
    __tablename__ = "container_state_timelines"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    container_name: Mapped[str] = mapped_column(String, ForeignKey("containers.name", ondelete="CASCADE"))
    state: Mapped[str] = mapped_column(String)                       # "running", "stopped", etc.
    start_time: Mapped[int] = mapped_column(BigInteger)
    end_time: Mapped[int] = mapped_column(BigInteger, nullable=True)  # null nếu đang diễn ra

class Container(Base):
    __tablename__ = "containers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)  
    name: Mapped[str] = mapped_column(String, nullable=False)       # container name
    image: Mapped[str] = mapped_column(String, nullable=False)      # container image
    created_at: Mapped[int] = mapped_column(BigInteger, server_default=func.strftime("%s","now"))
    version: Mapped[str] = mapped_column(String, nullable=False)

class ContainerMetric(Base):
    __tablename__ = "container_metrics"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    container_name: Mapped[str] = mapped_column(String, ForeignKey("containers.name", ondelete="CASCADE"))
    ts_ms: Mapped[int] = mapped_column(BigInteger)                  # epoch ms
    cpu_percent: Mapped[float] = mapped_column(Float)
    mem_bytes: Mapped[int] = mapped_column(BigInteger)