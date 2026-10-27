"""从直播间录制音频样本，用于离线开发 ASR 和分析模块。

用法：
    uv run scripts/record.py https://live.douyin.com/295178185857 --minutes 30
    uv run scripts/record.py 295178185857 --minutes 1 --out samples/test.wav
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import time
import wave
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from livewatch.audio import BYTES_PER_SEC, CHANNELS, SAMPLE_RATE, SAMPLE_WIDTH, AudioStream  # noqa: E402
from livewatch.sources import StreamOffline, get_source  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="录制直播间音频（16kHz 单声道 WAV）")
    ap.add_argument("target", help="直播间 URL / 房间号 / 本地文件")
    ap.add_argument("--minutes", type=float, default=30, help="录制时长（分钟）")
    ap.add_argument("--out", type=Path, help="输出路径，默认 samples/<房间号>_<时间>.wav")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    source = get_source(args.target)
    try:
        info = source.resolve(args.target)
    except StreamOffline as e:
        sys.exit(f"✗ {e}")

    print(f"▶ {info.anchor} | {info.title} | 在线 {info.extra.get('online', '-')}")

    started = datetime.now()
    out: Path = args.out or Path("samples") / f"{info.room_id}_{started:%Y%m%d_%H%M%S}.wav"
    out.parent.mkdir(parents=True, exist_ok=True)

    stream = AudioStream(source, args.target)
    signal.signal(signal.SIGINT, lambda *_: stream.stop())

    target_bytes = int(args.minutes * 60 * BYTES_PER_SEC)
    written = 0
    last_print = 0.0
    with wave.open(str(out), "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(SAMPLE_RATE)
        for chunk in stream:
            wf.writeframes(chunk)
            written += len(chunk)
            now = time.monotonic()
            if now - last_print >= 5:
                last_print = now
                print(f"\r● 已录制 {written / BYTES_PER_SEC:6.1f}s / {args.minutes * 60:.0f}s", end="", flush=True)
            if written >= target_bytes:
                stream.stop()
                break

    duration = written / BYTES_PER_SEC
    meta = {
        "platform": info.platform,
        "room_id": info.room_id,
        "anchor": info.anchor,
        "title": info.title,
        "online": info.extra.get("online"),
        "started_at": started.isoformat(timespec="seconds"),
        "duration_sec": round(duration, 1),
        "sample_rate": SAMPLE_RATE,
    }
    out.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    print(f"\n✓ 已保存 {out}（{duration:.1f}s）")


if __name__ == "__main__":
    main()
