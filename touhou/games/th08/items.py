"""道具系统(th08 东方永夜抄) —— 移植自 th08-ref ItemManager.cpp。

通用容器/运动学在引擎基座 engine/item_base.py; 本模块是 th08 专属:
- ItemType(ItemManager.hpp:8-22): 比 th07 多 POINT_STAR(弹消星)/TIME(时刻符点)/
  POINT_SMALL(满火力转小点)/TIME_APEX_AUTOCOLLECT_REQUEST(生成即转 TIME);
- POC 线吸附条件 (ItemManager.cpp:266-270): 满火力 或 非低速 或 魔理沙系
  (shotType 1/6) 且自机在 POC 线上 —— 注意与 th07 不同(低速也收);
- 时刻符点道具的上升态(TIME_RISING/TIME_RISING_TO_APEX, ItemManager.cpp:229-250);
- 收集结算: 点道具分值随 pointItemValue(难度初值 GameManagerSetup.cpp:149-161),
  极限人类 ×2 (CollectPoint, ItemManager.cpp:456-512); 时刻符点
  (CollectTimeOrb, ItemManager.cpp:600-638)。

分值语义同 th07: r.score 一律为显示分(C++ AddScore 入参代码值 = 显示分×10)。
"""

from __future__ import annotations

import msgspec
from enum import IntEnum
from typing import Callable

from .data import DROP_TABLE, FULL_POWER, POWER_LEVELS
from .globals import next_point_item_extend_threshold
from ...engine.item_base import (  # noqa: F401 (常量/基类再导出)
    ITEM_COLLECT_RADIUS,
    ITEM_COLLECT_SPEED,
    STATE_ATTRACT,
    STATE_FALL,
    STATE_SPAWN,
    CollectResultBase,
    ItemBase,
    ItemContextBase,
    ItemWorldBase,
)
from ...engine.player_base import PlayerState
from ...utils import Vec2

# 道具掉出屏幕的 subrank 惩罚 (ItemManager.cpp:294, OnUpdate moveItem 段)
OFFSCREEN_SUBRANK_PENALTY = 3

# 时刻符点道具状态 (ItemManager.hpp ItemState)
STATE_TIME_RISING = 3  # 上升: 每帧 vel.y+0.05, 过顶(>0)或自机停火 → 吸附
STATE_TIME_RISING_TO_APEX = 5  # 上升到顶后吸附(同上, 生成入口不同)

# 弹字颜色 (CreateScorePopup 实参, ARGB)
POPUP_WHITE = 0xFFFFFFFF
POPUP_YELLOW = 0xFFFFFF00  # 满值收点(POC 线上)
POPUP_POWERUP = 0xFFFFC0A0  # 火力升档 PowerUp 字形
# 时刻符点弹字 (ItemManager.cpp:625-626): 达标(可 Last Spell)变色
POPUP_TIME_ORB = -536870913  # 0xDFFFFFFF 未达标
POPUP_TIME_ORB_READY = -536875136  # 0xDFFFFF80 达标


class ItemType(IntEnum):
    """th08 道具类型 (ItemManager.hpp:8-22)。"""

    POWER_SMALL = 0
    POINT = 1
    POWER_BIG = 2
    BOMB = 3
    FULL_POWER = 4
    LIFE = 5  # ITEM_EXTEND
    POINT_STAR = 6  # 弹消星(cancelItemType=6, BulletManager.cpp:49)
    TIME = 7  # 时刻符点
    POINT_SMALL = 8  # 满火力后小 P 转成的小点
    TIME_APEX_REQUEST = 10  # ITEM_TIME_APEX_AUTOCOLLECT_REQUEST: 生成即转 TIME
    NO_ITEM = 255


class CollectResult(CollectResultBase):
    """收集一个道具后的结算(尚未应用到全局)。"""

    extends: int = 0  # 本次收集获得的残机数(点道具阈值)
    clear_bullets: bool = False  # 满火力清弹(ClearBulletsForTransition)
    convert_power_items: bool = False  # 满火力时全场 P 转时刻符点
    point_items_collected: int = 0
    time_orbs: int = 0  # 时刻符点增量(AddTimeOrbs)
    gauge_delta: int = 0  # 妖率计增量(AddToYoukaiGauge)
    bonus_progress: int = 0  # 符卡 bonusProgress 增量(Spellcard.AddBonusProgress)
    subrank: int = 0
    reached_full_power: bool = False
    # 得分弹字: (显示数值(代码值口径, -1=PowerUp 字形), ARGB 颜色, 槽位)
    popups: list[tuple[int, int, int]] = msgspec.field(default_factory=list)


class GameContext(ItemContextBase):
    """ItemWorld 依赖的游戏状态快照(通用字段在基类 ItemContextBase)。"""

    power: float = 0.0
    lives: int = 3
    bombs: int = 2
    focus: bool = False  # POC 条件用(focusMode != UNFOCUSED)
    shot_type: int = 0  # POC 条件用(魔理沙系 1/6 无火力前提)
    difficulty: int = 1
    bombing: bool = False
    player_firing: bool = True  # shotTimer >= 0(时刻符点停火即吸附)
    point_item_value: int = 100000  # 当前点道具分值
    point_items_collected: int = 0  # 全局累计(时刻符点分值用)
    point_items_collected_this_stage: int = 0
    point_item_extends_so_far: int = 0
    next_point_item_extend_threshold: int = 100
    gauge_extremely_human: bool = False  # 极限人类: 点道具分 ×2
    time_orb_ready: bool = False  # 符点已达 Last Spell 阈值(弹字变色)
    spellcard_active: bool = False  # 符卡中满火力不清弹
    poc_y: float = 128.0


class Item(ItemBase):
    """一个 th08 道具(通用运动学在基类 ItemBase, 这里加类型字段)。"""

    type: ItemType = ItemType.NO_ITEM
    is_max_value: bool = False  # isMaxValue: 满值收点标记(CollectPoint)


class ItemWorld(ItemWorldBase[Item, GameContext]):
    """th08 道具管理器(通用容器机制在基类 ItemWorldBase)。

    rng 由上层(world)注入, 供时刻符点出生速度随机
    (ItemManager.cpp:113-116 的 GetRandomF32InRange)使用; 未注入时
    回落确定中值(测试友好)。
    """

    rng_float: Callable[[float], float] | None = None  # [0, r) 随机

    def _rand(self, r: float) -> float:
        if self.rng_float is not None:
            return self.rng_float(r)
        return r / 2.0

    # ---- 生成 ----
    def spawn(
        self, at: Vec2, it: int, power: float = 0.0, state: int = STATE_FALL
    ) -> Item | None:
        """ItemManager::SpawnItem (ItemManager.cpp:44-145) 的改编。

        - 满火力时 POWER_SMALL/BIG → POINT_SMALL (:55-58);
        - TIME → TIME_RISING 出生(随机上飘); TIME_APEX_REQUEST → TIME+
          TIME_RISING_TO_APEX (:59-64);
        - x 出 [-64, 448] 不生成 (:48-51); TIME 槽满不生成 (:75-78 简化:
          本实现无固定槽位, 不模拟);
        返回 None = 未生成。
        """
        try:
            t = ItemType(it)
        except ValueError:
            return None
        if t == ItemType.NO_ITEM:
            return None
        if at.x < -64.0 or at.x > 448.0:
            return None
        if power >= FULL_POWER and t in (ItemType.POWER_SMALL, ItemType.POWER_BIG):
            t = ItemType.POINT_SMALL
        item = Item(type=t, pos=at, start=Vec2(0, -2.2))
        if t == ItemType.TIME:
            # ITEM_STATE_TIME_RISING (ItemManager.cpp:111-116)
            item.state = STATE_TIME_RISING
            item.start = Vec2(self._rand(1.2) - 0.6, -2.0 - self._rand(0.2))
        elif t == ItemType.TIME_APEX_REQUEST:
            item.type = t = ItemType.TIME
            item.state = STATE_TIME_RISING_TO_APEX
            item.start = Vec2(self._rand(1.2) - 0.6, -2.0 - self._rand(0.2))
        else:
            item.state = state
        self.items.append(item)
        return item

    def drop_random(
        self,
        at: Vec2,
        table: list[int] | None = None,
        counter: int = 0,
        power: float = 0.0,
    ) -> Item | None:
        """按掉落表放一个道具(小怪死亡掉落; 0=小P 1=点)。"""
        tbl = table or DROP_TABLE
        return self.spawn(at, tbl[counter % len(tbl)], power=power)

    # ---- 每帧(基类 step 的 th08 版: 多 TIME_RISING 两态) ----
    def step(self, ctx: GameContext, dt: float = 1.0) -> list[Item]:
        dropped: list[Item] = []
        keep: list[Item] = []
        for item in self.items:
            self._status_change(item, ctx)
            if item.state == STATE_SPAWN and item.target is not None:
                # 死亡爆道具重撒 (ITEM_STATE_DEATH_DROP_SPREAD, :213-228)
                item.timer += 1
                t = min(item.timer / 60.0, 1.0)
                src = item.start_pos or item.pos
                item.pos = src + (item.target - src) * t
                if item.timer >= 60:
                    item.state = STATE_FALL
                    item.start = Vec2.zero()
            elif item.state in (STATE_TIME_RISING, STATE_TIME_RISING_TO_APEX):
                # 时刻符点上升 (ItemManager.cpp:229-250): vel.y 每帧 +0.05,
                # 过顶或自机停火 → 吸附; 自机死亡中 → 缓降(上层经
                # _status_change 已处理, 这里只负责上升段)
                item.start = Vec2(item.start.x, item.start.y + 0.05 * dt)
                item.pos = item.pos + item.start * dt
                if item.start.y > 0.0 or not ctx.player_firing:
                    item.state = STATE_ATTRACT
            elif item.state == STATE_ATTRACT:
                item.start = (
                    ctx.player_pos - item.pos
                ).normalized() * ctx.item_collect_speed
                item.pos = item.pos + item.start * dt
            else:  # STATE_FALL
                item.step(dt)
                item.pos = item.pos + item.start * dt
            if item.pos.y >= 448 + 16:
                dropped.append(item)
            else:
                keep.append(item)
        self.items = keep
        return dropped

    # ---- 吸附触发(th08: 满火力/低速/魔理沙系 的 POC 线) ----
    def _status_change(self, item: ItemBase, ctx: GameContext) -> None:
        """ItemManager.cpp:264-282 的状态段。

        C++ 条件 `state==1 || ((满火力||非低速||魔理沙系) && 玩家在 POC 线上)`;
        玩家重生/死亡中(playerState==SPAWNING/DYING)连已吸附的道具也改回
        下落态缓降 (:256-259/:275-278 段)。"""
        if item.state == STATE_SPAWN:
            return
        if ctx.player_state == PlayerState.DEAD and item.state in (
            STATE_TIME_RISING,
            STATE_TIME_RISING_TO_APEX,
            STATE_ATTRACT,
        ):
            item.start = Vec2(0.0, -0.7)
            item.state = STATE_FALL
            return
        trigger = (
            item.state == STATE_ATTRACT
            or (
                (
                    ctx.power >= FULL_POWER
                    or ctx.focus
                    or ctx.shot_type in (1, 6)  # 魔理沙系 (ItemManager.cpp:269)
                )
                and ctx.player_pos.y < ctx.poc_y
            )
        )
        if not trigger:
            return
        if ctx.player_state == PlayerState.SPAWNING:
            item.start = Vec2(0.0, -0.5)
            item.state = STATE_FALL
            return
        item.state = STATE_ATTRACT

    # ---- 收集结算 ----
    def collect(self, item: Item, ctx: GameContext) -> CollectResult:
        """结算一个被收集的道具。返回结果(由上层应用)。"""
        r = CollectResult()
        t = item.type
        if t == ItemType.POWER_SMALL:
            # CollectPowerSmall (ItemManager.cpp:401-453)
            r.subrank = 1
            if ctx.power < FULL_POWER:
                r.delta_power = 1
                r.score = 1  # AddScore(10)
                if ctx.power + 1 >= FULL_POWER:
                    r.reached_full_power = True
                    r.clear_bullets = not ctx.spellcard_active
                    r.convert_power_items = True
                if _power_level(min(ctx.power + 1, FULL_POWER)) != _power_level(
                    ctx.power
                ):
                    r.popups.append((-1, POPUP_POWERUP, 1))
                else:
                    r.popups.append((10, POPUP_WHITE, 1))
        elif t == ItemType.POWER_BIG:
            # CollectPowerBig (ItemManager.cpp:549-598): 无 subrank
            if ctx.power < FULL_POWER:
                r.delta_power = 8
                r.score = 1  # AddScore(10)
                if ctx.power + 8 >= FULL_POWER:
                    r.reached_full_power = True
                    r.clear_bullets = not ctx.spellcard_active
                    r.convert_power_items = True
                if _power_level(min(ctx.power + 8, FULL_POWER)) != _power_level(
                    ctx.power
                ):
                    r.popups.append((-1, POPUP_POWERUP, 1))
                else:
                    r.popups.append((10, POPUP_WHITE, 1))
        elif t == ItemType.BOMB:
            if ctx.bombs < 8:
                r.delta_bombs = 1
            r.subrank = 5
        elif t == ItemType.LIFE:
            r.delta_lives = 1
        elif t == ItemType.FULL_POWER:
            # ItemManager.cpp:330-345: 无符卡豁免, 恒清弹
            if ctx.power < FULL_POWER:
                r.reached_full_power = True
                r.clear_bullets = True
                r.convert_power_items = True
                r.popups.append((-1, POPUP_POWERUP, 1))
            r.delta_power = max(0.0, FULL_POWER - ctx.power)
            r.score = 100  # AddScore(1000)
            r.popups.append((1000, POPUP_WHITE, 1))
        elif t == ItemType.POINT:
            # CollectPoint (ItemManager.cpp:456-512)
            code, color = _point_score(item, ctx, small=False)
            r.score = code // 10
            r.popups.append((code, color, 1))
            r.point_items_collected = 1
            r.subrank = 10 if code >= ctx.point_item_value else 3
            r.extends = _point_extends(ctx)
        elif t == ItemType.POINT_SMALL:
            # CollectPointSmall (ItemManager.cpp:514-547): 分值 /10, 无奖残/subrank
            code, color = _point_score(item, ctx, small=True)
            r.score = code // 10
            r.popups.append((code, color, 1))
        elif t == ItemType.POINT_STAR:
            # 弹消星: th08-ref 未见独立 Collect(弹转道具的固定小额分);
            # 按 POINT_SMALL 同型结算(分值 /10 轨)
            code, color = _point_score(item, ctx, small=True)
            r.score = code // 10
            r.popups.append((code, color, 2))
        elif t == ItemType.TIME:
            # CollectTimeOrb (ItemManager.cpp:600-638)
            if ctx.point_items_collected_this_stage >= 2000:
                code = 10000
            else:
                code = (ctx.point_items_collected // 2) * 10
                if code < 100:
                    code = 100
            r.score = code // 10
            r.time_orbs = 1
            # 符卡 bonusProgress +8000 (ItemManager.cpp:631)
            r.bonus_progress = 8000
            # 妖率计 ±111 (ItemManager.cpp:634-636: focus=妖 +111, 否则 -111;
            # timeOrbGaugeChangeSuppressionTimer 抑制期不加是上层职责)
            r.gauge_delta = 111 if ctx.focus else -111
            r.popups.append(
                (
                    code,
                    POPUP_TIME_ORB_READY if ctx.time_orb_ready else POPUP_TIME_ORB,
                    1,
                )
            )
        return r

    # ---- 批量操作(th08 专属) ----
    def convert_power_items_to_time_orbs(self, skip: Item | None = None) -> None:
        """ConvertAllPowerItemsToTimeOrbs (ItemManager.cpp:655-687):
        满火力时全场 POWER_SMALL/POWER_BIG 转 TIME(上升态)。"""
        for item in self.items:
            if item is skip:
                continue
            if item.type in (ItemType.POWER_SMALL, ItemType.POWER_BIG):
                item.type = ItemType.TIME
                item.state = STATE_TIME_RISING
                item.start = Vec2(self._rand(1.2) - 0.6, -2.0 - self._rand(0.2))


def _power_level(power: float) -> int:
    """火力档位 (g_PowerUpThresholds 的 while 循环, ItemManager.cpp:407-409)。"""
    n = 0
    while n < len(POWER_LEVELS) and int(power) >= POWER_LEVELS[n]:
        n += 1
    return n


def _point_score(item: Item, ctx: GameContext, *, small: bool) -> tuple[int, int]:
    """点道具代码值分 + 弹字颜色 (CollectPoint/CollectPointSmall,
    ItemManager.cpp:456-475/514-527)。

    POC 线上 = 满值 pointItemValue; 线下 = base/2 - (y-pocY)*(value/1000);
    isMaxValue 恒满值; 极限人类 ×2; small=True(小点/弹消星)再 /10。
    """
    base = ctx.point_item_value
    if item.pos.y < ctx.poc_y or item.is_max_value:
        code = base
    else:
        code = base // 2 - int(item.pos.y - ctx.poc_y) * (base // 1000)
    if small:
        base //= 10
        base -= base % 10
        code //= 10
    code -= code % 10
    if ctx.gauge_extremely_human:
        code += code
    color = POPUP_YELLOW if code >= base else POPUP_WHITE
    return code, color


def _point_extends(ctx: GameContext) -> int:
    """本个点道具触发的残机数 (ItemManager.cpp:497-510, 循环判可连升多个)。"""
    if ctx.point_item_extends_so_far < 0:
        return 0
    collected = ctx.point_items_collected + 1  # 含本道具
    e = ctx.point_item_extends_so_far
    n = 0
    while collected >= ctx.next_point_item_extend_threshold:
        n += 1
        e += 1
        # 阈值推进由上层按 extends 入账后重算(globals.next_point_item_extend_threshold)
        ctx.next_point_item_extend_threshold = next_point_item_extend_threshold(
            e, ctx.difficulty
        )
    return n
