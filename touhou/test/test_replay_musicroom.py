"""Touhou: Music Room / Replay 测试。

- musiccmt 解析(合成字节流, Shift-JIS);
- MusicRoomFlow 导航(光标回绕/窗口偏移/播放停止/退出);
- replay 编解码与录制-读档往返(RLE);
- 录制-播放逐值一致性(真实 th07 数据, 确定性 —— replay 的核心性质);
- GameApp 场景流: Music Room 进出恢复标题曲, Replay 菜单选择播放, 暂停 Save Replay。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, r"D:\python_play\Touhou08")

from touhou.engine import replay as replay_mod  # noqa: E402
from touhou.engine.render import FrameInput  # noqa: E402
from touhou.engine.view.screens import (  # noqa: E402
    MenuAction, MusicRoomFlow, ReplayFlow, Screen)
from touhou.schema.musiccmt import parse_musiccmt  # noqa: E402

DAT = Path(r"D:\TOUHOU_GAME\[th07] 东方妖妖梦 (日文版)\th07.dat")


# ---------------------------------------------------------------------------
# musiccmt 解析
# ---------------------------------------------------------------------------

def _cmt_bytes() -> bytes:
    text = (
        "０１２３４５６７８９\r\n"                       # 占位行(忽略)
        "@bgm/th07_01.mid\r\n"
        "妖々夢　〜 Snow or Cherry Petal\r\n"
        "No.1 妖々夢　〜 Snow or Cherry Petal\r\n"
        "　タイトル画面テーマです。\r\n"
        "\r\n"                                          # 空行 = 块分隔
        "@bgm/th07_02.mid\r\n"
        "無何有の郷　〜 Deep Mountain\r\n"
        "　１面テーマです。\r\n"
        "　コメント二行目。\r\n"
    )
    return text.encode("shift_jis")


def test_musiccmt_parse() -> None:
    tracks = parse_musiccmt(_cmt_bytes())
    assert len(tracks) == 2
    assert tracks[0].path == "bgm/th07_01.mid"
    assert tracks[0].title == "妖々夢　〜 Snow or Cherry Petal"
    assert tracks[0].file_name == "th07_01.mid"
    assert tracks[0].comment == (
        "No.1 妖々夢　〜 Snow or Cherry Petal", "タイトル画面テーマです。")
    assert tracks[1].title == "無何有の郷　〜 Deep Mountain"
    assert len(tracks[1].comment) == 2


def test_musiccmt_empty_and_comment_cap() -> None:
    assert parse_musiccmt(b"") == []
    assert parse_musiccmt("no blocks here".encode("shift_jis")) == []
    # 评论超过 8 行截断 (TrackDescriptor.description[8])
    text = "@bgm/x.mid\r\nタイトル\r\n" + "".join(
        f"　行{i}\r\n" for i in range(12))
    tracks = parse_musiccmt(text.encode("shift_jis"))
    assert len(tracks[0].comment) == 8


# ---------------------------------------------------------------------------
# MusicRoomFlow 导航
# ---------------------------------------------------------------------------

class _T:
    def __init__(self, title):
        self.title = title
        self.comment = ()
        self.file_name = title + ".mid"


def _flow(n: int = 20) -> MusicRoomFlow:
    return MusicRoomFlow(tracks=[_T(f"track{i}") for i in range(n)])


def test_musicroom_flow_wrap_and_window() -> None:
    f = _flow()
    assert f.handle(MenuAction.UP) is None
    assert f.cursor == 19                     # 回绕到末位
    assert f.listing_offset == 10             # 窗口跟随(末 10 首)
    f.handle(MenuAction.DOWN)
    assert f.cursor == 0 and f.listing_offset == 0
    for _ in range(12):                       # 下移到第 13 首, 窗口滑动
        f.handle(MenuAction.DOWN)
    assert f.cursor == 12
    assert f.listing_offset == 3              # cursor-9
    f.handle(MenuAction.UP)
    assert f.listing_offset == 3              # 光标仍在窗口内, 不动


def test_musicroom_flow_play_stop_quit() -> None:
    f = _flow()
    f.handle(MenuAction.DOWN)
    r = f.handle(MenuAction.CONFIRM)          # 播放光标曲
    assert r == {"action": "play", "index": 1}
    assert f.playing == 1
    f.handle(MenuAction.DOWN)                 # 选别的曲 → 换曲(不是停止)
    r = f.handle(MenuAction.CONFIRM)
    assert r == {"action": "play", "index": 2}
    assert f.playing == 2
    r = f.handle(MenuAction.CONFIRM)          # 播放中再按 = 停止
    assert r == {"action": "stop"}
    assert f.playing is None
    assert f.handle(MenuAction.BACK) == {"action": "quit"}


def test_musicroom_flow_empty() -> None:
    f = MusicRoomFlow(tracks=[])
    assert f.handle(MenuAction.UP) is None
    assert f.handle(MenuAction.CONFIRM) is None
    assert f.handle(MenuAction.BACK) == {"action": "quit"}


# ---------------------------------------------------------------------------
# replay 编解码 / 录制-读档往返
# ---------------------------------------------------------------------------

def test_replay_input_codec_roundtrip() -> None:
    keys = (True, False, True, False, True, True)
    code = replay_mod.encode_input(keys, True, True, False)
    k2, bomb, advance, skip = replay_mod.decode_input(code)
    assert k2 == keys and bomb and advance and not skip
    assert replay_mod.decode_input(0) == ((False,) * 6, False, False, False)


def test_replay_recorder_save_load(tmp_path) -> None:
    rec = replay_mod.ReplayRecorder(replay_mod.make_meta(
        difficulty=1, character=2, stage=3, seed=42, initial_lives=5))
    for i in range(300):
        keys = (i % 2 == 0, False, False, False, i % 3 == 0, True)
        rec.record(keys, i % 100 == 50, False, False)
    path = rec.save(tmp_path / "r1.json")
    r = replay_mod.load_replay(path)
    assert r["meta"]["difficulty"] == 1 and r["meta"]["character"] == 2
    assert r["meta"]["stage"] == 3 and r["meta"]["seed"] == 42
    assert r["meta"]["initial_lives"] == 5
    assert len(r["codes"]) == 300            # RLE 展开后帧数还原
    k0 = replay_mod.decode_input(r["codes"][0])[0]
    assert k0 == (True, False, False, False, True, True)
    # list_replays 能扫到且跳过坏文件
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    entries = replay_mod.list_replays(tmp_path)
    assert len(entries) == 1 and entries[0]["meta"]["seed"] == 42


def test_replay_load_rejects_bad(tmp_path) -> None:
    p = tmp_path / "x.json"
    p.write_text("{}", encoding="utf-8")
    try:
        replay_mod.load_replay(p)
        raise AssertionError("应当拒绝")
    except ValueError:
        pass
    assert replay_mod.list_replays(tmp_path / "nonexistent") == []


# ---------------------------------------------------------------------------
# 录制-播放逐值一致性(真实数据, 确定性 —— replay 的核心性质)
# ---------------------------------------------------------------------------

def _scripted_inputs(frames: int) -> list:
    """固定输入脚本(与游戏状态无关): 移动/射击/炸弹/对话推进。"""
    out = []
    for i in range(frames):
        keys = (i % 5 == 0, i % 7 == 0, i % 11 == 0, i % 13 == 0,
                i % 3 == 0, True)
        out.append((keys, i % 500 == 250, i % 7 == 0, i % 11 == 0))
    return out


def _play_meta_and_inputs(meta: dict, inputs: list) -> tuple:
    """按录像 meta 重建 game 并喂输入, 返回逐帧状态签名。"""
    from touhou.core.impl import PerfectCherryBloom

    g = PerfectCherryBloom(data_path=DAT,
                           character=meta["character"],
                           difficulty=meta["difficulty"],
                           seed=meta["seed"])
    g.globals.lives_remaining = float(meta["initial_lives"])
    sig = []
    for keys, bomb, advance, skip in inputs:
        g.tick(keys=keys, bomb=bomb, advance=advance, skip=skip)
        sig.append((g.globals.score, round(g.player.pos.x, 9),
                    round(g.player.pos.y, 9), g.lives, g.bombs,
                    g.globals.cherry, g.globals.graze_in_total))
    return sig


def test_replay_record_playback_identical(tmp_path) -> None:
    """录制一局 800 帧 → 存档读回 → 重放两遍, 逐帧状态必须与录制局一致。"""
    from touhou.core.impl import PerfectCherryBloom

    meta = replay_mod.make_meta(difficulty=1, character=0, stage=1,
                                seed=12345, initial_lives=3)
    rec = replay_mod.ReplayRecorder(meta)
    g = PerfectCherryBloom(data_path=DAT, character=0, difficulty=1,
                           seed=12345)
    g.globals.lives_remaining = 3.0
    inputs = _scripted_inputs(800)
    recorded_sig = []
    for keys, bomb, advance, skip in inputs:
        g.tick(keys=keys, bomb=bomb, advance=advance, skip=skip)
        rec.record(keys, bomb, advance, skip)
        recorded_sig.append((g.globals.score, round(g.player.pos.x, 9),
                             round(g.player.pos.y, 9), g.lives, g.bombs,
                             g.globals.cherry, g.globals.graze_in_total))
    # 存档 → 读回 → 逐帧输入还原
    path = rec.save(tmp_path / "run.json")
    r = replay_mod.load_replay(path)
    assert len(r["codes"]) == 800
    decoded = [replay_mod.decode_input(c) for c in r["codes"]]
    assert decoded == inputs
    # 重放两遍, 都必须与录制局逐值一致
    for _ in range(2):
        assert _play_meta_and_inputs(r["meta"], decoded) == recorded_sig


# ---------------------------------------------------------------------------
# GameApp 场景流(Music Room / Replay / Save Replay)
# ---------------------------------------------------------------------------

class StubGame:
    def __init__(self, **kw):
        self.kw = kw
        self.ticks = 0

    def tick(self, **kw):
        self.ticks += 1


def _app(tmp_path, monkeypatch, make=StubGame):
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    import pygame

    pygame.init()
    if pygame.display.get_surface() is None:
        pygame.display.set_mode((640, 480))
    from touhou.engine.view import GameApp

    return GameApp(make, config_path=tmp_path / "config.json",
                   score_path=tmp_path / "score.json",
                   replay_dir=tmp_path / "replays")


def _goto(app, item: str) -> None:
    while app._flow.cursor.current != item:
        app._on_menu(MenuAction.DOWN)
    app._on_menu(MenuAction.CONFIRM)


def test_music_room_enter_play_stop_leave(tmp_path, monkeypatch) -> None:
    app = _app(tmp_path, monkeypatch)
    _goto(app, "Music Room")
    assert app._screen == Screen.MUSIC_ROOM
    flow = app._mr_flow
    assert flow is not None and len(flow.tracks) == 20  # 真实 musiccmt.txt
    # 无声卡环境 SoundPlayer 静音, 但 flow 状态/接口照常走
    app._on_menu(MenuAction.DOWN)
    assert flow.cursor == 1
    app._on_menu(MenuAction.CONFIRM)          # 播放
    assert flow.playing == 1
    app._on_menu(MenuAction.CONFIRM)          # 停止
    assert flow.playing is None
    app._on_menu(MenuAction.BACK)             # 退出 → 回标题
    assert app._screen == Screen.MAIN_MENU


def test_replay_menu_empty_and_back(tmp_path, monkeypatch) -> None:
    app = _app(tmp_path, monkeypatch)
    _goto(app, "Replay")
    assert app._screen == Screen.REPLAY
    assert app._rp_flow is not None and app._rp_flow.entries == []
    app._on_menu(MenuAction.CONFIRM)          # 空列表确认不炸
    assert app._screen == Screen.REPLAY
    app._on_menu(MenuAction.BACK)
    assert app._screen == Screen.MAIN_MENU


def test_pause_save_replay_and_playback_flow(tmp_path, monkeypatch) -> None:
    """开局录几帧 → 暂停 Save Replay → 回标题 → Replay 菜单播放 → Esc 中止。"""
    import pygame

    app = _app(tmp_path, monkeypatch)
    app._on_menu(MenuAction.CONFIRM)          # 开始游戏 → 难度
    app._on_menu(MenuAction.CONFIRM)          # 难度 → 角色
    app._on_menu(MenuAction.CONFIRM)          # 角色 → 游玩
    assert app._screen == Screen.PLAYING
    scr = pygame.display.get_surface()
    keys = pygame.key.get_pressed()
    for _ in range(5):
        app._run_game(FrameInput())
    assert app._recorder is not None and app._recorder.frames == 5
    # 暂停 → Save Replay
    app._run_game(FrameInput(esc=True))
    assert app._paused
    while app._pause_cursor.current != "Save Replay":
        app._run_game(FrameInput(menu_actions=(MenuAction.DOWN,)))
    app._run_game(FrameInput(menu_actions=(MenuAction.CONFIRM,)))
    saved = list((tmp_path / "replays").glob("*.json"))
    assert len(saved) == 1
    r = replay_mod.load_replay(saved[0])
    assert len(r["codes"]) == 5
    assert app._pause_hint_timer > 0          # Saved 提示
    # 回标题 → Replay 菜单应列出该录像
    app._run_game(FrameInput(menu_actions=(MenuAction.BACK,)))  # 先退出暂停(Resume)
    app._run_game(FrameInput(esc=True))                        # 再暂停
    while app._pause_cursor.current != "Quit to Title":
        app._run_game(FrameInput(menu_actions=(MenuAction.DOWN,)))
    app._run_game(FrameInput(menu_actions=(MenuAction.CONFIRM,)))
    assert app._screen == Screen.MAIN_MENU
    _goto(app, "Replay")
    assert len(app._rp_flow.entries) == 1
    app._on_menu(MenuAction.CONFIRM)          # 播放
    assert app._screen == Screen.PLAYING
    assert app._playback is not None
    # 播完(5 帧录像 + StubGame 无 result) → 自动回标题
    for _ in range(6):
        app._run_game(FrameInput())
        if app._screen == Screen.MAIN_MENU:
            break
    assert app._screen == Screen.MAIN_MENU
    assert app._playback is None
