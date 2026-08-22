"""道具系统基座 —— 不分作品的道具运动学与容器机制。

对照 ItemManager.cpp 中作品无关的部分: 道具三态(下落/吸附/生成动画)、
下落加速与吸附/生成插值运动、收点判定、批量操作(RemoveAllItems /
ActivateAllItems)。作品专属(th07 的满火力转樱点道具、POC/结界吸附触发、
收集分值表、掉落表)由作品层子类扩展(games/th07/items.py)。

关于 ``ItemTypeBase``: Python 禁止继承"已有成员的 Enum"再扩展成员, 引擎层
无法提供可被子类加成员的 IntEnum 基类, 故这里以普通 int 常量类声明各作品
共有的道具类型子集(值序与 th07 ItemManager 一致); 作品层各自定义自己的
IntEnum(th07 = games/th07/items.py 的 ItemType, 含 CHERRY/STAR 等扩展)。
"""

from __future__ import annotations

import msgspec
from typing import Generic, TypeVar

from .player_base import PlayerState
from ..utils import Vec2

__all__ = [
    "STATE_ATTRACT",
    "STATE_FALL",
    "STATE_SPAWN",
    "CollectResultBase",
    "ItemBase",
    "ItemContextBase",
    "ItemTypeBase",
    "ItemWorldBase",
]


class ItemTypeBase:
    """通用道具类型子集(int 常量; 不可枚举继承的替代, 见模块 docstring)。"""

    POWER_SMALL = 0
    POINT = 1
    POWER_BIG = 2
    BOMB = 3
    FULL_POWER = 4
    LIFE = 5
    NO_ITEM = 255


# 状态
STATE_FALL = 0      # 下落
STATE_ATTRACT = 1   # 向玩家吸附
STATE_SPAWN = 2     # 生成动画(60帧飞向目标后转下落)

# 吸附速度/吸附半径默认值(.sht itemCollectSpeed/itemCollectRadius)
ITEM_COLLECT_SPEED = 4.0
ITEM_COLLECT_RADIUS = 16.0


class ItemContextBase(msgspec.Struct):
    """ItemWorld 判定所需的最小环境快照(作品层子类追加作品专属字段)。"""

    player_pos: Vec2 = Vec2(192, 400)
    player_alive: bool = True
    player_state: int = 0       # PlayerState.SPAWNING 时道具缓降不吸附
    item_collect_speed: float = ITEM_COLLECT_SPEED
    item_collect_radius: float = ITEM_COLLECT_RADIUS


CtxT = TypeVar("CtxT", bound=ItemContextBase)
ItemT = TypeVar("ItemT", bound="ItemBase")


class ItemBase(msgspec.Struct):
    """一个道具的通用运动学。类型/分值语义由作品子类追加
    (th07: Item 加 ``type: ItemType`` 字段)。"""

    pos: Vec2 = Vec2.zero()
    start: Vec2 = Vec2.zero()   # 每帧落速/吸附速度
    state: int = STATE_FALL
    auto_collect: bool = False
    timer: int = 0
    target: Vec2 | None = None     # 生成动画目标(若非空)
    start_pos: Vec2 | None = None  # 生成动画起点

    def drop(self) -> None:
        """出生: 向上初速, 随后加速下落。"""
        self.state = STATE_FALL
        self.start = Vec2(0, -2.2)
        self.target = self.start_pos = None

    def spawn_to(self, target: Vec2) -> None:
        """生成动画: 60 帧从当前位置飞向 target, 然后下落。"""
        self.state = STATE_SPAWN
        self.timer = 0
        self.target = target
        self.start_pos = self.pos

    def step(self, dt: float = 1.0) -> None:
        """下落状态的速度渐变: 向上速度不超 -2.2, 每帧 +0.03 加速到 +3.0 封顶。"""
        if self.state != STATE_FALL:
            return
        if self.start.y < -2.2:
            self.start = Vec2(self.start.x, -2.2)
        if self.start.y < 3.0:
            self.start = Vec2(self.start.x, min(self.start.y + 0.03 * dt, 3.0))


class CollectResultBase(msgspec.Struct):
    """收集一个道具后的通用结算字段(作品层子类追加专属轨道, 如 th07 樱点)。"""

    score: int = 0            # 显示分变化(需上层加)
    delta_power: float = 0.0
    delta_bombs: int = 0
    delta_lives: int = 0


class ItemWorldBase(msgspec.Struct, Generic[ItemT, CtxT]):
    """道具管理器的通用容器机制(按道具/上下文类型参数化)。

    扩展点: ``_status_change(item, ctx)`` —— 决定道具是否进入吸附(作品层
    实现各自的触发条件; th07 = 满火力/Extra 的 POC 线 + 结界); 基类默认
    不触发吸附。生成(spawn)与收集结算(collect)绑死作品数值语义, 不进基类。
    """

    items: list["ItemT"] = msgspec.field(default_factory=list)

    # ---- 每帧 ----
    def step(self, ctx: CtxT, dt: float = 1.0) -> list["ItemT"]:
        """推进所有道具; 返回本帧掉出屏幕被删除的道具(惩罚由上层按返回列表应用)。"""
        dropped: list["ItemT"] = []
        keep: list["ItemT"] = []
        for item in self.items:
            self._status_change(item, ctx)
            if item.state == STATE_SPAWN and item.target is not None:
                # 60 帧插值飞向目标
                item.timer += 1
                t = min(item.timer / 60.0, 1.0)
                item.pos = _lerp(item.start_pos or item.pos, item.target, t)
                if item.timer >= 60:
                    item.state = STATE_FALL
                    item.start = Vec2.zero()
            elif item.state == STATE_ATTRACT:
                item.start = (ctx.player_pos - item.pos).normalized() * ctx.item_collect_speed
                item.pos = item.pos + item.start * dt
            else:  # STATE_FALL
                item.step(dt)  # 速度渐变
                item.pos = item.pos + item.start * dt
            # 出屏(底边)删除: 惩罚由上层按返回列表应用
            if item.pos.y >= 448 + 16:
                dropped.append(item)
            else:
                keep.append(item)
        self.items = keep
        return dropped

    def _status_change(self, item: "ItemT", ctx: CtxT) -> None:
        """决定 item 是否进入吸附(非生成动画中)。基类不吸附; 作品层覆盖。"""

    def collect_pickup(self, item: "ItemT", ctx: CtxT) -> bool:
        """收集判定: 与玩家收点盒相交且玩家非死亡/重生。"""
        if not ctx.player_alive or ctx.player_state == PlayerState.SPAWNING:
            return False
        return item.pos.distance(ctx.player_pos) <= ctx.item_collect_radius

    # ---- 批量操作 (§E.5) ----
    def remove_all_items(self) -> None:
        """全部道具转吸附, 速度 (0,-0.5) (ItemManager::RemoveAllItems)。"""
        for item in self.items:
            item.state = STATE_ATTRACT
            item.start = Vec2(0.0, -0.5)

    def activate_all_items(self) -> None:
        """吸附中的道具转回下落, 速度 (0,-0.9) (ItemManager::ActivateAllItems)。"""
        for item in self.items:
            if item.state == STATE_ATTRACT:
                item.state = STATE_FALL
                item.start = Vec2(0.0, -0.9)

    def remove(self, item: "ItemT") -> None:
        if item in self.items:
            self.items.remove(item)

    def clear(self) -> None:
        self.items.clear()

    def alive(self) -> list["ItemT"]:
        return self.items

    def __len__(self) -> int:
        return len(self.items)


def _lerp(a: Vec2, b: Vec2, t: float) -> Vec2:
    return a + (b - a) * t
