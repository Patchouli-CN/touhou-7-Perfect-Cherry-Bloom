"""作品无关的 ECL 核心指令 handler —— 各作品 VM 共享的指令集内核。

从 games/th07/ecl_vm.py 逐字迁移(只改 import 不改逻辑; 原 th07 模块级
``@EclMachineTh07.register`` 装饰器形态改为 ``register_core_ops`` 内的等价登记),
覆盖: stop/wait/nop、跳转/条件跳、call/ret、mov、rand-sign、3 操作数算术
(dict 查表)、inc/dec、sin/cos/atan2、lerp、init_interp、normalize_angle。
作品专属指令(th07 的 RAND 系 6-9、GET_BOSS 系 43/44 等)不在本模块。

th07 ↔ th08 核心指令编号对照(出处: th07 = engine/ecl.py EclOpcode 对照
th07 EclManager.cpp; th08 = Reference/th08-ref/src/EclRunLow.inl:223-422,
跳转时间/位移用 raw 操作数见 EclRunLow.inl:16-20):

===========================  ================  ================
语义                          th07              th08
===========================  ================  ================
stop(脚本结束)                1 UNIMP           1 (return ZUN_ERROR)
wait                          45 SET_WAIT_TIMER 2 (secondaryTime)
nop                           0 (+141 填充)     3 (另 84/85 同路)
jump                          2 JUMP            4
dec-jump                      3 DEC_JUMP        5
mov int/float                 4/5               6/7
rand-sign int/float           10/11             8/9
2 操作数算术 int/float        — (th07 无)       10-14/15-19
3 操作数算术 int (+-*/%)      12-16             20-24 (同序)
3 操作数算术 float            19-23             25-29 (同序)
inc/dec                       17/18             30/31
sin/cos/atan2                 24/25/26          32/33/34
lerp                          159 LERP          35 (公式逐字相同,
                                                EclDependencies.cpp:279-291)
init_interp                   27 INIT_INTERP    36 (8 参布局相同,
                                                EclDependencies.cpp:350-377)
normalize_angle               40                37
polar(cos/sin×mag)            151               38
dist                          — (th07 无)       39
条件跳 12 条(==/!=/</<=/>/>=  28-39             40-51 (同序交错)
  int/float 交错)
call/ret                      41/42             52/53
rand 系(纯随机 4 条)          6-9               — (th08 随机走变量)
GET_BOSS int/float            43/44             — (th08 无对应)
===========================  ================  ================

**结论: 仅 1 号(stop)同号同义, 其余全部错位(系统性重排), 且两作集合不完全
重合。** 故共享核按 int 编号参数化: 作品侧提供 ``CoreOps`` 编号表,
``register_core_ops(machine_cls, ops)`` 把同一批 handler 登记到作品 VM 类上。
th08 独有的 2 操作数算术(10-19)/polar(38)/dist(39)不进共享核, 由 th08 VM
侧自行登记(th07 的 polar=151 同理留在 th07 侧)。
"""

from __future__ import annotations

import math
import msgspec
from typing import TYPE_CHECKING, Callable

from ..logger import logger as log
from ..utils import add_normalize_angle, cdiv, cmod

if TYPE_CHECKING:
    from .ecl import EclInstr  # 仅类型检查期
    from .ecl_base import EclMachineBase  # 仅类型检查期(ecl_base 不依赖本模块)


class CoreOps(msgspec.Struct, frozen=True):
    """作品无关核心指令的编号表(语义槽位 → 作品的 opcode int)。

    th07/th08 编号系统性错位(见模块 docstring 对照表), 故共享核按 int 参数化。
    """

    unimp: int
    nop: tuple[int, ...]  # 无操作 opcode(可有多个填充号)
    wait_timer: int
    jump: int
    dec_jump: int
    set_int: int
    set_float: int
    rand_sign: int
    rand_sign_float: int
    int_arith: tuple[int, int, int, int, int]  # +,-,*,/,% (3 操作数: dest,a,b)
    float_arith: tuple[int, int, int, int, int]
    inc: int
    dec: int
    sin: int
    cos: int
    atan2: int
    lerp: int
    init_interp: int
    normalize_angle: int
    # 条件跳 12 条, 序: EQ,EQ_F,NEQ,NEQ_F,LT,LT_F,LEQ,LEQ_F,GT,GT_F,GEQ,GEQ_F
    cond_jumps: tuple[int, ...]
    sub_call: int
    sub_ret: int


def _compare(cond_jumps: tuple[int, ...], op: int, a: float, b: float) -> bool:
    """条件跳比较: cond_jumps 按 ==/!=/</<=/>/>= 的 int/float 交错序(对子同义)。"""
    kind = cond_jumps.index(op) // 2
    if kind == 0:
        return a == b
    if kind == 1:
        return a != b
    if kind == 2:
        return a < b
    if kind == 3:
        return a <= b
    if kind == 4:
        return a > b
    return a >= b


def _init_interp(m: EclMachineBase, instr: EclInstr) -> None:
    """INIT_INTERP 的填槽逻辑(th07/th08 的 8 参布局相同, 见模块 docstring)。"""
    ctx = m.current
    target = int(instr.arg_float(0))  # f32 值形式存的变量 id
    for it in ctx.interps:
        if it.active and it.target_var != target:
            continue
        it.active = True
        it.timer = 0
        it.target_var = target
        it.duration = m._int_arg(instr, 1)
        it.func_idx = m._int_arg(instr, 2)
        it.easing = m._int_arg(instr, 3)
        it.params = [
            m._float_arg(instr, 4),
            m._float_arg(instr, 5),
            m._float_arg(instr, 6),
            m._float_arg(instr, 7),
        ]
        break


# 算术四则(+取模) 5 合一(与 CoreOps.int_arith/float_arith 同序: +,-,*,/,%)
_INT_BINOPS = (
    lambda a, b: a + b,
    lambda a, b: a - b,
    lambda a, b: a * b,
    lambda a, b: cdiv(a, b) if b else 0,
    lambda a, b: cmod(a, b) if b else 0,
)
_FLOAT_BINOPS = (
    lambda a, b: a + b,
    lambda a, b: a - b,
    lambda a, b: a * b,
    lambda a, b: a / b if b != 0.0 else 0.0,
    lambda a, b: math.fmod(a, b) if b != 0.0 else 0.0,
)


def register_core_ops(machine_cls: type[EclMachineBase], ops: CoreOps) -> None:
    """把共享核心 handler 按 ops 编号登记到 machine_cls(行为与作品无关)。

    handler 返回值契约同 ``EclMachineBase._execute``:
    None=顺序前进 / EclInstr=跳转目标 / "restart"=重取指令 / "error"=脚本结束。
    """
    _INT_BINOP: dict[int, Callable[[int, int], int]] = dict(
        zip(ops.int_arith, _INT_BINOPS)
    )
    _FLOAT_BINOP: dict[int, Callable[[float, float], float]] = dict(
        zip(ops.float_arith, _FLOAT_BINOPS)
    )
    _INC_DEC: dict[int, int] = {ops.inc: 1, ops.dec: -1}

    @machine_cls.register(ops.unimp)
    def _op_unimp(m: EclMachineBase, instr: EclInstr):
        return "error"  # RunEcl 直接返回错误(= 脚本结束/despawn)

    @machine_cls.register(ops.nop)
    def _op_noop(m: EclMachineBase, instr: EclInstr):
        return None

    @machine_cls.register(ops.wait_timer)
    def _op_set_wait_timer(m: EclMachineBase, instr: EclInstr):
        m.current.wait_timer = m._int_arg(instr, 0)

    @machine_cls.register(ops.dec_jump)
    def _op_dec_jump(m: EclMachineBase, instr: EclInstr):
        t = m._int_target(instr, 2)
        if t is not None:
            m._set_int(t, m._get_int(t) - 1)
        if m._int_arg(instr, 2) <= 0:
            return None  # 顺序前进
        return m._do_jump(instr, instr.arg_int(0), instr.arg_int(1))

    @machine_cls.register(ops.jump)
    def _op_jump(m: EclMachineBase, instr: EclInstr):
        return m._do_jump(instr, instr.arg_int(0), instr.arg_int(1))

    @machine_cls.register(ops.set_int)
    def _op_set_int(m: EclMachineBase, instr: EclInstr):
        m._store_int(instr, 0, m._int_arg(instr, 1))

    @machine_cls.register(ops.set_float)
    def _op_set_float(m: EclMachineBase, instr: EclInstr):
        m._store_float(instr, 0, m._float_arg(instr, 1))

    @machine_cls.register(ops.normalize_angle)
    def _op_normalize_angle(m: EclMachineBase, instr: EclInstr):
        m._store_float(instr, 0, add_normalize_angle(m._float_arg(instr, 0), 0.0))

    @machine_cls.register(ops.rand_sign)
    def _op_rand_sign(m: EclMachineBase, instr: EclInstr):
        m._store_int(instr, 0, m.world.rng.sign() * m._int_arg(instr, 1))

    @machine_cls.register(ops.rand_sign_float)
    def _op_rand_sign_float(m: EclMachineBase, instr: EclInstr):
        m._store_float(instr, 0, float(m.world.rng.sign()) * m._float_arg(instr, 1))

    @machine_cls.register(tuple(_INC_DEC))
    def _op_inc_dec(m: EclMachineBase, instr: EclInstr):
        t = m._int_target(instr, 0)
        if t is not None:
            m._set_int(t, m._get_int(t) + _INC_DEC[instr.id])

    # 算术四则(+取模) 5 合一: int 版
    @machine_cls.register(ops.int_arith)
    def _op_int_arith(m: EclMachineBase, instr: EclInstr):
        m._store_int(
            instr, 0, _INT_BINOP[instr.id](m._int_arg(instr, 1), m._int_arg(instr, 2))
        )

    # 算术四则(+取模) 5 合一: float 版
    @machine_cls.register(ops.float_arith)
    def _op_float_arith(m: EclMachineBase, instr: EclInstr):
        m._store_float(
            instr,
            0,
            _FLOAT_BINOP[instr.id](m._float_arg(instr, 1), m._float_arg(instr, 2)),
        )

    @machine_cls.register(ops.sin)
    def _op_sin(m: EclMachineBase, instr: EclInstr):
        m._store_float(instr, 0, math.sin(m._float_arg(instr, 1)))

    @machine_cls.register(ops.cos)
    def _op_cos(m: EclMachineBase, instr: EclInstr):
        m._store_float(instr, 0, math.cos(m._float_arg(instr, 1)))

    @machine_cls.register(ops.atan2)
    def _op_atan2(m: EclMachineBase, instr: EclInstr):
        m._store_float(
            instr,
            0,
            math.atan2(
                m._float_arg(instr, 4) - m._float_arg(instr, 2),
                m._float_arg(instr, 3) - m._float_arg(instr, 1),
            ),
        )

    @machine_cls.register(ops.lerp)
    def _op_lerp(m: EclMachineBase, instr: EclInstr):
        delta = m._float_arg(instr, 1) - m._float_arg(instr, 2)
        m._store_float(
            instr, 0, delta * m._float_arg(instr, 3) + m._float_arg(instr, 2)
        )

    @machine_cls.register(ops.init_interp)
    def _op_init_interp(m: EclMachineBase, instr: EclInstr):
        _init_interp(m, instr)

    # 条件跳转 6 合一: int 版
    @machine_cls.register(ops.cond_jumps[0::2])
    def _op_jump_if_int(m: EclMachineBase, instr: EclInstr):
        a, b = m._int_arg(instr, 0), m._int_arg(instr, 1)
        if _compare(ops.cond_jumps, instr.id, a, b):
            return m._do_jump(instr, instr.arg_int(2), instr.arg_int(3))
        return None

    # 条件跳转 6 合一: float 版
    @machine_cls.register(ops.cond_jumps[1::2])
    def _op_jump_if_float(m: EclMachineBase, instr: EclInstr):
        fa, fb = m._float_arg(instr, 0), m._float_arg(instr, 1)
        if _compare(ops.cond_jumps, instr.id, fa, fb):
            return m._do_jump(instr, instr.arg_int(2), instr.arg_int(3))
        return None

    @machine_cls.register(ops.sub_call)
    def _op_sub_call(m: EclMachineBase, instr: EclInstr):
        e, w, ctx = m.enemy, m.world, m.current
        ctx.instr_offset = instr.offset + instr.size
        if not e.no_stack_ret:
            m._push_context()
        m.call_sub(instr.arg_int(0))
        # 新 sub 拿到活动全局变量的快照(C: eclContextArgs.globalVars = g_GlobalEclVars)
        ctx.args.global_ints = list(w.global_ints)
        ctx.args.global_floats = list(w.global_floats)
        return "restart"

    @machine_cls.register(ops.sub_ret)
    def _op_sub_ret(m: EclMachineBase, instr: EclInstr):
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
