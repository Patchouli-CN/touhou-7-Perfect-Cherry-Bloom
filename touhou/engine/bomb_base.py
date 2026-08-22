"""炸弹系统基座 —— 不分作品的炸弹生命周期与盒判定框架。

对照 BombData.cpp / Player.cpp / 规格 §D.2 中作品无关的部分:
- 触发 (try_start_bomb): 消耗一枚 → isInUse, 首帧初始化 duration/invuln,
  透出 subrank/respawn/符卡事件给上层(subrank 是 th07 动态难度概念,
  无此概念的作品忽略 BombStartResult.subrank_delta 即可)。
- 每帧 (BombBase.tick): UpdateBombProjectiles(伤害盒 size.x 清零、清弹盒
  lifetime--/半径增长) → ``_tick_resource_cost`` hook(作品层资源消耗,
  th07 = 樱点 drain) → ``_calc`` stub(子类实现机体炸弹逻辑)。
- DamageBox/ClearBox/BombSubInfo: 伤害盒/清弹盒/子弹状态机容器。

作品专属(th07: 12 套机体 calc 查表分派/樱点消耗/樱之结界 Border)在作品层
(games/th07/bomb.py)。引擎只定契约, 作品来履约。
"""

from __future__ import annotations

import msgspec
from typing import Callable, Generic, TypeVar

from ..utils import Vec2

__all__ = [
    "BOMB_DURATION_PLACEHOLDER",
    "BOMB_RESPAWN_PENALTY",
    "BOMB_SUBRANK_PENALTY",
    "ITEM_POINT_BULLET",
    "BombBase",
    "BombContext",
    "BombStartResult",
    "BombSubInfo",
    "ClearBox",
    "DamageBox",
    "try_start_bomb",
]

# 清弹盒掉落道具类型的通用默认: 弹消点(值 6 与 th07 ItemType.POINT_BULLET
# 同值; 作品层 spawn 时可按自己的道具体系覆盖 item_type)
ITEM_POINT_BULLET = 6

BOMB_SUBRANK_PENALTY = 200    # DecreaseSubrank(200) (Player.cpp:1747)
BOMB_RESPAWN_PENALTY = 6      # respawnTimer += 6, 封顶 initialRespawnTimer (Player.cpp:1750-1754)
BOMB_DURATION_PLACEHOLDER = 999  # 触发时占位 duration (Player.cpp:1736)


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
    """PlayerBombSubInfo 的逻辑部分 (§D.1): 子弹状态机 0=空 1=飞行 2=爆开。

    pos/vel 对应 bombRegionPositions/bombRegionVelocities; accel/accel_vec/
    angle_drift/sub_timer 的复用语义由作品层各机体 calc 定义
    (th07 见 games/th07/bomb.py)。轨迹 trails/AnmVm/Effect 为视觉, 不移植。
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
    """bombCalc 的每帧外部输入的通用子集(作品层子类追加专属字段, th07:
    cherry/cherry_start/last_enemy_hit —— 见 games/th07/bomb.py)。"""

    player_pos: Vec2
    difficulty: int = 1
    rng_float: Callable[[], float] | None = None  # [0,1) 随机, None→作品层回落(如 random.random)


BombCtxT = TypeVar("BombCtxT", bound=BombContext)


class BombStartResult(msgspec.Struct):
    """try_start_bomb 的透出: 上层据此更新 globals/符卡/respawn (Player.cpp:1728-1754)。"""

    started: bool = False
    bombs_used_delta: int = 0              # AddBombsUsed(1)
    bombs_remaining_delta: int = 0         # AddBombsRemaining(-1)
    subrank_delta: int = 0                 # DecreaseSubrank(200)(th07 动态难度; 无此概念可忽略)
    respawn_timer: int = 0                 # min(respawnTimer+6, initialRespawnTimer)
    spellcard_capture_reset: bool = False  # captureScore=0, isCapturing=0
    spellcard_used_bomb: bool = False      # usedBomb = isActive


class BombBase(msgspec.Struct, Generic[BombCtxT]):
    """一次炸弹的生命周期 (PlayerBombInfo + UpdateBorderAndBombState 的 bomb 分支)。

    上层每帧消费 damage_boxes(伤害) / clear_boxes(清弹) /
    invulnerability_timer(首帧设定的无敌) / invulnerable(bomb 期间
    playerState=INVULNERABLE) / move_speed_multiplier(炸弹中移速倍率) / events。

    扩展点:
    - ``_calc(ctx)``: stub, 子类实现机体炸弹逻辑(th07 按 (character, focus)
      查表分派 12 套);
    - ``_tick_resource_cost(in_use)``: 每帧资源消耗 hook(th07 = 樱点 drain),
      基类无消耗;
    - ``_reset_run_state()``: start() 时重置作品专属运行状态的 hook。
    """

    is_in_use: bool = False
    is_focus: bool = False
    duration: int = 0
    timer: int = 0
    has_ticked: bool = False  # ZunTimer: current != previous
    invulnerability_timer: int = 0
    invulnerable: bool = True
    move_speed_multiplier: float = 1.0
    start_pos: Vec2 = msgspec.field(default_factory=Vec2.zero)
    item_type: int = ITEM_POINT_BULLET  # CheckBombGraze 命中后透出的掉落类型
    damage_boxes: list[DamageBox] = msgspec.field(default_factory=list)
    clear_boxes: list[ClearBox] = msgspec.field(default_factory=list)
    sub_info: list[BombSubInfo] = msgspec.field(default_factory=list)
    events: list[str] = msgspec.field(default_factory=list)
    shakes: list[tuple[int, int, int]] = msgspec.field(default_factory=list)  # 震屏事件

    # ---- 触发/结束 ----
    def start(self, *, focus: bool, ctx: BombCtxT) -> None:
        """触发炸弹 (Player.cpp:1732-1744)。duration/invuln 由机体 calc 首帧设定。"""
        self.is_in_use = True
        self.is_focus = focus
        self.duration = BOMB_DURATION_PLACEHOLDER
        self.timer = 0
        self.has_ticked = True  # ZunTimer operator=: previous=-999 → 视为已 tick
        self.invulnerability_timer = 0
        self.invulnerable = True
        self.move_speed_multiplier = 1.0
        self.damage_boxes = [DamageBox(Vec2.zero(), Vec2.zero(), 0) for _ in range(112)]
        self.clear_boxes = []
        self.sub_info = [BombSubInfo() for _ in range(128)]  # C++ subInfo[128] (Player.hpp:116)
        self.events = []
        self.shakes = []
        self._reset_run_state()
        self._calc(ctx)  # 触发当帧立刻调用一次

    def tick(self, ctx: BombCtxT) -> bool:
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
            self._tick_resource_cost(False)
            return False
        self._tick_resource_cost(True)
        self._calc(ctx)
        return self.is_in_use

    def _calc(self, ctx: BombCtxT) -> None:
        """机体炸弹逻辑 stub —— 子类实现(th07 查表分派, 见 games/th07/bomb.py)。"""
        raise NotImplementedError("机体炸弹逻辑由作品层子类实现")

    def _tick_resource_cost(self, in_use: bool) -> None:
        """每帧资源消耗 hook(th07: 樱点 drain; 无消耗的作品留空)。"""

    def _reset_run_state(self) -> None:
        """start() 时重置作品专属运行状态的 hook(th07: 樱点 drain 清零)。"""

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


def try_start_bomb(bomb: BombBase[BombCtxT], ctx: BombCtxT, *, focus: bool,
                   bombs_remaining: float, respawn_timer: int,
                   initial_respawn_timer: int, border_invulnerability_time: int,
                   bomb_pressed: bool, spellcard_active: bool) -> BombStartResult:
    """炸弹触发 (Player::UpdateBorderAndBombState 触发分支, Player.cpp:1719-1755)。

    条件: 非 bomb 中 && respawn_timer != 0 && bombs > 0 && border_invulnerability_time == 0
    && 按下 bomb 键 (对话框/integrity 检查属上层, 不在此)。
    成功时 bomb 已 start(含当帧一次 calc), 事件见 BombStartResult。
    注意: 有结界类系统(th07 has_border != NONE)时按 bomb 键可能是破结界而非
    触发炸弹 (Player.cpp:1686-1692), 该分支由上层先判断。
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
