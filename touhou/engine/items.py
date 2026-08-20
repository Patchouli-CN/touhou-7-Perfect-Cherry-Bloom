""" 道具系统 —— 移植自 ItemManager.cpp / 规格 §E。

道具生命周期: 下落 → (吸附/POC/结界) 吸附向玩家 → 收集结算。
收集后的结算经由 CollectResult 返回, 由上层游戏状态应用。
分值语义: C++ AddScore 入参为代码值(=显示分×10); 本模块 r.score 一律为显示分。
"""

from __future__ import annotations

import msgspec
from enum import IntEnum

from ..games_th07 import (
    DROP_TABLE,
    FULL_POWER,
    FULL_POWER_SCORE_BONUS,
    POWER_LEVELS,
)
from ..utils import Vec2

# POC 收集线默认高度(.sht pocY, 上层接线前用此值)
POC_Y = 128.0

# 吸附速度/吸附半径默认值(.sht itemCollectSpeed/itemCollectRadius)
ITEM_COLLECT_SPEED = 4.0
ITEM_COLLECT_RADIUS = 16.0

# 玩家状态: 1 = SPAWNING(重生无敌, 道具缓降不吸附)
PLAYER_STATE_SPAWNING = 1

# 道具掉出屏幕的 subrank 惩罚(OnUpdate: DecreaseSubrank(3))
OFFSCREEN_SUBRANK_PENALTY = 3

# DROP_TABLE / POWER_LEVELS / FULL_POWER / FULL_POWER_SCORE_BONUS:
# th07 数值表, 单一来源在 touhou/games_th07.py; 这里的导入即引擎默认表
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


# 状态
STATE_FALL = 0      # 下落
STATE_ATTRACT = 1   # 向玩家吸附
STATE_SPAWN = 2     # 生成动画(60帧飞向目标后转下落)


class CollectResult(msgspec.Struct):
    """收集一个道具后的结算(尚未应用到全局)。"""

    score: int = 0            # 显示分变化(需上层加)
    delta_power: float = 0.0
    delta_bombs: int = 0
    delta_lives: int = 0
    delta_cherry: int = 0       # 仅 cherry 轨(AddCherry)
    delta_cherry_plus: int = 0  # cherryPlus 轨(AddCherryPlus, 同时也累加 cherry)
    extends: int = 0            # 本次收集获得的残机数(点道具阈值, 可连升多个)
    clear_bullets: bool = False
    point_items_collected: int = 0  # 计入残机累计的点道具数
    full_power: bool = False
    subrank: int = 0
    power_overflow_next: int | None = None  # 满火力计分计数器新值(非空则需上层写回)


class GameContext(msgspec.Struct):
    """ItemWorld 依赖的游戏状态(输入环境快照, 带默认值)。"""

    power: float = 0.0
    lives: int = 3
    bombs: int = 2
    graze_total: int = 0
    player_pos: Vec2 = Vec2(192, 400)
    player_alive: bool = True
    player_state: int = 0       # 1=SPAWNING(重生中, 道具缓降不吸附)
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
    item_collect_speed: float = ITEM_COLLECT_SPEED
    item_collect_radius: float = ITEM_COLLECT_RADIUS


class Item(msgspec.Struct):
    """一个道具。"""

    type: ItemType
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


class ItemWorld(msgspec.Struct):
    """道具管理器。"""

    items: list[Item] = msgspec.field(default_factory=list)

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

    # ---- 每帧 ----
    def step(self, ctx: GameContext, dt: float = 1.0) -> list[Item]:
        """推进所有道具; 返回本帧掉出屏幕被删除的道具(每个应 subrank -3)。"""
        dropped: list[Item] = []
        keep: list[Item] = []
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
            # 出屏(底边)删除: subrank -3 由上层按返回列表应用
            if item.pos.y >= 448 + 16:
                dropped.append(item)
            else:
                keep.append(item)
        self.items = keep
        return dropped

    def _status_change(self, item: Item, ctx: GameContext) -> None:
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

    def collect_pickup(self, item: Item, ctx: GameContext) -> bool:
        """收集判定: 与玩家收点盒相交且玩家非死亡/重生。"""
        if not ctx.player_alive or ctx.player_state == PLAYER_STATE_SPAWNING:
            return False
        return item.pos.distance(ctx.player_pos) <= ctx.item_collect_radius

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

    # ---- 批量操作 (§E.5) ----
    def remove_all_items(self) -> None:
        """全部道具转吸附, 速度 (0,-0.5) (ItemManager::RemoveAllItems)。"""
        for item in self.items:
            item.state = STATE_ATTRACT
            item.start = Vec2(0.0, -0.5)

    def despawn_all_items(self, skip: Item | None = None) -> None:
        """场上 POWER_SMALL/POWER_BIG 转 CHERRY (ItemManager::DespawnAllItems)。"""
        for item in self.items:
            if item is skip:
                continue
            if item.type in (ItemType.POWER_SMALL, ItemType.POWER_BIG):
                if item.start.y > -0.5:
                    item.start = Vec2(0.0, -0.5)
                item.type = ItemType.CHERRY

    def activate_all_items(self) -> None:
        """吸附中的道具转回下落, 速度 (0,-0.9) (ItemManager::ActivateAllItems)。"""
        for item in self.items:
            if item.state == STATE_ATTRACT:
                item.state = STATE_FALL
                item.start = Vec2(0.0, -0.9)

    def remove(self, item: Item) -> None:
        if item in self.items:
            self.items.remove(item)

    def clear(self) -> None:
        self.items.clear()

    def alive(self) -> list[Item]:
        return self.items

    def __len__(self) -> int:
        return len(self.items)


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


def _lerp(a: Vec2, b: Vec2, t: float) -> Vec2:
    return a + (b - a) * t
