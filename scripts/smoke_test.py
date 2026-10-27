"""冒烟测试：不调 AI，回放一段录音，检查整条链路在当前系统上能跑通。

在 CI 的 Windows / macOS 机器上运行，也可以本地运行：
    uv run scripts/smoke_test.py samples/demo.wav

特意把录音复制到带中文和空格的目录里再回放，覆盖“路径含中文”的情况。
"""

from __future__ import annotations

import json
import locale
import os
import platform
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from livewatch import session as session_mod  # noqa: E402
from livewatch.audio import find_ffmpeg  # noqa: E402
from livewatch.server import app  # noqa: E402
from livewatch.session import RoomSession  # noqa: E402


class NoAI:
    enabled = False


def check(cond: bool, msg: str) -> None:
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        sys.exit(1)


def main() -> None:
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "samples/demo.wav")
    env = f"{platform.platform()} | Python {platform.python_version()} | 默认编码 {locale.getpreferredencoding(False)}"
    ffmpeg = find_ffmpeg()
    print(f"系统 {env}")
    print(f"ffmpeg: {ffmpeg}")

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "盯播 测试"
        work.mkdir()
        wav = work / "样本 录音.wav"
        shutil.copy(src, wav)
        side = src.with_suffix(".viewers.jsonl")
        if side.exists():
            shutil.copy(side, wav.with_suffix(".viewers.jsonl"))
        session_mod.SESSIONS_DIR = work / "sessions"

        print("1. 界面服务")
        client = TestClient(app)
        check(client.get("/").status_code == 200, "首页可访问")
        check("beauty_default" in client.get("/api/config").json()["frameworks"], "话术框架可加载")

        print("2. 回放（ffmpeg → VAD → SenseVoice → 指标 → 告警）")
        s = RoomSession(str(wav), "beauty_default", lambda *a: None, NoAI(), interval_sec=30, speed=20)
        t0 = time.monotonic()
        s.start()
        while s.status not in ("ended", "error") and time.monotonic() - t0 < 300:
            time.sleep(0.5)
        check(s.status == "ended", f"会话正常结束（{s.status} {s.info.get('error', '')}），音频 {s.audio_sec:.0f} 秒，用时 {time.monotonic() - t0:.0f} 秒")
        check(len(s.segments) > 0, f"转写 {len(s.segments)} 句，首句：{s.segments[0].text[:20] if s.segments else ''}")
        check(len(s.minute_metrics) > 0, f"指标 {len(s.minute_metrics)} 个周期，语速 {[m['rate_cpm'] for m in s.minute_metrics]}")
        if side.exists():
            check(all(m.get("viewers") for m in s.minute_metrics), f"在线人数 {[m['viewers'] for m in s.minute_metrics]}")

        print("3. 落盘与回听")
        for name in ("meta.json", "segments.jsonl", "metrics.jsonl"):
            text = (s.dir / name).read_bytes().decode("utf-8")
            check(bool(text), f"{name} 是 UTF-8")
        meta = json.loads((s.dir / "meta.json").read_text(encoding="utf-8"))
        check(meta["title"] == wav.name, f"中文文件名原样保存：{meta['title']}")
        clip = s.audio_clip(1, 3)
        check(clip[:4] == b"RIFF" and len(clip) > 60000, f"回听切片 {len(clip)} 字节")

    print("全部通过")
    # GitHub Actions 的日志要登录才能看，注释（annotation）公开可见：把关键结果写成注释，方便核对
    if os.environ.get("GITHUB_ACTIONS"):
        which = "内置 imageio-ffmpeg" if "imageio_ffmpeg" in ffmpeg else "系统 PATH"
        print(f"::notice title=冒烟测试通过 ({platform.system()})::{env} | ffmpeg 来自{which} | "
              f"回放 {s.audio_sec:.0f} 秒，转写 {len(s.segments)} 句，首句「{s.segments[0].text[:15]}」 | "
              f"语速 {[m['rate_cpm'] for m in s.minute_metrics]} | 在线人数 {[m.get('viewers') for m in s.minute_metrics]} | "
              f"中文路径：{wav.parent.name}/{wav.name}")


if __name__ == "__main__":
    main()
