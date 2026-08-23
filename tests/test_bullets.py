"""Touhou: 敌弹世界测试 —— aim 模式分布 / 命令接入 / exFlags 更新器 / 弹型表。

数值权威: th07/src/th07/BulletManager.cpp (SpawnSingleBullet / RunCommands /
UpdateBullet* / OnUpdate)。
"""
from __future__ import annotations

import math
import sys

sys.path.insert(0, r"D:\python_play\Touhou08")

from touhou.engine.bullet_commands import (  # noqa: E402
    CmdFlag,
    BulletCommand,
    BulletState,
    step_bullet,
)
from touhou.engine.bullets import (  # noqa: E402
    BULLET_TYPE_SPECS,
    Aim,
    Bullet,
    BulletWorld,
    Burst,
    bullet_type_size,
    rank_lerp,
    rank_lerp_int,
)
from touhou.engine.rng import Rng  # noqa: E402
from touhou.utils import Vec2, angle_to  # noqa: E402

P = Vec2(100, 100)  # 玩家占位


def _mk(angle: float = 0.0, speed: float = 3.0, pos: Vec2 = Vec2(100, 100)) -> BulletState:
    return BulletState(pos=pos, angle=angle, speed=speed)


# ======================================================================
# aim 模式 (SpawnSingleBullet 的 switch; x=arm, y=ring)
# ======================================================================
def test_spread_odd_symmetric() -> None:
    b = Burst(Vec2(0, 0), 0.5, Aim.SPREAD_AIMED, 3, 1, 2.0, 2.0, 0.2)
    got = [b.angle_speed(x, 0, Rng(0))[0] for x in range(3)]
    # 奇数颗: 0, -step, +step ((x+1)//2, 奇下标取负)
    assert got == [0.5, 0.5 - 0.2, 0.5 + 0.2]


def test_spread_even_half_step() -> None:
    b = Burst(Vec2(0, 0), 0.0, Aim.SPREAD_ABSOLUTE, 4, 1, 2.0, 2.0, 0.2)
    got = [b.angle_speed(x, 0, Rng(0))[0] for x in range(4)]
    # 偶数颗: ±step/2, ±3step/2
    for want, g in zip((0.1, -0.1, 0.3, -0.3), got):
        assert abs(g - want) < 1e-9


def test_ring_aimed_and_ring_rotation_per_layer() -> None:
    b = Burst(Vec2(0, 0), 1.0, Aim.RING_AIMED, 4, 2, 3.0, 1.0, 0.1)
    a0, s0 = b.angle_speed(0, 0, Rng(0))
    a1, _ = b.angle_speed(1, 0, Rng(0))
    a2, s1 = b.angle_speed(0, 1, Rng(0))
    assert abs(a0 - 1.0) < 1e-9
    assert abs(a1 - (1.0 + math.tau / 4)) < 1e-9
    # 第二层整体多转 ring*angle2, 速度按 ring/count2 向 speed_b 插值
    assert abs(a2 - (1.0 + 0.1)) < 1e-9
    assert s0 == 3.0 and abs(s1 - 2.0) < 1e-9


def test_ring_shift_adds_half_step_no_layer_term() -> None:
    b = Burst(Vec2(0, 0), 0.0, Aim.RING_SHIFT_ABSOLUTE, 4, 2, 2.0, 2.0, 0.3)
    a0, _ = b.angle_speed(0, 0, Rng(0))
    a1, _ = b.angle_speed(1, 0, Rng(0))
    a_ring1, _ = b.angle_speed(0, 1, Rng(0))
    assert abs(a0 - math.pi / 4) < 1e-9
    assert abs(a1 - (math.pi / 4 + math.tau / 4)) < 1e-9
    # 源码里 shifted 环没有 y*angle2 项
    assert abs(a_ring1 - a0) < 1e-9


def test_angle_random_in_range() -> None:
    rng = Rng(42)
    b = Burst(Vec2(0, 0), 1.0, Aim.ANGLE_RANDOM, 8, 1, 2.0, 2.0, 0.3)
    for x in range(8):
        a, s = b.angle_speed(x, 0, rng)
        assert 0.3 <= a < 1.0  # [angle2, angle1)
        assert s == 2.0


def test_ring_speed_random() -> None:
    rng = Rng(42)
    b = Burst(Vec2(0, 0), 0.0, Aim.RING_SPEED_RANDOM, 4, 1, 5.0, 1.0, 0.0)
    for x in range(4):
        a, s = b.angle_speed(x, 0, rng)
        assert abs(a - x * math.tau / 4) < 1e-9
        assert 1.0 <= s < 5.0


def test_random_angle_and_speed() -> None:
    rng = Rng(42)
    b = Burst(Vec2(0, 0), 2.0, Aim.RANDOM, 8, 1, 6.0, 2.0, -1.0)
    seen = set()
    for x in range(8):
        a, s = b.angle_speed(x, 0, rng)
        assert -1.0 <= a < 2.0
        assert 2.0 <= s < 6.0
        seen.add(a)
    assert len(seen) > 1  # 确实随机


# ======================================================================
# rank 插值 (§0.5 / EnemyManager.hpp BulletRank*Inner)
# ======================================================================
def test_rank_lerp_endpoints() -> None:
    assert rank_lerp(1.0, 3.0, 0) == 1.0
    assert rank_lerp(1.0, 3.0, 32) == 3.0
    assert rank_lerp_int(0, 5, 0) == 0
    assert rank_lerp_int(0, 5, 32) == 5


def test_rank_lerp_int_truncates_toward_zero() -> None:
    assert rank_lerp_int(1, 4, 16) == 2   # 16*3/32=1.5 → 1
    assert rank_lerp_int(5, 0, 16) == 3   # 16*(-5)/32=-2.5 → C++ 截断 -2 (非 floor -3)


# ======================================================================
# 命令系统接入: 执行顺序 + spawn 即跑一次 RunCommands
# ======================================================================
def test_world_step_runs_commands_before_movement() -> None:
    w = BulletWorld()
    cmd = BulletCommand(CmdFlag.TARGET_ANGLE, speed=-2.0, angle=0.0, duration=60)
    w.fire(Burst(Vec2(100, 100), 0.0, Aim.SPREAD_ABSOLUTE, 1, 1, 3.0, 3.0, 0.0,
                 commands=(cmd,)))
    b = w.alive()[0]
    w.step()
    # 命令先跑: 速度 3→1 后再位移 → x=101 (若先位移则是 103)
    assert b.speed == 1.0
    assert b.pos.x == 101.0


def test_spawn_runs_run_commands_immediately() -> None:
    w = BulletWorld()
    w.fire(Burst(P, 0.0, Aim.SPREAD_ABSOLUTE, 1, 1, 3.0, 3.0, 0.0,
                 commands=(BulletCommand(CmdFlag.BURST),)))
    b = w.alive()[0]
    assert b.ex_flags & CmdFlag.BURST  # SpawnSingleBullet 末尾的 RunCommands
    w.step()
    # 爆发首帧 k=5: vel = (5+3) 向右, pos.x += 8
    assert b.pos.x == 108.0


def test_spawn_delay_survives_offscreen_until_delay_ends() -> None:
    w = BulletWorld()
    cmd = BulletCommand(CmdFlag.SPAWN_DELAY, duration=30)
    w.fire(Burst(Vec2(-50, 100), 0.0, Aim.SPREAD_ABSOLUTE, 1, 1, 1.0, 1.0, 0.0,
                 commands=(cmd,)))
    for _ in range(29):
        w.step()
    assert len(w) == 1  # 延迟期间出界不销毁(仍在移动)
    w.step()
    assert len(w) == 0  # 延迟耗尽, 出界销毁


def test_fire_sets_more_flags_and_type_size() -> None:
    w = BulletWorld()
    w.fire(Burst(P, 0.0, Aim.RING_ABSOLUTE, 2, 1, 2.0, 2.0, 0.0,
                 sprite=8, flags=0x200))
    b = w.alive()[0]
    assert b.more_flags == 0x200
    assert b.size == Vec2(32.0, 32.0)  # 弹型 8 = 32px 刀弹


# ======================================================================
# exFlags 更新器(逐一对应 UpdateBullet*)
# ======================================================================
def test_dir_change_relative_slows_then_turns() -> None:
    b = _mk(angle=0.0, speed=3.0)
    # ZUN quirk: cmd.speed 进状态槽 angle(转向量), cmd.angle 进 speed(新速度)
    b.add_command(BulletCommand(CmdFlag.DIR_CHANGE, speed=1.0, angle=2.0,
                                duration=10, loop=1))
    for _ in range(5):
        step_bullet(b, P)
    # 刹停中: cur = 3 - 4*3/10 = 1.8
    assert abs(b.vel.length - 1.8) < 1e-9
    for _ in range(6):
        step_bullet(b, P)
    assert abs(b.angle - 1.0) < 1e-9   # 相对转向 += 1.0
    assert b.speed == 2.0              # 恢复目标速度
    assert not (b.ex_flags & CmdFlag.DIR_CHANGE)  # loop=1 跑完清位


def test_dir_change_absolute_sets_angle() -> None:
    b = _mk(angle=0.5, speed=3.0)
    b.add_command(BulletCommand(CmdFlag.DIR_CHANGE_ABS, speed=1.0, angle=2.0,
                                duration=10, loop=1))
    for _ in range(11):
        step_bullet(b, P)
    assert abs(b.angle - 1.0) < 1e-9  # 绝对角 = st.angle(=cmd.speed), 不是 0.5+1.0
    assert b.speed == 2.0


def test_dir_change_aim_at_player() -> None:
    b = _mk(angle=0.0, speed=3.0, pos=Vec2(100, 100))
    b.add_command(BulletCommand(CmdFlag.DIR_CHANGE_AIM, speed=0.25, angle=2.0,
                                duration=10, loop=1))
    player = Vec2(100, 200)  # 正下方
    for _ in range(10):
        step_bullet(b, player)
    # 转向角在回速瞬间按当时位置算 AngleToPlayer
    want = angle_to(b.pos, player) + 0.25
    step_bullet(b, player)
    assert abs(b.angle - want) < 1e-9


def test_dir_change_loops_before_clearing() -> None:
    b = _mk(angle=0.0, speed=3.0)
    b.add_command(BulletCommand(CmdFlag.DIR_CHANGE, speed=0.5, angle=2.0,
                                duration=10, loop=2))
    for _ in range(11):
        step_bullet(b, P)
    assert b.ex_flags & CmdFlag.DIR_CHANGE  # 第一次转向后仍在(loop=2)
    for _ in range(10):
        step_bullet(b, P)
    assert not (b.ex_flags & CmdFlag.DIR_CHANGE)
    assert abs(b.angle - 1.0) < 1e-9  # 转了两次 0.5


def test_bounce_left_wall_and_count_exhaustion() -> None:
    b = _mk(angle=math.pi, speed=2.0, pos=Vec2(-9, 100))  # 已出左界(16px 弹)
    b.add_command(BulletCommand(CmdFlag.BOUNCE, speed=-1.0, duration=1))
    step_bullet(b, P)
    # 左右反弹: angle = -angle - pi → 0; 速度取回弹速度(负→沿用当前)
    assert abs(b.angle) < 1e-9 and b.vel.x > 0
    assert b.speed == 2.0
    assert not (b.ex_flags & (CmdFlag.BOUNCE | CmdFlag.BOUNCE_NO_FLOOR))  # 1 次用尽


def test_bounce_floor_only_with_floor_flag() -> None:
    # BOUNCE(0x400): 底边也弹
    b = _mk(angle=math.pi / 2, speed=2.0, pos=Vec2(200, 457))
    b.add_command(BulletCommand(CmdFlag.BOUNCE, speed=-1.0, duration=3))
    step_bullet(b, P)
    assert abs(b.angle - (-math.pi / 2)) < 1e-9  # 向下 → 向上
    assert b.ex_flags & CmdFlag.BOUNCE           # 次数未尽, 位保留
    # BOUNCE_NO_FLOOR(0x800): 底边不弹
    b2 = _mk(angle=math.pi / 2, speed=2.0, pos=Vec2(200, 457))
    b2.add_command(BulletCommand(CmdFlag.BOUNCE_NO_FLOOR, speed=-1.0, duration=3))
    step_bullet(b2, P)
    assert abs(b2.angle - math.pi / 2) < 1e-9    # 角度不变(仍向下)
    assert b2.speed == 2.0                       # 但回弹速度已重置


def test_offscreen_grace_128_frames_for_commanded_bullets() -> None:
    w = BulletWorld()
    # 普通弹出界立即销毁
    w.fire(Burst(Vec2(-50, 100), math.pi, Aim.SPREAD_ABSOLUTE, 1, 1, 2.0, 2.0, 0.0))
    w.step()
    assert len(w) == 0
    # 带转向命令的弹出界宽限 128 帧
    cmd = BulletCommand(CmdFlag.DIR_CHANGE, speed=0.1, angle=1.0, duration=500)
    w.fire(Burst(Vec2(-50, 100), math.pi, Aim.SPREAD_ABSOLUTE, 1, 1, 2.0, 2.0, 0.0,
                 commands=(cmd,)))
    for _ in range(127):
        w.step()
    assert len(w) == 1
    w.step()
    assert len(w) == 0


# ======================================================================
# 弹型模板表 (g_BulletTypeInfos + AddedCallback 判定树, 高度取自 etama.anm)
# ======================================================================
def test_bullet_type_specs_table() -> None:
    assert len(BULLET_TYPE_SPECS) == 16
    s0 = BULLET_TYPE_SPECS[0]
    assert (s0.anm_file_idx, s0.height, s0.graze_size, s0.collision_type) == \
        (0x200, 8.0, Vec2(4, 4), 5)
    s1 = BULLET_TYPE_SPECS[1]
    assert (s1.graze_size, s1.collision_type) == (Vec2(6, 6), 3)  # 16px 默认档
    s2 = BULLET_TYPE_SPECS[2]
    assert (s2.graze_size, s2.collision_type) == (Vec2(4, 4), 4)  # anm 514 特判
    s7 = BULLET_TYPE_SPECS[7]
    assert (s7.graze_size, s7.collision_type) == (Vec2(10, 10), 2)  # 32px 默认档
    s8 = BULLET_TYPE_SPECS[8]
    assert (s8.graze_size, s8.collision_type) == (Vec2(5, 5), 1)  # anm 520 特判
    s9 = BULLET_TYPE_SPECS[9]
    assert (s9.graze_size, s9.collision_type) == (Vec2(8, 8), 2)  # anm 521 特判
    s10 = BULLET_TYPE_SPECS[10]
    assert (s10.anm_file_idx, s10.height, s10.collision_type) == (0x2A8, 8.0, 5)
    assert BULLET_TYPE_SPECS[11].anm_file_idx == 0  # 11..15 未初始化


def test_bullet_type_size_fallback() -> None:
    assert bullet_type_size(8) == Vec2(32.0, 32.0)
    assert bullet_type_size(0) == Vec2(8.0, 8.0)
    assert bullet_type_size(99) == Vec2(16, 16)  # 未知弹型默认 16px


# ======================================================================
# spawn 特效态 (BulletManager.cpp:255-283 出生 / :1022-1047 每帧)
# 出生态帧数 = etama.anm spawn 特效脚本时长 T+1 (脚本 t=T EXIT_HIDE2):
# 弹型 0-6 → T=10/16/32 (fast/normal/slow), 弹型 7-9 → 32, 弹型 10 → 24
# ======================================================================

def _spawn_one(flag: int, *, sprite: int = 1, speed: float = 2.0,
               angle: float = math.pi / 2, pos: Vec2 = Vec2(192, 100),
               commands: tuple = ()) -> tuple[BulletWorld, Bullet]:
    w = BulletWorld()
    w.fire(Burst(pos, angle, Aim.RING_ABSOLUTE, 1, 1, speed, speed, 0.0,
                 sprite=sprite, flags=flag, commands=commands))
    return w, w.alive()[0]


def test_spawn_fast_pos_rollback_and_speed_curve() -> None:
    """flags=2 (SPAWNING_FAST): 出生 pos -= vel*4; 10 帧 vel/2;
    第 11 帧 (脚本 0x215 t=10 播完) 转 NORMAL 并当帧落入正常分支(+vel/2+vel)。"""
    w, b = _spawn_one(2)
    assert b.spawn_state == 2
    assert b.pos.distance(Vec2(192, 92)) < 1e-9  # 100 - 2*4
    for _ in range(10):
        w.step()
    assert b.spawn_state == 2 and b.pos.distance(Vec2(192, 102)) < 1e-9
    w.step()  # 转变帧: +2/2 后转 NORMAL, 再 +2
    assert b.spawn_state == 0
    assert b.pos.distance(Vec2(192, 105)) < 1e-9
    assert b.age == 1  # timer1 从转变帧起计 (出生态 timer2 冻结, C++ --/++ 抵消)


def test_spawn_normal_and_slow_divisors() -> None:
    """flags=4 → vel/2.5 共 16 帧, 第 17 帧转变; flags=8 → vel/3 共 32 帧,
    第 33 帧转变 (脚本 0x216 t=16 / 0x217 t=32)。"""
    w4, b4 = _spawn_one(4)
    for _ in range(16):
        w4.step()
    assert b4.spawn_state == 4 and abs(b4.pos.y - (92 + 16 * 0.8)) < 1e-9
    w4.step()
    assert b4.spawn_state == 0 and abs(b4.pos.y - (92 + 17 * 0.8 + 2)) < 1e-9
    w8, b8 = _spawn_one(8)
    for _ in range(32):
        w8.step()
    assert b8.spawn_state == 8 and abs(b8.pos.y - (92 + 32 * (2 / 3))) < 1e-6
    w8.step()
    assert b8.spawn_state == 0 and abs(b8.pos.y - (92 + 33 * (2 / 3) + 2)) < 1e-6


def test_spawn_big_bullets_share_t32() -> None:
    """弹型 7-9 三态共用脚本 0x218 (t=32): flags=2 也是 33 帧转变。"""
    w, b = _spawn_one(2, sprite=7)
    for _ in range(32):
        w.step()
    assert b.spawn_state == 2
    w.step()
    assert b.spawn_state == 0


def test_spawn_light_bullet_t24() -> None:
    """弹型 10 (光弹) 三态共用脚本 0x2aa (t=24): 25 帧转变。"""
    w, b = _spawn_one(2, sprite=10)
    for _ in range(24):
        w.step()
    assert b.spawn_state == 2
    w.step()
    assert b.spawn_state == 0


def test_spawn_state_runs_no_command_updaters() -> None:
    """出生态不跑 exFlags 更新器 (OnUpdate SPAWNING_* 分支无 RunCommands/更新器):
    BURST 附加速度在出生态冻结, 转变帧才开始。"""
    cmd = BulletCommand(CmdFlag.BURST)
    w, b = _spawn_one(2, commands=(cmd,))
    # SpawnSingleBullet 末尾的 RunCommands 已激活 BURST, 但出生态不跑更新器
    assert b.ex_flags & CmdFlag.BURST
    v0 = b.vel
    w.step()
    assert b.vel == v0  # 冻结
    for _ in range(10):
        w.step()  # 到转变帧
    assert b.spawn_state == 0
    # 转变帧起 BURST 更新器跑: vel = angle 方向 (5+speed)*1
    assert b.vel != v0


def test_spawn_state_no_offscreen_despawn_no_hit() -> None:
    """出生态不做出界判定 (SPAWNING 分支不到出界段), 也不参与命中。"""
    # 向上高速: 普通弹早已出界消弹, 出生态弹(弹型 7, T=32)仍在场
    w, b = _spawn_one(2, sprite=7, speed=20.0, angle=-math.pi / 2)
    w2, b2 = _spawn_one(0, sprite=7, speed=20.0, angle=-math.pi / 2)
    for _ in range(19):  # 19 帧后 y=180-10*19=-10 < -8 → 普通弹出界
        w.step()
        w2.step()
    assert b2.dead
    assert not b.dead and b.spawn_state == 2
    # 命中: 出生态弹即使罩住玩家也不算
    w3, b3 = _spawn_one(2, pos=Vec2(100, 100), speed=0.0)
    w3.player_pos = Vec2(100, 100)
    assert not w3.hits_player()
    for _ in range(11):
        w3.step()
    assert w3.hits_player()


# ======================================================================
# 判定半径物化 (fire 时把 BulletWorld.bullet_radius 写到 Bullet.hitbox,
# 供 apis 观测面读取; 判定消费仍走世界字段, 行为不变)
# ======================================================================
def test_bullet_hitbox_materialized_from_world() -> None:
    w = BulletWorld()
    w.fire(Burst(P, 0.0, Aim.RING_ABSOLUTE, 4, 1, 2.0, 2.0, 0.1, sprite=0))
    assert [b.hitbox for b in w.alive()] == [w.bullet_radius] * 4
    # 世界半宽改动后, 新生成的弹携带新值; 已生成的保持生成时的值
    w.bullet_radius = 5.0
    w.fire(Burst(P, 0.0, Aim.RING_ABSOLUTE, 2, 1, 2.0, 2.0, 0.1, sprite=7))
    assert sorted({b.hitbox for b in w.alive()}) == [3.5, 5.0]


def test_bullet_default_hitbox() -> None:
    # 直接构造(不经 fire)给与世界字段默认一致的 3.5
    assert Bullet(Vec2(0, 0), 0.0, 1.0).hitbox == 3.5
