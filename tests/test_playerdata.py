"""Touhou: Player Data(Result 画面)导航与数据装配测试。"""
from __future__ import annotations

import sys

sys.path.insert(0, r"D:\python_play\Touhou08")

from touhou.games.th07 import playerdata  # noqa: E402
from touhou.engine.score_store import ScoreStore, make_highscore_record  # noqa: E402
from touhou.games.th07.view.screens import (  # noqa: E402
    PLAYERDATA_SECTIONS,
    MenuAction,
    PlayerDataFlow,
    TitleFlow,
    practice_max_stage,
)


# ---------------------------------------------------------------------------
# TitleFlow 入口
# ---------------------------------------------------------------------------

def _goto(flow, item):
    while flow.cursor.current != item:
        flow.handle(MenuAction.DOWN)


def test_flow_player_data_emits() -> None:
    f = TitleFlow()
    _goto(f, "Player Data")
    assert f.handle(MenuAction.CONFIRM) == {"action": "player_data"}


def test_flow_practice_emits() -> None:
    f = TitleFlow()
    _goto(f, "Practice Start")
    assert f.handle(MenuAction.CONFIRM) == {"action": "practice"}


# ---------------------------------------------------------------------------
# PlayerDataFlow 翻页导航
# ---------------------------------------------------------------------------

def test_playerdata_difficulty_wrap() -> None:
    f = PlayerDataFlow()
    assert f.difficulty == 1  # 默认 Normal
    assert f.handle(MenuAction.UP) is None
    assert f.difficulty == 0
    f.handle(MenuAction.UP)
    assert f.difficulty == 5  # 回绕到 Phantasm
    f.handle(MenuAction.DOWN)
    assert f.difficulty == 0


def test_playerdata_character_wrap() -> None:
    f = PlayerDataFlow()
    assert f.handle(MenuAction.LEFT) is None
    assert f.character == 5   # 回绕到 SakuyaB
    f.handle(MenuAction.RIGHT)
    assert f.character == 0


def test_playerdata_section_cycle_and_quit() -> None:
    f = PlayerDataFlow()
    for i in range(1, len(PLAYERDATA_SECTIONS) + 1):
        assert f.handle(MenuAction.CONFIRM) is None
        assert f.section == i % len(PLAYERDATA_SECTIONS)
    assert f.handle(MenuAction.BACK) == {"action": "quit"}


# ---------------------------------------------------------------------------
# practice_max_stage(MainMenu.cpp:1912-1926 clrd 解锁)
# ---------------------------------------------------------------------------

def test_practice_max_stage_clamp() -> None:
    s = ScoreStore()
    assert practice_max_stage(s, 0, 1) == 1       # 无记录 → 只能 Stage 1
    s.record_clear(0, 1, 3, 0)                    # 无续关到过 3 面
    assert practice_max_stage(s, 0, 1) == 3
    assert practice_max_stage(s, 0, 0) == 1       # 别的难度不受影响
    assert practice_max_stage(s, 2, 1) == 1       # 别的机体不受影响
    s.clrd[1]["without_retries"][1] = 99
    assert practice_max_stage(s, 1, 1) == 6       # >=99 → 6
    s.clrd[3]["without_retries"][2] = 8
    assert practice_max_stage(s, 3, 2) == 6       # 超 6 截断


def test_practice_max_stage_bad_input() -> None:
    assert practice_max_stage(ScoreStore(), 99, 99) == 1
    assert practice_max_stage(object(), 0, 0) == 1


# ---------------------------------------------------------------------------
# 数据装配(games/th07/playerdata.py, 空记录不炸)
# ---------------------------------------------------------------------------

def test_highscore_rows_defaults_when_empty() -> None:
    rows = playerdata.highscore_rows(ScoreStore(), 1, 0)
    assert len(rows) == 10
    assert rows[0]["score"] == 100000             # GetHighScore 底线
    assert rows[9]["score"] == 10000
    assert all(r["name"] == "--------" for r in rows)


def test_highscore_rows_real_records_first() -> None:
    s = ScoreStore()
    s.insert_score(make_highscore_record(500000, 0, 1, 6))
    s.insert_score(make_highscore_record(200000, 0, 1, 2))
    rows = playerdata.highscore_rows(s, 1, 0)
    assert [r["score"] for r in rows[:2]] == [500000, 200000]
    assert rows[2]["score"] == 80000              # 默认空位补齐


def test_spellcard_page_empty() -> None:
    page = playerdata.spellcard_page(ScoreStore(), 0)
    assert page["attempted"] == 0 and page["attempts"] == 0
    assert page["cards"] == []


def test_spellcard_page_counts_and_total() -> None:
    s = ScoreStore(spellcard_count=141)  # 141 = th07 符卡数
    s.record_spellcard_attempt(0, "霜符「Frost Columns」", 0)
    s.record_spellcard_attempt(0, "霜符「Frost Columns」", 0)
    s.record_spellcard_success(0, 0, 123456)
    s.record_spellcard_attempt(5, "Test Card", 1)   # 别的机体
    page = playerdata.spellcard_page(s, 0)
    assert page["attempted"] == 1 and page["captured"] == 1
    assert (page["attempts"], page["successes"]) == (2, 1)
    assert page["cards"][0]["name"].startswith("霜符")
    total = playerdata.spellcard_page(s, 6)         # 合计页
    assert total["attempted"] == 2 and total["attempts"] == 3


def test_play_stats_assembly() -> None:
    s = ScoreStore()
    s.record_play(0, 1)
    s.record_run_end(0, 1, score=1000, frames=3600, cleared=True, num_retries=2)
    s.record_clear(0, 1, 4, 0)
    st = playerdata.play_stats(s)
    assert st["play_count"] == 1
    assert st["clear_count"] == 1
    assert st["retry_count"] == 2
    assert st["play_seconds"] == 60.0
    assert st["clrd"][0]["without_retries"][1] == 4
    assert st["clrd"][1]["without_retries"][1] == 0
