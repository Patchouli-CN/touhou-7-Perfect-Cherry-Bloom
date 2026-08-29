"""ECL 控制流 IR —— CONTROL 静态翻译模式的中间结构(作品无关)。

对照 VM 的跳转语义(ecl_base.py _do_jump): 跳转目标 = 当前指令 offset +
相对偏移(字节), 同时把 context time 重置为 new_time。操作数布局对照作品
VM 的 jump handler(th07 见 games/th07/ecl_vm.py):
- JUMP:        args=(new_time, rel_offset)
- DEC_JUMP:    args=(new_time, rel_offset, counter_var) —— 计数器自减后 >0 才跳
- JUMP_IF_*:   args=(a, b, new_time, rel_offset), param_mask bit0/1 标记
               a/b 是变量引用(float 版的变量 id 存成 f32 值)

重建算法(build_ir): 顺序扫描 + 递归区间结构化 —
- **回边**(目标下标 <= 当前指令): 无条件 JUMP → 无限循环(``IrLoop.condition
  is None``); DEC_JUMP → 计数循环(``counter_var``); JUMP_IF_* → 条件循环。
  仅当回边目标落在当前区间**顶层节点边界**且循环体内无逃逸跳转时才折叠,
  否则跳转保留为 IrOp(goto 蛛网不做完备结构化, log.debug 说明)。
- **条件前跳**(JUMP_IF_* 目标 > 下一条): → IrIf; if_true 末尾恰是无条件
  JUMP 且跳到更后面时识别出 else 双臂, 否则单臂。
- 其余指令原样进 IrOp(携带 EclInstr; ``instr.time`` = 等待帧数语义)。
  IrLoop 额外携带 ``loop_time``(回边重置的 new_time)与 ``period``(=
  回边指令 time - new_time, 一轮的帧数) —— 循环体的时间语义全在这两个
  字段 + IrOp.instr.time 上(妖归的 interval/delay、未来 LuaSTG 的
  task.wait 都靠它)。

兜底: 递归深度 > _MAX_DEPTH 或节点数 > _MAX_NODES 时放弃结构化, 剩余指令
平铺为 IrOp —— 不可归约的跳转不许死循环/炸栈。
"""

from __future__ import annotations

from typing import NamedTuple, Optional

import msgspec

from ...logger import logger as log
from ..ecl import EclFile, EclInstr, EclOpcode

__all__ = [
    "IrOperand",
    "IrCond",
    "IrNode",
    "IrOp",
    "IrIf",
    "IrLoop",
    "IrSeq",
    "build_ir",
]

_MAX_DEPTH = 32
_MAX_NODES = 4096

# 条件跳转 opcode → 比较运算符(与 ecl_base.py _compare 同语义)
_COND_JUMP_OPS: dict[int, str] = {
    EclOpcode.JUMP_IF_EQ: "==",
    EclOpcode.JUMP_IF_NEQ: "!=",
    EclOpcode.JUMP_IF_LT: "<",
    EclOpcode.JUMP_IF_LEQ: "<=",
    EclOpcode.JUMP_IF_GT: ">",
    EclOpcode.JUMP_IF_GEQ: ">=",
    EclOpcode.JUMP_IF_EQ_FLOAT: "==",
    EclOpcode.JUMP_IF_NEQ_FLOAT: "!=",
    EclOpcode.JUMP_IF_LT_FLOAT: "<",
    EclOpcode.JUMP_IF_LEQ_FLOAT: "<=",
    EclOpcode.JUMP_IF_GT_FLOAT: ">",
    EclOpcode.JUMP_IF_GEQ_FLOAT: ">=",
}

_FLOAT_JUMP_OPS = {
    EclOpcode.JUMP_IF_EQ_FLOAT,
    EclOpcode.JUMP_IF_NEQ_FLOAT,
    EclOpcode.JUMP_IF_LT_FLOAT,
    EclOpcode.JUMP_IF_LEQ_FLOAT,
    EclOpcode.JUMP_IF_GT_FLOAT,
    EclOpcode.JUMP_IF_GEQ_FLOAT,
}


class IrOperand(msgspec.Struct, frozen=True):
    """比较操作数: 立即数或 ECL 变量引用(param_mask 置位)。"""

    value: int | float
    is_var: bool = False
    is_float: bool = False


class IrCond(msgspec.Struct, frozen=True):
    """JUMP_IF_* 的比较条件: ``lhs op rhs``。"""

    op: str  # "==" "!=" "<" "<=" ">" ">="
    lhs: IrOperand
    rhs: IrOperand


class IrNode(msgspec.Struct):
    """IR 节点基类(IrSeq/IrLoop/IrIf/IrOp)。IR 只在内存中传递, 不做编解码。"""


class IrOp(IrNode):
    """单条非跳转指令(或不可归约的跳转), 原样携带 EclInstr。"""

    instr: EclInstr


class IrIf(IrNode):
    """条件前跳重建的 if(/else)。"""

    condition: IrCond
    if_true: list[IrNode] = msgspec.field(default_factory=list)
    if_false: list[IrNode] = msgspec.field(default_factory=list)


class IrLoop(IrNode):
    """回边重建的循环体。

    - ``condition is None``: 无限循环(无条件 JUMP 回边);
    - ``counter_var >= 0``: DEC_JUMP 计数循环(每轮自减, >0 续跳);
    - ``loop_time``: 回边 JUMP 的 new_time(每轮 context time 重置到它);
    - ``period``: 回边指令 time - new_time = 一轮的帧数(迭代 k 里 time=T
      的指令在绝对帧 T + k*period 执行)。
    """

    body: list[IrNode] = msgspec.field(default_factory=list)
    condition: Optional[IrCond] = None
    counter_var: int = -1
    loop_time: int = 0
    period: int = 0


class IrSeq(IrNode):
    """顺序节点序列(build_ir 的根)。"""

    nodes: list[IrNode] = msgspec.field(default_factory=list)


class _JumpInfo(NamedTuple):
    target: int  # 绝对文件偏移
    new_time: int
    cond: Optional[IrCond]  # None = 无条件
    counter_var: int = -1


def _cmp_operand(ins: EclInstr, idx: int, is_float: bool) -> IrOperand:
    if ins.param_mask & (1 << idx):
        if is_float:
            # float 变量 id 存成 f32 值(ecl_base.py _float_target 同款还原)
            return IrOperand(int(ins.arg_float(idx)), is_var=True, is_float=True)
        return IrOperand(ins.arg_int(idx), is_var=True)
    if is_float:
        return IrOperand(ins.arg_float(idx), is_float=True)
    return IrOperand(ins.arg_int(idx))


def _jump_of(ins: EclInstr) -> Optional[_JumpInfo]:
    """跳转指令 → 目标/条件描述; 非跳转返回 None。"""
    op = ins.id
    if op == EclOpcode.JUMP:
        return _JumpInfo(ins.offset + ins.arg_int(1), ins.arg_int(0), None)
    if op == EclOpcode.DEC_JUMP:
        var = ins.arg_int(2) if ins.param_mask & 4 else -1
        cond = None
        if var >= 0:
            cond = IrCond(">", IrOperand(var, is_var=True), IrOperand(0))
        return _JumpInfo(ins.offset + ins.arg_int(1), ins.arg_int(0), cond, var)
    cmp_op = _COND_JUMP_OPS.get(op)
    if cmp_op is not None:
        is_float = op in _FLOAT_JUMP_OPS
        cond = IrCond(
            cmp_op,
            _cmp_operand(ins, 0, is_float),
            _cmp_operand(ins, 1, is_float),
        )
        return _JumpInfo(ins.offset + ins.arg_int(3), ins.arg_int(2), cond)
    return None


def build_ir(ecl_file: EclFile, sub_id: int) -> IrSeq:
    """把 sub 的指令流重建成控制流 IR(入口; 边界检查由调用方做)。"""
    instrs = [i for i in ecl_file.subs[sub_id] if not i.is_terminator]
    index_of = {ins.offset: n for n, ins in enumerate(instrs)}
    return IrSeq(_IrBuilder(instrs, index_of).parse_range(0, len(instrs), 0))


class _IrBuilder:
    def __init__(self, instrs: list[EclInstr], index_of: dict[int, int]) -> None:
        self.instrs = instrs
        self.index_of = index_of
        self.count = 0  # 已建节点数(兜底预算)

    def _reducible(self, lo: int, hi: int) -> bool:
        """[lo, hi) 内所有跳转的目标都不逃出 [lo, hi](= 可结构化区间)。"""
        for n in range(lo, hi):
            j = _jump_of(self.instrs[n])
            if j is None:
                continue
            tgt = self.index_of.get(j.target)
            if tgt is None or not (lo <= tgt <= hi):
                return False
        return True

    def parse_range(self, lo: int, hi: int, depth: int) -> list[IrNode]:
        if depth > _MAX_DEPTH:
            log.debug("IR 重建超过最大深度 {}, [{}, {}) 平铺为 IrOp", _MAX_DEPTH, lo, hi)
            return [IrOp(i) for i in self.instrs[lo:hi]]
        nodes: list[IrNode] = []
        starts: list[int] = []  # nodes[k] 覆盖的起始指令下标(与 nodes 平行)
        i = lo
        while i < hi:
            if self.count > _MAX_NODES:
                log.debug("IR 重建超过节点上限 {}, 剩余平铺为 IrOp", _MAX_NODES)
                nodes.extend(IrOp(x) for x in self.instrs[i:hi])
                break
            ins = self.instrs[i]
            j = _jump_of(ins)
            if j is not None:
                tgt = self.index_of.get(j.target)
                if tgt is None:
                    log.debug("跳转目标 {:#x} 不在本 sub 内, 保留为 IrOp", j.target)
                elif tgt <= i:  # 回边 → 循环
                    if tgt in starts and self._reducible(tgt, i):
                        pos = starts.index(tgt)
                        body = nodes[pos:]
                        nodes[pos:] = [
                            IrLoop(
                                body,
                                j.cond,
                                j.counter_var,
                                j.new_time,
                                ins.time - j.new_time,
                            )
                        ]
                        starts[pos:] = [tgt]
                        self.count += 1
                        i += 1
                        continue
                    log.debug(
                        "回边 {:#x} → {:#x} 不可归约(目标不在节点边界或体内有逃逸), "
                        "保留为 IrOp",
                        ins.offset,
                        j.target,
                    )
                elif j.cond is not None:  # 条件前跳 → if
                    end = self._match_if(i, tgt, hi)
                    if end is not None:
                        mid, nxt = end
                        if_true = self.parse_range(i + 1, mid, depth + 1)
                        if_false = self.parse_range(tgt, nxt, depth + 1) if nxt > tgt else []
                        nodes.append(IrIf(j.cond, if_true, if_false))
                        starts.append(i)
                        self.count += 1
                        i = nxt
                        continue
                    log.debug("条件前跳 {:#x} 不可归约, 保留为 IrOp", ins.offset)
                else:
                    log.debug("无条件前跳(goto) {:#x} 保留为 IrOp", ins.offset)
            nodes.append(IrOp(ins))
            starts.append(i)
            self.count += 1
            i += 1
        return nodes

    def _match_if(self, i: int, tgt: int, hi: int) -> Optional[tuple[int, int]]:
        """条件前跳 i → tgt 的 if 区间: 返回 (if_true_end, next)。

        单臂: (tgt, tgt); 双臂(if_true 末尾是无条件 JUMP 到更后面): (tgt-1, else_end)。
        区间内有逃逸跳转时返回 None(不可归约)。
        """
        last = self.instrs[tgt - 1]
        lj = _jump_of(last)
        if lj is not None and lj.cond is None:
            lend = self.index_of.get(lj.target)
            if lend is not None and tgt < lend <= hi:
                if self._reducible(i + 1, tgt - 1) and self._reducible(tgt, lend):
                    return tgt - 1, lend
        if self._reducible(i + 1, tgt):
            return tgt, tgt
        return None
