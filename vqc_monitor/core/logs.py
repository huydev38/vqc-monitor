import asyncio
import re
from typing import Dict, Set, Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, HTTPException
from fastapi.responses import PlainTextResponse
from starlette.concurrency import run_in_threadpool
from asyncio.subprocess import PIPE
import shlex

# Tùy bạn lấy từ config của app (services hợp lệ)
ALLOWED_SERVICES: Set[str] = set()  # set ở startup từ config.yaml

SERVICE_RE = re.compile(r"^[\w@.\-]+\.service$")  # ví dụ: nginx.service, foo@bar.service
MAX_LINE_BYTES = 16 * 1024  # 16KB/line để tránh gửi quá dài
TAIL_DEFAULT = 200

router = APIRouter(prefix="/logs", tags=["logs"])


class LogHub:
    """Quản lý subscriber/reader cho mỗi service."""
    def __init__(self):
        self._clients: Dict[str, Set[WebSocket]] = {}
        self._tasks: Dict[str, asyncio.Task] = {}
        self._procs: Dict[str, asyncio.subprocess.Process] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, service: str, ws: WebSocket, tail: int):
        # Đăng ký client
        async with self._lock:
            self._clients.setdefault(service, set()).add(ws)
            # start follower nếu chưa có
            if service not in self._tasks:
                self._tasks[service] = asyncio.create_task(self._follow_task(service))

        # Gửi backlog 'tail' dòng gần nhất
        if tail > 0:
            cmd = ["journalctl", "-u", service, "-n", str(tail), "--no-pager", "-o", "short-iso"]
            try:
                proc = await asyncio.create_subprocess_exec(*cmd, stdout=PIPE, stderr=PIPE)
                async for raw in proc.stdout:
                    await self._send_line(ws, raw)
                await proc.wait() 
            except Exception as e:
                await ws.send_text(f"[journalctl tail error] {e}")

    async def unsubscribe(self, service: str, ws: WebSocket):
        async with self._lock:
            clients = self._clients.get(service)
            if clients and ws in clients:
                clients.remove(ws)
            if clients and len(clients) > 0:
                return
            # Không còn client: dừng task & proc
            task = self._tasks.pop(service, None)
            proc = self._procs.pop(service, None)
            self._clients.pop(service, None)
            if proc and proc.returncode is None:
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass
            if task:
                task.cancel()

    async def _follow_task(self, service: str):
        """Chạy journalctl -fu và broadcast."""
        cmd = ["journalctl", "-fu", service, "--no-pager", "-o", "short-iso"]
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(*cmd, stdout=PIPE, stderr=PIPE)
            async with self._lock:
                self._procs[service] = proc
            # Đọc dòng và phát cho mọi client
            while True:
                raw = await proc.stdout.readline()
                if not raw:
                    # journalctl kết thúc bất ngờ → thử delay nhỏ rồi thoát (task được tạo lại khi có client mới)
                    await asyncio.sleep(0.2)
                    break
                await self._broadcast(service, raw)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            await self._broadcast_text(service, f"[journalctl follow error] {e}")
        finally:
            if proc and proc.returncode is None:
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass

    async def _broadcast(self, service: str, raw: bytes):
        async with self._lock:
            clients = list(self._clients.get(service, []))
        if not clients:
            return
        # Cắt line dài & decode
        data = raw[:MAX_LINE_BYTES].decode("utf-8", errors="replace").rstrip("\n")
        # Gửi concurrent nhưng không crash toàn bộ nếu 1 socket lỗi
        await asyncio.gather(*(self._safe_send(ws, data) for ws in clients), return_exceptions=True)

    async def _broadcast_text(self, service: str, msg: str):
        async with self._lock:
            clients = list(self._clients.get(service, []))
        await asyncio.gather(*(self._safe_send(ws, msg) for ws in clients), return_exceptions=True)

    async def _send_line(self, ws: WebSocket, raw: bytes):
        data = raw[:MAX_LINE_BYTES].decode("utf-8", errors="replace").rstrip("\n")
        await self._safe_send(ws, data)

    async def _safe_send(self, ws: WebSocket, text: str):
        try:
            await ws.send_text(text)
        except Exception:
            # client có thể đã đóng
            pass


hub = LogHub()


def _validate_service(name: str) -> str:
    # Chuẩn hóa & validate
    name = name.strip()
    if not name.endswith(".service"):
        name += ".service"
    if not SERVICE_RE.match(name):
        raise HTTPException(400, "Invalid service name")
    if ALLOWED_SERVICES and name not in ALLOWED_SERVICES:
        raise HTTPException(403, "Service not allowed")
    return name


# @router.get("/services")
# def list_allowed_services():
#     # tiện cho FE
#     return sorted(ALLOWED_SERVICES) if ALLOWED_SERVICES else []



