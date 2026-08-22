"""公共类型门面 —— 给外部使用者与 IDE/类型检查器的集中类型定义。

分层约定:

- 本模块运行时只依赖 typing/pathlib, **不 import 任何引擎实现**,
  因此引擎内部(ecl_host 等)与 api.py 都可安全引用, 无循环 import。
- 别名与 Protocol: ``PathLike`` / ``KeysTuple`` / ``PosLike`` /
  ``Positioned`` / ECL 宿主钩子(``SetBossHook`` 等), 运行时可正常导入。
- 公共数据类型(Input/GameEvent/Snapshot/枚举/TouhouWorldEventStream…)
  正放在 touhou.api, 并随 ``touhou`` 顶层导出; 这里仅在 TYPE_CHECKING 下
  再导出, 供 IDE ``from touhou.types import Input`` 解析。
  (运行时若直接 import api 会形成 api → engine → types → api 循环,
  故运行时代码请 ``from touhou.api import …``。)
"""
from __future__ import annotations

from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Iterable,
    Protocol,
    Sequence,
    TypeAlias,
    runtime_checkable,
)

if TYPE_CHECKING:
    # 公共数据类型再导出(仅类型检查期可见; 运行时请 from touhou.api import …)
    from .apis.basic import (
        BossSnapshot,
        BulletSnapshot,
        Character,
        Difficulty,
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
    # 引擎侧公共结构(仅类型检查期): ECL 宿主接口/敌人状态/道具收集上下文
    from .engine.ecl import EclEnemyState, EclHost
    from .games.th07.items import GameContext
    # 协议符合性静态断言用(见文件末尾; 运行时不 import, 无循环)
    from .games.th07.world import PerfectCherryBloom

__all__ = [
    "BeginSpellcardHook",
    "BossFace",
    "BulletFace",
    "EndSpellcardHook",
    "EnemyFace",
    "EventList",
    "GameEngine",
    "GameGlobalsFace",
    "IntHook",
    "ItemFace",
    "KeysTuple",
    "LaserFace",
    "PathLike",
    "PlayerFace",
    "PosLike",
    "Positioned",
    "SetBossHook",
]


# ---- 基础别名 ----

#: 路径参数(str 或 Path 皆可)。
PathLike: TypeAlias = str | Path

#: 一帧按键元组 (left, right, up, down, focus, shoot) —— 即 ``Input._keys()``
#: 的产物, 也是 ``PerfectCherryBloom.tick(keys=…)`` 的完整形态
#: (tick 另兼容无 shoot 的 5 元旧格式)。
KeysTuple: TypeAlias = tuple[bool, bool, bool, bool, bool, bool]

#: 一帧内发生的事件列表(``Game.step`` 的返回类型)。注解专用(运行时是字符串,
#: GameEvent 运行时定义在 touhou.api, 避免循环 import)。
EventList: TypeAlias = "list[GameEvent]"


# ---- 结构式 Protocol(鸭子类型, 引擎实体天然满足) ----

@runtime_checkable
class PosLike(Protocol):
    """有 x/y 坐标的位置(``Vec2``/``Vec3``/``pygame.Vector2`` 皆匹配)。

    成员声明为只读 property, 以兼容 frozen dataclass(如 Vec2)与可变结构。
    """

    @property
    def x(self) -> float: ...

    @property
    def y(self) -> float: ...


class Positioned(Protocol):
    """有 ``pos.x``/``pos.y`` 的世界实体(子弹/敌人/道具/激光/玩家…)。"""

    @property
    def pos(self) -> PosLike: ...


# ---- ECL 宿主事件钩子(games/th07/ecl_host.py 的 GameEclHost.on_* 回调签名) ----

#: SET_BOSS: (boss 槽位 idx, 敌人状态; None = 清除该槽)。
SetBossHook: TypeAlias = Callable[[int, "EclEnemyState | None"], None]

#: BEGIN_SPELLCARD: (敌人状态, gui 编号, 全局符卡 idx, 符卡名)。
BeginSpellcardHook: TypeAlias = Callable[["EclEnemyState", int, int, str], None]

#: END_SPELLCARD / 符卡超时(捕获失败记账): (敌人状态,)。
EndSpellcardHook: TypeAlias = Callable[["EclEnemyState"], None]

#: 整数值回调(SET_POWER / ADD_CHERRY_PLUS)。
IntHook: TypeAlias = Callable[[int], None]


# ---- GameEngine 协议(api.Game 门面只面向它编程, 不认具体作品) ----
# 全成员声明为只读 property: 协变判定让 int 字段可满足 float 声明
# (如 EclEnemy.life), msgspec.Struct/普通类的同名属性天然满足。
# th07 的 games.th07.world.PerfectCherryBloom 鸭子满足本协议(无 adapter),
# 静态符合性由文件末尾的 _perfect_cherry_bloom_satisfies_game_engine 钉住。


class GameGlobalsFace(Protocol):
    """对局计数/分数访问面(ZunGlobals 的只读形态)。"""

    @property
    def score(self) -> int: ...

    @property
    def deaths(self) -> int: ...

    @property
    def bombs_used(self) -> float: ...

    @property
    def spell_cards_captured(self) -> int: ...

    @property
    def cherry_max(self) -> int: ...

    @property
    def graze_in_total(self) -> int: ...


class BossFace(Positioned, Protocol):
    """Boss 状态面(无 Boss 时引擎的 ``boss`` 为 None)。"""

    @property
    def name(self) -> str: ...

    @property
    def life(self) -> float: ...

    @property
    def max_life(self) -> float: ...

    @property
    def is_active(self) -> int: ...        # 0=无符卡 1=进行中 2=超时失败

    @property
    def spellcard_idx(self) -> int: ...    # -1 = 非符卡阶段


class BorderFace(Protocol):
    """结界状态面(无结界系统的作品给一个 active 恒 False 的对象即可)。"""

    @property
    def active(self) -> bool: ...


class PlayerStateFace(Protocol):
    """玩家状态枚举面(IntEnum 的 .name)。"""

    @property
    def name(self) -> str: ...


class PlayerFace(Positioned, Protocol):
    """玩家只读形态。"""

    @property
    def state(self) -> PlayerStateFace: ...

    @property
    def focus(self) -> bool: ...

    @property
    def invulnerability_timer(self) -> int: ...


class BulletFace(Positioned, Protocol):
    """敌弹只读形态。"""

    @property
    def angle(self) -> float: ...

    @property
    def speed(self) -> float: ...

    @property
    def sprite(self) -> int: ...


class EnemyFace(Positioned, Protocol):
    """敌人只读形态。"""

    @property
    def life(self) -> float: ...

    @property
    def radius(self) -> float: ...

    @property
    def is_boss(self) -> bool: ...


class ItemTypeFace(Protocol):
    """道具类型枚举面(IntEnum 的 .name)。"""

    @property
    def name(self) -> str: ...


class ItemFace(Positioned, Protocol):
    """道具只读形态。"""

    @property
    def type(self) -> ItemTypeFace: ...


class LaserFace(Positioned, Protocol):
    """激光只读形态(state 为三态机的 int: 0=出现 1=全宽命中 2=消散)。"""

    @property
    def angle(self) -> float: ...

    @property
    def width(self) -> float: ...

    @property
    def state(self) -> int: ...

    @property
    def in_use(self) -> bool: ...


class BulletWorldFace(Protocol):
    """敌弹容器。"""

    def alive(self) -> Iterable[BulletFace]: ...


class EnemyHostFace(Protocol):
    """敌人容器。"""

    def alive(self) -> Iterable[EnemyFace]: ...


class ItemWorldFace(Protocol):
    """道具容器。"""

    def alive(self) -> Iterable[ItemFace]: ...


class LaserWorldFace(Protocol):
    """激光容器(``lasers`` 为全部槽位, 调用方按 in_use 过滤)。"""

    @property
    def lasers(self) -> Sequence[LaserFace]: ...


class GameEngine(Protocol):
    """对局引擎协议 —— 一部作品的"主逻辑类"应满足的最小面。

    api.Game 的 _probe/_diff_events/snapshot/phase 只消费这里声明的成员;
    作品的专属探测逻辑不进协议, 走可选能力位(api 用 getattr 回落 False):
    - ``spellcard_active() -> bool``: 符卡进行中(无符卡概念可不实现)
    - ``msg_active() -> bool``:       对话/剧情进行中(无对话概念可不实现)
    """

    # ---- 帧/关/终局状态 ----
    @property
    def frame(self) -> int: ...

    @property
    def stage_no(self) -> int: ...

    @property
    def game_over(self) -> bool: ...

    @property
    def cleared(self) -> bool: ...

    @property
    def result(self) -> dict | None: ...       # 总结算(门面只判 None/读出)

    @property
    def stage_results(self) -> Any: ...        # 过关结算快照(门面只判 None)

    @property
    def ending(self) -> Any: ...               # 结局数据(门面只判 None)

    # ---- globals / 资源访问面 ----
    @property
    def globals(self) -> GameGlobalsFace: ...

    @property
    def lives(self) -> float: ...

    @property
    def bombs(self) -> float: ...

    @property
    def power(self) -> float: ...

    # cherry(樱点)是 th07 专属概念, 不进必选协议; 门面经
    # ``getattr(impl, "cherry", 0)`` 能力位读取(无樱点的作品得 0)。

    # ---- boss / 结界 / 实体容器(只读形态) ----
    @property
    def boss(self) -> BossFace | None: ...

    @property
    def border(self) -> BorderFace: ...

    @property
    def player(self) -> PlayerFace: ...

    @property
    def bullets(self) -> BulletWorldFace: ...

    @property
    def host(self) -> EnemyHostFace: ...

    @property
    def items(self) -> ItemWorldFace: ...

    @property
    def lasers(self) -> LaserWorldFace: ...

    # ---- 推进与控制 ----
    def tick(self, *, keys: tuple[bool, ...] | None = None, bomb: bool = False,
             advance: bool = False, skip: bool = False) -> None: ...

    def enter_stage(self, stage_no: int) -> None: ...

    def finalize_game_over(self) -> None: ...

    def finish_ending(self) -> None: ...


def _perfect_cherry_bloom_satisfies_game_engine(
        impl: "PerfectCherryBloom") -> GameEngine:
    """静态断言: th07 主逻辑天然满足 GameEngine 协议(鸭子, 无 adapter)。

    仅供 mypy 检查(协议面与实现对漂移时这里报错); 运行时不被调用。
    """
    return impl
