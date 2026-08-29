"""渲染后端解耦测试: registry renderer 维度 + 假 renderer 桩插桩。

桩 renderer(StubRenderer)实现 Renderer 协议的最小面, 记录每次调用;
GameApp(renderer=实例) 直接注入, 不建窗口不碰 pygame display, 验证
应用壳的场景流/状态机与渲染后端完全解耦。
"""

from __future__ import annotations

import sys

sys.path.insert(0, r"D:\python_play\Touhou08")

import pytest  # noqa: E402

from touhou.engine.render import EndingFrame, FrameInput  # noqa: E402
from touhou.games.th07.view.screens import MenuAction, Screen  # noqa: E402
from touhou.registry import (  # noqa: E402
    get_renderer,
    register_renderer,
    registered_renderers,
)


# ---------------------------------------------------------------------------
# registry 渲染后端维度
# ---------------------------------------------------------------------------


def test_pygame_renderer_registered_by_default() -> None:
    """import touhou 即登记默认后端 "pygame"(import 链上的 decorator)。"""
    import touhou  # noqa: F401

    assert "pygame" in registered_renderers()
    from touhou.games.th07.view.pygame_backend import PygameRenderer

    assert get_renderer("pygame") is PygameRenderer


def test_get_renderer_unknown_raises_with_list() -> None:
    import touhou  # noqa: F401

    with pytest.raises(KeyError, match="pygame"):
        get_renderer("moderngl")


def test_register_renderer_roundtrip_and_dup() -> None:
    class _Dummy:
        pass

    name = "_test_dummy"
    try:
        assert register_renderer(name)(_Dummy) is _Dummy  # 原样返回
        assert get_renderer(name) is _Dummy
        with pytest.raises(ValueError, match="重复注册"):
            register_renderer(name)(_Dummy)
    finally:
        from touhou.registry import _RENDERER

        _RENDERER.pop(name, None)


# ---------------------------------------------------------------------------
# 假 renderer 桩(记录调用, 无窗口无 pygame display)
# ---------------------------------------------------------------------------


class StubRenderer:
    """Renderer 协议最小桩: 记录调用序列, poll_input 返回空帧。"""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.keymaps: list[dict] = []
        self.opened = 0
        self.closed = 0

    # ---- 窗口生命周期 ----
    def open(self, *, scale: int) -> None:
        self.opened += 1
        self.calls.append(("open", scale))

    def close(self) -> None:
        self.closed += 1

    def resize(self, screen, scale: int) -> None:
        self.calls.append(("resize", screen, scale))

    def present(self) -> None:
        self.calls.append(("present",))

    # ---- 输入 ----
    def set_keymap(self, keymap) -> None:
        self.keymaps.append(dict(keymap))

    def poll_input(self, *, capturing: bool = False) -> FrameInput:
        return FrameInput()

    # ---- 场景渲染(全部只记录) ----
    def render_title(self, cursor, frame, *, show_unimplemented=False):
        self.calls.append(("title", cursor, frame, show_unimplemented))

    def render_difficulty(self, cursor):
        self.calls.append(("difficulty", cursor))

    def render_character(self, cursor):
        self.calls.append(("character", cursor))

    def render_practice_stage(self, cursor, max_stage, *, difficulty, character):
        self.calls.append(("practice_stage", cursor, max_stage))

    def render_extra(self, cursor):
        self.calls.append(("extra", cursor))

    def render_option(self, flow):
        self.calls.append(("option",))

    def render_keyconfig(self, flow):
        self.calls.append(("keyconfig",))

    def render_player_data(self, flow, store, frame):
        self.calls.append(("player_data", frame))

    def render_music_room(self, flow, frame):
        self.calls.append(("music_room", frame))

    def render_replay_menu(self, flow, frame):
        self.calls.append(("replay_menu", frame))

    def begin_game(self, game, *, character):
        self.calls.append(("begin_game", character))

    def render_game(self, game):
        self.calls.append(("game", getattr(game, "frame", None)))

    def render_pause(self, game, cursor, *, hint=None, confirm=None):
        self.calls.append(("pause", cursor, hint, confirm))

    def render_continue(self, game, cursor, retries_left):
        self.calls.append(("continue", cursor, retries_left))

    def render_result(self, result, frame, *, store, name_entry, replay_save=None):
        self.calls.append(("result", frame, replay_save))

    def render_ending(self, ending, frame):
        self.calls.append(("ending", frame))
        return EndingFrame(finished=False)

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
                "rating": 1.0,
                "rank": -1,
                "cleared": True,
                "clear_percent": 100.0,
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
                "slow_percent": 0.0,
                "high_score": 100,
            }


def _stub_app(tmp_path):
    from touhou.games.th07.view import GameApp

    stub = StubRenderer()
    app = GameApp(
        StubGame,
        config_path=tmp_path / "config.json",
        score_path=tmp_path / "score.json",
        renderer=stub,
    )
    return app, stub


def test_app_accepts_renderer_instance_and_rebuilds_keymap(tmp_path) -> None:
    """renderer=实例 直接注入; 构造即把 config.keymap 推给后端。"""
    app, stub = _stub_app(tmp_path)
    assert app._renderer is stub
    assert len(stub.keymaps) == 1 and "shoot" in stub.keymaps[0]
    app._rebuild_keymap()
    assert len(stub.keymaps) == 2


def test_app_renderer_by_name_via_registry(tmp_path) -> None:
    """renderer="pygame"(默认) 经 registry 解析为 PygameRenderer 实例。"""
    from touhou.games.th07.view import GameApp
    from touhou.games.th07.view.pygame_backend import PygameRenderer

    app = GameApp(lambda **kw: None, config_path=tmp_path / "config.json")
    assert isinstance(app._renderer, PygameRenderer)


def test_stub_renderer_full_scene_flow(tmp_path) -> None:
    """桩后端跑完整场景流: 标题 → 难度 → 角色 → 游玩 → 结算 → 回标题。"""
    app, stub = _stub_app(tmp_path)
    app._run_title_menu((MenuAction.CONFIRM,))  # 开始游戏 → 难度
    assert app._screen == Screen.DIFFICULTY
    app._run_menu((MenuAction.CONFIRM,))  # 难度 → 角色
    app._run_menu((MenuAction.CONFIRM,))  # 角色 → 游玩
    assert app._screen == Screen.PLAYING
    kinds = [c[0] for c in stub.calls if c[0] != "se"]  # 滤掉菜单音效
    assert kinds[:3] == ["title", "difficulty", "character"]
    assert "begin_game" in kinds and "resize" in kinds
    assert ("se", "ok") in stub.calls  # 菜单确认音走后端
    app._run_game(FrameInput())  # 第 1 帧: 渲染对局
    app._run_game(FrameInput())  # 第 2 帧: 出 result → 结算
    assert app._screen == Screen.RESULT
    assert ("game", 1) in stub.calls  # render_game 拿到 game 对象
    app._run_result((MenuAction.CONFIRM,))  # 未入榜: 确认 → Save Replay? 询问
    assert app._screen == Screen.RESULT
    assert app._result_save == "ask"
    assert ("result", 1, None) in stub.calls  # render_result(帧号从 1 起)
    # 询问态选 No → 保存回标题(原版 state 11 BACK → state 2)
    app._run_result((MenuAction.RIGHT, MenuAction.CONFIRM))
    assert app._screen == Screen.MAIN_MENU


def test_stub_renderer_pause_and_continue_paths(tmp_path) -> None:
    """暂停面板/续关菜单的渲染调用也全部落到后端(冻结画面 + 覆盖层)。"""
    app, stub = _stub_app(tmp_path)
    app._on_menu(MenuAction.CONFIRM)
    app._on_menu(MenuAction.CONFIRM)
    app._on_menu(MenuAction.CONFIRM)
    app._run_game(FrameInput(esc=True))  # Esc → 暂停
    assert app._paused
    assert stub.calls[-1][0] == "pause"  # 暂停面板渲染
    app._run_game(FrameInput(menu_actions=(MenuAction.BACK,)))
    assert not app._paused
