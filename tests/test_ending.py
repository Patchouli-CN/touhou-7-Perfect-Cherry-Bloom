"""结局/staff roll 测试: .end 指令集解析 (parse_end_ops) + 播放状态机 (EndingPlayer)。

数值权威来源: th07/src/th07/Ending.cpp (ParseEndFile/OnUpdate/FadingEffect)。
注意: 测试数据里的参数分隔符是字面 NUL, 必须写 \\x00 (\\0 后随数字会被
Python 当八进制转义)。
"""
from __future__ import annotations

import sys

import pytest

sys.path.insert(0, r"D:\python_play\Touhou08")

from touhou.engine.ending import (  # noqa: E402
    FADE_IN_BLACK,
    FADE_IN_WHITE,
    FADE_OUT_BLACK,
    FADE_OUT_WHITE,
    EndingData,
    EndingPlayer,
    parse_end_ops,
)


def _end(lines: list[bytes]) -> bytes:
    return b"\n".join(lines)


# ---- parse_end_ops: 指令全集 (Ending.cpp:242-372) ----

def test_parse_ops_full_coverage() -> None:
    data = _end([
        b"@mbgm/th07_14.mid\x00",
        b"@s70\x0012\x00",
        b"@bdata/end/end00.jpg\x00",
        b"@c15790320\x00",
        b"@v147\x00",
        b"@V120\x003100\x00",
        b"@a1\x002\x003\x00",
        b"@R\x00",
        b"@w120\x00120\x00",
        b"@r1200\x004\x00",
        b"@030\x00",
        b"@160\x00",
        b"@2180\x00",
        b"@3240\x00",
        b"@M5\x00",
        "咲夜　「セリフ」".encode("cp932"),
        b"@Fdata/staff00.end\x00",
        b"@z",
    ])
    ops = parse_end_ops(data)
    assert ("music", "th07_14.mid") in ops
    assert ("line_speed", 70, 12) in ops
    assert ("bg", "end00.jpg") in ops
    assert ("color", 15790320) in ops
    assert ("bg_y", 147) in ops
    assert ("bg_scroll", 120, 3100) in ops
    assert ("face", 1, 2, 3) in ops
    assert ("clear_faces",) in ops
    assert ("wait", 120, 120) in ops
    assert ("wait_reset", 1200, 4) in ops
    assert ("fade", FADE_OUT_BLACK, 30) in ops   # @0
    assert ("fade", FADE_IN_BLACK, 60) in ops    # @1
    assert ("fade", FADE_OUT_WHITE, 180) in ops  # @2
    assert ("fade", FADE_IN_WHITE, 240) in ops   # @3
    assert ("music_fade", 5) in ops
    assert ("text", "咲夜　「セリフ」") in ops
    assert ("load", "staff00.end") in ops
    assert ops[-1] == ("end",)


# ---- EndingPlayer: 文本行节奏 (ParseEndFile :386-410) ----

def test_player_text_pacing() -> None:
    """文本逐行显示, 间隔 line2Delay 帧; @s 改 (line2Delay, topLineDelay)。"""
    p = EndingPlayer(_end([
        b"@s3\x009\x00",
        "第一行".encode("cp932"),
        "第二行".encode("cp932"),
        b"@z",
    ]))
    p.tick()
    assert [l.text for l in p.texts] == ["第一行"]
    for _ in range(3):                 # timer2=3 等待 3 帧
        p.tick()
        assert len(p.texts) == 1
    p.tick()                           # 第 4 帧解析出第二行
    assert [l.text for l in p.texts] == ["第一行", "第二行"]


def test_player_advance_held_uses_top_delay() -> None:
    """按住确认键: 行间隔换 topLineDelay (:399-408)。"""
    p = EndingPlayer(_end([
        b"@s70\x002\x00",
        "一".encode("cp932"),
        "二".encode("cp932"),
        b"@z",
    ]))
    p.tick(advance_held=True)
    assert len(p.texts) == 1
    for _ in range(2):                 # topLineDelay=2 等待 2 帧
        p.tick(advance_held=True)
        assert len(p.texts) == 1
    p.tick(advance_held=True)
    assert len(p.texts) == 2


def test_player_wait_and_skip_gate() -> None:
    """@w: minWait 耗尽后确认键按下沿可提前结束等待 (:222-234)。"""
    p = EndingPlayer(_end([
        b"@w10\x004\x00",   # 等 10 帧, 前 4 帧不可跳
        b"@z",
    ]))
    p.tick()                           # 解析到 @w, timer2=10
    for _ in range(3):
        p.tick(advance_pressed=True)   # minWait=4 内不可跳
    assert not p.done
    p.tick(advance_pressed=True)       # minWait 耗尽 (4→0)
    p.tick(advance_pressed=True)       # 本帧确认沿 → timer2 清零
    assert not p.done                  # 清零当帧仍 goto stop
    p.tick()
    assert p.done                      # 次帧解析 @z


def test_player_wait_reset_clears_texts() -> None:
    """@r: 等待 timer3 帧, 归零当帧清空已显示文本行并继续解析 (:206-217)。"""
    p = EndingPlayer(_end([
        b"@s0\x000\x00",     # 行间隔 0: 文本帧次帧即继续解析
        "一".encode("cp932"),
        b"@r5\x000\x00",
        "二".encode("cp932"),
        b"@z",
    ]))
    p.tick()
    assert [l.text for l in p.texts] == ["一"]
    p.tick()                           # 解析 @r, timer3=5
    for _ in range(4):
        p.tick()
        assert [l.text for l in p.texts] == ["一"]   # 等待中不清
    p.tick()                           # timer3 归零: 清屏 + 继续解析出 "二"
    assert [l.text for l in p.texts] == ["二"]


# ---- 淡入淡出 (FadingEffect :99-165) ----

def test_player_fade_out_black() -> None:
    """@0: 黑幕淡出, alpha 255→0, 完成后无覆盖。"""
    p = EndingPlayer(_end([b"@010\x00", b"@w600\x00600\x00", b"@z"]))
    p.tick()                           # 解析 @0 (fade_type=1, t=0), 停在 @w
    ov = p.fade_overlay()
    assert ov is not None and ov[:3] == (0, 0, 0)
    a0 = ov[3]
    p.tick()
    assert p.fade_overlay()[3] < a0    # 渐透明
    for _ in range(20):
        p.tick()
    assert p.fade_overlay() is None    # 淡出完成 → fadeType=0


def test_player_fade_in_white_sticks() -> None:
    """@3: 白幕淡入, 完成后停在不透明白 (:146-156)。"""
    p = EndingPlayer(_end([b"@310\x00", b"@w600\x00600\x00", b"@z"]))
    p.tick()
    assert p.fade_overlay()[:3] == (255, 255, 255)
    for _ in range(20):
        p.tick()
    assert p.fade_overlay() == (255, 255, 255, 255)


# ---- 背景滚动 (@v/@V, stop 收尾 :420-427) ----

def test_player_bg_scroll_and_clamp() -> None:
    """@v 设 y, @V 设速度 dist/dur; 每次解析停帧 y-=speed, 夹到 0 停。"""
    p = EndingPlayer(_end([
        b"@v147\x00",
        b"@V120\x003100\x00",  # speed = 120/3100
        b"@w600\x00600\x00",
        b"@z",
    ]))
    speed = 120 / 3100
    p.tick()                           # @v/@V/@w 同帧处理, stop 收尾滚一格
    assert p.bg_y == pytest.approx(147.0 - speed)
    p.tick()
    assert p.bg_y == pytest.approx(147.0 - 2 * speed)
    p.bg_y = 0.01                      # 快进到底
    for _ in range(5):
        p.tick()
    assert p.bg_y == 0.0 and p.bg_scroll == 0.0


# ---- @F 续载 (staff roll 衔接) / @m 音乐事件 / @z 结束 ----

def test_player_load_chains_staff_roll() -> None:
    staff = _end([
        b"@R\x00",
        b"@bdata/end/staff00.jpg\x00",
        b"@mbgm/th07_15.mid\x00",
        b"@a0\x000\x000\x00",
        b"@z",
    ])
    p = EndingPlayer(
        _end([b"@mbgm/th07_14.mid\x00", b"@Fdata/staff00.end\x00", b"@z"]),
        loader=lambda name: staff if name == "staff00.end" else None)
    p.tick()                           # @m + @F(清立绘) + staff 的 @R/@b/@m/@a
    assert p.bg_name == "staff00.jpg"
    assert p.faces == {0: (0, 0)}
    assert p.music_events == [("play", "th07_14.mid"), ("play", "th07_15.mid")]
    p.tick()                           # staff 的 @z
    assert p.done


def test_player_load_failure_ends() -> None:
    """@F 载入失败 → LoadEnding 返回 ZUN_ERROR → 结局结束 (:271-275)。"""
    p = EndingPlayer(_end([b"@Fdata/none.end\x00", b"@z"]),
                     loader=lambda name: None)
    p.tick()
    assert p.done


def test_player_music_fade_event() -> None:
    p = EndingPlayer(_end([b"@M5\x00", b"@z"]))
    p.tick()
    assert ("fadeout", 5) in p.music_events


# ---- GameApp 集成: 播完自动进结算 ----

def test_app_ending_autofinish_to_result() -> None:
    """结局脚本播完 (@z) → GameApp 自动 finish_ending + 进结算
    (Ending 链移除 → curState=6, Ending.cpp:520)。"""
    pygame = pytest.importorskip("pygame")
    import os
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    pygame.init()
    from touhou.engine.view import GameApp
    from touhou.engine.view.screens import Screen

    class _Stub:
        def __init__(self) -> None:
            self.ending = EndingData(
                character=0, bad=False, path="",
                segments=[], ops=[("end",)])   # 首帧即 @z
            self.finished = False

        def finish_ending(self):  # noqa: D102
            self.finished = True
            self.ending = None

    app = GameApp(lambda **kw: _Stub())
    app._game = _Stub()
    app._screen = Screen.ENDING
    app._run_ending([])       # 无输入: 播完自动走
    assert app._game is not None and app._game.finished
    assert app._screen == Screen.RESULT


# ---- 真实数据 smoke (th07.dat; 缺失跳过) ----

def _real_archive():
    from touhou.paths import DEFAULT_DATA
    if not DEFAULT_DATA.exists():
        pytest.skip("th07.dat 不在默认路径")
    from touhou.schema.archive import GameArchive
    return GameArchive.open(DEFAULT_DATA)


def test_real_ending_and_staff_roll() -> None:
    """end00.end 全程 + @F staff00.end staff roll: 文本/CG/滚动/音乐事件全通。"""
    archive = _real_archive()
    ending = EndingData.load(archive, character=0, bad=False)
    assert ("load", "staff00.end") in ending.ops
    p = EndingPlayer(ending.ops, loader=lambda name: archive.load(name))
    saw_face = False
    saw_staff_bg = False
    saw_ending_no = False
    frames = 0
    while not p.done and frames < 60000:
        p.tick()
        saw_face = saw_face or bool(p.faces)
        saw_staff_bg = saw_staff_bg or p.bg_name == "staff00.jpg"
        saw_ending_no = saw_ending_no or any(
            "ＥＮＤＩＮＧ" in l.text for l in p.texts)
        frames += 1
    assert p.done                      # staff00.end 的 @z
    assert saw_ending_no               # end00.end 的 "ＥＮＤＩＮＧ　Ｎｏ．４" 行
    assert saw_face and saw_staff_bg   # staff roll: CG 立绘 + staff00.jpg
    plays = [n for k, n in p.music_events if k == "play"]
    assert plays == ["th07_14.mid", "th07_15.mid"]   # 结局曲 → staff 曲
    assert ("fadeout", 5) in p.music_events          # @M5


def test_real_bad_ending_also_has_staff_roll() -> None:
    """bad ending (end00b.end): 末尾同样 @F 接 staff roll (原版如此)。"""
    archive = _real_archive()
    ending = EndingData.load(archive, character=0, bad=True)
    assert ("load", "staff00.end") in ending.ops
    p = EndingPlayer(ending.ops, loader=lambda name: archive.load(name))
    frames = 0
    saw_staff_bg = False
    while not p.done and frames < 60000:
        p.tick()
        saw_staff_bg = saw_staff_bg or p.bg_name == "staff00.jpg"
        frames += 1
    assert p.done and saw_staff_bg


# ---- view 渲染 smoke (headless pygame) ----

def test_ending_view_render_smoke() -> None:
    """EndingView: 结局文本帧 + staff roll CG (@a → staff01.anm VM) 渲染不炸。"""
    pygame = pytest.importorskip("pygame")
    archive = _real_archive()
    import os
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    pygame.init()
    from touhou.paths import DEFAULT_DATA
    from touhou.engine.view.ending_view import EndingView
    view = EndingView(DEFAULT_DATA)
    surf = pygame.Surface((640, 480))
    # 结局前段: 文本行 + 白幕淡出 (@230000/@2180)
    ending = EndingData.load(archive, character=0, bad=False)
    for f in range(400):
        view.render(surf, ending, f)
    assert view._player is not None and len(view._player.texts) > 0
    assert ("play", "th07_14.mid") in view.pending_music
    # staff roll 段: 直接喂 staff00.end, 跑到首个 @a (@w300+180+320 后)
    staff = EndingData(character=0, bad=False, path="staff00.end",
                       segments=[], ops=parse_end_ops(archive.load("staff00.end")))
    for f in range(900):
        view.render(surf, staff, f)
    assert view._player.bg_name == "staff00.jpg"
    assert view._face_vms               # @a 立绘/CG VM 已建
    assert ("play", "th07_15.mid") in view.pending_music
