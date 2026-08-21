"""Touhou: 子弹命令系统测试。"""
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
from touhou.utils import Vec2  # noqa: E402


def _mk(angle: float = -math.pi / 2, speed: float = 3.0) -> BulletState:
    return BulletState(pos=Vec2(100, 100), angle=angle, speed=speed,
                       vel=Vec2.from_angle(angle, speed))


def test_burst_speeds_up_then_settles() -> None:
    b = _mk()
    b.add_command(BulletCommand(CmdFlag.BURST))
    prev = b.vel
    for _ in range(5):
        step_bullet(b, Vec2(0, 0))
    # 爆发期速度应比初始快(burst 把 speed+k 放大)
    assert b.vel.length > prev.length


def test_target_velocity_follows_command() -> None:
    b = _mk()
    # 加一个"目标速度: 向右速度 5"的命令
    b.add_command(BulletCommand(CmdFlag.TARGET_VEL, speed=5.0, angle=0.0, duration=60))
    step_bullet(b, Vec2(0, 0))
    # 每帧沿目标速度累积加速度
    # 未到 duration 期间, vel 应逐步偏向 (5,0)
    for _ in range(30):
        step_bullet(b, Vec2(0, 0))
    # 30 帧后速度应更接近向右(target (5,0))
    assert b.vel.x > abs(b.vel.y), f"应更多向右: {b.vel}"


def test_target_angle_rotates() -> None:
    b = _mk(angle=0.0, speed=3.0)  # 初始向右
    b.add_command(BulletCommand(CmdFlag.TARGET_ANGLE, angle=0.05, duration=60))
    a0 = b.angle
    for _ in range(10):
        step_bullet(b, Vec2(0, 0))
    # 角度应随时间增长(angvel 0.05/帧)
    assert b.angle > a0


def test_no_command_moves_straight() -> None:
    b = _mk(angle=0.0, speed=3.0)
    x0 = b.pos.x
    step_bullet(b, Vec2(0, 0))
    for _ in range(5):
        step_bullet(b, Vec2(0, 0))
    assert b.pos.x > x0
    assert abs(b.pos.y - 100.0) < 1e-6
