"""Touhou: 全局状态(ZunGlobals)与 rng 测试。"""
from __future__ import annotations

import sys

sys.path.insert(0, r"D:\python_play\Touhou08")

from touhou.games.th07.globals import (  # noqa: E402
    CHERRY_MAX_RANGE,
    CHERRY_PLUS_RANGE,
    GUI_SCORE_INCREMENT_MAX,
    SCORE_MAX,
    STATUS_CHERRY_MAX,
    STATUS_FULL_POWER,
    ZunGlobals,
)
from touhou.engine.rng import Rng  # noqa: E402
from touhou.utils import Vec2  # noqa: E402


# ---- rng: 与 Rng::GetRandomU16 (TH07 0x00431870) 递推一致 ----
def test_rng_u16_matches_cpp_recurrence() -> None:
    # 手算(seed=0x5EED):
    #  u1=(0x5EED^0x9630)-0x6553=0xC8DD-0x6553=0x638A → (0x638A&0xC000)>>14=1
    #    seed=(1+0x638A*4)&0xFFFF=0x8E29
    #  u2=(0x8E29^0x9630)-0x6553=0x1819-0x6553=-19770→&0xFFFF=0xB2C6 → >>14位=2
    #    seed=(2+0xB2C6*4)&0xFFFF=0xCB1A
    #  u3=(0xCB1A^0x9630)-0x6553=0x5D2A-0x6553=-2089→&0xFFFF=0xF7D7 → >>14位=3
    #    seed=(3+0xF7D7*4)&0xFFFF=0xDF5F
    r = Rng(0x5EED)
    assert [r.u16(), r.u16(), r.u16()] == [0x8E29, 0xCB1A, 0xDF5F]
    assert r.gen == 3


def test_rng_seed_zero() -> None:
    # u=(0^0x9630)-0x6553=0x30DD, 高两位=0 → seed=0x30DD*4=0xC374
    assert Rng(0).u16() == 0xC374


def test_rng_u32_is_two_u16() -> None:
    assert Rng(0x5EED).u32() == (0x8E29 << 16) | 0xCB1A
    assert Rng(0x5EED).unit() == ((0x8E29 << 16) | 0xCB1A) / 4294967296.0


def test_rng_stays_16bit() -> None:
    r = Rng(0xFFFF)
    for _ in range(1000):
        assert 0 <= r.u16() <= 0xFFFF


# ---- 分数 (§0.2) ----
def test_add_score_divides_by_10() -> None:
    g = ZunGlobals()
    g.add_score(2000)  # 代码值 2000 → 入账 200
    assert g.score == 200
    g.add_score(15)  # 整数除法
    assert g.score == 201


def test_score_capped_at_max() -> None:
    g = ZunGlobals(score=SCORE_MAX)
    g.add_score(5000)
    g.tick_gui_score()
    assert g.score == SCORE_MAX


def test_gui_score_chases_and_converges() -> None:
    g = ZunGlobals()
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
    g = ZunGlobals(score=1)
    g.tick_gui_score()
    assert g.gui_score == 1  # 差值>>5==0 时最小步进 1


def test_gui_score_increment_capped() -> None:
    g = ZunGlobals(score=100_000_000)
    g.tick_gui_score()
    assert g.gui_score == GUI_SCORE_INCREMENT_MAX
    assert g.gui_score_difference == GUI_SCORE_INCREMENT_MAX


def test_snap_gui_score() -> None:
    g = ZunGlobals(score=12345)
    g.snap_gui_score()
    assert g.gui_score == 12345 and g.gui_score_difference == 0


# ---- 樱点 (§0.3) ----
def test_add_cherry_caps_at_max() -> None:
    g = ZunGlobals(cherry=100, cherry_max=150, cherry_start=0)
    g.add_cherry(80)
    assert g.cherry == 150


def test_add_cherry_plus_caps_and_signals_border() -> None:
    # C++ 开局 cherryPlus = cherryStart
    g = ZunGlobals(cherry=0, cherry_max=999999, cherry_plus=1000, cherry_start=1000)
    assert g.add_cherry_plus(30000) is False
    assert g.cherry == 30000 and g.cherry_plus == 31000
    # 触达 cherryStart+50000 → 返回 True(应开结界), cherryPlus 封顶
    assert g.add_cherry_plus(30000) is True
    assert g.cherry_plus == 1000 + CHERRY_PLUS_RANGE
    # 负数不加 cherryPlus
    assert g.add_cherry_plus(-5000) is True  # 仍在上限
    assert g.cherry == 55000 and g.cherry_plus == 51000


def test_increase_cherry_max_caps() -> None:
    g = ZunGlobals(cherry_max=100, cherry_start=0)
    g.increase_cherry_max(500)
    assert g.cherry_max == 600
    g.increase_cherry_max(CHERRY_MAX_RANGE * 2)
    assert g.cherry_max == CHERRY_MAX_RANGE


def test_subtract_cherry_drain_floors_at_start() -> None:
    g = ZunGlobals(cherry=8000, cherry_start=1000)
    g.subtract_cherry_drain(4000)
    assert g.cherry == 4000
    g.subtract_cherry_drain(9999)  # 不足 → 封底 cherryStart
    assert g.cherry == 1000


# ---- 动态难度 ----
def test_initialize_rank_per_difficulty() -> None:
    g = ZunGlobals()
    g.initialize_rank(1)  # Normal
    assert (g.rank, g.min_rank, g.max_rank) == (16, 10, 32)
    g.initialize_rank(4)  # Extra
    assert (g.rank, g.min_rank, g.max_rank) == (16, 15, 16)


def test_increase_subrank_rolls_into_rank() -> None:
    g = ZunGlobals()
    g.initialize_rank(1)
    g.increase_subrank(250)  # 2*100 + 50
    assert g.rank == 18 and g.subrank == 50


def test_increase_subrank_caps_at_max_rank() -> None:
    g = ZunGlobals()
    g.initialize_rank(0)  # Easy: max 20
    g.increase_subrank(10000)
    assert g.rank == 20


def test_decrease_subrank_floors_at_min_rank() -> None:
    g = ZunGlobals()
    g.initialize_rank(1)  # Normal: min 10
    g.decrease_subrank(10000)
    assert g.rank == 10
    g2 = ZunGlobals()
    g2.initialize_rank(1)
    g2.decrease_subrank(150)  # subrank=-150 → rank 16→14, subrank=50
    assert g2.rank == 14 and g2.subrank == 50


# ---- 得分弹字 / 状态横幅 (AsciiManager / Gui) ----

def test_popup_rises_and_expires() -> None:
    """弹字每帧上浮 0.5px, 寿命 60 帧 (AsciiManager.cpp:55-60)。"""
    g = ZunGlobals()
    g.add_popup(Vec2(100, 200), 50000, 0xFFFFFFFF)
    g.step_popups()
    p = g.popups[0]
    assert p.pos.y == 199.5 and p.timer == 1
    for _ in range(60):
        g.step_popups()
    assert g.popups == []  # timer > 60 消


def test_popup2_ring_caps_at_three() -> None:
    """CreatePopup2 仅 3 槽, 写满覆盖最旧 (AsciiManager.cpp:411-415)。"""
    g = ZunGlobals()
    for i in range(5):
        g.add_popup(Vec2(0, 0), i, 0xFFFFFFFF, kind=2)
    assert [p.value for p in g.popups] == [2, 3, 4]


def test_status_popup_show_and_expire() -> None:
    """状态横幅 180 帧隐藏 (Gui.cpp:1343-1347)。"""
    g = ZunGlobals()
    g.show_status_popup(0, STATUS_FULL_POWER)
    assert g.status_popup == STATUS_FULL_POWER and g.status_popup_timer == 0
    for _ in range(179):
        g.step_popups()
    assert g.status_popup == STATUS_FULL_POWER
    g.step_popups()
    assert g.status_popup == 0


def test_cherry_max_shows_status_popup() -> None:
    """樱点触达上限弹 "CherryPoint Max!" (GameManager.cpp:934-937/949-952)。"""
    g = ZunGlobals(cherry=0, cherry_start=0, cherry_max=1000)
    g.add_cherry(500)
    assert g.status_popup == 0  # 未满不弹
    g.add_cherry(500)
    assert g.status_popup == STATUS_CHERRY_MAX
    assert g.status_popup_arg == 1000  # cherry - cherryStart
    g.status_popup = 0
    g.add_cherry_plus(100)  # 已满且本次无变化 → 不重弹 (oldCherry != cherry 条件)
    assert g.status_popup == 0


# ---- highScore 跟随 (BUGS.md#15, GameManager.cpp:265-268) ----
def test_high_score_follows_gui_score() -> None:
    """显示分破纪录时 highScore 实时同步, 并记当时续关数。"""
    g = ZunGlobals(high_score=100000, high_score_num_continues=1)
    g.add_score(2_000_000)  # score = 200000
    g.num_retries = 2
    for _ in range(120):
        g.tick_gui_score()
        g.tick_high_score()
    assert g.gui_score == 200000
    assert g.high_score == 200000
    assert g.high_score_num_continues == 2


def test_high_score_not_lowered_below_record() -> None:
    """未破纪录时 highScore 保持原值。"""
    g = ZunGlobals(high_score=500000)
    g.add_score(1_000_000)  # score = 100000 < high_score
    for _ in range(120):
        g.tick_gui_score()
        g.tick_high_score()
    assert g.high_score == 500000
    assert g.high_score_num_continues == 0


def test_high_score_syncs_during_chase_not_just_at_end() -> None:
    """追赶途中 guiScore 一过线 highScore 就跟(BUGS.md#15 的实时语义)。"""
    g = ZunGlobals(high_score=100000)
    g.score = 150000
    seen = False
    for _ in range(120):
        g.tick_gui_score()
        g.tick_high_score()
        if g.gui_score > 100000:
            seen = True
            assert g.high_score == g.gui_score
    assert seen


def test_bonus_banners_expire() -> None:
    """BONUS 横幅 250 帧 / Spell Card Bonus 横幅 280 帧 (BUGS.md#6,
    Gui.cpp:1323/1351)。"""
    g = ZunGlobals()
    g.show_bonus_score(12345)
    g.show_spellcard_bonus(67890)
    for _ in range(249):
        g.step_popups()
    assert g.bonus_score == 12345 and g.spellcard_bonus == 67890
    g.step_popups()
    assert g.bonus_score == 0
    assert g.spellcard_bonus == 67890
    for _ in range(30):
        g.step_popups()
    assert g.spellcard_bonus == 0
