"""本地音视频文件作为数据源：用于离线开发、回归测试和演示（直播间未开播时的备用方案）。"""

from __future__ import annotations

from pathlib import Path

from .base import StreamInfo, StreamSource


class FileSource(StreamSource):
    platform = "file"

    def __init__(self, speed: float = 1.0):
        # 回放倍速：演示时可加速播放录制样本
        self.speed = speed

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
