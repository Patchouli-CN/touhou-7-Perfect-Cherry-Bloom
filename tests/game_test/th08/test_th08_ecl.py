"""EclMachineTh08(games/th08/ecl_vm.py)+ 时刻 + 时间轴 runner 的测试。

th08 布局手工指令流跑 VM: 变量读写全区间抽查、算术/跳转/call 对标
C 语义(EclRunLow.inl/EclRunHigh.inl/EclDependencies.cpp)、难度掩码过滤、
child 上下文块并行、op180/181 经宿主路由、EX 指令路由、
Th08TimelineRunner 各 opcode 行为(stub host/world)。
效果类只断言"调用了对的 host 方法 + 参数解析正确", 不断言世界效果。
@needs_data: 真实 ecldata1.ecl 全 sub smoke(start + 逐帧 step 有限帧不炸,
效果走 no-op host)。
"""

from __future__ import annotations

import math
import struct

import pytest

import touhou  # noqa: F401  # import 即完成 th08 注册
from touhou.engine.ecl import EclHost, Vec3
from touhou.games.th08.clock import MAX_UNITS, Th08Clock
from touhou.games.th08.crypt import try_decrypt_from_table
from touhou.games.th08.ecl_file import EclFileTh08
from touhou.games.th08.ecl_state import Th08EclWorld, Th08EnemyState
from touhou.games.th08.ecl_host import Th08GameEclHost, Th08NullHost
from touhou.games.th08.ecl_timeline import Th08TimelineRunner
from touhou.games.th08.ecl_vm import EclMachineTh08, Th08EclOpcode as Op, Th08EclVarId as V
from touhou.paths import DEFAULT_DATA_PATHS
from touhou.schema.archive import open_archive
from touhou.utils.gensokyo_time import INCIDENT_ETERNAL_NIGHT

from .conftest import needs_data

TH08_DAT = DEFAULT_DATA_PATHS["th08"]


# ---- 手工构造 th08 .ecl 二进制(同 test_th08_ecl_file.py 的布局) ----


def _f(v: float) -> int:
    """float 打包成 u32 操作数字。"""
    return struct.unpack("<I", struct.pack("<f", v))[0]


def _instr(
    time: int,
    op: int,
    args: tuple = (),
    mask: int = 0,
    skip: int = 0xFF,
    unused: int = 0,
) -> bytes:
    size = 12 + 4 * len(args)
    return struct.pack("<IhhBBH", time, op, size, unused, skip, mask) + b"".join(
        struct.pack("<I", a & 0xFFFFFFFF) for a in args
    )


def _tl_instr(time: int, op: int, args: tuple = (0,) * 7, diff: int = 0xFF) -> bytes:
    size = 8 + 4 * len(args)
    return struct.pack("<ihBB", time, op, size, diff) + b"".join(
        struct.pack("<I", a & 0xFFFFFFFF) for a in args
    )


def _build_ecl(subs: list[list[bytes]], timelines: list[list[bytes]] = ()) -> bytes:
    n = len(subs)
    header_size = 0x48 + 4 * n
    offsets, blobs = [], []
    off = header_size
    for s in subs:
        offsets.append(off)
        blob = b"".join(s) + _instr(0xFFFFFFFF, -1)
        blobs.append(blob)
        off += len(blob)
    tl_offsets = [0] * 16
    for i, t in enumerate(timelines):
        tl_offsets[i] = off
        blob = b"".join(t) + _tl_instr(-1, 0)
        blobs.append(blob)
        off += len(blob)
    header = (
        struct.pack("<Ihh", 0x800, n, len(timelines))
        + struct.pack("<16I", *tl_offsets)
        + struct.pack(f"<{n}I", *offsets)
    )
    return header + b"".join(blobs)


def _machine(
    subs: list[list[bytes]],
    *,
    world: Th08EclWorld | None = None,
    host: EclHost | None = None,
    enemy: Th08EnemyState | None = None,
    timelines: list[list[bytes]] = (),
) -> EclMachineTh08:
    ecl = EclFileTh08.parse(_build_ecl(subs, timelines))
    m = EclMachineTh08(ecl, enemy=enemy, world=world, host=host)
    m.start(0)
    return m


def _step(m: EclMachineTh08, frames: int = 1) -> None:
    for _ in range(frames):
        if not m.step():
            break


class _RecHost(Th08NullHost):
    """记录宿主调用的 stub(效果类断言"调了对的方法 + 参数对")。

    继承 Th08NullHost: 15 个 th08 接缝的 no-op 兜底(原 EclHost 基类提供,
    下沉后由 games/th08 层承担)。"""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.laser_handle = object()

    def spawn_bullet_pattern(self, props) -> None:
        self.calls.append(("shot", props))

    def spawn_laser_pattern(self, props):
        self.calls.append(("laser", props))
        return self.laser_handle

    def spawn_enemy(self, sub_id, pos, life, item_drop, score, mirror, context_args):
        self.calls.append(("enemy", sub_id, pos, life, item_drop, score, mirror))
        return None

    def spawn_familiar(
        self, kind, sub_id, pos, life, item_drop, score, context_args, parent=None
    ):
        self.calls.append(("familiar", kind, sub_id, pos, life, item_drop, score))
        return None

    def spawn_item(self, pos, item_type) -> None:
        self.calls.append(("item", item_type))

    def play_sound(self, idx) -> None:
        self.calls.append(("sound", idx))

    def clock_advance(self) -> None:
        self.calls.append(("clock_adv",))

    def clock_hide(self) -> None:
        self.calls.append(("clock_hide",))

    def msg_read(self, msg_id) -> None:
        self.calls.append(("msg", msg_id))

    def set_power(self, value) -> None:
        self.calls.append(("power", value))

    def show_retry_menu(self) -> None:
        self.calls.append(("retry",))

    def set_boss(self, idx, enemy) -> None:
        self.calls.append(("boss", idx, enemy is not None))

    def begin_spellcard(self, enemy, gui_id, spellcard_idx, name) -> None:
        self.calls.append(("spell", gui_id, spellcard_idx, name))

    def remove_all_enemies(self, score_max, score_min):
        self.calls.append(("clear_enemies", score_max, score_min))


# ---- 变量系统全区间抽查 ----


def test_int_var_routing() -> None:
    """int 变量: 上下文/enemy/extra/调用参数/全局调用参数/世界状态读写。"""
    m = _machine(
        [
            [
                _instr(0, Op.SET_INT, (V.LOCAL_INT0, 42), mask=0b01),
                _instr(0, Op.SET_INT, (V.ENEMY_INT0 + 3, -7), mask=0b01),
                _instr(0, Op.SET_INT, (V.EXTRA_INT0 + 2, 5), mask=0b01),
                _instr(0, Op.SET_INT, (V.CALL_INT0 + 1, 9), mask=0b01),
                _instr(0, Op.SET_INT, (V.GLOBAL_CALL_INT0 + 2, 33), mask=0b01),
                _instr(0, Op.SET_INT, (V.LIFE, 800), mask=0b01),
                _instr(0, Op.SET_INT, (V.SCORE, 999), mask=0b01),
                _instr(0, Op.SET_INT, (V.ITEM_DROP, 4), mask=0b01),
                _instr(0, Op.SET_INT, (V.RNG_INT, 123), mask=0b01),  # 不可写, 丢弃
            ]
        ]
    )
    _step(m)
    assert m._get_int(V.LOCAL_INT0) == 42
    assert m._get_int(V.ENEMY_INT0 + 3) == -7
    assert m._get_int(V.EXTRA_INT0 + 2) == 5
    assert m._get_int(V.CALL_INT0 + 1) == 9
    assert m._get_int(V.GLOBAL_CALL_INT0 + 2) == 33
    assert m._get_int(V.LIFE) == 800
    assert m._get_int(V.SCORE) == 999
    assert m._get_int(V.ITEM_DROP) == 4
    assert m._get_int(V.DIFFICULTY) == m.world.difficulty
    assert m._get_int(V.RANK) == m.world.rank
    # 立即数 default: 不在变量空间的原样返回(EclOperandsInt.cpp:149)
    assert m._get_int(777) == 777
    # int 读 10079-10082 走 default 原样返回(EclOperandsInt.cpp:25)
    assert m._get_int(V.INTERP_DELTA_X) == V.INTERP_DELTA_X


def test_float_var_routing() -> None:
    """float 变量: 读写 + 位置分量 + 不可写丢弃。"""
    m = _machine(
        [
            [
                _instr(0, Op.SET_FLOAT, (_f(float(V.LOCAL_FLOAT0)), _f(2.5)), mask=0b01),
                _instr(0, Op.SET_FLOAT, (_f(float(V.ENEMY_FLOAT0 + 1)), _f(-1.25)), mask=0b01),
                _instr(0, Op.SET_FLOAT, (_f(float(V.POS_X)), _f(192.0)), mask=0b01),
                _instr(0, Op.SET_FLOAT, (_f(float(V.EXTRA_FLOAT0)), _f(0.75)), mask=0b01),
                _instr(0, Op.SET_FLOAT, (_f(float(V.GLOBAL_CALL_FLOAT0)), _f(8.5)), mask=0b01),
                _instr(0, Op.SET_FLOAT, (_f(float(V.ANGLE)), _f(1.0)), mask=0b01),
            ]
        ]
    )
    _step(m)
    assert m._get_float(V.LOCAL_FLOAT0) == 2.5
    assert m._get_float(V.ENEMY_FLOAT0 + 1) == -1.25
    assert m._get_float(V.POS_X) == 192.0
    assert m._get_float(V.EXTRA_FLOAT0) == 0.75
    assert m._get_float(V.GLOBAL_CALL_FLOAT0) == 8.5
    assert m._get_float(V.ANGLE) == 1.0
    # int 读 float 变量: f32→i32 截断(EclOperandsInt.cpp:69-84)
    assert m._get_int(V.LOCAL_FLOAT0) == 2
    # float 读未命中(如 10100)原样返回 raw(EclOperandsFloat.cpp:144-146)
    assert m._get_float_value(V.SPELLCARD_TIMER, 1234.5) == 1234.5


def test_world_position_reads_pos_offset() -> None:
    """位置变量读 worldPosition(= position + positionOffset, EclRun.cpp:54-56),
    写落 position(EclOperandsFloat.cpp:183-185)。"""
    m = _machine([[]])
    m.enemy.pos.set(10.0, 20.0, 0.0)
    m.enemy.pos_offset.set(1.0, 2.0, 3.0)
    assert m._get_float(V.POS_X) == 11.0
    assert m._get_float(V.POS_Y) == 22.0
    m._set_float(V.POS_X, 50.0)
    assert m.enemy.pos.x == 50.0
    assert m._get_float(V.POS_X) == 51.0  # 读仍含 offset


# ---- 算术/三角对标 C 语义 ----


def test_arith_2op_and_3op() -> None:
    """2 操作数(10-19, th08 独有)/3 操作数(20-29)/inc/dec 对标 EclRunLow.inl。"""
    m = _machine(
        [
            [
                _instr(0, Op.SET_INT, (V.LOCAL_INT0, 7), mask=0b01),
                _instr(0, Op.ADD_ASSIGN, (V.LOCAL_INT0, 5), mask=0b01),  # 7+5=12
                _instr(0, Op.SUB_ASSIGN, (V.LOCAL_INT0, 2), mask=0b01),  # 10
                _instr(0, Op.MUL_ASSIGN, (V.LOCAL_INT0, 3), mask=0b01),  # 30
                _instr(0, Op.DIV_ASSIGN, (V.LOCAL_INT0, 4), mask=0b01),  # 7 (cdiv)
                _instr(0, Op.MOD_ASSIGN, (V.LOCAL_INT0, 4), mask=0b01),  # 3
                _instr(0, Op.DIV_ASSIGN, (V.LOCAL_INT0, 0), mask=0b01),  # 除 0 → 0
                _instr(0, Op.INC, (V.LOCAL_INT0,), mask=0b01),  # 1
                _instr(0, Op.ADD, (V.LOCAL_INT0 + 1, 3, 4), mask=0b001),
                _instr(0, Op.MOD, (V.LOCAL_INT0 + 2, -7, 4), mask=0b001),  # cmod → -3
            ]
        ]
    )
    _step(m)
    assert m._get_int(V.LOCAL_INT0) == 1
    assert m._get_int(V.LOCAL_INT0 + 1) == 7
    assert m._get_int(V.LOCAL_INT0 + 2) == -3  # C 截断取模(-7/4=-1 余 -3)


def test_float_arith_and_trig() -> None:
    dst = V.LOCAL_FLOAT0
    m = _machine(
        [
            [
                _instr(0, Op.SET_FLOAT, (_f(float(dst)), _f(8.0)), mask=0b01),
                _instr(0, Op.ADD_ASSIGN_FLOAT, (_f(float(dst)), _f(2.0)), mask=0b01),
                _instr(0, Op.DIV_ASSIGN_FLOAT, (_f(float(dst)), _f(4.0)), mask=0b01),
                _instr(0, Op.MUL_FLOAT, (_f(float(dst + 1)), _f(1.5), _f(2.0)), mask=0b001),
                _instr(0, Op.SIN, (_f(float(dst + 2)), _f(0.0)), mask=0b01),
                _instr(0, Op.COS, (_f(float(dst + 3)), _f(0.0)), mask=0b01),
                _instr(0, Op.NORMALIZE_ANGLE, (_f(float(dst + 4)), _f(0.0)), mask=0b01),
                _instr(0, Op.LERP, (_f(float(dst + 5)), _f(10.0), _f(2.0), _f(0.5)), mask=0b0001),
            ]
        ]
    )
    _step(m)
    assert m._get_float(dst) == 2.5  # (8+2)/4
    assert m._get_float(dst + 1) == 3.0
    assert m._get_float(dst + 2) == 0.0
    assert m._get_float(dst + 3) == 1.0
    # normalize: arg0 先读再归一(读的是 dst+4 自己=0)
    assert m._get_float(dst + 4) == 0.0
    assert m._get_float(dst + 5) == 6.0  # (10-2)*0.5+2


def test_polar_and_dist() -> None:
    """op38 polar(角度 normalize)/op39 dist/op166 polar 不 normalize。"""
    dst = V.LOCAL_FLOAT0
    m = _machine(
        [
            [
                # op38: angle=0, mag=2 → x=2, y=0
                _instr(0, Op.VEC_FROM_ANGLE_MAG, (_f(float(dst)), _f(float(dst + 1)), _f(0.0), _f(2.0)), mask=0b0011),
                # op39: (0,0)-(3,4) → 5
                _instr(0, Op.DIST, (_f(float(dst + 2)), _f(0.0), _f(0.0), _f(3.0), _f(4.0)), mask=0b001),
                # op166: angle=π/2, mag=2 → x≈0, y≈2
                _instr(0, Op.VEC_FROM_ANGLE_MAG_RAW, (_f(float(dst + 3)), _f(float(dst + 4)), _f(math.pi / 2), _f(2.0)), mask=0b0011),
            ]
        ]
    )
    _step(m)
    assert m._get_float(dst) == pytest.approx(2.0)
    assert m._get_float(dst + 1) == pytest.approx(0.0)
    assert m._get_float(dst + 2) == pytest.approx(5.0)
    assert m._get_float(dst + 3) == pytest.approx(0.0, abs=1e-6)
    assert m._get_float(dst + 4) == pytest.approx(2.0)


# ---- 跳转/条件跳/call/ret/wait ----


def test_cond_jump_taken_and_fallthrough() -> None:
    """条件跳(40-51): 跳转时间/位移用 raw 操作数(EclRunLow.inl:16-20)。

    布局: A=set var=5(20B) B=jump-if-eq(28B) C=set var=99(20B) D=set var2=7。
    B 命中 → 跳过 C 落 D; 不命中 → C 覆盖成 99。
    """
    # B.offset - A.offset = 20, D.offset - B.offset = 28 + 20 = 48
    jump = _instr(0, Op.JUMP_IF_EQ, (V.LOCAL_INT0, 5, 0, 48), mask=0b0011)
    taken = [
        _instr(0, Op.SET_INT, (V.LOCAL_INT0, 5), mask=0b01),
        jump,
        _instr(0, Op.SET_INT, (V.LOCAL_INT0, 99), mask=0b01),
        _instr(0, Op.SET_INT, (V.LOCAL_INT0 + 1, 7), mask=0b01),
    ]
    m = _machine([taken])
    _step(m)
    assert m._get_int(V.LOCAL_INT0) == 5  # 跳过了 99
    assert m._get_int(V.LOCAL_INT0 + 1) == 7

    not_taken = [
        _instr(0, Op.SET_INT, (V.LOCAL_INT0, 4), mask=0b01),
        jump,
        _instr(0, Op.SET_INT, (V.LOCAL_INT0, 99), mask=0b01),
        _instr(0, Op.SET_INT, (V.LOCAL_INT0 + 1, 7), mask=0b01),
    ]
    m2 = _machine([not_taken])
    _step(m2)
    assert m2._get_int(V.LOCAL_INT0) == 99


def test_dec_jump_loop() -> None:
    """DEC_JUMP(5): 先自减, >0 才跳(EclRunLow.inl:233-242)。"""
    # A=set counter=3(20B)@0 B=inc acc(16B)@20 C=dec_jump(24B)@36
    # C 的跳转位移 = B.offset - C.offset = 20 - 36 = -16, 时间回到 0
    m = _machine(
        [
            [
                _instr(0, Op.SET_INT, (V.LOCAL_INT0, 3), mask=0b01),  # A: 计数器
                _instr(0, Op.INC, (V.LOCAL_INT0 + 1,), mask=0b01),  # B: 累加
                _instr(0, Op.DEC_JUMP, (0, -16, V.LOCAL_INT0), mask=0b100),  # C
            ]
        ]
    )
    _step(m)
    assert m._get_int(V.LOCAL_INT0) == 0  # 减到 0 不再跳
    assert m._get_int(V.LOCAL_INT0 + 1) == 3  # 循环 3 次


def test_sub_call_ret_and_call_params() -> None:
    """SUB_CALL(52): 压栈 + 新上下文拿 g_EclCallParameters 快照
    (EclDependencies.cpp:485-487); SUB_RET(53) 弹栈回主。"""
    m = _machine(
        [
            [
                _instr(0, Op.SUB_CALL, (1,)),
                _instr(0, Op.SET_INT, (V.LOCAL_INT0, 7), mask=0b01),
            ],
            [
                # callee: 把调用参数写进 enemy 变量(跨上下文可见) + 改局部
                _instr(0, Op.SET_INT, (V.ENEMY_INT0, V.CALL_INT0), mask=0b011),
                _instr(0, Op.SET_INT, (V.LOCAL_INT0, 42), mask=0b01),
                _instr(0, Op.SUB_RET),
            ],
        ]
    )
    m.call_params_ints = [11, 22, 33, 44]
    _step(m)
    assert m._get_int(V.ENEMY_INT0) == 11  # 快照进了 callee 的 callParameterInts
    assert m._get_int(V.LOCAL_INT0) == 7  # ret 后主流程继续(42 被 7 覆盖)
    assert not m.stack


def test_wait_secondary_time() -> None:
    """WAIT(2)= secondaryTime: 设置后 redispatch 立即开始递减, 递减期间
    time 回退(EclRun.cpp:58-65); 递减完的下一帧才轮到后续指令。"""
    m = _machine(
        [
            [
                _instr(0, Op.WAIT, (3,)),
                _instr(0, Op.SET_INT, (V.LOCAL_INT0, 1), mask=0b01),
            ]
        ]
    )
    _step(m)
    # 当帧: wait 执行 → advance → redispatch 先查 secondaryTime → 递减并退出
    assert m.current.wait_timer == 2
    assert m._get_int(V.LOCAL_INT0) == 0  # 后续指令被压住
    _step(m)
    assert m.current.wait_timer == 1
    _step(m)
    assert m.current.wait_timer == 0
    assert m._get_int(V.LOCAL_INT0) == 0
    _step(m)  # 递减完, time==0 轮到 SET_INT
    assert m._get_int(V.LOCAL_INT0) == 1


def test_difficulty_mask_th08_semantics() -> None:
    """th08 难度掩码: 指令掩码需完整包含(全局难度位|敌人覆盖位)
    (EclRun.cpp:67-74) —— 与 th07 的"有交集即执行"在带覆盖位时分叉。"""
    w = Th08EclWorld(difficulty=1)  # N, 难度位 0x02
    sub = [[_instr(0, Op.SET_INT, (V.LOCAL_INT0, 1), mask=0b01, skip=0x02)]]
    m = _machine(sub, world=w)
    _step(m)
    assert m._get_int(V.LOCAL_INT0) == 1  # 单难度位: 与 th07 等价, 执行

    w2 = Th08EclWorld(difficulty=1)
    m2 = _machine(sub, world=w2)
    m2.enemy.difficulty_mask_override = 0x01  # eff = 0x03
    _step(m2)
    # th07 语义会执行(0x02&0x02), th08 要求 (0x02&0x03)==0x03 → 跳过
    assert m2._get_int(V.LOCAL_INT0) == 0


# ---- child 上下文块(op135) ----


def test_child_block_runs_in_parallel() -> None:
    """op135 建块后, 同一 step 内主上下文与 child 各跑一帧
    (EclRun.cpp:188-202 的轮询)。"""
    m = _machine(
        [
            [
                _instr(0, Op.SET_INT, (V.LOCAL_INT0, 11), mask=0b01),
                _instr(0, Op.SET_CHILD_CONTEXT, (0, 1)),
                _instr(0, Op.SET_INT, (V.LOCAL_INT0, 22), mask=0b01),
            ],
            [
                # child: 继承主上下文 locals(op135 的 memcpy), 写 enemy 变量
                _instr(0, Op.SET_INT, (V.ENEMY_INT0, V.LOCAL_INT0), mask=0b011),
            ],
        ]
    )
    _step(m)
    assert m._get_int(V.LOCAL_INT0) == 22
    # child 在建块瞬间继承 locals(=11), 当帧就跑了自己的 sub
    assert m._get_int(V.ENEMY_INT0) == 11
    assert m._child_blocks[0] is not None
    assert not m.finished


def test_child_block_freed_on_stack_underflow() -> None:
    """child 块 ret 栈下溢 = 释放该块继续轮询(PopEclContext 返回 1,
    EclDependencies.cpp:508-524), 主上下文不受牵连。"""
    m = _machine(
        [
            [_instr(0, Op.SET_CHILD_CONTEXT, (0, 1))],
            [
                _instr(0, Op.SET_INT, (V.ENEMY_INT0, 99), mask=0b01),
                _instr(0, Op.SUB_RET),  # child 栈空 → 释放块
            ],
        ]
    )
    _step(m)
    assert m._get_int(V.ENEMY_INT0) == 99
    assert m._child_blocks[0] is None
    assert not m.finished
    _step(m)  # 主上下文还活着
    assert not m.finished


def test_child_block_stop_kills_machine() -> None:
    """child 里 STOP(1) 与主上下文同罪(RunEcl 返回 ZUN_ERROR)。"""
    m = _machine(
        [
            [_instr(0, Op.SET_CHILD_CONTEXT, (0, 1))],
            [_instr(0, Op.STOP)],
        ]
    )
    assert m.step() is False
    assert m.finished


def test_child_block_clear_with_negative_sub() -> None:
    """op135 sub_id<0 = 只释放旧块(EclRunHigh.inl:583-588)。"""
    m = _machine(
        [
            [
                _instr(0, Op.SET_CHILD_CONTEXT, (0, 1)),
                _instr(0, Op.SET_CHILD_CONTEXT, (0, -1)),
            ],
            [_instr(0, Op.NOP)],
        ]
    )
    _step(m)
    assert m._child_blocks[0] is None
    assert not m.finished


# ---- 时刻(op180/181)与 clock 值对象 ----


def test_clock_value_object() -> None:
    """23:00 开局 / 30 分钟每单位 / 上限 12=5:00 / EX 面 2:00 开局。"""
    from touhou.games.th08 import clock as clock_mod

    assert clock_mod.INCIDENT is INCIDENT_ETERNAL_NIGHT  # 日期锚点尊重 ZUN 设定
    c = Th08Clock.for_stage()
    assert (c.units, c.time_of_day, str(c), c.next_day) == (0, (23, 0), "23:00", False)
    assert c.advance()
    assert c.time_of_day == (23, 30)
    c.units = 6
    assert (c.time_of_day, c.next_day) == ((2, 0), True)  # 跨午夜进 9 月 28 日
    for _ in range(20):
        c.advance()
    assert c.units == MAX_UNITS == 12
    assert str(c) == "5:00"
    assert not c.advance()  # 封顶
    ex = Th08Clock.for_extra()
    assert (ex.units, str(ex)) == (6, "2:00")
    # moment: datetime 原生表达(年月日经 gensokyo_time 的 at() 桥提供)
    c2 = Th08Clock.for_stage()
    assert str(c2.moment) == "2004-09-27 23:00:00"
    assert not c2.next_day
    c2.units = MAX_UNITS
    assert str(c2.moment) == "2004-09-28 05:00:00"  # 跨午夜进中秋名月当天
    assert c2.next_day


def test_clock_ops_route_to_host() -> None:
    """op180/181 经 host 路由; Th08GameEclHost 的 advance 封顶 12。"""
    host = _RecHost()
    m = _machine(
        [[_instr(0, Op.ADVANCE_CLOCK), _instr(0, Op.HIDE_CLOCK)]], host=host
    )
    _step(m)
    assert host.calls == [("clock_adv",), ("clock_hide",)]

    th08_host = Th08GameEclHost()
    m2 = _machine([[_instr(0, Op.ADVANCE_CLOCK)]], host=th08_host)
    for _ in range(20):
        _step(m2)  # 指令只在 time 0 跑一帧, 直接调宿主补满
    for _ in range(20):
        th08_host.clock_advance()
    assert th08_host.clock.units == 12  # 封顶(EclRunHigh.inl:957-967)
    th08_host.clock_hide()
    assert th08_host.clock.hidden


# ---- EX 指令路由(op136/137) ----


def test_run_ex_ins_immediate() -> None:
    """op136: 立即跑一次 EX; ex19 把符卡号写进上下文 intVariables[0]
    (EclExIns.cpp:787-791)。"""
    host = Th08GameEclHost()
    host.current_spellcard_number = 77
    m = _machine([[_instr(0, Op.RUN_EX_INS, (19,))]], host=host)
    _step(m)
    assert m.current.args.th08_ints[0] == 77


def test_ex_framerate_divisor() -> None:
    """ex18 SetFrameRateDivisor: value @0x10 = args[4](EclManager.hpp:158-173)。"""
    w = Th08EclWorld()
    host = Th08GameEclHost(w)
    m = _machine([[_instr(0, Op.RUN_EX_INS, (18, 0, 0, 0, 2))]], world=w, host=host)
    _step(m)
    assert w.framerate_multiplier == 0.5
    m2 = _machine([[_instr(0, Op.RUN_EX_INS, (29, 0, 0, 0, 2))]], world=w, host=host)
    _step(m2)
    assert w.framerate_multiplier == 1.0


def test_set_ex_ins_per_frame() -> None:
    """op137: 注册每帧 EX 回调(life>0 时在帧收尾跑, EclRun.cpp:124-126)。"""
    host = Th08GameEclHost()
    enemy = Th08EnemyState(life=100)
    m = _machine([[_instr(0, Op.SET_EX_INS, (30, 0, 0, 0, 42))]], host=host, enemy=enemy)
    _step(m)
    assert host.screen_effect_counter == 42  # 当帧收尾就跑了一次
    _step(m)
    assert host.screen_effect_counter == 42
    assert m.current.ex_instr_idx == 30


# ---- 弹幕/激光(效果类: 断言 host 调用 + 参数解析) ----


def test_shot_pattern_parse_and_host_call() -> None:
    """96-104 九合一: ShotArgs 布局/aim_mode/标志位解析
    (EclDependencies.cpp:687-780)。"""
    host = _RecHost()
    w = Th08EclWorld()
    w.spellcard_active = True  # 跳过 rank 缩放, 钉死原值
    enemy = Th08EnemyState(life=100)
    enemy.pos.set(100.0, 50.0, 0.0)
    enemy.shoot_offset.set(1.0, 2.0, 0.0)
    word0 = 5 | (3 << 16)  # bulletType=5, color=3
    m = _machine(
        [
            [
                _instr(
                    0, Op.SPAWN_BULLET_PATTERN_RING_AIMED,
                    (word0, 4, 2, _f(1.5), _f(0.5), _f(0.25), _f(0.125), 0x40),
                )
            ]
        ],
        world=w,
        host=host,
        enemy=enemy,
    )
    _step(m)
    assert len(host.calls) == 1 and host.calls[0][0] == "shot"
    p = host.calls[0][1]
    assert (p.sprite, p.sprite_offset, p.aim_mode) == (5, 3, 2)  # aim = 98-96
    assert (p.count1, p.count2) == (4, 2)
    assert (p.speed1, p.speed2, p.angle1, p.angle2) == (1.5, 0.5, 0.25, 0.125)
    assert p.flags == 0x40
    assert (p.pos.x, p.pos.y) == (101.0, 52.0)  # worldPosition + shootOffset


def test_shot_operand_mask_and_life_gate() -> None:
    """操作数标志位: count1 走变量(bit2); life<=0 不发弹。"""
    host = _RecHost()
    enemy = Th08EnemyState(life=100)
    word0 = 5 | (3 << 16)
    m = _machine(
        [
            [
                _instr(0, Op.SET_INT, (V.LOCAL_INT0, 9), mask=0b01),
                _instr(
                    0, Op.SPAWN_BULLET_PATTERN_SPREAD_ABS,
                    (word0, V.LOCAL_INT0, 2, _f(1.5), _f(0.5), _f(0.25), _f(0.125), 0),
                    mask=0b100,  # count1 是变量 id
                ),
            ]
        ],
        host=host,
        enemy=enemy,
    )
    m.world.spellcard_active = True
    _step(m)
    assert host.calls[0][1].count1 == 9

    host2 = _RecHost()
    m2 = _machine(
        [[_instr(0, Op.SPAWN_BULLET_PATTERN_SPREAD_ABS, (word0,) + (0,) * 7)]],
        host=host2,
        enemy=Th08EnemyState(life=0),
    )
    _step(m2)
    assert host2.calls == []  # life<=0 门控(EclRunHigh.inl:172-173)


def test_defer_bullet_pattern_and_auto_shoot() -> None:
    """op107 defer: 弹幕指令存 pending(EclRunHigh.inl:174-181);
    自动射击到点重新派发(EclDependencies.cpp:791-802)。"""
    host = _RecHost()
    enemy = Th08EnemyState(life=100)
    word0 = 5 | (3 << 16)
    m = _machine(
        [
            [
                _instr(0, Op.DEFER_BULLET_PATTERN),
                _instr(
                    0, Op.SPAWN_BULLET_PATTERN_RING_ABS,
                    (word0, 4, 2, _f(1.5), _f(0.5), _f(0.25), _f(0.125), 0),
                ),
                _instr(0, Op.SET_SHOOT_INTERVAL, (2,)),
            ]
        ],
        host=host,
        enemy=enemy,
    )
    m.world.spellcard_active = True
    _step(m)
    # 当帧: interval=2 刚到 1, 不 fire; pending 已存
    assert host.calls == []
    assert enemy.pending_shot_instr is not None
    _step(m)  # 计时到 2 → 重新派发 pending
    assert [c[0] for c in host.calls] == ["shot"]
    assert host.calls[0][1].aim_mode == 3  # 99-96


def test_laser_pattern_parse() -> None:
    """op114/115: LaserSpawnArgs 布局(EclRunHigh.inl:53-76) + 句柄入槽。"""
    host = _RecHost()
    enemy = Th08EnemyState(life=100)
    enemy.pos.set(10.0, 20.0, 0.0)
    word0 = 7 | (2 << 16)  # bulletType=7, color=2
    args = (
        word0, _f(0.5), _f(1.0), _f(10.0), _f(20.0), _f(30.0), _f(4.0),
        15, 60, 10, 20, 5, 0x99,
    )
    m = _machine([[_instr(0, Op.SPAWN_LASER_PATTERN, args)]], host=host, enemy=enemy)
    _step(m)
    assert host.calls[0][0] == "laser"
    p = host.calls[0][1]
    assert (p.sprite, p.sprite_offset) == (7, 2)
    assert (p.angle1, p.speed1) == (0.5, 1.0)
    assert (p.start_offset, p.end_offset, p.start_length, p.width) == (10.0, 20.0, 30.0, 4.0)
    assert (p.start_time, p.duration, p.end_time) == (15, 60, 10)
    assert (p.hitbox_start_time, p.hitbox_end_time) == (20, 5)
    assert p.flags == 0x99
    assert p.type == 1  # 114 = BULLET_AIM_FAN(固定)
    assert enemy.lasers[0] is host.laser_handle

    m2 = _machine(
        [[_instr(0, Op.SPAWN_LASER_PATTERN_AIMED, args)]],
        host=_RecHost(),
        enemy=Th08EnemyState(life=100),
    )
    _step(m2)
    assert m2.enemy.laser_props.type == 0  # 115 = FAN_AIMED(出生瞄玩家)


def test_test_laser_in_use_writes_extra_var() -> None:
    """op120: 激光占用写 extraIntVariables[2](EclRunHigh.inl:385-393)。"""
    host = _RecHost()
    enemy = Th08EnemyState(life=100)
    m = _machine([[_instr(0, Op.TEST_LASER_IN_USE, (0,))]], host=host, enemy=enemy)
    _step(m)
    assert m._get_int(V.EXTRA_INT0 + 2) == 0  # 无激光
    enemy.lasers[0] = host.laser_handle
    host.laser_in_use = lambda h: True  # type: ignore[method-assign]
    m2 = _machine([[_instr(0, Op.TEST_LASER_IN_USE, (0,))]], host=host, enemy=enemy)
    _step(m2)
    assert m2._get_int(V.EXTRA_INT0 + 2) == 1


# ---- 使魔/敌生成/符卡/host 路由 ----


def test_spawn_familiar_routing() -> None:
    """op90/91: 音效 0x24 无条件, 生成经 host.spawn_familiar; 91 加父偏移。"""
    host = _RecHost()
    enemy = Th08EnemyState(life=100)
    enemy.pos.set(50.0, 60.0, 0.0)
    m = _machine(
        [
            [
                _instr(0, Op.SPAWN_FAMILIAR, (3, _f(10.0), _f(20.0), 800, -2, 10)),
                _instr(0, Op.SPAWN_FAMILIAR_REL, (4, _f(5.0), _f(6.0), 100, 0, 0)),
            ]
        ],
        host=host,
        enemy=enemy,
    )
    _step(m)
    fam = [c for c in host.calls if c[0] == "familiar"]
    sounds = [c for c in host.calls if c[0] == "sound"]
    assert len(sounds) == 2 and all(c[1] == 0x24 for c in sounds)
    assert fam[0][1:3] == (Op.SPAWN_FAMILIAR, 3)  # kind, sub id(raw)
    assert (fam[0][3].x, fam[0][3].y) == (10.0, 20.0)
    assert fam[0][4:] == (800, -2, 10)
    assert fam[1][1] == Op.SPAWN_FAMILIAR_REL
    assert (fam[1][3].x, fam[1][3].y) == (55.0, 66.0)  # + worldPosition


def test_spawn_enemy_abs_rel() -> None:
    host = _RecHost()
    enemy = Th08EnemyState(life=100)
    enemy.pos.set(50.0, 60.0, 0.0)
    m = _machine(
        [
            [
                _instr(0, Op.SPAWN_ENEMY_ABS, (3, _f(10.0), _f(20.0), _f(0.0), 800, -2, 10)),
                _instr(0, Op.SPAWN_ENEMY_REL, (4, _f(5.0), _f(6.0), _f(0.0), 100, 0, 0)),
            ]
        ],
        host=host,
        enemy=enemy,
    )
    _step(m)
    spawns = [c for c in host.calls if c[0] == "enemy"]
    assert spawns[0][1] == 3 and (spawns[0][2].x, spawns[0][2].y) == (10.0, 20.0)
    assert spawns[0][3:] == (800, -2, 10, 0)
    assert spawns[1][1] == 4 and (spawns[1][2].x, spawns[1][2].y) == (55.0, 66.0)


def test_begin_spellcard_parse() -> None:
    """op122: 符卡名 XOR 0xAA 解码(Spellcard.cpp:743) + 宿主交接。"""
    host = _RecHost()
    enemy = Th08EnemyState(life=100)
    name = "永夜返し".encode("shift_jis")
    encoded = bytes(b ^ 0xAA for b in name).ljust(0x30, b"\xAA")  # NUL 也按 0xAA
    args = [7 | (123 << 16), 5000]  # enemyFace=7, spellCardNumber=123, bonus
    blob = b"".join(struct.pack("<I", a) for a in args) + encoded
    blob = blob.ljust(0xF4, b"\x00")  # 布局全长(EclDependencies.cpp:18-36)
    words = struct.unpack(f"<{len(blob) // 4}I", blob)
    m = _machine(
        [[_instr(0, Op.BEGIN_SPELLCARD, words)]], host=host, enemy=enemy
    )
    _step(m)
    spell = [c for c in host.calls if c[0] == "spell"]
    assert spell == [("spell", 7, 123, "永夜返し")]


def test_boss_and_misc_host_routing() -> None:
    host = _RecHost()
    enemy = Th08EnemyState(life=100)
    m = _machine(
        [
            [
                _instr(0, Op.SET_BOSS, (0,)),
                _instr(0, Op.REMOVE_ALL_ENEMIES),
                _instr(0, Op.SET_BOSS, (-1,)),
            ]
        ],
        host=host,
        enemy=enemy,
    )
    _step(m)
    assert ("boss", 0, True) in host.calls
    assert ("clear_enemies", 8000, 0) in host.calls
    assert ("boss", 0, False) in host.calls
    assert m.world.bosses[0] is None


# ---- 时间轴 runner ----


def _timeline_machine(
    tl: list[bytes], *, world: Th08EclWorld | None = None, host: EclHost | None = None
) -> Th08TimelineRunner:
    ecl = EclFileTh08.parse(_build_ecl([[_instr(0, Op.NOP)]], [tl]))
    return Th08TimelineRunner(ecl, 0, world or Th08EclWorld(), host or _RecHost())


def test_timeline_fixed_spawn() -> None:
    """op0/1 定点生敌(1=镜像 X); 参数映射 EnemyTimeline.cpp:140-152。"""
    host = _RecHost()
    r = _timeline_machine(
        [_tl_instr(0, 0, (3, _f(10.0), _f(20.0), 800, 2, 500, 0))], host=host
    )
    r.step()
    kind, sub, pos, life, item, score, mirror = host.calls[0]
    assert (kind, sub) == ("enemy", 3)
    assert (pos.x, pos.y, pos.z) == (10.0, 20.0, 0.0)
    assert (life, item, score, mirror) == (800, 2, 500, 0)

    host2 = _RecHost()
    r2 = _timeline_machine(
        [_tl_instr(0, 1, (3, _f(10.0), _f(20.0), 800, 2, 500, 0))], host=host2
    )
    r2.step()
    assert host2.calls[0][-1] == 1  # 镜像 X


def test_timeline_spawn_gating() -> None:
    """boss 在场 / op175 抑制 → 跳过(15 强制生敌无门控)。"""
    w = Th08EclWorld()
    w.bosses[0] = Th08EnemyState()
    host = _RecHost()
    r = _timeline_machine(
        [
            _tl_instr(0, 0, (3, _f(1.0), _f(2.0), 100, 0, 0, 0)),
            _tl_instr(0, 15, (4, _f(1.0), _f(2.0), 100, 0, 0, 0)),
        ],
        world=w,
        host=host,
    )
    r.step()
    assert [c[1] for c in host.calls] == [4]  # op0 被门控, op15 照生

    w2 = Th08EclWorld(suppress_timeline_spawns=1)
    host2 = _RecHost()
    r2 = _timeline_machine(
        [_tl_instr(0, 0, (3, _f(1.0), _f(2.0), 100, 0, 0, 0))],
        world=w2,
        host=host2,
    )
    r2.step()
    assert host2.calls == []


def test_timeline_difficulty_filter() -> None:
    """难度掩码过滤(EnemyTimeline.cpp:131-132): 不含当前难度位则跳过。"""
    host = _RecHost()
    r = _timeline_machine(
        [_tl_instr(0, 0, (3, _f(1.0), _f(2.0), 100, 0, 0, 0), diff=0x01)],
        world=Th08EclWorld(difficulty=1),  # N = 0x02, 不在掩码里
        host=host,
    )
    r.step()
    assert host.calls == []
    assert r.idx == 1  # 指令已翻过(跳过≠停轴)


def test_timeline_random_spawns() -> None:
    """op2 x 区间随机 / op3 全屏随机 x。"""
    host = _RecHost()
    r = _timeline_machine(
        [_tl_instr(0, 2, (3, _f(50.0), _f(150.0), _f(20.0), 800, 2, 500))],
        host=host,
    )
    r.step()
    pos = host.calls[0][2]
    assert 50.0 <= pos.x <= 150.0 and pos.y == 20.0

    host2 = _RecHost()
    r2 = _timeline_machine(
        [_tl_instr(0, 3, (3, _f(20.0), 800, 2, 500, 0, 0))], host=host2
    )
    r2.step()
    pos2 = host2.calls[0][2]
    assert 0.0 <= pos2.x <= 384.0 and pos2.y == 20.0
    assert host2.calls[0][3:] == (800, 2, 500, 0)


def test_timeline_msg_wait_and_boss_wait() -> None:
    """op7 MsgWait 等待期间时间轴停住; op10 等 boss 死同理。"""
    host = _RecHost()
    host.msg_wait = lambda: True  # type: ignore[method-assign]
    r = _timeline_machine([_tl_instr(0, 7, (0,) * 7)], host=host)
    r.step()
    assert (r.time, r.idx) == (0, 0)  # time-- 抵消 time++, 停轴
    host.msg_wait = lambda: False  # type: ignore[method-assign]
    r.step()
    assert (r.time, r.idx) == (1, 1)

    w = Th08EclWorld()
    w.bosses[1] = Th08EnemyState(active=1)
    host2 = _RecHost()
    r2 = _timeline_machine([_tl_instr(0, 10, (1,) + (0,) * 6)], world=w, host=host2)
    r2.step()
    assert (r2.time, r2.idx) == (0, 0)
    w.bosses[1] = None
    r2.step()
    assert (r2.time, r2.idx) == (1, 1)


def test_timeline_boss_pending_power_retry() -> None:
    """op8 boss pendingSub / op9 SetPower / op16 Retry 菜单。"""
    w = Th08EclWorld()
    boss = Th08EnemyState()
    w.bosses[0] = boss
    host = _RecHost()
    r = _timeline_machine(
        [
            _tl_instr(0, 8, (0, 2) + (0,) * 5),
            _tl_instr(0, 9, (128,) + (0,) * 6),
            _tl_instr(0, 16, (0,) * 7),
        ],
        world=w,
        host=host,
    )
    r.step()
    assert boss.run_interrupt == 2
    assert ("power", 128) in host.calls
    assert ("retry",) in host.calls


def test_timeline_event_slots() -> None:
    """op13/14 事件槽同步(EnemyTimeline.cpp:253-282): 14 填空槽, 13 等匹配。"""
    w = Th08EclWorld()
    host = _RecHost()
    r = _timeline_machine(
        [_tl_instr(0, 14, (7,) + (0,) * 6)], world=w, host=host
    )
    r.step()
    assert w.timeline_event_slots == [7, 7, 7, 7]

    r2 = _timeline_machine(
        [_tl_instr(0, 13, (7,) + (0,) * 6)], world=w, host=host
    )
    r2.step()
    assert (r2.time, r2.idx) == (1, 1)  # 有匹配 → 清掉并通过
    assert w.timeline_event_slots == [-1, -1, -1, -1]

    r3 = _timeline_machine(
        [_tl_instr(0, 13, (9,) + (0,) * 6)], world=w, host=host
    )
    r3.step()
    assert (r3.time, r3.idx) == (0, 0)  # 无匹配 → 停轴等


def test_timeline_drop_count_spawn() -> None:
    """op11/12 带掉落数: 掉到新敌的 point/powerOrPoint 字段
    (EnemyTimeline.cpp:165-185); stub 返回 None 时不炸。"""

    class _SpawnHost(_RecHost):
        class _Spawned:
            def __init__(self) -> None:
                self.state = Th08EnemyState()

        def spawn_enemy(self, sub_id, pos, life, item_drop, score, mirror, context_args):
            super().spawn_enemy(sub_id, pos, life, item_drop, score, mirror, context_args)
            self.last = self._Spawned()
            return self.last

    host = _SpawnHost()
    r = _timeline_machine(
        [_tl_instr(0, 11, (3, _f(10.0), _f(20.0), 800, 4, 5, 999))], host=host
    )
    r.step()
    kind, sub, pos, life, item, score, mirror = host.calls[0]
    assert (sub, life, item, score, mirror) == (3, 800, -1, 999, 0)
    assert host.last.state.point_item_drop_count == 4
    assert host.last.state.power_or_point_item_drop_count == 5


# ---- 真实数据 smoke ----


@needs_data
def test_real_ecldata1_all_subs_smoke() -> None:
    """真实 ecldata1.ecl 全 sub: start + 逐帧 step 有限帧不炸(no-op host)。"""
    arc = open_archive(TH08_DAT, game="th08")
    ecl = EclFileTh08.parse(try_decrypt_from_table(arc.load("ecldata1.ecl")))
    stepped = 0
    for sub_id in range(ecl.sub_count):
        enemy = Th08EnemyState(life=100)
        m = EclMachineTh08(ecl, enemy=enemy)  # 默认 world/host 全 no-op
        m.start(sub_id)
        for _ in range(120):
            if not m.step():
                break
        stepped += 1
    assert stepped == ecl.sub_count


@needs_data
def test_real_ecldata1_timelines_smoke() -> None:
    """真实 ecldata1.ecl 全时间轴: 逐帧 step 有限帧不炸(stub host)。"""
    arc = open_archive(TH08_DAT, game="th08")
    ecl = EclFileTh08.parse(try_decrypt_from_table(arc.load("ecldata1.ecl")))
    assert ecl.timeline_count > 0
    for i in range(ecl.timeline_count):
        host = _RecHost()
        r = Th08TimelineRunner(ecl, i, Th08EclWorld(), host)
        for _ in range(200):
            r.step()
