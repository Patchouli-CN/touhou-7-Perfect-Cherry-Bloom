"""th08 Practice/Spell Practice 菜单的纯逻辑 —— 面选/符卡选/显示行生成, 无 pygame。

对照 th08-ref(@1861f88, 行号相对其 src/) TitleScreen.cpp:
- OnUpdatePracticeStageSelect(:1857-1968): 8 行(1面..6B), 光标只停已通面
  (clearInfo 位, progress.practice_clear_info), BACK 回机体选择;
- OnUpdateSpellStageSelect(:1971-2192): 10 行(8 面 + Extra + Last Word),
  LEFT/RIGHT 切机体(跳过该组 Spell Practice 未解锁的, :2102-2118),
  BACK 回主菜单(光标 2); 行文本 = DrawSpellStageSelect(:2790-2870) 的
  "标记+面名 + 收取(本机/合计)/挑战可能/总数";
- OnUpdateSpellCardSelect(:2195-2545): 每页 15 张, LEFT/RIGHT 翻页,
  确认守卫(:2453-2458)两组 attempts[SHOT_ALL] 任一非零或 LW 已解锁,
  BACK 回面选。

三个画面进场都有 8 帧输入门(Init 态 stateTimer2==8 才 Ready,
:1887/:2086/:2333)。handle 返回 None 或 {"action", "se"} dict(se 由调用方
播菜单音效; "invalid" = SOUND_INVALID_ACTION)。
"""

from __future__ import annotations

import msgspec

from typing import TYPE_CHECKING

from ...th07.view.screens import MenuAction
from ..progress import (
    SHOT_ALL_ROW,
    card_attempted_any,
    is_last_word_spellcard_attempted,
    spell_practice_captured,
)
from ..spellcards import (
    LAST_SPELL_CARDS,
    LAST_WORD_COMMENTS,
    SPELLCARD_LAST_WORD_START,
    SPELL_STAGE_COUNT,
    STAGE_SPELLCARD_CARDS,
    spellcard_difficulty,
)

if TYPE_CHECKING:
    from ....engine.score_store import ScoreStore

# 进场输入门帧数(stateTimer2==8 → Ready)
INPUT_GATE_FRAMES = 8

PRACTICE_STAGE_ROWS = 8  # PracticeStageSelect 行数(MoveCursorVertical(8), :1919)
SPELLCARDS_PER_PAGE = 15  # TITLE_SPELL_CARD_SPELLCARDS_PER_PAGE

# 面选 10 行名(g_StageNamesSpellPractice, :175-187; 尾部空格原作固有)
SPELL_STAGE_NAMES = (
    "Stage1   ",
    "Stage2   ",
    "Stage3   ",
    "Stage4A  ",
    "Stage4B  ",
    "Stage5   ",
    "Stage6A  ",
    "Stage6B  ",
    "Extra    ",
    "Last Word",
)

# 卡名显示占位(TITLE_SPELLCARD_NOT_UNLOCKED, config/i18n.csv:172)
SPELLCARD_NAME_HIDDEN = "？？？？？？？？"
# Last Word 已解锁未遭遇的显示(TITLE_SPELLCARD_AVAILABLE, i18n.csv:173)
SPELLCARD_NAME_AVAILABLE = "挑戦者歓迎中！"
# 卡选表头(TITLE_SPELL_CARD_INFO, i18n.csv:174)
SPELL_CARD_TABLE_HEADER = "取得数/挑戦数（()内は本編での集計）"
# 面选表头(TITLE_SPELL_STAGE_INFO, i18n.csv:160)
SPELL_STAGE_TABLE_HEADER = "ステージ    取得/挑戦可能/総数（()内は全キャラ合計）"

# 难度名(g_TitleSpellDifficultyNames, TitleSpellCardData.inl; 下标 =
# GetDifficultyFromSpellCard 0..5)
SPELL_DIFFICULTY_NAMES = (
    "初月（イージー）",
    "三日月（ノーマル）",
    "上つ弓張（ハード）",
    "待宵（ルナティック）",
    "望月（エクストラ）",
    "鬼意（ラストワード）",
)


def spell_card_display_name(store: ScoreStore, card_no: int) -> str:
    """卡选行显示名(:2254-2270 的三分支): 已遭遇(两组 attempts[SHOT_ALL]
    任一非零) → 真名; 非 Last Word 或未解锁 → 隐藏占位; LW 已解锁未遭遇 →
    挑戦者歓迎中。"""
    if card_attempted_any(store, card_no, SHOT_ALL_ROW):
        name = store.catk[card_no]["name"]
        return name if name else SPELLCARD_NAME_HIDDEN
    if spellcard_difficulty(card_no) <= 4 or not is_last_word_spellcard_attempted(
        store, card_no
    ):
        return SPELLCARD_NAME_HIDDEN
    return SPELLCARD_NAME_AVAILABLE


def spell_card_selectable(store: ScoreStore, card_no: int) -> bool:
    """卡选确认守卫(:2453-2458): 已遭遇, 或 Last Word 已解锁。"""
    return card_attempted_any(store, card_no, SHOT_ALL_ROW) or (
        card_no >= SPELLCARD_LAST_WORD_START
        and is_last_word_spellcard_attempted(store, card_no)
    )


def spell_stage_line(store: ScoreStore, row: int, shot_type: int) -> str:
    """面选行文本(:2850-2872): 标记(@ = 本机 practice 全收, * = 合计
    practice 全收) + 面名 + 收取(本机/合计)/挑战可能/总数。"""
    cards = STAGE_SPELLCARD_CARDS[row]
    cap_shot = sum(1 for c in cards if spell_practice_captured(store, c, shot_type))
    cap_all = sum(1 for c in cards if spell_practice_captured(store, c, SHOT_ALL_ROW))
    attempted = sum(
        1
        for c in cards
        if card_attempted_any(store, c, SHOT_ALL_ROW)
        or (row == SPELL_STAGE_COUNT - 1 and is_last_word_spellcard_attempted(store, c))
    )
    if cap_shot >= len(cards):
        marker = "@"
    elif cap_all >= len(cards):
        marker = "*"
    else:
        marker = " "
    return "%s%s %3d(%3d)/%3d/%3d" % (
        marker,
        SPELL_STAGE_NAMES[row],
        cap_shot,
        cap_all,
        attempted,
        len(cards),
    )


def spell_card_info_lines(store: ScoreStore, card_no: int, shot_type: int) -> tuple:
    """卡选信息区文本(FormatSpellCardInfo, TitleFormatSpellCardInfo.inl;
    简化为 4 行: 卡号+名 / 难度+Last / 练习(本篇)战绩 / 挑战条件或评论)。
    原作评论两行存 catk.spellCommentLine1/2, score.json 无评论字段,
    已捕获卡评论行留空(偏离注明)。卡号越界(非 th08 形状 store)给空行。"""
    if not 0 <= int(card_no) < len(store.catk):
        return ("", "", "", "", "")
    name = spell_card_display_name(store, card_no)
    difficulty = spellcard_difficulty(card_no)
    entry = store.catk[card_no]
    practice = entry.get("practice")
    if not isinstance(practice, dict):
        practice = {"attempts": [0] * 13, "successes": [0] * 13, "highscore": [0] * 13}
    line0 = "No.%03d  %s" % (card_no + 1, name)
    line1 = "%s %s" % (
        SPELL_DIFFICULTY_NAMES[min(difficulty, 5)],
        "Last" if card_no in _LAST_SPELL_SET else " ",
    )
    if card_attempted_any(store, card_no, SHOT_ALL_ROW):
        line2 = "  %3d/%3d(%3d/%3d)[%.8d]" % (
            practice["successes"][shot_type],
            practice["attempts"][shot_type],
            entry["successes"][shot_type],
            entry["attempts"][shot_type],
            practice["highscore"][shot_type],
        )
    else:
        line2 = "  ---/---(---/---)[--------]"
    # 挑战条件: 未遭遇且未解锁的 Last Word(下标 = 卡号-204,
    # TitleFormatSpellCardInfo.inl:104-108); 其余给评论行(无存储, 空)
    if (
        not card_attempted_any(store, card_no, SHOT_ALL_ROW)
        and card_no >= SPELLCARD_LAST_WORD_START - 1
        and card_no < SPELLCARD_LAST_WORD_START + len(LAST_WORD_COMMENTS) - 1
        and not is_last_word_spellcard_attempted(store, card_no)
    ):
        comment = LAST_WORD_COMMENTS[card_no - (SPELLCARD_LAST_WORD_START - 1)]
    else:
        comment = ("", "")
    return (line0, line1, line2, *comment)


_LAST_SPELL_SET = frozenset(LAST_SPELL_CARDS)


class PracticeStageFlowTh08(msgspec.Struct):
    """Practice 面选的选择状态(OnUpdatePracticeStageSelect 的对应物)。

    clear_info = progress.practice_clear_info 的位掩码(bit i = c_stage i
    可练); 光标只停置位行(:1915-1939 的 UP/DOWN while 循环)。"""

    clear_info: int = 1
    cursor: int = 0
    frames: int = 0

    def tick_frame(self) -> None:
        """每帧推进进场计时(:1965-1967 stateTimer2++)。"""
        self.frames += 1

    def _selectable(self, row: int) -> bool:
        return bool(self.clear_info >> row & 1)

    def clamp_cursor(self) -> None:
        """光标停未解锁行 → 回 0(:1914-1917)。"""
        if not self._selectable(self.cursor):
            self.cursor = 0

    def handle(self, action: MenuAction) -> dict | None:
        """处理一次菜单按键(8 帧进场门内不受理)。"""
        if self.frames < INPUT_GATE_FRAMES:
            return None
        if action in (MenuAction.UP, MenuAction.DOWN):
            delta = 1 if action == MenuAction.DOWN else -1
            self.cursor = (self.cursor + delta) % PRACTICE_STAGE_ROWS
            for _ in range(PRACTICE_STAGE_ROWS):
                if self._selectable(self.cursor):
                    break
                self.cursor = (self.cursor + delta) % PRACTICE_STAGE_ROWS
            return {"action": "move", "se": "select"}
        if action == MenuAction.CONFIRM:
            # currentStage = cursor → GameManager(:1941-1950)
            return {"action": "start", "row": self.cursor, "se": "ok"}
        if action == MenuAction.BACK:
            # 回 CharacterSelectPractice(:1952-1959)
            return {"action": "quit", "se": "cancel"}
        return None


class SpellStageFlowTh08(msgspec.Struct):
    """Spell Practice 面选的选择状态(OnUpdateSpellStageSelect 的对应物)。

    cursor = 行(0..9, 8=Extra 9=Last Word); character = 绝对机体下标;
    char_unlocked = 可选机体(is_spell_practice_unlocked_for_character 快照,
    长 4 或 12 = menuLength 规则)的平行表 —— LEFT/RIGHT 在此表内回绕并
    跳过锁定机体(:2102-2118)。"""

    char_unlocked: list[bool] = msgspec.field(default_factory=list)
    character: int = 0
    cursor: int = 0
    frames: int = 0

    def tick_frame(self) -> None:
        """每帧推进进场计时(:2189-2191 stateTimer2++)。"""
        self.frames += 1

    def clamp_character(self) -> None:
        """进画面时机体钳到已解锁(:1999-2008 的 while; 调用方喂表后调)。"""
        n = len(self.char_unlocked)
        if n == 0:
            self.character = 0
            return
        self.character %= n
        for _ in range(n):
            if self.char_unlocked[self.character]:
                return
            self.character = (self.character + 1) % n

    def handle(self, action: MenuAction) -> dict | None:
        """处理一次菜单按键(8 帧进场门内不受理)。"""
        if self.frames < INPUT_GATE_FRAMES:
            return None
        n = len(self.char_unlocked)
        if action in (MenuAction.LEFT, MenuAction.RIGHT) and n:
            delta = 1 if action == MenuAction.RIGHT else -1
            self.character = (self.character + delta) % n
            for _ in range(n):
                if self.char_unlocked[self.character]:
                    break
                self.character = (self.character + delta) % n
            return {"action": "character", "se": "select"}
        if action == MenuAction.UP:
            self.cursor = (self.cursor - 1) % SPELL_STAGE_COUNT
            return {"action": "move", "se": "select"}
        if action == MenuAction.DOWN:
            self.cursor = (self.cursor + 1) % SPELL_STAGE_COUNT
            return {"action": "move", "se": "select"}
        if action == MenuAction.CONFIRM:
            # currentStage = cursor → SpellCardSelect(:2130-2145)
            return {"action": "select", "row": self.cursor, "se": "ok"}
        if action == MenuAction.BACK:
            # 回主菜单光标 2(:2150-2166)
            return {"action": "quit", "se": "cancel"}
        return None


class SpellCardFlowTh08(msgspec.Struct):
    """Spell Practice 卡选的选择状态(OnUpdateSpellCardSelect 的对应物)。

    cards = 本面卡号序列(STAGE_SPELLCARD_CARDS[row]); names/selectable =
    调用方按 store 快照解析的显示名(spell_card_display_name)与确认守卫
    (spell_card_selectable)平行表 —— flow 不持 store(原作卡名进场烘焙,
    战斗后的记账要回标题重建画面才反映, 口径一致)。"""

    cards: tuple = ()
    names: list = msgspec.field(default_factory=list)
    selectable: list = msgspec.field(default_factory=list)
    cursor: int = 0
    frames: int = 0

    @property
    def page(self) -> int:
        """当前页号(15/页, :2376)。"""
        return self.cursor // SPELLCARDS_PER_PAGE if self.cards else 0

    def tick_frame(self) -> None:
        """每帧推进进场计时(:2542-2544 stateTimer2++)。"""
        self.frames += 1

    def handle(self, action: MenuAction) -> dict | None:
        """处理一次菜单按键(8 帧进场门内不受理)。"""
        if self.frames < INPUT_GATE_FRAMES:
            return None
        n = len(self.cards)
        if n == 0:
            if action == MenuAction.BACK:
                return {"action": "quit", "se": "cancel"}
            return None
        if action in (MenuAction.UP, MenuAction.DOWN):
            delta = 1 if action == MenuAction.DOWN else -1
            self.cursor = (self.cursor + delta) % n
            return {"action": "move", "se": "select"}
        if n > SPELLCARDS_PER_PAGE and action in (MenuAction.LEFT, MenuAction.RIGHT):
            if action == MenuAction.LEFT:
                # 整页 -15 回绕(:2310-2322)
                self.cursor -= SPELLCARDS_PER_PAGE
                if self.cursor < 0:
                    self.cursor = n - 1
                if self.cursor >= n:
                    self.cursor = 0
            else:
                # 尾页回卷 cursor %= 15, 否则 +15(:2324-2342)
                if n - self.cursor <= n % SPELLCARDS_PER_PAGE:
                    self.cursor %= SPELLCARDS_PER_PAGE
                else:
                    self.cursor = min(self.cursor + SPELLCARDS_PER_PAGE, n - 1)
            return {"action": "move", "se": "select"}
        if action == MenuAction.CONFIRM:
            if self.selectable[self.cursor]:
                return {
                    "action": "start",
                    "card": self.cards[self.cursor],
                    "se": "ok",
                }
            # 未遭遇/未解锁: SOUND_INVALID_ACTION(:2512-2514)
            return {"action": "invalid", "se": "invalid"}
        if action == MenuAction.BACK:
            # 回面选, 光标记忆由调用方存(:2520-2535)
            return {"action": "quit", "se": "cancel"}
        return None


__all__ = [
    "INPUT_GATE_FRAMES",
    "PRACTICE_STAGE_ROWS",
    "SPELLCARDS_PER_PAGE",
    "SPELL_CARD_TABLE_HEADER",
    "SPELL_DIFFICULTY_NAMES",
    "SPELL_STAGE_NAMES",
    "SPELL_STAGE_TABLE_HEADER",
    "SPELLCARD_NAME_AVAILABLE",
    "SPELLCARD_NAME_HIDDEN",
    "PracticeStageFlowTh08",
    "SpellCardFlowTh08",
    "SpellStageFlowTh08",
    "spell_card_display_name",
    "spell_card_info_lines",
    "spell_card_selectable",
    "spell_stage_line",
]
