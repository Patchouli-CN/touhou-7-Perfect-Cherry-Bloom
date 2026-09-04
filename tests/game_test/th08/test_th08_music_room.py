"""th08 Music Room 测试(C 期第 2 片) —— 纯逻辑 + 应用壳接线 + 真数据 smoke。

对照 th08-ref MusicRoom.cpp(ProcessInput :51-238 / OnDraw :318-389 /
AddedCallback :392-553); 行为细节见 games/th08/view/music_flow.py 与
music_view.py 的 docstring。
"""

from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402

from touhou.engine.render import FrameInput  # noqa: E402
from touhou.games.th07.view.screens import MenuAction, Screen  # noqa: E402
from touhou.games.th08.view.music_flow import (  # noqa: E402
    DESC_LINES,
    INPUT_DELAY_FRAMES,
    LOCKED_TITLE,
    LOCKED_WARNING,
    MUSIC_ROOM_VISIBLE,
    MusicRoomFlowTh08,
)
from touhou.schema.musiccmt import TrackDescriptor  # noqa: E402

from .conftest import needs_data  # noqa: E402
from .test_th08_view import StubGame, StubRenderer  # noqa: E402

pygame.init()

_N_TRACKS = 21  # 本机实包曲目数


def _tracks(n: int = _N_TRACKS) -> list[TrackDescriptor]:
    return [
        TrackDescriptor(
            path=f"bgm/th08_{i:02d}.mid",
            title=f"曲{i}",
            comment=tuple(f"c{i}_{k}" for k in range(7)),
        )
        for i in range(n)
    ]


def _flow(n: int = _N_TRACKS, unlocked: bool = True) -> MusicRoomFlowTh08:
    return MusicRoomFlowTh08(tracks=_tracks(n), unlocked=[unlocked] * n)


def _enable(flow: MusicRoomFlowTh08) -> None:
    """走过进场 8 帧输入门(CheckInputEnable :41)。"""
    for _ in range(INPUT_DELAY_FRAMES):
        flow.tick_frame()


def _stub_app(tmp_path):
    from touhou.games.th08.view import GameApp

    return GameApp(
        StubGame,
        config_path=tmp_path / "config.json",
        score_path=tmp_path / "score.json",
        renderer=StubRenderer(),
    )


def _enter_mr(app, monkeypatch=None, tracks=None):
    """主菜单 Music Room 项(下标 6)确认进 Music Room; 可钉曲目表。"""
    if tracks is not None:
        from touhou.games.th08.view import impl as impl_mod

        monkeypatch.setattr(impl_mod, "load_tracks", lambda data_path: tracks)
    app._flow.cursor.index = 6
    app._run_title_menu((MenuAction.CONFIRM,))
    assert app._screen == Screen.MUSIC_ROOM


# ---- MusicRoomFlowTh08 纯逻辑: 输入门 ----


def test_input_gate_first_8_frames() -> None:
    """进场 frameCount<8 不受理输入(CheckInputEnable :41, 只锁进场)。"""
    flow = _flow()
    for _ in range(INPUT_DELAY_FRAMES - 1):
        flow.tick_frame()
        assert flow.handle(MenuAction.DOWN) is None
        assert flow.cursor == 0
    flow.tick_frame()  # 第 8 帧起受理
    assert flow.input_enabled
    flow.handle(MenuAction.DOWN)
    assert flow.cursor == 1
    # 移动清零 frames 但不再回锁(inputState 只进不退)
    assert flow.frames == 0 and flow.input_enabled


# ---- 滚动窗口(:56-67/:107-118) ----


def test_scroll_window_down() -> None:
    """DOWN: 光标出 10 行窗口时 listingOffset 跟随; 底端回绕归零。"""
    flow = _flow()
    _enable(flow)
    for _ in range(MUSIC_ROOM_VISIBLE - 1):  # 光标 1..9, 窗口不动
        flow.handle(MenuAction.DOWN)
        assert flow.listing_offset == 0
    flow.handle(MenuAction.DOWN)  # 光标 10 → offset 1
    assert flow.cursor == 10 and flow.listing_offset == 1
    flow.handle(MenuAction.DOWN)
    assert flow.listing_offset == 2
    for _ in range(_N_TRACKS - 12):  # 到光标 20
        flow.handle(MenuAction.DOWN)
    assert flow.cursor == 20 and flow.listing_offset == 11  # n-10
    flow.handle(MenuAction.DOWN)  # 底端回绕
    assert flow.cursor == 0 and flow.listing_offset == 0


def test_scroll_window_up() -> None:
    """UP: 顶行回绕到底 + offset=n-10; 光标上穿出窗时 offset 跟随光标。"""
    flow = _flow()
    _enable(flow)
    flow.handle(MenuAction.UP)  # 顶行回绕
    assert flow.cursor == _N_TRACKS - 1
    assert flow.listing_offset == _N_TRACKS - MUSIC_ROOM_VISIBLE
    flow.handle(MenuAction.UP)  # 光标 19, 仍在窗内
    assert flow.listing_offset == 11
    for _ in range(9):  # 光标 19→10, offset 逐步跟到 10
        flow.handle(MenuAction.UP)
    assert flow.cursor == 10 and flow.listing_offset == 10
    flow.handle(MenuAction.UP)
    assert flow.cursor == 9 and flow.listing_offset == 9


def test_scroll_window_few_tracks() -> None:
    """曲目不足一屏: offset 恒 0, 回绕正常(:60-63 的 max(0, n-10))。"""
    flow = _flow(5)
    _enable(flow)
    flow.handle(MenuAction.UP)
    assert flow.cursor == 4 and flow.listing_offset == 0
    flow.handle(MenuAction.DOWN)
    assert flow.cursor == 0 and flow.listing_offset == 0


# ---- 按键语义(:190-238) ----


def test_confirm_plays_cursor_track() -> None:
    """SELECTMENU: 播光标曲 + selected/played 落位 + frameCount 清零(:190-209)。"""
    flow = _flow()
    _enable(flow)
    flow.handle(MenuAction.DOWN)
    flow.handle(MenuAction.DOWN)
    flow.frames = 5
    r = flow.handle(MenuAction.CONFIRM)
    assert r == {"action": "play", "index": 2}
    assert flow.selected == 2 and flow.played == 2 and flow.frames == 0
    assert flow.now_playing_line() == "c2_0"  # Now Playing 行 = 简介第 0 行(:374-378)


def test_reset_replays_selected() -> None:
    """RESET: 重播 selected(非光标, :232-238); 未播过时 = 0 号曲(原作 quirk)。"""
    flow = _flow()
    _enable(flow)
    assert flow.handle(MenuAction.RESET) == {"action": "replay", "index": 0}
    assert flow.played is None  # RESET 不更新 Now Playing 行(原作不烘 vm[7])
    flow.handle(MenuAction.DOWN)
    flow.handle(MenuAction.CONFIRM)  # 播 1 号
    flow.handle(MenuAction.DOWN)  # 光标移到 2
    assert flow.handle(MenuAction.RESET) == {"action": "replay", "index": 1}


def test_skip_fadeout_and_back_quit() -> None:
    """SKIP → FadeOutMusic(8.0)(:228-230); BOMB/MENU → 回标题(:212-219)。"""
    flow = _flow()
    _enable(flow)
    assert flow.handle(MenuAction.SKIP) == {"action": "fadeout"}
    assert flow.handle(MenuAction.BACK) == {"action": "quit"}
    # LEFT/RIGHT 无效
    flow2 = _flow()
    _enable(flow2)
    assert flow2.handle(MenuAction.LEFT) is None
    assert flow2.handle(MenuAction.RIGHT) is None


def test_empty_tracks() -> None:
    """空曲目表(资源缺失容错): 只有 BACK 有效。"""
    flow = MusicRoomFlowTh08()
    _enable(flow)
    assert flow.handle(MenuAction.DOWN) is None
    assert flow.handle(MenuAction.CONFIRM) is None
    assert flow.handle(MenuAction.BACK) == {"action": "quit"}


# ---- 解锁隐藏(:522-536/:162-169) ----


def test_locked_track_hides_title_and_comment() -> None:
    """未解锁: 曲名占位 LOCKED_TITLE(:534), 简介换警告文(:166-168)。"""
    flow = _flow(unlocked=False)
    assert flow.display_title(0) == LOCKED_TITLE
    assert flow.display_title(5) == LOCKED_TITLE
    # 光标 0 == selected 0(初值) → 0 号曲即使锁定也显示真简介(原作 quirk)
    assert flow.description_lines()[0] == "c0_0"
    flow.cursor = 1
    lines = flow.description_lines()
    assert lines == tuple(LOCKED_WARNING[:DESC_LINES])
    # 解锁后恢复真名/真简介
    flow.unlocked[1] = True
    assert flow.display_title(1) == "曲1"
    assert flow.description_lines()[0] == "c1_0"


def test_played_locked_track_shows_real_comment() -> None:
    """锁定曲被播放后: 光标在它上面时 selected==cursor → 显示真简介(:162)。"""
    flow = _flow(unlocked=False)
    _enable(flow)
    flow.handle(MenuAction.DOWN)  # 光标 1
    flow.handle(MenuAction.CONFIRM)  # 播 1 号(selected=1)
    assert flow.description_lines()[0] == "c1_0"
    flow.handle(MenuAction.DOWN)  # 光标 2(未解锁且非 selected)
    assert flow.description_lines()[0] == LOCKED_WARNING[0]


# ---- 应用壳接线(StubRenderer) ----


def test_app_enter_and_leave_music_room(tmp_path, monkeypatch) -> None:
    """主菜单 Music Room 项进 Screen.MUSIC_ROOM; BACK 回标题光标落 6
    (wantedState2 规则, TitleScreen.cpp:3692-3693)。"""
    app = _stub_app(tmp_path)
    _enter_mr(app, monkeypatch, _tracks())
    stub = app._renderer
    assert ("se", "ok") in stub.calls
    flow = app._music_flow
    assert len(flow.tracks) == _N_TRACKS and not flow.input_enabled
    for _ in range(INPUT_DELAY_FRAMES):
        app._run_music_room(FrameInput())
    assert ("music_room", 0, 0) in stub.calls  # frame==0 = 进屏
    app._run_music_room(FrameInput(menu_actions=(MenuAction.DOWN,)))
    assert flow.cursor == 1
    app._run_music_room(FrameInput(menu_actions=(MenuAction.BACK,)))
    assert app._screen == Screen.MAIN_MENU
    assert app._flow.cursor.index == 6


def test_app_play_unlocks_bgm_and_persists(tmp_path, monkeypatch) -> None:
    """播曲即置位 bgmUnlocked(Supervisor.cpp:1617/:1632)并落盘 score.json。"""
    app = _stub_app(tmp_path)
    _enter_mr(app, monkeypatch, _tracks())
    flow = app._music_flow
    assert not flow.unlocked[3]
    for _ in range(INPUT_DELAY_FRAMES):
        app._run_music_room(FrameInput())
    flow.cursor = 3
    app._run_music_room(FrameInput(menu_actions=(MenuAction.CONFIRM,)))
    assert flow.played == 3
    from touhou.games.th08.progress import load_score_store

    store = load_score_store(tmp_path / "score.json")
    assert store.plst["bgmUnlocked"][3] == 1
    assert store.plst["bgmUnlocked"][2] == 0  # 不误置邻位


def test_app_skip_and_reset_paths(tmp_path, monkeypatch) -> None:
    """SKIP 淡出/RESET 重播: dummy 声卡下静音不炸, 画面不动。"""
    app = _stub_app(tmp_path)
    _enter_mr(app, monkeypatch, _tracks())
    for _ in range(INPUT_DELAY_FRAMES):
        app._run_music_room(FrameInput())
    app._run_music_room(FrameInput(menu_actions=(MenuAction.SKIP,)))
    assert app._screen == Screen.MUSIC_ROOM
    app._run_music_room(FrameInput(menu_actions=(MenuAction.RESET,)))
    assert app._screen == Screen.MUSIC_ROOM
    assert app._music_flow.played is None  # RESET 不算"播过"(Now Playing 行不更新)


# ---- 真数据 smoke ----


@needs_data
def test_real_musiccmt_parsed() -> None:
    """真实 th08.dat: musiccmt.txt(edz 解密)解出 21 首, 首曲标题钉住。"""
    from touhou.games.th08.view.music_flow import load_tracks
    from touhou.paths import DEFAULT_DATA_PATHS

    tracks = load_tracks(DEFAULT_DATA_PATHS["th08"])
    assert len(tracks) == 21
    assert tracks[0].path == "bgm/th08_01.mid"
    assert tracks[0].title == "永夜抄\u3000〜 Eastern Night."
    assert tracks[0].file_name == "th08_01.mid"


@needs_data
def test_real_music_room_render() -> None:
    """真实 th08.dat: MusicRoomView 贴图渲染 + 后端 render_music_room 若干帧不炸。"""
    from touhou.games.th08.view import PygameTh08Renderer
    from touhou.games.th08.view.music_flow import load_tracks
    from touhou.paths import DEFAULT_DATA_PATHS

    renderer = PygameTh08Renderer(DEFAULT_DATA_PATHS["th08"])
    renderer.open(scale=1)
    try:
        tracks = load_tracks(DEFAULT_DATA_PATHS["th08"])
        # 全锁定(占位曲名 + 警告文)与全解锁(真名/真简介)两种快照都画
        for unlocked in (False, True):
            flow = MusicRoomFlowTh08(tracks=tracks, unlocked=[unlocked] * len(tracks))
            for i in range(30):  # 跑过进场淡入 + 简介逐行重绘
                renderer.render_music_room(flow, i)
                flow.tick_frame()
            flow.handle(MenuAction.DOWN)
            flow.handle(MenuAction.CONFIRM)  # Now Playing 行也画
            for i in range(30, 60):
                renderer.render_music_room(flow, i)
                flow.tick_frame()
        assert renderer._music_view is not None  # 贴图视图加载成功(未回退文字)
    finally:
        renderer.close()
        pygame.init()  # close() 会 pygame.quit(), 恢复以免影响后续用例
