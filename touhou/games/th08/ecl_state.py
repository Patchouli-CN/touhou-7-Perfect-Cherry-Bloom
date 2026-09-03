"""TH08(东方永夜抄)的 ECL 状态扩展 —— 基类(engine/ecl.py)的作品专属字段下沉。

对照 th08 反编译源码(Reference/th08-ref/src/):
- ``Th08ContextArgs``: th08 上下文变量区(EnemyEclContext,
  EclManager.hpp:217-250): intVariables[8]/floatVariables[8]/
  extraIntVariables[4]/extraFloatVariables[2]/callParameterInts[4]/
  callParameterFloats[4];
- ``Th08EnemyState``: th08 Enemy 的 ECL 可见扩展字段(使魔父链/人妖对齐/
  defer 弹幕/绘制分组等);
- ``Th08EclWorld``: th08 的全局扩展(时间轴门控/事件槽/g_EclCallParameters/
  变量 10097-10100 的 world 快照)。

构造全部由 ``EclMachineTh08`` 的工厂覆写(``_make_enemy_state``/
``_make_world``/``_make_context``, 见 ecl_vm.py)与 ``Th08GameEclHost`` 注入,
引擎层不再知晓这些字段。
"""

from __future__ import annotations

from typing import Optional

import msgspec

from ...engine.ecl import (
    EclContextArgs,
    EclEnemyState,
    EclInstr,
    EclWorld,
    Vec3,
)


class Th08ContextArgs(EclContextArgs):
    """th08 上下文变量区(EclManager.hpp:217-250); th07 不使用。"""

    # ---- th08 上下文变量区(EclManager.hpp:225-230) ----
    th08_ints: list[int] = msgspec.field(default_factory=lambda: [0] * 8)
    th08_floats: list[float] = msgspec.field(default_factory=lambda: [0.0] * 8)
    th08_extra_ints: list[int] = msgspec.field(default_factory=lambda: [0] * 4)
    th08_extra_floats: list[float] = msgspec.field(default_factory=lambda: [0.0] * 2)
    th08_call_ints: list[int] = msgspec.field(default_factory=lambda: [0] * 4)
    th08_call_floats: list[float] = msgspec.field(default_factory=lambda: [0.0] * 4)

    def clone(self) -> "Th08ContextArgs":
        return Th08ContextArgs(
            list(self.int_vars1),
            list(self.float_vars1),
            list(self.int_vars2),
            list(self.float_vars2),
            list(self.global_ints),
            list(self.global_floats),
            list(self.th08_ints),
            list(self.th08_floats),
            list(self.th08_extra_ints),
            list(self.th08_extra_floats),
            list(self.th08_call_ints),
            list(self.th08_call_floats),
        )


class Th08EnemyState(EclEnemyState):
    """th08 专属敌人字段(Reference/th08-ref/src; th07 不使用)。"""

    # saved_context_args 的周期回调快照也须带 th08 变量区(EclManager.hpp
    # 的 EnemyEclContext memcpy), 默认构造换成本子类
    saved_context_args: EclContextArgs = msgspec.field(
        default_factory=Th08ContextArgs
    )
    # positionOffset: worldPosition = position + positionOffset (EclRun.cpp:54-56;
    # op92 使魔继承父位置时置位)
    pos_offset: Vec3 = msgspec.field(default_factory=Vec3)
    # enemy->eclIntVariables[8]/eclFloatVariables[8] (EclOperandsInt.cpp:38-45)
    th08_enemy_ints: list[int] = msgspec.field(default_factory=lambda: [0] * 8)
    th08_enemy_floats: list[float] = msgspec.field(default_factory=lambda: [0.0] * 8)
    anm_alt_bank: int = 0  # flags2 bit2: 用 alternateEnemyAnm 银行 (EclRunLow.inl:457)
    defer_bullet_pattern: int = 0  # ENEMY_FLAG_DEFER_BULLET_PATTERN (op107/108)
    # defer 期间弹幕指令的 memcpy 快照 (EclRunHigh.inl:176-181), 自动射击时重新派发
    pending_shot_instr: Optional[EclInstr] = None
    min_player_dist_sq: float = 0.0  # op82: 距自机过近时压住弹幕 (EclDependencies.cpp:704-710)
    form_effect: int = 0  # op83: flags2 formEffect
    no_sprite: int = 0  # op79-81 bit3: ENEMY_FLAG_NO_SPRITE
    draw_group: int = 0  # op156/159: 绘制分组
    youkai_aligned: int = 0  # flags1 YOUKAI_ALIGNED (使魔 spawn 时按自机人妖定)
    point_item_drop_count: int = 0  # op144 pointItemDropCount
    power_or_point_item_drop_count: int = 0  # op144 powerOrPointItemDropCount
    phase_starting_life: int = 0  # op131/177 phaseStartingLife
    extra_vm_fixed_offset: int = 0  # op182: flags2 extraVmFixedOffset
    no_damage_during_stop: int = 0  # op183: flags1 noDamageDuringStop
    difficulty_mask_override: int = 0  # eclDifficultyMaskOverride (EclRun.cpp:67-74)
    # 使魔父链: linkedChildCount(EclRunLow.inl:788-790; 链本体在 th08 宿主侧,
    # 喂 VM 变量 10096, EclOperandsInt.cpp:125-129)
    linked_child_count: int = 0


class Th08EclWorld(EclWorld):
    """th08 专属世界字段(Reference/th08-ref/src; th07 不使用)。"""

    # op175: EnemyManager.suppressTimelineSpawns (EclRunHigh.inl:953), 时间轴生敌门控
    suppress_timeline_spawns: int = 0
    # 时间轴 op13/14 的事件槽 (EnemyManager.timelineEventSlots[4], EnemyTimeline.cpp:253-282)
    timeline_event_slots: list[int] = msgspec.field(default_factory=lambda: [-1] * 4)
    opcode163_value: int = 0  # op163: EnemyManager.opcode163Value (EclRunHigh.inl:425)
    # th08: g_EclCallParameters(EclGlobals.cpp:105) —— C 是全局静态(全敌人共享,
    # sub 调用时拷入新上下文, EclDependencies.cpp:485-487), 这里按 world 共享
    # (同一 world 的全部 EclMachineTh08 引用同一对列表)
    ecl_call_params_ints: list[int] = msgspec.field(default_factory=lambda: [0] * 4)
    ecl_call_params_floats: list[float] = msgspec.field(
        default_factory=lambda: [0.0] * 4
    )
    # th08 world 快照(变量 10097-10100 的读源, 每帧由宿主 frame_update 同步):
    # 10097 自机妖形态(EclOperandsInt.cpp:138)/10098 时刻符点 Last Spell 状态
    # (:139-144)/10099 符卡取得状态(:145-147)/10100 符卡计时(:148)
    player_is_youkai: int = 0
    last_spell_orb_status: int = 0
    spellcard_capture_status: int = 0
    spellcard_timer_frames: int = 0
