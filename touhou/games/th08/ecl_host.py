"""TH08(东方永夜抄)ECL 宿主 —— Th08GameEclHost: 把 EclHost 钩子接到游戏世界。

骨架(时刻/EX dispatch)在阶段 2 建好; 本阶段(3 单 A)按 th07 的
games/th07/ecl_host.py 模式丰满:
- 构造契约 ``(ecl_file, world, *, enemies, bullets, lasers, items,
  ecl_machine_cls)`` 容器注入(与 th07 GameEclHost 同形; 兼容旧的
  ``Th08GameEclHost(world)`` 裸构造, 容器缺省时世界效果方法安全 no-op);
- ``frame_update`` 每帧世界快照(自机位置/难度/rank/火力/机体/符卡/
  冻结/炸弹 + th08 专属: 人妖形态 player_is_youkai);
- spawn_bullet_pattern/spawn_laser_pattern/spawn_enemy/spawn_familiar/
  spawn_item/remove_all_* 按 th08 反编译语义(BulletManager.cpp/
  EnemyManager.cpp, 各方法注释标行号);
- 使魔父链: spawn_familiar 挂父链尾(EclRunLow.inl:737-929),
  VM 变量 10096(CountParentChain, EclOperandsInt.cpp:125-129)经
  count_parent_chain/attached_parent 读取;
- 32 条 EX 指令(EclGlobals.cpp:65-98): 纯状态/纯逻辑按 EclExIns.cpp
  实现; 依赖 view 特效(屏闪 pulse/背景染色/结界绘制/旋转激光判定依赖
  anm VM rotation/符卡 cut-in)的留标记注释;
- 消弹核对结论(th08 BulletManager.cpp vs th07):
  RemoveAllBullets(mode) (:484-561) mode!=4 → 弹转 cancelItemType
  (=6 ITEM_POINT_STAR, BulletManager.cpp:49) 道具(state=mode)、
  flags&4 激光豁免且沿线每 32px 出道具; mode==4 → 纯 despawn 无道具、
  激光不豁免; 末尾恒 spawnSuppressionFrames=10 (:561, th07 同位的
  screenClearTime=10)。ClearBulletsForTransition()=RemoveAllBullets(1)
  (Spellcard.cpp:883-886)。RemoveBulletsInRadius (:663-681): 半径内弹转
  ITEM_POINT_STAR 吸附道具, 不碰激光, 无 suppression。
  与 th07 的语义差: 转道具类型是时刻星(6)而非弹消点(th07 也是 6 但类型
  体系不同), th07 的 mode==10(符卡超时: 激光不豁免无道具)在 th08 由
  mode==4 承担。

事件透出(震屏/BGM/敌人退场/音效)照 th07 形态, 由 world 帧末收口。
"""

from __future__ import annotations

import math
from typing import Callable, Optional, Protocol, cast

from ...engine.ecl import (
    EclContext,
    EclContextArgs,
    EclEnemyState,
    EclFile,
    EclHost,
    EclInstr,
    EclWorld,
    EnemyBulletShooter,
    EnemyLaserShooter,
    Vec3,
)
from ...engine.bullet_commands import BulletCommand, CmdFlag
from ...engine.bullets import Aim, BulletWorld, Burst
from ...engine.ecl_base import EclMachineBase
from ...engine.enemies import EclEnemy, EnemyHost
from ...engine.lasers import Laser, LaserState, LaserWorld
from ...registry import register_game_hooks
from ...schema.msg import MsgVm
from ...schema.sound import SoundQueue
from ...types import BeginSpellcardHook, EndSpellcardHook, IntHook, SetBossHook
from ...utils import Vec2, add_normalize_angle, angle_to, f32
from .clock import Th08Clock
from .ecl_state import Th08ContextArgs, Th08EclWorld, Th08EnemyState
from .items import STATE_ATTRACT, ItemType, ItemWorld

# C 弹幕上限 (BulletManager::SpawnBulletPattern 的 activeBulletCount >= 0x600 检查)
_MAX_BULLETS = 0x600
# C 激光上限 (SpawnLaserPattern 的 lasers[0x100] 槽位)
_MAX_LASERS = 0x100

# ECL aimed 模式(出生时叠加 angle_to_player); aim_mode = opcode - 96
# (EclDependencies.cpp DispatchShotInstruction), 0/2/4 = SPREAD_AIMED/
# RING_AIMED/RING_SHIFT_AIMED(与 th07 同位)
_AIMED_MODES = (Aim.SPREAD_AIMED, Aim.RING_AIMED, Aim.RING_SHIFT_AIMED)

# 弹上的 EX 触发标记 (BulletManager.hpp:152 BULLET_TRANSFORM_ECL_EX_TRIGGER_MARKER)
_BULLET_FLAG_EX_TRIGGER_MARKER = 0x100000

# Bullet.state2 在 th08 侧的借用编码(th08 ECL/EX 不用 th07 的 state2 语义):
# 结界折跃冷却 = 1..2(zoneTransitionCooldownFrames, EclExIns.cpp:197-200);
# 铃仙冻结相位 = 100(frozen, bulletVm.type 0)/200(mid, type 2)
# (EclExIns.cpp:601-715; 无判定 = collisionDisabled)
_BSTATE_FREEZEN = 100
_BSTATE_FREEZEN_MID = 200


def _args_of(ctx: EclContext) -> Th08ContextArgs:
    """收窄上下文变量区(cast 是运行时 no-op; th08 机器恒产 Th08ContextArgs)。"""
    return cast(Th08ContextArgs, ctx.args)


class Th08HostProto(Protocol):
    """th08 特有的 ECL 宿主接缝(EclHost 通用钩子之外的 15 个)。

    签名/docstring 自 engine/ecl.py 下沉(那里曾是带默认 no-op 的基类方法)。
    运行时鸭子类型不变: VM 侧经 ``cast(Th08HostProto, self.host)`` 收窄
    (ecl_vm._h)。

    注: typing.Protocol 运行时禁止继承具体类, 故不能写成
    ``(EclHost, Protocol)``; 时间轴 runner 用到的基类方法
    (spawn_enemy/msg_read/msg_wait/set_power)在此按原签名重声明。
    """

    # ---- 基类方法重声明(Th08TimelineRunner 消费; 签名照 EclHost) ----

    def spawn_enemy(
        self,
        sub_id: int,
        pos: Vec3,
        life: int,
        item_drop: int,
        score: int,
        mirror: int,
        context_args: EclContextArgs,
    ) -> EclEnemy | None:
        """SpawnEnemy: 返回入场敌人(失败 None)。"""
        ...

    def msg_read(self, msg_id: int) -> None: ...

    def msg_wait(self) -> bool:
        """True = 消息仍在显示(时间轴暂停)。"""
        ...

    def set_power(self, value: int) -> None: ...

    # ---- th08 特有接缝 ----

    def spawn_familiar(
        self,
        kind: int,
        sub_id: int,
        pos: Vec3,
        life: int,
        item_drop: int,
        score: int,
        context_args: EclContextArgs,
        parent: Optional[Th08EnemyState] = None,
    ) -> EclEnemy | None:
        """th08 op90-92 使魔生成(含附着链登记, EclRunLow.inl:737-929)。

        kind = opcode(90 定点 / 91 父偏移 / 92 继承父位置); parent = 调用方
        敌人(父链挂载用)。
        """
        ...

    def call_sub_on_boss(self, boss: EclEnemyState, sub_id: int) -> None:
        """th08 op88: 让别的 boss 压栈并调用 sub(EclRunLow.inl:712-717)。"""
        ...

    def clock_advance(self) -> None:
        """th08 op181: 时刻 +1 单位(封顶 12, EclRunHigh.inl:957-967)。"""
        ...

    def clock_hide(self) -> None:
        """th08 op180: 隐藏时刻表盘(EclRunHigh.inl:956)。"""
        ...

    def show_retry_menu(self) -> None:
        """th08 时间轴 op16: 显示 Retry 菜单(EnemyTimeline.cpp:136-138)。"""
        ...

    def clear_bullets_for_transition(self) -> None:
        """th08 op112/符卡开始: ClearBulletsForTransition(EclRunHigh.inl:789)。"""
        ...

    def set_stage_script_label(self, label: int) -> None:
        """th08 op147: Background.pendingStageScriptLabel(EclRunHigh.inl:711)。"""
        ...

    def start_stage_background_sequence(self) -> None:
        """th08 op179: Gui.StartStageBackgroundSequence(EclRunHigh.inl:955)。"""
        ...

    def set_spellcard_effect_tracking(self, disabled: int, pos: Vec3) -> None:
        """th08 op164: Spellcard 特效跟踪开关+记录向量(EclRunHigh.inl:856-863)。"""
        ...

    def set_bonus_updates_disabled(self, v: int) -> None:
        """th08 op184: Spellcard.SetBonusUpdatesDisabled(EclRunHigh.inl:972)。"""
        ...

    def spawn_alignment_effect(self, kind: int) -> None:
        """th08 op174: 人妖对齐特效(结界光环, EclRunHigh.inl:936-952)。"""
        ...

    def set_last_spell_flags(self) -> None:
        """th08 op176: Last Spell 的 GameManager 标志位操作(EclRunHigh.inl:902-919)。"""
        ...

    def set_spellcard_bonus(self, bonus: int) -> None:
        """th08 op122: 符卡 bonus(EclSpellCardInstructionArgs.bonus @0x10,
        EclDependencies.cpp:18-36)在 begin_spellcard 前传递。"""
        ...

    def count_parent_chain(self, enemy: Th08EnemyState) -> int:
        """th08: CountParentChain(使魔父链节点数, EclOperandsInt.cpp:125-129
        的 10096 变量读源)。"""
        ...

    def attached_parent(self, enemy: Th08EnemyState) -> Optional[Th08EnemyState]:
        """th08: HasAttachedEnemy → parentEnemy(同 10096 判定)。"""
        ...


class Th08NullHost(EclHost):
    """Th08HostProto 的全 no-op 兜底实现(EclMachineTh08 缺省宿主)。

    原 engine/ecl.py EclHost 上的 15 个 th08 默认 no-op 下沉于此:
    保证 th08 VM 在未注入真实宿主时(冒烟/单测)不因缺方法而炸。
    真实世界效果见 Th08GameEclHost。
    """

    def spawn_familiar(
        self,
        kind: int,
        sub_id: int,
        pos: Vec3,
        life: int,
        item_drop: int,
        score: int,
        context_args: EclContextArgs,
        parent: Optional[Th08EnemyState] = None,
    ) -> EclEnemy | None:
        """op90-92 使魔生成接缝(EclRunLow.inl:737-929); 默认无操作。"""
        return None

    def call_sub_on_boss(self, boss: EclEnemyState, sub_id: int) -> None:
        """op88(EclRunLow.inl:712-717); 默认无操作。"""

    def clock_advance(self) -> None:
        """op181(EclRunHigh.inl:957-967); 默认无操作。"""

    def clock_hide(self) -> None:
        """op180(EclRunHigh.inl:956); 默认无操作。"""

    def show_retry_menu(self) -> None:
        """时间轴 op16(EnemyTimeline.cpp:136-138); 默认无操作。"""

    def clear_bullets_for_transition(self) -> None:
        """op112/符卡开始(EclRunHigh.inl:789); 默认无操作。"""

    def set_stage_script_label(self, label: int) -> None:
        """op147(EclRunHigh.inl:711); 默认无操作。"""

    def start_stage_background_sequence(self) -> None:
        """op179(EclRunHigh.inl:955); 默认无操作。"""

    def set_spellcard_effect_tracking(self, disabled: int, pos: Vec3) -> None:
        """op164(EclRunHigh.inl:856-863); 默认无操作。"""

    def set_bonus_updates_disabled(self, v: int) -> None:
        """op184(EclRunHigh.inl:972); 默认无操作。"""

    def spawn_alignment_effect(self, kind: int) -> None:
        """op174(EclRunHigh.inl:936-952); 默认无操作。"""

    def set_last_spell_flags(self) -> None:
        """op176(EclRunHigh.inl:902-919); 默认无操作。"""

    def set_spellcard_bonus(self, bonus: int) -> None:
        """op122(EclDependencies.cpp:18-36); 默认无操作。"""

    def count_parent_chain(self, enemy: Th08EnemyState) -> int:
        """CountParentChain(EclOperandsInt.cpp:125-129); 默认无链(0)。"""
        return 0

    def attached_parent(self, enemy: Th08EnemyState) -> Optional[Th08EnemyState]:
        """HasAttachedEnemy → parentEnemy(同 10096 判定); 默认无父(None)。"""
        return None


@register_game_hooks("th08", msg_file="msg{n}{team}.dat")
class Th08GameEclHost(EclHost):
    """th08 的 ECL 宿主: 时刻 + 世界效果 + EX 指令 dispatch。

    关卡资源命名随包登记(msg{n}{team}.dat; stage/ecl 文件名不规整
    (stage4a/4b 等), world 侧按 data.STAGE_STD_FILES/STAGE_ECL_FILES/
    MSG_FILES 表取, 不走 hooks 的 format 模板)。
    """

    def __init__(
        self,
        ecl_file: EclFile | EclWorld | None = None,
        world: Optional[EclWorld] = None,
        *,
        enemies: EnemyHost | None = None,
        bullets: BulletWorld | None = None,
        lasers: LaserWorld | None = None,
        items: ItemWorld | None = None,
        ecl_machine_cls: type[EclMachineBase] | None = None,
        extra: bool = False,
    ) -> None:
        # 兼容裸构造(既有测试): 第一参是 EclWorld 时按 world 处理
        if isinstance(ecl_file, EclWorld) and world is None:
            world, ecl_file = ecl_file, None
        assert ecl_file is None or isinstance(ecl_file, EclFile)  # 收窄类型
        self.file: EclFile | None = ecl_file
        # world 运行时是 Th08EclWorld(world._load_ecl 构造; 兼容裸构造时缺省
        # 自建); cast 是运行时 no-op, 仅为收窄 th08 扩展字段的类型
        self.world: Th08EclWorld = (
            cast(Th08EclWorld, world) if world is not None else Th08EclWorld()
        )
        self.enemies = enemies
        self.bullets = bullets
        self.lasers = lasers
        self.items = items
        self.ecl_machine_cls = ecl_machine_cls
        # 时刻: 本篇 23:00 开局, EX 面 2:00(GameManagerSetup.cpp:101-105)
        self.clock = Th08Clock.for_extra() if extra else Th08Clock.for_stage()
        # ---- EX 指令的宿主侧状态(EclExIns.cpp 对照) ----
        self.night_blindness_alpha = 0  # ex0: AsciiManager.nightBlindnessAlpha
        self.night_blindness_radius = 0.0  # ex0: nightBlindnessRadius
        self.current_spellcard_number = -1  # ex19: GameManager.currentSpellCardNumber
        self.spellcards_captured = 0  # ex24: globals->spellcardsCaptured
        self.bonus_updates_disabled = 0  # op184: Spellcard BONUS_UPDATES_DISABLED
        # (world 每帧同步到 boss.bonus_updates_disabled)
        self.scripted_update_freeze = 0  # ex26: GameManager.scriptedUpdateFreeze
        self.screen_effect_counter = 0  # ex30: g_ScreenEffectCounter
        # ---- 宿主侧运行状态 ----
        self.frozen = False  # 炸弹中/玩家非 ALIVE (freeze_ecl_during_bombs 用)
        self.bomb_in_use = False  # 炸弹中(体术豁免/ex31 用)
        self.spellcard_idx = -1  # g_EnemyManager.spellcardInfo.spellcardIdx
        self.power = 0.0  # spawn_item 满火力转换用(每帧由 world 同步)
        self.last_msg_id = -1  # msg_read 记录
        self.msg_vm: MsgVm | None = None  # 对话 VM(world 装入)
        self.boss_health: tuple = ()
        self.boss_life_markers = 0
        self.enemy_by_state: dict[int, EclEnemy] = {}
        # 使魔父链(id(state) 键; on_enemy_gone 清理):
        # _attach_next: 链尾指针链, _attach_parent: 子 → 父;
        # _attach_inherit_pos: op92 位继承子机(detach 时 positionOffset=父位置)
        self._attach_next: dict[int, Th08EnemyState] = {}
        self._attach_parent: dict[int, Th08EnemyState] = {}
        self._attach_inherit_pos: set[int] = set()
        # ---- 事件透出(world 接线; 均为可选) ----
        self.on_set_boss: SetBossHook | None = None
        self.on_begin_spellcard: BeginSpellcardHook | None = None
        self.on_end_spellcard: EndSpellcardHook | None = None
        self.on_spellcard_timeout: EndSpellcardHook | None = None
        self.on_set_power: IntHook | None = None
        # 击坠(kill)时的使魔链奖励回调 (DetachEnemyChain(1), world 接线;
        # 在 on_enemy_gone 拆链前触发)
        self.on_chain_kill: Callable[[EclEnemy], None] | None = None
        self.pending_spellcard_bonus = 0  # op122 的 bonus(world 在
        # on_begin_spellcard 里取走; VM 经 set_spellcard_bonus 传递)
        self.sound: SoundQueue | None = None
        self.bgm_events: list[tuple] = []
        self.gone_events: list[tuple] = []
        self.shake_events: list[tuple[int, int, int]] = []

    def _play_sound(self, idx: int) -> None:
        if self.sound is not None:
            self.sound.play(idx)

    # ---- 每帧同步(world 在跑时间轴/敌人前调用) ----
    def frame_update(
        self,
        *,
        player_pos: Vec2,
        difficulty: int,
        rank: int,
        power: float,
        shottype: int,
        spellcard_active: bool,
        frozen: bool,
        bomb_in_use: bool = False,
        player_is_youkai: bool = False,
    ) -> None:
        w = self.world
        w.player_pos.set(player_pos.x, player_pos.y, 0.0)
        w.difficulty = difficulty
        w.rank = rank
        w.current_power = int(power)
        w.player_shottype = shottype
        w.spellcard_active = spellcard_active
        w.player_is_youkai = int(player_is_youkai)
        self.power = power
        self.frozen = frozen
        self.bomb_in_use = bomb_in_use
        self.update_familiar_alignment()

    def update_familiar_alignment(self) -> None:
        """Enemy::UpdateYoukaiAlignment (EnemyManager.cpp:869-902; 使魔
        (linkedChild) 每帧在 RunEcl 前调用, EnemyManagerUpdate.cpp:153-154):
        youkaiAligned ← 自机 IsYoukai 跟随, drawGroup 妖 0/人 2,
        eclDifficultyMaskOverride 妖 64/人 32; 形态翻转时音效 40(→妖)/
        39(→人), 对齐特效 interrupt 2/1 是 view 侧(EclRunLow.inl:747-770)。
        """
        if self.enemies is None:
            return
        youkai = self.world.player_is_youkai
        for sid in list(self._attach_parent):
            e = self.enemy_by_state.get(sid)
            if e is None:
                continue
            st = cast(Th08EnemyState, e.state)
            if st.youkai_aligned != youkai:
                # 形态翻转 (:871-896)
                self._play_sound(40 if youkai else 39)
            st.youkai_aligned = youkai
            st.draw_group = 0 if youkai else 2
            st.difficulty_mask_override = 64 if youkai else 32

    # ---- 弹幕 ----
    def spawn_bullet_pattern(self, props: EnemyBulletShooter) -> None:
        if self.bullets is None:
            return
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
                # RunCommands 在第一个 type==0 槽停止评估 (BulletManager 的
                # transforms 链), 按截断处理(同 th07)
                break
            try:
                t = CmdFlag(c.type)
            except ValueError:
                continue
            cmds.append(
                BulletCommand(
                    t,
                    speed=c.speed,
                    angle=c.angle,
                    duration=c.duration,
                    loop=c.loop_count,
                    flag=c.flag,
                )
            )
        self.bullets.fire(
            Burst(
                pos,
                base,
                aim,
                props.count1,
                props.count2,
                props.speed1,
                props.speed2,
                props.angle2,
                sprite=props.sprite,
                sprite_offset=props.sprite_offset,
                commands=tuple(cmds),
                flags=props.flags,
            )
        )
        if props.flags & 0x200:
            # 发弹音 (SpawnBulletPattern 尾部, BULLET_TRANSFORM_PLAY_SPAWN_SOUND)
            self._play_sound(props.sound_idx)

    # ---- 激光(句柄 = Laser 对象) ----
    def spawn_laser_pattern(self, props: EnemyLaserShooter):
        if self.lasers is None or self.bullets is None:
            return None
        if len(self.lasers.lasers) >= _MAX_LASERS:
            return None
        # BulletManager.cpp:430f20 段: spawnSuppressionFrames 窗口内,
        # 不带 SPAWN_NORMAL(0x4) flag 的激光不生成(返回假句柄, None 等价)
        if self.bullets.screen_clear_time != 0 and not (props.flags & 4):
            return None
        pos = Vec2(props.pos.x, props.pos.y)
        angle = props.angle1
        if props.type == 0:  # C: MOVING(0) 出生即瞄玩家
            angle = angle_to(pos, self.bullets.player_pos) + angle
        laser = Laser(
            pos=pos,
            angle=angle,
            width=props.width,
            speed=props.speed1,
            start_time=props.start_time,
            hitbox_start_time=props.hitbox_start_time,
            duration=props.duration,
            end_time=props.end_time,
            hitbox_end_time=props.hitbox_end_time,
            start_length=props.start_length,
            flags=props.flags,
            color=props.sprite_offset,
        )
        laser.offset_a = props.start_offset
        laser.offset_b = props.end_offset
        self.lasers.lasers.append(laser)
        return laser

    def laser_set_angle(self, handle: Laser, angle: float) -> None:
        handle.angle = angle

    def laser_add_angle(self, handle: Laser, delta: float) -> None:
        handle.angle += delta

    def laser_aim_at_player(self, handle: Laser, offset: float) -> None:
        if self.bullets is not None:
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
    def spawn_enemy(
        self,
        sub_id: int,
        pos: Vec3,
        life: int,
        item_drop: int,
        score: int,
        mirror: int,
        context_args: EclContextArgs,
    ) -> EclEnemy | None:
        """EnemyManager::SpawnEnemy(Ex): 立即跑一帧 RunEcl, 失败则不登记。"""
        if self.file is None or self.enemies is None or self.ecl_machine_cls is None:
            return None
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

    def spawn_familiar(
        self,
        kind: int,
        sub_id: int,
        pos: Vec3,
        life: int,
        item_drop: int,
        score: int,
        context_args: EclContextArgs,
        parent: Optional[Th08EnemyState] = None,
    ) -> EclEnemy | None:
        """th08 op90-92 使魔生成 (EclRunLow.inl:737-929)。

        挂父链尾(FindAttachmentChainTail): linkedChild=1, youkaiAligned =
        自机人妖形态, 清碰撞(flags1 COLLISION), op92 继承父位置
        (positionOffset = parent->position); 父 linkedChildCount++
        (喂 VM 变量 10096)。音效 0x24 由 VM 侧无条件播放
        (EclRunLow.inl:792-794)。
        """
        e = self.spawn_enemy(sub_id, pos, life, item_drop, score, 0, context_args)
        if e is None or parent is None:
            return e
        child = cast(Th08EnemyState, e.state)
        child.youkai_aligned = self.world.player_is_youkai
        child.draw_group = 0 if child.youkai_aligned else 2  # (v?-1:0)&-2)+2
        child.difficulty_mask_override = 64 if child.youkai_aligned else 32
        # (UpdateYoukaiAlignment 每帧跟随, EnemyManager.cpp:901)
        child.has_contact_hitbox = 0
        child.has_no_collision = 1
        if kind == 92:  # 继承父位置: positionOffset = parent->position
            child.pos_offset = parent.pos.copy()
            self._attach_inherit_pos.add(id(child))
        # 链尾挂载
        tail = parent
        while id(tail) in self._attach_next:
            tail = self._attach_next[id(tail)]
        self._attach_next[id(tail)] = child
        self._attach_parent[id(child)] = parent
        parent.linked_child_count += 1
        return e

    def count_parent_chain(self, st: Th08EnemyState) -> int:
        """CountParentChain: 从 st 起沿链尾走到头的节点数(不含 st 自身)。"""
        n = 0
        cur: Th08EnemyState = st
        while id(cur) in self._attach_next:
            cur = self._attach_next[id(cur)]
            n += 1
        return n

    def attached_parent(self, st: Th08EnemyState) -> Optional[Th08EnemyState]:
        """HasAttachedEnemy → parentEnemy (EclOperandsInt.cpp:125-129 用)。"""
        return self._attach_parent.get(id(st))

    def detach_chain(self, st: Th08EnemyState) -> list[Th08EnemyState]:
        """DetachEnemyChain 的拆链段 (EnemyManager.cpp:229-264):
        把 st 的子链从头至尾摘下(清双向链指针), op92 位继承的子机
        (spawn_familiar kind==92)positionOffset = st.pos (:243-246)。
        返回摘下的子机(顺序 = 链序); 奖励掉落在 world._kill_reward 结算。
        """
        children: list[Th08EnemyState] = []
        cur = st
        while id(cur) in self._attach_next:
            child = self._attach_next.pop(id(cur))
            self._attach_parent.pop(id(child), None)
            if id(child) in self._attach_inherit_pos:
                child.pos_offset = cur.pos.copy()  # 继承父位置 (:244-245)
            child.has_contact_hitbox = 0
            children.append(child)
            cur = child
        st.linked_child_count = 0
        return children

    def set_bonus_updates_disabled(self, v: int) -> None:
        """op184 (EclRunHigh.inl:972): Spellcard.SetBonusUpdatesDisabled。
        宿主暂存, world 每帧同步到 boss.bonus_updates_disabled。"""
        self.bonus_updates_disabled = v

    def on_enemy_gone(self, e: EclEnemy) -> None:
        """EclEnemy despawn/击坠时回调: 清登记/父链, boss 槽联动。

        击坠(kill)且带使魔链 → 先触发 on_chain_kill (DetachEnemyChain(1)
        的奖励段, EnemyManager.cpp:229-345; despawn 等价 DetachEnemyChain(0),
        只拆链不奖励)。"""
        if e.died_by_kill and self.on_chain_kill is not None:
            self.on_chain_kill(e)
        st = e.state
        self.enemy_by_state.pop(id(st), None)
        self._attach_next.pop(id(st), None)
        self._attach_inherit_pos.discard(id(st))
        parent = self._attach_parent.pop(id(st), None)
        if parent is not None and parent.linked_child_count > 0:
            parent.linked_child_count -= 1
        if 0 <= st.boss_id < 8 and self.world.bosses[st.boss_id] is st:
            self.world.bosses[st.boss_id] = None
        # 渲染层死亡特效用(只读记录)
        self.gone_events.append(
            (id(st), st.pos.x, st.pos.y, st.life, st.death_anm, bool(st.is_boss))
        )

    def clear_field(self, source: EclEnemy) -> None:
        """回调切阶段时的清场 (C: 非 boss 敌 life=0, !canDie 的跑死亡回调)。"""
        if self.enemies is None:
            return
        for e in self.enemies.all():
            if e is source or not e.alive or e.is_boss:
                continue
            e.life = 0
            if (
                isinstance(e, EclEnemy)
                and not e.state.can_die
                and e.state.death_callback_sub >= 0
            ):
                e._run_death_callback()

    def on_timer_callback(self, e: EclEnemy) -> None:
        """Enemy::HandleTimerCallback 的世界侧: 清场恒做; 捕获失败记账透出。"""
        self.clear_field(e)
        if self.on_spellcard_timeout is not None:
            self.on_spellcard_timeout(e.state)

    def remove_all_enemies(self, score_max: int, score_min: int) -> int:
        """EnemyManager::KillAllNonBossEnemies (EnemyManager.cpp:1424-1520):
        跳过 boss/canDie=0; specialInteraction(isProjectile)敌掉弹消星并
        累计弹字分(2000 起 +30, score_max 封顶); 敌历史轨迹的连带掉星
        (trailFlags 段) 同 th07 既有简化不生成。"""
        total = score_min
        popup = 2000
        if self.enemies is None:
            return total
        for e in self.enemies.all():
            if not e.alive or e.is_boss:
                continue
            e.life = 0
            if isinstance(e, EclEnemy):
                if e.state.is_projectile:
                    # specialInteraction 敌掉弹消星, 出生即吸附 (:1452-1456)
                    self.spawn_item(
                        Vec3(e.pos.x, e.pos.y, 0.0), int(ItemType.POINT_STAR)
                    )
                    g = getattr(self.world, "globals", None)
                    if g is not None:
                        g.add_popup(
                            e.pos,
                            popup,
                            0xFFFFFF00 if popup >= score_max else 0xFFFFFFFF,
                            kind=1,
                        )
                    total += popup
                    popup = min(popup + 30, score_max)
                if not e.state.can_die and e.state.death_callback_sub >= 0:
                    e._run_death_callback()
        return total

    # ---- 道具/清弹 ----
    def spawn_item(self, pos: Vec3, item_type: int) -> None:
        if self.items is None:
            return
        self.items.spawn(Vec2(pos.x, pos.y), item_type, power=self.power)

    def _spawn_point_star(self, pos: Vec2) -> None:
        """弹消星道具 (cancelItemType=6, BulletManager.cpp:49);
        出生即吸附 (RemoveAllBullets 的 SpawnItem(…, ITEM_STATE_AUTOCOLLECT))。"""
        if self.items is not None:
            self.items.spawn(
                pos, int(ItemType.POINT_STAR), power=self.power, state=STATE_ATTRACT
            )

    def remove_all_bullets(self, spawn_items: bool) -> None:
        """BulletManager::RemoveAllBullets (:484-561)。

        spawn_items=True ↔ mode=1: 弹转弹消星(吸附), flags&4 激光豁免、
        沿线每 32px 出星; spawn_items=False ↔ mode=4(op162): 纯 despawn
        无道具, 激光不豁免。末尾恒 spawnSuppressionFrames=10 (:561)。
        """
        if self.bullets is None:
            return
        for b in self.bullets.alive():
            if spawn_items:
                self._spawn_point_star(b.pos)
            b.dead = True
        if self.lasers is not None:
            self.lasers.remove_all(
                spawn_items=spawn_items,
                skip_flag4=spawn_items,  # mode!=4 才豁免 flags&4 (:522-524)
                spawn_item=self._spawn_point_star if spawn_items else None,
            )
        self.bullets.screen_clear_time = 10  # :561 spawnSuppressionFrames=10

    def clear_bullets_for_transition(self) -> None:
        """ClearBulletsForTransition (Spellcard.cpp:883-886) = RemoveAllBullets(1)。
        op112/符卡开始(StartSpell, Spellcard.cpp:746)走这里。"""
        self.remove_all_bullets(True)

    def remove_bullets_in_radius(self, pos: Vec3, radius: float) -> None:
        """BulletManager::RemoveBulletsInRadius (:663-681): 半径内弹转弹消星
        (吸附), 不碰激光, 无 spawnSuppressionFrames。"""
        if self.bullets is None:
            return
        r2 = radius * radius
        for b in self.bullets.alive():
            dx = b.pos.x - pos.x
            dy = b.pos.y - pos.y
            if dx * dx + dy * dy <= r2:
                self._spawn_point_star(b.pos)
                b.dead = True

    # ---- Boss/符卡(事件透出, 记账在 world) ----
    def set_boss(self, idx: int, enemy: Optional[EclEnemyState]) -> None:
        if self.on_set_boss is not None:
            self.on_set_boss(idx, enemy)

    def set_boss_health(self, idx: int, cur: int, max_life: int, color: int) -> None:
        self.boss_health = (idx, cur, max_life, color)

    def set_boss_life_markers(self, n: int) -> None:
        self.boss_life_markers = n

    def begin_spellcard(
        self, enemy: EclEnemyState, gui_id: int, spellcard_idx: int, name: str
    ) -> None:
        self.world.spellcard_active = True
        self.spellcard_idx = spellcard_idx
        # ex19 发布值 (GameManager.currentSpellCardNumber)
        self.current_spellcard_number = spellcard_idx
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

    def set_spellcard_bonus(self, bonus: int) -> None:
        """op122 的 bonus 传递(在 begin_spellcard 前由 VM 调)。"""
        self.pending_spellcard_bonus = bonus

    # ---- 系统/表现 ----
    def play_sound(self, idx: int) -> None:
        """ECL PLAY_SOUND(124): 原样透传音效 idx。"""
        self._play_sound(idx)

    def msg_read(self, msg_id: int) -> None:
        """时间轴 op6 (EnemyTimeline.cpp:221-223): MsgRead(arg0)。

        th08 文件名已按队伍分(msg{n}{team}.dat), arg0 直接用(无 th07 的
        character*10 偏移)。StartMessage 尾部清场 (Gui.cpp:242-244):
        ClearBulletsForTransition + KillAllNonBossEnemies(0,0) +
        AutoCollectAllItems(道具转吸附)。
        """
        self.last_msg_id = msg_id
        vm = self.msg_vm
        if vm is None:
            return
        vm.read(msg_id)
        if vm.has_current_msg_idx():
            self.clear_bullets_for_transition()
            self.remove_all_enemies(0, 0)
            if self.items is not None:
                self.items.remove_all_items()

    def msg_wait(self) -> bool:
        """时间轴 op7: 消息未读完则停轴 (Gui::MsgWait, Gui.cpp:869-876)。"""
        if self.msg_vm is None:
            return False
        return self.msg_vm.msg_wait()

    def set_power(self, value: int) -> None:
        if self.on_set_power is not None:
            self.on_set_power(value)

    # ---- 时刻(op180/181 的宿主端) ----

    def clock_advance(self) -> None:
        """op181(EclRunHigh.inl:957-967): <12 才推进; 音效 0x2D;
        到 12 表盘快闪否则慢闪(GUI 表现, 不接)。"""
        if self.clock.units < 12:
            self.play_sound(0x2D)
            self.clock.advance()

    def clock_hide(self) -> None:
        """op180(EclRunHigh.inl:956): Gui.HideClockTime。"""
        self.clock.hide()

    # ---- 其余 th08 接缝(Th08HostProto): 世界侧效果未接, 显式 no-op ----

    def call_sub_on_boss(self, boss: EclEnemyState, sub_id: int) -> None:
        """op88: 让别的 boss 压栈并调用 sub(EclRunLow.inl:712-717); 未接。"""

    def show_retry_menu(self) -> None:
        """时间轴 op16: 显示 Retry 菜单(EnemyTimeline.cpp:136-138); 未接。"""

    def set_stage_script_label(self, label: int) -> None:
        """op147: Background.pendingStageScriptLabel(EclRunHigh.inl:711); 未接。"""

    def start_stage_background_sequence(self) -> None:
        """op179: Gui.StartStageBackgroundSequence(EclRunHigh.inl:955); 未接。"""

    def set_spellcard_effect_tracking(self, disabled: int, pos: Vec3) -> None:
        """op164: Spellcard 特效跟踪开关+记录向量(EclRunHigh.inl:856-863); 未接。"""

    def spawn_alignment_effect(self, kind: int) -> None:
        """op174: 人妖对齐特效(结界光环, EclRunHigh.inl:936-952); 未接。"""

    def set_last_spell_flags(self) -> None:
        """op176: Last Spell 的 GameManager 标志位操作(EclRunHigh.inl:902-919);
        未接。"""

    # ---- EX 指令(32 条, EclGlobals.cpp:65-98) ----

    def run_ex_instr(
        self,
        idx: int,
        enemy: EclEnemyState,
        instr: Optional[EclInstr],
        ctx: Optional[EclContext] = None,
    ) -> bool:
        handler = self._EX_DISPATCH.get(idx)
        if handler is None:
            return False
        handler(self, enemy, instr, ctx)
        return True

    @staticmethod
    def _ex_value(instr: Optional[EclInstr]) -> int:
        """EclExInstruction.value @0x10(EclManager.hpp:158-173) = args[4]。"""
        return instr.arg_int(4) if instr is not None else 0

    # -- 纯状态类(按 EclExIns.cpp 实现) --

    def _ex0_night_blindness(self, enemy, instr, ctx) -> None:
        """ex0 ConfigureNightBlindness(EclExIns.cpp:30-35)。"""
        if ctx is None:
            return
        a = _args_of(ctx)
        self.night_blindness_alpha = a.th08_ints[0]
        self.night_blindness_radius = a.th08_floats[0]

    def _ex2_bouncing_motion(self, enemy, instr, ctx) -> None:
        """ex2 UpdateBouncingEnemyMotion(EclExIns.cpp:44-82): 边界反弹 +
        重力(速度/位置都是敌人自身状态)。"""
        if ctx is None:
            return
        e = enemy
        a = _args_of(ctx)
        changed = False
        if e.pos.x <= 0.0 or e.pos.x >= 384.0:
            e.axis_speed.x = -e.axis_speed.x
            changed = True
        if e.axis_speed.y < a.th08_floats[7]:
            e.axis_speed.y = f32(e.axis_speed.y + a.th08_floats[6])
            changed = True
        if e.pos.y < -64.0:
            e.axis_speed.y = -e.axis_speed.y
            changed = True
        elif e.pos.y >= 480.0:
            e.disable_oob_despawn = 0  # 清 ENEMY_FLAG_ALLOW_OFFSCREEN
        if changed:
            e.angle = f32(math.atan2(e.axis_speed.y, e.axis_speed.x))

    def _ex18_framerate_divisor(self, enemy, instr, ctx) -> None:
        """ex18 SetFrameRateDivisor(EclExIns.cpp:775-784)。"""
        value = self._ex_value(instr)
        if value:
            self.world.framerate_multiplier = 1.0 / value

    def _ex19_publish_spellcard_number(self, enemy, instr, ctx) -> None:
        """ex19 PublishCurrentSpellCardNumber(EclExIns.cpp:787-791)。"""
        if ctx is not None:
            _args_of(ctx).th08_ints[0] = self.current_spellcard_number

    def _ex24_publish_captured_count(self, enemy, instr, ctx) -> None:
        """ex24 PublishCapturedSpellCardCount(EclExIns.cpp:808-812)。"""
        if ctx is not None:
            _args_of(ctx).th08_ints[0] = self.spellcards_captured

    def _ex26_scripted_update_freeze(self, enemy, instr, ctx) -> None:
        """ex26 SetScriptedUpdateFreeze(EclExIns.cpp:815-830);
        背景 spellVms interrupt 是表现侧, 不接。"""
        self.scripted_update_freeze = self._ex_value(instr) & 0xFF

    def _ex28_enter_bullet_time(self, enemy, instr, ctx) -> None:
        """ex28 EnterScaledBulletTime(EclExIns.cpp:858-882): 帧率缩放 +
        全场弹速重标(:869-881); 换皮(baseSpriteIndex)是表现侧, 不接。"""
        value = self._ex_value(instr)
        if value:
            mult = 1.0 / value
            self.world.framerate_multiplier = mult
            if self.bullets is not None:
                self.bullets.time_scale = mult
                for b in self.bullets.alive():
                    b.vel = b.vel * mult

    def _ex29_exit_bullet_time(self, enemy, instr, ctx) -> None:
        """ex29 ExitScaledBulletTime(EclExIns.cpp:886-913): 弹速还原 +
        恢复 1.0; 换皮还原是表现侧, 不接。"""
        mult = self.world.framerate_multiplier
        fps = 1.0 / mult if mult else 1.0
        if self.bullets is not None:
            for b in self.bullets.alive():
                b.vel = b.vel * fps
            self.bullets.time_scale = 1.0
        self.world.framerate_multiplier = 1.0

    def _ex30_screen_effect_counter(self, enemy, instr, ctx) -> None:
        """ex30 SetScreenEffectCounter(EclExIns.cpp:522-525)。"""
        self.screen_effect_counter = self._ex_value(instr)

    # -- 屏闪/震屏(特效本体是 view 侧; 震屏经 shake_events 透出) --

    def _ex1_short_screen_pulse(self, enemy, instr, ctx) -> None:
        """ex1 TriggerShortScreenPulse(EclExIns.cpp:38-41):
        ScreenEffect ARCADE_PULSE(60,1) —— 纯视觉屏闪, 无逻辑效果, 不接。"""

    def _ex10_screen_pulse_and_shake(self, enemy, instr, ctx) -> None:
        """ex10 TriggerScreenPulseAndShake(EclExIns.cpp:513-519):
        pulse 纯视觉不接; 震屏 SHAKE_ENVELOPE(4,120,190,60) 透出
        (th07 形态: (duration, amp_start, amp_end) 三元组)。"""
        self.shake_events.append((120, 190, 60))

    def _ex15_screen_shake(self, enemy, instr, ctx) -> None:
        """ex15 TriggerScreenShake(EclExIns.cpp:725-729):
        SHAKE_ENVELOPE(16,20,20,20) 透出。"""
        self.shake_events.append((16, 20, 20))

    def _ex17_long_screen_pulse(self, enemy, instr, ctx) -> None:
        """ex17 TriggerLongScreenPulse(EclExIns.cpp:768-771):
        ARCADE_PULSE(180,1) —— 纯视觉, 不接。"""

    def _ex13_red_background_tint(self, enemy, instr, ctx) -> None:
        """ex13 ApplyRedBackgroundTint(EclExIns.cpp:719-722):
        Background.AccumulateTint(0xffc03030) —— 纯视觉, 不接。"""

    # -- 子弹折跃结界(ex3-7/20/21): Start/Stop 是特效+绘制回调(view 侧),
    #    Warp 是纯弹道逻辑, 实装 --

    def _ex4_warp_narrow(self, enemy, instr, ctx) -> None:
        """ex4 WarpBulletsAcrossNarrowBarrier(EclExIns.cpp:185-256)。"""
        if self.bullets is None:
            return
        # 阈值矩形 (C 内嵌常量): 内区中心 (192,208) 半宽 67.88, 外区半宽 135.76
        self._warp_bullets_region(
            inner=(124.11774444580078, 140.11773681640625,
                   259.88226318359375, 275.88226318359375),
            outer=(56.23548889160156, 72.23548889160156,
                   327.7645263671875, 343.7645263671875),
            center=(192.0, 208.0),
            scale_num=135.76451110839844,
            scale_den=67.88225555419922,
        )

    def _warp_bullets_region(
        self,
        inner: tuple[float, float, float, float],
        outer: tuple[float, float, float, float],
        center: tuple[float, float],
        scale_num: float,
        scale_den: float,
    ) -> None:
        """WarpBulletsAcross*Barrier 公共段 (EclExIns.cpp:185-256 等):
        弹跨越内/外区边界时反转速度并按比例缩放位置(绕中心),
        每弹 2 帧冷却(state2 借用, 见模块头注释)。"""
        if self.bullets is None:
            return
        cx, cy = center

        def zone(p: Vec2) -> int:
            if inner[0] < p.x < inner[2] and inner[1] < p.y < inner[3]:
                return 0
            if outer[0] < p.x < outer[2] and outer[1] < p.y < outer[3]:
                return 1
            return 2

        for b in self.bullets.alive():
            if 0 < b.state2 <= 2:  # zoneTransitionCooldownFrames
                b.state2 -= 1
                continue
            prev = b.pos - b.vel
            cur_zone = zone(b.pos)
            if cur_zone == zone(prev):
                continue
            b.state2 = 2
            b.vel = b.vel * -1.0
            if cur_zone == 0 or zone(prev) == 0:
                ratio = scale_num / scale_den
            else:
                ratio = scale_den / scale_num
            b.pos = Vec2((b.pos.x - cx) * ratio + cx, (b.pos.y - cy) * ratio + cy)
            b.angle = add_normalize_angle(b.angle, math.pi)

    def _ex7_warp_wide(self, enemy, instr, ctx) -> None:
        """ex7 WarpBulletsAcrossWideBarrier(EclExIns.cpp:366-437)。"""
        self._warp_bullets_region(
            inner=(56.23548889160156, 88.23548889160156,
                   327.7645263671875, 359.7645263671875),
            outer=(-32.0, 0.0, 416.0, 448.0),
            center=(192.0, 224.0),
            scale_num=224.0,
            scale_den=135.76451110839844,
        )

    def _ex21_warp_medium(self, enemy, instr, ctx) -> None:
        """ex21 WarpBulletsAcrossMediumBarrier(EclExIns.cpp:272-343)。"""
        self._warp_bullets_region(
            inner=(112.80403137207031, 128.8040313720703,
                   271.1959533691406, 287.1959533691406),
            outer=(33.608070373535156, 49.608070373535156,
                   350.3919372558594, 366.3919372558594),
            center=(192.0, 208.0),
            scale_num=158.39193725585938,
            scale_den=79.19596862792969,
        )

    def _ex_barrier_view_stub(self, enemy, instr, ctx) -> None:
        """ex3/5/6/20 Start/Stop*BulletWarpBarrier (EclExIns.cpp:85-92/346-351/
        354-360/259-266): 固定槽特效 + spellBackgroundDrawCallback 绘制 —
        纯 view 表现, 弹道逻辑在 ex4/7/21(脚本继续注册它们), 不接。"""

    # -- 使魔/附着链联动 --

    def _ex8_sync_orbiting_children(self, enemy, instr, ctx) -> None:
        """ex8 SynchronizeOrbitingChildFormation(EclExIns.cpp:444-506):
        父链同组(extraIntVariables[2])子机环绕角同步。"""
        if ctx is None:
            return
        parent = self._attach_parent.get(id(enemy))
        if parent is None:
            return
        a = _args_of(ctx)
        group_id = a.th08_extra_ints[2]
        # 沿父链收集同组子机, 编号进 extraIntVariables[1]
        count = 0
        first: Th08EnemyState | None = None
        cursor = parent
        members: list[Th08EnemyState] = []
        while id(cursor) in self._attach_next:
            cursor = self._attach_next[id(cursor)]
            peer = self.enemy_by_state.get(id(cursor))
            if peer is None:
                continue
            peer_args = _args_of(peer.machine.current)
            if peer_args.th08_extra_ints[2] == group_id:
                peer_args.th08_extra_ints[1] = count
                if first is None:
                    first = cursor
                members.append(cursor)
                count += 1
        a.th08_ints[5] = 0
        if a.th08_ints[6] != count:
            if a.th08_ints[6] != 0:
                a.th08_ints[5] = 1
            a.th08_ints[6] = count
        my_group = a.th08_extra_ints[1]
        a.th08_ints[7] += 1
        if my_group == 0 or first is None:
            return
        first_peer = self.enemy_by_state.get(id(first))
        target = first.move_angle + my_group * 6.2831854820251465 / count
        if first_peer is not None and (
            _args_of(first_peer.machine.current).th08_ints[7] != a.th08_ints[7]
        ):
            target = add_normalize_angle(target, first.move_angular_velocity)
        delta = add_normalize_angle(enemy.move_angle, enemy.move_angular_velocity)
        delta = target - delta
        if abs(delta) > math.pi:
            delta = (
                -6.2831854820251465 + delta
                if delta > 0.0
                else 6.2831854820251465 + delta
            )
        delta *= 0.02
        enemy.move_angle = add_normalize_angle(enemy.move_angle, delta)

    def _ex16_trigger_children_near_marked_bullets(self, enemy, instr, ctx) -> None:
        """ex16 TriggerChildrenNearMarkedBullets(EclExIns.cpp:735-765):
        标记弹(0x100000)64px 内的链上子机(extraIntVariables[2]==0)
        触发: extraIntVariables[2]=60 + intVariables[7] 同步。"""
        if self.bullets is None or ctx is None:
            return
        for b in self.bullets.alive():
            if not (b.more_flags & _BULLET_FLAG_EX_TRIGGER_MARKER):
                continue
            cursor = enemy
            while id(cursor) in self._attach_next:
                cursor = self._attach_next[id(cursor)]
                peer = self.enemy_by_state.get(id(cursor))
                if peer is None:
                    continue
                args = _args_of(peer.machine.current)
                if args.th08_extra_ints[2] != 0:
                    continue
                dx = b.pos.x - cursor.pos.x
                dy = b.pos.y - cursor.pos.y
                if dx * dx + dy * dy < 4096.0:
                    args.th08_extra_ints[2] = 60
                    args.th08_ints[7] = _args_of(ctx).th08_ints[7]

    def _ex27_spawn_enemies_from_marked_bullets(self, enemy, instr, ctx) -> None:
        """ex27 SpawnEnemiesFromMarkedBullets(EclExIns.cpp:835-855):
        标记弹位置生敌(sub = extraIntVariables[2], life 800, 掉落 -2,
        分 10), 清标记; 弹角写 floatVariables[0]。"""
        if self.bullets is None or ctx is None:
            return
        a = _args_of(ctx)
        for b in self.bullets.alive():
            if not (b.more_flags & _BULLET_FLAG_EX_TRIGGER_MARKER):
                continue
            a.th08_floats[0] = b.angle
            self.spawn_enemy(
                a.th08_extra_ints[2],
                Vec3(b.pos.x, b.pos.y, 0.0),
                800,
                -2,
                10,
                0,
                a.clone(),
            )
            b.more_flags &= ~_BULLET_FLAG_EX_TRIGGER_MARKER

    def _ex31_spawn_bomb_or_extend_item(self, enemy, instr, ctx) -> None:
        """ex31 SpawnBombOrExtendItem(EclExIns.cpp:917-925): 炸弹中掉 B,
        否则掉残机。"""
        self.spawn_item(
            enemy.pos,
            int(ItemType.BOMB if self.bomb_in_use else ItemType.LIFE),
        )

    # -- 铃仙冻结弹(ex12/14): 弹速/判定是逻辑侧, 换皮/alpha 是 view 侧 --
    # Bullet.state2 借用编码: 0=正常(C bulletVm.type 1), 100=冻结(type 0,
    # collisionDisabled), 200=过渡(type 2, 仍无判定)

    def _ex12_reisen_freeze_bullets(self, enemy, instr, ctx) -> None:
        """ex12 ReisenFreezeBullets(EclExIns.cpp:601-665): 匹配掩码弹的
        冻结/解冻翻转(冻结: 无判定 + 慢速漂移; 解冻: 恢复原速);
        换皮(±16 sprite)/链上 FORCE_PAUSE/背景 VM interrupt 是表现侧, 不接。
        """
        if self.bullets is None or ctx is None:
            return
        a = _args_of(ctx)
        mask = a.th08_ints[0]
        mult = self.world.framerate_multiplier
        for b in self.bullets.alive():
            if not (b.more_flags & mask):
                continue
            if b.state2 == 0:  # type 1 → 冻结
                b.state2 = _BSTATE_FREEZEN
                b.vel = Vec2.from_angle(
                    a.th08_floats[0], mult * a.th08_floats[1]
                )
            else:  # → 解冻
                b.state2 = 0
                b.vel = Vec2.from_angle(b.angle, mult * b.speed)
        # intVariables[1]==0 → 链上 FORCE_PAUSE 置位(:643-652), 表现/冻结
        # 链属附着链机制, 留标记(follow-up)

    def _ex14_advance_reisen_bullet_phase(self, enemy, instr, ctx) -> None:
        """ex14 AdvanceReisenBulletPhase(EclExIns.cpp:669-715): 相位推进
        正常→冻结(无判定慢速)→过渡(无判定)→正常; alpha 插值/换皮不接。"""
        if self.bullets is None or ctx is None:
            return
        mask = _args_of(ctx).th08_ints[0]
        mult = self.world.framerate_multiplier
        for b in self.bullets.alive():
            if not (b.more_flags & mask):
                continue
            if b.state2 == 0:  # type 1 → type 0(冻结)
                b.state2 = _BSTATE_FREEZEN
                b.vel = Vec2.from_angle(b.angle, mult * _args_of(ctx).th08_floats[1])
            elif b.state2 == _BSTATE_FREEZEN:  # type 0 → type 2(过渡)
                b.state2 = _BSTATE_FREEZEN_MID
            else:  # type 2 → type 1(恢复)
                b.state2 = 0
                b.vel = Vec2.from_angle(b.angle, mult * b.speed)

    def bullet_collision_disabled(self, b) -> bool:
        """world 的弹判定循环用: 冻结/过渡相位的弹无碰撞
        (collisionDisabled, EclExIns.cpp:622/687)。"""
        return b.state2 in (_BSTATE_FREEZEN, _BSTATE_FREEZEN_MID)

    # -- 旋转激光判定(ex9/11/25): 依赖 anm VM rotation(表现侧状态), 不接 --

    def _ex_rotating_laser_stub(self, enemy, instr, ctx) -> None:
        """ex9/11/25 Update*RotatingLaserHitbox (EclExIns.cpp:529-597):
        Player::CalcLaserHitbox 的旋转角取自 enemy->vm.rotation.z —— anm VM
        是 view 侧状态, 本层不模拟, 留标记(follow-up: view 阶段接)。"""

    # -- 符卡演出(ex22/23): cut-in/落幕是 view 侧 --

    def _ex22_mokou_resurrection(self, enemy, instr, ctx) -> None:
        """ex22 MokouResurrection(EclExIns.cpp:794-799):
        Spellcard.CutInEnemyNoPortrait(「リザレクション」) —— 符卡 cut-in
        演出(view 侧), 无逻辑效果, 不接。"""

    def _ex23_hide_spellcard_presentation(self, enemy, instr, ctx) -> None:
        """ex23 HideSpellCardPresentation(EclExIns.cpp:802-805):
        Spellcard.HideEnemySpellPresentation —— 演出落幕(view 侧), 不接。"""

    _EX_DISPATCH = {
        0: _ex0_night_blindness,
        1: _ex1_short_screen_pulse,
        2: _ex2_bouncing_motion,
        3: _ex_barrier_view_stub,  # StartNarrowBulletWarpBarrier
        4: _ex4_warp_narrow,
        5: _ex_barrier_view_stub,  # StopBulletWarpBarrier
        6: _ex_barrier_view_stub,  # StartWideBulletWarpBarrier
        7: _ex7_warp_wide,
        8: _ex8_sync_orbiting_children,
        9: _ex_rotating_laser_stub,  # UpdateNarrowRotatingLaserHitbox
        10: _ex10_screen_pulse_and_shake,
        11: _ex_rotating_laser_stub,  # UpdateMediumRotatingLaserHitbox
        12: _ex12_reisen_freeze_bullets,
        13: _ex13_red_background_tint,
        14: _ex14_advance_reisen_bullet_phase,
        15: _ex15_screen_shake,
        16: _ex16_trigger_children_near_marked_bullets,
        17: _ex17_long_screen_pulse,
        18: _ex18_framerate_divisor,
        19: _ex19_publish_spellcard_number,
        20: _ex_barrier_view_stub,  # StartMediumBulletWarpBarrier
        21: _ex21_warp_medium,
        22: _ex22_mokou_resurrection,
        23: _ex23_hide_spellcard_presentation,
        24: _ex24_publish_captured_count,
        25: _ex_rotating_laser_stub,  # UpdateWideRotatingLaserHitbox
        26: _ex26_scripted_update_freeze,
        27: _ex27_spawn_enemies_from_marked_bullets,
        28: _ex28_enter_bullet_time,
        29: _ex29_exit_bullet_time,
        30: _ex30_screen_effect_counter,
        31: _ex31_spawn_bomb_or_extend_item,
    }
