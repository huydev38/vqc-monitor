# app/metrics/system.py
from __future__ import annotations
import os
from pathlib import Path

# --------- CPU (/proc/stat) ----------
def _read_proc_stat():
    # dòng "cpu  user nice system idle iowait irq softirq steal guest guest_nice"
    line = next(l for l in Path("/proc/stat").read_text().splitlines() if l.startswith("cpu "))
    parts = line.split()
    # convert jiffies -> int
    vals = list(map(int, parts[1:11]))  # 10 trường đầu
    keys = ["user","nice","system","idle","iowait","irq","softirq","steal","guest","guest_nice"]
    return dict(zip(keys, vals))

def _cpu_active_idle_jiffies(d: dict[str,int]) -> tuple[int,int]:
    idle = d["idle"] + d.get("iowait",0)
    active = d["user"]+d["nice"]+d["system"]+d.get("irq",0)+d.get("softirq",0)+d.get("steal",0)
    return active, idle

# --------- MEM (/proc/meminfo) ----------
def _read_meminfo():
    mi = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        k, v = line.split(":", 1)
        num = int(v.strip().split()[0])  # kB
        mi[k] = num * 1024  # bytes
    # MemUsed ~ MemTotal - MemAvailable
    used = mi["MemTotal"] - mi.get("MemAvailable", mi["MemFree"])
    return {"mem_total": mi["MemTotal"], "mem_used": used}

# --------- DISK (/proc/diskstats) ----------
def _read_diskstats_bytes():
    # read_sectors at col 6, write_sectors at col 10 for modern kernels
    # We'll skip loop/ram/zram partitions
    total_r = total_w = 0
    for line in Path("/proc/diskstats").read_text().splitlines():
        parts = line.split()
        if len(parts) < 14: 
            continue
        dev = parts[2]
        if dev.startswith(("loop","ram","zram","dm-")):
            continue
        read_sectors = int(parts[5])
        written_sectors = int(parts[9])
        # Assume 512 bytes/sector (most kernels report 512 logical)
        total_r += read_sectors * 512
        total_w += written_sectors * 512
    return total_r, total_w

# --------- NET (/proc/net/dev) ----------
def _read_net_bytes():
    rx = tx = 0
    for line in Path("/proc/net/dev").read_text().splitlines():
        if ":" not in line: 
            continue
        iface, rest = line.split(":")
        iface = iface.strip()
        if iface in ("lo",):  # bỏ loopback; tùy bạn, có thể giữ lại
            continue
        fields = rest.split()
        rx_bytes = int(fields[0])
        tx_bytes = int(fields[8])
        rx += rx_bytes
        tx += tx_bytes
    return rx, tx

def _root_disk_usage():
    st = os.statvfs("/")  # phân vùng root, đủ dùng cho dashboard tổng
    total = st.f_frsize * st.f_blocks
    free  = st.f_frsize * st.f_bavail  # free usable cho user thường
    used  = max(0, total - free)
    pct   = (used / total * 100.0) if total > 0 else 0.0
    return used, total, pct

def snapshot() -> dict:
    cpu = _read_proc_stat()
    mem = _read_meminfo()
    disk_r, disk_w = _read_diskstats_bytes()
    net_r, net_w   = _read_net_bytes()

    used, total, pct = _root_disk_usage()         # <-- thêm

    return {
        "cpu": cpu,
        "mem_total": mem["mem_total"],
        "mem_used":  mem["mem_used"],
        "disk_rbytes": disk_r,
        "disk_wbytes": disk_w,
        "net_rx_bytes": net_r,
        "net_tx_bytes": net_w,

        # số liệu tức thời cho FE
        "disk_used_bytes":   used,                # <-- thêm
        "disk_total_bytes":  total,               # <-- thêm
        "disk_used_percent": pct,                 # <-- thêm
    }

def compute_rates(prev: dict, curr: dict, dt_sec: float) -> dict:
    # CPU%
    a1, i1 = _cpu_active_idle_jiffies(prev["cpu"])
    a2, i2 = _cpu_active_idle_jiffies(curr["cpu"])
    d_active = max(0, a2 - a1)
    d_idle   = max(0, i2 - i1)
    total = d_active + d_idle
    cpu_pct_total = (d_active / total * 100.0) if total > 0 else 0.0
    # RAM hiện tại
    mem_bytes = curr["mem_used"]
    # Disk/Net rates
    d_r = max(0, curr["disk_rbytes"] - prev["disk_rbytes"])
    d_w = max(0, curr["disk_wbytes"] - prev["disk_wbytes"])
    d_rx = max(0, curr["net_rx_bytes"] - prev["net_rx_bytes"])
    d_tx = max(0, curr["net_tx_bytes"] - prev["net_tx_bytes"])
    return {
        "cpu_percent": cpu_pct_total,     # % tổng hệ thống (all cores)
        "mem_bytes": mem_bytes,           # bytes used (instant)
        "read_Bps": d_r / dt_sec,
        "write_Bps": d_w / dt_sec,
        "net_rx_Bps": d_rx / dt_sec,
        "net_tx_Bps": d_tx / dt_sec,
    }
