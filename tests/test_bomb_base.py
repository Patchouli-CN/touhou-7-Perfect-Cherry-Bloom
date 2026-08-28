"""BombBase 通用框架测试(engine/bomb_base.py) —— stub 子类驱动。

钉住作品无关的框架行为: 触发门槛(try_start_bomb)、每帧盒推进
(UpdateBombProjectiles: 伤害盒 size.x 清零 / 清弹盒 lifetime-- 与半径增长)、
资源消耗 hook(_tick_resource_cost)、清弹/伤害盒判定几何。
th07 的 12 套机体炸弹与樱点 drain 不在本文件(见 test_bomb.py)。
"""

from __future__ import annotations

import sys

import msgspec

sys.path.insert(0, r"D:\python_play\Touhou08")

from touhou.engine.bomb_base import (  # noqa: E402
    BOMB_DURATION_PLACEHOLDER,
    BOMB_RESPAWN_PENALTY,
    BOMB_SUBRANK_PENALTY,
    ITEM_POINT_BULLET,
    BombBase,
    BombContext,
    ClearBox,
    DamageBox,
    try_start_bomb,
)
from touhou.utils import Vec2  # noqa: E402


class StubBomb(BombBase[BombContext]):
    """最小机体炸弹: 首帧设 10 帧持续 + 一个伤害盒, 每帧计时, 到时结束。"""

    # _tick_resource_cost 的 in_use 记录(Struct 实例不允许临时属性, 声明为字段)
    cost_calls: list[bool] = msgspec.field(default_factory=list)

    def _calc(self, ctx: BombContext) -> None:
        if self.timer == 0:
            self.duration = 10
            self.invulnerability_timer = 10
            self.damage_boxes[0] = DamageBox(self.start_pos, Vec2(40.0, 40.0), 5)
        self.timer += 1
        if self.timer >= self.duration:
            self.is_in_use = False

    def _tick_resource_cost(self, in_use: bool) -> None:
        self.cost_calls.append(in_use)


class BareBomb(BombBase[BombContext]):
    """不布置任何盒的炸弹(_calc 空操作) —— 用于占位 duration/盒清零测试。"""

    def _calc(self, ctx: BombContext) -> None:
        pass


CTX = BombContext(player_pos=Vec2(100.0, 300.0))


def _start(**overrides) -> StubBomb:
    kw = dict(
        focus=False,
        bombs_remaining=3.0,
        respawn_timer=30,
        initial_respawn_timer=30,
        border_invulnerability_time=0,
        bomb_pressed=True,
        spellcard_active=False,
    )
    kw.update(overrides)
    b = StubBomb()
    b.start_pos = CTX.player_pos
    result = try_start_bomb(b, CTX, **kw)
    assert result.started
    return b


# ---- 触发门槛 (try_start_bomb) ----
def test_start_gating() -> None:
    b = StubBomb()
    base = dict(
        focus=False,
        bombs_remaining=3.0,
        respawn_timer=30,
        initial_respawn_timer=30,
        border_invulnerability_time=0,
        bomb_pressed=True,
        spellcard_active=False,
    )
    assert not try_start_bomb(b, CTX, **{**base, "bomb_pressed": False}).started
    assert not try_start_bomb(b, CTX, **{**base, "bombs_remaining": 0.0}).started
    assert not try_start_bomb(b, CTX, **{**base, "respawn_timer": 0}).started
    assert not try_start_bomb(
        b, CTX, **{**base, "border_invulnerability_time": 5}
    ).started
    assert not b.is_in_use  # 全部拒绝, 未触发


def test_start_success_events() -> None:
    b = StubBomb()
    r = try_start_bomb(
        b,
        CTX,
        focus=True,
        bombs_remaining=3.0,
        respawn_timer=30,
        initial_respawn_timer=30,
        border_invulnerability_time=0,
        bomb_pressed=True,
        spellcard_active=True,
    )
    assert r.started and b.is_in_use and b.is_focus
    assert r.bombs_used_delta == 1 and r.bombs_remaining_delta == -1
    assert r.subrank_delta == -BOMB_SUBRANK_PENALTY
    assert r.respawn_timer == min(30 + BOMB_RESPAWN_PENALTY, 30)  # 封顶 initial
    assert r.spellcard_capture_reset and r.spellcard_used_bomb
    # start() 当帧已跑一次 calc: duration 由占位 999 被机体设定为 10
    assert b.duration == 10 and b.invulnerability_timer == 10 and b.timer == 1
    assert len(b.damage_boxes) == 112 and len(b.sub_info) == 128


def test_no_double_start_while_in_use() -> None:
    b = _start()
    r = try_start_bomb(
        b,
        CTX,
        focus=False,
        bombs_remaining=3.0,
        respawn_timer=30,
        initial_respawn_timer=30,
        border_invulnerability_time=0,
        bomb_pressed=True,
        spellcard_active=False,
    )
    assert not r.started


# ---- 每帧盒推进 (UpdateBombProjectiles) ----
def test_tick_clears_damage_box_width_each_frame() -> None:
    b = _start()
    assert b.damage_boxes[0].size.x == 40.0  # start 当帧 calc 布置
    b.tick(CTX)  # 帧首清零; stub calc 仅首帧布置
    assert b.damage_boxes[0].size.x == 0.0


def test_bomb_ends_after_duration_and_hook_called() -> None:
    b = _start()
    alive = [b.tick(CTX) for _ in range(8)]
    assert all(alive)
    assert not b.tick(CTX)  # 第 9 帧 calc 置 is_in_use=False(start 当帧已计 1)
    # 资源消耗 hook: 进行中每帧 True, 结束后 False
    assert b.cost_calls == [True] * 9
    assert not b.tick(CTX)  # 已结束仍推进清弹盒, 返回 False
    assert b.cost_calls[-1] is False


def test_clear_box_tick_and_growth() -> None:
    c = ClearBox(Vec2(100, 100), Vec2(0.0, 16.0), 3, ITEM_POINT_BULLET, growth=2.0)
    assert c.active
    c.tick()
    assert c.lifetime == 2 and c.size.y == 18.0
    c.tick()
    c.tick()
    assert c.lifetime == 0
    c.tick()  # lifetime<=0 → 清零
    assert c.size.y == 0.0 and c.pos_z == 0.0 and not c.active


# ---- 判定几何 ----
def test_damage_to_and_hits() -> None:
    b = _start()
    total = b.damage_to(Vec2(100.0, 300.0), Vec2(10.0, 10.0))  # 盒中心重叠
    assert total == 5  # lifetime 即每帧伤害
    assert b.damage_boxes[0].damage == 5  # 累计
    assert b.hits(Vec2(100.0, 300.0), Vec2(10.0, 10.0))
    assert not b.hits(Vec2(500.0, 300.0), Vec2(10.0, 10.0))
    assert b.damage_to(Vec2(500.0, 300.0), Vec2(10.0, 10.0)) == 0


def test_check_bomb_graze_circle_and_segment() -> None:
    b = StubBomb()
    # 圆: dist² < size.y²
    b.clear_boxes.append(ClearBox(Vec2(100, 100), Vec2(0.0, 30.0), 5, 6))
    assert b.check_bomb_graze(Vec2(110, 100), Vec2(4, 4)) == 2
    assert b.item_type == 6  # 透出命中盒的掉落类型
    assert b.check_bomb_graze(Vec2(200, 100), Vec2(4, 4)) == 0
    # 线性段: 宽=pos_z, 高=size.x
    b.clear_boxes.append(ClearBox(Vec2(300, 100), Vec2(20.0, 0.0), 5, 8, pos_z=40.0))
    assert b.check_bomb_graze(Vec2(310, 100), Vec2(4, 4)) == 2
    assert b.item_type == 8
    assert b.check_bomb_graze(Vec2(310, 200), Vec2(4, 4)) == 0


def test_duration_placeholder_constant() -> None:
    """触发时占位 duration (Player.cpp:1736) = 999, 由机体 calc 首帧覆盖。"""
    b = BareBomb()  # _calc 不覆盖占位
    b.start(focus=False, ctx=CTX)
    assert b.duration == BOMB_DURATION_PLACEHOLDER == 999
