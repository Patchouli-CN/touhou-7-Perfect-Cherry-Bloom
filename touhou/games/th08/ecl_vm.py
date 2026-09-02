"""TH08(东方永夜抄)专属 ECL 虚拟机实现 —— 阶段 2 单 A: 占位空壳 + 注册。

单 A 只完成格式类(``EclFileTh08``, 见 games/th08/ecl_file.py)接入与注册表
登记。以下内容由阶段 2 单 B 填充(对照 Reference/th08-ref/src/
EclRunLow.inl / EclRunHigh.inl / EclOperandsInt.cpp / EclOperandsFloat.cpp):
- ``Th08EclOpcode``(全 184 条)与 th08 变量命名空间(10000-10100);
- 变量系统(_get_int/_set_int/_get_float/_set_float 全变量路由);
- 184 条 opcode handler —— 核心 1-53 从 engine/ecl_std_ops 按 th08 编号表
  注册(``CoreOps``; th07↔th08 编号对照结论: 仅 1 号同号同义, 其余系统性
  错位, 详见 ecl_std_ops 模块 docstring), 其余照 th07 同语义 handler 改编
  或新写(使魔/child 上下文块/EX 指令/时刻系)。
"""

from __future__ import annotations

from ...engine.ecl_base import EclMachineBase
from ...registry import register_ecl
from .ecl_file import EclFileTh08


@register_ecl("th08", file_format=EclFileTh08)
class EclMachineTh08(EclMachineBase):
    """TH08 的 ECL VM 占位(阶段 2 单 A)。

    变量系统与全部 opcode handler 由阶段 2 单 B 填充(见模块 docstring);
    当前只有注册表登记(``spec.ecl.machine`` / ``spec.ecl.file_format``)。
    """
