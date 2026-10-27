"""单个直播间的盯播会话：拉流 → ASR → 实时规则 → 定时分析 → 告警，全部事件通过 emit 回调推给界面。

时间基准统一用"音频时间"（已收到的音频秒数），而不是墙上时钟：
- 直播和文件回放（包括倍速回放）用同一套调度逻辑；
- 断流重连期间时间不前进，不会凭空产生"冷场"。
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from . import metrics as metrics_mod
from .analyzer import LLMAnalyzer
from .asr import Segment, SenseVoiceASR
from .audio import BYTES_PER_SEC, AudioStream
from .framework import load_framework
from .rules import BannedWordChecker, apply_corrections
from .sources import FileSource, get_source

log = logging.getLogger(__name__)
SESSIONS_DIR = Path(__file__).resolve().parent.parent / "sessions"

Emit = Callable[[str, str, dict], None]


def _bigrams(text: str) -> set[str]:
    chars = [c for c in text if c.isalnum()]
    return {chars[i] + chars[i + 1] for i in range(len(chars) - 1)}


def _similar(a: str, b: str, threshold: float = 0.6) -> bool:
    """两段原文是否在说同一件事：短的那段有 60% 以上的字符二元组出现在长的那段里。

    AI 每次引用的片段长短不一，用包含度而不是 Jaccard，部分引用也能匹配上。
    """
    ga, gb = _bigrams(a), _bigrams(b)
    if not ga or not gb:
        return a == b
    return len(ga & gb) / min(len(ga), len(gb)) >= threshold


class RoomSession:
    def __init__(
        self,
        target: str,
        framework: str,
        emit: Emit,
        analyzer: LLMAnalyzer,
        window_min: float = 5,
        interval_sec: float = 60,
        speed: float = 1.0,
    ):
        self.id = uuid.uuid4().hex[:8]
        self.target = target
        self.framework_name = framework
        self.fw = load_framework(framework)
        self.emit_raw = emit
        self.analyzer = analyzer
        self.window = window_min * 60
        self.interval = interval_sec
        self.speed = speed

        self.status = "starting"
        self.info: dict = {}
        self.audio_sec = 0.0
        self.segments: list[Segment] = []
        self.alerts: list[dict] = []
        self.analyses: list[dict] = []
        self.minute_metrics: list[dict] = []
        self._lock = threading.Lock()
        self._stream: AudioStream | None = None
        self._pool = ThreadPoolExecutor(max_workers=1)
        self._analyzing = False
        self.dir = SESSIONS_DIR / f"{datetime.now():%Y%m%d_%H%M%S}_{self.id}"

    # ---------- 生命周期 ----------
    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True, name=f"room-{self.id}").start()

    def stop(self) -> None:
        self.status = "stopping"
        if self._stream:
            self._stream.stop()

    def snapshot(self) -> dict:
        return {
            "id": self.id,
            "target": self.target,
            "framework": self.framework_name,
            "status": self.status,
            "info": self.info,
            "audio_sec": round(self.audio_sec, 1),
            "window_min": self.window / 60,
            "interval_sec": self.interval,
            "speed": self.speed,
            "llm": self.analyzer.enabled,
            "thresholds": self.fw["thresholds"],
            "segments": [s.to_dict() for s in self.segments[-200:]],
            "alerts": self.alerts[-100:],
            "analyses": self.analyses[-1:],
            "minute_metrics": self.minute_metrics,
        }

    def emit(self, type_: str, data: dict) -> None:
        self.emit_raw(self.id, type_, data)

    def _save(self, name: str, obj: dict) -> None:
        with (self.dir / name).open("a") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    # ---------- 主循环（音频线程） ----------
    def _run(self) -> None:
        try:
            source = get_source(self.target)
            if isinstance(source, FileSource):
                source.speed = self.speed
            si = source.resolve(self.target)
            self.info = {"platform": si.platform, "room_id": si.room_id, "anchor": si.anchor,
                         "title": si.title, "online": si.extra.get("online", "")}
            self.dir.mkdir(parents=True, exist_ok=True)
            (self.dir / "meta.json").write_text(json.dumps(
                {**self.info, "target": self.target, "framework": self.framework_name,
                 "started_at": datetime.now().isoformat(timespec="seconds")}, ensure_ascii=False, indent=2))
            self.status = "running"
            self.emit("status", self.snapshot())

            asr = SenseVoiceASR()
            checker = BannedWordChecker(self.fw["extra_banned"])
            self._stream = AudioStream(source, self.target)
            next_tick = self.interval
            last_clock = 0.0

            for chunk in self._stream:
                self.audio_sec += len(chunk) / BYTES_PER_SEC
                for seg in asr.accept_pcm(chunk):
                    self._on_segment(seg, checker)
                if self.audio_sec >= next_tick:
                    self._schedule_analysis(self.audio_sec, open_tail=asr.in_speech)
                    next_tick += self.interval
                now = time.monotonic()
                if now - last_clock > 0.5:
                    last_clock = now
                    self.emit("clock", {"audio_sec": round(self.audio_sec, 1)})

            for seg in asr.flush():
                self._on_segment(seg, checker)
            self._schedule_analysis(self.audio_sec, final=True)
            self._pool.shutdown(wait=True)
            self.status = "ended"
        except Exception as e:  # noqa: BLE001
            log.exception("会话异常")
            self.status = "error"
            self.info["error"] = str(e)
        self.emit("status", self.snapshot())

    def _on_segment(self, seg: Segment, checker: BannedWordChecker) -> None:
        seg.text = apply_corrections(seg.text, self.fw["asr_corrections"])
        with self._lock:
            self.segments.append(seg)
        self._save("segments.jsonl", seg.to_dict())
        hits = checker.check(seg.text)
        self.emit("segment", {**seg.to_dict(), "hits": [h.word for h in hits]})
        for h in hits:
            self._raise_alert({
                "code": f"ban:{h.word}", "source": "rule", "severity": h.severity,
                "title": f"违禁词「{h.word}」", "detail": f"{h.category}：{h.reason}",
                "quote": seg.text, "suggestion": f"避免使用「{h.word}」，换成有依据的客观描述",
            }, seg.end)

    # ---------- 定时分析（分析线程） ----------
    def _schedule_analysis(self, t: float, final: bool = False, open_tail: bool = False) -> None:
        if self._analyzing and not final:
            log.warning("上一次分析尚未完成，跳过 t=%.0f", t)
            return
        self._analyzing = True
        self._pool.submit(self._analyze, t, open_tail)

    def _analyze(self, t_end: float, open_tail: bool = False) -> None:
        try:
            t_start = max(0.0, t_end - self.window)
            with self._lock:
                window_segs = [s for s in self.segments if s.end > t_start and s.start < t_end]
                all_segs = list(self.segments)
            th = self.fw["thresholds"]
            # 表达指标看最近一个周期（及时、且不会因 5 分钟窗口重叠而重复报警）
            m_min = metrics_mod.compute(all_segs, max(0.0, t_end - self.interval), t_end, th, open_tail)
            m_win = metrics_mod.compute(all_segs, t_start, t_end, th, open_tail)
            self.minute_metrics.append({"t": round(t_end, 1), **m_min})
            self._save("metrics.jsonl", {"t": t_end, "minute": m_min, "window": m_win})
            self.emit("metrics", {"t": round(t_end, 1), "minute": m_min, "window": m_win})
            for f in m_min["flags"]:
                self._raise_alert({**f, "source": "metric", "quote": ""}, t_end)

            if not self.analyzer.enabled:
                return
            if not window_segs:
                return
            result = self.analyzer.analyze(self.fw, window_segs, m_win, t_start)
            result["t"] = round(t_end, 1)
            result["window"] = [round(t_start, 1), round(t_end, 1)]
            self.analyses.append(result)
            self._save("analyses.jsonl", result)
            self.emit("analysis", result)
            for issue in result.get("issues", []):
                self._raise_alert({**issue, "source": "llm"}, t_end)
        except Exception as e:  # noqa: BLE001
            log.exception("分析失败")
            self.emit("error", {"message": str(e), "t": round(t_end, 1)})
        finally:
            self._analyzing = False

    # ---------- 告警去重 ----------
    def _raise_alert(self, alert: dict, t: float) -> None:
        """同一问题在一个分析窗口内只告警一次，再次出现只累加次数。

        5 分钟窗口每分钟滑动一次，同一段讲话会被分析 5 次，不去重会刷屏。
        "同一问题"的判断：
        - 违禁词 / 表达指标 / 缺失类（没有原文证据）：问题编码相同即可；
        - AI 合规类问题（事件型）：编码相同且证据相似才算重复。只按编码合并会把
          "保价三年三倍退款""虚构央视报道"这类新风险当成旧问题吞掉；
        - AI 内容 / 缺失 / 节奏类问题（状态型）：按编码合并，否则主播一直逼单会每分钟报一次。
        两种极端在 30 分钟样本上都实测过：只按编码 30 条告警但漏掉新风险，全按证据 83 条太吵。
        """
        code = alert.get("code") or alert.get("title", "")
        quote = (alert.get("quote") or "").strip()
        with self._lock:
            # 跨层合并：AI 报的问题若引用的正是某条违禁词告警的原句，视为 AI 确认，不再新建
            if alert.get("source") == "llm" and quote:
                twin = next((a for a in self._recent(t) if a["source"] == "rule"
                             and a.get("quote") and _similar(quote, a["quote"])), None)
                if twin:
                    twin["ai_confirmed"] = True
                    twin["suggestion"] = alert.get("suggestion") or twin.get("suggestion")
                    self.emit("alert_update", {"id": twin["id"], "ai_confirmed": True,
                                               "suggestion": twin["suggestion"]})
                    return
            # 合规类是"事件型"：每个新的虚假说法都要单独提醒；
            # 内容 / 缺失 / 节奏类是"状态型"：持续逼单、一直没报价，换句话说仍是同一个问题
            by_evidence = alert.get("source") == "llm" and bool(quote) and code.startswith("compliance:")
            prev = next((a for a in self._recent(t) if a["code"] == code
                         and (not by_evidence or any(_similar(quote, q) for q in a["quotes"]))), None)
            if prev:
                prev["count"] += 1
                prev["last_t"] = round(t, 1)
                if quote and quote not in prev["quotes"]:
                    prev["quotes"].append(quote)
                self.emit("alert_update", {"id": prev["id"], "count": prev["count"], "last_t": prev["last_t"]})
                return
            item = {**alert, "id": uuid.uuid4().hex[:8], "code": code, "t": round(t, 1),
                    "last_t": round(t, 1), "count": 1, "quotes": [quote] if quote else []}
            self.alerts.append(item)
        self._save("alerts.jsonl", item)
        self.emit("alert", item)

    def _recent(self, t: float) -> list[dict]:
        """一个分析窗口内仍活跃的告警，新的在前。"""
        return [a for a in reversed(self.alerts) if t - a["last_t"] < self.window]

    # ---------- 场次报告 ----------
    def report(self) -> dict:
        with self._lock:
            alerts = list(self.alerts)
            segs = list(self.segments)
        overall = metrics_mod.compute(segs, 0, max(self.audio_sec, 1), self.fw["thresholds"])
        by_source: dict[str, int] = {}
        for a in alerts:
            by_source[a["source"]] = by_source.get(a["source"], 0) + 1
        scores = [a.get("score") for a in self.analyses if isinstance(a.get("score"), int)]
        top = sorted(alerts, key=lambda a: ({"high": 0, "medium": 1, "low": 2}.get(a["severity"], 3), -a["count"]))
        return {
            "info": self.info,
            "duration": round(self.audio_sec),
            "overall": overall,
            "alert_total": len(alerts),
            "alert_by_source": by_source,
            "score_avg": round(sum(scores) / len(scores)) if scores else None,
            "score_trend": [{"t": a["t"], "score": a.get("score")} for a in self.analyses],
            "rate_trend": [{"t": m["t"], "rate": m["rate_cpm"]} for m in self.minute_metrics],
            "top_alerts": top[:10],
        }
