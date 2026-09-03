"""touhou —— 通用东方弹幕游戏框架; TH07《东方妖妖梦》为首个接入作品/参考实现。

对外 API 见 touhou/apis/basic.py; 顶层直接导出全部公共类型。作品经
touhou/registry.py 的 decorator 注册接入; import 本包即完成 th07 的
全维度注册(见下方 games.th07 的触发 import)与 th08 的数据+ECL+ANM 维度
注册(阶段 1/2 地基 + 单 A; world 等维度后续阶段接入)。

窗口版游戏入口: ``python -m touhou`` 或安装后的 ``touhou07`` 命令。
"""

from __future__ import annotations

from .games.th07 import data as _th07_data  # noqa: F401  (登记 th07 数值表)
from .games.th07 import world as _th07_world  # noqa: F401  (触发 th07 注册:

#  world/ecl_host; ecl 经 world→ecl_vm,
#  anm 经 world→…→schema.anm 链)
from .games.th07 import view as _th07_view  # noqa: F401  (登记 th07 窗口 App 与

#  pygame 渲染后端; apis 不 import
#  games.* 后, 注册触发点在本模块)
from .games.th07 import mods as _th07_mods  # noqa: F401  (登记 th07 mod 能力

#  提供者 Th07Mods)
from .games.th08 import data as _th08_data  # noqa: F401  (登记 th08 数值表)
from .games.th08 import anm as _th08_anm  # noqa: F401  (登记 th08 ANM v3:

#  Th08AnmFile)
from .games.th08 import ecl_vm as _th08_ecl_vm  # noqa: F401  (登记 th08 ECL 维度:

#  EclMachineTh08 + EclFileTh08)
from .games.th08 import ecl_host as _th08_ecl_host  # noqa: F401  (登记 th08 游戏

#  回调包: Th08GameEclHost + 关卡资源命名)
from .games.th08 import world as _th08_world  # noqa: F401  (登记 th08 对局实现:

#  ImperishableNight)
from .games.th08 import view as _th08_view  # noqa: F401  (登记 th08 窗口 App:

#  GameApp; pygame 后端自持不进 renderer 注册表)
from .apis.basic import (
    BossSnapshot,
    BulletSnapshot,
    EnemySnapshot,
    Game,
    GameEvent,
    GameEventKind,
    GamePhase,
    Input,
    ItemSnapshot,
    LaserSnapshot,
    PlayerSnapshot,
    Snapshot,
    TouhouWorld,
    TouhouWorldEventStream,
    WorldData,
)
from .apis.modding import ModApi
from .registry import (
    AnmSpec,
    ArchiveSpec,
    EclSpec,
    GameData,
    GameHooks,
    GameSpec,
    get_archive_spec,
    get_game,
    register_anm,
    register_app,
    register_archive,
    register_ecl,
    register_game_data,
    register_game_hooks,
    register_mods,
    register_world_impl,
    registered_archives,
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
    "ArchiveSpec",
    "BossSnapshot",
    "BulletSnapshot",
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
    "ModApi",
    "MsgParseError",
    "NotImplementedEclError",
    "NotRegisteredError",
    "ParseError",
    "PlayerSnapshot",
    "RegistryError",
    "Snapshot",
    "ThbgmFormatError",
    "TouhouError",
    "TouhouWorld",
    "TouhouWorldEventStream",
    "WorldData",
    "get_archive_spec",
    "get_game",
    "register_anm",
    "register_app",
    "register_archive",
    "register_ecl",
    "register_game_data",
    "register_game_hooks",
    "register_mods",
    "register_world_impl",
    "registered_archives",
    "registered_games",
    "registered_renderers",
]
