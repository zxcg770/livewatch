"""告警去重、AI 引用定位、音频切片。只测会话里的纯逻辑，不拉流、不调 AI。"""

import io
import wave

import pytest

from livewatch import session as session_mod
from livewatch.asr import Segment
from livewatch.audio import BYTES_PER_SEC
from livewatch.session import RoomSession, _locate, _similar


class NoAI:
    enabled = False


@pytest.fixture
def room(tmp_path, monkeypatch):
    monkeypatch.setattr(session_mod, "SESSIONS_DIR", tmp_path)
    events = []
    r = RoomSession("samples/x.wav", "beauty_default", lambda rid, t, d: events.append((t, d)), NoAI())
    r.dir.mkdir(parents=True)
    r.events = events
    return r


def alert(code, quote="", source="llm", **kw):
    return {"code": code, "source": source, "severity": "medium", "title": code, "quote": quote, **kw}


# ---------- 证据相似度 ----------
def test_similar_partial_quote():
    # AI 每次引用的长短不一：短的一段大部分出现在长的一段里就算同一件事
    assert _similar("保价三年三倍退款", "姐妹们放心，保价三年三倍退款，直接拍")
    assert not _similar("保价三年三倍退款", "央视都报道过的大品牌")


# ---------- 去重 ----------
def test_state_issue_merges_by_code(room):
    # 状态型（持续逼单）：换一句话说仍是同一个问题，只累加次数
    room._raise_alert(alert("content:pressure", "最后三单了啊"), 60)
    room._raise_alert(alert("content:pressure", "没抢到的赶紧拍"), 120)
    assert len(room.alerts) == 1
    assert room.alerts[0]["count"] == 2
    assert room.alerts[0]["quotes"] == ["最后三单了啊", "没抢到的赶紧拍"]


def test_compliance_issue_with_new_evidence_is_new_alert(room):
    # 事件型（合规）：每个新的虚假说法都要单独提醒，不能被旧告警吞掉
    room._raise_alert(alert("compliance:false", "保价三年三倍退款"), 60)
    room._raise_alert(alert("compliance:false", "央视都报道过我们"), 120)
    room._raise_alert(alert("compliance:false", "保价三年，三倍退款"), 180)
    assert len(room.alerts) == 2
    assert room.alerts[0]["count"] == 2


def test_same_issue_alerts_again_after_window(room):
    room._raise_alert(alert("content:pressure", "最后三单"), 60)
    room._raise_alert(alert("content:pressure", "最后三单"), 60 + room.window + 1)
    assert len(room.alerts) == 2


def test_ai_confirms_rule_alert_instead_of_duplicating(room):
    quote = "用了它皱纹松弛全部改善"
    room._raise_alert(alert("ban:全部改善", quote, source="rule"), 50)
    room._raise_alert(alert("compliance:efficacy", quote, suggestion="改成有助于改善"), 60)
    assert len(room.alerts) == 1
    assert room.alerts[0]["ai_confirmed"] is True
    assert room.alerts[0]["suggestion"] == "改成有助于改善"


# ---------- AI 引用定位（告警回听） ----------
SEGS = [
    Segment(0.0, 30.0, "面霜买一送二直接发三套是不是但只有三单了啊然后这个券每个人只能领一张"),
    Segment(30.0, 40.0, "你看我们第三代多牛皱纹可以减少百分之十七"),
]


def test_locate_quote_inside_long_sentence():
    # 一句 30 秒，引用只是其中几个字：按字的位置换算时间，而不是回听整句
    start, end = _locate("但只有三单了啊", SEGS)
    assert 10 < start < 20 and end - start < 12


def test_locate_quote_across_sentences():
    start, end = _locate("只能领一张你看我们第三代多牛", SEGS)
    assert start < 30 < end


def test_locate_paraphrased_quote_returns_none():
    assert _locate("主播承诺无效全额退款", SEGS) is None
    assert _locate("", SEGS) is None


# ---------- 音频切片 ----------
def test_audio_clip_is_valid_wav_aligned_to_samples(room):
    (room.dir / "audio.pcm").write_bytes(bytes(range(256)) * (BYTES_PER_SEC * 3 // 256))
    data = room.audio_clip(1.00003, 2.0)  # 起点换算成字节是奇数，必须对齐到采样
    with wave.open(io.BytesIO(data)) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2
        frames = w.readframes(w.getnframes())
    offset = int(1.00003 * BYTES_PER_SEC) // 2 * 2
    assert frames[:4] == (bytes(range(256)) * 400)[offset % 256 : offset % 256 + 4]
    assert abs(len(frames) - BYTES_PER_SEC) <= 2


def test_audio_clip_capped_at_30s(room):
    (room.dir / "audio.pcm").write_bytes(b"\0" * BYTES_PER_SEC * 60)
    with wave.open(io.BytesIO(room.audio_clip(0, 60))) as w:
        assert w.getnframes() == 16000 * 30
