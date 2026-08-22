"""《东方妖妖梦》(TH07) 的游戏逻辑包 —— 作品层, 架在通用 engine/ 机制之上。

收录 th07 专属逻辑:
- data.py       数值表/名单(单一来源, register_game_data("th07") 登记)
- player.py     自机系统(移动/射击/死亡重生/擦弹/樱点结算事件)
- boss.py       Boss 阶段/符卡状态机
- bomb.py       12 套炸弹 + 樱之结界
- items.py      道具经济(掉落/吸附/收集结算)
- globals.py    ZunGlobals 计数(分数/樱点/动态难度)
- results.py    结算与评级
- ecl_host.py   ECL 宿主钩子(register_game_hooks("th07") 登记)
- ecl_vm.py     TH07 ECL 虚拟机(EclMachineTh07 + EclVarId + 161 条 opcode
  handler, register_ecl("th07") 登记; VM 框架在 engine/ecl_base.py)
- playerdata.py Player Data 画面装配
- world.py      对局主逻辑 PerfectCherryBloom(register_world_impl("th07") 登记)
- view/         表现层(GameApp 应用壳/菜单场景/贴图渲染/PygameRenderer 后端;
  通用渲染机制留在 engine/view/, 协议在 engine/render/)

engine/ 只留可复用机制(bullets/lasers/enemies/ecl/replay/config/score_store/
player_base 玩家状态基座, 及 view/ 的作品无关渲染基建)。PlayerState 定义在
engine/player_base.py, 本包 player.py 再导出以保持
``games.th07.player.PlayerState`` 引用兼容; engine 层运行时不 import
games.*(单向依赖: 引擎 ←—— 作品; 唯一例外是 engine/render 协议在
TYPE_CHECKING 下引用本包 screens.py 的菜单流类型, 仅 mypy 可见)。
导入本包即完成 th07 在 registry 的 world/hooks/data 维度注册
(ECL/ANM 经 engine.ecl/schema.anm 链; ``import touhou`` 保证全链触发)。
"""
from .data import TH07_DATA
from .player import Player, PlayerState
from .boss import SPELLCARD_SCORE, Boss
from .bomb import Bomb, Border, BorderState
from .items import ItemType, ItemWorld
from .globals import ZunGlobals
from .results import RunStats
from .ecl_host import GameEclHost
from .world import PerfectCherryBloom

__all__ = [
    "TH07_DATA",
    "SPELLCARD_SCORE",
    "Bomb",
    "Border",
    "BorderState",
    "Boss",
    "GameEclHost",
    "ItemType",
    "ItemWorld",
    "PerfectCherryBloom",
    "Player",
    "PlayerState",
    "RunStats",
    "ZunGlobals",
]
