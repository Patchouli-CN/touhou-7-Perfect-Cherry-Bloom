"""ECL → 目标格式翻译子包: 录制基类 + 妖归符卡 JSON 编译器。

- ``EclTranslatorBase``: EclHost 录制实现 + record()/translate() 模板方法;
- ``YoukaiDanmakuTranslator``: compile() 实现 ECL→妖归 SpellDefinition 映射。
"""

from .base import EclTranslatorBase, TraceEvent, decode_spellcard_name
from .youkai import YoukaiDanmakuTranslator

__all__ = [
    "EclTranslatorBase",
    "TraceEvent",
    "YoukaiDanmakuTranslator",
    "decode_spellcard_name",
]
