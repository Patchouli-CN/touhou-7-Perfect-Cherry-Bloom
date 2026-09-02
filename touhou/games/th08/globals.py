"""全局状态(th08 东方永夜抄) —— 时刻符点/妖率计/动态难度收口。

对照 th08 反编译源码(Reference/th08-ref/src/) GameManager 计分层:
- 妖率计 youkaiGauge + 6 槽界 (Player.cpp:1607-1639 初始化,
  GameManager.cpp:1312-1324 AddToYoukaiGauge 夹取);
- 时刻符点 currentTimeOrbs/totalTimeOrbs/lastSpellTimeOrbThreshold
  (GameManager.cpp:200-232 AddTimeOrbs/GetTimeOrbs/GetLastSpellTimeOrbThreshold);
- 动态难度 rank/subrank (GameManager.cpp 的 Increase/DecreaseSubrank,
  与 th07 同构, 表从 GameData.rank_table 取);
- 点道具计数/奖残阈值(ItemManager.cpp:157 的 g_PointItemExtendThresholds 系);
- 得分弹字/状态横幅(AsciiManager/Gui 纯逻辑部分, 同 th07 形态)。

通用计分基座(分数/残机/炸弹/火力/死亡/重试)在 engine/globals_base.py。
"""

from __future__ import annotations

import msgspec

from ...engine.globals_base import GlobalsBase
from ...utils import Vec2
from .data import RANK_TABLE

# 点道具奖残阈值 (ItemManager.cpp:157-160): 本篇 6 档后每 500 一档;
# EX 用 g_ExPointItemExtendThresholds {200, 666, 9999}
_EXTEND_THRESHOLDS = (100, 250, 500, 800, 1100, 9999)
_EXTEND_THRESHOLDS_EX = (200, 666, 9999)

# 弹字槽容量/寿命 (同 th07 AsciiManager 语义)
POPUP_LIFETIME = 60  # AsciiManager.cpp:55-60
POPUP1_CAP = 720
POPUP2_CAP = 3

# 擦弹计数上限 (GameManager.cpp 的 9999/999999 夹取, Player.cpp:475-479 段)
GRAZE_STAGE_CAP = 9999
GRAZE_TOTAL_CAP = 999999


def next_point_item_extend_threshold(extends_so_far: int, difficulty: int) -> int:
    """ItemManager::UpdatePointItemExtendThreshold (ItemManager.cpp:162-186)。"""
    if difficulty < 4:
        if extends_so_far < 6:
            return _EXTEND_THRESHOLDS[extends_so_far]
        return (extends_so_far - 5) * 500 + _EXTEND_THRESHOLDS[5]
    if extends_so_far < 3:
        return _EXTEND_THRESHOLDS_EX[extends_so_far]
    return (extends_so_far - 2) * 500 + _EXTEND_THRESHOLDS_EX[2]


class ScorePopup(msgspec.Struct):
    """一个得分弹字 (AsciiManagerPopup); value 为代码值口径, -1 = PowerUp 字形。"""

    pos: Vec2 = msgspec.field(default_factory=Vec2.zero)
    value: int = 0
    color: int = 0xFFFFFFFF  # ARGB
    timer: int = 0
    kind: int = 1  # 1=普通 (CreateScorePopup), 2=弹消点系


class Th08Globals(GlobalsBase):
    """一局 th08 的全局计数状态。

    通用字段(score/gui_score/残机/炸弹/火力/死亡/重试)在基类 GlobalsBase;
    以下均为 th08 专属。``deaths``/``bombs_used``/``spell_cards_captured``/
    ``graze_in_total`` 字段名与基座/th07 一致(apis._probe 依赖)。
    """

    # ---- 动态难度(GameManager.cpp:32-39 表驱动) ----
    rank: int = 10
    min_rank: int = 8
    max_rank: int = 16
    subrank: int = 0

    # ---- 妖率计 (GameManager.globals->youkaiGauge) ----
    youkai_gauge: int = 0
    # 6 槽界: [0]人限(夹取下限) [1]妖限(夹取上限) [2]人特效阈 [3]妖特效阈
    # [4]人染色阈 [5]妖染色阈 (g_PlayerGaugeBounds, Player.cpp:1607-1639)
    gauge_bounds: list[int] = msgspec.field(
        default_factory=lambda: [-10000, 10000, -8000, 8000, -2000, 2000]
    )

    # ---- 时刻符点 (GameManager.globals->currentTimeOrbs 系) ----
    current_time_orbs: int = 0  # 本关已收符点(GameManager.cpp:218 换关清零)
    total_time_orbs: int = 0  # 累计符点
    last_spell_time_orb_threshold: int = 0  # 本关阈值(GameManager.cpp:881)

    # ---- 最高分跟随(GameManager globals.highScore) ----
    high_score: int = 0
    high_score_num_continues: int = 0

    # ---- 计数 ----
    graze_in_stage: int = 0
    graze_in_total: int = 0
    spell_cards_captured: int = 0
    point_items_collected: int = 0  # 全局累计(CollectPoint, ItemManager.cpp:486)
    point_items_collected_this_stage: int = 0  # 本关(pointItemsCollectedInStage)
    point_item_extends_so_far: int = 0  # pointItemExtendsSoFar(-1=封口)
    next_point_item_extend_threshold: int = 100
    point_item_value: int = 100000  # 点道具当前分值(GameManagerSetup.cpp:149-161 初值)

    # ---- 得分弹字 / 横幅(AsciiManager/Gui 纯逻辑) ----
    popups: list[ScorePopup] = msgspec.field(default_factory=list)
    spellcard_bonus: int = 0  # "Spell Card Bonus!" +N (捕获分)
    spellcard_bonus_timer: int = 0
    bonus_score: int = 0  # "BONUS %8d"(清弹/清场累计分)
    bonus_score_timer: int = 0

    # ---- 动态难度(GameManager::Increase/DecreaseSubrank, 同 th07 语义) ----
    def initialize_rank(self, difficulty: int, table=()) -> None:
        """按难度初始化 rank; 表 = GameData.rank_table(空 = data.py 默认表)。"""
        t = table or RANK_TABLE
        self.rank, self.min_rank, self.max_rank = t[min(difficulty, len(t) - 1)]
        self.subrank = 0

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

    # ---- 妖率计 ----
    def add_to_youkai_gauge(self, amount: int) -> None:
        """GameManager::AddToYoukaiGauge (GameManager.cpp:1312-1324):
        按槽界 [0]/[1] 夹取(炸弹中不加是调用方职责, C 的 forceUpdate 参数)。"""
        self.youkai_gauge += amount
        if self.youkai_gauge < self.gauge_bounds[0]:
            self.youkai_gauge = self.gauge_bounds[0]
        elif self.youkai_gauge > self.gauge_bounds[1]:
            self.youkai_gauge = self.gauge_bounds[1]

    def gauge_is_extremely_human(self) -> bool:
        """GaugeIsExtremelyHuman (GameManager.hpp:208-211)。"""
        return self.youkai_gauge <= self.gauge_bounds[2]

    def gauge_is_extremely_youkai(self) -> bool:
        """GaugeIsExtremelyYoukai (GameManager.hpp:215-218)。"""
        return self.youkai_gauge >= self.gauge_bounds[3]

    def gauge_is_moderately_youkai(self) -> bool:
        """GaugeIsModeratelyYoukai (GameManager.hpp:219-222)。"""
        return self.youkai_gauge >= self.gauge_bounds[5]

    # ---- 时刻符点 ----
    def add_time_orbs(self, amount: int) -> None:
        """GameManager::AddTimeOrbs (GameManager.cpp:200-218):
        负值不扣穿 0; 累计轨只增不减(C 的 totalTimeOrbs 只在正增量时加)。"""
        if amount >= 0 or self.current_time_orbs >= -amount:
            self.current_time_orbs += amount
            if amount > 0:
                self.total_time_orbs += amount
        else:
            self.current_time_orbs = 0

    # ---- 最高分跟随 ----
    def tick_high_score(self) -> None:
        """highScore 跟随显示分 (GameManager::OnUpdate 尾段, 同 th07 语义)。"""
        if self.high_score < self.gui_score:
            self.high_score = self.gui_score
            self.high_score_num_continues = self.num_retries

    # ---- 得分弹字 / 横幅 ----
    def add_popup(self, pos: Vec2, value: int, color: int, kind: int = 1) -> None:
        """登记一个得分弹字(AsciiManager::CreateScorePopup 等价物)。"""
        cap = POPUP1_CAP if kind == 1 else POPUP2_CAP
        same = [p for p in self.popups if p.kind == kind]
        while len(same) >= cap:
            self.popups.remove(same.pop(0))
        self.popups.append(ScorePopup(pos=pos, value=value, color=color, kind=kind))

    def show_spellcard_bonus(self, score: int) -> None:
        """"Spell Card Bonus!" 横幅(Gui::ShowSpellcardBonus 等价物)。"""
        self.spellcard_bonus = score
        self.spellcard_bonus_timer = 0

    def show_bonus_score(self, score: int) -> None:
        """"BONUS %8d" 清场奖励横幅(Gui::ShowBonusScore 等价物)。"""
        self.bonus_score = score
        self.bonus_score_timer = 0

    def step_popups(self) -> None:
        """每帧: 弹字上浮 0.5px + 寿命 60 帧; 横幅计时(250/280 帧消, 同 th07)。"""
        keep: list[ScorePopup] = []
        for p in self.popups:
            p.pos = Vec2(p.pos.x, p.pos.y - 0.5)
            p.timer += 1
            if p.timer <= POPUP_LIFETIME:
                keep.append(p)
        self.popups = keep
        if self.bonus_score != 0:
            self.bonus_score_timer += 1
            if self.bonus_score_timer >= 250:
                self.bonus_score = 0
        if self.spellcard_bonus != 0:
            self.spellcard_bonus_timer += 1
            if self.spellcard_bonus_timer >= 280:
                self.spellcard_bonus = 0
