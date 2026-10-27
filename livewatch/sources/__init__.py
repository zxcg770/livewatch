from .base import StreamInfo, StreamOffline, StreamSource
from .douyin import DouyinSource
from .file import FileSource

SOURCES: list[type[StreamSource]] = [DouyinSource, FileSource]


def get_source(target: str) -> StreamSource:
    for cls in SOURCES:
        if cls.match(target):
            return cls()
    raise ValueError(f"不支持的输入: {target}")


__all__ = ["StreamInfo", "StreamOffline", "StreamSource", "DouyinSource", "FileSource", "get_source"]
