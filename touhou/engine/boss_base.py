"""Boss 系统基座 —— 不分作品的 Boss 阶段/符卡状态机框架。

对照 EnemyManager.cpp / EclManager.cpp / 规格 §B 中作品无关的部分:
生命阈值(跌破→切阶段+清场)、符卡 Begin/End(捕获条件: 超时/用弹/死亡 →
不捕获)、捕获分随时间线性衰减、超时回调状态机骨架。

注入点/扩展点(引擎只定契约, 作品来履约):
- ``spellcard_scores``: 符卡基础分值表(作品级注入, 空 = 未配置); 作品层可
  覆盖 ``_score_table()`` 提供内置默认表(th07 回落到 games/th07/data.py)。
- 作品专属结算(th07 超时樱点惩罚/樱点擦弹加成)留在作品层子类
  (games/th07/boss.py 的 handle_timer_callback / add_graze_bonus)。
"""

from __future__ import annotations

import msgspec
from typing import Any, Sequence

from .enemies import DamageResult, settle_damage
from ..utils import Vec2

__all__ = ["BossBase"]


class BossBase(msgspec.Struct):
    """一个可被打且带阶段/符卡的 Boss 的通用框架。"""

    name: str = "Boss"
    pos: Vec2 = Vec2.zero()
    life: float = 0.0
    max_life: float = 0.0
    is_active: int = 0                     # 0=无符卡 1=进行中 2=超时失败
    phase: int = 0
    boss_id: int = 0
    invincibility_timer: int = 0
    # (阈值, 阶段切换回调标识); 阈值按生命降序
    life_thresholds: list[tuple[float, int]] = msgspec.field(default_factory=list)
    # 超时回调 (ECL_SET_TIMER_CALLBACK_THRESHOLD/SUB)
    timer_callback_threshold: int = -1     # 帧; -1=无
    timer_callback_sub: int = 0
    death_callback_sub: int = -1
    seconds_remaining: int = 0             # 剩余秒显示(bossId==0 时每帧更新)
    # 符卡
    spellcard_idx: int = -1
    spellcard_face: int = 0                # 宣言立绘 sprite 下标 (ECL BEGIN_SPELLCARD
                                           # arg0, Gui.cpp:367 +1197 取 face_0{stage}_00)
    spellcard_time_limit: int = 0          # 帧
    is_capturing: bool = False
    is_survival_spellcard: bool = False
    capture_score: int = 0                 # 代码值
    graze_bonus_score: int = 0             # 代码值(擦弹加成)
    score_drain_rate: int = 0
    used_bomb: bool = False
    timer: int = 0
    # 符卡分值表(作品级注入; 空 = 未配置, th07 子类覆盖 _score_table 回落默认表)
    spellcard_scores: tuple[int, ...] = ()

    def _score_table(self) -> Sequence[int]:
        """生效的符卡分值表(注入表; 作品层可覆盖以提供内置默认表)。"""
        return self.spellcard_scores

    # ---- 生命阈值 / 阶段 ----
    def set_life(self, life: float) -> None:
        self.life = self.max_life = life

    def check_life_threshold(self, clear_field_cb=None) -> int:
        """HandleLifeCallback (EnemyManager.cpp:373-428)。

        若生命跌破阈值, 钉住生命、切阶段并清场。返回命中的回调标识(0=无)。
        """
        for i, (threshold, cb) in enumerate(self.life_thresholds):
            if self.life < threshold:
                self.life = threshold          # 钉住生命
                self.phase = i + 1
                # 清掉已触发的阈值与超时回调
                self.life_thresholds = self.life_thresholds[i + 1:]
                self.timer_callback_threshold = -1
                if clear_field_cb:
                    clear_field_cb()           # 清场(非 boss 敌 deathCallback)
                return cb
        return 0

    # ---- 符卡 ----
    def begin_spellcard(self, idx: int, time_limit: int,
                        timeout_sub: int = 0) -> None:
        """BeginSpellcard (EclManager.cpp:658-752)。"""
        self.spellcard_idx = idx
        self.spellcard_time_limit = time_limit
        self.is_active = 1
        self.is_capturing = True
        self.capture_score = self._score_table()[idx]
        self.graze_bonus_score = 0
        # scoreDrainRate 为 int: captureScore / (threshold/60 + 10)
        self.score_drain_rate = self.capture_score // (time_limit // 60 + 10)
        self.used_bomb = False
        self.timer = 0
        # C++ 中 threshold/sub 由 ECL 单独设置; 这里一并登记方便超时状态机使用
        self.set_timer_callback(time_limit, timeout_sub)

    def set_timer_callback(self, threshold: int, sub: int) -> None:
        """ECL_SET_TIMER_CALLBACK_THRESHOLD(114)/SUB(115): 超时阈值(帧)与回调。"""
        self.timer_callback_threshold = threshold
        self.timer_callback_sub = sub

    def tick(self) -> None:
        """每帧: 计时 + 捕获分线性衰减 (EclManager.cpp:2241-2257)。

        非 survival 符卡每帧由基础分重算:
        captureScore = SpellcardScore[idx] - timer*scoreDrainRate/60, 向下取整到 10;
        到 (时间限制+10) 秒时衰减到 0。
        """
        if not self.is_active:
            return
        self.timer += 1
        if (self.is_capturing and self.spellcard_idx >= 0
                and not self.is_survival_spellcard):
            score = int(self._score_table()[self.spellcard_idx]
                        - self.timer * self.score_drain_rate / 60.0)
            if score > 0:
                score -= score % 10
            self.capture_score = max(0, score)

    # ---- 捕获判定 ----
    def mark_bombed(self) -> None:
        """玩家用弹 (Player.cpp:1745-1749): 不算捕获, usedBomb=isActive。"""
        self.capture_score = 0
        self.is_capturing = False
        self.used_bomb = bool(self.is_active)

    def mark_death(self) -> None:
        """玩家死亡/结界破裂 (Player.cpp:1782-1783, 2175-2176): 捕获失败。"""
        self.capture_score = 0
        self.is_capturing = False

    def end_spellcard(self) -> dict:
        """EndSpellcard (EclManager.cpp:755-849), 返回结算/清场事件 dict:

          ended: 是否有进行中的符卡;  timed_out: is_active==2(超时失败);
          captured / score(代码值 = captureScore+grazeBonusScore) /
          spell_cards_captured(上层计数);
          despawn_bullets=(8000,1) / remove_all_enemies=(8000,0):
            非超时时清弹清敌事件, 上层执行 BulletManager.DespawnBullets /
            EnemyManager.RemoveAllEnemies(scoreMax, scoreMin=清弹返回值)。
        """
        result: dict[str, Any] = {
            "ended": False, "timed_out": False, "captured": False,
            "score": 0, "spell_cards_captured": 0,
            "despawn_bullets": None, "remove_all_enemies": None}
        if self.is_active:
            result["ended"] = True
            if self.is_active == 1:
                result["despawn_bullets"] = (8000, 1)
                result["remove_all_enemies"] = (8000, 0)
                if self.is_capturing:
                    result["captured"] = True
                    result["score"] = self.capture_score + self.graze_bonus_score
                    result["spell_cards_captured"] = 1
            else:
                result["timed_out"] = True
            self.is_active = 0
        self.spellcard_idx = -1
        return result

    def damage(self, amount: float, *, from_bomb: bool = False,
               is_focus: bool = False, bomb_in_use: bool | None = None,
               stage: int = 1, is_reimu_a: bool = False) -> DamageResult:
        """受击结算 (规格 §A.6 下半, 数值以 EnemyManager.cpp:782-890 为准)。

        返回 DamageResult(damage/cherry_gain/score_code), 由上层接 globals。
        """
        if bomb_in_use is None:
            bomb_in_use = from_bomb
        r = settle_damage(
            int(amount), is_boss=True, is_focus=is_focus,
            bomb_in_use=bomb_in_use, bomb_damage=from_bomb, stage=stage,
            spellcard_active=bool(self.is_active) and self.spellcard_idx >= 0,
            used_bomb=self.used_bomb,
            invincibility_timer=self.invincibility_timer,
            enemy_timer=self.timer, is_reimu_a=is_reimu_a,
        )
        self.life -= r.damage
        if self.life <= 0:
            self.life = 0
        return r

    @property
    def alive(self) -> bool:
        return bool(self.is_active) and self.life > 0
