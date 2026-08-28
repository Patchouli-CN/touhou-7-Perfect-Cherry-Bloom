"""Touhou: 结算/评级测试。"""

from __future__ import annotations

import sys

sys.path.insert(0, r"D:\python_play\Touhou08")

from touhou.games.th07.results import (  # noqa: E402
    DIFFICULTY_WEIGHTS,
    RunStats,
    ScoreRecord,
    TopList,
    rating,
)


def test_default_rating_starts_low() -> None:
    st = RunStats()
    # 默认(score=0, Normal): score 段 -20 + 难度 -10 + 死亡/炸弹各自 +10 → 约 -10
    r = rating(st)
    assert r < 0 and r > -100


def test_higher_score_raises_rating() -> None:
    low = rating(RunStats(score=1_000_000))
    high = rating(RunStats(score=3_000_000))
    assert high > low


def test_multiple_spellcards_boost() -> None:
    one = rating(RunStats(spellcards_captured=1, difficulty=1))
    two = rating(RunStats(spellcards_captured=2, difficulty=1))
    assert two > one


def test_cleared_gets_bonus() -> None:
    no = rating(RunStats())
    yes = rating(RunStats(cleared=True))
    assert yes > no + 50


def test_extreme_slow_is_cheat() -> None:
    st = RunStats(score=100_000_000)
    assert rating(st, slow_percent=80) == -999.0


def test_toplist_insert_sorts() -> None:
    tl = TopList()
    tl.insert(ScoreRecord(score=999999, character=0, difficulty=1, stage=1))
    tl.insert(ScoreRecord(score=500000, character=0, difficulty=1, stage=1))
    tl.insert(ScoreRecord(score=2000000, character=0, difficulty=1, stage=1))
    top = tl.top(3)
    assert [r.score for r in top] == [2000000, 999999, 500000]


def test_toplist_caps_at_10() -> None:
    tl = TopList()
    for i in range(15):
        tl.insert(ScoreRecord(score=i * 1000, character=0, difficulty=1, stage=1))
    assert len(tl) == 10


def test_difficulty_weights_shape() -> None:
    # Easy 最低, 逐渐升到 Extra
    assert DIFFICULTY_WEIGHTS == (-30, -10, 20, 30, 30)
