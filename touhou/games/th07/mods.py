"""TH07(妖妖梦)作品专属 mod 能力 —— 樱点系/结界系。

分层纪律(见 apis/modding.py 模块 docstring): 全系列共有概念住 ModApi
通用核命名空间; th07 专属机制只住本文件, 经 ``@register_mods("th07")``
登记, 方法用 ``@mod_namespace(ns)`` 声明归属(touhou/registry.py):
``ModApi(game)`` 构造时实例化本提供者并把公开方法按归属收割 ——
樱点系并入核心命名空间 ``api.player``, 结界系自成新命名空间
``api.border``。作品包内摸 ``game._impl`` 是同层操作, 不算破封装。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, cast

from ...registry import mod_namespace, register_mods
from .bomb import BorderState
from .globals import CHERRY_MAX_RANGE
from .world import PerfectCherryBloom

if TYPE_CHECKING:
    from ...apis.basic import Game

__all__ = ["Th07Mods"]


@register_mods("th07")
class Th07Mods:
    """th07 专属 mod 能力提供者: 樱点直改/上限直改/强制破结界。"""

    def __init__(self, game: Game) -> None:
        self._game = game
        # 门面协议(GameEngine)不含作品专属字段, 收窄到本作对局实现
        self._impl = cast(PerfectCherryBloom, game._impl)

    @mod_namespace("player")
    def set_cherry(self, value: int) -> None:
        """樱点直改为 ``value``(合法域 0..当前 cherryMax, 上限读引擎实况)。"""
        g = self._impl.globals
        if not 0 <= value <= g.cherry_max:
            raise ValueError(
                f"樱点 {value} 超出合法范围 0..{g.cherry_max}"
                f"(cherryMax 为引擎当前实况值)")
        g.cherry = value

    @mod_namespace("player")
    def set_cherry_max(self, value: int) -> None:
        """樱点上限 cherryMax 直改(域 cherryStart..cherryStart+CHERRY_MAX_RANGE)。"""
        g = self._impl.globals
        lo, hi = g.cherry_start, g.cherry_start + CHERRY_MAX_RANGE
        if not lo <= value <= hi:
            raise ValueError(
                f"樱点上限 {value} 超出合法范围 {lo}..{hi}"
                f"(CHERRY_MAX_RANGE={CHERRY_MAX_RANGE}, 见 games/th07/globals.py)")
        g.cherry_max = value

    @mod_namespace("border")
    def border_break(self) -> None:
        """强制破裂当前樱之结界(主动破账: 全屏清弹圆+破结界无敌; 无结界报错)。"""
        if self._impl.border.has_border == BorderState.NONE:
            raise ValueError("当前没有结界可破(has_border=NONE; 满樱 READY/ACTIVE 时才可破)")
        # 与 bomb 键主动破同一入口(world._break_border 的 BreakBorder 账)
        self._impl._break_border(by_bomb_key=True)
