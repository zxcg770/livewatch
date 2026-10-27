"""话术框架加载。框架是 YAML 文件，运营可在界面中直接编辑。"""

from __future__ import annotations

from pathlib import Path

import yaml

FRAMEWORK_DIR = Path(__file__).resolve().parent.parent / "config" / "frameworks"

DEFAULT_THRESHOLDS = {
    "rate_fast": 380,
    "rate_slow": 160,
    "silence_gap": 8,
    "talk_ratio_min": 0.5,
    "filler_per_100": 4,
}


def list_frameworks() -> list[str]:
    return sorted(p.stem for p in FRAMEWORK_DIR.glob("*.yaml"))


def framework_path(name: str) -> Path:
    path = (FRAMEWORK_DIR / f"{name}.yaml").resolve()
    if path.parent != FRAMEWORK_DIR.resolve():
        raise ValueError("非法的框架名")
    return path


def load_framework(name: str) -> dict:
    data = yaml.safe_load(framework_path(name).read_text()) or {}
    data["thresholds"] = {**DEFAULT_THRESHOLDS, **(data.get("thresholds") or {})}
    data.setdefault("stages", [])
    data.setdefault("rules", [])
    data.setdefault("asr_corrections", {})
    data.setdefault("extra_banned", [])
    return data


def save_framework(name: str, text: str) -> None:
    yaml.safe_load(text)  # 先校验，格式错误直接抛出
    framework_path(name).write_text(text)
