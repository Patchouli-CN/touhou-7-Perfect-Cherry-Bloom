"""touhou —— 《东方妖妖梦》(TH07) 的 Python 重实现。

对外 API 见 touhou/apis/basic.py; 顶层直接导出全部公共类型。

窗口版游戏入口: ``python -m touhou`` 或安装后的 ``touhou07`` 命令。
"""
from __future__ import annotations

from .games.th07 import data as _th07_data    # noqa: F401  (登记 th07 数值表)
from .games.th07 import world as _th07_world  # noqa: F401  (触发 th07 注册:
                                              #  world/ecl_host; ecl 经 world→ecl_vm,
                                              #  anm 经 world→…→schema.anm 链)
from .games.th07 import view as _th07_view    # noqa: F401  (登记 th07 窗口 App 与
                                              #  pygame 渲染后端; apis 不 import
                                              #  games.* 后, 注册触发点在本模块)
from .apis.basic import (
    BossSnapshot,
    BulletSnapshot,
    Character,
    Difficulty,
    EnemySnapshot,
    Game,
    GameEvent,
    GameEventKind,
    GamePhase,
    Input,
    ItemSnapshot,
    LaserSnapshot,
    PlayerSnapshot,
    ShotType,
    Snapshot,
    TouhouWorld,
    TouhouWorldEventStream,
    WorldData,
)
from .registry import (
    AnmSpec,
    EclSpec,
    GameData,
    GameHooks,
    GameSpec,
    get_game,
    register_anm,
    register_app,
    register_ecl,
    register_game_data,
    register_game_hooks,
    register_world_impl,
    registered_games,
    registered_renderers,
)
from .exceptions import (
    ArchiveFormatError,
    DuplicateRegistrationError,
    EclParseError,
    MsgParseError,
    NotImplementedEclError,
    NotRegisteredError,
    ParseError,
    RegistryError,
    ThbgmFormatError,
    TouhouError,
)
from .types import GameEngine

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "AnmSpec",
    "ArchiveFormatError",
    "BossSnapshot",
    "BulletSnapshot",
    "Character",
    "Difficulty",
    "DuplicateRegistrationError",
    "EclParseError",
    "EclSpec",
    "EnemySnapshot",
    "Game",
    "GameData",
    "GameEngine",
    "GameEvent",
    "GameEventKind",
    "GameHooks",
    "GamePhase",
    "GameSpec",
    "Input",
    "ItemSnapshot",
    "LaserSnapshot",
    "MsgParseError",
    "NotImplementedEclError",
    "NotRegisteredError",
    "ParseError",
    "PlayerSnapshot",
    "RegistryError",
    "ShotType",
    "Snapshot",
    "ThbgmFormatError",
    "TouhouError",
    "TouhouWorld",
    "TouhouWorldEventStream",
    "WorldData",
    "get_game",
    "register_anm",
    "register_app",
    "register_ecl",
    "register_game_data",
    "register_game_hooks",
    "register_world_impl",
    "registered_games",
    "registered_renderers",
]
