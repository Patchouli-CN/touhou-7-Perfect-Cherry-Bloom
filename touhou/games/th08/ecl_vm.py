"""TH08(东方永夜抄)专属 ECL 虚拟机实现 —— 阶段 2 单 B: 全量。

对照 th08 反编译源码(Reference/th08-ref/src/):
- ``Th08EclOpcode``: 全 184 条(EclRunLow.inl:223-929 = 1-92,
  EclRunHigh.inl:163-972 = 93-184; 跳表清单 EclRunHigh.inl:16-37);
- ``Th08EclVarId``: 变量空间 10000-10100(EclOperandsInt.cpp:26-150 读/
  :156-201 int 写, EclOperandsFloat.cpp:23-146 float 读/:155-211 float 写);
- ``EclMachineTh08``: 变量系统全路由 + 184 条 handler。

指令分层:
- 核心 1-53(stop/wait/nop/跳转/算术/三角/lerp/插值/条件跳)从
  engine/ecl_std_ops 按 th08 编号表(``_TH08_CORE_OPS``)注册; th07↔th08
  编号对照(仅 1 号同号, 其余系统性错位)见 ecl_std_ops 模块 docstring。
  其中 sub_call(52)/sub_ret(53) 注册后又以 th08 语义重登记
  (调用参数快照 g_EclCallParameters + child 块栈下溢释放, 见下)。
- th08 独有的 2 操作数算术(10-19)/polar(38)/dist(39) 按 EclRunLow.inl 新写;
  anm/移动/弹幕/激光/符卡系照 th07 同语义 handler 改编(games/th07/ecl_vm.py
  是模板), 效果类全走 host 接口(EclHost 的 th08 no-op 接缝)。
- **child 上下文块**(op135): 主上下文 + 最多 4 个 child 块轮询
  (EclRun.cpp:188-202), 每块 = 独立 context + 调用栈(16 层,
  EclManager.hpp:255-268); step 覆写轮询, 帧级移动积分(C UpdateMovement)
  在全部上下文跑完后统一做一次 —— 基类 _update_movement 据此延后。
- **EX 指令**: op136/137 路由 ``host.run_ex_instr``(32 条表
  EclGlobals.cpp:65-98, 语义在 games/th08/ecl_host.py)。

双计时器核对结论: 共享核 wait(op2) 写 ``ctx.wait_timer``, th08 C 写
secondaryTime(EclRun.cpp:58-65) —— 递减/回退 time/退出的语义与基类
wait_timer 逐行同构, 直接复用。
"""

from __future__ import annotations

import math
import struct
from enum import IntEnum
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .ecl_host import Th08GameEclHost  # 仅类型检查期(cast 收窄宿主用)

from ...engine.ecl import (
    EclContext,
    EclEnemyState,
    EclInstr,
    Vec3,
)
from ...engine.ecl_base import EclMachineBase
from ...engine.ecl_std_ops import CoreOps, register_core_ops
from ...logger import logger as log
from ...registry import register_ecl
from ...utils import (
    ZUN_2PI,
    ZUN_PI,
    add_normalize_angle,
    cdiv,
    cmod,
    f32,
    i32,
)
from .ecl_file import EclFileTh08

# 人妖门控的 transformFlags 位 (BulletManager.hpp; DispatchShotInstruction
# 检查, EclDependencies.cpp:697-702)
_BULLET_TRANSFORM_ONLY_WHEN_PLAYER_YOUKAI = 0x100
_BULLET_TRANSFORM_ONLY_WHEN_PLAYER_HUMAN = 0x200
# 弹幕发射音标志位 (BulletManager.hpp:141; op113 与 th07 SET_BULLET_SOUND 同值)
_BULLET_TRANSFORM_PLAY_SPAWN_SOUND = 0x200


class Th08EclOpcode(IntEnum):
    """th08 全 184 条 ECL 指令(出处见模块 docstring)。"""

    STOP = 1  # RunEcl 返回 ZUN_ERROR(脚本结束/despawn)
    WAIT = 2  # secondaryTime = arg0
    NOP = 3  # 普通前进路径(84/85 同, EclRunLow.inl:10-11)
    JUMP = 4
    DEC_JUMP = 5
    SET_INT = 6
    SET_FLOAT = 7
    RAND_SIGN = 8
    RAND_SIGN_FLOAT = 9
    # 2 操作数算术(th08 独有): arg0 读改写
    ADD_ASSIGN = 10
    SUB_ASSIGN = 11
    MUL_ASSIGN = 12
    DIV_ASSIGN = 13
    MOD_ASSIGN = 14
    ADD_ASSIGN_FLOAT = 15
    SUB_ASSIGN_FLOAT = 16
    MUL_ASSIGN_FLOAT = 17
    DIV_ASSIGN_FLOAT = 18
    MOD_ASSIGN_FLOAT = 19
    # 3 操作数算术: arg0 = arg1 op arg2
    ADD = 20
    SUB = 21
    MUL = 22
    DIV = 23
    MOD = 24
    ADD_FLOAT = 25
    SUB_FLOAT = 26
    MUL_FLOAT = 27
    DIV_FLOAT = 28
    MOD_FLOAT = 29
    INC = 30
    DEC = 31
    SIN = 32
    COS = 33
    ATAN2 = 34
    LERP = 35  # ApplyInterpolationOperation(EclDependencies.cpp:279-291)
    INIT_INTERP = 36  # InstallInterpolationSlot(EclDependencies.cpp:350-377)
    NORMALIZE_ANGLE = 37
    VEC_FROM_ANGLE_MAG = 38  # polar, 角度先 normalize(EclRunLow.inl:368-373)
    DIST = 39  # 两点距离(EclRunLow.inl:375-388)
    JUMP_IF_EQ = 40
    JUMP_IF_EQ_FLOAT = 41
    JUMP_IF_NEQ = 42
    JUMP_IF_NEQ_FLOAT = 43
    JUMP_IF_LT = 44
    JUMP_IF_LT_FLOAT = 45
    JUMP_IF_LEQ = 46
    JUMP_IF_LEQ_FLOAT = 47
    JUMP_IF_GT = 48
    JUMP_IF_GT_FLOAT = 49
    JUMP_IF_GEQ = 50
    JUMP_IF_GEQ_FLOAT = 51
    SUB_CALL = 52  # CallSubOnEnemy(EclDependencies.cpp:466-495)
    SUB_RET = 53  # PopEclContext(EclDependencies.cpp:498-530)
    SET_ANM = 54
    SET_MOVE_ANM_SEQ = 55  # 主 anm 6 脚本 = arg0..arg0+5
    SET_MOVE_ANM = 56  # 主 anm 6 脚本 = arg0..arg5
    SET_SUB_ANM = 57
    SET_ANM_ALT = 58  # 54-57 的 alternateEnemyAnm 银行版(flags2 bit2)
    SET_MOVE_ANM_SEQ_ALT = 59
    SET_MOVE_ANM_ALT = 60
    SET_SUB_ANM_ALT = 61
    SET_SPECIAL_ANM = 62  # 用 anmScripts.special
    SET_POS = 63
    MOVE_POS_TIME = 64  # ConfigureRelativeMotion(EclHelpers.cpp:59-87)
    SET_MOVE_POLAR = 65  # angle+speed 直飞(movementMode 1)
    MOVE_DIR_TIME = 66  # 限时角度位移(<=0 直飞, >0 ConfigurePolarMotion)
    MOVE_BOUNDARY_AWARE = 67  # BeginBoundaryAwareMove(EclDependencies.cpp:122-185)
    MOVE_AT_PLAYER = 68
    MOVE_AT_PLAYER_TIME = 69
    SET_ANGULAR_VEL = 70
    SET_MOVE_ACCEL = 71
    MOVE_ORBIT = 72  # 绕指定原点轨道(movementMode 3)
    MOVE_ORBIT_AROUND_SELF = 73  # 绕当前位置轨道(半径 0 起)
    SET_ORBIT_VELS = 74  # 轨道角速度+径向速度
    SET_MOVEMENT_BOUNDS = 75
    DISABLE_MOVEMENT_BOUNDS = 76
    SET_HITBOX_SIZE = 77
    SET_GRAZE_SIZE = 78  # secondaryHitboxDimensions
    SET_ENEMY_FLAGS = 79  # flags1/2 按掩码直接赋值(前三位反相)
    CLEAR_ENEMY_FLAGS = 80  # 掩码置位 → 对应能力关
    ENABLE_ENEMY_FLAGS = 81  # 掩码置位 → 对应能力开
    SET_MIN_PLAYER_DISTANCE = 82  # 距自机过近压住弹幕(存平方)
    SET_FORM_EFFECT = 83  # flags2 formEffect
    NOP_FILL_84 = 84  # 普通前进路径(EclRunLow.inl:690-692)
    NOP_FILL_85 = 85
    GET_BOSS_INT = 86  # 以别的 boss 为上下文取变量(th07 43/44 同语义)
    GET_BOSS_FLOAT = 87
    CALL_SUB_ON_BOSS = 88  # 让指定 boss 压栈调 sub
    SET_BOSS_PENDING_SUB = 89  # boss pendingEclSubroutineIndex
    SPAWN_FAMILIAR = 90  # 使魔: 脚本坐标(EclRunLow.inl:737-796)
    SPAWN_FAMILIAR_REL = 91  # 使魔: 父位置偏移(EclRunLow.inl:797-856)
    SPAWN_FAMILIAR_INHERIT = 92  # 使魔: 继承父位置(EclRunLow.inl:857-929)
    SPAWN_ENEMY_ABS = 93
    SPAWN_ENEMY_REL = 94
    REMOVE_ALL_ENEMIES = 95  # KillAllNonBossEnemies(8000, 0)
    SPAWN_BULLET_PATTERN_SPREAD_AIMED = 96
    SPAWN_BULLET_PATTERN_SPREAD_ABS = 97
    SPAWN_BULLET_PATTERN_RING_AIMED = 98
    SPAWN_BULLET_PATTERN_RING_ABS = 99
    SPAWN_BULLET_PATTERN_RING_SHIFTED_AIMED = 100
    SPAWN_BULLET_PATTERN_RING_SHIFTED_ABS = 101
    SPAWN_BULLET_PATTERN_ANGLE_RANDOM = 102
    SPAWN_BULLET_PATTERN_RING_SPEED_RANDOM = 103
    SPAWN_BULLET_PATTERN_RANDOM = 104
    SET_SHOOT_INTERVAL = 105
    SET_SHOOT_INTERVAL_RAND = 106
    DEFER_BULLET_PATTERN = 107  # ENEMY_FLAG_DEFER_BULLET_PATTERN 置位
    DISABLE_DEFER_BULLET_PATTERN = 108
    SPAWN_PREV_BULLET_PATTERN = 109  # 按持久 descriptor 再发一次
    SET_SHOOT_OFFSET = 110
    INIT_BULLET_CMD = 111  # bulletSpawnDescriptor.transforms 记录
    CLEAR_BULLETS_FOR_TRANSITION = 112
    SET_BULLET_SOUND = 113
    SPAWN_LASER_PATTERN = 114  # BULLET_AIM_FAN
    SPAWN_LASER_PATTERN_AIMED = 115  # BULLET_AIM_FAN_AIMED
    SET_LASER_IDX = 116
    ADD_LASER_ANGLE = 117
    AIM_LASER_AT_PLAYER = 118
    SET_LASER_POS_REL = 119
    TEST_LASER_IN_USE = 120  # 写 extraIntVariables[2](10038)
    STOP_LASER = 121
    BEGIN_SPELLCARD = 122  # StartEnemySpell(EclDependencies.cpp:39-49)
    END_SPELLCARD = 123
    PLAY_SOUND = 124
    RUN_PENDING_SUB = 125  # pendingSub → eclSubroutineIds[idx] 压栈调用
    SET_INTERRUPT = 126  # eclSubroutineIds[arg1] = arg0
    SET_BOSS = 127
    SPAWN_ATTACHED_EFFECT = 128  # 附着特效(不接)
    SET_DEATH_TYPE = 129
    SET_DEATH_CALLBACK_SUB = 130
    SET_LIFE = 131  # phaseStartingLife = life = maxLife
    SET_TIMER = 132  # bossTimer
    SET_LIFE_CALLBACK = 133  # 血量阈值回调(idx, threshold, sub)
    SET_TIMER_CALLBACK = 134  # 计时器回调(threshold, sub), bossTimer=0
    SET_CHILD_CONTEXT = 135  # child 上下文块(EclRunHigh.inl:580-613)
    RUN_EX_INS = 136  # 立即跑一次 EX 指令
    SET_EX_INS = 137  # 注册每帧 EX 回调(<0 注销)
    SET_DEATH_ANM = 138
    SPAWN_EFFECT = 139  # 特效(不接)
    SPAWN_EFFECT_VELOCITY = 140  # 带速度特效(不接)
    SPAWN_ITEM = 141
    SPAWN_ITEMS = 142  # 火力未满大P+小P, 满火力全点(EclRunHigh.inl:644-665)
    SET_ITEM_DROP = 143
    SET_ITEM_DROP_COUNTS = 144  # 点道具数/火力或点道具数
    SET_VM_AUTO_ROTATE = 145  # rotateAnmWithMovement
    ADD_TIME = 146
    SET_STAGE_SCRIPT_LABEL = 147  # Background.pendingStageScriptLabel
    SET_BOSS_LIFE_MARKERS = 148  # Gui.SetBossLifeMarkerCount
    SET_PRIMARY_VM_INTERRUPT = 149
    SET_VM_INTERRUPT = 150  # secondaryVms[idx].pendingInterrupt
    SET_NO_STACK_RET = 151  # disableEclCallStack
    SET_BULLET_RANK_PARAMS = 152  # bulletRankInfluence
    BIND_TIMER_CALLBACK_TO_DEATH = 153
    CLEAR_LASERS = 154
    SET_TIMEOUT_SPELL = 155  # timeoutSpell(生存符)
    SET_SPECIAL_INTERACTION = 156  # specialInteraction + drawGroup=2
    SET_TRAIL = 157
    SET_BOSS_GAUGE_SLOT = 158  # Gui.SetBossGaugeSlot+Color
    SET_DRAW_GROUP = 159
    SET_DAMAGE_REDUCTION_TIMER = 160
    REMOVE_BULLETS_RADIUS = 161
    REMOVE_ALL_BULLETS_DESPAWN = 162  # RemoveAllBullets(4): 直接消弹不掉道具
    SET_ENEMY_MANAGER_VALUE = 163  # EnemyManager.opcode163Value
    SET_SPELLCARD_EFFECT_TRACKING = 164
    SET_PRIMARY_VM_ROT_Z = 165
    VEC_FROM_ANGLE_MAG_RAW = 166  # polar, 角度不 normalize(th07 151 同型)
    SET_LASER_ANGLE = 167
    SPAWN_POINT_ITEMS = 168
    RAND_EXIT_ANGLE = 169  # 简化版出场随机角(EclRunHigh.inl:882-893)
    SET_LASER_HIDE_WARNING = 170  # hideCapDuringStartup
    SET_LASER_START_LEN = 171
    SET_LASER_OFFSETS = 172
    SET_PAUSE_TIMER = 173  # pauseTimer(炸弹中冻结 ECL 推进)
    SPAWN_ALIGNMENT_EFFECT = 174  # 人妖对齐特效(结界光环)
    SUPPRESS_TIMELINE_SPAWNS = 175  # 时间轴生敌全局抑制
    SET_LAST_SPELL_FLAGS = 176  # Last Spell 标志位 + pause timer
    SET_PHASE_START_LIFE = 177
    MOVE_RANDOM_BIASED = 178  # ApplyRandomBiasedMove(EclDependencies.cpp:188-274)
    START_STAGE_BACKGROUND_SEQUENCE = 179
    HIDE_CLOCK = 180  # Gui.HideClockTime
    ADVANCE_CLOCK = 181  # 时刻 +1 封顶 12(EclRunHigh.inl:957-967)
    SET_EXTRA_VM_FIXED_OFFSET = 182
    SET_NO_DAMAGE_DURING_STOP = 183
    SET_BONUS_UPDATES_DISABLED = 184


class Th08EclVarId(IntEnum):
    """th08 变量空间 10000-10100(0x2710-0x2774)。

    读路由: EclOperandsInt.cpp:26-150(int)/EclOperandsFloat.cpp:23-146(float);
    写路由(可写子集): EclOperandsInt.cpp:156-201/EclOperandsFloat.cpp:155-211。
    连续区间只标首成员: LOCAL_INT0=10000 表示 10000-10007 的 intVariables[8]。
    """

    LOCAL_INT0 = 10000  # 上下文 intVariables[0..7] = 10000-10007
    ENEMY_INT0 = 10008  # enemy->eclIntVariables[0..7] = 10008-10015
    LOCAL_FLOAT0 = 10016  # 上下文 floatVariables[0..7] = 10016-10023
    ENEMY_FLOAT0 = 10024  # enemy->eclFloatVariables[0..7] = 10024-10031
    RNG_INT = 10032  # g_Rng u32 & 0x7fffffff
    RNG_UNIT_INT = 10033  # (i32)GetRandomF32
    RNG_U32 = 10034  # (i32)GetRandomU32
    RNG_SIGNED_INT = 10035  # (i32)GetRandomF32Signed
    EXTRA_INT0 = 10036  # 上下文 extraIntVariables[0..3] = 10036-10039
    DIFFICULTY = 10040
    RANK = 10041
    POS_X = 10042  # worldPosition(读)/position(写) = 10042-10044
    POS_Y = 10043
    POS_Z = 10044
    PLAYER_POS_X = 10045  # 自机位置 = 10045-10047
    PLAYER_POS_Y = 10046
    PLAYER_POS_Z = 10047
    ANGLE_TO_PLAYER = 10048
    BOSS_TIMER = 10049
    DIST_TO_PLAYER = 10050
    LIFE = 10051
    SHOT_TYPE = 10052
    CALL_INT0 = 10053  # callParameterInts[0..3] = 10053-10056
    CALL_FLOAT0 = 10057  # callParameterFloats[0..3] = 10057-10060
    GLOBAL_CALL_INT0 = 10061  # g_EclCallParameters.ints[0..3] = 10061-10064
    GLOBAL_CALL_FLOAT0 = 10065  # g_EclCallParameters.floats[0..3] = 10065-10068
    ANGLE = 10069  # movementAngle
    ANGULAR_VELOCITY = 10070
    MOVE_SPEED = 10071
    MOVE_ACCEL = 10072
    ORBIT_RADIUS = 10073
    INTERP_ORIGIN_X = 10074  # movementInterpolationOrigin = 10074-10076
    INTERP_ORIGIN_Y = 10075
    INTERP_ORIGIN_Z = 10076
    ORBIT_ANGLE = 10077
    ORBIT_ANGULAR_VELOCITY = 10078
    INTERP_DELTA_X = 10079  # movementInterpolationDelta = 10079-10081
    INTERP_DELTA_Y = 10080  # (int 读 10079-10082 走 default 原样返回,
    INTERP_DELTA_Z = 10081  #  EclOperandsInt.cpp:25)
    RNG_RADIAN = 10082  # 仅 float 读: 随机弧度 [-π, π)
    LAST_DAMAGE = 10083
    BOSS_SLOT = 10084
    DELTA_POS_X = 10085  # lastFrameDisplacement = 10085-10087
    DELTA_POS_Y = 10086
    DELTA_POS_Z = 10087
    LIFE_CALLBACK0 = 10088  # lifeCallbackThresholds[0..3] = 10088-10091
    ITEM_DROP = 10092
    SCORE = 10093
    EXTRA_FLOAT0 = 10094  # 上下文 extraFloatVariables[0..1] = 10094-10095
    PARENT_CHAIN_COUNT = 10096  # 父链个数(阶段 3 链接入, 现恒 0)
    PLAYER_IS_YOUKAI = 10097  # 妖形态(g_Player.IsYoukai)
    LAST_SPELL_ORBS = 10098  # 时刻符点 Last Spell 判定(仅 int 读)
    SPELLCARD_CAPTURED = 10099  # 符卡取得状态
    SPELLCARD_TIMER = 10100  # 符卡计时(仅 int 读)


class _ChildEclBlock:
    """op135 的 child 上下文块(EnemyChildEclBlock, EclManager.hpp:255-268):

    独立 context + 独立调用栈(C 是 16 层定长数组, 这里沿用基类
    _MAX_STACK=15 的压栈上限语义)。"""

    __slots__ = ("sub_id", "context", "stack")

    def __init__(self, sub_id: int) -> None:
        self.sub_id = sub_id
        self.context = EclContext(sub_id=sub_id)
        self.stack: list[EclContext] = []


@register_ecl("th08", file_format=EclFileTh08)
class EclMachineTh08(EclMachineBase):
    """TH08 的 ECL VM: 框架(``EclMachineBase``) + 本作变量映射/handler。

    作品无关核心 handler 经 ``register_core_ops`` 注册(本模块底部
    ``_TH08_CORE_OPS``); 作品专属 handler 用 ``@EclMachineTh08.register``
    登记。child 上下文块(op135)的轮询在 step 覆写里。
    """

    # 插值写位置分量(10042-10044)时回算 axis_speed(见基类 _step_interps)
    _INTERP_POS_VARS = (
        Th08EclVarId.POS_X,
        Th08EclVarId.POS_Y,
        Th08EclVarId.POS_Z,
    )
    # th08 的 32 条 EX 无空操作槽(EclGlobals.cpp:65-98), 关掉基类的 idx==3 短路
    _EX_NOOP_IDX = -1

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # g_EclCallParameters(EclGlobals.cpp:105): C 是全局静态, 这里按机器
        # (= 敌人)持有; 跨敌人传递(父→spawn 子)是 world 阶段接线
        self.call_params_ints: list[int] = [0] * 4
        self.call_params_floats: list[float] = [0.0] * 4
        # child 上下文块 4 槽(op135; EclManager.hpp:255-268)
        self._child_blocks: list[Optional[_ChildEclBlock]] = [None] * 4
        self._active_child_slot = -1  # 正在轮询的 child 槽(-1 = 主上下文)
        self._child_returned = False  # child 块 ret 栈下溢信号(释放块用)
        self._in_context_polling = False  # 轮询期间抑制逐上下文的移动积分

    # ---- 框架钩子 ----

    def _difficulty_skip(self, instr: EclInstr) -> bool:
        """th08 难度掩码: 指令掩码需完整包含(全局难度位 | 敌人覆盖位)才执行
        (EclRun.cpp:67-74); 单难度位且 override=0 时与 th07 语义等价。"""
        eff = self.world.difficulty_mask | self.enemy.difficulty_mask_override
        return (instr.skip_difficulty & eff) != eff

    def _update_movement(self) -> None:
        # C: UpdateMovement 在全部上下文(主+child)跑完后统一执行一次
        # (EclRun.cpp:204-206); 轮询期间的逐上下文 _frame_update 跳过
        if self._in_context_polling:
            return
        super()._update_movement()

    def _update_shoot(self) -> None:
        # C: UpdateShotAndAnm 同样在全部上下文跑完后统一执行一次
        # (EclRun.cpp:207); 轮询期间跳过, step 收尾补一次
        if self._in_context_polling:
            return
        super()._update_shoot()

    def _auto_shoot(self) -> None:
        # C: UpdateShotAndAnm 用 pendingShotInstruction 重新派发
        # (EclDependencies.cpp:791-802) —— 操作数每次发射时重新解析
        instr = self.enemy.pending_shot_instr
        if instr is not None:
            self._fire_shot(instr)

    def step(self) -> bool:
        """推进一帧: 主上下文 + child 块轮询(EclRun.cpp:45-202)。

        轮询顺序照 C: 主上下文先跑一帧, 然后 child 槽 0..3 顺序各跑一帧;
        child 块 ret 栈下溢 = 释放该块继续下一块(PopEclContext 返回 1 →
        LOW_SELECT_NEXT_CONTEXT, EclDependencies.cpp:508-524); child 里
        STOP/跑飞与主上下文同罪(整个 RunEcl 返回 ZUN_ERROR)。
        """
        if self.finished:
            return False
        self._in_context_polling = True
        try:
            if not self._run_ecl():  # 主上下文
                self.finished = True
                return False
            for slot in range(4):
                block = self._child_blocks[slot]
                if block is None:
                    continue
                saved_current, saved_stack = self.current, self.stack
                self.current, self.stack = block.context, block.stack
                self._active_child_slot = slot
                self._child_returned = False
                ok = self._run_ecl()
                # ret 弹栈会换掉 current 对象, 写回块(stack 列表本身原地变化)
                block.context, block.stack = self.current, self.stack
                self.current, self.stack = saved_current, saved_stack
                self._active_child_slot = -1
                if not ok:
                    if self._child_returned:
                        self._child_blocks[slot] = None  # 释放块, 轮询继续
                        continue
                    self.finished = True
                    return False
        finally:
            self._in_context_polling = False
        # OnUpdate 收尾(顺序同基类 step): 移动积分 → ClampPos → Move → 计时;
        # 自动射击在全部上下文跑完后补一次(见 _update_shoot 注释)
        e = self.enemy
        if not e.disable_movement:
            self._update_movement()
            e.clamp_pos()
            self._move()
            e.clamp_pos()
        if e.life > 0:
            self._update_shoot()
        e.timer = i32(e.timer + 1)
        if e.invincibility_timer > 0:
            e.invincibility_timer -= 1
        return True

    # ---- 变量系统(EclOperands::ResolveInt/ResolveIntLValue +
    #      Enemy::ResolveFloat/ResolveFloatLValue) ----

    def _world_pos(self) -> Vec3:
        """worldPosition = position + positionOffset(EclRun.cpp:54-56)。"""
        e = self.enemy
        return e.pos + e.pos_offset

    def _get_int(self, var_id: int) -> int:
        e, w, a = self.enemy, self.world, self.current.args
        if not (10000 <= var_id <= 10100):
            return var_id  # C default: 原样返回(即立即数)
        if 10000 <= var_id <= 10007:
            return a.th08_ints[var_id - 10000]
        if 10008 <= var_id <= 10015:
            return e.th08_enemy_ints[var_id - 10008]
        if 10016 <= var_id <= 10023:
            return int(a.th08_floats[var_id - 10016])  # C: f32→i32 截断
        if 10024 <= var_id <= 10031:
            return int(e.th08_enemy_floats[var_id - 10024])
        if var_id == Th08EclVarId.RNG_INT:
            return w.rng.u32() & 0x7FFFFFFF
        if var_id == Th08EclVarId.RNG_UNIT_INT:
            return int(w.rng.unit())
        if var_id == Th08EclVarId.RNG_U32:
            return i32(w.rng.u32())
        if var_id == Th08EclVarId.RNG_SIGNED_INT:
            return int(w.rng.unit() * 2.0 - 1.0)
        if 10036 <= var_id <= 10039:
            return a.th08_extra_ints[var_id - 10036]
        if var_id == Th08EclVarId.DIFFICULTY:
            return w.difficulty
        if var_id == Th08EclVarId.RANK:
            return w.rank
        if 10042 <= var_id <= 10044:
            wp = self._world_pos()
            return int((wp.x, wp.y, wp.z)[var_id - 10042])
        if 10045 <= var_id <= 10047:
            p = w.player_pos
            return int((p.x, p.y, p.z)[var_id - 10045])
        if var_id == Th08EclVarId.ANGLE_TO_PLAYER:
            return int(w.angle_to_player(self._world_pos()))
        if var_id == Th08EclVarId.BOSS_TIMER:
            return e.timer
        if var_id == Th08EclVarId.DIST_TO_PLAYER:
            return int((w.player_pos - self._world_pos()).length)
        if var_id == Th08EclVarId.LIFE:
            return e.life
        if var_id == Th08EclVarId.SHOT_TYPE:
            return w.player_shottype
        if 10053 <= var_id <= 10056:
            return a.th08_call_ints[var_id - 10053]
        if 10057 <= var_id <= 10060:
            return int(a.th08_call_floats[var_id - 10057])
        if 10061 <= var_id <= 10064:
            return self.call_params_ints[var_id - 10061]
        if 10065 <= var_id <= 10068:
            return int(self.call_params_floats[var_id - 10065])
        if var_id == Th08EclVarId.ANGLE:
            return int(e.angle)
        if var_id == Th08EclVarId.ANGULAR_VELOCITY:
            return int(e.angular_velocity)
        if var_id == Th08EclVarId.MOVE_SPEED:
            return int(e.move_speed)
        if var_id == Th08EclVarId.MOVE_ACCEL:
            return int(e.move_acceleration)
        if var_id == Th08EclVarId.ORBIT_RADIUS:
            return int(e.move_radius)
        if 10074 <= var_id <= 10076:
            o = e.move_interp_start_pos
            return int((o.x, o.y, o.z)[var_id - 10074])
        if var_id == Th08EclVarId.ORBIT_ANGLE:
            return int(e.move_angle)
        if var_id == Th08EclVarId.ORBIT_ANGULAR_VELOCITY:
            return int(e.move_angular_velocity)
        # 10079-10082(int 读)走 default 原样返回(EclOperandsInt.cpp:25)
        if var_id == Th08EclVarId.LAST_DAMAGE:
            return e.last_damage
        if var_id == Th08EclVarId.BOSS_SLOT:
            return e.boss_id
        if 10085 <= var_id <= 10087:
            d = e.delta_pos
            return int((d.x, d.y, d.z)[var_id - 10085])
        if 10088 <= var_id <= 10091:
            return e.life_callback_threshold[var_id - 10088]
        if var_id == Th08EclVarId.ITEM_DROP:
            return e.item_drop
        if var_id == Th08EclVarId.SCORE:
            return e.score
        if 10094 <= var_id <= 10095:
            return int(a.th08_extra_floats[var_id - 10094])
        if var_id == Th08EclVarId.PARENT_CHAIN_COUNT:
            return 0  # 父/附着链模型是 world 阶段接线, 现恒 0
        if var_id == Th08EclVarId.PLAYER_IS_YOUKAI:
            # world 阶段接线: 自机妖形态(world 暂无此属性, 默认 0)
            return int(getattr(w, "player_is_youkai", 0))
        if var_id == Th08EclVarId.LAST_SPELL_ORBS:
            # world 阶段接线: 时刻符点 Last Spell 状态(默认 0)
            return int(getattr(w, "last_spell_orb_status", 0))
        if var_id == Th08EclVarId.SPELLCARD_CAPTURED:
            # world 阶段接线: 符卡取得状态(默认 0)
            return int(getattr(w, "spellcard_capture_status", 0))
        if var_id == Th08EclVarId.SPELLCARD_TIMER:
            # world 阶段接线: 符卡计时(默认 0)
            return int(getattr(w, "spellcard_timer_frames", 0))
        return var_id  # C default: 原样返回

    def _set_int(self, var_id: int, value: int) -> None:
        """ResolveIntLValue 的可写集合; 不在集合里的写入被丢弃
        (C 写进指令内存, 无意义)。"""
        e, w, a = self.enemy, self.world, self.current.args
        value = i32(value)
        if 10000 <= var_id <= 10007:
            a.th08_ints[var_id - 10000] = value
        elif 10008 <= var_id <= 10015:
            e.th08_enemy_ints[var_id - 10008] = value
        elif 10036 <= var_id <= 10039:
            a.th08_extra_ints[var_id - 10036] = value
        elif var_id == Th08EclVarId.DIFFICULTY:
            w.difficulty = value
        elif var_id == Th08EclVarId.RANK:
            w.rank = value
        elif var_id == Th08EclVarId.BOSS_TIMER:
            e.timer = value
        elif var_id == Th08EclVarId.LIFE:
            e.life = value
        elif 10053 <= var_id <= 10056:
            a.th08_call_ints[var_id - 10053] = value
        elif 10061 <= var_id <= 10064:
            self.call_params_ints[var_id - 10061] = value
        elif var_id == Th08EclVarId.ITEM_DROP:
            e.item_drop = value
        elif var_id == Th08EclVarId.SCORE:
            e.score = value
        # default: 丢弃

    def _get_float(self, var_id: int) -> float:
        return self._get_float_value(var_id, float(var_id))

    def _get_float_value(self, var_id: int, raw: float) -> float:
        """Enemy::ResolveFloat: var_id 是 (i32) 转换后的值, raw 是原始 f32
        (默认值; 未命中变量表时原样返回)。"""
        e, w, a = self.enemy, self.world, self.current.args
        if not (10000 <= var_id <= 10100):
            return raw
        if 10000 <= var_id <= 10007:
            return float(a.th08_ints[var_id - 10000])
        if 10008 <= var_id <= 10015:
            return float(e.th08_enemy_ints[var_id - 10008])
        if 10016 <= var_id <= 10023:
            return a.th08_floats[var_id - 10016]
        if 10024 <= var_id <= 10031:
            return e.th08_enemy_floats[var_id - 10024]
        if var_id == Th08EclVarId.RNG_INT:
            return float(w.rng.u32() & 0x7FFFFFFF)
        if var_id == Th08EclVarId.RNG_UNIT_INT:
            return w.rng.unit()
        if var_id == Th08EclVarId.RNG_U32:
            return float(i32(w.rng.u32()))
        if var_id == Th08EclVarId.RNG_SIGNED_INT:
            return w.rng.unit() * 2.0 - 1.0
        if 10036 <= var_id <= 10039:
            return float(a.th08_extra_ints[var_id - 10036])
        if var_id == Th08EclVarId.DIFFICULTY:
            return float(w.difficulty)
        if var_id == Th08EclVarId.RANK:
            return float(w.rank)
        if 10042 <= var_id <= 10044:
            wp = self._world_pos()
            return (wp.x, wp.y, wp.z)[var_id - 10042]
        if 10045 <= var_id <= 10047:
            p = w.player_pos
            return (p.x, p.y, p.z)[var_id - 10045]
        if var_id == Th08EclVarId.ANGLE_TO_PLAYER:
            return w.angle_to_player(self._world_pos())
        if var_id == Th08EclVarId.BOSS_TIMER:
            return float(e.timer)
        if var_id == Th08EclVarId.DIST_TO_PLAYER:
            return (w.player_pos - self._world_pos()).length
        if var_id == Th08EclVarId.LIFE:
            return float(e.life)
        if var_id == Th08EclVarId.SHOT_TYPE:
            return float(w.player_shottype)
        if 10053 <= var_id <= 10056:
            return float(a.th08_call_ints[var_id - 10053])
        if 10057 <= var_id <= 10060:
            return a.th08_call_floats[var_id - 10057]
        if 10061 <= var_id <= 10064:
            return float(self.call_params_ints[var_id - 10061])
        if 10065 <= var_id <= 10068:
            return self.call_params_floats[var_id - 10065]
        if var_id == Th08EclVarId.ANGLE:
            return e.angle
        if var_id == Th08EclVarId.ANGULAR_VELOCITY:
            return e.angular_velocity
        if var_id == Th08EclVarId.MOVE_SPEED:
            return e.move_speed
        if var_id == Th08EclVarId.MOVE_ACCEL:
            return e.move_acceleration
        if var_id == Th08EclVarId.ORBIT_RADIUS:
            return e.move_radius
        if 10074 <= var_id <= 10076:
            o = e.move_interp_start_pos
            return (o.x, o.y, o.z)[var_id - 10074]
        if var_id == Th08EclVarId.ORBIT_ANGLE:
            return e.move_angle
        if var_id == Th08EclVarId.ORBIT_ANGULAR_VELOCITY:
            return e.move_angular_velocity
        if 10079 <= var_id <= 10081:
            d = e.move_interp
            return (d.x, d.y, d.z)[var_id - 10079]
        if var_id == Th08EclVarId.RNG_RADIAN:
            return w.rng.unit() * ZUN_2PI - ZUN_PI
        if var_id == Th08EclVarId.LAST_DAMAGE:
            return float(e.last_damage)
        if var_id == Th08EclVarId.BOSS_SLOT:
            return float(e.boss_id)
        if 10085 <= var_id <= 10087:
            d = e.delta_pos
            return (d.x, d.y, d.z)[var_id - 10085]
        if 10088 <= var_id <= 10091:
            return float(e.life_callback_threshold[var_id - 10088])
        if var_id == Th08EclVarId.ITEM_DROP:
            return float(e.item_drop)
        if var_id == Th08EclVarId.SCORE:
            return float(e.score)
        if 10094 <= var_id <= 10095:
            return a.th08_extra_floats[var_id - 10094]
        if var_id == Th08EclVarId.PARENT_CHAIN_COUNT:
            return 0.0  # world 阶段接线(同 int 读)
        if var_id == Th08EclVarId.PLAYER_IS_YOUKAI:
            return float(getattr(w, "player_is_youkai", 0))  # world 阶段接线
        if var_id == Th08EclVarId.SPELLCARD_CAPTURED:
            return float(getattr(w, "spellcard_capture_status", 0))  # world 阶段接线
        # 10098(LAST_SPELL_ORBS)在 float 读里走 default(EclOperandsFloat.cpp:144-145)
        return raw

    def _set_float(self, var_id: int, value: float) -> None:
        """ResolveFloatLValue 的可写集合; 其余丢弃。"""
        e, w, a = self.enemy, self.world, self.current.args
        value = f32(value)
        if 10016 <= var_id <= 10023:
            a.th08_floats[var_id - 10016] = value
        elif 10024 <= var_id <= 10031:
            e.th08_enemy_floats[var_id - 10024] = value
        elif 10057 <= var_id <= 10060:
            a.th08_call_floats[var_id - 10057] = value
        elif var_id == Th08EclVarId.POS_X:
            e.pos.x = value
        elif var_id == Th08EclVarId.POS_Y:
            e.pos.y = value
        elif var_id == Th08EclVarId.POS_Z:
            e.pos.z = value
        elif var_id == Th08EclVarId.PLAYER_POS_X:
            w.player_pos.x = value
        elif var_id == Th08EclVarId.PLAYER_POS_Y:
            w.player_pos.y = value
        elif var_id == Th08EclVarId.PLAYER_POS_Z:
            w.player_pos.z = value
        elif 10094 <= var_id <= 10095:
            a.th08_extra_floats[var_id - 10094] = value
        elif 10065 <= var_id <= 10068:
            self.call_params_floats[var_id - 10065] = value
        elif 10074 <= var_id <= 10076:
            o = e.move_interp_start_pos
            if var_id == 10074:
                o.x = value
            elif var_id == 10075:
                o.y = value
            else:
                o.z = value
        elif 10079 <= var_id <= 10081:
            d = e.move_interp
            if var_id == 10079:
                d.x = value
            elif var_id == 10080:
                d.y = value
            else:
                d.z = value
        elif var_id == Th08EclVarId.ANGLE:
            e.angle = value
        elif var_id == Th08EclVarId.ANGULAR_VELOCITY:
            e.angular_velocity = value
        elif var_id == Th08EclVarId.MOVE_SPEED:
            e.move_speed = value
        elif var_id == Th08EclVarId.MOVE_ACCEL:
            e.move_acceleration = value
        elif var_id == Th08EclVarId.ORBIT_RADIUS:
            e.move_radius = value
        elif var_id == Th08EclVarId.ORBIT_ANGLE:
            e.move_angle = value
        elif var_id == Th08EclVarId.ORBIT_ANGULAR_VELOCITY:
            e.move_angular_velocity = value
        # default: 丢弃

    # ---- GET_BOSS 系(op86/87): 以别的 boss 为上下文取变量 ----

    def _peer_args(self, boss: EclEnemyState):
        """boss (EclEnemyState) 当前上下文的 args —— 经 host.enemy_by_state
        反查 boss 机器的 current.args; 查不到(裸跑/无该宿主的单测)退回 None
        (调用方维持自身 args 的旧行为)。同 th07 的同名辅助。"""
        registry = getattr(self.host, "enemy_by_state", None)
        if not registry:
            return None
        peer = registry.get(id(boss))
        return peer.machine.current.args if peer is not None else None

    def _peer_int(self, boss: EclEnemyState, instr: EclInstr, arg_idx: int) -> int:
        """op86: 以别的 boss 为上下文取 int 变量(EclRunLow.inl:694-701)。

        C 的 ResolveInt(boss, ...) 读 boss->activeEclContext, 故本地变量
        须取自 boss 机器的当前 args(同 th07 _peer_int 的修正)。"""
        saved_enemy = self.enemy
        self.enemy = boss
        ctx = self.current
        saved_args = ctx.args
        peer_args = self._peer_args(boss)
        if peer_args is not None:
            ctx.args = peer_args
        try:
            return self._int_arg(instr, arg_idx)
        finally:
            ctx.args = saved_args
            self.enemy = saved_enemy

    def _peer_float(
        self, boss: EclEnemyState, instr: EclInstr, arg_idx: int
    ) -> float:
        """op87: 以别的 boss 为上下文取 float 变量(EclRunLow.inl:703-710)。"""
        saved_enemy = self.enemy
        self.enemy = boss
        ctx = self.current
        saved_args = ctx.args
        peer_args = self._peer_args(boss)
        if peer_args is not None:
            ctx.args = peer_args
        try:
            return self._float_arg(instr, arg_idx)
        finally:
            ctx.args = saved_args
            self.enemy = saved_enemy

    # ---- 作品辅助方法(弹幕/激光/符卡/道具/出场角) ----

    def _fire_shot(self, instr: EclInstr) -> None:
        """DispatchShotInstruction(EclDependencies.cpp:687-780): 96-104 九合一。

        ShotArgs 布局: bulletType i16 @0 / color i16 @2 / count1 @4 /
        count2 @8 / speed1 f32 @0xc / speed2 @0x10 / angle @0x14 /
        angleStep @0x18 / transformFlags @0x1c; 操作数标志位: type=bit0,
        color=bit1, count1=bit2, count2=bit3, speed1=bit4, speed2=bit5,
        angle=bit6, angleStep=bit7。
        """
        e, w = self.enemy, self.world
        p = e.bullet_props
        flags = instr.args[7]  # transformFlags(raw, 不解析)
        # 人妖门控: 与敌人自身的 youkaiAligned 比较(EclDependencies.cpp:697-702)
        if (flags & _BULLET_TRANSFORM_ONLY_WHEN_PLAYER_YOUKAI and not e.youkai_aligned) or (
            flags & _BULLET_TRANSFORM_ONLY_WHEN_PLAYER_HUMAN and e.youkai_aligned
        ):
            return
        # 距自机过近压住弹幕(EclDependencies.cpp:704-710)
        wp = self._world_pos()
        if e.min_player_dist_sq > 0.0:
            dx = wp.x - w.player_pos.x
            dy = wp.y - w.player_pos.y
            if dx * dx + dy * dy < e.min_player_dist_sq:
                return
        p.pos = wp + e.shoot_offset
        sprite = instr.arg_i16(0, 0)
        p.sprite = self._get_int(sprite) if instr.param_mask & 1 else sprite
        p.aim_mode = instr.id - 96
        p.count1 = self._int_arg(instr, 1, 2)
        p.count2 = self._int_arg(instr, 2, 3)
        p.angle1 = self._float_arg(instr, 5, 6)
        p.speed1 = self._float_arg(instr, 3, 4)
        p.angle2 = self._float_arg(instr, 6, 7)
        p.speed2 = self._float_arg(instr, 4, 5)
        if not w.spellcard_active:
            # rank 缩放(EclDependencies.cpp:743-770, 与 th07 同构)
            p.count1 = i32(p.count1 + e.bullet_rank_amount1(w.rank))
            if p.count1 <= 0:
                p.count1 = 1
            p.count2 = i32(p.count2 + e.bullet_rank_amount2(w.rank))
            if p.count2 <= 0:
                p.count2 = 1
            if p.speed1 != 0.0:
                p.speed1 = f32(p.speed1 + e.bullet_rank_speed(float(w.rank)))
                if p.speed1 < 0.3:
                    p.speed1 = 0.3
            p.speed2 = f32(p.speed2 + e.bullet_rank_speed(float(w.rank)) / 2.0)
            if p.speed2 < 0.3:
                p.speed2 = 0.3
        p.flags = flags
        color = instr.arg_i16(0, 1)
        p.sprite_offset = self._get_int(color) if instr.param_mask & 2 else color
        self.host.spawn_bullet_pattern(p)

    def _spawn_laser_pattern(self, instr: EclInstr) -> None:
        """op114/115(EclRunHigh.inl:260-334)。

        LaserSpawnArgs 布局(EclRunHigh.inl:53-76): bulletType u16 @0 /
        color i16 @2 / angle @4 / speed @8 / startOffset @0xc /
        endOffset @0x10 / startLength @0x14 / width @0x18 / startTime @0x1c /
        duration @0x20 / despawnDuration @0x24 / hitboxStartTime @0x28 /
        hitboxEndDelay @0x2c / transformFlags @0x30; 标志位: color=bit1,
        angle=bit2, speed=bit3, startOffset=bit4, endOffset=bit5,
        startLength=bit6, width=bit7, startTime=bit8, duration=bit9,
        despawnDuration=bit10。
        """
        e = self.enemy
        p = e.laser_props
        p.pos = self._world_pos() + e.shoot_offset
        p.sprite = instr.arg_u16(0, 0)  # bulletType(raw, 不解析)
        color = instr.arg_i16(0, 1)
        p.sprite_offset = self._get_int(color) if instr.param_mask & 2 else color
        p.angle1 = self._float_arg(instr, 1, 2)
        p.speed1 = self._float_arg(instr, 2, 3)
        p.start_offset = self._float_arg(instr, 3, 4)
        p.end_offset = self._float_arg(instr, 4, 5)
        p.start_length = self._float_arg(instr, 5, 6)
        p.width = self._float_arg(instr, 6, 7)
        p.start_time = self._int_arg(instr, 7, 8)
        p.duration = self._int_arg(instr, 8, 9)
        p.end_time = self._int_arg(instr, 9, 10)
        p.hitbox_start_time = instr.arg_int(10)  # raw
        p.hitbox_end_time = instr.arg_int(11)  # hitboxEndDelay(raw)
        p.flags = instr.args[12]  # transformFlags(raw)
        # 115 = BULLET_AIM_FAN_AIMED(出生即瞄玩家, 同 th07 MOVING→type 0)
        p.type = 0 if instr.id == Th08EclOpcode.SPAWN_LASER_PATTERN_AIMED else 1
        e.lasers[e.laser_idx & 31] = self.host.spawn_laser_pattern(p)

    def _jitter_pos(self) -> Vec3:
        e, w = self.enemy, self.world
        return Vec3(
            e.pos.x + w.rng.unit() * 128.0 - 64.0,
            e.pos.y + w.rng.unit() * 128.0 - 64.0,
            e.pos.z,
        )

    def _spawn_items(self, num: int) -> None:
        """op142: 火力未满第一个掉大P其余小P, 满火力全掉点
        (EclRunHigh.inl:644-665; g_GameManager.GetPower() < 0x80)。"""
        w = self.world
        for i in range(num):
            pos = self._jitter_pos()
            if w.current_power < 128:
                self.host.spawn_item(pos, 2 if i == 0 else 0)  # POWER_BIG / POWER_SMALL
            else:
                self.host.spawn_item(pos, 1)  # ITEM_POINT

    def _begin_spellcard(self, instr: EclInstr) -> None:
        """StartEnemySpell(EclDependencies.cpp:39-49)。

        EclSpellCardInstructionArgs 布局(EclDependencies.cpp:18-36):
        enemyFace i16 @0xC / spellCardNumber u16 @0xE / bonus i32 @0x10 /
        encodedName[0x30] @0x14(XOR 0xAA, Spellcard.cpp:743) /
        encodedOwner[0x30] @0x44 / comment[0x40]×2 @0x74/0xB4。
        bonus/owner/comment 的传递是 world 阶段接线。
        """
        e = self.enemy
        # args 区偏移 = 指令偏移 - 0x0C(12 字节头): encodedName @args+8
        name_bytes = bytes(b ^ 0xAA for b in instr.raw_arg_bytes()[8:56])
        name = name_bytes.split(b"\x00", 1)[0].decode("shift_jis", errors="replace")
        gui_id = instr.arg_i16(0, 0)
        spellcard_idx = instr.arg_u16(0, 1)
        # C: StartSpell 里 ClearBulletsForTransition(Spellcard.cpp:747)
        self.host.clear_bullets_for_transition()
        # C: ResetBulletRankInfluence(模板默认值, 同 th07 的交接)
        e.bullet_rank_speed_low = -0.5
        e.bullet_rank_speed_high = 0.5
        e.bullet_rank_amount1_low = e.bullet_rank_amount1_high = 0
        e.bullet_rank_amount2_low = e.bullet_rank_amount2_high = 0
        self.host.begin_spellcard(e, gui_id, spellcard_idx, name)

    def _exit_angle(self) -> float:
        """BeginBoundaryAwareMove 的选角(EclDependencies.cpp:126-168):
        朝屏幕外逃的随机角(带边界反弹修正)。"""
        e, w = self.enemy, self.world
        if w.player_pos.x < e.pos.x:
            angle = add_normalize_angle(w.rng.unit() * 1.5707964 + 2.3561945, 0.0)
        else:
            angle = f32(w.rng.unit() * 1.5707964 - 0.78539819)
        if e.pos.x < e.lower_move_limit.x + 96.0:
            if angle > 1.5707964:
                angle = f32(3.1415927 - angle)
            elif angle < -1.5707964:
                angle = f32(-3.1415927 - angle)
        if e.pos.x > e.upper_move_limit.x - 96.0:
            # C 这里用的是 enemy->movementAngle(疑似原版 bug, 照抄;
            # th07 同型 bug 用 enemy->angle)
            if 0.0 <= angle < 1.5707964:
                angle = f32(3.1415927 - e.angle)
            elif -1.5707964 < angle <= 0.0:
                angle = f32(-3.1415927 - angle)
        if e.pos.y < e.lower_move_limit.y + 48.0 and angle < 0.0:
            angle = -angle
        if e.pos.y > e.upper_move_limit.y - 48.0 and angle > 0.0:
            angle = -angle
        return angle

    def _timed_polar_displacement(self, instr: EclInstr, angle: float) -> None:
        """StartTimedPolarDisplacement(EclDependencies.cpp:99-119):
        给定角度 + 指令里的时长/速度, 装 movementMode 2 的极位移。"""
        e = self.enemy
        duration = self._int_arg(instr, 0)
        speed = self._float_arg(instr, 2)
        e.move_interp.set(
            f32(math.cos(angle) * speed * duration),
            f32(math.sin(angle) * speed * duration),
            0.0,
        )
        e.move_interp_start_pos = self._world_pos()
        e.move_interp_timer = e.move_interp_start_time = duration
        e.interp_easing = self._int_arg(instr, 1)
        e.move_mode = 2

    def _configure_polar_motion(self, instr: EclInstr) -> None:
        """ConfigurePolarMotion(EclHelpers.cpp:27-54): 角度取自指令 arg2。"""
        e = self.enemy
        angle = add_normalize_angle(self._float_arg(instr, 2), 0.0)
        duration = self._int_arg(instr, 0)
        speed = self._float_arg(instr, 3)
        e.move_interp.set(
            f32(math.cos(angle) * speed * duration),
            f32(math.sin(angle) * speed * duration),
            0.0,
        )
        e.move_interp_start_pos = self._world_pos()
        e.move_interp_timer = e.move_interp_start_time = duration
        e.interp_easing = self._int_arg(instr, 1)
        e.move_mode = 2
        if e.mirror:
            e.move_interp.x = -e.move_interp.x


# ==========================================================================
# opcode handler(模块级自由函数 + @EclMachineTh08.register, 照 th07 风格)
# ==========================================================================

# 作品无关核心指令(stop/wait/nop/跳转/算术/三角/lerp/插值/条件跳/call/ret)
# 从 engine/ecl_std_ops 按 th08 编号注册(编号对照见该模块 docstring;
# 仅 1 号与 th07 同号同义, 其余系统性错位)。
_TH08_CORE_OPS = CoreOps(
    unimp=int(Th08EclOpcode.STOP),
    # 0: 编译器生成的时间同步 nop(真实数据含); 3/84/85: C 的 dispatch 表
    # 这三项都落普通前进路径(EclRunLow.inl:10-11, :230-231, :690-692)
    nop=(0, int(Th08EclOpcode.NOP), 84, 85),
    wait_timer=int(Th08EclOpcode.WAIT),
    jump=int(Th08EclOpcode.JUMP),
    dec_jump=int(Th08EclOpcode.DEC_JUMP),
    set_int=int(Th08EclOpcode.SET_INT),
    set_float=int(Th08EclOpcode.SET_FLOAT),
    rand_sign=int(Th08EclOpcode.RAND_SIGN),
    rand_sign_float=int(Th08EclOpcode.RAND_SIGN_FLOAT),
    int_arith=(
        int(Th08EclOpcode.ADD),
        int(Th08EclOpcode.SUB),
        int(Th08EclOpcode.MUL),
        int(Th08EclOpcode.DIV),
        int(Th08EclOpcode.MOD),
    ),
    float_arith=(
        int(Th08EclOpcode.ADD_FLOAT),
        int(Th08EclOpcode.SUB_FLOAT),
        int(Th08EclOpcode.MUL_FLOAT),
        int(Th08EclOpcode.DIV_FLOAT),
        int(Th08EclOpcode.MOD_FLOAT),
    ),
    inc=int(Th08EclOpcode.INC),
    dec=int(Th08EclOpcode.DEC),
    sin=int(Th08EclOpcode.SIN),
    cos=int(Th08EclOpcode.COS),
    atan2=int(Th08EclOpcode.ATAN2),
    lerp=int(Th08EclOpcode.LERP),
    init_interp=int(Th08EclOpcode.INIT_INTERP),
    normalize_angle=int(Th08EclOpcode.NORMALIZE_ANGLE),
    cond_jumps=(
        int(Th08EclOpcode.JUMP_IF_EQ),
        int(Th08EclOpcode.JUMP_IF_EQ_FLOAT),
        int(Th08EclOpcode.JUMP_IF_NEQ),
        int(Th08EclOpcode.JUMP_IF_NEQ_FLOAT),
        int(Th08EclOpcode.JUMP_IF_LT),
        int(Th08EclOpcode.JUMP_IF_LT_FLOAT),
        int(Th08EclOpcode.JUMP_IF_LEQ),
        int(Th08EclOpcode.JUMP_IF_LEQ_FLOAT),
        int(Th08EclOpcode.JUMP_IF_GT),
        int(Th08EclOpcode.JUMP_IF_GT_FLOAT),
        int(Th08EclOpcode.JUMP_IF_GEQ),
        int(Th08EclOpcode.JUMP_IF_GEQ_FLOAT),
    ),
    sub_call=int(Th08EclOpcode.SUB_CALL),
    sub_ret=int(Th08EclOpcode.SUB_RET),
)
register_core_ops(EclMachineTh08, _TH08_CORE_OPS)


# ---- 52/53: th08 语义重登记(覆盖共享核版本) ----


@EclMachineTh08.register(Th08EclOpcode.SUB_CALL)
def _op_sub_call(m: EclMachineTh08, instr: EclInstr):
    """CallSubOnEnemy(EclDependencies.cpp:466-495): 压栈 + 调 sub(raw sub id,
    EclRunLow.inl:16-20) + 新上下文拿到 g_EclCallParameters 快照。"""
    e, ctx = m.enemy, m.current
    ctx.instr_offset = instr.offset + instr.size
    if not e.no_stack_ret:
        m._push_context()
    m.call_sub(instr.arg_int(0))
    # C: activeEclContext->callParameterInts/Floats = g_EclCallParameters
    # (EclDependencies.cpp:485-487)
    ctx.args.th08_call_ints = list(m.call_params_ints)
    ctx.args.th08_call_floats = list(m.call_params_floats)
    return "restart"


@EclMachineTh08.register(Th08EclOpcode.SUB_RET)
def _op_sub_ret(m: EclMachineTh08, instr: EclInstr):
    """PopEclContext(EclDependencies.cpp:498-530): 弹栈; child 块栈下溢 =
    释放该 child 块, 轮询继续下一块(返回 1 → LOW_SELECT_NEXT_CONTEXT)。"""
    e, ctx = m.enemy, m.current
    if e.no_stack_ret:
        log.warning("ECL_SUB_RET with noStackRet")
    if not m.stack:
        if m._active_child_slot >= 0:
            m._child_returned = True  # 由 step 轮询释放该块
            return "error"
        log.error("ECL 调用栈下溢")
        return "error"
    if ctx.is_periodic_sub:
        e.saved_context_args = ctx.args.clone()
        ctx.is_periodic_sub = 0
    m.current = m.stack.pop()
    return "restart"


# ---- th08 独有: 2 操作数算术(10-19, EclRunLow.inl:264-289) ----

_INT2_BINOP = {
    int(Th08EclOpcode.ADD_ASSIGN): lambda a, b: a + b,
    int(Th08EclOpcode.SUB_ASSIGN): lambda a, b: a - b,
    int(Th08EclOpcode.MUL_ASSIGN): lambda a, b: a * b,
    int(Th08EclOpcode.DIV_ASSIGN): lambda a, b: cdiv(a, b) if b else 0,
    int(Th08EclOpcode.MOD_ASSIGN): lambda a, b: cmod(a, b) if b else 0,
}
_FLOAT2_BINOP = {
    int(Th08EclOpcode.ADD_ASSIGN_FLOAT): lambda a, b: a + b,
    int(Th08EclOpcode.SUB_ASSIGN_FLOAT): lambda a, b: a - b,
    int(Th08EclOpcode.MUL_ASSIGN_FLOAT): lambda a, b: a * b,
    int(Th08EclOpcode.DIV_ASSIGN_FLOAT): lambda a, b: a / b if b != 0.0 else 0.0,
    int(Th08EclOpcode.MOD_ASSIGN_FLOAT): lambda a, b: (
        math.fmod(a, b) if b != 0.0 else 0.0
    ),
}


@EclMachineTh08.register(tuple(_INT2_BINOP))
def _op_int_arith2(m: EclMachineTh08, instr: EclInstr):
    t = m._int_target(instr, 0)
    if t is not None:
        m._set_int(t, _INT2_BINOP[instr.id](m._get_int(t), m._int_arg(instr, 1)))


@EclMachineTh08.register(tuple(_FLOAT2_BINOP))
def _op_float_arith2(m: EclMachineTh08, instr: EclInstr):
    t = m._float_target(instr, 0)
    if t is not None:
        m._set_float(
            t, _FLOAT2_BINOP[instr.id](m._get_float(t), m._float_arg(instr, 1))
        )


# ---- th08 独有: polar(38) / dist(39) / polar 不 normalize(166) ----


@EclMachineTh08.register(Th08EclOpcode.VEC_FROM_ANGLE_MAG)
def _op_vec_from_angle_mag(m: EclMachineTh08, instr: EclInstr):
    # op38: 角度先 AddNormalizeAngle(EclRunLow.inl:368-373)
    ang = add_normalize_angle(m._float_arg(instr, 2), 0.0)
    mag = m._float_arg(instr, 3)
    m._store_float(instr, 0, math.cos(ang) * mag)
    m._store_float(instr, 1, math.sin(ang) * mag)


@EclMachineTh08.register(Th08EclOpcode.DIST)
def _op_dist(m: EclMachineTh08, instr: EclInstr):
    dx = m._float_arg(instr, 1) - m._float_arg(instr, 3)
    dy = m._float_arg(instr, 2) - m._float_arg(instr, 4)
    m._store_float(instr, 0, math.sqrt(dx * dx + dy * dy))


@EclMachineTh08.register(Th08EclOpcode.VEC_FROM_ANGLE_MAG_RAW)
def _op_vec_from_angle_mag_raw(m: EclMachineTh08, instr: EclInstr):
    # op166: 角度不 normalize(EclRunHigh.inl:868-881), th07 151 同型
    ang = m._float_arg(instr, 2)
    mag = m._float_arg(instr, 3)
    m._store_float(instr, 1, math.sin(ang) * mag)
    m._store_float(instr, 0, math.cos(ang) * mag)


# ---- anm 系(54-62, EclRunLow.inl:424-494; 只存脚本 id, 渲染侧按 id 起 VM) ----


@EclMachineTh08.register((Th08EclOpcode.SET_ANM, Th08EclOpcode.SET_ANM_ALT))
def _op_set_anm(m: EclMachineTh08, instr: EclInstr):
    e = m.enemy
    e.anm_idx = m._int_arg(instr, 0)
    e.anm_alt_bank = 1 if instr.id == Th08EclOpcode.SET_ANM_ALT else 0


@EclMachineTh08.register(
    (Th08EclOpcode.SET_MOVE_ANM_SEQ, Th08EclOpcode.SET_MOVE_ANM_SEQ_ALT)
)
def _op_set_move_anm_seq(m: EclMachineTh08, instr: EclInstr):
    # SetPrimaryAnmScripts(s, s+1, ..., s+5)(EclDependencies.cpp:449-460)
    base = m._int_arg(instr, 0)
    m.enemy.move_anm = tuple(base + i for i in range(6))
    m.enemy.anm_alt_bank = 1 if instr.id == Th08EclOpcode.SET_MOVE_ANM_SEQ_ALT else 0


@EclMachineTh08.register(
    (Th08EclOpcode.SET_MOVE_ANM, Th08EclOpcode.SET_MOVE_ANM_ALT)
)
def _op_set_move_anm(m: EclMachineTh08, instr: EclInstr):
    m.enemy.move_anm = tuple(m._int_arg(instr, i) for i in range(6))
    m.enemy.anm_alt_bank = 1 if instr.id == Th08EclOpcode.SET_MOVE_ANM_ALT else 0


@EclMachineTh08.register(
    (Th08EclOpcode.SET_SUB_ANM, Th08EclOpcode.SET_SUB_ANM_ALT)
)
def _op_set_sub_anm(m: EclMachineTh08, instr: EclInstr):
    # SetExtraAnmScript(EclDependencies.cpp:534-566): arg1<0 → scriptIndex=-1
    e = m.enemy
    e.anm_alt_bank = 1 if instr.id == Th08EclOpcode.SET_SUB_ANM_ALT else 0
    idx = m._int_arg(instr, 0)
    if 0 <= idx < len(e.sub_anm_idx):
        e.sub_anm_idx[idx] = m._int_arg(instr, 1)


@EclMachineTh08.register(Th08EclOpcode.SET_SPECIAL_ANM)
def _op_set_special_anm(m: EclMachineTh08, instr: EclInstr):
    e = m.enemy
    if len(e.move_anm) >= 6:
        e.anm_idx = e.move_anm[5]  # anmScripts.special


# ---- 移动系(63-76/82, EclRunLow.inl:496-632, EclRunHigh.inl:922-935) ----


@EclMachineTh08.register(Th08EclOpcode.SET_POS)
def _op_set_pos(m: EclMachineTh08, instr: EclInstr):
    e = m.enemy
    e.pos.set(m._float_arg(instr, 0), m._float_arg(instr, 1), 0.0)
    e.clamp_pos()


@EclMachineTh08.register(Th08EclOpcode.MOVE_POS_TIME)
def _op_move_pos_time(m: EclMachineTh08, instr: EclInstr):
    """ConfigureRelativeMotion(EclHelpers.cpp:59-87): 目标点转位移插值。"""
    e = m.enemy
    target = Vec3(m._float_arg(instr, 2), m._float_arg(instr, 3), 0.0)
    e.move_interp = target - m._world_pos()
    # C: origin 用 enemy->position(不是 worldPosition, EclHelpers.cpp:70-72)
    e.move_interp_start_pos = e.pos.copy()
    e.move_interp_timer = e.move_interp_start_time = m._int_arg(instr, 0)
    e.interp_easing = m._int_arg(instr, 1)
    e.move_mode = 2
    e.axis_speed = Vec3()
    if e.mirror:
        e.move_interp.x = -e.move_interp.x


@EclMachineTh08.register(Th08EclOpcode.SET_MOVE_POLAR)
def _op_set_move_polar(m: EclMachineTh08, instr: EclInstr):
    e = m.enemy
    e.angle = add_normalize_angle(m._float_arg(instr, 0), 0.0)
    e.move_speed = m._float_arg(instr, 1)
    e.move_mode = 1
    e.move_interp_timer = e.move_interp_start_time = 0


@EclMachineTh08.register(Th08EclOpcode.MOVE_DIR_TIME)
def _op_move_dir_time(m: EclMachineTh08, instr: EclInstr):
    e = m.enemy
    if m._int_arg(instr, 0) <= 0:
        e.angle = add_normalize_angle(m._float_arg(instr, 2), 0.0)
        e.move_speed = m._float_arg(instr, 3)
        e.move_mode = 1
        e.move_interp_timer = e.move_interp_start_time = 0
    else:
        m._configure_polar_motion(instr)


@EclMachineTh08.register(Th08EclOpcode.MOVE_BOUNDARY_AWARE)
def _op_move_boundary_aware(m: EclMachineTh08, instr: EclInstr):
    """BeginBoundaryAwareMove(EclDependencies.cpp:122-185)。"""
    e = m.enemy
    angle = m._exit_angle()
    if m._int_arg(instr, 0) <= 0:
        e.angle = angle
        e.move_speed = m._float_arg(instr, 2)
        e.move_mode = 1
        e.move_interp_timer = e.move_interp_start_time = 0
    else:
        m._timed_polar_displacement(instr, angle)


@EclMachineTh08.register(Th08EclOpcode.MOVE_AT_PLAYER)
def _op_move_at_player(m: EclMachineTh08, instr: EclInstr):
    # C case 68 只设 angle/speed, 不动 movementMode(EclRunLow.inl:534-541)
    e, w = m.enemy, m.world
    e.angle = add_normalize_angle(
        m._float_arg(instr, 0), w.angle_to_player(m._world_pos())
    )
    e.move_speed = m._float_arg(instr, 1)


@EclMachineTh08.register(Th08EclOpcode.MOVE_AT_PLAYER_TIME)
def _op_move_at_player_time(m: EclMachineTh08, instr: EclInstr):
    e, w = m.enemy, m.world
    if m._int_arg(instr, 0) <= 0:
        e.angle = add_normalize_angle(
            m._float_arg(instr, 2), w.angle_to_player(m._world_pos())
        )
        e.move_speed = m._float_arg(instr, 3)
        e.move_mode = 1
        # C: 再解析一次 arg0 赋 timer(EclRunLow.inl:553-555)
        e.move_interp_timer = e.move_interp_start_time = m._int_arg(instr, 0)
    else:
        # C: else 分支是不带瞄准的 ConfigurePolarMotion(EclRunLow.inl:558-560)
        m._configure_polar_motion(instr)


@EclMachineTh08.register(Th08EclOpcode.SET_ANGULAR_VEL)
def _op_set_angular_vel(m: EclMachineTh08, instr: EclInstr):
    e = m.enemy
    e.angular_velocity = m._float_arg(instr, 0)
    e.move_mode = 1


@EclMachineTh08.register(Th08EclOpcode.SET_MOVE_ACCEL)
def _op_set_move_accel(m: EclMachineTh08, instr: EclInstr):
    e = m.enemy
    e.move_acceleration = m._float_arg(instr, 0)
    e.move_mode = 1


@EclMachineTh08.register(Th08EclOpcode.MOVE_ORBIT)
def _op_move_orbit(m: EclMachineTh08, instr: EclInstr):
    e = m.enemy
    e.move_interp_timer = e.move_interp_start_time = m._int_arg(instr, 0)
    e.move_interp_start_pos.set(
        m._float_arg(instr, 1), m._float_arg(instr, 2), 0.0
    )
    e.move_angle = m._float_arg(instr, 3)
    e.move_angular_velocity = m._float_arg(instr, 4)
    e.move_radius = m._float_arg(instr, 5)
    e.move_radial_velocity = m._float_arg(instr, 6)
    e.move_mode = 3


@EclMachineTh08.register(Th08EclOpcode.MOVE_ORBIT_AROUND_SELF)
def _op_move_orbit_around_self(m: EclMachineTh08, instr: EclInstr):
    e = m.enemy
    e.move_interp_timer = e.move_interp_start_time = m._int_arg(instr, 0)
    e.move_interp_start_pos = e.pos.copy()
    e.move_angle = m._float_arg(instr, 1)
    e.move_angular_velocity = m._float_arg(instr, 2)
    e.move_radius = 0.0
    e.move_radial_velocity = m._float_arg(instr, 3)
    e.move_mode = 3


@EclMachineTh08.register(Th08EclOpcode.SET_ORBIT_VELS)
def _op_set_orbit_vels(m: EclMachineTh08, instr: EclInstr):
    e = m.enemy
    e.move_interp_timer = e.move_interp_start_time = m._int_arg(instr, 0)
    e.move_angular_velocity = m._float_arg(instr, 1)
    e.move_radial_velocity = m._float_arg(instr, 2)
    e.move_mode = 3


@EclMachineTh08.register(Th08EclOpcode.SET_MOVEMENT_BOUNDS)
def _op_set_movement_bounds(m: EclMachineTh08, instr: EclInstr):
    e = m.enemy
    e.lower_move_limit.x = m._float_arg(instr, 0)
    e.lower_move_limit.y = m._float_arg(instr, 1)
    e.upper_move_limit.x = m._float_arg(instr, 2)
    e.upper_move_limit.y = m._float_arg(instr, 3)
    e.has_movement_bounds = 1


@EclMachineTh08.register(Th08EclOpcode.DISABLE_MOVEMENT_BOUNDS)
def _op_disable_movement_bounds(m: EclMachineTh08, instr: EclInstr):
    m.enemy.has_movement_bounds = 0


@EclMachineTh08.register(Th08EclOpcode.SET_HITBOX_SIZE)
def _op_set_hitbox_size(m: EclMachineTh08, instr: EclInstr):
    e = m.enemy
    e.hitbox_size.x = m._float_arg(instr, 0)
    e.hitbox_size.y = m._float_arg(instr, 1)


@EclMachineTh08.register(Th08EclOpcode.SET_GRAZE_SIZE)
def _op_set_graze_size(m: EclMachineTh08, instr: EclInstr):
    e = m.enemy
    e.graze_size.x = m._float_arg(instr, 0)
    e.graze_size.y = m._float_arg(instr, 1)


@EclMachineTh08.register(Th08EclOpcode.SET_MIN_PLAYER_DISTANCE)
def _op_set_min_player_distance(m: EclMachineTh08, instr: EclInstr):
    e = m.enemy
    d = m._float_arg(instr, 0)
    e.min_player_dist_sq = f32(d * d)


@EclMachineTh08.register(Th08EclOpcode.SET_FORM_EFFECT)
def _op_set_form_effect(m: EclMachineTh08, instr: EclInstr):
    m.enemy.form_effect = m._int_arg(instr, 0)


# ---- 敌人标志位(79-81, EclRunLow.inl:650-688) ----


@EclMachineTh08.register(Th08EclOpcode.SET_ENEMY_FLAGS)
def _op_set_enemy_flags(m: EclMachineTh08, instr: EclInstr):
    """flags 按掩码直接赋值(前三位反相语义, EclRunLow.inl:650-658)。"""
    e = m.enemy
    lhs = m._int_arg(instr, 0)
    e.can_be_damaged = 0 if (lhs & 1) else 1
    e.has_contact_hitbox = 0 if (lhs & 2) else 1
    e.is_hittable = 0 if (lhs & 4) else 1
    e.no_sprite = 1 if (lhs & 8) else 0
    e.disable_oob_despawn = 1 if (lhs & 0x10) else 0
    e.can_die = 0 if (lhs & 0x20) else 1


@EclMachineTh08.register(Th08EclOpcode.CLEAR_ENEMY_FLAGS)
def _op_clear_enemy_flags(m: EclMachineTh08, instr: EclInstr):
    """掩码置位 → 对应能力关(EclRunLow.inl:660-673; bit1 顺带清
    alignmentEffect->vm.flag17, 特效是 world 阶段接线)。"""
    e = m.enemy
    lhs = m._int_arg(instr, 0)
    if lhs & 1:
        e.can_be_damaged = 0
    if lhs & 2:
        e.has_contact_hitbox = 0
    if lhs & 4:
        e.is_hittable = 0
    if lhs & 8:
        e.no_sprite = 1
    if lhs & 0x10:
        e.disable_oob_despawn = 1
    if lhs & 0x20:
        e.can_die = 0


@EclMachineTh08.register(Th08EclOpcode.ENABLE_ENEMY_FLAGS)
def _op_enable_enemy_flags(m: EclMachineTh08, instr: EclInstr):
    """掩码置位 → 对应能力开(EclRunLow.inl:675-688)。"""
    e = m.enemy
    lhs = m._int_arg(instr, 0)
    if lhs & 1:
        e.can_be_damaged = 1
    if lhs & 2:
        e.has_contact_hitbox = 1
    if lhs & 4:
        e.is_hittable = 1
    if lhs & 8:
        e.no_sprite = 0
    if lhs & 0x10:
        e.disable_oob_despawn = 0
    if lhs & 0x20:
        e.can_die = 1


# ---- boss 系(86-89/127/148/158) ----


@EclMachineTh08.register(Th08EclOpcode.GET_BOSS_INT)
def _op_get_boss_int(m: EclMachineTh08, instr: EclInstr):
    w = m.world
    idx = m._int_arg(instr, 2)
    boss = w.bosses[idx] if 0 <= idx < len(w.bosses) else None
    if boss is None:
        return None  # C 不查空(会崩), 这里防御性跳过
    m._store_int(instr, 0, m._peer_int(boss, instr, 1))


@EclMachineTh08.register(Th08EclOpcode.GET_BOSS_FLOAT)
def _op_get_boss_float(m: EclMachineTh08, instr: EclInstr):
    w = m.world
    idx = m._int_arg(instr, 2)
    boss = w.bosses[idx] if 0 <= idx < len(w.bosses) else None
    if boss is None:
        return None
    m._store_float(instr, 0, m._peer_float(boss, instr, 1))


@EclMachineTh08.register(Th08EclOpcode.CALL_SUB_ON_BOSS)
def _op_call_sub_on_boss(m: EclMachineTh08, instr: EclInstr):
    w = m.world
    idx = m._int_arg(instr, 0)
    boss = w.bosses[idx] if 0 <= idx < len(w.bosses) else None
    if boss is None:
        return None
    # sub id 用 raw(EclRunLow.inl:16-20); 压栈+调用交宿主(boss 机器的接缝)
    m.host.call_sub_on_boss(boss, instr.arg_int(1))


@EclMachineTh08.register(Th08EclOpcode.SET_BOSS_PENDING_SUB)
def _op_set_boss_pending_sub(m: EclMachineTh08, instr: EclInstr):
    w = m.world
    idx = m._int_arg(instr, 0)
    boss = w.bosses[idx] if 0 <= idx < len(w.bosses) else None
    if boss is not None:
        # pendingEclSubroutineIndex: boss 下次 restart 时进
        # eclSubroutineIds[idx](= 本引擎的 run_interrupt + interrupts 机制)
        boss.run_interrupt = m._int_arg(instr, 1)


@EclMachineTh08.register(Th08EclOpcode.SET_BOSS)
def _op_set_boss(m: EclMachineTh08, instr: EclInstr):
    """op127(EclRunHigh.inl:426-456); GUI 血条/boss 标记经宿主。"""
    e, w = m.enemy, m.world
    idx = m._int_arg(instr, 0)
    if idx >= 0:
        if idx < len(w.bosses):
            w.bosses[idx] = e
        e.is_boss = 1
        e.boss_id = idx
        e.min_player_dist_sq = 0.0
        m.host.set_boss(idx, e)
    else:
        if 0 <= e.boss_id < len(w.bosses):
            w.bosses[e.boss_id] = None
            m.host.set_boss(e.boss_id, None)
        e.is_boss = 0


@EclMachineTh08.register(Th08EclOpcode.SET_BOSS_LIFE_MARKERS)
def _op_set_boss_life_markers(m: EclMachineTh08, instr: EclInstr):
    m.host.set_boss_life_markers(m._int_arg(instr, 0))


@EclMachineTh08.register(Th08EclOpcode.SET_BOSS_GAUGE_SLOT)
def _op_set_boss_gauge_slot(m: EclMachineTh08, instr: EclInstr):
    # C: SetBossGaugeSlot(idx, arg1/maxLife, arg2/maxLife) + 颜色 arg3
    # (EclRunHigh.inl:530-540); 归一化是显示层的事, 原值交宿主
    m.host.set_boss_health(
        m._int_arg(instr, 0),
        m._int_arg(instr, 1),
        m._int_arg(instr, 2),
        m._int_arg(instr, 3),
    )


# ---- 使魔 spawn(90-92, EclRunLow.inl:737-929) ----


@EclMachineTh08.register(
    (
        Th08EclOpcode.SPAWN_FAMILIAR,
        Th08EclOpcode.SPAWN_FAMILIAR_REL,
        Th08EclOpcode.SPAWN_FAMILIAR_INHERIT,
    )
)
def _op_spawn_familiar(m: EclMachineTh08, instr: EclInstr):
    """使魔生成: 90 脚本坐标 / 91 父位置偏移 / 92 继承父位置。

    附着链登记/人妖对齐/drawGroup 等 C 侧行为(EclRunLow.inl:743-790)是
    world 阶段接线, 全走 host.spawn_familiar 接缝; 音效 0x24 无条件播放
    (EclRunLow.inl:792-794)。
    """
    e = m.enemy
    pos = Vec3(m._float_arg(instr, 1), m._float_arg(instr, 2), 0.0)
    if instr.id == Th08EclOpcode.SPAWN_FAMILIAR_REL:
        pos = pos + m._world_pos()
    # C: parent life<=0 时不生成(lastSpawnFailed=1, EclDependencies.cpp:590-611)
    if e.life > 0:
        m.host.spawn_familiar(
            instr.id,
            instr.arg_int(0),  # sub id(raw)
            pos,
            m._int_arg(instr, 3),
            m._int_arg(instr, 4),
            m._int_arg(instr, 5),
            m.current.args.clone(),
        )
    m.host.play_sound(0x24)  # SOUND_FAMILIAR_SPAWN


# ---- 敌生成/清场(93-95) ----


@EclMachineTh08.register(
    (Th08EclOpcode.SPAWN_ENEMY_ABS, Th08EclOpcode.SPAWN_ENEMY_REL)
)
def _op_spawn_enemy(m: EclMachineTh08, instr: EclInstr):
    e = m.enemy
    if e.life > 0:
        pos = Vec3(
            m._float_arg(instr, 1), m._float_arg(instr, 2), m._float_arg(instr, 3)
        )
        if instr.id == Th08EclOpcode.SPAWN_ENEMY_REL:
            pos = pos + e.pos
        m.host.spawn_enemy(
            instr.arg_int(0),  # sub id(raw)
            pos,
            m._int_arg(instr, 4),
            m._int_arg(instr, 5),
            m._int_arg(instr, 6),
            0,
            m.current.args.clone(),
        )


@EclMachineTh08.register(Th08EclOpcode.REMOVE_ALL_ENEMIES)
def _op_remove_all_enemies(m: EclMachineTh08, instr: EclInstr):
    m.host.remove_all_enemies(8000, 0)  # KillAllNonBossEnemies(8000, 0)


# ---- 弹幕系(96-113) ----


@EclMachineTh08.register(range(96, 105))  # 弹幕生成 9 合一(aim_mode = id - 96)
def _op_spawn_bullet_pattern(m: EclMachineTh08, instr: EclInstr):
    e = m.enemy
    if e.life <= 0:
        return None
    if e.defer_bullet_pattern:
        # memcpy pendingShotInstruction(EclRunHigh.inl:174-181):
        # 自动射击时重新派发(见 _auto_shoot)
        e.pending_shot_instr = instr
        return None
    m._fire_shot(instr)


@EclMachineTh08.register(Th08EclOpcode.SET_SHOOT_INTERVAL)
def _op_set_shoot_interval(m: EclMachineTh08, instr: EclInstr):
    e, w = m.enemy, m.world
    e.shoot_interval = m._int_arg(instr, 0)
    if e.shoot_interval != 0:
        # ScaleIntBasedOnRank(interval/5, -interval/5)(EclRunHigh.inl:215-218),
        # 与 th07 的 shoot_interval_rank_delta 同构
        e.shoot_interval = i32(e.shoot_interval + e.shoot_interval_rank_delta(w.rank))
        e.shoot_interval_timer = 0


@EclMachineTh08.register(Th08EclOpcode.SET_SHOOT_INTERVAL_RAND)
def _op_set_shoot_interval_rand(m: EclMachineTh08, instr: EclInstr):
    e, w = m.enemy, m.world
    e.shoot_interval = m._int_arg(instr, 0)
    if e.shoot_interval != 0:
        e.shoot_interval = i32(e.shoot_interval + e.shoot_interval_rank_delta(w.rank))
        e.shoot_interval_timer = w.rng.int_below(e.shoot_interval)


@EclMachineTh08.register(Th08EclOpcode.DEFER_BULLET_PATTERN)
def _op_defer_bullet_pattern(m: EclMachineTh08, instr: EclInstr):
    m.enemy.defer_bullet_pattern = 1


@EclMachineTh08.register(Th08EclOpcode.DISABLE_DEFER_BULLET_PATTERN)
def _op_disable_defer_bullet_pattern(m: EclMachineTh08, instr: EclInstr):
    m.enemy.defer_bullet_pattern = 0


@EclMachineTh08.register(Th08EclOpcode.SPAWN_PREV_BULLET_PATTERN)
def _op_spawn_prev_bullet_pattern(m: EclMachineTh08, instr: EclInstr):
    e = m.enemy
    # C: descriptor.position = position + shootOffset(EclRunHigh.inl:237-248)
    e.bullet_props.pos = e.pos + e.shoot_offset
    m.host.spawn_bullet_pattern(e.bullet_props)


@EclMachineTh08.register(Th08EclOpcode.SET_SHOOT_OFFSET)
def _op_set_shoot_offset(m: EclMachineTh08, instr: EclInstr):
    m.enemy.shoot_offset.set(m._float_arg(instr, 0), m._float_arg(instr, 1), 0.0)


@EclMachineTh08.register(Th08EclOpcode.INIT_BULLET_CMD)
def _op_init_bullet_cmd(m: EclMachineTh08, instr: EclInstr):
    """bulletSpawnDescriptor.transforms 记录(EclRunHigh.inl:187-203):
    kind/allowWhileActive/int0/int1/float0/float1。"""
    cmds = m.enemy.bullet_props.commands
    idx = m._int_arg(instr, 0)
    if 0 <= idx < len(cmds):
        cmd = cmds[idx]
        cmd.type = m._int_arg(instr, 1)
        cmd.flag = m._int_arg(instr, 2)
        cmd.duration = m._int_arg(instr, 3)
        cmd.loop_count = m._int_arg(instr, 4)
        cmd.speed = m._float_arg(instr, 5)
        cmd.angle = m._float_arg(instr, 6)


@EclMachineTh08.register(Th08EclOpcode.CLEAR_BULLETS_FOR_TRANSITION)
def _op_clear_bullets_for_transition(m: EclMachineTh08, instr: EclInstr):
    m.host.clear_bullets_for_transition()


@EclMachineTh08.register(Th08EclOpcode.SET_BULLET_SOUND)
def _op_set_bullet_sound(m: EclMachineTh08, instr: EclInstr):
    p = m.enemy.bullet_props
    idx = m._int_arg(instr, 0)
    if idx >= 0:
        p.sound_idx = idx
        p.flags |= _BULLET_TRANSFORM_PLAY_SPAWN_SOUND
    else:
        p.flags &= ~_BULLET_TRANSFORM_PLAY_SPAWN_SOUND
    p.sound_override = m._int_arg(instr, 1)


# ---- 激光系(114-121/154/167/170-172) ----


@EclMachineTh08.register(
    (
        Th08EclOpcode.SPAWN_LASER_PATTERN,
        Th08EclOpcode.SPAWN_LASER_PATTERN_AIMED,
    )
)
def _op_spawn_laser_pattern(m: EclMachineTh08, instr: EclInstr):
    m._spawn_laser_pattern(instr)


@EclMachineTh08.register(Th08EclOpcode.SET_LASER_IDX)
def _op_set_laser_idx(m: EclMachineTh08, instr: EclInstr):
    m.enemy.laser_idx = m._int_arg(instr, 0)


@EclMachineTh08.register(Th08EclOpcode.ADD_LASER_ANGLE)
def _op_add_laser_angle(m: EclMachineTh08, instr: EclInstr):
    h = m.enemy.lasers[m._int_arg(instr, 0) & 31]
    if h is not None:
        m.host.laser_add_angle(h, m._float_arg(instr, 1))


@EclMachineTh08.register(Th08EclOpcode.AIM_LASER_AT_PLAYER)
def _op_aim_laser_at_player(m: EclMachineTh08, instr: EclInstr):
    h = m.enemy.lasers[m._int_arg(instr, 0) & 31]
    if h is not None:
        m.host.laser_aim_at_player(h, m._float_arg(instr, 1))


@EclMachineTh08.register(Th08EclOpcode.SET_LASER_POS_REL)
def _op_set_laser_pos_rel(m: EclMachineTh08, instr: EclInstr):
    e = m.enemy
    h = e.lasers[m._int_arg(instr, 0) & 31]
    if h is not None:
        wp = m._world_pos()
        m.host.laser_set_pos(
            h,
            Vec3(
                m._float_arg(instr, 1) + wp.x,
                m._float_arg(instr, 2) + wp.y,
                m._float_arg(instr, 3) + wp.z,
            ),
        )


@EclMachineTh08.register(Th08EclOpcode.TEST_LASER_IN_USE)
def _op_test_laser_in_use(m: EclMachineTh08, instr: EclInstr):
    """在写 extraIntVariables[2](EclRunHigh.inl:385-393)。"""
    h = m.enemy.lasers[m._int_arg(instr, 0) & 31]
    m._set_int(
        int(Th08EclVarId.EXTRA_INT0) + 2,
        1 if (h is not None and m.host.laser_in_use(h)) else 0,
    )


@EclMachineTh08.register(Th08EclOpcode.STOP_LASER)
def _op_stop_laser(m: EclMachineTh08, instr: EclInstr):
    h = m.enemy.lasers[m._int_arg(instr, 0) & 31]
    if h is not None:
        m.host.laser_stop(h)


@EclMachineTh08.register(Th08EclOpcode.CLEAR_LASERS)
def _op_clear_lasers(m: EclMachineTh08, instr: EclInstr):
    m.enemy.lasers = [None] * 32


@EclMachineTh08.register(Th08EclOpcode.SET_LASER_ANGLE)
def _op_set_laser_angle(m: EclMachineTh08, instr: EclInstr):
    h = m.enemy.lasers[m._int_arg(instr, 0) & 31]
    if h is not None:
        m.host.laser_set_angle(h, m._float_arg(instr, 1))


@EclMachineTh08.register(Th08EclOpcode.SET_LASER_HIDE_WARNING)
def _op_set_laser_hide_warning(m: EclMachineTh08, instr: EclInstr):
    h = m.enemy.lasers[m._int_arg(instr, 0) & 31]
    if h is not None:
        m.host.laser_set_hide_warning(h, m._int_arg(instr, 1))


@EclMachineTh08.register(Th08EclOpcode.SET_LASER_START_LEN)
def _op_set_laser_start_len(m: EclMachineTh08, instr: EclInstr):
    h = m.enemy.lasers[m._int_arg(instr, 0) & 31]
    if h is not None:
        m.host.laser_set_start_length(h, m._float_arg(instr, 1))


@EclMachineTh08.register(Th08EclOpcode.SET_LASER_OFFSETS)
def _op_set_laser_offsets(m: EclMachineTh08, instr: EclInstr):
    h = m.enemy.lasers[m._int_arg(instr, 0) & 31]
    if h is not None:
        m.host.laser_set_offsets(h, m._float_arg(instr, 1), m._float_arg(instr, 2))


# ---- 符卡/回调/中断(122-134/148-153/155) ----


@EclMachineTh08.register(Th08EclOpcode.BEGIN_SPELLCARD)
def _op_begin_spellcard(m: EclMachineTh08, instr: EclInstr):
    m._begin_spellcard(instr)


@EclMachineTh08.register(Th08EclOpcode.END_SPELLCARD)
def _op_end_spellcard(m: EclMachineTh08, instr: EclInstr):
    m.host.end_spellcard(m.enemy)


@EclMachineTh08.register(Th08EclOpcode.PLAY_SOUND)
def _op_play_sound(m: EclMachineTh08, instr: EclInstr):
    m.host.play_sound(m._int_arg(instr, 0))


@EclMachineTh08.register(Th08EclOpcode.RUN_PENDING_SUB)
def _op_run_pending_sub(m: EclMachineTh08, instr: EclInstr):
    """pendingSub → eclSubroutineIds[idx] 压栈调用(EclRunHigh.inl:492-519
    的 enter_subroutine, 与 pending 检查共用)。"""
    e = m.enemy
    idx = m._int_arg(instr, 0)
    e.run_interrupt = idx  # 语义溯源(pending 槽位); _do_interrupt_call 会清 -1
    if m._do_interrupt_call(instr, e.interrupts[idx & 31]) is None:
        return "error"
    return "restart"


@EclMachineTh08.register(Th08EclOpcode.SET_INTERRUPT)
def _op_set_interrupt(m: EclMachineTh08, instr: EclInstr):
    # eclSubroutineIds[arg1] = arg0(EclRunHigh.inl:488-491)
    m.enemy.interrupts[m._int_arg(instr, 1) & 31] = m._int_arg(instr, 0)


@EclMachineTh08.register(Th08EclOpcode.SET_DEATH_TYPE)
def _op_set_death_type(m: EclMachineTh08, instr: EclInstr):
    m.enemy.death_type = instr.arg_bytes(0)[0]


@EclMachineTh08.register(Th08EclOpcode.SET_DEATH_CALLBACK_SUB)
def _op_set_death_callback_sub(m: EclMachineTh08, instr: EclInstr):
    m.enemy.death_callback_sub = instr.arg_u16(0, 0)


@EclMachineTh08.register(Th08EclOpcode.SET_LIFE)
def _op_set_life(m: EclMachineTh08, instr: EclInstr):
    e = m.enemy
    e.phase_starting_life = e.life = e.max_life = m._int_arg(instr, 0)


@EclMachineTh08.register(Th08EclOpcode.SET_TIMER)
def _op_set_timer(m: EclMachineTh08, instr: EclInstr):
    m.enemy.timer = m._int_arg(instr, 0)


@EclMachineTh08.register(Th08EclOpcode.SET_LIFE_CALLBACK)
def _op_set_life_callback(m: EclMachineTh08, instr: EclInstr):
    e = m.enemy
    idx = m._int_arg(instr, 0)
    if 0 <= idx < 4:
        e.life_callback_threshold[idx] = m._int_arg(instr, 1)
        e.life_callback_sub[idx] = m._int_arg(instr, 2)


@EclMachineTh08.register(Th08EclOpcode.SET_TIMER_CALLBACK)
def _op_set_timer_callback(m: EclMachineTh08, instr: EclInstr):
    e = m.enemy
    e.timer_callback_threshold = m._int_arg(instr, 0)
    e.timer_callback_sub = m._int_arg(instr, 1)
    e.timer = 0


@EclMachineTh08.register(Th08EclOpcode.SET_DEATH_ANM)
def _op_set_death_anm(m: EclMachineTh08, instr: EclInstr):
    raw = instr.arg_bytes(0)
    m.enemy.death_anm = (
        struct.unpack("<b", raw[0:1])[0],
        raw[1],
        struct.unpack("<b", raw[2:3])[0],
    )


@EclMachineTh08.register(Th08EclOpcode.SET_VM_AUTO_ROTATE)
def _op_set_vm_auto_rotate(m: EclMachineTh08, instr: EclInstr):
    m.enemy.primary_vm_auto_rotate = instr.arg_bytes(0)[0]


@EclMachineTh08.register(Th08EclOpcode.ADD_TIME)
def _op_add_time(m: EclMachineTh08, instr: EclInstr):
    ctx = m.current
    ctx.time = i32(ctx.time + m._int_arg(instr, 0))


@EclMachineTh08.register(Th08EclOpcode.SET_PRIMARY_VM_INTERRUPT)
def _op_set_primary_vm_interrupt(m: EclMachineTh08, instr: EclInstr):
    m.enemy.primary_vm_interrupt = m._int_arg(instr, 0)


@EclMachineTh08.register(Th08EclOpcode.SET_VM_INTERRUPT)
def _op_set_vm_interrupt(m: EclMachineTh08, instr: EclInstr):
    e = m.enemy
    idx = instr.arg_int(0)  # raw(EclRunHigh.inl:784-787)
    if 0 <= idx < len(e.vm_interrupts):
        e.vm_interrupts[idx] = instr.arg_u16(1, 0)


@EclMachineTh08.register(Th08EclOpcode.SET_NO_STACK_RET)
def _op_set_no_stack_ret(m: EclMachineTh08, instr: EclInstr):
    m.enemy.no_stack_ret = instr.arg_bytes(0)[0]


@EclMachineTh08.register(Th08EclOpcode.SET_BULLET_RANK_PARAMS)
def _op_set_bullet_rank_params(m: EclMachineTh08, instr: EclInstr):
    e = m.enemy
    e.bullet_rank_speed_low = m._float_arg(instr, 0)
    e.bullet_rank_speed_high = m._float_arg(instr, 1)
    e.bullet_rank_amount1_low = m._int_arg(instr, 2)
    e.bullet_rank_amount1_high = m._int_arg(instr, 3)
    e.bullet_rank_amount2_low = m._int_arg(instr, 4)
    e.bullet_rank_amount2_high = m._int_arg(instr, 5)


@EclMachineTh08.register(Th08EclOpcode.BIND_TIMER_CALLBACK_TO_DEATH)
def _op_bind_timer_callback_to_death(m: EclMachineTh08, instr: EclInstr):
    e = m.enemy
    e.timer_callback_sub = e.death_callback_sub
    e.timer = 0


@EclMachineTh08.register(Th08EclOpcode.SET_TIMEOUT_SPELL)
def _op_set_timeout_spell(m: EclMachineTh08, instr: EclInstr):
    # timeoutSpell(生存符); g_Spellcard.scoreLimit=99999990 是计分侧, world 阶段
    m.enemy.is_survival_spellcard = instr.arg_bytes(0)[0]


@EclMachineTh08.register(Th08EclOpcode.SET_SPECIAL_INTERACTION)
def _op_set_special_interaction(m: EclMachineTh08, instr: EclInstr):
    e = m.enemy
    e.is_projectile = instr.arg_bytes(0)[0]  # specialInteraction
    e.draw_group = 2


@EclMachineTh08.register(Th08EclOpcode.SET_PAUSE_TIMER)
def _op_set_pause_timer(m: EclMachineTh08, instr: EclInstr):
    # pauseTimer(炸弹中冻结 ECL 推进, 同 th07 FREEZE_ECL_DURING_BOMB 语义)
    m.enemy.freeze_ecl_during_bombs = m._int_arg(instr, 0)


# ---- child 上下文块 / EX 指令(135-137) ----


@EclMachineTh08.register(Th08EclOpcode.SET_CHILD_CONTEXT)
def _op_set_child_context(m: EclMachineTh08, instr: EclInstr):
    """child 上下文块安装(EclRunHigh.inl:580-613)。

    slot 旧块直接释放; sub_id >= 0 时建块: CallEclSub 后把活动上下文的
    intVariables..callParameterFloats 拷给 child(到 secondaryTime 为止的
    memcpy, EclRunHigh.inl:605-609)。
    """
    slot = m._int_arg(instr, 0)
    if not (0 <= slot < 4):
        return None  # C 直接越界写, 这里防御性丢弃
    m._child_blocks[slot] = None
    sub_id = m._int_arg(instr, 1)
    if sub_id < 0:
        return None
    block = _ChildEclBlock(sub_id)
    block.context.instr_offset = m.file.sub_offset(sub_id)
    src, dst = m.current.args, block.context.args
    dst.th08_ints = list(src.th08_ints)
    dst.th08_floats = list(src.th08_floats)
    dst.th08_extra_ints = list(src.th08_extra_ints)
    dst.th08_extra_floats = list(src.th08_extra_floats)
    dst.th08_call_ints = list(src.th08_call_ints)
    dst.th08_call_floats = list(src.th08_call_floats)
    m._child_blocks[slot] = block


@EclMachineTh08.register(Th08EclOpcode.RUN_EX_INS)
def _op_run_ex_ins(m: EclMachineTh08, instr: EclInstr):
    m._run_ex(m._int_arg(instr, 0), instr)


@EclMachineTh08.register(Th08EclOpcode.SET_EX_INS)
def _op_set_ex_ins(m: EclMachineTh08, instr: EclInstr):
    ctx = m.current
    idx = m._int_arg(instr, 0)
    if idx >= 0:
        ctx.ex_instr_idx = idx
        ctx.ex_instr = instr
    else:
        ctx.ex_instr_idx = -1


# ---- 道具/特效(139-144/168) ----


@EclMachineTh08.register(Th08EclOpcode.SPAWN_ATTACHED_EFFECT)
def _op_spawn_attached_effect(m: EclMachineTh08, instr: EclInstr):
    pass  # 附着特效(EclRunHigh.inl:458-474), 不接


@EclMachineTh08.register(
    (Th08EclOpcode.SPAWN_EFFECT, Th08EclOpcode.SPAWN_EFFECT_VELOCITY)
)
def _op_spawn_effect(m: EclMachineTh08, instr: EclInstr):
    pass  # 特效, 不接


@EclMachineTh08.register(Th08EclOpcode.SPAWN_ITEM)
def _op_spawn_item(m: EclMachineTh08, instr: EclInstr):
    m.host.spawn_item(m.enemy.pos, m._int_arg(instr, 0))


@EclMachineTh08.register(Th08EclOpcode.SPAWN_ITEMS)
def _op_spawn_items(m: EclMachineTh08, instr: EclInstr):
    m._spawn_items(m._int_arg(instr, 0))


@EclMachineTh08.register(Th08EclOpcode.SPAWN_POINT_ITEMS)
def _op_spawn_point_items(m: EclMachineTh08, instr: EclInstr):
    for _ in range(m._int_arg(instr, 0)):
        m.host.spawn_item(m._jitter_pos(), 1)  # ITEM_POINT


@EclMachineTh08.register(Th08EclOpcode.SET_ITEM_DROP)
def _op_set_item_drop(m: EclMachineTh08, instr: EclInstr):
    m.enemy.item_drop = m._int_arg(instr, 0)


@EclMachineTh08.register(Th08EclOpcode.SET_ITEM_DROP_COUNTS)
def _op_set_item_drop_counts(m: EclMachineTh08, instr: EclInstr):
    e = m.enemy
    e.point_item_drop_count = m._int_arg(instr, 0)
    e.power_or_point_item_drop_count = m._int_arg(instr, 1)


# ---- 系统/表现(146-184) ----


@EclMachineTh08.register(Th08EclOpcode.SET_STAGE_SCRIPT_LABEL)
def _op_set_stage_script_label(m: EclMachineTh08, instr: EclInstr):
    m.host.set_stage_script_label(m._int_arg(instr, 0))


@EclMachineTh08.register(Th08EclOpcode.SET_TRAIL)
def _op_set_trail(m: EclMachineTh08, instr: EclInstr):
    m.enemy.trail = (
        instr.arg_bytes(0)[0],
        m._int_arg(instr, 1),
        m._int_arg(instr, 2),
        m._int_arg(instr, 3),
        0,
    )


@EclMachineTh08.register(Th08EclOpcode.SET_DRAW_GROUP)
def _op_set_draw_group(m: EclMachineTh08, instr: EclInstr):
    m.enemy.draw_group = m._int_arg(instr, 0)


@EclMachineTh08.register(Th08EclOpcode.SET_DAMAGE_REDUCTION_TIMER)
def _op_set_damage_reduction_timer(m: EclMachineTh08, instr: EclInstr):
    m.enemy.invincibility_timer = m._int_arg(instr, 0)


@EclMachineTh08.register(Th08EclOpcode.REMOVE_BULLETS_RADIUS)
def _op_remove_bullets_radius(m: EclMachineTh08, instr: EclInstr):
    m.host.remove_bullets_in_radius(m._world_pos(), m._float_arg(instr, 0))


@EclMachineTh08.register(Th08EclOpcode.REMOVE_ALL_BULLETS_DESPAWN)
def _op_remove_all_bullets_despawn(m: EclMachineTh08, instr: EclInstr):
    # RemoveAllBullets(4): 直接 despawn 不掉道具(BulletManager.cpp:502-505)
    m.host.remove_all_bullets(False)


@EclMachineTh08.register(Th08EclOpcode.SET_ENEMY_MANAGER_VALUE)
def _op_set_enemy_manager_value(m: EclMachineTh08, instr: EclInstr):
    m.world.opcode163_value = m._int_arg(instr, 0)


@EclMachineTh08.register(Th08EclOpcode.SET_SPELLCARD_EFFECT_TRACKING)
def _op_set_spellcard_effect_tracking(m: EclMachineTh08, instr: EclInstr):
    m.host.set_spellcard_effect_tracking(
        m._int_arg(instr, 0),
        Vec3(m._float_arg(instr, 1), m._float_arg(instr, 2), m._float_arg(instr, 3)),
    )


@EclMachineTh08.register(Th08EclOpcode.SET_PRIMARY_VM_ROT_Z)
def _op_set_primary_vm_rot_z(m: EclMachineTh08, instr: EclInstr):
    m.enemy.primary_vm_rot_z = m._float_arg(instr, 0)


@EclMachineTh08.register(Th08EclOpcode.RAND_EXIT_ANGLE)
def _op_rand_exit_angle(m: EclMachineTh08, instr: EclInstr):
    """简化版出场随机角(EclRunHigh.inl:882-893): 只看自机侧/右边缘。"""
    e, w = m.enemy, m.world
    if (w.player_pos.x < e.pos.x and e.pos.x > 96.0) or e.pos.x > 288.0:
        angle = add_normalize_angle(w.rng.unit() * 1.5707964 + 2.3561945, 0.0)
    else:
        angle = f32(w.rng.unit() * 1.5707964 - 0.78539819)
    m._store_float(instr, 0, angle)


@EclMachineTh08.register(Th08EclOpcode.SPAWN_ALIGNMENT_EFFECT)
def _op_spawn_alignment_effect(m: EclMachineTh08, instr: EclInstr):
    # 人妖对齐特效(结界光环, EclRunHigh.inl:936-952); 效果本体 world 阶段
    m.host.spawn_alignment_effect(m._int_arg(instr, 0))


@EclMachineTh08.register(Th08EclOpcode.SUPPRESS_TIMELINE_SPAWNS)
def _op_suppress_timeline_spawns(m: EclMachineTh08, instr: EclInstr):
    m.world.suppress_timeline_spawns = m._int_arg(instr, 0)


@EclMachineTh08.register(Th08EclOpcode.SET_LAST_SPELL_FLAGS)
def _op_set_last_spell_flags(m: EclMachineTh08, instr: EclInstr):
    # GameManager 标志位操作(EclRunHigh.inl:902-919)是 world 阶段接线;
    # ENEMY_FLAG_PAUSE_TIMER 置位在本侧
    m.enemy.freeze_ecl_during_bombs = 1
    m.host.set_last_spell_flags()


@EclMachineTh08.register(Th08EclOpcode.SET_PHASE_START_LIFE)
def _op_set_phase_start_life(m: EclMachineTh08, instr: EclInstr):
    m.enemy.phase_starting_life = m._int_arg(instr, 0)


@EclMachineTh08.register(Th08EclOpcode.MOVE_RANDOM_BIASED)
def _op_move_random_biased(m: EclMachineTh08, instr: EclInstr):
    """ApplyRandomBiasedMove(EclDependencies.cpp:188-274):
    3/4 概率朝自机(环绕取近路) ±45° 随机, 1/4 完全随机。"""
    e, w = m.enemy, m.world
    if w.rng.int_below(4) != 0:
        if w.player_pos.x < e.pos.x:
            wrapped = w.player_pos.x + 384.0
            if e.pos.x - w.player_pos.x < wrapped - e.pos.x:
                angle = add_normalize_angle(w.rng.unit() * 1.5707964 + 2.3561945, 0.0)
            else:
                angle = add_normalize_angle(w.rng.unit() * 1.5707964 - 0.78539819, 0.0)
        else:
            wrapped = w.player_pos.x - 384.0
            if w.player_pos.x - e.pos.x < e.pos.x - wrapped:
                angle = f32(w.rng.unit() * 1.5707964 - 0.78539819)
            else:
                angle = add_normalize_angle(w.rng.unit() * 1.5707964 + 2.3561945, 0.0)
    else:
        angle = f32(w.rng.unit() * 2.0 * ZUN_PI - ZUN_PI)
    if e.pos.y < e.lower_move_limit.y + 48.0 and angle < 0.0:
        angle = -angle
    if e.pos.y > e.upper_move_limit.y - 48.0 and angle > 0.0:
        angle = -angle
    if m._int_arg(instr, 0) <= 0:
        e.angle = angle
        e.move_speed = m._float_arg(instr, 2)
        e.move_mode = 1
        e.move_interp_timer = e.move_interp_start_time = 0
    else:
        m._timed_polar_displacement(instr, angle)


@EclMachineTh08.register(Th08EclOpcode.START_STAGE_BACKGROUND_SEQUENCE)
def _op_start_stage_background_sequence(m: EclMachineTh08, instr: EclInstr):
    m.host.start_stage_background_sequence()


@EclMachineTh08.register(Th08EclOpcode.HIDE_CLOCK)
def _op_hide_clock(m: EclMachineTh08, instr: EclInstr):
    m.host.clock_hide()


@EclMachineTh08.register(Th08EclOpcode.ADVANCE_CLOCK)
def _op_advance_clock(m: EclMachineTh08, instr: EclInstr):
    # 封顶 12/音效/表盘闪动在宿主侧(EclRunHigh.inl:957-967, 见
    # Th08GameEclHost.clock_advance)
    m.host.clock_advance()


@EclMachineTh08.register(Th08EclOpcode.SET_EXTRA_VM_FIXED_OFFSET)
def _op_set_extra_vm_fixed_offset(m: EclMachineTh08, instr: EclInstr):
    m.enemy.extra_vm_fixed_offset = m._int_arg(instr, 0)


@EclMachineTh08.register(Th08EclOpcode.SET_NO_DAMAGE_DURING_STOP)
def _op_set_no_damage_during_stop(m: EclMachineTh08, instr: EclInstr):
    m.enemy.no_damage_during_stop = m._int_arg(instr, 0)


@EclMachineTh08.register(Th08EclOpcode.SET_BONUS_UPDATES_DISABLED)
def _op_set_bonus_updates_disabled(m: EclMachineTh08, instr: EclInstr):
    m.host.set_bonus_updates_disabled(m._int_arg(instr, 0))
