""" 道具系统(th07) —— 移植自 ItemManager.cpp / 规格 §E。

通用机制(道具三态运动学/收点判定/批量操作/容器)已上移到引擎层基座
engine/item_base.py(ItemBase/ItemContextBase/ItemWorldBase/CollectResultBase);
本模块只留 th07 专属: 道具类型扩展(CHERRY/STAR 等)、满火力转樱点道具、
POC 线/结界吸附触发、收集分值表、掉落表、点道具残机阈值。

道具生命周期: 下落 → (吸附/POC/结界) 吸附向玩家 → 收集结算。
收集后的结算经由 CollectResult 返回, 由上层游戏状态应用。
分值语义: C++ AddScore 入参为代码值(=显示分×10); 本模块 r.score 一律为显示分。
"""

from __future__ import annotations

import msgspec
from enum import IntEnum

from .data import (
    DROP_TABLE,
    FULL_POWER,
    FULL_POWER_SCORE_BONUS,
    POWER_LEVELS,
)
from ...engine.item_base import (  # noqa: F401 (常量/基类再导出, 保持本模块引用兼容)
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

# POC 收集线默认高度(.sht pocY, 上层接线前用此值)
POC_Y = 128.0

# 玩家状态: SPAWNING(重生无敌, 道具缓降不吸附) —— = engine/player_base.PlayerState.SPAWNING
PLAYER_STATE_SPAWNING = PlayerState.SPAWNING

# 道具掉出屏幕的 subrank 惩罚(OnUpdate: DecreaseSubrank(3))
OFFSCREEN_SUBRANK_PENALTY = 3

# DROP_TABLE / POWER_LEVELS / FULL_POWER / FULL_POWER_SCORE_BONUS:
# th07 数值表, 单一来源在同包 data.py; 这里的导入即 th07 默认表
# (作品级覆盖: 掉落表经 ItemWorld.drop_random(table=...) / 对局 data 注入)。


def next_needed_point_items_for_extend(extends: int, difficulty: int) -> int:
    """第 extends 次(0 起)点道具残机所需累计点道具数 (ItemManager.cpp:289-315)。"""
    if difficulty < 4:
        if extends < 3:
            return extends * 75 + 50       # 50/125/200
        if extends < 5:
            return (extends - 3) * 150 + 300
        return (extends - 5) * 200 + 800
    if extends == 0:
        return 200
    if extends == 1:
        return 500
    return (extends - 2) * 500 + 800


class ItemType(IntEnum):
    POWER_SMALL = 0
    POINT = 1
    POWER_BIG = 2
    BOMB = 3
    FULL_POWER = 4
    LIFE = 5
    POINT_BULLET = 6
    CHERRY = 7
    CHERRY_SMALL = 8
    STAR = 9
    NO_ITEM = 255


class CollectResult(CollectResultBase):
    """收集一个道具后的结算(尚未应用到全局)。通用字段在基类 CollectResultBase。"""

    delta_cherry: int = 0       # 仅 cherry 轨(AddCherry)
    delta_cherry_plus: int = 0  # cherryPlus 轨(AddCherryPlus, 同时也累加 cherry)
    extends: int = 0            # 本次收集获得的残机数(点道具阈值, 可连升多个)
    clear_bullets: bool = False
    point_items_collected: int = 0  # 计入残机累计的点道具数
    full_power: bool = False
    subrank: int = 0
    power_overflow_next: int | None = None  # 满火力计分计数器新值(非空则需上层写回)


class GameContext(ItemContextBase):
    """ItemWorld 依赖的游戏状态(输入环境快照, 带默认值)。

    通用字段(player_pos/player_alive/player_state/item_collect_speed/radius)
    在基类 ItemContextBase; 以下均为 th07 专属。
    """

    power: float = 0.0
    lives: int = 3
    bombs: int = 2
    graze_total: int = 0
    border_active: bool = False
    difficulty: int = 1
    bombing: bool = False
    power_overflow_counter: int = 0
    spell_cards_captured: int = 0
    cherry_gap: int = 0         # cherry - cherryStart
    cherry_maxed: bool = False  # cherry >= cherryMax
    extends_from_point_items: int = 0
    point_items_collected_for_extend: int = 0
    poc_y: float = POC_Y


class Item(ItemBase):
    """一个 th07 道具(通用运动学在基类 ItemBase, 这里加类型字段)。

    ``type`` 给默认值 NO_ITEM 是 msgspec 字段序要求(基类字段均带默认,
    子类追加字段亦须带默认); 原有调用点都显式传 type, 行为不变。
    """

    type: ItemType = ItemType.NO_ITEM


class ItemWorld(ItemWorldBase[Item, GameContext]):
    """th07 道具管理器(通用容器/运动机制在基类 ItemWorldBase)。"""

    # ---- 生成 ----
    def spawn(self, at: Vec2, it: ItemType, power: float = 0.0) -> Item:
        # 满火力时 POWER_SMALL/BIG 自动转 CHERRY (ItemManager::SpawnItem)
        if power >= FULL_POWER and it in (ItemType.POWER_SMALL, ItemType.POWER_BIG):
            it = ItemType.CHERRY
        item = Item(type=it, pos=at, start=Vec2(0, -2.2))
        self.items.append(item)
        return item

    def drop_random(self, at: Vec2, table: list[int] | None = None,
                    counter: int = 0, power: float = 0.0) -> Item:
        """按掉落表放一个道具(小怪死亡掉落)。"""
        tbl = table or DROP_TABLE
        it = ItemType(tbl[counter % len(tbl)])
        return self.spawn(at, it, power=power)

    # ---- 吸附触发(th07: 满火力/Extra 的 POC 线 + 结界) ----
    def _status_change(self, item: ItemBase, ctx: GameContext) -> None:
        """决定 item 是否进入吸附(非生成动画中)。"""
        if item.state in (STATE_ATTRACT, STATE_SPAWN):
            return
        trigger = (
            ((ctx.power >= FULL_POWER or ctx.difficulty >= 4)
             and ctx.player_pos.y < ctx.poc_y)
            or ctx.border_active
        )
        if not trigger:
            return
        if ctx.player_state == PLAYER_STATE_SPAWNING:
            # 玩家重生中: 不吸附, 改 y=-0.5 缓降(死亡爆道具重撒)
            item.start = Vec2(0.0, -0.5)
            return
        item.state = STATE_ATTRACT
        if ctx.border_active:
            item.auto_collect = True  # 仅结界收集标满分(C++ 仅 hasBorder 时置位)

    def collect(self, item: Item, ctx: GameContext) -> CollectResult:
        """结算一个被收集的道具。返回结果(由上层应用)。"""
        r = CollectResult()
        t = item.type
        if t == ItemType.POWER_SMALL:
            r.subrank = 1
            if ctx.power >= FULL_POWER:
                # 满火力: counter+1 cap 30, 查表 (ItemManager.cpp:202-212)
                n = min(ctx.power_overflow_counter + 1, 30)
                r.power_overflow_next = n
                # C++ 表仅 30 项, n=30 时越界读; 这里封顶末档(显示 1200)
                idx = min(n, len(FULL_POWER_SCORE_BONUS) - 1)
                r.score = FULL_POWER_SCORE_BONUS[idx] // 10
                r.full_power = True
            else:
                r.delta_power = 1
                r.score = 1  # AddScore(10), 显示分 1
                r.power_overflow_next = 0
                if ctx.power + 1 >= FULL_POWER:
                    r.clear_bullets = True
                    self.despawn_all_items(skip=item)
        elif t == ItemType.POWER_BIG:
            if ctx.power < FULL_POWER:
                r.delta_power = 8
                r.score = 1  # AddScore(10)
                if ctx.power + 8 >= FULL_POWER:
                    r.clear_bullets = True
                    self.despawn_all_items(skip=item)
            # 满火力后大 P 无分(C++ 仅弹窗)
        elif t == ItemType.BOMB:
            if ctx.bombs < 8:
                r.delta_bombs = 1
            r.subrank = 5
        elif t == ItemType.LIFE:
            r.delta_lives = 1
        elif t == ItemType.FULL_POWER:
            if ctx.power < FULL_POWER:
                r.clear_bullets = True
                self.despawn_all_items(skip=item)
            r.delta_power = max(0.0, FULL_POWER - ctx.power)
            r.score = 100  # AddScore(1000)
        elif t == ItemType.POINT:
            r.score = _point_score(item, ctx)
            r.point_items_collected = 1
            r.subrank = 10 if item.pos.y < 128.0 else 3  # C++ 硬编码 128.0(非 pocY)
            r.extends = _point_extends(ctx)
        elif t == ItemType.POINT_BULLET:
            if not ctx.bombing:
                r.score = _graze_score(ctx.graze_total)
                r.delta_cherry_plus = 20
            else:
                r.score = 10  # 代码值 100
                # C++ 按 bombInfo.isInUse 且以道具索引奇偶隔帧 +10 cherryPlus/+10 cherry;
                # 此处简化为 bomb 中固定 cherryPlus/cherry 各 +10
                r.delta_cherry_plus = 10
                r.delta_cherry = 10
        elif t == ItemType.CHERRY:
            if ctx.cherry_maxed:
                # 满樱时按 POINT 计分(无樱差加成), ≤5000 显示 (ItemManager.cpp:428-436)
                if item.pos.y < ctx.poc_y or item.auto_collect:
                    code = 50000
                else:
                    code = 50000 - int(item.pos.y - ctx.poc_y) * 100
                code -= code % 10
                r.score = code // 10
            r.delta_cherry_plus = 1000 + ctx.spell_cards_captured * 100
        elif t == ItemType.CHERRY_SMALL:
            r.delta_cherry_plus = 30
            r.delta_cherry = 70
        elif t == ItemType.STAR:
            r.score = _graze_score(ctx.graze_total)
            r.delta_cherry_plus = 100
        return r

    # ---- 批量操作(th07 专属部分; remove/activate_all 在基类) ----
    def despawn_all_items(self, skip: Item | None = None) -> None:
        """场上 POWER_SMALL/POWER_BIG 转 CHERRY (ItemManager::DespawnAllItems)。"""
        for item in self.items:
            if item is skip:
                continue
            if item.type in (ItemType.POWER_SMALL, ItemType.POWER_BIG):
                if item.start.y > -0.5:
                    item.start = Vec2(0.0, -0.5)
                item.type = ItemType.CHERRY


def _point_score(item: Item, ctx: GameContext) -> int:
    """POINT 道具显示分 (ItemManager.cpp:253-273)。"""
    if item.pos.y < ctx.poc_y:
        code = 50000
    else:
        code = 50000 - int(item.pos.y - ctx.poc_y) * 100  # 低于 POC 线每像素 -100(代码值)
    if item.auto_collect:
        code = 50000  # 结界收集恒满值
    gap = ctx.cherry_gap
    if code >= 50000:
        if gap > 50000:
            code = gap  # 满樱改用樱差计分
    elif gap > 50000:
        code += (gap - 50000) // 5  # 满樱且樱差>50000 追加 1/5
    code -= code % 10
    return code // 10


def _point_extends(ctx: GameContext) -> int:
    """本个点道具触发的残机数 (ItemManager.cpp:285-325, 循环判可一次连升多个)。"""
    if ctx.extends_from_point_items < 0:
        return 0
    collected = ctx.point_items_collected_for_extend + 1  # 含本道具
    e = ctx.extends_from_point_items
    n = 0
    while collected >= next_needed_point_items_for_extend(e, ctx.difficulty):
        n += 1
        e += 1
    return n


def _graze_score(graze_total: int) -> int:
    """擦弹分(显示): 代码 graze/40*10+300, 下限代码值 10。"""
    code = graze_total // 40 * 10 + 300
    if code <= 0:
        code = 10
    return code // 10
