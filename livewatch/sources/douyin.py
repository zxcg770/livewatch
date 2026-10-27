"""抖音直播间解析。

流程：
1. 访问 live.douyin.com 获取游客 cookie（ttwid），接口不带它会返回空数据。
2. 调用 webcast/room/web/enter 接口，用直播间号（web_rid）换取房间信息和拉流地址。
3. 只需要音频，所以优先选最低清晰度，节省带宽。
"""

from __future__ import annotations

import re

import httpx

from .base import StreamInfo, StreamOffline, StreamSource

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
ENTER_API = "https://live.douyin.com/webcast/room/web/enter/"
# 低清晰度在前：音频码率各档相同，视频越小越省带宽
QUALITY_ORDER = ["SD2", "SD1", "HD1", "FULL_HD1"]
STATUS_LIVE = 2

_RID_RE = re.compile(r"live\.douyin\.com/(\d+)")


class DouyinSource(StreamSource):
    platform = "douyin"

    def __init__(self, timeout: float = 15.0):
        self._client = httpx.Client(
            headers={"User-Agent": UA, "Referer": "https://live.douyin.com/"},
            follow_redirects=True,
            timeout=timeout,
        )

    @classmethod
    def match(cls, target: str) -> bool:
        return bool(_RID_RE.search(target)) or target.isdigit()

    @staticmethod
    def parse_room_id(target: str) -> str:
        if target.isdigit():
            return target
        m = _RID_RE.search(target)
        if not m:
            raise ValueError(f"无法从输入中解析抖音直播间号: {target}")
        return m.group(1)

    def _ensure_cookie(self) -> None:
        if "ttwid" not in self._client.cookies:
            self._client.get("https://live.douyin.com/")

    def resolve(self, target: str) -> StreamInfo:
        rid = self.parse_room_id(target)
        self._ensure_cookie()
        params = {
            "aid": "6383",
            "app_name": "douyin_web",
            "live_id": "1",
            "device_platform": "web",
            "language": "zh-CN",
            "enter_from": "web_live",
            "cookie_enabled": "true",
            "screen_width": "1920",
            "screen_height": "1080",
            "browser_language": "zh-CN",
            "browser_platform": "MacIntel",
            "browser_name": "Chrome",
            "browser_version": "124.0.0.0",
            "web_rid": rid,
        }
        resp = self._client.get(ENTER_API, params=params)
        resp.raise_for_status()
        payload = resp.json().get("data") or {}
        rooms = payload.get("data") or []
        if not rooms:
            raise RuntimeError(f"接口未返回房间数据（可能被风控），room={rid}")

        room = rooms[0]
        anchor = (payload.get("user") or {}).get("nickname", "")
        is_live = room.get("status") == STATUS_LIVE
        if not is_live:
            raise StreamOffline(f"{anchor or rid} 当前未开播")

        flv = (room.get("stream_url") or {}).get("flv_pull_url") or {}
        input_url = next((flv[q] for q in QUALITY_ORDER if q in flv), None)
        if not input_url:
            raise RuntimeError("未找到可用的拉流地址")

        return StreamInfo(
            platform=self.platform,
            room_id=rid,
            title=room.get("title", ""),
            anchor=anchor,
            is_live=True,
            input_url=input_url,
            headers={"User-Agent": UA, "Referer": "https://live.douyin.com/"},
            extra={"online": room.get("user_count_str", "")},
        )
