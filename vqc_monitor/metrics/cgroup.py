import subprocess
from pathlib import Path
import os
import time

from typing import Optional

def _parse_kv(line: str) -> dict[str,int]:
    out = {}
    for tok in line.strip().split():
        if "=" in tok:
            k,v = tok.split("=",1)
            out[k] = int(v)
    return out

def read_cpu_usage_us(cg: Path) -> int:
    for line in (cg/"cpu.stat").read_text().splitlines():
        if line.startswith("usage_usec"):
            return int(line.split()[1])
    return 0

def read_mem_bytes(cg: Path) -> int:
    for line in (cg / "memory.stat").read_text().splitlines():
        key, val = line.split()
        if key == "anon":
            return int(val)
    raise KeyError("anon not found in memory.stat")

def read_io_bytes(cg: Path) -> tuple[int,int]:
    p = cg/"io.stat"
    rbytes = wbytes = 0
    if p.exists():
        for line in p.read_text().splitlines():
            kv = _parse_kv(line)
            rbytes += kv.get("rbytes", 0)
            wbytes += kv.get("wbytes", 0)
    return rbytes, wbytes

def snapshot(cgroup_path: str) -> dict:
    cg = Path(cgroup_path)
    rbytes, wbytes = read_io_bytes(cg)
    return {
        "cpu_usage_us": read_cpu_usage_us(cg),
        "mem_bytes":    read_mem_bytes(cg),
        "io_rbytes":    rbytes,
        "io_wbytes":    wbytes,
    }

def compute_rates(prev: dict, curr: dict, dt_sec: float) -> dict:
    ncpu = os.cpu_count() or 1
    d_cpu_us = max(0, curr["cpu_usage_us"] - prev["cpu_usage_us"])
    d_rbytes = max(0, curr["io_rbytes"] - prev["io_rbytes"])
    d_wbytes = max(0, curr["io_wbytes"] - prev["io_wbytes"])
    cpu_pct = (d_cpu_us / 1e6) / dt_sec * 100.0 / ncpu
    return {
        "cpu_percent": cpu_pct,
        "mem_bytes": curr["mem_bytes"],
        "read_Bps": d_rbytes / dt_sec,
        "write_Bps": d_wbytes / dt_sec,
    }

def get_service_uptime(service_name: str) -> Optional[float]:
    """
    Trả về số giây service đã chạy (uptime) hoặc None nếu không xác định được.
    """
    try:
        cp = subprocess.run(
            ["systemctl", "show", "-p", "ActiveEnterTimestampMonotonic", service_name],
            check=True, capture_output=True, text=True
        )
        line = (cp.stdout or "").strip()
        if "=" in line:
            _, value = line.split("=", 1)
            value = value.strip()
            if value.isdigit():
                # Trả về uptime tính từ lúc hiện tại
                monotonic_now_us = int(float(time.monotonic() * 1e6))
                started_us = int(value)
                uptime_s = (monotonic_now_us - started_us) / 1e6
                formatted_time = time.strftime("%H:%M:%S", time.gmtime(uptime_s))

                return formatted_time
    except subprocess.CalledProcessError as e:
        print(f"[WARN] systemctl show thất bại cho {service_name}: {e}")
    return None
