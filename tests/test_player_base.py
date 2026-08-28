"""PlayerBase 通用骨架测试(engine/player_base.py) —— stub 子类驱动。

钉住作品无关的框架行为: 出生状态机(SPAWNING→INVULNERABLE→ALIVE)、移动与
边界钳制、判定/擦弹 AABB、死亡→结算→重生流程、清弹期事件。th07 专属结算
(樱点/subrank)不在本文件(见 test_player.py)。
"""

from __future__ import annotations

import sys

sys.path.insert(0, r"D:\python_play\Touhou08")

from touhou.engine.player_base import (  # noqa: E402
    BULLET_GRACE_PERIOD,
    RESPAWN_INVULN,
    SPAWN_INVULN,
    SPAWN_TICKS,
    DeathContext,
    DeathSettle,
    KillResult,
    PlayerBase,
    PlayerEventKind,
    PlayerState,
)
from touhou.utils import Vec2  # noqa: E402


class StubPlayer(PlayerBase[DeathContext]):
    """最小可运行实现: 固定移速, 无射击/子机, 死亡结算 power 归 0。"""

    def __init__(self, **kw) -> None:
        kw.setdefault("hitbox_radius", 2.0)
        kw.setdefault("graze_radius", 24.0)
        kw.setdefault("initial_respawn_timer", 30)
        super().__init__(**kw)
        self.grazed_count = 0

    def _current_speeds(self) -> tuple[float, float]:
        return 4.0, 2.0  # (直线, 斜向)

    def _settle_death(self, ctx: DeathContext | None) -> DeathSettle:
        return DeathSettle(bool(ctx is not None and ctx.lives > 0), 0.0)

    def _on_graze(self) -> None:
        self.grazed_count += 1


def make_player(**kw) -> StubPlayer:
    return StubPlayer(**kw)


# ---- 出生状态机 ----
def test_initial_state_is_spawning() -> None:
    p = make_player()
    assert p.state == PlayerState.SPAWNING
    assert p.invulnerability_timer == SPAWN_INVULN
    assert p.respawn_timer == 30


def test_spawning_enters_invulnerable() -> None:
    p = make_player()
    p.step()
    # invulnerabilityTimer>=30 → INVULNERABLE(240) + 60 帧清弹期
    assert p.state == PlayerState.INVULNERABLE
    assert p.invulnerability_timer == RESPAWN_INVULN
    assert p.bullet_grace_period == BULLET_GRACE_PERIOD


def test_invulnerable_counts_down_to_alive() -> None:
    p = make_player()
    p.step()  # → INVULNERABLE(240)
    p.bullet_grace_period = 0  # 免得清弹事件干扰事件断言
    for _ in range(RESPAWN_INVULN):
        p.step()
    assert p.state == PlayerState.ALIVE
    assert p.invulnerability_timer == 0


def test_bullet_grace_period_emits_clear_event() -> None:
    p = make_player()
    p.step()  # 进入 INVULNERABLE, grace=60
    p.step()
    kinds = [e.kind for e in p.events]
    assert PlayerEventKind.REMOVE_ALL_BULLETS in kinds


# ---- 移动 ----
def test_move_straight_and_bounds_clamp() -> None:
    p = make_player(pos=Vec2(192, 400))
    p.step()  # → INVULNERABLE(可移动)
    p.push(1, 0)
    p.step()
    assert p.pos.x == 196.0 and p.pos.y == 400
    # 右边界钳制(默认 bounds 右缘 SCREEN.x-8 = 376)
    p.push(1, 0)
    for _ in range(100):
        p.step()
    assert p.pos.x == 376.0


def test_move_diagonal_uses_diagonal_speed() -> None:
    p = make_player(pos=Vec2(192, 400))
    p.step()
    p.push(1, 1)
    p.step()
    assert p.pos.x == 194.0 and p.pos.y == 402.0  # 斜向速度 2.0


def test_dead_or_spawning_does_not_move() -> None:
    p = make_player(pos=Vec2(192, 400))
    p.invulnerability_timer = SPAWN_TICKS - 1  # 保持 SPAWNING(不触发转入)
    p.push(1, 0)
    p.step()  # SPAWNING: 不移动
    assert p.pos.x == 192.0 and p.state == PlayerState.SPAWNING


# ---- 判定/擦弹 ----
def test_check_killbox_alive_dies() -> None:
    p = make_player()
    p.state = PlayerState.ALIVE
    r = p.check_killbox(p.pos, (4.0, 4.0))
    assert r == KillResult.DEATH and p.state == PlayerState.DEAD


def test_check_killbox_miss_and_invulnerable() -> None:
    p = make_player()
    p.state = PlayerState.ALIVE
    assert p.check_killbox(p.pos + Vec2(100, 0), (4.0, 4.0)) == KillResult.NONE
    p.state = PlayerState.INVULNERABLE
    assert p.check_killbox(p.pos, (4.0, 4.0)) == KillResult.NONE


def test_check_contact_hits_without_dying_when_invulnerable() -> None:
    p = make_player()
    p.state = PlayerState.INVULNERABLE
    assert p.check_contact(p.pos, (4.0, 4.0)) is True
    assert p.state == PlayerState.INVULNERABLE  # 仅命中, 无玩家侧效果


def test_graze_hook_fires_once_per_bullet_geometry() -> None:
    p = make_player()
    p.state = PlayerState.ALIVE
    assert p.check_graze(p.pos + Vec2(30, 0), (4.0, 4.0)) is True  # 24+2+20=46 内
    assert p.grazed_count == 1
    assert p.check_graze(p.pos + Vec2(100, 0), (4.0, 4.0)) is False
    assert p.grazed_count == 1


def test_graze_skipped_when_dead() -> None:
    p = make_player()
    p.state = PlayerState.DEAD
    assert p.check_graze(p.pos, (4.0, 4.0)) is False
    assert p.grazed_count == 0


# ---- 死亡→结算→重生 ----
def test_death_settle_and_respawn_cycle() -> None:
    p = make_player()
    p.state = PlayerState.ALIVE
    p.die()
    assert p.state == PlayerState.DEAD and p.respawn_timer == 30
    seen: list[PlayerEventKind] = []
    for _ in range(30):
        p.step(DeathContext(lives=1))
        seen.extend(e.kind for e in p.events)
    assert PlayerEventKind.DEATH_SETTLE in seen
    assert PlayerEventKind.RESPAWNED in seen
    assert p.state == PlayerState.INVULNERABLE
    assert p.invulnerability_timer == RESPAWN_INVULN


def test_alive_property_compat() -> None:
    """向后兼容派生字段: alive/invuln 的 getter/setter。"""
    p = make_player()
    p.alive = False
    assert p.state == PlayerState.DEAD and not p.alive
    p.alive = True
    assert p.state == PlayerState.ALIVE
    p.invuln = 7
    assert p.invulnerability_timer == 7 and p.invuln == 7
