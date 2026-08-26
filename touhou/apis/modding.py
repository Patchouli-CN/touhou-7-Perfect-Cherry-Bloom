"""对外公共魔改 API —— 给 mod 制作者用的写入门面(分层命名空间结构)。

与 basic.py 的读写分离约定: ``Game`` = 只读观测, ``ModApi`` = 改写。
**ModApi 是官方魔改口子: 这里的写操作(无敌/资源直改/自定义弹幕/画面
覆盖层)绕过正常游戏规则**, 仅供魔改/实验/调试, 不计入正常对局语义。

作品无关约定(与 basic.py 同一铁律: 本层不 import games.*, AST 守护钉死):

- 只面向 touhou/types.py 的 ``ModdableEngine`` 可变协议编程, 成员命名是
  作品无关语义(power/bombs/lives/invulnerability_timer/score/boss), 不出现
  任何作品的内部字段名;
- 引擎不满足的成员走"getattr 能力位探测 + 清晰报错": 抛 NotImplementedError
  (带缺失成员名的中文说明), 不静默失败;
- 作品数值语义经注册表提供(满火力 = ``GameData.full_power``), 弹型模板号
  等参数含义由作品定义, 本模块只透传不做假设。

分层纪律(防 ModApi 堆成 God Class) —— 命名空间结构::

    api = ModApi(game)
    api.player   自机: god_mode/set_invulnerability_time/set_power/set_bombs/
                 set_lives + pos/full_power(属性)
    api.boss     Boss: exists(属性)/set_life/set_pos
    api.bullets  敌弹: fire/fire_ring/clear + count(属性)
    api.score    分数: add
    api.gui      画面覆盖层: line/circle/polyline/text(立即模式, 见
                 engine/render/overlay.py; headless 下 no-op)
    api.border   ← 作品注册的新命名空间(th07 结界), 非通用核

- **通用核**(player/boss/bullets/score/gui 五个命名空间类的真方法,
  IDE/mypy 全支持)只收**全系列共有概念**(无敌/火力/残机/Bomb/分数/
  自定义弹幕/Boss 血量/覆盖层);
- **作品专属机制**(th07 樱点/结界、未来 th08 时刻/人妖槽…)永远住
  ``games/thXX/mods.py``, 经 ``@register_mods(name)``(touhou/registry.py)
  登记提供者类, 方法用 ``@mod_namespace(ns)`` 声明归属: 指向核心命名空间
  (如 "player")= 往里加方法; 新名字(如 "border")= 注册整棵新命名空间;
  未声明的方法挂到以作品名命名的命名空间(如 ``api.th07``);
- 收割时与目标命名空间既有成员重名 **fail fast**(ValueError), 不许静默
  覆盖通用核;
- ``available()`` 返回分层清单(命名空间 → {能力名: 一句话说明}),
  ``is_capabilities_exist("player.set_cherry")`` 按点路径探测。
"""
from __future__ import annotations

import math
from typing import Any, Callable

from ..engine.bullets import Aim, Burst
from ..engine.render.overlay import (
    OverlayCircle,
    OverlayLine,
    OverlayPolyline,
    OverlaySink,
    OverlayText,
    SINK,
)
from ..registry import GameData
from ..utils import Vec2
from .basic import Game

# 再导出: mod 脚本 ``from touhou.apis.modding import ModApi, Burst, Aim, Vec2``
# 一条龙, 不必再摸 engine.bullets 内部模块
__all__ = [
    "Aim",
    "BossMods",
    "BulletsMods",
    "Burst",
    "GameModsNamespace",
    "GuiMods",
    "ModApi",
    "PlayerMods",
    "ScoreMods",
    "Vec2",
]

#: getattr 三参默认值的哨兵(区分"成员缺失"与"成员值为 None")
_MISSING: Any = object()

#: 通用核能力的一句话说明(available() 用; 手写映射, 与下方命名空间类的
#: 真方法一一对应; 作品能力收割时并入, 见 ModApi.available)
_CORE_CAPABILITIES: dict[str, dict[str, str]] = {
    "player": {
        "god_mode": "无敌挂(= set_invulnerability_time(999), 须每帧调用)",
        "set_invulnerability_time": "自机无敌计时直改(帧)",
        "set_power": "火力直改(0..full_power, 上限取自作品数值表)",
        "set_bombs": "Bomb 数直改(>=0)",
        "set_lives": "残机数直改(>=0)",
        "pos": "自机坐标 (x, y)(属性)",
        "full_power": "满火力值(属性, 取自作品数值表)",
    },
    "boss": {
        "exists": "场上是否有 Boss(属性)",
        "set_life": "Boss 当前生命直改(不改上限 max_life)",
        "set_pos": "Boss 位置直改(Vec2 不可变, 整体重赋 pos)",
    },
    "bullets": {
        "fire": "发射一发自定义 Burst 弹幕",
        "fire_ring": "便捷: 中心放一圈单层匀速环形弹幕",
        "clear": "清屏: 移除全部敌弹",
        "count": "场上敌弹总数(属性)",
    },
    "score": {
        "add": "真实分加算(直接入账, 不走作品计分规则)",
    },
    "gui": {
        "line": "覆盖层画线段(立即模式, 本帧有效)",
        "circle": "覆盖层画圆(立即模式, 本帧有效)",
        "polyline": "覆盖层画折线(立即模式, 本帧有效)",
        "text": "覆盖层画文字(立即模式, 本帧有效)",
    },
}

#: 通用核命名空间名(作品新命名空间不许与 ModApi 既有成员重名, 见收割)
_CORE_NAMESPACES = tuple(_CORE_CAPABILITIES)


class _ModNamespace:
    """命名空间基座: 持有引擎 impl 锚点 + 能力位探测(写入面缺失即报错)。

    探测风格照 basic.py 的 getattr 回落, 但写入面缺失即报错(不静默失败)。
    """

    def __init__(self, impl: Any) -> None:
        # 与 basic.Game 同层的协议锚点(apis 内部共享 _impl, 不算破封装);
        # 协议面见 touhou/types.py 的 ModdableEngine(鸭子满足, 无 adapter)
        self._impl = impl

    def _require_writable(self, obj: object, name: str, purpose: str) -> None:
        """要求 obj.name 存在且可赋值, 否则抛带缺失成员名的中文错误。"""
        if getattr(obj, name, _MISSING) is _MISSING:
            raise NotImplementedError(
                f"当前作品引擎({type(self._impl).__name__})不支持{purpose}: "
                f"缺少成员 {type(obj).__name__}.{name}"
                f"(ModdableEngine 协议要求, 见 touhou/types.py)")
        # 只读 property(类上有 property 但无 setter)同样视为不可写;
        # 实例属性/msgspec 字段无类级 property, 走不到这个分支
        cls_attr = getattr(type(obj), name, None)
        if isinstance(cls_attr, property) and cls_attr.fset is None:
            raise NotImplementedError(
                f"当前作品引擎({type(self._impl).__name__})不支持{purpose}: "
                f"成员 {type(obj).__name__}.{name} 为只读"
                f"(ModdableEngine 协议要求可写, 见 touhou/types.py)")


class PlayerMods(_ModNamespace):
    """``ModApi.player`` —— 自机写入面: 无敌/火力/Bomb/残机 + 坐标观测。"""

    def __init__(self, impl: Any, full_power: int) -> None:
        super().__init__(impl)
        # 满火力值取自作品数值表(GameData.full_power); 作品未登记数值表时
        # 回落 GameData 的自带默认值(注册表契约: 空 GameData() = 未提供)
        self._full_power = full_power

    @property
    def full_power(self) -> int:
        """满火力值(作品数值表 GameData.full_power)。"""
        return self._full_power

    # ---- 无敌 ----
    def god_mode(self) -> None:
        """ 无敌挂 """
        return self.set_invulnerability_time(999)

    def set_invulnerability_time(self, timer: int = 999) -> None:
        """无敌时间设置: 把自机无敌计时重置为 ``timer`` 帧。

        引擎每帧递减该计时(归零即恢复可中弹), 故须在输入策略(policy)里
        **每帧调用**才能持续无敌; 单次调用只保 ``timer`` 帧。
        """
        player = self._impl.player
        self._require_writable(player, "invulnerability_timer",
                               "无敌改写(god_mode)")
        setattr(player, "invulnerability_timer", timer)

    # ---- 资源直改(公共签名用 int; 引擎内部 float 表示不外泄) ----
    def set_power(self, power: int) -> None:
        """火力直改为 ``power``(合法域 0..``self.full_power``, 上限取自作品数值表)。"""
        if not 0 <= power <= self._full_power:
            raise ValueError(
                f"火力 {power} 超出本作品合法范围 0..{self._full_power}"
                f"(满火力值来自注册表 GameData.full_power)")
        self._require_writable(self._impl, "power", "火力改写")
        setattr(self._impl, "power", float(power))

    def set_bombs(self, bombs: int) -> None:
        """Bomb 数直改为 ``bombs``(>=0; HUD 显示即此值)。"""
        if bombs < 0:
            raise ValueError(f"Bomb 数 {bombs} 非法(须 >= 0)")
        self._require_writable(self._impl, "bombs", "Bomb 数改写")
        setattr(self._impl, "bombs", float(bombs))

    def set_lives(self, lives: int) -> None:
        """残机数直改为 ``lives``(>=0; HUD 显示即此值)。"""
        if lives < 0:
            raise ValueError(f"残机数 {lives} 非法(须 >= 0)")
        self._require_writable(self._impl, "lives", "残机数改写")
        setattr(self._impl, "lives", float(lives))

    # ---- 观测补充(mod 常用的坐标; 全景观测仍走 Game.snapshot) ----
    @property
    def pos(self) -> tuple[float, float]:
        """自机坐标 (x, y)(自定义弹幕起点等场景用)。"""
        pos = self._impl.player.pos
        return (pos.x, pos.y)


class BossMods(_ModNamespace):
    """``ModApi.boss`` —— Boss 写入面(场上无 Boss 时写操作报中文错)。"""

    @property
    def exists(self) -> bool:
        """场上当前是否有 Boss。"""
        return getattr(self._impl, "boss", None) is not None

    def _require_boss(self) -> Any:
        """取当前 Boss 对象; 无 Boss(或未进 Boss 战)时报中文错。"""
        boss = getattr(self._impl, "boss", None)
        if boss is None:
            raise ValueError("当前场上没有 Boss(未进 Boss 战或已击破), "
                             "先判 api.boss.exists 再写")
        return boss

    def set_life(self, life: float) -> None:
        """Boss 当前生命直改为 ``life``(不改上限 max_life)。

        跌破引擎生命阈值后, 阶段切换/清场仍由引擎正常流程驱动 —— 本方法
        只写数值, 不绕过作品机制语义。
        """
        boss = self._require_boss()
        self._require_writable(boss, "life", "Boss 生命改写")
        setattr(boss, "life", float(life))

    def set_pos(self, x: float, y: float) -> None:
        """Boss 位置直改为 (x, y)(Vec2 不可变, 写位置 = 整体重赋 pos 对象)。"""
        boss = self._require_boss()
        self._require_writable(boss, "pos", "Boss 位置改写")
        setattr(boss, "pos", Vec2(x, y))


class BulletsMods(_ModNamespace):
    """``ModApi.bullets`` —— 敌弹写入面: 自定义弹幕发射/清屏 + 计数观测。"""

    def fire(self, burst: Burst) -> int:
        """发射一发自定义 ``Burst``(弹幕参数全量直通引擎), 返回生成颗数。"""
        bullets = self._impl.bullets
        fire = getattr(bullets, "fire", None)
        if not callable(fire):
            raise NotImplementedError(
                f"当前作品引擎({type(self._impl).__name__})不支持自定义弹幕: "
                f"缺少成员 {type(bullets).__name__}.fire"
                f"(ModdableEngine 协议要求, 见 touhou/types.py)")
        return int(fire(burst))

    def fire_ring(self, x: float, y: float, *, arms: int = 24,
                  speed: float = 1.5, sprite: int = 1,
                  sprite_offset: int = 6) -> int:
        """便捷: 以 (x, y) 为中心发一圈 ``arms`` 颗的单层匀速环形弹幕。

        ``sprite``/``sprite_offset`` 是弹型模板号/变体偏移, **取值含义由作品
        定义**(th07: sprite 0..10 弹型模板, offset 为颜色/变体偏移, 数据见
        games.th07); 本 API 不做作品假设, 原样透传给引擎。返回生成颗数。
        """
        return self.fire(Burst(
            path=Vec2(x, y), base_angle=math.pi / 2, aim=Aim.RING_ABSOLUTE,
            arms=arms, rings=1, speed_a=speed, speed_b=speed, angle_step=0.0,
            sprite=sprite, sprite_offset=sprite_offset))

    def clear(self) -> None:
        """清屏: 移除全部敌弹(容器需有 clear() 能力位, 缺失报中文错)。

        只清弹体本身, 不动引擎的清弹窗口(screen_clear_time)等记账 ——
        与 Bomb/结界破裂的规则内清弹不同, 这是魔改直清。
        """
        bullets = self._impl.bullets
        clear = getattr(bullets, "clear", None)
        if not callable(clear):
            raise NotImplementedError(
                f"当前作品引擎({type(self._impl).__name__})不支持清屏: "
                f"缺少成员 {type(bullets).__name__}.clear"
                f"(ModdableEngine 协议要求, 见 touhou/types.py)")
        clear()

    # ---- 观测补充(mod 常用的计数; 全景观测仍走 Game.snapshot) ----
    @property
    def count(self) -> int:
        """场上敌弹总数(含出生特效态的弹)。"""
        return sum(1 for _ in self._impl.bullets.alive())


class ScoreMods(_ModNamespace):
    """``ModApi.score`` —— 分数写入面。"""

    def add(self, score: int) -> None:
        """真实分 += ``score``(直接入账, 不走作品的道具/符卡计分规则)。"""
        g = self._impl.globals
        self._require_writable(g, "score", "分数改写")
        setattr(g, "score", getattr(g, "score") + score)


class GuiMods:
    """``ModApi.gui`` —— 立即模式画面覆盖层(自定义导航线/文字弹出等)。

    调用即推入一条**只活一帧**的绘制命令到汇聚点
    (``engine/render/overlay.py`` 的进程级 ``SINK``); 渲染后端(窗口/观战
    模式的 pygame 后端)每帧取走并画在游戏区上层, 故**每帧都要重新推**
    (在 policy 里调用)。**headless 下是 no-op**: 没有后端消费时命令静默
    丢弃, 同一 policy 脚本两种模式通用。

    坐标系: 游戏区像素(th07: 384x448, 原点左上, **y 向下**), 与
    ``game.player_pos`` / ``bullets_array()`` 同一坐标系; 颜色 RGB 三元组。
    """

    def __init__(self, sink: OverlaySink = SINK) -> None:
        self._sink = sink

    def line(self, x1: float, y1: float, x2: float, y2: float, *,
             color: tuple[int, int, int] = (255, 255, 255),
             width: int = 1) -> None:
        """画一条线段 (x1, y1)-(x2, y2)(本帧有效)。"""
        self._sink.push(OverlayLine(x1, y1, x2, y2, color=color, width=width))

    def circle(self, x: float, y: float, radius: float, *,
               color: tuple[int, int, int] = (255, 255, 255),
               width: int = 1) -> None:
        """画一个圆 (x, y) 半径 ``radius``; ``width=0`` 为实心填充(本帧有效)。"""
        self._sink.push(
            OverlayCircle(x, y, radius, color=color, width=width))

    def polyline(self, points: list[tuple[float, float]] | tuple[tuple[float, float], ...],
                 *, color: tuple[int, int, int] = (255, 255, 255),
                 width: int = 1, closed: bool = False) -> None:
        """画一条折线(导航路线等); ``closed=True`` 首尾相连成多边形(本帧有效)。"""
        self._sink.push(OverlayPolyline(
            tuple((float(px), float(py)) for px, py in points),
            color=color, width=width, closed=closed))

    def text(self, x: float, y: float, content: str, *,
             color: tuple[int, int, int] = (255, 255, 255),
             size: int = 16) -> None:
        """画一段文字, 左上角锚在 (x, y)(自定义弹出提示; 本帧有效)。"""
        self._sink.push(OverlayText(x, y, content, color=color, size=size))


class GameModsNamespace:
    """作品注册的整棵新命名空间容器(如 th07 的 ``api.border``)。

    能力 = 收割进来的实例属性(已绑定方法); 与核心命名空间类同是
    "点号直达"的调用形态, 区别仅在没有预声明的真方法(作品专属,
    IDE 补全不到, 清单见 ``available()``)。
    """

    def __init__(self, ns_name: str, game_name: str) -> None:
        self._ns_name = ns_name
        self._game_name = game_name

    def __repr__(self) -> str:
        caps = sorted(k for k in self.__dict__ if not k.startswith("_"))
        return f"<ModApi 命名空间 {self._game_name}:{self._ns_name} {caps}>"


class ModApi:
    """mod 制作的官方入口: 包住一个 ``Game``(只读观测), 叠加写操作面。用法::

        mods = ModApi(game)
        def policy(game):                    # 输入策略每帧被调
            mods.player.god_mode()           # 无敌挂(计时每帧递减, 故要每帧重置)
            mods.player.set_power(mods.player.full_power)
            mods.gui.circle(*mods.player.pos, 32, color=(0, 255, 0))
            return Input(shoot=True)

    写操作绕过正常游戏规则(见模块 docstring); 观测仍走 Game 的只读属性。
    作品引擎需满足 ModdableEngine 协议(touhou/types.py): 不满足的成员
    调用时报 NotImplementedError(中文说明), 不静默失败。

    分层命名空间(纪律见模块 docstring):

    - 通用核: ``player``/``boss``/``bullets``/``score``/``gui`` 五个命名空间
      对象的**真方法**(IDE/mypy 全支持);
    - 作品能力: 由作品经 ``@register_mods(name)`` 登记、``@mod_namespace``
      声明归属的提供者方法 —— 或并入核心命名空间(如
      ``mods.player.set_cherry``), 或自成新命名空间(如
      ``mods.border.border_break``); 重名通用核成员在构造时 fail fast。
    """

    def __init__(self, game: Game) -> None:
        self.game = game
        # 与 basic.Game 同层的协议锚点(apis 内部共享 _impl, 不算破封装)
        impl = game._impl
        data = game.spec.data
        # 满火力值取自作品数值表(GameData.full_power); 作品未登记数值表时
        # 回落 GameData 的自带默认值(注册表契约: 空 GameData() = 未提供)
        full_power = (data if data is not None else GameData()).full_power
        self.player = PlayerMods(impl, full_power)
        self.boss = BossMods(impl)
        self.bullets = BulletsMods(impl)
        self.score = ScoreMods(impl)
        self.gui = GuiMods()
        # 作品能力收割记录: (命名空间名, 能力名, 已绑定方法); available() 用
        self._game_caps: list[tuple[str, str, Callable[..., Any]]] = []
        provider_cls = game.spec.mods
        if provider_cls is not None:
            self._harvest_provider(provider_cls(game), game.spec.name)

    # ---- 作品能力收割(命名空间归属版; 声明机制见 registry.mod_namespace) ----
    def _harvest_provider(self, provider: object, spec_name: str) -> None:
        """把提供者的公开方法按 ``@mod_namespace`` 归属收割进命名空间。"""
        # 类级默认归属(提供者类上 @mod_namespace 一次全类生效), 方法级优先
        class_ns = getattr(provider, "_mod_namespace", None)
        for name in dir(provider):
            if name.startswith("_"):
                continue
            member = getattr(provider, name)
            if not callable(member):
                continue
            ns_name = getattr(member, "_mod_namespace", None) \
                or class_ns or spec_name
            ns = self.__dict__.get(ns_name)
            if ns is None:
                # 整棵新命名空间: 名字不许与 ModApi 既有成员(类/实例)冲突
                if hasattr(ModApi, ns_name) or ns_name in self.__dict__:
                    raise ValueError(
                        f"作品 {spec_name!r} 的 mod 命名空间 {ns_name!r} "
                        f"与 ModApi 既有成员重名(请给命名空间改名)")
                ns = GameModsNamespace(ns_name, spec_name)
                self.__dict__[ns_name] = ns
            # fail fast: 与目标命名空间既有成员(通用核真方法/已收割能力)
            # 重名不许静默覆盖
            if hasattr(ns, name):
                raise ValueError(
                    f"作品 {spec_name!r} 的 mod 能力 {ns_name}.{name} "
                    f"与命名空间既有成员重名(请给作品能力改名; "
                    f"通用核成员见 available())")
            setattr(ns, name, member)
            self._game_caps.append((ns_name, name, member))

    # ---- 未知成员的中文提示(必须是 AttributeError, hasattr 语义才正确) ----
    def __getattr__(self, name: str) -> Any:
        game = self.__dict__.get("game")
        spec_name = game.spec.name if game is not None else "?"
        spec_mods = game.spec.mods if game is not None else None
        namespaces = sorted(k for k in self.__dict__ if not k.startswith("_")
                            and k != "game")
        hint = ""
        if spec_mods is None:
            hint = (f"; 作品 {spec_name!r} 未注册 mod 能力(需要 "
                    f"@register_mods({spec_name!r}) 装饰能力提供者类, "
                    f"见 touhou/registry.py)")
        raise AttributeError(
            f"ModApi 没有成员 {name!r}: 现有命名空间 {namespaces}{hint}"
            f"(全量能力清单见 available())")

    def is_capabilities_exist(self, path: str) -> bool:
        """能力是否存在, 点路径 ``"命名空间.能力名"``(如 ``"player.set_cherry"``)。

        通用核真方法与作品能力同口径探测; 裸名(无点号)恒 False。
        """
        ns_name, _, cap = path.partition(".")
        if not cap:
            return False
        ns = self.__dict__.get(ns_name)
        return ns is not None and hasattr(ns, cap)

    def available(self) -> dict[str, dict[str, str]]:
        """全部可用能力的分层清单: 命名空间 → {能力名: 一句话说明}。

        通用核用手写映射(模块级 ``_CORE_CAPABILITIES``); 作品能力的说明
        取方法 docstring 首行, 按其命名空间归属并入。
        """
        out = {ns: dict(caps) for ns, caps in _CORE_CAPABILITIES.items()}
        for ns_name, name, fn in self._game_caps:
            doc = (getattr(fn, "__doc__", None) or "").strip()
            out.setdefault(ns_name, {})[name] = \
                doc.splitlines()[0] if doc else "(无说明)"
        return out
