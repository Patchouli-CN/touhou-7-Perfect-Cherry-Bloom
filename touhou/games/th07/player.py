""" 玩家(自机, th07) —— 基于真实 .sht 射击数据的完整实现。

通用骨架(状态机五态/移动/判定·擦弹 AABB/死亡重生流程/事件结构)已上移到
引擎层基座 engine/player_base.py(PlayerBase/PlayerState/PlayerEventKind/
PlayerEvent/DeathSettle/DeathContext/KillResult); 本模块留 th07 专属:
.sht 驱动的射击系统、子机(§A.4)、樱点擦弹/死亡结算 hook、6 机体常量与回调表。

移动速度、判定/擦弹半径、射击等级、每发弹的角度/速度/伤害/命中盒
全部来自游戏资源解析的 ShotData(§A 规格)。

§A.7 状态机/死亡重生/擦弹判定对照 th07 Player.cpp:
Die / UpdateDeath / Respawn / UpdateState / CheckGraze / ScoreGraze /
CalcKillboxCollision / AddedCallback / HandlePlayerInputs(子机)。

§A.1/A.5/A.6 自机弹系统对照 Player.cpp:
96 弹池 / 3 个 PlayerBulletTimer 持续弹槽 / fire·update·hit 回调分派
(g_ShtFireFuncs/g_ShtUpdateFuncs/g_ShtHitFuncs) / SpawnBullets / UpdateShots /
CalcDamageToEnemy(iter_hits)。

纯逻辑模块: 不依赖 globals/items; 外部值经 DeathContext 传入或作为字段由上层设置
(sakuya_target_position / position_of_last_enemy_hit / bomb_active /
dialog_active / is_marisa_b / rand_float),
副作用(掉 P、樱点惩罚、清弹、擦弹得分等)以 self.events 事件透出;
发弹/死亡/擦弹音效写入上层注入的 self.sound 队列(schema/sound.SoundQueue)。
"""

from __future__ import annotations

import math
import random
import msgspec
from enum import IntEnum
from typing import Callable, Iterator

from ...engine.bullets import SCREEN
from ...engine.player_base import (  # noqa: F401 (枚举/结构/常量为兼容再导出)
    BULLET_GRACE_PERIOD,
    GRAZE_EXPAND,
    RESPAWN_INVULN,
    SPAWN_INVULN,
    SPAWN_TICKS,
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
from ...utils import Vec2, normalize_angle_diff

_DOWN = Vec2(0, 1)

# ---- §A.7 关键常量(Player.cpp; 通用骨架常量在 engine/player_base.py 并再导出) ----
GRAZE_SCORE_DISPLAY = 200    # 擦弹显示分(代码值 AddScore(2000))
GRAZE_SUBRANK = 6            # 擦弹 subrank 增量
GRAZE_STAGE_CAP = 9999
GRAZE_TOTAL_CAP = 999999
DEATH_SUBRANK_PENALTY = 1600
DEATH_POWER_LOSS = 16
CHERRY_PENALTY_CAP = 100000        # 非咲夜
CHERRY_PENALTY_CAP_SAKUYA = 60000
FOCUS_TRANSITION_FRAMES = 8        # 子机 focus/unfocus 过渡帧数
OPTION_FOCUS_ANGLE = 0.22439948    # 咲夜B focus 子机夹角半宽
OPTION_ANGLE_MIN = -2.1991148
OPTION_ANGLE_MAX = -0.9424778
OPTION_ANGLE_CENTER = -1.5707964   # -pi/2
OPTION_ANGLE_RETURN_STEP = 0.06283186
OPTION_ANGLE_RETURN_EPS = 0.03141593

# ---- §A.1/A.2 自机弹回调索引(对照 g_ShtFireFuncs/g_ShtUpdateFuncs/g_ShtHitFuncs) ----
# 注意: 各数组 0 号位是 NULL, 所以具名回调从 1 起(与真实 .sht 数据核对一致)。
FIRE_DEFAULT = 1        # fire_cb 0(NULL) 也回落到这里
FIRE_ORB_UNFOCUSED = 2  # 占 timers[fire_offset] 槽的持续弹(要求 optionState==UNFOCUSED)
FIRE_ORB_FOCUSED = 3    # 同上(要求 FOCUSED, 槽计时 999, trail_length=fire_interval)
FIRE_HOMING = 4         # 咲夜A: default 发射后朝 sakuya_target_position 重定向, 速度×1.5
FIRE_ROTATING_ORB = 5   # 咲夜B: 发射角 = option_angle + entry.angle + pi/2

UPDATE_HOMING = 1          # 40 帧内朝 position_of_last_enemy_hit 转向, 速度 cap 10, 否则 +0.3333
UPDATE_HOMING_FOCUSED = 2  # 同上, cap 18 / +0.6
UPDATE_UPWARD_ACCEL = 3    # velocity.y -= rand(0..0.1) + 0.27
UPDATE_ORB_LASER = 4       # 弹跟随子机 options[option_id-1]+offset, hitbox 高=pos.y, pos.y/=2
UPDATE_PLAYER_LASER = 5    # 弹跟随本体, pos_history 每帧右移, trail_length=fire_interval

HIT_MISSILE = 1     # 魔理沙A 导弹: 首中爆炸变形(大判定盒+随机上向速度), 之后隔帧伤害
HIT_PARTICLES = 2   # 视觉粒子(SpawnHitParticles), 逻辑上空壳

BULLET_POOL_SIZE = 96      # Player.bullets[96]
LASER_HISTORY = 16         # posHistory[16]
HOMING_STEER_FRAMES = 40   # homing 转向窗口(timer < 40)
PERSIST_DIALOG_BOMB_CAP = 20   # 对话框/炸弹中持续弹剩余计时压到 20
PERSIST_RELEASE_CAP = 50       # 松开射击(fireBulletTimer<0)时槽计时压到 50

# OnMissileHit: anmFileIdx → (爆炸后判定盒全宽/全高, 爆炸速度)
MISSILE_BLAST = {
    1089: (32.0, 4.0), 1090: (42.0, 4.0), 1091: (48.0, 4.0), 1092: (56.0, 4.0),
    1093: (48.0, 6.0), 1094: (64.0, 6.0), 1095: (80.0, 6.0), 1096: (96.0, 6.0),
}


# PlayerState/PlayerEventKind/PlayerEvent/KillResult/DeathSettle/DeathContext
# 已提升到引擎层(touhou/engine/player_base.py, 弹幕 STG 通用骨架); 本模块
# 从引擎 import 并再导出, 保持 ``games.th07.player.*`` 引用兼容(测试与
# world.py 仍经本模块取)。DeathSettle/DeathContext 在此子类化追加 th07 专属
# 字段(樱点惩罚/subrank/咲夜判定)。

class OptionState(IntEnum):
    """子机状态(Player.hpp OptionState)。"""

    HIDDEN = 0
    UNFOCUSED = 1
    FOCUSING = 2
    FOCUSED = 3
    UNFOCUSING = 4


class DeathSettle(_DeathSettleBase):
    """死亡倒计时归 0 的结算(UpdateDeath, th07 扩展: 樱罚/subrank)。
    通用字段(has_lives/new_power/掉 P/重撒)在基类。"""

    cherry_penalty: int = 0     # 已 cap + 向下取整 10
    subrank_delta: int = -DEATH_SUBRANK_PENALTY


class DeathContext(_DeathContextBase):
    """死亡结算需要的外部状态快照(th07 扩展: 樱点/咲夜); lives 在基类。"""

    cherry: int = 0
    cherry_start: int = 0
    is_sakuya: bool = False


_HISTORY_SENTINEL = Vec2(-999.0, 0.0)


class PlayerBullet(msgspec.Struct):
    """一颗自机弹(§A.1 PlayerBullet)。池化复用, bullet_state==0 为空位。

    hitbox 为全宽/全高(判定按 pos ± hitbox/2)。回调字段存 .sht 里的索引,
    分派见 Player._run_update_cb / Player._run_hit_cb。
    """

    pos: Vec2 = msgspec.field(default_factory=Vec2.zero)
    velocity: Vec2 = msgspec.field(default_factory=Vec2.zero)
    offset: Vec2 = msgspec.field(default_factory=Vec2.zero)   # orb/laser 相对发射点偏移
    pos_history: list[Vec2] = msgspec.field(
        default_factory=lambda: [_HISTORY_SENTINEL] * LASER_HISTORY)
    hitbox: tuple[float, float] = (6.0, 6.0)          # 全宽/全高(C++ hitboxSize.x/y)
    speed: float = 0.0
    angle: float = 0.0
    timer: int = 0                 # 存活帧数(ZunTimer)
    damage: int = 1
    bullet_state: int = 0          # 0=死 1=活 2=命中爆炸(仍可能继续判定, 见 iter_hits)
    bullet_state2: int = 0         # 3=穿透(命中不减速) 4/5=激光型(奇偶帧减半伤害)
    timer_idx: int = 0             # 占用的 timers 槽(0/1/2)
    option_id: int = 0             # 发射该弹的 option(0=本体 1/2=子机)
    trail_length: int = 0          # 拖尾长度(playerLaser 的 fire_interval)
    anm_file_idx: int = 0          # missile 爆炸变形查 MISSILE_BLAST 用
    update_cb: int = 0
    draw_cb: int = 0
    hit_cb: int = 0
    entry: ShotEntry | None = None  # 发射它的 ShtEntry

    @property
    def dead(self) -> bool:
        """兼容接口: dead=True 等价于放回弹池(bullet_state=0)。"""
        return self.bullet_state == 0

    @dead.setter
    def dead(self, v: bool) -> None:
        if v:
            self.bullet_state = 0


class PlayerBulletTimer(msgspec.Struct):
    """持续弹槽(§A.1 PlayerBulletTimer): 每槽一个 orb/laser 持续弹。"""

    timer: int = 0
    bullet: PlayerBullet | None = None


class Player(PlayerBase[DeathContext]):
    """自机(th07): 状态机/移动/判定/死亡重生骨架在引擎基类 PlayerBase;
    本类实现按 .sht 射击调度、子机系统与 th07 专属结算 hook。"""

    # 射击总周期(fireTime 0..总周期-1), 见规格 A.5(以 30 帧滚动)
    FIRE_CYCLE = 30

    DEATH_SE = 4  # SOUND_PICHUN (Player.cpp:1238, Die)

    def __init__(
        self,
        *,
        shot_data: ShotData,
        shot_data_focus: ShotData | None = None,
        pos: Vec2 | None = None,
        bounds: tuple[Vec2, Vec2] | None = None,
        power: float = 0.0,
        rotating_options: bool = False,
    ) -> None:
        # 判定/擦弹半宽(来自 .sht, A.3: 半宽 = radius/2)
        super().__init__(
            hitbox_radius=shot_data.hitbox_radius / 2,
            graze_radius=shot_data.grab_item_radius / 2,
            initial_respawn_timer=shot_data.initial_respawn_timer,
            pos=pos, bounds=bounds)
        self.shot_data = shot_data
        self.shot_data_focus = shot_data_focus or shot_data
        self.power = power
        # 咲夜B: 子机绕 optionAngle 旋转(§A.4)
        self.rotating_options = rotating_options

        self.fire_time = -1       # fireBulletTimer: -1=未射击, 0..29 滚动
        self._firing = False      # 是否按住射击
        self._fire_active = False
        # 子机(A.4): optionState 状态机 + focusMovementTimer 8 帧过渡
        self.option_state = OptionState.UNFOCUSED
        self.focus_movement_timer = 0
        self.option_angle = OPTION_ANGLE_CENTER
        self.options: list[Vec2] = [self.pos - Vec2(24, 0), self.pos + Vec2(24, 0)]

        # ---- §A.1 自机弹池 / 持续弹槽 ----
        self.bullet_pool: list[PlayerBullet] = [
            PlayerBullet() for _ in range(BULLET_POOL_SIZE)]
        self.timers: list[PlayerBulletTimer] = [PlayerBulletTimer() for _ in range(3)]
        # 各槽当前生效的 ShtEntry(C++ shtEntries[4], 换 entry 时中断旧持续弹)
        self.sht_entries: list[ShotEntry | None] = [None, None, None, None]

        # ---- 上层设置的外部状态(纯逻辑, 不 import globals/enemies) ----
        self.sakuya_target_position = Vec2(-999.0, -999.0)   # 咲夜索敌目标
        self.position_of_last_enemy_hit = Vec2(-999.0, -999.0)  # homing 更新目标
        self.bomb_active = False     # 炸弹使用中(持续弹压计时/伤害 /3)
        self.dialog_active = False   # 对话框中(持续弹压计时)
        self.is_marisa_b = False     # MarisaB: 炸弹中不发射(UpdateFireBulletTimer)
        # 随机数注入点: rand_float(r) 返回 [0, r) 的浮点
        self.rand_float: Callable[[float], float] = lambda r: random.random() * r

    @property
    def shots(self) -> list[PlayerBullet]:
        """存活自机弹视图(world 离屏渲染与 view 渲染迭代; 写 dead=True 消弹)。"""
        return [b for b in self.bullet_pool if b.bullet_state != 0]

    # ---- 基类 hook 实现 ----
    def _on_push(self, firing: bool) -> None:
        """push 的射击按住状态。"""
        self._firing = firing

    def _tick_options(self) -> None:
        """子机系统(§A.4): optionState 状态机 + 角度回中。"""
        self._update_options()
        self._update_option_angle()

    def _tick_shots(self) -> None:
        """射击调度: OnUpdate 顺序 UpdateShots → UpdateFireBulletTimer(内部 SpawnBullets)。"""
        self._update_shots()
        self._update_fire_timer()

    def _current_speeds(self) -> tuple[float, float]:
        """当前 (直线速度, 斜向速度): 按 focus 查 .sht。"""
        sd = self.shot_data
        if self.focus:
            return sd.speed_focus, sd.speed_diagonal_focus
        return sd.speed, sd.speed_diagonal

    def _on_graze(self) -> None:
        """擦弹结算: 音效 + GRAZE 事件(显示分 200, subrank+6 由上层接)。"""
        self._play_sound(30)  # SOUND_GRAZE (Player.cpp:1210, ScoreGraze)
        self.events.append(PlayerEvent(PlayerEventKind.GRAZE, value=GRAZE_SCORE_DISPLAY))

    # ---- 死亡结算(th07: power 罚/掉 P/樱罚/重撒; 骨架在基类) ----
    def _settle_death(self, ctx: DeathContext | None) -> DeathSettle:
        ctx = ctx or DeathContext()
        if ctx.lives > 0:
            # 有残机: power>16 则 -16 否则归 0; 掉 1 大 P + 5 小 P
            if int(self.power) <= DEATH_POWER_LOSS:
                new_power = 0.0
            else:
                new_power = self.power - DEATH_POWER_LOSS
            penalty = int((ctx.cherry - ctx.cherry_start)
                          * self.shot_data.cherry_penalty_multiplier)
            cap = CHERRY_PENALTY_CAP_SAKUYA if ctx.is_sakuya else CHERRY_PENALTY_CAP
            if penalty > cap:
                penalty = cap
            penalty -= int(math.fmod(penalty, 10))  # C++ i32 % 10(向零截断)
            settle = DeathSettle(True, new_power, drop_power_big=1, drop_power_small=5,
                                 cherry_penalty=penalty, activate_all_items=True)
        else:
            # 无残机: power 归 0, 掉 5 个 FULL_POWER
            settle = DeathSettle(False, 0.0, drop_full_power=5)
        self.power = settle.new_power
        return settle

    # ---- 子机(§A.4, HandlePlayerInputs 的 optionState 状态机) ----
    def _update_options(self) -> None:
        if self.rotating_options:
            self._update_options_rotating()
        else:
            self._update_options_plain()

    def _update_options_plain(self) -> None:
        """非咲夜B: (±24,0) ↔ (±8,-32), focusMovementTimer 0..8 过渡。
        注意 C++ 中 y 随 t 线性, x 随 t*t (二次)。"""
        st = self.option_state
        if st == OptionState.HIDDEN:
            self.focus_movement_timer = 0
            return
        ox = oy = 0.0
        if st == OptionState.UNFOCUSED:
            ox = 24.0
            self.focus_movement_timer = 0
            if self.focus:
                self.option_state = OptionState.FOCUSING
        if self.option_state == OptionState.FOCUSING:
            self.focus_movement_timer += 1
            t = self.focus_movement_timer / 8.0
            oy = -32.0 + (1.0 - t) * 32.0
            ox = -16.0 * t * t + 24.0
            if self.focus_movement_timer >= FOCUS_TRANSITION_FRAMES:
                self.option_state = OptionState.FOCUSED
            elif not self.focus:
                self.option_state = OptionState.UNFOCUSING
                self.focus_movement_timer = 8 - self.focus_movement_timer
        elif self.option_state == OptionState.FOCUSED:
            ox, oy = 8.0, -32.0
            self.focus_movement_timer = 0
            if not self.focus:
                self.option_state = OptionState.UNFOCUSING
        elif self.option_state == OptionState.UNFOCUSING:
            self.focus_movement_timer += 1
            t = self.focus_movement_timer / 8.0
            oy = -32.0 + 32.0 * t
            ox = -16.0 * (1.0 - t * t) + 24.0
            if self.focus_movement_timer >= FOCUS_TRANSITION_FRAMES:
                self.option_state = OptionState.UNFOCUSED
            elif self.focus:
                self.option_state = OptionState.FOCUSING
                self.focus_movement_timer = 8 - self.focus_movement_timer
        self.options = [self.pos + Vec2(-ox, oy), self.pos + Vec2(ox, oy)]

    def _update_options_rotating(self) -> None:
        """咲夜B: 两子机绕 optionAngle 旋转半径 24; focus 收窄到 optionAngle±0.2244。"""
        st = self.option_state
        if st == OptionState.HIDDEN:
            self.focus_movement_timer = 0
            return
        base = Vec2.from_angle(self.option_angle + math.pi / 2, 24.0)
        tgt1 = Vec2.from_angle(self.option_angle + OPTION_FOCUS_ANGLE, 24.0)
        tgt0 = Vec2.from_angle(self.option_angle - OPTION_FOCUS_ANGLE, 24.0)
        if st == OptionState.UNFOCUSED:
            self.focus_movement_timer = 0
            if self.focus:
                self.option_state = OptionState.FOCUSING
            else:
                self.options = [self.pos - base, self.pos + base]
                return
        if self.option_state == OptionState.FOCUSING:
            if not self.focus:
                self.option_state = OptionState.UNFOCUSING
                self.focus_movement_timer = 8 - self.focus_movement_timer
            else:
                self.focus_movement_timer += 1
                t = self.focus_movement_timer / 8.0
                if self.focus_movement_timer >= FOCUS_TRANSITION_FRAMES:
                    self.option_state = OptionState.FOCUSED
                self.options = [self.pos + (-base).lerp(tgt0, t),
                                self.pos + base.lerp(tgt1, t)]
                return
        if self.option_state == OptionState.FOCUSED:
            self.focus_movement_timer = 0
            if not self.focus:
                self.option_state = OptionState.UNFOCUSING
            else:
                self.options = [self.pos + tgt0, self.pos + tgt1]
                return
        if self.option_state == OptionState.UNFOCUSING:
            if self.focus:
                self.option_state = OptionState.FOCUSING
                self.focus_movement_timer = 8 - self.focus_movement_timer
            else:
                self.focus_movement_timer += 1
                t = 1.0 - self.focus_movement_timer / 8.0
                if self.focus_movement_timer >= FOCUS_TRANSITION_FRAMES:
                    self.option_state = OptionState.UNFOCUSED
                self.options = [self.pos + (-base).lerp(tgt0, t),
                                self.pos + base.lerp(tgt1, t)]

    def _update_option_angle(self) -> None:
        """optionAngle 随横向速度摆动/回中(C++ 仅射击且非 focus 时更新)。"""
        if not self.rotating_options or not self._firing or self.focus:
            return
        vx = self.velocity.x
        if vx != 0.0:
            self.option_angle += (vx / 4.0) * math.pi / 5.0 / 10.0
            if self.option_angle < OPTION_ANGLE_MIN:
                self.option_angle = OPTION_ANGLE_MIN
            elif self.option_angle > OPTION_ANGLE_MAX:
                self.option_angle = OPTION_ANGLE_MAX
        elif abs(self.option_angle - OPTION_ANGLE_CENTER) > OPTION_ANGLE_RETURN_EPS:
            step = OPTION_ANGLE_RETURN_STEP
            if self.option_angle > OPTION_ANGLE_CENTER:
                step = -step
            self.option_angle += step
        else:
            self.option_angle = OPTION_ANGLE_CENTER

    # ---- 射击调度(UpdateFireBulletTimer / SpawnBullets, §A.5) ----
    def _update_fire_timer(self) -> None:
        """fireBulletTimer: 按住射击从 -1 置 0 启动, 每帧推进, 30 帧滚动;
        到 30 或 DEAD/SPAWNING 归 -1(下次按下从 0 重启)。MarisaB 炸弹中不发射。"""
        if self._firing and self.fire_time < 0 \
                and self.state not in (PlayerState.DEAD, PlayerState.SPAWNING):
            self.fire_time = 0    # StartFireBulletTimer(HandlePlayerInputs, 死亡/出生时不调)
        if self.fire_time < 0:
            return
        if not (self.bomb_active and self.is_marisa_b):
            self._spawn_bullets()
        self.fire_time += 1
        if (self.fire_time >= self.FIRE_CYCLE
                or self.state in (PlayerState.DEAD, PlayerState.SPAWNING)):
            self.fire_time = -1

    def _spawn_bullets(self) -> None:
        """SpawnBullets: 每个空弹位顺次尝试 entry 链, 一个弹位一帧至多发射一条 entry。"""
        sd = self.shot_data_focus if self.focus else self.shot_data
        level = sd.level_for_power(self.power)
        entries = [e for e in level.entries if not e.is_sentinel]
        ei = 0
        for bullet in self.bullet_pool:      # 找第一个 bullet_state==0 的空位
            if ei >= len(entries):
                return
            if bullet.bullet_state != 0:
                continue
            while ei < len(entries):
                entry = entries[ei]
                ei += 1
                if self._fire_entry(entry, bullet):
                    bullet.bullet_state = 1
                    bullet.entry = entry
                    bullet.update_cb = entry.update_cb
                    bullet.draw_cb = entry.draw_cb
                    bullet.hit_cb = entry.hit_cb
                    break

    # ---- fire 回调(g_ShtFireFuncs) ----
    def _fire_entry(self, entry: ShotEntry, bullet: PlayerBullet) -> bool:
        cb = entry.fire_cb
        if cb == FIRE_ORB_UNFOCUSED:
            return self._fire_orb(entry, bullet, focused=False)
        if cb == FIRE_ORB_FOCUSED:
            return self._fire_orb(entry, bullet, focused=True)
        if cb == FIRE_HOMING:
            return self._fire_homing(entry, bullet)
        if cb == FIRE_ROTATING_ORB:
            return self._fire_rotating_orb(entry, bullet)
        # 0(NULL)/1/未知: FireBulletDefault
        if self.fire_time % entry.fire_interval != entry.fire_offset:
            return False
        self._init_bullet(entry, bullet)
        return True

    def _init_bullet(self, entry: ShotEntry, bullet: PlayerBullet) -> None:
        """DefaultFireBulletCallback: 从 entry 初始化弹(不含回调专属字段)。"""
        origin = self.pos if entry.option == 0 else self.options[entry.option - 1]
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
            # 自机弹发弹音 (Player.cpp:116-119, SpawnBullets 的 shtEntry->soundIdx)
            self._play_sound(entry.sound_idx)

    def _fire_orb(self, entry: ShotEntry, bullet: PlayerBullet, *, focused: bool) -> bool:
        """FireOrbBulletUnfocused/Focused: 占 timers[fire_offset] 槽的持续弹。
        槽被占且 entry 变了 → 中断旧弹本帧不发射; 要求 optionState 匹配。"""
        slot = entry.fire_offset
        if slot >= len(self.timers):
            return False
        ts = self.timers[slot]
        if ts.bullet is not None:
            if self.sht_entries[slot] is not entry:
                # C++ 置 vm.pendingInterrupt(动画退出消弹), 逻辑层直接消弹
                ts.bullet.bullet_state = 0
                ts.bullet = None
            return False
        need = OptionState.FOCUSED if focused else OptionState.UNFOCUSED
        if self.option_state != need:
            return False
        ts.timer = 999 if focused else entry.fire_interval
        ts.bullet = bullet
        bullet.timer_idx = slot
        bullet.option_id = entry.option
        bullet.offset = Vec2(*entry.offset)
        self._init_bullet(entry, bullet)
        if focused:
            bullet.trail_length = entry.fire_interval
            bullet.pos_history = [_HISTORY_SENTINEL] * LASER_HISTORY
            bullet.pos = Vec2(-999.0, bullet.pos.y)
        self.sht_entries[slot] = entry
        return True

    def _fire_homing(self, entry: ShotEntry, bullet: PlayerBullet) -> bool:
        """FireHomingBullet(咲夜A): default 发射后, 若索敌目标有效则朝目标重定向,
        速度×1.5(bullet.speed 字段保持 entry.speed, 与 C++ 一致)。"""
        if self.fire_time % entry.fire_interval != entry.fire_offset:
            return False
        self._init_bullet(entry, bullet)
        tgt = self.sakuya_target_position
        if tgt.x > -100.0:
            angle = normalize_angle_diff(
                math.atan2(tgt.y - bullet.pos.y, tgt.x - bullet.pos.x)
                + entry.angle + math.pi / 2)
            bullet.velocity = Vec2.from_angle(angle, entry.speed * 1.5)
            bullet.angle = angle
        return True

    def _fire_rotating_orb(self, entry: ShotEntry, bullet: PlayerBullet) -> bool:
        """FireRotatingOrbBullet(咲夜B): 发射角 = option_angle + entry.angle + pi/2。"""
        if self.fire_time % entry.fire_interval != entry.fire_offset:
            return False
        self._init_bullet(entry, bullet)
        angle = normalize_angle_diff(self.option_angle + entry.angle + math.pi / 2)
        bullet.velocity = Vec2.from_angle(angle, entry.speed)
        bullet.angle = angle
        return True

    # ---- UpdateShots(§A.5): 持续弹槽清理 + 活弹逐帧更新 ----
    def _update_shots(self) -> None:
        # 槽位状态清理: focus 弹(槽2)在非 FOCUSED 时立即消;
        # 非 focus 弹(槽0/1)在非 UNFOCUSED 时中断(C++ 为 pendingInterrupt, 逻辑层直接消)
        if self.option_state != OptionState.FOCUSED and self.timers[2].bullet is not None:
            self.timers[2].bullet.bullet_state = 0
            self.timers[2].bullet = None
        if self.option_state != OptionState.UNFOCUSED:
            for i in (0, 1):
                b = self.timers[i].bullet
                if b is not None:
                    b.bullet_state = 0
                    self.timers[i].bullet = None
        if self.state == PlayerState.DEAD:
            for ts in self.timers:
                if ts.bullet is not None:
                    ts.bullet.bullet_state = 0
                    ts.bullet = None
        # 槽计时: 0<timer<999 每帧递减; 未射击时压到 50; 归零脱钩(弹体由 update 回调收尾)
        for ts in self.timers:
            if ts.bullet is None:
                continue
            if 0 < ts.timer < 999:
                ts.timer -= 1
            if self.fire_time < 0 and ts.timer > PERSIST_RELEASE_CAP:
                ts.timer = PERSIST_RELEASE_CAP
            if ts.timer == 0:
                ts.bullet = None
        for bullet in self.bullet_pool:
            if bullet.bullet_state == 0:
                continue
            if self._run_update_cb(bullet):
                bullet.bullet_state = 0
                continue
            bullet.pos = bullet.pos + bullet.velocity
            if bullet.bullet_state2 not in (4, 5) and _bullet_out_of_bounds(bullet):
                bullet.bullet_state = 0
            bullet.timer += 1

    # ---- update 回调(g_ShtUpdateFuncs), 返回 1 → 弹置 0 ----
    def _run_update_cb(self, bullet: PlayerBullet) -> int:
        cb = bullet.update_cb
        if cb == UPDATE_HOMING:
            return self._update_homing(bullet, cap=10.0, accel=0.33333334)
        if cb == UPDATE_HOMING_FOCUSED:
            return self._update_homing(bullet, cap=18.0, accel=0.6)
        if cb == UPDATE_UPWARD_ACCEL:
            if bullet.bullet_state == 1:
                v = bullet.velocity
                bullet.velocity = Vec2(v.x, v.y - (self.rand_float(0.1) + 0.27))
            return 0
        if cb == UPDATE_ORB_LASER:
            return self._update_orb_laser(bullet)
        if cb == UPDATE_PLAYER_LASER:
            return self._update_player_laser(bullet)
        return 0

    def _update_homing(self, bullet: PlayerBullet, *, cap: float, accel: float) -> int:
        """UpdateHomingBullet(Focused): 40 帧内有目标则每帧重算方向转向(速度 cap),
        否则沿当前方向加速到 cap。目标 = position_of_last_enemy_hit(上层设置)。"""
        if bullet.bullet_state != 1:
            return 0
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
            if length == 0.0:
                return 0    # C++ 此处产生 nan; 逻辑层直接保持原速
            bullet.speed = min(max(length, 1.0), cap)
            bullet.velocity = Vec2(x * bullet.speed / length, y * bullet.speed / length)
        elif bullet.speed < cap:
            bullet.speed += accel
            vlen = bullet.velocity.length
            if vlen > 0.0:
                bullet.velocity = bullet.velocity * (bullet.speed / vlen)
        return 0

    def _update_orb_laser(self, bullet: PlayerBullet) -> int:
        """UpdateOrbLaser: 弹跟随子机 options[option_id-1]+offset(仅 x);
        hitbox 高 = pos.y(从子机到版顶), pos.y /= 2; 槽计时归 0 结束。"""
        ts = self.timers[bullet.timer_idx]
        if (self.dialog_active or self.bomb_active) and ts.timer > PERSIST_DIALOG_BOMB_CAP:
            ts.timer = PERSIST_DIALOG_BOMB_CAP
        if ts.timer <= 0:
            ts.timer = 0
            ts.bullet = None
            bullet.bullet_state = 0
            return 1
        opt = self.options[bullet.option_id - 1]
        bullet.pos = Vec2(opt.x + bullet.offset.x, opt.y)
        if self.state == PlayerState.DEAD:
            return 1
        bullet.hitbox = (bullet.hitbox[0], bullet.pos.y)
        bullet.pos = Vec2(bullet.pos.x, bullet.pos.y / 2)
        return 0

    def _update_player_laser(self, bullet: PlayerBullet) -> int:
        """UpdatePlayerLaser: 弹跟随本体; pos_history 每帧右移(历史段伤害见 iter_hits);
        hitbox 高 = 本体 y+64, pos.y = y/2-32; 槽计时归 0 结束。"""
        ts = self.timers[bullet.timer_idx]
        if (self.dialog_active or self.bomb_active) and ts.timer > PERSIST_DIALOG_BOMB_CAP:
            ts.timer = PERSIST_DIALOG_BOMB_CAP
        if ts.timer <= 0:
            ts.timer = 0
            ts.bullet = None
            bullet.bullet_state = 0
            return 1
        for i in range(LASER_HISTORY - 1, 0, -1):
            bullet.pos_history[i] = bullet.pos_history[i - 1]
        bullet.pos_history[0] = bullet.pos
        if self.state == PlayerState.DEAD:
            return 1
        bullet.pos = Vec2(self.pos.x + bullet.offset.x, self.pos.y)
        bullet.hitbox = (bullet.hitbox[0], self.pos.y + 64.0)
        bullet.pos = Vec2(bullet.pos.x, bullet.pos.y / 2 - 32.0)
        return 0

    # ---- hit 回调(g_ShtHitFuncs), 返回 1 → 跳过当帧伤害 ----
    def _run_hit_cb(self, bullet: PlayerBullet) -> int:
        cb = bullet.hit_cb
        if cb == HIT_MISSILE:
            return self._on_missile_hit(bullet)
        # HIT_PARTICLES 等: 纯视觉, 逻辑空壳
        return 0

    def _on_missile_hit(self, bullet: PlayerBullet) -> int:
        """OnMissileHit: 爆炸中(state==2)隔帧跳过, 命中帧伤害 /3(最低 1), 速度×0.88;
        首中(state!=2)按 anmFileIdx 扩大判定盒并给随机上向速度(爆炸变形)。"""
        if bullet.bullet_state == 2:
            if bullet.timer % 2 != 0:
                return 1
            bullet.damage = bullet.damage // 3
            if bullet.damage == 0:
                bullet.damage = 1
            bullet.velocity = bullet.velocity * 0.88
        else:
            blast = MISSILE_BLAST.get(bullet.anm_file_idx)
            if blast is not None:
                angle = self.rand_float(math.pi / 2) - 3 * math.pi / 4
                bullet.hitbox = (blast[0], blast[0])
                bullet.velocity = Vec2.from_angle(angle, blast[1])
        return 0

    # ---- 命中敌人(CalcDamageToEnemy, §A.6) ----
    def iter_hits(self, enemy_center: Vec2, enemy_size: tuple[float, float],
                  *, bomb_active: bool | None = None
                  ) -> Iterator[tuple[PlayerBullet, int]]:
        """对一个敌人(center ± size/2)逐发结算本帧伤害, 产出 (bullet, damage)。

        有副作用, 每帧每敌人至多调用一次:
        - 非激光弹命中后 bullet_state=2(爆炸); bullet_state2!=3 才速度/8(穿透不减速);
        - bullet_state2 4/5(激光型)只在 timer%2==0 出伤害;
        - hit 回调(如 missile 隔帧)返回 1 则跳过当帧伤害;
        - bomb 中伤害 max(damage//3, 1)(bomb_active 缺省取 self.bomb_active);
        - playerLaser 的 pos_history 拖尾段各算 1 点伤害
          (对照 C++ UpdatePlayerLaser 写 bombDamageBoxes[96+i] lifetime=1)。

        简化: C++ 开头 `!invulnerabilityTimer.HasTicked()` 的 0 伤害守卫依赖
        ZunTimer 暂停语义(current==previous), 整数计时模型下恒为已 tick, 故省略。
        敌人侧的 grazeSize 追加、樱点/得分/70 封顶/符卡缩放不在此结算(见 §A.6 后段)。
        """
        bomb = self.bomb_active if bomb_active is None else bomb_active
        ex, ey = enemy_size[0] / 2, enemy_size[1] / 2
        for bullet in self.bullet_pool:
            if bullet.bullet_state == 0:
                continue
            # 只结算活弹; 爆炸(state==2)后仅穿透弹(bs2==3)继续判定
            if bullet.bullet_state != 1 and bullet.bullet_state2 != 3:
                continue
            if not _aabb_intersect(bullet.pos, bullet.hitbox[0] / 2, bullet.hitbox[1] / 2,
                                   enemy_center, ex, ey):
                continue
            if bullet.bullet_state2 in (4, 5) and bullet.timer % 2 != 0:
                continue
            if self._run_hit_cb(bullet):
                continue
            dmg = bullet.damage if not bomb else max(bullet.damage // 3, 1)
            if bullet.bullet_state2 not in (4, 5):
                bullet.bullet_state = 2
                if bullet.bullet_state2 != 3:
                    bullet.velocity = bullet.velocity / 8.0
            yield bullet, dmg
        # playerLaser 拖尾历史段(每段 1 点, 无奇偶减半)
        for bullet in self.bullet_pool:
            if bullet.bullet_state == 0 or bullet.update_cb != UPDATE_PLAYER_LASER:
                continue
            for i in range(min(bullet.trail_length, LASER_HISTORY)):
                hp = bullet.pos_history[i]
                if hp.x < -900.0:
                    break
                if _aabb_intersect(hp, bullet.hitbox[0] / 2, bullet.hitbox[1] / 2,
                                   enemy_center, ex, ey):
                    yield bullet, 1

    def calc_damage_to_enemy(self, enemy_center: Vec2, enemy_size: tuple[float, float],
                             *, bomb_active: bool | None = None) -> int:
        """iter_hits 的求和封装(一帧对一个敌人的总伤害)。"""
        return sum(d for _, d in self.iter_hits(enemy_center, enemy_size,
                                                bomb_active=bomb_active))


# _aabb_intersect 已上移到 engine/player_base.py(顶部 import 再导出)。

def _bullet_out_of_bounds(b: PlayerBullet) -> bool:
    """弹中心±半宽完全离开 [0,384]x[0,448] 版面(GameManager::IsInBounds)。
    C++ 用精灵像素宽高做边距, 纯逻辑层用判定盒全宽/全高近似。"""
    hx, hy = b.hitbox[0] / 2, b.hitbox[1] / 2
    return (b.pos.x + hx < 0.0 or b.pos.x - hx > SCREEN.x
            or b.pos.y + hy < 0.0 or b.pos.y - hy > SCREEN.y)
