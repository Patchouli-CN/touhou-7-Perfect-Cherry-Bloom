"""th08 Practice/Spell Practice 画面与开局接线的 mixin —— GameApp 的职责拆分。

从 impl.py 拆出(单文件红线; C 期第 5 片): Practice 面选/Spell 面选/卡选
三个画面的进出与逐帧分发 + 练习开局(_start_practice/_start_spell_practice)
+ 练习局存档落盘。方法全部挂在 self(= GameApp)上, 状态字段由 impl 构造。
对照出处与原作语义见 practice_flow.py / impl.py 的 docstring。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ....logger import logger as log
from ....engine.render import FrameInput
from ...th07.view.screens import Screen
from ..progress import (
    NUM_TEAMS,
    is_extra_unlocked_with_all_teams,
    is_spell_practice_unlocked_for_character,
    load_score_store,
    practice_clear_info,
    unlock_last_words,
)
from ..sound import SE
from ..spellcards import (
    LAST_WORD_STAGE_MAP,
    STAGE_SPELLCARD_CARDS,
    spellcard_difficulty,
)
from .practice_flow import (
    SPELL_STAGE_NAMES,
    PracticeStageFlowTh08,
    SpellCardFlowTh08,
    SpellStageFlowTh08,
    spell_card_display_name,
    spell_card_info_lines,
    spell_card_selectable,
    spell_stage_line,
)
from .title_flow import ITEM_SPELL_PRACTICE

if TYPE_CHECKING:
    from ....engine.score_store import ScoreStore


class PracticeScreensMixin:
    """Practice/Spell Practice 画面接线(挂在 GameApp 上的方法群)。"""

    # ---- Practice 面选(OnUpdatePracticeStageSelect, TitleScreen.cpp:1857-1968) ----
    def _enter_practice_stage_select(self) -> None:
        """进 Practice 面选(Init :1866-1887): clearInfo 按存档快照(保底 1 面 +
        6B 通开 4A/4B, progress.practice_clear_info), 光标钳制(:1914-1917)。
        标题内子画面往返, 不淡入(同 _leave_option 口径)。"""
        store = self._title_store
        if store is None:  # 防御(正常 _reload_title_unlocks 已读)
            store = load_score_store(self._score_path)
            self._title_store = store
        flow = PracticeStageFlowTh08(
            clear_info=practice_clear_info(
                store, self._char_flow.cursor.index, self._char_flow.difficulty
            )
        )
        flow.clamp_cursor()
        self._practice_flow = flow
        self._screen = Screen.PRACTICE_STAGE

    def _run_practice_stage_select(self, inp: FrameInput) -> None:
        """Practice 面选一帧: 渲染 → 菜单键(8 帧进场门, flow 内门控)。"""
        flow = self._practice_flow
        if flow is None:  # 防御(正常 _enter_practice_stage_select 已建)
            self._enter_main_menu()
            return
        if self._last_menu_screen != Screen.PRACTICE_STAGE:
            self._last_menu_screen = Screen.PRACTICE_STAGE
            self._menu_sub_frame = 0
        frame = self._menu_sub_frame
        self._menu_sub_frame += 1
        title = "Practice  {} / {}".format(
            self._diff.current or "", self._char_flow.cursor.current or ""
        )
        lines = [
            SPELL_STAGE_NAMES[i].strip()
            + ("" if flow.clear_info >> i & 1 else "  (locked)")
            for i in range(8)
        ]
        self._renderer.render_practice_stage_select(flow, title, lines, frame)
        for act in inp.menu_actions:
            r = flow.handle(act)
            if not r:
                continue
            se = r.get("se")
            if se is not None:
                self._renderer.play_menu_se(se)
            if r["action"] == "quit":
                # BACK 回机体选择(光标 = shotType, :1952-1959)
                self._screen = Screen.CHARACTER
                return
            if r["action"] == "start":
                self._start_practice(r["row"])
                return
        flow.tick_frame()

    # ---- Spell Practice 面选/卡选(TitleScreen.cpp:1971-2545) ----
    def _spell_char_unlocked(self, store: "ScoreStore") -> list[bool]:
        """面选可选机体的解锁表(menuLength = IsExtraUnlockedWithAllTeams
        ? 12 : 4, :1997/:2098; 逐机体 IsSpellPracticeUnlockedForCharacter)。"""
        n = len(self._characters)
        if not is_extra_unlocked_with_all_teams(store):
            n = min(n, NUM_TEAMS)
        return [is_spell_practice_unlocked_for_character(store, c) for c in range(n)]

    def _enter_spell_stage_select(self, row: int = 0) -> None:
        """进 Spell 面选(Init :1986-2088): 机体初值 = 上次选择(:1997
        cursor=shotType)钳到已解锁, 行光标按 row 恢复(卡选 BACK/对局回来)。"""
        store = self._title_store
        if store is None:  # 防御
            store = load_score_store(self._score_path)
            self._title_store = store
        flow = SpellStageFlowTh08(char_unlocked=self._spell_char_unlocked(store))
        flow.character = self._char_flow.cursor.index
        flow.cursor = row
        flow.clamp_character()
        self._spell_stage_flow = flow
        self._screen = Screen.SPELL_STAGE

    def _run_spell_stage_select(self, inp: FrameInput) -> None:
        """Spell 面选一帧: 渲染 → 菜单键(8 帧进场门, flow 内门控)。行文本
        每帧按存档快照重建(原作同帧统计, DrawSpellStageSelect :2790-2870)。"""
        flow = self._spell_stage_flow
        if flow is None:  # 防御(正常 _enter_spell_stage_select 已建)
            self._enter_main_menu()
            return
        if self._last_menu_screen != Screen.SPELL_STAGE:
            self._last_menu_screen = Screen.SPELL_STAGE
            self._menu_sub_frame = 0
        frame = self._menu_sub_frame
        self._menu_sub_frame += 1
        store = self._title_store
        ch_name = (
            self._characters[flow.character]
            if flow.character < len(self._characters)
            else ""
        )
        lines = (
            [spell_stage_line(store, i, flow.character) for i in range(10)]
            if store is not None
            else list(SPELL_STAGE_NAMES)
        )
        self._renderer.render_spell_stage_select(
            flow, "Spell Practice  {}".format(ch_name), lines, frame
        )
        for act in inp.menu_actions:
            r = flow.handle(act)
            if not r:
                continue
            se = r.get("se")
            if se is not None:
                self._renderer.play_menu_se(se)
            if r["action"] == "quit":
                # BACK 回主菜单, 光标落 Spell Practice 项(:2150-2166)
                self._screen = Screen.MAIN_MENU
                self._flow.cursor.index = ITEM_SPELL_PRACTICE
                return
            if r["action"] == "select":
                self._enter_spell_card_select(r["row"])
                return
        flow.tick_frame()

    def _enter_spell_card_select(self, row: int) -> None:
        """进 Spell 卡选(Init :2208-2331): 先跑 Last Word 解锁判定(:2221
        UnlockLastWordSpellCards; 有新解锁即时落盘), 卡名/确认守卫按存档
        快照烘焙(口径同原作进场烘字), 光标按上次卡号恢复(:2224-2231)。"""
        store = self._title_store
        if store is None:  # 防御
            store = load_score_store(self._score_path)
            self._title_store = store
        newly = unlock_last_words(store)
        if newly:
            log.info("Last Word 新解锁: {}", newly)
            try:
                store.save(self._score_path)
            except OSError:
                pass
        cards = STAGE_SPELLCARD_CARDS[row]
        flow = SpellCardFlowTh08(
            cards=cards,
            names=[spell_card_display_name(store, c) for c in cards],
            selectable=[spell_card_selectable(store, c) for c in cards],
        )
        if self._spell_last_card in cards:
            flow.cursor = cards.index(self._spell_last_card)
        self._spell_card_row = row
        self._spell_card_flow = flow
        self._screen = Screen.SPELL_CARD

    def _run_spell_card_select(self, inp: FrameInput) -> None:
        """Spell 卡选一帧: 渲染 → 菜单键(8 帧进场门, flow 内门控; 未解锁
        确认播 SOUND_INVALID_ACTION, :2512-2514)。"""
        flow = self._spell_card_flow
        stage_flow = self._spell_stage_flow
        if flow is None or stage_flow is None:  # 防御
            self._enter_main_menu()
            return
        if self._last_menu_screen != Screen.SPELL_CARD:
            self._last_menu_screen = Screen.SPELL_CARD
            self._menu_sub_frame = 0
        frame = self._menu_sub_frame
        self._menu_sub_frame += 1
        store = self._title_store
        info = (
            spell_card_info_lines(store, flow.cards[flow.cursor], stage_flow.character)
            if store is not None and flow.cards
            else ()
        )
        self._renderer.render_spell_card_select(
            flow, SPELL_STAGE_NAMES[self._spell_card_row].strip(), info, frame
        )
        for act in inp.menu_actions:
            r = flow.handle(act)
            if not r:
                continue
            se = r.get("se")
            if se == "invalid":
                self._play_se(SE.SOUND_INVALID_ACTION)
            elif se is not None:
                self._renderer.play_menu_se(se)
            if r["action"] == "quit":
                # BACK: 光标处卡号记忆(currentSpellCardNumber, :2520-2527)
                # → 回面选(光标保留, :2530-2535)
                self._spell_last_card = flow.cards[flow.cursor]
                self._screen = Screen.SPELL_STAGE
                return
            if r["action"] == "start":
                self._start_spell_practice(r["card"])
                return
        flow.tick_frame()

    # ---- 练习开局/存档 ----
    def _start_practice(self, row: int) -> None:
        """Practice 面选确认 → 指定面开局(:1941-1950: difficulty =
        defaultDifficulty, currentStage = cursor → GameManager); 残机 8/
        火力按面由 world.configure_practice 覆写(GameManagerSetup.cpp
        :108-109/:244-258)。"""
        log.debug(
            "Practice 进关: stage={} difficulty={} character={}",
            row + 1,
            self._diff.current,
            self._char_flow.cursor.current,
        )
        self._start_game(configure=lambda g: g.configure_practice(row + 1))
        self._run_practice = True

    def _start_spell_practice(self, card: int) -> None:
        """Spell 卡选确认 → 直接进该符卡战(:2448-2510): 难度 = 卡原生难度
        (Spellcard::GetDifficultyFromSpellCard), Last Word 按表映面 +
        difficulty=NORMAL; 残机 8/火力按卡号/sp 资源换表由
        world.configure_spell_practice 处理(GameManagerSetup.cpp:262-276,
        EnemyManager.cpp:1329-1353); BGM = 符卡练习曲
        (g_SpellcardMusicInfo, GameManager.cpp:54-73)。"""
        difficulty = spellcard_difficulty(card)
        if difficulty > 4:  # Last Word(:2466-2510)
            stage_no = LAST_WORD_STAGE_MAP[card] + 1
            difficulty = 1  # NORMAL
        else:
            stage_no = self._spell_card_row + 1
        # 摆 flow 光标(机体表可能按解锁态截成 4 组, 放开成全表再指;
        # 同 _start_replay 口径)
        char = self._spell_stage_flow.character if self._spell_stage_flow else 0
        self._char_flow.cursor.items = list(self._characters)
        self._char_flow.cursor.index = char
        extra = difficulty >= 4
        if not extra and difficulty < len(self._main_difficulties):
            self._diff.index = difficulty
        log.debug(
            "Spell Practice 进卡: card={} stage={} difficulty={} character={}",
            card,
            stage_no,
            difficulty,
            char,
        )
        self._start_game(
            extra=extra,
            configure=lambda g: g.configure_spell_practice(stage_no, card),
        )
        game = self._game
        self._run_spell_card = card
        self._spell_last_card = card
        # 符卡练习曲(GameManagerSetup.cpp:386-398); 置 _bgm_stage 防
        # _run_game 的换关监听用 _s.std 面曲覆写
        bgm = getattr(game, "spell_practice_bgm", None)
        if bgm:
            self._sound.play_music(bgm)
            self._bgm_stage = game.stage_no

    def _save_run_store(self) -> None:
        """练习对局的存档落盘(clrd 面位/catk 记账/flsp; 写盘失败不炸,
        容错同 _save_result_and_exit)并把标题系快照切到本局 store。"""
        store = getattr(self._game, "store", None)
        if store is not None:
            try:
                store.save(self._score_path)
            except OSError:
                pass
            self._title_store = store


__all__ = ["PracticeScreensMixin"]
