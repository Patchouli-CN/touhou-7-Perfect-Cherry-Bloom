"""Touhou: 炸弹(参数表/CherryDrain/触发/ReimuA) + 樱花结界测试。

数值权威来源: th07/src/th07/BombData.cpp 与 Player.cpp。
"""

from __future__ import annotations

import math
import sys

import pytest

sys.path.insert(0, r"D:\python_play\Touhou08")

from touhou.games.th07.bomb import (  # noqa: E402
    BOMB_PARAMS,
    CHAR_MARISA_A,
    CHAR_MARISA_B,
    CHAR_REIMU_A,
    CHAR_REIMU_B,
    CHAR_SAKUYA_A,
    CHAR_SAKUYA_B,
    DIFF_EXTRA,
    DIFF_HARD,
    DIFF_LUNATIC,
    DIFF_NORMAL,
    DIFF_PHANTASM,
    EVENT_END_PLAYER_SPELLCARD,
    EVENT_REMOVE_ALL_ITEMS,
    EVENT_STOP_BULLET_MOVEMENT,
    ITEM_CHERRY_SMALL,
    ITEM_POINT_BULLET,
    Bomb,
    BombContext,
    Border,
    BorderState,
    ClearBox,
    _aabb,
    compute_bomb_cherry_drain,
    try_start_bomb,
)
from touhou.games.th07.globals import ZunGlobals  # noqa: E402
from touhou.utils import Vec2  # noqa: E402

CTX = BombContext(
    player_pos=Vec2(100.0, 300.0),
    cherry=101000.0,
    cherry_start=1000.0,
    difficulty=DIFF_NORMAL,
)


def _started_bomb(**kwargs) -> Bomb:
    b = Bomb(character=kwargs.pop("character", CHAR_REIMU_A))
    b.start(focus=kwargs.pop("focus", False), ctx=kwargs.pop("ctx", CTX), **kwargs)
    return b


# ---- §D.3 参数表抽样 (BombData.cpp 各 *Calc 首帧) ----


def test_bomb_params_table_spot_check() -> None:
    assert BOMB_PARAMS[(CHAR_REIMU_A, False)].duration == 140  # BombData.cpp:137
    assert BOMB_PARAMS[(CHAR_REIMU_A, False)].invulnerability == 200
    assert BOMB_PARAMS[(CHAR_REIMU_A, False)].drain_min_cost == 4000
    assert BOMB_PARAMS[(CHAR_REIMU_A, False)].drain_scale == pytest.approx(0.20)
    p = BOMB_PARAMS[(CHAR_MARISA_B, True)]  # BombData.cpp:1107-1120
    assert (p.duration, p.invulnerability, p.drain_min_cost) == (340, 390, 10000)
    assert p.drain_scale == pytest.approx(0.41)
    p = BOMB_PARAMS[(CHAR_SAKUYA_B, True)]  # BombData.cpp:1633-1659
    assert (p.duration, p.invulnerability, p.drain_min_cost) == (300, 420, 6000)
    p = BOMB_PARAMS[(CHAR_MARISA_A, False)]  # BombData.cpp:723-739
    assert (p.duration, p.invulnerability, p.drain_min_cost) == (200, 250, 8000)


# ---- ComputeBombCherryDrain (BombData.cpp:87-112) ----


def test_cherry_drain_normal() -> None:
    # drain=int(100000*0.2)=20000; /140=142→140; minCost=4000/140=28→20; max=140
    d = compute_bomb_cherry_drain(
        cherry=101000,
        cherry_start=1000,
        difficulty=DIFF_NORMAL,
        bomb_duration=140,
        min_cost=4000,
        scale=0.20,
    )
    assert d == 140


def test_cherry_drain_difficulty_divisors() -> None:
    base = dict(
        cherry=101000, cherry_start=1000, bomb_duration=140, min_cost=4000, scale=0.20
    )
    assert (
        compute_bomb_cherry_drain(difficulty=DIFF_HARD, **base) == 70
    )  # 20000/2/140=71→70
    assert (
        compute_bomb_cherry_drain(difficulty=DIFF_LUNATIC, **base) == 30
    )  # 20000/4/140=35→30
    assert (
        compute_bomb_cherry_drain(difficulty=DIFF_EXTRA, **base) == 40
    )  # 6666/140=47→40
    assert compute_bomb_cherry_drain(difficulty=DIFF_PHANTASM, **base) == 40


def test_cherry_drain_min_cost_floor() -> None:
    # drain=0 (cherry==cherryStart) → minCost=4000/140=28→20 生效
    d = compute_bomb_cherry_drain(
        cherry=1000,
        cherry_start=1000,
        difficulty=DIFF_NORMAL,
        bomb_duration=140,
        min_cost=4000,
        scale=0.20,
    )
    assert d == 20


def test_cherry_drain_float_truncation() -> None:
    # (i32)(f32) 向零截断: int(149*0.2)=int(29.8)=29 → /10=2 → 2-2%10=0; minCost=0 → 0
    d = compute_bomb_cherry_drain(
        cherry=1149,
        cherry_start=1000,
        difficulty=DIFF_NORMAL,
        bomb_duration=10,
        min_cost=0,
        scale=0.20,
    )
    assert d == 0


# ---- 触发条件 (Player.cpp:1719-1755) ----


def _try(bomb=None, **kw) -> "object":
    args = dict(
        focus=False,
        bombs_remaining=3.0,
        respawn_timer=30,
        initial_respawn_timer=60,
        border_invulnerability_time=0,
        bomb_pressed=True,
        spellcard_active=True,
    )
    args.update(kw)
    return try_start_bomb(bomb or Bomb(), CTX, **args)


def test_start_bomb_success() -> None:
    b = Bomb()
    r = _try(b)
    assert r.started
    assert r.bombs_used_delta == 1 and r.bombs_remaining_delta == -1
    assert r.subrank_delta == -200
    assert r.respawn_timer == 36  # 30+6
    assert r.spellcard_capture_reset and r.spellcard_used_bomb
    # 首帧初始化已在当帧 calc 里完成 (ReimuA)
    assert b.is_in_use and not b.is_focus
    assert b.duration == 140 and b.invulnerability_timer == 200
    assert b.cherry_drain == 140 and b.timer == 1
    assert EVENT_REMOVE_ALL_ITEMS in b.events


def test_start_bomb_respawn_capped() -> None:
    assert _try(respawn_timer=58).respawn_timer == 60  # min(58+6, 60)


def test_start_bomb_rejections() -> None:
    assert not _try(bomb_pressed=False).started
    assert not _try(respawn_timer=0).started
    assert not _try(bombs_remaining=0).started
    assert not _try(border_invulnerability_time=10).started
    b = _started_bomb()  # bomb 中
    assert not _try(b).started


def test_all_twelve_bombs_dispatch() -> None:
    """12 套 (character, focus) 全部可分派, 首帧初始化与参数表一致。"""
    ctx = BombContext(
        player_pos=Vec2(100.0, 300.0),
        cherry=101000.0,
        cherry_start=1000.0,
        difficulty=DIFF_NORMAL,
        rng_float=lambda: 0.5,
    )
    for character in range(6):
        for focus in (False, True):
            b = Bomb(character=character)
            b.start(focus=focus, ctx=ctx)
            params = BOMB_PARAMS[(character, focus)]
            assert b.is_in_use and b.duration == params.duration
            assert b.invulnerability_timer == params.invulnerability
            assert b.cherry_drain == compute_bomb_cherry_drain(
                cherry=ctx.cherry,
                cherry_start=ctx.cherry_start,
                difficulty=ctx.difficulty,
                bomb_duration=params.duration,
                min_cost=params.drain_min_cost,
                scale=params.drain_scale,
            )
            assert b.invulnerable and b.timer == 1


# ---- ReimuA 非集中: 珠弹节奏 (BombData.cpp:116-256) ----


def test_reimu_a_orb_spawn_rhythm() -> None:
    b = _started_bomb()
    # timer 12 (=8 起首个 %6==0) 出第一个珠
    while b.timer <= 11:
        b.tick(CTX)
        assert b.sub_info[0].state == 0
    b.tick(CTX)  # 处理 timer==12
    sub = b.sub_info[0]
    assert sub.state == 1
    assert sub.speed == pytest.approx(14.6)  # 15 - 0.4 (当帧即衰减)
    # 角度 0: -pi/2 → 向上, 位置当帧移动
    assert sub.pos.x == pytest.approx(CTX.player_pos.x, abs=1e-4)
    assert sub.pos.y == pytest.approx(300.0 - 14.6)
    # 每 6 帧一个: timer 18 出第二个
    while b.timer <= 18:
        b.tick(CTX)
    assert b.sub_info[1].state == 1
    assert b.damage_boxes[0].size.x == 48.0  # 飞行中伤害盒 48×48
    assert b.damage_boxes[0].lifetime == 8


def test_reimu_a_orb_explosion() -> None:
    b = _started_bomb()
    # 第 63 次衰减 speed=-10.2 < -10 → timer 74 爆开
    while b.timer <= 74:
        b.tick(CTX)
    sub = b.sub_info[0]
    assert sub.state == 2
    # C++ 同帧覆写: 爆开帧伤害盒最终为 48×48/8 (BombData.cpp:220-229)
    box = b.damage_boxes[0]
    assert box.size == Vec2(48.0, 48.0) and box.lifetime == 8
    # 爆开清弹圆 SpawnBombEffect(64, 4.2667, 30)
    boom = [c for c in b.clear_boxes if c.growth == pytest.approx(4.266667)]
    assert len(boom) == 1
    assert boom[0].size.y == pytest.approx(64.0) and boom[0].lifetime == 30
    # 后续帧: 256×256/lifetime=2, 清弹圆半径增长
    b.tick(CTX)
    box = b.damage_boxes[0]
    assert box.size == Vec2(256.0, 256.0) and box.lifetime == 2
    assert sub.counter == 1
    assert boom[0].size.y == pytest.approx(64.0 + 4.266667)
    assert boom[0].lifetime == 29
    # 爆开后 30 帧 (timer 104) 珠消失
    while b.timer <= 104:
        b.tick(CTX)
    assert b.sub_info[0].state == 0


def test_reimu_a_lifecycle_and_drain() -> None:
    b = _started_bomb()
    ticks = 0
    while b.is_in_use:
        b.tick(CTX)
        ticks += 1
        assert b.drain_applied == b.cherry_drain  # 每帧扣 (Player.cpp:1705-1708)
        assert b.invulnerable  # bomb 期间 INVULNERABLE
    assert ticks == 140  # duration=140: start 处理 timer0, 再 140 帧后 timer==140 结束
    assert EVENT_END_PLAYER_SPELLCARD in b.events
    # 樱点封底 cherryStart (ZunGlobals.subtract_cherry_drain)
    g = ZunGlobals(cherry=1300, cherry_start=1000)
    g.subtract_cherry_drain(b.cherry_drain)  # 1300-140=1160
    assert g.cherry == 1160
    g.subtract_cherry_drain(b.cherry_drain)  # 1160-140=1020
    g.subtract_cherry_drain(b.cherry_drain)  # 1020-140=880 <1000 → 封底
    assert g.cherry == 1000


# ---- ReimuA 集中: 追踪珠 (BombData.cpp:310-474) ----


def test_reimu_a_focused() -> None:
    ctx = BombContext(
        player_pos=Vec2(100.0, 300.0),
        cherry=101000.0,
        cherry_start=1000.0,
        difficulty=DIFF_NORMAL,
        rng_float=lambda: 0.5,
    )
    b = _started_bomb(focus=True, ctx=ctx)
    assert b.duration == 300 and b.invulnerability_timer == 360
    assert b.move_speed_multiplier == pytest.approx(0.6)
    # timer 64 的 i==0 跳过, timer 80 出第一个珠 (i=1)
    while b.timer <= 80:
        b.tick(ctx)
    assert b.sub_info[0].state == 0
    sub = b.sub_info[1]
    assert sub.state == 1 and sub.accel > 0
    # 累计伤害 >= 100 → 爆开: 256×256/400 (集中版不被覆写)
    b.damage_boxes[1].damage = 100
    b.tick(ctx)
    assert sub.state == 2
    box = b.damage_boxes[1]
    assert box.size == Vec2(256.0, 256.0) and box.lifetime == 400
    boom = [c for c in b.clear_boxes if c.growth == pytest.approx(6.6666665)]
    assert len(boom) == 1 and boom[0].lifetime == 15
    assert boom[0].size.y == pytest.approx(32.0)


# ---- 伤害盒 / 清弹盒判定 ----


def test_damage_box_hits_enemy_and_accumulates() -> None:
    b = _started_bomb()
    box = b.damage_boxes[0]
    box.pos, box.size, box.lifetime = Vec2(100, 100), Vec2(50, 50), 8
    # size 为全宽: 盒 75..125, 敌 (110,110)±10 → 相交
    assert b.damage_to(Vec2(110, 110), Vec2(10, 10)) == 8
    assert box.damage == 8  # 累计 (Player.cpp:926)
    assert b.damage_to(Vec2(110, 110), Vec2(10, 10)) == 8
    assert box.damage == 16
    assert b.damage_to(Vec2(500, 500), Vec2(10, 10)) == 0


def test_clear_box_circle() -> None:
    b = _started_bomb()
    b.clear_boxes = [ClearBox(Vec2(100, 100), Vec2(0, 40), 10, 6)]
    assert b.check_bomb_graze(Vec2(120, 120), Vec2(4, 4)) == 2  # dist²=800 < 1600
    assert b.item_type == 6
    # 圆判定不看弹盒 size, 且严格小于
    assert b.check_bomb_graze(Vec2(140, 100), Vec2(4, 4)) == 0  # dist²=1600 不 <
    assert b.check_bomb_graze(Vec2(300, 300), Vec2(4, 4)) == 0


def test_clear_box_linear_segment() -> None:
    # pos_z!=0 → 线性段 AABB: 宽=pos_z=40, 高=size.x=20 (Player.cpp:967-980)
    seg = ClearBox(Vec2(100, 100), Vec2(20, 0), 10, 8, pos_z=40.0)
    assert seg.active
    assert seg.hits(Vec2(123, 100), Vec2(8, 8))  # 23 <= (40+8)/2
    assert not seg.hits(Vec2(125, 100), Vec2(8, 8))  # 25 > 24
    assert seg.hits(Vec2(100, 113), Vec2(8, 8))  # 13 <= (20+8)/2
    assert not seg.hits(Vec2(100, 115), Vec2(8, 8))


def test_clear_box_tick_and_expiry() -> None:
    c = ClearBox(Vec2(0, 0), Vec2(0, 32), 1, 6, growth=16.0)
    c.tick()  # lifetime 1→0, 半径 32→48, 仍活跃
    assert c.lifetime == 0 and c.size.y == pytest.approx(48.0) and c.active
    c.tick()  # lifetime<=0 → 清零 (Player.cpp:1670-1674)
    assert c.size.y == 0.0 and not c.active


# ---- 樱之结界 (§D.5 / Player.cpp) ----


def test_border_ready_and_activate() -> None:
    br = Border()
    br.ready_border()
    assert br.has_border == BorderState.READY
    # bomb 中不可激活, 保持 READY
    assert not br.activate_border(bombing=True)
    assert br.has_border == BorderState.READY
    assert br.activate_border()
    assert br.active
    assert br.invulnerability_timer == 540 and br.border_timer == 540


def test_border_tick_cherry_plus_display() -> None:
    br = Border()
    br.ready_border()
    br.activate_border()
    plus, res = br.tick(cherry=5000, cherry_start=1000, cherry_max=999999)
    assert res is None
    assert plus == 1000 + 540 * 50000 // 540  # 满格 51000 (先算后减)
    assert br.invulnerability_timer == 539
    plus, _ = br.tick(cherry=5000, cherry_start=1000, cherry_max=999999)
    assert plus == 1000 + 539 * 50000 // 540  # 50907


def test_border_natural_break() -> None:
    br = Border()
    br.ready_border()
    br.activate_border()
    br.invulnerability_timer = 1
    plus, res = br.tick(cherry=500, cherry_start=1000, cherry_max=20000)
    assert res is not None
    # IncreaseCherryMax(+10000)→30000; IncreaseCherry(+10000)→10500; score=(10500-1000)*10
    assert res.cherry_max == 30000 and res.cherry == 10500
    assert res.score == (10500 - 1000) * 10
    assert res.cherry_plus == 1000 and plus == 1000
    assert res.invulnerability_timer == 40 and res.border_invulnerability_time == 40
    assert br.has_border == BorderState.NONE
    assert br.invulnerability_timer == 40 and br.border_invulnerability_time == 40
    # 自然破后 border_invulnerability_time 每帧递减 (Player.cpp:1699-1701)
    br.tick(cherry=0, cherry_start=1000, cherry_max=0)
    assert br.border_invulnerability_time == 39


def test_border_natural_break_cherry_capped() -> None:
    br = Border()
    br.ready_border()
    br.activate_border()
    br.invulnerability_timer = 1
    # IncreaseCherry 封顶 cherryMax: min(25000+10000, 30000)=30000
    _, res = br.tick(cherry=25000, cherry_start=1000, cherry_max=20000)
    assert res.cherry_max == 30000
    assert res.cherry == 30000
    assert res.score == (30000 - 1000) * 10


def test_border_break_early() -> None:
    br = Border()
    br.ready_border()
    br.activate_border()
    box = br.break_border(Vec2(100, 200))
    assert br.has_border == BorderState.NONE
    assert br.invulnerability_timer == 40 and br.border_invulnerability_time == 40
    # SpawnBombEffect(32, 16, 50, CHERRY_SMALL) (Player.cpp:2182)
    assert box.size.y == pytest.approx(32.0) and box.growth == pytest.approx(16.0)
    assert box.lifetime == 50 and box.item_type == ITEM_CHERRY_SMALL
    assert box.pos == Vec2(100, 200) and box.active


def test_aabb_overlap() -> None:
    assert _aabb(Vec2(0, 0), Vec2(5, 5), Vec2(3, 0), Vec2(5, 5))
    assert not _aabb(Vec2(0, 0), Vec2(5, 5), Vec2(100, 100), Vec2(5, 5))


# ---- 确定性随机 ctx (rng_float 恒 0.5) ----

CTX_RNG = BombContext(
    player_pos=Vec2(100.0, 300.0),
    cherry=101000.0,
    cherry_start=1000.0,
    difficulty=DIFF_NORMAL,
    rng_float=lambda: 0.5,
)
CTX_RNG_ENEMY = BombContext(
    player_pos=Vec2(100.0, 300.0),
    cherry=101000.0,
    cherry_start=1000.0,
    difficulty=DIFF_NORMAL,
    last_enemy_hit=Vec2(276.0, 300.0),
    rng_float=lambda: 0.5,
)


def _tick_through(b: Bomb, ctx: BombContext, timer: int) -> None:
    """tick 到处理完 bombTimer==timer (start 已处理 timer 0)。"""
    while b.timer <= timer:
        b.tick(ctx)


# ---- ReimuB 非集中: 结界光束 (BombData.cpp:512-601) ----


def test_reimu_b_unfocused_init() -> None:
    b = _started_bomb(character=CHAR_REIMU_B)
    assert b.duration == 140 and b.invulnerability_timer == 200
    assert b.cherry_drain == 120  # 17000/140=121→120; min 3000/140=21→20
    # 4 条光束锚点在触发帧定格
    assert b.sub_info[0].pos == Vec2(100.0, 224.0)
    assert b.sub_info[1].pos == Vec2(192.0, 300.0)
    assert b.sub_info[2].pos == Vec2(100.0, 224.0)
    assert b.sub_info[3].pos == Vec2(192.0, 300.0)
    # 首帧 (timer==0) 不布置任何盒 (C++ if/else)
    assert len(b.clear_boxes) == 0
    assert b.damage_boxes[0].size.x == 0.0


def test_reimu_b_unfocused_beams() -> None:
    b = _started_bomb(character=CHAR_REIMU_B)
    b.tick(CTX)  # timer==1 (奇数): 段移到锚点 + 伤害盒
    assert len(b.clear_boxes) == 4
    # SpawnBombProjectile: 62×448 竖 / 384×62 横, lifetime=0
    assert [c.pos_z for c in b.clear_boxes] == [62.0, 384.0, 62.0, 384.0]
    assert [c.size.x for c in b.clear_boxes] == [448.0, 62.0, 448.0, 62.0]
    assert all(
        c.lifetime == 0 and c.item_type == ITEM_POINT_BULLET for c in b.clear_boxes
    )
    assert b.clear_boxes[0].pos == Vec2(100.0, 224.0)  # 竖束锚点
    assert b.clear_boxes[1].pos == Vec2(192.0, 300.0)  # 横束锚点
    # 伤害盒 size=(段宽,段高), lifetime=16
    assert b.damage_boxes[0].size == Vec2(62.0, 448.0)
    assert b.damage_boxes[1].size == Vec2(384.0, 62.0)
    assert b.damage_boxes[0].pos == Vec2(100.0, 224.0)
    assert b.damage_boxes[1].pos == Vec2(192.0, 300.0)
    assert all(b.damage_boxes[i].lifetime == 16 for i in range(4))
    b.tick(CTX)  # timer==2 (偶数): 段留在玩家位置, 伤害盒不刷新
    assert all(c.pos == CTX.player_pos for c in b.clear_boxes)
    assert b.damage_boxes[0].size.x == 0.0
    assert len(b.clear_boxes) == 4  # lifetime=0 槽位复用, 不无限增长


def test_reimu_b_unfocused_lifecycle() -> None:
    b = _started_bomb(character=CHAR_REIMU_B)
    ticks = 0
    while b.is_in_use:
        b.tick(CTX)
        ticks += 1
        assert b.drain_applied == 120 and b.invulnerable
    assert ticks == 140
    assert EVENT_END_PLAYER_SPELLCARD in b.events


# ---- ReimuB 集中: 大结界圆 (BombData.cpp:645-694) ----


def test_reimu_b_focused() -> None:
    b = _started_bomb(character=CHAR_REIMU_B, focus=True)
    assert b.duration == 190 and b.invulnerability_timer == 250
    assert b.cherry_drain == 80  # 17000/190=89→80; min 3000/190=15→10
    assert b.move_speed_multiplier == pytest.approx(0.4)
    assert b.start_pos == CTX.player_pos
    # 首帧 SpawnBombEffect(192, 0.384, 210)
    circle = b.clear_boxes[0]
    assert circle.size.y == pytest.approx(192.0)
    assert circle.growth == pytest.approx(0.384) and circle.lifetime == 210
    assert b.damage_boxes[0].size.x == 0.0  # 首帧无伤害盒
    b.tick(CTX)  # timer==1: 伤害盒 256×256/18 钉在 startPos
    box = b.damage_boxes[0]
    assert box.size == Vec2(256.0, 256.0) and box.lifetime == 18
    assert box.pos == Vec2(100.0, 300.0)
    assert circle.lifetime == 209 and circle.size.y == pytest.approx(192.384)
    # 跑完 190 帧: 移速复位, 清弹圆比 bomb 多活 20 帧
    ticks = 1
    while b.is_in_use:
        b.tick(CTX)
        ticks += 1
    assert ticks == 190 and b.move_speed_multiplier == 1.0
    assert circle.active and circle.lifetime == 20
    assert circle.size.y == pytest.approx(192.0 + 0.384 * 190)
    # bomb 结束后 UpdateBombProjectiles 仍每帧推进清弹盒 (Player.cpp:2231)
    b.tick(CTX)
    assert circle.lifetime == 19 and b.drain_applied == 0


# ---- MarisaA 非集中: 星尘 (BombData.cpp:690-770) ----


def test_marisa_a_unfocused() -> None:
    b = _started_bomb(character=CHAR_MARISA_A)
    assert b.duration == 200 and b.invulnerability_timer == 250
    assert b.cherry_drain == 150  # 30000/200=150; min 8000/200=40
    # 8 星从玩家以 2px/帧 向 8 方向
    for i in range(8):
        assert b.sub_info[i].pos == CTX.player_pos
    assert b.sub_info[0].vel == Vec2(2.0, 0.0)
    assert b.sub_info[2].vel.x == pytest.approx(0.0, abs=1e-9)
    assert b.sub_info[2].vel.y == pytest.approx(2.0)
    b.tick(CTX)  # timer==1 (%3!=0): 移动 + 伤害盒/清弹圆
    assert b.sub_info[0].pos == Vec2(102.0, 300.0)
    box = b.damage_boxes[0]
    assert box.size == Vec2(128.0, 128.0) and box.lifetime == 8
    assert box.pos == Vec2(102.0, 300.0)
    star_clear = [c for c in b.clear_boxes if c.size.y == pytest.approx(96.0)]
    assert len(star_clear) == 8  # 每星一个 SpawnBombEffect(96, 0, 0)
    _tick_through(b, CTX, 3)  # timer==3 (%3==0): 停一拍
    assert b.damage_boxes[0].size.x == 0.0
    assert not any(c.active for c in b.clear_boxes)
    assert b.sub_info[0].pos == Vec2(106.0, 300.0)  # 移动不停
    ticks = 0
    while b.is_in_use:
        b.tick(CTX)
        ticks += 1
    assert ticks == 197  # 已 tick 3 帧; 共 200


# ---- MarisaA 集中: 银河 (BombData.cpp:779-891) ----


def test_marisa_a_focused_spawn() -> None:
    b = _started_bomb(character=CHAR_MARISA_A, focus=True, ctx=CTX_RNG)
    assert b.duration == 260 and b.invulnerability_timer == 310
    assert b.cherry_drain == 120  # 33000/260=126→120; min 9000/260=34→30
    assert b.move_speed_multiplier == pytest.approx(0.4)
    # 首帧 (timer==0) 即放 i=0: rng=0.5 → 角 -1.5707964 (f32 近似 -pi/2), 初速 -5 (向下), 加速 0.24 向上
    sub = b.sub_info[0]
    assert sub.state == 1
    assert sub.vel.x == pytest.approx(0.0, abs=1e-5)
    assert sub.vel.y == pytest.approx(4.76)  # -5 起, 当帧 +0.24
    assert sub.accel_vec.y == pytest.approx(-0.24)
    assert sub.pos.x == pytest.approx(100.0) and sub.pos.y == pytest.approx(305.0)
    box = b.damage_boxes[0]
    assert box.size == Vec2(128.0, 128.0) and box.lifetime == 12
    assert b.sub_info[1].state == 0
    _tick_through(b, CTX_RNG, 6)  # timer==6 放第二颗 (每 6 帧)
    assert b.sub_info[1].state == 1
    _tick_through(b, CTX_RNG, 7)  # timer==7 不放
    assert b.sub_info[2].state == 0


def test_marisa_a_focused_damage_cap_and_despawn() -> None:
    b = _started_bomb(character=CHAR_MARISA_A, focus=True, ctx=CTX_RNG)
    # 累计伤害 >=80 → 该星停刷伤害盒 (BombData.cpp:874-880)
    b.damage_boxes[0].damage = 80
    b.tick(CTX_RNG)
    assert b.damage_boxes[0].size.x == 0.0
    assert b.damage_boxes[0].lifetime == 12  # 残留 lifetime 不清
    # 另起: y<-256 出界消 (rng=0.5 轨道约 93 帧出界)
    b2 = _started_bomb(character=CHAR_MARISA_A, focus=True, ctx=CTX_RNG)
    _tick_through(b2, CTX_RNG, 120)
    assert b2.sub_info[0].state == 0
    ticks = 0
    while b2.is_in_use:
        b2.tick(CTX_RNG)
        ticks += 1
    assert ticks == 260 - 120  # 已 tick 120 帧
    assert b2.move_speed_multiplier == 1.0


# ---- MarisaB 非集中: 旋转激光 (BombData.cpp:952-1048) ----


def test_marisa_b_unfocused() -> None:
    b = _started_bomb(character=CHAR_MARISA_B)
    assert b.duration == 300 and b.invulnerability_timer == 300
    assert b.cherry_drain == 110  # 35000/300=116→110; min 8000/300=26→20
    assert b.move_speed_multiplier == pytest.approx(0.4)
    assert b.start_pos == CTX.player_pos
    # 3 臂初始角 i*2pi/3 - pi/2
    assert b.sub_info[0].accel == pytest.approx(-math.pi / 2)
    assert b.sub_info[1].accel == pytest.approx(2 * math.tau / 6 - math.pi / 2)
    assert b.sub_info[2].accel == pytest.approx(4 * math.tau / 6 - math.pi / 2)
    assert b.damage_boxes[0].size.x == 0.0  # 首帧无盒
    b.tick(CTX)  # timer==1: startPos.x=100<192 → 正转 pi/9000
    d = math.pi / 9000.0
    assert b.sub_info[0].accel == pytest.approx(-math.pi / 2 + d)
    # 每臂 6 盒, offset=32 起每 256/5 一个, 128×128/lifetime=10
    for i in range(3):
        for j in range(6):
            box = b.damage_boxes[i * 6 + j]
            assert box.size == Vec2(128.0, 128.0) and box.lifetime == 10
    box0 = b.damage_boxes[0]
    assert box0.pos.x == pytest.approx(100.0 + 32.0 * math.sin(d), abs=1e-4)
    assert box0.pos.y == pytest.approx(300.0 - 32.0 * math.cos(d), abs=1e-4)
    box1 = b.damage_boxes[1]
    assert box1.pos.y == pytest.approx(300.0 - 83.2 * math.cos(d), abs=1e-4)
    assert b.damage_boxes[18].size.x == 0.0  # 只用 18 个槽
    lasers = [c for c in b.clear_boxes if c.size.y == pytest.approx(64.0)]
    assert len(lasers) == 18  # 每盒一个 SpawnBombEffect(64, 0, 0)
    # startPos 在右半 → 反转
    ctx_r = BombContext(
        player_pos=Vec2(300.0, 300.0),
        cherry=101000.0,
        cherry_start=1000.0,
        difficulty=DIFF_NORMAL,
    )
    b2 = _started_bomb(character=CHAR_MARISA_B, ctx=ctx_r)
    b2.tick(ctx_r)
    assert b2.sub_info[0].accel == pytest.approx(-math.pi / 2 - d)
    ticks = 0
    while b.is_in_use:
        b.tick(CTX)
        ticks += 1
    assert ticks == 299 and b.move_speed_multiplier == 1.0


# ---- MarisaB 集中: Master Spark (BombData.cpp:1104-1170) ----


def test_marisa_b_focused() -> None:
    b = _started_bomb(character=CHAR_MARISA_B, focus=True)
    assert b.duration == 340 and b.invulnerability_timer == 390
    assert b.cherry_drain == 120  # 41000/340=120; min 10000/340=29→20
    assert b.move_speed_multiplier == pytest.approx(0.2)
    b.tick(CTX)  # timer==1 (%4!=0): 全屏纵束
    box = b.damage_boxes[0]
    assert box.size == Vec2(384.0, 300.0)  # 宽 384 × 高 player.y
    assert box.pos == Vec2(192.0, 150.0)  # (192, player.y/2)
    assert box.lifetime == 23
    # SpawnBombProjectile: 线性段 宽 384 高 300 @ (192,150), lifetime=0
    seg = b.clear_boxes[0]
    assert seg.pos_z == pytest.approx(384.0) and seg.size.x == pytest.approx(300.0)
    assert seg.pos == Vec2(192.0, 150.0) and seg.lifetime == 0
    _tick_through(b, CTX, 4)  # timer==4 (%4==0): 停一拍
    assert b.damage_boxes[0].size.x == 0.0
    assert not any(c.active for c in b.clear_boxes)
    ticks = 0
    while b.is_in_use:
        b.tick(CTX)
        ticks += 1
    assert ticks == 336 and b.move_speed_multiplier == 1.0  # 已 tick 4 帧


# ---- SakuyaA 非集中: 无差别飞刀 (BombData.cpp:1201-1290) ----


def test_sakuya_a_unfocused_spawn() -> None:
    b = _started_bomb(character=CHAR_SAKUYA_A, ctx=CTX_RNG)
    assert b.duration == 160 and b.invulnerability_timer == 210
    assert b.cherry_drain == 170  # 28000/160=175→170; min 6000/160=37→30
    assert b.start_pos == CTX_RNG.player_pos
    _tick_through(b, CTX_RNG, 59)
    assert all(b.sub_info[i].state == 0 for i in range(96))  # timer<60 无刀
    b.tick(CTX_RNG)  # timer==60: 每帧至多 5 把
    for i in range(5):
        assert b.sub_info[i].state == 1
    assert b.sub_info[5].state == 0
    # rng=0.5: 角 0, 初速 8.5, 加速 0.15, 漂移 0; 初始 pos=startPos+24*dir
    sub = b.sub_info[0]
    assert sub.angle == pytest.approx(0.0)
    assert sub.speed == pytest.approx(8.5) and sub.accel == pytest.approx(0.15)
    assert sub.angle_drift == pytest.approx(0.0)
    assert sub.pos == Vec2(124.0, 300.0)
    assert b.damage_boxes[0].damage == 0
    b.tick(CTX_RNG)  # timer==61: 移动 (speed 8.5+0.15) + 伤害盒
    assert sub.pos.x == pytest.approx(132.65) and sub.pos.y == pytest.approx(300.0)
    box = b.damage_boxes[0]
    assert box.size == Vec2(24.0, 24.0) and box.lifetime == 10
    assert box.pos.x == pytest.approx(132.65)
    knife_clear = [c for c in b.clear_boxes if c.size.y == pytest.approx(32.0)]
    assert len(knife_clear) >= 5  # 每刀一个 SpawnBombEffect(32, 0, 0)


def test_sakuya_a_unfocused_pin_and_despawn() -> None:
    b = _started_bomb(character=CHAR_SAKUYA_A, ctx=CTX_RNG)
    _tick_through(b, CTX_RNG, 79)  # 96 把到 timer 79 已全部 spawn 过
    assert b.sub_info[95].state == 1
    sub = b.sub_info[0]
    # 累计伤害 >=30 → 刀钉住: 不移动不刷盒, damage=999 (BombData.cpp:1261-1272)
    b.damage_boxes[0].damage = 30
    pos_before = sub.pos
    b.tick(CTX_RNG)
    assert sub.pos == pos_before
    assert b.damage_boxes[0].damage == 999 and b.damage_boxes[0].size.x == 0.0
    # 出界 (IsInBounds 64×64, GameManager.cpp:42-65) → 消失
    sub.pos = Vec2(500.0, 300.0)  # 500-32 > 384
    b.tick(CTX_RNG)
    assert sub.state == 0
    ticks = 0
    while b.is_in_use:
        b.tick(CTX_RNG)
        ticks += 1
    assert ticks == 160 - 81  # 已 tick 81 帧


# ---- SakuyaA 集中: 杀人玩偶 停时悬停 (BombData.cpp:1333-1473) ----


def test_sakuya_a_focused_spawn() -> None:
    b = _started_bomb(character=CHAR_SAKUYA_A, focus=True, ctx=CTX_RNG_ENEMY)
    assert b.duration == 250 and b.invulnerability_timer == 290
    assert b.cherry_drain == 110  # 29000/250=116→110; min 6500/250=26→20
    assert b.move_speed_multiplier == pytest.approx(0.3)
    _tick_through(b, CTX_RNG_ENEMY, 19)
    assert b.sub_info[0].state == 0  # timer<20 无刀
    b.tick(CTX_RNG_ENEMY)  # timer==20: i%48==0 → i=0 与 i=48 两把
    assert b.sub_info[0].state == 1 and b.sub_info[48].state == 1
    assert b.sub_info[1].state == 0
    sub = b.sub_info[0]
    # rng=0.5: 角 -pi, speed 1.0, accel 0.08, 漂移恒 -0.15707964 (GetRandomU16InRange(1)==0)
    assert sub.angle == pytest.approx(-math.pi)
    assert sub.angle_drift == pytest.approx(-0.15707964)
    # 初始 pos=player+24*dir=(76,300); 当帧 speed 1.08 再移 → (74.92,300)
    assert sub.speed == pytest.approx(1.08)
    assert sub.pos.x == pytest.approx(74.92) and sub.pos.y == pytest.approx(300.0)
    assert sub.sub_timer == 1
    box = b.damage_boxes[0]
    assert box.size == Vec2(24.0, 24.0) and box.lifetime == 22
    # timer 114 放最后两把 (i=47,95), 共 96
    _tick_through(b, CTX_RNG_ENEMY, 114)
    assert b.sub_info[95].state == 1


def test_sakuya_a_focused_hover_and_aim() -> None:
    b = _started_bomb(character=CHAR_SAKUYA_A, focus=True, ctx=CTX_RNG_ENEMY)
    _tick_through(b, CTX_RNG_ENEMY, 49)  # sub_timer 已到 30 边界前
    sub = b.sub_info[0]
    assert sub.sub_timer == 30
    pos_before = sub.pos
    b.tick(CTX_RNG_ENEMY)  # sub_timer==30 → 停时悬停: vel=0, 只转角
    assert sub.vel == Vec2(0.0, 0.0) and sub.pos == pos_before
    # 漂移 -0.157/帧; -pi-0.157 经 AddNormalizeAngle 绕回 (-pi, pi]
    assert sub.angle == pytest.approx(math.pi - 0.15707964)
    _tick_through(b, CTX_RNG_ENEMY, 89)
    b.tick(CTX_RNG_ENEMY)  # sub_timer==70 → 瞄 positionOfLastEnemyHit, speed=14
    assert sub.speed == pytest.approx(14.08)  # 14 + accel 0.08
    assert sub.vel.x > 0.0  # 敌在右 (276 > pos.x≈7.7)
    assert sub.vel.y == pytest.approx(0.0, abs=1e-3)


def test_sakuya_a_focused_pin_and_lifecycle() -> None:
    b = _started_bomb(character=CHAR_SAKUYA_A, focus=True, ctx=CTX_RNG_ENEMY)
    _tick_through(b, CTX_RNG_ENEMY, 20)
    sub = b.sub_info[0]
    # 命中 (damage != 0) → 刀钉住: 不移动不刷盒, damage=999 (BombData.cpp:1439-1451)
    b.damage_boxes[0].damage = 22
    pos_before = sub.pos
    b.tick(CTX_RNG_ENEMY)
    assert sub.pos == pos_before
    assert b.damage_boxes[0].damage == 999 and b.damage_boxes[0].size.x == 0.0
    ticks = 0
    while b.is_in_use:
        b.tick(CTX_RNG_ENEMY)
        ticks += 1
    assert ticks == 250 - 21
    assert b.move_speed_multiplier == 1.0


# ---- SakuyaB 非集中: 完美方阵 停时 (BombData.cpp:1502-1598) ----


def test_sakuya_b_unfocused() -> None:
    b = _started_bomb(character=CHAR_SAKUYA_B)
    assert b.duration == 160 and b.invulnerability_timer == 260
    assert b.cherry_drain == 160  # 26000/160=162→160; min 5500/160=34→30
    assert b.move_speed_multiplier == pytest.approx(2.0)
    # 停时: 首帧 + timer 60/120 各一次 StopBulletMovement
    assert b.events.count(EVENT_STOP_BULLET_MOVEMENT) == 1
    _tick_through(b, CTX, 29)
    assert all(b.sub_info[i].state == 0 for i in range(4))
    b.tick(CTX)  # timer==30: 4 方阵视觉锚点激活; 30%4!=0 无伤害盒
    assert all(b.sub_info[i].state == 1 for i in range(4))
    assert b.damage_boxes[0].size.x == 0.0
    _tick_through(b, CTX, 32)  # timer==32 (%4==0): 全场伤害盒
    box = b.damage_boxes[0]
    assert box.pos == Vec2(192.0, 224.0)
    assert box.size == Vec2(352.0, 416.0) and box.lifetime == 3
    ticks = 32  # 已 tick 到 timer 32
    b.events.clear()
    while b.is_in_use:
        b.tick(CTX)
        ticks += 1
    assert ticks == 160
    # timer 60/120 的停时事件
    assert b.events.count(EVENT_STOP_BULLET_MOVEMENT) == 2
    assert b.move_speed_multiplier == 1.0
    # 结束帧 SpawnBombEffect(player, 800, 0, 0)
    assert b.clear_boxes[0].pos == CTX.player_pos
    assert b.clear_boxes[0].size.y == pytest.approx(800.0)
    assert b.clear_boxes[0].lifetime == 0


# ---- SakuyaB 集中: 私人方阵 追踪领域 (BombData.cpp:1633-1724) ----


def test_sakuya_b_focused_init_and_track() -> None:
    b = _started_bomb(character=CHAR_SAKUYA_B, focus=True)
    assert b.duration == 300 and b.invulnerability_timer == 420
    assert b.cherry_drain == 90  # 29000/300=96→90; min 6000/300=20
    assert b.move_speed_multiplier == pytest.approx(1.5)
    # 首帧即有: 领域 96 清弹圆 + 伤害盒 160×160/lifetime=1 @ sub0.pos
    assert b.sub_info[0].state == 1 and b.sub_info[1].state == 1
    assert b.clear_boxes[0].size.y == pytest.approx(96.0)
    box = b.damage_boxes[0]
    assert box.size == Vec2(160.0, 160.0) and box.lifetime == 1
    assert box.pos == CTX.player_pos
    # 追踪: 玩家移开后领域以 (playerPos-pos)/1700 加速 (伤害盒慢一拍, 取移动前 pos)
    ctx2 = BombContext(
        player_pos=Vec2(200.0, 200.0),
        cherry=101000.0,
        cherry_start=1000.0,
        difficulty=DIFF_NORMAL,
    )
    b.tick(ctx2)
    accel = Vec2(100.0 / 1700.0, -100.0 / 1700.0)
    sub = b.sub_info[0]
    assert sub.vel.x == pytest.approx(accel.x) and sub.vel.y == pytest.approx(accel.y)
    assert sub.pos.x == pytest.approx(100.0 + accel.x)
    assert sub.pos.y == pytest.approx(300.0 + accel.y)
    assert b.damage_boxes[0].pos == CTX.player_pos  # 慢 sub 一拍
    # timer 40/100 停时
    _tick_through(b, ctx2, 100)
    assert b.events.count(EVENT_STOP_BULLET_MOVEMENT) == 2


def test_sakuya_b_focused_end_clear_box_quirk() -> None:
    b = _started_bomb(character=CHAR_SAKUYA_B, focus=True)
    ticks = 0
    while b.is_in_use:
        b.tick(CTX)
        ticks += 1
    assert ticks == 300 and b.move_speed_multiplier == 1.0
    # 结束: SpawnBombEffect(800,0,0) 落在 0 槽后被覆写为线性段
    # (192,224) 宽448×高512, size.y=800 残留 (BombData.cpp:1646-1655, ZUN quirk)
    box0 = b.clear_boxes[0]
    assert box0.pos == Vec2(192.0, 224.0)
    assert box0.pos_z == pytest.approx(448.0)
    assert box0.size.x == pytest.approx(512.0) and box0.size.y == pytest.approx(800.0)
    assert box0.lifetime == 0
    # pos_z != 0 → 判定走线性段 (448×512), 而非 800 圆
    assert box0.hits(Vec2(192.0, 224.0), Vec2(8.0, 8.0))
    assert box0.hits(Vec2(192.0 + 227.0, 224.0), Vec2(8.0, 8.0))  # 227<228
    assert not box0.hits(Vec2(192.0 + 300.0, 224.0), Vec2(8.0, 8.0))  # 圆内段外
    # 下一帧 UpdateBombProjectiles 清零 (lifetime<=0)
    b.tick(CTX)
    assert not box0.active
