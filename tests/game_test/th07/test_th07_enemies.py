"""敌人体术判定测试 —— EnemyHost.contact_hits。

数值权威: th07 EnemyManager.cpp:754-775 (OnUpdate 体术段) +
Enemy::CheckBulletPlayerCollision (EnemyManager.cpp:576-595) +
Player::CalcKillboxCollision (Player.cpp:1003-1040)。

要点: 判定盒 = hitboxSize/1.5 (本体) + trail 历史节点; 命中含玩家无敌中的
纯相交(C++ 返回 1), canDie && !isBoss && !isProjectile 的敌人 life -= 10;
普通敌人体术无擦弹, isProjectile 敌人 timer%6==0 时按 hitboxSize/0.7 擦弹。
"""

from __future__ import annotations

import sys

sys.path.insert(0, r"D:\python_play\Touhou08")

from touhou.engine.ecl import Vec3  # noqa: E402
from touhou.games.th07.ecl_vm import EclMachineTh07 as EclMachine  # noqa: E402
from touhou.engine.enemies import (  # noqa: E402
    EnemyHost,
    EclEnemy,
    Targeting,
    settle_damage,
)
from touhou.engine.player_base import PlayerState  # noqa: E402
from touhou.games.th07.player import PlayerEventKind  # noqa: E402
from touhou.utils import Vec2  # noqa: E402
from tests.game_test.th07.test_th07_ecl import _instr, build_ecl  # noqa: E402
from tests.game_test.th07.test_th07_ecl import EclOpcode as OP  # noqa: E402
from tests.game_test.th07.test_th07_player import make_player  # noqa: E402

# make_player 的 SD: hitbox_radius=4 → 判定半宽 2; grab 48 → 擦弹半宽 24


def _host_with_enemy_at(
    pos: Vec2, *, life: int = 100, radius: float = 24.0, **flags
) -> tuple[EnemyHost, object]:
    host = EnemyHost()
    e = host.spawn(path=[pos], life=life, speed=0.0, radius=radius)
    for k, v in flags.items():
        setattr(e, k, v)
    return host, e


def test_contact_hit_kills_player_and_damages_enemy() -> None:
    """本体命中: 玩家 ALIVE → 死; 敌人(非 boss/弹) life -= 10 (C++:589-594)。"""
    p = make_player()
    host, e = _host_with_enemy_at(p.pos)
    assert host.contact_hits(p) is True
    assert p.state == PlayerState.DEAD
    assert e.life == 90


def test_contact_hitbox_is_hitbox_div_1_5() -> None:
    """判定盒 = hitboxSize/1.5 (C++:588): 半宽 48/1.5/2=16, 加玩家半宽 2 →
    |dx|<=18 命中, 19 不中。"""
    p = make_player()
    host, _ = _host_with_enemy_at(p.pos + Vec2(18.0, 0.0))
    assert host.contact_hits(p) is True
    p2 = make_player()
    host2, _ = _host_with_enemy_at(p2.pos + Vec2(19.0, 0.0))
    assert host2.contact_hits(p2) is False
    assert p2.state == PlayerState.ALIVE


def test_contact_exemptions() -> None:
    """豁免 (C++:754-756): hasNoCollision / hasContactHitbox=0 不判定。"""
    p = make_player()
    host, _ = _host_with_enemy_at(p.pos, has_no_collision=1)
    assert host.contact_hits(p) is False
    host, _ = _host_with_enemy_at(p.pos, has_contact_hitbox=0)
    assert host.contact_hits(p) is False
    host, _ = _host_with_enemy_at(p.pos, invisible_on_bomb=1)
    assert host.contact_hits(p) is False
    assert p.state == PlayerState.ALIVE


def test_contact_boss_and_projectile_lose_no_life() -> None:
    """命中扣血仅对 canDie && !isBoss && !isProjectile (C++:591-593)。"""
    p = make_player()
    host, boss = _host_with_enemy_at(p.pos, is_boss=True)
    assert host.contact_hits(p) is True
    assert boss.life == 100  # boss 撞人不掉血
    p2 = make_player()
    host2, proj = _host_with_enemy_at(p2.pos, is_projectile=1)
    assert host2.contact_hits(p2) is True
    assert proj.life == 100


def test_contact_hit_while_invulnerable_still_damages_enemy() -> None:
    """玩家无敌中相交: C++ CalcKillboxCollision 仍返回 1 → 玩家不死, 敌人 -10。"""
    p = make_player()
    p.state = PlayerState.INVULNERABLE
    host, e = _host_with_enemy_at(p.pos)
    assert host.contact_hits(p) is False  # 玩家没死
    assert p.state == PlayerState.INVULNERABLE
    assert e.life == 90


def test_contact_kill_settles_in_shoot_hits() -> None:
    """体术把血撞空 → life<=0 击杀分支在门槛外 (C++:941), 由 shoot_hits 结算。"""
    p = make_player()
    p.state = PlayerState.INVULNERABLE  # 不死, 只看敌人侧
    host, e = _host_with_enemy_at(p.pos, life=5)
    assert host.contact_hits(p) is False
    assert e.life == -5
    _, kills = host.shoot_hits(
        p, Targeting(), is_focus=False, is_sakuya=False, bomb_in_use=False, stage=1
    )
    assert kills == [e]
    assert not e.alive


def test_contact_no_graze_for_normal_enemy() -> None:
    """普通敌人体术无擦弹 (CheckBulletPlayerCollision 的擦弹分支要 isProjectile)。"""
    p = make_player()
    host, e = _host_with_enemy_at(p.pos)
    e._tick = 6  # 即使对齐 %6 也不擦
    host.contact_hits(p)
    assert not [ev for ev in p.events if ev.kind == PlayerEventKind.GRAZE]


def test_contact_projectile_grazes_every_6_frames() -> None:
    """isProjectile 敌人: timer%6==0 时按 hitboxSize/0.7 擦弹 (C++:582-587)。"""
    p = make_player()
    # 不相撞(体术盒半宽 16+2=18)但在擦弹圈(48/0.7/2≈34.3+20+24≈78)内
    host, e = _host_with_enemy_at(p.pos + Vec2(40.0, 0.0), is_projectile=1)
    e._tick = 5  # %6 != 0 → 不擦
    host.contact_hits(p)
    assert not [ev for ev in p.events if ev.kind == PlayerEventKind.GRAZE]
    e._tick = 6  # %6 == 0 → 擦
    host.contact_hits(p)
    assert [ev for ev in p.events if ev.kind == PlayerEventKind.GRAZE]


def _trail_enemy(player_pos: Vec3, trail: tuple[int, int, int, int, int]) -> EclEnemy:
    """裸 EclEnemy: 本体在远处, trail 历史节点 1 放在玩家处。"""
    f = build_ecl([_instr(0, OP.UNIMP)])
    e = EclEnemy(EclMachine(f))
    e.state.life = 100
    e.state.pos = Vec3(0.0, -500.0, 0.0)  # 本体远离玩家
    e.state.hitbox_size.set(48.0, 48.0, 48.0)
    e.state.trail = trail
    e.state.trail_history = [Vec3(-999.0, 0.0, 0.0)] * trail[1]
    e.state.trail_history[1] = player_pos.copy()
    return e


def test_contact_trail_history_node_hits() -> None:
    """trail 敌人: 历史节点也做体术判定 (C++:760-774, j=1..trailInterval 步进 6)。"""
    p = make_player()
    e = _trail_enemy(Vec3(p.pos.x, p.pos.y, 0.0), (25, 32, 16, 1, 0))
    host = EnemyHost()
    host.add(e)
    assert host.contact_hits(p) is True
    assert p.state == PlayerState.DEAD
    assert e.life == 90  # 100 - 10 (trail 节点命中也扣)


def test_contact_trail_node_beyond_interval_not_checked() -> None:
    """节点 j >= trailInterval 不判定 (C++:763 循环上界)。"""
    p = make_player()
    e = _trail_enemy(Vec3(-999.0, 0.0, 0.0), (25, 32, 16, 1, 0))
    e.state.trail_history[17] = Vec3(p.pos.x, p.pos.y, 0.0)  # j=17 >= 16
    host = EnemyHost()
    host.add(e)
    assert host.contact_hits(p) is False
    assert p.state == PlayerState.ALIVE


def test_freeze_keeps_timer_and_ticks_invuln() -> None:
    """freeze_ecl_during_bombs + 宿主 frozen: 整帧跳过, timer 净不变,
    invincibilityTimer 照减 (EnemyManager.cpp:658-663 → LAB_00421da7 尾部
    timer++/invuln-- :1096-1100)。回归: 旧实现 timer 净 -1/帧, 死亡频繁时
    boss 计时器倒走, 超时类阶段永不触发 (8 面末符卡卡死)。"""

    class _Host:
        frozen = True

    f = build_ecl([_instr(0, OP.SET_LIFE, (100,)), _instr(9999, OP.UNIMP)])
    e = EclEnemy(EclMachine(f), host=_Host())
    e.machine.call_sub(0)
    st = e.state
    st.freeze_ecl_during_bombs = 1
    st.invincibility_timer = 5
    st.timer = 10
    e.step()
    assert st.timer == 10, "freeze 帧 timer 应净不变(C 尾部 ++ 抵消 --)"
    assert st.invincibility_timer == 4, "freeze 帧 invincibilityTimer 照减"
    e._host.frozen = False
    e.step()
    assert st.timer == 11


class _DamageStubPlayer:
    """calc_damage_to_enemy 依次返回固定值(第 1 次主盒, 第 2 次 graze 盒)。"""

    def __init__(self, *returns: int) -> None:
        self.pos = Vec2(192, 400)
        self._returns = list(returns)

    def calc_damage_to_enemy(self, center, size, *, bomb_active=None):
        return self._returns.pop(0) if self._returns else 0


def _graze_enemy_host() -> tuple[EnemyHost, object]:
    host = EnemyHost()
    e = host.spawn(
        path=[Vec2(192, 100)], life=1000, speed=0.0, graze_size=Vec2(40.0, 40.0)
    )
    return host, e


def test_shoot_hits_graze_extra_without_bomb() -> None:
    """无 bomb: graze 盒追加 grazeDamage/2.5 (10 + 25/2.5 = 20)。"""
    host, e = _graze_enemy_host()
    p = _DamageStubPlayer(10, 25)
    results, _ = host.shoot_hits(
        p, Targeting(), is_focus=True, is_sakuya=False, bomb_in_use=False, stage=1
    )
    assert results[0][1].damage == 20


def test_shoot_hits_bomb_box_hit_skips_graze_extra() -> None:
    """EnemyManager.cpp:783-790: bomb 盒命中 graze 盒 (collisionOut!=0) 时
    跳过 grazeDamage/2.5 额外伤; 未命中/非 bomb 中照常。"""
    # bomb 盒命中 graze 盒 → 跳过
    host, e = _graze_enemy_host()
    p = _DamageStubPlayer(10, 25)
    results, _ = host.shoot_hits(
        p,
        Targeting(),
        is_focus=True,
        is_sakuya=False,
        bomb_in_use=True,
        stage=1,
        bomb_box_hit=lambda pos, full: True,
    )
    assert results[0][1].damage == 10
    # bomb 盒未命中 graze 盒 → 照常
    host, e = _graze_enemy_host()
    p = _DamageStubPlayer(10, 25)
    results, _ = host.shoot_hits(
        p,
        Targeting(),
        is_focus=True,
        is_sakuya=False,
        bomb_in_use=True,
        stage=1,
        bomb_box_hit=lambda pos, full: False,
    )
    assert results[0][1].damage == 20
    # 非 bomb 中: 谓词命中也不跳过 (C++ collisionOut 只在 bomb 中置位)
    host, e = _graze_enemy_host()
    p = _DamageStubPlayer(10, 25)
    results, _ = host.shoot_hits(
        p,
        Targeting(),
        is_focus=True,
        is_sakuya=False,
        bomb_in_use=False,
        stage=1,
        bomb_box_hit=lambda pos, full: True,
    )
    assert results[0][1].damage == 20


def test_mixed_bullet_bomb_damage_split_settlement() -> None:
    """钉住【分路径结算】现状: 符卡中 + used_bomb 同帧子弹与 bomb 盒混合命中时,
    子弹部分走 shoot_hits 的 /7 分支, bomb 盒部分走 _apply_bomb_boxes 的
    /2.5 分支, 各自 int 截断后相加 —— 而非 C++ 的合并成一笔再按
    collisionOut 统一缩放 (Player.cpp:825-938 → EnemyManager.cpp:849-868)。
    偏差: 21+25 混合命中, 本实现 21//7 + int(25/2.5) = 3+10 = 13;
    C++ 合并为 int(46/2.5) = 18。对齐需改帧内作用点与 box.damage 累计
    语义, 故保持分路径 (见 shoot_hits / impl._apply_bomb_boxes 注记)。"""
    # 子弹路径: 符卡中 bomb_damage=False → /7 (min 1)
    host, e = _graze_enemy_host()
    e.life = 100000
    p = _DamageStubPlayer(21, 0)  # 主盒 21, graze 盒 0(bomb 中)
    results, _ = host.shoot_hits(
        p,
        Targeting(),
        is_focus=True,
        is_sakuya=False,
        bomb_in_use=True,
        stage=1,
        spellcard_active=True,
        used_bomb=True,
        bomb_box_hit=lambda pos, full: True,
    )
    bullet_part = results[0][1].damage
    assert bullet_part == 21 // 7 == 3
    # bomb 盒路径: 符卡中 bomb_damage=True + used_bomb → /2.5
    bomb_part = settle_damage(
        25,
        is_boss=True,
        is_focus=False,
        bomb_in_use=True,
        bomb_damage=True,
        stage=1,
        spellcard_active=True,
        used_bomb=True,
    ).damage
    assert bomb_part == int(25 / 2.5) == 10
    # 现状合计 13, 与 C++ 合并口径 18 的偏差即本条测试钉住的语义
    assert bullet_part + bomb_part == 13
    assert int((21 + 25) / 2.5) == 18  # C++ 对照值(文档用, 非实现目标)


def test_targeting_skips_out_of_bounds_enemies() -> None:
    """索敌只锁定版内敌人 (BUGS.md 增量#5, GameManager::IsInBounds 口径):
    飞出版底/未进版的敌人不再被 positionOfLastEnemyHit 的"最靠下"准则
    选中 (追踪弹因此不会锁到版外杂鱼); 版外敌人照常结算伤害。"""
    p = make_player()
    host, inside = _host_with_enemy_at(Vec2(192.0, 200.0))
    host.spawn(path=[Vec2(192.0, 600.0)], life=100, speed=0.0)  # 已飞出版底
    targeting = Targeting()
    results, _ = host.shoot_hits(
        p, targeting, is_focus=False, is_sakuya=False, bomb_in_use=False, stage=1
    )
    assert targeting.position_of_last_enemy_hit == inside.pos
    assert len(results) == 2  # 版外敌人照常受击结算, 只是不做追踪目标


def test_targeting_skips_enemy_not_yet_entered() -> None:
    """版顶上方的入场中敌人 (y+半高<0) 同样不锁定: 只有它在场时索敌保持
    无效值 (-999) (BUGS.md 增量#5)。"""
    p = make_player()
    host, _ = _host_with_enemy_at(Vec2(192.0, -100.0))
    targeting = Targeting()
    host.shoot_hits(
        p, targeting, is_focus=False, is_sakuya=False, bomb_in_use=False, stage=1
    )
    assert targeting.position_of_last_enemy_hit == Vec2(-999.0, -999.0)
    assert not targeting.targeting
