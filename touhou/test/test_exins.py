"""ExIns (24 条 boss 特技) 单元测试: 合成 ECL 指令流驱动 GameEclHost 实现。

语义权威: th07/src/th07/EnemyEclInstr.cpp (各断言注释标 C++ 行号)。
覆盖真实 ecldata 中出现的全部 idx 0..23; 9/15/19/20 为表现侧(只验证分派不炸)。
"""
from __future__ import annotations

import math
import sys

import pytest

sys.path.insert(0, r"D:\python_play\Touhou08")

from touhou.engine.bullet_commands import CmdFlag  # noqa: E402
from touhou.engine.bullets import Aim, BulletWorld, Burst  # noqa: E402
from touhou.engine.ecl import EclMachine, EclOpcode, EclWorld, Vec3  # noqa: E402
from touhou.games.th07.ecl_host import GameEclHost  # noqa: E402
from touhou.engine.enemies import EnemyHost  # noqa: E402
from touhou.games.th07.items import ItemWorld  # noqa: E402
from touhou.engine.lasers import Laser, LaserState, LaserWorld  # noqa: E402
from touhou.utils import Vec2  # noqa: E402
from touhou.test.test_ecl import _f, _instr, build_ecl  # noqa: E402

OP = EclOpcode


def _setup(*subs: list[bytes], difficulty: int = 1):
    """GameEclHost + 待跑 sub0 的 EclMachine (调用方布置世界后 m.step())。"""
    f = build_ecl(*subs)
    world = EclWorld(difficulty=difficulty)
    host = GameEclHost(f, world, enemies=EnemyHost(), bullets=BulletWorld(),
                       lasers=LaserWorld(), items=ItemWorld())
    m = EclMachine(f, world=world, host=host)
    m.enemy.life = 10
    m.start(0)
    return m, host, world


def _ex_machine(idx: int, arg1: int = 0, **kw):
    """sub0: time0 RUN_EX_INS(idx, arg1) → 9999 UNIMP。"""
    return _setup(
        [_instr(0, OP.RUN_EX_INS, (idx, arg1)), _instr(9999, OP.UNIMP)], **kw)


def _fire(host: GameEclHost, at: tuple[float, float], *, sprite: int = 0,
          sprite_offset: int = 0, count: int = 1, speed: float = 2.0,
          angle: float = math.pi / 2) -> None:
    host.bullets.fire(Burst(Vec2(*at), angle, Aim.RING_ABSOLUTE, count, 1,
                            speed, speed, 0.0, sprite=sprite,
                            sprite_offset=sprite_offset))


# ---- 分派 ----

def test_all_24_dispatched() -> None:
    assert sorted(GameEclHost._EX_DISPATCH) == list(range(24))


@pytest.mark.parametrize("idx", [3, 9, 15, 19, 20])  # NoOp + 表现侧 4 条
def test_passthrough_exins(idx: int) -> None:
    m, host, _ = _ex_machine(idx, 7)
    assert m.step()  # 未炸; 表现侧无逻辑效果


# ---- 0 SetPosToBoss (EnemyEclInstr.cpp:55) ----

def test_ex0_set_pos_to_boss() -> None:
    m, host, world = _ex_machine(0, 2)
    boss = type(m.enemy)()
    boss.pos = Vec3(111.0, 222.0, 0.0)
    boss.axis_speed = Vec3(1.0, 2.0, 0.0)
    boss.angle = 0.7
    world.bosses[2] = boss
    m.step()
    assert m.enemy.pos.x == 111.0 and m.enemy.pos.y == 222.0
    assert m.enemy.axis_speed.x == 1.0 and m.enemy.angle == 0.7
    assert m.enemy.disable_movement == 1


# ---- 1 AliceCurveBullets (L66) ----

def test_ex1_curves_matching_bullets() -> None:
    m, host, _ = _ex_machine(1, 0)
    _fire(host, (100, 100), sprite=2, sprite_offset=2, count=2)
    _fire(host, (200, 100), sprite=1, sprite_offset=0, count=1)
    m.step()
    curved = [b for b in host.bullets.alive() if b.state2 == 1]
    assert len(curved) == 2
    for b in curved:
        assert b.speed == 0.3
        assert len(b.commands) == 1 and b.commands[0].type == CmdFlag.TARGET_ANGLE
        assert b.commands[0].angle < 0  # offset 2 → -pi/(rng+180)
        assert b.commands[0].duration == 60  # difficulty 1 < 3


def test_ex1_selects_by_offset() -> None:
    m, host, _ = _ex_machine(1, 1)  # 只处理 spriteOffset==8
    _fire(host, (100, 100), sprite=8, sprite_offset=8)
    _fire(host, (200, 100), sprite=2, sprite_offset=2)
    m.step()
    states = {b.sprite_offset: b.state2 for b in host.bullets.alive()}
    assert states == {8: 1, 2: 0}


# ---- 2 TurnBulletsIntoOtherBullets (L127) ----

def test_ex2_transforms_offset2_within_radius() -> None:
    m, host, _ = _ex_machine(2, 0)  # 半径 128
    m.enemy.pos = Vec3(192.0, 200.0, 0.0)
    _fire(host, (192, 220), sprite=2, sprite_offset=2)   # 距离 20 < 128 → 变换
    _fire(host, (192, 400), sprite=2, sprite_offset=2)   # 距离 200 → 不动
    m.step()
    near, far = host.bullets.alive()[:2]
    assert near.dead
    assert not far.dead
    spawned = [b for b in host.bullets.alive() if not b.dead and b is not far]
    assert len(spawned) == 2  # count1=2
    assert all(b.sprite == 0 and b.sprite_offset == 6 for b in spawned)
    assert all(b.speed == pytest.approx(0.7) for b in spawned)
    assert all(b.commands[0].type == CmdFlag.TARGET_VEL for b in spawned)


# ---- 4 DespawnLargeBulletAndSavePos (L196) ----

def test_ex4_saves_large_bullet_pos() -> None:
    m, host, _ = _ex_machine(4, 0)
    _fire(host, (150, 250), sprite=10, sprite_offset=0)  # 64px 大玉
    _fire(host, (50, 50), sprite=0)                      # 8px 小弹
    m.step()
    assert m.current.args.float_vars1[0] == pytest.approx(150.0)
    assert m.current.args.float_vars1[1] == pytest.approx(250.0)
    big, small = host.bullets.alive()
    assert big.dead and not small.dead


def test_ex4_no_large_bullet_sets_minus999() -> None:
    m, host, _ = _ex_machine(4, 0)
    _fire(host, (150, 250), sprite=0)
    m.step()
    assert m.current.args.float_vars1[0] == -999.0


# ---- 5 CopyMainBossMovement (L227) ----

def test_ex5_copies_main_boss_movement() -> None:
    m, host, world = _ex_machine(5, 0)
    boss = type(m.enemy)()
    boss.pos = Vec3(64.0, 96.0, 0.0)
    boss.move_radius = 33.0
    boss.move_angular_velocity = 0.02
    world.bosses[0] = boss
    m.step()
    assert m.enemy.move_interp_start_pos.x == 64.0
    assert m.enemy.move_radius == 33.0
    assert m.enemy.move_angular_velocity == 0.02


# ---- 6 SplitBulletsOrShootBackwards (L242) ----

def test_ex6_splits_offset6_bullets() -> None:
    m, host, _ = _ex_machine(6, 0, difficulty=1)
    _fire(host, (192, 100), sprite=6, sprite_offset=6, speed=2.0)
    _fire(host, (192, 300), sprite=6, sprite_offset=4)  # 不匹配
    m.step()
    orig = host.bullets.alive()[0]
    assert orig.dead
    new = [b for b in host.bullets.alive() if not b.dead and b.sprite_offset == 15]
    # diff<3: 4 + 2 + 1 = 7 颗, 全 sprite 6 offset 15
    assert len(new) == 7
    assert all(b.sprite == 6 and b.sprite_offset == 15 for b in new)
    # 第一批 count1=4: speed*1.1; 二批 ×0.7 count1=2; 三批 ×0.85 count1=1
    speeds = sorted(round(b.speed, 3) for b in new)
    assert speeds == [1.4, 1.4, 1.7, 2.2, 2.2, 2.2, 2.2]


# ---- 7/8 激光交互 (L366/L454) ----

def _laser(host: GameEclHost) -> Laser:
    laser = Laser(pos=Vec2(100, 100), angle=0.0, width=8.0, start_time=0)
    laser.offset_a = 0.0
    laser.offset_b = 200.0
    laser.state = LaserState.ACTIVE
    host.lasers.lasers.append(laser)
    return laser


def test_ex7_reflects_bullets_in_laser_rect() -> None:
    m, host, _ = _ex_machine(7, 0)
    m.enemy.timer = 0  # timer % 2 == 激光下标 0
    _laser(host)
    _fire(host, (150, 100), angle=math.pi / 2)  # 盒内, 向下飞
    _fire(host, (150, 300))                     # 盒外
    m.step()
    inside, outside = host.bullets.alive()
    assert inside.state2 == 10 and inside.sprite == 5
    # dot = cos*vy + sin*vx = 1*speed > 0 → angle = laser.angle + pi/2
    assert inside.angle == pytest.approx(math.pi / 2)
    assert outside.state2 == 0 and outside.sprite == 0


def test_ex8_redirects_bullets_along_laser() -> None:
    m, host, _ = _ex_machine(8, 0, difficulty=1)
    m.enemy.timer = 0  # timer % 3 == 0 == 激光下标 0 % 3
    _laser(host)
    _fire(host, (150, 100), angle=math.pi / 2)  # 向下; 激光 +x → 侧向 ±y
    m.step()
    b = host.bullets.alive()[0]
    assert b.sprite == 5
    assert b.state2 == -1  # difficulty < 2
    # dir=(-sin0, cos0)=(0,1); dot>0 → 保持 (0,1)·speed
    assert b.vel.y == pytest.approx(b.speed)
    assert abs(b.vel.x) < 1e-9


# ---- 10/11 妖梦减速 (L556/L585) ----

def test_ex10_ex11_game_speed() -> None:
    m, host, world = _setup(
        [_instr(0, OP.RUN_EX_INS, (10, 2)),      # 半速
         _instr(1, OP.RUN_EX_INS, (11, 1)),      # 恢复
         _instr(9999, OP.UNIMP)])
    _fire(host, (192, 100), speed=2.0)
    b = host.bullets.alive()[0]
    v0 = b.vel.y
    m.step()
    assert world.framerate_multiplier == 0.5
    assert host.bullets.time_scale == 0.5
    assert b.vel.y == pytest.approx(v0 * 0.5)
    m.step()  # time==1 → ex11
    assert world.framerate_multiplier == 1.0
    assert host.bullets.time_scale == 1.0
    assert b.vel.y == pytest.approx(v0)


def test_ex10_spawned_bullets_scaled() -> None:
    m, host, world = _ex_machine(10, 4)  # 1/4 速
    m.step()
    # 减速中新出生的弹: 出生速度乘 time_scale (SpawnSingleBullet)
    _fire(host, (192, 100), speed=2.0)
    b = host.bullets.alive()[-1]
    assert b.vel.length == pytest.approx(0.5)


# ---- 12/21 大弹爆裂 (L621/L853) ----

def test_ex12_bursts_large_bullets_normal() -> None:
    m, host, _ = _ex_machine(12, 0, difficulty=1)
    m.enemy.pos = Vec3(192.0, 100.0, 0.0)
    _fire(host, (200, 120), sprite=10, sprite_offset=0)  # 64px, y窗 ±64 内
    _fire(host, (200, 300), sprite=10, sprite_offset=0)  # 窗外
    m.step()
    in_range, out_range = host.bullets.alive()[:2]
    assert in_range.dead and not out_range.dead
    new = [b for b in host.bullets.alive() if not b.dead and b is not out_range]
    assert len(new) == 18  # Normal
    assert all(b.speed == 0.1 for b in new)
    assert all(b.commands[0].type == CmdFlag.TARGET_ANGLE for b in new)
    assert {(b.sprite, b.sprite_offset) for b in new} <= {(0, 2), (3, 2), (7, 1)}


def test_ex21_bursts_15_and_sprite_table() -> None:
    m, host, _ = _ex_machine(21, 1, difficulty=1)  # 非 Hard → y窗 ±180
    m.enemy.pos = Vec3(192.0, 100.0, 0.0)
    _fire(host, (192, 250), sprite=10, sprite_offset=1)  # 64px (681)
    m.step()
    new = [b for b in host.bullets.alive() if not b.dead]
    assert len(new) == 15
    assert {(b.sprite, b.sprite_offset) for b in new} <= {(0, 4), (3, 4), (7, 2)}


# ---- 13/14 妖梦弹幕操控 (L696/L725) ----

def test_ex13_curves_bullets_below() -> None:
    m, host, _ = _ex_machine(13, 0)
    m.enemy.pos = Vec3(192.0, 100.0, 0.0)
    _fire(host, (192, 200), speed=1.8)   # 正下方, x±16 内
    _fire(host, (300, 200))              # x 窗外
    _fire(host, (192, 400))              # y >= 352 外
    m.step()
    below, _, _ = host.bullets.alive()
    assert below.state2 == 1
    assert below.commands[0].type == CmdFlag.TARGET_ANGLE
    assert below.commands[0].speed == pytest.approx(-1.8 / 180.0)
    assert below.commands[0].angle == pytest.approx(-0.05235988)  # i=0 偶 → 负
    others = host.bullets.alive()[1:]
    assert all(b.state2 == 0 for b in others)


def test_ex14_redirects_state2_1_to_player() -> None:
    m, host, _ = _ex_machine(14, 0)
    host.bullets.player_pos = Vec2(192, 400)
    _fire(host, (192, 100))
    _fire(host, (100, 100))
    host.bullets.alive()[0].state2 = 1
    m.step()
    aimed, other = host.bullets.alive()
    assert aimed.state2 == 2
    assert aimed.commands[0].type == CmdFlag.TARGET_VEL
    assert aimed.commands[0].duration == 90
    assert aimed.commands[0].angle == pytest.approx(math.pi / 2)  # 正下方玩家
    assert other.state2 == 0


# ---- 16/17/18 幽幽子蝶弹 (L757/L791/L829) ----

def test_ex16_butterfly_spawns_backspread() -> None:
    m, host, _ = _ex_machine(16, 0)
    m.current.args.float_vars1[1] = 2.5
    _fire(host, (192, 150), sprite=8, sprite_offset=4, angle=0.0)  # 蝶弹 636
    m.step()
    new = host.bullets.alive()[1:]
    assert len(new) == 5  # count1=5
    assert all(b.sprite == 0 and b.sprite_offset == 6 for b in new)
    assert all(b.speed == 2.5 for b in new)
    # 扇形对称分布在 angle+pi 两侧(归一化到 ±pi)
    assert all(abs(abs(b.angle) - math.pi) <= 0.79 for b in new)
    assert not host.bullets.alive()[0].dead  # C 不消原蝶弹


def test_ex17_butterfly_spawns_enemy() -> None:
    m, host, _ = _setup(
        [_instr(0, OP.RUN_EX_INS, (17, 0)), _instr(9999, OP.UNIMP)],
        [_instr(0, 0), _instr(9999, OP.UNIMP)],  # sub1: 空转(不改 life)
    )
    _fire(host, (120, 130), sprite=8, sprite_offset=4, angle=0.5)  # 636 → 刷敌
    _fire(host, (200, 200), sprite=8, sprite_offset=5)             # 637 → 只消弹
    m.step()
    spawned = host.enemies.all()
    assert len(spawned) == 1
    st = spawned[0].state
    assert st.pos.x == pytest.approx(120) and st.pos.y == pytest.approx(130)
    assert st.life == 1 and st.score == 10 and st.item_drop == -2
    assert all(b.dead for b in host.bullets.alive())  # 两种蝶弹都消


def test_ex18_counts_636_butterflies() -> None:
    m, host, _ = _ex_machine(18, 0)
    _fire(host, (100, 100), sprite=8, sprite_offset=4, count=2)  # 636 ×2
    _fire(host, (200, 100), sprite=8, sprite_offset=6)           # 638 不计
    m.step()
    assert m.current.args.int_vars1[0] == 2


# ---- 22/23 Extra/Phantasm 大弹追踪 (L936/L1005) ----

def test_ex22_odd_timer_spawns_dirchange_bullets() -> None:
    m, host, _ = _ex_machine(22, 0)
    m.enemy.timer = 1  # %3≠0, 奇数 → sprite 1 speed 1.2 带 DIR_CHANGE_AIM
    _fire(host, (192, 100), sprite=10, sprite_offset=1)  # 64px > 60, y<320
    m.step()
    new = host.bullets.alive()[1:]
    assert len(new) == 1  # count1=1
    b = new[0]
    assert b.sprite == 1 and b.sprite_offset == 6  # 原 offset==1 → 6
    assert b.speed == 1.2
    assert b.commands[0].type == CmdFlag.DIR_CHANGE_AIM
    assert b.commands[0].duration == 60
    assert not host.bullets.alive()[0].dead  # 原大弹不消


def test_ex22_timer_mod3_zero_skips() -> None:
    m, host, _ = _ex_machine(22, 0)
    m.enemy.timer = 3
    _fire(host, (192, 100), sprite=10, sprite_offset=1)
    m.step()
    assert len(host.bullets.alive()) == 1  # 无新弹


def test_ex22_even_timer_sprite3_count2_no_cmd() -> None:
    m, host, _ = _ex_machine(22, 0)
    m.enemy.timer = 2  # %3=2≠0, 偶数 → sprite 3, count1=2, 无转向命令
    _fire(host, (192, 100), sprite=10, sprite_offset=0)  # offset≠1 → 2
    m.step()
    new = host.bullets.alive()[1:]
    assert len(new) == 2
    assert all(b.sprite == 3 and b.sprite_offset == 2 and b.speed == 0.8
               for b in new)
    assert all(not b.commands for b in new)


def test_ex23_variants() -> None:
    # mod3=2 → 直接 return
    m, host, _ = _ex_machine(23, 0)
    m.enemy.timer = 2
    _fire(host, (192, 100), sprite=10, sprite_offset=2)
    m.step()
    assert len(host.bullets.alive()) == 1
    # mod3=1 → sprite 1, offset: 原==2 → 10, 带 40 帧 DIR_CHANGE_AIM
    m, host, _ = _ex_machine(23, 0)
    m.enemy.timer = 1
    _fire(host, (192, 100), sprite=10, sprite_offset=2)
    m.step()
    b = host.bullets.alive()[1]
    assert b.sprite == 1 and b.sprite_offset == 10 and b.speed == 1.2
    assert b.commands[0].type == CmdFlag.DIR_CHANGE_AIM
    assert b.commands[0].duration == 40
    # mod3=0 → sprite 3, offset: 原≠2 → 13, 无命令
    m, host, _ = _ex_machine(23, 0)
    m.enemy.timer = 3
    _fire(host, (192, 100), sprite=10, sprite_offset=0)
    m.step()
    b = host.bullets.alive()[1]
    assert b.sprite == 3 and b.sprite_offset == 13 and b.speed == 0.8
    assert not b.commands


# ---- SET_EX_INS 每帧回调路径 ----

def test_set_ex_ins_per_frame_callback() -> None:
    """SET_EX_INS(22) 注册后每帧触发(对照 EclManager.cpp:2169); -1 注销。"""
    m, host, _ = _setup(
        [_instr(0, OP.SET_EX_INS, (22, 0)),
         _instr(4, OP.SET_EX_INS, (-1, 0)),
         _instr(9999, OP.UNIMP)])
    _fire(host, (192, 100), sprite=10, sprite_offset=1)
    for _ in range(6):
        m.step()
    # 回调帧: timer=0(%3=0 跳过) 1(奇,补1颗) 2(偶,补2颗) 3(跳过);
    # time==4 的帧先执行注销再走帧末回调? —— SET_EX_INS 在 ctx 上, 同帧回调仍跑
    n_after_reg = len(host.bullets.alive())
    assert n_after_reg > 1
    for _ in range(3):
        m.step()
    assert len(host.bullets.alive()) == n_after_reg


def test_remove_all_bullets_clears_lasers() -> None:
    """remove_all_bullets 连带激光 (BulletManager.cpp:439-471):
    普通激光进 DESPAWNING + 沿线出弹消点, flags&4 激光豁免。"""
    m, host, world = _setup([_instr(9999, OP.UNIMP)])
    normal = Laser(pos=Vec2(100, 100), angle=0.0)
    normal.offset_a, normal.offset_b = 0.0, 70.0
    flag4 = Laser(pos=Vec2(200, 100), angle=0.0, flags=4)
    flag4.offset_a, flag4.offset_b = 0.0, 70.0
    host.lasers.lasers += [normal, flag4]
    host.remove_all_bullets(True)
    assert normal.state == LaserState.DESPAWNING
    assert normal.hitbox_end_time == 0
    assert flag4.state != LaserState.DESPAWNING
    # 沿线 0/32/64 共 3 个弹消点道具
    assert len(host.items.alive()) == 3
    # spawn_items=False: 不出道具, 但 flags&4 仍豁免
    host2_items = len(host.items.alive())
    normal2 = Laser(pos=Vec2(100, 300), angle=0.0)
    normal2.offset_a, normal2.offset_b = 0.0, 70.0
    host.lasers.lasers.append(normal2)
    host.remove_all_bullets(False)
    assert normal2.state == LaserState.DESPAWNING
    assert len(host.items.alive()) == host2_items
