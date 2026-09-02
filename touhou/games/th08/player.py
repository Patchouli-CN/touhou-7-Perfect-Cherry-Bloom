"""玩家(自机, th08 东方永夜抄) —— 基于 th08 .sht(PlayerRawShtFile)的实现。

通用骨架(状态机/移动/判定·擦弹 AABB/死亡重生)在引擎基座
engine/player_base.py(PlayerBase); 本模块是 th08 专属:
- 人妖形态: 双人组(shotType 0-3)低速(focus)=妖形态, 单人人类恒人/单人妖怪
  恒妖(Player::IsYoukai 读 isYoukai 标志, PlayerBomb.cpp:31-34; 形态随
  focusMode 切换, Player.cpp 的 focus 转换段);
- sht 双表按 focus 切换(Player.cpp:35-44 的主/副 sht; SpawnShots
  Player.cpp:3035-3038 按 focusMode 选表);
- 射击: shotTimer 20 帧滚动(UpdateShooting, Player.cpp:3217-3267),
  spawn/update/collision 回调表 g_PlayerShotSpawnCallbacks 等
  (Player.cpp:186-196); 回调语义逐条见 _run_spawn_cb/_run_update_cb 注释;
- 死亡结算: UpdateDeathAndRespawn (Player.cpp:1277-1369): power -16/归 0,
  掉 1 大 P+5 小 P(咲夜/蕾米系 shotType 2/8/9 且有弹时追加 1 B),
  无残机掉 5 个 FULL_POWER; 决死窗(死亡倒计时)= sht deathbombWindowFrames
  (Player.hpp:22-56 @0x8; Die() 的 bombs×6+达标7/符卡×2/灵梦系×9/5 公式
  是后续阶段(单 B)的工作, 本层先用 sht 值)。

纯逻辑模块: 副作用(events/发声)透出给上层(world)接线。
"""

from __future__ import annotations

import math
import msgspec
from typing import Callable, Iterator

from ...engine.bullets import SCREEN
from ...engine.player_base import (  # noqa: F401 (枚举/结构/常量为兼容再导出)
    KillResult,
    PlayerBase,
    PlayerEvent,
    PlayerEventKind,
    PlayerState,
    _aabb_intersect,
)
from ...engine.player_base import DeathContext as _DeathContextBase
from ...engine.player_base import DeathSettle as _DeathSettleBase
from ...schema.shot_data import ShotData, ShotEntry
from ...utils import Vec2

# ---- th08 关键常量(Player.cpp) ----
GRAZE_SCORE_NORMAL = 200  # 擦弹显示分(代码值 AddScore(2000), Player.cpp:483-485 段)
GRAZE_SCORE_MODERATE_YOUKAI = 400  # 中度妖擦弹 4000 (同段 GaugeIsModeratelyYoukai)
GRAZE_GAUGE_YOUKAI = 100  # 妖形态擦弹 gauge+100 (Player.cpp:483-484)
GRAZE_SUBRANK = 6  # 擦弹 subrank+6 (同段 IncreaseSubrank(6))
GRAZE_STAGE_CAP = 9999
GRAZE_TOTAL_CAP = 999999
DEATH_SUBRANK_PENALTY = 1600  # DecreaseSubrank(1600) (Player.cpp:1353)
DEATH_POWER_LOSS = 16  # AddPower(-16) (Player.cpp:1323-1325)
FIRE_CYCLE = 20  # shotTimer 滚动周期 (UpdateShooting, Player.cpp:3246-3248)
BOMB_FIRE_ALLOWED_SHOT_TYPES = (1, 6, 7)  # 炸弹中仍发射的机体
# (UpdateShooting 的 isInUse==0 || (shotType!=1 && !=7 && !=6), Player.cpp:3234-3237)

BULLET_POOL_SIZE = 128  # Player.shots[0x80] (Player.hpp)

# ---- spawn 回调 (g_PlayerShotSpawnCallbacks, Player.cpp:186-189) ----
SPAWN_HOMING = 1  # SpawnHomingShot: 朝 optionHomingTarget 重定向, 速度×1.5
SPAWN_UNLESS_BOMBING_A = 2  # SpawnShotUnlessBombingCallback
SPAWN_UNLESS_BOMBING_B = 3
SPAWN_PERSISTENT = 4  # SpawnPersistentShotCallback(持续弹槽, 见下注)
SPAWN_AIMED_AT_TRACKED = 5  # SpawnShotAimedAtTrackedPoint(×1.5)
SPAWN_ALONG_PLAYER_ANGLE = 6  # SpawnShotAlongPlayerAngle(baseShotAngle 修正)
SPAWN_RANDOMIZED = 7  # SpawnRandomizedShot: 角 ±π/96 抖动
SPAWN_ALONG_OPTION_ANGLE = 8  # SpawnShotAlongOptionAngle(optionStates[2] 朝向)

# ---- update 回调 (g_PlayerShotUpdateCallbacks, Player.cpp:190-191) ----
UPDATE_HOMING = 1  # UpdateHomingShot (Player.cpp:2850 附近): 40 帧转向窗口
UPDATE_FALLING = 3  # UpdateFallingShot: velocity.y -= rand(0.1)+0.27
# 4=UpdatePersistentShot / 5=UpdateShotTrail: 持续激光槽(timeline 槽位耦合,
# 依赖 C 的 PlayerTimeline 机制); 本期按直线弹处理(不消散), 标记 follow-up。

HOMING_STEER_FRAMES = 40

_HISTORY_SENTINEL = Vec2(-999.0, 0.0)


class DeathSettle(_DeathSettleBase):
    """死亡倒计时归 0 的结算(th08: UpdateDeathAndRespawn 决死窗耗尽分支)。"""

    drop_bomb: int = 0  # 咲夜/蕾米系(shotType 2/8/9)有弹时追加 1 个 B 道具
    time_orb_penalty: int = 0  # 时刻符点惩罚(正值, 上层 add_time_orbs(-v))
    subrank_delta: int = -DEATH_SUBRANK_PENALTY


class DeathContext(_DeathContextBase):
    """死亡结算需要的外部状态快照(th08 扩展); lives 在基类。"""

    shot_type: int = 0  # 掉 B 判定用(shotType 2/8/9)
    bombs: int = 0  # 掉 B 判定用(GetBombsRemaining()>0)
    time_orbs: int = 0  # 符点惩罚基数(currentTimeOrbs)


class PlayerBullet(msgspec.Struct):
    """一颗自机弹(th08 PlayerShot 的逻辑部分; 池化, state==0 空位)。"""

    pos: Vec2 = msgspec.field(default_factory=Vec2.zero)
    velocity: Vec2 = msgspec.field(default_factory=Vec2.zero)
    hitbox: tuple[float, float] = (6.0, 6.0)  # 全宽/全高
    speed: float = 0.0
    angle: float = 0.0
    timer: int = 0
    damage: int = 1
    bullet_state: int = 0  # 0=INACTIVE 1=ACTIVE 2=HIT(命中爆炸, 穿透弹续判)
    bullet_state2: int = 0  # = PlayerShotDescriptor.shotType: 3=穿透 4/5=激光型
    entry: ShotEntry | None = None
    update_cb: int = 0
    hit_cb: int = 0
    anm_file_idx: int = 0

    @property
    def dead(self) -> bool:
        return self.bullet_state == 0

    @dead.setter
    def dead(self, v: bool) -> None:
        if v:
            self.bullet_state = 0


class Th08Player(PlayerBase[DeathContext]):
    """自机(th08): 骨架在 PlayerBase; sht 驱动的射击 + 人妖形态 + th08 结算。"""

    DEATH_SE = 4  # SOUND_PICHUN (Player.cpp:518, Die)

    def __init__(
        self,
        *,
        shot_data: ShotData,
        shot_data_focus: ShotData | None = None,
        shot_type: int = 0,
        pos: Vec2 | None = None,
        bounds: tuple[Vec2, Vec2] | None = None,
        power: float = 0.0,
    ) -> None:
        # 判定/擦弹半宽(th08 sht: hurtboxSize/grazeBoxSize 即全宽语义,
        # AABB 判定用半宽 = 值/2, 同 th07 换算)
        super().__init__(
            hitbox_radius=shot_data.hitbox_radius / 2,
            graze_radius=shot_data.grab_item_radius / 2,
            initial_respawn_timer=shot_data.initial_respawn_timer,
            pos=pos,
            bounds=bounds,
        )
        self.shot_data = shot_data
        self.shot_data_focus = shot_data_focus or shot_data
        self.shot_type = shot_type
        self.power = power

        self.fire_time = -1  # shotTimer: -1=未射击, 0..19 滚动
        self._firing = False
        # 子机(简化): 双人组/单人都给 2 个静态僚机位(±24,0);
        # th08 的 optionStates[4] 回调表(UpdateHomingOption 等,
        # Player.cpp:151-179)是后续阶段的工作
        self.options: list[Vec2] = [self.pos - Vec2(24, 0), self.pos + Vec2(24, 0)]

        self.bullet_pool: list[PlayerBullet] = [
            PlayerBullet() for _ in range(BULLET_POOL_SIZE)
        ]

        # ---- 上层设置的外部状态 ----
        self.position_of_last_enemy_hit = Vec2(-999.0, -999.0)  # 追踪弹目标
        # (tailPosition0/1; 索敌由上层 targeting 写回)
        self.bomb_active = False
        self.dialog_active = False
        # 随机数注入点(确定性): rand_float(r) 返回 [0, r)
        self.rand_float: Callable[[float], float] = lambda r: 0.0

    # ---- 人妖形态 ----
    @property
    def is_youkai(self) -> bool:
        """Player::IsYoukai (PlayerBomb.cpp:31-34): 双人组 = focus(低速=妖形态),
        单人妖怪(5/7/9/11)恒妖, 单人人类(4/6/8/10)恒人。"""
        if self.shot_type < 4:
            return self.focus
        return self.shot_type in (5, 7, 9, 11)

    @property
    def shots(self) -> list[PlayerBullet]:
        """存活自机弹视图(观测/渲染用)。"""
        return [b for b in self.bullet_pool if b.bullet_state != 0]

    # ---- 基类 hook 实现 ----
    def _on_push(self, firing: bool) -> None:
        self._firing = firing

    def _current_speeds(self) -> tuple[float, float]:
        """当前 (直线速度, 斜向速度): 按 focus 查 .sht(双人组低速=妖形态表)。"""
        sd = self.shot_data_focus if self.focus else self.shot_data
        # th08 sht 每张表自带 normal/focused 两组速度; 主/副表切换后仍按
        # focus 取表内速度(Player.cpp 的 currentHorizontalSpeed 计算段)
        if self.focus:
            return sd.speed_focus, sd.speed_diagonal_focus
        return sd.speed, sd.speed_diagonal

    def _tick_options(self) -> None:
        """子机位置(简化: 跟随本体 ±24; th08 optionStates 回调表留后续)。"""
        self.options = [self.pos - Vec2(24, 0), self.pos + Vec2(24, 0)]

    def _tick_shots(self) -> None:
        """射击调度: UpdateShots(活弹推进) → UpdateShooting(发弹计时)。"""
        self._update_shots()
        self._update_fire_timer()

    def _on_graze(self) -> None:
        """擦弹结算透出: 音效 + GRAZE 事件(分值按妖率计档位由上层算)。"""
        self._play_sound(30)  # SOUND_GRAZE (Player.cpp:484 段)
        self.events.append(
            PlayerEvent(PlayerEventKind.GRAZE, value=GRAZE_SCORE_NORMAL)
        )

    # ---- 死亡结算(th08: UpdateDeathAndRespawn 决死窗耗尽分支) ----
    def _settle_death(self, ctx: DeathContext | None) -> DeathSettle:
        ctx = ctx or DeathContext()
        # 符点惩罚 (Player.cpp:1313-1316): current>5000 → -500 否则 -current/10
        penalty = 500 if ctx.time_orbs > 5000 else ctx.time_orbs // 10
        if ctx.lives > 0:
            # 有残机: power>16 则 -16 否则归 0; 掉 1 大 P + 5 小 P;
            # 咲夜/蕾米系(2/8/9)且有弹追加 1 B (Player.cpp:1318-1338)
            new_power = 0.0 if int(self.power) <= DEATH_POWER_LOSS else (
                self.power - DEATH_POWER_LOSS
            )
            settle = DeathSettle(
                True,
                new_power,
                drop_power_big=1,
                drop_power_small=5,
                drop_bomb=1
                if (ctx.shot_type in (2, 8, 9) and ctx.bombs > 0)
                else 0,
                time_orb_penalty=penalty,
            )
        else:
            # 无残机: power 归 0, 掉 5 个 FULL_POWER (Player.cpp:1341-1348)
            settle = DeathSettle(False, 0.0, drop_full_power=5, time_orb_penalty=penalty)
        self.power = settle.new_power
        return settle

    # ---- 射击调度(UpdateShooting, Player.cpp:3217-3267) ----
    def _update_fire_timer(self) -> None:
        if (
            self._firing
            and self.fire_time < 0
            and self.state not in (PlayerState.DEAD, PlayerState.SPAWNING)
            and not self.dialog_active  # !g_Gui.IsDialoguePresent() (:3254)
        ):
            self.fire_time = 0
        if self.fire_time < 0:
            return
        # 炸弹中只有魔理沙系(1/6/7)继续发射 (:3234-3237)
        if not self.bomb_active or self.shot_type in BOMB_FIRE_ALLOWED_SHOT_TYPES:
            self._spawn_bullets(self.fire_time)
        self.fire_time += 1
        if self.fire_time >= FIRE_CYCLE or self.state in (
            PlayerState.DEAD,
            PlayerState.SPAWNING,
        ):
            self.fire_time = -1

    def _spawn_bullets(self, value: int) -> None:
        """SpawnShots (Player.cpp:3029-3080): 按 focus 选 sht 表, 按火力选档,
        每个空弹位顺次尝试 entry 链(一弹位一帧至多一条 entry)。"""
        sd = self.shot_data_focus if self.focus else self.shot_data
        level = sd.level_for_power(self.power)
        entries = [e for e in level.entries if not e.is_sentinel]
        ei = 0
        for bullet in self.bullet_pool:
            if ei >= len(entries):
                return
            if bullet.bullet_state != 0:
                continue
            while ei < len(entries):
                entry = entries[ei]
                ei += 1
                if self._fire_entry(entry, bullet, value):
                    bullet.bullet_state = 1
                    bullet.entry = entry
                    bullet.update_cb = entry.update_cb
                    bullet.hit_cb = entry.hit_cb
                    break

    def _fire_entry(self, entry: ShotEntry, bullet: PlayerBullet, value: int) -> bool:
        cb = entry.fire_cb
        if cb in (SPAWN_UNLESS_BOMBING_A, SPAWN_UNLESS_BOMBING_B):
            # SpawnShotUnlessBombingCallback: 炸弹中不发射
            if self.bomb_active:
                return False
        elif cb == SPAWN_HOMING or cb == SPAWN_AIMED_AT_TRACKED:
            return self._fire_homing(entry, bullet, value)
        elif cb == SPAWN_RANDOMIZED:
            return self._fire_randomized(entry, bullet, value)
        elif cb == SPAWN_ALONG_PLAYER_ANGLE:
            # baseShotAngle=-pi/2 修正后与 default 等价
            # (angle = -pi/2 + entry.angle + pi/2, Player.cpp:2714-2742)
            pass
        elif cb == SPAWN_ALONG_OPTION_ANGLE:
            # optionStates[2] 朝向依赖子机系统(后续阶段); 暂按 entry.angle
            # 直飞(标记 follow-up), 炸弹中不发射(Player.cpp:2744-2762)
            if self.bomb_active:
                return False
        elif cb == SPAWN_PERSISTENT:
            # 持续弹槽(UpdatePersistentShot 耦合 C PlayerTimeline):
            # 本期按普通弹发射, 直线飞行(标记 follow-up)
            pass
        # 0(NULL)/6/其他: SpawnShotOnSchedule 语义
        if value % entry.fire_interval != entry.fire_offset:
            return False
        self._init_bullet(entry, bullet)
        return True

    def _init_bullet(self, entry: ShotEntry, bullet: PlayerBullet) -> None:
        """InitializeShot (Player.cpp:2570-2620)。"""
        if entry.option == 0 or entry.option > len(self.options):
            origin = self.pos
        else:
            origin = self.options[entry.option - 1]
        bullet.pos = origin + Vec2(*entry.offset)
        bullet.hitbox = entry.hitbox
        bullet.angle = entry.angle
        bullet.speed = entry.speed
        bullet.velocity = Vec2.from_angle(entry.angle, entry.speed)
        bullet.timer = 0
        bullet.bullet_state2 = entry.bullet_state2
        bullet.damage = entry.damage
        bullet.anm_file_idx = entry.anm_file_idx
        if entry.sound_idx >= 0:
            # 发弹音 (Player.cpp:2604-2609)
            self._play_sound(entry.sound_idx)

    def _fire_homing(self, entry: ShotEntry, bullet: PlayerBullet, value: int) -> bool:
        """SpawnHomingShot/SpawnShotAimedAtTrackedPoint (Player.cpp:2779-2798/
        2696-2726): default 发射后朝目标重定向, 速度×1.5。
        C 的目标是 optionHomingTarget/tailPosition1; 本层用上层写回的
        position_of_last_enemy_hit(索敌最近命中点)。"""
        if value % entry.fire_interval != entry.fire_offset:
            return False
        self._init_bullet(entry, bullet)
        tgt = self.position_of_last_enemy_hit
        if tgt.x > -100.0:
            angle = math.atan2(tgt.y - bullet.pos.y, tgt.x - bullet.pos.x)
            angle += entry.angle + math.pi / 2
            angle = math.atan2(math.sin(angle), math.cos(angle))  # AddNormalizeAngle
            bullet.velocity = Vec2.from_angle(angle, entry.speed * 1.5)
            bullet.angle = angle
        return True

    def _fire_randomized(
        self, entry: ShotEntry, bullet: PlayerBullet, value: int
    ) -> bool:
        """SpawnRandomizedShot (Player.cpp:2764-2778): 角 ±π/96 抖动(-π/2 起)。"""
        if value % entry.fire_interval != entry.fire_offset:
            return False
        self._init_bullet(entry, bullet)
        angle = self.rand_float(math.pi / 48.0) - math.pi / 2.0
        bullet.angle = angle
        bullet.velocity = Vec2.from_angle(angle, entry.speed)
        return True

    # ---- 活弹推进(UpdateShots) ----
    def _update_shots(self) -> None:
        for bullet in self.bullet_pool:
            if bullet.bullet_state == 0:
                continue
            if self.state == PlayerState.DEAD:
                bullet.bullet_state = 0
                continue
            self._run_update_cb(bullet)
            bullet.pos = bullet.pos + bullet.velocity
            if bullet.bullet_state2 not in (4, 5) and _bullet_out_of_bounds(bullet):
                bullet.bullet_state = 0
            bullet.timer += 1

    def _run_update_cb(self, bullet: PlayerBullet) -> None:
        cb = bullet.update_cb
        if cb == UPDATE_HOMING:
            self._update_homing(bullet)
        elif cb == UPDATE_FALLING:
            # UpdateFallingShot (Player.cpp:2850-2858): 上向加速(重力上抛)
            if bullet.bullet_state == 1:
                v = bullet.velocity
                bullet.velocity = Vec2(v.x, v.y - (self.rand_float(0.1) + 0.27))
        # 4/5(持续弹/拖尾激光): 直线飞行(follow-up 标记, 见模块 docstring)

    def _update_homing(self, bullet: PlayerBullet) -> None:
        """UpdateHomingShot (Player.cpp:2799-2848): 40 帧内向目标转向
        (速度 cap 10), 否则沿当前方向加速 +1/3 到 cap 10。"""
        if bullet.bullet_state == 1:
            tgt = self.position_of_last_enemy_hit
            if tgt.x > -100.0 and bullet.timer < HOMING_STEER_FRAMES:
                x = tgt.x - bullet.pos.x
                y = tgt.y - bullet.pos.y
                length = math.hypot(x, y) / (bullet.speed / 4.0)
                if length < 1.0:
                    length = 1.0
                x = x / length + bullet.velocity.x
                y = y / length + bullet.velocity.y
                length = math.hypot(x, y)
                if length != 0.0:  # C 此处产生 nan; 逻辑层保持原速
                    bullet.speed = min(max(length, 1.0), 10.0)
                    bullet.velocity = Vec2(
                        x * bullet.speed / length, y * bullet.speed / length
                    )
            elif bullet.speed < 10.0:
                bullet.speed += 1.0 / 3.0
                vlen = bullet.velocity.length
                if vlen > 0.0:
                    bullet.velocity = bullet.velocity * (bullet.speed / vlen)
        # C 每帧(无论 state)按速度向量回写 angle (Player.cpp:2847)
        bullet.angle = math.atan2(bullet.velocity.y, bullet.velocity.x)

    # ---- 命中敌人(CalcDamageToEnemy, Player.cpp:3283-3360) ----
    def iter_hits(
        self,
        enemy_center: Vec2,
        enemy_size: tuple[float, float],
        *,
        bomb_active: bool | None = None,
    ) -> Iterator[tuple[PlayerBullet, int]]:
        """对一个敌人(center ± size/2)逐发结算本帧伤害, 产出 (bullet, damage)。

        - 只结算活弹; HIT 态(2)仅穿透弹(bs2==3)继续判定 (:3304);
        - bs2 4/5(激光型)只在 timer%2==0 出伤害 (:3312);
        - bomb 中伤害 max(damage//5, 1) (:3325-3327, 注意 th08 是 /5);
        - 命中后弹进 HIT 态, 非穿透弹速度/8 (:3332-3344);
        - collision 回调(1=ApplyShotHitBehavior/2=命中特效)的逻辑侧是
          妖率计/时刻符点联动, 留后续阶段(单 B)。
        """
        bomb = self.bomb_active if bomb_active is None else bomb_active
        ex, ey = enemy_size[0] / 2, enemy_size[1] / 2
        for bullet in self.bullet_pool:
            if bullet.bullet_state == 0:
                continue
            if bullet.bullet_state != 1 and bullet.bullet_state2 != 3:
                continue
            if not _aabb_intersect(
                bullet.pos,
                bullet.hitbox[0] / 2,
                bullet.hitbox[1] / 2,
                enemy_center,
                ex,
                ey,
            ):
                continue
            if bullet.bullet_state2 in (4, 5) and bullet.timer % 2 != 0:
                continue
            dmg = bullet.damage if not bomb else max(bullet.damage // 5, 1)
            if bullet.bullet_state2 not in (4, 5, 6):
                bullet.bullet_state = 2
                if bullet.bullet_state2 != 3:
                    bullet.velocity = bullet.velocity / 8.0
            yield bullet, dmg

    def calc_damage_to_enemy(
        self,
        enemy_center: Vec2,
        enemy_size: tuple[float, float],
        *,
        bomb_active: bool | None = None,
    ) -> int:
        """iter_hits 的求和封装(一帧对一个敌人的总伤害)。"""
        return sum(
            d
            for _, d in self.iter_hits(
                enemy_center, enemy_size, bomb_active=bomb_active
            )
        )


def _bullet_out_of_bounds(b: PlayerBullet) -> bool:
    """弹中心±半宽完全离开 [0,384]x[0,448] 版面(GameManager::IsInBounds)。"""
    hx, hy = b.hitbox[0] / 2, b.hitbox[1] / 2
    return (
        b.pos.x + hx < 0.0
        or b.pos.x - hx > SCREEN.x
        or b.pos.y + hy < 0.0
        or b.pos.y - hy > SCREEN.y
    )
