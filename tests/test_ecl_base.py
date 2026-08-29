"""EclMachineBase 框架测试: 裸基类 + 测试内最小 VM(TinyMachine)。

覆盖: step 主循环/wait timer、SUB_CALL/SUB_RET 调用栈、strict 未实现指令、
未命中走宿主钩子、trace、handler 表继承隔离(子类注册不污染父类/兄弟类)、
变量系统 stub 的 NotImplementedError、th07 全 opcode 注册完整性。
"""

from __future__ import annotations

import sys

import pytest

sys.path.insert(0, r"D:\python_play\Touhou08")

from touhou.engine.ecl import EclFile, EclHost, EclOpcode  # noqa: E402
from touhou.engine.ecl_base import EclMachineBase  # noqa: E402
from touhou.exceptions import NotImplementedEclError  # noqa: E402
from touhou.games.th07.ecl_vm import EclMachineTh07, Th07EclOpcode  # noqa: E402
from tests.test_ecl import _instr, build_ecl  # noqa: E402

OP = EclOpcode


class TinyMachine(EclMachineBase):
    """测试用最小 VM: 变量系统按立即数直传(不落变量表的指令也能跑)。"""

    def _get_int(self, var_id: int) -> int:
        return var_id

    def _set_int(self, var_id: int, value: int) -> None:
        pass

    def _get_float(self, var_id: int) -> float:
        return float(var_id)

    def _set_float(self, var_id: int, value: float) -> None:
        pass


@TinyMachine.register(0)
def _tiny_noop(m: TinyMachine, instr):
    return None


@TinyMachine.register(OP.SET_WAIT_TIMER)
def _tiny_wait(m: TinyMachine, instr):
    m.current.wait_timer = m._int_arg(instr, 0)


@TinyMachine.register(OP.JUMP)
def _tiny_jump(m: TinyMachine, instr):
    return m._do_jump(instr, instr.arg_int(0), instr.arg_int(1))


@TinyMachine.register(OP.SUB_CALL)
def _tiny_call(m: TinyMachine, instr):
    m.current.instr_offset = instr.offset + instr.size
    m._push_context()
    m.call_sub(instr.arg_int(0))
    return "restart"


@TinyMachine.register(OP.SUB_RET)
def _tiny_ret(m: TinyMachine, instr):
    if not m.stack:
        return "error"
    m.current = m.stack.pop()
    return "restart"


@TinyMachine.register(OP.UNIMP)
def _tiny_unimp(m: TinyMachine, instr):
    return "error"


class _SpyHost(EclHost):
    def __init__(self) -> None:
        self.unhandled: list[int] = []

    def on_unhandled_opcode(self, machine, instr) -> None:
        self.unhandled.append(instr.id)


# ---- 框架主循环: wait timer + 调用栈 ----


def _program() -> EclFile:
    """sub0: t0 WAIT(2) → t3 CALL sub1 → t6 JUMP t9 → t9 UNIMP; sub1: t0 noop → t1 RET。"""
    return build_ecl(
        [
            _instr(0, OP.SET_WAIT_TIMER, (2,)),
            _instr(3, OP.SUB_CALL, (1,)),
            _instr(6, OP.JUMP, (9, 20)),  # byte_offset 相对本条: 20 = 下一条
            _instr(9, OP.UNIMP),
        ],
        [_instr(0, 0), _instr(1, OP.SUB_RET)],
    )


def test_step_wait_timer_and_call_stack() -> None:
    """逐帧跑小程序: trace 精确还原 wait 延迟 + call/ret + jump 的执行序列。"""
    m = TinyMachine(_program())
    m.enemy.life = 10
    m.start(0)
    m.trace = []
    for _ in range(9):
        assert m.step() is True
    # 第 10 帧: JUMP 到 t9 的 UNIMP → 脚本结束
    assert m.step() is False and m.finished
    assert m.trace == [OP.SET_WAIT_TIMER, OP.SUB_CALL, 0, OP.SUB_RET, OP.JUMP, OP.UNIMP]
    assert m.stack == [] and m.current.sub_id == 0  # 栈已平衡弹回


def test_sub_ret_underflow_is_error() -> None:
    """空栈 SUB_RET → "error" → step False。"""
    m = TinyMachine(build_ecl([_instr(0, OP.SUB_RET)]))
    m.start(0)
    assert m.step() is False


# ---- 未命中分发 ----


def test_unhandled_strict_raises() -> None:
    m = TinyMachine(build_ecl([_instr(0, 777)]), strict=True)
    m.start(0)
    with pytest.raises(NotImplementedEclError):
        m.step()


def test_unhandled_goes_to_host_hook() -> None:
    host = _SpyHost()
    m = TinyMachine(build_ecl([_instr(0, 777)]), host=host)
    m.start(0)
    m.step()
    assert host.unhandled == [777]


# ---- 变量系统 stub ----


def test_base_var_stubs_raise() -> None:
    m = EclMachineBase(build_ecl([_instr(0, 0)]))
    with pytest.raises(NotImplementedError):
        m._get_int(10000)
    with pytest.raises(NotImplementedError):
        m._set_int(10000, 1)
    with pytest.raises(NotImplementedError):
        m._get_float(10018)
    with pytest.raises(NotImplementedError):
        m._set_float(10018, 1.0)


# ---- handler 表继承隔离 ----


def test_handler_table_isolation() -> None:
    """子类注册不污染父类/基类/兄弟作品 VM。"""
    assert EclMachineBase._handlers == {}  # 裸基类永远空表
    assert OP.SET_WAIT_TIMER in TinyMachine._handlers

    class TinyMachine2(TinyMachine):
        pass

    @TinyMachine2.register(901)
    def _tiny2_marker(m, instr):
        return None

    assert 901 in TinyMachine2._handlers  # 自身注册生效
    assert OP.SET_WAIT_TIMER in TinyMachine2._handlers  # 继承父类表
    assert 901 not in TinyMachine._handlers  # 不污染父类
    assert 901 not in EclMachineBase._handlers  # 不污染基类
    # 兄弟隔离: th07 的弹幕 9 合一(64..72) 不在 Tiny 系, Tiny 的记号不在 th07
    assert 64 not in TinyMachine._handlers
    assert 901 not in EclMachineTh07._handlers


def test_th07_all_opcodes_registered() -> None:
    """EclOpcode(通用) + Th07EclOpcode(作品专属) 全部在 EclMachineTh07
    都有 handler(迁移完整性守卫)。"""
    missing = [
        op.name
        for op in (*EclOpcode, *Th07EclOpcode)
        if int(op) not in EclMachineTh07._handlers
    ]
    assert not missing, missing
    # 编译器生成的无操作标记也在(0 = 时间同步点, 141 = C 枚举跳号)
    assert 0 in EclMachineTh07._handlers and 141 in EclMachineTh07._handlers
