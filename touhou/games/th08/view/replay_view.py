"""th08 Replay 菜单(录像列表)的渲染 —— select00.png 背景 + 直接绘字。

对照 th08-ref(@1861f88, 行号相对其 src/) TitleScreen.cpp DrawReplayMenu
(:2550-2677) 与 OnUpdateReplayMenu state 0 的背景装载(:3232 LoadSurface(0,
"title/select00.png")); 原作列表文字由 AsciiManager 直接绘字(非烘贴图 vm),
这里同样直接绘字:

- 表头 "No.   Name       Date  Player   Rank"(:2556);
- 每页 15 行(:2558-2559), 光标行白色, 其余 0xff808080 灰(:2568-2576);
- 行格式 "%s %8s  %6s %7s  %8s"(:2581) 的文本生成在 replay_flow.entry_line。

行位用 title01.anm scripts 79(表头)/80..94(15 行) interrupt 14 后的落位
实测: 表头 (24,128), 行 (24,160+i*17)。原作的逐行进退场动画
(SetInterruptArray 14/16 + replayChoiceFadeoutTimer)不做(偏离注明;
输入门节奏由 replay_flow 保留)。
"""

from __future__ import annotations

import io

import pygame

from ....engine.view.sprite_bank import SpriteBank
from ..crypt import try_decrypt_from_table
from .replay_flow import REPLAYS_PER_PAGE, ReplayFlowTh08, entry_line
from .title_view import _load_font

_W, _H = 640, 480

_REPLAY_BG = "select00.png"  # 封包内条目名(C++ 侧 "title/select00.png", :3232)

_HEADER_POS = (24, 128)  # 表头落位(title01.anm script 79, interrupt 14 后)
_ROW_X = 24  # 行 x(script 80..94 同 x)
_ROW_Y0 = 160  # 首行 y(script 80)
_ROW_H = 17  # 行距(script 80→81 的 y 差)
_FONT_SIZE = 15  # AsciiManager 缺省档(fontHeight=15)
_WHITE = (255, 255, 255)
_GRAY = (128, 128, 128)  # 非光标行 0xff808080(:2574)
_SHADOW = (0, 0, 48)
_EMPTY_HINT = "(replay 目录里没有 th8_*.json 录像)"


def _blit_text(
    surf: pygame.Surface,
    font: pygame.font.Font | None,
    text: str,
    x: int,
    y: int,
    color: tuple[int, int, int],
    shadow: tuple[int, int, int] = _SHADOW,
) -> None:
    """左对齐绘字(4 向描边; 与 music_view._blit_text 同法)。"""
    if font is None or not text:
        return
    img = font.render(text, True, color)
    sh = font.render(text, True, shadow)
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        surf.blit(sh, (x + dx, y + dy))
    surf.blit(img, (x, y))


class ReplayMenuView:
    """Replay 菜单渲染器: render(flow, frame) 画一帧到 640x480 surface。

    构造失败(无数据/资源损坏)抛异常, 由后端回退文字菜单(口径同
    TitleView/MusicRoomView)。
    """

    def __init__(self, data_path) -> None:
        self._bank = SpriteBank(data_path, game="th08")
        raw = try_decrypt_from_table(self._bank._archive().load(_REPLAY_BG))
        self._bg = pygame.image.load(io.BytesIO(raw))
        try:
            self._bg = self._bg.convert()  # 快速 blit 路径(需 display 初始化)
        except pygame.error:
            pass
        self._frame = pygame.Surface((_W, _H))
        self._font = None

    def render(self, flow: ReplayFlowTh08, frame: int = 0) -> pygame.Surface:
        surf = self._frame
        surf.blit(self._bg, (0, 0))
        if self._font is None:
            self._font = _load_font(_FONT_SIZE)
        # 表头(:2556, AsciiManager 白字常显)
        _blit_text(
            surf,
            self._font,
            "No.   Name       Date  Player   Rank",
            *_HEADER_POS,
            _WHITE,
        )
        n = len(flow.entries)
        if n == 0:
            _blit_text(surf, self._font, _EMPTY_HINT, _ROW_X, _ROW_Y0, _GRAY)
            return surf
        # 当前页 15 行(:2558-2566; 光标行白, 其余灰)
        start = flow.page_start
        for i in range(start, min(start + REPLAYS_PER_PAGE, n)):
            y = _ROW_Y0 + (i - start) * _ROW_H
            color = _WHITE if i == flow.cursor else _GRAY
            _blit_text(surf, self._font, entry_line(flow.entries[i]), _ROW_X, y, color)
        return surf


__all__ = ["ReplayMenuView"]
