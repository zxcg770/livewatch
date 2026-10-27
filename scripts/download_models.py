"""下载 SenseVoice（int8 量化，约 163MB）和 Silero VAD 模型到 models/。

Mac / Windows / Linux 通用，不依赖 curl、tar 等系统命令：
    uv run scripts/download_models.py
"""

from __future__ import annotations

import sys
import tarfile
import tempfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"
BASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models"
SENSEVOICE = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"


def download(url: str, dest: Path) -> None:
    """流式下载并显示进度；先写临时文件，下载完整再改名，中断不会留下半个文件。"""
    tmp = dest.with_suffix(dest.suffix + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=60) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        done = 0
        with tmp.open("wb") as f:
            for chunk in resp.iter_bytes(1 << 20):
                f.write(chunk)
                done += len(chunk)
                if total:
                    print(f"\r  {dest.name}: {done / total:6.1%} ({done >> 20}/{total >> 20} MB)", end="", flush=True)
    print()
    tmp.replace(dest)


def main() -> None:
    # 这个脚本不导入 livewatch 包，需要自己把控制台输出设成 UTF-8（原因见 livewatch/__init__.py）
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    MODELS.mkdir(exist_ok=True)

    vad = MODELS / "silero_vad.onnx"
    if vad.exists():
        print(f"✓ 已存在 {vad.name}")
    else:
        print("↓ 下载 Silero VAD")
        download(f"{BASE}/silero_vad.onnx", vad)

    model_dir = MODELS / SENSEVOICE
    if model_dir.is_dir():
        print(f"✓ 已存在 {SENSEVOICE}")
    else:
        print("↓ 下载 SenseVoice")
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / f"{SENSEVOICE}.tar.bz2"
            download(f"{BASE}/{SENSEVOICE}.tar.bz2", archive)
            print("  解压中…")
            with tarfile.open(archive, "r:bz2") as tar:
                # filter="data" 拒绝绝对路径、../ 和链接，防止压缩包写到 models/ 之外；
                # Python 3.11.4 之前没有这个参数
                safe = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
                tar.extractall(MODELS, **safe)

    print("模型就绪：" + "、".join(sorted(p.name for p in MODELS.iterdir())))


if __name__ == "__main__":
    try:
        main()
    except httpx.HTTPError as e:
        sys.exit(f"✗ 下载失败：{e}\n  国内网络访问 GitHub 不稳定时可重试，或手动下载后放进 models/")
