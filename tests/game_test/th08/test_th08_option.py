"""th08 Option/KeyConfig 画面测试(C 期) —— 纯逻辑 + 应用壳接线 + 真数据 smoke。

对照 th08-ref TitleScreen.cpp:644-1153(Option)/:1156-1402(KeyConfig);
vm 布局/偏离点见 games/th08/view/option_view.py 与 title_flow.py docstring。
"""

from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402

from touhou.engine.config import GameConfig  # noqa: E402
from touhou.engine.render import FrameInput  # noqa: E402
from touhou.games.th07.view.screens import MenuAction, Screen  # noqa: E402
from touhou.games.th08.view.title_flow import (  # noqa: E402
    KEYCONFIG_HELP_TEXTS,
    KEYCONFIG_ITEMS,
    OPTION_HELP_TEXTS,
    OPTION_ITEMS,
    KeyConfigFlowTh08,
    OptionFlowTh08,
)

from .conftest import needs_data  # noqa: E402
from .test_th08_view import StubGame, StubRenderer  # noqa: E402

pygame.init()


def _option_flow(pc: int = 0, **cfg_kw) -> OptionFlowTh08:
    return OptionFlowTh08(config=GameConfig(**cfg_kw), play_count=pc)


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


def _enter_option(app):
    """主菜单 Option 项(下标 7)确认进 Option 画面。"""
    app._flow.cursor.index = 7
    app._run_title_menu((MenuAction.CONFIRM,))
    assert app._screen == Screen.OPTION


# ---- OptionFlowTh08 纯逻辑 ----


def test_option_items_match_enum_order() -> None:
    """10 项 = TITLE_MENU_ITEM_OPTION_* 枚举序(TitleScreen.cpp:93-106)。"""
    assert OPTION_ITEMS == (
        "Player",
        "Graphic",
        "BGM",
        "Vol",
        "S.E.Vol",
        "Mode",
        "SlowMode",
        "Reset",
        "KeyConfig",
        "Quit",
    )
    assert len(OPTION_HELP_TEXTS) == 10


def test_option_locked_rows_skipped() -> None:
    """Graphic(1)/SlowMode(6)无引擎对应物恒锁定: 光标移动跳过。"""
    flow = _option_flow()
    assert flow.locked(1) and flow.locked(6)
    assert not any(flow.locked(i) for i in (0, 2, 3, 4, 5, 7, 8, 9))
    assert flow.cursor.index == 0
    flow.handle(MenuAction.DOWN)
    assert flow.cursor.index == 2  # 跳过 Graphic
    flow.handle(MenuAction.UP)
    assert flow.cursor.index == 0
    flow.cursor.index = 5
    flow.handle(MenuAction.DOWN)
    assert flow.cursor.index == 7  # 跳过 SlowMode
    flow.handle(MenuAction.UP)
    assert flow.cursor.index == 5


def test_option_max_lives_unlock_tiers() -> None:
    """残机上限按 attemptsTotal 解锁(:826-844): <30→5, <60→6, 否则 7。"""
    assert _option_flow(pc=0).max_lives == 5
    assert _option_flow(pc=29).max_lives == 5
    assert _option_flow(pc=30).max_lives == 6
    assert _option_flow(pc=59).max_lives == 6
    assert _option_flow(pc=60).max_lives == 7
    assert _option_flow(pc=100).max_lives == 7


def test_option_lives_adjust_wraps() -> None:
    """残机左右调值在 [LIVES_MIN=2, max_lives] 内回绕(下限 2 是引擎偏离)。"""
    flow = _option_flow(pc=100, initial_lives=7)
    r = flow.handle(MenuAction.RIGHT)
    assert r == {"action": "changed", "item": "Player", "value": 2, "se": "select"}
    assert flow.config.initial_lives == 2
    r = flow.handle(MenuAction.LEFT)
    assert r["value"] == 7  # 下限再左 → 回绕到上限
    flow2 = _option_flow(pc=0, initial_lives=5)  # 未解锁时上限 5
    r = flow2.handle(MenuAction.RIGHT)
    assert r["value"] == 2


def test_option_volume_step_and_clamp() -> None:
    """音量 ±4 步进 + 0..100 截断(:901-945); 音量行调值无逐次音效。"""
    flow = _option_flow(bgm_volume=50, se_volume=0)
    flow.cursor.index = 3  # Vol
    r = flow.handle(MenuAction.RIGHT)
    assert r == {"action": "changed", "item": "Vol", "value": 54, "se": None}
    flow.config.bgm_volume = 100
    assert flow.handle(MenuAction.RIGHT)["value"] == 100  # 截断不溢出
    flow.cursor.index = 4  # S.E.Vol
    r = flow.handle(MenuAction.LEFT)
    assert r["item"] == "S.E.Vol" and r["value"] == 0  # 下限截断


def test_option_hold_accel() -> None:
    """长按 ±1 连调(:947-988): 按住满 30 帧才起步, 仅音量行响应。"""
    flow = _option_flow(bgm_volume=50)
    flow.cursor.index = 3
    for _ in range(29):
        assert flow.tick_held(left=True, right=False) is None
    assert flow.config.bgm_volume == 50
    r = flow.tick_held(left=True, right=False)  # 第 30 帧起每帧 -1
    assert r == {"action": "changed", "item": "Vol", "value": 49, "se": None}
    assert flow.tick_held(left=False, right=False) is None  # 松开清零
    assert flow.hold_left == 0
    # 非音量行长按无效
    flow.cursor.index = 0
    for _ in range(40):
        assert flow.tick_held(left=True, right=False) is None
    assert flow.config.initial_lives == 3


def test_option_idle_timeout() -> None:
    """3600 帧无输入自动退回(:1086-1092); 有输入(含按住)清零。"""
    flow = _option_flow()
    for _ in range(3599):
        assert flow.tick_idle(False) is False
    assert flow.tick_idle(True) is False  # 有输入清零
    assert flow.idle_frames == 0
    for _ in range(3599):
        flow.tick_idle(False)
    assert flow.tick_idle(False) is True  # 满 3600


def test_option_bgm_toggle_and_mode_wrap() -> None:
    """BGM wav/midi 互换(无 OFF, 偏离); Mode 映射 window_scale 1-3 回绕。"""
    flow = _option_flow(bgm_source="wav")
    flow.cursor.index = 2
    assert flow.handle(MenuAction.RIGHT)["value"] == "midi"
    assert flow.handle(MenuAction.RIGHT)["value"] == "wav"
    flow.config.window_scale = 2
    flow.cursor.index = 5  # Mode
    assert flow.handle(MenuAction.RIGHT)["value"] == 3
    assert flow.handle(MenuAction.RIGHT)["value"] == 1  # 上限右移回绕
    assert flow.handle(MenuAction.LEFT)["value"] == 3  # 下限左移回绕


def test_option_reset_defaults() -> None:
    """Reset(:1095-1104): 残机回 3 + BGM 回 WAV; 音量/窗口不动。"""
    flow = _option_flow(initial_lives=5, bgm_source="midi", bgm_volume=10, se_volume=20)
    flow.cursor.index = 7
    r = flow.handle(MenuAction.CONFIRM)
    assert r == {"action": "reset", "se": "ok"}
    assert flow.config.initial_lives == 3
    assert flow.config.bgm_source == "wav"
    assert flow.config.bgm_volume == 10 and flow.config.se_volume == 20


def test_option_back_jumps_to_quit() -> None:
    """BACK 光标跳 Quit 行(:1130-1145); Quit 行上 BACK/确认 = 退出。"""
    flow = _option_flow()
    r = flow.handle(MenuAction.BACK)
    assert r == {"action": "back", "se": "cancel"}
    assert flow.cursor.index == 9
    assert flow.handle(MenuAction.BACK) == {"action": "quit", "se": "cancel"}
    flow2 = _option_flow()
    flow2.cursor.index = 9
    assert flow2.handle(MenuAction.CONFIRM) == {"action": "quit", "se": "cancel"}


def test_option_keyconfig_entry() -> None:
    """KeyConfig 行确认 → 进子画面(:1105-1110)。"""
    flow = _option_flow()
    flow.cursor.index = 8
    assert flow.handle(MenuAction.CONFIRM) == {"action": "keyconfig", "se": "ok"}


# ---- KeyConfigFlowTh08 纯逻辑 ----


def test_keyconfig_items_and_help() -> None:
    """名单 = 8 动作 + Reset + Quit; 帮助行经 ROW_MAP 映射原作 12 行。"""
    assert KEYCONFIG_ITEMS == [
        "shoot",
        "bomb",
        "focus",
        "skip",
        "up",
        "down",
        "left",
        "right",
        "reset",
        "quit",
    ]
    assert len(KEYCONFIG_HELP_TEXTS) == 12
    flow = KeyConfigFlowTh08(config=GameConfig())
    assert flow.help_text == KEYCONFIG_HELP_TEXTS[0]  # shoot → 原作行 0
    flow.cursor.index = 4  # up → 原作行 5(跳过 4=Pause)
    assert flow.help_text == KEYCONFIG_HELP_TEXTS[5]
    flow.cursor.index = 9  # quit → 原作行 11
    assert flow.help_text == KEYCONFIG_HELP_TEXTS[11]


def test_keyconfig_capture_flow() -> None:
    """确认进捕获态 → capture 落键(即时生效); Esc/X 取消不改键。"""
    cfg = GameConfig()
    flow = KeyConfigFlowTh08(config=cfg)
    r = flow.handle(MenuAction.CONFIRM)
    assert r == {"action": "capture", "item": "shoot", "se": "ok"}
    assert flow.capturing == "shoot"
    # 捕获态下菜单动作无效
    assert flow.handle(MenuAction.DOWN) is None
    r = flow.capture("q")
    assert r == {"action": "changed", "item": "shoot", "value": "q", "se": "ok"}
    assert cfg.keymap["shoot"][0] == "q"
    assert flow.capturing is None
    # Esc / X 取消
    flow.handle(MenuAction.CONFIRM)
    assert flow.capture("escape")["action"] == "cancel"
    flow.handle(MenuAction.CONFIRM)
    assert flow.capture("x")["action"] == "cancel"
    assert cfg.keymap["shoot"][0] == "q"  # 取消不改键


def test_keyconfig_reset_and_back() -> None:
    """Reset 恢复默认 keymap(:1354-1360); BACK 跳 Quit 行(th07 口径)。"""
    cfg = GameConfig()
    cfg.set_keymap_primary("bomb", "q")
    flow = KeyConfigFlowTh08(config=cfg)
    flow.cursor.index = 8  # reset
    r = flow.handle(MenuAction.CONFIRM)
    assert r == {"action": "changed", "item": "reset", "se": "ok"}
    assert cfg.keymap == GameConfig().keymap
    # BACK: 非 Quit 行 → 光标跳 Quit; Quit 行 → 退出
    flow.cursor.index = 0
    r = flow.handle(MenuAction.BACK)
    assert r == {"action": "back", "se": "cancel"}
    assert flow.cursor.current == "quit"
    assert flow.handle(MenuAction.BACK) == {"action": "quit", "se": "cancel"}


def test_keyconfig_idle_timeout() -> None:
    """3600 帧无输入退回 Option(:1345-1347)。"""
    flow = KeyConfigFlowTh08(config=GameConfig())
    for _ in range(3599):
        assert flow.tick_idle(False) is False
    assert flow.tick_idle(False) is True


# ---- 应用壳接线(StubRenderer) ----


def test_option_menu_item_enters_option_screen(tmp_path) -> None:
    """主菜单 Option 项进 Screen.OPTION(C1 实装接线)。"""
    app, stub = _stub_app(tmp_path)
    _enter_option(app)
    app._run_option(FrameInput())
    assert ("option", 0, 0) in stub.calls  # frame==0 = 进屏(进场动画)


def test_app_option_quit_returns_cursor_to_option_item(tmp_path) -> None:
    """Option 退出 → 主菜单光标落 Option 项(:1113)。"""
    app, _ = _stub_app(tmp_path)
    _enter_option(app)
    app._run_option(FrameInput(menu_actions=(MenuAction.BACK,)))  # 光标跳 Quit
    assert app._screen == Screen.OPTION
    assert app._option_flow.cursor.index == 9
    app._run_option(FrameInput(menu_actions=(MenuAction.BACK,)))  # Quit 行 BACK → 退出
    assert app._screen == Screen.MAIN_MENU
    assert app._flow.cursor.index == 7


def test_app_option_play_count_unlocks_lives(tmp_path) -> None:
    """进 Option 时从 score 快照喂 play_count(残机 7 架档解锁)。"""
    from touhou.engine.score_store import ScoreStore

    store = ScoreStore()
    store.plst["play_count"] = 60
    store.save(tmp_path / "score.json")
    app, _ = _stub_app(tmp_path)
    _enter_option(app)
    assert app._option_flow.play_count == 60
    assert app._option_flow.max_lives == 7


def test_app_option_change_persists_to_config(tmp_path) -> None:
    """Option 改值即时落盘: config.json 可读回(th07 口径)。"""
    app, _ = _stub_app(tmp_path)
    _enter_option(app)
    # Player 行(0): RIGHT 调残机 3→4
    app._run_option(FrameInput(menu_actions=(MenuAction.RIGHT,)))
    assert app._config.initial_lives == 4
    assert GameConfig.load(app._config_path).initial_lives == 4


def test_app_option_mode_triggers_resize(tmp_path) -> None:
    """Mode 行调值 → window_scale 即时 resize(偏离原作全屏切换)。"""
    app, stub = _stub_app(tmp_path)
    _enter_option(app)
    app._option_flow.cursor.index = 5
    assert app._config.window_scale == 2  # 默认值(engine/config.py)
    app._run_option(FrameInput(menu_actions=(MenuAction.RIGHT,)))
    assert app._config.window_scale == 3
    assert ("resize", Screen.OPTION, 3) in stub.calls


def test_app_option_hold_adjust_via_input(tmp_path) -> None:
    """音量行长按左键 30 帧后每帧 -1(经 _run_option 的 held 通路)。"""
    app, _ = _stub_app(tmp_path)
    _enter_option(app)
    app._option_flow.cursor.index = 3
    base = app._config.bgm_volume
    inp = FrameInput(held=frozenset({"left"}))
    for _ in range(29):
        app._run_option(inp)
    assert app._config.bgm_volume == base
    app._run_option(inp)
    assert app._config.bgm_volume == base - 1


def test_app_option_idle_timeout_returns_to_menu(tmp_path) -> None:
    """Option 3600 帧无输入 → 回主菜单 + cancel 音 + 光标落 Option 项。"""
    app, stub = _stub_app(tmp_path)
    _enter_option(app)
    app._option_flow.idle_frames = 3599
    app._run_option(FrameInput())
    assert app._screen == Screen.MAIN_MENU
    assert app._flow.cursor.index == 7
    assert ("se", "cancel") in stub.calls


def test_app_option_to_keyconfig_and_back(tmp_path) -> None:
    """Option → KeyConfig(光标 0)→ 退出回 Option 且光标落 KeyConfig 行(:1369)。"""
    app, stub = _stub_app(tmp_path)
    _enter_option(app)
    app._option_flow.cursor.index = 8
    app._run_option(FrameInput(menu_actions=(MenuAction.CONFIRM,)))
    assert app._screen == Screen.KEY_CONFIG
    assert app._keyconfig_flow.cursor.index == 0
    app._run_keyconfig(FrameInput())
    assert ("keyconfig", 0, 0) in stub.calls
    app._run_keyconfig(FrameInput(menu_actions=(MenuAction.BACK,)))  # 跳 Quit 行
    assert app._keyconfig_flow.cursor.current == "quit"
    app._run_keyconfig(FrameInput(menu_actions=(MenuAction.BACK,)))  # 退出
    assert app._screen == Screen.OPTION
    assert app._option_flow.cursor.index == 8


def test_app_keyconfig_capture_applies_and_persists(tmp_path) -> None:
    """捕获改键即时生效(重建后端键表)+ 落盘; 取消路径不动配置。"""
    app, stub = _stub_app(tmp_path)
    _enter_option(app)
    app._option_flow.cursor.index = 8
    app._run_option(FrameInput(menu_actions=(MenuAction.CONFIRM,)))
    app._run_keyconfig(FrameInput(menu_actions=(MenuAction.CONFIRM,)))  # 捕获 shoot
    assert app._keyconfig_flow.capturing == "shoot"
    app._keyconfig_capture("q")
    assert app._config.keymap["shoot"][0] == "q"
    assert stub.keymaps[-1]["shoot"][0] == "q"  # set_keymap 重建
    assert GameConfig.load(app._config_path).keymap["shoot"][0] == "q"
    # 取消路径: 进捕获后 Esc 不改键不重建
    n_keymaps = len(stub.keymaps)
    app._run_keyconfig(FrameInput(menu_actions=(MenuAction.CONFIRM,)))
    app._keyconfig_capture("escape")
    assert app._config.keymap["shoot"][0] == "q"
    assert len(stub.keymaps) == n_keymaps


def test_app_keyconfig_idle_timeout_returns_to_option(tmp_path) -> None:
    """KeyConfig 3600 帧无输入 → 回 Option + 光标落 KeyConfig 行(:1345-1347)。"""
    app, stub = _stub_app(tmp_path)
    _enter_option(app)
    app._option_flow.cursor.index = 8
    app._run_option(FrameInput(menu_actions=(MenuAction.CONFIRM,)))
    app._keyconfig_flow.idle_frames = 3599
    app._run_keyconfig(FrameInput())
    assert app._screen == Screen.OPTION
    assert app._option_flow.cursor.index == 8
    assert ("se", "cancel") in stub.calls


# ---- 真数据 smoke ----


@needs_data
def test_real_option_keyconfig_render() -> None:
    """真实 th08.dat: Option/KeyConfig 贴图视图 + 后端两方法渲染若干帧不炸。"""
    from touhou.games.th08.view import PygameTh08Renderer
    from touhou.paths import DEFAULT_DATA_PATHS

    renderer = PygameTh08Renderer(DEFAULT_DATA_PATHS["th08"])
    renderer.open(scale=1)
    try:
        cfg = GameConfig()
        flow = OptionFlowTh08(config=cfg, play_count=100)
        for i in range(90):
            renderer.render_option(flow, frame=i)
        # 贴图视图加载成功(未回退文字菜单)
        assert renderer._option_view is not None
        kc = KeyConfigFlowTh08(config=cfg)
        kc.capturing = "shoot"  # 捕获态行也画一帧
        for i in range(90):
            renderer.render_keyconfig(kc, frame=i)
        assert renderer._keyconfig_view is not None
    finally:
        renderer.close()
        pygame.init()  # close() 会 pygame.quit(), 恢复以免影响后续用例
