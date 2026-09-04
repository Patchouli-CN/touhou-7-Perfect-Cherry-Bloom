"""th08 Replay 菜单测试(C 期第 4 片) —— 纯逻辑 + 应用壳接线 + 真数据 smoke。

对照 th08-ref TitleScreen.cpp OnUpdateReplayMenu(:3213-3548)/DrawReplayMenu
(:2550-2677); 行为细节见 games/th08/view/replay_flow.py 与 replay_view.py
的 docstring。回放接线用合成 JSON 录像(engine/replay.py 格式) + StubGame。
"""

from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402

from touhou.engine import replay as replay_mod  # noqa: E402
from touhou.engine.render import FrameInput  # noqa: E402
from touhou.games.th07.view.screens import MenuAction, Screen  # noqa: E402
from touhou.games.th08.view.replay_flow import (  # noqa: E402
    INPUT_GATE_FRAMES,
    MAX_REPLAYS,
    REPLAYS_PER_PAGE,
    ReplayFlowTh08,
    entry_line,
    entry_tag,
    scan_replays,
)

from .conftest import needs_data  # noqa: E402
from .test_th08_view import StubGame, StubRenderer  # noqa: E402

pygame.init()


def _entry(name: str, **meta) -> dict:
    """合成列表条目(不真的落盘; meta 缺省值同 make_meta)。"""
    from pathlib import Path

    m = {
        "difficulty": 1,
        "character": 0,
        "stage": 1,
        "seed": 1,
        "initial_lives": 3,
        "frames": 10,
        "created": "2026-09-03T12:00:00+08:00",
    }
    m.update(meta)
    return {"path": Path(name), "meta": m}


def _flow(n: int = 3) -> ReplayFlowTh08:
    return ReplayFlowTh08(
        entries=[_entry(f"th8_ud26090000000{i:02d}.json") for i in range(n)]
    )


def _enable(flow: ReplayFlowTh08) -> None:
    """走过进场 10 帧输入门(确认/返回门, :3344-3347)。"""
    for _ in range(INPUT_GATE_FRAMES):
        flow.tick_frame()


def _write_replay(path, frames: list[int], **meta) -> None:
    """合成 JSON 录像落盘(engine/replay.py 格式)。"""
    m = {"difficulty": 1, "character": 0, "stage": 1, "seed": 1, "initial_lives": 3}
    m.update(meta)
    rec = replay_mod.ReplayRecorder(replay_mod.make_meta(**m))
    for code in frames:
        keys, bomb, adv, skip = replay_mod.decode_input(code)
        rec.record(keys, bomb, adv, skip)
    rec.save(path)


def _stub_app(tmp_path):
    from touhou.games.th08.view import GameApp

    replay_dir = tmp_path / "replays"
    replay_dir.mkdir()
    return GameApp(
        StubGame,
        config_path=tmp_path / "config.json",
        score_path=tmp_path / "score.json",
        replay_dir=replay_dir,
        renderer=StubRenderer(),
    )


def _enter_replay(app):
    """主菜单 Replay 项(下标 4)确认进 Replay 菜单。"""
    app._flow.cursor.index = 4
    app._run_title_menu((MenuAction.CONFIRM,))
    assert app._screen == Screen.REPLAY
    for _ in range(INPUT_GATE_FRAMES):
        app._run_replay_menu(FrameInput())


class _StubGameLong(StubGame):
    """不出 result 的假游戏(回放喂输入/播完路径用; tick 记录实参)。"""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.seen: list[dict] = []

    def tick(self, **kw):
        self.frame += 1
        self.seen.append(kw)


# ---- ReplayFlowTh08 纯逻辑: 输入门(:3344-3347, 移动不受门控) ----


def test_input_gate_confirm_back_only() -> None:
    """进场 10 帧内确认/返回不受理, 光标移动即时受理(:3321 在门检查之前)。"""
    flow = _flow()
    assert flow.handle(MenuAction.CONFIRM) is None
    assert flow.handle(MenuAction.BACK) is None
    assert flow.handle(MenuAction.DOWN) == {"action": "move", "se": "select"}
    assert flow.cursor == 1
    _enable(flow)
    assert flow.handle(MenuAction.BACK) == {"action": "quit", "se": "cancel"}


def test_move_wraps() -> None:
    """UP/DOWN 回绕(MoveCursorVertical :3321)。"""
    flow = _flow(3)
    flow.handle(MenuAction.UP)
    assert flow.cursor == 2
    flow.handle(MenuAction.DOWN)
    assert flow.cursor == 0


def test_paging_only_when_over_one_page() -> None:
    """LEFT/RIGHT 整页 ±15 回绕, 仅当总数超一页(:3323-3341)。"""
    flow = _flow(40)
    flow.handle(MenuAction.RIGHT)
    assert flow.cursor == 15 and flow.page_start == 15
    flow.handle(MenuAction.RIGHT)
    assert flow.cursor == 30 and flow.page_start == 30
    flow.handle(MenuAction.RIGHT)  # 45 >= 40 → 回绕 5
    assert flow.cursor == 5
    flow.handle(MenuAction.LEFT)  # 5 - 15 < 0 → 回绕 30
    assert flow.cursor == 30
    small = _flow(REPLAYS_PER_PAGE)
    assert small.handle(MenuAction.RIGHT) is None  # 不足一页不翻页
    assert small.cursor == 0


def test_empty_entries() -> None:
    """空列表: 移动/确认无效, 过门后仅 BACK 有效(:3350-3353 空档不确认)。"""
    flow = ReplayFlowTh08()
    _enable(flow)
    assert flow.handle(MenuAction.DOWN) is None
    assert flow.handle(MenuAction.CONFIRM) is None
    assert flow.handle(MenuAction.BACK) == {"action": "quit", "se": "cancel"}


def test_confirm_returns_play() -> None:
    """CONFIRM → play 光标档(JSON 路线无选面/播放方式, 直接从头播放)。"""
    flow = _flow()
    _enable(flow)
    flow.handle(MenuAction.DOWN)
    assert flow.handle(MenuAction.CONFIRM) == {"action": "play", "index": 1, "se": "ok"}


# ---- 目录扫描/行格式 ----


def test_scan_filters_prefix_and_bad_files(tmp_path) -> None:
    """只收 th8_*.json(原作只枚举 th8_NN.rpy/th8_ud????.rpy, :3245-3308);
    th7_* 与坏档跳过; 按文件名序。"""
    d = tmp_path / "replays"
    d.mkdir()
    _write_replay(d / "th8_ud26090312000000.json", [0, 0])
    _write_replay(d / "th8_01.json", [0], stage=9, difficulty=4)
    _write_replay(d / "th7_ud26090312000001.json", [0])
    (d / "th8_bad.json").write_bytes(b"not json")
    entries = scan_replays(d)
    names = [e["path"].name for e in entries]
    assert names == ["th8_01.json", "th8_ud26090312000000.json"]


def test_scan_caps_at_max(tmp_path) -> None:
    """超 TITLE_MAX_REPLAYS=60 截断(:3312 numReplays>60 截断)。"""
    d = tmp_path / "replays"
    d.mkdir()
    for i in range(MAX_REPLAYS + 5):
        _write_replay(d / f"th8_ud260903{i:08d}.json", [0])
    assert len(scan_replays(d)) == MAX_REPLAYS


def test_entry_tag_and_line() -> None:
    """行首编号: 固定槽命名 → "No.NN", 用户命名 → "User "(:3263/:3297);
    行文本含日期/机体/难度列(:2581)。"""
    assert entry_tag(_entry("th8_07.json")) == "No.07"
    assert entry_tag(_entry("th8_ud26090312000000.json")) == "User "
    line = entry_line(_entry("th8_01.json", character=3, difficulty=2, stage=9))
    assert "No.01" in line and "2026-09-03" in line
    assert "Ym & Yy" in line and "Hard" in line
    # 越界下标容错(坏 meta 不炸)
    line = entry_line(_entry("th8_01.json", character=99, difficulty=9))
    assert "??????" in line and "????????" in line


# ---- 应用壳接线(StubRenderer) ----


def test_replay_no_longer_unsupported() -> None:
    """主菜单 9 项全部实装(C 期第 5 片后 _UNSUPPORTED_ACTIONS 已拆除)。"""
    import touhou.games.th08.view.impl as impl_mod

    assert not hasattr(impl_mod, "_UNSUPPORTED_ACTIONS")


def test_app_enter_and_leave_replay_menu(tmp_path) -> None:
    """主菜单 Replay 项进 Screen.REPLAY; BACK 回标题光标落 4
    (state 4 → StartMenu cursor=TITLE_MENU_ITEM_START_REPLAY, :3532-3537)。"""
    app = _stub_app(tmp_path)
    _enter_replay(app)
    stub = app._renderer
    assert ("se", "ok") in stub.calls
    assert ("replay_menu", 0, 0) in stub.calls  # frame==0 = 进屏
    app._run_replay_menu(FrameInput(menu_actions=(MenuAction.BACK,)))
    assert app._screen == Screen.MAIN_MENU
    assert app._flow.cursor.index == 4


def test_app_empty_list_confirm_stays(tmp_path) -> None:
    """空目录: 确认不播放(:3350-3353), 留在列表。"""
    app = _stub_app(tmp_path)
    _enter_replay(app)
    app._run_replay_menu(FrameInput(menu_actions=(MenuAction.CONFIRM,)))
    assert app._screen == Screen.REPLAY
    assert app._playback is None


def test_app_playback_full_run(tmp_path) -> None:
    """确认播放: 按 meta 重建对局(难度/机体/种子), 逐帧喂录像输入;
    出结算 → 回标题光标 0( FinishReplay → wantedState2=GameManager 分支,
    TitleScreen.cpp:3684-3687)。"""
    app = _stub_app(tmp_path)
    _write_replay(
        app._replay_dir / "th8_ud26090312000000.json",
        [1, 1, 0, 0, 0],
        character=2,
        difficulty=2,
        seed=4321,
    )
    _enter_replay(app)
    app._run_replay_menu(FrameInput(menu_actions=(MenuAction.CONFIRM,)))
    assert app._screen == Screen.PLAYING
    assert app._playback is not None
    # 对局按录像 meta 重建(StubGame.kw = make_game 实参)
    assert app._game.kw["difficulty"] == 2 and app._game.kw["character"] == 2
    assert app._char_flow.cursor.index == 2 and app._diff.index == 2
    app._run_game(FrameInput())  # StubGame 第 1 帧
    app._run_game(FrameInput())  # 第 2 帧出 result → 播完回标题
    assert app._screen == Screen.MAIN_MENU
    assert app._playback is None and app._game is None
    assert app._flow.cursor.index == 0


def test_app_playback_feeds_recorded_inputs(tmp_path) -> None:
    """逐帧喂录像输入: tick 实参 = decode_input(录像帧)。"""
    from touhou.games.th08.view import GameApp

    replay_dir = tmp_path / "replays"
    replay_dir.mkdir()
    _write_replay(replay_dir / "th8_ud26090312000000.json", [1, 1, 0])
    app = GameApp(
        _StubGameLong,
        config_path=tmp_path / "config.json",
        score_path=tmp_path / "score.json",
        replay_dir=replay_dir,
        renderer=StubRenderer(),
    )
    _enter_replay(app)
    app._run_replay_menu(FrameInput(menu_actions=(MenuAction.CONFIRM,)))
    app._run_game(FrameInput())
    app._run_game(FrameInput())
    app._run_game(FrameInput())
    game = app._game
    keys_l = replay_mod.decode_input(1)[0]
    keys_0 = replay_mod.decode_input(0)[0]
    assert [s["keys"] for s in game.seen] == [keys_l, keys_l, keys_0]
    app._run_game(FrameInput())  # 输入耗尽 → 播完回标题
    assert app._screen == Screen.MAIN_MENU
    assert app._playback is None


def test_app_playback_esc_aborts(tmp_path) -> None:
    """播放中 Esc = 中止回标题(不进暂停菜单), 光标 0。"""
    app = _stub_app(tmp_path)
    _write_replay(app._replay_dir / "th8_ud26090312000000.json", [0] * 100)
    _enter_replay(app)
    app._run_replay_menu(FrameInput(menu_actions=(MenuAction.CONFIRM,)))
    app._run_game(FrameInput())
    assert app._screen == Screen.PLAYING
    app._run_game(FrameInput(esc=True))
    assert app._screen == Screen.MAIN_MENU
    assert app._playback is None and not app._paused
    assert app._flow.cursor.index == 0


def test_app_playback_extra_cursor_1(tmp_path) -> None:
    """Extra 录像(stage=9)播完回标题光标 1(difficulty>=EXTRA 分支,
    TitleScreen.cpp:3684-3687)。"""
    from touhou.games.th08.view import GameApp

    replay_dir = tmp_path / "replays"
    replay_dir.mkdir()
    _write_replay(replay_dir / "th8_ud26090312000000.json", [0], stage=9, difficulty=4)
    app = GameApp(
        _StubGameLong,
        config_path=tmp_path / "config.json",
        score_path=tmp_path / "score.json",
        replay_dir=replay_dir,
        renderer=StubRenderer(),
    )
    _enter_replay(app)
    app._run_replay_menu(FrameInput(menu_actions=(MenuAction.CONFIRM,)))
    assert app._screen == Screen.PLAYING
    assert app._game.kw["difficulty"] == 4
    app._run_game(FrameInput())
    app._run_game(FrameInput())  # 输入耗尽 → 播完
    assert app._screen == Screen.MAIN_MENU
    assert app._flow.cursor.index == 1


def test_app_unsupported_stage_stays(tmp_path) -> None:
    """起始关非 1/Extra 的录像: 警告 + 留在列表(JSON 路线无逐面数据)。"""
    app = _stub_app(tmp_path)
    _write_replay(app._replay_dir / "th8_ud26090312000000.json", [0], stage=3)
    _enter_replay(app)
    app._run_replay_menu(FrameInput(menu_actions=(MenuAction.CONFIRM,)))
    assert app._screen == Screen.REPLAY
    assert app._playback is None


def test_app_corrupt_replay_stays(tmp_path) -> None:
    """坏档确认: 加载失败留在列表(load_replay ValueError 容错)。"""
    app = _stub_app(tmp_path)
    (app._replay_dir / "th8_ud26090312000000.json").write_bytes(b"garbage")
    app._enter_replay_menu()
    # 坏档被 scan 滤掉(load_replay 头校验) → 空列表
    assert app._replay_flow.entries == []


# ---- 真数据 smoke ----


@needs_data
def test_real_world_replay_deterministic() -> None:
    """真实 th08.dat: 同种子两实例, 一边录一边播 600 帧合成输入,
    帧数/分数/自机位置/擦弹逐位一致(JSON 录像路线确定性打通)。"""
    from touhou.games.th08.world import ImperishableNight
    from touhou.paths import DEFAULT_DATA_PATHS

    dp = DEFAULT_DATA_PATHS["th08"]
    codes = [(i * 37 + 11) % 64 | (64 if i % 97 == 0 else 0) for i in range(600)]

    def _run():
        g = ImperishableNight(data_path=dp, character=0, difficulty=1, seed=77)
        for c in codes:
            keys, bomb, adv, skip = replay_mod.decode_input(c)
            g.tick(keys=keys, bomb=bomb, advance=adv, skip=skip)
        return g

    g1, g2 = _run(), _run()
    assert g1.frame == g2.frame
    assert g1.globals.gui_score == g2.globals.gui_score
    assert tuple(g1.player.pos) == tuple(g2.player.pos)
    assert g1.globals.graze_in_total == g2.globals.graze_in_total
    assert g1.globals.deaths == g2.globals.deaths


@needs_data
def test_real_replay_menu_render() -> None:
    """真实 th08.dat: ReplayMenuView 贴图渲染 + 后端 render_replay_menu
    (有/无录像两种快照)若干帧不炸。"""
    from touhou.games.th08.view import PygameTh08Renderer
    from touhou.paths import DEFAULT_DATA_PATHS

    renderer = PygameTh08Renderer(DEFAULT_DATA_PATHS["th08"])
    renderer.open(scale=1)
    try:
        entries = [
            _entry("th8_01.json"),
            _entry("th8_ud26090312000000.json", character=5, difficulty=3, stage=9),
        ]
        for flow in (ReplayFlowTh08(), ReplayFlowTh08(entries=entries)):
            for i in range(20):
                renderer.render_replay_menu(flow, i)
                flow.tick_frame()
            flow.handle(MenuAction.DOWN)
            renderer.render_replay_menu(flow, 20)
        assert renderer._replay_view is not None  # 贴图视图加载成功(未回退文字)
    finally:
        renderer.close()
        pygame.init()  # close() 会 pygame.quit(), 恢复以免影响后续用例
