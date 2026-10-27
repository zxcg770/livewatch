"""本地 HTTP + WebSocket 服务：界面通过 REST 管理直播间，通过 WebSocket 接收实时事件。

后台线程（音频 / 分析）产生事件 → loop.call_soon_threadsafe 投递到每个 WebSocket 客户端的队列。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse, Response
from pydantic import BaseModel

from .analyzer import LLMAnalyzer
from .framework import framework_path, list_frameworks, save_framework
from .session import RoomSession

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
log = logging.getLogger(__name__)

app = FastAPI(title="LiveWatch")
rooms: dict[str, RoomSession] = {}
clients: set[asyncio.Queue] = set()
analyzer = LLMAnalyzer()
_loop: asyncio.AbstractEventLoop | None = None


def broadcast(room_id: str, type_: str, data: dict) -> None:
    """可在任意线程调用。"""
    if _loop is None:
        return
    msg = {"room": room_id, "type": type_, "data": data}
    for q in list(clients):
        _loop.call_soon_threadsafe(q.put_nowait, msg)


@app.on_event("startup")
async def _startup() -> None:
    global _loop
    _loop = asyncio.get_running_loop()


class RoomReq(BaseModel):
    target: str
    framework: str = "beauty_default"
    window_min: float = 5
    interval_sec: float = 60
    speed: float = 1.0


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/config")
def config() -> dict:
    return {"llm": analyzer.enabled, "model": analyzer.model, "frameworks": list_frameworks()}


@app.get("/api/rooms")
def list_rooms() -> list[dict]:
    return [r.snapshot() for r in rooms.values()]


@app.post("/api/rooms")
def add_room(req: RoomReq) -> dict:
    target = req.target.strip()
    if not target:
        raise HTTPException(400, "请输入直播间地址或本地文件路径")
    try:
        room = RoomSession(target, req.framework, broadcast, analyzer,
                           window_min=req.window_min, interval_sec=req.interval_sec, speed=req.speed)
    except FileNotFoundError as e:
        raise HTTPException(400, f"话术框架不存在：{e}") from e
    rooms[room.id] = room
    room.start()
    return room.snapshot()


@app.post("/api/rooms/{room_id}/stop")
def stop_room(room_id: str) -> dict:
    room = rooms.get(room_id) or _404()
    room.stop()
    return {"ok": True}


@app.delete("/api/rooms/{room_id}")
def remove_room(room_id: str) -> dict:
    room = rooms.pop(room_id, None) or _404()
    room.stop()
    return {"ok": True}


@app.get("/api/rooms/{room_id}/report")
def report(room_id: str) -> dict:
    return (rooms.get(room_id) or _404()).report()


@app.get("/api/rooms/{room_id}/audio")
def audio(room_id: str, start: float, end: float) -> Response:
    room = rooms.get(room_id) or _404()
    try:
        return Response(room.audio_clip(start, end), media_type="audio/wav")
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e


@app.get("/api/frameworks/{name}", response_class=PlainTextResponse)
def get_framework(name: str) -> str:
    path = framework_path(name)
    if not path.exists():
        _404()
    return path.read_text()


class FrameworkBody(BaseModel):
    text: str


@app.put("/api/frameworks/{name}")
def put_framework(name: str, body: FrameworkBody) -> dict:
    try:
        save_framework(name, body.text)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"YAML 格式错误：{e}") from e
    return {"ok": True, "note": "新开的盯播任务生效"}


@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    await websocket.accept()
    q: asyncio.Queue = asyncio.Queue()
    clients.add(q)
    try:
        while True:
            await websocket.send_json(await q.get())
    except WebSocketDisconnect:
        pass
    finally:
        clients.discard(q)


def _404():
    raise HTTPException(404, "直播间不存在")
