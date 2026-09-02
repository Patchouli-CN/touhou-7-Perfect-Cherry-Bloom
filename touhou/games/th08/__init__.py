"""《东方永夜抄》(TH08) 的游戏逻辑包 —— 作品层, 架在通用 engine/ 机制之上。

阶段 1(地基)仅注册数据维度:
- data.py  数值表/名单(单一来源, register_game_data("th08") 登记)

ECL/ANM/world/view 等维度留待后续阶段; 分层纪律与 th07 相同
(单向依赖: 引擎 ←—— 作品, engine/ 运行时不 import games.*)。
"""

from .data import TH08_DATA

__all__ = [
    "TH08_DATA",
]
