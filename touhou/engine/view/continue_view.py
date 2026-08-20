"""GameOver 续关菜单渲染(pygame) —— ascii.anm entry2(pause.png)贴图移植。

原版 (AsciiManager.cpp RetryMenu::OnUpdate:848-862 / OnDraw:1018-1046,
script 264-268 稳态, 游戏区 384x448 坐标, 中心锚 —— script 无 ANM_22):

- sprite5 "コンティニューしますか?" 中心 (224,192)   (script 264)
- sprite6 "あと □" 中心 (224,224) + 剩余次数数字 sprite9..13 中心 (229,224)
  (script 265/268; 数字 = maxRetries+8-numRetries 局部 id, 即可续次数 1..5)
- sprite7 "はい" 中心 (224,256), sprite8 "いいえ" 中心 (224,292)
  (script 266/267)

选中项红 0xffff8080 + (-4,-4) 偏移, 未选中半透明灰 0x80808080
(RetryMenu::OnUpdate case 1/2, :870-918)。默认选中 "はい"(curState=1)。
原版无倒计时, 菜单无限等待。
"""
from __future__ import annotations

from pathlib import Path

import pygame

from .sprite_view import GAME_X, GAME_Y, WIN_H, WIN_W, SpriteBank
from .title_view import DEFAULT_DATA

_ASCII = "ascii.anm"
# (sprite 局部 id, 中心 x, 中心 y) —— script 264-267 稳态(op 6 立即到位;
# op 20/21/32/34 的 40 帧缩放入场省略)
_LAYOUT = ((5, 224, 192),    # "コンティニューしますか?"
           (6, 224, 224),    # "あと □"
           (7, 224, 256),    # "はい"
           (8, 224, 292))    # "いいえ"
_DIGIT_CENTER = (229, 224)   # script 268: 数字填进 "あと □" 的框
_DIGIT_SPRITE0 = 9           # sprite 9..13 = 数字 1..5(局部 id)
_YES_ROW = 2                 # _LAYOUT 里 "はい" 的行号

_LIT_COLOR = (255, 128, 128, 255)    # 0xffff8080 选中
_DIM_COLOR = (128, 128, 128, 128)    # 0x80808080 未选中
_LIT_OFFSET = (-4, -4)               # 选中项突出偏移


class ContinueView:
    """续关菜单: render(surf, cursor, retries_left) 画一帧(叠加在游戏面上)。

    surf 为 640x480 的 SRCALPHA 叠加层; 游戏区坐标 + (GAME_X, GAME_Y)。
    """

    def __init__(self, data_path: str | Path = DEFAULT_DATA) -> None:
        self._bank = SpriteBank(data_path)
        self._tinted: dict[tuple[int, bool], pygame.Surface | None] = {}

    def _sprite(self, sid: int, lit: bool) -> pygame.Surface | None:
        """entry2 sprite + 选中/未选中调色(BLEND_RGBA_MULT 乘法)。"""
        key = (sid, lit)
        if key not in self._tinted:
            base = self._bank.sprite(_ASCII, sid, entry=2)
            out = None
            if base is not None:
                out = base.copy()
                color = _LIT_COLOR if lit else _DIM_COLOR
                out.fill(color, special_flags=pygame.BLEND_RGBA_MULT)
            self._tinted[key] = out
        return self._tinted[key]

    def render(self, surf: pygame.Surface, cursor: int,
               retries_left: int) -> None:
        veil = pygame.Surface((WIN_W, WIN_H), pygame.SRCALPHA)
        veil.fill((0, 0, 16, 128))
        surf.blit(veil, (0, 0))
        for row, (sid, cx, cy) in enumerate(_LAYOUT):
            lit = row == _YES_ROW + cursor  # cursor 0=Yes(_LAYOUT[2]), 1=No([3])
            spr = self._sprite(sid, lit)
            if spr is None:
                continue
            dx, dy = _LIT_OFFSET if lit else (0, 0)
            surf.blit(spr, spr.get_rect(
                center=(GAME_X + cx + dx, GAME_Y + cy + dy)))
        if 1 <= retries_left <= 5:
            digit = self._sprite(_DIGIT_SPRITE0 + retries_left - 1, True)
            if digit is not None:
                surf.blit(digit, digit.get_rect(
                    center=(GAME_X + _DIGIT_CENTER[0],
                            GAME_Y + _DIGIT_CENTER[1])))
