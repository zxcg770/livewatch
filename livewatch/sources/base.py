"""直播数据源抽象。

每个平台（抖音、快手、本地文件、系统音频……）实现一个 StreamSource，
负责把用户输入（URL / 文件路径）解析成 ffmpeg 可以直接读取的输入地址。
下游的音频管线、ASR、分析模块只依赖 StreamInfo，不关心平台细节。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class StreamOffline(Exception):
    """直播间当前未开播。"""


@dataclass
class StreamInfo:
    platform: str
    room_id: str
    title: str
    anchor: str
    is_live: bool
    # ffmpeg 的输入地址（http-flv / m3u8 / 本地文件路径）
    input_url: str
    # 拉流时需要附带的 HTTP 头
    headers: dict[str, str] = field(default_factory=dict)
    extra: dict = field(default_factory=dict)


class StreamSource(ABC):
    platform: str

    @classmethod
    @abstractmethod
    def match(cls, target: str) -> bool:
        """是否能处理该输入。"""

    @abstractmethod
    def resolve(self, target: str) -> StreamInfo:
        """解析出可拉流的地址。拉流地址带签名会过期，断流重连时需要重新调用。"""
