"""Touhou: GameApp 菜单流测试(不依赖 pygame 窗口)。"""
from __future__ import annotations

import sys

sys.path.insert(0, r"D:\python_play\Touhou08")

from touhou.engine.render import FrameInput  # noqa: E402
from touhou.games.th07.view.screens import MenuAction, Screen  # noqa: E402


class StubGame:
    def __init__(self, **kw):
        self.kw = kw
        self.ticks = 0

    def tick(self, **kw):  # noqa: D102
        self.ticks += 1


class _App:
    """与 GameApp 同构的菜单状态机(避开 pygame.display 依赖)。"""

    def __init__(self, make_game):
        from touhou.games.th07.view import GameApp

        self._impl = GameApp(make_game)
        self._impl._screen = Screen.MAIN_MENU
        self._make = make_game

    def main_mnu_action(self, act):
        self._impl._on_menu(act)

    # 模拟: 到主菜单"开始游戏"(index0) 确认 → 难度 → 角色 → 开始
    def play_to_start(self):
        impl = self._impl
        impl._on_menu(MenuAction.CONFIRM)          # 开始游戏 → 难度
        assert impl._screen == Screen.DIFFICULTY
        impl._on_menu(MenuAction.CONFIRM)          # 难度确认(默认 Normal) → 角色
        assert impl._screen == Screen.CHARACTER
        impl._on_menu(MenuAction.CONFIRM)          # 角色确认(默认 ReimuA) → 开始
        assert impl._screen == Screen.PLAYING
        return impl._game


def test_app_reaches_playing_and_constructs_game() -> None:
    make = lambda difficulty, character: StubGame(difficulty=difficulty, character=character)
    app = _App(make)
    g = app.play_to_start()
    assert isinstance(g, StubGame)
    # 默认难度 Normal(idx1) 角色 ReimuA(idx0)
    assert g.kw["difficulty"] == 1
    assert g.kw["character"] == 0


def test_app_menu_navigation() -> None:
    from touhou.games.th07.view import GameApp

    app = GameApp(lambda **kw: None)
    app._screen = Screen.DIFFICULTY
    app._diff.move(1)  # Normal → Hard
    assert app._diff.current == "Hard"
    app._on_menu(MenuAction.BACK)
    assert app._screen == Screen.MAIN_MENU


def test_main_difficulty_cursor_wraps_within_four() -> None:
    """BUGS.md#1 回归: 本篇难度选择只含 4 项(Easy..Lunatic)。

    光标曾在全名单(含 Extra/Phantasm)上回绕: 第 4 次 DOWN 选中不可见的
    Extra(difficulty=4)出界。修复后回绕发生在 4 项内, 永远选不到额外关卡。
    """
    from touhou.games.th07.view import GameApp

    app = GameApp(lambda difficulty, character:
                  StubGame(difficulty=difficulty, character=character))
    app._screen = Screen.DIFFICULTY
    assert app._diff.current == "Normal"          # index 1
    app._diff.move(1)
    app._diff.move(1)
    assert app._diff.current == "Lunatic"         # index 3 = 最后一项
    app._diff.move(1)
    assert app._diff.current == "Easy"            # 回绕到 0(旧 bug: 这里会是 Extra)
    # 确认开局: 难度 int 永远落在 0..3, 到不了 4(Extra)/5(Phantasm)
    app._screen = Screen.DIFFICULTY
    app._on_menu(MenuAction.CONFIRM)              # 难度(Easy) → 角色
    app._on_menu(MenuAction.CONFIRM)              # 角色(默认 ReimuA) → 开始
    assert app._game.kw["difficulty"] == 0


# ---------------------------------------------------------------------------
# Extra Start 入口流(简化: 不设解锁条件, 选机体后可选 Extra/Phantasm)
# ---------------------------------------------------------------------------

class StubExtraGame(StubGame):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.entered = None

    def enter_stage(self, stage_no):  # noqa: D102
        self.entered = stage_no
        self.stage_no = stage_no


def test_extra_start_flow() -> None:
    """Extra Start → 选机体 → Extra → 以难度 4 进 stage 7。"""
    from touhou.games.th07.view import GameApp

    app = GameApp(StubExtraGame)
    app._on_menu(MenuAction.DOWN)      # 主菜单: 开始游戏 → Extra Start
    app._on_menu(MenuAction.CONFIRM)
    assert app._screen == Screen.CHARACTER     # Extra 不选难度, 直接选机体
    app._on_menu(MenuAction.DOWN)      # ReimuA → ReimuB
    app._on_menu(MenuAction.CONFIRM)
    assert app._screen == Screen.EXTRA_LEVEL
    app._on_menu(MenuAction.CONFIRM)   # Extra
    assert app._screen == Screen.PLAYING
    assert app._game.kw["difficulty"] == 4
    assert app._game.kw["character"] == 1
    assert app._game.entered == 7


def test_phantasm_entry_flow() -> None:
    """Extra Start → 选机体 → Phantasm → 以难度 5 进 stage 8; BACK 可回退。"""
    from touhou.games.th07.view import GameApp

    app = GameApp(StubExtraGame)
    app._on_menu(MenuAction.DOWN)
    app._on_menu(MenuAction.CONFIRM)     # Extra Start
    app._on_menu(MenuAction.CONFIRM)     # 机体(默认 ReimuA)
    assert app._screen == Screen.EXTRA_LEVEL
    app._on_menu(MenuAction.BACK)        # 回退到选机体
    assert app._screen == Screen.CHARACTER
    app._on_menu(MenuAction.CONFIRM)
    app._on_menu(MenuAction.DOWN)        # Extra → Phantasm
    app._on_menu(MenuAction.CONFIRM)
    assert app._screen == Screen.PLAYING
    assert app._game.kw["difficulty"] == 5
    assert app._game.entered == 8


# ---------------------------------------------------------------------------
# 结算画面场景流(SDL dummy 驱动, 无真窗口)
# ---------------------------------------------------------------------------

class StubResultGame:
    """打 2 帧就进结算的假游戏。"""

    def __init__(self, **kw):
        from touhou.engine.score_store import ScoreStore

        self.kw = kw
        self.store = ScoreStore()
        self.store.record_play(kw.get("character", 0), kw.get("difficulty", 1))
        self.result = None
        self.ticks = 0

    def tick(self, **kw):  # noqa: D102
        self.ticks += 1
        self.result = {
                "score": 123456, "rating": 42.0, "rank": 0, "cleared": True,
                "clear_percent": 100.0, "difficulty": self.kw["difficulty"],
                "character": self.kw["character"], "stage": 1, "name": "PLAYER",
                "retries": 0, "deaths": 1, "bombs": 2.0, "spellcards": 1,
                "graze": 30, "point_items": 40, "slow_percent": 0.0,
                "high_score": 123456,
            }


def test_result_screen_flow(tmp_path, monkeypatch) -> None:
    """游玩 → game.result 出现 → 切 RESULT; 确认 → 存 score.json → 回主菜单。"""
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    import pygame

    pygame.init()
    pygame.display.set_mode((640, 480))
    from touhou.games.th07.view import GameApp

    app = GameApp(StubResultGame, score_path=tmp_path / "score.json")
    app._on_menu(MenuAction.CONFIRM)   # 开始游戏 → 难度
    app._on_menu(MenuAction.CONFIRM)   # 难度 → 角色
    app._on_menu(MenuAction.CONFIRM)   # 角色 → 游玩
    assert app._screen == Screen.PLAYING
    scr = pygame.display.get_surface()
    keys = pygame.key.get_pressed()
    app._run_game(FrameInput())           # 首帧即出 result
    assert app._screen == Screen.RESULT
    assert app._name_entry is not None  # 入榜(rank=0) → 名字输入态
    # 8 槽各确认('A') → 输满自动跳 END; 再确认 = 完成 → 保存 → 回主菜单
    app._run_result([MenuAction.CONFIRM] * 9)
    assert app._screen == Screen.MAIN_MENU
    assert app._game is None
    from touhou.engine.score_store import ScoreStore

    s = ScoreStore.load(tmp_path / "score.json")
    assert s.plst["play_count"] == 1
    assert s.lsnm == "AAAAAAAA"


# ---------------------------------------------------------------------------
# 结局画面场景流(6 面通关 → ENDING → 确认 → RESULT)
# ---------------------------------------------------------------------------

class StubEndingGame:
    """首帧出结局的假游戏(6 面通关语义)。"""

    def __init__(self, **kw):
        from touhou.engine.ending import EndingData, EndingSegment
        from touhou.engine.score_store import ScoreStore

        self.kw = kw
        self.store = ScoreStore()
        self.result = None
        self.stage_no = 6
        self.character = kw.get("character", 0)
        self.ending = None
        self._ending_data = EndingData(
            character=0, bad=False, path="end00.end",
            segments=[EndingSegment(None, ["ALL CLEAR!!"])])

    def tick(self, **kw):  # noqa: D102
        self.ending = self._ending_data

    def finish_ending(self):  # noqa: D102
        self.ending = None
        self.result = {"score": 999, "rating": 1.0, "rank": 0, "cleared": True,
                       "clear_percent": 100.0, "difficulty": self.kw["difficulty"],
                       "character": self.kw["character"], "stage": 6,
                       "name": "PLAYER", "retries": 0, "deaths": 0,
                       "bombs": 0.0, "spellcards": 0, "graze": 0,
                       "point_items": 0, "slow_percent": 0.0, "high_score": 999}


def test_ending_screen_flow(tmp_path, monkeypatch) -> None:
    """游玩 → game.ending 出现 → 切 ENDING; 确认 → finish_ending → RESULT。"""
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    import pygame

    pygame.init()
    pygame.display.set_mode((640, 480))
    from touhou.games.th07.view import GameApp

    app = GameApp(StubEndingGame, score_path=tmp_path / "score.json")
    app._on_menu(MenuAction.CONFIRM)   # 开始游戏 → 难度
    app._on_menu(MenuAction.CONFIRM)   # 难度 → 角色
    app._on_menu(MenuAction.CONFIRM)   # 角色 → 游玩
    scr = pygame.display.get_surface()
    keys = pygame.key.get_pressed()
    app._run_game(FrameInput())           # 首帧即出 ending
    assert app._screen == Screen.ENDING
    app._run_ending([MenuAction.CONFIRM])  # 看完 → 总结算
    assert app._screen == Screen.RESULT
    assert app._name_entry is not None  # 入榜 → 名字输入态
    app._run_result([MenuAction.CONFIRM] * 9)  # 输满 8 槽 + END 完成
    assert app._screen == Screen.MAIN_MENU


# ---------------------------------------------------------------------------
# Option 设置页场景流(进入/调值实时生效+落盘/退出)
# ---------------------------------------------------------------------------

class _FakeSound:
    def __init__(self):
        self.volumes = []

    def set_volume(self, v):  # noqa: D102
        self.volumes.append(v)


def _goto_option(app):
    from touhou.games.th07.view.screens import MAIN_MENU_ITEMS

    while app._flow.cursor.current != "Option":
        app._on_menu(MenuAction.DOWN)
    assert MAIN_MENU_ITEMS[app._flow.cursor.index] == "Option"
    app._on_menu(MenuAction.CONFIRM)
    assert app._screen == Screen.OPTION


def test_option_enter_and_leave(tmp_path) -> None:
    from touhou.games.th07.view import GameApp

    app = GameApp(lambda **kw: None, config_path=tmp_path / "config.json")
    _goto_option(app)
    app._on_menu(MenuAction.BACK)      # 光标跳到"退出"
    assert app._screen == Screen.OPTION
    app._on_menu(MenuAction.BACK)      # 退出 → 主菜单
    assert app._screen == Screen.MAIN_MENU


def test_option_adjust_applies_and_saves(tmp_path) -> None:
    """BGM/SE 音量调值实时作用于 SoundPlayer 并即时写 config.json。"""
    from touhou.engine.config import GameConfig
    from touhou.games.th07.view import GameApp
    from touhou.engine.view.sound_player import _db_to_gain
    from touhou.schema.sound import SE_VOLUMES

    app = GameApp(lambda **kw: None, config_path=tmp_path / "config.json")
    fake = _FakeSound()
    app._sound.sounds = {0: fake}
    _goto_option(app)
    app._on_menu(MenuAction.LEFT)      # BGM 音量 100 → 90
    assert app._sound._bgm_volume == 0.9
    app._on_menu(MenuAction.DOWN)      # → SE 音量
    app._on_menu(MenuAction.LEFT)      # 100 → 90: SE 独立音量 × 0.9
    assert fake.volumes == [_db_to_gain(SE_VOLUMES[0]) * 0.9]
    cfg = GameConfig.load(tmp_path / "config.json")
    assert cfg.bgm_volume == 90 and cfg.se_volume == 90


def test_option_source_switch_and_scale(tmp_path, monkeypatch) -> None:
    """音源切换调 set_bgm_source + 重播当前曲; 缩放改 self._scale 并 resize。"""
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    import pygame

    pygame.init()
    pygame.display.set_mode((640, 480))
    from touhou.engine.config import GameConfig
    from touhou.games.th07.view import GameApp

    app = GameApp(lambda **kw: None, config_path=tmp_path / "config.json")
    _goto_option(app)
    app._on_menu(MenuAction.DOWN)
    app._on_menu(MenuAction.DOWN)      # → 音源
    app._on_menu(MenuAction.RIGHT)     # wav → midi
    assert app._sound._bgm_source == "midi"
    app._on_menu(MenuAction.DOWN)      # → 窗口缩放
    app._on_menu(MenuAction.RIGHT)     # 2 → 3
    assert app._scale == 3
    cfg = GameConfig.load(tmp_path / "config.json")
    assert cfg.bgm_source == "midi" and cfg.window_scale == 3


def test_option_initial_lives_applied_at_game_start(tmp_path) -> None:
    """config.initial_lives 开局覆写 impl 初始残(difficulty<4)。"""
    from types import SimpleNamespace

    from touhou.games.th07.view import GameApp

    class StubLivesGame(StubGame):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.globals = SimpleNamespace(lives_remaining=3.0)

    app = GameApp(StubLivesGame, config_path=tmp_path / "config.json")
    app._config.initial_lives = 5
    app._on_menu(MenuAction.CONFIRM)   # 开始游戏 → 难度
    app._on_menu(MenuAction.CONFIRM)   # 难度 → 角色
    app._on_menu(MenuAction.CONFIRM)   # 角色 → 游玩
    assert app._game.globals.lives_remaining == 5.0


# ---------------------------------------------------------------------------
# 游戏内暂停(Esc → 冻结 tick; Resume/Retry/Quit to Title)
# ---------------------------------------------------------------------------

def _pause_app(tmp_path, monkeypatch):
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    import pygame

    pygame.init()
    pygame.display.set_mode((640, 480))
    from touhou.games.th07.view import GameApp

    app = GameApp(StubGame, config_path=tmp_path / "config.json")
    app._on_menu(MenuAction.CONFIRM)   # 开始游戏 → 难度
    app._on_menu(MenuAction.CONFIRM)   # 难度 → 角色
    app._on_menu(MenuAction.CONFIRM)   # 角色 → 游玩
    assert app._screen == Screen.PLAYING
    return app, pygame.display.get_surface(), pygame.key.get_pressed()


def test_pause_freezes_tick_and_resume(tmp_path, monkeypatch) -> None:
    app, scr, keys = _pause_app(tmp_path, monkeypatch)
    app._run_game(FrameInput())
    assert app._game.ticks == 1
    app._run_game(FrameInput(esc=True))         # Esc → 暂停
    assert app._paused
    app._run_game(FrameInput())                   # 暂停中 tick 不推进
    app._run_game(FrameInput())
    assert app._game.ticks == 1
    app._run_game(FrameInput(menu_actions=(MenuAction.CONFIRM,)))  # Resume
    assert not app._paused
    app._run_game(FrameInput())                   # 恢复后 tick 继续
    assert app._game.ticks == 2


def test_pause_esc_or_back_resumes(tmp_path, monkeypatch) -> None:
    app, scr, keys = _pause_app(tmp_path, monkeypatch)
    app._run_game(FrameInput(esc=True))
    assert app._paused
    app._run_game(FrameInput(menu_actions=(MenuAction.BACK,)))  # Esc/X → Resume
    assert not app._paused


def test_pause_retry_rebuilds_game(tmp_path, monkeypatch) -> None:
    app, scr, keys = _pause_app(tmp_path, monkeypatch)
    app._run_game(FrameInput())
    old = app._game
    app._run_game(FrameInput(esc=True))
    app._run_game(FrameInput(menu_actions=(MenuAction.DOWN,)))  # → Retry
    app._run_game(FrameInput(menu_actions=(MenuAction.CONFIRM,)))
    assert not app._paused
    assert app._screen == Screen.PLAYING
    assert app._game is not old                # 重开 = 新 game
    assert app._game.ticks == 0
    assert app._game.kw == old.kw              # 同难度同机体


def test_pause_quit_to_title(tmp_path, monkeypatch) -> None:
    app, scr, keys = _pause_app(tmp_path, monkeypatch)
    app._run_game(FrameInput(esc=True))
    while app._pause_cursor.current != "Quit to Title":
        app._run_game(FrameInput(menu_actions=(MenuAction.DOWN,)))
    app._run_game(FrameInput(menu_actions=(MenuAction.CONFIRM,)))
    assert app._screen == Screen.MAIN_MENU
    assert app._game is None
    assert not app._paused


# ---------------------------------------------------------------------------
# GameOver 续关菜单场景流(待续关 → Yes 接着玩 / No 进结算; Extra 不出现菜单)
# ---------------------------------------------------------------------------

class StubContinueGame(StubGame):
    """GameOver 待续关的假游戏: 首帧即 game_over, 透出 continue_available。"""

    def __init__(self, **kw):
        super().__init__(**kw)
        from types import SimpleNamespace

        self.globals = SimpleNamespace(lives_remaining=0.0, num_retries=0)
        self.game_over = False
        self.result = None
        self.max_retries = 3
        self.continued = 0
        self.finalized = 0

    @property
    def continue_available(self):  # noqa: D102
        return (self.game_over and self.result is None
                and self.kw.get("difficulty", 1) < 4
                and self.globals.num_retries < self.max_retries)

    def tick(self, **kw):  # noqa: D102
        super().tick(**kw)
        self.game_over = True
        if self.result is None and not self.continue_available:
            self.finalize_game_over()  # 不可续关: impl 同帧进结算

    def continue_play(self):  # noqa: D102
        self.continued += 1
        self.globals.num_retries += 1
        self.game_over = False

    def finalize_game_over(self):  # noqa: D102
        self.finalized += 1
        self.game_over = False
        self.result = {
            "score": 0, "rating": 0.0, "rank": -1, "cleared": False,
            "clear_percent": 50.0, "difficulty": self.kw["difficulty"],
            "character": self.kw["character"], "stage": 1, "name": "PLAYER",
            "retries": self.globals.num_retries, "deaths": 1, "bombs": 0.0,
            "spellcards": 0, "graze": 0, "point_items": 0,
            "slow_percent": 0.0, "high_score": 100000,
        }


def _continue_app(tmp_path, monkeypatch):
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    import pygame

    pygame.init()
    pygame.display.set_mode((640, 480))
    from touhou.games.th07.view import GameApp

    app = GameApp(StubContinueGame, config_path=tmp_path / "config.json")
    app._on_menu(MenuAction.CONFIRM)   # 开始游戏 → 难度
    app._on_menu(MenuAction.CONFIRM)   # 难度 → 角色
    app._on_menu(MenuAction.CONFIRM)   # 角色 → 游玩
    assert app._screen == Screen.PLAYING
    return app, pygame.display.get_surface(), pygame.key.get_pressed()


def test_continue_menu_yes_resumes(tmp_path, monkeypatch) -> None:
    """GameOver → 续关菜单弹出(默认 Yes); 选 Yes → continue_play 接着玩。"""
    app, scr, keys = _continue_app(tmp_path, monkeypatch)
    app._run_game(FrameInput())           # 首帧即死透 → 续关菜单
    assert app._screen == Screen.PLAYING
    assert app._in_continue and app._continue_cursor.index == 0  # 默认 Yes
    app._run_game(FrameInput(esc=True))  # 续关菜单中 Esc 无效(不开暂停)
    assert not app._paused
    app._run_game(FrameInput(menu_actions=(MenuAction.DOWN,)))
    assert app._continue_cursor.index == 1                     # Yes → No
    app._run_game(FrameInput(menu_actions=(MenuAction.UP,)))
    assert app._continue_cursor.index == 0                     # No → Yes
    app._run_game(FrameInput(menu_actions=(MenuAction.CONFIRM,)))
    g = app._game
    assert g.continued == 1 and g.globals.num_retries == 1
    assert not app._in_continue
    assert app._screen == Screen.PLAYING   # 接着玩(不切结算)


def test_continue_menu_no_goes_result(tmp_path, monkeypatch) -> None:
    """续关菜单选 No → finalize_game_over → 次帧进结算画面。"""
    app, scr, keys = _continue_app(tmp_path, monkeypatch)
    app._run_game(FrameInput())
    app._run_game(FrameInput(menu_actions=(MenuAction.DOWN,)))    # → No
    app._run_game(FrameInput(menu_actions=(MenuAction.CONFIRM,)))
    assert app._game.finalized == 1 and not app._in_continue
    app._run_game(FrameInput())               # result 已填 → 结算
    assert app._screen == Screen.RESULT
    assert app._game.result["retries"] == 0


def test_continue_menu_absent_on_extra(tmp_path, monkeypatch) -> None:
    """Extra/Phantasm 不可续关(difficulty>=4): 不出现菜单, 直接进结算。"""
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    import pygame

    pygame.init()
    pygame.display.set_mode((640, 480))
    from touhou.games.th07.view import GameApp

    app = GameApp(StubContinueGame, config_path=tmp_path / "config.json")
    app._on_menu(MenuAction.DOWN)      # 主菜单: 开始游戏 → Extra Start
    app._on_menu(MenuAction.CONFIRM)
    app._on_menu(MenuAction.CONFIRM)   # 机体(默认 ReimuA)
    app._on_menu(MenuAction.CONFIRM)   # Extra
    assert app._screen == Screen.PLAYING
    scr = pygame.display.get_surface()
    app._run_game(FrameInput())
    assert not app._in_continue            # 无续关菜单
    assert app._screen == Screen.RESULT    # 直接进结算


# ---------------------------------------------------------------------------
# 结算名字输入场景流(入榜 → 输名字 → 保存的名字正确 → 回标题; 未入榜跳过)
# ---------------------------------------------------------------------------

class StubRankedGame:
    """入榜的假游戏: tick 即入榜(store 有真实记录)并出 result。"""

    store = None  # 类级注入点: 预设 store(测 LSNM 带出)

    def __init__(self, **kw):
        from touhou.engine.score_store import ScoreStore

        self.kw = kw
        self.store = type(self).store or ScoreStore()
        self.result = None

    def tick(self, **kw):  # noqa: D102
        from touhou.engine.score_store import make_highscore_record

        ch, dif = self.kw["character"], self.kw["difficulty"]
        rank = self.store.insert_score(make_highscore_record(
            123456, ch, dif, 1, name=self.store.last_name))
        self.result = {
            "score": 123456, "rating": 42.0, "rank": rank, "cleared": True,
            "clear_percent": 100.0, "difficulty": dif, "character": ch,
            "stage": 1, "name": self.store.last_name,
            "retries": 0, "deaths": 1, "bombs": 2.0, "spellcards": 1,
            "graze": 30, "point_items": 40, "slow_percent": 0.0,
            "high_score": 123456,
        }


class StubUnrankedGame(StubRankedGame):
    """未入榜的假游戏: 榜满 10 条且都更高 → insert_score 返回 -1。"""

    def tick(self, **kw):  # noqa: D102
        from touhou.engine.score_store import make_highscore_record

        ch, dif = self.kw["character"], self.kw["difficulty"]
        if not self.store.entries(dif, ch):
            for i in range(10):
                self.store.insert_score(make_highscore_record(
                    999999 - i, ch, dif, 1, name="OLD"))
        super().tick(**kw)  # 123456 < 榜尾 → rank=-1


def _play_to_result(app, scr):
    import pygame

    app._on_menu(MenuAction.CONFIRM)   # 开始游戏 → 难度
    app._on_menu(MenuAction.CONFIRM)   # 难度 → 角色
    app._on_menu(MenuAction.CONFIRM)   # 角色 → 游玩
    app._run_game(FrameInput())  # 首帧即出 result
    assert app._screen == Screen.RESULT


def test_result_name_entry_flow(tmp_path, monkeypatch) -> None:
    """入榜 → 名字输入(初始名=LSNM 默认) → 输 'ZUN' → END 完成 →
    score.json 记录名/LSNM 正确 → 回主菜单。"""
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    import pygame

    pygame.init()
    pygame.display.set_mode((640, 480))
    from touhou.games.th07.view import GameApp

    StubRankedGame.store = None
    app = GameApp(StubRankedGame, score_path=tmp_path / "score.json")
    scr = pygame.display.get_surface()
    _play_to_result(app, scr)
    e = app._name_entry
    assert e is not None and e.cursor == 0
    assert e.name == "PLAYER  "       # 无 LSNM → 默认名补空格, 光标在 'A'
    assert e.selected == 0
    # 输 'Z'(25) 'U'(20) 'N'(13) 覆盖 LSNM 前 3 槽(原版: 初始名=LSNM 逐槽改写)
    app._run_result([MenuAction.DOWN] + [MenuAction.RIGHT] * 9
                    + [MenuAction.CONFIRM])
    app._run_result([MenuAction.LEFT] * 5 + [MenuAction.CONFIRM])
    app._run_result([MenuAction.UP] + [MenuAction.RIGHT] * 9
                    + [MenuAction.CONFIRM])
    assert e.name == "ZUNYER  " and e.cursor == 3
    # 去 END: 行尾(15) → 下 5 行(95) → 确认完成
    app._run_result([MenuAction.RIGHT] * 2 + [MenuAction.DOWN] * 5
                    + [MenuAction.CONFIRM])
    assert app._screen == Screen.MAIN_MENU and app._game is None
    from touhou.engine.score_store import ScoreStore

    s = ScoreStore.load(tmp_path / "score.json")
    ent = s.entries(1, 0)
    assert len(ent) == 1 and ent[0]["name"] == "ZUNYER  "
    assert s.lsnm == "ZUNYER  "       # LSNM 已保存

    # 第二局: 名字输入带出上次输入的名字(LSNM), 光标直接在 END
    StubRankedGame.store = s
    app2 = GameApp(StubRankedGame, score_path=tmp_path / "score.json")
    _play_to_result(app2, scr)
    e2 = app2._name_entry
    assert e2 is not None and e2.name == "ZUNYER  " and e2.selected == 95
    app2._run_result([MenuAction.CONFIRM])  # END 直接完成
    assert app2._screen == Screen.MAIN_MENU
    s2 = ScoreStore.load(tmp_path / "score.json")
    assert [r["name"] for r in s2.entries(1, 0)] == ["ZUNYER  "] * 2
    StubRankedGame.store = None


def test_result_unranked_skips_name_entry(tmp_path, monkeypatch) -> None:
    """未入榜(rank=-1) → 无名字输入态, 确认直接保存回标题(维持原行为)。"""
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    import pygame

    pygame.init()
    pygame.display.set_mode((640, 480))
    from touhou.games.th07.view import GameApp

    StubRankedGame.store = None
    app = GameApp(StubUnrankedGame, score_path=tmp_path / "score.json")
    scr = pygame.display.get_surface()
    _play_to_result(app, scr)
    assert app._game.result["rank"] == -1
    assert app._name_entry is None
    app._run_result([MenuAction.CONFIRM])
    assert app._screen == Screen.MAIN_MENU and app._game is None
    from touhou.engine.score_store import ScoreStore

    s = ScoreStore.load(tmp_path / "score.json")
    assert len(s.entries(1, 0)) == 10  # 未入榜记录未加进去
    assert s.lsnm is None              # 未输名字不写 LSNM
