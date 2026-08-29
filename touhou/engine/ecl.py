"""ECL 字节码解析与解释器状态结构 —— Pythonic。

对照 th07 反编译源码 `EclManager.cpp/.hpp`、`EnemyEclInstr.cpp` 还原:
.ecl 文件解析(头/sub 表/时间轴表) + 解释器读写的状态结构 + 时间轴执行器。

真实格式与规格 §B.6 的差异(以 EclManager.hpp 为准):
- 指令头是 12 字节: `u32 time; i16 id; i16 size; u8 unused; u8 skipOnDifficulty;
  u16 paramMask`(规格漏了 unused 字节)。
- `skipOnDifficulty` 是位掩码而非开关: `(skip & (1<<difficulty)) != 0` 时才执行。
- header 里 timelinePtr[16]/subTable[] 都是相对文件头的 i32 偏移(Load 时加基址)。
- sub 指令流以 `time=0xFFFFFFFF, id=-1` 的记录结尾; 时间轴以 `time<0` 结尾。

本模块是作品无关层: 文件格式(EclFile/EclInstr/EclTimelineInstr)、状态结构
(EclEnemyState/EclContext/EclContextArgs/EclWorld/Vec3)、宿主协议(EclHost)、
时间轴执行器(EclTimelineRunner)、已知 opcode 命名常量(EclOpcode)。
EclFile 双向: parse 解字节, serialize 写回(承诺 serialize(parse(data)) == data
逐字节成立, 保留字段的取舍见 EclFile docstring)。按作品名解析格式的统一
enc/dec 入口在 engine/ecl_codec.py(EclCodec, 经注册表 EclSpec.file_format)。
VM 框架在 engine/ecl_base.py(EclMachineBase), TH07 的变量映射与 161 条
opcode 实现在 games/th07/ecl_vm.py(EclMachineTh07 + EclVarId)。

ExIns (RUN_EX_INS/SET_EX_INS, 24 条 boss 特技) 覆盖状态:
- idx 0..23 全部在 ecl_host.GameEclHost 实现 (EnemyEclInstr.cpp 逐条对照);
  8 个真实 ecldata 中 0..23 均有出现 (统计见 scratch_dbg/exins_stats.py)。
- 表现侧: 震屏(BombEffects type=1)经 host.shake_events、音乐(19 淡出/
  20 复活蝶 BGM)经 host.bgm_events 透出给播放层; 仍不接的只有纯视觉
  (15 闪屏 pulse、各条里的特效/换皮) —— 均为注释说明的无逻辑效果。
"""

from __future__ import annotations

import math
import struct
import msgspec
from enum import IntEnum
from typing import TYPE_CHECKING, Optional

from ..exceptions import EclParseError
from ..logger import logger as log
from ..utils import cdiv, i16, i32
from .rng import Rng

if TYPE_CHECKING:
    from .ecl_base import EclMachineBase  # 仅类型检查期(ecl_base 运行时依赖本模块)
    from .enemies import EclEnemy  # 仅类型检查期(enemies 运行时依赖本模块)

# 游戏可视区(供 ClampPos 默认值/随机坐标用, 见 g_GameManager.playerMovementAreaSize)
PLAYFIELD_W = 384.0
PLAYFIELD_H = 448.0


class EclOpcode(IntEnum):
    UNIMP = 1  # RunEcl 直接返回错误(= 脚本结束/despawn)
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
    ADD_CHERRY_PLUS = 160 # 提醒AI：通用EclOpcode不应该有作品有关的枚举，必须修改
    FREEZE_ECL_DURING_BOMB = 161


# ---- 文件结构(解析) ----

_INSTR_HEADER = struct.Struct(
    "<IhhBBH"
)  # time, id, size, unused, skipOnDifficulty, paramMask
_INSTR_HEADER_SIZE = _INSTR_HEADER.size  # 12
_TIMELINE_HEADER = struct.Struct("<hhhh")  # time, arg0, opcode, size

# sub 指令流终止记录
_TERMINATOR_ID = -1


class EclInstr(msgspec.Struct, frozen=True):
    """一条解析好的 ECL 指令。args 是 u32 原始字, 按 paramMask 解释。"""

    offset: int  # 文件内绝对偏移(模拟 C 的 EclRawInstr*)
    time: int  # u32: 到点(context time == time)才执行
    id: int  # i16: EclOpcode 或 -1(终止)
    size: int  # i16: 整条字节数(含 12 字节头)
    unused: int  # u8: 指令头保留字节(执行语义不用, 原样保留保 round-trip)
    skip_difficulty: int  # u8 位掩码: 当前难度位为 1 才执行(0xFF = 全难度)
    param_mask: int  # u16: bit i = 1 → args[i] 是变量 id 而非立即数
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
    time: int  # i16, <0 表示时间轴结束
    arg0: int  # i16, 通常是 sub id
    opcode: int  # i16: 0..7 spawn, 8 msg, 9 msgWait, 10 boss中断, 11 火力, 12 等boss
    size: int
    args: tuple[int, ...]

    def arg_int(self, idx: int) -> int:
        return i32(self.args[idx])

    def arg_float(self, idx: int) -> float:
        f: float = struct.unpack("<f", struct.pack("<I", self.args[idx]))[0]
        return f


class EclFile(msgspec.Struct):
    """解析后的 .ecl: sub 指令流 + 时间轴。

    round-trip 保留字段(执行语义不用, 只为 serialize 逐字节还原):
    - ``EclInstr.unused``: 指令头保留字节(真实 ecldata 里有非零值);
    - ``_timeline_offsets``: 头部 16 个时间轴偏移槽原值(首个空槽 = 文件长哨兵);
    - ``_timeline_trailing``: 每条时间轴末尾的截短终止记录(``ff ff 04 00``,
      无则空 bytes)。
    """

    sub_count: int
    timeline_count: int
    subs: list[tuple[EclInstr, ...]]
    timelines: list[tuple[EclTimelineInstr, ...]]
    _instr_at: dict[int, EclInstr]
    _timeline_offsets: tuple[int, ...]
    _timeline_trailing: list[bytes]

    def __repr__(self) -> str:
        # _instr_at 等索引/保留字段不进 repr (对照原 dataclass 的 field(repr=False))
        return (
            f"EclFile(sub_count={self.sub_count!r}, "
            f"timeline_count={self.timeline_count!r}, subs={self.subs!r}, "
            f"timelines={self.timelines!r})"
        )

    @classmethod
    def parse(cls, data: bytes) -> "EclFile":
        if len(data) < 68:
            raise EclParseError("文件太小, 没有完整 EclRawHeader")
        sub_count, timeline_count = struct.unpack_from("<hh", data, 0)
        if not (0 <= sub_count <= 4096 and 0 <= timeline_count <= 16):
            raise EclParseError(
                f"非法 header: subCount={sub_count} timelineCount={timeline_count}"
            )
        timeline_offsets = struct.unpack_from("<16i", data, 4)
        sub_offsets = struct.unpack_from(f"<{sub_count}i", data, 68)

        subs: list[tuple[EclInstr, ...]] = []
        instr_at: dict[int, EclInstr] = {}
        for sub_id, off in enumerate(sub_offsets):
            instrs: list[EclInstr] = []
            while True:
                if off + _INSTR_HEADER_SIZE > len(data):
                    raise EclParseError(f"sub {sub_id}: 指令越界 (off={off})")
                time, op_id, size, unused, skip, mask = _INSTR_HEADER.unpack_from(
                    data, off
                )
                if size < _INSTR_HEADER_SIZE or (size - _INSTR_HEADER_SIZE) % 4 != 0:
                    raise EclParseError(f"sub {sub_id}: 非法 size={size} (off={off})")
                if off + size > len(data):
                    raise EclParseError(f"sub {sub_id}: 指令截断 (off={off})")
                n_args = (size - _INSTR_HEADER_SIZE) // 4
                args = struct.unpack_from(f"<{n_args}I", data, off + _INSTR_HEADER_SIZE)
                instr = EclInstr(off, time, op_id, size, unused, skip, mask, args)
                instrs.append(instr)
                instr_at[off] = instr
                off += size
                if instr.is_terminator:
                    break
            subs.append(tuple(instrs))

        timelines: list[tuple[EclTimelineInstr, ...]] = []
        trailing: list[bytes] = []
        for i in range(timeline_count):
            off = timeline_offsets[i]
            tl: list[EclTimelineInstr] = []
            while off < len(data):  # 时间轴可以没有终止符, 直接延伸到 EOF
                if off + _TIMELINE_HEADER.size > len(data):
                    # 尾部可能有 4 字节截短终止记录(如 ff ff 04 00, time=-1)
                    tail = (
                        struct.unpack_from("<h", data, off)[0]
                        if off + 2 <= len(data)
                        else -1
                    )
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
            # 解析未消费的字节(截短终止记录)原样保留, 供 serialize 还原
            nxt = timeline_offsets[i + 1] if i + 1 < timeline_count else len(data)
            trailing.append(data[off:nxt])

        return cls(
            sub_count,
            timeline_count,
            subs,
            timelines,
            instr_at,
            tuple(timeline_offsets),
            trailing,
        )

    def serialize(self) -> bytes:
        """把解析结果写回 .ecl 二进制(parse 的逆运算)。

        承诺: ``serialize(parse(data)) == data`` 逐字节成立(真实 th07.dat 的
        ecldata1..8 全覆盖, 见 tests/test_ecl_codec.py)。各段写回各自记录的
        绝对偏移, 不依赖排布连续性; 指令头 unused 字节、时间轴原始偏移槽、
        截短终止记录都按 parse 保留的原样字段写回(见类 docstring)。
        改动指令内容后再序列化(长度变化需重排偏移表)是后续工作。
        """
        header_size = 4 + 64 + 4 * self.sub_count
        end = header_size
        for sub in self.subs:
            for ins in sub:
                end = max(end, ins.offset + ins.size)
        for i, tl in enumerate(self.timelines):
            tail_start = self._timeline_offsets[i]
            for tins in tl:
                end = max(end, tins.offset + tins.size)
                tail_start = tins.offset + tins.size
            end = max(end, tail_start + len(self._timeline_trailing[i]))
        buf = bytearray(end)

        struct.pack_into("<hh", buf, 0, self.sub_count, self.timeline_count)
        struct.pack_into("<16i", buf, 4, *self._timeline_offsets)
        for sub_id, sub in enumerate(self.subs):
            if not sub:
                continue  # 空 sub 无偏移可还原(手工构造的边界情形)
            struct.pack_into("<i", buf, 68 + 4 * sub_id, sub[0].offset)
            for ins in sub:
                _INSTR_HEADER.pack_into(
                    buf,
                    ins.offset,
                    ins.time,
                    ins.id,
                    ins.size,
                    ins.unused,
                    ins.skip_difficulty,
                    ins.param_mask,
                )
                if ins.args:
                    struct.pack_into(
                        f"<{len(ins.args)}I",
                        buf,
                        ins.offset + _INSTR_HEADER_SIZE,
                        *ins.args,
                    )
        for i, tl in enumerate(self.timelines):
            tail_start = self._timeline_offsets[i]
            for tins in tl:
                _TIMELINE_HEADER.pack_into(
                    buf, tins.offset, tins.time, tins.arg0, tins.opcode, tins.size
                )
                if tins.args:
                    struct.pack_into(
                        f"<{len(tins.args)}I",
                        buf,
                        tins.offset + _TIMELINE_HEADER.size,
                        *tins.args,
                    )
                tail_start = tins.offset + tins.size
            tail = self._timeline_trailing[i]
            buf[tail_start : tail_start + len(tail)] = tail
        return bytes(buf)

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
        return EclContextArgs(
            list(self.int_vars1),
            list(self.float_vars1),
            list(self.int_vars2),
            list(self.float_vars2),
            list(self.global_ints),
            list(self.global_floats),
        )


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
        default_factory=lambda: [BulletCommandData() for _ in range(6)]
    )
    count1: int = 0
    count2: int = 0
    aim_mode: int = 0  # = opcode - 64, 对应 bullets.Aim
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
        default_factory=lambda: [BulletCommandData() for _ in range(5)]
    )
    start_offset: float = 0.0
    end_offset: float = 0.0
    start_length: float = 0.0
    width: float = 0.0
    start_time: int = 0
    duration: int = 0
    end_time: int = 0
    hitbox_start_time: int = 0
    hitbox_end_time: int = 0
    type: int = 0  # 0 = 跟随敌人, 1 = 固定(注意 C 里 MOVING→0, FIXED→1)
    flags: int = 0
    sound_override: int = -1


class EclInterpState(msgspec.Struct):
    """C EclInterp: 跨帧变量插值(INIT_INTERP 注册, 每帧推进)。"""

    active: bool = False
    timer: int = 0
    duration: int = 0  # args[0]
    func_idx: int = 0  # args[1]: 0..6=lerp, 7=cubic hermite
    easing: int = 0  # args[2]: 0 线性, 1..3 ease-in, 4..6 ease-out
    params: list[float] = msgspec.field(
        default_factory=lambda: [0.0] * 4
    )  # p0,p1,m0,m1
    target_var: int = 0  # args[7]: 目标变量 id

    def clear(self) -> None:
        self.active = False
        self.timer = 0

    def clone(self) -> "EclInterpState":
        return EclInterpState(
            self.active,
            self.timer,
            self.duration,
            self.func_idx,
            self.easing,
            list(self.params),
            self.target_var,
        )


class EclContext(msgspec.Struct):
    """C EnemyEclContext: 一层 sub 调用的执行现场。"""

    instr_offset: int = 0
    time: int = 0
    wait_timer: int = 0
    ex_instr_idx: int = -1  # SET_EX_INS 注册的每帧回调(-1=无)
    ex_instr: Optional[EclInstr] = None
    args: EclContextArgs = msgspec.field(default_factory=EclContextArgs)
    interps: list[EclInterpState] = msgspec.field(
        default_factory=lambda: [EclInterpState() for _ in range(8)]
    )
    laser_not_in_use: int = 0
    is_periodic_sub: int = 0
    sub_id: int = -1

    def clone(self) -> "EclContext":
        return EclContext(
            self.instr_offset,
            self.time,
            self.wait_timer,
            self.ex_instr_idx,
            self.ex_instr,
            self.args.clone(),
            [i.clone() for i in self.interps],
            self.laser_not_in_use,
            self.is_periodic_sub,
            self.sub_id,
        )


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
    timer: int = 0  # C enemy->timer(每帧 +1)
    item_drop: int = 0
    last_damage: int = 0
    boss_id: int = -1
    # C 敌人模板默认值 (EnemyManager.hpp:350-351): soundIdx=SOUND_BOMB_MARISA_A_FOCUS(7),
    # soundOverride=SOUND_25; 发射音门槛 flags&0x200 (BulletManager.cpp:611)
    bullet_props: EnemyBulletShooter = msgspec.field(
        default_factory=lambda: EnemyBulletShooter(sound_idx=7, sound_override=25)
    )
    laser_props: EnemyLaserShooter = msgspec.field(default_factory=EnemyLaserShooter)
    lasers: list = msgspec.field(
        default_factory=lambda: [None] * 32
    )  # 宿主返回的激光句柄
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
    # anm 只存 id(渲染侧经 id 起 anm 脚本 VM, 见 games/th07/view/sprite_view.py)
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
        return (
            cdiv(
                rank * (self.bullet_rank_amount1_high - self.bullet_rank_amount1_low),
                32,
            )
            + self.bullet_rank_amount1_low
        )

    def bullet_rank_amount2(self, rank: int) -> int:
        return (
            cdiv(
                rank * (self.bullet_rank_amount2_high - self.bullet_rank_amount2_low),
                32,
            )
            + self.bullet_rank_amount2_low
        )

    def bullet_rank_speed(self, rank: float) -> float:
        return (
            rank * (self.bullet_rank_speed_high - self.bullet_rank_speed_low) / 32
            + self.bullet_rank_speed_low
        )

    def shoot_interval_rank_delta(self, rank: int) -> int:
        """Enemy::ShootInterval: low=interval/5, high=-interval/5 的 rank 插值。"""
        low = cdiv(self.shoot_interval, 5)
        return cdiv(rank * (-low - low), 32) + low


# ---- 世界与宿主接口 ----


class EclWorld(msgspec.Struct):
    """ECL 可见的全局状态(g_GameManager/g_GlobalEclVars/g_Player 的切片)。"""

    rng: Rng = msgspec.field(default_factory=Rng)
    difficulty: int = 1  # 0=E 1=N 2=H 3=L
    rank: int = 16  # 0..32
    player_pos: Vec3 = msgspec.field(default_factory=lambda: Vec3(192.0, 400.0, 0.0))
    player_shottype: int = 0
    current_power: int = 0
    current_stage: int = 1
    global_ints: list[int] = msgspec.field(default_factory=lambda: [0] * 4)
    global_floats: list[float] = msgspec.field(default_factory=lambda: [0.0] * 4)
    bosses: list[Optional[EclEnemyState]] = msgspec.field(
        default_factory=lambda: [None] * 8
    )
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
    spellcard_idx: int = -1  # g_EnemyManager.spellcardInfo.spellcardIdx

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
    def spawn_enemy(
        self,
        sub_id: int,
        pos: Vec3,
        life: int,
        item_drop: int,
        score: int,
        mirror: int,
        context_args: EclContextArgs,
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

    def begin_spellcard(
        self, enemy: EclEnemyState, gui_id: int, spellcard_idx: int, name: str
    ) -> None:
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

    def set_script_wait_time(self, value: int) -> None:
        pass

    def boss_active(self, idx: int) -> bool:
        return False

    def run_ex_instr(
        self,
        idx: int,
        enemy: EclEnemyState,
        instr: Optional[EclInstr],
        ctx: Optional[EclContext] = None,
    ) -> bool:
        """ECL ex 指令(24 条 boss 专用特技)。返回 True = 宿主已处理。

        ctx 是触发时的当前上下文 (C 里 ExIns 直接读写 enemy->currentContext:
        eclContextArgs / subId); 仅 ExIns 4/16/17/18 需要。
        """
        return False

    def on_unhandled_opcode(self, machine: "EclMachineBase", instr: EclInstr) -> None:
        """未实现指令的钩子(默认记日志跳过)。"""
        log.warning(
            "未实现的 ECL 指令 id={} offset={:#x} sub={}, 已跳过",
            instr.id,
            instr.offset,
            machine.current.sub_id,
        )


# ---- 时间轴(EnemyManager::RunEclTimeline) ----


class EclTimelineRunner:
    """一条时间轴的执行器。每帧 step() 一次。"""

    def __init__(
        self, ecl_file: EclFile, index: int, world: EclWorld, host: EclHost
    ) -> None:
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
                            host.spawn_enemy(
                                instr.arg0,
                                pos,
                                -1,
                                -1,
                                -1,
                                1 if op >= 2 else 0,
                                EclContextArgs(),
                            )
                        else:
                            host.spawn_enemy(
                                instr.arg0,
                                pos,
                                instr.arg_int(3),
                                instr.arg_int(4),
                                instr.arg_int(5),
                                1 if op >= 2 else 0,
                                EclContextArgs(),
                            )
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
