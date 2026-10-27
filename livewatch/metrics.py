"""表达质量指标：全部本地确定性计算，不交给大模型。

便宜、稳定、零延迟，结果可解释（"语速 412 字/分"比"语速偏快"更有说服力），
大模型只拿这些数字做参考，不负责计算。
"""

from __future__ import annotations

import re
from collections import Counter

from .asr import Segment

_HAN_RE = re.compile(r"[一-鿿A-Za-z0-9]")
FILLERS = ["那个", "然后", "就是", "对吧", "这个这个", "嗯", "呃"]


def count_chars(text: str) -> int:
    """只数汉字 / 字母 / 数字，不计标点。"""
    return len(_HAN_RE.findall(text))


def compute(segments: list[Segment], start: float, end: float, thresholds: dict, open_tail: bool = False) -> dict:
    """open_tail：VAD 此刻仍检测到人声，即最后一句还没切出来，末尾空白算"讲话中"而不是冷场。"""
    span = max(end - start, 1e-6)
    segs = [s for s in segments if s.end > start and s.start < end]
    overlaps = [min(s.end, end) - max(s.start, start) for s in segs]
    # 跨窗口边界的句子按时长比例分摊字数，否则语速会虚高
    chars = round(sum(count_chars(s.text) * o / max(s.duration, 1e-6) for s, o in zip(segs, overlaps)))
    rate_speech = sum(overlaps)
    text = "".join(s.text for s in segs)

    # 最长冷场：相邻语音段之间（含窗口首尾）的最大空白
    edges = [start] + [x for s in segs for x in (max(s.start, start), min(s.end, end))] + [end]
    gaps = [edges[i + 1] - edges[i] for i in range(0, len(edges) - 1, 2)]
    tail = gaps.pop() if open_tail and gaps else 0.0
    max_gap = max(gaps) if gaps else (0.0 if open_tail else span)
    speech = rate_speech + tail

    fillers = {w: text.count(w) for w in FILLERS if text.count(w)}
    filler_total = sum(fillers.values())

    # 重复度：8 字片段的重复占比，带货中适度重复正常，过高说明车轱辘话
    plain = "".join(_HAN_RE.findall(text))
    grams = Counter(plain[i : i + 8] for i in range(max(len(plain) - 7, 0)))
    repeated = sum(c for c in grams.values() if c > 1)
    top_repeat = [g for g, c in grams.most_common(3) if c > 2]

    emotions = Counter(s.emotion for s in segs if s.emotion)
    m = {
        "start": round(start, 1),
        "end": round(end, 1),
        "chars": chars,
        "speech_sec": round(speech, 1),
        "talk_ratio": round(speech / span, 2),
        "rate_cpm": round(chars / rate_speech * 60) if rate_speech > 1 else 0,
        "max_gap": round(max_gap, 1),
        "fillers": fillers,
        "filler_per_100": round(filler_total / chars * 100, 1) if chars else 0,
        "repeat_ratio": round(repeated / max(len(grams), 1), 2),
        "top_repeat": top_repeat,
        "emotions": dict(emotions),
    }
    m["flags"] = check(m, thresholds)
    return m


def check(m: dict, t: dict) -> list[dict]:
    """把指标对照阈值转成问题列表（code 用于去重）。"""
    flags = []
    if m["rate_cpm"] > t["rate_fast"]:
        flags.append({"code": "expr:rate_fast", "severity": "medium", "title": "语速过快",
                      "detail": f"{m['rate_cpm']} 字/分，超过 {t['rate_fast']}",
                      "suggestion": "放慢语速，关键信息（价格、赠品）一字一顿讲清楚"})
    elif 0 < m["rate_cpm"] < t["rate_slow"]:
        flags.append({"code": "expr:rate_slow", "severity": "low", "title": "语速过慢",
                      "detail": f"{m['rate_cpm']} 字/分，低于 {t['rate_slow']}",
                      "suggestion": "提高节奏，减少拖音和停顿"})
    if m["max_gap"] > t["silence_gap"]:
        flags.append({"code": "expr:silence", "severity": "high", "title": "出现冷场",
                      "detail": f"最长 {m['max_gap']} 秒无人讲话",
                      "suggestion": "马上接话：抛福利、点名互动或继续讲产品"})
    if m["talk_ratio"] < t["talk_ratio_min"]:
        flags.append({"code": "expr:low_talk", "severity": "medium", "title": "讲话占比低",
                      "detail": f"讲话时长仅占 {m['talk_ratio']:.0%}",
                      "suggestion": "减少空档，保持持续讲解"})
    if m["filler_per_100"] > t["filler_per_100"]:
        top = "、".join(list(m["fillers"])[:3])
        flags.append({"code": "expr:filler", "severity": "low", "title": "口头禅偏多",
                      "detail": f"每百字 {m['filler_per_100']} 次（{top}）",
                      "suggestion": "减少口头禅，用停顿代替"})
    return flags
