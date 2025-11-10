import asyncio, time
from vqc_monitor.core.config import settings
from vqc_monitor.db.base import SessionLocal
from vqc_monitor.db import repo
from vqc_monitor.metrics.cgroup import snapshot, compute_rates
from vqc_monitor.metrics import system as sysm
import subprocess
import shlex
from datetime import datetime
from psutil import boot_time

class Collector:
    def __init__(self):
        self.prev = {}  # app_id -> (snap, t)
        self.sys_prev = None

        
    async def run(self):
        interval = settings.SAMPLE_INTERVAL_MS / 1000
        while True:
            t1 = time.time()
            with SessionLocal() as db:
                # Bỏ những app không còn path (do service tắt → cgroup biến mất)
                for app_id, app_info in list(settings.APPS.items()):
                    try:
                        snap = snapshot(app_info.cgroup)
                        repo.open_or_close_state_timeline(db, app_id, "running")
                    except FileNotFoundError:
                        # cgroup biến mất giữa chừng
                        self.prev.pop(app_id, None)
                        repo.open_or_close_state_timeline(db, app_id, "stopped")
                        continue

                    now_ms = int(t1 * 1000)
                    if app_id in self.prev:
                        prev_snap, t0 = self.prev[app_id]
                        dt = max(1e-6, t1 - t0)
                        rates = compute_rates(prev_snap, snap, dt)
                        repo.insert_sample(
                            db, app_id, now_ms,
                            rates["cpu_percent"], rates["mem_bytes"],
                            rates["read_Bps"], rates["write_Bps"]
                        )
                    self.prev[app_id] = (snap, t1)

                sys_now = sysm.snapshot()
                if self.sys_prev:
                    prev_snap, t0 = self.sys_prev
                    dt = max(1e-6, t1 - t0)
                    rates = sysm.compute_rates(prev_snap, sys_now, dt)
                    repo.insert_sample(db, "__system__", int(t1*1000),
                                       rates["cpu_percent"], rates["mem_bytes"],
                                       rates["read_Bps"] + rates.get("net_rx_Bps",0),   # tùy bạn: có thể tách disk/net
                                       rates["write_Bps"] + rates.get("net_tx_Bps",0))
                    # ↑ Nếu muốn riêng Disk/Net, hãy mở rộng bảng, hoặc thêm cột net_rx/tx_Bps.
                self.sys_prev = (sys_now, t1)
                save_container_metrics(list(settings.CONTAINERS.keys()), db)

                db.commit()
            await asyncio.sleep(max(0, interval))

def update_timeline_when_system_start():

    apps = list(settings.APPS.keys())
    with SessionLocal() as db:
        last_state = {}
        for app_id in apps:
            last_state = repo.get_last_state(db, app_id)
            if last_state is None:
                continue
            if last_state.state == "running" and last_state.end_time is None:
                print(last_state.start_time)
                if last_state.start_time is not None and last_state.start_time < boot_time() * 1000:
                    print(f"Cập nhật timeline cho app_id={app_id} do khởi động lại hệ thống")
                    repo.update_state_timeline_end(db, app_id, get_last_shutdown_time())
        db.commit()

    containers = list(settings.CONTAINERS.keys())
    with SessionLocal() as db:
        last_state = {}
        for container_name in containers:
            last_state = repo.get_last_state_container(db, container_name)
            if last_state is None:
                continue
            if last_state.state == "running" and last_state.end_time is None:
                print(last_state.start_time)
                if last_state.start_time is not None and last_state.start_time < boot_time() * 1000:
                    print(f"Cập nhật timeline cho container={container_name} do khởi động lại hệ thống")
                    repo.update_state_timeline_end_container(db, container_name, get_last_shutdown_time())
        db.commit()

def get_last_shutdown_time():
    """
    Chạy lệnh 'last --fulltimes reboot -n 2' và trích xuất
    thời gian tắt máy (shutdown) cuối cùng.
    """
    
    # 1. Câu lệnh cần chạy
    # Dùng shlex.split để xử lý chuỗi lệnh một cách an toàn
    command = "last --fulltimes reboot -n 2"
    command_args = shlex.split(command)
    
    try:
        # 2. Thực thi câu lệnh
        # capture_output=True: Lấy stdout và stderr
        # text=True: Trả về kết quả dạng string (thay vì bytes)
        # check=True: Tự động báo lỗi nếu lệnh thất bại
        result = subprocess.run(command_args, 
                                capture_output=True, 
                                text=True, 
                                check=True,
                                encoding='utf-8')
        
        output = result.stdout.strip()
        
        if not output:
            print("Không có kết quả từ lệnh 'last'.")
            return None
            
        # 3. Phân tích kết quả
        lines = output.split('\n')
        last_shutdown_line = None
        
        # Lần tắt máy cuối cùng là bản ghi KHÔNG chứa "still running"
        for line in lines:
            if 'reboot' in line and 'still running' not in line:
                last_shutdown_line = line
                break # Tìm thấy bản ghi gần nhất, dừng vòng lặp

        if last_shutdown_line is None:
            print("Không tìm thấy bản ghi tắt máy nào (có thể đây là lần khởi động đầu tiên).")
            return None
            
        # 4. Trích xuất thời gian
        # Dòng mẫu: "... 2025 - Fri Oct 31 17:39:21 2025  (09:33)"
        
        # Tách dòng bởi dấu " - "
        parts = last_shutdown_line.split(' - ')
        
        if len(parts) < 2:
            print(f"Định dạng dòng không mong muốn: {last_shutdown_line}")
            return None
            
        # Lấy phần thứ hai (thông tin tắt máy)
        shutdown_info = parts[1].strip()
        
        # Tách chuỗi theo khoảng trắng và lấy 5 phần tử đầu tiên
        # (Day, Mon, DD, HH:MM:SS, YYYY)
        time_parts = shutdown_info.split()
        
        if len(time_parts) < 5:
            print(f"Không thể phân tích thời gian tắt máy từ: '{shutdown_info}'")
            return None
            
        # Ghép 5 phần tử đầu tiên lại
        shutdown_time_str = ' '.join(time_parts[:5])
        time_format = "%a %b %d %H:%M:%S %Y"
        shutdown_time = datetime.strptime(shutdown_time_str, time_format)
        epoch_ms = shutdown_time.timestamp() * 1000
        print(f"Thời gian tắt máy lần trước: {shutdown_time} (epoch ms: {epoch_ms})")
        return epoch_ms

    except FileNotFoundError:
        print(f"Lỗi: Không tìm thấy lệnh 'last'.")
        return None
    except subprocess.CalledProcessError as e:
        print(f"Lỗi khi chạy lệnh 'last': {e.stderr}")
        return None
    except Exception as e:
        print(f"Đã xảy ra lỗi: {e}")
        return None
    

def get_metrics_from_containers(container_names: list[str]):
        if(not container_names):
            return {}
        metrics = {}
        cmd = "docker stats --no-stream --format \"{{.Name}} {{.CPUPerc}} {{.MemUsage}}\" " + " ".join(container_names)
        command_args = shlex.split(cmd)
        try:
            result = subprocess.run(command_args,
                                    capture_output=True,
                                    text=True,
                                    check=True,
                                    encoding='utf-8')
            output = result.stdout.strip()
            lines = output.split('\n')
            for line in lines:
                parts = line.split()
                if len(parts) < 3:
                    continue
                name = parts[0]
                cpu_str = parts[1].replace('%','')
                
                mem_limit_str = parts[4].strip()
                mem_usage_str = parts[2].strip()
                # Chuyển đổi mem_usage_str sang bytes
                if mem_usage_str.lower().endswith('mib'):
                    mem_bytes = float(mem_usage_str[:-3].strip()) * 1024 * 1024
                elif mem_usage_str.lower().endswith('gib'):
                    mem_bytes = float(mem_usage_str[:-3].strip()) * 1024 * 1024 * 1024
                elif mem_usage_str.lower().endswith('kib'):
                    mem_bytes = float(mem_usage_str[:-3].strip()) * 1024
                elif mem_usage_str.lower().endswith('b'):
                    mem_bytes = mem_usage_str[:-1].strip()
                else:
                    mem_bytes = float(mem_usage_str)

              
                if mem_limit_str.lower().endswith('mib'):
                    mem_limit = float(mem_limit_str[:-3].strip()) * 1024 * 1024
                elif mem_limit_str.lower().endswith('gib'):
                    mem_limit = float(mem_limit_str[:-3].strip()) * 1024 * 1024 * 1024
                elif mem_limit_str.lower().endswith('kib'):
                    mem_limit = float(mem_limit_str[:-3].strip()) * 1024
                elif mem_limit_str.lower().endswith('b'):
                    mem_limit = mem_limit_str[:-1].strip()
                else:
                    mem_limit = float(mem_limit_str)
                
                

                metrics[name] = {
                    "cpu_percent": float(cpu_str),
                    "mem_bytes": int(mem_bytes),
                    "mem_limit": int(mem_limit),
                }
            return metrics
        except Exception as e:
            print(f"Lỗi khi lấy metrics từ docker: {e}")
            return {}
        

def save_container_metrics(containers: list[str], db):
        metrics = get_metrics_from_containers(containers)

        for name, metric in metrics.items():
            if metric:
                repo.insert_container_sample(
                    db, name, int(time.time()*1000),
                    metric["cpu_percent"],
                    metric["mem_bytes"]
                )
                if int(metric["mem_limit"]) == 0:
                    repo.open_or_close_state_timeline_container(db, name, "stopped")
                else:
                    repo.open_or_close_state_timeline_container(db, name, "running")