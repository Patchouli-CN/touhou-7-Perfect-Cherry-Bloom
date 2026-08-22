""" ECL 宿主实现 —— 把 EclHost 钩子接到游戏世界。

GameEclHost 持有 BulletWorld/LaserWorld/ItemWorld/EnemyHost 的引用:
- spawn_bullet_pattern: EnemyBulletShooter → Burst 展开进 BulletWorld
  (aimed 模式在此加 angle_to_player, 对照 BulletManager::SpawnBulletPattern);
- spawn_laser_pattern: EnemyLaserShooter → LaserWorld, 返回 Laser 作句柄
  (C SpawnLaserPattern: type==0 出生即瞄玩家);
- spawn_enemy: SpawnEnemy 语义 —— 模板默认值 → 定 pos/mirror/life →
  CallEclSub + 立即跑一次 RunEcl → 再落 itemDrop/score/maxLife;
- spawn_item / remove_all_bullets / remove_bullets_in_radius /
  remove_all_enemies 按 C 同名函数语义(弹转 POINT_BULLET 道具等);
- Boss/符卡: set_boss / begin_spellcard / end_spellcard / on_timer_callback
  只登记 world.bosses 与 spellcard_active, 记账通过 on_* 回调透出给 impl
  (boss.py 状态机)。

24 条 ExIns boss 特技 (EnemyEclInstr.cpp g_EclExInstr) 全部实现, 见
run_ex_instr 的 _EX_DISPATCH —— 8 个 ecldata 里 0..23 全部有真实使用。
BombEffects/闪屏等视觉表现不接(各方法注释注明); 音效经 self.sound 队列、
BGM 经 self.bgm_events 透出(PLAY_SOUND / ex19 / ex20), 由 impl 帧末收口。
"""

from __future__ import annotations

import math
from typing import Optional

from ...registry import register_game_hooks
from ...schema.msg import MsgVm
from ...schema.sound import SoundQueue
from ...types import (
    BeginSpellcardHook,
    EndSpellcardHook,
    IntHook,
    PosLike,
    SetBossHook,
)
from ...engine.bullets import (
    Aim,
    BulletWorld,
    Burst,
    bullet_active_sprite_idx,
    bullet_sprite_height,
    bullet_type_size,
)
from ...engine.bullet_commands import BulletCommand, CmdFlag
from ...engine.ecl import (
    BulletCommandData,
    EclContextArgs,
    EclEnemyState,
    EclFile,
    EclHost,
    EnemyBulletShooter,
    EnemyLaserShooter,
    Vec3,
)
from ...engine.ecl_base import EclMachineBase
from ...engine.enemies import EclEnemy, EnemyHost
from .items import ItemType, ItemWorld
from ...engine.lasers import Laser, LaserState, LaserWorld
from ...utils import Vec2, add_normalize_angle, angle_to

# C 弹幕上限 (BulletManager::SpawnBulletPattern 的 bulletCount >= 1024 检查)
_MAX_BULLETS = 1024
# C 激光上限 64 (SpawnLaserPattern 的槽位数)
_MAX_LASERS = 64

# ECL aimed 模式(出生时叠加 angle_to_player)
_AIMED_MODES = (Aim.SPREAD_AIMED, Aim.RING_AIMED, Aim.RING_SHIFT_AIMED)


@register_game_hooks("th07")
class GameEclHost(EclHost):
    """接真实游戏世界的 ECL 宿主。

    注册为 th07 的游戏回调包(registry.GameHooks): 宿主类 + 关卡资源
    命名规则(默认值即 th07 的 stage{n}.std / ecldata{n}.ecl / msg{n}.dat)。
    """

    def __init__(self, ecl_file: EclFile, world, *, enemies: EnemyHost,
                 bullets: BulletWorld, lasers: LaserWorld, items: ItemWorld,
                 ecl_machine_cls: type[EclMachineBase]) -> None:
        self.file = ecl_file
        self.world = world
        self.enemies = enemies
        self.bullets = bullets
        self.lasers = lasers
        self.items = items
        # VM 类构造注入(解耦硬编码实例化; PerfectCherryBloom 注入 EclMachineTh07)
        self.ecl_machine_cls = ecl_machine_cls
        self.frozen = False       # 炸弹中/玩家非 ALIVE (freeze_ecl_during_bombs 用)
        self.bomb_in_use = False  # 炸弹中 (invisible_on_bomb 规则用, EclManager.cpp:2261)
        self.spellcard_idx = -1   # 当前符卡全局 idx (g_EnemyManager.spellcardInfo.spellcardIdx)
        self.power = 0.0          # spawn_item 满火力转换用(每帧由 impl 同步)
        self.last_msg_id = -1     # msg_read 记录(最近一次 MsgRead 的原始 arg0)
        # 消息系统(impl 装入): msg_vm 为 None 时维持旧行为(仅记录, 不停轴)
        self.msg_vm: MsgVm | None = None
        self.msg_character = 0    # C g_GameManager.character (0=灵梦 1=魔理沙 2=咲夜)
        self.boss_health: tuple = ()   # set_boss_health 登记(GUI 血条数据)
        self.boss_life_markers = 0     # set_boss_life_markers 登记
        self.enemy_by_state: dict[int, EclEnemy] = {}
        # ---- 事件透出(impl 接线; 均为可选) ----
        self.on_set_boss: SetBossHook | None = None          # (idx, EclEnemyState|None)
        self.on_begin_spellcard: BeginSpellcardHook | None = None  # (state, gui_id, idx, name)
        self.on_end_spellcard: EndSpellcardHook | None = None      # (state)
        self.on_spellcard_timeout: EndSpellcardHook | None = None  # (state) 捕获失败记账
        self.on_set_power: IntHook | None = None             # (value)
        self.on_add_cherry_plus: IntHook | None = None       # (value)
        # 发声队列(schema.sound.SoundQueue, impl 注入; None = 静音)
        self.sound: SoundQueue | None = None
        # BGM 事件(impl 帧末收走): ("music_file", name) / ("fadeout", 秒)
        self.bgm_events: list[tuple] = []
        # 敌人退场事件(渲染层帧末收走, 只增不改行为):
        # (id(state), x, y, life, death_anm, is_boss) —— 死亡爆炸特效触发用
        # (C++ EnemyManager.cpp:1017 life<=0 && deathAnm1>=0 → SpawnParticles)
        self.gone_events: list[tuple] = []
        # 震屏事件 (BombEffects::RegisterChain(1,...) 注册点, 元素为
        # (duration, amp_start, amp_end); impl 帧末收进 frame_shakes, view 衰减)
        self.shake_events: list[tuple[int, int, int]] = []

    def _play_sound(self, idx: int) -> None:
        if self.sound is not None:
            self.sound.play(idx)

    # ---- 每帧同步(impl 在跑时间轴/敌人前调用) ----
    def frame_update(self, *, player_pos: Vec2, difficulty: int, rank: int,
                     power: float, shottype: int, spellcard_active: bool,
                     frozen: bool, bomb_in_use: bool = False) -> None:
        w = self.world
        w.player_pos.set(player_pos.x, player_pos.y, 0.0)
        w.difficulty = difficulty
        w.rank = rank
        w.current_power = int(power)
        w.player_shottype = shottype
        w.spellcard_active = spellcard_active
        self.power = power
        self.frozen = frozen
        self.bomb_in_use = bomb_in_use

    # ---- 弹幕 ----
    def spawn_bullet_pattern(self, props: EnemyBulletShooter) -> None:
        if props.count1 <= 0 or props.count2 <= 0:
            return
        if len(self.bullets) >= _MAX_BULLETS:
            return
        pos = Vec2(props.pos.x, props.pos.y)
        try:
            aim = Aim(props.aim_mode)
        except ValueError:
            aim = Aim.RING_ABSOLUTE
        base = props.angle1
        if aim in _AIMED_MODES:
            base += angle_to(pos, self.bullets.player_pos)
        cmds = []
        for c in props.commands:
            if not c.type:
                continue
            try:
                t = CmdFlag(c.type)
            except ValueError:
                continue
            cmds.append(BulletCommand(t, speed=c.speed, angle=c.angle,
                                      duration=c.duration, loop=c.loop_count,
                                      flag=c.flag))
        self.bullets.fire(Burst(pos, base, aim, props.count1, props.count2,
                                props.speed1, props.speed2, props.angle2,
                                sprite=props.sprite, sprite_offset=props.sprite_offset,
                                commands=tuple(cmds),
                                flags=props.flags))
        if props.flags & 0x200:
            # 发弹音 (BulletManager.cpp:611-615, SpawnBulletPattern 尾部)
            self._play_sound(props.sound_idx)

    # ---- 激光(句柄 = Laser 对象) ----
    def spawn_laser_pattern(self, props: EnemyLaserShooter):
        if len(self.lasers.lasers) >= _MAX_LASERS:
            return None
        pos = Vec2(props.pos.x, props.pos.y)
        angle = props.angle1
        if props.type == 0:  # C: type 0 (MOVING) 出生即瞄玩家
            angle = angle_to(pos, self.bullets.player_pos) + angle
        laser = Laser(pos=pos, angle=angle, width=props.width, speed=props.speed1,
                      start_time=props.start_time,
                      hitbox_start_time=props.hitbox_start_time,
                      duration=props.duration, end_time=props.end_time,
                      hitbox_end_time=props.hitbox_end_time,
                      start_length=props.start_length, flags=props.flags,
                      color=props.sprite_offset)
        laser.offset_a = props.start_offset
        laser.offset_b = props.end_offset
        self.lasers.lasers.append(laser)
        return laser

    def laser_set_angle(self, handle: Laser, angle: float) -> None:
        handle.angle = angle

    def laser_add_angle(self, handle: Laser, delta: float) -> None:
        handle.angle += delta

    def laser_aim_at_player(self, handle: Laser, offset: float) -> None:
        handle.angle = angle_to(handle.pos, self.bullets.player_pos) + offset

    def laser_set_pos(self, handle: Laser, pos: Vec3) -> None:
        handle.pos = Vec2(pos.x, pos.y)

    def laser_set_hide_warning(self, handle: Laser, v: int) -> None:
        handle.hide_warning = bool(v)

    def laser_in_use(self, handle: Laser) -> bool:
        return handle.in_use

    def laser_stop(self, handle: Laser) -> None:
        handle.state = LaserState.DESPAWNING
        handle.timer = 0

    def laser_set_start_length(self, handle: Laser, v: float) -> None:
        handle.start_length = v

    def laser_set_offsets(self, handle: Laser, start: float, end: float) -> None:
        handle.offset_a = start
        handle.offset_b = end

    # ---- 敌人 ----
    def spawn_enemy(self, sub_id: int, pos: Vec3, life: int, item_drop: int,
                    score: int, mirror: int, context_args: EclContextArgs
                    ) -> EclEnemy | None:
        """EnemyManager::SpawnEnemy(Ex): 立即跑一帧 RunEcl, 失败则不登记。"""
        machine = self.ecl_machine_cls(self.file, world=self.world, host=self)
        e = EclEnemy(machine, host=self)
        st = machine.enemy
        st.mirror = mirror
        if life >= 0:
            st.life = life
        st.pos = pos.copy()
        st.prev_pos = pos.copy()
        machine.call_sub(sub_id)
        machine.current.args = context_args.clone()
        self.enemy_by_state[id(st)] = e  # 先登记: 首帧 sub 可能即 SET_BOSS
        if not machine._run_ecl():  # C: RunEcl 出错 → active=0, 不入场
            self.enemy_by_state.pop(id(st), None)
            return None
        st.item_drop = item_drop
        if score >= 0:
            st.score = score
        st.max_life = st.life
        self.enemies.add(e)
        return e

    def on_enemy_gone(self, e: EclEnemy) -> None:
        """EclEnemy despawn/击坠时回调: 清登记, boss 槽联动(时间轴 op12 等退场)。"""
        st = e.state
        self.enemy_by_state.pop(id(st), None)
        if 0 <= st.boss_id < 8 and self.world.bosses[st.boss_id] is st:
            self.world.bosses[st.boss_id] = None
        # 渲染层死亡特效用(只读记录; C++ 在 life<=0 死亡分支播 deathAnm1/2)
        self.gone_events.append((id(st), st.pos.x, st.pos.y, st.life,
                                 st.death_anm, bool(st.is_boss)))

    def clear_field(self, source: EclEnemy) -> None:
        """回调切阶段时的清场 (C: 非 boss 敌 life=0, !canDie 的跑死亡回调)。"""
        for e in self.enemies.all():
            if e is source or not e.alive or e.is_boss:
                continue
            e.life = 0
            if isinstance(e, EclEnemy) and not e.state.can_die \
                    and e.state.death_callback_sub >= 0:
                e._run_death_callback()

    def on_timer_callback(self, e: EclEnemy) -> None:
        """Enemy::HandleTimerCallback 的世界侧: 清场恒做; 捕获失败记账透出。"""
        self.clear_field(e)
        if self.on_spellcard_timeout is not None:
            self.on_spellcard_timeout(e.state)

    def remove_all_enemies(self, score_max: int, score_min: int) -> int:
        """EnemyManager::RemoveAllEnemies: 跳过 boss; isProjectile 掉弹消点并累计分。"""
        total = score_min
        popup = 2000
        for e in self.enemies.all():
            if not e.alive or e.is_boss:
                continue
            e.life = 0
            if isinstance(e, EclEnemy):
                if e.state.is_projectile:
                    self.items.spawn(e.pos, ItemType.POINT_BULLET, power=self.power)
                    total += popup
                    popup = min(popup + 30, score_max)
                if not e.state.can_die and e.state.death_callback_sub >= 0:
                    e._run_death_callback()
        return total

    # ---- 道具/清弹 ----
    def spawn_item(self, pos: Vec3, item_type: int) -> None:
        try:
            t = ItemType(item_type)
        except ValueError:
            return
        if t == ItemType.NO_ITEM:
            return
        self.items.spawn(Vec2(pos.x, pos.y), t, power=self.power)

    def remove_all_bullets(self, spawn_items: bool) -> None:
        """BulletManager::RemoveAllBullets: spawn_items 时弹转弹消点道具;
        连带激光 (BulletManager.cpp:439-471): flags&4 豁免(param 0/1 均 !=10),
        其余进 DESPAWNING, spawn_items 时沿线每 32px 出弹消点。"""
        for b in self.bullets.alive():
            if spawn_items:
                self.items.spawn(b.pos, ItemType.POINT_BULLET, power=self.power)
            b.dead = True
        self.lasers.remove_all(
            spawn_items=spawn_items, skip_flag4=True,
            spawn_item=self._spawn_point_bullet if spawn_items else None)

    def _spawn_point_bullet(self, pos: Vec2) -> None:
        """弹消点道具 (C RemoveAllBullets/DespawnBullets 的 this->itemType)。"""
        self.items.spawn(pos, ItemType.POINT_BULLET, power=self.power)

    def remove_bullets_in_radius(self, pos: Vec3, radius: float) -> None:
        """BulletManager::RemoveBulletsInRadius: 半径内弹转弹消点道具。"""
        r2 = radius * radius
        for b in self.bullets.alive():
            dx = b.pos.x - pos.x
            dy = b.pos.y - pos.y
            if dx * dx + dy * dy <= r2:
                self.items.spawn(b.pos, ItemType.POINT_BULLET, power=self.power)
                b.dead = True

    # ---- Boss/符卡(事件透出, 记账在 impl/boss.py) ----
    def set_boss(self, idx: int, enemy: Optional[EclEnemyState]) -> None:
        if self.on_set_boss is not None:
            self.on_set_boss(idx, enemy)

    def set_boss_health(self, idx: int, cur: int, max_life: int, color: int) -> None:
        self.boss_health = (idx, cur, max_life, color)

    def set_boss_life_markers(self, n: int) -> None:
        self.boss_life_markers = n

    def begin_spellcard(self, enemy: EclEnemyState, gui_id: int,
                        spellcard_idx: int, name: str) -> None:
        self.world.spellcard_active = True
        self.spellcard_idx = spellcard_idx
        if self.on_begin_spellcard is not None:
            self.on_begin_spellcard(enemy, gui_id, spellcard_idx, name)

    def end_spellcard(self, enemy: EclEnemyState) -> None:
        self.world.spellcard_active = False
        self.spellcard_idx = -1
        if self.on_end_spellcard is not None:
            self.on_end_spellcard(enemy)

    def boss_active(self, idx: int) -> bool:
        b = self.world.bosses[idx & 7]
        return b is not None and bool(b.active)

    # ---- 系统/表现 ----
    def play_sound(self, idx: int) -> None:
        """ECL PLAY_SOUND(105): 原样透传音效 idx (EclManager.cpp:1662-1664)。"""
        self._play_sound(idx)

    def msg_read(self, msg_id: int) -> None:
        """时间轴 op8 (EnemyManager.cpp:332): MsgRead(arg0 + character*10)。

        C MsgRead 同时清场: RemoveAllBullets(1) → 弹转弹消点、
        RemoveAllEnemies(0,0)(跳过 boss)、RemoveAllItems()。
        """
        self.last_msg_id = msg_id
        vm = self.msg_vm
        if vm is None:
            return
        vm.read(msg_id + self.msg_character * 10)
        if vm.has_current_msg_idx():
            self.remove_all_bullets(True)      # 弹转道具…
            self.remove_all_enemies(0, 0)      # …随即被下一行清掉(同 C 顺序)
            self.items.remove_all_items()

    def msg_wait(self) -> bool:
        """时间轴 op9: 消息未读完则停轴 (Gui::MsgWait, 含 APPEAR_ENEMY 放行窗)。"""
        if self.msg_vm is None:
            return False
        return self.msg_vm.msg_wait()

    def set_power(self, value: int) -> None:
        if self.on_set_power is not None:
            self.on_set_power(value)

    def add_cherry_plus(self, value: int) -> None:
        if self.on_add_cherry_plus is not None:
            self.on_add_cherry_plus(value)

    def run_ex_instr(self, idx: int, enemy: EclEnemyState, instr,
                     ctx=None) -> bool:
        """24 条 boss 特技 (EnemyEclInstr.cpp g_EclExInstr) 的分派。

        语义逐条照抄 C++(各方法注释标行号); BombEffects/音效/音乐等视觉部分
        留注释不接。全部 24 条在 8 个 ecldata 中均有真实使用(见文件头注释)。
        """
        fn = self._EX_DISPATCH.get(idx)
        if fn is None:
            return False
        fn(self, enemy, instr, ctx)
        return True

    # ---- ExIns 实现 (idx = g_EclExInstr 下标) ----

    @staticmethod
    def _ex_arg1(instr, default: int = 0) -> int:
        """C 直接读 instr->args[1].i (不做变量解析)。"""
        return instr.arg_int(1) if instr is not None and len(instr.args) > 1 else default

    def _ex0_set_pos_to_boss(self, enemy: EclEnemyState, instr, ctx) -> None:
        # EnemyEclInstr.cpp:55 ExInsSetPosToBoss
        boss = self.world.bosses[self._ex_arg1(instr) & 7]
        if boss is None:
            return
        enemy.pos = boss.pos.copy()
        enemy.axis_speed = boss.axis_speed.copy()
        enemy.angle = boss.angle
        enemy.disable_movement = 1

    def _ex1_alice_curve_bullets(self, enemy: EclEnemyState, instr, ctx) -> None:
        # EnemyEclInstr.cpp:66 ExInsAliceCurveBullets (BombEffects 闪屏 pulse 不接)
        sel = self._ex_arg1(instr)
        # EnemyEclInstr.cpp:73 震屏 RegisterChain(1,30,12,0)
        self.shake_events.append((30, 12, 0))
        rng = self.world.rng
        for b in self.bullets.alive():
            if b.state2 != 0:
                continue
            if sel == 1 and b.sprite_offset != 8:
                continue
            if sel == 2 and b.sprite_offset != 4:
                continue
            if b.sprite_offset == 2:
                turn = -math.pi / (rng.in_range(0.0, 60.0) + 180.0)
            elif b.sprite_offset == 6 or b.sprite_offset == 8:
                turn = math.pi / (rng.in_range(0.0, 60.0) + 180.0)
            elif b.sprite_offset == 4:
                turn = -math.pi / (rng.in_range(0.0, 60.0) + 180.0)
            else:
                continue  # C 里 local_10 未初始化(ZUN bloat), 这里按跳过处理
            b.speed = 0.3
            b.commands = []  # memset(bullet->commands, 0, ...)
            b.cur_cmd_idx = 0
            if self.world.difficulty < 3:
                b.set_command(0, BulletCommand(CmdFlag.TARGET_ANGLE,
                                               speed=0.016666668, angle=turn,
                                               duration=60))
            else:
                b.set_command(0, BulletCommand(CmdFlag.TARGET_ANGLE,
                                               speed=0.005263158, angle=turn,
                                               duration=240))
            b.state2 = 1

    def _ex2_turn_bullets_into_other_bullets(self, enemy: EclEnemyState,
                                             instr, ctx) -> None:
        # EnemyEclInstr.cpp:127 ExInsTurnBulletsIntoOtherBullets
        sel = self._ex_arg1(instr)
        if sel == 0:
            # EnemyEclInstr.cpp:139 震屏 RegisterChain(1,32,12,0) (同帧闪屏 pulse 不接)
            self.shake_events.append((32, 12, 0))
        radius = (128.0, 192.0, 256.0, 999.0)[sel & 3]
        rng = self.world.rng
        for b in list(self.bullets.alive()):  # 快照: 循环内 spawn
            if b.sprite_offset != 2:
                continue
            dx = enemy.pos.x - b.pos.x
            dy = enemy.pos.y - b.pos.y
            if math.sqrt(dx * dx + dy * dy) >= radius:
                continue
            props = EnemyBulletShooter(
                sprite=0, sprite_offset=6,
                pos=Vec3(b.pos.x, b.pos.y, 0.0),
                angle1=0.0, angle2=-math.pi, speed1=0.7,
                count1=2, count2=1, flags=2, aim_mode=6)
            props.commands[0] = BulletCommandData(
                type=int(CmdFlag.TARGET_VEL), duration=180,
                speed=rng.in_range(0.0, 0.005) + 0.013, angle=1.5707964)
            self.spawn_bullet_pattern(props)
            b.dead = True  # bullet->Initialize()

    def _ex4_despawn_large_bullet_and_save_pos(self, enemy: EclEnemyState,
                                               instr, ctx) -> None:
        # EnemyEclInstr.cpp:196 ExInsDespawnLargeBulletAndSavePos
        if ctx is None:
            return
        ctx.args.float_vars1[0] = -999.0
        for b in self.bullets.alive():
            if bullet_sprite_height(b.sprite, b.sprite_offset) >= 60.0:
                ctx.args.float_vars1[0] = b.pos.x
                ctx.args.float_vars1[1] = b.pos.y
                # C 另有 SpawnParticles(2, pos, 1, white), 视觉不接
                b.dead = True
                break

    def _ex5_copy_main_boss_movement(self, enemy: EclEnemyState, instr, ctx) -> None:
        # EnemyEclInstr.cpp:227 ExInsCopyMainBossMovement
        boss = self.world.bosses[0]
        if boss is None:
            return
        enemy.move_interp_start_pos = boss.pos.copy()
        enemy.move_radius = boss.move_radius
        enemy.move_angular_velocity = boss.move_angular_velocity

    def _ex6_split_bullets_or_shoot_backwards(self, enemy: EclEnemyState,
                                              instr, ctx) -> None:
        # EnemyEclInstr.cpp:242 ExInsSplitBulletsOrShootBackwards
        sel = self._ex_arg1(instr)
        diff = self.world.difficulty
        for b in list(self.bullets.alive()):
            if not ((sel == 0 and b.sprite_offset == 6)
                    or (sel == 1 and b.sprite_offset == 15)
                    or (sel == 2 and b.sprite_offset == 2)):
                continue
            props = EnemyBulletShooter(
                sprite=6, sprite_offset=15,
                pos=Vec3(b.pos.x, b.pos.y, 0.0),
                angle1=add_normalize_angle(b.angle, math.pi),
                angle2=0.5235988, speed1=b.speed * 1.1,
                count1=4 if diff < 3 else 2, count2=1, flags=2, aim_mode=1)
            if diff >= 3:
                props.angle2 = 1.5707964
            props.commands[0] = BulletCommandData(
                type=int(CmdFlag.SPAWN_DELAY), duration=130)
            if sel == 0:
                props.flags = 0x2002
            elif sel == 1:
                # C quirk: 非 Lunatic 时 flags=2 清掉 0x2000, spawnDelay 命令
                # 被 RunCommands 跳过; Python fire 会按命令重 OR 回来, 存小异
                props.flags = 2 if diff != 3 else 0x2002
                props.sprite_offset = 2
            elif sel == 2:
                props.flags = 2
                props.sprite_offset = 10
            self.spawn_bullet_pattern(props)
            props.angle2 = 1.0471976
            if sel == 0:
                props.flags = 0x2000
            elif sel == 1:
                props.flags = 0 if diff != 3 else 0x2000
            else:
                props.flags = 0
            props.speed1 = b.speed * 0.7
            props.count1 = 2
            self.spawn_bullet_pattern(props)
            props.speed1 = b.speed * 0.85
            props.count1 = 1
            self.spawn_bullet_pattern(props)
            b.dead = True

    @staticmethod
    def _point_in_rotated_rect(px: float, py: float, cx: float, cy: float,
                               sx: float, sy: float, pivot: PosLike,
                               sine: float, cosine: float) -> bool:
        # EnemyEclInstr.cpp:336 IsPointInRotatedRect
        dx = px - pivot.x
        dy = py - pivot.y
        rx = dx * cosine + dy * sine + pivot.x
        ry = dy * cosine - dx * sine + pivot.y
        return (cx - sx / 2.0 <= rx <= cx + sx / 2.0
                and cy - sy / 2.0 <= ry <= cy + sy / 2.0)

    def _ex7_reflect_bullets_from_lasers(self, enemy: EclEnemyState,
                                         instr, ctx) -> None:
        # EnemyEclInstr.cpp:366 ExInsReflectBulletsFromLasers
        for i, laser in enumerate(self.lasers.lasers):
            if not laser.in_use or enemy.timer % 2 != i:
                continue
            if laser.state >= LaserState.DESPAWNING:
                continue
            size_x = laser.offset_b - laser.offset_a
            cx = size_x / 2.0 + laser.offset_a + laser.pos.x
            cy = laser.pos.y
            sine, cosine = math.sin(laser.angle), math.cos(laser.angle)
            for b in self.bullets.alive():
                if not self._point_in_rotated_rect(
                        b.pos.x, b.pos.y, cx, cy, size_x, laser.width,
                        laser.pos, sine, cosine):
                    continue
                if b.state2 > 0:
                    b.state2 -= 1
                if b.state2 != 0:
                    continue
                if b.speed > 0.5:
                    b.speed -= 0.1
                dot = cosine * b.vel.y + sine * b.vel.x
                b.angle = add_normalize_angle(
                    laser.angle, 1.5707964 if dot >= 0.0 else -1.5707964)
                b.vel = Vec2.from_angle(
                    b.angle, self.world.framerate_multiplier * b.speed)
                b.state2 = 10
                b.sprite = 5  # bulletTypeTemplates[5]; C 另 SetActiveSprite 换皮
                b.size = bullet_type_size(5)

    def _ex8_shoot_bullets_along_laser(self, enemy: EclEnemyState,
                                       instr, ctx) -> None:
        # EnemyEclInstr.cpp:454 ExInsShootBulletsAlongLaser
        rng = self.world.rng
        diff = self.world.difficulty
        for i, laser in enumerate(self.lasers.lasers):
            if not laser.in_use or enemy.timer % 3 != i % 3:
                continue
            if laser.state >= LaserState.DESPAWNING:
                continue
            size_x = laser.offset_b - laser.offset_a
            cx = size_x / 2.0 + laser.offset_a + laser.pos.x
            cy = laser.pos.y
            sine, cosine = math.sin(laser.angle), math.cos(laser.angle)
            dir_x, dir_y = -sine, cosine
            for b in self.bullets.alive():
                if b.state2 == i + 1 or b.state2 < 0:
                    continue
                if not self._point_in_rotated_rect(
                        b.pos.x, b.pos.y, cx, cy, size_x, laser.width * 1.5,
                        laser.pos, sine, cosine):
                    continue
                if diff < 2:
                    b.speed *= rng.in_range(0.0, 0.3) + 0.7
                else:
                    b.speed *= rng.in_range(0.0, 0.4) + 0.8
                dot = dir_x * b.vel.x + dir_y * b.vel.y
                if dot >= 0.0:
                    b.vel = Vec2(dir_x, dir_y)
                else:
                    b.vel = Vec2(-dir_x, -dir_y)
                b.sprite = 5  # bulletTypeTemplates[5], 换皮注释同 ex7
                b.size = bullet_type_size(5)
                b.angle = math.atan2(b.vel.y, b.vel.x)
                b.vel = Vec2.from_angle(b.angle, b.speed)
                b.state2 = -1 if diff < 2 else i + 1

    def _ex9_effect1e_accel(self, enemy: EclEnemyState, instr, ctx) -> None:
        # EnemyEclInstr.cpp:549 ExInsEffect1eAccel
        # :551 震屏 BombEffects::RegisterChain(1,80,8,0);
        # EffectManager::ModifyEffect1eAcceleration 为特效系统表现, 未移植, 无逻辑效果
        self.shake_events.append((80, 8, 0))

    def _ex10_youmu_set_game_speed(self, enemy: EclEnemyState, instr, ctx) -> None:
        # EnemyEclInstr.cpp:556 ExInsYoumuSetGameSpeed
        # (spellcardVms pendingInterrupt=2 / sprite 608-623 换帧为表现侧, 不接)
        mult = 1.0 / float(self._ex_arg1(instr, 1) or 1)
        self.world.framerate_multiplier = mult
        self.bullets.time_scale = mult
        for b in self.bullets.alive():
            b.vel = b.vel * mult

    def _ex11_youmu_restore_game_speed(self, enemy: EclEnemyState,
                                       instr, ctx) -> None:
        # EnemyEclInstr.cpp:585 ExInsYoumuRestoreGameSpeed
        # (C 先置 1/arg 再强制回 1.0 + forceIntegerTimer, 终值恒 1.0)
        mult = self.world.framerate_multiplier
        fps = 1.0 / mult if mult else 1.0
        for b in self.bullets.alive():
            b.vel = b.vel * fps
        self.world.framerate_multiplier = 1.0
        self.bullets.time_scale = 1.0

    def _burst_large_bullets(self, enemy: EclEnemyState, instr, ctx,
                             count_by_diff: tuple[int, ...], y_range: float,
                             sprite_table: tuple[tuple[int, int], ...]) -> None:
        # EnemyEclInstr.cpp:621/853 ExInsBurstLargeBullets{,2} 公共部分
        # (BombEffects 不接)
        rng = self.world.rng
        n = count_by_diff[min(self.world.difficulty, 3)]
        sel = self._ex_arg1(instr)
        for b in list(self.bullets.alive()):
            if bullet_sprite_height(b.sprite, b.sprite_offset) <= 48.0:
                continue
            if not (enemy.pos.y - y_range < b.pos.y < enemy.pos.y + y_range):
                continue
            for j in range(n):
                sprite, offset = sprite_table[rng.int_below(3)]
                if sel == 0:
                    angle1 = rng.in_range(0.0, 4.712389) - 1.5707964
                else:
                    angle1 = add_normalize_angle(rng.in_range(0.0, 4.712389),
                                                 0.7853982)
                props = EnemyBulletShooter(
                    sprite=sprite, sprite_offset=offset,
                    pos=Vec3(b.pos.x + rng.in_range(0.0, 32.0) - 16.0,
                             b.pos.y + rng.in_range(0.0, 32.0) - 16.0, 0.0),
                    angle1=angle1, speed1=0.1, count1=1, count2=1,
                    flags=2 if j & 1 else 0, aim_mode=1)
                props.commands[0] = BulletCommandData(
                    type=int(CmdFlag.TARGET_ANGLE), duration=100, angle=0.0,
                    speed=rng.in_range(0.0, 0.008) + 0.01)
                self.spawn_bullet_pattern(props)
            b.dead = True

    def _ex12_burst_large_bullets(self, enemy: EclEnemyState, instr, ctx) -> None:
        # EnemyEclInstr.cpp:621: 数量 10/18/22/25, y 窗 ±64 (H/L  ±48)
        diff = self.world.difficulty
        self._burst_large_bullets(
            enemy, instr, ctx, (10, 18, 22, 25),
            64.0 if diff < 2 else 48.0,
            ((0, 2), (3, 2), (7, 1)))

    def _ex13_youmu_curve_bullets_below(self, enemy: EclEnemyState,
                                        instr, ctx) -> None:
        # EnemyEclInstr.cpp:696 ExInsYoumuCurveBulletsBelow
        # (C 用弹槽下标 i 的奇偶选转向; Python 弹池无空槽, 用存活序号代替)
        for i, b in enumerate(self.bullets.alive()):
            if b.state2 != 0:
                continue
            if not (enemy.pos.y < b.pos.y < 352.0
                    and enemy.pos.x - 16.0 < b.pos.x < enemy.pos.x + 16.0):
                continue
            b.set_command(0, BulletCommand(
                CmdFlag.TARGET_ANGLE, duration=160,
                angle=0.05235988 if i & 1 else -0.05235988,
                speed=-b.speed / 180.0))
            b.state2 = 1

    def _ex14_youmu_redirect_bullets_to_player(self, enemy: EclEnemyState,
                                               instr, ctx) -> None:
        # EnemyEclInstr.cpp:725 ExInsYoumuRedirectBulletsToPlayer (BombEffects 不接)
        for b in self.bullets.alive():
            if b.state2 != 1:
                continue
            b.set_command(0, BulletCommand(
                CmdFlag.TARGET_VEL, duration=90, speed=0.026666667,
                angle=angle_to(b.pos, self.bullets.player_pos)))
            b.clear_command(1)
            b.state2 = 2

    def _ex15_flash_screen(self, enemy: EclEnemyState, instr, ctx) -> None:
        # EnemyEclInstr.cpp:751 ExInsFlashScreen —— BombEffects 闪屏, 纯视觉不接
        pass

    def _ex16_yuyuko_transform_butterfly_bullets(self, enemy: EclEnemyState,
                                                 instr, ctx) -> None:
        # EnemyEclInstr.cpp:757 ExInsYuyukoTransformButterflyBullets
        # 蝶弹 = sprite 8 (活动 sprite 632-639, etama.anm 实测)
        speed = ctx.args.float_vars1[1] if ctx is not None else 0.0
        for b in list(self.bullets.alive()):
            if b.state2 != 0 or b.sprite != 8 or not 0 <= b.sprite_offset <= 7:
                continue
            props = EnemyBulletShooter(
                sprite=0, sprite_offset=6,
                pos=Vec3(b.pos.x, b.pos.y, 0.0),
                angle1=add_normalize_angle(b.angle, math.pi),
                angle2=0.3926991, speed1=speed, count1=5, count2=1,
                flags=2, aim_mode=1)
            self.spawn_bullet_pattern(props)

    def _ex17_yuyuko_butterfly_spawn_enemy(self, enemy: EclEnemyState,
                                           instr, ctx) -> None:
        # EnemyEclInstr.cpp:791 ExInsYuyukoButterflySpawnEnemy (BombEffects 不接)
        if ctx is None:
            return
        args = ctx.args.clone()
        angle_offset = -math.pi
        for b in list(self.bullets.alive()):
            idx632 = bullet_active_sprite_idx(b.sprite, b.sprite_offset)
            if b.state2 == 0 and idx632 == 636:
                args.float_vars1[0] = b.angle
                args.float_vars1[7] = angle_offset
                angle_offset += 0.7853982
                self.spawn_enemy(ctx.sub_id + 1, Vec3(b.pos.x, b.pos.y, 0.0),
                                 1, -2, 10, 0, args.clone())
                b.dead = True
            elif 632 <= idx632 <= 639:
                b.dead = True

    def _ex18_yuyuko_count_butterfly_bullets(self, enemy: EclEnemyState,
                                             instr, ctx) -> None:
        # EnemyEclInstr.cpp:829 ExInsYuyukoCountButterflyBullets
        if ctx is None:
            return
        n = 0
        for b in self.bullets.alive():
            if b.state2 == 0 \
                    and bullet_active_sprite_idx(b.sprite, b.sprite_offset) == 636:
                n += 1
        ctx.args.int_vars1[0] = n

    def _ex19_yuyuko_fade_out_music(self, enemy: EclEnemyState, instr, ctx) -> None:
        # EnemyEclInstr.cpp:919 —— Supervisor::FadeOutMusic(3.0)
        self.bgm_events.append(("fadeout", 3.0))

    def _ex20_yuyuko_play_resurrection_bgm(self, enemy: EclEnemyState,
                                           instr, ctx) -> None:
        # EnemyEclInstr.cpp:925 —— PlayLoadedAudio(2) 失败回退 PlayAudio("bgm/th07_13b.mid")
        self.bgm_events.append(("music_file", "th07_13b.mid"))

    def _ex21_burst_large_bullets2(self, enemy: EclEnemyState, instr, ctx) -> None:
        # EnemyEclInstr.cpp:853: 数量恒 15, y 窗 Hard ±128, 其余 ±180
        diff = self.world.difficulty
        self._burst_large_bullets(
            enemy, instr, ctx, (15, 15, 15, 15),
            128.0 if diff == 2 else 180.0,
            ((0, 4), (3, 4), (7, 2)))

    def _ex22_spawn_bullets_with_dir_change(self, enemy: EclEnemyState,
                                            instr, ctx) -> None:
        # EnemyEclInstr.cpp:936 ExInsSpawnBulletsWithDirChange
        if enemy.timer % 3 == 0:
            return
        rng = self.world.rng
        odd = enemy.timer % 2 != 0
        for b in list(self.bullets.alive()):
            if (b.ex_flags & 0x40) or b.pos.y >= 320.0 \
                    or bullet_sprite_height(b.sprite, b.sprite_offset) <= 60.0:
                continue
            props = EnemyBulletShooter(
                sprite=1 if odd else 3,
                sprite_offset=6 if b.sprite_offset == 1 else 2,
                pos=Vec3(b.pos.x, b.pos.y, 0.0),
                angle1=rng.in_range(0.0, 6.2831855) - math.pi, angle2=-math.pi,
                speed1=1.2 if odd else 0.8,
                count1=1 if odd else 2, count2=1, flags=0x208, aim_mode=3)
            if odd:
                props.commands[0] = BulletCommandData(
                    type=int(CmdFlag.DIR_CHANGE_AIM), duration=60, loop_count=1,
                    speed=0.0, angle=3.1)
            self.spawn_bullet_pattern(props)

    def _ex23_spawn_bullets_with_dir_change2(self, enemy: EclEnemyState,
                                             instr, ctx) -> None:
        # EnemyEclInstr.cpp:1005 ExInsSpawnBulletsWithDirChange2
        if enemy.timer % 3 == 2:
            return
        rng = self.world.rng
        mod3 = enemy.timer % 3
        for b in list(self.bullets.alive()):
            if (b.ex_flags & 0x40) or b.pos.y >= 320.0 \
                    or bullet_sprite_height(b.sprite, b.sprite_offset) <= 60.0:
                continue
            props = EnemyBulletShooter(
                sprite=1 if mod3 else 3,
                sprite_offset=10 if b.sprite_offset == 2 else 13,
                pos=Vec3(b.pos.x, b.pos.y, 0.0),
                angle1=rng.in_range(0.0, 6.2831855) - math.pi, angle2=-math.pi,
                speed1=1.2 if mod3 else 0.8,
                count1=1, count2=1, flags=0x208, aim_mode=3)
            if mod3:
                props.commands[0] = BulletCommandData(
                    type=int(CmdFlag.DIR_CHANGE_AIM), duration=40, loop_count=1,
                    speed=0.0, angle=2.9)
            self.spawn_bullet_pattern(props)

    # idx → 实现 (g_EclExInstr 下标; idx 3 NoOp 在 EclMachineBase._run_ex 已短路)
    _EX_DISPATCH = {
        0: _ex0_set_pos_to_boss,
        1: _ex1_alice_curve_bullets,
        2: _ex2_turn_bullets_into_other_bullets,
        3: lambda self, enemy, instr, ctx: None,
        4: _ex4_despawn_large_bullet_and_save_pos,
        5: _ex5_copy_main_boss_movement,
        6: _ex6_split_bullets_or_shoot_backwards,
        7: _ex7_reflect_bullets_from_lasers,
        8: _ex8_shoot_bullets_along_laser,
        9: _ex9_effect1e_accel,
        10: _ex10_youmu_set_game_speed,
        11: _ex11_youmu_restore_game_speed,
        12: _ex12_burst_large_bullets,
        13: _ex13_youmu_curve_bullets_below,
        14: _ex14_youmu_redirect_bullets_to_player,
        15: _ex15_flash_screen,
        16: _ex16_yuyuko_transform_butterfly_bullets,
        17: _ex17_yuyuko_butterfly_spawn_enemy,
        18: _ex18_yuyuko_count_butterfly_bullets,
        19: _ex19_yuyuko_fade_out_music,
        20: _ex20_yuyuko_play_resurrection_bgm,
        21: _ex21_burst_large_bullets2,
        22: _ex22_spawn_bullets_with_dir_change,
        23: _ex23_spawn_bullets_with_dir_change2,
    }
