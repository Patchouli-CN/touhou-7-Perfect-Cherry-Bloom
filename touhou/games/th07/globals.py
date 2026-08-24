""" 全局状态(樱点/动态难度) —— 移植自 GameManager.cpp / 规格 §0.2 §0.3。

ZunGlobals 继承引擎层通用计分基座(engine/globals_base.py 的 GlobalsBase:
score/gui_score 追赶、残机/炸弹/火力/死亡/重试), 本模块只留 th07 专属:
樱点四元组(cherry/cherryMax/cherryPlus/cherryStart)与 subrank/rank 动态难度、
擦弹/符卡/点道具计数, 以及 AsciiManager 的得分弹字 (CreatePopup1/2) 与
Gui 状态横幅 (ShowStatusPopup) 的纯逻辑部分(计时/生命周期; 渲染在 view)。
"""

from __future__ import annotations

import msgspec

from ...engine.globals_base import (  # noqa: F401 (SCORE_MAX/GUI_SCORE_INCREMENT_MAX 为兼容再导出)
    GUI_SCORE_INCREMENT_MAX, SCORE_MAX, GlobalsBase)
from ...utils import Vec2

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

# 状态横幅种类 (Gui.hpp GUI_DISPLAY_*; 0=隐藏)
STATUS_FULL_POWER = 1    # "Full Power Mode!"
STATUS_BORDER = 2        # "Supernatural Border!!"
STATUS_CHERRY_MAX = 3    # "CherryPoint Max!"
STATUS_BORDER_BONUS = 4  # "Border Bonus %7d"

# 弹字槽容量 (AsciiManager: popups[720] + popups[720..723], CreatePopup1/2 环形覆盖)
POPUP1_CAP = 720
POPUP2_CAP = 3

# 弹字寿命 (AsciiManager.cpp:56-60: timer > 60 消)
POPUP_LIFETIME = 60
# 状态横幅显示帧数 (Gui.cpp:1343)
STATUS_POPUP_LIFETIME = 180


class ScorePopup(msgspec.Struct):
    """一个得分弹字 (AsciiManagerPopup): 收点/清弹得分在道具位置跳出的数字。

    ``value`` 为代码值口径(与 C++ 同, POC 线上收点弹 50000); -1 = PowerUp
    特殊字形 (C++ CreatePopup1 value<0 → digits[0]='\\n')。
    ``kind``: 1=普通弹字 (CreatePopup1, 720 环), 2=弹消点弹字 (CreatePopup2, 3 环)。
    """

    pos: Vec2 = msgspec.field(default_factory=Vec2.zero)
    value: int = 0
    color: int = 0xFFFFFFFF  # ARGB
    timer: int = 0
    kind: int = 1


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

    # ---- 最高分跟随(GameManager globals.highScore) ----
    high_score: int = 0                # HUD HISCORE; 开局从 score.dat 载入
    high_score_num_continues: int = 0  # 破纪录当时的续关数

    # ---- 计数(th07 专属) ----
    graze_in_stage: int = 0
    graze_in_total: int = 0
    spell_cards_captured: int = 0
    point_items_collected_this_stage: int = 0
    point_items_collected_for_extend: int = 0
    extends_from_point_items: int = 0
    next_needed_point_items_for_extend: int = 50

    # ---- 得分弹字 / 状态横幅(AsciiManager/Gui 的纯逻辑部分; 渲染在 view) ----
    popups: list[ScorePopup] = msgspec.field(default_factory=list)
    status_popup: int = 0        # GUI_DISPLAY_*; 0=隐藏
    status_popup_arg: int = 0    # fmtArg (Border Bonus 的数值等)
    status_popup_timer: int = 0

    # ---- 最高分跟随 ----
    def tick_high_score(self) -> None:
        """highScore 跟随显示分 (GameManager::OnUpdate 尾段,
        GameManager.cpp:265-268): guiScore 超过 highScore 即同步,
        并记录当时的续关数。"""
        if self.high_score < self.gui_score:
            self.high_score = self.gui_score
            self.high_score_num_continues = self.num_retries

    def initialize_rank(self, difficulty: int) -> None:
        """按难度初始化 rank (GameManager::InitializeRank / g_RankArray)。"""
        self.rank, self.min_rank, self.max_rank = RANK_TABLE[difficulty]
        self.subrank = 0

    # ---- 得分弹字 (AsciiManager::CreatePopup1/2 + OnUpdate) ----
    def add_popup(self, pos: Vec2, value: int, color: int, kind: int = 1) -> None:
        """登记一个得分弹字(位置为游戏区坐标; view 渲染时加窗口偏移)。

        C++ 是定长环形缓冲区(720/3 槽, 写满回卷覆盖最旧); 这里等价为
        超出容量时丢最旧的同槽弹字。"""
        cap = POPUP1_CAP if kind == 1 else POPUP2_CAP
        same = [p for p in self.popups if p.kind == kind]
        while len(same) >= cap:
            self.popups.remove(same.pop(0))
        self.popups.append(ScorePopup(pos=pos, value=value, color=color, kind=kind))

    def show_status_popup(self, arg: int, kind: int) -> None:
        """Gui::ShowStatusPopup (Gui.cpp:88-95): 状态横幅出现并重置计时。"""
        self.status_popup = kind
        self.status_popup_arg = arg
        self.status_popup_timer = 0

    def step_popups(self) -> None:
        """每帧: 弹字上浮 0.5px + 寿命 60 帧 (AsciiManager.cpp:55-60);
        状态横幅计时, 180 帧隐藏 (Gui.cpp:1329-1347)。"""
        keep: list[ScorePopup] = []
        for p in self.popups:
            p.pos = Vec2(p.pos.x, p.pos.y - 0.5)
            p.timer += 1
            if p.timer <= POPUP_LIFETIME:
                keep.append(p)
        self.popups = keep
        if self.status_popup != 0:
            self.status_popup_timer += 1
            if self.status_popup_timer >= STATUS_POPUP_LIFETIME:
                self.status_popup = 0

    # ---- 樱点 (§0.3) ----
    def add_cherry(self, x: int) -> None:
        """cherry += x, 封顶 cherryMax (GameManager::AddCherry)。

        触达上限且本次有变化 → "CherryPoint Max!" 横幅
        (GameManager.cpp:949-952, ShowStatusPopup(…, 3))。"""
        old = self.cherry
        self.cherry = min(self.cherry + x, self.cherry_max)
        if self.cherry >= self.cherry_max and old != self.cherry:
            self.show_status_popup(self.cherry - self.cherry_start, STATUS_CHERRY_MAX)

    def add_cherry_plus(self, x: int) -> bool:
        """GameManager::AddCherryPlus: cherry 与 cherryPlus 同时累加。

        cherryPlus 封顶 cherryStart+50000; 返回本次是否触达该上限(=应开结界,
        结界本身由玩家侧处理)。cherry 触达上限 → "CherryPoint Max!" 横幅
        (GameManager.cpp:934-937)。
        """
        old = self.cherry
        self.cherry = min(self.cherry + x, self.cherry_max)
        if self.cherry >= self.cherry_max and old != self.cherry:
            self.show_status_popup(self.cherry - self.cherry_start, STATUS_CHERRY_MAX)
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
