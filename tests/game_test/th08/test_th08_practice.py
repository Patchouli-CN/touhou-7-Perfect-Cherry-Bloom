"""th08 Practice/Spell Practice 测试(C 期第 5 片) —— 纯逻辑 + 应用壳接线 + 真数据 smoke。

对照 th08-ref TitleScreen.cpp(OnUpdatePracticeStageSelect :1857-1968 /
OnUpdateSpellStageSelect :1971-2192 / OnUpdateSpellCardSelect :2195-2545)、
TitleUnlockLastWords.inl(17 条件)、GameManagerSetup.cpp(练习开局参数);
行为细节见 games/th08/view/practice_flow.py 与 games/th08/progress.py。
"""

from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import types  # noqa: E402

import pygame  # noqa: E402

from touhou.engine.render import FrameInput  # noqa: E402
from touhou.games.th07.view.screens import MenuAction, Screen  # noqa: E402
from touhou.games.th08 import progress as prog  # noqa: E402
from touhou.games.th08.spellcards import (  # noqa: E402
    LAST_WORD_STAGE_MAP,
    STAGE_SPELLCARD_CARDS,
    spellcard_difficulty,
)
from touhou.games.th08.view.practice_flow import (  # noqa: E402
    INPUT_GATE_FRAMES,
    SPELLCARD_NAME_AVAILABLE,
    SPELLCARD_NAME_HIDDEN,
    PracticeStageFlowTh08,
    SpellCardFlowTh08,
    SpellStageFlowTh08,
    spell_card_display_name,
    spell_card_info_lines,
    spell_card_selectable,
    spell_stage_line,
)

from .conftest import needs_data  # noqa: E402
from .test_th08_view import StubGame, StubRenderer  # noqa: E402

pygame.init()


def _store():
    """th08 口径空存档(222 卡 × 13 槽双组 catk + clrd 13 行)。"""
    from touhou.engine.score_store import ScoreStore

    return ScoreStore.from_dict(
        None,
        spellcard_count=prog.SPELLCARD_COUNT,
        num_characters=prog.CLRD_ROWS,
        num_difficulties=prog.NUM_DIFFICULTIES,
        catk_slot_count=prog.CATK_SLOT_COUNT,
        catk_practice_group=True,
    )


def _enable(flow) -> None:
    """走过进场 8 帧输入门(:1887/:2086/:2333)。"""
    for _ in range(INPUT_GATE_FRAMES):
        flow.tick_frame()


# ---- progress: practice_clear_info / catk 判定 / flsp ----


def test_practice_clear_info_floor_and_6b() -> None:
    """clearInfo: 空档保底 1 面(:1901-1904); 6B 通开 4A/4B(:1906-1909)。"""
    store = _store()
    assert prog.practice_clear_info(store, 0, 1) == 1
    prog.record_stage_clear(store, 0, 1, 0, 0)  # 1 面
    assert prog.practice_clear_info(store, 0, 1) == 1
    prog.record_stage_clear(store, 0, 1, prog.STAGE_6B, 0)  # 6B
    info = prog.practice_clear_info(store, 0, 1)
    assert info & 0x80 and info & 0x18  # 6B 位 + 4A/4B 位
    # 难度隔离: 别的难度不受影响
    assert prog.practice_clear_info(store, 0, 2) == 1


def test_catk_predicates() -> None:
    """AttemptedAny/CapturedAny/SpellPracticeCaptured 的两组语义
    (ScoreDat.hpp:188-205)。"""
    store = _store()
    assert not prog.card_attempted_any(store, 66, prog.SHOT_ALL_ROW)
    store.record_spellcard_attempt(66, "n", 0)  # inGame 组
    assert prog.card_attempted_any(store, 66, prog.SHOT_ALL_ROW)
    assert not prog.spell_practice_captured(store, 66, prog.SHOT_ALL_ROW)
    store.record_spellcard_success(66, 0, 100, practice=True)  # practice 组
    assert prog.card_captured_any(store, 66, prog.SHOT_ALL_ROW)
    assert prog.spell_practice_captured(store, 66, prog.SHOT_ALL_ROW)
    assert not prog.spell_practice_captured(store, 66, 1)  # 别的机体槽


def test_flsp_roundtrip(tmp_path) -> None:
    """flsp 落点 plst.lastWordUnlocked: save → load_score_store 回读保留
    (引擎 from_dict 只回读已知 plst 键, progress 补注)。"""
    store = _store()
    for ch in (0, 1):
        store.clrd[ch]["without_retries"][0] |= prog.EXTRA_UNLOCKED_FLAG
    assert prog.unlock_last_words(store) == [205]
    path = tmp_path / "score.json"
    store.save(path)
    reloaded = prog.load_score_store(path)
    assert prog.last_word_unlocked(reloaded, 205)
    assert prog.is_last_word_spellcard_attempted(reloaded, 205)
    assert not prog.last_word_unlocked(reloaded, 206)
    # 幂等: 再跑一遍无新解锁
    assert prog.unlock_last_words(reloaded) == []


def test_unlock_last_words_conditions() -> None:
    """17 条件代表项(TitleUnlockLastWords.inl; 全表见
    th08-title-systems.md §8.3)。"""
    # 207: spellPractice 组收取总数 >= 50(只看 practice 组)
    store = _store()
    for c in range(50):
        store.record_spellcard_success(c, 0, 10, practice=True)
    store.record_spellcard_success(100, 0, 10)  # inGame 组不计
    assert 207 in prog.unlock_last_words(store)
    # 210/219: Last Spell practice 收取 15/30
    store = _store()
    from touhou.games.th08.spellcards import LAST_SPELL_CARDS

    for c in LAST_SPELL_CARDS[:15]:
        store.record_spellcard_success(c, 0, 10, practice=True)
    assert 210 in prog.unlock_last_words(store)
    # 211: 145/195 已收 + 204 已见
    store = _store()
    store.record_spellcard_success(145, 0, 10)
    store.record_spellcard_success(195, 0, 10, practice=True)
    store.record_spellcard_attempt(204, "n", 0)
    assert 211 in prog.unlock_last_words(store)
    # 214: 魔理沙 Normal 全卡收取(SHOT_MARISA 槽)
    store = _store()
    from touhou.games.th08.spellcards import SPELLCARDS_PER_DIFFICULTY

    for c in SPELLCARDS_PER_DIFFICULTY[1]:
        store.record_spellcard_success(c, prog.SHOT_MARISA, 10)
    assert 214 in prog.unlock_last_words(store)
    # 215: 单人灵梦 H/L 无续关 6B(EXTRA_UNLOCKED_FLAG)
    store = _store()
    store.clrd[prog.SHOT_REIMU]["without_retries"][2] |= prog.EXTRA_UNLOCKED_FLAG
    assert 215 in prog.unlock_last_words(store)
    # 218: Extra 面位(bit8) 3 机体
    store = _store()
    for ch in (0, 1, 2):
        store.clrd[ch]["without_retries"][prog.EXTRA_DIFFICULTY] |= (
            1 << prog.STAGE_EXTRA
        )
    assert 218 in prog.unlock_last_words(store)
    # 220: SHOT_ALL with_retries[LUNATIC] & 0xC000
    store = _store()
    store.clrd[prog.SHOT_ALL_ROW]["with_retries"][3] |= 0xC000
    assert 220 in prog.unlock_last_words(store)
    # 221: 205-220 全已见
    store = _store()
    for c in range(205, 221):
        store.record_spellcard_attempt(c, "n", 0, practice=True)
    assert 221 in prog.unlock_last_words(store)


def test_spellcard_difficulty() -> None:
    """GetDifficultyFromSpellCard: E/N/H/L/EX 按页, Last Word = 5(>EXTRA)。"""
    assert spellcard_difficulty(2) == 0  # ST1_BOSS_1E
    assert spellcard_difficulty(0) == 2  # ST1_MBOSS_1H
    assert spellcard_difficulty(191) == 4  # EX
    assert spellcard_difficulty(205) == 5  # Last Word
    assert spellcard_difficulty(221) == 5


# ---- 显示行生成 ----


def test_display_name_branches() -> None:
    """卡名三分支(:2254-2270): 未遭遇隐藏 / 已遭遇真名 / LW 已解锁未遭遇
    = 挑戦者歓迎中。"""
    store = _store()
    assert spell_card_display_name(store, 2) == SPELLCARD_NAME_HIDDEN
    store.record_spellcard_attempt(2, "流符「流光」", 0)
    assert spell_card_display_name(store, 2) == "流符「流光」"
    # Last Word: 未解锁恒隐藏(即使 IsLastWordSpellCardAttempted 为假)
    assert spell_card_display_name(store, 205) == SPELLCARD_NAME_HIDDEN
    store.plst["lastWordUnlocked"] = [205] + [0] * 16
    assert spell_card_display_name(store, 205) == SPELLCARD_NAME_AVAILABLE
    assert spell_card_selectable(store, 205)
    assert not spell_card_selectable(store, 206)


def test_stage_line_counts() -> None:
    """面选行: 标记 + 面名 + 收取(本机/合计)/挑战可能/总数(:2850-2872)。"""
    store = _store()
    line = spell_stage_line(store, 0, 0)
    assert line.startswith(" Stage1") and line.endswith("  0(  0)/  0/ 13")
    cards = STAGE_SPELLCARD_CARDS[0]
    for c in cards:
        store.record_spellcard_success(c, 0, 10, practice=True)
        store.record_spellcard_attempt(c, "n", 0)
    line = spell_stage_line(store, 0, 0)
    assert line.startswith("@")  # 本机 practice 全收
    assert "/ 13/ 13" in line


def test_info_lines() -> None:
    """卡选信息区: 未遭遇给 --- 行 + Last Word 挑战条件(TitleFormatSpellCardInfo)。"""
    store = _store()
    lines = spell_card_info_lines(store, 2, 0)
    assert lines[0].startswith("No.003") and "---/---" in lines[2]
    lw = spell_card_info_lines(store, 205, 0)
    assert "挑戦可能条件" in lw[3]  # 挑战条件文本(卡号-204 下标)


# ---- flow 纯逻辑 ----


def test_practice_stage_flow_gate_and_skip() -> None:
    """Practice 面选: 8 帧门; 光标只停置位行(:1915-1939); BACK → quit。"""
    flow = PracticeStageFlowTh08(clear_info=0b0000111)
    assert flow.handle(MenuAction.DOWN) is None  # 门内
    _enable(flow)
    flow.cursor = 2
    assert flow.handle(MenuAction.DOWN) == {"action": "move", "se": "select"}
    assert flow.cursor == 0  # 3..7 未解锁, 回绕到 0
    assert flow.handle(MenuAction.UP)["action"] == "move"
    assert flow.cursor == 2
    assert flow.handle(MenuAction.CONFIRM) == {"action": "start", "row": 2, "se": "ok"}
    assert flow.handle(MenuAction.BACK) == {"action": "quit", "se": "cancel"}


def test_practice_stage_flow_clamp() -> None:
    """光标停未解锁行 → 回 0(:1914-1917)。"""
    flow = PracticeStageFlowTh08(clear_info=0b0000011, cursor=5)
    flow.clamp_cursor()
    assert flow.cursor == 0


def test_spell_stage_flow_character_switch() -> None:
    """Spell 面选: LEFT/RIGHT 切机体跳过未解锁(:2102-2118); 8 帧门。"""
    flow = SpellStageFlowTh08(char_unlocked=[True, False, True, True], character=0)
    _enable(flow)
    assert flow.handle(MenuAction.RIGHT) == {"action": "character", "se": "select"}
    assert flow.character == 2  # 跳过锁定的 1
    flow.handle(MenuAction.LEFT)
    assert flow.character == 0
    flow.handle(MenuAction.UP)
    assert flow.cursor == 9  # 回绕到 Last Word 行
    assert flow.handle(MenuAction.CONFIRM) == {"action": "select", "row": 9, "se": "ok"}
    assert flow.handle(MenuAction.BACK) == {"action": "quit", "se": "cancel"}


def test_spell_stage_flow_clamp_character() -> None:
    """进画面机体钳到已解锁(:1999-2008)。"""
    flow = SpellStageFlowTh08(char_unlocked=[False, True, False], character=8)
    flow.clamp_character()
    assert flow.character == 1


def test_spell_card_flow_paging() -> None:
    """卡选翻页(:2303-2355): LEFT 整页 -15 回绕; RIGHT 尾页回卷 %=15;
    不足一页不翻。"""
    cards = STAGE_SPELLCARD_CARDS[7]  # 6B 44 张
    flow = SpellCardFlowTh08(
        cards=cards, names=["n"] * len(cards), selectable=[True] * len(cards)
    )
    _enable(flow)
    flow.handle(MenuAction.RIGHT)
    assert flow.cursor == 15 and flow.page == 1
    flow.handle(MenuAction.RIGHT)
    assert flow.cursor == 30
    flow.handle(MenuAction.RIGHT)  # 44-30=14 <= 44%15=14 → 回卷 0
    assert flow.cursor == 0
    flow.handle(MenuAction.LEFT)  # -15 < 0 → 回绕 43
    assert flow.cursor == len(cards) - 1
    small = SpellCardFlowTh08(cards=(1, 2), names=["a", "b"], selectable=[True, True])
    _enable(small)
    assert small.handle(MenuAction.RIGHT) is None


def test_spell_card_flow_confirm_guard() -> None:
    """确认守卫(:2453-2458): 不可选播 invalid, 可选给 start+卡号。"""
    flow = SpellCardFlowTh08(cards=(66, 67), names=["a", "b"], selectable=[False, True])
    _enable(flow)
    assert flow.handle(MenuAction.CONFIRM) == {"action": "invalid", "se": "invalid"}
    flow.handle(MenuAction.DOWN)
    assert flow.handle(MenuAction.CONFIRM) == {
        "action": "start",
        "card": 67,
        "se": "ok",
    }
    assert flow.handle(MenuAction.BACK) == {"action": "quit", "se": "cancel"}


# ---- 应用壳接线(StubRenderer) ----


class StubPracticeGame(StubGame):
    """练习开局接线用的假游戏: configure_* 记账 + 残机覆写观测点。"""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.store = _store()  # th08 形状(222 卡双组), 收尾落盘路径要读 catk
        self.stage_no = 1
        self.stage = None
        self.game_over = False
        self.continue_available = False
        self.practice_call: int | None = None
        self.spell_call: tuple | None = None
        self.spell_practice_bgm = None
        self.globals = types.SimpleNamespace(
            lives_remaining=3.0, bombs_remaining=3.0, num_retries=0
        )

    def configure_practice(self, stage_no: int) -> None:
        self.practice_call = stage_no
        self.stage_no = stage_no
        self.globals.lives_remaining = 8.0

    def configure_spell_practice(self, stage_no: int, card_no: int) -> None:
        self.spell_call = (stage_no, card_no)
        self.stage_no = stage_no
        self.globals.lives_remaining = 8.0

    def continue_play(self) -> None:
        self.game_over = False

    def finalize_game_over(self) -> None:
        self.tick()  # 出 result 的捷径(StubGame.tick 第 2 帧出)


def _practice_app(tmp_path, store=None):
    from touhou.games.th08.view import GameApp

    score_path = tmp_path / "score.json"
    if store is not None:
        store.save(score_path)
    return GameApp(
        StubPracticeGame,
        config_path=tmp_path / "config.json",
        score_path=score_path,
        renderer=StubRenderer(),
    )


def _spell_unlocked_store():
    """Spell Practice 解锁(0 号机体 with_retries 带 bit15)的空存档。"""
    store = _store()
    store.clrd[0]["with_retries"][1] |= prog.SPELL_PRACTICE_UNLOCKED_FLAG
    return store


def _enter_practice_stage(app) -> None:
    """主菜单 Practice(3) → 难度 → 机体 → 面选。"""
    app._flow.cursor.index = 3
    app._run_title_menu((MenuAction.CONFIRM,))
    assert app._screen == Screen.DIFFICULTY and app._practice_mode
    app._run_menu((MenuAction.CONFIRM,))  # 难度(Normal)
    app._run_menu((MenuAction.CONFIRM,))  # 机体 → 面选
    assert app._screen == Screen.PRACTICE_STAGE
    for _ in range(INPUT_GATE_FRAMES):
        app._run_practice_stage_select(FrameInput())


def _enter_spell_stage(app) -> None:
    """主菜单 Spell Practice(2, 已解锁) → 面选。"""
    app._flow.cursor.index = 2
    app._run_title_menu((MenuAction.CONFIRM,))
    assert app._screen == Screen.SPELL_STAGE
    for _ in range(INPUT_GATE_FRAMES):
        app._run_spell_stage_select(FrameInput())


def test_app_practice_flow_to_stage_select(tmp_path) -> None:
    """Practice Start 流: 难度 → 机体 → 面选(clearInfo 保底 1 面);
    BACK 回机体选择(:1952-1959)。"""
    app = _practice_app(tmp_path)
    _enter_practice_stage(app)
    assert app._practice_flow.clear_info == 1  # 空档保底 1 面
    stub = app._renderer
    assert ("practice_stage", 0, 0) in stub.calls  # frame==0 = 进屏
    app._run_practice_stage_select(FrameInput(menu_actions=(MenuAction.BACK,)))
    assert app._screen == Screen.CHARACTER


def test_app_practice_start_and_finish(tmp_path) -> None:
    """面选确认 → configure_practice(面) 开局(残机 8 覆写); 出 result →
    不进结算画面, 存档落盘回 Practice 面选(practiceState=1 净效果)。"""
    app = _practice_app(tmp_path)
    _enter_practice_stage(app)
    app._run_practice_stage_select(FrameInput(menu_actions=(MenuAction.CONFIRM,)))
    assert app._screen == Screen.PLAYING
    game = app._game
    assert game.practice_call == 1 and game.globals.lives_remaining == 8.0
    assert app._run_practice and app._run_spell_card is None
    app._run_game(FrameInput())
    app._run_game(FrameInput())  # 第 2 帧出 result → 回面选
    assert app._screen == Screen.PRACTICE_STAGE
    assert app._game is None and not app._run_practice
    assert (tmp_path / "score.json").exists()  # 存档落盘
    assert app._title_store is game.store  # 标题快照切到本局 store


def test_app_practice_quit_to_title(tmp_path) -> None:
    """练习局暂停 Quit to Title(确认 Yes) → 落盘回 Practice 面选
    (不回主菜单; 对照 practiceState 链)。"""
    app = _practice_app(tmp_path)
    _enter_practice_stage(app)
    app._run_practice_stage_select(FrameInput(menu_actions=(MenuAction.CONFIRM,)))
    app._run_game(FrameInput(esc=True))  # 暂停
    assert app._paused
    app._run_game(FrameInput(menu_actions=(MenuAction.DOWN,)))  # → Retry
    app._run_game(FrameInput(menu_actions=(MenuAction.DOWN,)))  # → Quit to Title
    app._run_game(FrameInput(menu_actions=(MenuAction.CONFIRM,)))  # 二次确认弹窗
    app._run_game(FrameInput(menu_actions=(MenuAction.UP,)))  # Yes
    app._run_game(FrameInput(menu_actions=(MenuAction.CONFIRM,)))
    assert app._screen == Screen.PRACTICE_STAGE
    assert app._game is None


def test_app_spell_stage_select_navigation(tmp_path) -> None:
    """Spell 面选: 切机体(跳过未解锁) + BACK 回主菜单光标 2(:2150-2166)。"""
    store = _spell_unlocked_store()
    app = _practice_app(tmp_path, store)
    _enter_spell_stage(app)
    flow = app._spell_stage_flow
    assert flow.character == 0  # 只有 0 号机体解锁
    app._run_spell_stage_select(FrameInput(menu_actions=(MenuAction.RIGHT,)))
    assert flow.character == 0  # 其余 3 组未解锁, 回绕仍 0
    app._run_spell_stage_select(FrameInput(menu_actions=(MenuAction.BACK,)))
    assert app._screen == Screen.MAIN_MENU
    assert app._flow.cursor.index == 2


def test_app_spell_locked_menu_item() -> None:
    """Spell Practice 未解锁时主菜单项锁定(确认无效, TitleFlowTh08 既有口径)。"""
    from touhou.games.th08.view.title_flow import TitleFlowTh08

    flow = TitleFlowTh08()
    flow.cursor.index = 2
    assert flow.handle(MenuAction.CONFIRM) is None
    flow.spell_practice_unlocked = True
    assert flow.handle(MenuAction.CONFIRM) == {"action": "spell_practice"}


def test_app_spell_card_select_and_start(tmp_path) -> None:
    """卡选: 未遭遇确认播 invalid 留下; 已遭遇确认 →
    configure_spell_practice(面, 卡) 开局(残机 8)。"""
    store = _spell_unlocked_store()
    store.record_spellcard_attempt(66, "n", 0)  # 3 面卡(32..53)? 66 在 4A(54..76)
    app = _practice_app(tmp_path, store)
    _enter_spell_stage(app)
    app._spell_stage_flow.cursor = 3  # Stage4A 行
    app._run_spell_stage_select(FrameInput(menu_actions=(MenuAction.CONFIRM,)))
    assert app._screen == Screen.SPELL_CARD
    flow = app._spell_card_flow
    assert flow.cards == STAGE_SPELLCARD_CARDS[3]
    for _ in range(INPUT_GATE_FRAMES):
        app._run_spell_card_select(FrameInput())
    # 光标在 66 号(已遭遇恢复 :2224-2231 口径)? 未进过卡选 → 0; 移到 66
    flow.cursor = flow.cards.index(66)
    app._run_spell_card_select(FrameInput(menu_actions=(MenuAction.CONFIRM,)))
    assert app._screen == Screen.PLAYING
    assert app._game.spell_call == (4, 66)  # 4A = stage_no 4
    assert app._game.globals.lives_remaining == 8.0
    assert app._run_spell_card == 66


def test_app_spell_card_invalid_stays(tmp_path) -> None:
    """未遭遇卡确认: SOUND_INVALID_ACTION + 留在卡选(:2512-2514)。"""
    store = _spell_unlocked_store()
    app = _practice_app(tmp_path, store)
    _enter_spell_stage(app)
    app._run_spell_stage_select(FrameInput(menu_actions=(MenuAction.CONFIRM,)))
    for _ in range(INPUT_GATE_FRAMES):
        app._run_spell_card_select(FrameInput())
    app._run_spell_card_select(FrameInput(menu_actions=(MenuAction.CONFIRM,)))
    assert app._screen == Screen.SPELL_CARD


def test_app_spell_card_back_restores_cursor(tmp_path) -> None:
    """卡选 BACK → 面选光标保留(:2530-2535); 卡号记忆进下次卡选
    (:2520-2527)。"""
    store = _spell_unlocked_store()
    store.record_spellcard_attempt(13, "n", 0)  # 2 面首张
    app = _practice_app(tmp_path, store)
    _enter_spell_stage(app)
    app._spell_stage_flow.cursor = 1  # Stage2 行
    app._run_spell_stage_select(FrameInput(menu_actions=(MenuAction.CONFIRM,)))
    for _ in range(INPUT_GATE_FRAMES):
        app._run_spell_card_select(FrameInput())
    assert app._spell_card_flow.cursor == 0  # 13 号记忆恢复
    app._run_spell_card_select(FrameInput(menu_actions=(MenuAction.DOWN,)))
    app._run_spell_card_select(FrameInput(menu_actions=(MenuAction.BACK,)))
    assert app._screen == Screen.SPELL_STAGE
    assert app._spell_stage_flow.cursor == 1
    assert app._spell_last_card == 14


def test_app_spell_run_finish_and_retry(tmp_path) -> None:
    """符卡战出 result → 回 Spell 面选(practiceState=2 净效果, 行光标恢复);
    GameOver retry Yes → 重开同卡(SpellcardPracticeRestart 口径)。"""
    store = _spell_unlocked_store()
    store.record_spellcard_attempt(66, "n", 0)
    app = _practice_app(tmp_path, store)
    _enter_spell_stage(app)
    app._spell_stage_flow.cursor = 3
    app._run_spell_stage_select(FrameInput(menu_actions=(MenuAction.CONFIRM,)))
    for _ in range(INPUT_GATE_FRAMES):
        app._run_spell_card_select(FrameInput())
    app._spell_card_flow.cursor = app._spell_card_flow.cards.index(66)
    app._run_spell_card_select(FrameInput(menu_actions=(MenuAction.CONFIRM,)))
    first = app._game
    app._run_game(FrameInput())
    app._run_game(FrameInput())  # 出 result → 回面选
    assert app._screen == Screen.SPELL_STAGE
    assert app._spell_stage_flow.cursor == 3
    assert app._run_spell_card is None
    # 再进一次 → GameOver retry Yes → 重开同卡(新实例)
    app._run_spell_stage_select(FrameInput(menu_actions=(MenuAction.CONFIRM,)))
    for _ in range(INPUT_GATE_FRAMES):
        app._run_spell_card_select(FrameInput())
    app._spell_card_flow.cursor = app._spell_card_flow.cards.index(66)
    app._run_spell_card_select(FrameInput(menu_actions=(MenuAction.CONFIRM,)))
    second = app._game
    assert second is not first and second.spell_call == (4, 66)
    second.game_over = True
    second.result = None
    second.continue_available = True
    app._run_game(FrameInput())  # 弹 retry 菜单
    app._run_continue_menu((MenuAction.CONFIRM,))  # Yes → 重开(绕过 tick 自动出 result)
    third = app._game
    assert third is not second and third.spell_call == (4, 66)


def test_app_last_word_start_maps_stage(tmp_path) -> None:
    """Last Word 卡开局: 按表映面 + difficulty=NORMAL(:2466-2510)。"""
    store = _spell_unlocked_store()
    store.plst["lastWordUnlocked"] = [205] + [0] * 16
    app = _practice_app(tmp_path, store)
    _enter_spell_stage(app)
    app._spell_stage_flow.cursor = 9  # Last Word 行
    app._run_spell_stage_select(FrameInput(menu_actions=(MenuAction.CONFIRM,)))
    for _ in range(INPUT_GATE_FRAMES):
        app._run_spell_card_select(FrameInput())
    assert app._spell_card_flow.cards == STAGE_SPELLCARD_CARDS[9]
    app._run_spell_card_select(FrameInput(menu_actions=(MenuAction.CONFIRM,)))
    assert app._screen == Screen.PLAYING
    assert app._game.spell_call == (LAST_WORD_STAGE_MAP[205] + 1, 205)
    assert app._game.kw["difficulty"] == 1  # NORMAL


def test_app_unlock_last_words_on_card_select_entry(tmp_path) -> None:
    """进卡选跑 Last Word 解锁判定(:2221): 条件达成即解锁 + 落盘 +
    行显示变为可选。"""
    store = _spell_unlocked_store()
    for ch in (0, 1):
        store.clrd[ch]["without_retries"][0] |= prog.EXTRA_UNLOCKED_FLAG
    app = _practice_app(tmp_path, store)
    _enter_spell_stage(app)
    app._spell_stage_flow.cursor = 9
    app._run_spell_stage_select(FrameInput(menu_actions=(MenuAction.CONFIRM,)))
    flow = app._spell_card_flow
    assert flow.selectable[0]  # 205 已解锁
    assert flow.names[0] == SPELLCARD_NAME_AVAILABLE
    reloaded = prog.load_score_store(tmp_path / "score.json")
    assert prog.last_word_unlocked(reloaded, 205)


# ---- 真数据 smoke ----


@needs_data
def test_real_spell_practice_fight() -> None:
    """真实 th08.dat: configure_spell_practice 进 1 面卡(sp ECL/_s.std 换表 +
    ex19 选卡), 空弹 1200 帧不炸; 符卡 attempts 记进 practice 组。"""
    from touhou.games.th08.world import ImperishableNight
    from touhou.paths import DEFAULT_DATA_PATHS

    g = ImperishableNight(
        data_path=DEFAULT_DATA_PATHS["th08"], character=0, difficulty=0, seed=3
    )
    g.configure_spell_practice(1, 2)  # ST1_BOSS_1E
    assert g.spell_practice_card == 2 and g.globals.lives_remaining == 8.0
    assert g.ecl_host.current_spellcard_number == 2
    assert g.globals.last_spell_time_orb_threshold == 0
    assert g.spell_practice_bgm == "th08_03.mid"  # 卡 <=12(g_SpellcardMusicInfo)
    for _ in range(700):
        g.tick(keys=(False, False, False, False, False, False), advance=True)
    assert g.ecl_host.spellcard_idx == 2  # 已进该卡战(ex19 选卡生效)
    entry = g.store.catk[2]
    assert entry["practice"]["attempts"][0] >= 1  # catk 记 practice 组
    assert entry["attempts"][0] == 0  # inGame 组不记


@needs_data
def test_real_practice_mode_result() -> None:
    """真实 th08.dat: configure_practice 后 _advance_or_ending = 练习单面
    结算(写 CLRD/不入 HSCR 榜/无结局); final_result 幂等。"""
    from touhou.games.th08.world import ImperishableNight
    from touhou.paths import DEFAULT_DATA_PATHS

    g = ImperishableNight(
        data_path=DEFAULT_DATA_PATHS["th08"], character=0, difficulty=1, seed=3
    )
    g.configure_practice(2)
    assert g.stage_no == 2 and g.globals.lives_remaining == 8.0
    assert g.power == 112.0  # 2 面火力(GameManagerSetup.cpp:250-253)
    g._advance_or_ending()
    assert g.cleared and g.result is not None and g.ending is None
    assert g.result["rank"] == -1  # 不入 HSCR 榜
    assert not g.store.entries(1, 0)  # 榜为空
    # 练习通关写 CLRD 面位(GameManager.cpp:297-308 不被 practice 门控)
    assert prog.stage_cleared_with_retries(g.store, 0, 1, 1)


@needs_data
def test_real_spell_practice_no_clrd() -> None:
    """符卡练习结算不写 CLRD 面位(原作符卡练习无过面入账)。"""
    from touhou.games.th08.world import ImperishableNight
    from touhou.paths import DEFAULT_DATA_PATHS

    g = ImperishableNight(
        data_path=DEFAULT_DATA_PATHS["th08"], character=0, difficulty=0, seed=3
    )
    g.configure_spell_practice(1, 2)
    g._advance_or_ending()
    assert g.cleared and g.result is not None
    assert not prog.stage_cleared_with_retries(g.store, 0, 0, 0)


@needs_data
def test_real_practice_views_render() -> None:
    """真实 th08.dat: PracticeMenuView 贴图渲染三画面若干帧不炸。"""
    from touhou.games.th08.view import PygameTh08Renderer
    from touhou.paths import DEFAULT_DATA_PATHS

    renderer = PygameTh08Renderer(DEFAULT_DATA_PATHS["th08"])
    renderer.open(scale=1)
    try:
        store = _spell_unlocked_store()
        p = PracticeStageFlowTh08(clear_info=0b101)
        renderer.render_practice_stage_select(
            p, "Practice  Normal / ReimuYukari", ["Stage1", "Stage2"], 0
        )
        s = SpellStageFlowTh08(char_unlocked=[True, False, True, True])
        lines = [spell_stage_line(store, i, 0) for i in range(10)]
        renderer.render_spell_stage_select(s, "Spell Practice  ReimuYukari", lines, 0)
        c = SpellCardFlowTh08(
            cards=STAGE_SPELLCARD_CARDS[0],
            names=[spell_card_display_name(store, x) for x in STAGE_SPELLCARD_CARDS[0]],
            selectable=[
                spell_card_selectable(store, x) for x in STAGE_SPELLCARD_CARDS[0]
            ],
        )
        info = spell_card_info_lines(store, 0, 0)
        renderer.render_spell_card_select(c, "Stage1", info, 0)
        assert renderer._practice_view is not None  # 贴图视图加载成功(未回退)
    finally:
        renderer.close()
        pygame.init()  # close() 会 pygame.quit(), 恢复以免影响后续用例
