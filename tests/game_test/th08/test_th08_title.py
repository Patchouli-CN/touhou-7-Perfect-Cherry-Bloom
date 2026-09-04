"""th08 标题主菜单(A 期原作化)测试 —— flow 纯逻辑 + 初始光标规则 + 真数据渲染。

对照 th08-ref TitleScreen.cpp OnUpdateStartMenu(:280-643):
9 项名单/锁定项跳过/置灰/确认分发/BACK 跳 Quit/初始光标(wantedState2 分支)。
headless 全覆盖纯逻辑; 贴图渲染路径打 needs_data(真 th08.dat + SDL dummy)。
"""

from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402

from touhou.engine.render import FrameInput  # noqa: E402
from touhou.games.th07.view.screens import MenuAction, Screen  # noqa: E402
from touhou.games.th08.progress import (  # noqa: E402
    load_score_store,
    record_ending_clear,
)
from touhou.games.th08.view.title_flow import (  # noqa: E402
    CURSOR_FROM_GAME,
    CURSOR_FROM_RESULT,
    HELP_TEXTS,
    TITLE_MENU_ITEMS,
    TitleFlowTh08,
    unlock_flags,
)

from .conftest import needs_data  # noqa: E402
from .test_th08_view import StubGame, StubRenderer  # noqa: E402 桩复用

pygame.init()


def _stub_app(tmp_path, score_path=None):
    from touhou.games.th08.view import GameApp

    stub = StubRenderer()
    app = GameApp(
        StubGame,
        config_path=tmp_path / "config.json",
        score_path=score_path or tmp_path / "score.json",
        renderer=stub,
    )
    return app, stub


# ---- 名单与帮助文本(TitleScreen.cpp:79-91 / i18n.csv:129-137) ----
def test_menu_items_are_original_nine() -> None:
    """主菜单 = 原作 9 项, 枚举序(不再复用 th07 的 8 项名单)。"""
    assert list(TITLE_MENU_ITEMS) == [
        "Start",
        "Extra Start",
        "Spell Practice",
        "Practice Start",
        "Replay",
        "Result",
        "Music Room",
        "Option",
        "Quit",
    ]
    assert len(HELP_TEXTS) == len(TITLE_MENU_ITEMS)


# ---- 光标移动: 回绕 + 锁定跳过(:3128-3160 + :383-404) ----
def test_cursor_skips_locked_items() -> None:
    """全新存档(Extra/Spell Practice 未解锁): 光标顺向跳过下标 1/2。"""
    flow = TitleFlowTh08()
    assert flow.cursor.index == 0
    flow.handle(MenuAction.DOWN)  # 0 → 1(锁) → 2(锁) → 3
    assert flow.cursor.index == 3
    flow.handle(MenuAction.UP)  # 3 → 2(锁) → 1(锁) → 0
    assert flow.cursor.index == 0
    flow.handle(MenuAction.UP)  # 0 → 回绕 8(Quit, 不锁)
    assert flow.cursor.index == 8
    flow.handle(MenuAction.DOWN)  # 8 → 回绕 0
    assert flow.cursor.index == 0


def test_cursor_full_range_when_unlocked() -> None:
    """解锁后 1/2 可停, 移动逐项不跳。"""
    flow = TitleFlowTh08(extra_unlocked=True, spell_practice_unlocked=True)
    for expect in range(1, 9):
        flow.handle(MenuAction.DOWN)
        assert flow.cursor.index == expect


# ---- 确认分发(:499-598) 与锁定项确认无效(:531-558 守卫落空) ----
def test_confirm_dispatch() -> None:
    flow = TitleFlowTh08(extra_unlocked=True, spell_practice_unlocked=True)
    expect = {
        0: "select_difficulty",
        1: "extra_start",
        2: "spell_practice",
        3: "practice",
        4: "replay",
        5: "result",
        6: "music_room",
        7: "option",
        8: "quit",
    }
    for idx, action in expect.items():
        flow.cursor.index = idx
        assert flow.handle(MenuAction.CONFIRM) == {"action": action}


def test_confirm_on_locked_item_is_noop() -> None:
    """锁定项确认 = 无操作(无结果, 调用方不播音效)。"""
    flow = TitleFlowTh08()
    flow.cursor.index = 1  # Extra Start 锁定
    assert flow.handle(MenuAction.CONFIRM) is None
    flow.cursor.index = 2  # Spell Practice 锁定
    assert flow.handle(MenuAction.CONFIRM) is None


def test_back_jumps_to_quit() -> None:
    """BACK 光标直接跳到 Quit(:601-608)。"""
    flow = TitleFlowTh08()
    flow.cursor.index = 3
    assert flow.handle(MenuAction.BACK) is None
    assert flow.cursor.index == 8


# ---- 解锁判定(CLRD 位掩码原作语义, Ending.cpp:509 的 flag 写回) ----
def test_unlock_flags(tmp_path) -> None:
    """6A 通关 → Spell Practice(bit15, 判定读 with 表, 含续关也算);
    6B 通关 → Extra(bit14, 判定读 without 表, 只认无续关)。"""
    store = load_score_store(tmp_path / "a.json")  # 缺文件 → 全新
    assert unlock_flags(store) == (False, False)
    record_ending_clear(store, 0, 1, cleared_6b=False, num_retries=0)
    assert unlock_flags(store) == (False, True)  # 6A → Spell Practice 解锁
    store = load_score_store(tmp_path / "b.json")
    record_ending_clear(store, 0, 1, cleared_6b=True, num_retries=0)
    assert unlock_flags(store) == (True, False)  # 6B 无续关 → 只解锁 Extra
    store = load_score_store(tmp_path / "c.json")
    record_ending_clear(store, 0, 1, cleared_6b=True, num_retries=1)
    assert unlock_flags(store) == (False, False)  # 6B 有续关 → Extra 不解锁
    store = load_score_store(tmp_path / "d.json")
    record_ending_clear(store, 0, 1, cleared_6b=False, num_retries=1)
    assert unlock_flags(store) == (False, True)  # 6A 有续关 → Spell 仍解锁


# ---- 初始光标规则(:3682-3698; 只接现有路径能产生的来源) ----
def test_initial_cursor_on_boot(tmp_path) -> None:
    """启动 → 光标 0(Start, :3695-3696 默认分支)。"""
    app, _stub = _stub_app(tmp_path)
    assert app._screen == Screen.MAIN_MENU
    assert app._flow.cursor.index == 0


def test_initial_cursor_after_quit_to_title(tmp_path) -> None:
    """游戏中途 Quit to Title 回来 → 光标 1(A 期规格; :3684-3687)。"""
    app, _stub = _stub_app(tmp_path)
    app._run_title_menu((MenuAction.CONFIRM,))  # Start → 难度
    app._run_menu((MenuAction.CONFIRM,))  # 难度 → 机体
    app._run_menu((MenuAction.CONFIRM,))  # 机体 → 游玩
    app._run_game(FrameInput(esc=True))  # Esc → 暂停
    # 暂停菜单: Resume/Retry/Quit to Title —— 下下选 Quit, 确认进二次确认
    app._run_game(FrameInput(menu_actions=(MenuAction.DOWN, MenuAction.DOWN)))
    app._run_game(FrameInput(menu_actions=(MenuAction.CONFIRM,)))
    # 二次确认默认 No —— 上移到 Yes 再确认
    app._run_game(FrameInput(menu_actions=(MenuAction.UP, MenuAction.CONFIRM)))
    assert app._screen == Screen.MAIN_MENU
    assert app._flow.cursor.index == CURSOR_FROM_GAME == 1
    assert app._title_fade == 0  # 回标题重新白淡入(:3800-3807)


def test_initial_cursor_after_result(tmp_path) -> None:
    """结算回来 → 光标 5(Result 项, :3689-3690)。"""
    app, _stub = _stub_app(tmp_path)
    app._run_title_menu((MenuAction.CONFIRM,))
    app._run_menu((MenuAction.CONFIRM,))
    app._run_menu((MenuAction.CONFIRM,))
    app._run_game(FrameInput())
    app._run_game(FrameInput())  # StubGame 2 帧出 result → 结算
    assert app._screen == Screen.RESULT
    app._run_result((MenuAction.CONFIRM,))  # 确认 → 存档回标题
    assert app._screen == Screen.MAIN_MENU
    assert app._flow.cursor.index == CURSOR_FROM_RESULT == 5


def test_sub_screen_back_keeps_cursor(tmp_path) -> None:
    """标题内子画面往返(难度 BACK)不重置光标、不重新淡入(原作同屏内行为)。"""
    app, _stub = _stub_app(tmp_path)
    app._run_title_menu((MenuAction.DOWN,))  # 0 → 3(顺向跳过锁定的 1/2)
    assert app._flow.cursor.index == 3
    app._run_title_menu((MenuAction.CONFIRM,))  # Practice Start → 难度选(C5: 选难度→选关)
    assert app._screen == Screen.DIFFICULTY
    app._run_menu((MenuAction.BACK,))  # BACK → 主菜单
    assert app._screen == Screen.MAIN_MENU
    app._flow.cursor.index = 0
    app._run_title_menu((MenuAction.CONFIRM,))  # Start → 难度
    assert app._screen == Screen.DIFFICULTY
    app._run_menu((MenuAction.BACK,))  # BACK → 主菜单
    assert app._screen == Screen.MAIN_MENU
    assert app._flow.cursor.index == 0
    # 关键证据: 子画面返回不走 _enter_title_scene, 淡入计数不被清零
    assert app._title_fade > 0


def test_unlocks_reloaded_on_title_entry(tmp_path) -> None:
    """带 6A+6B 通关存档构造 → Extra/Spell Practice 解锁, 光标可停 1/2。"""
    score_path = tmp_path / "score.json"
    store = load_score_store(score_path)
    record_ending_clear(store, 0, 1, cleared_6b=False, num_retries=0)
    record_ending_clear(store, 0, 1, cleared_6b=True, num_retries=0)
    store.save(score_path)
    app, _stub = _stub_app(tmp_path, score_path=score_path)
    assert app._flow.extra_unlocked and app._flow.spell_practice_unlocked
    app._run_title_menu((MenuAction.DOWN,))
    assert app._flow.cursor.index == 1  # Extra Start 可停
    app._run_title_menu((MenuAction.DOWN,))
    assert app._flow.cursor.index == 2  # Spell Practice 可停


# ---- 贴图渲染(真 th08.dat) ----
@needs_data
def test_title_view_real_data() -> None:
    """TitleView 真资源: 10 槽 vm/base sprite 对/置灰/白淡入/帮助行不炸。"""
    from touhou.games.th08.view.title_view import TitleView
    from touhou.paths import DEFAULT_DATA_PATHS

    view = TitleView(DEFAULT_DATA_PATHS["th08"])
    # baseSpriteIndex = 脚本首帧 sprite(AnmManager.cpp:956): 脚本 0..9 →
    # sprite 0(logo) + 菜单 9 项的选中帧 1,3,13,5,7,9,11,15,17
    # (title01.anm 的 sprite 存储 id 全文件连续, 扁平下标按装载序)
    assert view._base_sprites == [0, 1, 3, 13, 5, 7, 9, 11, 15, 17]
    assert view._bg.get_size() == (640, 480)

    flow = TitleFlowTh08()  # 全新存档: Extra/Spell Practice 锁定
    surf = view.render(flow, fade_frame=0)
    assert surf.get_at((0, 0))[:3] == (255, 255, 255)  # 白淡入首帧全白
    for i in range(1, 80):  # 跑过淡入与入场演出
        surf = view.render(flow, fade_frame=i)
    # 选中项(Start) = base sprite, 未选中 = base+1(:349-353)
    assert view._vms[1].vm.active_sprite_idx == 1
    assert view._vms[2].vm.active_sprite_idx == 4  # Extra Start base=3, 未选中 +1
    # 未解锁置灰 0xff404040(:356-365)
    assert view._vms[2].vm.color[:3] == [0x40, 0x40, 0x40]
    assert view._vms[3].vm.color[:3] == [0x40, 0x40, 0x40]
    assert view._vms[1].vm.color[:3] == [255, 255, 255]
    # 光标下移跳过锁定项 → Practice Start(3)选中
    flow.handle(MenuAction.DOWN)
    assert flow.cursor.index == 3
    surf = view.render(flow)
    assert view._vms[4].vm.active_sprite_idx == 5  # Practice Start base=5
    assert view._vms[1].vm.active_sprite_idx == 2  # Start 未选中 = base+1
    # 帮助行/未实装提示渲染不炸(字体缺失环境画方块也算过)
    view.render(flow, show_unimplemented=True)
