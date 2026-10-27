"""违禁词、纠错表、话术框架、数据源。"""

import json

import pytest

from livewatch.framework import framework_path, load_framework
from livewatch.rules import BannedWordChecker, apply_corrections
from livewatch.sources import DouyinSource, FileSource


def test_banned_words_longest_match_first():
    # “根治”和“根除”都在词表里；文本里的“全网最低”不能只命中更短的词
    hits = BannedWordChecker().check("全网最低价，用了根治痘痘")
    assert [h.word for h in hits] == ["全网最低", "根治"]


def test_banned_words_reported_once_per_sentence():
    assert len(BannedWordChecker().check("最便宜，真的最便宜")) == 1


def test_extra_banned_words_from_framework():
    checker = BannedWordChecker([{"word": "郑总临时加的", "category": "虚构紧迫", "severity": "high"}])
    hit = checker.check("这是郑总临时加的福利")[0]
    assert (hit.category, hit.severity) == ("虚构紧迫", "high")


def test_case_insensitive():
    assert BannedWordChecker().check("加我VX")[0].word == "VX"


def test_asr_corrections():
    assert apply_corrections("韩数浅店七天无流", {"韩数": "韩束", "浅店": "旗舰店", "无流": "无理由"}) == "韩束旗舰店七天无理由"


def test_framework_loads_with_default_thresholds():
    fw = load_framework("beauty_default")
    assert fw["thresholds"]["viewer_drop"] == 0.15
    assert len(fw["stages"]) == 8


def test_framework_name_cannot_escape_directory():
    with pytest.raises(ValueError):
        framework_path("../../etc/passwd")


def test_douyin_room_id_parsing():
    assert DouyinSource.parse_room_id("https://live.douyin.com/295178185857?from=share") == "295178185857"
    assert DouyinSource.parse_room_id("295178185857") == "295178185857"
    assert DouyinSource.match("https://live.douyin.com/1") and not DouyinSource.match("samples/a.wav")


def test_file_source_viewers_from_sidecar(tmp_path):
    wav = tmp_path / "rec.wav"
    wav.touch()
    rows = [{"t": 0, "viewers": 600}, {"t": 30.5, "viewers": 650}]
    (tmp_path / "rec.viewers.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    src = FileSource()
    # 取 t 之前最近的一次记录
    assert [src.viewers(str(wav), t) for t in (-1, 0, 30, 30.5, 999)] == [None, 600, 600, 650, 650]


def test_file_source_without_sidecar(tmp_path):
    wav = tmp_path / "old.wav"
    wav.touch()
    assert FileSource().viewers(str(wav), 10) is None
