"""TH08(东方永夜抄)ECL 宿主骨架 —— Th08GameEclHost + 32 条 EX 指令 dispatch。

阶段 2 单 B 的范围:
- ``@register_game_hooks("th08")`` 登记游戏回调包; 关卡资源命名:
  stage_file/ecl_file 与 th07 默认相同(stage{n}.std / ecldata{n}.ecl,
  已对照真实 th08.dat 条目核实), msg 文件是 msg{n}{team}.dat
  (4 个自机组 a-d, 与 th07 的 msg{n}.dat 不同, 在此覆盖)。
- 时刻(clock.py Th08Clock): op180/181 的宿主端(clock_hide/clock_advance,
  对照 EclRunHigh.inl:956-967)。
- 32 条 EX 指令(EclGlobals.cpp:65-98 的 g_EclExInsn 表): 纯状态类按
  EclExIns.cpp 实现(夜盲参数/帧率除数/符卡号发布/更新冻结/反弹运动等),
  world 效果类(子弹折返结界/震屏/特效/道具生成等)留 stub 标 follow-up
  —— 世界效果随阶段 3 的 world 机制接线。

完整世界效果(弹幕/激光/道具真生成、msg、boss GUI)是阶段 3 随 world 来的
工作(对照 th07 的 games/th07/ecl_host.py GameEclHost)。
"""

from __future__ import annotations

import math
from typing import Optional

from ...engine.ecl import (
    EclContext,
    EclEnemyState,
    EclHost,
    EclInstr,
    EclWorld,
)
from ...registry import register_game_hooks
from ...utils import f32
from .clock import Th08Clock


@register_game_hooks("th08", msg_file="msg{n}{team}.dat")
class Th08GameEclHost(EclHost):
    """th08 的 ECL 宿主骨架: 时刻 + EX 指令 dispatch + 状态记账。

    关卡资源命名随包登记(见模块 docstring); world 效果类接口保持
    EclHost 默认 no-op, 阶段 3 逐个覆盖。
    """

    def __init__(self, world: Optional[EclWorld] = None, *, extra: bool = False) -> None:
        self.world = world if world is not None else EclWorld()
        # 时刻: 本篇 23:00 开局, EX 面 2:00(GameManagerSetup.cpp:101-105)
        self.clock = Th08Clock.for_extra() if extra else Th08Clock.for_stage()
        # ---- EX 指令的宿主侧状态(EclExIns.cpp 对照) ----
        self.night_blindness_alpha = 0  # ex0: AsciiManager.nightBlindnessAlpha
        self.night_blindness_radius = 0.0  # ex0: nightBlindnessRadius
        self.current_spellcard_number = -1  # ex19: GameManager.currentSpellCardNumber
        self.spellcards_captured = 0  # ex24: globals->spellcardsCaptured
        self.scripted_update_freeze = 0  # ex26: GameManager.scriptedUpdateFreeze
        self.screen_effect_counter = 0  # ex30: g_ScreenEffectCounter

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
        self.night_blindness_alpha = ctx.args.th08_ints[0]
        self.night_blindness_radius = ctx.args.th08_floats[0]

    def _ex2_bouncing_motion(self, enemy, instr, ctx) -> None:
        """ex2 UpdateBouncingEnemyMotion(EclExIns.cpp:44-82): 边界反弹 +
        重力(速度/位置都是敌人自身状态)。"""
        if ctx is None:
            return
        e = enemy
        changed = False
        if e.pos.x <= 0.0 or e.pos.x >= 384.0:
            e.axis_speed.x = -e.axis_speed.x
            changed = True
        if e.axis_speed.y < ctx.args.th08_floats[7]:
            e.axis_speed.y = f32(e.axis_speed.y + ctx.args.th08_floats[6])
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
            ctx.args.th08_ints[0] = self.current_spellcard_number

    def _ex24_publish_captured_count(self, enemy, instr, ctx) -> None:
        """ex24 PublishCapturedSpellCardCount(EclExIns.cpp:808-812)。"""
        if ctx is not None:
            ctx.args.th08_ints[0] = self.spellcards_captured

    def _ex26_scripted_update_freeze(self, enemy, instr, ctx) -> None:
        """ex26 SetScriptedUpdateFreeze(EclExIns.cpp:815-830);
        背景 spellVms interrupt 是表现侧, 不接。"""
        self.scripted_update_freeze = self._ex_value(instr) & 0xFF

    def _ex28_enter_bullet_time(self, enemy, instr, ctx) -> None:
        """ex28 EnterScaledBulletTime(EclExIns.cpp:858-882): 帧率缩放;
        子弹速度重标/换皮是 world 阶段接线。"""
        value = self._ex_value(instr)
        if value:
            self.world.framerate_multiplier = 1.0 / value

    def _ex29_exit_bullet_time(self, enemy, instr, ctx) -> None:
        """ex29 ExitScaledBulletTime(EclExIns.cpp:886-913): 恢复 1.0;
        子弹速度还原/换皮是 world 阶段接线。"""
        self.world.framerate_multiplier = 1.0

    def _ex30_screen_effect_counter(self, enemy, instr, ctx) -> None:
        """ex30 SetScreenEffectCounter(EclExIns.cpp:522-525)。"""
        self.screen_effect_counter = self._ex_value(instr)

    # -- world 效果类 stub(follow-up: 阶段 3 随 world 接线) --

    def _ex_stub(self, enemy, instr, ctx) -> None:
        """world 效果类 EX(屏幕特效/子弹折返结界/激光判定/道具生成等),
        阶段 3 接线; 现按已路由的无操作处理。"""

    _EX_DISPATCH = {
        0: _ex0_night_blindness,
        1: _ex_stub,  # TriggerShortScreenPulse
        2: _ex2_bouncing_motion,
        3: _ex_stub,  # StartNarrowBulletWarpBarrier
        4: _ex_stub,  # WarpBulletsAcrossNarrowBarrier
        5: _ex_stub,  # StopBulletWarpBarrier
        6: _ex_stub,  # StartWideBulletWarpBarrier
        7: _ex_stub,  # WarpBulletsAcrossWideBarrier
        8: _ex_stub,  # SynchronizeOrbitingChildFormation(需附着链)
        9: _ex_stub,  # UpdateNarrowRotatingLaserHitbox
        10: _ex_stub,  # TriggerScreenPulseAndShake
        11: _ex_stub,  # UpdateMediumRotatingLaserHitbox
        12: _ex_stub,  # ReisenFreezeBullets
        13: _ex_stub,  # ApplyRedBackgroundTint
        14: _ex_stub,  # AdvanceReisenBulletPhase
        15: _ex_stub,  # TriggerScreenShake
        16: _ex_stub,  # TriggerChildrenNearMarkedBullets
        17: _ex_stub,  # TriggerLongScreenPulse
        18: _ex18_framerate_divisor,
        19: _ex19_publish_spellcard_number,
        20: _ex_stub,  # StartMediumBulletWarpBarrier
        21: _ex_stub,  # WarpBulletsAcrossMediumBarrier
        22: _ex_stub,  # MokouResurrection(符卡 cut-in)
        23: _ex_stub,  # HideSpellCardPresentation
        24: _ex24_publish_captured_count,
        25: _ex_stub,  # UpdateWideRotatingLaserHitbox
        26: _ex26_scripted_update_freeze,
        27: _ex_stub,  # SpawnEnemiesFromMarkedBullets
        28: _ex28_enter_bullet_time,
        29: _ex29_exit_bullet_time,
        30: _ex30_screen_effect_counter,
        31: _ex_stub,  # SpawnBombOrExtendItem(需自机 bomb 状态)
    }
