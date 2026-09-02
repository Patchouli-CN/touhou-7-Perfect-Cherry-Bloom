"""Boss 战状态机(th08 东方永夜抄) —— BossBase + 动态符卡分骨架。

对照 th08 反编译源码(Reference/th08-ref/src/):
- 通用框架(生命阈值/符卡 Begin·End/捕获条件/超时)在引擎基座
  engine/boss_base.py 的 BossBase;
- th08 无静态符卡分值表: bonus 由 ECL op122 指令携带
  (EclSpellCardInstructionArgs.bonus, EclDependencies.cpp:18-36), 经
  host.set_spellcard_bonus → world 取入; 捕获分衰减按 Spellcard 的
  bonusCounter 模型(Spellcard.cpp:735-736/1029)是后续阶段(单 B)的工作,
  本期骨架沿用 BossBase 的线性衰减形(以 bonus 为基值)。
"""

from __future__ import annotations

from ...engine.boss_base import BossBase


class Th08Boss(BossBase):
    """th08 的 Boss: 符卡分基值来自 ECL bonus(非静态表)。"""

    capture_base: int = 0  # 本张符卡的 bonus 基值(begin 时钉住, tick 衰减用)

    def begin_spellcard(
        self, idx: int, time_limit: int, timeout_sub: int = 0, *, bonus: int = 0
    ) -> None:
        """BeginSpellcard 的 th08 版: captureScore 基值 = ECL bonus。"""
        self.spellcard_idx = idx
        self.spellcard_time_limit = time_limit
        self.is_active = 1
        self.is_capturing = True
        self.capture_base = bonus
        self.capture_score = bonus
        self.graze_bonus_score = 0
        # scoreDrainRate 为 int: bonus / (threshold/60 + 10)(沿用基座形态)
        self.score_drain_rate = bonus // (time_limit // 60 + 10)
        self.used_bomb = False
        self.timer = 0
        self.set_timer_callback(time_limit, timeout_sub)

    def tick(self) -> None:
        """每帧: 计时 + 捕获分线性衰减(以 capture_base 为基值;
        th08 的 bonusCounter 衰减模型(Spellcard.cpp:1029)留单 B)。"""
        if not self.is_active:
            return
        self.timer += 1
        if (
            self.is_capturing
            and self.spellcard_idx >= 0
            and not self.is_survival_spellcard
        ):
            score = int(
                self.capture_base - self.timer * self.score_drain_rate / 60.0
            )
            if score > 0:
                score -= score % 10
            self.capture_score = max(0, score)
