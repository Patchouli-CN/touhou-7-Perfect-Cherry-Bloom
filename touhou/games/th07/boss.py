"""Boss 战状态机(th07) —— 移植自 EnemyManager.cpp / EclManager.cpp / 规格 §B。

通用框架(生命阈值切阶段/符卡 Begin·End/捕获分衰减/受击结算)已上移到引擎层
基座 engine/boss_base.py 的 BossBase; 本模块只留 th07 专属:
- 符卡分值表默认回落(SPELLCARD_SCORE, 141 张, 单一来源在同包 data.py);
- handle_timer_callback 超时状态机的樱点惩罚(cherry_penalty);
- add_graze_bonus 的樱点擦弹加成公式。
纯逻辑: 不 import globals, 得分/樱点/清弹/清敌都通过返回值透出给上层。
"""

from __future__ import annotations

from typing import Sequence

from .data import SPELLCARD_SCORE
from ...engine.boss_base import BossBase

# SPELLCARD_SCORE: 141 张符卡基础分值(代码值 = 显示分*10,
# 照抄 EnemyManager.cpp:16-37 g_SpellcardScore[141]), 数值表单一来源在
# 同包 data.py; 这里的导入即 th07 默认表, 单作品覆盖经
# Boss.spellcard_scores 注入(空 = 用默认表)。


class Boss(BossBase):
    """th07 的 Boss: 通用框架见基类 BossBase, 这里挂 th07 默认分值表与樱点结算。"""

    def _score_table(self) -> Sequence[int]:
        """生效的符卡分值表(注入表优先, 否则模块默认 = th07 表)。"""
        return self.spellcard_scores or SPELLCARD_SCORE

    # ---- B.5 超时状态机(th07: 含樱点惩罚) ----
    def handle_timer_callback(
        self, *, cherry_above_start: int = 0, clear_field_cb=None
    ) -> dict:
        """HandleTimerCallback (EnemyManager.cpp:432-525)。

        返回事件 dict:
          fired / callback(触发的 sub id, 上层据此切阶段或符卡) /
          cherry_penalty(=(cherry-cherryStart)*0.25 向下取整 10, 上层 cherry 自减) /
          remove_all_bullets(RemoveAllBullets(10) 信号) / clear_field。
        """
        ev = {
            "fired": False,
            "callback": 0,
            "cherry_penalty": 0,
            "remove_all_bullets": False,
            "clear_field": False,
        }
        if self.timer_callback_threshold < 0:
            return ev
        if self.boss_id == 0:
            self.seconds_remaining = (self.timer_callback_threshold - self.timer) // 60
        if self.timer < self.timer_callback_threshold:
            return ev
        # 若有更高的生命阈值, 先钉生命并清掉(不触发其回调)
        pending = [t for t, _ in self.life_thresholds]
        if pending and max(pending) > 0:
            top = max(pending)
            self.life = top
            self.life_thresholds.remove(
                next(pair for pair in self.life_thresholds if pair[0] == top)
            )
        ev["fired"] = True
        ev["callback"] = self.timer_callback_sub
        ev["clear_field"] = True
        self.timer_callback_threshold = -1
        self.timer_callback_sub = self.death_callback_sub
        self.timer = 0
        if not self.is_survival_spellcard:
            self.capture_score = 0
            self.is_capturing = False
            if self.is_active:
                self.is_active += 1  # 2 = 超时失败
            ev["remove_all_bullets"] = True
            penalty = int(cherry_above_start * 0.25)
            penalty -= penalty % 10
            ev["cherry_penalty"] = penalty
        if clear_field_cb:
            clear_field_cb()  # 清场(非 boss 敌 life=0)
        return ev

    def add_graze_bonus(self, cherry_above_start: int) -> None:
        """ScoreGraze 的符卡擦弹加成: +2500 + (cherry-cherryStart)/1500*20 (代码值)。"""
        self.graze_bonus_score += 2500 + cherry_above_start // 1500 * 20
