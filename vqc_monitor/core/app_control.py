import subprocess

def control_service(service: str, action: str) -> bool:
    """
    Thực thi lệnh systemctl <action> <service>.
    Trả về True nếu thành công (exit code 0), False nếu thất bại (exit code khác 0).
    """
    try:
        subprocess.run(
            ["systemctl", action, service],
            check=True, capture_output=True, text=True
        )
        return True
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Lệnh systemctl {action} {service} thất bại: {e.stderr}")
        return False