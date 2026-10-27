"""离线转写一段音频，输出带时间戳的 JSONL，并统计识别速度（RTF）。

用法：
    uv run scripts/transcribe.py samples/xxx.wav --minutes 3
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from livewatch.asr import SenseVoiceASR  # noqa: E402
from livewatch.audio import BYTES_PER_SEC, CHUNK_BYTES  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("wav", type=Path)
    ap.add_argument("--minutes", type=float, default=0, help="只转写前 N 分钟，0 表示全部")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    asr = SenseVoiceASR()
    out = args.out or args.wav.with_suffix(".jsonl")
    limit = int(args.minutes * 60 * BYTES_PER_SEC) if args.minutes else None

    t0 = time.perf_counter()
    read = 0
    segs = []
    with wave.open(str(args.wav)) as wf:
        while True:
            pcm = wf.readframes(CHUNK_BYTES // 2)
            if not pcm or (limit and read >= limit):
                break
            read += len(pcm)
            for s in asr.accept_pcm(pcm):
                segs.append(s)
                print(f"[{s.start:7.1f}-{s.end:7.1f}] {s.emotion:>8} {s.event:>6} | {s.text}")
    segs += asr.flush()
    cost = time.perf_counter() - t0
    audio_sec = read / BYTES_PER_SEC

    with out.open("w", encoding="utf-8") as f:
        for s in segs:
            f.write(json.dumps(s.to_dict(), ensure_ascii=False) + "\n")
    chars = sum(len(s.text) for s in segs)
    print(f"\n音频 {audio_sec:.0f}s，耗时 {cost:.1f}s，RTF={cost / audio_sec:.3f}，{len(segs)} 段 {chars} 字 → {out}")


if __name__ == "__main__":
    main()
