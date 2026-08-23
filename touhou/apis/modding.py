"""对外公共魔改 API —— 给 mod 制作者用的写入门面。

与 basic.py 的读写分离约定: ``Game`` = 只读观测, ``ModApi`` = 改写。
**ModApi 是官方魔改口子: 这里的写操作(无敌/资源直改/自定义弹幕)绕过正常
游戏规则**, 仅供魔改/实验/调试, 不计入正常对局语义。

作品无关约定(与 basic.py 同一铁律: 本层不 import games.*, AST 守护钉死):

- 只面向 touhou/types.py 的 ``ModdableEngine`` 可变协议编程, 成员命名是
  作品无关语义(power/bombs/lives/invulnerability_timer/score), 不出现
  任何作品的内部字段名;
- 引擎不满足的成员走"getattr 能力位探测 + 清晰报错": 抛 NotImplementedError
  (带缺失成员名的中文说明), 不静默失败;
- 作品数值语义经注册表提供(满火力 = ``GameData.full_power``), 弹型模板号
  等参数含义由作品定义, 本模块只透传不做假设。

分层纪律(防 ModApi 堆成 God Class):

- **通用核**(本类的真方法, IDE/mypy 全支持)只收**全系列共有概念**
  (无敌/火力/残机/Bomb/分数/自定义弹幕);
- **作品专属机制**(th07 樱点/结界、未来 th08 时刻/人妖槽…)永远住
  ``games/thXX/mods.py``, 经 ``@register_mods(name)``(touhou/registry.py)
  登记提供者类, ``ModApi.__init__`` 实例化提供者并把其公开方法收割进
  ``capabilities`` 能力表; 调用走 ``__getattr__`` 查表分发(只查表,
  不穿透 impl 动态找成员), ``available()`` 列出全量能力清单。
"""
from __future__ import annotations

import math
from typing import Any, Callable

from ..engine.bullets import Aim, Burst
from ..registry import GameData
from ..utils import Vec2
from .basic import Game

# 再导出: mod 脚本 ``from touhou.apis.modding import ModApi, Burst, Aim, Vec2``
# 一条龙, 不必再摸 engine.bullets 内部模块
__all__ = ["Aim", "Burst", "ModApi", "Vec2"]

#: getattr 三参默认值的哨兵(区分"成员缺失"与"成员值为 None")
_MISSING: Any = object()

#: 通用核能力的一句话说明(available() 用; 手写映射, 与下方真方法一一对应)
_CORE_CAPABILITIES: dict[str, str] = {
    "god_mode": "无敌挂(= set_invulnerability_time(999), 须每帧调用)",
    "set_invulnerability_time": "自机无敌计时直改(帧)",
    "set_power": "火力直改(0..full_power, 上限取自作品数值表)",
    "set_bombs": "Bomb 数直改(>=0)",
    "set_lives": "残机数直改(>=0)",
    "add_score": "真实分加算(直接入账, 不走作品计分规则)",
    "fire": "发射一发自定义 Burst 弹幕",
    "fire_ring": "便捷: 中心放一圈单层匀速环形弹幕",
}


class ModApi:
    """mod 制作的官方入口: 包住一个 ``Game``(只读观测), 叠加写操作面。用法::

        mods = ModApi(game)
        def policy(game):            # 输入策略每帧被调
            mods.god_mode()          # 无敌挂(计时每帧递减, 故要每帧重置)
            mods.set_power(mods.full_power)
            return Input(shoot=True)

    写操作绕过正常游戏规则(见模块 docstring); 观测仍走 Game 的只读属性。
    作品引擎需满足 ModdableEngine 协议(touhou/types.py): 不满足的成员
    调用时报 NotImplementedError(中文说明), 不静默失败。

    能力分两层(分层纪律见模块 docstring):

    - 通用核: 本类真方法(god_mode/set_power/fire_ring/...), IDE/mypy 全支持;
    - 作品能力: ``capabilities`` 表(公开字段, 值是已绑定方法, 可直接调用),
      由作品经 ``@register_mods(name)`` 登记的提供者类收割而来;
      ``mods.set_cherry(...)`` 这类调用经 ``__getattr__`` 查表分发。
    """

    def __init__(self, game: Game) -> None:
        self.game = game
        # 与 basic.Game 同层的协议锚点(apis 内部共享 _impl, 不算破封装)
        self._impl = game._impl
        data = game.spec.data
        # 满火力值取自作品数值表(GameData.full_power); 作品未登记数值表时
        # 回落 GameData 的自带默认值(注册表契约: 空 GameData() = 未提供)
        self.full_power: int = (data if data is not None else GameData()).full_power
        # 作品专属能力表: 能力名 → 已绑定方法; 作品未注册 mods 维度时为空表
        # (此时调作品能力报"未注册 mod 能力"的中文提示, 见 __getattr__)
        self.capabilities: dict[str, Callable[..., Any]] = {}
        provider_cls = game.spec.mods
        if provider_cls is not None:
            provider = provider_cls(game)
            for name in dir(provider):
                if name.startswith("_"):
                    continue
                member = getattr(provider, name)
                if not callable(member):
                    continue
                # fail fast: 与通用核真方法/既有实例字段重名不许静默被核覆盖
                if hasattr(ModApi, name) or name in self.__dict__:
                    raise ValueError(
                        f"作品 {game.spec.name!r} 的 mod 能力 {name!r} "
                        f"与 ModApi 通用核成员重名(请给作品能力改名; "
                        f"通用核成员见 available())")
                self.capabilities[name] = member

    # ---- 作品能力表分发(只查表, 不穿透 _impl 动态找成员) ----
    def __getattr__(self, name: str) -> Callable[..., Any]:
        """查 ``capabilities`` 表分发作品专属能力; 未知名抛 AttributeError。

        必须是 AttributeError, hasattr/getattr 语义才正确; 报错信息列出
        该作品已注册的能力名单(未注册 mods 维度的作品给登记提示)。
        """
        caps: dict[str, Callable[..., Any]] = self.__dict__.get("capabilities", {})
        if name in caps:
            return caps[name]
        game = self.__dict__.get("game")
        spec_name = game.spec.name if game is not None else "?"
        spec_mods = game.spec.mods if game is not None else None
        if spec_mods is None:
            raise AttributeError(
                f"ModApi 没有能力 {name!r}: 作品 {spec_name!r} 未注册 "
                f"mod 能力(需要 @register_mods({spec_name!r}) 装饰能力"
                f"提供者类, 见 touhou/registry.py)")
        raise AttributeError(
            f"ModApi 没有能力 {name!r}(作品 {spec_name!r} 已注册能力: "
            f"{sorted(caps)}; 全量清单见 available())")

    def is_capabilities_exist(self, name: str) -> bool:
        """作品能力表是否含 ``name``(只查表, 通用核真方法不算)。"""
        return name in self.capabilities

    def available(self) -> dict[str, str]:
        """全部可用能力清单: 能力名 → 一句话说明(通用核 + 作品能力表)。

        作品能力的说明取方法 docstring 首行; 通用核用手写映射
        (模块级 ``_CORE_CAPABILITIES``)。
        """
        out = dict(_CORE_CAPABILITIES)
        for name, fn in self.capabilities.items():
            doc = (getattr(fn, "__doc__", None) or "").strip()
            out[name] = doc.splitlines()[0] if doc else "(无说明)"
        return out

    # ---- 能力位探测(风格照 basic.py 的 getattr 回落, 但写入面缺失即报错) ----
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

    # ---- 玩家 ----
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
        if not 0 <= power <= self.full_power:
            raise ValueError(
                f"火力 {power} 超出本作品合法范围 0..{self.full_power}"
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

    def add_score(self, score: int) -> None:
        """真实分 += ``score``(直接入账, 不走作品的道具/符卡计分规则)。"""
        g = self._impl.globals
        self._require_writable(g, "score", "分数改写")
        setattr(g, "score", getattr(g, "score") + score)

    # ---- 自定义弹幕 ----
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

    # ---- 观测补充(mod 常用的计数/坐标; 全景观测仍走 Game.snapshot) ----
    @property
    def bullet_count(self) -> int:
        """场上敌弹总数(含出生特效态的弹)。"""
        return sum(1 for _ in self._impl.bullets.alive())

    @property
    def player_pos(self) -> tuple[float, float]:
        """自机坐标 (x, y)(自定义弹幕起点等场景用)。"""
        pos = self._impl.player.pos
        return (pos.x, pos.y)
