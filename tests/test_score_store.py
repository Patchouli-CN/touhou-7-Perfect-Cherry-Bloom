"""score_store 持久化层测试: JSON 往返/容错/Top10/catk/CLRD/PSCR/PLST。"""
from __future__ import annotations

import sys

import pytest

sys.path.insert(0, r"D:\python_play\Touhou08")

from touhou.engine.score_store import (  # noqa: E402
    CATK_CAP,
    SPELLCARD_COUNT,
    ScoreStore,
    default_score,
    make_highscore_record,
)


def _rec(score: int, **kw) -> dict:
    kw.setdefault("character", 0)
    kw.setdefault("difficulty", 1)
    kw.setdefault("stage", 1)
    return make_highscore_record(score, **kw)


# ---- JSON 往返 ----

def test_json_roundtrip(tmp_path) -> None:
    s = ScoreStore()
    assert s.insert_score(_rec(123456)) == 0
    s.record_spellcard_attempt(3, "测试符卡", 0)
    s.record_spellcard_success(3, 0, 200000)
    s.record_clear(0, 1, 1, 0)
    s.record_play(0, 1)
    s.record_run_end(0, 1, score=123456, frames=9000, cleared=True, num_retries=0)
    p = tmp_path / "score.json"
    s.save(p)
    s2 = ScoreStore.load(p)
    assert s2.to_dict() == s.to_dict()
    assert s2.entries(1, 0)[0]["score"] == 123456
    assert s2.catk[3]["attempts"] == [1, 0, 0, 0, 0, 0, 1]
    assert s2.catk[3]["successes"][6] == 1
    assert s2.catk[3]["highscore"][0] == 200000


def test_load_missing_file_returns_default(tmp_path) -> None:
    s = ScoreStore.load(tmp_path / "nope.json")
    assert s.highscores == {} and len(s.catk) == SPELLCARD_COUNT


@pytest.mark.parametrize("content", [
    "",                        # 空文件
    "{not json",               # JSON 语法损坏
    "[]",                      # 顶层类型不对
    '{"highscores": 42}',      # 字段类型不对
    '{"highscores": {"1,0": [{"score": "abc"}]}}',  # 记录字段类型不对
    '{"catk": [{"name": 1}]}', # catk 缺字段
])
def test_load_corrupted_falls_back_to_default(tmp_path, content) -> None:
    p = tmp_path / "score.json"
    p.write_text(content, encoding="utf-8")
    s = ScoreStore.load(p)
    # 不炸, 坏字段回退默认; 顶层合法时好字段仍保留
    assert isinstance(s, ScoreStore)
    assert len(s.catk) == SPELLCARD_COUNT


def test_load_partial_keeps_good_fields(tmp_path) -> None:
    p = tmp_path / "score.json"
    p.write_text(
        '{"highscores": {"1,0": ['
        + __import__("json").dumps(_rec(50000, date="2026-01-01T00:00:00+00:00"))
        + ', {"score": -1}], "bad": []},'
        ' "plst": {"play_count": 7, "total_frames": "x"}}',
        encoding="utf-8")
    s = ScoreStore.load(p)
    assert [r["score"] for r in s.entries(1, 0)] == [50000]  # 坏记录丢弃
    assert s.plst["play_count"] == 7
    assert s.plst["total_frames"] == 0  # 类型不对回退默认


# ---- Top10 ----

def test_insert_sorted_descending() -> None:
    s = ScoreStore()
    assert s.insert_score(_rec(300)) == 0
    assert s.insert_score(_rec(100)) == 1
    assert s.insert_score(_rec(200)) == 1
    assert [r["score"] for r in s.entries(1, 0)] == [300, 200, 100]


def test_insert_tie_new_record_first() -> None:
    """LinkScore: 同分时新记录排在旧记录前。"""
    s = ScoreStore()
    s.insert_score(_rec(200, name="OLD"))
    s.insert_score(_rec(200, name="NEW"))
    assert [r["name"] for r in s.entries(1, 0)] == ["NEW", "OLD"]


def test_insert_full_list_evicts_lowest() -> None:
    s = ScoreStore()
    for i in range(10):
        s.insert_score(_rec(1000 + i))
    assert len(s.entries(1, 0)) == 10
    assert s.insert_score(_rec(500)) == -1      # 比榜尾低, 不进榜
    assert s.insert_score(_rec(10000)) == 0     # 新榜首, 挤出原榜尾
    scores = [r["score"] for r in s.entries(1, 0)]
    assert scores[0] == 10000 and scores[-1] == 1001 and len(scores) == 10


def test_insert_boundary_against_defaults() -> None:
    """空榜默认分 100000-k*10000: 低于默认榜尾(10000)不入展示位。"""
    s = ScoreStore()
    # 对纯空榜: 任何正分都进真实榜(默认位只是展示补齐)
    assert s.insert_score(_rec(1)) == 0
    assert default_score(0) == 100000 and default_score(9) == 10000
    disp = s.display_entries(1, 0)
    assert disp[0]["score"] == 1 and disp[1]["score"] == 90000
    assert len(disp) == 10


def test_high_score_floor_100000() -> None:
    """GetHighScore: 空榜/低分底线 100000。"""
    s = ScoreStore()
    assert s.high_score(1, 0) == 100000
    s.insert_score(_rec(50000))
    assert s.high_score(1, 0) == 100000
    s.insert_score(_rec(200000))
    assert s.high_score(1, 0) == 200000


def test_boards_are_per_difficulty_character() -> None:
    s = ScoreStore()
    s.insert_score(_rec(111, difficulty=0, character=2))
    s.insert_score(_rec(222, difficulty=1, character=0))
    assert [r["score"] for r in s.entries(0, 2)] == [111]
    assert [r["score"] for r in s.entries(1, 0)] == [222]
    assert s.entries(1, 2) == []


# ---- catk ----

def test_catk_attempt_and_success() -> None:
    s = ScoreStore()
    s.record_spellcard_attempt(10, "卡名", 2)
    s.record_spellcard_attempt(10, "卡名", 2)
    s.record_spellcard_success(10, 2, 150000)
    e = s.catk[10]
    assert e["name"] == "卡名"
    assert e["attempts"][2] == 2 and e["attempts"][6] == 2
    assert e["successes"][2] == 1 and e["successes"][6] == 1
    assert e["highscore"][2] == 150000 and e["highscore"][6] == 150000
    # highscore 只取 max
    s.record_spellcard_success(10, 2, 100000)
    assert s.catk[10]["highscore"][2] == 150000


def test_catk_cap_and_bounds() -> None:
    s = ScoreStore()
    s.catk[0]["attempts"][0] = CATK_CAP
    s.record_spellcard_attempt(0, "x", 0)
    assert s.catk[0]["attempts"][0] == CATK_CAP  # 封顶不再 ++
    s.record_spellcard_attempt(-1, "x", 0)       # 越界忽略
    s.record_spellcard_attempt(SPELLCARD_COUNT, "x", 0)
    s.record_spellcard_success(999, 0, 1)


# ---- CLRD / PSCR / PLST ----

def test_clrd_records_max_stage() -> None:
    s = ScoreStore()
    s.record_clear(0, 1, 1, num_retries=0)
    assert s.clrd[0]["with_retries"][1] == 1
    assert s.clrd[0]["without_retries"][1] == 1
    s.record_clear(0, 1, 1, num_retries=2)
    # C++ quirk: 有续关时 with_retries 不更新, without_retries 照常
    assert s.clrd[0]["with_retries"][1] == 1
    # 取 max 不累加
    s.record_clear(0, 1, 1, num_retries=0)
    assert s.clrd[0]["with_retries"][1] == 1


def test_pscr_plst_counters() -> None:
    s = ScoreStore()
    s.record_play(0, 1)
    s.record_play(0, 1)
    s.record_play(3, 2)
    assert s.pscr["1,0"]["play_count"] == 2
    assert s.plst["play_count"] == 3
    s.record_run_end(0, 1, score=80000, frames=5000, cleared=True, num_retries=1)
    s.record_run_end(0, 1, score=60000, frames=3000, cleared=False, num_retries=0)
    assert s.pscr["1,0"]["highscore"] == 80000  # 取 max
    assert s.plst["total_frames"] == 8000
    assert s.plst["clear_count"] == 1
    assert s.plst["retry_count"] == 1


def test_default_name_and_date() -> None:
    r = _rec(100)
    assert r["name"] == "PLAYER" and len(r["name"]) <= 9
    assert "T" in r["date"]  # ISO 格式


# ---- LSNM(上次输入的名字)与入榜改名 ----

def test_last_name_defaults_to_default_name() -> None:
    """从未输入过 → last_name = DEFAULT_NAME(原版 LSNM 缺省 8 空格, 本期 PLAYER)。"""
    s = ScoreStore()
    assert s.lsnm is None and s.last_name == "PLAYER"


def test_lsnm_roundtrip(tmp_path) -> None:
    """set_last_name 登记 LSNM; JSON 往返保留(截断 8 字符)。"""
    s = ScoreStore()
    s.set_last_name("ZUN")
    assert s.lsnm == "ZUN" and s.last_name == "ZUN"
    s.set_last_name("TOOLONGNAME99")
    assert s.lsnm == "TOOLONGN"
    p = tmp_path / "score.json"
    s.save(p)
    s2 = ScoreStore.load(p)
    assert s2.lsnm == "TOOLONGN" and s2.last_name == "TOOLONGN"
    # 坏字段回退 None(不炸)
    p.write_text('{"lsnm": 42}', encoding="utf-8")
    assert ScoreStore.load(p).lsnm is None


def test_set_entry_name_renames_ranked_record() -> None:
    """名字输入完成 → 改写榜上第 rank 条的名字(原地改, 同原版改 curScore.name)。"""
    s = ScoreStore()
    assert s.insert_score(_rec(300, name="OLD")) == 0
    assert s.insert_score(_rec(100, name="OLD")) == 1
    s.set_entry_name(1, 0, 1, "NEW")
    assert [r["name"] for r in s.entries(1, 0)] == ["OLD", "NEW"]
    # 越界/无榜静默忽略
    s.set_entry_name(1, 0, 5, "X")
    s.set_entry_name(0, 0, 0, "X")
    assert [r["name"] for r in s.entries(1, 0)] == ["OLD", "NEW"]


def test_make_highscore_record_accepts_name() -> None:
    r = make_highscore_record(100, 0, 1, 1, name="REIMU")
    assert r["name"] == "REIMU"
    r = make_highscore_record(100, 0, 1, 1, name="TOOLONGNAME99")
    assert r["name"] == "TOOLONGNA"  # 原版 name[9] 截断
