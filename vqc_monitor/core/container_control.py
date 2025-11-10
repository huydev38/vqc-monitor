import subprocess
def control_container(container_name: str, action: str):
    
    if action in ["start", "stop", "restart"]:
        try:
            subprocess.run(
                ["docker", action, container_name],
                check=True, capture_output=True, text=True
            )
        except subprocess.CalledProcessError as e:
            return {"status": "error", "message": str(e)}
        return {"status": "success"}
    else:
        return {"status": "error", "message": "Invalid action"}