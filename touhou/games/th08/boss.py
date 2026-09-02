"""Boss 战状态机(th08 东方永夜抄) —— BossBase + Spellcard 动态符卡分。

对照 th08 反编译源码(Reference/th08-ref/src/):
- 通用框架(生命阈值/符卡 Begin·End/捕获条件/超时)在引擎基座
  engine/boss_base.py 的 BossBase;
- th08 无静态符卡分值表: bonus 由 ECL op122 指令携带
  (EclSpellCardInstructionArgs.bonus, EclDependencies.cpp:18-36), 经
  host.set_spellcard_bonus → world 取入;
- 动态分(Spellcard.cpp StartSpell :728-737): bonusProgress=bonus,
  scoreLimit=bonus(生存符 op155 timeoutSpell → 99999990, :730-733);
  bonusCounter = (bonus − bonus/7) / (时限帧/60) (:735-736);
- 每帧衰减(OnUpdateImpl :1293-1301): 捕获有效且未禁更新且非生存符时,
  bonusProgress -= bonusCounter/60, 再向下取整到 10 的倍数;
- AddBonusProgress(:1243-1257, 时刻符点收集 +8000, ItemManager.cpp:631):
  未达 scoreLimit 时 bonusCounter += amount/120, 达到则夹取到 scoreLimit;
- 收取(EndSpell :1029-1049): bonusAward = bonusProgress; 时刻符点奖
  pendingTimeOrbs: 生存符 700, 否则剩余帧 ≥ 时限−时限/7 → 1000,
  ≥180 → 900*(剩余−180)/(i−180)+100, 否则 100; 结算时 AddTimeOrbs
  (:763-766);
- op184 SetBonusUpdatesDisabled(EclRunHigh.inl:972)经宿主同步到
  bonus_updates_disabled, 门控衰减与 AddBonusProgress。
"""

from __future__ import annotations

from ...engine.boss_base import BossBase

# 生存符(op155 timeoutSpell)的符分上限 (EclRunHigh.inl:830, Spellcard.cpp:733)
TIMEOUT_SPELL_SCORE_LIMIT = 99999990


class Th08Boss(BossBase):
    """th08 的 Boss: 符卡分 = Spellcard 的 bonusProgress/bonusCounter 模型。"""

    capture_base: int = 0  # 本张符卡的 bonus 初值(begin 时钉住)
    bonus_counter: int = 0  # Spellcard.bonusCounter(衰减速度系数)
    score_limit: int = 0  # Spellcard.scoreLimit(常态=bonus, 生存符=99999990)
    pending_time_orbs: int = 0  # 收取待给时刻符点(EndSpell 算, 结算时入账)
    bonus_updates_disabled: int = 0  # op184 (SPELLCARD_FLAG_BONUS_UPDATES_DISABLED)
    was_captured: bool = False  # 上一张符卡是否捕获(Spellcard WasCaptured,
    # 变量 10099 在符卡结束后读它, EclOperandsInt.cpp:145-147)

    def begin_spellcard(
        self,
        idx: int,
        time_limit: int,
        timeout_sub: int = 0,
        *,
        bonus: int = 0,
        timeout_spell: bool = False,
    ) -> None:
        """BeginSpellcard 的 th08 版 (StartSpell, Spellcard.cpp:710-752)。"""
        self.spellcard_idx = idx
        self.spellcard_time_limit = time_limit
        self.is_active = 1
        self.is_capturing = True
        self.is_survival_spellcard = timeout_spell
        self.capture_base = bonus
        self.capture_score = bonus  # bonusProgress
        # scoreLimit (Spellcard.cpp:729-733)
        self.score_limit = TIMEOUT_SPELL_SCORE_LIMIT if timeout_spell else bonus
        # bonusCounter 初值 (Spellcard.cpp:735-736); 时限不足 1 秒守除零
        self.bonus_counter = (bonus - bonus // 7) // max(time_limit // 60, 1)
        self.pending_time_orbs = 0
        self.graze_bonus_score = 0
        self.score_drain_rate = self.bonus_counter
        self.used_bomb = False
        self.was_captured = False
        self.timer = 0
        self.set_timer_callback(time_limit, timeout_sub)

    @property
    def time_remaining(self) -> int:
        """Spellcard.timeRemaining (GetTimerFrames, EclManager.cpp:149-153;
        喂 VM 变量 10100)。"""
        if not self.is_active or self.spellcard_idx < 0:
            return 0
        return max(self.spellcard_time_limit - self.timer, 0)

    def tick(self) -> None:
        """每帧: 计时 + 捕获分衰减 (OnUpdateImpl, Spellcard.cpp:1293-1301)。

        仅捕获有效(CAPTURE_VALID)且未禁更新(BONUS_UPDATES_DISABLED)且
        非生存符(TIMEOUT_SPELL)时: bonusProgress -= bonusCounter/60,
        再向下取整 10。决死冻结(deathbombFreezeActive)期间 world 侧
        不调本 tick(:1268)。
        """
        if not self.is_active:
            return
        self.timer += 1
        if (
            self.is_capturing
            and self.spellcard_idx >= 0
            and not self.bonus_updates_disabled
            and not self.is_survival_spellcard
        ):
            score = self.capture_score - self.bonus_counter // 60
            score -= score % 10
            self.capture_score = max(0, score)

    def add_bonus_progress(self, amount: int) -> None:
        """Spellcard::AddBonusProgress (Spellcard.cpp:1243-1257):
        时刻符点收集等加分; 未达 scoreLimit 时 bonusCounter 同步 +amount/120。"""
        if self.bonus_updates_disabled:
            return
        self.capture_score += amount
        if self.capture_score >= self.score_limit:
            self.capture_score = self.score_limit
        else:
            self.bonus_counter += amount // 120

    def end_spellcard(self) -> dict:
        """EndSpellcard 的 th08 版: 捕获分 = bonusProgress(capture_score),
        捕获时算 pendingTimeOrbs (Spellcard.cpp:1029-1049) 透出给上层入账。"""
        if (
            self.is_active == 1
            and self.is_capturing
            and self.spellcard_idx >= 0
        ):
            self.pending_time_orbs = self._capture_time_orbs()
        res = super().end_spellcard()
        res["pending_time_orbs"] = self.pending_time_orbs
        if res["captured"]:
            self.was_captured = True
        return res

    def _capture_time_orbs(self) -> int:
        """捕获的时刻符点奖 (Spellcard.cpp:1030-1050)。"""
        if self.is_survival_spellcard:
            return 700
        i = self.spellcard_time_limit - self.spellcard_time_limit // 7
        tr = self.time_remaining
        if tr >= i:
            return 1000
        if tr >= 180:
            return 900 * (tr - 180) // (i - 180) + 100
        return 100
