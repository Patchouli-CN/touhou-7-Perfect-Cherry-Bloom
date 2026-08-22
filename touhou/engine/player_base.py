"""玩家系统基座 —— 不分作品的弹幕 STG 通用玩家框架与判定契约。

引擎只定契约, 作品来履约: 本模块定义引擎层(enemies 命中判定等)需要消费
的玩家最小面与通用框架, 具体玩家实现(games/th07/player.py 的 Player)继承
``PlayerBase`` 并覆盖 hook; 引擎不 import 任何作品包(单向依赖: 引擎 ←—— 作品)。

成员:
- ``PlayerState``: 玩家状态机五态(数值序与 th07 原作 Player.hpp 一致,
  勿改 —— 测试与序列化依赖)。
- ``PlayerEventKind`` / ``PlayerEvent`` / ``DeathSettle`` / ``DeathContext`` /
  ``KillResult``: 玩家透出事件与死亡结算的通用结构(th07 在同包 player.py
  子类化 DeathSettle/DeathContext 追加樱点/subrank 字段)。
- ``PlayerBase``: 状态机 + 移动 + 判定/擦弹 + 死亡重生骨架(plain class)。
  作品层 hook: ``_current_speeds``(移速来源) / ``_tick_options``(子机) /
  ``_tick_shots``(射击) / ``_on_graze``(擦弹结算) / ``_settle_death``(死亡结算)。
- ``PlayerCombatFace``: enemies.contact_hits/shoot_hits 消费的玩家判定面
  (协议, 非快照面; 只读快照面见 touhou.types.PlayerFace, 两者分工不同)。

帧数值(SPAWN_INVULN/SPAWN_TICKS/RESPAWN_INVULN/BULLET_GRACE_PERIOD/
GRAZE_EXPAND)以 th07 Player.cpp 为默认; 作品可按需以模块级常量覆盖语义
(目前仅 th07 一个作品, 需要参数化时再下沉为类属性)。
"""

from __future__ import annotations

import msgspec
from enum import IntEnum
from typing import Generic, Protocol, TypeVar

from .bullets import SCREEN, Bullet
from ..schema.sound import SoundQueue
from ..utils import Vec2

__all__ = [
    "BULLET_GRACE_PERIOD",
    "GRAZE_EXPAND",
    "RESPAWN_INVULN",
    "SPAWN_INVULN",
    "SPAWN_TICKS",
    "DeathContext",
    "DeathSettle",
    "KillResult",
    "PlayerBase",
    "PlayerCombatFace",
    "PlayerEvent",
    "PlayerEventKind",
    "PlayerState",
]

# ---- 状态机/判定关键常量(默认值以 th07 Player.cpp 为准) ----
GRAZE_EXPAND = 20.0          # CheckGraze 弹盒外扩像素
RESPAWN_INVULN = 240         # 重生无敌帧数
SPAWN_INVULN = 120           # 出生 invulnerabilityTimer(AddedCallback)
SPAWN_TICKS = 30             # Respawn 触发阈值(invulnerabilityTimer>=30)
BULLET_GRACE_PERIOD = 60     # 重生后每帧 RemoveAllBullets(0) 的帧数


class PlayerState(IntEnum):
    """玩家状态机(Player.hpp PlayerState, 弹幕 STG 通用)。

    ALIVE/SPAWNING/DEAD/INVULNERABLE 为通用四态; BORDER(结界中)是带结界
    系统的作品(th07)用的扩展态, 无结界的作品不使用即可。
    """

    ALIVE = 0
    SPAWNING = 1
    DEAD = 2
    INVULNERABLE = 3
    BORDER = 4


class PlayerEventKind(IntEnum):
    """step/判定透出的事件(由上层整合接线 globals/items/bullets)。

    DEATH_SETTLE/RESPAWNED/GRAZE/REMOVE_ALL_BULLETS 为通用事件;
    BREAK_BORDER 是带结界系统的作品(th07)用的扩展事件。
    """

    DEATH_SETTLE = 1       # data: DeathSettle(死亡结算: power/掉 P/重撒; th07 另有樱罚/subrank)
    RESPAWNED = 2          # 重生完成(DEAD→INVULNERABLE); 上层扣残机/重置炸弹
    GRAZE = 3              # 擦弹; value=显示分(th07=200, 另见 th07 的 GRAZE_SUBRANK)
    BREAK_BORDER = 4       # BORDER 中弹 → 结界破保命(不死)
    REMOVE_ALL_BULLETS = 5  # bulletGracePeriod 内每帧透出(清弹信号)


class DeathSettle(msgspec.Struct):
    """死亡倒计时归 0 的结算(UpdateDeath)通用字段。数值均已算出, 待上层应用。

    作品层可子类化追加专属结算(th07: cherry_penalty/subrank_delta,
    见 games/th07/player.py)。
    """

    has_lives: bool
    new_power: float
    drop_power_big: int = 0     # 大 P 个数(th07: 有残机 1)
    drop_power_small: int = 0   # 小 P 个数(th07: 有残机 5)
    drop_full_power: int = 0    # FULL_POWER 个数(th07: 无残机 5)
    activate_all_items: bool = False


class DeathContext(msgspec.Struct):
    """死亡结算需要的外部状态快照的通用子集(上层每帧或仅死亡时传入)。

    作品层子类化追加专属输入(th07: cherry/cherry_start/is_sakuya)。
    """

    lives: int = 1


class PlayerEvent(msgspec.Struct):
    kind: PlayerEventKind
    value: int = 0
    data: DeathSettle | None = None


DeathCtxT = TypeVar("DeathCtxT", bound=DeathContext)


class KillResult(IntEnum):
    """CalcKillboxCollision 结果。"""

    NONE = 0
    DEATH = 1          # 命中且 ALIVE → Die()
    BORDER_BREAK = 2   # 命中且 BORDER → 结界破(结界系统作品用)


class PlayerCombatFace(Protocol):
    """enemies 命中判定消费的玩家最小面(contact_hits/shoot_hits 参数型)。

    只声明引擎实际调用的成员: 位置/状态 + 擦弹/体术/自机弹伤害三个判定
    入口。作品层 Player(games/th07/player.py)天然满足, 无需继承。
    """

    pos: Vec2
    state: PlayerState

    def check_graze(self, center: Vec2, size: tuple[float, float]) -> bool: ...

    def check_contact(self, center: Vec2, size: tuple[float, float]) -> bool: ...

    def calc_damage_to_enemy(self, enemy_center: Vec2,
                             enemy_size: tuple[float, float],
                             *, bomb_active: bool | None = None) -> int: ...


class PlayerBase(Generic[DeathCtxT]):
    """自机通用骨架: 状态机 + 移动 + 判定/擦弹 + 死亡重生(不含射击/子机)。

    作品层子类(th07 的 Player)负责: 射击系统(_tick_shots)、子机
    (_tick_options)、擦弹结算(_on_graze)、死亡结算(_settle_death)、
    移速来源(_current_speeds)。hook 基类默认: _tick_options/_tick_shots
    空操作(纯框架不射击), _on_graze 空操作, _current_speeds/_settle_death
    必须覆盖(NotImplementedError)。
    """

    DEATH_SE: int | None = None  # 死亡音效索引(作品 SE 表; th07=4 SOUND_PICHUN)

    def __init__(
        self,
        *,
        hitbox_radius: float,
        graze_radius: float,
        initial_respawn_timer: int,
        pos: Vec2 | None = None,
        bounds: tuple[Vec2, Vec2] | None = None,
    ) -> None:
        self.pos = pos or Vec2(SCREEN.x / 2, SCREEN.y - 64)
        self.bounds = bounds or (Vec2(8, 16), Vec2(SCREEN.x - 8, SCREEN.y - 16))
        # 判定/擦弹半宽(A.3: 半宽 = radius/2; 由作品层从射击数据换算传入)
        self.hitbox_radius = hitbox_radius
        self.graze_radius = graze_radius
        self.initial_respawn_timer = initial_respawn_timer

        self.focus = False
        # 状态机(AddedCallback): SPAWNING + invulnerabilityTimer=120
        self.state = PlayerState.SPAWNING
        self.invulnerability_timer = SPAWN_INVULN
        self.respawn_timer = initial_respawn_timer
        self.bullet_grace_period = 0
        self.events: list[PlayerEvent] = []
        self.frame = 0
        self._move = Vec2.zero()
        self.velocity = Vec2.zero()
        # 发声队列(schema.sound.SoundQueue, 上层注入; None = 静音)
        self.sound: SoundQueue | None = None

    # ---- 向后兼容派生字段(games/th07/world.py 在用) ----
    @property
    def alive(self) -> bool:
        return self.state != PlayerState.DEAD

    @alive.setter
    def alive(self, v: bool) -> None:
        self.state = PlayerState.ALIVE if v else PlayerState.DEAD

    @property
    def invuln(self) -> int:
        return self.invulnerability_timer

    @invuln.setter
    def invuln(self, v: int) -> None:
        self.invulnerability_timer = v

    def take_events(self) -> list[PlayerEvent]:
        """取走并清空当前累计的事件。"""
        ev, self.events = self.events, []
        return ev

    def _play_sound(self, idx: int) -> None:
        """PlaySoundByIdx 透出(上层注入 SoundQueue; 未注入则静音)。"""
        if self.sound is not None:
            self.sound.play(idx)

    # ---- 输入 ----
    def push(self, x: int, y: int, *, focus: bool = False, firing: bool = True) -> None:
        self._move = Vec2(float(x), float(y))
        self.focus = focus
        self._on_push(firing)

    def push_keys(self, *, left=False, right=False, up=False, down=False,
                  focus=False, firing=True) -> None:
        self.push((1 if right else 0) - (1 if left else 0),
                  (1 if down else 0) - (1 if up else 0), focus=focus, firing=firing)

    def _on_push(self, firing: bool) -> None:
        """push 的射击按住状态 hook(th07: self._firing = firing)。"""

    # ---- 每帧(对照 Player::OnUpdate 简化) ----
    def step(self, death_ctx: DeathCtxT | None = None) -> None:
        self.frame += 1
        self.events = []

        # UpdateState: bulletGracePeriod 内每帧清弹
        if self.bullet_grace_period > 0:
            self.bullet_grace_period -= 1
            self.events.append(PlayerEvent(PlayerEventKind.REMOVE_ALL_BULLETS))

        if self.state == PlayerState.DEAD:
            self._update_death(death_ctx)
        elif self.state == PlayerState.SPAWNING:
            # Respawn: invulnerabilityTimer>=30 → INVULNERABLE(240)
            # (AddedCallback 给 120>=30, 出生次帧即转入)
            if self.invulnerability_timer >= SPAWN_TICKS:
                self._enter_invulnerable()
        elif self.state == PlayerState.INVULNERABLE:
            self.invulnerability_timer -= 1
            if self.invulnerability_timer <= 0:
                self.invulnerability_timer = 0
                self.state = PlayerState.ALIVE

        # HandlePlayerInputs: DEAD/SPAWNING 不移动
        if self.state not in (PlayerState.DEAD, PlayerState.SPAWNING):
            self._move_player()
            self._tick_options()

        # OnUpdate 顺序: UpdateShots → UpdateFireBulletTimer(内部 SpawnBullets)
        self._tick_shots()

    # ---- 作品层 hook(射击/子机) ----
    def _tick_options(self) -> None:
        """子机系统 hook(th07: optionState 状态机 + 角度回中)。基类空操作。"""

    def _tick_shots(self) -> None:
        """射击系统 hook(th07: UpdateShots → UpdateFireBulletTimer)。基类空操作。"""

    # ---- 死亡/重生(§A.7: Die/UpdateDeath/Respawn) ----
    def die(self) -> None:
        self.state = PlayerState.DEAD
        self.invulnerability_timer = 0
        self.respawn_timer = self.initial_respawn_timer
        if self.DEATH_SE is not None:
            self._play_sound(self.DEATH_SE)  # (th07: Player.cpp:1238, Die)

    def _update_death(self, ctx: DeathCtxT | None) -> None:
        if self.respawn_timer > 0:
            self.respawn_timer -= 1
            if self.respawn_timer == 0:
                self.events.append(PlayerEvent(
                    PlayerEventKind.DEATH_SETTLE, data=self._settle_death(ctx)))
                self.respawn()
                self.events.append(PlayerEvent(PlayerEventKind.RESPAWNED))

    def _settle_death(self, ctx: DeathCtxT | None) -> DeathSettle:
        """死亡结算 stub —— 子类实现(th07: power 罚/掉 P/樱罚/重撒)。"""
        raise NotImplementedError("死亡结算由作品层子类实现")

    def respawn(self, pos: Vec2 | None = None) -> None:
        """重生: INVULNERABLE + 240 无敌 + 60 帧清弹期(Respawn)。"""
        self.pos = pos or Vec2(SCREEN.x / 2, SCREEN.y - 64)
        self._enter_invulnerable()

    def _enter_invulnerable(self) -> None:
        self.state = PlayerState.INVULNERABLE
        self.invulnerability_timer = RESPAWN_INVULN
        self.respawn_timer = self.initial_respawn_timer
        self.bullet_grace_period = BULLET_GRACE_PERIOD

    # ---- 判定/擦弹(§A.7: CheckGraze/CalcKillboxCollision, AABB) ----
    def check_graze(self, center: Vec2, size: tuple[float, float]) -> bool:
        """擦弹判定: 弹盒 center±size/2 外扩 20px 与擦弹盒(半宽 graze_radius) AABB 相交。
        DEAD/SPAWNING 不擦。命中调 _on_graze hook(th07: 发声+透出 GRAZE 事件)。"""
        if self.state in (PlayerState.DEAD, PlayerState.SPAWNING):
            return False
        hx, hy = size[0] / 2 + GRAZE_EXPAND, size[1] / 2 + GRAZE_EXPAND
        if not _aabb_intersect(center, hx, hy, self.pos, self.graze_radius, self.graze_radius):
            return False
        self._on_graze()
        return True

    def _on_graze(self) -> None:
        """擦弹结算 hook(th07: 音效 + GRAZE 事件/樱点)。基类空操作。"""

    def graze_bullet(self, b: Bullet, size: tuple[float, float]) -> bool:
        """对一颗敌弹做擦弹判定; 每颗弹只擦一次(擦过置 grazed=True)。"""
        if b.grazed:
            return False
        if self.check_graze(b.pos, size):
            b.grazed = True
            return True
        return False

    def check_killbox(self, center: Vec2, size: tuple[float, float]) -> KillResult:
        """命中判定: 弹盒 center±size/2 与判定盒(半宽 hitbox_radius) AABB 相交。"""
        if not _aabb_intersect(center, size[0] / 2, size[1] / 2,
                               self.pos, self.hitbox_radius, self.hitbox_radius):
            return KillResult.NONE
        if self.state == PlayerState.BORDER:
            self.events.append(PlayerEvent(PlayerEventKind.BREAK_BORDER))
            return KillResult.BORDER_BREAK
        if self.state != PlayerState.ALIVE:
            return KillResult.NONE
        self.die()
        return KillResult.DEATH

    def check_contact(self, center: Vec2, size: tuple[float, float]) -> bool:
        """体术命中判定 (Player::CalcKillboxCollision 返回 1 的分支,
        Player.cpp:1014-1039): 盒 center±size/2 与判定盒 AABB 相交即算命中 ——
        BORDER → 结界破事件, ALIVE → die(), 其余状态(无敌/出生/死亡)仅命中
        无玩家侧效果(敌人侧 life-=10 由调用方做, EnemyManager.cpp:589-594)。
        C++ 开头 CheckBombGraze(返回 2) 分支不走这里: 炸弹盒由上层管线处理
        (impl 炸弹中跳过体术判定, 见 tick)。"""
        if not _aabb_intersect(center, size[0] / 2, size[1] / 2,
                               self.pos, self.hitbox_radius, self.hitbox_radius):
            return False
        if self.state == PlayerState.BORDER:
            self.events.append(PlayerEvent(PlayerEventKind.BREAK_BORDER))
            return True
        if self.state == PlayerState.ALIVE:
            self.die()
        return True

    # ---- 旧接口(圆形近似, 现无调用方; 判定走上面的 AABB check_contact) ----
    def is_hit(self, pos: Vec2) -> bool:
        return self.pos.distance(pos) <= self.hitbox_radius

    def grazes(self, pos: Vec2) -> bool:
        return self.pos.distance(pos) <= self.graze_radius

    # ---- 移动(速度由作品层 hook 提供, th07 来自 .sht) ----
    def _move_player(self) -> None:
        straight, diagonal = self._current_speeds()
        mv = self._move
        if mv.x and mv.y:
            v = Vec2(mv.x * diagonal, mv.y * diagonal)
        else:
            v = Vec2(mv.x * straight, mv.y * straight)
        self.velocity = v
        self.pos = self.pos + v
        lo, hi = self.bounds
        self.pos = Vec2(max(lo.x, min(self.pos.x, hi.x)), max(lo.y, min(self.pos.y, hi.y)))

    def _current_speeds(self) -> tuple[float, float]:
        """当前 (直线速度, 斜向速度) hook —— 子类实现(th07 按 focus 查 .sht)。"""
        raise NotImplementedError("移速来源由作品层子类实现")


def _aabb_intersect(c1: Vec2, hx1: float, hy1: float,
                    c2: Vec2, hx2: float, hy2: float) -> bool:
    """两 AABB(中心+半宽) 是否相交(边相接算相交, 同 C++ 的 > 判定)。"""
    return not (c1.x - hx1 > c2.x + hx2 or c1.y - hy1 > c2.y + hy2
                or c1.x + hx1 < c2.x - hx2 or c1.y + hy1 < c2.y - hy2)
