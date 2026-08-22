"""KeyConfig 键位自定义测试: config keymap 容错 / 编辑流 / 游戏内改键生效。

对照 MainMenu.cpp OnUpdateKeyConfig(:891-1088) + Controller.hpp
(SELECTMENU=ENTER|SHOOT, RETURNMENU=MENU|BOMB)。
"""
from __future__ import annotations

import sys

sys.path.insert(0, r"D:\python_play\Touhou08")

from touhou.engine.config import (DEFAULT_KEYMAP, KEYMAP_ACTIONS,  # noqa: E402
                                  GameConfig)
from touhou.engine.render import FrameInput  # noqa: E402
from touhou.games.th07.view.screens import (KEYCONFIG_ITEMS, KeyConfigFlow,  # noqa: E402
                                        MenuAction, Screen)

# ---------------------------------------------------------------------------
# config keymap 模型
# ---------------------------------------------------------------------------


def test_keymap_defaults() -> None:
    """默认值 = 改造前硬编码键位(方向/WASD 两组, 小键盘 IME 备用)。"""
    cfg = GameConfig()
    assert set(cfg.keymap) == set(KEYMAP_ACTIONS)
    assert cfg.keymap["shoot"] == ["z", "[0]"]
    assert cfg.keymap["bomb"] == ["x", "[1]", "j"]
    assert cfg.keymap["focus"] == ["left shift", "right shift"]
    assert cfg.keymap["skip"] == ["left ctrl", "right ctrl"]
    assert cfg.keymap["up"] == ["up", "w"]
    assert cfg.keymap["left"] == ["left", "a"]


def test_keymap_save_load_roundtrip(tmp_path) -> None:
    p = tmp_path / "config.json"
    cfg = GameConfig()
    cfg.keymap["shoot"] = ["q", "[0]"]
    cfg.save(p)
    got = GameConfig.load(p)
    assert got.keymap == cfg.keymap
    assert got == cfg


def test_keymap_load_missing_keymap_returns_defaults(tmp_path) -> None:
    """旧版 config.json(无 keymap 段) → 全默认。"""
    p = tmp_path / "config.json"
    p.write_text('{"bgm_volume": 50}', encoding="utf-8")
    got = GameConfig.load(p)
    assert got.bgm_volume == 50
    assert got.keymap == DEFAULT_KEYMAP


def test_keymap_from_dict_field_level_fallback() -> None:
    """keymap 逐动作容错: 坏动作回退该动作默认, 好动作保留。"""
    got = GameConfig.from_dict({"keymap": {
        "shoot": ["q", "[0]"],     # 合法保留
        "bomb": "x",               # 非 list → 默认
        "focus": [1, 2],           # 非字符串 → 过滤后空 → 默认
        "skip": [],                # 空表 → 默认
        "up": ["i", ""],           # 空串滤掉, "i" 保留
        "cheat": ["z"],            # 未知动作忽略
    }})
    assert got.keymap["shoot"] == ["q", "[0]"]
    assert got.keymap["bomb"] == DEFAULT_KEYMAP["bomb"]
    assert got.keymap["focus"] == DEFAULT_KEYMAP["focus"]
    assert got.keymap["skip"] == DEFAULT_KEYMAP["skip"]
    assert got.keymap["up"] == ["i"]
    assert "cheat" not in got.keymap


def test_keymap_escape_is_filtered() -> None:
    """Esc 不许当动作键(防锁死): 读入时滤掉; 滤完为空回退默认。"""
    got = GameConfig.from_dict({"keymap": {
        "shoot": ["escape"],
        "bomb": ["escape", "x"],
    }})
    assert got.keymap["shoot"] == DEFAULT_KEYMAP["shoot"]
    assert got.keymap["bomb"] == ["x"]


def test_keymap_non_dict_returns_defaults() -> None:
    got = GameConfig.from_dict({"keymap": [1, 2, 3]})
    assert got.keymap == DEFAULT_KEYMAP


def test_set_keymap_primary() -> None:
    """设主键: 新键排首位, 备用键保留, 重复键去重。"""
    cfg = GameConfig()
    assert cfg.set_keymap_primary("shoot", "q")
    assert cfg.keymap["shoot"] == ["q", "z", "[0]"]
    assert cfg.set_keymap_primary("shoot", "[0]")  # 已有键提前
    assert cfg.keymap["shoot"] == ["[0]", "q", "z"]
    assert not cfg.set_keymap_primary("shoot", "escape")   # Esc 不许
    assert not cfg.set_keymap_primary("nope", "q")         # 未知动作
    assert not cfg.set_keymap_primary("shoot", "")         # 空名
    assert cfg.keymap["shoot"] == ["[0]", "q", "z"]


def test_reset_keymap() -> None:
    cfg = GameConfig()
    cfg.keymap["shoot"] = ["q"]
    cfg.reset_keymap()
    assert cfg.keymap == DEFAULT_KEYMAP


# ---------------------------------------------------------------------------
# KeyConfigFlow 编辑流(纯逻辑)
# ---------------------------------------------------------------------------


def _flow() -> KeyConfigFlow:
    return KeyConfigFlow()


def test_keyconfig_flow_capture_sets_primary() -> None:
    """确认 → 捕获状态; 下一个键设为主键(备用保留); 捕获中菜单动作无效。"""
    f = _flow()
    assert f.cursor.current == "shoot"
    r = f.handle(MenuAction.CONFIRM)
    assert r == {"action": "capture", "item": "shoot"}
    assert f.capturing == "shoot"
    assert f.handle(MenuAction.UP) is None     # 捕获中菜单动作无效
    r = f.capture("q")
    assert r == {"action": "changed", "item": "shoot", "value": "q"}
    assert f.capturing is None
    assert f.config.keymap["shoot"] == ["q", "z", "[0]"]


def test_keyconfig_flow_capture_cancel() -> None:
    """捕获中 Esc/X = 取消, 键位不变。"""
    for cancel_key in ("escape", "x"):
        f = _flow()
        f.handle(MenuAction.CONFIRM)           # 捕获 shoot
        r = f.capture(cancel_key)
        assert r["action"] == "cancel"
        assert f.capturing is None
        assert f.config.keymap["shoot"] == DEFAULT_KEYMAP["shoot"]


def test_keyconfig_flow_reset_to_default() -> None:
    f = _flow()
    f.config.keymap["shoot"] = ["q"]
    while f.cursor.current != "reset":
        f.handle(MenuAction.DOWN)
    r = f.handle(MenuAction.CONFIRM)
    assert r == {"action": "changed", "item": "reset"}
    assert f.config.keymap == DEFAULT_KEYMAP


def test_keyconfig_flow_back_and_quit() -> None:
    """BACK 跳到"返回"; "返回"上 BACK/CONFIRM 退出(同 OptionFlow 语义)。"""
    f = _flow()
    assert f.handle(MenuAction.BACK) is None
    assert f.cursor.current == "back"
    assert f.handle(MenuAction.BACK) == {"action": "quit"}
    f = _flow()
    f.handle(MenuAction.BACK)                  # 光标到"返回"
    assert f.handle(MenuAction.CONFIRM) == {"action": "quit"}


def test_keyconfig_items_layout() -> None:
    """条目 = 8 动作 + reset + back(原版: shoot/bomb/focus/skip/方向 + 复位 + 退出)。"""
    assert KEYCONFIG_ITEMS == ["shoot", "bomb", "focus", "skip",
                               "up", "down", "left", "right",
                               "reset", "back"]


# ---------------------------------------------------------------------------
# GameApp 接入: Option → KeyConfig → 捕获/取消/恢复默认 → 落盘
# ---------------------------------------------------------------------------


def _app(tmp_path):
    from touhou.games.th07.view import GameApp

    return GameApp(lambda **kw: None, config_path=tmp_path / "config.json")


def _enter_keyconfig(app) -> None:
    """主菜单 → Option → Key Config 页。"""
    app._on_menu(MenuAction.DOWN)      # 开始游戏 → ... 光标走到 Option
    while app._flow.cursor.current != "Option":
        app._on_menu(MenuAction.DOWN)
    app._on_menu(MenuAction.CONFIRM)
    assert app._screen == Screen.OPTION
    while app._option_flow.cursor.current != "Key Config":
        app._on_menu(MenuAction.DOWN)
    app._on_menu(MenuAction.CONFIRM)
    assert app._screen == Screen.KEY_CONFIG


def test_app_keyconfig_enter_and_capture(tmp_path) -> None:
    """Option → Key Config → 选 shoot → 捕获 Q → keymap 改动并即时落盘。"""
    import pygame

    app = _app(tmp_path)
    _enter_keyconfig(app)
    assert app._keyconfig_flow.cursor.current == "shoot"
    app._on_menu(MenuAction.CONFIRM)           # 进入"按新键"
    assert app._keyconfig_flow.capturing == "shoot"
    app._keyconfig_capture("q")         # 捕获 Q
    assert app._config.keymap["shoot"] == ["q", "z", "[0]"]
    got = GameConfig.load(tmp_path / "config.json")  # 即时写盘
    assert got.keymap["shoot"] == ["q", "z", "[0]"]
    # 改键后菜单确认跟随 shoot(原版 SELECTMENU=ENTER|SHOOT)
    assert app._renderer._menu_keys[pygame.K_q] == MenuAction.CONFIRM


def test_app_keyconfig_capture_cancel_keeps_binding(tmp_path) -> None:
    import pygame

    app = _app(tmp_path)
    _enter_keyconfig(app)
    app._on_menu(MenuAction.CONFIRM)
    app._keyconfig_capture("escape")    # Esc 取消(不许设成 Esc)
    assert app._config.keymap["shoot"] == DEFAULT_KEYMAP["shoot"]
    app._on_menu(MenuAction.CONFIRM)
    app._keyconfig_capture("x")         # X 取消
    assert app._config.keymap["shoot"] == DEFAULT_KEYMAP["shoot"]


def test_app_keyconfig_reset_and_back(tmp_path) -> None:
    app = _app(tmp_path)
    _enter_keyconfig(app)
    app._config.keymap["shoot"] = ["q"]
    app._rebuild_keymap()
    while app._keyconfig_flow.cursor.current != "reset":
        app._on_menu(MenuAction.DOWN)
    app._on_menu(MenuAction.CONFIRM)           # 恢复默认
    assert app._config.keymap == DEFAULT_KEYMAP
    got = GameConfig.load(tmp_path / "config.json")
    assert got.keymap == DEFAULT_KEYMAP
    app._on_menu(MenuAction.DOWN)              # → back
    app._on_menu(MenuAction.CONFIRM)           # 返回 Option
    assert app._screen == Screen.OPTION


def test_app_menu_antilock_enter_and_escape(tmp_path) -> None:
    """防锁死: 确认键改丢后菜单仍接受 Enter(硬编码), Esc 返回不动。"""
    import pygame

    app = _app(tmp_path)
    app._config.keymap["shoot"] = ["q"]        # Z 不再是确认键
    app._rebuild_keymap()
    assert app._renderer._menu_keys[pygame.K_RETURN] == MenuAction.CONFIRM
    assert app._renderer._menu_keys[pygame.K_ESCAPE] == MenuAction.BACK
    assert app._renderer._menu_keys[pygame.K_q] == MenuAction.CONFIRM
    assert pygame.K_z not in app._renderer._menu_keys
    # 导航键不被动作键覆盖(把 bomb 绑到 ↑ 上, ↑ 仍是 UP)
    app._config.keymap["bomb"] = ["up"]
    app._rebuild_keymap()
    assert app._renderer._menu_keys[pygame.K_UP] == MenuAction.UP


# ---------------------------------------------------------------------------
# 游戏内改键生效(fake keys 注入)
# ---------------------------------------------------------------------------


class _FakeKeys:
    """pygame.key.get_pressed() 同构: 按键码索引。"""

    def __init__(self, pressed=()):
        self.pressed = set(pressed)

    def __getitem__(self, code):
        return code in self.pressed


class _TickGame:
    """记录 tick 输入的假游戏(_run_game 日志路径需要的属性齐全)。"""

    def __init__(self, **kw):
        from types import SimpleNamespace

        self.kw = kw
        self.frame = 0
        self.globals = SimpleNamespace(bombs_remaining=3.0)
        self.bomb = SimpleNamespace(is_in_use=False)
        self.border = SimpleNamespace(has_border=False)
        self.player = SimpleNamespace(fire_time=0)
        self.tick_kw = None

    def tick(self, **kw):
        self.tick_kw = kw


def _game_app(tmp_path, monkeypatch):
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    import pygame

    pygame.init()
    pygame.display.set_mode((640, 480))
    from touhou.games.th07.view import GameApp

    app = GameApp(_TickGame, config_path=tmp_path / "config.json")
    app._on_menu(MenuAction.CONFIRM)   # 开始游戏 → 难度
    app._on_menu(MenuAction.CONFIRM)   # 难度 → 角色
    app._on_menu(MenuAction.CONFIRM)   # 角色 → 游玩
    assert app._screen == Screen.PLAYING
    return app, pygame.display.get_surface()


def test_game_default_bindings_unchanged(tmp_path, monkeypatch) -> None:
    """默认键位行为不变: Z=射击, X=bomb, Shift=focus, 方向/WASD=移动。"""
    import pygame

    app, scr = _game_app(tmp_path, monkeypatch)
    keys = _FakeKeys({pygame.K_z, pygame.K_x, pygame.K_LSHIFT, pygame.K_LEFT})
    app._run_game(FrameInput(held=app._renderer.held_actions(keys)))
    kw = app._game.tick_kw
    assert kw["keys"][0] is True or kw["keys"][0]   # left
    assert kw["keys"][4]                            # focus
    assert kw["keys"][5]                            # shoot
    assert kw["bomb"]


def test_game_rebind_takes_effect(tmp_path, monkeypatch) -> None:
    """改键生效: shoot 主键 → Q 后按 Q 射击; 整表替换后 Z 不再射击。"""
    import pygame

    app, scr = _game_app(tmp_path, monkeypatch)
    app._config.set_keymap_primary("shoot", "q")   # 捕获语义: Q 主键, Z/[0] 留作备用
    app._rebuild_keymap()
    app._run_game(FrameInput(held=app._renderer.held_actions(_FakeKeys({pygame.K_q}))))
    assert app._game.tick_kw["keys"][5]             # Q = shoot
    app._run_game(FrameInput(held=app._renderer.held_actions(_FakeKeys({pygame.K_z}))))
    assert app._game.tick_kw["keys"][5]             # Z 仍是备用键
    app._config.keymap["shoot"] = ["q"]             # 整表替换(如手改 config.json)
    app._rebuild_keymap()
    app._run_game(FrameInput(held=app._renderer.held_actions(_FakeKeys({pygame.K_z}))))
    assert not app._game.tick_kw["keys"][5]         # Z 不再射击
    app._run_game(FrameInput(held=app._renderer.held_actions(_FakeKeys({pygame.K_q}))))
    assert app._game.tick_kw["keys"][5]             # Q 仍射击


def test_game_rebind_movement_and_bomb(tmp_path, monkeypatch) -> None:
    """方向/bomb 改键: 上 → I, bomb → C。"""
    import pygame

    app, scr = _game_app(tmp_path, monkeypatch)
    app._config.set_keymap_primary("up", "i")
    app._config.set_keymap_primary("bomb", "c")
    app._rebuild_keymap()
    app._run_game(FrameInput(held=app._renderer.held_actions(_FakeKeys({pygame.K_i, pygame.K_c}))))
    kw = app._game.tick_kw
    assert kw["keys"][2]                            # up
    assert kw["bomb"]
    app._run_game(FrameInput(held=app._renderer.held_actions(_FakeKeys({pygame.K_UP, pygame.K_x}))))
    kw = app._game.tick_kw
    assert kw["keys"][2]                            # ↑ 仍是备用键
    assert kw["bomb"]                               # X 仍是备用键
