"""th08 Result 浏览面(BROWSE 模式)的纯逻辑状态机 —— 类别/难度/机体选择与输入门, 无 pygame。

对照 th08-ref(@1861f88, 行号相对其 src/) ResultScreen.cpp:
HandleCategorySelectScreen(:544-691) / HandleHighScoreDifficultySelect(:694-861)
/ HandleHighScoreCharacterSelect(:863-959) / HandleHighScoreScreen(:961-1001)
/ HandleSpellCardDifficultySelect(:1003-1092) / HandleSpellCardCharacterSelect
(:1094-1192) / HandleSpellCardScreen(:1195-1291) / HandleOtherStatsScreen
(:1988-2151)。入榜名字输入/replay 保存流(GAME_RESULT 模式)不在本模块;
榜单/符卡/统计的取数与格式化在同包 result_data.py。

按键语义: 移动 = MoveCursor/MoveCursorHorizontally/MoveShotTypeCursor
(:3095-3177, 回绕 + SOUND_MOVE_MENU); 确认 = SOUND_SELECT, 返回 = SOUND_BACK。
handle 返回 None 或 {"action": ..., "se": ...}: action ∈
move/enter/back/quit(quit = 回标题), se 由调用方播菜单音效。
"""

from __future__ import annotations

from enum import IntEnum
from typing import TYPE_CHECKING

import msgspec

from ...th07.view.screens import MenuAction
from ..progress import SHOT_ALL_ROW
from ..spellcards import SPELLCARDS_PER_DIFFICULTY
from .result_data import (
    NUM_CHARACTERS_SELECT,
    NUM_SHOT_TYPES,
    SPELLCARD_PAGE_ROWS,
)

if TYPE_CHECKING:
    from ....engine.score_store import ScoreStore

# 类别选择 4 项(HandleCategorySelectScreen; 标签是 result00.anm 贴图 sprite 21-24
# 的实测文字, 只用于无资源环境的文字回退)
CATEGORY_ITEMS = (
    "最高記録一覧",
    "スペルカード一覧",
    "その他の状態一覧",
    "タイトルに戻る",
)
CATEGORY_EXIT = 3  # タイトルに戻る

# 难度名单: 高分榜 5 项(MAX_DIFFICULTIES, :730), 符卡 6 项(+全ての難易度, :1037)
HIGHSCORE_DIFFICULTY_ITEMS = ("Easy", "Normal", "Hard", "Lunatic", "Extra")
SPELLCARD_DIFFICULTY_ITEMS = HIGHSCORE_DIFFICULTY_ITEMS + ("全ての難易度",)
SPELLCARD_DIFFICULTY_ALL = 5  # 全难度页下标(selectedSpellcardDifficulty 初值, :3025)

# 输入门(进场多少帧后才受理): 类别 20(:576-579), 各选择/榜单 6
# (:723/:892/:970/:1030/:1121/:1244), 统计 40(OTHER_STATS_SCREEN_INIT 淡入段,
# :2094)
_INPUT_GATE_CATEGORY = 20
_INPUT_GATE_SELECT = 6
_INPUT_GATE_STATS = 40


class ResultBrowseState(IntEnum):
    """浏览面状态(BROWSE 模式的 ResultScreenState 子集, :14-46)。"""

    CATEGORY = 0  # CHOOSING_CATEGORY
    HIGHSCORE_DIFFICULTY = 1  # BEST_SCORES_CHOOSING_DIFFICULTY
    HIGHSCORE_CHARACTER = 2  # BEST_SCORES_CHOOSING_CHARACTER
    HIGHSCORE = 3  # BEST_SCORES(榜单)
    SPELLCARD_DIFFICULTY = 4  # SPELLCARDS_CHOOSING_DIFFICULTY
    SPELLCARD_CHARACTER = 5  # SPELLCARDS_CHOOSING_CHARACTER
    SPELLCARD = 6  # SPELLCARDS(战绩页)
    STATS = 7  # OTHER_STATS_SCREEN


class ResultFlowTh08(msgspec.Struct):
    """Result 浏览面状态机(BROWSE 模式; 入榜/replay 保存不在内)。

    store = 进画面时的存档快照(当次会话内不刷新, 同原作 AddedCallback
    一次开档口径); 选择记忆(selected_*)跨子画面保留(:750/:917/:1057/
    :1148/:1174 的写回)。frames = 各状态的进场计时(输入门用; 榜单上
    切机体/翻页也清零, :979/:1250/:1258)。
    """

    store: "ScoreStore"
    state: ResultBrowseState = ResultBrowseState.CATEGORY
    cursor: int = 0
    frames: int = 0
    selected_difficulty: int = 1  # selectedDifficulty 初值(:3054)
    selected_character: int = 0  # selectedHighScoreCharacter 初值(:3055)
    selected_spellcard_difficulty: int = SPELLCARD_DIFFICULTY_ALL  # :3025
    shot_type: int = SHOT_ALL_ROW  # shotTypeCursor 初值 = SHOT_ALL(:3026)
    page: int = 0  # 符卡页(SPELLCARDS 态的 cursor, :1180/:1249)

    # ---- 输入门 ----
    def _gate(self) -> int:
        if self.state == ResultBrowseState.CATEGORY:
            return _INPUT_GATE_CATEGORY
        if self.state == ResultBrowseState.STATS:
            return _INPUT_GATE_STATS
        return _INPUT_GATE_SELECT

    @property
    def input_enabled(self) -> bool:
        """进场门过后才受理输入(各 Handle* 的 statePhaseTimer/frameTimer 段)。"""
        return self.frames >= self._gate()

    def tick_frame(self) -> None:
        """每帧推进进场计时(OnUpdate :2431 的 frameTimer++)。"""
        self.frames += 1

    def _enter(self, state: ResultBrowseState, cursor: int) -> None:
        self.state = state
        self.cursor = cursor
        self.frames = 0

    # ---- 光标/候选数 ----
    def _item_count(self) -> int:
        s = self.state
        if s == ResultBrowseState.CATEGORY:
            return len(CATEGORY_ITEMS)
        if s == ResultBrowseState.HIGHSCORE_DIFFICULTY:
            return len(HIGHSCORE_DIFFICULTY_ITEMS)
        if s == ResultBrowseState.HIGHSCORE_CHARACTER:
            return NUM_CHARACTERS_SELECT
        if s == ResultBrowseState.SPELLCARD_DIFFICULTY:
            return len(SPELLCARD_DIFFICULTY_ITEMS)
        return NUM_SHOT_TYPES  # SPELLCARD_CHARACTER

    def _move(self, delta: int) -> dict:
        """MoveCursor 纵列回绕(:3095-3125) + SOUND_MOVE_MENU。"""
        n = self._item_count()
        self.cursor = (self.cursor + delta) % n
        return {"action": "move", "se": "select"}

    # ---- 主入口 ----
    def handle(self, action: MenuAction) -> dict | None:
        """处理一次菜单按键; 输入门内(state 进场初期)一律不受理。"""
        if not self.input_enabled:
            return None
        s = self.state
        if s == ResultBrowseState.CATEGORY:
            return self._handle_category(action)
        if s in (
            ResultBrowseState.HIGHSCORE_DIFFICULTY,
            ResultBrowseState.SPELLCARD_DIFFICULTY,
        ):
            return self._handle_difficulty(action)
        if s in (
            ResultBrowseState.HIGHSCORE_CHARACTER,
            ResultBrowseState.SPELLCARD_CHARACTER,
        ):
            return self._handle_character(action)
        if s == ResultBrowseState.HIGHSCORE:
            return self._handle_highscore(action)
        if s == ResultBrowseState.SPELLCARD:
            return self._handle_spellcard(action)
        # STATS: SELECTMENU|RETURNMENU 都退回类别选择(:2124-2128)
        if action in (MenuAction.CONFIRM, MenuAction.BACK):
            self._enter(ResultBrowseState.CATEGORY, self.cursor)
            return {"action": "back", "se": "cancel"}
        return None

    # ---- 类别选择(:544-691) ----
    def _handle_category(self, action: MenuAction) -> dict | None:
        if action in (MenuAction.UP, MenuAction.DOWN):
            return self._move(1 if action == MenuAction.DOWN else -1)
        if action == MenuAction.BACK:
            # 不在退出项: 光标跳"タイトルに戻る"(:602-623); 在退出项: 退出
            if self.cursor == CATEGORY_EXIT:
                return {"action": "quit", "se": "cancel"}
            self.cursor = CATEGORY_EXIT
            return {"action": "move", "se": "cancel"}
        if action == MenuAction.CONFIRM:
            if self.cursor == 0:
                self._enter(
                    ResultBrowseState.HIGHSCORE_DIFFICULTY, self.selected_difficulty
                )
            elif self.cursor == 1:
                self._enter(
                    ResultBrowseState.SPELLCARD_DIFFICULTY,
                    self.selected_spellcard_difficulty,
                )
            elif self.cursor == 2:
                self._enter(ResultBrowseState.STATS, self.cursor)
            else:
                return {"action": "quit", "se": "cancel"}
            return {"action": "enter", "se": "ok"}
        return None

    # ---- 难度选择(高分榜 :694-861 / 符卡 :1003-1092) ----
    def _handle_difficulty(self, action: MenuAction) -> dict | None:
        if action in (MenuAction.UP, MenuAction.DOWN):
            return self._move(1 if action == MenuAction.DOWN else -1)
        hs = self.state == ResultBrowseState.HIGHSCORE_DIFFICULTY
        if action == MenuAction.BACK:
            # 记忆所选难度, 回类别选择并落回对应类别行(:750-751/:1057-1058)
            if hs:
                self.selected_difficulty = self.cursor
            else:
                self.selected_spellcard_difficulty = self.cursor
            self._enter(ResultBrowseState.CATEGORY, 0 if hs else 1)
            return {"action": "back", "se": "cancel"}
        if action == MenuAction.CONFIRM:
            if hs:
                self.selected_difficulty = self.cursor
                self._enter(
                    ResultBrowseState.HIGHSCORE_CHARACTER, self.selected_character
                )
            else:
                self.selected_spellcard_difficulty = self.cursor
                self._enter(ResultBrowseState.SPELLCARD_CHARACTER, self.shot_type)
            return {"action": "enter", "se": "ok"}
        return None

    # ---- 机体选择(高分榜 :863-959 / 符卡 :1094-1192) ----
    def _handle_character(self, action: MenuAction) -> dict | None:
        if action in (MenuAction.UP, MenuAction.DOWN):
            return self._move(1 if action == MenuAction.DOWN else -1)
        hs = self.state == ResultBrowseState.HIGHSCORE_CHARACTER
        if action == MenuAction.BACK:
            if hs:
                self.selected_character = self.cursor
                self._enter(
                    ResultBrowseState.HIGHSCORE_DIFFICULTY, self.selected_difficulty
                )
            else:
                self.shot_type = self.cursor
                self._enter(
                    ResultBrowseState.SPELLCARD_DIFFICULTY,
                    self.selected_spellcard_difficulty,
                )
            return {"action": "back", "se": "cancel"}
        if action == MenuAction.CONFIRM:
            if hs:
                self.selected_character = self.cursor
                self._enter(ResultBrowseState.HIGHSCORE, self.cursor)
            else:
                self.shot_type = self.cursor
                self.page = 0  # :1180-1181 进战绩页光标(=页号)归零
                self._enter(ResultBrowseState.SPELLCARD, 0)
            return {"action": "enter", "se": "ok"}
        return None

    # ---- 高分榜榜单(:961-1001): 左右切机体 ----
    def _handle_highscore(self, action: MenuAction) -> dict | None:
        if action in (MenuAction.LEFT, MenuAction.RIGHT):
            # MoveCursorHorizontally(SHOT_ALL)(:977; :979 切机体也重置 frameTimer)
            d = 1 if action == MenuAction.RIGHT else -1
            self.cursor = (self.cursor + d) % NUM_CHARACTERS_SELECT
            self.selected_character = self.cursor
            self.frames = 0
            return {"action": "move", "se": "select"}
        if action == MenuAction.BACK:
            self.selected_character = self.cursor
            self._enter(ResultBrowseState.HIGHSCORE_CHARACTER, self.cursor)
            return {"action": "back", "se": "cancel"}
        return None

    # ---- 符卡战绩页(:1195-1291): 左右翻页/上下切机体 ----
    def _page_count(self) -> int:
        count = len(SPELLCARDS_PER_DIFFICULTY[self.selected_spellcard_difficulty])
        return (count + SPELLCARD_PAGE_ROWS - 1) // SPELLCARD_PAGE_ROWS

    def _handle_spellcard(self, action: MenuAction) -> dict | None:
        if action in (MenuAction.LEFT, MenuAction.RIGHT):
            # MoveCursorHorizontally((count+9)/10)(:1249)
            d = 1 if action == MenuAction.RIGHT else -1
            self.page = (self.page + d) % self._page_count()
            self.frames = 0  # :1250-1252 翻页重置 frameTimer(重烘文本用)
            return {"action": "move", "se": "select"}
        if action in (MenuAction.UP, MenuAction.DOWN):
            # MoveShotTypeCursor(SHOT_ALL+1)(:1256; :1258 同翻页也重置 frameTimer)
            d = 1 if action == MenuAction.DOWN else -1
            self.shot_type = (self.shot_type + d) % NUM_SHOT_TYPES
            self.frames = 0
            return {"action": "move", "se": "select"}
        if action == MenuAction.BACK:
            self._enter(ResultBrowseState.SPELLCARD_CHARACTER, self.shot_type)
            return {"action": "back", "se": "cancel"}
        return None


__all__ = [
    "CATEGORY_ITEMS",
    "HIGHSCORE_DIFFICULTY_ITEMS",
    "ResultBrowseState",
    "ResultFlowTh08",
    "SPELLCARD_DIFFICULTY_ALL",
    "SPELLCARD_DIFFICULTY_ITEMS",
]
