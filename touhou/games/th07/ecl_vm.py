"""TH07(东方妖妖梦)专属 ECL 虚拟机实现。

对照 th07 反编译源码 `EclManager.cpp/.hpp`、`EnemyEclInstr.cpp`:
- ``EclVarId``: TH07 变量命名空间(10000~10073, EclManager.hpp 的变量表);
- ``EclMachineTh07``: 变量系统(_get_int/_set_int/_get_float/_set_float +
  GET_BOSS 系的 _peer_int/_peer_float) + 161 条 opcode handler + 作品辅助
  方法(_spawn_bullet_pattern/_spawn_laser_pattern/_begin_spellcard/
  _init_interp/_exit_angle/_jitter_pos/_spawn_items)。

VM 框架(主循环/调用栈/分发/参数编解码)在引擎层 ``engine/ecl_base.py``
的 ``EclMachineBase``; ex 指令(24 条 boss 特技)语义在宿主层
``ecl_host.GameEclHost.run_ex_instr``, VM 只负责分发。

新增 opcode 的注册示例:

    @EclMachineTh07.register(EclOpcode.NEW_OP)
    def _op_new(m: EclMachineTh07, instr: EclInstr):
        e, w, ctx = m.enemy, m.world, m.current
        ...

    @EclMachineTh07.register(range(64, 73))   # 多 key 共享一个 handler
    def _op_bullet(m, instr): ...

handler 返回值契约: None=顺序前进 / EclInstr=跳转 / "restart" / "error"。
"""

from __future__ import annotations

import math
import struct
from enum import IntEnum
from typing import TYPE_CHECKING, Callable, cast

if TYPE_CHECKING:
    from .ecl_host import GameEclHost  # 仅类型检查期(cast 收窄宿主用)

from ...engine.ecl import (
    EclEnemyState,
    EclFile,
    EclInstr,
    EclOpcode,
    Vec3,
)
from ...engine.ecl_base import EclMachineBase
from ...logger import logger as log
from ...registry import register_ecl
from ...utils import (
    ZUN_2PI,
    ZUN_PI,
    add_normalize_angle,
    cdiv,
    cmod,
    f32,
    i16,
    i32,
)

# ---- TH07 专属 opcode(作品机制, 不进通用 EclOpcode —— 见 engine/ecl.py) ----
# Python 不能继承已有成员的 Enum, 故 th07 专属号单立 IntEnum;
# EclMachineBase.register 接受裸 int, 注册语义与通用号一致


class Th07EclOpcode(IntEnum):
    ADD_CHERRY_PLUS = 160  # cherryPlus 入账(樱点/结界机制; host.add_cherry_plus)
    FREEZE_ECL_DURING_BOMB = 161  # Bomb 中冻结本 VM 的 ECL 推进


# ---- TH07 变量命名空间(照抄 EclManager.hpp; 10000~10073) ----


class EclVarId(IntEnum):
    LOCAL_INT1_1 = 10000
    LOCAL_INT1_2 = 10001
    LOCAL_INT1_3 = 10002
    LOCAL_INT1_4 = 10003
    LOCAL_FLOAT1_1 = 10004
    LOCAL_FLOAT1_2 = 10005
    LOCAL_FLOAT1_3 = 10006
    LOCAL_FLOAT1_4 = 10007
    LOCAL_FLOAT1_5 = 10008
    LOCAL_FLOAT1_6 = 10009
    LOCAL_FLOAT1_7 = 10010
    LOCAL_FLOAT1_8 = 10011
    LOCAL_INT2_1 = 10012
    LOCAL_INT2_2 = 10013
    LOCAL_INT2_3 = 10014
    LOCAL_INT2_4 = 10015
    DIFFICULTY = 10016
    RANK = 10017
    POS_X = 10018
    POS_Y = 10019
    POS_Z = 10020
    PLAYER_POS_X = 10021
    PLAYER_POS_Y = 10022
    PLAYER_POS_Z = 10023
    ANGLE_TO_PLAYER = 10024
    CUR_TIME = 10025
    DISTANCE_FROM_PLAYER = 10026
    LIFE = 10027
    PLAYER_SHOTTYPE = 10028
    LOCAL_INT3_1 = 10029
    LOCAL_INT3_2 = 10030
    LOCAL_INT3_3 = 10031
    LOCAL_INT3_4 = 10032
    LOCAL_FLOAT3_1 = 10033
    LOCAL_FLOAT3_2 = 10034
    LOCAL_FLOAT3_3 = 10035
    LOCAL_FLOAT3_4 = 10036
    GLOBAL_INT_1 = 10037
    GLOBAL_INT_2 = 10038
    GLOBAL_INT_3 = 10039
    GLOBAL_INT_4 = 10040
    GLOBAL_FLOAT_1 = 10041
    GLOBAL_FLOAT_2 = 10042
    GLOBAL_FLOAT_3 = 10043
    GLOBAL_FLOAT_4 = 10044
    ANGLE = 10045
    ANGULAR_VELOCITY = 10046
    MOVE_SPEED = 10047
    MOVE_ACCELERATION = 10048
    MOVE_RADIUS = 10049
    MOVE_INTERP_ORIGIN_X = 10050
    MOVE_INTERP_ORIGIN_Y = 10051
    MOVE_INTERP_ORIGIN_Z = 10052
    MOVE_ANGLE = 10053
    MOVE_ANGULAR_VELOCITY = 10054
    RNG = 10055
    RNG_CUSTOM_BOUND = 10056
    MOVE_INTERP_TARGET_X = 10057
    MOVE_INTERP_TARGET_Y = 10058
    MOVE_INTERP_TARGET_Z = 10059
    RNG_RADIAN = 10060
    LAST_DAMAGE = 10061
    BOSS_ID = 10062
    DELTA_POS_X = 10063
    DELTA_POS_Y = 10064
    DELTA_POS_Z = 10065
    BOSS_LIFE_THRESHOLD1 = 10066
    BOSS_LIFE_THRESHOLD2 = 10067
    BOSS_LIFE_THRESHOLD3 = 10068
    BOSS_LIFE_THRESHOLD4 = 10069
    ITEMDROP = 10070
    SCORE = 10071
    LOCAL_FLOAT2_1 = 10072
    LOCAL_FLOAT2_2 = 10073


@register_ecl("th07", file_format=EclFile)
class EclMachineTh07(EclMachineBase):
    """TH07 的 ECL VM: 框架(``EclMachineBase``) + 本作变量映射/辅助方法。

    opcode handler 全部在本模块底部用 ``@EclMachineTh07.register`` 登记。
    """

    # 插值写位置分量时要回算 axis_speed(见基类 _step_interps)
    _INTERP_POS_VARS = (EclVarId.POS_X, EclVarId.POS_Y, EclVarId.POS_Z)

    # ---- 变量系统(EclManager::GetVar/GetVarValue/GetFloatVar/GetFloatVarValue) ----

    def _get_int(self, var_id: int) -> int:
        e, w, a = self.enemy, self.world, self.current.args
        v = EclVarId(var_id) if 10000 <= var_id <= 10073 else None
        if v is None:
            return var_id  # C default: 原样返回(即立即数)
        if EclVarId.LOCAL_INT1_1 <= v <= EclVarId.LOCAL_INT1_4:
            return a.int_vars1[var_id - 10000]
        if EclVarId.LOCAL_INT3_1 <= v <= EclVarId.LOCAL_INT3_4:
            return a.global_ints[var_id - 10029]
        if EclVarId.LOCAL_INT2_1 <= v <= EclVarId.LOCAL_INT2_4:
            return a.int_vars2[var_id - 10012]
        if EclVarId.GLOBAL_INT_1 <= v <= EclVarId.GLOBAL_INT_4:
            return w.global_ints[var_id - 10037]
        if v == EclVarId.DIFFICULTY:
            return w.difficulty
        if v == EclVarId.RANK:
            return w.rank
        if v == EclVarId.CUR_TIME:
            return e.timer
        if v == EclVarId.LIFE:
            return e.life
        if v == EclVarId.ITEMDROP:
            return e.item_drop
        if v == EclVarId.SCORE:
            return e.score
        if v == EclVarId.PLAYER_SHOTTYPE:
            return w.player_shottype
        if v == EclVarId.BOSS_ID:
            return e.boss_id
        if v == EclVarId.LAST_DAMAGE:
            return e.last_damage
        if v == EclVarId.RNG:
            return w.rng.u32()
        if v == EclVarId.RNG_CUSTOM_BOUND:
            return w.rng.int_below(a.global_ints[0]) + a.global_ints[1]
        if EclVarId.BOSS_LIFE_THRESHOLD1 <= v <= EclVarId.BOSS_LIFE_THRESHOLD4:
            return e.life_callback_threshold[var_id - 10066]
        if v in (
            EclVarId.POS_X,
            EclVarId.POS_Y,
            EclVarId.POS_Z,
            EclVarId.PLAYER_POS_X,
            EclVarId.PLAYER_POS_Y,
            EclVarId.PLAYER_POS_Z,
        ):
            return int(self._get_float(var_id))  # C: float 字段按位读出当 int 用会错,
            # 但源码 int 版确实直接 return enemy->pos.x(f32→i32 截断)
        return int(self._get_float(var_id))  # 其余纯 float 变量: f32→i32

    def _set_int(self, var_id: int, value: int) -> None:
        """C GetVar 的可写集合; 不在集合里的写入被丢弃(C 写进指令内存, 无意义)。"""
        e, w, a = self.enemy, self.world, self.current.args
        value = i32(value)
        if EclVarId.LOCAL_INT1_1 <= var_id <= EclVarId.LOCAL_INT1_4:
            a.int_vars1[var_id - 10000] = value
        elif EclVarId.LOCAL_INT3_1 <= var_id <= EclVarId.LOCAL_INT3_4:
            a.global_ints[var_id - 10029] = value
        elif EclVarId.LOCAL_INT2_1 <= var_id <= EclVarId.LOCAL_INT2_4:
            a.int_vars2[var_id - 10012] = value
        elif EclVarId.GLOBAL_INT_1 <= var_id <= EclVarId.GLOBAL_INT_4:
            w.global_ints[var_id - 10037] = value
        elif var_id == EclVarId.DIFFICULTY:
            w.difficulty = value
        elif var_id == EclVarId.RANK:
            w.rank = value
        elif var_id == EclVarId.CUR_TIME:
            e.timer = value
        elif var_id == EclVarId.LIFE:
            e.life = value
        elif var_id == EclVarId.ITEMDROP:
            e.item_drop = value
        elif var_id == EclVarId.SCORE:
            e.score = value
        # default: 丢弃

    def _get_float(self, var_id: int) -> float:
        return self._get_float_value(var_id, float(var_id))

    def _get_float_value(self, var_id: int, raw: float) -> float:
        """GetFloatVarValue: var_id 是 (i32) 转换后的值, raw 是原始 f32(默认值)。"""
        e, w, a = self.enemy, self.world, self.current.args
        if not (10000 <= var_id <= 10073):
            return raw
        v = EclVarId(var_id)
        if EclVarId.LOCAL_FLOAT1_1 <= v <= EclVarId.LOCAL_FLOAT1_8:
            return a.float_vars1[var_id - 10004]
        if EclVarId.LOCAL_FLOAT3_1 <= v <= EclVarId.LOCAL_FLOAT3_4:
            return a.global_floats[var_id - 10033]
        if EclVarId.LOCAL_FLOAT2_1 <= v <= EclVarId.LOCAL_FLOAT2_2:
            return a.float_vars2[var_id - 10072]
        if EclVarId.GLOBAL_FLOAT_1 <= v <= EclVarId.GLOBAL_FLOAT_4:
            return w.global_floats[var_id - 10041]
        if v == EclVarId.POS_X:
            return e.pos.x
        if v == EclVarId.POS_Y:
            return e.pos.y
        if v == EclVarId.POS_Z:
            return e.pos.z
        if v == EclVarId.PLAYER_POS_X:
            return w.player_pos.x
        if v == EclVarId.PLAYER_POS_Y:
            return w.player_pos.y
        if v == EclVarId.PLAYER_POS_Z:
            return w.player_pos.z
        if v == EclVarId.ANGLE_TO_PLAYER:
            return w.angle_to_player(e.pos)
        if v == EclVarId.DISTANCE_FROM_PLAYER:
            return (w.player_pos - e.pos).length
        if v == EclVarId.ANGLE:
            return e.angle
        if v == EclVarId.ANGULAR_VELOCITY:
            return e.angular_velocity
        if v == EclVarId.MOVE_SPEED:
            return e.move_speed
        if v == EclVarId.MOVE_ACCELERATION:
            return e.move_acceleration
        if v == EclVarId.MOVE_RADIUS:
            return e.move_radius
        if v == EclVarId.MOVE_ANGLE:
            return e.move_angle
        if v == EclVarId.MOVE_ANGULAR_VELOCITY:
            return e.move_angular_velocity
        if v == EclVarId.MOVE_INTERP_ORIGIN_X:
            return e.move_interp_start_pos.x
        if v == EclVarId.MOVE_INTERP_ORIGIN_Y:
            return e.move_interp_start_pos.y
        if v == EclVarId.MOVE_INTERP_ORIGIN_Z:
            return e.move_interp_start_pos.z
        if v == EclVarId.MOVE_INTERP_TARGET_X:
            return e.move_interp.x
        if v == EclVarId.MOVE_INTERP_TARGET_Y:
            return e.move_interp.y
        if v == EclVarId.MOVE_INTERP_TARGET_Z:
            return e.move_interp.z
        if v == EclVarId.DELTA_POS_X:
            return e.delta_pos.x
        if v == EclVarId.DELTA_POS_Y:
            return e.delta_pos.y
        if v == EclVarId.DELTA_POS_Z:
            return e.delta_pos.z
        if v == EclVarId.RNG:
            return w.rng.unit()
        if v == EclVarId.RNG_CUSTOM_BOUND:
            return w.rng.unit() * a.global_floats[0] + a.global_floats[1]
        if v == EclVarId.RNG_RADIAN:
            return w.rng.unit() * ZUN_2PI - ZUN_PI
        if EclVarId.BOSS_LIFE_THRESHOLD1 <= v <= EclVarId.BOSS_LIFE_THRESHOLD4:
            return float(e.life_callback_threshold[var_id - 10066])
        # int 变量按 (f32) 转换
        return float(self._get_int(var_id))

    def _set_float(self, var_id: int, value: float) -> None:
        """C GetFloatVar 的可写集合; 其余丢弃。"""
        e, w, a = self.enemy, self.world, self.current.args
        value = f32(value)
        if EclVarId.LOCAL_FLOAT1_1 <= var_id <= EclVarId.LOCAL_FLOAT1_8:
            a.float_vars1[var_id - 10004] = value
        elif EclVarId.LOCAL_FLOAT3_1 <= var_id <= EclVarId.LOCAL_FLOAT3_4:
            a.global_floats[var_id - 10033] = value
        elif EclVarId.LOCAL_FLOAT2_1 <= var_id <= EclVarId.LOCAL_FLOAT2_2:
            a.float_vars2[var_id - 10072] = value
        elif EclVarId.GLOBAL_FLOAT_1 <= var_id <= EclVarId.GLOBAL_FLOAT_4:
            w.global_floats[var_id - 10041] = value
        elif var_id == EclVarId.POS_X:
            e.pos.x = value
        elif var_id == EclVarId.POS_Y:
            e.pos.y = value
        elif var_id == EclVarId.POS_Z:
            e.pos.z = value
        elif var_id == EclVarId.PLAYER_POS_X:
            w.player_pos.x = value
        elif var_id == EclVarId.PLAYER_POS_Y:
            w.player_pos.y = value
        elif var_id == EclVarId.PLAYER_POS_Z:
            w.player_pos.z = value
        elif var_id == EclVarId.MOVE_INTERP_ORIGIN_X:
            e.move_interp_start_pos.x = value
        elif var_id == EclVarId.MOVE_INTERP_ORIGIN_Y:
            e.move_interp_start_pos.y = value
        elif var_id == EclVarId.MOVE_INTERP_ORIGIN_Z:
            e.move_interp_start_pos.z = value
        elif var_id == EclVarId.MOVE_INTERP_TARGET_X:
            e.move_interp.x = value
        elif var_id == EclVarId.MOVE_INTERP_TARGET_Y:
            e.move_interp.y = value
        elif var_id == EclVarId.MOVE_INTERP_TARGET_Z:
            e.move_interp.z = value
        elif var_id == EclVarId.ANGLE:
            e.angle = value
        elif var_id == EclVarId.ANGULAR_VELOCITY:
            e.angular_velocity = value
        elif var_id == EclVarId.MOVE_SPEED:
            e.move_speed = value
        elif var_id == EclVarId.MOVE_ACCELERATION:
            e.move_acceleration = value
        elif var_id == EclVarId.MOVE_RADIUS:
            e.move_radius = value
        elif var_id == EclVarId.MOVE_ANGLE:
            e.move_angle = value
        elif var_id == EclVarId.MOVE_ANGULAR_VELOCITY:
            e.move_angular_velocity = value
        # default: 丢弃

    # ---- GET_BOSS 系: 以别的 boss 为上下文取变量 ----

    def _peer_args(self, boss: EclEnemyState):
        """boss (EclEnemyState) 当前上下文的 args —— 经 host.enemy_by_state
        反查 boss 的 EclEnemy.machine.current.args; 查不到(裸跑/无该宿主的
        单测)退回 None (调用方维持自身 args 的旧行为)。"""
        registry = getattr(self.host, "enemy_by_state", None)
        if not registry:
            return None
        peer = registry.get(id(boss))
        return peer.machine.current.args if peer is not None else None

    def _peer_int(self, boss: EclEnemyState, instr: EclInstr, arg_idx: int) -> int:
        """GET_BOSS_INT: 以别的 boss 为上下文取变量值 (EclManager.cpp:998-1002)。

        C 的 GetVarValue(boss, ...) 读 boss->currentContext.eclContextArgs
        (EclManager.cpp:116+), 故本地变量(intVars/floatVars)须取自 boss
        机器的当前 args, 而非调用方(本机)的 —— 旧实现只换 enemy 没换
        args, 读本地变量时拿到的是调用方自己的值 (3 面爱丽丝人偶 sub43
        轮询 boss LOCAL_INT2_3(10014) 恒得 0, sub44 永远进不去)。"""
        saved_enemy = self.enemy
        self.enemy = boss
        ctx = self.current
        saved_args = ctx.args
        peer_args = self._peer_args(boss)
        if peer_args is not None:
            ctx.args = peer_args
        try:
            return self._int_arg(instr, arg_idx)
        finally:
            ctx.args = saved_args
            self.enemy = saved_enemy

    def _peer_float(self, boss: EclEnemyState, instr: EclInstr, arg_idx: int) -> float:
        """GET_BOSS_INT 的 float 版 (EclManager.cpp:1003-1007), args 同上。"""
        saved_enemy = self.enemy
        self.enemy = boss
        ctx = self.current
        saved_args = ctx.args
        peer_args = self._peer_args(boss)
        if peer_args is not None:
            ctx.args = peer_args
        try:
            return self._float_arg(instr, arg_idx)
        finally:
            ctx.args = saved_args
            self.enemy = saved_enemy

    # ---- 作品辅助方法(弹幕/激光/符卡/插值/道具) ----

    def _init_interp(self, instr: EclInstr) -> None:
        ctx = self.current
        target = int(instr.arg_float(0))  # f32 值形式存的变量 id
        for it in ctx.interps:
            if it.active and it.target_var != target:
                continue
            it.active = True
            it.timer = 0
            it.target_var = target
            it.duration = self._int_arg(instr, 1)
            it.func_idx = self._int_arg(instr, 2)
            it.easing = self._int_arg(instr, 3)
            it.params = [
                self._float_arg(instr, 4),
                self._float_arg(instr, 5),
                self._float_arg(instr, 6),
                self._float_arg(instr, 7),
            ]
            break

    def _spawn_bullet_pattern(self, instr: EclInstr) -> None:
        e, w = self.enemy, self.world
        if e.life <= 0:
            return
        p = e.bullet_props
        sprite = instr.arg_i16(0, 0)
        p.sprite = self._get_int(sprite) if instr.param_mask & 1 else sprite
        p.aim_mode = instr.id - 64
        p.count1 = self._int_arg(instr, 1, 2)
        p.count2 = self._int_arg(instr, 2, 3)
        p.pos = e.pos + e.shoot_offset
        p.angle1 = self._float_arg(instr, 5, 6)
        p.speed1 = self._float_arg(instr, 3, 4)
        p.angle2 = self._float_arg(instr, 6, 7)
        p.speed2 = self._float_arg(instr, 4, 5)
        if not w.spellcard_active:
            p.count1 = i16(p.count1 + e.bullet_rank_amount1(w.rank))
            if p.count1 <= 0:
                p.count1 = 1
            p.count2 = i16(p.count2 + e.bullet_rank_amount2(w.rank))
            if p.count2 <= 0:
                p.count2 = 1
            if p.speed1 != 0.0:
                p.speed1 = f32(p.speed1 + e.bullet_rank_speed(float(w.rank)))
                if p.speed1 < 0.3:
                    p.speed1 = 0.3
            p.speed2 = f32(p.speed2 + e.bullet_rank_speed(float(w.rank)) / 2.0)
            if p.speed2 < 0.3:
                p.speed2 = 0.3
        p.flags = instr.args[7]
        sprite_offset = instr.arg_i16(0, 1)
        p.sprite_offset = (
            self._get_int(sprite_offset) if instr.param_mask & 2 else sprite_offset
        )
        if not e.disable_bullets:
            self.host.spawn_bullet_pattern(p)

    def _spawn_laser_pattern(self, instr: EclInstr) -> None:
        e = self.enemy
        p = e.laser_props
        p.pos = e.pos + e.shoot_offset
        p.sprite = instr.arg_i16(0, 0)
        sprite_offset = instr.arg_i16(0, 1)
        p.sprite_offset = (
            self._get_int(sprite_offset) if instr.param_mask & 2 else sprite_offset
        )
        p.angle1 = self._float_arg(instr, 1, 2)
        p.speed1 = self._float_arg(instr, 2, 3)
        p.start_offset = self._float_arg(instr, 3, 4)
        p.end_offset = self._float_arg(instr, 4, 5)
        p.start_length = self._float_arg(instr, 5, 6)
        p.width = instr.arg_float(6)
        p.start_time = instr.arg_int(7)
        p.duration = instr.arg_int(8)
        p.end_time = instr.arg_int(9)
        p.hitbox_start_time = instr.arg_int(10)
        p.hitbox_end_time = instr.arg_int(11)
        p.flags = instr.args[12]
        p.type = 0 if instr.id == EclOpcode.SPAWN_LASER_PATTERN_MOVING else 1
        e.lasers[e.laser_idx & 31] = self.host.spawn_laser_pattern(p)

    def _jitter_pos(self) -> Vec3:
        e, w = self.enemy, self.world
        return Vec3(
            e.pos.x + w.rng.unit() * 128.0 - 64.0,
            e.pos.y + w.rng.unit() * 128.0 - 64.0,
            e.pos.z,
        )

    def _spawn_items(self, num: int) -> None:
        """ECL_SPAWN_ITEMS: 火力未满第一个掉大P其余小P, 满火力全掉点。"""
        w = self.world
        for i in range(num):
            pos = self._jitter_pos()
            if w.current_power < 128:
                self.host.spawn_item(pos, 2 if i == 0 else 0)  # POWER_BIG / POWER_SMALL
            else:
                self.host.spawn_item(pos, 1)  # ITEM_POINT

    def _begin_spellcard(self, instr: EclInstr) -> None:
        """BeginSpellcard: 解符卡名(XOR 0xaa), 状态交接给宿主(boss.py)。"""
        e = self.enemy
        name_bytes = bytes(b ^ 0xAA for b in instr.raw_arg_bytes()[4:52])
        name = name_bytes.split(b"\x00", 1)[0].decode("shift_jis", errors="replace")
        gui_id = instr.arg_i16(0, 0)
        spellcard_idx = instr.arg_u16(0, 1)
        # 宣告瞬间全屏弹转弹消点 (EclManager.cpp:673 RemoveAllBullets(1),
        # 在 spellcardInfo.isActive=1 之前)
        self.host.remove_all_bullets(True)
        # C 侧顺带重置 boss rank 参数
        e.bullet_rank_speed_low = -0.5
        e.bullet_rank_speed_high = 0.5
        e.bullet_rank_amount1_low = e.bullet_rank_amount1_high = 0
        e.bullet_rank_amount2_low = e.bullet_rank_amount2_high = 0
        self.host.begin_spellcard(e, gui_id, spellcard_idx, name)

    def _exit_angle(self, *, randomize: bool, simple: bool = False) -> float:
        """GET_EXIT_ANGLE(52) / RAND_EXIT_ANGLE(155): 朝屏幕外逃的随机角。"""
        e, w = self.enemy, self.world
        if simple:
            # 155: 只看玩家侧/右边缘
            if (w.player_pos.x < e.pos.x and e.pos.x > 96.0) or e.pos.x > 288.0:
                return add_normalize_angle(w.rng.unit() * 1.5707964 + 2.3561945, 0.0)
            return f32(w.rng.unit() * 1.5707964 - 0.7853982)
        if w.player_pos.x < e.pos.x:
            angle = add_normalize_angle(w.rng.unit() * 1.5707964 + 2.3561945, 0.0)
        else:
            angle = f32(w.rng.unit() * 1.5707964 - 0.7853982)
        if e.pos.x < e.lower_move_limit.x + 96.0:
            if angle > 1.5707964:
                angle = f32(3.1415927 - angle)
            elif angle < -1.5707964:
                angle = f32(-3.1415927 - angle)
        if e.upper_move_limit.x - 96.0 < e.pos.x:
            # 注意: C 源码这里用的是 enemy->angle(疑似原版 bug, 照抄)
            if 0.0 <= angle < 1.5707964:
                angle = f32(3.1415927 - e.angle)
            elif -1.5707964 < angle <= 0.0:
                angle = f32(-3.1415927 - angle)
        if e.lower_move_limit.y + 48.0 > e.pos.y and angle < 0.0:
            angle = -angle
        if e.upper_move_limit.y - 48.0 < e.pos.y and angle > 0.0:
            angle = -angle
        return angle


# ==========================================================================
# 161 条 opcode handler(从原 _execute 的 elif 链逐条拆出, self.→m. 机械替换)
# ==========================================================================


@EclMachineTh07.register(EclOpcode.UNIMP)
def _op_unimp(m: EclMachineTh07, instr: EclInstr):
    return "error"  # RunEcl 直接返回错误(= 脚本结束/despawn)


@EclMachineTh07.register((0, 141))
def _op_noop(m: EclMachineTh07, instr: EclInstr):
    # 0: 无操作标记(C 的 switch 没有 case 0, 编译器生成的时间同步点)
    # 141: C 枚举 140→142 跳号, switch 无 case 141, 二进制里等于无操作
    return None


@EclMachineTh07.register(EclOpcode.SET_WAIT_TIMER)
def _op_set_wait_timer(m: EclMachineTh07, instr: EclInstr):
    m.current.wait_timer = m._int_arg(instr, 0)


@EclMachineTh07.register(EclOpcode.DEC_JUMP)
def _op_dec_jump(m: EclMachineTh07, instr: EclInstr):
    t = m._int_target(instr, 2)
    if t is not None:
        m._set_int(t, m._get_int(t) - 1)
    if m._int_arg(instr, 2) <= 0:
        return None  # 顺序前进
    return m._do_jump(instr, instr.arg_int(0), instr.arg_int(1))


@EclMachineTh07.register(EclOpcode.JUMP)
def _op_jump(m: EclMachineTh07, instr: EclInstr):
    return m._do_jump(instr, instr.arg_int(0), instr.arg_int(1))


@EclMachineTh07.register(EclOpcode.SET_INT)
def _op_set_int(m: EclMachineTh07, instr: EclInstr):
    m._store_int(instr, 0, m._int_arg(instr, 1))


@EclMachineTh07.register(EclOpcode.SET_FLOAT)
def _op_set_float(m: EclMachineTh07, instr: EclInstr):
    m._store_float(instr, 0, m._float_arg(instr, 1))


@EclMachineTh07.register(EclOpcode.NORMALIZE_ANGLE)
def _op_normalize_angle(m: EclMachineTh07, instr: EclInstr):
    m._store_float(instr, 0, add_normalize_angle(m._float_arg(instr, 0), 0.0))


@EclMachineTh07.register(EclOpcode.RAND)
def _op_rand(m: EclMachineTh07, instr: EclInstr):
    m._store_int(instr, 0, m.world.rng.int_below(m._int_arg(instr, 1)))


@EclMachineTh07.register(EclOpcode.RAND_ADD)
def _op_rand_add(m: EclMachineTh07, instr: EclInstr):
    m._store_int(
        instr, 0, m.world.rng.int_below(m._int_arg(instr, 1)) + m._int_arg(instr, 2)
    )


@EclMachineTh07.register(EclOpcode.RAND_FLOAT)
def _op_rand_float(m: EclMachineTh07, instr: EclInstr):
    m._store_float(instr, 0, m.world.rng.unit() * m._float_arg(instr, 1))


@EclMachineTh07.register(EclOpcode.RAND_FLOAT_ADD)
def _op_rand_float_add(m: EclMachineTh07, instr: EclInstr):
    m._store_float(
        instr, 0, m.world.rng.unit() * m._float_arg(instr, 1) + m._float_arg(instr, 2)
    )


@EclMachineTh07.register(EclOpcode.RAND_SIGN)
def _op_rand_sign(m: EclMachineTh07, instr: EclInstr):
    m._store_int(instr, 0, m.world.rng.sign() * m._int_arg(instr, 1))


@EclMachineTh07.register(EclOpcode.RAND_SIGN_FLOAT)
def _op_rand_sign_float(m: EclMachineTh07, instr: EclInstr):
    m._store_float(instr, 0, float(m.world.rng.sign()) * m._float_arg(instr, 1))


_INC_DEC: dict[int, int] = {int(EclOpcode.INC): 1, int(EclOpcode.DEC): -1}


@EclMachineTh07.register(tuple(_INC_DEC))
def _op_inc_dec(m: EclMachineTh07, instr: EclInstr):
    t = m._int_target(instr, 0)
    if t is not None:
        m._set_int(t, m._get_int(t) + _INC_DEC[instr.id])


@EclMachineTh07.register(EclOpcode.GET_BOSS_INT)
def _op_get_boss_int(m: EclMachineTh07, instr: EclInstr):
    boss = m.world.bosses[m._int_arg(instr, 2) & 7]
    if boss is None:
        return None
    value = m._peer_int(boss, instr, 1)
    m._store_int(instr, 0, value)


@EclMachineTh07.register(EclOpcode.GET_BOSS_FLOAT)
def _op_get_boss_float(m: EclMachineTh07, instr: EclInstr):
    boss = m.world.bosses[m._int_arg(instr, 2) & 7]
    if boss is None:
        return None
    fvalue = m._peer_float(boss, instr, 1)
    m._store_float(instr, 0, fvalue)


# 算术四则(+取模) 5 合一: int 版
_INT_BINOP: dict[int, Callable[[int, int], int]] = {
    int(EclOpcode.ADD): lambda a, b: a + b,
    int(EclOpcode.SUB): lambda a, b: a - b,
    int(EclOpcode.MUL): lambda a, b: a * b,
    int(EclOpcode.DIV): lambda a, b: cdiv(a, b) if b else 0,
    int(EclOpcode.MOD): lambda a, b: cmod(a, b) if b else 0,
}


@EclMachineTh07.register(tuple(_INT_BINOP))
def _op_int_arith(m: EclMachineTh07, instr: EclInstr):
    m._store_int(
        instr, 0, _INT_BINOP[instr.id](m._int_arg(instr, 1), m._int_arg(instr, 2))
    )


# 算术四则(+取模) 5 合一: float 版
_FLOAT_BINOP: dict[int, Callable[[float, float], float]] = {
    int(EclOpcode.ADD_FLOAT): lambda a, b: a + b,
    int(EclOpcode.SUB_FLOAT): lambda a, b: a - b,
    int(EclOpcode.MUL_FLOAT): lambda a, b: a * b,
    int(EclOpcode.DIV_FLOAT): lambda a, b: a / b if b != 0.0 else 0.0,
    int(EclOpcode.MOD_FLOAT): lambda a, b: math.fmod(a, b) if b != 0.0 else 0.0,
}


@EclMachineTh07.register(tuple(_FLOAT_BINOP))
def _op_float_arith(m: EclMachineTh07, instr: EclInstr):
    m._store_float(
        instr, 0, _FLOAT_BINOP[instr.id](m._float_arg(instr, 1), m._float_arg(instr, 2))
    )


@EclMachineTh07.register(EclOpcode.SIN)
def _op_sin(m: EclMachineTh07, instr: EclInstr):
    m._store_float(instr, 0, math.sin(m._float_arg(instr, 1)))


@EclMachineTh07.register(EclOpcode.COS)
def _op_cos(m: EclMachineTh07, instr: EclInstr):
    m._store_float(instr, 0, math.cos(m._float_arg(instr, 1)))


@EclMachineTh07.register(EclOpcode.ATAN2)
def _op_atan2(m: EclMachineTh07, instr: EclInstr):
    m._store_float(
        instr,
        0,
        math.atan2(
            m._float_arg(instr, 4) - m._float_arg(instr, 2),
            m._float_arg(instr, 3) - m._float_arg(instr, 1),
        ),
    )


@EclMachineTh07.register(EclOpcode.LERP)
def _op_lerp(m: EclMachineTh07, instr: EclInstr):
    delta = m._float_arg(instr, 1) - m._float_arg(instr, 2)
    m._store_float(instr, 0, delta * m._float_arg(instr, 3) + m._float_arg(instr, 2))


@EclMachineTh07.register(EclOpcode.INIT_INTERP)
def _op_init_interp(m: EclMachineTh07, instr: EclInstr):
    m._init_interp(instr)


# 条件跳转 6 合一: int 版
_JUMP_IF_INT = (
    EclOpcode.JUMP_IF_EQ,
    EclOpcode.JUMP_IF_NEQ,
    EclOpcode.JUMP_IF_LT,
    EclOpcode.JUMP_IF_LEQ,
    EclOpcode.JUMP_IF_GT,
    EclOpcode.JUMP_IF_GEQ,
)


@EclMachineTh07.register(_JUMP_IF_INT)
def _op_jump_if_int(m: EclMachineTh07, instr: EclInstr):
    a, b = m._int_arg(instr, 0), m._int_arg(instr, 1)
    if m._compare(instr.id, a, b):
        return m._do_jump(instr, instr.arg_int(2), instr.arg_int(3))
    return None


# 条件跳转 6 合一: float 版
_JUMP_IF_FLOAT = (
    EclOpcode.JUMP_IF_EQ_FLOAT,
    EclOpcode.JUMP_IF_NEQ_FLOAT,
    EclOpcode.JUMP_IF_LT_FLOAT,
    EclOpcode.JUMP_IF_LEQ_FLOAT,
    EclOpcode.JUMP_IF_GT_FLOAT,
    EclOpcode.JUMP_IF_GEQ_FLOAT,
)


@EclMachineTh07.register(_JUMP_IF_FLOAT)
def _op_jump_if_float(m: EclMachineTh07, instr: EclInstr):
    fa, fb = m._float_arg(instr, 0), m._float_arg(instr, 1)
    if m._compare(instr.id, fa, fb):
        return m._do_jump(instr, instr.arg_int(2), instr.arg_int(3))
    return None


@EclMachineTh07.register(EclOpcode.SUB_CALL)
def _op_sub_call(m: EclMachineTh07, instr: EclInstr):
    e, w, ctx = m.enemy, m.world, m.current
    ctx.instr_offset = instr.offset + instr.size
    if not e.no_stack_ret:
        m._push_context()
    m.call_sub(instr.arg_int(0))
    # 新 sub 拿到活动全局变量的快照(C: eclContextArgs.globalVars = g_GlobalEclVars)
    ctx.args.global_ints = list(w.global_ints)
    ctx.args.global_floats = list(w.global_floats)
    return "restart"


@EclMachineTh07.register(EclOpcode.SUB_RET)
def _op_sub_ret(m: EclMachineTh07, instr: EclInstr):
    e, ctx = m.enemy, m.current
    if e.no_stack_ret:
        log.warning("ECL_SUB_RET with noStackRet")
    if not m.stack:
        log.error("ECL 调用栈下溢")
        return "error"
    if ctx.is_periodic_sub:
        e.saved_context_args = ctx.args.clone()
        ctx.is_periodic_sub = 0
    m.current = m.stack.pop()
    return "restart"


@EclMachineTh07.register(EclOpcode.SET_ANM)
def _op_set_anm(m: EclMachineTh07, instr: EclInstr):
    m.enemy.anm_idx = m._int_arg(instr, 0)


@EclMachineTh07.register(EclOpcode.SET_SUB_ANM)
def _op_set_sub_anm(m: EclMachineTh07, instr: EclInstr):
    e = m.enemy
    idx = m._int_arg(instr, 0)
    if 0 <= idx < len(e.sub_anm_idx):
        e.sub_anm_idx[idx] = m._int_arg(instr, 1)


@EclMachineTh07.register(EclOpcode.SET_DEATH_ANM)
def _op_set_death_anm(m: EclMachineTh07, instr: EclInstr):
    raw = instr.arg_bytes(0)
    m.enemy.death_anm = (
        struct.unpack("<b", raw[0:1])[0],
        raw[1],
        struct.unpack("<b", raw[2:3])[0],
    )


@EclMachineTh07.register(EclOpcode.SET_POS)
def _op_set_pos(m: EclMachineTh07, instr: EclInstr):
    e = m.enemy
    e.pos.set(m._float_arg(instr, 0), m._float_arg(instr, 1), m._float_arg(instr, 2))
    e.clamp_pos()


@EclMachineTh07.register(EclOpcode.SET_AXIS_SPEED)
def _op_set_axis_speed(m: EclMachineTh07, instr: EclInstr):
    e = m.enemy
    e.axis_speed.set(
        m._float_arg(instr, 0), m._float_arg(instr, 1), m._float_arg(instr, 2)
    )
    e.angle = f32(math.atan2(e.axis_speed.y, e.axis_speed.x))
    e.move_mode = 0


@EclMachineTh07.register(EclOpcode.SET_ANGULAR_VEL)
def _op_set_angular_vel(m: EclMachineTh07, instr: EclInstr):
    e = m.enemy
    e.angular_velocity = m._float_arg(instr, 0)
    e.move_mode = 1


@EclMachineTh07.register(EclOpcode.MOVE_AT_PLAYER)
def _op_move_at_player(m: EclMachineTh07, instr: EclInstr):
    e, w = m.enemy, m.world
    e.angle = add_normalize_angle(w.angle_to_player(e.pos), m._float_arg(instr, 0))
    e.move_speed = m._float_arg(instr, 1)
    e.move_mode = 1


@EclMachineTh07.register(EclOpcode.SET_MOVE_SPEED)
def _op_set_move_speed(m: EclMachineTh07, instr: EclInstr):
    e = m.enemy
    e.move_speed = m._float_arg(instr, 0)
    e.move_mode = 1


@EclMachineTh07.register(EclOpcode.SET_MOVE_ACCEL)
def _op_set_move_accel(m: EclMachineTh07, instr: EclInstr):
    e = m.enemy
    e.move_acceleration = m._float_arg(instr, 0)
    e.move_mode = 1


# 移动插值定时器 3 合一: polar(模式1) / radial(模式3) / interp(模式2)
_MOVE_INTERP_MODE: dict[int, int] = {
    int(EclOpcode.SET_MOVE_INTERP_TIMER_POLAR): 1,
    int(EclOpcode.SET_MOVE_INTERP_TIMER_RADIAL): 3,
    int(EclOpcode.SET_MOVE_INTERP_TIMER_INTERP): 2,
}


@EclMachineTh07.register(tuple(_MOVE_INTERP_MODE))
def _op_set_move_interp_timer(m: EclMachineTh07, instr: EclInstr):
    e = m.enemy
    e.move_mode = _MOVE_INTERP_MODE[instr.id]
    e.move_interp_timer = e.move_interp_start_time = m._int_arg(instr, 0)


@EclMachineTh07.register(range(64, 73))  # 弹幕生成 9 合一(aim_mode = id - 64)
def _op_spawn_bullet_pattern(m: EclMachineTh07, instr: EclInstr):
    m._spawn_bullet_pattern(instr)


@EclMachineTh07.register(EclOpcode.INIT_BULLET_CMD)
def _op_init_bullet_cmd(m: EclMachineTh07, instr: EclInstr):
    cmd = m.enemy.bullet_props.commands[m._int_arg(instr, 0)]
    cmd.type = m._int_arg(instr, 1)
    cmd.flag = m._int_arg(instr, 2)
    cmd.duration = m._int_arg(instr, 3)
    cmd.loop_count = m._int_arg(instr, 4)
    cmd.speed = m._float_arg(instr, 5)
    cmd.angle = m._float_arg(instr, 6)


@EclMachineTh07.register(EclOpcode.SET_SHOOT_INTERVAL)
def _op_set_shoot_interval(m: EclMachineTh07, instr: EclInstr):
    e, w = m.enemy, m.world
    e.shoot_interval = m._int_arg(instr, 0)
    if e.shoot_interval != 0:
        e.shoot_interval = i32(e.shoot_interval + e.shoot_interval_rank_delta(w.rank))
        e.shoot_interval_timer = 0


@EclMachineTh07.register(EclOpcode.SET_SHOOT_INTERVAL_RAND)
def _op_set_shoot_interval_rand(m: EclMachineTh07, instr: EclInstr):
    e, w = m.enemy, m.world
    e.shoot_interval = m._int_arg(instr, 0)
    if e.shoot_interval != 0:
        e.shoot_interval = i32(e.shoot_interval + e.shoot_interval_rank_delta(w.rank))
        e.shoot_interval_timer = w.rng.int_below(e.shoot_interval)


@EclMachineTh07.register(EclOpcode.DISABLE_BULLETS)
def _op_disable_bullets(m: EclMachineTh07, instr: EclInstr):
    m.enemy.disable_bullets = 1


@EclMachineTh07.register(EclOpcode.ENABLE_BULLETS)
def _op_enable_bullets(m: EclMachineTh07, instr: EclInstr):
    m.enemy.disable_bullets = 0


@EclMachineTh07.register(EclOpcode.SPAWN_PREV_BULLET_PATTERN)
def _op_spawn_prev_bullet_pattern(m: EclMachineTh07, instr: EclInstr):
    e = m.enemy
    e.bullet_props.pos = e.pos + e.shoot_offset
    m.host.spawn_bullet_pattern(e.bullet_props)


@EclMachineTh07.register(EclOpcode.SET_SHOOT_OFFSET)
def _op_set_shoot_offset(m: EclMachineTh07, instr: EclInstr):
    m.enemy.shoot_offset.set(
        m._float_arg(instr, 0), m._float_arg(instr, 1), m._float_arg(instr, 2)
    )


@EclMachineTh07.register(
    (EclOpcode.SPAWN_LASER_PATTERN_FIXED, EclOpcode.SPAWN_LASER_PATTERN_MOVING)
)
def _op_spawn_laser_pattern(m: EclMachineTh07, instr: EclInstr):
    m._spawn_laser_pattern(instr)


@EclMachineTh07.register(EclOpcode.SET_LASER_IDX)
def _op_set_laser_idx(m: EclMachineTh07, instr: EclInstr):
    m.enemy.laser_idx = m._int_arg(instr, 0)


@EclMachineTh07.register(EclOpcode.ADD_LASER_ANGLE)
def _op_add_laser_angle(m: EclMachineTh07, instr: EclInstr):
    h = m.enemy.lasers[m._int_arg(instr, 0) & 31]
    if h is not None:
        m.host.laser_add_angle(h, m._float_arg(instr, 1))


@EclMachineTh07.register(EclOpcode.SET_LASER_ANGLE)
def _op_set_laser_angle(m: EclMachineTh07, instr: EclInstr):
    h = m.enemy.lasers[m._int_arg(instr, 0) & 31]
    if h is not None:
        m.host.laser_set_angle(h, m._float_arg(instr, 1))


@EclMachineTh07.register(EclOpcode.AIM_LASER_ANGLE_AT_PLAYER)
def _op_aim_laser_angle_at_player(m: EclMachineTh07, instr: EclInstr):
    h = m.enemy.lasers[m._int_arg(instr, 0) & 31]
    if h is not None:
        m.host.laser_aim_at_player(h, m._float_arg(instr, 1))


@EclMachineTh07.register(EclOpcode.SET_LASER_POS_REL)
def _op_set_laser_pos_rel(m: EclMachineTh07, instr: EclInstr):
    e = m.enemy
    h = e.lasers[m._int_arg(instr, 0) & 31]
    if h is not None:
        m.host.laser_set_pos(
            h,
            Vec3(
                m._float_arg(instr, 1) + e.pos.x,
                m._float_arg(instr, 2) + e.pos.y,
                m._float_arg(instr, 3) + e.pos.z,
            ),
        )


@EclMachineTh07.register(EclOpcode.SET_LASER_HIDE_WARNING)
def _op_set_laser_hide_warning(m: EclMachineTh07, instr: EclInstr):
    h = m.enemy.lasers[m._int_arg(instr, 0) & 31]
    if h is not None:
        m.host.laser_set_hide_warning(h, m._int_arg(instr, 1))


@EclMachineTh07.register(EclOpcode.TEST_LASER_NOT_IN_USE)
def _op_test_laser_not_in_use(m: EclMachineTh07, instr: EclInstr):
    h = m.enemy.lasers[m._int_arg(instr, 0) & 31]
    m.current.laser_not_in_use = 0 if (h is not None and m.host.laser_in_use(h)) else 1


@EclMachineTh07.register(EclOpcode.STOP_LASER)
def _op_stop_laser(m: EclMachineTh07, instr: EclInstr):
    h = m.enemy.lasers[m._int_arg(instr, 0) & 31]
    if h is not None:
        m.host.laser_stop(h)


@EclMachineTh07.register(EclOpcode.CLEAR_LASERS)
def _op_clear_lasers(m: EclMachineTh07, instr: EclInstr):
    m.enemy.lasers = [None] * 32


@EclMachineTh07.register(EclOpcode.SET_LASER_START_LEN)
def _op_set_laser_start_len(m: EclMachineTh07, instr: EclInstr):
    h = m.enemy.lasers[m._int_arg(instr, 0) & 31]
    if h is not None:
        m.host.laser_set_start_length(h, m._float_arg(instr, 1))


@EclMachineTh07.register(EclOpcode.SET_LASER_OFFSETS)
def _op_set_laser_offsets(m: EclMachineTh07, instr: EclInstr):
    h = m.enemy.lasers[m._int_arg(instr, 0) & 31]
    if h is not None:
        m.host.laser_set_offsets(h, m._float_arg(instr, 1), m._float_arg(instr, 2))


@EclMachineTh07.register(EclOpcode.IDFK)
def _op_idfk(m: EclMachineTh07, instr: EclInstr):
    m.world.unused_9545f0 = m._int_arg(instr, 0)


@EclMachineTh07.register(EclOpcode.SET_BOSS)
def _op_set_boss(m: EclMachineTh07, instr: EclInstr):
    e, w = m.enemy, m.world
    idx = m._int_arg(instr, 0)
    if idx >= 0:
        w.bosses[idx & 7] = e
        e.is_boss = 1
        e.boss_id = idx
        m.host.set_boss(idx, e)
    else:
        if 0 <= e.boss_id < 8:
            w.bosses[e.boss_id] = None
            m.host.set_boss(e.boss_id, None)
        e.is_boss = 0


@EclMachineTh07.register(EclOpcode.SPAWN_EFFECT)
def _op_spawn_effect(m: EclMachineTh07, instr: EclInstr):
    pass  # 特效, 不接


@EclMachineTh07.register(EclOpcode.MOVE_DIR_TIME)
def _op_move_dir_time(m: EclMachineTh07, instr: EclInstr):
    e = m.enemy
    if m._int_arg(instr, 0) <= 0:
        e.angle = add_normalize_angle(m._float_arg(instr, 2), 0.0)
        e.move_speed = m._float_arg(instr, 3)
        e.move_mode = 1
        e.move_interp_timer = e.move_interp_start_time = m._int_arg(instr, 0)
    else:
        ang = add_normalize_angle(m._float_arg(instr, 2), 0.0)
        dist = m._float_arg(instr, 3) * m._int_arg(instr, 0)
        e.move_interp.set(f32(math.cos(ang) * dist), f32(math.sin(ang) * dist), 0.0)
        e.move_interp_start_pos = e.pos.copy()
        e.move_interp_timer = e.move_interp_start_time = m._int_arg(instr, 0)
        e.interp_easing = m._int_arg(instr, 1) & 0xFF
        e.move_mode = 2
        if e.mirror:
            e.move_interp.x = -e.move_interp.x


@EclMachineTh07.register(EclOpcode.MOVE_POS_TIME)
def _op_move_pos_time(m: EclMachineTh07, instr: EclInstr):
    e = m.enemy
    new_pos = Vec3(
        m._float_arg(instr, 2), m._float_arg(instr, 3), m._float_arg(instr, 4)
    )
    e.move_interp = new_pos - e.pos
    e.move_interp_start_pos = e.pos.copy()
    e.move_interp_timer = e.move_interp_start_time = m._int_arg(instr, 0)
    e.interp_easing = m._int_arg(instr, 1) & 0xFF
    e.move_mode = 2
    e.axis_speed = Vec3()
    if e.mirror:
        e.move_interp.x = -e.move_interp.x


@EclMachineTh07.register(EclOpcode.MOVE_ORBIT)
def _op_move_orbit(m: EclMachineTh07, instr: EclInstr):
    e = m.enemy
    e.move_interp_timer = e.move_interp_start_time = m._int_arg(instr, 0)
    e.move_interp_start_pos.set(
        m._float_arg(instr, 1), m._float_arg(instr, 2), m._float_arg(instr, 3)
    )
    e.move_angle = m._float_arg(instr, 4)
    e.move_angular_velocity = m._float_arg(instr, 5)
    e.move_radius = m._float_arg(instr, 6)
    e.move_radial_velocity = m._float_arg(instr, 7)
    e.move_mode = 3


@EclMachineTh07.register(EclOpcode.SET_ORBIT_RADIUS)
def _op_set_orbit_radius(m: EclMachineTh07, instr: EclInstr):
    e = m.enemy
    e.move_radius = m._float_arg(instr, 0)
    e.move_radial_velocity = m._float_arg(instr, 1)


@EclMachineTh07.register(EclOpcode.SET_ORBIT_ANGLE)
def _op_set_orbit_angle(m: EclMachineTh07, instr: EclInstr):
    e = m.enemy
    e.move_angle = m._float_arg(instr, 0)
    e.move_angular_velocity = m._float_arg(instr, 1)


@EclMachineTh07.register(EclOpcode.SET_MOVEMENT_BOUNDS)
def _op_set_movement_bounds(m: EclMachineTh07, instr: EclInstr):
    e = m.enemy
    e.lower_move_limit.x = m._float_arg(instr, 0)
    e.lower_move_limit.y = m._float_arg(instr, 1)
    e.upper_move_limit.x = m._float_arg(instr, 2)
    e.upper_move_limit.y = m._float_arg(instr, 3)
    e.has_movement_bounds = 1


@EclMachineTh07.register(EclOpcode.DISABLE_MOVEMENT_BOUNDS)
def _op_disable_movement_bounds(m: EclMachineTh07, instr: EclInstr):
    m.enemy.has_movement_bounds = 0


@EclMachineTh07.register(EclOpcode.RAND_FLOAT_RANGE)
def _op_rand_float_range(m: EclMachineTh07, instr: EclInstr):
    lo = m._float_arg(instr, 1)
    m._store_float(instr, 0, m.world.rng.unit() * (m._float_arg(instr, 2) - lo) + lo)


@EclMachineTh07.register(EclOpcode.GET_EXIT_ANGLE)
def _op_get_exit_angle(m: EclMachineTh07, instr: EclInstr):
    m._store_float(instr, 0, m._exit_angle(randomize=True))


@EclMachineTh07.register(EclOpcode.SET_MOVE_ANM)
def _op_set_move_anm(m: EclMachineTh07, instr: EclInstr):
    m.enemy.move_anm = (
        instr.arg_i16(0, 0),
        instr.arg_i16(0, 1),
        instr.arg_i16(1, 0),
        instr.arg_i16(1, 1),
        instr.arg_i16(2, 0),
    )


@EclMachineTh07.register(EclOpcode.SET_HITBOX_SIZE)
def _op_set_hitbox_size(m: EclMachineTh07, instr: EclInstr):
    m.enemy.hitbox_size.set(
        m._float_arg(instr, 0), m._float_arg(instr, 1), m._float_arg(instr, 2)
    )


@EclMachineTh07.register(EclOpcode.SET_GRAZE_SIZE)
def _op_set_graze_size(m: EclMachineTh07, instr: EclInstr):
    m.enemy.graze_size.set(
        m._float_arg(instr, 0), m._float_arg(instr, 1), m._float_arg(instr, 2)
    )


@EclMachineTh07.register(EclOpcode.SET_HAS_CONTACT_HITBOX)
def _op_set_has_contact_hitbox(m: EclMachineTh07, instr: EclInstr):
    m.enemy.has_contact_hitbox = instr.arg_bytes(0)[0]


@EclMachineTh07.register(EclOpcode.SET_CAN_BE_DAMAGED)
def _op_set_can_be_damaged(m: EclMachineTh07, instr: EclInstr):
    m.enemy.can_be_damaged = instr.arg_bytes(0)[0]


@EclMachineTh07.register(EclOpcode.SET_IS_HITTABLE)
def _op_set_is_hittable(m: EclMachineTh07, instr: EclInstr):
    m.enemy.is_hittable = instr.arg_bytes(0)[0]


@EclMachineTh07.register(EclOpcode.PLAY_SOUND)
def _op_play_sound(m: EclMachineTh07, instr: EclInstr):
    m.host.play_sound(m._int_arg(instr, 0))


@EclMachineTh07.register(EclOpcode.SET_DEATH_TYPE)
def _op_set_death_type(m: EclMachineTh07, instr: EclInstr):
    m.enemy.death_type = instr.arg_bytes(0)[0]


@EclMachineTh07.register(EclOpcode.SET_DEATH_CALLBACK_SUB)
def _op_set_death_callback_sub(m: EclMachineTh07, instr: EclInstr):
    m.enemy.death_callback_sub = instr.arg_bytes(0)[0]


@EclMachineTh07.register(EclOpcode.SET_INTERRUPT)
def _op_set_interrupt(m: EclMachineTh07, instr: EclInstr):
    m.enemy.interrupts[m._int_arg(instr, 1) & 31] = m._int_arg(instr, 0)


@EclMachineTh07.register(EclOpcode.SET_RUN_INTERRUPT)
def _op_set_run_interrupt(m: EclMachineTh07, instr: EclInstr):
    e = m.enemy
    e.run_interrupt = m._int_arg(instr, 0)
    if m._do_interrupt_call(instr, e.interrupts[e.run_interrupt]) is None:
        return "error"
    return "restart"


@EclMachineTh07.register(EclOpcode.SET_LIFE)
def _op_set_life(m: EclMachineTh07, instr: EclInstr):
    e = m.enemy
    e.life = e.max_life = m._int_arg(instr, 0)


@EclMachineTh07.register(EclOpcode.SET_BOSS_HEALTH)
def _op_set_boss_health(m: EclMachineTh07, instr: EclInstr):
    m.host.set_boss_health(
        m._int_arg(instr, 0),
        m._int_arg(instr, 1),
        m._int_arg(instr, 2),
        m._int_arg(instr, 3),
    )


@EclMachineTh07.register(EclOpcode.BEGIN_SPELLCARD)
def _op_begin_spellcard(m: EclMachineTh07, instr: EclInstr):
    m._begin_spellcard(instr)


@EclMachineTh07.register(EclOpcode.END_SPELLCARD)
def _op_end_spellcard(m: EclMachineTh07, instr: EclInstr):
    m.host.end_spellcard(m.enemy)


@EclMachineTh07.register(EclOpcode.SET_TIMER)
def _op_set_timer(m: EclMachineTh07, instr: EclInstr):
    m.enemy.timer = m._int_arg(instr, 0)


@EclMachineTh07.register(EclOpcode.SET_LIFE_CALLBACK_THRESHOLD)
def _op_set_life_callback_threshold(m: EclMachineTh07, instr: EclInstr):
    m.enemy.life_callback_threshold[0] = m._int_arg(instr, 0)


@EclMachineTh07.register(EclOpcode.SET_LIFE_CALLBACK_SUB)
def _op_set_life_callback_sub(m: EclMachineTh07, instr: EclInstr):
    m.enemy.life_callback_sub[0] = m._int_arg(instr, 0)


@EclMachineTh07.register(EclOpcode.SET_LIFE_CALLBACK)
def _op_set_life_callback(m: EclMachineTh07, instr: EclInstr):
    e = m.enemy
    idx = m._int_arg(instr, 0) & 3
    e.life_callback_threshold[idx] = m._int_arg(instr, 1)
    e.life_callback_sub[idx] = m._int_arg(instr, 2)


@EclMachineTh07.register(EclOpcode.SET_TIMER_CALLBACK_THRESHOLD)
def _op_set_timer_callback_threshold(m: EclMachineTh07, instr: EclInstr):
    e = m.enemy
    e.timer_callback_threshold = m._int_arg(instr, 0)
    e.timer = 0


@EclMachineTh07.register(EclOpcode.SET_TIMER_CALLBACK_SUB)
def _op_set_timer_callback_sub(m: EclMachineTh07, instr: EclInstr):
    m.enemy.timer_callback_sub = m._int_arg(instr, 0)


@EclMachineTh07.register(EclOpcode.SET_PERIODIC_CALLBACK)
def _op_set_periodic_callback(m: EclMachineTh07, instr: EclInstr):
    e, ctx = m.enemy, m.current
    e.periodic_timer = m._int_arg(instr, 0)
    e.periodic_callback_sub = m._int_arg(instr, 1)
    e.periodic_counter = 0
    e.saved_context_args = ctx.args.clone()


@EclMachineTh07.register(EclOpcode.SET_ENEMY_CAN_DIE)
def _op_set_enemy_can_die(m: EclMachineTh07, instr: EclInstr):
    m.enemy.can_die = instr.arg_bytes(0)[0]


@EclMachineTh07.register((EclOpcode.SPAWN_PARTICLES, EclOpcode.SPAWN_MOVING_PARTICLES))
def _op_spawn_particles(m: EclMachineTh07, instr: EclInstr):
    pass  # 粒子特效, 不接


@EclMachineTh07.register(EclOpcode.SPAWN_ITEMS)
def _op_spawn_items(m: EclMachineTh07, instr: EclInstr):
    m._spawn_items(m._int_arg(instr, 0))


@EclMachineTh07.register(EclOpcode.SPAWN_POINT_ITEMS)
def _op_spawn_point_items(m: EclMachineTh07, instr: EclInstr):
    for _ in range(m._int_arg(instr, 0)):
        m.host.spawn_item(m._jitter_pos(), 1)  # ITEM_POINT


@EclMachineTh07.register(EclOpcode.SET_VM_AUTO_ROTATE)
def _op_set_vm_auto_rotate(m: EclMachineTh07, instr: EclInstr):
    m.enemy.primary_vm_auto_rotate = instr.arg_bytes(0)[0]


@EclMachineTh07.register(EclOpcode.RUN_EX_INS)
def _op_run_ex_ins(m: EclMachineTh07, instr: EclInstr):
    m._run_ex(m._int_arg(instr, 0), instr)


@EclMachineTh07.register(EclOpcode.SET_EX_INS)
def _op_set_ex_ins(m: EclMachineTh07, instr: EclInstr):
    ctx = m.current
    idx = m._int_arg(instr, 0)
    if idx >= 0:
        ctx.ex_instr_idx = idx
        ctx.ex_instr = instr
    else:
        ctx.ex_instr_idx = -1


@EclMachineTh07.register(EclOpcode.ADD_TIME)
def _op_add_time(m: EclMachineTh07, instr: EclInstr):
    ctx = m.current
    ctx.time = i32(ctx.time + m._int_arg(instr, 0))


@EclMachineTh07.register(EclOpcode.SPAWN_ITEM)
def _op_spawn_item(m: EclMachineTh07, instr: EclInstr):
    m.host.spawn_item(m.enemy.pos, m._int_arg(instr, 0))


@EclMachineTh07.register(EclOpcode.SET_SCRIPT_WAIT_TIME)
def _op_set_script_wait_time(m: EclMachineTh07, instr: EclInstr):
    w = m.world
    w.script_wait_time = m._int_arg(instr, 0)
    m.host.set_script_wait_time(w.script_wait_time)


@EclMachineTh07.register(EclOpcode.SET_NUM_BOSS_LIFE_MARKERS)
def _op_set_num_boss_life_markers(m: EclMachineTh07, instr: EclInstr):
    m.host.set_boss_life_markers(m._int_arg(instr, 0))


@EclMachineTh07.register((EclOpcode.SPAWN_ENEMY_ABS, EclOpcode.SPAWN_ENEMY_REL))
def _op_spawn_enemy(m: EclMachineTh07, instr: EclInstr):
    e = m.enemy
    if e.life > 0:
        pos = Vec3(
            m._float_arg(instr, 1), m._float_arg(instr, 2), m._float_arg(instr, 3)
        )
        if instr.id == EclOpcode.SPAWN_ENEMY_REL:
            pos = pos + e.pos
        m.host.spawn_enemy(
            instr.arg_int(0),
            pos,
            m._int_arg(instr, 4),
            m._int_arg(instr, 5),
            m._int_arg(instr, 6),
            0,
            m.current.args.clone(),
        )


@EclMachineTh07.register(EclOpcode.REMOVE_ALL_ENEMIES)
def _op_remove_all_enemies(m: EclMachineTh07, instr: EclInstr):
    m.host.remove_all_enemies(8000, 0)


@EclMachineTh07.register(EclOpcode.SET_PRIMARY_VM_INTERRUPT)
def _op_set_primary_vm_interrupt(m: EclMachineTh07, instr: EclInstr):
    m.enemy.primary_vm_interrupt = m._int_arg(instr, 0)


@EclMachineTh07.register(EclOpcode.SET_VM_INTERRUPT)
def _op_set_vm_interrupt(m: EclMachineTh07, instr: EclInstr):
    e = m.enemy
    idx = instr.arg_int(0)
    if 0 <= idx < len(e.vm_interrupts):
        e.vm_interrupts[idx] = instr.arg_i16(1, 0)


@EclMachineTh07.register(EclOpcode.REMOVE_ALL_BULLETS_SPAWN_ITEMS)
def _op_remove_all_bullets_spawn_items(m: EclMachineTh07, instr: EclInstr):
    m.host.remove_all_bullets(True)


@EclMachineTh07.register(EclOpcode.SET_BULLET_SOUND)
def _op_set_bullet_sound(m: EclMachineTh07, instr: EclInstr):
    p = m.enemy.bullet_props
    idx = m._int_arg(instr, 0)
    if idx >= 0:
        p.sound_idx = idx
        p.flags |= 0x200
    else:
        p.flags &= 0xFFFFFDFF
    p.sound_override = m._int_arg(instr, 1)


@EclMachineTh07.register(EclOpcode.SET_NO_STACK_RET)
def _op_set_no_stack_ret(m: EclMachineTh07, instr: EclInstr):
    m.enemy.no_stack_ret = instr.arg_bytes(0)[0]


@EclMachineTh07.register(EclOpcode.SET_BULLET_RANK_PARAMS)
def _op_set_bullet_rank_params(m: EclMachineTh07, instr: EclInstr):
    e = m.enemy
    e.bullet_rank_speed_low = m._float_arg(instr, 0)
    e.bullet_rank_speed_high = m._float_arg(instr, 1)
    e.bullet_rank_amount1_low = m._int_arg(instr, 2)
    e.bullet_rank_amount1_high = m._int_arg(instr, 3)
    e.bullet_rank_amount2_low = m._int_arg(instr, 4)
    e.bullet_rank_amount2_high = m._int_arg(instr, 5)


@EclMachineTh07.register(EclOpcode.SET_HAS_NO_COLLISION)
def _op_set_has_no_collision(m: EclMachineTh07, instr: EclInstr):
    m.enemy.has_no_collision = instr.arg_bytes(0)[0]


@EclMachineTh07.register(EclOpcode.BIND_TIMER_CALLBACK_TO_DEATH)
def _op_bind_timer_callback_to_death(m: EclMachineTh07, instr: EclInstr):
    e = m.enemy
    e.timer_callback_sub = e.death_callback_sub
    e.timer = 0


@EclMachineTh07.register(EclOpcode.SET_IS_SURVIVAL_SPELLCARD)
def _op_set_is_survival_spellcard(m: EclMachineTh07, instr: EclInstr):
    m.enemy.is_survival_spellcard = instr.arg_bytes(0)[0]


@EclMachineTh07.register(EclOpcode.SET_IS_PROJECTILE)
def _op_set_is_projectile(m: EclMachineTh07, instr: EclInstr):
    m.enemy.is_projectile = instr.arg_bytes(0)[0]


@EclMachineTh07.register(EclOpcode.SET_DESPAWN_ON_OOB)
def _op_set_despawn_on_oob(m: EclMachineTh07, instr: EclInstr):
    m.enemy.disable_oob_despawn = instr.arg_bytes(0)[0]


@EclMachineTh07.register(EclOpcode.SET_TRAIL)
def _op_set_trail(m: EclMachineTh07, instr: EclInstr):
    m.enemy.trail = (
        instr.arg_bytes(0)[0],
        m._int_arg(instr, 1),
        m._int_arg(instr, 2),
        m._int_arg(instr, 3),
        0,
    )


@EclMachineTh07.register(EclOpcode.SET_GLOBAL_EFFECT_COLOR_MUL)
def _op_set_global_effect_color_mul(m: EclMachineTh07, instr: EclInstr):
    pass  # 渲染, 不接


@EclMachineTh07.register(EclOpcode.SET_INVINCIBILITY_TIMER)
def _op_set_invincibility_timer(m: EclMachineTh07, instr: EclInstr):
    m.enemy.invincibility_timer = m._int_arg(instr, 0)


@EclMachineTh07.register(EclOpcode.REMOVE_BULLETS_RADIUS)
def _op_remove_bullets_radius(m: EclMachineTh07, instr: EclInstr):
    m.host.remove_bullets_in_radius(m.enemy.pos, m._float_arg(instr, 0))


@EclMachineTh07.register(EclOpcode.SET_BOSS_RUN_INTERRUPT)
def _op_set_boss_run_interrupt(m: EclMachineTh07, instr: EclInstr):
    boss = m.world.bosses[m._int_arg(instr, 0) & 7]
    if boss is not None:
        boss.run_interrupt = m._int_arg(instr, 1)


@EclMachineTh07.register(EclOpcode.REMOVE_ALL_BULLETS_NO_ITEMS)
def _op_remove_all_bullets_no_items(m: EclMachineTh07, instr: EclInstr):
    m.host.remove_all_bullets(False)


@EclMachineTh07.register(EclOpcode.SET_SPECIAL_EFFECT_POS)
def _op_set_special_effect_pos(m: EclMachineTh07, instr: EclInstr):
    m.enemy.custom_special_effect_pos = m._int_arg(instr, 0)


@EclMachineTh07.register(EclOpcode.SET_PRIMARY_VM_ROT_Z)
def _op_set_primary_vm_rot_z(m: EclMachineTh07, instr: EclInstr):
    m.enemy.primary_vm_rot_z = m._float_arg(instr, 0)


@EclMachineTh07.register(EclOpcode.VEC_FROM_ANGLE_MAG)
def _op_vec_from_angle_mag(m: EclMachineTh07, instr: EclInstr):
    ang = m._float_arg(instr, 2)
    mag = m._float_arg(instr, 3)
    m._store_float(instr, 1, math.sin(ang) * mag)
    m._store_float(instr, 0, math.cos(ang) * mag)


@EclMachineTh07.register(EclOpcode.RAND_EXIT_ANGLE)
def _op_rand_exit_angle(m: EclMachineTh07, instr: EclInstr):
    m._store_float(instr, 0, m._exit_angle(randomize=True, simple=True))


@EclMachineTh07.register(Th07EclOpcode.ADD_CHERRY_PLUS)
def _op_add_cherry_plus(m: EclMachineTh07, instr: EclInstr):
    # 本作专属指令: 宿主即 GameEclHost(通用 EclHost 基座无樱点概念)
    cast("GameEclHost", m.host).add_cherry_plus(m._int_arg(instr, 0))


@EclMachineTh07.register(Th07EclOpcode.FREEZE_ECL_DURING_BOMB)
def _op_freeze_ecl_during_bomb(m: EclMachineTh07, instr: EclInstr):
    m.enemy.freeze_ecl_during_bombs = m._int_arg(instr, 0)
