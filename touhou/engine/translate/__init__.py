"""ECL → 目标格式翻译子包: 录制基类 + 妖归符卡 JSON 编译器。

- ``EclTranslatorBase``: EclHost 录制实现 + record()/translate() 模板方法,
  三模式(``TranslateMode.DIRECT`` 回放 trace / ``CONTROL`` 静态控制流 IR /
  ``AUTO`` 静态骨架+动态补盲);
- ``ir``: CONTROL 模式的控制流 IR(IrSeq/IrLoop/IrIf/IrOp)与重建算法;
- ``YoukaiDanmakuTranslator``: compile()/compile_ir()/merge() 实现 ECL→妖归
  SpellDefinition 映射。
"""

from .base import (
    EclTranslatorBase,
    TraceEvent,
    TranslateMode,
    decode_spellcard_name,
    list_spellcards,
    spellcard_name,
)
from .ir import IrCond, IrIf, IrLoop, IrNode, IrOp, IrOperand, IrSeq, build_ir
from .youkai import YoukaiDanmakuTranslator

__all__ = [
    "EclTranslatorBase",
    "TraceEvent",
    "TranslateMode",
    "IrCond",
    "IrIf",
    "IrLoop",
    "IrNode",
    "IrOp",
    "IrOperand",
    "IrSeq",
    "YoukaiDanmakuTranslator",
    "build_ir",
    "decode_spellcard_name",
    "list_spellcards",
    "spellcard_name",
]
