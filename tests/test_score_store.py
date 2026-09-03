"""score_store 持久化层测试: JSON 往返/容错/Top10/catk/CLRD/PSCR/PLST。"""

from __future__ import annotations

import sys

import msgspec
import pytest

sys.path.insert(0, r"D:\python_play\Touhou08")

from touhou.engine.score_store import (  # noqa: E402
    CATK_CAP,
    ScoreStore,
    default_score,
    make_highscore_record,
)

SPELLCARD_COUNT = 141  # th07 符卡总数(引擎层已参数化, 本测试按 th07 口径构造)


def _rec(score: int, **kw) -> dict:
    kw.setdefault("character", 0)
    kw.setdefault("difficulty", 1)
    kw.setdefault("stage", 1)
    return make_highscore_record(score, **kw)


# ---- JSON 往返 ----


def test_json_roundtrip(tmp_path) -> None:
    s = ScoreStore(spellcard_count=SPELLCARD_COUNT)
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
    s = ScoreStore.load(tmp_path / "nope.json", spellcard_count=SPELLCARD_COUNT)
    assert s.highscores == {} and len(s.catk) == SPELLCARD_COUNT


@pytest.mark.parametrize(
    "content",
    [
        "",  # 空文件
        "{not json",  # JSON 语法损坏
        "[]",  # 顶层类型不对
        '{"highscores": 42}',  # 字段类型不对
        '{"highscores": {"1,0": [{"score": "abc"}]}}',  # 记录字段类型不对
        '{"catk": [{"name": 1}]}',  # catk 缺字段
    ],
)
def test_load_corrupted_falls_back_to_default(tmp_path, content) -> None:
    p = tmp_path / "score.json"
    p.write_text(content, encoding="utf-8")
    s = ScoreStore.load(p, spellcard_count=SPELLCARD_COUNT)
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
        encoding="utf-8",
    )
    s = ScoreStore.load(p)
    assert [r["score"] for r in s.entries(1, 0)] == [50000]  # 坏记录丢弃
    assert s.plst["play_count"] == 7
    assert s.plst["total_frames"] == 0  # 类型不对回退默认


# ---- Top10 ----


def test_insert_sorted_descending() -> None:
    s = ScoreStore(spellcard_count=SPELLCARD_COUNT)
    assert s.insert_score(_rec(300)) == 0
    assert s.insert_score(_rec(100)) == 1
    assert s.insert_score(_rec(200)) == 1
    assert [r["score"] for r in s.entries(1, 0)] == [300, 200, 100]


def test_insert_tie_new_record_first() -> None:
    """LinkScore: 同分时新记录排在旧记录前。"""
    s = ScoreStore(spellcard_count=SPELLCARD_COUNT)
    s.insert_score(_rec(200, name="OLD"))
    s.insert_score(_rec(200, name="NEW"))
    assert [r["name"] for r in s.entries(1, 0)] == ["NEW", "OLD"]


def test_insert_full_list_evicts_lowest() -> None:
    s = ScoreStore(spellcard_count=SPELLCARD_COUNT)
    for i in range(10):
        s.insert_score(_rec(1000 + i))
    assert len(s.entries(1, 0)) == 10
    assert s.insert_score(_rec(500)) == -1  # 比榜尾低, 不进榜
    assert s.insert_score(_rec(10000)) == 0  # 新榜首, 挤出原榜尾
    scores = [r["score"] for r in s.entries(1, 0)]
    assert scores[0] == 10000 and scores[-1] == 1001 and len(scores) == 10


def test_insert_boundary_against_defaults() -> None:
    """空榜默认分 100000-k*10000: 低于默认榜尾(10000)不入展示位。"""
    s = ScoreStore(spellcard_count=SPELLCARD_COUNT)
    # 对纯空榜: 任何正分都进真实榜(默认位只是展示补齐)
    assert s.insert_score(_rec(1)) == 0
    assert default_score(0) == 100000 and default_score(9) == 10000
    disp = s.display_entries(1, 0)
    assert disp[0]["score"] == 1 and disp[1]["score"] == 90000
    assert len(disp) == 10


def test_high_score_floor_100000() -> None:
    """GetHighScore: 空榜/低分底线 100000。"""
    s = ScoreStore(spellcard_count=SPELLCARD_COUNT)
    assert s.high_score(1, 0) == 100000
    s.insert_score(_rec(50000))
    assert s.high_score(1, 0) == 100000
    s.insert_score(_rec(200000))
    assert s.high_score(1, 0) == 200000


def test_boards_are_per_difficulty_character() -> None:
    s = ScoreStore(spellcard_count=SPELLCARD_COUNT)
    s.insert_score(_rec(111, difficulty=0, character=2))
    s.insert_score(_rec(222, difficulty=1, character=0))
    assert [r["score"] for r in s.entries(0, 2)] == [111]
    assert [r["score"] for r in s.entries(1, 0)] == [222]
    assert s.entries(1, 2) == []


# ---- catk ----


def test_catk_attempt_and_success() -> None:
    s = ScoreStore(spellcard_count=SPELLCARD_COUNT)
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
    s = ScoreStore(spellcard_count=SPELLCARD_COUNT)
    s.catk[0]["attempts"][0] = CATK_CAP
    s.record_spellcard_attempt(0, "x", 0)
    assert s.catk[0]["attempts"][0] == CATK_CAP  # 封顶不再 ++
    s.record_spellcard_attempt(-1, "x", 0)  # 越界忽略
    s.record_spellcard_attempt(SPELLCARD_COUNT, "x", 0)
    s.record_spellcard_success(999, 0, 1)


# ---- CLRD / PSCR / PLST ----


def test_clrd_records_max_stage() -> None:
    s = ScoreStore(spellcard_count=SPELLCARD_COUNT)
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
    s = ScoreStore(spellcard_count=SPELLCARD_COUNT)
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
    s = ScoreStore(spellcard_count=SPELLCARD_COUNT)
    assert s.lsnm is None and s.last_name == "PLAYER"


def test_lsnm_roundtrip(tmp_path) -> None:
    """set_last_name 登记 LSNM; JSON 往返保留(截断 8 字符)。"""
    s = ScoreStore(spellcard_count=SPELLCARD_COUNT)
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
    s = ScoreStore(spellcard_count=SPELLCARD_COUNT)
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


# ---- 参数化口径(作品专属形状由构造参数注入, 引擎不硬编码) ----


def _th08_store() -> ScoreStore:
    """th08 口径: 222 卡 × 13 槽(shotType 轴 + SHOT_ALL 合计)双组 catk、
    clrd 13 行 × 5 难度(值语义引擎不解释, 位掩码见 games/th08/progress.py)。"""
    return ScoreStore(
        spellcard_count=222,
        num_characters=13,
        num_difficulties=5,
        catk_slot_count=13,
        catk_practice_group=True,
    )


def test_th08_shape_defaults() -> None:
    s = _th08_store()
    assert len(s.catk) == 222
    assert len(s.catk[0]["attempts"]) == 13
    assert s.catk[0]["practice"]["attempts"] == [0] * 13
    assert len(s.clrd) == 13
    assert all(len(r["with_retries"]) == 5 for r in s.clrd)
    assert s.plst["bgmUnlocked"] == [0] * 32


def test_default_shape_unchanged() -> None:
    """不传参数 → 旧默认形状(7 槽单组 catk / 6×6 clrd / bgmUnlocked[32])。"""
    s = ScoreStore(spellcard_count=SPELLCARD_COUNT)
    assert len(s.catk[0]["attempts"]) == 7
    assert "practice" not in s.catk[0]
    assert len(s.clrd) == 6 and len(s.clrd[0]["with_retries"]) == 6


def test_catk_total_slot_and_practice_group() -> None:
    """合计槽 = catk_slot_count-1; practice=True 记账进 practice 组, 两组独立。"""
    s = _th08_store()
    s.record_spellcard_attempt(0, "test", 3)
    s.record_spellcard_attempt(0, "test", 3, practice=True)
    e = s.catk[0]
    assert e["attempts"][3] == 1 and e["attempts"][12] == 1
    assert e["practice"]["attempts"][3] == 1 and e["practice"]["attempts"][12] == 1
    s.record_spellcard_success(0, 3, 5000, practice=True)
    assert e["successes"][3] == 0  # in-game 组不受 practice 入账影响
    assert e["practice"]["successes"][3] == 1
    assert e["practice"]["highscore"][12] == 5000


def test_practice_attempt_without_group_is_silent() -> None:
    """未配置 practice 组: practice=True 只记名字, 组缺失静默不炸。"""
    s = ScoreStore(spellcard_count=SPELLCARD_COUNT)
    s.record_spellcard_attempt(0, "x", 0, practice=True)
    assert s.catk[0]["attempts"][0] == 0
    assert s.catk[0]["name"] == "x"
    s.record_spellcard_success(0, 0, 100, practice=True)
    assert s.catk[0]["successes"][0] == 0


def test_record_clear_uses_configured_character_count() -> None:
    """取模按注入的 num_characters: 13 行口径下 12 是合法行(不混进 0)。"""
    s = _th08_store()
    s.record_clear(12, 2, 5, 0)
    assert s.clrd[12]["with_retries"][2] == 5
    assert s.clrd[0]["with_retries"][2] == 0
    s.record_clear(13, 2, 5, 0)  # 越界取模回 0(取模行为本身不变)
    assert s.clrd[0]["with_retries"][2] == 5


def test_bgm_unlocked_roundtrip_and_fallback(tmp_path) -> None:
    s = _th08_store()
    s.plst["bgmUnlocked"][18] = 1
    p = tmp_path / "score.json"
    s.save(p)
    s2 = ScoreStore.load(
        p,
        num_characters=13,
        num_difficulties=5,
        catk_slot_count=13,
        catk_practice_group=True,
    )
    assert s2.plst["bgmUnlocked"][18] == 1
    # 坏字段(槽数不对)回退全零
    raw = msgspec.json.decode(p.read_bytes())
    raw["plst"]["bgmUnlocked"] = [1] * 5
    p.write_bytes(msgspec.json.encode(raw))
    s3 = ScoreStore.load(p, num_characters=13, num_difficulties=5, catk_slot_count=13)
    assert s3.plst["bgmUnlocked"] == [0] * 32


def test_th08_slot_mismatch_falls_back(tmp_path) -> None:
    """旧形状数据在新口径下读: 7 槽 catk 条目/6 列 clrd 行校验不过, 回退默认。"""
    s = _th08_store()
    s.catk[0]["attempts"][1] = 9
    s.clrd[0]["with_retries"][1] = 0x67
    p = tmp_path / "score.json"
    s.save(p)
    raw = msgspec.json.decode(p.read_bytes())
    raw["catk"][0]["attempts"] = [1] * 7
    raw["clrd"][0]["with_retries"] = [1] * 6
    p.write_bytes(msgspec.json.encode(raw))
    s2 = ScoreStore.load(
        p,
        num_characters=13,
        num_difficulties=5,
        catk_slot_count=13,
        catk_practice_group=True,
    )
    assert s2.catk[0]["attempts"] == [0] * 13  # 整条回退默认
    assert s2.clrd[0]["with_retries"][1] == 0  # 6 列过不了 5 列校验


def test_corrupt_practice_group_falls_back_group_only(tmp_path) -> None:
    """practice 组单独损坏: 只回退该组, 条目主体保留。"""
    s = _th08_store()
    s.catk[0]["attempts"][1] = 5
    s.catk[0]["practice"]["attempts"][1] = 7
    p = tmp_path / "score.json"
    s.save(p)
    raw = msgspec.json.decode(p.read_bytes())
    raw["catk"][0]["practice"] = {"attempts": [1]}  # 槽数错误
    p.write_bytes(msgspec.json.encode(raw))
    s2 = ScoreStore.load(
        p,
        num_characters=13,
        num_difficulties=5,
        catk_slot_count=13,
        catk_practice_group=True,
    )
    assert s2.catk[0]["attempts"][1] == 5  # 主组保留
    assert s2.catk[0]["practice"]["attempts"] == [0] * 13  # 组回退默认


def test_load_grows_catk_to_archive_length(tmp_path) -> None:
    """读档 spellcard_count 小于存档条数 → 按存档扩容(读档不丢卡)。"""
    s = _th08_store()
    s.catk[221]["attempts"][0] = 3
    p = tmp_path / "score.json"
    s.save(p)
    s2 = ScoreStore.load(p, catk_slot_count=13, catk_practice_group=True)
    assert len(s2.catk) == 222
    assert s2.catk[221]["attempts"][0] == 3
