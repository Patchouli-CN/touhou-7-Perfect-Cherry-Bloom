"""Touhou: hud_view/select_view 渲染 smoke + 结局音乐解析测试。

渲染层测试保"不炸": dummy video driver 下 render 不抛异常、
关键贴图确实加载; BGM 播放路径在 dummy audio driver 下无异常。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

sys.path.insert(0, r"D:\python_play\Touhou08")

DAT = Path(r"D:\TOUHOU_GAME\[th07] 东方妖妖梦 (日文版)\th07.dat")
NEEDS_DAT = pytest.mark.skipif(not DAT.exists(), reason="需要真实 th07.dat")


class _StubGlobals:
    """hud_view 读取的 globals 字段(games/th07/globals.py ZunGlobals 同名)。"""

    gui_score = 151180
    num_retries = 0
    high_score_num_continues = 0
    lives_remaining = 2.0
    bombs_remaining = 3.0
    current_power = 64.0
    graze_in_total = 3
    point_items_collected_for_extend = 3
    next_needed_point_items_for_extend = 50
    cherry = 1100
    cherry_start = 0
    cherry_max = 200000
    cherry_plus = 1100


class _StubGame:
    def __init__(self):
        self.globals = _StubGlobals()
        self.store = None


@NEEDS_DAT
class TestHudView:
    def test_render_no_raise(self) -> None:
        import pygame

        pygame.init()
        from touhou.games.th07.view.hud_view import HudView

        hud = HudView(DAT)
        frame = pygame.Surface((640, 480))
        hud.render(frame, _StubGame())
        hud.render_overlay(frame, _StubGame())

    def test_key_sprites_loaded(self) -> None:
        import pygame  # noqa: F401

        pygame.init()
        from touhou.games.th07.view.hud_view import HudView

        hud = HudView(DAT)
        # front.anm: logo/标签/星/窗框; ascii.anm: 字形/樱点槽/樱点数字
        assert hud.bank.sprite("front.anm", 0) is not None
        assert hud.bank.sprite("front.anm", 10) is not None
        assert hud.bank.sprite("front.anm", 12) is not None
        assert hud.bank.sprite("front.anm", 13) is not None
        assert hud.bank.sprite("ascii.anm", 47) is not None  # '0'
        assert hud.bank.sprite("ascii.anm", 132) is not None  # 樱点数字 0
        assert hud.bank.sprite("ascii.anm", 142) is not None  # 樱点槽


class _StubBorder:
    """games/th07/bomb.py Border 的 has_border 透出桩。"""

    def __init__(self, state):
        self.has_border = state


@NEEDS_DAT
class TestHudBorder:
    """结界 READY/ACTIVE 的樱点表现 (AsciiManager.cpp:1230-1281)。"""

    def test_ready_active_no_raise_and_mark(self) -> None:
        import pygame

        pygame.init()
        from touhou.games.th07.bomb import BorderState
        from touhou.games.th07.view.hud_view import HudView

        hud = HudView(DAT)
        frames = {}
        for state in (BorderState.NONE, BorderState.READY, BorderState.ACTIVE):
            g = _StubGame()
            g.border = _StubBorder(state)
            g.frame = 0
            frame = pygame.Surface((640, 480))
            hud.render_overlay(frame, g)
            frames[state] = pygame.surfarray.array3d(frame)
        # READY: 上行数字变色/放大 (AsciiManager.cpp:1230-1253)
        assert (frames[BorderState.READY] != frames[BorderState.NONE]).any()
        # ACTIVE: 再加 cherryBorderActive 呼吸标记 (:1275-1281)
        assert (frames[BorderState.ACTIVE] != frames[BorderState.READY]).any()


@NEEDS_DAT
class TestSelectView:
    def test_render_no_raise(self) -> None:
        import pygame

        pygame.init()
        from touhou.games.th07.view.select_view import SelectView

        view = SelectView(DAT)
        surf = pygame.Surface((640, 480))
        view.render_difficulty(surf, 1)
        for i in range(6):
            view.render_character(surf, i)
        view.render_extra(surf, 1)

    def test_key_sprites_loaded(self) -> None:
        import pygame  # noqa: F401

        pygame.init()
        from touhou.games.th07.view.select_view import SelectView

        view = SelectView(DAT)
        view.ensure_loaded()
        assert view.background is not None  # select00.jpg
        assert len(view._diff_sprites) == 4  # Easy..Lunatic 亮/暗
        assert len(view._extra_sprites) == 2  # Extra/Phantasm
        assert len(view._portraits) == 3  # 灵梦/魔理沙/咲夜
        assert len(view._shot_blocks) == 3
        assert len(view._headers) == 2


class TestEndingMusic:
    """engine/ending.py 的 @m 透出(结局 BGM, view.py 播放点用)。"""

    def test_parse_end_music(self) -> None:
        from touhou.engine.ending import parse_end_music

        data = b"@bdata/end/end00.jpg\0\n@mbgm/th07_14.mid\0\n@M5\0\ntext\n"
        assert parse_end_music(data) == "th07_14.mid"

    def test_parse_end_music_absent(self) -> None:
        from touhou.engine.ending import parse_end_music

        assert parse_end_music(b"@bdata/end/end00.jpg\0\ntext\n") == ""

    @NEEDS_DAT
    def test_real_ending_has_music(self) -> None:
        from touhou.schema.archive import open_archive
        from touhou.engine.ending import EndingData

        arc = open_archive(DAT)
        ed = EndingData.load(arc, 0, bad=False)  # end00.end (灵梦A)
        assert ed.music == "th07_14.mid"


@NEEDS_DAT
class TestRunMenuSmoke:
    """经 GameApp._run_menu 的整链路(菜单逻辑 + select_view 渲染)。"""

    def test_run_menu_all_pages(self) -> None:
        import pygame

        pygame.init()
        from touhou.games.th07.view.screens import Screen
        from touhou.games.th07.view import GameApp

        app = GameApp(lambda **kw: None)
        for screen in (Screen.DIFFICULTY, Screen.CHARACTER, Screen.EXTRA_LEVEL):
            app._screen = screen
            app._run_menu([])  # 不抛异常即通过


@NEEDS_DAT
class TestBgmSmoke:
    """BGM 播放路径(dummy audio): 标题/关卡/结局/结算曲调用无异常。"""

    def test_play_music_paths(self) -> None:
        import pygame

        pygame.init()
        try:
            pygame.mixer.init()
        except pygame.error:
            pytest.skip("无 mixer")
        from touhou.engine.view.sound_player import SoundPlayer

        sp = SoundPlayer(DAT)
        sp.ensure_loaded()
        if not sp.silence:  # 静音豁免
            assert sp.enabled, "再未静音状态下不可用"
            for name in ("th07_01.mid", "th07_02.mid", "th07_14.mid", "init.mid"):
                sp.play_music(name)
                assert sp._current_bgm == name
            sp.fadeout_music(0.1)
            assert sp._current_bgm == ""
            sp.play_music("th07_02.mid")
            sp.stop_music()
            assert sp._current_bgm == ""
            # 不存在的曲子: 仅记日志不炸
            sp.play_music("th07_99.mid")
        pygame.mixer.quit()
