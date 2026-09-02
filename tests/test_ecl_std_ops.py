"""engine/ecl_std_ops.py 共享核心 handler 的行为钉(只 import engine, 不碰 games.*)。

TinyMachine 按 th08 编号表(EclRunLow.inl:223-422)注册共享核, 顺带钉住
"共享核按 int 编号参数化"这一前提(th07 用 EclOpcode 表注册同一批 handler
的回归钉见 game_test/th07 全套 ECL 测试)。
"""

from __future__ import annotations

import math
import struct

import pytest

from touhou.engine.ecl import EclFile
from touhou.engine.ecl_base import EclMachineBase
from touhou.engine.ecl_std_ops import CoreOps, register_core_ops

# th08 的核心编号(对照结论见 ecl_std_ops 模块 docstring: 仅 1 号与 th07 同号)
_TINY_OPS = CoreOps(
    unimp=1,
    nop=(3,),
    wait_timer=2,
    jump=4,
    dec_jump=5,
    set_int=6,
    set_float=7,
    rand_sign=8,
    rand_sign_float=9,
    int_arith=(20, 21, 22, 23, 24),  # +,-,*,/,%
    float_arith=(25, 26, 27, 28, 29),
    inc=30,
    dec=31,
    sin=32,
    cos=33,
    atan2=34,
    lerp=35,
    init_interp=36,
    normalize_angle=37,
    cond_jumps=(40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51),
    sub_call=52,
    sub_ret=53,
)


class TinyMachine(EclMachineBase):
    """测试用最小 VM: 变量系统 = 两个字典(局部变量表的最小形态)。"""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.int_vars: dict[int, int] = {}
        self.float_vars: dict[int, float] = {}

    def _get_int(self, var_id: int) -> int:
        return self.int_vars.get(var_id, 0)

    def _set_int(self, var_id: int, value: int) -> None:
        self.int_vars[var_id] = value

    def _get_float(self, var_id: int) -> float:
        return self.float_vars.get(var_id, 0.0)

    def _set_float(self, var_id: int, value: float) -> None:
        self.float_vars[var_id] = value


register_core_ops(TinyMachine, _TINY_OPS)


# ---- 手工构造 .ecl 二进制(engine EclFile 布局) ----


def _f(x: float) -> int:
    """float → u32 位型。"""
    return struct.unpack("<I", struct.pack("<f", x))[0]


def _instr(time: int, op: int, args: tuple = (), mask: int = 0, skip: int = 0xFF) -> bytes:
    size = 12 + 4 * len(args)
    return struct.pack("<IhhBBH", time, op, size, 0, skip, mask) + b"".join(
        struct.pack("<I", a & 0xFFFFFFFF) for a in args
    )


def _build_ecl(*subs: list[bytes]) -> EclFile:
    """把若干 sub(每条一串指令字节)拼成合法 .ecl 并解析。"""
    n = len(subs)
    header_size = 4 + 64 + 4 * n
    offsets, blobs = [], []
    off = header_size
    for s in subs:
        offsets.append(off)
        blob = b"".join(s) + _instr(0xFFFFFFFF, -1)
        blobs.append(blob)
        off += len(blob)
    header = (
        struct.pack("<hh", n, 0)
        + struct.pack("<16i", *([0] * 16))
        + struct.pack(f"<{n}i", *offsets)
    )
    return EclFile.parse(header + b"".join(blobs))


def _run(program: EclFile, frames: int = 1) -> TinyMachine:
    m = TinyMachine(program)
    m.start(0)
    for _ in range(frames):
        if not m.step():
            break
    return m


# ---- 注册形态 ----


def test_register_does_not_pollute_base() -> None:
    """共享核登记到子类, 基类 handler 表保持空(类隔离契约)。"""
    assert EclMachineBase._handlers == {}
    for op in (1, 4, 20, 40, 52):
        assert op in TinyMachine._handlers


# ---- mov / 算术 ----


def test_mov_and_int_arith() -> None:
    """SET_INT + 3 操作数 int 算术(+-*/%), 含除/模零保护。"""
    m = _run(
        _build_ecl(
            [
                _instr(0, 6, (100, 7), mask=0b01),  # SET_INT v100 = 7
                _instr(0, 20, (100, 100, 35), mask=0b011),  # ADD v100 = v100 + 35
                _instr(0, 21, (101, 100, 2), mask=0b011),  # SUB v101 = v100 - 2
                _instr(0, 22, (102, 101, 6), mask=0b011),  # MUL v102 = v101 * 6
                _instr(0, 23, (103, 102, -7), mask=0b011),  # DIV v103 = v102 / -7
                _instr(0, 24, (104, 102, 7), mask=0b011),  # MOD v104 = v102 % 7
                _instr(0, 23, (105, 100, 0), mask=0b011),  # DIV 除零 → 0
                _instr(0, 24, (106, 100, 0), mask=0b011),  # MOD 模零 → 0
            ]
        )
    )
    assert m.int_vars[100] == 42
    assert m.int_vars[101] == 40
    assert m.int_vars[102] == 240
    assert m.int_vars[103] == -34  # cdiv: C 截断语义 240/-7 = -34
    assert m.int_vars[104] == 2
    assert m.int_vars[105] == 0 and m.int_vars[106] == 0


def test_inc_dec() -> None:
    m = _run(
        _build_ecl(
            [
                _instr(0, 6, (100, 10), mask=0b01),
                _instr(0, 30, (100,), mask=0b1),  # INC
                _instr(0, 30, (100,), mask=0b1),
                _instr(0, 31, (100,), mask=0b1),  # DEC
            ]
        )
    )
    assert m.int_vars[100] == 11


def test_float_arith_and_math() -> None:
    """float 算术 + sin/cos/atan2/lerp/normalize_angle。"""
    m = _run(
        _build_ecl(
            [
                _instr(0, 7, (_f(200.0), _f(1.5)), mask=0b01),  # SET_FLOAT
                _instr(0, 25, (_f(201.0), _f(200.0), _f(2.5)), mask=0b011),  # ADD_F
                _instr(0, 28, (_f(202.0), _f(1.0), _f(0.0)), mask=0b011),  # 除零→0
                _instr(0, 32, (_f(203.0), _f(0.0)), mask=0b01),  # SIN 0 = 0
                _instr(0, 33, (_f(204.0), _f(0.0)), mask=0b01),  # COS 0 = 1
                # ATAN2: atan2(arg4-arg2, arg3-arg1) = atan2(1, 1)
                _instr(0, 34, (_f(205.0), _f(0.0), _f(0.0), _f(1.0), _f(1.0)), mask=1),
                # LERP: (arg1-arg2)*arg3 + arg2 = (10-4)*0.5+4
                _instr(0, 35, (_f(206.0), _f(10.0), _f(4.0), _f(0.5)), mask=1),
            ]
        )
    )
    assert m.float_vars[200] == 1.5
    assert m.float_vars[201] == 4.0
    assert m.float_vars[202] == 0.0
    assert m.float_vars[203] == 0.0 and m.float_vars[204] == 1.0
    assert math.isclose(m.float_vars[205], math.pi / 4)
    assert m.float_vars[206] == 7.0


def test_normalize_angle() -> None:
    """normalize_angle: 读 arg0 写 arg0(就地), 归一到 [-π, π)。"""
    m = _run(
        _build_ecl(
            [
                _instr(0, 7, (_f(200.0), _f(3.5 * math.pi)), mask=0b01),
                _instr(0, 37, (_f(200.0),), mask=1),
            ]
        )
    )
    # f32 精度链(f32 存参 + add_normalize_angle 的 f32 运算), 放宽容差
    assert m.float_vars[200] == pytest.approx(-0.5 * math.pi, abs=1e-6)


def test_rand_sign() -> None:
    """rand-sign: 绝对值 = 操作数, 符号随机(只钉绝对值)。"""
    m = _run(
        _build_ecl(
            [
                _instr(0, 8, (100, 5), mask=0b01),
                _instr(0, 9, (_f(200.0), _f(2.5)), mask=0b01),
            ]
        )
    )
    assert abs(m.int_vars[100]) == 5
    assert abs(m.float_vars[200]) == 2.5


# ---- 跳转 / 条件跳 ----


def test_jump_and_dec_jump_loop() -> None:
    """dec-jump 循环: counter 归零前跳回, 归零后顺序前进到 UNIMP。"""
    # 布局: i0 SET_INT(20B)@72, i1 INC(16B)@92, i2 DEC_JUMP(24B)@108, i3 UNIMP@132
    m = _run(
        _build_ecl(
            [
                _instr(0, 6, (100, 2), mask=0b01),  # counter = 2
                _instr(0, 30, (101,), mask=0b1),  # INC v101
                _instr(0, 5, (0, 92 - 108, 100), mask=0b100),  # DEC_JUMP 回 i1
                _instr(0, 1),  # UNIMP
            ]
        )
    )
    assert m.finished  # UNIMP → "error" → 脚本结束
    assert m.int_vars[101] == 2 and m.int_vars[100] == 0


def test_cond_jump_int_taken_and_not_taken() -> None:
    """JUMP_IF_EQ(int) 成立跳走 / JUMP_IF_LT(int) 不成立顺序前进。"""
    # i0 SET_INT(20B)@72, i1 JEQ(28B)@92 → i3@140, i2 SET_INT(20B)@120,
    # i3 JLT(28B)@140 不成立, i4 SET_INT(20B)@168, i5 UNIMP@188
    m = _run(
        _build_ecl(
            [
                _instr(0, 6, (100, 42), mask=0b01),
                _instr(0, 40, (100, 42, 0, 140 - 92), mask=0b001),  # EQ 成立 → i3
                _instr(0, 6, (101, 1), mask=0b01),  # 应被跳过
                _instr(0, 44, (100, 42, 0, 0), mask=0b001),  # LT 不成立
                _instr(0, 6, (102, 1), mask=0b01),
                _instr(0, 1),
            ]
        )
    )
    assert 101 not in m.int_vars
    assert m.int_vars[102] == 1


def test_cond_jump_float() -> None:
    """JUMP_IF_EQ_FLOAT: float 对子走 float 比较。"""
    # i0 JEQ_F(28B)@72 成立 → i2@120; i1 SET_INT(20B)@100 被跳过; i2 UNIMP@120
    m = _run(
        _build_ecl(
            [
                _instr(0, 41, (_f(1.5), _f(1.5), 0, 120 - 72)),
                _instr(0, 6, (100, 1), mask=0b01),
                _instr(0, 1),
            ]
        )
    )
    assert m.finished and 100 not in m.int_vars


# ---- call / ret / wait ----


def test_call_ret_and_wait_timer() -> None:
    """逐帧跑小程序: trace 精确还原 wait 延迟 + call/ret + jump 的执行序列。"""
    # sub0(2 sub 头=76): WAIT(16B)@76, CALL(16B)@92, JUMP(20B)@108, UNIMP@128
    # sub1@140: nop(12B)@140, RET(12B)@152
    program = _build_ecl(
        [
            _instr(0, 2, (2,)),  # WAIT 2
            _instr(3, 52, (1,)),  # CALL sub1
            _instr(6, 4, (9, 128 - 108)),  # JUMP t9
            _instr(9, 1),  # UNIMP
        ],
        [_instr(0, 3), _instr(1, 53)],  # nop; RET
    )
    m = TinyMachine(program)
    m.start(0)
    m.trace = []
    for _ in range(9):
        assert m.step() is True
    assert m.step() is False and m.finished  # 第 10 帧: JUMP 到 UNIMP
    assert m.trace == [2, 52, 3, 53, 4, 1]
    assert m.stack == [] and m.current.sub_id == 0  # 栈已平衡弹回


def test_init_interp_slot() -> None:
    """init_interp: 填第一个可用插值槽(目标变量 id 以 f32 位型存放)。"""
    m = _run(
        _build_ecl(
            [
                _instr(
                    0,
                    36,
                    (_f(12345.0), 60, 0, 1, _f(0.0), _f(1.0), _f(0.0), _f(0.0)),
                ),
            ]
        )
    )
    slot = m.current.interps[0]
    assert slot.active and slot.target_var == 12345
    assert slot.duration == 60 and slot.easing == 1
    assert slot.params == [0.0, 1.0, 0.0, 0.0]
