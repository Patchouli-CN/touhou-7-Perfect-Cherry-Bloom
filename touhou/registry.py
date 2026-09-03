"""作品注册表 —— 让 touhou 包成为"通用 touhou 框架", th07 是第一个注册作品。

注册维度(decorator 形态, 按作品名登记):
- ECL 虚拟机:   ``@register_ecl(name, file_format=...)`` 装饰 EclMachine 实现类
- ANM 格式变体: ``@register_anm(name, version=...)``    装饰 AnmFile 解析类
- 游戏回调包:   ``@register_game_hooks(name, ...)``     装饰 EclHost 宿主实现类
- 对局实现:     ``@register_world_impl(name)``          装饰主逻辑类(th07 =
  games.th07.world.PerfectCherryBloom), TouhouWorld(game=...) 经此构造对局
- 数值表:       ``register_game_data(name, GameData)``  登记作品的数值表/名单
  (符卡分值/炸弹参数/掉落表/火力档/机体 sht 映射/角色与难度名单), th07 的
  实例集中在 touhou/games/th07/data.py(TH07_DATA)
- 窗口 App:     ``@register_app(name)``              装饰窗口应用类(th07 =
  games.th07.view.GameApp), ``TouhouWorld.run()``(headless=False) 经此构造;
  未登记的作品只能 headless 运行
- mod 能力:     ``@register_mods(name)``             装饰作品专属 mod 能力
  提供者类(th07 = games.th07.mods.Th07Mods), ``ModApi(game)`` 构造时实例化
  并把其公开方法收割进分层命名空间(归属用 ``@mod_namespace(ns)`` 声明,
  作品机制不堆进 ModApi 通用核, 见 apis/modding.py 的分层纪律)
- 资源包格式:   ``@register_archive(games, format_name=...)`` 装饰容器实现类
  (schema.archive.ArchiveBase 子类; th07 = pbg4 的 Pbg4Archive)。一种格式
  常服务多作(pbgz 之于 th08/th09), 故按"作品名 → 规格"与"格式名 → 规格"
  双表登记; 消费方不 import 具体格式类, 走 schema.archive.open_archive
  (不指定作品时按文件头在已注册格式里认头)

另有与作品名无关的正交维度:
- 渲染后端:     ``@register_renderer(name)``            装饰 Renderer 实现类
  (协议见 engine/render/__init__.py), ``GameApp(renderer=...)`` 按名解析;
  "pygame" 为默认后端(games/th07/view/pygame_backend.py, import touhou 即登记)

本模块是叶子模块: 不 import engine/schema/games, 不产生循环 import;
注册由各组件定义处的 decorator 在 import 链上触发(``import touhou`` 即完成
th07 的全部注册)。重复注册同一名(同一维度)报 DuplicateRegistrationError
(ValueError 子类), 防静默覆盖; 查找未注册名报带已注册列表的
NotRegisteredError(KeyError 子类)。

world impl 的构造契约(现阶段): 接受 th07 风格关键字参数
(data_path/character/difficulty/seed/score_path/initial_lives/hooks/data);
data 为 GameData(缺省 None = 实现对局用自己的内置默认表, th07 即
games.th07.data.TH07_DATA)。指令集拆分为独立子包是未来作品(th08)落地时的工作,
这里只做接缝。
"""

from __future__ import annotations

import msgspec
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Callable, TypeVar, TYPE_CHECKING, overload, Literal

if TYPE_CHECKING:
    from .engine.ecl_base import EclMachineBase
    from .engine.ecl import EclFile
    from .schema.archive import ArchiveBase

from .exceptions import DuplicateRegistrationError, NotRegisteredError

__all__ = [
    "AnmSpec",
    "ArchiveSpec",
    "EclSpec",
    "GameData",
    "GameHooks",
    "GameSpec",
    "default_game",
    "get_archive_format",
    "get_archive_spec",
    "get_game",
    "get_renderer",
    "mod_namespace",
    "register_anm",
    "register_app",
    "register_archive",
    "register_default_game",
    "register_ecl",
    "register_game_data",
    "register_game_hooks",
    "register_mods",
    "register_renderer",
    "register_world_impl",
    "registered_archives",
    "registered_games",
    "registered_renderers",
]


@dataclass(frozen=True)
class EclSpec:
    """一部作品的 ECL 虚拟机实现(指令集解释器 + 二进制文件格式)。"""

    machine: type["EclMachineBase"]  # EclMachine 类(指令集/解释器实现)
    file_format: type["EclFile"]  # EclFile 类(ecldata 二进制 parse/serialize;
    # 统一 enc/dec 入口 engine/ecl_codec.EclCodec 经此解析)


@dataclass(frozen=True)
class AnmSpec:
    """一部作品的 ANM 格式变体。"""

    format: type  # AnmFile 类(解析器)
    version: int  # 期望的 anm 版本号(th07 = 2)


class ArchiveSpec(msgspec.Struct, frozen=True):
    """一部作品的资源包容器格式(``.dat`` 解包实现)。"""

    container_cls: type["ArchiveBase"]  # 容器实现类, 如 pbg4.Pbg4Archive
    format_name: str  # 格式标识(pbg4/pbgz/tha1…), 供按格式直取与认头遍历


class GameData(msgspec.Struct, frozen=True):
    """作品数值表/名单(registry 只搬运; th07 实例见 games/th07/data.py)。"""

    characters: tuple[str, ...] = ()  # 机体名单(下标 = shotType)
    difficulties: tuple[str, ...] = ()  # 难度名单(下标 = difficulty)
    extra_stages: tuple[str, ...] = ()  # Extra Start 后的关卡名单
    stage_count: int = 6  # 本篇面数(practice 选关上限)
    practice_difficulty_count: int = 4  # practice 可选难度数(原版 4)
    main_difficulty_count: int = 4  # 本篇 Start 可选难度数(原版 4;
    # Extra/Phantasm 是额外关卡, 不算难度)
    character_sht: dict[int, tuple[str, str]] = msgspec.field(
        default_factory=dict
    )  # 机体 → (非 focus, focus) .sht 文件
    spellcard_scores: tuple[int, ...] = ()  # 符卡基础分值(代码值)
    bomb_params: dict[tuple[int, bool], tuple[int, int, int, float]] = msgspec.field(
        default_factory=dict
    )  # (机体, focus) → 炸弹参数原始行
    drop_table: tuple[int, ...] = ()  # 小怪随机掉落表
    power_levels: tuple[int, ...] = ()  # 火力档位阈值
    full_power: int = 128  # 满火力值
    full_power_score_bonus: tuple[int, ...] = ()  # 满火力后小 P 递增分表
    # ---- th08(东方永夜抄)专属数值表(th07 留默认空值) ----
    time_orb_thresholds: tuple[tuple[int, ...], ...] = ()  # 时刻符点阈值表
    # g_TimeRequirementParams[9][4] (GameManager.cpp:42-52): 行 = C currentStage
    # (0-8, 含 4A/4B/6A/6B/EX), 列 = 难度 E/N/H/L
    youkai_gauge_bounds: tuple[int, ...] = ()  # 妖率槽界 6 槽默认值
    # g_PlayerGaugeBounds 初始化段 (Player.cpp:1607-1612):
    # [0]人限(下限)/[1]妖限(上限)/[2]人特效阈/[3]妖特效阈/[4]人染色阈/[5]妖染色阈;
    # 按机体的变体调整(咏唱妖梦半幅/单人封顶)在 globals 初始化时做
    rank_table: tuple[tuple[int, int, int], ...] = ()  # 动态难度表
    # g_RankParams[6] (GameManager.cpp:32-39): 难度 → (初始rank, minRank, maxRank)
    point_item_values: tuple[int, ...] = ()  # 难度 → 点道具初值
    # (GameManagerSetup.cpp:149-161: 60000/100000/200000/300000/300000)


@dataclass(frozen=True)
class GameHooks:
    """游戏回调包: 宿主钩子类 + 关卡资源命名规则。

    默认值即 th07 规则; 新作品注册时按自己的资源命名覆盖。
    """

    host: type | None = None  # EclHost 实现类(如 th07 的 GameEclHost)
    stage_file: str = "stage{n}.std"  # 关卡背景/几何文件({n} = 关号)
    ecl_file: str = "ecldata{n}.ecl"  # 关卡 ECL 脚本文件
    msg_file: str = "msg{n}.dat"  # 关卡对话文件


@dataclass(frozen=True)
class GameSpec:
    """一部已注册作品的完整描述(``get_game`` 的返回)。

    各维度允许为 None(部分注册); hooks 未注册时回落到 th07 默认规则。
    """

    name: str
    ecl: EclSpec | None
    anm: AnmSpec | None
    hooks: GameHooks
    world: type | None  # 对局实现类(register_world_impl 登记)
    data: GameData | None = None  # 数值表(register_game_data 登记; None=未登记)
    app: type | None = None  # 窗口 App 类(register_app 登记; None=未登记)
    mods: type | None = None  # mod 能力提供者类(register_mods 登记; None=未登记)
    archive: ArchiveSpec | None = None  # 作品dat文件解析器


# ---- 全局注册表(按维度分表; 同名不同维度允许共存) ----
_ECL: dict[str, EclSpec] = {}
_ANM: dict[str, AnmSpec] = {}
_HOOKS: dict[str, GameHooks] = {}
_WORLD: dict[str, type] = {}
_DATA: dict[str, GameData] = {}
_APP: dict[str, type] = {}
_MODS: dict[str, type] = {}
_RENDERER: dict[str, type] = {}  # 渲染后端(与作品名无关的正交维度)
_ARCHIVE: dict[str, ArchiveSpec] = {}  # 作品名 → 容器格式
_ARCHIVE_FORMAT: dict[str, ArchiveSpec] = {}  # 格式名 → 容器格式(认头遍历用)

#: 已注册作品号 → 作品名(检测到对应资源时打印; 新作品注册时在此补名字)
GAME_TITLES = {
    "th07": "東方妖々夢 〜 Perfect Cherry Blossom",
    "th08": "東方永夜抄 〜 Imperishable Night.",
}


def _put(table: dict[str, Any], kind: str, name: str, value: Any) -> None:
    """写入一个维度; 同名重复注册报错(防静默覆盖)。"""
    if name in table:
        raise DuplicateRegistrationError(
            f"{kind}重复注册: {name!r} (已注册 {table[name]!r})"
        )
    table[name] = value


def register_ecl(name: str, *, file_format: type) -> Callable[[type], type]:
    """注册作品的 ECL 虚拟机(装饰 EclMachine 实现类, 原样返回)。"""

    def deco(cls: type) -> type:
        _put(_ECL, "ECL 虚拟机", name, EclSpec(machine=cls, file_format=file_format))
        return cls

    return deco


def register_anm(name: str, *, version: int) -> Callable[[type], type]:
    """注册作品的 ANM 格式变体(装饰 AnmFile 解析类, 原样返回)。"""

    def deco(cls: type) -> type:
        _put(_ANM, "ANM 格式", name, AnmSpec(format=cls, version=version))
        return cls

    return deco


def register_game_hooks(
    name: str,
    *,
    stage_file: str = "stage{n}.std",
    ecl_file: str = "ecldata{n}.ecl",
    msg_file: str = "msg{n}.dat",
) -> Callable[[type], type]:
    """注册游戏回调包(装饰 EclHost 宿主实现类, 原样返回)。

    关卡文件命名规则随包登记, 由对局实现在装载关卡资源时消费
    (games/th07/world.py 的 hooks 接缝)。
    """

    def deco(cls: type) -> type:
        _put(
            _HOOKS,
            "游戏回调包",
            name,
            GameHooks(
                host=cls, stage_file=stage_file, ecl_file=ecl_file, msg_file=msg_file
            ),
        )
        return cls

    return deco


def register_world_impl(name: str) -> Callable[[type], type]:
    """注册对局实现类(装饰主逻辑类, 原样返回)。

    TouhouWorld/Game 的 ``game=`` 参数经此构造对局; 构造契约见模块 docstring。
    """

    def deco(cls: type) -> type:
        _put(_WORLD, "对局实现", name, cls)
        return cls

    return deco


def register_game_data(name: str, data: GameData) -> GameData:
    """注册作品的数值表/名单(原样返回 data, 便于 ``X = register_game_data(...)``)。

    对局实现经构造参数 ``data`` 收到此表(见 Game/TouhouWorld 的构造接缝);
    引擎模块的模块级同名常量是该作品的默认表, 独立使用时无需注册。
    """
    _put(_DATA, "数值表", name, data)
    return data


def register_app(name: str) -> Callable[[type], type]:
    """注册作品的窗口 App(装饰窗口应用类/工厂, 原样返回)。

    构造契约: ``app_factory(make_game, *, data_path, bgm_path, game_data)``
    (被装饰类可有多余的带默认值关键字参数 —— 契约是关键字子集), 返回对象
    须有 ``run()`` 方法(弹窗并阻塞至关窗)。``TouhouWorld.run()``
    (headless=False) 经此解析; th07 的实现是 games/th07/view/impl.py 的
    GameApp。

    可选观战契约: ``TouhouWorld(auto_input=callable, headless=False)`` 时
    追加关键字参数 ``spectate=<policy>``(``game -> Input`` 逐帧策略, 作品
    无关形态见 touhou/types.py 的 ``InputSource``)。App 可声明同名带默认值
    的 kwarg 支持观战(跳过标题菜单直接开局, 每帧输入来自策略而非键盘;
    policy 的实参应有完整观测面, 如包出现存对局的 apis.basic.Game);
    不声明的 App 收不到该参数, 行为不变。
    """

    def deco(cls: type) -> type:
        _put(_APP, "窗口 App", name, cls)
        return cls

    return deco


def register_mods(name: str) -> Callable[[type], type]:
    """注册作品专属 mod 能力提供者类(装饰提供者类, 原样返回)。

    契约: ``provider(game)`` 构造(game 是 apis.basic.Game 门面; 作品包内摸
    ``game._impl`` 是同层操作), 其**公开方法**(非 ``_`` 开头、callable)被
    ``ModApi(game)`` 收割进分层命名空间(见 apis/modding.py)。
    归属声明: 方法/类上用 ``@mod_namespace("player")`` 标注挂在哪个命名
    空间下(往核心命名空间加方法, 或开一棵作品专属的新命名空间, 如
    th07 的 ``api.border``); 未标注的方法默认挂到以作品名命名的命名空间
    (如 ``api.th07``)。作品专属机制(如 th07 樱点/结界)经此喂给 ModApi,
    不进通用核。
    """

    def deco(cls: type) -> type:
        _put(_MODS, "mod 能力提供者", name, cls)
        return cls

    return deco


def mod_namespace(name: str) -> Callable[[Any], Any]:
    """声明作品 mod 能力的命名空间归属(装饰提供者方法, 或提供者类作全类默认)。

    配合 ``@register_mods(name)`` 使用 —— ``ModApi(game)`` 收割提供者公开
    方法时按此归属挂载::

        @register_mods("th07")
        class Th07Mods:
            @mod_namespace("player")     # → api.player.set_cherry (加入核心命名空间)
            def set_cherry(self, value): ...

            @mod_namespace("border")     # → api.border.border_break (新命名空间)
            def border_break(self): ...

    方法级标注优先; 未标注的方法回落到类级标注, 都没有则挂到以作品名命名
    的命名空间。与目标命名空间既有成员重名在 ``ModApi`` 构造时 fail fast
    (ValueError), 不许静默覆盖通用核。
    """
    if not name:
        raise ValueError("mod 能力的命名空间名不能为空")

    def deco(obj: Any) -> Any:
        obj._mod_namespace = name
        return obj

    return deco


#: 容器实现类(装饰器保型: 返回的还是被装饰的那个具体类)
_ArchiveT = TypeVar("_ArchiveT", bound="ArchiveBase")


def register_archive(
    games: str | Sequence[str],
    *,
    format_name: str | None = None,
) -> Callable[[type[_ArchiveT]], type[_ArchiveT]]:
    """注册资源包容器格式(装饰 ArchiveBase 子类, 原样返回)。

    一种格式常服务多部作品(如 pbgz 之于 th08/th09), 故 ``games`` 收字符串
    或字符串序列; 格式名缺省取类的 ``format_name`` 类属性。格式表按格式名
    登记一次(同名同类幂等, 同名换类报错), 作品表按作品名登记, 重复报
    DuplicateRegistrationError。

    Args:
        games: 本格式服务的作品名(单个或多个)
        format_name: 格式标识; 缺省取 ``cls.format_name``
    """
    names = [games] if isinstance(games, str) else list(games)
    if not names:
        raise ValueError("register_archive 至少要指定一个作品名")

    def deco(cls: type[_ArchiveT]) -> type[_ArchiveT]:
        fmt = format_name or getattr(cls, "format_name", "")
        if not fmt:
            raise ValueError(
                f"{cls.__name__} 未声明格式名: 传 format_name= 或设类属性 format_name"
            )
        spec = ArchiveSpec(container_cls=cls, format_name=fmt)
        known = _ARCHIVE_FORMAT.get(fmt)
        if known is None:
            _ARCHIVE_FORMAT[fmt] = spec
        elif known.container_cls is not cls:
            raise DuplicateRegistrationError(
                f"资源包格式重复注册: {fmt!r} (已注册 {known.container_cls.__name__})"
            )
        for game in names:
            _put(_ARCHIVE, "资源包格式", game, spec)
        return cls

    return deco


def get_archive_spec(name: str) -> ArchiveSpec:
    """按作品名取容器格式; 未注册报带已注册列表的 NotRegisteredError。"""
    if name not in _ARCHIVE:
        raise NotRegisteredError(
            f"作品 {name!r} 未注册资源包格式 (已注册: {sorted(_ARCHIVE)})"
        )
    return _ARCHIVE[name]


def get_archive_format(format_name: str) -> ArchiveSpec:
    """按格式名取容器格式; 未注册报带已注册列表的 NotRegisteredError。"""
    if format_name not in _ARCHIVE_FORMAT:
        raise NotRegisteredError(
            f"未注册的资源包格式: {format_name!r} (已注册: {registered_archives()})"
        )
    return _ARCHIVE_FORMAT[format_name]


def registered_archives() -> list[str]:
    """全部已注册资源包格式名, 排序返回(认头遍历按此顺序)。"""
    return sorted(_ARCHIVE_FORMAT)


def registered_games() -> list[str]:
    """全部已注册作品名(任一维度出现即算), 排序返回。"""
    return sorted(
        set(_ECL)
        | set(_ANM)
        | set(_HOOKS)
        | set(_WORLD)
        | set(_DATA)
        | set(_APP)
        | set(_MODS)
        | set(_ARCHIVE)
    )


# ---- 默认作品(Game()/TouhouWorld() 等不传 game= 时的构造对象) ----
#: 显式声明制: 作品包在自己的注册处(data.py)自报, 不靠导入顺序推导
#: (测试进程另有假作品先注册也不受影响)。框架层不散落 "th07" 字面量默认值
#: (分层红线, tests/test_api.py 的泄漏守护钉死)。
_DEFAULT_GAME: str | None = None


def register_default_game(name: str) -> None:
    """声明框架默认作品; 同名重复声明幂等, 改判他人报 DuplicateRegistrationError。"""
    global _DEFAULT_GAME
    if _DEFAULT_GAME is not None and _DEFAULT_GAME != name:
        raise DuplicateRegistrationError(
            f"默认作品重复声明: {_DEFAULT_GAME!r} 在先, 拒绝改判 {name!r}"
        )
    _DEFAULT_GAME = name


def default_game() -> str:
    """框架默认作品名; 尚无作品声明(作品包未导入)时报 NotRegisteredError。"""
    if _DEFAULT_GAME is None:
        raise NotRegisteredError(
            "尚无默认作品(作品包导入时经 register_default_game 声明)"
        )
    return _DEFAULT_GAME


# ---- 渲染后端维度(与作品名正交: 后端名 → Renderer 实现类) ----


def register_renderer(name: str) -> Callable[[type], type]:
    """注册渲染后端(装饰 Renderer 实现类, 原样返回)。

    构造契约: ``cls(data_path=None)``(资源包路径, 懒加载容错); 协议面见
    engine/render/__init__.py 的 Renderer。``GameApp(renderer=name)`` 经
    get_renderer 解析; "pygame" 为默认后端。
    """

    def deco(cls: type) -> type:
        _put(_RENDERER, "渲染后端", name, cls)
        return cls

    return deco


def registered_renderers() -> list[str]:
    """全部已注册渲染后端名, 排序返回。"""
    return sorted(_RENDERER)


def get_renderer(name: str) -> type:
    """按名取渲染后端类; 未注册报带已注册列表的 NotRegisteredError。"""
    if name not in _RENDERER:
        raise NotRegisteredError(
            f"未注册的渲染后端: {name!r} (已注册: {registered_renderers()})"
        )
    return _RENDERER[name]


@overload
def get_game(name: str | None, report_err: Literal[False]) -> GameSpec | None: ...


@overload
def get_game(name: str | None, report_err: Literal[True] = True) -> GameSpec: ...


def get_game(name: str | None, report_err: bool = True) -> GameSpec | None:
    """按作品名取注册描述; 未注册时，如果`report_err`为`True`，则报带已注册列表的 NotRegisteredError。"""
    if (
        name not in _ECL
        and name not in _ANM
        and name not in _HOOKS
        and name not in _WORLD
        and name not in _DATA
        and name not in _APP
        and name not in _MODS
        and name not in _ARCHIVE
    ):
        if report_err:
            raise NotRegisteredError(
                f"未注册的作品: {name!r} (已注册: {registered_games()})"
            )
        return None
    return GameSpec(
        name=name,
        ecl=_ECL.get(name),
        anm=_ANM.get(name),
        hooks=_HOOKS.get(name, GameHooks()),
        world=_WORLD.get(name),
        data=_DATA.get(name),
        app=_APP.get(name),
        mods=_MODS.get(name),
        archive=_ARCHIVE.get(name),
    )
