"""th08 Practice/Spell Practice 画面的渲染 —— select00.png 背景 + 直接绘字。

对照 th08-ref(@1861f88, 行号相对其 src/): 背景 LoadSurface(0,
"title/select00.png")(SpellStageSelect :1994, PracticeStageSelect/卡选同系
选择画面共用); 面选/卡选列表文字原作由 AsciiManager 直接绘字
(DrawSpellStageSelect :2790-2900 / spellCardNameVms 烘字 :2233-2272),
这里同样直接绘字。行文本生成在 practice_flow(纯逻辑), 本模块只管落位/颜色。
原作的逐行进退场动画(SetInterruptArray 18/26)与收取率饼图不做(偏离注明;
输入门节奏由 practice_flow 保留)。
"""

from __future__ import annotations

import io

import pygame

from ....engine.view.sprite_bank import SpriteBank
from ..crypt import try_decrypt_from_table
from .practice_flow import (
    SPELL_CARD_TABLE_HEADER,
    SPELL_STAGE_TABLE_HEADER,
    SPELLCARDS_PER_PAGE,
    PracticeStageFlowTh08,
    SpellCardFlowTh08,
    SpellStageFlowTh08,
)
from .title_view import _load_font

_W, _H = 640, 480

_BG = "select00.png"  # 封包内条目名(C++ 侧 "title/select00.png")

_FONT_SIZE = 15  # AsciiManager 缺省档(fontHeight=15)
_WHITE = (255, 255, 255)
_GRAY = (160, 160, 160)  # 非光标行 0xffa0a0a0(:2799)
_SHADOW = (0, 0, 48)

# 面选/卡选落位(AsciiManager 行距 16, :2873; 起始位置按选择画面系惯例)
_HEADER_POS = (32, 64)
_ROW_X = 48
_ROW_Y0 = 96
_ROW_H = 16
_INFO_X = 64
_INFO_Y0 = 344  # spellCardInfoVms 落位 (64, i*16+344)(:2292-2299)
_TITLE_Y = 32


def _blit_text(
    surf: pygame.Surface,
    font: pygame.font.Font | None,
    text: str,
    x: int,
    y: int,
    color: tuple[int, int, int],
    shadow: tuple[int, int, int] = _SHADOW,
) -> None:
    """左对齐绘字(4 向描边; 与 replay_view._blit_text 同法)。"""
    if font is None or not text:
        return
    img = font.render(text, True, color)
    sh = font.render(text, True, shadow)
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        surf.blit(sh, (x + dx, y + dy))
    surf.blit(img, (x, y))


class PracticeMenuView:
    """Practice/Spell Practice 画面渲染器: 三个 render_* 各画一帧到
    640x480 surface。构造失败(无数据/资源损坏)抛异常, 由后端回退文字菜单
    (口径同 ReplayMenuView)。"""

    def __init__(self, data_path) -> None:
        self._bank = SpriteBank(data_path, game="th08")
        raw = try_decrypt_from_table(self._bank._archive().load(_BG))
        self._bg = pygame.image.load(io.BytesIO(raw))
        try:
            self._bg = self._bg.convert()  # 快速 blit 路径(需 display 初始化)
        except pygame.error:
            pass
        self._frame = pygame.Surface((_W, _H))
        self._font = None

    def _text(self, surf, text, x, y, color) -> None:
        if self._font is None:
            self._font = _load_font(_FONT_SIZE)
        _blit_text(surf, self._font, text, x, y, color)

    def _rows(self, surf, lines, cursor, y0=_ROW_Y0) -> None:
        """画列表行: 光标行白, 其余灰(:2795-2800)。"""
        start = 0
        if len(lines) > SPELLCARDS_PER_PAGE:
            start = cursor - cursor % SPELLCARDS_PER_PAGE
        for i in range(start, min(start + SPELLCARDS_PER_PAGE, len(lines))):
            color = _WHITE if i == cursor else _GRAY
            self._text(surf, lines[i], _ROW_X, y0 + (i - start) * _ROW_H, color)

    def render_practice_stage(
        self, flow: PracticeStageFlowTh08, title: str, lines: list, frame: int = 0
    ) -> pygame.Surface:
        """Practice 面选: 难度+机体标题行 + 8 行面列表。"""
        surf = self._frame
        surf.blit(self._bg, (0, 0))
        self._text(surf, title, _HEADER_POS[0], _TITLE_Y, _WHITE)
        self._rows(surf, lines, flow.cursor)
        return surf

    def render_spell_stage(
        self, flow: SpellStageFlowTh08, title: str, lines: list, frame: int = 0
    ) -> pygame.Surface:
        """Spell 面选: 机体标题行 + 表头(:2861-2866 落位) + 10 行面列表。"""
        surf = self._frame
        surf.blit(self._bg, (0, 0))
        self._text(surf, title, _HEADER_POS[0], _TITLE_Y, _WHITE)
        self._text(surf, SPELL_STAGE_TABLE_HEADER, *_HEADER_POS, _WHITE)
        self._rows(surf, lines, flow.cursor)
        return surf

    def render_spell_card(
        self,
        flow: SpellCardFlowTh08,
        title: str,
        info_lines: tuple,
        frame: int = 0,
    ) -> pygame.Surface:
        """Spell 卡选: 面名+表头(:2290 落位) + 当前页 15 行 + 底部信息区
        (spellCardInfoVms, (64, i*16+344))。"""
        surf = self._frame
        surf.blit(self._bg, (0, 0))
        self._text(surf, title, _HEADER_POS[0], _TITLE_Y, _WHITE)
        self._text(surf, SPELL_CARD_TABLE_HEADER, *_HEADER_POS, _WHITE)
        self._rows(surf, flow.names, flow.cursor)
        for i, line in enumerate(info_lines):
            self._text(surf, line, _INFO_X, _INFO_Y0 + i * 16, _WHITE)
        return surf


__all__ = ["PracticeMenuView"]
