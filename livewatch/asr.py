"""语音识别：Silero VAD 切分语音段 + SenseVoice 离线识别（sherpa-onnx 本地运行）。

SenseVoice 本身是非流式模型，这里用 VAD 实现"伪流式"：
主播一停顿就切出一段送去识别，单段延迟约等于句长（实测 7–25 秒），
对于"每分钟分析一次"的场景足够。
SenseVoice 还会顺带输出情绪（HAPPY/NEUTRAL/…）和声音事件（BGM/Laughter/…）。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import sherpa_onnx

from .audio import SAMPLE_RATE

ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = ROOT / "models" / "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"
VAD_MODEL = ROOT / "models" / "silero_vad.onnx"

_TAG_RE = re.compile(r"<\|[^|]*\|>")


@dataclass
class Segment:
    start: float  # 秒，相对本场开始
    end: float
    text: str
    emotion: str = ""
    event: str = ""

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict:
        return asdict(self)


def _clean_tag(tag: str) -> str:
    return tag.strip("<|>") if tag else ""


class SenseVoiceASR:
    def __init__(self, num_threads: int = 2, max_speech: float = 12.0):
        if not MODEL_DIR.exists() or not VAD_MODEL.exists():
            raise FileNotFoundError("模型未下载，请先运行 scripts/download_models.sh")
        self.recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(MODEL_DIR / "model.int8.onnx"),
            tokens=str(MODEL_DIR / "tokens.txt"),
            num_threads=num_threads,
            language="zh",
            use_itn=True,  # 逆文本标准化：输出"99元"而不是"九十九元"，带标点
        )
        cfg = sherpa_onnx.VadModelConfig()
        cfg.silero_vad.model = str(VAD_MODEL)
        cfg.silero_vad.threshold = 0.5
        cfg.silero_vad.min_silence_duration = 0.3
        cfg.silero_vad.min_speech_duration = 0.25
        # 带货主播经常连续讲很久不停顿，希望尽量切短句；注意这是软限制，实测仍有 25 秒长句
        cfg.silero_vad.max_speech_duration = max_speech
        cfg.sample_rate = SAMPLE_RATE
        self.vad = sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=120)
        self.window = cfg.silero_vad.window_size
        self._buf = np.zeros(0, dtype=np.float32)
        self._fed = 0  # 已送入 VAD 的样本数

    def accept_pcm(self, pcm: bytes) -> list[Segment]:
        """送入 s16le PCM，返回本次新识别出的语音段。"""
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768
        self._buf = np.concatenate([self._buf, samples])
        while len(self._buf) >= self.window:
            self.vad.accept_waveform(self._buf[: self.window])
            self._buf = self._buf[self.window :]
            self._fed += self.window
        return self._drain()

    @property
    def in_speech(self) -> bool:
        """VAD 当前是否处于讲话中（最后一句尚未切出）。"""
        return self.vad.is_speech_detected()

    def flush(self) -> list[Segment]:
        self.vad.flush()
        return self._drain()

    def _drain(self) -> list[Segment]:
        out = []
        while not self.vad.empty():
            seg = self.vad.front
            stream = self.recognizer.create_stream()
            stream.accept_waveform(SAMPLE_RATE, seg.samples)
            self.recognizer.decode_stream(stream)
            r = stream.result
            text = _TAG_RE.sub("", r.text).strip()
            start = seg.start / SAMPLE_RATE
            if text:
                out.append(
                    Segment(
                        start=round(start, 2),
                        end=round(start + len(seg.samples) / SAMPLE_RATE, 2),
                        text=text,
                        emotion=_clean_tag(getattr(r, "emotion", "")),
                        event=_clean_tag(getattr(r, "event", "")),
                    )
                )
            self.vad.pop()
        return out
