"""Touhou: Practice Start / Player Data 场景流测试(SDL dummy, 无真窗口)。"""

from __future__ import annotations

import sys

sys.path.insert(0, r"D:\python_play\Touhou08")

from touhou.engine.render import FrameInput  # noqa: E402
from touhou.games.th07.view.screens import MenuAction, Screen  # noqa: E402


class StubPracticeGame:
    """可 enter_stage、可模拟通关(换关)/GameOver 的假游戏。"""

    def __init__(self, **kw):
        from touhou.engine.score_store import ScoreStore

        self.kw = kw
        self.store = ScoreStore(spellcard_count=141)  # 141 = th07 符卡数
        self.stage_no = 1
        self.entered = None
        self.result = None
        self.ending = None
        self.msg_vm = None
        self.frame = 0
        self.mode = None  # "clear"=tick 后换关; "over"=tick 后 result
        self.ticks = 0

    def enter_stage(self, stage_no):  # noqa: D102
        self.entered = stage_no
        self.stage_no = stage_no

    def tick(self, **kw):  # noqa: D102
        self.ticks += 1
        self.frame += 1
        if self.mode == "clear":
            self.stage_no += 1  # 练习面打通 → _advance_stage 换关
        elif self.mode == "over":
            self.result = {"score": 0, "cleared": False}


def _make_app(tmp_path, monkeypatch, game_cls=StubPracticeGame):
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    import pygame

    if not pygame.display.get_init():
        pygame.init()
        pygame.display.set_mode((640, 480))
    from touhou.games.th07.view import GameApp

    return GameApp(
        game_cls,
        score_path=tmp_path / "score.json",
        config_path=tmp_path / "config.json",
    )


def _goto_practice(app):
    """主菜单 → Practice Start → 难度页(practice)。"""
    while app._flow.cursor.current != "Practice Start":
        app._on_menu(MenuAction.DOWN)
    app._on_menu(MenuAction.CONFIRM)
    assert app._screen == Screen.DIFFICULTY
    assert app._practice_mode


# ---------------------------------------------------------------------------
# 进入流: Practice Start → 难度(4 项) → 机体 → 选关 → enter_stage(n)
# ---------------------------------------------------------------------------


def test_practice_enter_flow(tmp_path, monkeypatch) -> None:
    app = _make_app(tmp_path, monkeypatch)
    _goto_practice(app)
    app._on_menu(MenuAction.UP)  # Normal → Easy
    assert app._practice_diff.current == "Easy"
    app._on_menu(MenuAction.CONFIRM)  # 难度 → 机体
    assert app._screen == Screen.CHARACTER
    app._on_menu(MenuAction.DOWN)  # ReimuA → ReimuB
    app._on_menu(MenuAction.CONFIRM)  # 机体 → 选关
    assert app._screen == Screen.PRACTICE_STAGE
    assert app._practice_max_stage == 1  # 无 clrd 记录 → 只能 Stage 1
    app._on_menu(MenuAction.CONFIRM)  # Stage 1 → 进关
    assert app._screen == Screen.PLAYING
    assert app._game.kw["difficulty"] == 0  # practice 难度页的 Easy
    assert app._game.kw["character"] == 1
    assert app._game.stage_no == 1
    assert app._practice_stage == 1


def test_practice_difficulty_page_has_4_items(tmp_path, monkeypatch) -> None:
    """practice 难度页只有 E/N/H/L(MainMenu.cpp:1210 numDifficulties=4)。"""
    app = _make_app(tmp_path, monkeypatch)
    _goto_practice(app)
    assert len(app._practice_diff) == 4
    app._on_menu(MenuAction.DOWN)
    app._on_menu(MenuAction.DOWN)
    assert app._practice_diff.current == "Lunatic"
    app._on_menu(MenuAction.DOWN)  # 回绕到 Easy, 无 Extra/Phantasm
    assert app._practice_diff.current == "Easy"


def test_practice_stage_unlock_and_back(tmp_path, monkeypatch) -> None:
    """clrd 解锁到 4 面: 光标在 4 项内回绕; BACK 逐层退回标题。"""
    from touhou.engine.score_store import ScoreStore

    s = ScoreStore()
    s.record_clear(0, 1, 4, 0)  # ReimuA Normal 无续关到过 4 面
    s.save(tmp_path / "score.json")
    app = _make_app(tmp_path, monkeypatch)
    _goto_practice(app)
    app._on_menu(MenuAction.CONFIRM)  # 难度(默认 Normal)
    app._on_menu(MenuAction.CONFIRM)  # 机体(默认 ReimuA)
    assert app._screen == Screen.PRACTICE_STAGE
    assert app._practice_max_stage == 4
    for _ in range(4):  # 1→2→3→4→回绕 1
        app._on_menu(MenuAction.DOWN)
    assert app._practice_stage_cursor.index == 0
    app._on_menu(MenuAction.DOWN)
    app._on_menu(MenuAction.DOWN)  # → Stage 3
    app._on_menu(MenuAction.CONFIRM)
    assert app._screen == Screen.PLAYING
    assert app._game.entered == 3  # enter_stage(3)
    # BACK 逐层: 选关 → 机体 → 难度 → 主菜单
    app2 = _make_app(tmp_path, monkeypatch)
    _goto_practice(app2)
    app2._on_menu(MenuAction.CONFIRM)
    app2._on_menu(MenuAction.CONFIRM)
    app2._on_menu(MenuAction.BACK)
    assert app2._screen == Screen.CHARACTER
    app2._on_menu(MenuAction.BACK)
    assert app2._screen == Screen.DIFFICULTY
    app2._on_menu(MenuAction.BACK)
    assert app2._screen == Screen.MAIN_MENU
    assert not app2._practice_mode


# ---------------------------------------------------------------------------
# 打完回标题: 通关(换关)/GameOver 都不进结算, catk 合并落盘, Top10 不写
# ---------------------------------------------------------------------------


def _start_practice(app, stage=1):
    _goto_practice(app)
    app._on_menu(MenuAction.CONFIRM)  # 难度
    app._on_menu(MenuAction.CONFIRM)  # 机体
    for _ in range(stage - 1):
        app._on_menu(MenuAction.DOWN)
    app._on_menu(MenuAction.CONFIRM)  # 选关 → 游玩
    assert app._screen == Screen.PLAYING


def test_practice_clear_returns_to_title(tmp_path, monkeypatch) -> None:
    import pygame

    app = _make_app(tmp_path, monkeypatch)
    _start_practice(app)
    app._game.mode = "clear"
    app._game.store.catk[0]["attempts"][6] = 2  # 练习中遇到的符卡
    scr = pygame.display.get_surface()
    app._run_game(FrameInput())
    assert app._screen == Screen.MAIN_MENU  # 不进 RESULT
    assert app._game is None
    assert app._practice_stage is None
    from touhou.engine.score_store import ScoreStore

    disk = ScoreStore.load(tmp_path / "score.json")
    assert disk.catk[0]["attempts"][6] == 2  # catk 记
    assert disk.highscores == {}  # Top10 不写
    assert all(v == 0 for v in disk.clrd[0]["without_retries"])  # clrd 不写


def test_practice_gameover_returns_to_title(tmp_path, monkeypatch) -> None:
    import pygame

    app = _make_app(tmp_path, monkeypatch)
    _start_practice(app)
    app._game.mode = "over"
    scr = pygame.display.get_surface()
    app._run_game(FrameInput())
    assert app._screen == Screen.MAIN_MENU
    assert app._game is None
    # 不写盘(无 catk 变化时不产生 score.json 以外的副作用)
    from touhou.engine.score_store import ScoreStore

    assert ScoreStore.load(tmp_path / "score.json").highscores == {}


# ---------------------------------------------------------------------------
# Player Data 画面: 进入/渲染/返回
# ---------------------------------------------------------------------------


def test_player_data_screen_flow(tmp_path, monkeypatch) -> None:
    import pygame

    app = _make_app(tmp_path, monkeypatch)
    while app._flow.cursor.current != "Player Data":
        app._on_menu(MenuAction.DOWN)
    app._on_menu(MenuAction.CONFIRM)
    assert app._screen == Screen.PLAYER_DATA
    scr = pygame.display.get_surface()
    app._run_player_data([MenuAction.RIGHT])  # 切机体页(空记录不炸)
    assert app._pd_flow.character == 1
    app._run_player_data([MenuAction.CONFIRM])  # 切板块 → 符卡
    assert app._pd_flow.section == 1
    app._run_player_data([MenuAction.BACK])
    assert app._screen == Screen.MAIN_MENU


class StubPracticeGameOver(StubPracticeGame):
    """GameOver 走 game_over + continue_available 透出(新 impl 语义)的假游戏。"""

    def __init__(self, **kw):
        super().__init__(**kw)
        from types import SimpleNamespace

        self.globals = SimpleNamespace(lives_remaining=0.0, num_retries=0)
        self.game_over = False
        self.max_retries = 3
        self.finalized = 0

    @property
    def continue_available(self):  # noqa: D102
        return self.game_over and self.result is None

    def tick(self, **kw):  # noqa: D102
        self.ticks += 1
        self.frame += 1
        if self.mode == "over":
            self.game_over = True  # 待续关(不自动填 result)
        elif self.mode == "clear":
            self.stage_no += 1

    def finalize_game_over(self):  # noqa: D102
        self.finalized += 1
        self.game_over = False
        self.result = {"score": 0, "cleared": False}


def test_practice_gameover_no_continue_menu(tmp_path, monkeypatch) -> None:
    """Practice 不可续关(C++ practice 跳过 retry 菜单): game_over 直接结算
    回标题, 不弹 Continue? 菜单。"""
    import pygame

    app = _make_app(tmp_path, monkeypatch, game_cls=StubPracticeGameOver)
    _start_practice(app)
    app._game.mode = "over"
    g = app._game
    scr = pygame.display.get_surface()
    app._run_game(FrameInput())
    assert not app._in_continue  # 无续关菜单
    assert g.finalized == 1  # 结算照走(store 入账一致)
    assert app._screen == Screen.MAIN_MENU
    assert app._game is None


# ---------------------------------------------------------------------------
# 中选开局樱点补偿 (GameManager.cpp:609-628 AddedCallback switch):
# 2面 cherry=cherryMax; N面 cherryMax += 50000*(N-2) 且 cherry=cherryMax
# ---------------------------------------------------------------------------


class StubPracticeCherryGame(StubPracticeGame):
    """带真实 globals(樱点字段)的练习桩, 复刻 world 的 Normal 开局初始化。"""

    def __init__(self, **kw):
        super().__init__(**kw)
        from touhou.games.th07.globals import ZunGlobals

        g = ZunGlobals()
        g.initialize_rank(1)  # Normal
        g.cherry_max = g.cherry_start + 200000  # 同 world 新开局分支
        self.globals = g


def test_practice_midstage_start_cherry_bonus(tmp_path, monkeypatch) -> None:
    from touhou.engine.score_store import ScoreStore

    s = ScoreStore()
    s.record_clear(0, 1, 4, 0)  # 解锁到 4 面(同 test_practice_stage_unlock)
    s.save(tmp_path / "score.json")
    # 4 面起步: cherryMax +100000 且填满
    app = _make_app(tmp_path, monkeypatch, game_cls=StubPracticeCherryGame)
    _start_practice(app, stage=4)
    g = app._game.globals
    assert g.cherry_max == g.cherry_start + 200000 + 100000
    assert g.cherry == g.cherry_max
    # 2 面起步: 上限不加, 只填满
    app2 = _make_app(tmp_path, monkeypatch, game_cls=StubPracticeCherryGame)
    _start_practice(app2, stage=2)
    g2 = app2._game.globals
    assert g2.cherry_max == g2.cherry_start + 200000
    assert g2.cherry == g2.cherry_max
