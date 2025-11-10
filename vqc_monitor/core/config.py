from pathlib import Path
from pydantic import BaseModel, Field
import yaml
import subprocess
import shlex
import os
from typing import Optional

ETC_DIR = Path("/etc/vqc-monitor")
ETC_CONFIG = ETC_DIR / "config.yaml"
CGROUP_ROOT = Path("/sys/fs/cgroup")  # cgroup v2


# ---- Config models ----
class Service(BaseModel):
    name: str
    version: str
    cpu_threshold: Optional[float] = 80  # future use
    memory_threshold_mb: Optional[float] = 1024  # future use


class Container(BaseModel):
    name: str
    image: str
    version: str
    memory_threshold_mb: Optional[float] = 1024  # future use
    cpu_threshold: Optional[float] = 5  # future use


class FileConfig(BaseModel):
    sample_interval_ms: int = 3000
    retention_days: int = 30
    cpu_threshold: float = 80
    memory_threshold: float = 80
    disk_threshold: float = 90
    services: list[Service] = Field(default_factory=list)  # name + version
    containers: list[Container] = Field(default_factory=list)  # name + version


# ---- Runtime models ----
class AppInfo(BaseModel):
    cgroup: str
    version: str
    running: bool = False  # runtime only
    trackable: bool = False
    cpu_threshold: Optional[float] = 80  # future use
    memory_threshold_mb: Optional[float] = 1024  # future use
    version_real: Optional[str] = None


class ContainerInfo(BaseModel):
    name: str
    image: str
    version: str
    running: bool = False  # runtime only
    version_real: Optional[str] = None
    cpu_threshold: Optional[float] = 5  # future use
    memory_threshold_mb: Optional[float] = 1024  # future use


class Settings(BaseModel):
    DB_PATH: str = "monitor.db"
    SAMPLE_INTERVAL_MS: int = 1000
    RETENTION_DAYS: int = 30
    CPU_THRESHOLD: float = 80
    MEMORY_THRESHOLD: float = 80
    DISK_THRESHOLD: float = 90
    TOTAL_RAM_BYTES: int = 0
    ALERT_WINDOW_MS: int = 300000  # 5 minutes
    ALERT_COOLDOWN_MS: int = 900000  # 15 minutes
    # Sau khi resolve, APPS = {app_id: AppInfo}
    APPS: dict[str, AppInfo] = Field(default_factory=dict)
    CONTAINERS: dict[str, ContainerInfo] = Field(default_factory=dict)

    def load_file_config(self, path: Path = ETC_CONFIG) -> FileConfig:
        data = {}
        if path.exists():
            data = yaml.safe_load(path.read_text()) or {}
        fc = FileConfig(**data)
        self.SAMPLE_INTERVAL_MS = fc.sample_interval_ms
        self.RETENTION_DAYS = fc.retention_days
        self.CPU_THRESHOLD = fc.cpu_threshold
        self.MEMORY_THRESHOLD = fc.memory_threshold
        self.DISK_THRESHOLD = fc.disk_threshold
        # Resolve services -> APPS
        self.APPS = resolve_services_to_cgroups(fc.services)
        self.CONTAINERS = resolve_containers_to_info(fc.containers)
        return fc


# ---- Resolver helpers ----
def resolve_services_to_cgroups(services: list[Service]) -> dict[str, AppInfo]:
    """
    Trả về map {service_name_without_suffix: AppInfo(cgroup=<abs cgroup path>, version=<version>)}.
    Dùng `systemctl show -p ControlGroup` để lấy path dưới /sys/fs/cgroup.
    Validate sự tồn tại các file cpu.stat, memory.current.
    """
    out: dict[str, AppInfo] = {}

    for svc in services:
        isRunning = True
        isTrackable = True
        svc_name = svc.name.strip()
        if not svc_name.endswith(".service"):
            # chấp nhận người dùng viết thiếu .service
            svc_name += ".service"

        cg_path = _resolve_cgroup_path(svc_name)
        real_version = get_real_version_of_service(svc_name)
        if cg_path is None:
            print(f"[WARN] Không tìm được cgroup cho {svc_name} (service có chạy không?). Bỏ qua.")
            isRunning = False
        else:
            # validate các file quan trọng
            cpu_ok = (cg_path / "cpu.stat").exists()
            mem_ok = (cg_path / "memory.current").exists()
            if not (cpu_ok and mem_ok):
                print(f"[WARN] cgroup path thiếu file cpu/mem cho {svc_name}: {cg_path}")
                isTrackable = False

        # app_id (khóa) dùng tên service không đuôi .service
        app_id = svc_name.removesuffix(".service")
        out[app_id] = AppInfo(
            cgroup=str(cg_path) if cg_path else "",
            version=svc.version,
            running=isRunning,
            trackable=isTrackable,
            memory_threshold_mb=svc.memory_threshold_mb,
            cpu_threshold=svc.cpu_threshold,
            version_real=real_version,
        )

    return out


def resolve_service_to_cgroup(service: str) -> Optional[Path]:
    """
    Wrapper cho trường hợp chỉ có một service.
    Trả về Path tuyệt đối /sys/fs/cgroup/<control_group> hoặc None nếu không xác định được.
    """
    svc = service.strip()
    if not svc.endswith(".service"):
        svc += ".service"
    return _resolve_cgroup_path(svc)


def _resolve_cgroup_path(service_name: str) -> Optional[Path]:
    """
    Trả về Path tuyệt đối /sys/fs/cgroup/<control_group> hoặc None nếu không xác định được.
    """
    try:
        # Ví dụ output: "ControlGroup=/system.slice/nginx.service"
        cp = subprocess.run(
            ["systemctl", "show", "-p", "ControlGroup", service_name],
            check=True,
            capture_output=True,
            text=True,
        )
        line = (cp.stdout or "").strip()
        if "=" in line:
            _, value = line.split("=", 1)
            value = value.strip()  # ví dụ "/system.slice/nginx.service"
            if value:
                cg = CGROUP_ROOT / value.lstrip("/")
                # Nếu đường dẫn không tồn tại, có thể service chưa chạy
                return cg if cg.exists() else None
    except subprocess.CalledProcessError as e:
        print(f"[WARN] systemctl show thất bại cho {service_name}: {e}")
    return None


def get_total_ram_bytes() -> int:
    """
    Đọc tổng RAM hệ thống từ /proc/meminfo, trả về bytes.
    """
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemTotal:"):
            parts = line.split()
            if len(parts) >= 2:
                # MemTotal: 16384256 kB
                kb = int(parts[1])
                return kb * 1024
    return 0


def get_real_version_of_service(package_name: str) -> Optional[str]:
    """
    Lấy version thực tế của gói (package) liên quan đến dịch vụ 
    từ cơ sở dữ liệu dpkg.

    Args:
        package_name (str): Tên gói (ví dụ: 'nginx', 'apache2', 'mysql-server').
                            Không cần thêm '.service'.

    Returns:
        Optional[str]: Phiên bản của gói, hoặc None nếu không tìm thấy.
    """
    pkg = package_name.strip()

    # Chúng ta sử dụng tên gói, không phải tên systemd unit
    if pkg.endswith(".service"):
        pkg = pkg[:-len(".service")]

    try:
        # Lệnh dpkg -s <tên_gói> sẽ xuất ra nhiều dòng thông tin
        cp = subprocess.run(
            ["dpkg", "-s", pkg],
            capture_output=True,
            text=True,
            check=False,  # Không ném CalledProcessError nếu gói không được cài đặt
        )

        # Nếu dpkg không tìm thấy gói, returncode sẽ khác 0 (thường là 1)
        if cp.returncode != 0:
            return None

        # Phân tích đầu ra (stdout) theo từng dòng để tìm dòng "Version:"
        for line in cp.stdout.splitlines():
            if line.startswith("Version:"):
                _, value = line.split(":", 1)
                return value.strip()

        # Trường hợp hiếm: Gói được cài đặt nhưng không có dòng Version:
        return None

    except FileNotFoundError:
        print("[ERROR] Lệnh 'dpkg' không được tìm thấy. Bạn đang chạy trên Ubuntu/Debian chứ?")
        return None
    except Exception as e:
        print(f"[WARN] Lỗi không mong muốn khi lấy version cho gói {pkg}: {e}")
        return None


def resolve_containers_to_info(containers: list[Container]) -> dict[str, "ContainerInfo"]:
    out: dict[str, ContainerInfo] = {}

    for ctr in containers:
        inspect_command = f"docker inspect {ctr.name}"
        inspect_args = shlex.split(inspect_command)
        try:
            cp = subprocess.run(
                inspect_args,
                capture_output=True,
                text=True,
                check=True,
            )
            inspect_data = yaml.safe_load(cp.stdout)
            if isinstance(inspect_data, list) and len(inspect_data) > 0:
                container_info = inspect_data[0]
                real_version = container_info.get("Config", {}).get("Image", "")
                isRunning = container_info.get("State", {}).get("Running", False)
                out[ctr.name] = ContainerInfo(
                    name=ctr.name,
                    image=ctr.image,
                    version=ctr.version,
                    running=isRunning,
                    version_real=real_version,
                    cpu_threshold=ctr.cpu_threshold,
                    memory_threshold_mb=ctr.memory_threshold_mb,
                )
            else:
                out[ctr.name] = ContainerInfo(
                    name=ctr.name,
                    image=ctr.image,
                    version=ctr.version,
                    running=False,
                    version_real=None,
                    cpu_threshold=ctr.cpu_threshold,
                    memory_threshold_mb=ctr.memory_threshold_mb,
                )
        except subprocess.CalledProcessError as e:
            print(f"[WARN] docker inspect thất bại cho {ctr.name}: {e}")
            real_version = None
            out[ctr.name] = ContainerInfo(
                name=ctr.name,
                image=ctr.image,
                version=ctr.version,
                running=False,
                version_real=real_version,
            )
    return out


def reload_list_services() -> dict[str, AppInfo]:
    """
    Reload lại file config và cập nhật lại list_services.
    Trả về dict mới.
    """
    fc = settings.load_file_config()
    global list_services
    global list_containers
    list_services = settings.APPS
    list_containers = settings.CONTAINERS
    print(
        f"[INFO] Reloaded config: sample_interval_ms={fc.sample_interval_ms}, "
        f"retention_days={fc.retention_days}, {len(fc.services)} services"
    )
    return list_services


# ---- Tạo singleton settings & nạp config SAU KHI ĐỊNH NGHĨA HÀM ----
settings = Settings()
settings.load_file_config()
list_services = settings.APPS
settings.TOTAL_RAM_BYTES = get_total_ram_bytes()
list_containers = settings.CONTAINERS
