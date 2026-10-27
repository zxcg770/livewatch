"""音频管线：用 ffmpeg 把任意数据源转成 16kHz / 单声道 / s16le PCM 流。

ASR 引擎（讯飞、SenseVoice、Paraformer 等）普遍要求这个格式，
所以录样本和实时识别共用这一个出口。

断流处理：直播拉流地址带签名、会过期，CDN 也可能中途断开，
因此 ffmpeg 退出后会重新 resolve 拿新地址再拉，直到调用方停止或主播下播。
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import time
from collections.abc import Iterator

from .sources import StreamInfo, StreamOffline, StreamSource

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # s16le
BYTES_PER_SEC = SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH
CHUNK_MS = 100
CHUNK_BYTES = BYTES_PER_SEC * CHUNK_MS // 1000

log = logging.getLogger(__name__)


def find_ffmpeg() -> str:
    """优先用系统安装的 ffmpeg；没有就用 imageio-ffmpeg 自带的（Mac / Windows / Linux 都有），
    这样 Windows 用户不用自己装 ffmpeg、配 PATH。"""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("未找到 ffmpeg：请安装 ffmpeg 并加入 PATH，或执行 uv sync 安装内置版本") from e


def ffmpeg_cmd(info: StreamInfo) -> list[str]:
    cmd = [find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin"]
    if info.input_url.startswith("http"):
        if info.headers:
            header_str = "".join(f"{k}: {v}\r\n" for k, v in info.headers.items())
            cmd += ["-headers", header_str]
        # 10 秒读不到数据视为断流，交给外层重连
        cmd += ["-rw_timeout", "10000000"]
    # 本地文件的回放节奏由 AudioStream 在 Python 侧控制：
    # 实测 ffmpeg 8 的 -readrate 对 WAV 输入不可靠（10 倍速比 6 倍速还慢）
    cmd += [
        "-i", info.input_url,
        "-vn",
        "-ac", str(CHANNELS),
        "-ar", str(SAMPLE_RATE),
        "-f", "s16le",
        "pipe:1",
    ]
    return cmd


class AudioStream:
    """可迭代的 PCM 流，每次产出 CHUNK_MS 毫秒的音频。"""

    def __init__(self, source: StreamSource, target: str, max_retries: int = 5):
        self.source = source
        self.target = target
        self.max_retries = max_retries
        self.info: StreamInfo | None = None
        self._proc: subprocess.Popen | None = None
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()

    def __iter__(self) -> Iterator[bytes]:
        retries = 0
        while not self._stop.is_set():
            try:
                self.info = self.source.resolve(self.target)
            except StreamOffline:
                log.info("直播间已下播")
                return

            self._proc = subprocess.Popen(
                ffmpeg_cmd(self.info),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            got_data = False
            assert self._proc.stdout is not None
            speed = self.info.extra.get("speed", 1.0) if self.info.extra.get("realtime") else 0
            t0, sent = time.monotonic(), 0
            while not self._stop.is_set():
                chunk = self._proc.stdout.read(CHUNK_BYTES)
                if not chunk:
                    break
                got_data = True
                retries = 0
                if speed:
                    # 按"音频时间 / 倍速"节流，模拟直播的实时到达
                    sent += len(chunk)
                    ahead = sent / BYTES_PER_SEC / speed - (time.monotonic() - t0)
                    if ahead > 0:
                        time.sleep(ahead)
                yield chunk

            self._proc.wait()
            err = self._proc.stderr.read().decode(errors="ignore").strip() if self._proc.stderr else ""
            if self._stop.is_set() or self.info.platform == "file":
                return

            retries = 0 if got_data else retries + 1
            if retries > self.max_retries:
                raise RuntimeError(f"拉流连续失败 {retries} 次: {err}")
            backoff = min(2 ** retries, 30)
            log.warning("拉流中断，%ss 后重连（%s）", backoff, err or "EOF")
            time.sleep(backoff)
