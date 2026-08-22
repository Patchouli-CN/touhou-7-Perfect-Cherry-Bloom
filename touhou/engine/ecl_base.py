""" ECL 虚拟机框架基类 —— 作品无关的取指-译码-执行循环。

职责边界:
- 本模块只有 VM 框架: 主循环(step/_run_ecl)、调用栈、wait timer、O(1) handler
  分发(_handlers + register)、参数编解码(_int_arg/_float_arg/_store_*)、
  物理/插值通用逻辑(_move/_frame_update/_step_interps/_do_jump/_compare)、
  ex 指令分发框架(_run_ex —— 语义委托 host.run_ex_instr, 见 ecl.py 头注)。
- **没有任何具体 opcode 实现, 也没有 EclVarId 变量语义**: 变量读写是 stub,
  opcode handler 全部由作品子类用 ``@<子类>.register(...)`` 装饰器登记。
- 禁止 import touhou.games.*(单向依赖: 引擎 ←—— 作品)。

子类必须实现(基类默认 raise NotImplementedError):
- ``_get_int(var_id)`` / ``_set_int(var_id, value)``
- ``_get_float(var_id)`` / ``_set_float(var_id, value)``
可选重写:
- ``_get_float_value(var_id, raw)``: 带原始 f32 回落值的 float 变量读法,
  默认委托 ``_get_float``(th07 需要 raw 回落以还原 C 的位型语义)。
- ``_INTERP_POS_VARS``: 位置分量的变量 id 元组, 插值命中时回算 axis_speed
  (th07 = POS_X/POS_Y/POS_Z)。

handler 注册示例(作品子类侧):

    @EclMachineTh07.register(EclOpcode.JUMP)
    def _op_jump(m: EclMachineTh07, instr: EclInstr):
        return m._do_jump(instr, instr.arg_int(0), instr.arg_int(1))

    @EclMachineTh07.register(range(64, 73))   # 多 opcode 共享一个 handler
    def _op_bullet(m, instr): ...

handler 签名 ``(m, instr)``, 返回值契约同 ``_execute``:
None=顺序前进 / EclInstr=跳转目标 / "restart"=重取指令 / "error"=脚本结束。
handler 表按类隔离: 子类首次注册时自动复制父类表(cls.__dict__ 检查),
兄弟作品(th07/th08)互不污染。
"""

from __future__ import annotations

import math
from typing import Any, Callable, ClassVar, Iterable, Optional, TypeVar

from ..exceptions import NotImplementedEclError
from ..logger import logger as log
from ..utils import add_normalize_angle, f32, i32
from .ecl import (
    EclContext,
    EclEnemyState,
    EclFile,
    EclHost,
    EclInstr,
    EclOpcode,
    EclWorld,
    Vec3,
)

type EclHandler = Callable[["EclMachineBase", EclInstr], Any]
_F = TypeVar("_F", bound=Callable[..., Any])


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


class EclMachineBase:
    """单个敌人的 ECL 虚拟机框架: 当前上下文 + 调用栈(作品无关)。

    每帧调用一次 step()(= EclManager::RunEcl + Enemy 的移动/计时收尾)。
    step() 返回 False 表示脚本结束(ECL_UNIMP / 指令流跑飞), 宿主应 despawn。
    具体 opcode 由作品子类经 ``register`` 登记(见模块 docstring)。
    """

    _handlers: ClassVar[dict[int, EclHandler]] = {}
    # 位置分量变量 id(插值命中时回算 axis_speed); 子类按作品变量表覆盖
    _INTERP_POS_VARS: ClassVar[tuple[int, ...]] = ()

    @classmethod
    def register(
        cls,
        op: int | EclOpcode | Iterable[int | EclOpcode],
    ) -> Callable[[_F], _F]:
        """注册指令 handler。支持单个 opcode / range / list / tuple。

        子类首次注册时会自动复制父类的 handler 表，避免污染父类。
        """
        # 统一转成 list[int]
        if isinstance(op, Iterable) and not isinstance(op, (str, bytes)):
            ops = [int(o) for o in op]
        else:
            ops = [int(op)]

        def deco(handler: _F) -> _F:
            # 确保子类有自己的 handlers 副本，不污染父类
            if "_handlers" not in cls.__dict__:
                cls._handlers = dict(cls._handlers)
            for o in ops:
                cls._handlers[o] = handler
            return handler

        return deco

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
    # 变量 id 的语义映射是作品专属的(th07 见 games/th07/ecl_vm.py 的 EclVarId),
    # 基类只认 int, 全部 stub。

    def _get_int(self, var_id: int) -> int:
        raise NotImplementedError("作品子类须实现 int 变量读映射")

    def _set_int(self, var_id: int, value: int) -> None:
        raise NotImplementedError("作品子类须实现 int 变量写映射")

    def _get_float(self, var_id: int) -> float:
        raise NotImplementedError("作品子类须实现 float 变量读映射")

    def _get_float_value(self, var_id: int, raw: float) -> float:
        """带原始 f32 回落值的 float 读法; 默认委托 _get_float(子类可重写)。"""
        return self._get_float(var_id)

    def _set_float(self, var_id: int, value: float) -> None:
        raise NotImplementedError("作品子类须实现 float 变量写映射")

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
            if it.target_var in self._INTERP_POS_VARS:
                pos_modified = True
        if pos_modified:
            e.axis_speed.x = f32(e.pos.x - old_pos.x)
            e.axis_speed.y = f32(e.pos.y - old_pos.y)
            e.angle = f32(math.atan2(e.axis_speed.y, e.axis_speed.x))
            e.pos = old_pos

    def _run_ex(self, idx: int, instr: Optional[EclInstr]) -> None:
        """ex 指令分发框架: 语义在宿主侧(run_ex_instr), VM 只委托。"""
        if idx == 3:  # ExInsNoOp
            return
        if not self.host.run_ex_instr(idx, self.enemy, instr, ctx=self.current):
            if ("ex", idx) not in self._warned:
                self._warned.add(("ex", idx))
                log.warning("ECL ex 指令 {} 未实现, 按无操作处理", idx)

    # ---- 指令执行: O(1) dict 分发, 未命中走 _on_unhandled ----
    # 返回 None=顺序前进 / EclInstr=跳到 / "restart" / "error"

    def _execute(self, instr: EclInstr):
        handler = self._handlers.get(instr.id)
        if handler is not None:
            return handler(self, instr)
        return self._on_unhandled(instr)

    def _on_unhandled(self, instr: EclInstr):
        """未登记 handler 的指令: strict 抛异常, 否则交宿主钩子(记日志跳过)。"""
        if self.strict:
            raise NotImplementedEclError(
                f"ECL 指令 id={instr.id} offset={instr.offset:#x}")
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
