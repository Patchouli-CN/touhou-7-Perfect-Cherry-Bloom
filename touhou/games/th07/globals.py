""" 全局状态(樱点/动态难度) —— 移植自 GameManager.cpp / 规格 §0.2 §0.3。

ZunGlobals 继承引擎层通用计分基座(engine/globals_base.py 的 GlobalsBase:
score/gui_score 追赶、残机/炸弹/火力/死亡/重试), 本模块只留 th07 专属:
樱点四元组(cherry/cherryMax/cherryPlus/cherryStart)与 subrank/rank 动态难度、
擦弹/符卡/点道具计数。
"""

from __future__ import annotations

from ...engine.globals_base import (  # noqa: F401 (SCORE_MAX/GUI_SCORE_INCREMENT_MAX 为兼容再导出)
    GUI_SCORE_INCREMENT_MAX, SCORE_MAX, GlobalsBase)

# cherryMax 相对 cherryStart 的上限 (GameManager::IncreaseCherryMax)
CHERRY_MAX_RANGE = 9999990

# cherryPlus 相对 cherryStart 的上限, 达到即触发森罗结界 (GameManager::AddCherryPlus)
CHERRY_PLUS_RANGE = 50000

# 动态难度表 g_RankArray[difficulty] = (初始rank, minRank, maxRank)
RANK_TABLE = [
    (16, 12, 20),  # Easy
    (16, 10, 32),  # Normal
    (16, 10, 32),  # Hard
    (16, 10, 32),  # Lunatic
    (16, 15, 16),  # Extra
    (16, 15, 16),  # Phantasm
]


class ZunGlobals(GlobalsBase):
    """一局游戏的全局计数状态(对应 ZunGlobals + GameManager 计分部分)。

    通用字段(score/gui_score/残机/炸弹/火力/死亡/重试)在基类 GlobalsBase;
    以下均为 th07 专属。
    """

    # ---- 樱点 ----
    cherry: int = 0
    cherry_max: int = 0
    cherry_plus: int = 0
    cherry_start: int = 0           # 本关樱点 baseline

    # ---- 动态难度(0..32 scale) ----
    rank: int = 16
    min_rank: int = 10
    max_rank: int = 32
    subrank: int = 0

    # ---- 计数(th07 专属) ----
    graze_in_stage: int = 0
    graze_in_total: int = 0
    spell_cards_captured: int = 0
    point_items_collected_this_stage: int = 0
    point_items_collected_for_extend: int = 0
    extends_from_point_items: int = 0
    next_needed_point_items_for_extend: int = 50

    def initialize_rank(self, difficulty: int) -> None:
        """按难度初始化 rank (GameManager::InitializeRank / g_RankArray)。"""
        self.rank, self.min_rank, self.max_rank = RANK_TABLE[difficulty]
        self.subrank = 0

    # ---- 樱点 (§0.3) ----
    def add_cherry(self, x: int) -> None:
        """cherry += x, 封顶 cherryMax (GameManager::AddCherry)。"""
        self.cherry = min(self.cherry + x, self.cherry_max)

    def add_cherry_plus(self, x: int) -> bool:
        """GameManager::AddCherryPlus: cherry 与 cherryPlus 同时累加。

        cherryPlus 封顶 cherryStart+50000; 返回本次是否触达该上限(=应开结界,
        结界本身由玩家侧处理)。
        """
        self.cherry = min(self.cherry + x, self.cherry_max)
        if x > 0:
            self.cherry_plus = min(self.cherry_plus + x,
                                   self.cherry_start + CHERRY_PLUS_RANGE)
        return self.cherry_plus >= self.cherry_start + CHERRY_PLUS_RANGE

    def increase_cherry_max(self, x: int) -> None:
        """cherryMax += x, 封顶 cherryStart+9999990 (GameManager::IncreaseCherryMax)。"""
        self.cherry_max = min(self.cherry_max + x,
                              self.cherry_start + CHERRY_MAX_RANGE)

    def subtract_cherry_drain(self, drain: int) -> None:
        """炸弹樱点消耗, 封底 cherryStart (PlayerBombInfo::SubtractCherryDrain)。"""
        if self.cherry - self.cherry_start >= drain:
            self.cherry -= drain
        else:
            self.cherry = self.cherry_start

    # ---- 动态难度 ----
    def increase_subrank(self, x: int) -> None:
        """GameManager::IncreaseSubrank: 每满 100 升 1 rank, 封顶 maxRank。"""
        self.subrank += x
        while 100 <= self.subrank:
            self.rank += 1
            self.subrank -= 100
        if self.rank > self.max_rank:
            self.rank = self.max_rank

    def decrease_subrank(self, x: int) -> None:
        """GameManager::DecreaseSubrank: 每欠 100 降 1 rank, 封底 minRank。"""
        self.subrank -= x
        while self.subrank < 0:
            self.rank -= 1
            self.subrank += 100
        if self.rank < self.min_rank:
            self.rank = self.min_rank
