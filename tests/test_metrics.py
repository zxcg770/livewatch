"""表达指标和在线人数下跌检测。"""

from livewatch.asr import Segment
from livewatch.framework import DEFAULT_THRESHOLDS as TH
from livewatch.metrics import compute, count_chars, viewer_drop


def seg(start, end, text):
    return Segment(start, end, text)


def test_count_chars_ignores_punctuation():
    assert count_chars("买一送二，直接发 3 套！OK") == 11  # 汉字 8 + 数字 1 + 字母 2，不计标点和空格


def test_rate_counts_speech_time_only():
    # 60 秒窗口里讲了 30 秒、150 个字：语速按讲话时长算是 300 字/分，不是 150
    m = compute([seg(0, 30, "字" * 150)], 0, 60, TH)
    assert m["rate_cpm"] == 300
    assert m["talk_ratio"] == 0.5


def test_sentence_across_window_edge_is_split_by_duration():
    # 一句 20 秒、100 字，只有后一半落在窗口里：只算 50 字。全算会把语速虚高一倍
    m = compute([seg(50, 70, "字" * 100)], 60, 120, TH)
    assert m["chars"] == 50


def test_max_gap_and_where_it_starts():
    m = compute([seg(0, 20, "字" * 80), seg(32, 60, "字" * 100)], 0, 60, TH)
    assert m["max_gap"] == 12
    assert m["max_gap_at"] == 20
    assert any(f["code"] == "expr:silence" for f in m["flags"])


def test_open_tail_is_not_silence():
    # 最后一句还没被 VAD 切出来（主播仍在讲），末尾空白不能算冷场
    segs = [seg(0, 40, "字" * 200)]
    assert compute(segs, 0, 60, TH)["max_gap"] == 20
    assert compute(segs, 0, 60, TH, open_tail=True)["max_gap"] == 0


def test_fast_rate_flag():
    m = compute([seg(0, 60, "字" * 450)], 0, 60, TH)
    assert [f["code"] for f in m["flags"]] == ["expr:rate_fast"]


def minutes(*viewers):
    return [{"t": 60.0 * (i + 1), "viewers": v} for i, v in enumerate(viewers)]


def test_viewer_drop_compares_with_recent_peak():
    # 每分钟只掉几个百分点，逐分钟比较永远不报；和 3 分钟内的最高点比，累计跌了 18%
    h = minutes(1000, 950, 900, 820)
    d = viewer_drop(h, 240.0, TH["viewer_drop"])
    assert d and d["code"] == "viewers:drop"
    assert d["peak_t"] == 60.0
    assert "1000" in d["detail"] and "820" in d["detail"]
    assert d["severity"] == "medium"


def test_viewer_drop_severity_high_when_double_threshold():
    d = viewer_drop(minutes(1000, 600), 120.0, TH["viewer_drop"])
    assert d["severity"] == "high"


def test_viewer_drop_ignores_small_or_old_changes():
    assert viewer_drop(minutes(1000, 900), 120.0, 0.15) is None          # 只跌 10%
    assert viewer_drop(minutes(100, 82), 120.0, 0.15) is None            # 18% 但只少了 18 人
    assert viewer_drop(minutes(1000, 900, 900, 900, 800), 300.0, 0.15) is None  # 最高点已超出 3 分钟


def test_viewer_drop_without_data():
    assert viewer_drop([{"t": 60.0, "viewers": None}, {"t": 120.0}], 120.0, 0.15) is None
    assert viewer_drop(minutes(1000), 60.0, 0.15) is None
