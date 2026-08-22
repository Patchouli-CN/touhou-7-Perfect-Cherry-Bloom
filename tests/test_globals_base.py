"""GlobalsBase 通用计分基座测试(engine/globals_base.py)。

分数语义与 th07 口径一致(同一实现, ZunGlobals 继承不改行为), 这里直接对
基类钉住: add_score 代码值//10 入账、guiScore 逐帧追赶(最小 1 / 上限
578910 / 追上归零)、snap 对齐、SCORE_MAX 封顶。
"""
from __future__ import annotations

import sys

sys.path.insert(0, r"D:\python_play\Touhou08")

from touhou.engine.globals_base import (  # noqa: E402
    GUI_SCORE_INCREMENT_MAX, SCORE_MAX, GlobalsBase)


def test_add_score_divides_by_10() -> None:
    g = GlobalsBase()
    g.add_score(2000)  # 代码值 2000 → 入账 200
    assert g.score == 200
    g.add_score(15)  # 整数除法
    assert g.score == 201


def test_score_capped_at_max() -> None:
    g = GlobalsBase(score=SCORE_MAX)
    g.add_score(5000)
    g.tick_gui_score()
    assert g.score == SCORE_MAX


def test_gui_score_chases_and_converges() -> None:
    g = GlobalsBase()
    g.add_score(32000)  # score = 3200
    g.tick_gui_score()
    # 第一帧 inc = 3200>>5 = 100
    assert g.gui_score == 100
    assert g.gui_score_difference == 100
    for _ in range(60):
        g.tick_gui_score()
    assert g.gui_score == g.score == 3200
    assert g.gui_score_difference == 0  # 追上归零


def test_gui_score_increment_min_one() -> None:
    g = GlobalsBase(score=1)
    g.tick_gui_score()
    assert g.gui_score == 1  # 差值>>5==0 时最小步进 1


def test_gui_score_increment_capped() -> None:
    g = GlobalsBase(score=100_000_000)
    g.tick_gui_score()
    assert g.gui_score == GUI_SCORE_INCREMENT_MAX
    assert g.gui_score_difference == GUI_SCORE_INCREMENT_MAX


def test_snap_gui_score() -> None:
    g = GlobalsBase(score=12345)
    g.snap_gui_score()
    assert g.gui_score == 12345 and g.gui_score_difference == 0


def test_default_counters() -> None:
    """通用计数字段默认值(残机/炸弹/火力/死亡/重试)。"""
    g = GlobalsBase()
    assert (g.lives_remaining, g.bombs_remaining) == (3.0, 3.0)
    assert (g.bombs_used, g.current_power, g.deaths, g.num_retries) == (0.0, 0.0, 0, 0)
