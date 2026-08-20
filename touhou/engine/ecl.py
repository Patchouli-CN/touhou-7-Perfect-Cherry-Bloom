""" ECL 字节码解析与解释器 —— Pythonic。

对照 th07 反编译源码 `EclManager.cpp/.hpp`、`EnemyEclInstr.cpp` 还原:
.ecl 文件解析(头/sub 表/时间轴表) + 时间驱动的指令解释核心 + ECL 变量系统。

真实格式与规格 §B.6 的差异(以 EclManager.hpp 为准):
- 指令头是 12 字节: `u32 time; i16 id; i16 size; u8 unused; u8 skipOnDifficulty;
  u16 paramMask`(规格漏了 unused 字节)。
- `skipOnDifficulty` 是位掩码而非开关: `(skip & (1<<difficulty)) != 0` 时才执行。
- header 里 timelinePtr[16]/subTable[] 都是相对文件头的 i32 偏移(Load 时加基址)。
- sub 指令流以 `time=0xFFFFFFFF, id=-1` 的记录结尾; 时间轴以 `time<0` 结尾。

解释器不直接碰渲染/音效/弹幕世界, 世界交互全部走 `EclHost` 回调(默认无操作),
敌机状态集中在 `EclEnemyState`。enemies.py 整合时: 让 Enemy 持有/适配
EclEnemyState, 并实现 EclHost 把 spawn_bullet_pattern 等接到 BulletWorld。

ExIns (RUN_EX_INS/SET_EX_INS, 24 条 boss 特技) 覆盖状态:
- idx 0..23 全部在 ecl_host.GameEclHost 实现 (EnemyEclInstr.cpp 逐条对照);
  8 个真实 ecldata 中 0..23 均有出现 (统计见 tmp_title/exins_stats.py)。
- 表现侧不接: 9 (Effect1e 加速)、15 (闪屏)、19 (音乐淡出)、20 (复活蝶 BGM)
  以及各条里的 BombEffects/音效/换皮 —— 均为注释说明的无逻辑效果。
"""

from __future__ import annotations

import math
import struct
import msgspec
from enum import IntEnum
from typing import TYPE_CHECKING, Optional

from ..exceptions import EclParseError, NotImplementedEclError
from ..logger import logger as log
from ..registry import register_ecl
from ..utils import (
    ZUN_2PI,
    ZUN_PI,
    add_normalize_angle,
    cdiv,
    cmod,
    f32,
    i16,
    i32,
)
from .rng import Rng

if TYPE_CHECKING:
    from .enemies import EclEnemy  # 仅类型检查期(enemies 运行时依赖本模块)

# 游戏可视区(供 ClampPos 默认值/随机坐标用, 见 g_GameManager.playerMovementAreaSize)
PLAYFIELD_W = 384.0
PLAYFIELD_H = 448.0


# ---- 枚举(照抄 EclManager.hpp) ----

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


class EclOpcode(IntEnum):
    UNIMP = 1                       # RunEcl 直接返回错误(= 脚本结束/despawn)
    JUMP = 2
    DEC_JUMP = 3
    SET_INT = 4
    SET_FLOAT = 5
    RAND = 6
    RAND_ADD = 7
    RAND_FLOAT = 8
    RAND_FLOAT_ADD = 9
    RAND_SIGN = 10
    RAND_SIGN_FLOAT = 11
    ADD = 12
    SUB = 13
    MUL = 14
    DIV = 15
    MOD = 16
    INC = 17
    DEC = 18
    ADD_FLOAT = 19
    SUB_FLOAT = 20
    MUL_FLOAT = 21
    DIV_FLOAT = 22
    MOD_FLOAT = 23
    SIN = 24
    COS = 25
    ATAN2 = 26
    INIT_INTERP = 27
    JUMP_IF_EQ = 28
    JUMP_IF_EQ_FLOAT = 29
    JUMP_IF_NEQ = 30
    JUMP_IF_NEQ_FLOAT = 31
    JUMP_IF_LT = 32
    JUMP_IF_LT_FLOAT = 33
    JUMP_IF_LEQ = 34
    JUMP_IF_LEQ_FLOAT = 35
    JUMP_IF_GT = 36
    JUMP_IF_GT_FLOAT = 37
    JUMP_IF_GEQ = 38
    JUMP_IF_GEQ_FLOAT = 39
    NORMALIZE_ANGLE = 40
    SUB_CALL = 41
    SUB_RET = 42
    GET_BOSS_INT = 43
    GET_BOSS_FLOAT = 44
    SET_WAIT_TIMER = 45
    SET_POS = 46
    SET_AXIS_SPEED = 47
    SET_ANGULAR_VEL = 48
    SET_MOVE_SPEED = 49
    SET_MOVE_ACCEL = 50
    RAND_FLOAT_RANGE = 51
    GET_EXIT_ANGLE = 52
    MOVE_AT_PLAYER = 53
    MOVE_DIR_TIME = 54
    MOVE_POS_TIME = 55
    MOVE_ORBIT = 56
    SET_ORBIT_RADIUS = 57
    SET_ORBIT_ANGLE = 58
    SET_MOVE_INTERP_TIMER_POLAR = 59
    SET_MOVE_INTERP_TIMER_RADIAL = 60
    SET_MOVE_INTERP_TIMER_INTERP = 61
    SET_MOVEMENT_BOUNDS = 62
    DISABLE_MOVEMENT_BOUNDS = 63
    SPAWN_BULLET_PATTERN_SPREAD_AIMED = 64
    SPAWN_BULLET_PATTERN_SPREAD_ABS = 65
    SPAWN_BULLET_PATTERN_RING_AIMED = 66
    SPAWN_BULLET_PATTERN_RING_ABS = 67
    SPAWN_BULLET_PATTERN_RING_SHIFTED_AIMED = 68
    SPAWN_BULLET_PATTERN_RING_SHIFTED_ABS = 69
    SPAWN_BULLET_PATTERN_ANGLE_RANDOM = 70
    SPAWN_BULLET_PATTERN_RING_SPEED_RANDOM = 71
    SPAWN_BULLET_PATTERN_RANDOM = 72
    SET_SHOOT_INTERVAL = 73
    SET_SHOOT_INTERVAL_RAND = 74
    DISABLE_BULLETS = 75
    ENABLE_BULLETS = 76
    SPAWN_PREV_BULLET_PATTERN = 77
    SET_SHOOT_OFFSET = 78
    INIT_BULLET_CMD = 79
    REMOVE_ALL_BULLETS_SPAWN_ITEMS = 80
    SET_BULLET_SOUND = 81
    SPAWN_LASER_PATTERN_FIXED = 82
    SPAWN_LASER_PATTERN_MOVING = 83
    SET_LASER_IDX = 84
    ADD_LASER_ANGLE = 85
    AIM_LASER_ANGLE_AT_PLAYER = 86
    SET_LASER_POS_REL = 87
    TEST_LASER_NOT_IN_USE = 88
    STOP_LASER = 89
    BEGIN_SPELLCARD = 90
    END_SPELLCARD = 91
    SPAWN_ENEMY_ABS = 92
    SPAWN_ENEMY_REL = 93
    REMOVE_ALL_ENEMIES = 94
    SET_ANM = 95
    SET_MOVE_ANM = 96
    SET_SUB_ANM = 97
    SET_DEATH_ANM = 98
    SET_BOSS = 99
    SPAWN_EFFECT = 100
    SET_HITBOX_SIZE = 101
    SET_HAS_CONTACT_HITBOX = 102
    SET_CAN_BE_DAMAGED = 103
    SET_IS_HITTABLE = 104
    PLAY_SOUND = 105
    SET_DEATH_TYPE = 106
    SET_DEATH_CALLBACK_SUB = 107
    SET_INTERRUPT = 108
    SET_RUN_INTERRUPT = 109
    SET_LIFE = 110
    SET_TIMER = 111
    SET_LIFE_CALLBACK_THRESHOLD = 112
    SET_LIFE_CALLBACK_SUB = 113
    SET_TIMER_CALLBACK_THRESHOLD = 114
    SET_TIMER_CALLBACK_SUB = 115
    SET_ENEMY_CAN_DIE = 116
    SPAWN_PARTICLES = 117
    SPAWN_MOVING_PARTICLES = 118
    SPAWN_ITEMS = 119
    SET_VM_AUTO_ROTATE = 120
    RUN_EX_INS = 121
    SET_EX_INS = 122
    ADD_TIME = 123
    SPAWN_ITEM = 124
    SET_SCRIPT_WAIT_TIME = 125
    SET_NUM_BOSS_LIFE_MARKERS = 126
    SET_PRIMARY_VM_INTERRUPT = 128
    SET_VM_INTERRUPT = 129
    SET_NO_STACK_RET = 130
    SET_BULLET_RANK_PARAMS = 131
    SET_HAS_NO_COLLISION = 132
    BIND_TIMER_CALLBACK_TO_DEATH = 133
    CLEAR_LASERS = 134
    SET_IS_SURVIVAL_SPELLCARD = 135
    SET_IS_PROJECTILE = 136
    SET_DESPAWN_ON_OOB = 137
    SET_TRAIL = 138
    SET_BOSS_HEALTH = 139
    SET_GLOBAL_EFFECT_COLOR_MUL = 140
    SET_INVINCIBILITY_TIMER = 142
    REMOVE_BULLETS_RADIUS = 143
    SET_PERIODIC_CALLBACK = 144
    SET_BOSS_RUN_INTERRUPT = 145
    REMOVE_ALL_BULLETS_NO_ITEMS = 146
    IDFK = 147
    SET_LIFE_CALLBACK = 148
    SET_SPECIAL_EFFECT_POS = 149
    SET_PRIMARY_VM_ROT_Z = 150
    VEC_FROM_ANGLE_MAG = 151
    SET_LASER_ANGLE = 152
    SET_GRAZE_SIZE = 153
    SPAWN_POINT_ITEMS = 154
    RAND_EXIT_ANGLE = 155
    SET_LASER_HIDE_WARNING = 156
    SET_LASER_START_LEN = 157
    SET_LASER_OFFSETS = 158
    LERP = 159
    ADD_CHERRY_PLUS = 160
    FREEZE_ECL_DURING_BOMB = 161


# ---- 文件结构(解析) ----

_INSTR_HEADER = struct.Struct("<IhhBBH")  # time, id, size, unused, skipOnDifficulty, paramMask
_INSTR_HEADER_SIZE = _INSTR_HEADER.size  # 12
_TIMELINE_HEADER = struct.Struct("<hhhh")  # time, arg0, opcode, size

# sub 指令流终止记录
_TERMINATOR_ID = -1


class EclInstr(msgspec.Struct, frozen=True):
    """一条解析好的 ECL 指令。args 是 u32 原始字, 按 paramMask 解释。"""

    offset: int            # 文件内绝对偏移(模拟 C 的 EclRawInstr*)
    time: int              # u32: 到点(context time == time)才执行
    id: int                # i16: EclOpcode 或 -1(终止)
    size: int              # i16: 整条字节数(含 12 字节头)
    skip_difficulty: int   # u8 位掩码: 当前难度位为 1 才执行(0xFF = 全难度)
    param_mask: int        # u16: bit i = 1 → args[i] 是变量 id 而非立即数
    args: tuple[int, ...]  # u32 字

    def arg_int(self, idx: int) -> int:
        return i32(self.args[idx])

    def arg_float(self, idx: int) -> float:
        f: float = struct.unpack("<f", struct.pack("<I", self.args[idx]))[0]
        return f

    def arg_i16(self, word: int, half: int) -> int:
        return i16((self.args[word] >> (16 * half)) & 0xFFFF)

    def arg_u16(self, word: int, half: int) -> int:
        return (self.args[word] >> (16 * half)) & 0xFFFF

    def arg_bytes(self, word: int) -> bytes:
        return struct.pack("<I", self.args[word])

    def raw_arg_bytes(self) -> bytes:
        return b"".join(struct.pack("<I", w) for w in self.args)

    @property
    def is_terminator(self) -> bool:
        return self.id == _TERMINATOR_ID


class EclTimelineInstr(msgspec.Struct, frozen=True):
    """时间轴指令(32 字节, 参数固定 6 个 u32, 前 3 个常作 Float3 坐标)。"""

    offset: int
    time: int     # i16, <0 表示时间轴结束
    arg0: int     # i16, 通常是 sub id
    opcode: int   # i16: 0..7 spawn, 8 msg, 9 msgWait, 10 boss中断, 11 火力, 12 等boss
    size: int
    args: tuple[int, ...]

    def arg_int(self, idx: int) -> int:
        return i32(self.args[idx])

    def arg_float(self, idx: int) -> float:
        f: float = struct.unpack("<f", struct.pack("<I", self.args[idx]))[0]
        return f


class EclFile(msgspec.Struct):
    """解析后的 .ecl: sub 指令流 + 时间轴。"""

    sub_count: int
    timeline_count: int
    subs: list[tuple[EclInstr, ...]]
    timelines: list[tuple[EclTimelineInstr, ...]]
    _instr_at: dict[int, EclInstr]

    def __repr__(self) -> str:
        # _instr_at 是全指令索引, 不进 repr (对照原 dataclass 的 field(repr=False))
        return (f"EclFile(sub_count={self.sub_count!r}, "
                f"timeline_count={self.timeline_count!r}, subs={self.subs!r}, "
                f"timelines={self.timelines!r})")

    @classmethod
    def parse(cls, data: bytes) -> "EclFile":
        if len(data) < 68:
            raise EclParseError("文件太小, 没有完整 EclRawHeader")
        sub_count, timeline_count = struct.unpack_from("<hh", data, 0)
        if not (0 <= sub_count <= 4096 and 0 <= timeline_count <= 16):
            raise EclParseError(f"非法 header: subCount={sub_count} timelineCount={timeline_count}")
        timeline_offsets = struct.unpack_from("<16i", data, 4)
        sub_offsets = struct.unpack_from(f"<{sub_count}i", data, 68)

        subs: list[tuple[EclInstr, ...]] = []
        instr_at: dict[int, EclInstr] = {}
        for sub_id, off in enumerate(sub_offsets):
            instrs: list[EclInstr] = []
            while True:
                if off + _INSTR_HEADER_SIZE > len(data):
                    raise EclParseError(f"sub {sub_id}: 指令越界 (off={off})")
                time, op_id, size, _unused, skip, mask = _INSTR_HEADER.unpack_from(data, off)
                if size < _INSTR_HEADER_SIZE or (size - _INSTR_HEADER_SIZE) % 4 != 0:
                    raise EclParseError(f"sub {sub_id}: 非法 size={size} (off={off})")
                if off + size > len(data):
                    raise EclParseError(f"sub {sub_id}: 指令截断 (off={off})")
                n_args = (size - _INSTR_HEADER_SIZE) // 4
                args = struct.unpack_from(f"<{n_args}I", data, off + _INSTR_HEADER_SIZE)
                instr = EclInstr(off, time, op_id, size, skip, mask, args)
                instrs.append(instr)
                instr_at[off] = instr
                off += size
                if instr.is_terminator:
                    break
            subs.append(tuple(instrs))

        timelines: list[tuple[EclTimelineInstr, ...]] = []
        for i in range(timeline_count):
            off = timeline_offsets[i]
            tl: list[EclTimelineInstr] = []
            while off < len(data):  # 时间轴可以没有终止符, 直接延伸到 EOF
                if off + _TIMELINE_HEADER.size > len(data):
                    # 尾部可能有 4 字节截短终止记录(如 ff ff 04 00, time=-1)
                    tail = struct.unpack_from("<h", data, off)[0] \
                        if off + 2 <= len(data) else -1
                    if tail < 0:
                        break
                    raise EclParseError(f"timeline {i}: 越界 (off={off})")
                time, arg0, opcode, size = _TIMELINE_HEADER.unpack_from(data, off)
                if size < 8 or off + size > len(data):
                    raise EclParseError(f"timeline {i}: 非法 size={size} (off={off})")
                n_args = (size - 8) // 4
                args = struct.unpack_from(f"<{n_args}I", data, off + 8)
                tl.append(EclTimelineInstr(off, time, arg0, opcode, size, args))
                off += size
                if time < 0:
                    break
            timelines.append(tuple(tl))

        return cls(sub_count, timeline_count, subs, timelines, instr_at)

    def instr_at(self, offset: int) -> Optional[EclInstr]:
        return self._instr_at.get(offset)

    def sub_offset(self, sub_id: int) -> int:
        return self.subs[sub_id][0].offset

    def opcode_histogram(self) -> dict[int, int]:
        """所有 sub 的指令 id 分布(终止符除外)。"""
        hist: dict[int, int] = {}
        for sub in self.subs:
            for instr in sub:
                if not instr.is_terminator:
                    hist[instr.id] = hist.get(instr.id, 0) + 1
        return dict(sorted(hist.items()))


# ---- 解释器状态 ----

class Vec3(msgspec.Struct):
    """可变三维向量(C Float3)。屏幕系 y 向下。"""

    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    def set(self, x: float, y: float, z: float) -> None:
        self.x, self.y, self.z = x, y, z

    def copy(self) -> "Vec3":
        return Vec3(self.x, self.y, self.z)

    def to_vec2(self) -> "tuple[float, float]":
        return (self.x, self.y)

    def __add__(self, o: "Vec3") -> "Vec3":
        return Vec3(self.x + o.x, self.y + o.y, self.z + o.z)

    def __sub__(self, o: "Vec3") -> "Vec3":
        return Vec3(self.x - o.x, self.y - o.y, self.z - o.z)

    def __mul__(self, s: float) -> "Vec3":
        return Vec3(self.x * s, self.y * s, self.z * s)

    __rmul__ = __mul__

    @property
    def length(self) -> float:
        return math.sqrt(self.x * self.x + self.y * self.y + self.z * self.z)


class EclContextArgs(msgspec.Struct):
    """随 sub 调用传递/保存的变量区(C EclContextArgs)。

    int3/float3 变量读的是这里的 global_* 快照(SUB_CALL 时从活动全局拷贝),
    与 EclWorld 里的活动全局变量区分。
    """

    int_vars1: list[int] = msgspec.field(default_factory=lambda: [0] * 4)
    float_vars1: list[float] = msgspec.field(default_factory=lambda: [0.0] * 8)
    int_vars2: list[int] = msgspec.field(default_factory=lambda: [0] * 4)
    float_vars2: list[float] = msgspec.field(default_factory=lambda: [0.0] * 2)
    global_ints: list[int] = msgspec.field(default_factory=lambda: [0] * 4)
    global_floats: list[float] = msgspec.field(default_factory=lambda: [0.0] * 4)

    def clone(self) -> "EclContextArgs":
        return EclContextArgs(list(self.int_vars1), list(self.float_vars1),
                              list(self.int_vars2), list(self.float_vars2),
                              list(self.global_ints), list(self.global_floats))


class BulletCommandData(msgspec.Struct):
    """C BulletCommand(§C.2 子弹命令, 由 INIT_BULLET_CMD 填充)。"""

    type: int = 0
    flag: int = 0
    duration: int = 0
    loop_count: int = 0
    speed: float = 0.0
    angle: float = 0.0


class EnemyBulletShooter(msgspec.Struct):
    """C EnemyBulletShooter: ECL 填好后交给宿主(host.spawn_bullet_pattern)展开。"""

    sprite: int = 0
    sprite_offset: int = 0
    pos: Vec3 = msgspec.field(default_factory=Vec3)
    angle1: float = 0.0
    angle2: float = 0.0
    speed1: float = 0.0
    speed2: float = 0.0
    commands: list[BulletCommandData] = msgspec.field(
        default_factory=lambda: [BulletCommandData() for _ in range(6)])
    count1: int = 0
    count2: int = 0
    aim_mode: int = 0       # = opcode - 64, 对应 bullets.Aim
    flags: int = 0
    sound_idx: int = 0
    sound_override: int = -1


class EnemyLaserShooter(msgspec.Struct):
    """C EnemyLaserShooter: ECL 填好后交给宿主(host.spawn_laser_pattern)。"""

    sprite: int = 0
    sprite_offset: int = 0
    pos: Vec3 = msgspec.field(default_factory=Vec3)
    angle1: float = 0.0
    angle2: float = 0.0
    speed1: float = 0.0
    speed2: float = 0.0
    commands: list[BulletCommandData] = msgspec.field(
        default_factory=lambda: [BulletCommandData() for _ in range(5)])
    start_offset: float = 0.0
    end_offset: float = 0.0
    start_length: float = 0.0
    width: float = 0.0
    start_time: int = 0
    duration: int = 0
    end_time: int = 0
    hitbox_start_time: int = 0
    hitbox_end_time: int = 0
    type: int = 0           # 0 = 跟随敌人, 1 = 固定(注意 C 里 MOVING→0, FIXED→1)
    flags: int = 0
    sound_override: int = -1


class EclInterpState(msgspec.Struct):
    """C EclInterp: 跨帧变量插值(INIT_INTERP 注册, 每帧推进)。"""

    active: bool = False
    timer: int = 0
    duration: int = 0       # args[0]
    func_idx: int = 0       # args[1]: 0..6=lerp, 7=cubic hermite
    easing: int = 0         # args[2]: 0 线性, 1..3 ease-in, 4..6 ease-out
    params: list[float] = msgspec.field(default_factory=lambda: [0.0] * 4)  # p0,p1,m0,m1
    target_var: int = 0     # args[7]: 目标变量 id

    def clear(self) -> None:
        self.active = False
        self.timer = 0

    def clone(self) -> "EclInterpState":
        return EclInterpState(self.active, self.timer, self.duration,
                              self.func_idx, self.easing, list(self.params),
                              self.target_var)


class EclContext(msgspec.Struct):
    """C EnemyEclContext: 一层 sub 调用的执行现场。"""

    instr_offset: int = 0
    time: int = 0
    wait_timer: int = 0
    ex_instr_idx: int = -1             # SET_EX_INS 注册的每帧回调(-1=无)
    ex_instr: Optional[EclInstr] = None
    args: EclContextArgs = msgspec.field(default_factory=EclContextArgs)
    interps: list[EclInterpState] = msgspec.field(
        default_factory=lambda: [EclInterpState() for _ in range(8)])
    laser_not_in_use: int = 0
    is_periodic_sub: int = 0
    sub_id: int = -1

    def clone(self) -> "EclContext":
        return EclContext(self.instr_offset, self.time, self.wait_timer,
                          self.ex_instr_idx, self.ex_instr, self.args.clone(),
                          [i.clone() for i in self.interps],
                          self.laser_not_in_use, self.is_periodic_sub, self.sub_id)


class EclEnemyState(msgspec.Struct):
    """解释器读写的敌机状态(C Enemy 中被 ECL 触碰的字段)。

    enemies.py 整合时可让 Enemy 适配/组合本结构。
    """

    pos: Vec3 = msgspec.field(default_factory=Vec3)
    axis_speed: Vec3 = msgspec.field(default_factory=Vec3)
    prev_pos: Vec3 = msgspec.field(default_factory=Vec3)
    delta_pos: Vec3 = msgspec.field(default_factory=Vec3)
    hitbox_size: Vec3 = msgspec.field(default_factory=Vec3)
    graze_size: Vec3 = msgspec.field(default_factory=Vec3)
    shoot_offset: Vec3 = msgspec.field(default_factory=Vec3)
    move_interp: Vec3 = msgspec.field(default_factory=Vec3)
    move_interp_start_pos: Vec3 = msgspec.field(default_factory=Vec3)
    lower_move_limit: Vec3 = msgspec.field(default_factory=Vec3)
    upper_move_limit: Vec3 = msgspec.field(default_factory=Vec3)
    angle: float = 0.0
    angular_velocity: float = 0.0
    move_angle: float = 0.0
    move_angular_velocity: float = 0.0
    move_speed: float = 0.0
    move_acceleration: float = 0.0
    move_radius: float = 0.0
    move_radial_velocity: float = 0.0
    move_interp_timer: int = 0
    move_interp_start_time: int = 0
    life: int = 0
    max_life: int = 0
    score: int = 0
    timer: int = 0                     # C enemy->timer(每帧 +1)
    item_drop: int = 0
    last_damage: int = 0
    boss_id: int = -1
    # C 敌人模板默认值 (EnemyManager.hpp:350-351): soundIdx=SOUND_BOMB_MARISA_A_FOCUS(7),
    # soundOverride=SOUND_25; 发射音门槛 flags&0x200 (BulletManager.cpp:611)
    bullet_props: EnemyBulletShooter = msgspec.field(
        default_factory=lambda: EnemyBulletShooter(sound_idx=7, sound_override=25))
    laser_props: EnemyLaserShooter = msgspec.field(default_factory=EnemyLaserShooter)
    lasers: list = msgspec.field(default_factory=lambda: [None] * 32)  # 宿主返回的激光句柄
    laser_idx: int = 0
    shoot_interval: int = 0
    shoot_interval_timer: int = 0
    # 回调
    life_callback_threshold: list[int] = msgspec.field(default_factory=lambda: [-1] * 4)
    life_callback_sub: list[int] = msgspec.field(default_factory=lambda: [-1] * 4)
    timer_callback_threshold: int = -1
    timer_callback_sub: int = -1
    death_callback_sub: int = -1
    periodic_timer: int = 0
    periodic_callback_sub: int = -1
    periodic_counter: int = 0
    saved_context_args: EclContextArgs = msgspec.field(default_factory=EclContextArgs)
    # 中断
    interrupts: list[int] = msgspec.field(default_factory=lambda: [0] * 32)
    run_interrupt: int = -1
    # 标志位(C 的 flags1..4 位域, 这里用普通 int/bool)
    move_mode: int = 0
    interp_easing: int = 0
    disable_bullets: int = 0
    mirror: int = 0
    active: int = 1
    can_die: int = 0
    has_contact_hitbox: int = 0
    can_be_damaged: int = 0
    has_no_collision: int = 0
    is_hittable: int = 0
    is_projectile: int = 0
    is_boss: int = 0
    has_movement_bounds: int = 0
    death_type: int = 0
    primary_vm_auto_rotate: int = 0
    no_stack_ret: int = 0
    is_survival_spellcard: int = 0
    disable_oob_despawn: int = 0
    disable_movement: int = 0
    custom_special_effect_pos: int = 0
    freeze_ecl_during_bombs: int = 0
    invincibility_timer: int = 0
    # rank 插值参数
    bullet_rank_speed_low: float = 0.0
    bullet_rank_speed_high: float = 0.0
    bullet_rank_amount1_low: int = 0
    bullet_rank_amount1_high: int = 0
    bullet_rank_amount2_low: int = 0
    bullet_rank_amount2_high: int = 0
    # anm 只存 id(渲染侧不接)
    anm_idx: int = -1
    sub_anm_idx: list[int] = msgspec.field(default_factory=lambda: [-1, -1])
    move_anm: tuple[int, ...] = ()
    death_anm: tuple[int, int, int] = (0, 0, 0)
    primary_vm_interrupt: int = 0
    vm_interrupts: list[int] = msgspec.field(default_factory=lambda: [0] * 2)
    primary_vm_rot_z: float = 0.0
    trail: tuple[int, int, int, int, int] = (0, 0, 0, 0, 0)
    # C enemyHistory[96]: trail 启用时每帧右移的历史位置 (x=-999 为哨兵,
    # EnemyManager.hpp:299-302); 延迟到首次使用时按 trailCount 分配
    trail_history: list[Vec3] = msgspec.field(default_factory=list)
    # C invisibleOnBomb/spellcardDelayTimer: 7/8 面 boss 符卡(idx>=118)且
    # 炸弹中 → 无碰撞不受击, 炸弹结束后延迟 1 帧解除 (EclManager.cpp:2261-2276)
    invisible_on_bomb: int = 0
    spellcard_delay_timer: int = 0

    def clamp_pos(self) -> None:
        """Enemy::ClampPos。"""
        if self.has_movement_bounds:
            p = self.pos
            if p.x < self.lower_move_limit.x:
                p.x = self.lower_move_limit.x
            elif p.x > self.upper_move_limit.x:
                p.x = self.upper_move_limit.x
            if p.y < self.lower_move_limit.y:
                p.y = self.lower_move_limit.y
            elif p.y > self.upper_move_limit.y:
                p.y = self.upper_move_limit.y

    def bullet_rank_amount1(self, rank: int) -> int:
        return cdiv(rank * (self.bullet_rank_amount1_high - self.bullet_rank_amount1_low), 32) \
            + self.bullet_rank_amount1_low

    def bullet_rank_amount2(self, rank: int) -> int:
        return cdiv(rank * (self.bullet_rank_amount2_high - self.bullet_rank_amount2_low), 32) \
            + self.bullet_rank_amount2_low

    def bullet_rank_speed(self, rank: float) -> float:
        return rank * (self.bullet_rank_speed_high - self.bullet_rank_speed_low) / 32 \
            + self.bullet_rank_speed_low

    def shoot_interval_rank_delta(self, rank: int) -> int:
        """Enemy::ShootInterval: low=interval/5, high=-interval/5 的 rank 插值。"""
        low = cdiv(self.shoot_interval, 5)
        return cdiv(rank * (-low - low), 32) + low


# ---- 世界与宿主接口 ----

class EclWorld(msgspec.Struct):
    """ECL 可见的全局状态(g_GameManager/g_GlobalEclVars/g_Player 的切片)。"""

    rng: Rng = msgspec.field(default_factory=Rng)
    difficulty: int = 1            # 0=E 1=N 2=H 3=L
    rank: int = 16                 # 0..32
    player_pos: Vec3 = msgspec.field(default_factory=lambda: Vec3(192.0, 400.0, 0.0))
    player_shottype: int = 0
    current_power: int = 0
    current_stage: int = 1
    global_ints: list[int] = msgspec.field(default_factory=lambda: [0] * 4)
    global_floats: list[float] = msgspec.field(default_factory=lambda: [0.0] * 4)
    bosses: list[Optional[EclEnemyState]] = msgspec.field(
        default_factory=lambda: [None] * 8)
    spellcard_active: bool = False  # g_EnemyManager.spellcardInfo.isActive
    framerate_multiplier: float = 1.0  # g_Supervisor.effectiveFramerateMultiplier
    script_wait_time: int = 0
    unused_9545f0: int = 0

    @property
    def difficulty_mask(self) -> int:
        return 1 << self.difficulty

    def angle_to_player(self, pos: Vec3) -> float:
        """Player::AngleToPlayer。"""
        x = self.player_pos.x - pos.x
        y = self.player_pos.y - pos.y
        if x == 0.0 and y == 0.0:
            return 1.5707964
        return math.atan2(y, x)


class EclHost:
    """世界交互接口。默认全部无操作; enemies.py 整合时重写。

    所有方法都已给出安全默认实现, 解释器不会因宿主缺方法而炸。
    """

    # 体术豁免 (EclManager.cpp:2261-2276) 读取的宿主上下文; GameEclHost 每帧同步
    bomb_in_use: bool = False
    spellcard_idx: int = -1     # g_EnemyManager.spellcardInfo.spellcardIdx

    # ---- 弹幕/激光(结构化输出) ----
    def spawn_bullet_pattern(self, props: EnemyBulletShooter) -> None:
        pass

    def spawn_laser_pattern(self, props: EnemyLaserShooter):
        return None  # 返回激光句柄(存入 enemy.lasers[idx])

    def laser_set_angle(self, handle, angle: float) -> None:
        pass

    def laser_add_angle(self, handle, delta: float) -> None:
        pass

    def laser_aim_at_player(self, handle, offset: float) -> None:
        pass

    def laser_set_pos(self, handle, pos: Vec3) -> None:
        pass

    def laser_set_hide_warning(self, handle, v: int) -> None:
        pass

    def laser_in_use(self, handle) -> bool:
        return False

    def laser_stop(self, handle) -> None:
        pass

    def laser_set_start_length(self, handle, v: float) -> None:
        pass

    def laser_set_offsets(self, handle, start: float, end: float) -> None:
        pass

    # ---- 敌人/道具/清场 ----
    def spawn_enemy(self, sub_id: int, pos: Vec3, life: int, item_drop: int,
                    score: int, mirror: int, context_args: EclContextArgs
                    ) -> EclEnemy | None:
        """SpawnEnemy: 返回入场敌人(失败 None); 默认宿主无操作。"""
        return None

    def spawn_item(self, pos: Vec3, item_type: int) -> None:
        pass

    def remove_all_bullets(self, spawn_items: bool) -> None:
        pass

    def remove_bullets_in_radius(self, pos: Vec3, radius: float) -> None:
        pass

    def remove_all_enemies(self, score_max: int, score_min: int) -> int | None:
        """清敌并返回清弹得分(GameEclHost 实现返回 int; 默认宿主无操作)。"""
        pass

    # ---- Boss/符卡 ----
    def set_boss(self, idx: int, enemy: Optional[EclEnemyState]) -> None:
        pass

    def set_boss_health(self, idx: int, cur: int, max_life: int, color: int) -> None:
        pass

    def set_boss_life_markers(self, n: int) -> None:
        pass

    def begin_spellcard(self, enemy: EclEnemyState, gui_id: int,
                        spellcard_idx: int, name: str) -> None:
        pass

    def end_spellcard(self, enemy: EclEnemyState) -> None:
        pass

    # ---- 系统/表现(默认忽略) ----
    def play_sound(self, idx: int) -> None:
        pass

    def msg_read(self, msg_id: int) -> None:
        pass

    def msg_wait(self) -> bool:
        """True = 消息仍在显示(时间轴暂停)。"""
        return False

    def set_power(self, value: int) -> None:
        pass

    def add_cherry_plus(self, value: int) -> None:
        pass

    def set_script_wait_time(self, value: int) -> None:
        pass

    def boss_active(self, idx: int) -> bool:
        return False

    def run_ex_instr(self, idx: int, enemy: EclEnemyState,
                     instr: Optional[EclInstr],
                     ctx: Optional[EclContext] = None) -> bool:
        """ECL ex 指令(24 条 boss 专用特技)。返回 True = 宿主已处理。

        ctx 是触发时的当前上下文 (C 里 ExIns 直接读写 enemy->currentContext:
        eclContextArgs / subId); 仅 ExIns 4/16/17/18 需要。
        """
        return False

    def on_unhandled_opcode(self, machine: "EclMachine", instr: EclInstr) -> None:
        """未实现指令的钩子(默认记日志跳过)。"""
        log.warning("未实现的 ECL 指令 id={} offset={:#x} sub={}, 已跳过",
                    instr.id, instr.offset, machine.current.sub_id)


# ---- 插值 easing(AnmManager 的 ANM_EASE_*) ----

def _ease(t: float, mode: int) -> float:
    if mode == 1:
        return t * t
    if mode == 2:
        return t * t * t
    if mode == 3:
        return t * t * t * t
    if mode == 4:
        t = 1.0 - t
        return 1.0 - t * t
    if mode == 5:
        t = 1.0 - t
        return 1.0 - t * t * t
    if mode == 6:
        t = 1.0 - t
        return 1.0 - t * t * t * t
    return t


# ---- 解释器 ----

_MAX_STACK = 15  # C: stackDepth < 15 才压栈(savedContextStack[16])


@register_ecl("th07", file_format=EclFile)
class EclMachine:
    """单个敌人的 ECL 虚拟机: 当前上下文 + 调用栈。

    每帧调用一次 step()(= EclManager::RunEcl + Enemy 的移动/计时收尾)。
    step() 返回 False 表示脚本结束(ECL_UNIMP / 指令流跑飞), 宿主应 despawn。
    """

    def __init__(self, ecl_file: EclFile, enemy: Optional[EclEnemyState] = None,
                 world: Optional[EclWorld] = None, host: Optional[EclHost] = None,
                 *, strict: bool = False) -> None:
        self.file = ecl_file
        self.enemy = enemy if enemy is not None else EclEnemyState()
        self.world = world if world is not None else EclWorld()
        self.host = host if host is not None else EclHost()
        self.strict = strict
        self.current = EclContext()
        self.stack: list[EclContext] = []
        self.finished = False
        # 调试: 置为一个 list 后, 每执行一条指令 append 其 id(测试/回放用)
        self.trace: Optional[list[int]] = None
        # 只警告一次的类别, 避免刷日志
        self._warned: set = set()

    # ---- CallEclSub ----
    def call_sub(self, sub_id: int) -> None:
        ctx = self.current
        ctx.instr_offset = self.file.sub_offset(sub_id)
        ctx.time = 0
        ctx.wait_timer = 0
        ctx.sub_id = sub_id

    def start(self, sub_id: int) -> None:
        """SpawnEnemyEx 的入口: 调 sub 并立刻跑第一帧。"""
        self.call_sub(sub_id)

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
        if v in (EclVarId.POS_X, EclVarId.POS_Y, EclVarId.POS_Z,
                 EclVarId.PLAYER_POS_X, EclVarId.PLAYER_POS_Y, EclVarId.PLAYER_POS_Z):
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

    # ---- 参数解码(GET_INT_VALUE/GET_FLOAT_VALUE; bitIdx 可与 argIdx 不同) ----

    def _int_arg(self, instr: EclInstr, arg_idx: int, bit_idx: Optional[int] = None) -> int:
        bit = arg_idx if bit_idx is None else bit_idx
        if instr.param_mask & (1 << bit):
            return self._get_int(instr.arg_int(arg_idx))
        return instr.arg_int(arg_idx)

    def _float_arg(self, instr: EclInstr, arg_idx: int, bit_idx: Optional[int] = None) -> float:
        bit = arg_idx if bit_idx is None else bit_idx
        if instr.param_mask & (1 << bit):
            # C: GetFloatVarValue(enemy, args[i].f) —— 先按 f32 位型解读,
            # (i32) 转换后命中变量表; 未命中时原样返回该 f32
            f = instr.arg_float(arg_idx)
            return self._get_float_value(int(f), f)
        return instr.arg_float(arg_idx)

    def _int_target(self, instr: EclInstr, arg_idx: int) -> Optional[int]:
        """可写 int 变量 id(mask 置位时); 否则 None(C 写进指令内存, 丢弃)。"""
        if instr.param_mask & (1 << arg_idx):
            return instr.arg_int(arg_idx)
        return None

    def _float_target(self, instr: EclInstr, arg_idx: int) -> Optional[int]:
        if instr.param_mask & (1 << arg_idx):
            # 编译器把 float 变量 id 存成 f32 值(如 10004.0f), 先还原成 int
            return int(instr.arg_float(arg_idx))
        return None

    def _store_int(self, instr: EclInstr, arg_idx: int, value: int) -> None:
        t = self._int_target(instr, arg_idx)
        if t is not None:
            self._set_int(t, value)

    def _store_float(self, instr: EclInstr, arg_idx: int, value: float) -> None:
        t = self._float_target(instr, arg_idx)
        if t is not None:
            self._set_float(t, value)

    # ---- 主循环(EclManager::RunEcl) ----

    def step(self) -> bool:
        """推进一帧。False = 脚本结束(宿主应 despawn 该敌人)。"""
        if self.finished:
            return False
        ok = self._run_ecl()
        if not ok:
            self.finished = True
            return False
        # OnUpdate 收尾: ClampPos → Move → ClampPos, timer++/无敌时间--
        e = self.enemy
        if not e.disable_movement:
            e.clamp_pos()
            self._move()
            e.clamp_pos()
        e.timer = i32(e.timer + 1)
        if e.invincibility_timer > 0:
            e.invincibility_timer -= 1
        return True

    def _move(self) -> None:
        """Enemy::Move(含 mirror)。"""
        e = self.enemy
        mult = self.world.framerate_multiplier
        e.delta_pos = e.pos - e.prev_pos
        e.prev_pos = e.pos.copy()
        if not e.mirror:
            e.pos.x = f32(e.pos.x + mult * e.axis_speed.x)
        else:
            e.pos.x = f32(e.pos.x - mult * e.axis_speed.x)
        e.pos.y = f32(e.pos.y + mult * e.axis_speed.y)
        e.pos.z = f32(e.pos.z + mult * e.axis_speed.z)

    def _instr(self, offset: int) -> Optional[EclInstr]:
        return self.file.instr_at(offset)

    def _push_context(self) -> None:
        self.stack.append(self.current.clone())
        if len(self.stack) > _MAX_STACK:
            self.stack.pop(0)  # C: stackDepth 封顶 15, 超深不再压栈

    def _do_interrupt_call(self, instr: EclInstr, sub_id: int) -> EclInstr:
        """SET_RUN_INTERRUPT/handle_interrupt 的公共尾巴: 压栈 + 进中断 sub。"""
        e = self.enemy
        self.current.instr_offset = instr.offset + instr.size
        if not e.no_stack_ret:
            self._push_context()
        self.call_sub(sub_id)
        e.run_interrupt = -1
        return self._instr(self.current.instr_offset)  # type: ignore[return-value]

    def _run_ecl(self) -> bool:
        e, w = self.enemy, self.world

        while True:  # restart:
            ctx = self.current
            instr = self._instr(ctx.instr_offset)
            if instr is None:
                log.error("ECL 指令流跑飞: offset={:#x}", ctx.instr_offset)
                return False
            if e.run_interrupt >= 0:
                instr = self._do_interrupt_call(instr, e.interrupts[e.run_interrupt])
                if instr is None:
                    return False
            if e.periodic_callback_sub >= 0:
                e.periodic_counter += 1
                if e.periodic_counter >= e.periodic_timer:
                    e.periodic_counter = 0
                    self._push_context()
                    ctx.args = e.saved_context_args.clone()
                    self.call_sub(e.periodic_callback_sub)
                    instr = self._instr(ctx.instr_offset)
                    if instr is None:
                        return False
                    ctx.is_periodic_sub = 1

            goto_exit = False
            while True:
                if ctx.wait_timer > 0:
                    ctx.wait_timer -= 1
                    ctx.time = i32(ctx.time - 1)
                    goto_exit = True
                    break
                if ctx.time == instr.time:
                    if (instr.skip_difficulty & w.difficulty_mask) == 0:
                        instr = self._advance(instr)
                        if instr is None:
                            return False
                        continue
                    r = self._execute(instr)
                    if self.trace is not None:
                        self.trace.append(instr.id)
                    if r == "error":
                        return False
                    if r == "restart":
                        break  # 回外层 restart 循环
                    if isinstance(r, EclInstr):  # 跳转目标
                        instr = r
                        continue
                    instr = self._advance(instr)
                    if instr is None:
                        return False
                    continue
                goto_exit = True  # time != instr.time → 本帧没活干
                break

            if goto_exit:
                self._frame_update(instr)
                return True

    def _advance(self, instr: EclInstr) -> Optional[EclInstr]:
        if instr.is_terminator:
            log.error("ECL 执行越过 sub 终止符 (offset={:#x})", instr.offset)
            return None
        nxt = self._instr(instr.offset + instr.size)
        if nxt is None:
            log.error("ECL 指令流跑飞: offset={:#x}", instr.offset + instr.size)
        return nxt

    def _frame_update(self, instr: EclInstr) -> None:
        """RunEcl 的 exit 路径: 移动模式/自动射击/ex 指令/插值。"""
        e, w, ctx = self.enemy, self.world, self.current
        mult = w.framerate_multiplier

        if e.move_mode == 3:
            e.move_angle = add_normalize_angle(e.move_angle, mult * e.move_angular_velocity)
            e.move_radius = f32(mult * e.move_radial_velocity + e.move_radius)
            mx = math.cos(e.move_angle) * e.move_radius
            my = math.sin(e.move_angle) * e.move_radius
            e.axis_speed.x = f32(mx + e.move_interp_start_pos.x - e.pos.x)
            e.axis_speed.y = f32(my + e.move_interp_start_pos.y - e.pos.y)
            e.angle = f32(math.atan2(e.axis_speed.y, e.axis_speed.x))
            if e.move_interp_start_time > 0:
                e.move_interp_timer -= 1
                if e.move_interp_timer <= 0:
                    e.move_mode = 0
        elif e.move_mode == 1:
            e.angle = add_normalize_angle(e.angle, mult * e.angular_velocity)
            e.move_speed = f32(mult * e.move_acceleration + e.move_speed)
            e.axis_speed.x = f32(math.cos(e.angle) * e.move_speed)
            e.axis_speed.y = f32(math.sin(e.angle) * e.move_speed)
            e.axis_speed.z = 0.0
            if e.move_interp_start_time > 0:
                e.move_interp_timer -= 1
                if e.move_interp_timer <= 0:
                    e.move_mode = 0
        elif e.move_mode == 2:
            e.move_interp_timer -= 1
            t = 1.0 - e.move_interp_timer / e.move_interp_start_time
            if t < 0.0:
                t = 0.0
            t = _ease(t, e.interp_easing)
            e.axis_speed.x = f32(t * e.move_interp.x + e.move_interp_start_pos.x - e.pos.x)
            e.axis_speed.y = f32(t * e.move_interp.y + e.move_interp_start_pos.y - e.pos.y)
            e.axis_speed.z = f32(t * e.move_interp.z + e.move_interp_start_pos.z - e.pos.z)
            if e.mirror:
                e.axis_speed.x = -e.axis_speed.x
            e.angle = f32(math.atan2(e.axis_speed.y, e.axis_speed.x))
            if e.move_interp_timer <= 0:
                e.move_mode = 0
                e.pos = e.move_interp_start_pos + e.move_interp
                e.axis_speed = Vec3()

        if e.life > 0:
            if e.shoot_interval > 0:
                e.shoot_interval_timer += 1
                if e.shoot_interval_timer >= e.shoot_interval:
                    e.bullet_props.pos = e.pos + e.shoot_offset
                    self.host.spawn_bullet_pattern(e.bullet_props)
                    e.shoot_interval_timer = 0
            if ctx.ex_instr_idx >= 0:
                self._run_ex(ctx.ex_instr_idx, ctx.ex_instr)
            self._step_interps()

        ctx.instr_offset = instr.offset
        ctx.time = i32(ctx.time + 1)

    def _step_interps(self) -> None:
        e = self.enemy
        pos_modified = False
        old_pos = e.pos.copy()
        for it in self.current.interps:
            if not it.active:
                continue
            it.timer += 1
            if it.timer >= it.duration:
                it.timer = it.duration
            t = _ease(it.timer / it.duration if it.duration else 1.0, it.easing)
            p = it.params
            if it.func_idx == 7:  # MathCubicInterp(hermite)
                h00 = (t - 1.0) * (t - 1.0) * (2.0 * t + 1.0)
                h01 = t * t * (3.0 - 2.0 * t)
                h10 = (1.0 - t) * (1.0 - t) * t
                h11 = (t - 1.0) * t * t
                value = h00 * p[0] + h01 * p[1] + h10 * p[2] + h11 * p[3]
            else:  # MathLerp(g_EclInterpFuncs[0..6])
                value = (p[1] - p[0]) * t + p[0]
            self._set_float(it.target_var, value)
            if it.timer >= it.duration:
                it.clear()
            if it.target_var in (EclVarId.POS_X, EclVarId.POS_Y, EclVarId.POS_Z):
                pos_modified = True
        if pos_modified:
            e.axis_speed.x = f32(e.pos.x - old_pos.x)
            e.axis_speed.y = f32(e.pos.y - old_pos.y)
            e.angle = f32(math.atan2(e.axis_speed.y, e.axis_speed.x))
            e.pos = old_pos

    def _run_ex(self, idx: int, instr: Optional[EclInstr]) -> None:
        if idx == 3:  # ExInsNoOp
            return
        if not self.host.run_ex_instr(idx, self.enemy, instr, ctx=self.current):
            if ("ex", idx) not in self._warned:
                self._warned.add(("ex", idx))
                log.warning("ECL ex 指令 {} 未实现, 按无操作处理", idx)

    # ---- 指令执行: 返回 None=顺序前进 / EclInstr=跳到 / "restart" / "error" ----

    def _execute(self, instr: EclInstr):
        op = instr.id
        e, w, ctx = self.enemy, self.world, self.current
        
        # 人类（我）备注给AI：这里用match-case是不是更优雅？
        if op == EclOpcode.UNIMP:
            return "error"
        elif op == 0:
            pass  # 无操作标记(C 的 switch 没有 case 0, 编译器生成的时间同步点)
        elif op == 141:
            pass  # C 枚举 140→142 跳号, switch 无 case 141, 二进制里等于无操作
        elif op == EclOpcode.SET_WAIT_TIMER:
            ctx.wait_timer = self._int_arg(instr, 0)
        elif op == EclOpcode.DEC_JUMP:
            t = self._int_target(instr, 2)
            if t is not None:
                self._set_int(t, self._get_int(t) - 1)
            if self._int_arg(instr, 2) <= 0:
                return None  # 顺序前进
            return self._do_jump(instr, instr.arg_int(0), instr.arg_int(1))
        elif op == EclOpcode.JUMP:
            return self._do_jump(instr, instr.arg_int(0), instr.arg_int(1))
        elif op == EclOpcode.SET_INT:
            self._store_int(instr, 0, self._int_arg(instr, 1))
        elif op == EclOpcode.SET_FLOAT:
            self._store_float(instr, 0, self._float_arg(instr, 1))
        elif op == EclOpcode.NORMALIZE_ANGLE:
            self._store_float(instr, 0, add_normalize_angle(self._float_arg(instr, 0), 0.0))
        elif op == EclOpcode.RAND:
            self._store_int(instr, 0, w.rng.int_below(self._int_arg(instr, 1)))
        elif op == EclOpcode.RAND_ADD:
            self._store_int(instr, 0, w.rng.int_below(self._int_arg(instr, 1))
                            + self._int_arg(instr, 2))
        elif op == EclOpcode.RAND_FLOAT:
            self._store_float(instr, 0, w.rng.unit() * self._float_arg(instr, 1))
        elif op == EclOpcode.RAND_FLOAT_ADD:
            self._store_float(instr, 0, w.rng.unit() * self._float_arg(instr, 1)
                              + self._float_arg(instr, 2))
        elif op == EclOpcode.RAND_SIGN:
            self._store_int(instr, 0, w.rng.sign() * self._int_arg(instr, 1))
        elif op == EclOpcode.RAND_SIGN_FLOAT:
            self._store_float(instr, 0, float(w.rng.sign()) * self._float_arg(instr, 1))
        elif op == EclOpcode.INC:
            t = self._int_target(instr, 0)
            if t is not None:
                self._set_int(t, self._get_int(t) + 1)
        elif op == EclOpcode.DEC:
            t = self._int_target(instr, 0)
            if t is not None:
                self._set_int(t, self._get_int(t) - 1)
        elif op == EclOpcode.GET_BOSS_INT:
            boss = w.bosses[self._int_arg(instr, 2) & 7]
            if boss is None:
                return None
            value = self._peer_int(boss, instr, 1)
            self._store_int(instr, 0, value)
        elif op == EclOpcode.GET_BOSS_FLOAT:
            boss = w.bosses[self._int_arg(instr, 2) & 7]
            if boss is None:
                return None
            fvalue = self._peer_float(boss, instr, 1)
            self._store_float(instr, 0, fvalue)
        elif op == EclOpcode.ADD:
            self._store_int(instr, 0, self._int_arg(instr, 1) + self._int_arg(instr, 2))
        elif op == EclOpcode.SUB:
            self._store_int(instr, 0, self._int_arg(instr, 1) - self._int_arg(instr, 2))
        elif op == EclOpcode.MUL:
            self._store_int(instr, 0, self._int_arg(instr, 1) * self._int_arg(instr, 2))
        elif op == EclOpcode.DIV:
            b = self._int_arg(instr, 2)
            self._store_int(instr, 0, cdiv(self._int_arg(instr, 1), b) if b else 0)
        elif op == EclOpcode.MOD:
            b = self._int_arg(instr, 2)
            self._store_int(instr, 0, cmod(self._int_arg(instr, 1), b) if b else 0)
        elif op == EclOpcode.ADD_FLOAT:
            self._store_float(instr, 0, self._float_arg(instr, 1) + self._float_arg(instr, 2))
        elif op == EclOpcode.SUB_FLOAT:
            self._store_float(instr, 0, self._float_arg(instr, 1) - self._float_arg(instr, 2))
        elif op == EclOpcode.MUL_FLOAT:
            self._store_float(instr, 0, self._float_arg(instr, 1) * self._float_arg(instr, 2))
        elif op == EclOpcode.DIV_FLOAT:
            bf = self._float_arg(instr, 2)
            self._store_float(instr, 0, self._float_arg(instr, 1) / bf if bf != 0.0 else 0.0)
        elif op == EclOpcode.MOD_FLOAT:
            bf = self._float_arg(instr, 2)
            self._store_float(instr, 0,
                              math.fmod(self._float_arg(instr, 1), bf) if bf != 0.0 else 0.0)
        elif op == EclOpcode.SIN:
            self._store_float(instr, 0, math.sin(self._float_arg(instr, 1)))
        elif op == EclOpcode.COS:
            self._store_float(instr, 0, math.cos(self._float_arg(instr, 1)))
        elif op == EclOpcode.ATAN2:
            self._store_float(instr, 0, math.atan2(
                self._float_arg(instr, 4) - self._float_arg(instr, 2),
                self._float_arg(instr, 3) - self._float_arg(instr, 1)))
        elif op == EclOpcode.LERP:
            delta = self._float_arg(instr, 1) - self._float_arg(instr, 2)
            self._store_float(instr, 0,
                              delta * self._float_arg(instr, 3) + self._float_arg(instr, 2))
        elif op == EclOpcode.INIT_INTERP:
            self._init_interp(instr)
        elif op in (EclOpcode.JUMP_IF_EQ, EclOpcode.JUMP_IF_NEQ, EclOpcode.JUMP_IF_LT,
                    EclOpcode.JUMP_IF_LEQ, EclOpcode.JUMP_IF_GT, EclOpcode.JUMP_IF_GEQ):
            a, b = self._int_arg(instr, 0), self._int_arg(instr, 1)
            if self._compare(op, a, b):
                return self._do_jump(instr, instr.arg_int(2), instr.arg_int(3))
        elif op in (EclOpcode.JUMP_IF_EQ_FLOAT, EclOpcode.JUMP_IF_NEQ_FLOAT,
                    EclOpcode.JUMP_IF_LT_FLOAT, EclOpcode.JUMP_IF_LEQ_FLOAT,
                    EclOpcode.JUMP_IF_GT_FLOAT, EclOpcode.JUMP_IF_GEQ_FLOAT):
            fa, fb = self._float_arg(instr, 0), self._float_arg(instr, 1)
            if self._compare(op, fa, fb):
                return self._do_jump(instr, instr.arg_int(2), instr.arg_int(3))
        elif op == EclOpcode.SUB_CALL:
            self.current.instr_offset = instr.offset + instr.size
            if not e.no_stack_ret:
                self._push_context()
            self.call_sub(instr.arg_int(0))
            # 新 sub 拿到活动全局变量的快照(C: eclContextArgs.globalVars = g_GlobalEclVars)
            ctx.args.global_ints = list(w.global_ints)
            ctx.args.global_floats = list(w.global_floats)
            return "restart"
        elif op == EclOpcode.SUB_RET:
            if e.no_stack_ret:
                log.warning("ECL_SUB_RET with noStackRet")
            if not self.stack:
                log.error("ECL 调用栈下溢")
                return "error"
            if ctx.is_periodic_sub:
                e.saved_context_args = ctx.args.clone()
                ctx.is_periodic_sub = 0
            self.current = self.stack.pop()
            return "restart"
        elif op == EclOpcode.SET_ANM:
            e.anm_idx = self._int_arg(instr, 0)
        elif op == EclOpcode.SET_SUB_ANM:
            idx = self._int_arg(instr, 0)
            if 0 <= idx < len(e.sub_anm_idx):
                e.sub_anm_idx[idx] = self._int_arg(instr, 1)
        elif op == EclOpcode.SET_DEATH_ANM:
            raw = instr.arg_bytes(0)
            e.death_anm = (struct.unpack("<b", raw[0:1])[0], raw[1],
                           struct.unpack("<b", raw[2:3])[0])
        elif op == EclOpcode.SET_POS:
            e.pos.set(self._float_arg(instr, 0), self._float_arg(instr, 1),
                      self._float_arg(instr, 2))
            e.clamp_pos()
        elif op == EclOpcode.SET_AXIS_SPEED:
            e.axis_speed.set(self._float_arg(instr, 0), self._float_arg(instr, 1),
                             self._float_arg(instr, 2))
            e.angle = f32(math.atan2(e.axis_speed.y, e.axis_speed.x))
            e.move_mode = 0
        elif op == EclOpcode.SET_ANGULAR_VEL:
            e.angular_velocity = self._float_arg(instr, 0)
            e.move_mode = 1
        elif op == EclOpcode.MOVE_AT_PLAYER:
            e.angle = add_normalize_angle(
                w.angle_to_player(e.pos), self._float_arg(instr, 0))
            e.move_speed = self._float_arg(instr, 1)
            e.move_mode = 1
        elif op == EclOpcode.SET_MOVE_SPEED:
            e.move_speed = self._float_arg(instr, 0)
            e.move_mode = 1
        elif op == EclOpcode.SET_MOVE_ACCEL:
            e.move_acceleration = self._float_arg(instr, 0)
            e.move_mode = 1
        elif op == EclOpcode.SET_MOVE_INTERP_TIMER_POLAR:
            e.move_mode = 1
            e.move_interp_timer = e.move_interp_start_time = self._int_arg(instr, 0)
        elif op == EclOpcode.SET_MOVE_INTERP_TIMER_RADIAL:
            e.move_mode = 3
            e.move_interp_timer = e.move_interp_start_time = self._int_arg(instr, 0)
        elif op == EclOpcode.SET_MOVE_INTERP_TIMER_INTERP:
            e.move_mode = 2
            e.move_interp_timer = e.move_interp_start_time = self._int_arg(instr, 0)
        elif 64 <= op <= 72:
            self._spawn_bullet_pattern(instr)
        elif op == EclOpcode.INIT_BULLET_CMD:
            cmd = e.bullet_props.commands[self._int_arg(instr, 0)]
            cmd.type = self._int_arg(instr, 1)
            cmd.flag = self._int_arg(instr, 2)
            cmd.duration = self._int_arg(instr, 3)
            cmd.loop_count = self._int_arg(instr, 4)
            cmd.speed = self._float_arg(instr, 5)
            cmd.angle = self._float_arg(instr, 6)
        elif op == EclOpcode.SET_SHOOT_INTERVAL:
            e.shoot_interval = self._int_arg(instr, 0)
            if e.shoot_interval != 0:
                e.shoot_interval = i32(e.shoot_interval +
                                        e.shoot_interval_rank_delta(w.rank))
                e.shoot_interval_timer = 0
        elif op == EclOpcode.SET_SHOOT_INTERVAL_RAND:
            e.shoot_interval = self._int_arg(instr, 0)
            if e.shoot_interval != 0:
                e.shoot_interval = i32(e.shoot_interval +
                                        e.shoot_interval_rank_delta(w.rank))
                e.shoot_interval_timer = w.rng.int_below(e.shoot_interval)
        elif op == EclOpcode.DISABLE_BULLETS:
            e.disable_bullets = 1
        elif op == EclOpcode.ENABLE_BULLETS:
            e.disable_bullets = 0
        elif op == EclOpcode.SPAWN_PREV_BULLET_PATTERN:
            e.bullet_props.pos = e.pos + e.shoot_offset
            self.host.spawn_bullet_pattern(e.bullet_props)
        elif op == EclOpcode.SET_SHOOT_OFFSET:
            e.shoot_offset.set(self._float_arg(instr, 0), self._float_arg(instr, 1),
                               self._float_arg(instr, 2))
        elif op in (EclOpcode.SPAWN_LASER_PATTERN_FIXED,
                    EclOpcode.SPAWN_LASER_PATTERN_MOVING):
            self._spawn_laser_pattern(instr)
        elif op == EclOpcode.SET_LASER_IDX:
            e.laser_idx = self._int_arg(instr, 0)
        elif op == EclOpcode.ADD_LASER_ANGLE:
            h = e.lasers[self._int_arg(instr, 0) & 31]
            if h is not None:
                self.host.laser_add_angle(h, self._float_arg(instr, 1))
        elif op == EclOpcode.SET_LASER_ANGLE:
            h = e.lasers[self._int_arg(instr, 0) & 31]
            if h is not None:
                self.host.laser_set_angle(h, self._float_arg(instr, 1))
        elif op == EclOpcode.AIM_LASER_ANGLE_AT_PLAYER:
            h = e.lasers[self._int_arg(instr, 0) & 31]
            if h is not None:
                self.host.laser_aim_at_player(h, self._float_arg(instr, 1))
        elif op == EclOpcode.SET_LASER_POS_REL:
            h = e.lasers[self._int_arg(instr, 0) & 31]
            if h is not None:
                self.host.laser_set_pos(h, Vec3(
                    self._float_arg(instr, 1) + e.pos.x,
                    self._float_arg(instr, 2) + e.pos.y,
                    self._float_arg(instr, 3) + e.pos.z))
        elif op == EclOpcode.SET_LASER_HIDE_WARNING:
            h = e.lasers[self._int_arg(instr, 0) & 31]
            if h is not None:
                self.host.laser_set_hide_warning(h, self._int_arg(instr, 1))
        elif op == EclOpcode.TEST_LASER_NOT_IN_USE:
            h = e.lasers[self._int_arg(instr, 0) & 31]
            ctx.laser_not_in_use = 0 if (h is not None and self.host.laser_in_use(h)) else 1
        elif op == EclOpcode.STOP_LASER:
            h = e.lasers[self._int_arg(instr, 0) & 31]
            if h is not None:
                self.host.laser_stop(h)
        elif op == EclOpcode.CLEAR_LASERS:
            e.lasers = [None] * 32
        elif op == EclOpcode.SET_LASER_START_LEN:
            h = e.lasers[self._int_arg(instr, 0) & 31]
            if h is not None:
                self.host.laser_set_start_length(h, self._float_arg(instr, 1))
        elif op == EclOpcode.SET_LASER_OFFSETS:
            h = e.lasers[self._int_arg(instr, 0) & 31]
            if h is not None:
                self.host.laser_set_offsets(h, self._float_arg(instr, 1),
                                            self._float_arg(instr, 2))
        elif op == EclOpcode.IDFK:
            w.unused_9545f0 = self._int_arg(instr, 0)
        elif op == EclOpcode.SET_BOSS:
            idx = self._int_arg(instr, 0)
            if idx >= 0:
                w.bosses[idx & 7] = e
                e.is_boss = 1
                e.boss_id = idx
                self.host.set_boss(idx, e)
            else:
                if 0 <= e.boss_id < 8:
                    w.bosses[e.boss_id] = None
                    self.host.set_boss(e.boss_id, None)
                e.is_boss = 0
        elif op == EclOpcode.SPAWN_EFFECT:
            pass  # 特效, 不接
        elif op == EclOpcode.MOVE_DIR_TIME:
            if self._int_arg(instr, 0) <= 0:
                e.angle = add_normalize_angle(self._float_arg(instr, 2), 0.0)
                e.move_speed = self._float_arg(instr, 3)
                e.move_mode = 1
                e.move_interp_timer = e.move_interp_start_time = self._int_arg(instr, 0)
            else:
                ang = add_normalize_angle(self._float_arg(instr, 2), 0.0)
                dist = self._float_arg(instr, 3) * self._int_arg(instr, 0)
                e.move_interp.set(f32(math.cos(ang) * dist),
                                  f32(math.sin(ang) * dist), 0.0)
                e.move_interp_start_pos = e.pos.copy()
                e.move_interp_timer = e.move_interp_start_time = self._int_arg(instr, 0)
                e.interp_easing = self._int_arg(instr, 1) & 0xFF
                e.move_mode = 2
                if e.mirror:
                    e.move_interp.x = -e.move_interp.x
        elif op == EclOpcode.MOVE_POS_TIME:
            new_pos = Vec3(self._float_arg(instr, 2), self._float_arg(instr, 3),
                           self._float_arg(instr, 4))
            e.move_interp = new_pos - e.pos
            e.move_interp_start_pos = e.pos.copy()
            e.move_interp_timer = e.move_interp_start_time = self._int_arg(instr, 0)
            e.interp_easing = self._int_arg(instr, 1) & 0xFF
            e.move_mode = 2
            e.axis_speed = Vec3()
            if e.mirror:
                e.move_interp.x = -e.move_interp.x
        elif op == EclOpcode.MOVE_ORBIT:
            e.move_interp_timer = e.move_interp_start_time = self._int_arg(instr, 0)
            e.move_interp_start_pos.set(self._float_arg(instr, 1),
                                        self._float_arg(instr, 2),
                                        self._float_arg(instr, 3))
            e.move_angle = self._float_arg(instr, 4)
            e.move_angular_velocity = self._float_arg(instr, 5)
            e.move_radius = self._float_arg(instr, 6)
            e.move_radial_velocity = self._float_arg(instr, 7)
            e.move_mode = 3
        elif op == EclOpcode.SET_ORBIT_RADIUS:
            e.move_radius = self._float_arg(instr, 0)
            e.move_radial_velocity = self._float_arg(instr, 1)
        elif op == EclOpcode.SET_ORBIT_ANGLE:
            e.move_angle = self._float_arg(instr, 0)
            e.move_angular_velocity = self._float_arg(instr, 1)
        elif op == EclOpcode.SET_MOVEMENT_BOUNDS:
            e.lower_move_limit.x = self._float_arg(instr, 0)
            e.lower_move_limit.y = self._float_arg(instr, 1)
            e.upper_move_limit.x = self._float_arg(instr, 2)
            e.upper_move_limit.y = self._float_arg(instr, 3)
            e.has_movement_bounds = 1
        elif op == EclOpcode.DISABLE_MOVEMENT_BOUNDS:
            e.has_movement_bounds = 0
        elif op == EclOpcode.RAND_FLOAT_RANGE:
            lo = self._float_arg(instr, 1)
            self._store_float(instr, 0,
                              w.rng.unit() * (self._float_arg(instr, 2) - lo) + lo)
        elif op == EclOpcode.GET_EXIT_ANGLE:
            self._store_float(instr, 0, self._exit_angle(randomize=True))
        elif op == EclOpcode.SET_MOVE_ANM:
            e.move_anm = (instr.arg_i16(0, 0), instr.arg_i16(0, 1),
                          instr.arg_i16(1, 0), instr.arg_i16(1, 1),
                          instr.arg_i16(2, 0))
        elif op == EclOpcode.SET_HITBOX_SIZE:
            e.hitbox_size.set(self._float_arg(instr, 0), self._float_arg(instr, 1),
                              self._float_arg(instr, 2))
        elif op == EclOpcode.SET_GRAZE_SIZE:
            e.graze_size.set(self._float_arg(instr, 0), self._float_arg(instr, 1),
                             self._float_arg(instr, 2))
        elif op == EclOpcode.SET_HAS_CONTACT_HITBOX:
            e.has_contact_hitbox = instr.arg_bytes(0)[0]
        elif op == EclOpcode.SET_CAN_BE_DAMAGED:
            e.can_be_damaged = instr.arg_bytes(0)[0]
        elif op == EclOpcode.SET_IS_HITTABLE:
            e.is_hittable = instr.arg_bytes(0)[0]
        elif op == EclOpcode.PLAY_SOUND:
            self.host.play_sound(self._int_arg(instr, 0))
        elif op == EclOpcode.SET_DEATH_TYPE:
            e.death_type = instr.arg_bytes(0)[0]
        elif op == EclOpcode.SET_DEATH_CALLBACK_SUB:
            e.death_callback_sub = instr.arg_bytes(0)[0]
        elif op == EclOpcode.SET_INTERRUPT:
            e.interrupts[self._int_arg(instr, 1) & 31] = self._int_arg(instr, 0)
        elif op == EclOpcode.SET_RUN_INTERRUPT:
            e.run_interrupt = self._int_arg(instr, 0)
            if self._do_interrupt_call(instr, e.interrupts[e.run_interrupt]) is None:
                return "error"
            return "restart"
        elif op == EclOpcode.SET_LIFE:
            e.life = e.max_life = self._int_arg(instr, 0)
        elif op == EclOpcode.SET_BOSS_HEALTH:
            self.host.set_boss_health(self._int_arg(instr, 0), self._int_arg(instr, 1),
                                      self._int_arg(instr, 2), self._int_arg(instr, 3))
        elif op == EclOpcode.BEGIN_SPELLCARD:
            self._begin_spellcard(instr)
        elif op == EclOpcode.END_SPELLCARD:
            self.host.end_spellcard(e)
        elif op == EclOpcode.SET_TIMER:
            e.timer = self._int_arg(instr, 0)
        elif op == EclOpcode.SET_LIFE_CALLBACK_THRESHOLD:
            e.life_callback_threshold[0] = self._int_arg(instr, 0)
        elif op == EclOpcode.SET_LIFE_CALLBACK_SUB:
            e.life_callback_sub[0] = self._int_arg(instr, 0)
        elif op == EclOpcode.SET_LIFE_CALLBACK:
            idx = self._int_arg(instr, 0) & 3
            e.life_callback_threshold[idx] = self._int_arg(instr, 1)
            e.life_callback_sub[idx] = self._int_arg(instr, 2)
        elif op == EclOpcode.SET_TIMER_CALLBACK_THRESHOLD:
            e.timer_callback_threshold = self._int_arg(instr, 0)
            e.timer = 0
        elif op == EclOpcode.SET_TIMER_CALLBACK_SUB:
            e.timer_callback_sub = self._int_arg(instr, 0)
        elif op == EclOpcode.SET_PERIODIC_CALLBACK:
            e.periodic_timer = self._int_arg(instr, 0)
            e.periodic_callback_sub = self._int_arg(instr, 1)
            e.periodic_counter = 0
            e.saved_context_args = ctx.args.clone()
        elif op == EclOpcode.SET_ENEMY_CAN_DIE:
            e.can_die = instr.arg_bytes(0)[0]
        elif op == EclOpcode.SPAWN_PARTICLES or op == EclOpcode.SPAWN_MOVING_PARTICLES:
            pass  # 粒子特效, 不接
        elif op == EclOpcode.SPAWN_ITEMS:
            self._spawn_items(self._int_arg(instr, 0))
        elif op == EclOpcode.SPAWN_POINT_ITEMS:
            for _ in range(self._int_arg(instr, 0)):
                self.host.spawn_item(self._jitter_pos(), 1)  # ITEM_POINT
        elif op == EclOpcode.SET_VM_AUTO_ROTATE:
            e.primary_vm_auto_rotate = instr.arg_bytes(0)[0]
        elif op == EclOpcode.RUN_EX_INS:
            self._run_ex(self._int_arg(instr, 0), instr)
        elif op == EclOpcode.SET_EX_INS:
            idx = self._int_arg(instr, 0)
            if idx >= 0:
                ctx.ex_instr_idx = idx
                ctx.ex_instr = instr
            else:
                ctx.ex_instr_idx = -1
        elif op == EclOpcode.ADD_TIME:
            ctx.time = i32(ctx.time + self._int_arg(instr, 0))
        elif op == EclOpcode.SPAWN_ITEM:
            self.host.spawn_item(e.pos, self._int_arg(instr, 0))
        elif op == EclOpcode.SET_SCRIPT_WAIT_TIME:
            w.script_wait_time = self._int_arg(instr, 0)
            self.host.set_script_wait_time(w.script_wait_time)
        elif op == EclOpcode.SET_NUM_BOSS_LIFE_MARKERS:
            self.host.set_boss_life_markers(self._int_arg(instr, 0))
        elif op in (EclOpcode.SPAWN_ENEMY_ABS, EclOpcode.SPAWN_ENEMY_REL):
            if e.life > 0:
                pos = Vec3(self._float_arg(instr, 1), self._float_arg(instr, 2),
                           self._float_arg(instr, 3))
                if op == EclOpcode.SPAWN_ENEMY_REL:
                    pos = pos + e.pos
                self.host.spawn_enemy(instr.arg_int(0), pos, self._int_arg(instr, 4),
                                      self._int_arg(instr, 5), self._int_arg(instr, 6),
                                      0, ctx.args.clone())
        elif op == EclOpcode.REMOVE_ALL_ENEMIES:
            self.host.remove_all_enemies(8000, 0)
        elif op == EclOpcode.SET_PRIMARY_VM_INTERRUPT:
            e.primary_vm_interrupt = self._int_arg(instr, 0)
        elif op == EclOpcode.SET_VM_INTERRUPT:
            idx = instr.arg_int(0)
            if 0 <= idx < len(e.vm_interrupts):
                e.vm_interrupts[idx] = instr.arg_i16(1, 0)
        elif op == EclOpcode.REMOVE_ALL_BULLETS_SPAWN_ITEMS:
            self.host.remove_all_bullets(True)
        elif op == EclOpcode.SET_BULLET_SOUND:
            idx = self._int_arg(instr, 0)
            if idx >= 0:
                e.bullet_props.sound_idx = idx
                e.bullet_props.flags |= 0x200
            else:
                e.bullet_props.flags &= 0xFFFFFDFF
            e.bullet_props.sound_override = self._int_arg(instr, 1)
        elif op == EclOpcode.SET_NO_STACK_RET:
            e.no_stack_ret = instr.arg_bytes(0)[0]
        elif op == EclOpcode.SET_BULLET_RANK_PARAMS:
            e.bullet_rank_speed_low = self._float_arg(instr, 0)
            e.bullet_rank_speed_high = self._float_arg(instr, 1)
            e.bullet_rank_amount1_low = self._int_arg(instr, 2)
            e.bullet_rank_amount1_high = self._int_arg(instr, 3)
            e.bullet_rank_amount2_low = self._int_arg(instr, 4)
            e.bullet_rank_amount2_high = self._int_arg(instr, 5)
        elif op == EclOpcode.SET_HAS_NO_COLLISION:
            e.has_no_collision = instr.arg_bytes(0)[0]
        elif op == EclOpcode.BIND_TIMER_CALLBACK_TO_DEATH:
            e.timer_callback_sub = e.death_callback_sub
            e.timer = 0
        elif op == EclOpcode.SET_IS_SURVIVAL_SPELLCARD:
            e.is_survival_spellcard = instr.arg_bytes(0)[0]
        elif op == EclOpcode.SET_IS_PROJECTILE:
            e.is_projectile = instr.arg_bytes(0)[0]
        elif op == EclOpcode.SET_DESPAWN_ON_OOB:
            e.disable_oob_despawn = instr.arg_bytes(0)[0]
        elif op == EclOpcode.SET_TRAIL:
            e.trail = (instr.arg_bytes(0)[0], self._int_arg(instr, 1),
                       self._int_arg(instr, 2), self._int_arg(instr, 3), 0)
        elif op == EclOpcode.SET_GLOBAL_EFFECT_COLOR_MUL:
            pass  # 渲染, 不接
        elif op == EclOpcode.SET_INVINCIBILITY_TIMER:
            e.invincibility_timer = self._int_arg(instr, 0)
        elif op == EclOpcode.REMOVE_BULLETS_RADIUS:
            self.host.remove_bullets_in_radius(e.pos, self._float_arg(instr, 0))
        elif op == EclOpcode.SET_BOSS_RUN_INTERRUPT:
            boss = w.bosses[self._int_arg(instr, 0) & 7]
            if boss is not None:
                boss.run_interrupt = self._int_arg(instr, 1)
        elif op == EclOpcode.REMOVE_ALL_BULLETS_NO_ITEMS:
            self.host.remove_all_bullets(False)
        elif op == EclOpcode.SET_SPECIAL_EFFECT_POS:
            e.custom_special_effect_pos = self._int_arg(instr, 0)
        elif op == EclOpcode.SET_PRIMARY_VM_ROT_Z:
            e.primary_vm_rot_z = self._float_arg(instr, 0)
        elif op == EclOpcode.VEC_FROM_ANGLE_MAG:
            ang = self._float_arg(instr, 2)
            mag = self._float_arg(instr, 3)
            self._store_float(instr, 1, math.sin(ang) * mag)
            self._store_float(instr, 0, math.cos(ang) * mag)
        elif op == EclOpcode.RAND_EXIT_ANGLE:
            self._store_float(instr, 0, self._exit_angle(randomize=True, simple=True))
        elif op == EclOpcode.ADD_CHERRY_PLUS:
            self.host.add_cherry_plus(self._int_arg(instr, 0))
        elif op == EclOpcode.FREEZE_ECL_DURING_BOMB:
            e.freeze_ecl_during_bombs = self._int_arg(instr, 0)
        else:
            if self.strict:
                raise NotImplementedEclError(
                    f"ECL 指令 id={op} offset={instr.offset:#x}")
            self.host.on_unhandled_opcode(self, instr)
        return None

    # ---- 指令辅助 ----

    def _do_jump(self, instr: EclInstr, new_time: int, byte_offset: int):
        self.current.time = i32(new_time)
        nxt = self._instr(instr.offset + byte_offset)
        if nxt is None:
            log.error("ECL 跳转目标非法: offset={:#x}", instr.offset + byte_offset)
            return "error"
        return nxt

    @staticmethod
    def _compare(op: int, a: float, b: float) -> bool:
        if op in (EclOpcode.JUMP_IF_EQ, EclOpcode.JUMP_IF_EQ_FLOAT):
            return a == b
        if op in (EclOpcode.JUMP_IF_NEQ, EclOpcode.JUMP_IF_NEQ_FLOAT):
            return a != b
        if op in (EclOpcode.JUMP_IF_LT, EclOpcode.JUMP_IF_LT_FLOAT):
            return a < b
        if op in (EclOpcode.JUMP_IF_LEQ, EclOpcode.JUMP_IF_LEQ_FLOAT):
            return a <= b
        if op in (EclOpcode.JUMP_IF_GT, EclOpcode.JUMP_IF_GT_FLOAT):
            return a > b
        return a >= b

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
            it.params = [self._float_arg(instr, 4), self._float_arg(instr, 5),
                         self._float_arg(instr, 6), self._float_arg(instr, 7)]
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
        p.sprite_offset = self._get_int(sprite_offset) if instr.param_mask & 2 \
            else sprite_offset
        if not e.disable_bullets:
            self.host.spawn_bullet_pattern(p)

    def _spawn_laser_pattern(self, instr: EclInstr) -> None:
        e = self.enemy
        p = e.laser_props
        p.pos = e.pos + e.shoot_offset
        p.sprite = instr.arg_i16(0, 0)
        sprite_offset = instr.arg_i16(0, 1)
        p.sprite_offset = self._get_int(sprite_offset) if instr.param_mask & 2 \
            else sprite_offset
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
        return Vec3(e.pos.x + w.rng.unit() * 128.0 - 64.0,
                    e.pos.y + w.rng.unit() * 128.0 - 64.0, e.pos.z)

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


# ---- 时间轴(EnemyManager::RunEclTimeline) ----

class EclTimelineRunner:
    """一条时间轴的执行器。每帧 step() 一次。"""

    def __init__(self, ecl_file: EclFile, index: int, world: EclWorld,
                 host: EclHost) -> None:
        self.timelines = ecl_file.timelines[index]
        self.world = world
        self.host = host
        self.time = 0
        self.idx = 0  # 当前指令下标(模拟 timelineInstr 指针)

    @property
    def done(self) -> bool:
        return self.idx >= len(self.timelines) or self.timelines[self.idx].time < 0

    def step(self) -> None:
        w, host = self.world, self.host
        while not self.done:
            instr = self.timelines[self.idx]
            if self.time == instr.time:
                op = instr.opcode
                if op in (0, 1, 2, 3, 4, 5, 6, 7):  # spawn 系, BossPresent 时不刷
                    if not self._boss_present():
                        pos = self._pos_of(instr)
                        if op & 1:  # 奇数: 默认 life/itemDrop/score
                            host.spawn_enemy(instr.arg0, pos, -1, -1, -1,
                                             1 if op >= 2 else 0, EclContextArgs())
                        else:
                            host.spawn_enemy(instr.arg0, pos, instr.arg_int(3),
                                             instr.arg_int(4), instr.arg_int(5),
                                             1 if op >= 2 else 0, EclContextArgs())
                elif op == 8:
                    host.msg_read(instr.arg0)  # C 还会加 character*10, 交给宿主
                elif op == 9:
                    if host.msg_wait():
                        self.time -= 1  # 底部 time++ 抵消, 时间轴停住
                        break
                elif op == 10:
                    boss = w.bosses[instr.arg_int(0) & 7]
                    if boss is not None:
                        boss.run_interrupt = instr.arg_int(1)
                elif op == 11:
                    host.set_power(instr.arg0)
                elif op == 12:
                    boss = w.bosses[instr.arg0 & 7]
                    if boss is not None and boss.active:
                        self.time -= 1  # 等 boss 退场
                        break
            elif self.time < instr.time:
                break
            self.idx += 1
        self.time += 1

    def _boss_present(self) -> bool:
        return any(b is not None for b in self.world.bosses)

    def _pos_of(self, instr: EclTimelineInstr) -> Vec3:
        """坐标 <= -990 的分量换成场内随机值。"""
        w = self.world
        x, y, z = (instr.arg_float(0), instr.arg_float(1), instr.arg_float(2))
        if x <= -990.0:
            x = w.rng.unit() * PLAYFIELD_W
        if y <= -990.0:
            y = w.rng.unit() * PLAYFIELD_H
        if z <= -990.0:
            z = w.rng.unit() * 800.0
        return Vec3(x, y, z)
