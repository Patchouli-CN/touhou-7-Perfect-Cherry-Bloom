""" 炸弹与樱之结界 —— 移植自 BombData.cpp / Player.cpp(Border 部分) / 规格 §D。

通用机制 (§D.2):
- 触发 (try_start_bomb): 消耗一枚 → isInUse, 首帧初始化 duration/invuln/cherryDrain,
  透出 subrank/respawn/符卡事件给上层。
- 每帧 (Bomb.tick): UpdateBombProjectiles(伤害盒 size.x 清零、清弹盒 lifetime--/半径增长)
  → 每帧扣 cherryDrain(透出 drain_applied, 上层调 ZunGlobals.subtract_cherry_drain)
  → 机体 calc(布置 bombDamageBoxes / bombClearBoxes)。
- 12 套炸弹按 (character, focus) 查表分派 (_BOMB_CALCS), 全部移植自 BombData.cpp。
- 樱之结界 (Border, §D.5): NONE→READY→ACTIVE→自然破/主动破 状态机。

纯逻辑模块: 不 import globals; 外部值(cherry/难度/玩家位置等)由参数传入,
效果(伤害盒/清弹盒/事件/计时器)由返回值与字段透出。
帧率倍率 effectiveFramerateMultiplier 按 1.0 处理。
"""

from __future__ import annotations

import math
import random
import msgspec
from enum import IntEnum
from typing import Callable

from ..games_th07 import BOMB_PARAMS as _BOMB_PARAMS_RAW
from ..utils import Vec2, cdiv, cmod, normalize_angle_diff

# 清弹盒掉落道具类型 (ItemManager.hpp; 与 items.ItemType 同值, 为避免耦合此处直接定义)
ITEM_POINT_BULLET = 6    # ITEM_POINT_BULLET: 弹消点
ITEM_CHERRY_SMALL = 8    # ITEM_CHERRY_SMALL: BreakBorder 清屏掉小樱点 (Player.cpp:2182)

# 难度索引 (同 globals.RANK_TABLE 顺序: Easy/Normal/Hard/Lunatic/Extra/Phantasm)
DIFF_EASY, DIFF_NORMAL, DIFF_HARD, DIFF_LUNATIC, DIFF_EXTRA, DIFF_PHANTASM = range(6)
# ComputeBombCherryDrain 难度除数 (BombData.cpp:91-103)
_DRAIN_DIVISOR = {DIFF_HARD: 2, DIFF_LUNATIC: 4, DIFF_EXTRA: 3, DIFF_PHANTASM: 3}

# 机体索引 (g_BombData / shotTypeAndCharacter 顺序)
CHAR_REIMU_A, CHAR_REIMU_B, CHAR_MARISA_A, CHAR_MARISA_B, CHAR_SAKUYA_A, CHAR_SAKUYA_B = range(6)

BOMB_SUBRANK_PENALTY = 200    # DecreaseSubrank(200) (Player.cpp:1747)
BOMB_RESPAWN_PENALTY = 6      # respawnTimer += 6, 封顶 initialRespawnTimer (Player.cpp:1750-1754)
BOMB_DURATION_PLACEHOLDER = 999  # 触发时占位 duration (Player.cpp:1736)

BORDER_DURATION = 540         # ActivateBorder: invulnerabilityTimer=borderTimer=540 (Player.cpp:2113-2114)
BORDER_BREAK_INVULN = 40      # BreakBorder(Naturally): invuln/borderInvulnerabilityTime=40
BORDER_CHERRY_GAIN = 10000    # BreakBorderNaturally: IncreaseCherryMax/IncreaseCherry(10000)
CHERRY_MAX_RANGE = 9999990    # IncreaseCherryMax 上限(同 globals.CHERRY_MAX_RANGE, 为避免耦合重定义)

# 透出事件(上层接线用)
EVENT_REMOVE_ALL_ITEMS = "remove_all_items"              # g_ItemManager.RemoveAllItems()
EVENT_END_PLAYER_SPELLCARD = "end_player_spellcard"      # g_Gui.EndPlayerSpellcard()
EVENT_STOP_BULLET_MOVEMENT = "stop_bullet_movement"      # g_BulletManager.StopBulletMovement() (咲夜B 停时)

# 震屏事件 (BombEffects::RegisterChain(1, ...) 各注册点, ScreenEffect.cpp:249
# OnUpdateScreenShake): bomb.shakes 元素为 (duration, amp_start, amp_end),
# 振幅随帧从 amp_start 线性插值到 amp_end; 衰减/随机偏移由 view 层维护。


class BorderState(IntEnum):
    NONE = 0
    ACTIVE = 1
    READY = 2


def compute_bomb_cherry_drain(*, cherry: float, cherry_start: float, difficulty: int,
                              bomb_duration: int, min_cost: int, scale: float) -> int:
    """BombData::ComputeBombCherryDrain (BombData.cpp:87-112)。照抄 C++ 整数截断顺序。

    drain=(cherry-cherryStart)*scale (i32 截断) → 按难度 Hard/2 Lunatic/4 Extra·Phantasm/3
    → /bombDuration → 取整到 10; minCost 同处理; cherryDrain = max(drain, minCost)。
    """
    drain = int((cherry - cherry_start) * scale)  # (i32)(f32) 向零截断
    divisor = _DRAIN_DIVISOR.get(difficulty)
    if divisor is not None:
        drain = cdiv(drain, divisor)
    drain = cdiv(drain, bomb_duration)
    drain -= cmod(drain, 10)
    min_cost = cdiv(min_cost, bomb_duration)
    min_cost -= cmod(min_cost, 10)
    return max(drain, min_cost)


class BombParams(msgspec.Struct, frozen=True):
    """§D.3: 单机体单形态炸弹的首帧初始化参数 (BombData.cpp 各 *Calc 的 bombTimer==0 分支)。"""

    duration: int         # bombDuration
    invulnerability: int  # player.invulnerabilityTimer
    drain_min_cost: int   # ComputeBombCherryDrain minCost
    drain_scale: float    # ComputeBombCherryDrain scale


# §D.3 六机体参数表 (数值以 BombData.cpp 为准; 原始行集中在
# touhou/games_th07.py 的 BOMB_PARAMS —— 单一来源, 这里只包成 BombParams)
BOMB_PARAMS: dict[tuple[int, bool], BombParams] = {
    key: BombParams(*raw) for key, raw in _BOMB_PARAMS_RAW.items()
}


class DamageBox(msgspec.Struct):
    """炸弹伤害盒(bombDamageBoxes): size 为全宽/全高, 判定 pos±size/2 (Player.cpp:914-915)。

    lifetime 即每帧伤害; damage 累计已造成伤害(供追踪类炸弹判断爆开, Player.cpp:926)。
    """

    pos: Vec2
    size: Vec2
    lifetime: int
    damage: int = 0

    @property
    def active(self) -> bool:
        return self.size.x > 0 and self.lifetime > 0


class ClearBox(msgspec.Struct):
    """炸弹清弹盒(bombClearBoxes, §D.2.6 / Player.cpp:949-999, 1658-1681)。

    - pos_z != 0 → 线性段 AABB: 宽=pos_z, 高=size.x (中心 pos 各取一半)。
    - pos_z == 0 且 size.y != 0 → 圆: dist²(center, pos) < size.y²。
    - 两者皆 0 → 空槽(不活跃)。
    tick (UpdateBombProjectiles): lifetime<=0 → 清零(size.y=0, pos_z=0);
    否则 lifetime--, size.y += growth (size.z, 半径增长)。
    """

    pos: Vec2
    size: Vec2           # size.x=线性段高, size.y=圆半径
    lifetime: int
    item_type: int
    pos_z: float = 0.0   # pos.z: 线性段宽
    growth: float = 0.0  # size.z: 半径每帧增长

    @property
    def active(self) -> bool:
        return self.pos_z != 0.0 or self.size.y != 0.0

    def tick(self) -> None:
        """Player::UpdateBombProjectiles 的清弹盒部分 (Player.cpp:1667-1679)。"""
        if self.lifetime <= 0:
            self.size = Vec2(self.size.x, 0.0)
            self.pos_z = 0.0
        else:
            self.lifetime -= 1
            self.size = Vec2(self.size.x, self.size.y + self.growth)

    def hits(self, center: Vec2, size: Vec2) -> bool:
        """CheckBombGraze: center±size/2 的弹盒是否命中此清弹盒 (Player.cpp:965-996)。"""
        if self.pos_z != 0.0:
            # 线性段: AABB, 宽=pos_z, 高=size.x
            return (abs(center.x - self.pos.x) <= (self.pos_z + size.x) / 2
                    and abs(center.y - self.pos.y) <= (self.size.x + size.y) / 2)
        if self.size.y != 0.0:
            # 圆: dist² < size.y²
            d = center - self.pos
            return d.x * d.x + d.y * d.y < self.size.y * self.size.y
        return False


class BombSubInfo(msgspec.Struct):
    """PlayerBombSubInfo 的逻辑部分 (§D.1): 珠弹状态机 0=空 1=飞行 2=爆开。

    pos/vel 对应 bombRegionPositions/bombRegionVelocities; accel 在魔理沙B 被
    ZUN 复用为激光臂角度; accel_vec 为 bombRegionAcceleration 的 Vec2 用途
    (魔理沙A 集中/咲夜B 集中); angle_drift 为其 .x 的角漂移用途 (咲夜A);
    sub_timer 为 per-sub ZunTimer (咲夜A 集中)。轨迹 trails/AnmVm/Effect 为视觉, 不移植。
    """

    state: int = 0
    counter: int = 0
    speed: float = 0.0
    accel: float = 0.0
    angle: float = 0.0
    pos: Vec2 = msgspec.field(default_factory=Vec2.zero)
    vel: Vec2 = msgspec.field(default_factory=Vec2.zero)
    accel_vec: Vec2 = msgspec.field(default_factory=Vec2.zero)
    angle_drift: float = 0.0
    sub_timer: int = 0


class BombContext(msgspec.Struct):
    """bombCalc 的每帧外部输入 (纯逻辑模块, 不依赖 globals)。"""

    player_pos: Vec2
    cherry: float = 0.0
    cherry_start: float = 0.0
    difficulty: int = DIFF_NORMAL
    last_enemy_hit: Vec2 | None = None  # positionOfLastEnemyHit (None/x<=-100 → 追玩家)
    rng_float: Callable[[], float] | None = None  # [0,1) 随机 (ReimuA/MarisaA 集中, 咲夜A), None→random.random


class BombStartResult(msgspec.Struct):
    """try_start_bomb 的透出: 上层据此更新 globals/符卡/respawn (Player.cpp:1728-1754)。"""

    started: bool = False
    bombs_used_delta: int = 0              # AddBombsUsed(1)
    bombs_remaining_delta: int = 0         # AddBombsRemaining(-1)
    subrank_delta: int = 0                 # DecreaseSubrank(200)
    respawn_timer: int = 0                 # min(respawnTimer+6, initialRespawnTimer)
    spellcard_capture_reset: bool = False  # captureScore=0, isCapturing=0
    spellcard_used_bomb: bool = False      # usedBomb = isActive


class Bomb(msgspec.Struct):
    """一次炸弹的生命周期 (PlayerBombInfo + UpdateBorderAndBombState 的 bomb 分支)。

    上层每帧消费 damage_boxes(伤害) / clear_boxes(清弹) / drain_applied(扣樱点) /
    invulnerability_timer(首帧设定的无敌) / invulnerable(bomb 期间 playerState=INVULNERABLE) /
    move_speed_multiplier(炸弹中移速倍率) / events。
    """

    character: int = CHAR_REIMU_A
    is_in_use: bool = False
    is_focus: bool = False
    duration: int = 0
    cherry_drain: int = 0
    timer: int = 0
    has_ticked: bool = False  # ZunTimer: current != previous
    invulnerability_timer: int = 0
    invulnerable: bool = True
    move_speed_multiplier: float = 1.0
    start_pos: Vec2 = msgspec.field(default_factory=Vec2.zero)
    item_type: int = ITEM_POINT_BULLET  # CheckBombGraze 命中后透出的掉落类型
    drain_applied: int = 0              # 本帧应扣樱点(上层调 subtract_cherry_drain)
    damage_boxes: list[DamageBox] = msgspec.field(default_factory=list)
    clear_boxes: list[ClearBox] = msgspec.field(default_factory=list)
    sub_info: list[BombSubInfo] = msgspec.field(default_factory=list)
    events: list[str] = msgspec.field(default_factory=list)
    shakes: list[tuple[int, int, int]] = msgspec.field(default_factory=list)  # 震屏事件(见文件头)

    # ---- 触发/结束 ----
    def start(self, *, focus: bool, ctx: BombContext) -> None:
        """触发炸弹 (Player.cpp:1732-1744)。duration/invuln/cherryDrain 由机体 calc 首帧设定。"""
        self.is_in_use = True
        self.is_focus = focus
        self.duration = BOMB_DURATION_PLACEHOLDER
        self.cherry_drain = 0
        self.timer = 0
        self.has_ticked = True  # ZunTimer operator=: previous=-999 → 视为已 tick
        self.invulnerability_timer = 0
        self.invulnerable = True
        self.move_speed_multiplier = 1.0
        self.drain_applied = 0
        self.damage_boxes = [DamageBox(Vec2.zero(), Vec2.zero(), 0) for _ in range(112)]
        self.clear_boxes = []
        self.sub_info = [BombSubInfo() for _ in range(128)]  # C++ subInfo[128] (Player.hpp:116)
        self.events = []
        self.shakes = []
        self._calc(ctx)  # 触发当帧立刻调用一次

    def tick(self, ctx: BombContext) -> bool:
        """推进一帧 (Player::OnUpdate: UpdateBombProjectiles → UpdateBorderAndBombState)。

        UpdateBombProjectiles 每帧无条件执行 (Player.cpp:2231), 即使 bomb 已结束
        (清弹盒可比 bomb 活得久, 如灵梦B 集中 lifetime=210 > duration=190)。
        返回是否仍在进行。
        """
        # UpdateBombProjectiles (Player.cpp:1658-1681): 伤害盒 size.x 清零, 清弹盒推进
        for box in self.damage_boxes:
            box.size = Vec2(0.0, box.size.y)
        for cbox in self.clear_boxes:
            cbox.tick()
        if not self.is_in_use:
            self.drain_applied = 0
            return False
        # 每帧扣樱点 (Player.cpp:1705-1708); 上层用 drain_applied 调 subtract_cherry_drain
        self.drain_applied = self.cherry_drain if self.has_ticked else 0
        self._calc(ctx)
        return self.is_in_use

    def _calc(self, ctx: BombContext) -> None:
        """按 (character, focus) 查表分派机体炸弹 (g_BombData, BombData.cpp:16-28)。"""
        calc = _BOMB_CALCS.get((self.character, self.is_focus))
        if calc is None:  # 12 套已全, 仅防御未知 character
            raise NotImplementedError(
                f"character={self.character} focus={self.is_focus} 炸弹未实现")
        calc(self, ctx)

    def _free_clear_slot(self) -> ClearBox:
        """Spawn* 的槽位搜索: 首个 pos_z==0 且 size.y==0 的空槽; 全满写第 96 槽
        (C++ 循环只扫 0..94, 落空后写 bomb[95], Player.cpp:2044-2050/2069-2075)。"""
        for box in self.clear_boxes:
            if box.pos_z == 0.0 and box.size.y == 0.0:
                return box
        if len(self.clear_boxes) < 96:
            box = ClearBox(Vec2.zero(), Vec2.zero(), 0, ITEM_POINT_BULLET)
            self.clear_boxes.append(box)
            return box
        return self.clear_boxes[95]

    def _spawn_clear(self, pos: Vec2, *, radius: float, growth: float,
                     lifetime: int, item_type: int) -> ClearBox:
        """Player::SpawnBombEffect (Player.cpp:2063-2084): 清弹圆, 半径每帧 +growth。

        照抄 C++: 复用槽时只写 pos/size.y/size.z/lifetime/itemType, 不动 size.x/pos_z。
        """
        box = self._free_clear_slot()
        box.pos = pos
        box.size = Vec2(box.size.x, radius)
        box.lifetime = lifetime
        box.item_type = item_type
        box.growth = growth
        return box

    def _spawn_projectile(self, pos: Vec2, *, width: float, height: float,
                          item_type: int) -> ClearBox:
        """Player::SpawnBombProjectile (Player.cpp:2038-2060): 线性段清弹盒。

        pos.z=width(段宽), size.x=height(段高), lifetime=0 (下帧 UpdateBombProjectiles 清零,
        仅存活当帧); 照抄 C++: 复用槽时不动 size.y/size.z。
        """
        box = self._free_clear_slot()
        box.pos = pos
        box.pos_z = width
        box.size = Vec2(height, box.size.y)
        box.lifetime = 0
        box.item_type = item_type
        return box

    # ---- 清弹判定 (CheckBombGraze) ----
    def check_bomb_graze(self, center: Vec2, size: Vec2) -> int:
        """Player::CheckBombGraze: 弹盒 center±size/2 命中任一清弹盒 → 返回 2 并透出 item_type。"""
        for box in self.clear_boxes:
            if box.hits(center, size):
                self.item_type = box.item_type
                return 2
        return 0

    # ---- 敌侧面伤害 (CalcDamageToEnemy 的炸弹盒部分) ----
    def damage_to(self, enemy_pos: Vec2, enemy_half: Vec2) -> int:
        """对 enemy 的总伤害: lifetime 即每帧伤害, 同时累计入 box.damage (Player.cpp:907-927)。"""
        total = 0
        for box in self.damage_boxes:
            if box.size.x <= 0.0:
                continue
            if _aabb(box.pos, box.size / 2, enemy_pos, enemy_half):
                total += box.lifetime
                box.damage += box.lifetime
        return total

    def hits(self, enemy_pos: Vec2, enemy_half: Vec2) -> bool:
        """伤害盒是否与敌人盒相交(纯判定, 不累计 box.damage)。

        对应 CalcDamageToEnemy 的 collisionOut 置位条件 (Player.cpp:939-942):
        炸弹中且任一伤害盒命中 → *param_3=1。"""
        for box in self.damage_boxes:
            if box.size.x <= 0.0:
                continue
            if _aabb(box.pos, box.size / 2, enemy_pos, enemy_half):
                return True
        return False


# 一个简单的 AABB 相交(盒中心 + 半宽)
def _aabb(a_center: Vec2, a_half: Vec2, b_center: Vec2, b_half: Vec2) -> bool:
    return (abs(a_center.x - b_center.x) < a_half.x + b_half.x
            and abs(a_center.y - b_center.y) < a_half.y + b_half.y)


def _in_bounds(x: float, y: float, width: float, height: float) -> bool:
    """GameManager::IsInBounds (GameManager.cpp:42-65): 盒心±半宽/半高落在
    (0..384, 0..448) 内为 True (咲夜A 非集中出界判定)。"""
    return not (width / 2.0 + x < 0.0 or x - width / 2.0 > 384.0
                or height / 2.0 + y < 0.0 or y - height / 2.0 > 448.0)


def try_start_bomb(bomb: Bomb, ctx: BombContext, *, focus: bool,
                   bombs_remaining: float, respawn_timer: int,
                   initial_respawn_timer: int, border_invulnerability_time: int,
                   bomb_pressed: bool, spellcard_active: bool) -> BombStartResult:
    """炸弹触发 (Player::UpdateBorderAndBombState 触发分支, Player.cpp:1719-1755)。

    条件: 非 bomb 中 && respawn_timer != 0 && bombs > 0 && border_invulnerability_time == 0
    && 按下 bomb 键 (对话框/integrity 检查属上层, 不在此)。
    成功时 bomb 已 start(含当帧一次 calc), 事件见 BombStartResult。
    注意: has_border != NONE 时按 bomb 键是 BreakBorder 而非触发炸弹 (Player.cpp:1686-1692),
    该分支由上层先判断。
    """
    result = BombStartResult()
    if (bomb.is_in_use or not bomb_pressed or respawn_timer == 0
            or bombs_remaining <= 0 or border_invulnerability_time != 0):
        return result
    bomb.start(focus=focus, ctx=ctx)
    result.started = True
    result.bombs_used_delta = 1
    result.bombs_remaining_delta = -1
    result.subrank_delta = -BOMB_SUBRANK_PENALTY
    result.respawn_timer = min(respawn_timer + BOMB_RESPAWN_PENALTY,
                               initial_respawn_timer)
    result.spellcard_capture_reset = True
    result.spellcard_used_bomb = spellcard_active
    return result


# ---------------------------------------------------------------------------
# ReimuA 「灵符·梦想妙珠」 (规格 §D.4)
# ---------------------------------------------------------------------------

def _calc_reimu_a_unfocused(bomb: Bomb, ctx: BombContext) -> None:
    """灵梦A 非集中「梦想妙珠散」 (BombData.cpp:116-256)。

    12 个珠从玩家向 8 方向散开(timer 12..78 每 6 帧一个, i=(timer-8)/6),
    速度 15 起每帧 -0.4; 速度 < -10 爆开: 伤害盒 256×256(lifetime=400,
    同帧被 48×48/8 覆写, 见下), 后续每帧 256×256/lifetime=2 共 30 帧;
    同时 SpawnBombEffect(64, 4.2667, 30) 清弹圆(半径增长)。
    """
    if bomb.timer >= bomb.duration:
        bomb.is_in_use = False
        bomb.events.append(EVENT_END_PLAYER_SPELLCARD)
        return
    if bomb.has_ticked and bomb.timer == 0:
        params = BOMB_PARAMS[(CHAR_REIMU_A, False)]
        bomb.duration = params.duration
        bomb.invulnerability_timer = params.invulnerability
        for sub in bomb.sub_info:
            sub.state = 0
        bomb.events.append(EVENT_REMOVE_ALL_ITEMS)
        bomb._spawn_clear(ctx.player_pos, radius=32.0, growth=8.0,
                          lifetime=16, item_type=ITEM_POINT_BULLET)
        bomb.start_pos = ctx.player_pos
        bomb.cherry_drain = compute_bomb_cherry_drain(
            cherry=ctx.cherry, cherry_start=ctx.cherry_start,
            difficulty=ctx.difficulty, bomb_duration=params.duration,
            min_cost=params.drain_min_cost, scale=params.drain_scale)
    if bomb.has_ticked and 8 <= bomb.timer < 80 and bomb.timer % 6 == 0:
        i = (bomb.timer - 8) // 6
        sub = bomb.sub_info[i]
        sub.state = 1
        sub.speed = 15.0
        sub.pos = ctx.player_pos
        if bomb.start_pos.x < 192.0:
            angle = i * math.tau / 8.0 - math.pi / 2
        else:
            angle = -i * math.tau / 8.0 - math.pi / 2
        sub.angle = angle % math.tau  # utils::AddNormalizeAngle(angle, 0)
        sub.counter = 0
        bomb.damage_boxes[i].damage = 0
    bomb.invulnerable = True  # playerState = INVULNERABLE (BombData.cpp:182)
    # 注意: C++ 只处理 subInfo[0..7]; spawn 的 i=8..11 静置原地 (ZUN quirk)
    for i in range(8):
        sub = bomb.sub_info[i]
        if sub.state == 0:
            continue
        box = bomb.damage_boxes[i]
        if sub.state == 1:
            sub.speed -= 0.4  # * effectiveFramerateMultiplier(=1.0)
            sub.vel = Vec2.from_angle(sub.angle, sub.speed)
            if sub.speed < -10.0:
                sub.state = 2
                box.pos = sub.pos
                box.size = Vec2(256.0, 256.0)
                box.lifetime = 400
                bomb._spawn_clear(sub.pos, radius=64.0, growth=4.266667,
                                  lifetime=30, item_type=ITEM_POINT_BULLET)
                sub.vel = Vec2.zero()
                # 珠爆开震屏 (BombData.cpp:218 RegisterChain(1,16,8,0))
                bomb.shakes.append((16, 8, 0))
            if bomb.has_ticked:
                # C++ 同帧继续执行 (BombData.cpp:220-229): 爆开当帧伤害盒被覆写为 48×48/8
                box.size = Vec2(48.0, 48.0)
                box.pos = sub.pos
                box.lifetime = 8
                bomb._spawn_clear(sub.pos, radius=128.0, growth=0.0,
                                  lifetime=0, item_type=ITEM_POINT_BULLET)
        elif bomb.has_ticked:
            box.pos = sub.pos
            box.size = Vec2(256.0, 256.0)
            box.lifetime = 2
            sub.counter += 1
            if sub.counter >= 30:
                sub.state = 0
        sub.pos = sub.pos + sub.vel
    bomb.timer += 1


def _calc_reimu_a_focused(bomb: Bomb, ctx: BombContext) -> None:
    """灵梦A 集中「梦想妙珠集」 (BombData.cpp:310-474)。

    7 个追踪珠(timer 80..176 每 16 帧一个, i=(timer-60)/16, i==0 跳过),
    初速 8 随机方向, 每帧朝 positionOfLastEnemyHit 追踪(限速 1..10);
    累计伤害 >= 100 或临近结束时爆开: 伤害盒 256×256/lifetime=400 +
    SpawnBombEffect(32, 6.6667, 15) 清弹圆。炸弹中移速 ×0.6。
    """
    if bomb.timer >= bomb.duration:
        bomb.is_in_use = False
        bomb.move_speed_multiplier = 1.0
        bomb.events.append(EVENT_END_PLAYER_SPELLCARD)
        return
    if bomb.has_ticked and bomb.timer == 0:
        params = BOMB_PARAMS[(CHAR_REIMU_A, True)]
        bomb.duration = params.duration
        bomb.invulnerability_timer = params.invulnerability
        for sub in bomb.sub_info:
            sub.state = 0
        bomb.events.append(EVENT_REMOVE_ALL_ITEMS)
        bomb._spawn_clear(ctx.player_pos, radius=32.0, growth=8.0,
                          lifetime=16, item_type=ITEM_POINT_BULLET)
        bomb.cherry_drain = compute_bomb_cherry_drain(
            cherry=ctx.cherry, cherry_start=ctx.cherry_start,
            difficulty=ctx.difficulty, bomb_duration=params.duration,
            min_cost=params.drain_min_cost, scale=params.drain_scale)
        bomb.move_speed_multiplier = 0.6
    if 60 <= bomb.timer < 180 and bomb.timer % 16 == 0:
        i = (bomb.timer - 60) // 16
        if i != 0:
            sub = bomb.sub_info[i]
            sub.state = 1
            sub.counter = 0
            sub.accel = 8.0
            sub.pos = ctx.player_pos
            rng = ctx.rng_float or random.random
            angle = rng() * math.tau - math.pi
            sub.vel = Vec2.from_angle(angle, sub.accel)
            bomb.damage_boxes[i].damage = 0
    bomb.invulnerable = True
    for i in range(8):
        sub = bomb.sub_info[i]
        if sub.state == 0:
            continue
        box = bomb.damage_boxes[i]
        if sub.state == 1:
            if bomb.has_ticked:
                if ctx.last_enemy_hit is not None and ctx.last_enemy_hit.x > -100.0:
                    target = ctx.last_enemy_hit
                else:
                    target = ctx.player_pos
                dx = target.x - sub.pos.x
                dy = target.y - sub.pos.y
                t = math.sqrt(dx * dx + dy * dy) / (sub.accel / 8.0)
                if t < 1.0:
                    t = 1.0
                vx = dx / t + sub.vel.x
                vy = dy / t + sub.vel.y
                speed = math.sqrt(vx * vx + vy * vy)
                sub.accel = min(speed, 10.0)
                if sub.accel < 1.0:
                    sub.accel = 1.0
                sub.vel = Vec2(vx * sub.accel / speed, vy * sub.accel / speed)
                box.size = Vec2(48.0, 48.0)
                box.pos = sub.pos
                box.lifetime = 8
                bomb._spawn_clear(sub.pos, radius=128.0, growth=0.0,
                                  lifetime=0, item_type=ITEM_POINT_BULLET)
                if box.damage >= 100 or bomb.timer >= bomb.duration - 30:
                    sub.state = 2
                    box.size = Vec2(256.0, 256.0)
                    box.lifetime = 400
                    bomb._spawn_clear(sub.pos, radius=32.0, growth=6.6666665,
                                      lifetime=15, item_type=ITEM_POINT_BULLET)
                    # 珠爆开震屏 (BombData.cpp:450 RegisterChain(1,16,8,0))
                    bomb.shakes.append((16, 8, 0))
        elif bomb.has_ticked:
            # 集中版 state=2 不再刷新伤害盒 (BombData.cpp:454-461)
            sub.counter += 1
            if sub.counter >= 30:
                sub.state = 0
        sub.pos = sub.pos + sub.vel
    bomb.timer += 1


# ---------------------------------------------------------------------------
# ReimuB 「灵符·梦想封印」 (规格 §D.4)
# ---------------------------------------------------------------------------

def _calc_reimu_b_unfocused(bomb: Bomb, ctx: BombContext) -> None:
    """灵梦B 非集中「梦想封印散」 (BombData.cpp:512-601)。

    4 条结界光束, 锚点在触发帧定格: subInfo[0..3] = (player.x,224)/(192,player.y) ×2。
    每帧 SpawnBombProjectile ×4 (62×448 竖 / 384×62 横, lifetime=0 仅存活当帧);
    奇数帧段中心移到锚点并刷新伤害盒 (size=(段宽,段高), lifetime=16),
    偶数帧段留在玩家位置 (ZUN quirk: projectiles 未移动时保持 positionCenter)。
    (C++ 段中心另加 vms[0].offset —— anm 脚本驱动的光束扫动, 视觉数据源, 本移植略。)
    """
    if bomb.timer >= bomb.duration:
        bomb.is_in_use = False
        bomb.events.append(EVENT_END_PLAYER_SPELLCARD)
        return
    if bomb.has_ticked and bomb.timer == 0:
        params = BOMB_PARAMS[(CHAR_REIMU_B, False)]
        bomb.duration = params.duration
        bomb.invulnerability_timer = params.invulnerability
        bomb.events.append(EVENT_REMOVE_ALL_ITEMS)
        # 锚点 (bombRegionPositions; .z=0.42/0.415/0.41/0.405 为视觉深度, 略)
        bomb.sub_info[0].pos = Vec2(ctx.player_pos.x, 224.0)
        bomb.sub_info[1].pos = Vec2(192.0, ctx.player_pos.y)
        bomb.sub_info[2].pos = Vec2(ctx.player_pos.x, 224.0)
        bomb.sub_info[3].pos = Vec2(192.0, ctx.player_pos.y)
        bomb.cherry_drain = compute_bomb_cherry_drain(
            cherry=ctx.cherry, cherry_start=ctx.cherry_start,
            difficulty=ctx.difficulty, bomb_duration=params.duration,
            min_cost=params.drain_min_cost, scale=params.drain_scale)
        # 首帧震屏 (BombData.cpp:559 RegisterChain(1,60,2,6))
        bomb.shakes.append((60, 2, 6))
    else:
        # BombData.cpp:566: bombTimer==60 大震屏 RegisterChain(1,80,20,0)
        if bomb.timer == 60:
            bomb.shakes.append((80, 20, 0))
        projectiles = [
            bomb._spawn_projectile(ctx.player_pos, width=62.0, height=448.0,
                                   item_type=ITEM_POINT_BULLET),
            bomb._spawn_projectile(ctx.player_pos, width=384.0, height=62.0,
                                   item_type=ITEM_POINT_BULLET),
            bomb._spawn_projectile(ctx.player_pos, width=62.0, height=448.0,
                                   item_type=ITEM_POINT_BULLET),
            bomb._spawn_projectile(ctx.player_pos, width=384.0, height=62.0,
                                   item_type=ITEM_POINT_BULLET),
        ]
        for i in range(4):
            if bomb.has_ticked and bomb.timer % 2 != 0:
                projectiles[i].pos = bomb.sub_info[i].pos  # + vms[0].offset (anm, 略)
                box = bomb.damage_boxes[i]
                box.size = Vec2(projectiles[i].pos_z, projectiles[i].size.x)
                box.pos = bomb.sub_info[i].pos             # + vms->offset (anm, 略)
                box.lifetime = 16
    bomb.invulnerable = True  # playerState = INVULNERABLE (BombData.cpp:598)
    bomb.timer += 1


def _calc_reimu_b_focused(bomb: Bomb, ctx: BombContext) -> None:
    """灵梦B 集中「梦想封印集」 (BombData.cpp:645-694)。

    首帧: SpawnBombEffect(192, 0.384, 210) 缓慢扩张的大清弹圆 (比 duration=190 多活 20 帧)
    + 移速 ×0.4; 之后每帧伤害盒 256×256/lifetime=18 钉在 startPos。
    (C++ 伤害盒位置另加 vms[0].offset —— anm 驱动, 略。)
    """
    if bomb.timer >= bomb.duration:
        bomb.is_in_use = False
        bomb.move_speed_multiplier = 1.0
        bomb.events.append(EVENT_END_PLAYER_SPELLCARD)
        return
    if bomb.has_ticked and bomb.timer == 0:
        params = BOMB_PARAMS[(CHAR_REIMU_B, True)]
        bomb.duration = params.duration
        bomb.invulnerability_timer = params.invulnerability
        bomb.events.append(EVENT_REMOVE_ALL_ITEMS)
        bomb.start_pos = ctx.player_pos
        bomb.cherry_drain = compute_bomb_cherry_drain(
            cherry=ctx.cherry, cherry_start=ctx.cherry_start,
            difficulty=ctx.difficulty, bomb_duration=params.duration,
            min_cost=params.drain_min_cost, scale=params.drain_scale)
        bomb.move_speed_multiplier = 0.4
        bomb._spawn_clear(ctx.player_pos, radius=192.0, growth=0.384,
                          lifetime=210, item_type=ITEM_POINT_BULLET)
        # 首帧震屏 (BombData.cpp:654 RegisterChain(1,60,2,6))
        bomb.shakes.append((60, 2, 6))
    else:
        # BombData.cpp:666: bombTimer==60 大震屏 RegisterChain(1,80,20,0)
        if bomb.timer == 60:
            bomb.shakes.append((80, 20, 0))
        box = bomb.damage_boxes[0]
        box.size = Vec2(256.0, 256.0)
        box.pos = bomb.start_pos  # + subInfo[0].vms[0].offset (anm, 略)
        box.lifetime = 18
    bomb.invulnerable = True
    bomb.timer += 1


# ---------------------------------------------------------------------------
# MarisaA 「魔符·Stardust Reverie / Milky Way」 (规格 §D.4)
# ---------------------------------------------------------------------------

def _calc_marisa_a_unfocused(bomb: Bomb, ctx: BombContext) -> None:
    """魔理沙A 非集中「星尘狂欢」 (BombData.cpp:690-770)。

    8 颗星从玩家以 2px/帧 向 8 方向匀速漂移; 仅 timer%3!=0 的帧布置伤害盒
    128×128/lifetime=8 + SpawnBombEffect(96, 0, 0) 清弹圆 (每 3 帧停一拍)。
    """
    if bomb.timer >= bomb.duration:
        bomb.is_in_use = False
        bomb.events.append(EVENT_END_PLAYER_SPELLCARD)
        return
    if bomb.has_ticked and bomb.timer == 0:
        params = BOMB_PARAMS[(CHAR_MARISA_A, False)]
        bomb.duration = params.duration
        bomb.invulnerability_timer = params.invulnerability
        bomb.events.append(EVENT_REMOVE_ALL_ITEMS)
        for i in range(8):
            sub = bomb.sub_info[i]
            sub.pos = ctx.player_pos
            sub.vel = Vec2.from_angle(i * math.tau / 8.0, 2.0)
        bomb.cherry_drain = compute_bomb_cherry_drain(
            cherry=ctx.cherry, cherry_start=ctx.cherry_start,
            difficulty=ctx.difficulty, bomb_duration=params.duration,
            min_cost=params.drain_min_cost, scale=params.drain_scale)
        # 首帧震屏 (BombData.cpp:738 RegisterChain(1,120,4,1))
        bomb.shakes.append((120, 4, 1))
    else:
        for i in range(8):
            sub = bomb.sub_info[i]
            sub.pos = sub.pos + sub.vel  # * effectiveFramerateMultiplier(=1.0)
            if bomb.has_ticked and bomb.timer % 3 != 0:
                bomb._spawn_clear(sub.pos, radius=96.0, growth=0.0,
                                  lifetime=0, item_type=ITEM_POINT_BULLET)
                box = bomb.damage_boxes[i]
                box.size = Vec2(128.0, 128.0)
                box.pos = sub.pos
                box.lifetime = 8
    bomb.invulnerable = True
    bomb.timer += 1


def _calc_marisa_a_focused(bomb: Bomb, ctx: BombContext) -> None:
    """魔理沙A 集中「银河」 (BombData.cpp:779-891)。

    timer%6==0 且 i=timer/6<24 时每 6 帧放一颗星 (首帧即放 i=0): 初速 -5
    (向下!) 方向 -π/2±0.196 随机, 加速度 0.24 同范围随机 (向上弯曲);
    y<-256 出界消。每帧 SpawnBombEffect(96,0,0) + 伤害盒 128×128/lifetime=12
    (该星累计伤害 >=80 后停刷)。移速 ×0.4。
    """
    if bomb.timer >= bomb.duration:
        bomb.is_in_use = False
        bomb.move_speed_multiplier = 1.0
        bomb.events.append(EVENT_END_PLAYER_SPELLCARD)
        return
    if bomb.has_ticked and bomb.timer == 0:
        params = BOMB_PARAMS[(CHAR_MARISA_A, True)]
        bomb.duration = params.duration
        bomb.invulnerability_timer = params.invulnerability
        bomb.events.append(EVENT_REMOVE_ALL_ITEMS)
        for sub in bomb.sub_info:
            sub.state = 0
        bomb.cherry_drain = compute_bomb_cherry_drain(
            cherry=ctx.cherry, cherry_start=ctx.cherry_start,
            difficulty=ctx.difficulty, bomb_duration=params.duration,
            min_cost=params.drain_min_cost, scale=params.drain_scale)
        bomb.move_speed_multiplier = 0.4
    if bomb.has_ticked and bomb.timer % 6 == 0:
        i = bomb.timer // 6
        if i < 24:
            rng = ctx.rng_float or random.random
            sub = bomb.sub_info[i]
            sub.state = 1
            sub.pos = ctx.player_pos
            angle = rng() * 0.3926991 - 0.19634955 - 1.5707964
            sub.vel = Vec2.from_angle(angle, -5.0)
            angle = rng() * 0.3926991 - 0.19634955 - 1.5707964
            sub.accel_vec = Vec2.from_angle(angle, 0.24)
            bomb.damage_boxes[i].damage = 0
            # 每颗星出生震屏 (BombData.cpp:867 RegisterChain(1,120,4,1))
            bomb.shakes.append((120, 4, 1))
    for i in range(24):
        sub = bomb.sub_info[i]
        if sub.state == 0:
            continue
        # 轨迹 trails[8] 为视觉拖尾, 略
        sub.pos = sub.pos + sub.vel
        sub.vel = sub.vel + sub.accel_vec
        if sub.pos.y < -256.0:
            sub.state = 0
        bomb._spawn_clear(sub.pos, radius=96.0, growth=0.0,
                          lifetime=0, item_type=ITEM_POINT_BULLET)
        box = bomb.damage_boxes[i]
        if box.damage < 80:
            box.size = Vec2(128.0, 128.0)
            box.pos = sub.pos
            box.lifetime = 12
    bomb.invulnerable = True
    bomb.timer += 1


# ---------------------------------------------------------------------------
# MarisaB 「恋符·Non-Directional Laser / Master Spark」 (规格 §D.4)
# ---------------------------------------------------------------------------

# 魔理沙B 非集中激光伤害盒间距: vms[0].sprite->heightPx * scale.y / 5 (BombData.cpp:1027)。
# sprite 高×缩放属 anm 数据 (仓库无此数据源), 按 256/5 处理 —— 见移植报告偏差记录。
MARISA_B_LASER_STEP = 256.0 / 5.0


def _calc_marisa_b_unfocused(bomb: Bomb, ctx: BombContext) -> None:
    """魔理沙B 非集中「非定向激光」 (BombData.cpp:952-1048)。

    3 条旋转激光臂 (subInfo[i].accel 被 ZUN 复用为臂角度, 初始 i*2π/3-π/2),
    每帧累计转过 ±timer*π/30/duration (触发时 startPos.x<192 正转, 否则反转);
    每臂从 offset=32 起每 256/5 布一个伤害盒 128×128/lifetime=10 共 6 个
    (damage_boxes[i*6+j]) + SpawnBombEffect(64,0,0)。移速 ×0.4。
    """
    if bomb.timer >= bomb.duration:
        bomb.is_in_use = False
        bomb.move_speed_multiplier = 1.0
        bomb.events.append(EVENT_END_PLAYER_SPELLCARD)
        return
    if bomb.has_ticked and bomb.timer == 0:
        params = BOMB_PARAMS[(CHAR_MARISA_B, False)]
        bomb.duration = params.duration
        bomb.invulnerability_timer = params.invulnerability
        bomb.events.append(EVENT_REMOVE_ALL_ITEMS)
        bomb.start_pos = ctx.player_pos
        for i in range(3):
            sub = bomb.sub_info[i]
            sub.pos = ctx.player_pos
            sub.accel = i * math.tau / 3.0 - math.pi / 2
        bomb.move_speed_multiplier = 0.4
        bomb.cherry_drain = compute_bomb_cherry_drain(
            cherry=ctx.cherry, cherry_start=ctx.cherry_start,
            difficulty=ctx.difficulty, bomb_duration=params.duration,
            min_cost=params.drain_min_cost, scale=params.drain_scale)
    else:
        # BombData.cpp:1047/1051: timer==20 渐强震屏 / timer==80 大震屏
        if bomb.timer == 20:
            bomb.shakes.append((60, 1, 7))
        elif bomb.timer == 80:
            bomb.shakes.append((100, 24, 0))
        for i in range(3):
            sub = bomb.sub_info[i]
            delta = bomb.timer * math.pi / 30.0 / bomb.duration
            if bomb.start_pos.x < 192.0:  # AddNormalizeAngle → (-π, π]
                sub.accel = normalize_angle_diff(sub.accel + delta)
            else:
                sub.accel = normalize_angle_diff(sub.accel - delta)
            offset = 32.0
            for j in range(6):
                box = bomb.damage_boxes[i * 6 + j]
                box.pos = ctx.player_pos + Vec2.from_angle(sub.accel, offset)
                box.size = Vec2(128.0, 128.0)
                box.lifetime = 10
                bomb._spawn_clear(box.pos, radius=64.0, growth=0.0,
                                  lifetime=0, item_type=ITEM_POINT_BULLET)
                offset += MARISA_B_LASER_STEP
    bomb.invulnerable = True
    bomb.timer += 1


def _calc_marisa_b_focused(bomb: Bomb, ctx: BombContext) -> None:
    """魔理沙B 集中「 Master Spark」 (BombData.cpp:1104-1170)。

    timer%4!=0 的帧: 伤害盒 384×player.y 于 (192, player.y/2) lifetime=23 +
    SpawnBombProjectile(宽 384, 高 player.y) 线性段清弹。每 4 帧停一拍。移速 ×0.2。
    """
    if bomb.timer >= bomb.duration:
        bomb.is_in_use = False
        bomb.move_speed_multiplier = 1.0
        bomb.events.append(EVENT_END_PLAYER_SPELLCARD)
        return
    if bomb.has_ticked and bomb.timer == 0:
        params = BOMB_PARAMS[(CHAR_MARISA_B, True)]
        bomb.duration = params.duration
        bomb.invulnerability_timer = params.invulnerability
        bomb.events.append(EVENT_REMOVE_ALL_ITEMS)
        bomb.move_speed_multiplier = 0.2
        bomb.cherry_drain = compute_bomb_cherry_drain(
            cherry=ctx.cherry, cherry_start=ctx.cherry_start,
            difficulty=ctx.difficulty, bomb_duration=params.duration,
            min_cost=params.drain_min_cost, scale=params.drain_scale)
    else:
        # BombData.cpp:1126/1130: timer==60 渐强震屏 / timer==120 大震屏
        if bomb.timer == 60:
            bomb.shakes.append((60, 1, 7))
        elif bomb.timer == 120:
            bomb.shakes.append((200, 24, 0))
        if bomb.has_ticked and bomb.timer % 4 != 0:
            box = bomb.damage_boxes[0]
            box.size = Vec2(384.0, ctx.player_pos.y)
            box.pos = Vec2(192.0, ctx.player_pos.y / 2.0)
            box.lifetime = 23
            bomb._spawn_projectile(box.pos, width=384.0, height=ctx.player_pos.y,
                                   item_type=ITEM_POINT_BULLET)
    bomb.invulnerable = True
    bomb.timer += 1


# ---------------------------------------------------------------------------
# SakuyaA 「幻符·Indiscriminate / Killing Doll」 (规格 §D.4)
# ---------------------------------------------------------------------------

def _calc_sakuya_a_unfocused(bomb: Bomb, ctx: BombContext) -> None:
    """咲夜A 非集中「无差别」 (BombData.cpp:1201-1290)。

    timer 60..120 每帧至多 5 把刀 (共 96): 随机角度/初速 5.5~11.5/加速 0.1~0.2/
    角漂移 ±0.0314, 初始 pos=startPos+24*dir。每帧角漂移+加速; 该刀累计伤害 <30
    时移动并放伤害盒 24×24/lifetime=10 + SpawnBombEffect(32,0,0); 命中后 damage=999
    钉住 (视觉换 anm 1120, 略); 出界 (IsInBounds 64×64) 消失。
    """
    if bomb.timer >= bomb.duration:
        bomb.is_in_use = False
        bomb.events.append(EVENT_END_PLAYER_SPELLCARD)
        return
    if bomb.has_ticked and bomb.timer == 0:
        params = BOMB_PARAMS[(CHAR_SAKUYA_A, False)]
        bomb.duration = params.duration
        bomb.invulnerability_timer = params.invulnerability
        bomb.events.append(EVENT_REMOVE_ALL_ITEMS)
        bomb.start_pos = ctx.player_pos
        for sub in bomb.sub_info:
            sub.state = 0
        bomb.cherry_drain = compute_bomb_cherry_drain(
            cherry=ctx.cherry, cherry_start=ctx.cherry_start,
            difficulty=ctx.difficulty, bomb_duration=params.duration,
            min_cost=params.drain_min_cost, scale=params.drain_scale)
    if bomb.timer >= 60:
        rng = ctx.rng_float or random.random
        spawns_remaining = 5
        for i in range(96):
            sub = bomb.sub_info[i]
            if sub.state == 0:
                if bomb.timer <= 120 and spawns_remaining != 0:
                    sub.state = 1
                    sub.angle = rng() * math.tau - math.pi
                    sub.speed = rng() * 6.0 + 5.5
                    sub.accel = rng() * 0.1 + 0.1
                    sub.angle_drift = rng() * 0.06283186 - 0.03141593
                    sub.vel = Vec2.from_angle(sub.angle, 24.0)
                    sub.pos = bomb.start_pos + sub.vel
                    bomb.damage_boxes[i].damage = 0
                    spawns_remaining -= 1
                continue
            sub.angle = normalize_angle_diff(sub.angle + sub.angle_drift)
            sub.speed += sub.accel
            sub.vel = Vec2.from_angle(sub.angle, sub.speed)
            box = bomb.damage_boxes[i]
            if box.damage < 30:
                sub.pos = sub.pos + sub.vel
                bomb._spawn_clear(sub.pos, radius=32.0, growth=0.0,
                                  lifetime=0, item_type=ITEM_POINT_BULLET)
                box.size = Vec2(24.0, 24.0)
                box.pos = sub.pos
                box.lifetime = 10
            elif box.damage < 999:
                box.damage = 999
            if not _in_bounds(sub.pos.x, sub.pos.y, 64.0, 64.0):
                sub.state = 0
    bomb.invulnerable = True
    bomb.timer += 1


def _calc_sakuya_a_focused(bomb: Bomb, ctx: BombContext) -> None:
    """咲夜A 集中「杀人玩偶」 (BombData.cpp:1333-1473)。

    timer 20..114 每偶数帧放 2 把刀 (i 满足 timer==i%48*2+20, 共 96), 角度
    i*2π/96-π 均布, 初速 24 沿角射出; sub_timer 30..69 停时悬停 (vel=0,
    角漂移 -0.157 —— C++ GetRandomU16InRange(1) 恒为 0 (Rng.hpp:11-14) 恒取
    负支, ZUN quirk), 70 帧瞄 positionOfLastEnemyHit 转 speed=14 飞出。
    伤害盒 24×24/lifetime=22 仅累计伤害==0 时布置 (命中即钉住 damage=999)。
    移速 ×0.3。
    """
    if bomb.timer >= bomb.duration:
        bomb.is_in_use = False
        bomb.move_speed_multiplier = 1.0
        bomb.events.append(EVENT_END_PLAYER_SPELLCARD)
        return
    if bomb.has_ticked and bomb.timer == 0:
        params = BOMB_PARAMS[(CHAR_SAKUYA_A, True)]
        bomb.duration = params.duration
        bomb.invulnerability_timer = params.invulnerability
        bomb.events.append(EVENT_REMOVE_ALL_ITEMS)
        for sub in bomb.sub_info:
            sub.state = 0
        bomb.cherry_drain = compute_bomb_cherry_drain(
            cherry=ctx.cherry, cherry_start=ctx.cherry_start,
            difficulty=ctx.difficulty, bomb_duration=params.duration,
            min_cost=params.drain_min_cost, scale=params.drain_scale)
        bomb.move_speed_multiplier = 0.3
        # 首帧震屏 (BombData.cpp:1388 RegisterChain(1,120,4,1))
        bomb.shakes.append((120, 4, 1))
    if 20 <= bomb.timer < 116:
        rng = ctx.rng_float or random.random
        for i in range(96):
            if not (bomb.has_ticked and bomb.timer == (i % 48) * 2 + 20):
                continue
            sub = bomb.sub_info[i]
            sub.state = 1
            sub.angle = i * math.tau / 96.0 - math.pi
            sub.speed = rng() * 1.0 + 0.5
            sub.accel = rng() * 0.1 + 0.03
            sub.angle_drift = -0.15707964  # GetRandomU16InRange(1)%1==0 → 恒负支
            sub.vel = Vec2.from_angle(sub.angle, 24.0)
            sub.pos = ctx.player_pos + sub.vel
            sub.sub_timer = 0
            bomb.damage_boxes[i].damage = 0
    for i in range(96):
        sub = bomb.sub_info[i]
        if sub.state == 0:
            continue
        t = sub.sub_timer
        if t < 30 or t >= 70:
            if t == 70:
                if ctx.last_enemy_hit is not None and ctx.last_enemy_hit.x > -100.0:
                    sub.angle = normalize_angle_diff(math.atan2(
                        ctx.last_enemy_hit.y - sub.pos.y,
                        ctx.last_enemy_hit.x - sub.pos.x))
                sub.speed = 14.0
            sub.speed += sub.accel
            sub.vel = Vec2.from_angle(sub.angle, sub.speed)
        else:
            sub.angle = normalize_angle_diff(sub.angle + sub.angle_drift)
            sub.vel = Vec2.zero()
        box = bomb.damage_boxes[i]
        if box.damage == 0:
            sub.pos = sub.pos + sub.vel
            bomb._spawn_clear(sub.pos, radius=32.0, growth=0.0,
                              lifetime=0, item_type=ITEM_POINT_BULLET)
            box.size = Vec2(24.0, 24.0)
            box.pos = sub.pos
            box.lifetime = 22
        elif box.damage < 999:
            box.damage = 999
        sub.sub_timer += 1
    bomb.invulnerable = True
    bomb.timer += 1


# ---------------------------------------------------------------------------
# SakuyaB 「时符·Perfect Square / Private Square」 (规格 §D.4)
# ---------------------------------------------------------------------------

def _calc_sakuya_b_unfocused(bomb: Bomb, ctx: BombContext) -> None:
    """咲夜B 非集中「完美方阵」 (BombData.cpp:1502-1598)。

    停时: timer 0/60/120 StopBulletMovement (透出 EVENT_STOP_BULLET_MOVEMENT,
    上层调 BulletManager); 移速 ×2.0。timer>=30 且 %4==0: 全场伤害盒
    352×416 @ (192,224) lifetime=3。结束时 SpawnBombEffect(player, 800, 0, 0)。
    """
    if bomb.timer >= bomb.duration:
        bomb.is_in_use = False
        bomb.move_speed_multiplier = 1.0
        bomb.events.append(EVENT_END_PLAYER_SPELLCARD)
        bomb._spawn_clear(ctx.player_pos, radius=800.0, growth=0.0,
                          lifetime=0, item_type=ITEM_POINT_BULLET)
        return
    if bomb.has_ticked and bomb.timer == 0:
        params = BOMB_PARAMS[(CHAR_SAKUYA_B, False)]
        bomb.duration = params.duration
        bomb.invulnerability_timer = params.invulnerability
        bomb.events.append(EVENT_REMOVE_ALL_ITEMS)
        for i in range(4):
            bomb.sub_info[i].state = 0
        bomb.cherry_drain = compute_bomb_cherry_drain(
            cherry=ctx.cherry, cherry_start=ctx.cherry_start,
            difficulty=ctx.difficulty, bomb_duration=params.duration,
            min_cost=params.drain_min_cost, scale=params.drain_scale)
        bomb.move_speed_multiplier = 2.0
        bomb.events.append(EVENT_STOP_BULLET_MOVEMENT)
    if bomb.has_ticked and bomb.timer == 60:
        bomb.events.append(EVENT_STOP_BULLET_MOVEMENT)
    if bomb.has_ticked and bomb.timer == 120:
        bomb.events.append(EVENT_STOP_BULLET_MOVEMENT)
    # 停时震屏 (BombData.cpp:1559/:1563 RegisterChain(1,60,1,7) / (1,70,24,0))
    if bomb.has_ticked and bomb.timer == 40:
        bomb.shakes.append((60, 1, 7))
    if bomb.has_ticked and bomb.timer == 100:
        bomb.shakes.append((70, 24, 0))
    if bomb.has_ticked and bomb.timer == 30:
        # vm->pos = (192±128, 224±128) 为视觉方阵锚点, 略
        for i in range(4):
            bomb.sub_info[i].state = 1
    if bomb.timer >= 30 and bomb.has_ticked and bomb.timer % 4 == 0:
        box = bomb.damage_boxes[0]
        box.pos = Vec2(192.0, 224.0)
        box.size = Vec2(352.0, 416.0)
        box.lifetime = 3
    bomb.invulnerable = True
    bomb.timer += 1


def _calc_sakuya_b_focused(bomb: Bomb, ctx: BombContext) -> None:
    """咲夜B 集中「私人方阵」 (BombData.cpp:1633-1724)。

    2 个时停领域从玩家位置出发, 每帧以 (playerPos-pos)/1700 加速追踪玩家。
    每帧 (含首帧): SpawnBombEffect(sub0.pos, 96, 0, 0) + 伤害盒 160×160/
    lifetime=1 @ sub0.pos。timer 40/100 StopBulletMovement。移速 ×1.5。
    结束: SpawnBombEffect(player, 800, 0, 0), 且 bombClearBoxes[0] 被覆写为
    (192,224) 宽448×高512 线性段 (ZUN quirk: 与 800 圆同槽, size.y=800 残留)。
    """
    if bomb.timer >= bomb.duration:
        bomb.is_in_use = False
        bomb.move_speed_multiplier = 1.0
        bomb.events.append(EVENT_END_PLAYER_SPELLCARD)
        bomb._spawn_clear(ctx.player_pos, radius=800.0, growth=0.0,
                          lifetime=0, item_type=ITEM_POINT_BULLET)
        # C++ 无条件写 bombClearBoxes[0] (BombData.cpp:1652-1655)
        box0 = bomb.clear_boxes[0]
        box0.pos = Vec2(192.0, 224.0)
        box0.pos_z = 448.0
        box0.size = Vec2(512.0, box0.size.y)
        return
    if bomb.has_ticked and bomb.timer == 0:
        params = BOMB_PARAMS[(CHAR_SAKUYA_B, True)]
        bomb.duration = params.duration
        bomb.invulnerability_timer = params.invulnerability
        bomb.events.append(EVENT_REMOVE_ALL_ITEMS)
        # isBombing=0 (BombData.cpp:1648) —— 触发帧即清 isBombing 标记, 属上层状态, 注记
        for i in range(2):
            sub = bomb.sub_info[i]
            sub.state = 1
            sub.pos = ctx.player_pos
            sub.vel = Vec2.zero()
            sub.accel_vec = Vec2(0.0, -0.008)  # 首帧即被追踪公式覆写, 仅初值意义
        bomb.move_speed_multiplier = 1.5
        bomb.cherry_drain = compute_bomb_cherry_drain(
            cherry=ctx.cherry, cherry_start=ctx.cherry_start,
            difficulty=ctx.difficulty, bomb_duration=params.duration,
            min_cost=params.drain_min_cost, scale=params.drain_scale)
    bomb._spawn_clear(bomb.sub_info[0].pos, radius=96.0, growth=0.0,
                      lifetime=0, item_type=ITEM_POINT_BULLET)
    box = bomb.damage_boxes[0]
    box.pos = bomb.sub_info[0].pos
    box.size = Vec2(160.0, 160.0)
    box.lifetime = 1
    if bomb.has_ticked and bomb.timer == 40:
        bomb.events.append(EVENT_STOP_BULLET_MOVEMENT)
        # 停时震屏 (BombData.cpp:1672 RegisterChain(1,60,1,7))
        bomb.shakes.append((60, 1, 7))
    if bomb.has_ticked and bomb.timer == 100:
        bomb.events.append(EVENT_STOP_BULLET_MOVEMENT)
        # BombData.cpp:1678 RegisterChain(1,70,24,0)
        bomb.shakes.append((70, 24, 0))
    for i in range(2):
        sub = bomb.sub_info[i]
        if sub.state == 0:
            continue
        # 轨迹 trails[32] 为视觉拖尾, 略
        sub.accel_vec = (ctx.player_pos - sub.pos) / 1700.0
        sub.vel = sub.vel + sub.accel_vec
        sub.pos = sub.pos + sub.vel
    bomb.invulnerable = True
    bomb.timer += 1


# (character, focus) → calc (g_BombData, BombData.cpp:16-28)
_BOMB_CALCS = {
    (CHAR_REIMU_A, False): _calc_reimu_a_unfocused,
    (CHAR_REIMU_A, True): _calc_reimu_a_focused,
    (CHAR_REIMU_B, False): _calc_reimu_b_unfocused,
    (CHAR_REIMU_B, True): _calc_reimu_b_focused,
    (CHAR_MARISA_A, False): _calc_marisa_a_unfocused,
    (CHAR_MARISA_A, True): _calc_marisa_a_focused,
    (CHAR_MARISA_B, False): _calc_marisa_b_unfocused,
    (CHAR_MARISA_B, True): _calc_marisa_b_focused,
    (CHAR_SAKUYA_A, False): _calc_sakuya_a_unfocused,
    (CHAR_SAKUYA_A, True): _calc_sakuya_a_focused,
    (CHAR_SAKUYA_B, False): _calc_sakuya_b_unfocused,
    (CHAR_SAKUYA_B, True): _calc_sakuya_b_focused,
}


# ---------------------------------------------------------------------------
# 樱之结界 (规格 §D.5)
# ---------------------------------------------------------------------------

class BorderBreakResult(msgspec.Struct):
    """BreakBorderNaturally 的入账透出 (Player.cpp:2004-2034)。

    上层: 应用 cherry/cherry_max/cherry_plus, add_score(score)(代码值),
    playerState → INVULNERABLE, invulnerability_timer/border_invulnerability_time 生效。
    """

    cherry: int
    cherry_max: int
    cherry_plus: int                  # = cherry_start
    score: int                        # (cherry - cherry_start) * 10 (代码值)
    invulnerability_timer: int = BORDER_BREAK_INVULN
    border_invulnerability_time: int = BORDER_BREAK_INVULN


class Border(msgspec.Struct):
    """樱之结界 (Player.cpp ActivateBorder/UpdateState/BreakBorder*/UpdateBorderAndBombState)。

    上层职责: 满樱(add_cherry_plus 触达 cherryStart+50000)时调 ready_border();
    每帧 READY 时调 activate_border(bombing=...), ACTIVE 时调 tick();
    中弹/按 bomb 键/死亡(has_border != NONE)时调 break_border() 并清 captureScore/
    isCapturing、cherry_plus=cherry_start(按 bomb 键破时另需 RemoveAllItems, Player.cpp:1691)。
    """

    has_border: BorderState = BorderState.NONE
    invulnerability_timer: int = 0
    border_timer: int = 0              # 激活时定格 540, cherryPlus 公式分母
    border_invulnerability_time: int = 0

    def ready_border(self) -> None:
        """满樱信号 → READY (GameManager.cpp:928-931 → ActivateBorder 的延迟路径)。"""
        if self.has_border == BorderState.NONE:
            self.has_border = BorderState.READY

    def activate_border(self, *, bombing: bool = False) -> bool:
        """Player::ActivateBorder (Player.cpp:2087-2144): 非 bomb 时 READY→ACTIVE。

        激活: invulnerability_timer=540, border_timer=540, has_border=ACTIVE
        (上层把 playerState 置 BORDER)。bomb 中保持 READY 返回 False。
        (C++ 另按 playerState 延迟: SPAWNING/INVULNERABLE/DEAD 等, 由上层择时调用。)
        """
        if self.has_border != BorderState.READY or bombing:
            return False
        self.invulnerability_timer = BORDER_DURATION
        self.border_timer = BORDER_DURATION
        self.has_border = BorderState.ACTIVE
        return True

    def tick(self, *, cherry: int, cherry_start: int, cherry_max: int
             ) -> tuple[int, BorderBreakResult | None]:
        """每帧 (UpdateBorderAndBombState 冷却 + UpdateState 的 BORDER 分支)。

        返回 (cherry_plus 显示值, 自然破结果或 None)。
        cherryPlus = cherryStart + invuln*50000/borderTimer (C++ i32 乘除, 先算后减,
        Player.cpp:1952-1959); invuln 归 0 → 自然破(见 break_border_naturally)。
        """
        if self.border_invulnerability_time != 0:
            self.border_invulnerability_time -= 1
        if self.has_border != BorderState.ACTIVE:
            return cherry_start, None
        plus = self.invulnerability_timer * 50000 // self.border_timer
        if plus < 0:
            plus = 0
        cherry_plus = cherry_start + plus
        self.invulnerability_timer -= 1
        if self.invulnerability_timer <= 0:
            result = self.break_border_naturally(
                cherry=cherry, cherry_start=cherry_start, cherry_max=cherry_max)
            return result.cherry_plus, result
        return cherry_plus, None

    def break_border_naturally(self, *, cherry: int, cherry_start: int,
                               cherry_max: int) -> BorderBreakResult:
        """Player::BreakBorderNaturally: 自然破 → +10000 上限/樱点, 得分 (cherry-cherryStart)*10。

        顺序照抄 C++: IncreaseCherryMax(10000) → IncreaseCherry(10000)(封顶 cherryMax)
        → score 用加完的 cherry。has_border=NONE, invuln/border_invuln=40。
        (C++ 另有 SPAWNING 态重生分支, 属上层。)
        """
        cherry_max = min(cherry_max + BORDER_CHERRY_GAIN,
                         cherry_start + CHERRY_MAX_RANGE)
        cherry = min(cherry + BORDER_CHERRY_GAIN, cherry_max)
        score = (cherry - cherry_start) * 10
        self.has_border = BorderState.NONE
        self.invulnerability_timer = BORDER_BREAK_INVULN
        self.border_invulnerability_time = BORDER_BREAK_INVULN
        return BorderBreakResult(cherry=cherry, cherry_max=cherry_max,
                                 cherry_plus=cherry_start, score=score)

    def break_border(self, player_pos: Vec2) -> ClearBox:
        """Player::BreakBorder (Player.cpp:2148-2182): 主动破(bomb键)/中弹破/死亡破。

        has_border=NONE, invuln/border_invuln=40, 全屏清弹圆
        SpawnBombEffect(center, 32, 16, 50, CHERRY_SMALL)(半径 32 每帧 +16 持续 50 帧)。
        上层: 清 captureScore/isCapturing, cherry_plus=cherry_start, playerState→INVULNERABLE。
        """
        self.has_border = BorderState.NONE
        self.invulnerability_timer = BORDER_BREAK_INVULN
        self.border_invulnerability_time = BORDER_BREAK_INVULN
        return ClearBox(pos=player_pos, size=Vec2(0.0, 32.0), lifetime=50,
                        item_type=ITEM_CHERRY_SMALL, growth=16.0)

    @property
    def active(self) -> bool:
        return self.has_border == BorderState.ACTIVE
