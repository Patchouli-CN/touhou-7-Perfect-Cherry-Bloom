"""TH08(东方永夜抄)的 16 槽时间轴执行器 —— Th08TimelineRunner。

对照 th08 反编译源码 EnemyTimeline.cpp:121-297(EclTimeline::Run):
- 指令布局: time i32 / opcode i16 / size u8 / difficultyMask u8 /
  args int/float[7](操作数全 raw, EnemyManager.hpp:419-430), 解析在
  games/th08/ecl_file.py 的 ``EclTimelineInstrTh08``;
- 17 种 opcode(EnemyTimeline.cpp:134-283):
  0/1 定点生敌(1=镜像 X)、2/4 x 区间随机、3/5 全屏随机 x、
  11/12 带掉落数生敌、15 强制生敌(无门控)、6 MsgRead、7 MsgWait、
  8 boss pendingSub、9 SetPower、10 等 boss 死、13/14 事件槽同步、
  16 Retry 菜单;
- 生敌门控: boss 在场(g_Gui.IsBossPresent)或 op175 置位的
  suppressTimelineSpawns 时跳过(0-5/11/12 系; 15 无门控);
- 难度掩码过滤: 指令掩码不含当前难度位则跳过(与 ECL 指令不同,
  时间轴无 override 概念, EnemyTimeline.cpp:131-132)。

world 驱动接线(由哪个对象每帧 step、spawn 进真实敌表)是阶段 3 的工作;
本轮 runner 单测用 stub host/world。
"""

from __future__ import annotations

from typing import cast

from ...engine.ecl import (
    EclContextArgs,
    EclHost,
    EclWorld,
    Vec3,
    PLAYFIELD_W,
)
from .ecl_file import EclFileTh08, EclTimelineInstrTh08

# 生敌门控适用的 opcode(0-5/11/12; 15 强制生敌无门控, EnemyTimeline.cpp:154-163)
_GATED_SPAWNS = (0, 1, 2, 3, 4, 5, 11, 12)


class Th08TimelineRunner:
    """一条 th08 时间轴的执行器。每帧 step() 一次。"""

    def __init__(
        self, ecl_file: EclFileTh08, index: int, world: EclWorld, host: EclHost
    ) -> None:
        # 基类 timelines 注记的是 th07 形态(engine 层无法引用 th08 类型),
        # EclFileTh08 里实际恒为 EclTimelineInstrTh08(见 ecl_file.py parse)
        self.timelines = cast(
            tuple[EclTimelineInstrTh08, ...], ecl_file.timelines[index]
        )
        self.world = world
        self.host = host
        self.time = 0
        self.idx = 0  # 当前指令下标(模拟 this->instruction 指针)

    @property
    def done(self) -> bool:
        return self.idx >= len(self.timelines) or self.timelines[self.idx].time < 0

    def _spawn_gated(self) -> bool:
        """生敌门控: boss 在场或 op175 全局抑制时跳过
        (EnemyTimeline.cpp:143/168/190/207)。"""
        if self.world.suppress_timeline_spawns:
            return False
        return not any(b is not None for b in self.world.bosses)

    def step(self) -> None:
        w, host = self.world, self.host
        while not self.done:
            instr = self.timelines[self.idx]
            if self.time == instr.time:
                # 难度掩码过滤(EnemyTimeline.cpp:131-132)
                if (instr.difficulty_mask & w.difficulty_mask) == 0:
                    self.idx += 1
                    continue
                op = instr.opcode
                mirror = 1 if op in (1, 4, 12) else 0  # 奇数变体 = 镜像 X
                if op in (0, 1, 15) or op in (11, 12):
                    if op == 15 or self._spawn_gated():
                        pos = Vec3(instr.arg_float(1), instr.arg_float(2), 0.0)
                        if op in (11, 12):
                            # 带掉落数(EnemyTimeline.cpp:165-185):
                            # itemDrop=-1, score=args[6], 掉落数落到新敌
                            spawned = host.spawn_enemy(
                                instr.arg_int(0), pos, instr.arg_int(3), -1,
                                instr.arg_int(6), mirror, EclContextArgs(),
                            )
                            if spawned is not None:
                                st = spawned.state
                                st.point_item_drop_count = instr.arg_int(4)
                                st.power_or_point_item_drop_count = instr.arg_int(5)
                        else:
                            host.spawn_enemy(
                                instr.arg_int(0), pos, instr.arg_int(3),
                                instr.arg_int(4), instr.arg_int(5),
                                mirror, EclContextArgs(),
                            )
                elif op in (2, 4):
                    if self._spawn_gated():
                        # x 区间随机(EnemyTimeline.cpp:187-202)
                        lo, hi = instr.arg_float(1), instr.arg_float(2)
                        pos = Vec3(w.rng.unit() * (hi - lo) + lo, instr.arg_float(3), 0.0)
                        host.spawn_enemy(
                            instr.arg_int(0), pos, instr.arg_int(4),
                            instr.arg_int(5), instr.arg_int(6),
                            mirror, EclContextArgs(),
                        )
                elif op in (3, 5):
                    if self._spawn_gated():
                        # 全屏随机 x(EnemyTimeline.cpp:204-219)
                        pos = Vec3(
                            w.rng.unit() * PLAYFIELD_W, instr.arg_float(1), 0.0
                        )
                        host.spawn_enemy(
                            instr.arg_int(0), pos, instr.arg_int(2),
                            instr.arg_int(3), instr.arg_int(4),
                            mirror, EclContextArgs(),
                        )
                elif op == 6:
                    host.msg_read(instr.arg_int(0))
                elif op == 7:
                    if host.msg_wait():
                        self.time -= 1  # 底部 time++ 抵消, 时间轴停住
                        break
                elif op == 8:
                    boss = w.bosses[instr.arg_int(0)] if 0 <= instr.arg_int(0) < len(
                        w.bosses
                    ) else None
                    if boss is not None:
                        # boss pendingEclSubroutineIndex(= run_interrupt 机制)
                        boss.run_interrupt = instr.arg_int(1)
                elif op == 9:
                    host.set_power(instr.arg_int(0))
                elif op == 10:
                    boss = w.bosses[instr.arg_int(0)] if 0 <= instr.arg_int(0) < len(
                        w.bosses
                    ) else None
                    if boss is not None and boss.active:
                        self.time -= 1  # 等 boss 退场(EnemyTimeline.cpp:243-251)
                        break
                elif op == 13:
                    # 事件槽同步(消费): 有匹配则清掉继续, 无匹配停轴等
                    # (EnemyTimeline.cpp:253-271)
                    matched = False
                    for i in range(4):
                        if w.timeline_event_slots[i] == instr.arg_int(0):
                            w.timeline_event_slots[i] = -1
                            matched = True
                    if not matched:
                        self.time -= 1
                        break
                elif op == 14:
                    # 事件槽同步(投放): 填进所有空槽(EnemyTimeline.cpp:273-282)
                    for i in range(4):
                        if w.timeline_event_slots[i] < 0:
                            w.timeline_event_slots[i] = instr.arg_int(0)
                elif op == 16:
                    host.show_retry_menu()
            elif self.time < instr.time:
                break
            self.idx += 1
        self.time += 1
