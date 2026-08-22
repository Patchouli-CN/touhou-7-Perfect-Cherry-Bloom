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

engine/ 只留可复用机制(bullets/lasers/enemies/ecl/replay/config/score_store)。
注意 import 顺序: player 必须先于 boss/ecl_host/world 导入
(engine/enemies.py 运行时引回 PlayerState, 是 engine → games 的唯一反向边)。
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
