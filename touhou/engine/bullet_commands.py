"""子弹命令系统 —— 移植自 BulletManager.cpp 的 RunCommands + 7 个 exFlags 更新器。

命令让弹幕"花活": 爆发 / 目标速度 / 目标角速度 / 转向后回速(相对/瞄准/绝对) / 反弹。
用 IntFlag 表达 exFlags 激活位; 每颗弹持一个命令队列, 每帧:
  1. run_commands()  按 C++ RunCommands 语义从队列激活下一条命令
  2. step_commands() 按 OnUpdate 的顺序跑各 exFlags 更新器
  3. 位移 (pos += vel) 在命令之后 —— 见 step_bullet / BulletWorld.step

语法参考(ECL_INIT_BULLET_CMD / AddCommand):
  BulletCommand(CmdFlag.TARGET_VEL, speed=5.0, angle=0.0, duration=60)
"""

from __future__ import annotations

import math
import msgspec
from enum import IntFlag

from ..utils import Vec2, angle_to, normalize_angle_diff

# 可视区(GameManager::IsInBounds / UpdateBulletBounce 的硬编码边界)
SCREEN_W, SCREEN_H = 384.0, 448.0


class CmdFlag(IntFlag):
    """命令类型(= exFlags 位, 值与 BulletManager.cpp RunCommands 的 cmd->type 一致)。"""

    BURST = 0x1  # 爆发: 出场 16 帧内速度额外 +5 线性衰减到 +0
    TARGET_VEL = 0x10  # 目标速度: 每帧叠加一个固定速度矢量
    TARGET_ANGLE = 0x20  # 目标角: 角速度+加速度持续 duration 帧
    DIR_CHANGE = 0x40  # 转向后回速: 先线性刹停, 再相对转向并恢复速度
    DIR_CHANGE_AIM = 0x80  # 转向后回速: 转向瞄准玩家+偏移角
    DIR_CHANGE_ABS = 0x100  # 转向后回速: 转向绝对角
    BOUNCE = 0x400  # 反弹(四边, 含底边)
    BOUNCE_NO_FLOOR = 0x800  # 反弹(左右上三边, 底边不弹)
    SPAWN_DELAY = 0x2000  # 延迟出屏判定(无更新器, 激活时直接置 spawn_delay)


# 带这些 exFlags 的弹出界后不立即销毁, 宽限 128 帧 (OnUpdate: exFlags & 0xdc0)
OFFSCREEN_GRACE = int(
    CmdFlag.DIR_CHANGE
    | CmdFlag.DIR_CHANGE_AIM
    | CmdFlag.DIR_CHANGE_ABS
    | CmdFlag.BOUNCE
    | CmdFlag.BOUNCE_NO_FLOOR
)
OFFSCREEN_GRACE_FRAMES = 128

# step_commands 热路径用的 int 位型(避免 IntFlag 的 Python 级 __and__)
_F_BURST = CmdFlag.BURST.value
_F_TARGET_VEL = CmdFlag.TARGET_VEL.value
_F_TARGET_ANGLE = CmdFlag.TARGET_ANGLE.value
_F_DIR_CHANGE = CmdFlag.DIR_CHANGE.value
_F_DIR_CHANGE_AIM = CmdFlag.DIR_CHANGE_AIM.value
_F_DIR_CHANGE_ABS = CmdFlag.DIR_CHANGE_ABS.value
_F_BOUNCE_ANY = CmdFlag.BOUNCE.value | CmdFlag.BOUNCE_NO_FLOOR.value


# 命令槽位(C++ Bullet::commandStates[5] 的下标)
_SLOT_BURST = 0
_SLOT_TARGET_VEL = 1
_SLOT_TARGET_ANGLE = 2
_SLOT_DIR_CHANGE = 3  # 0x40/0x80/0x100 共用一个槽
_SLOT_BOUNCE = 4  # 0x400/0x800 共用一个槽
NUM_SLOTS = 5


class BulletCommand(msgspec.Struct):
    """一条命令(来自 ECL 的 AddCommand)。

    flag: C++ BulletCommand.flag —— 0 表示等上一条命令的更新器跑完(exFlags==0)
    才激活; 非 0 表示立即并行激活。
    """

    type: CmdFlag
    speed: float = 0.0
    angle: float = 0.0
    duration: int = 0
    loop: int = 1
    flag: int = 0


class CmdState(msgspec.Struct):
    """命令运行时状态(对应 C++ BulletCommandState)。

    注意 duration 在反弹槽里被复用为"已反弹次数"(ZUN 风格)。
    """

    timer: int = 0
    speed: float = 0.0
    angle: float = 0.0
    duration: int = 0
    max_times: int = 0
    min_times: int = 0
    vel: Vec2 = Vec2.zero()  # 目标速度矢量(TARGET_VEL)


class BulletState:
    """一颗弹在命令系统下的可写状态(对应 C++ Bullet 的命令相关字段)。

    纯粹的逻辑状态: pos/angle/speed/vel + 命令队列 + 5 个命令槽。
    bullets.Bullet 继承本类补上 sprite/age/grazed 等游戏层字段。
    """

    __slots__ = (
        "pos",
        "angle",
        "speed",
        "vel",
        "size",
        "ex_flags",
        "more_flags",
        "commands",
        "cur_cmd_idx",
        "states",
        "spawn_delay",
    )

    def __init__(
        self,
        pos: Vec2,
        angle: float,
        speed: float,
        vel: Vec2 | None = None,
        *,
        size: Vec2 = Vec2(16, 16),
    ) -> None:
        self.pos = pos
        self.angle = angle
        self.speed = speed
        self.vel = vel if vel is not None else Vec2.from_angle(angle, speed)
        self.size = size  # 精灵宽高(px), 出界/反弹判定用
        self.ex_flags = 0
        self.more_flags = 0
        self.commands: list[BulletCommand] = []
        self.cur_cmd_idx = 0
        self.states = [CmdState() for _ in range(NUM_SLOTS)]
        self.spawn_delay = 0

    # ---- 命令队列 ----
    def add_command(self, cmd: BulletCommand) -> None:
        """入队一条命令(对应 AddCommand: 记入 moreFlags, 由 run_commands 激活)。"""
        self.commands.append(cmd)
        self.more_flags |= cmd.type

    def set_command(self, slot: int, cmd: BulletCommand) -> None:
        """Bullet::AddCommand 的活弹版: 写固定槽位 + moreFlags 记位 +
        curCmdIdx 归零重新评估队列 (ExIns 给场上弹挂命令用)。"""
        while len(self.commands) <= slot:
            self.commands.append(BulletCommand(CmdFlag(0)))
        self.commands[slot] = cmd
        self.more_flags |= cmd.type
        self.cur_cmd_idx = 0

    def clear_command(self, slot: int) -> None:
        """Bullet::ClearCommand: 槽位清零 (type=0 会截断后续队列评估, 同 C++)。"""
        if slot < len(self.commands):
            self.commands[slot] = BulletCommand(CmdFlag(0))

    def run_commands(self, dt: float = 1.0) -> None:
        """从队列激活下一条命令 —— 对照 Bullet::RunCommands (0x00424290)。

        每次调用最多激活一条; flag==0 的命令要等 exFlags 清空(上一条跑完)。
        dt = effectiveFramerateMultiplier: C++ 在激活 TARGET_VEL 时把
        它烘进状态矢量 (BulletManager.cpp:347-349), 之后每帧更新器再乘一次
        当前的 mult (:703-704) —— 妖梦减速中激活会双重缩放, 照抄。
        """
        while self.cur_cmd_idx < len(self.commands):
            cmd = self.commands[self.cur_cmd_idx]
            if cmd.type == 0:
                return
            if cmd.flag == 0 and self.ex_flags != 0:
                return
            if not (self.more_flags & cmd.type):
                self.cur_cmd_idx += 1
                continue
            t = cmd.type
            if t == CmdFlag.BURST:
                self.ex_flags |= CmdFlag.BURST
                st = self.states[_SLOT_BURST]
                st.timer = 0
            elif t == CmdFlag.TARGET_VEL:
                self.ex_flags |= CmdFlag.TARGET_VEL
                st = self.states[_SLOT_TARGET_VEL]
                st.speed = cmd.speed
                st.angle = cmd.angle if cmd.angle > -990.0 else self.angle
                st.timer = 0
                st.duration = cmd.duration
                # BulletManager.cpp:347-349: 激活时烘入 effectiveFramerateMultiplier
                st.vel = Vec2.from_angle(st.angle, st.speed * dt)
            elif t == CmdFlag.TARGET_ANGLE:
                self.ex_flags |= CmdFlag.TARGET_ANGLE
                st = self.states[_SLOT_TARGET_ANGLE]
                st.speed = cmd.speed
                st.angle = cmd.angle
                st.timer = 0
                st.duration = cmd.duration
            elif t & (
                CmdFlag.DIR_CHANGE | CmdFlag.DIR_CHANGE_AIM | CmdFlag.DIR_CHANGE_ABS
            ):
                self.ex_flags |= t
                st = self.states[_SLOT_DIR_CHANGE]
                # ZUN quirk: 状态槽的 angle 装的是 cmd.speed, speed 装 cmd.angle
                st.angle = cmd.speed
                st.speed = cmd.angle if cmd.angle > -999.0 else self.speed
                st.timer = 0
                st.duration = cmd.duration
                st.max_times = cmd.loop
                st.min_times = 0
            elif t & (CmdFlag.BOUNCE | CmdFlag.BOUNCE_NO_FLOOR):
                self.ex_flags |= t
                st = self.states[_SLOT_BOUNCE]
                st.speed = cmd.speed if cmd.speed >= 0.0 else self.speed
                st.max_times = cmd.duration  # 允许反弹次数
                st.duration = 0  # 复用为已反弹次数
            elif t == CmdFlag.SPAWN_DELAY:
                self.spawn_delay = cmd.duration
                self.cur_cmd_idx += 1
                continue
            self.cur_cmd_idx += 1
            return

    # ---- 每帧: 按 OnUpdate 的顺序跑激活的更新器 ----
    def step_commands(self, player_pos: Vec2, dt: float = 1.0) -> None:
        # 热路径: ex_flags 可能是 IntFlag 实例(3.12+ int|IntFlag 走 enum 反射),
        # enum 的 __and__ 是纯 Python 巨慢 —— 全程用 int 位型比较。
        f = int(self.ex_flags)
        if f & _F_BURST:
            self._update_burst(dt)
        if f & _F_TARGET_VEL:
            self._update_target_vel(dt)
        if f & _F_TARGET_ANGLE:
            self._update_target_angle(dt)
        if f & _F_DIR_CHANGE:
            self._update_dir_change(dt, CmdFlag.DIR_CHANGE, "relative", player_pos)
        if f & _F_DIR_CHANGE_ABS:
            self._update_dir_change(dt, CmdFlag.DIR_CHANGE_ABS, "absolute", player_pos)
        if f & _F_DIR_CHANGE_AIM:
            self._update_dir_change(dt, CmdFlag.DIR_CHANGE_AIM, "aim", player_pos)
        if f & _F_BOUNCE_ANY:
            self._update_bounce(dt)

    # ---- 更新器(逐一对应 BulletManager.cpp 的 UpdateBullet* 函数) ----
    def _update_burst(self, dt: float) -> None:
        """UpdateBulletBurstSpeed: 16 帧内附加速度从 +5 线性降到 +0。"""
        st = self.states[_SLOT_BURST]
        if st.timer <= 16:
            k = 5.0 - st.timer * 5.0 / 16.0
            self.vel = Vec2.from_angle(self.angle, (k + self.speed) * dt)
        else:
            self.ex_flags ^= CmdFlag.BURST
        st.timer += 1

    def _update_target_vel(self, dt: float) -> None:
        """UpdateBulletTargetVelocity: 每帧叠加目标速度矢量, 角度跟随合速度。"""
        st = self.states[_SLOT_TARGET_VEL]
        if st.timer >= st.duration:
            self.ex_flags &= ~CmdFlag.TARGET_VEL
        else:
            self.vel = self.vel + st.vel * dt
            if abs(self.vel.x) > 0.0001 or abs(self.vel.y) > 0.0001:
                self.angle = self.vel.angle()
        st.timer += 1

    def _update_target_angle(self, dt: float) -> None:
        """UpdateBulletTargetAngle: 角速度+加速度, 速度矢量整体重建。"""
        st = self.states[_SLOT_TARGET_ANGLE]
        if st.timer >= st.duration:
            self.ex_flags &= ~CmdFlag.TARGET_ANGLE
        else:
            self.angle = normalize_angle_diff(self.angle + st.angle * dt)
            self.speed += st.speed * dt
            self.vel = Vec2.from_angle(self.angle, self.speed * dt)
        st.timer += 1

    def _update_dir_change(
        self, dt: float, bit: CmdFlag, mode: str, player_pos: Vec2
    ) -> None:
        """UpdateBulletDirChange{,Absolute,AimAtPlayer}AndResume 三合一。

        前 duration 帧线性强减速到 0(vel 用衰减后的临时速度),
        到帧后转向(相对+=/绝对=/瞄准玩家+)并把速度置为目标速度;
        循环 loop 次后清 flag。三种模式共用一个命令槽。
        """
        st = self.states[_SLOT_DIR_CHANGE]
        if st.timer >= st.duration:
            st.min_times += 1
            if st.min_times >= st.max_times:
                self.ex_flags &= ~bit
            if mode == "relative":
                self.angle += st.angle
            elif mode == "absolute":
                self.angle = st.angle
            else:  # aim
                self.angle = normalize_angle_diff(
                    angle_to(self.pos, player_pos) + st.angle
                )
            self.speed = st.speed
            cur = self.speed
            st.timer = 0
        else:
            cur = self.speed - st.timer * self.speed / st.duration
        self.vel = Vec2.from_angle(self.angle, cur * dt)
        st.timer += 1

    def _update_bounce(self, dt: float) -> None:
        """UpdateBulletBounce: 出界反弹。底边只在 BOUNCE(0x400) 下弹。

        反弹次数存 st.duration(复用), 达到 st.max_times 后同时清两个反弹位
        (C++ 里是 exFlags & 0xfffff3ff)。
        """
        st = self.states[_SLOT_BOUNCE]
        hw, hh = self.size.x / 2.0, self.size.y / 2.0
        in_bounds = (
            self.pos.x + hw >= 0.0
            and self.pos.x - hw <= SCREEN_W
            and self.pos.y + hh >= 0.0
            and self.pos.y - hh <= SCREEN_H
        )
        if in_bounds:
            return
        if self.pos.x < 0.0 or self.pos.x >= SCREEN_W:
            self.angle = normalize_angle_diff(-self.angle - math.pi)
        if self.pos.y < 0.0 or (
            self.pos.y >= SCREEN_H and (int(self.ex_flags) & CmdFlag.BOUNCE.value)
        ):
            self.angle = -self.angle
        self.speed = st.speed
        self.vel = Vec2.from_angle(self.angle, self.speed * dt)
        st.duration += 1
        if st.duration >= st.max_times:
            self.ex_flags &= ~(CmdFlag.BOUNCE | CmdFlag.BOUNCE_NO_FLOOR)


def step_bullet(bs: BulletState, player_pos: Vec2, dt: float = 1.0) -> None:
    """让一颗带命令的弹走一帧 —— 顺序对照 OnUpdate 的 BULLET_NORMAL 分支:
    RunCommands → exFlags 更新器 → spawnDelay 递减 → pos += velocity。
    (出界/碰撞判定由 BulletWorld / player 层负责。)
    """
    bs.run_commands(dt)
    bs.step_commands(player_pos, dt)
    if bs.spawn_delay != 0:
        bs.spawn_delay -= 1
    bs.pos = bs.pos + bs.vel
