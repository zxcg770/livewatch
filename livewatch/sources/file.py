"""本地音视频文件作为数据源：用于离线开发、回归测试和演示（直播间未开播时的备用方案）。"""

from __future__ import annotations

import bisect
import json
from pathlib import Path

from .base import StreamInfo, StreamSource


class FileSource(StreamSource):
    platform = "file"

    def __init__(self, speed: float = 1.0):
        # 回放倍速：演示时可加速播放录制样本
        self.speed = speed
        self._viewers: tuple[list[float], list[int]] | None = None

    @classmethod
    def match(cls, target: str) -> bool:
        return Path(target).is_file()

    def resolve(self, target: str) -> StreamInfo:
        path = Path(target).resolve()
        return StreamInfo(
            platform=self.platform,
            room_id=path.stem,
            title=path.name,
            anchor="",
            is_live=True,
            input_url=str(path),
            # 按真实时间速度读取，模拟直播
            extra={"realtime": True, "speed": self.speed},
        )

    def viewers(self, target: str, t: float) -> int | None:
        """读录制时同步保存的 <录音名>.viewers.jsonl，取 t 之前最近的一次人数。"""
        if self._viewers is None:
            ts: list[float] = []
            vs: list[int] = []
            side = Path(target).with_suffix(".viewers.jsonl")
            if side.is_file():
                for line in side.read_text().splitlines():
                    if line.strip():
                        row = json.loads(line)
                        ts.append(row["t"])
                        vs.append(row["viewers"])
            self._viewers = (ts, vs)
        ts, vs = self._viewers
        i = bisect.bisect_right(ts, t) - 1
        return vs[i] if i >= 0 else None
