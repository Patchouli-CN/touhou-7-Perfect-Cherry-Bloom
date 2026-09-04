"""th08 view 层 smoke 测试 —— SDL dummy 后端跑构造 + 若干帧渲染不炸。

对照 th07 的 test_th07_renderer.py 模式:
- StubRenderer 桩插桩 GameApp 场景流(标题→难度→机体→游玩→结算→回标题),
  不建窗口不碰 display;
- needs_data 标记的真数据用例: PygameTh08Renderer 自持后端构造 +
  GameView/HudView 真实渲染(bg3d 直跑状态钉住)。

本机 mixer 环境失败存量不算账(th07 hud_select 的 BGM smoke 同坑):
SDL_AUDIODRIVER=dummy 下 SoundPlayer 整体静音(ensure_loaded 不炸)。
"""

from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402

from touhou.engine.render import FrameInput  # noqa: E402
from touhou.games.th07.view.screens import MenuAction, Screen  # noqa: E402

from .conftest import needs_data  # noqa: E402

pygame.init()


class StubRenderer:
    """Renderer 协议最小桩: 记录调用序列, poll_input 返回空帧。"""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.keymaps: list[dict] = []
        self.opened = 0
        self.closed = 0

    def open(self, *, scale: int) -> None:
        self.opened += 1
        self.calls.append(("open", scale))

    def close(self) -> None:
        self.closed += 1

    def resize(self, screen, scale: int) -> None:
        self.calls.append(("resize", screen, scale))

    def present(self) -> None:
        self.calls.append(("present",))

    def set_keymap(self, keymap) -> None:
        self.keymaps.append(dict(keymap))

    def poll_input(self, *, capturing: bool = False) -> FrameInput:
        return FrameInput()

    def render_title(self, flow, frame, *, show_unimplemented=False, fade_frame=None):
        self.calls.append(
            ("title", flow.cursor.index, frame, show_unimplemented, fade_frame)
        )

    def render_difficulty(self, cursor, *, items=(), frame=0):
        self.calls.append(("difficulty", cursor, frame))

    def render_character(self, flow, *, completion=None, frame=0):
        self.calls.append(("character", flow.cursor.index, completion, frame))

    def render_extra(self, cursor, *, items=(), frame=0):
        self.calls.append(("extra", cursor, frame))

    def render_option(self, flow, *, frame=0):
        self.calls.append(("option", flow.cursor.index, frame))

    def render_keyconfig(self, flow, *, frame=0):
        self.calls.append(("keyconfig", flow.cursor.index, frame))

    def render_music_room(self, flow, frame=0):
        self.calls.append(("music_room", flow.cursor, frame))

    def render_player_data(self, flow, store=None, frame=0):
        self.calls.append(("player_data", flow.state, flow.cursor, frame))

    def render_replay_menu(self, flow, frame=0):
        self.calls.append(("replay_menu", flow.cursor, frame))

    def render_practice_stage_select(self, flow, title, lines, frame=0):
        self.calls.append(("practice_stage", flow.cursor, frame))

    def render_spell_stage_select(self, flow, title, lines, frame=0):
        self.calls.append(("spell_stage", flow.cursor, flow.character, frame))

    def render_spell_card_select(self, flow, title, info_lines=(), frame=0):
        self.calls.append(("spell_card", flow.cursor, frame))

    def begin_game(self, game, *, character):
        self.calls.append(("begin_game", character))

    def render_game(self, game):
        self.calls.append(("game", getattr(game, "frame", None)))

    def render_pause(self, game, cursor, *, hint=None, confirm=None):
        self.calls.append(("pause", cursor, confirm))

    def render_continue(self, game, cursor, retries_left):
        self.calls.append(("continue", cursor, retries_left))

    def render_result(self, result, frame, *, store=None):
        self.calls.append(("result", frame))

    def play_menu_se(self, key: str) -> None:
        self.calls.append(("se", key))


class StubGame:
    """打 2 帧出 result 的假游戏(场景流用; 属性够应用壳日志路径即可)。"""

    def __init__(self, **kw):
        from touhou.engine.score_store import ScoreStore

        self.kw = kw
        self.store = ScoreStore()
        self.result = None
        self.frame = 0

    def tick(self, **kw):
        self.frame += 1
        if self.frame >= 2:
            self.result = {
                "score": 100,
                "cleared": True,
                "difficulty": self.kw["difficulty"],
                "character": self.kw["character"],
                "stage": 1,
                "name": "PLAYER",
                "retries": 0,
                "deaths": 0,
                "bombs": 0.0,
                "spellcards": 0,
                "graze": 0,
                "point_items": 0,
                "time_orbs": 0,
                "high_score": 100,
            }


def _stub_app(tmp_path):
    from touhou.games.th08.view import GameApp

    stub = StubRenderer()
    app = GameApp(
        StubGame,
        config_path=tmp_path / "config.json",
        score_path=tmp_path / "score.json",
        renderer=stub,
    )
    return app, stub


def test_app_constructs_with_stub_renderer(tmp_path) -> None:
    """renderer=实例 直接注入; 名单取自注册表 th08 表(12 机体/4 本篇难度)。"""
    app, stub = _stub_app(tmp_path)
    assert app._renderer is stub
    assert len(app._characters) == 12  # TH08_DATA (ScoreDat.hpp:54-69)
    assert app._main_difficulties == ["Easy", "Normal", "Hard", "Lunatic"]
    assert app._extra_stages == ["Extra"]


def test_app_default_renderer_is_self_hosted(tmp_path) -> None:
    """renderer 缺省 = 自持 PygameTh08Renderer(不进 register_renderer)。"""
    from touhou.games.th08.view import GameApp, PygameTh08Renderer
    from touhou.registry import registered_renderers

    app = GameApp(lambda **kw: None, config_path=tmp_path / "config.json")
    assert isinstance(app._renderer, PygameTh08Renderer)
    # 渲染后端注册表里没有 th08 后端(全局唯一名 "pygame" 是 th07 的)
    assert "pygame" in registered_renderers()
    from touhou.games.th07.view.pygame_backend import PygameRenderer

    assert type(app._renderer) is not PygameRenderer


def test_stub_renderer_full_scene_flow(tmp_path) -> None:
    """桩后端跑完整场景流: 标题 → 难度 → 角色 → 游玩 → 结算 → 回标题。"""
    app, stub = _stub_app(tmp_path)
    app._run_title_menu((MenuAction.CONFIRM,))  # 开始游戏 → 难度
    assert app._screen == Screen.DIFFICULTY
    app._run_menu((MenuAction.CONFIRM,))  # 难度 → 角色
    app._run_menu((MenuAction.CONFIRM,))  # 角色 → 游玩
    assert app._screen == Screen.PLAYING
    kinds = [c[0] for c in stub.calls if c[0] != "se"]
    assert kinds[:3] == ["title", "difficulty", "character"]
    assert "begin_game" in kinds and "resize" in kinds
    assert ("se", "ok") in stub.calls  # 菜单确认音走后端
    app._run_game(FrameInput())  # 第 1 帧: 渲染对局
    app._run_game(FrameInput())  # 第 2 帧: 出 result → 结算
    assert app._screen == Screen.RESULT
    assert ("game", 1) in stub.calls  # render_game 拿到 game 对象
    app._run_result((MenuAction.CONFIRM,))  # 确认 → 存档回标题
    assert app._screen == Screen.MAIN_MENU


def test_stub_renderer_pause_and_practice_entry(tmp_path) -> None:
    """暂停面板渲染落到后端; Practice 项进练习难度流(C 期第 5 片实装后
    不再是未实装提示)。"""
    app, stub = _stub_app(tmp_path)
    # Practice(th08 9 项名单下标 3) → 难度选择(practice 流)
    app._flow.cursor.index = 3
    app._run_title_menu((MenuAction.CONFIRM,))
    assert app._screen == Screen.DIFFICULTY
    assert app._practice_mode
    app._on_menu(MenuAction.BACK)  # 难度 BACK → 主菜单
    assert app._screen == Screen.MAIN_MENU
    app._practice_mode = False
    app._flow.cursor.index = 0  # 回 "开始游戏"
    app._on_menu(MenuAction.CONFIRM)  # 开始游戏
    app._on_menu(MenuAction.CONFIRM)  # 难度
    app._on_menu(MenuAction.CONFIRM)  # 机体 → 游玩
    app._run_game(FrameInput(esc=True))  # Esc → 暂停
    assert app._paused
    assert stub.calls[-1][0] == "pause"  # 暂停面板渲染
    app._run_game(FrameInput(menu_actions=(MenuAction.BACK,)))
    assert not app._paused


def test_th08_app_registered() -> None:
    """import touhou 即登记 th08 窗口 App 维度(GameApp)。"""
    import touhou  # noqa: F401
    from touhou.games.th08.view import GameApp
    from touhou.registry import get_game

    assert get_game("th08").app is GameApp


@needs_data
def test_real_backend_smoke(tmp_path) -> None:
    """真实 th08.dat + SDL dummy: 自持后端构造 + 真对局渲染若干帧不炸。

    bg3d 直跑状态钉住: stage 1 的 StageScene 加载成功(_bg3d 非 None)
    且渲染不炸(_bg3d_broken 不置位)。
    """
    from touhou.games.th08.view import PygameTh08Renderer
    from touhou.games.th08.view.title_flow import TitleFlowTh08
    from touhou.games.th08.world import ImperishableNight
    from touhou.paths import DEFAULT_DATA_PATHS

    dp = DEFAULT_DATA_PATHS["th08"]
    renderer = PygameTh08Renderer(dp)
    renderer.open(scale=1)
    try:
        # 标题(原作版贴图渲染): 跑过整个 70 帧白淡入 + 菜单入场演出
        flow = TitleFlowTh08()
        for i in range(90):
            renderer.render_title(flow, i, fade_frame=i)
        assert renderer._title_view is not None  # 贴图视图加载成功(未回退文字)
        game = ImperishableNight(data_path=dp, character=0, difficulty=1, seed=1)
        renderer.begin_game(game, character=0)
        for _ in range(200):
            game.tick(keys=(False, False, False, False, False, True), advance=True)
            renderer.render_game(game)
            renderer.present()
        # GameView/HudView 都建出来了(容错降级会留 None)
        assert renderer._game_view is not None
        assert renderer._hud_view is not None
        # bg3d 直跑(schema/stage.py 与 th08 同构; 失败会退回 2D 平铺)
        assert renderer._game_view._bg3d is not None
        assert not renderer._game_view._bg3d_broken
        # 暂停/续关覆盖层 smoke(渲染路径不炸)
        renderer.render_pause(game, 0, confirm=None)
        renderer.render_continue(game, 0, 3)
    finally:
        renderer.close()


@needs_data
def test_window_chain_touhouworld() -> None:
    """TouhouWorld(game="th08") 非 headless 的窗口 App 解析链路通
    (spec.app 命中 GameApp; 不真的弹窗 —— run() 的弹窗由真机验证)。"""
    from touhou.apis.basic import TouhouWorld
    from touhou.games.th08.view import GameApp

    tw = TouhouWorld(game="th08", character="ReimuYukari", headless=False)
    assert tw.spec.app is GameApp
