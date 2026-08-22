""" 敌人与波次 —— Pythonic。

Enemy 用"脚本动作列表"(移动到某个点 / 停一下 / 放一发弹)驱动,
以可读的 Python 数据表达, 取代 ECL 字节码的 C 味直译。
"""

from __future__ import annotations

import math
import msgspec
from typing import Callable, Sequence

from .bullets import BulletWorld
from .ecl import EnemyBulletShooter, Vec3
from .ecl_base import EclMachineBase
from .player_base import PlayerCombatFace, PlayerState
from ..utils import Vec2, angle_to


# ---- 敌人侧伤害结算 (EnemyManager::OnUpdate / 规格 §A.6 下半) ----

class DamageResult(msgspec.Struct):
    """一次伤害结算的产出(透出给上层, 由整合层接到 globals)。"""
    damage: int = 0        # 实际扣血(全部缩放/封顶之后)
    cherry_gain: int = 0   # 樱点增量(0 = 不加; 上层走 ZunGlobals.add_cherry_plus)
    score_code: int = 0    # 伤害得分, 代码值(=显示分*10, 上层走 ZunGlobals.add_score)


def stage_factor(stage: int) -> int:
    """stageFactor (EnemyManager.cpp:624)。"""
    return 10 if stage >= 5 else stage * 2


def settle_damage(damage: int, *, is_boss: bool, is_focus: bool,
                  bomb_in_use: bool = False, bomb_damage: bool = False,
                  stage: int = 1, spellcard_active: bool = False,
                  used_bomb: bool = False, invincibility_timer: int = 0,
                  enemy_timer: int = 0, can_be_damaged: bool = True,
                  graze_damage: int = 0, is_reimu_a: bool = False) -> DamageResult:
    """敌人受击后处理, 数值以 EnemyManager.cpp:782-890 为准(C++ int 截断语义)。

    顺序: grazeSize 额外伤害 → cherryGain(用未封顶的原始伤害, 含 ReimuA 机型
    修正) → 伤害封顶 70 → 得分(damage/5) → 符卡缩放 → 无敌时间缩放。
    """
    # grazeSize 额外伤害: 无 bomb 伤害时 damage += grazeDamage/2.5
    if graze_damage > 0 and not bomb_damage:
        damage = int(damage + graze_damage / 2.5)
    result = DamageResult()
    if damage <= 0:
        return result
    sf = stage_factor(stage)
    # 樱点获取: 非 bomb 且 (boss 或未 focus)
    if (is_boss or not is_focus) and not bomb_in_use:
        if is_boss and not is_focus:
            cherry_gain = damage // (10 - sf // 3) * 10
        else:
            cherry_gain = damage // (30 - sf) * 10
        if cherry_gain > 70:
            cherry_gain = 70
        if cherry_gain == 0 and (not is_focus or enemy_timer & 1):
            cherry_gain = 10
        # ReimuA 机型修正 (EnemyManager.cpp:815-835, case SHOT_REIMU_A):
        # cherryGain 为 20/30 且敌 timer 为奇 → -10; stage 5-6 非 boss 伤害
        # 减半, stage 4 非 boss 伤害 -1/4-1/16。注意整段在樱点块内, 故仅
        # (boss 或未 focus) 且非 bomb 时生效 (同 C++ 条件嵌套)。
        if is_reimu_a:
            if cherry_gain in (20, 30) and enemy_timer & 1:
                cherry_gain -= 10
            if 5 <= stage <= 6 and not is_boss:
                damage = damage // 2
            if stage == 4 and not is_boss:
                damage -= damage // 4 + damage // 16
        result.cherry_gain = cherry_gain
    if damage >= 70:
        damage = 70
    result.score_code = damage // 5 * 10
    if can_be_damaged:
        # 符卡中的伤害缩放
        if spellcard_active:
            if not bomb_damage:
                damage = damage // 7 if damage > 7 else (1 if damage != 0 else 0)
            elif used_bomb:
                damage = int(damage / 2.5) if damage > 2 else (1 if damage != 0 else 0)
            else:
                damage = 0
        if invincibility_timer > 0:
            damage = damage // 9 if is_boss else 0
        result.damage = damage
    return result


# ---- 索敌 (EnemyManager.cpp:894-938) ----

# 咲夜索敌窗口: 目标相对玩家的 atan2 角度须在 [-120°, -60°] (正上方 ±30°)
_SAKUYA_TARGET_ANGLE_LO = -2.0943952
_SAKUYA_TARGET_ANGLE_HI = -1.0471976


class Targeting(msgspec.Struct):
    """每帧的索敌状态 (Player.hpp 的 positionOfLastEnemyHit/sakuyaTargetPosition/
    targetingEnemy)。每帧由上层 reset (Player::UpdateUI 对应), 伤害扫描中
    按敌人顺序 update, 扫完由上层写回 player 字段。"""

    position_of_last_enemy_hit: Vec2 = msgspec.field(
        default_factory=lambda: Vec2(-999.0, -999.0))
    sakuya_target_position: Vec2 = msgspec.field(default_factory=lambda: Vec2(-999.0, -999.0))
    targeting: bool = False

    def reset(self) -> None:
        self.position_of_last_enemy_hit = Vec2(-999.0, -999.0)
        self.sakuya_target_position = Vec2(-999.0, -999.0)
        self.targeting = False

    def update(self, enemy_pos: Vec2, player_pos: Vec2, *, is_boss: bool,
               is_sakuya: bool) -> None:
        """EnemyManager.cpp:894-938。Boss: 取 |dx| 离玩家更近者, 咲夜另按角度窗口
        更新 sakuya 目标并立 targeting; 未 targeting 时取最靠下的敌人,
        咲夜在窗口内补 sakuya 目标(仅当尚未设置)。"""
        if is_boss:
            enemy_diff = enemy_pos - player_pos
            diff = self.position_of_last_enemy_hit - player_pos
            if not self.targeting or abs(diff.x) > abs(enemy_diff.x):
                self.position_of_last_enemy_hit = enemy_pos
            if is_sakuya:
                diff = self.sakuya_target_position - player_pos
                angle = math.atan2(enemy_diff.y, enemy_diff.x)
                if _SAKUYA_TARGET_ANGLE_LO <= angle <= _SAKUYA_TARGET_ANGLE_HI and (
                        not self.targeting or abs(diff.x) > abs(enemy_diff.x)):
                    self.sakuya_target_position = enemy_pos
                    self.targeting = True
            else:
                self.targeting = True
        if not self.targeting:
            if self.position_of_last_enemy_hit.y < enemy_pos.y:
                self.position_of_last_enemy_hit = enemy_pos
            if is_sakuya and self.sakuya_target_position.y < -900.0:
                angle = math.atan2(enemy_pos.y - player_pos.y,
                                   enemy_pos.x - player_pos.x)
                if _SAKUYA_TARGET_ANGLE_LO <= angle <= _SAKUYA_TARGET_ANGLE_HI:
                    self.sakuya_target_position = enemy_pos


class ScriptedEnemy(msgspec.Struct):
    """一个由动作列表驱动的敌人。"""

    path: list[Vec2]          # 移动轨迹(途经点)
    fire: Callable[["ScriptedEnemy", BulletWorld], None] | None = None
    life: int = 8
    speed: float = 2.0        # 途经点间飞行速度
    radius: float = 12.0      # 判定/绘制半径(命中判定为 pos±radius 的 AABB)
    graze_size: Vec2 = msgspec.field(default_factory=Vec2.zero)  # 擦弹盒(全宽/全高); x>0 时追加一次判定
    is_boss: bool = False
    can_die: bool = True
    is_hittable: bool = True
    can_be_damaged: bool = True
    invincibility_timer: int = 0

    pos: Vec2 = None          # type: ignore[assignment]
    _target_idx: int = 0
    fire_delay: int = 10      # 放弹间隔
    alive: bool = True
    # 体术/伤害门槛标志 (C enemyTemplate 默认值, EnemyManager.hpp:328-333)
    has_no_collision: int = 0
    has_contact_hitbox: int = 1
    is_projectile: int = 0
    invisible_on_bomb: int = 0
    _tick: int = 0            # 内部帧计数(__post_init__ 归零; Struct 字段须声明)

    def __post_init__(self) -> None:
        self.pos = self.path[0]
        self._target_idx = 0
        self._tick = 0

    @property
    def done(self) -> bool:
        return self._target_idx >= len(self.path)

    @property
    def hitbox_full(self) -> tuple[float, float]:
        """判定盒全宽/全高 (C++ 直接把 hitboxSize 传给 CheckBulletPlayerCollision)。"""
        return (self.radius * 2.0, self.radius * 2.0)

    def _step_move(self) -> None:
        if self.done:
            return
        target = self.path[self._target_idx]
        to_target = target - self.pos
        dist = to_target.length
        if dist <= self.speed:
            self.pos = target
            self._target_idx += 1
        else:
            self.pos = self.pos + to_target.normalized() * self.speed

    def step(self, world: BulletWorld, *, rng=None) -> None:
        """推进一帧: 移动 + 按间隔放弹。"""
        self._tick += 1
        if self.invincibility_timer > 0:
            self.invincibility_timer -= 1
        self._step_move()
        if self.fire is not None and self._tick % self.fire_delay == 0:
            self.fire(self, world)

    def kill(self) -> bool:
        """被击坠(生命归零)。返回 True = 计入击杀(得分/掉落)。"""
        self.alive = False
        return True


class EnemyHost:
    """敌人管理: 进程中的波次表。"""

    def __init__(self) -> None:
        # EclEnemy 鸭子类型适配 ScriptedEnemy 接口(见 EclEnemy 类 docstring)
        self._enemies: list[ScriptedEnemy | EclEnemy] = []

    def add(self, enemy: ScriptedEnemy | EclEnemy) -> None:
        self._enemies.append(enemy)

    def spawn(self, *, path: Sequence[Vec2], life: int = 8, speed: float = 2.0,
              fire: Callable[[ScriptedEnemy, BulletWorld], None] | None = None,
              radius: float = 12.0, graze_size: Vec2 | None = None) -> ScriptedEnemy:
        e = ScriptedEnemy(list(path), fire=fire, life=life, speed=speed, radius=radius,
                          graze_size=graze_size if graze_size is not None else Vec2.zero())
        self._enemies.append(e)
        return e

    def step(self, world: BulletWorld, *, rng=None) -> None:
        for e in self._enemies:
            if e.alive:
                e.step(world, rng=rng)
        self._enemies = [e for e in self._enemies if e.alive and not e.done]

    def contact_hits(self, player: PlayerCombatFace) -> bool:
        """敌人体术判定 (EnemyManager.cpp:754-775 → Enemy::CheckBulletPlayerCollision
        :576-595)。每帧在 shoot_hits 前调用(同 C++ OnUpdate 伤害段顺序)。

        门槛 (C++:754-756): !hasNoCollision && !invisibleOnBomb && canDie
        && hasContactHitbox。判定盒 = hitboxSize/1.5 (pos 为中心) vs 玩家
        killbox; 带 trail 的敌人追加历史位置节点 (j=1..trailInterval 步进 6,
        trailFlags&2 时判定盒随 j 线性收缩 —— 实机 8 关 ECL 的 trailFlags
        全为 25, 收缩路径仅为完备保留)。
        命中按 C++ CalcKillboxCollision==1 语义(含玩家无敌/重生中的纯相交):
        canDie && !isBoss && !isProjectile → enemy.life -= 10 (每个盒独立扣,
        撞死由 shoot_hits 的 life<=0 分支统一结算, 同 C++:941 在门槛外)。
        isProjectile 敌人另有擦弹 (timer%6==0, 盒=hitboxSize/0.7, C++:582-587);
        普通敌人体术无擦弹。返回本帧玩家是否被体术撞死(供上层记死亡点)。
        """
        died = False
        for e in self._enemies:
            if not e.alive or e.has_no_collision or e.invisible_on_bomb:
                continue
            if not (e.can_die and e.has_contact_hitbox):
                continue
            fw, fh = e.hitbox_full
            # 本体盒 + trail 历史节点盒 (center, 全宽, 全高)
            boxes = [(e.pos, fw, fh)]
            st = getattr(e, "state", None)
            if st is not None and st.trail[0] != 0:
                interval = st.trail[2]
                for j in range(1, interval, 6):
                    if j >= len(st.trail_history):
                        break
                    cw, ch = fw, fh
                    if st.trail[0] & 2:  # 收缩: hitboxSize - hitboxSize*j/interval
                        cw = fw - fw * j / interval
                        ch = fh - fh * j / interval
                    hp = st.trail_history[j]
                    boxes.append((Vec2(hp.x, hp.y), cw, ch))
            for center, w, h in boxes:
                if e.is_projectile and e._tick % 6 == 0:
                    player.check_graze(center, (w / 0.7, h / 0.7))
                pre = player.state
                if not player.check_contact(center, (w / 1.5, h / 1.5)):
                    continue
                if pre == PlayerState.ALIVE and player.state == PlayerState.DEAD:
                    died = True
                if e.can_die and not e.is_boss and not e.is_projectile:
                    e.life -= 10
        return died

    def shoot_hits(self, player: PlayerCombatFace, targeting: Targeting, *, is_focus: bool,
                   is_sakuya: bool, bomb_in_use: bool, stage: int,
                   spellcard_active: bool = False, used_bomb: bool = False,
                   is_reimu_a: bool = False, bomb_box_hit=None
                   ) -> tuple[list[tuple[ScriptedEnemy | EclEnemy, DamageResult]],
                              list[ScriptedEnemy | EclEnemy]]:
        """自机弹 vs 敌人完整管线 (EnemyManager.cpp:754-938 OnUpdate 伤害段)。

        每帧对每个 can_die/is_hittable 敌人调一次 player.calc_damage_to_enemy
        (判定盒 pos±radius; 有副作用: 命中弹进爆炸态); graze_size.x>0 时用
        graze_size 再算一次, 额外伤 grazeDamage/2.5 由 settle_damage 应用。
        bomb 中且 bomb 伤害盒命中 graze 盒时跳过该额外伤 (EnemyManager.cpp:
        783-790 的 collisionOut!=0 分支; bomb_box_hit(center, full_size) 谓词
        由上层提供, 纯判定不累计 bomb 盒 damage)。

        【与 C++ 的已知偏差: 分路径结算】 C++ 的 CalcDamageToEnemy
        (Player.cpp:825-938) 把子弹伤害与 bomb 伤害盒(lifetime)合成一笔
        damage, 再在 EnemyManager.cpp:849-868 按 collisionOut 对整个总额做
        一次符卡缩放(collisionOut==0 → /7; !=0 && usedBomb → /2.5; !=0 &&
        !usedBomb → 0)。本实现分两条路径: 子弹走本函数(settle_damage
        bomb_damage=False → 恒 /7 分支), bomb 盒走 impl._apply_bomb_boxes
        (bomb_damage=True → /2.5 分支)。同帧混合命中时与 C++ 有偏差:
        两处 int 截断 vs 一处(±1 级), 且 bomb 盒命中时 C++ 对含子弹的总额
        用 /2.5 而这里子弹部分仍 /7(bomb 中子弹已预 /3, 每帧额小, 实际差
        通常个位数)。此外 box.damage 累计次数不同(C++ hitbox+graze 盒两次
        CalcDamageToEnemy 调用各累计一次, 影响盒销毁阈值如 BombData 的
        damage>=100)与帧内作用点不同(C++ 在 RunEcl 后, 这里 bomb 盒在
        ECL 步进前)。全量对齐需把 bomb 盒并入本函数且移动帧内作用点,
        会改动既有测试钉住的数值语义, 故保持分路径(偏差由
        test_enemies.py::test_mixed_bullet_bomb_damage_split_settlement 钉住)。
        is_reimu_a 透传 settle_damage 的 ReimuA 机型修正 (EnemyManager.cpp:
        815-835)。每个敌人(无论是否受伤)都参与索敌 (targeting.update)。
        hasNoCollision/invisibleOnBomb 跳过整个碰撞/伤害段 (C++:754);
        life<=0 && canDie 的击杀分支在该门槛之外 (C++:941), 体术撞掉的血
        (contact_hits 的 life-=10) 也在此结算。
        返回 (每敌结算列表, 本帧击杀列表); cherry_gain/score_code 由上层入账。
        """
        results: list[tuple[ScriptedEnemy | EclEnemy, DamageResult]] = []
        kills: list[ScriptedEnemy | EclEnemy] = []
        for e in self._enemies:
            if not e.alive:
                continue
            if not (e.has_no_collision or e.invisible_on_bomb) \
                    and e.can_die and e.is_hittable:
                damage = player.calc_damage_to_enemy(
                    e.pos, (e.radius * 2.0, e.radius * 2.0), bomb_active=bomb_in_use)
                graze_damage = 0
                if e.graze_size.x > 0.0:
                    graze_damage = player.calc_damage_to_enemy(
                        e.pos, (e.graze_size.x, e.graze_size.y), bomb_active=bomb_in_use)
                    if bomb_in_use and bomb_box_hit is not None and bomb_box_hit(
                            e.pos, (e.graze_size.x, e.graze_size.y)):
                        # collisionOut!=0: grazeDamage 整体丢弃(含 bomb 盒对
                        # graze 盒的伤害, 该部分 C++ 也不计入 damage)
                        graze_damage = 0
                r = settle_damage(
                    damage, is_boss=e.is_boss, is_focus=is_focus,
                    bomb_in_use=bomb_in_use, stage=stage,
                    spellcard_active=spellcard_active, used_bomb=used_bomb,
                    invincibility_timer=e.invincibility_timer,
                    enemy_timer=e._tick, can_be_damaged=e.can_be_damaged,
                    graze_damage=graze_damage, is_reimu_a=is_reimu_a)
                e.life -= r.damage
                targeting.update(e.pos, player.pos, is_boss=e.is_boss, is_sakuya=is_sakuya)
                results.append((e, r))
            if e.life <= 0 and e.can_die:
                if e.kill():
                    kills.append(e)
        self._enemies = [e for e in self._enemies if e.alive]
        return results, kills

    def clear(self) -> None:
        self._enemies.clear()

    def alive(self) -> list[ScriptedEnemy | EclEnemy]:
        return [e for e in self._enemies if e.alive]

    def all(self) -> list[ScriptedEnemy | EclEnemy]:
        """含正在死亡动画中的敌人(供清场/RemoveAllEnemies 用)。"""
        return list(self._enemies)


def aimed_ring_fire(arms: int = 12, speed: float = 2.2):
    """返回一个"瞄准玩家放环"的 fire 回调。"""

    def fire(enemy: ScriptedEnemy, world: BulletWorld) -> None:
        world.ring(enemy.pos, arms, speed)

    return fire


def aimed_spread_fire(arms: int = 5, spread_angle: float = 0.2, speed: float = 2.8):
    def fire(enemy: ScriptedEnemy, world: BulletWorld) -> None:
        world.spread(enemy.pos, arms, speed, spread_angle)

    return fire


# ---- ECL 驱动的敌人 (EnemyManager::OnUpdate 的 RunEcl/回调段) ----

class EclEnemy:
    """携带 ECL 虚拟机(EclMachineBase 子类)的敌人, 鸭子类型适配 ScriptedEnemy 接口。

    权威状态在 EclEnemyState (machine.enemy), 字段映射:
      pos         ← state.pos (Vec3 的 x/y, 返回拷贝)
      life        ↔ state.life (读写直通, ECL 的 LIFE 变量与伤害管线共用)
      radius      ← state.hitbox_size.x / 2 (管线判定盒为 pos±radius,
                    故全宽 = hitbox_size.x, 与 C 直接把 hitboxSize 传给
                    CalcDamageToEnemy 一致)
      graze_size  ← state.graze_size (x/y)
      is_boss / can_die / is_hittable / can_be_damaged /
      invincibility_timer / _tick ← state 同名字段
      done        恒 False (ECL 敌人由 machine.step() 返回 False 来 despawn)
    生命/超时回调 (HandleLifeCallback/HandleTimerCallback) 在 C 里属于
    EnemyManager 而非 RunEcl, 故由本类在 step() 里触发; 清场与符卡超时
    记账经 host (GameEclHost) 透出。
    """

    def __init__(self, machine: EclMachineBase, host=None) -> None:
        self.machine = machine
        self.state = machine.enemy
        self._host = host          # GameEclHost (清场/超时事件), None=裸跑
        self.alive = True
        self._kill_no_score = False  # kill() 置位: death_type==2 不计分
        st = self.state
        # enemyTemplate 默认值 (EnemyManager::Initialize)
        st.hitbox_size.set(12.0, 12.0, 12.0)
        st.life = st.max_life = 1
        st.score = 100
        st.can_die = 1
        st.has_contact_hitbox = 1
        st.can_be_damaged = 1
        st.is_hittable = 1
        st.bullet_rank_speed_low = -0.15
        st.bullet_rank_speed_high = 0.15

    # ---- 字段映射 ----
    @property
    def pos(self) -> Vec2:
        return Vec2(self.state.pos.x, self.state.pos.y)

    @property
    def life(self) -> int:
        return self.state.life

    @life.setter
    def life(self, v: int) -> None:
        self.state.life = int(v)

    @property
    def max_life(self) -> int:
        return self.state.max_life

    @property
    def radius(self) -> float:
        return self.state.hitbox_size.x / 2.0

    @property
    def graze_size(self) -> Vec2:
        return Vec2(self.state.graze_size.x, self.state.graze_size.y)

    @property
    def is_boss(self) -> bool:
        return bool(self.state.is_boss)

    @property
    def hitbox_full(self) -> tuple[float, float]:
        """hitboxSize 全宽/全高 (C++ 直接把 Float3 传给 CheckBulletPlayerCollision)。"""
        return (self.state.hitbox_size.x, self.state.hitbox_size.y)

    @property
    def has_no_collision(self) -> bool:
        return bool(self.state.has_no_collision)

    @property
    def has_contact_hitbox(self) -> bool:
        return bool(self.state.has_contact_hitbox)

    @property
    def is_projectile(self) -> bool:
        return bool(self.state.is_projectile)

    @property
    def invisible_on_bomb(self) -> bool:
        return bool(self.state.invisible_on_bomb)

    @property
    def can_die(self) -> bool:
        return bool(self.state.can_die)

    @property
    def is_hittable(self) -> bool:
        return bool(self.state.is_hittable)

    @property
    def can_be_damaged(self) -> bool:
        return bool(self.state.can_be_damaged)

    @property
    def invincibility_timer(self) -> int:
        return self.state.invincibility_timer

    @property
    def _tick(self) -> int:
        return self.state.timer

    @property
    def done(self) -> bool:
        return False

    # ---- 每帧 (EnemyManager::OnUpdate: RunEcl → 回调 → [伤害由 shoot_hits]) ----
    def step(self, world: BulletWorld | None = None, *, rng=None) -> None:
        st = self.state
        if (st.freeze_ecl_during_bombs and self._host is not None
                and self._host.frozen):
            # C: timer-- 后 goto 循环尾 (LAB_00421da7), 尾部 timer++ 抵消 →
            # 净不变; invincibilityTimer-- 照常 (EnemyManager.cpp:658-663,
            # 1096-1100)。旧实现 timer 净 -1/帧, 死亡频繁时 boss 计时器倒走。
            if st.invincibility_timer > 0:
                st.invincibility_timer -= 1
            return
        if not self.machine.step():
            self._deactivate()
            return
        # EnemyManager.cpp:682-696: trail 历史每帧右移(在 disableMovement 之外)
        if st.trail[0] != 0:
            self._shift_trail_history()
        # EnemyManager.cpp:697-700: 无 sprite 的敌人失去碰撞(只置位, 不清位);
        # 逻辑层无贴图加载, 以 anm_idx<0 (未 SET_ANM) 近似 primaryVm.sprite==NULL
        if st.anm_idx < 0:
            st.has_no_collision = 1
        # EclManager.cpp:2261-2276 (RunEcl 收尾): 7/8 面 boss 符卡(idx>=118)
        # 且炸弹中 → invisibleOnBomb(无碰撞/不受击), 炸弹结束后延迟 1 帧解除
        h = self._host
        if h is not None and st.is_boss and h.world.current_stage >= 7:
            if h.bomb_in_use and h.world.spellcard_active and h.spellcard_idx >= 118:
                st.invisible_on_bomb = 1
                st.spellcard_delay_timer = 1
            elif st.spellcard_delay_timer > 0:
                st.spellcard_delay_timer -= 1
            else:
                st.invisible_on_bomb = 0
        self._handle_life_callback()
        if self.alive:
            self._handle_timer_callback()

    def _shift_trail_history(self) -> None:
        """EnemyManager.cpp:682-696: enemyHistory 右移, [0]=当前 pos。"""
        st = self.state
        n = st.trail[1]  # trailCount
        h = st.trail_history
        if len(h) < n:
            # C Initialize: 全部 pos.x=-999 哨兵 (EnemyManager.hpp:299-302)
            h.extend(Vec3(-999.0, 0.0, 0.0) for _ in range(n - len(h)))
        for j in range(n - 1, 0, -1):
            h[j] = h[j - 1]
        h[0] = st.pos.copy()

    def kill(self) -> bool:
        """生命归零的死亡分支 (EnemyManager.cpp:943 OnUpdate: life<=0 && canDie)。

        返回 True = 计入击杀(得分/掉落, 由上层入账)。death_type:
        0 正常击坠 despawn; 1 计分后 canDie=0 继续跑死亡 sub;
        2 阶段击破(如 4 面三姐妹): 不计分, 保持 active, 跑死亡回调
          (回调通常 SET_LIFE 复活并进下一阶段, 见 ecldata4 sub44/45);
        3 boss 离场(escape): 不计分不掉落, 钉 life=1 继续跑死亡 sub。
        C 里四种 type 都会跑 deathCallbackSub(case 0/1 goto END_BOSS 后
        落进 case 2 的掉落段, 然后统一进死亡回调), 这里一并对齐。
        """
        st = self.state
        dt = st.death_type
        self._kill_no_score = dt == 2  # C case 2 无 AddScore(其余 case 有)
        st.life_callback_threshold = [-1] * 4
        st.timer_callback_threshold = -1
        st.periodic_callback_sub = -1
        if dt == 3:
            st.life = 1
            st.can_be_damaged = 0
            st.death_type = 0
            self._run_death_callback()
            return False
        if dt == 1:
            st.can_die = 0
            st.life = 0  # C case 1 goto END_BOSS 落进 case 2: life=0
            self._run_death_callback()
            return True
        if dt == 2:
            # C case 2: 无 AddScore, active 不变; 掉落/清场由上层入账
            st.life = 0
            self._run_death_callback()
            return True
        self._deactivate()
        self._run_death_callback()  # C: active=0 后死亡回调照样 CallEclSub
        return True

    # ---- 内部 ----
    def _deactivate(self) -> None:
        self.alive = False
        self.state.active = 0
        if self._host is not None:
            self._host.on_enemy_gone(self)

    def _callback_reset(self) -> None:
        """回调切换时的公共复位 (C: rank 参数/bulletProps/shootInterval/stackDepth)。"""
        st = self.state
        st.bullet_rank_speed_low = -0.5
        st.bullet_rank_speed_high = 0.5
        st.bullet_rank_amount1_low = st.bullet_rank_amount1_high = 0
        st.bullet_rank_amount2_low = st.bullet_rank_amount2_high = 0
        st.bullet_props = EnemyBulletShooter()
        st.shoot_interval = 0
        self.machine.stack.clear()

    def _rerun_ecl(self) -> None:
        """回调后的 goto HUH: 当帧重跑一次 RunEcl。"""
        if not self.machine._run_ecl():
            self._deactivate()

    def _handle_life_callback(self) -> None:
        """Enemy::HandleLifeCallback: 跌破阈值 → 钉生命 + 切 sub + 清场。"""
        st = self.state
        for i in range(4):
            t = st.life_callback_threshold[i]
            if t < 0 or st.life >= t:
                continue
            st.life = t
            sub = st.life_callback_sub[i]
            st.life_callback_threshold[i] = -1
            st.timer_callback_threshold = -1
            st.periodic_callback_sub = -1
            self.machine.call_sub(sub)
            self._callback_reset()
            if self._host is not None:
                self._host.clear_field(self)  # 杀光非 boss 敌
            self._rerun_ecl()
            return

    def _handle_timer_callback(self) -> None:
        """Enemy::HandleTimerCallback: 超时 → 切 sub + 符卡按失败处理(经 host)。"""
        st = self.state
        if st.timer_callback_threshold < 0 or st.timer < st.timer_callback_threshold:
            return
        # 若还有更高的生命阈值, 钉生命并清掉(不触发其回调)
        best, best_i = 0, -1
        for i in range(4):
            t = st.life_callback_threshold[i]
            if t >= 0 and t > best:
                best, best_i = t, i
        if best > 0:
            st.life = best
            st.life_callback_threshold[best_i] = -1
        sub = st.timer_callback_sub
        self.machine.call_sub(sub)
        st.timer_callback_threshold = -1
        st.timer_callback_sub = st.death_callback_sub
        st.timer = 0
        if self._host is not None:
            self._host.on_timer_callback(self)  # 捕获失败/清弹/樱罚 + 清场
        st.periodic_callback_sub = -1
        self._callback_reset()
        self._rerun_ecl()

    def _run_death_callback(self) -> None:
        """死亡回调 sub (复位后 CallEclSub, 后续帧由 machine.step 继续跑)。"""
        st = self.state
        if st.death_callback_sub < 0:
            return
        sub = st.death_callback_sub
        st.death_callback_sub = -1
        self._callback_reset()
        self.machine.call_sub(sub)
