"""Touhou: 道具系统测试。"""
from __future__ import annotations

import sys

sys.path.insert(0, r"D:\python_play\Touhou08")

from touhou.engine.items import (  # noqa: E402
    DROP_TABLE,
    FULL_POWER,
    FULL_POWER_SCORE_BONUS,
    PLAYER_STATE_SPAWNING,
    STATE_ATTRACT,
    STATE_FALL,
    STATE_SPAWN,
    GameContext,
    ItemType,
    ItemWorld,
    next_needed_point_items_for_extend,
)
from touhou.utils import Vec2  # noqa: E402


# ---- POWER_SMALL ----

def test_power_small_increases_power() -> None:
    w = ItemWorld()
    ctx = GameContext(power=10)
    it = w.spawn(Vec2(100, 100), ItemType.POWER_SMALL)
    r = w.collect(it, ctx)
    assert r.delta_power == 1
    assert r.score == 1  # AddScore(10) 代码值 → 显示分 1
    assert r.subrank == 1
    assert r.power_overflow_next == 0  # 非满火力小 P 重置计分计数器


def test_power_small_at_full_uses_bonus_table() -> None:
    w = ItemWorld()
    # counter 0 → +1 → 表索引 1 → 代码值 20 → 显示 2
    ctx = GameContext(power=FULL_POWER, power_overflow_counter=0)
    r = w.collect(w.spawn(Vec2(100, 100), ItemType.POWER_SMALL), ctx)
    assert r.full_power
    assert r.power_overflow_next == 1
    assert r.score == FULL_POWER_SCORE_BONUS[1] // 10 == 2


def test_power_small_bonus_table_progression_and_cap() -> None:
    w = ItemWorld()
    # 递进: counter 9 → 索引 10 → 代码 200 → 显示 20
    ctx = GameContext(power=FULL_POWER, power_overflow_counter=9)
    r = w.collect(w.spawn(Vec2(0, 0), ItemType.POWER_SMALL), ctx)
    assert r.score == FULL_POWER_SCORE_BONUS[10] // 10 == 20
    # cap 30: counter 29 → n=30 → 封顶末档(显示 1200)
    ctx2 = GameContext(power=FULL_POWER, power_overflow_counter=29)
    r2 = w.collect(w.spawn(Vec2(0, 0), ItemType.POWER_SMALL), ctx2)
    assert r2.power_overflow_next == 30
    assert r2.score == FULL_POWER_SCORE_BONUS[29] // 10 == 1200
    # counter 再大也不超过 30
    ctx3 = GameContext(power=FULL_POWER, power_overflow_counter=99)
    r3 = w.collect(w.spawn(Vec2(0, 0), ItemType.POWER_SMALL), ctx3)
    assert r3.power_overflow_next == 30 and r3.score == 1200


def test_full_power_bonus_table_matches_cpp() -> None:
    # g_FullPowerScoreBonus: 10..100 / 200..1000 / 2000..12000
    assert len(FULL_POWER_SCORE_BONUS) == 30
    assert FULL_POWER_SCORE_BONUS[0] == 10
    assert FULL_POWER_SCORE_BONUS[9] == 100
    assert FULL_POWER_SCORE_BONUS[10] == 200
    assert FULL_POWER_SCORE_BONUS[18] == 1000
    assert FULL_POWER_SCORE_BONUS[19] == 2000
    assert FULL_POWER_SCORE_BONUS[29] == 12000


def test_power_small_crossing_full_clears_and_despawns() -> None:
    w = ItemWorld()
    ctx = GameContext(power=FULL_POWER - 1)
    other = w.spawn(Vec2(50, 50), ItemType.POWER_BIG)
    it = w.spawn(Vec2(100, 100), ItemType.POWER_SMALL)
    r = w.collect(it, ctx)
    assert r.clear_bullets
    assert other.type == ItemType.CHERRY  # 场上其它 P 转樱
    assert it.type == ItemType.POWER_SMALL  # 被收集者本身除外


# ---- POWER_BIG / BOMB / LIFE / FULL_POWER ----

def test_power_big() -> None:
    w = ItemWorld()
    ctx = GameContext(power=10)
    r = w.collect(w.spawn(Vec2(0, 0), ItemType.POWER_BIG), ctx)
    assert r.delta_power == 8
    assert r.score == 1  # AddScore(10)
    assert r.subrank == 0  # 大 P 无 subrank


def test_power_big_at_full_gives_nothing() -> None:
    w = ItemWorld()
    ctx = GameContext(power=FULL_POWER)
    r = w.collect(w.spawn(Vec2(0, 0), ItemType.POWER_BIG), ctx)
    assert r.delta_power == 0 and r.score == 0


def test_bomb_capped_at_8() -> None:
    w = ItemWorld()
    r = w.collect(w.spawn(Vec2(0, 0), ItemType.BOMB), GameContext(bombs=2))
    assert r.delta_bombs == 1 and r.subrank == 5
    r2 = w.collect(w.spawn(Vec2(0, 0), ItemType.BOMB), GameContext(bombs=8))
    assert r2.delta_bombs == 0 and r2.subrank == 5  # subrank 不受上限影响


def test_life_and_full_power() -> None:
    w = ItemWorld()
    r = w.collect(w.spawn(Vec2(0, 0), ItemType.LIFE), GameContext())
    assert r.delta_lives == 1
    ctx = GameContext(power=100)
    r2 = w.collect(w.spawn(Vec2(0, 0), ItemType.FULL_POWER), ctx)
    assert r2.delta_power == 28 and r2.score == 100 and r2.clear_bullets
    # 已满火力吃 FULL_POWER: 仍有分但不清屏
    r3 = w.collect(w.spawn(Vec2(0, 0), ItemType.FULL_POWER),
                   GameContext(power=FULL_POWER))
    assert r3.score == 100 and not r3.clear_bullets and r3.delta_power == 0


# ---- POINT 高度衰减 / autoCollect / 满樱 ----

def test_point_full_value_above_poc() -> None:
    w = ItemWorld()
    ctx = GameContext()
    it = w.spawn(Vec2(100, 50), ItemType.POINT)  # y=50 < poc 128
    r = w.collect(it, ctx)
    assert r.score == 5000
    assert r.point_items_collected == 1
    assert r.subrank == 10  # 道具 y<128


def test_point_decays_below_poc() -> None:
    w = ItemWorld()
    ctx = GameContext()
    it = w.spawn(Vec2(100, 228), ItemType.POINT)  # 低于 POC 100px → -10000 代码值
    r = w.collect(it, ctx)
    assert r.score == 4000
    assert r.subrank == 3


def test_point_auto_collect_always_full() -> None:
    w = ItemWorld()
    ctx = GameContext()
    it = w.spawn(Vec2(100, 400), ItemType.POINT)
    it.auto_collect = True
    r = w.collect(it, ctx)
    assert r.score == 5000


def test_point_cherry_gap_branches() -> None:
    w = ItemWorld()
    # 满值 + 樱差>50000: 改用樱差
    it = w.spawn(Vec2(100, 50), ItemType.POINT)
    r = w.collect(it, GameContext(cherry_gap=60000))
    assert r.score == 6000
    # 非满值 + 樱差>50000: 追加 (差-50000)/5
    it2 = w.spawn(Vec2(100, 228), ItemType.POINT)  # 基础 40000 代码值
    r2 = w.collect(it2, GameContext(cherry_gap=60000))
    assert r2.score == (40000 + 10000 // 5) // 10 == 4200
    # 樱差 ≤50000 不影响
    it3 = w.spawn(Vec2(100, 228), ItemType.POINT)
    r3 = w.collect(it3, GameContext(cherry_gap=50000))
    assert r3.score == 4000


# ---- extend 阈值表 ----

def test_extend_threshold_table() -> None:
    # 难度 <4: 50/125/200, 300/450, 800/1000...
    assert [next_needed_point_items_for_extend(e, 1) for e in range(3)] == [50, 125, 200]
    assert [next_needed_point_items_for_extend(e, 1) for e in (3, 4)] == [300, 450]
    assert [next_needed_point_items_for_extend(e, 1) for e in (5, 6)] == [800, 1000]
    # 难度 ≥4: 200/500, 之后 (e-2)*500+800
    assert next_needed_point_items_for_extend(0, 4) == 200
    assert next_needed_point_items_for_extend(1, 4) == 500
    assert next_needed_point_items_for_extend(2, 4) == 800
    assert next_needed_point_items_for_extend(3, 4) == 1300


def test_point_extend_single() -> None:
    w = ItemWorld()
    ctx = GameContext(point_items_collected_for_extend=49,
                      extends_from_point_items=0, difficulty=1)
    r = w.collect(w.spawn(Vec2(100, 50), ItemType.POINT), ctx)
    assert r.extends == 1  # 第 50 个 → 阈值 50


def test_point_extend_multiple_at_once() -> None:
    w = ItemWorld()
    # 一次结算时累计 200 个且 e=0: 50/125/200 全过 → 连升 3 个
    ctx = GameContext(point_items_collected_for_extend=199,
                      extends_from_point_items=0, difficulty=1)
    r = w.collect(w.spawn(Vec2(100, 50), ItemType.POINT), ctx)
    assert r.extends == 3
    # 难度 ≥4: 累计 500, e=0 → 200/500 过 → 2 个
    ctx2 = GameContext(point_items_collected_for_extend=499,
                       extends_from_point_items=0, difficulty=4)
    r2 = w.collect(w.spawn(Vec2(100, 50), ItemType.POINT), ctx2)
    assert r2.extends == 2
    # 未达阈值
    ctx3 = GameContext(point_items_collected_for_extend=10,
                       extends_from_point_items=0, difficulty=1)
    r3 = w.collect(w.spawn(Vec2(100, 50), ItemType.POINT), ctx3)
    assert r3.extends == 0


# ---- CHERRY / CHERRY_SMALL / STAR / POINT_BULLET ----

def test_cherry_big() -> None:
    w = ItemWorld()
    ctx = GameContext(spell_cards_captured=3)
    r = w.collect(w.spawn(Vec2(100, 100), ItemType.CHERRY), ctx)
    assert r.delta_cherry_plus == 1000 + 300
    assert r.delta_cherry == 0
    assert r.score == 0  # 未满樱无分


def test_cherry_big_at_max_scores_like_point() -> None:
    w = ItemWorld()
    ctx = GameContext(cherry_maxed=True)
    it = w.spawn(Vec2(100, 50), ItemType.CHERRY)
    r = w.collect(it, ctx)
    assert r.score == 5000
    # 线下衰减
    it2 = w.spawn(Vec2(100, 228), ItemType.CHERRY)
    r2 = w.collect(it2, ctx)
    assert r2.score == 4000
    # auto_collect 恒满值
    it3 = w.spawn(Vec2(100, 400), ItemType.CHERRY)
    it3.auto_collect = True
    r3 = w.collect(it3, ctx)
    assert r3.score == 5000


def test_cherry_small_dual_track() -> None:
    w = ItemWorld()
    r = w.collect(w.spawn(Vec2(0, 0), ItemType.CHERRY_SMALL), GameContext())
    assert r.delta_cherry_plus == 30
    assert r.delta_cherry == 70


def test_point_bullet_graze_formula() -> None:
    w = ItemWorld()
    ctx = GameContext(graze_total=400)
    r = w.collect(w.spawn(Vec2(0, 0), ItemType.POINT_BULLET), ctx)
    assert r.score == 400 // 40 + 30  # 显示分 = graze/40 + 30
    assert r.delta_cherry_plus == 20
    assert r.delta_cherry == 0


def test_point_bullet_while_bombing() -> None:
    w = ItemWorld()
    ctx = GameContext(graze_total=400, bombing=True)
    r = w.collect(w.spawn(Vec2(0, 0), ItemType.POINT_BULLET), ctx)
    assert r.score == 10
    assert r.delta_cherry_plus == 10
    assert r.delta_cherry == 10


def test_star() -> None:
    w = ItemWorld()
    r = w.collect(w.spawn(Vec2(0, 0), ItemType.STAR), GameContext(graze_total=400))
    assert r.score == 400 // 40 + 30
    assert r.delta_cherry_plus == 100
    assert r.delta_cherry == 0


# ---- 运动 / 状态 ----

def test_item_falls_and_accelerates() -> None:
    w = ItemWorld()
    ctx = GameContext()
    it = w.spawn(Vec2(100, 100), ItemType.POINT)
    v0 = it.start.y
    # 下落速度从 -2.2 起, 每帧 +0.03 加速(向下转正), 封顶 3.0
    assert v0 == -2.2
    for _ in range(80):
        w.step(ctx)
    # 80 帧后速度已转正且 <3.0 (仍在加速中)
    assert 0 < it.start.y <= 3.0


def test_item_fall_speed_caps_at_3() -> None:
    w = ItemWorld()
    ctx = GameContext()
    it = w.spawn(Vec2(100, 100), ItemType.POINT)
    for _ in range(300):
        w.step(ctx)
    assert it.start.y == 3.0


def test_poc_attracts_when_full_power_and_above_line() -> None:
    w = ItemWorld()
    ctx = GameContext(power=FULL_POWER, player_pos=Vec2(192, 60))
    it = w.spawn(Vec2(100, 400), ItemType.POWER_SMALL)
    w.step(ctx)
    assert it.state == STATE_ATTRACT
    assert not it.auto_collect  # POC 吸附不标满分(C++ 仅结界置位)


def test_border_collects_everything_with_auto_collect() -> None:
    w = ItemWorld()
    ctx = GameContext(border_active=True, player_pos=Vec2(192, 60))
    it = w.spawn(Vec2(100, 400), ItemType.POINT)
    w.step(ctx)
    assert it.state == STATE_ATTRACT
    assert it.auto_collect


def test_player_spawning_slows_items() -> None:
    w = ItemWorld()
    ctx = GameContext(power=FULL_POWER, player_pos=Vec2(192, 60),
                      player_state=PLAYER_STATE_SPAWNING)
    it = w.spawn(Vec2(100, 400), ItemType.POINT)
    w.step(ctx)
    assert it.state == STATE_FALL  # 不吸附
    assert it.start.y == -0.5 or it.start.y == -0.5 + 0.03  # 缓降
    # 重生中也不可收集
    it.pos = Vec2(192, 60)
    assert not w.collect_pickup(it, ctx)


def test_offscreen_items_dropped_with_subrank_penalty() -> None:
    w = ItemWorld()
    ctx = GameContext()
    it = w.spawn(Vec2(100, 500), ItemType.POINT)  # 已在底边外
    dropped = w.step(ctx)
    assert it in dropped
    assert len(w) == 0
    # 屏顶(y<0)的道具不删除
    it2 = w.spawn(Vec2(100, -10), ItemType.POINT)
    dropped2 = w.step(ctx)
    assert it2 not in dropped2 and len(w) == 1


def test_spawn_animation_flies_to_target() -> None:
    w = ItemWorld()
    ctx = GameContext()
    it = w.spawn(Vec2(100, 100), ItemType.POINT)
    it.spawn_to(Vec2(200, 50))
    for _ in range(60):
        w.step(ctx)
    assert it.state == STATE_FALL
    assert it.pos == Vec2(200, 50)
    assert it.state != STATE_SPAWN


# ---- 批量操作 (§E.5) ----

def test_remove_all_items() -> None:
    w = ItemWorld()
    a = w.spawn(Vec2(0, 0), ItemType.POINT)
    b = w.spawn(Vec2(10, 10), ItemType.CHERRY)
    w.remove_all_items()
    for it in (a, b):
        assert it.state == STATE_ATTRACT
        assert it.start == Vec2(0.0, -0.5)


def test_despawn_all_items() -> None:
    w = ItemWorld()
    p1 = w.spawn(Vec2(0, 0), ItemType.POWER_SMALL)
    p2 = w.spawn(Vec2(10, 10), ItemType.POWER_BIG)
    pt = w.spawn(Vec2(20, 20), ItemType.POINT)
    p1.start = Vec2(0.0, 1.0)  # 下落中(y > -0.5)才被改速
    w.despawn_all_items()
    assert p1.type == ItemType.CHERRY and p2.type == ItemType.CHERRY
    assert pt.type == ItemType.POINT
    assert p1.start == Vec2(0.0, -0.5)
    assert p2.start == Vec2(0.0, -2.2)  # 出生初速 y=-2.2 ≤ -0.5 时保持(C++ 条件)


def test_activate_all_items() -> None:
    w = ItemWorld()
    a = w.spawn(Vec2(0, 0), ItemType.POINT)
    b = w.spawn(Vec2(10, 10), ItemType.CHERRY)
    w.remove_all_items()
    w.activate_all_items()
    for it in (a, b):
        assert it.state == STATE_FALL
        assert it.start == Vec2(0.0, -0.9)


# ---- 满火力生成转换 ----

def test_spawn_converts_power_items_at_full_power() -> None:
    w = ItemWorld()
    it = w.spawn(Vec2(0, 0), ItemType.POWER_SMALL, power=FULL_POWER)
    assert it.type == ItemType.CHERRY
    it2 = w.spawn(Vec2(0, 0), ItemType.POWER_BIG, power=FULL_POWER)
    assert it2.type == ItemType.CHERRY
    it3 = w.spawn(Vec2(0, 0), ItemType.POWER_SMALL, power=127)
    assert it3.type == ItemType.POWER_SMALL


def test_drop_table_length_and_values() -> None:
    assert len(DROP_TABLE) == 32
    assert max(DROP_TABLE) <= 7
