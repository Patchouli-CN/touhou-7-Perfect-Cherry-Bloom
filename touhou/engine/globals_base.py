"""全局计分基座 —— 不分作品的 STG 通用计数(分数/残机/炸弹/火力/死亡/重试)。

对照 GameManager 计分层中作品无关的部分: 真实分/显示分追赶(guiScore
逐帧追赶, 结算时 snap 对齐)、残机/炸弹/火力/死亡/重试计数。

作品专属概念(th07 的樱点四元组/动态难度 rank)不在这里, 由作品层子类扩展
(games/th07/globals.py 的 ZunGlobals)。引擎只定契约, 作品来履约。
"""

from __future__ import annotations

import msgspec

# 分数上限 (GameManager::OnUpdate / CutChain)
SCORE_MAX = 999999999

# guiScore 每帧追赶增量上限 (GameManager::OnUpdate)
GUI_SCORE_INCREMENT_MAX = 578910

__all__ = ["GUI_SCORE_INCREMENT_MAX", "SCORE_MAX", "GlobalsBase"]


class GlobalsBase(msgspec.Struct):
    """一局游戏的通用计数基座(对应 ZunGlobals + GameManager 的作品无关部分)。

    作品层继承追加专属字段与结算方法(th07: 樱点/动态难度/擦弹计数);
    add_score 等为可覆盖扩展点(th07 原样继承, 未覆盖)。
    """

    # ---- 分数 ----
    score: int = 0                  # 真实分(=显示分语义)
    gui_score: int = 0              # HUD 显示分, 每帧向 score 追赶
    gui_score_difference: int = 0   # 当前帧追赶步长(单调只增)

    # ---- 计数 ----
    lives_remaining: float = 3.0
    bombs_remaining: float = 3.0
    bombs_used: float = 0.0
    current_power: float = 0.0
    deaths: int = 0
    num_retries: int = 0

    # ---- 分数 (§0.2) ----
    def add_score(self, v: int) -> None:
        """入参为代码值(=显示分*10), 真实入账 v//10 (GameManager::AddScore)。"""
        self.score += v // 10

    def tick_gui_score(self) -> None:
        """每帧: guiScore 追赶 score (GameManager::OnUpdate)。

        inc = (score-guiScore)>>5, 最小 1 最大 578910;
        guiScoreDifference 单调只增不降, 追上后归零。
        """
        if self.score >= 1000000000:
            self.score = SCORE_MAX
        if self.gui_score == self.score:
            return
        if self.score < self.gui_score:
            self.score = self.gui_score
        inc = (self.score - self.gui_score) >> 5
        if inc >= GUI_SCORE_INCREMENT_MAX:
            inc = GUI_SCORE_INCREMENT_MAX
        elif inc == 0:
            inc = 1
        if self.gui_score_difference < inc:
            self.gui_score_difference = inc
        if self.gui_score + self.gui_score_difference > self.score:
            self.gui_score_difference = self.score - self.gui_score
        self.gui_score += self.gui_score_difference
        if self.gui_score >= self.score:
            self.gui_score_difference = 0
            self.gui_score = self.score

    def snap_gui_score(self) -> None:
        """显示分立即对齐真实分 (GameManager::CutChain, 结算/切关时用)。"""
        if self.score >= 1000000000:
            self.score = SCORE_MAX
        self.gui_score = self.score
        self.gui_score_difference = 0
