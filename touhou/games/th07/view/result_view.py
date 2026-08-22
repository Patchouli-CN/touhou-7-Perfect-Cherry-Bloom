""" 结算画面渲染(pygame) —— 对照 ResultScreen.cpp 的成绩结算段(简化)。

原版结算画面用 result00.anm 的 41 个 VM 逐行动画 + 8 字符 ascii 名字输入;
本期简化:
- 背景 result.jpg(640x480, th07.dat 解出, 落盘不 Persist)。
- 文字行列出结算项(Score/难度/通关率/Continue/Miss/Bomb/Spellcard/
  点道具/擦弹/Slow%/综合评级) + 本次是否入榜(第几名)。
- 入榜(rank>=0)时进名字输入态(HandleResultKeyboard): ascii.anm 贴字画
  6x16 字表(NAME_ALPHABET) + 8 槽名字(光标槽闪烁 '_'), 选中字脉动放大,
  末行两格用 ascii.anm 0x80/0x81 图标(空格/END)。
- 未入榜: Z/Enter 确认 → 保存 score.json → 回标题(由 view.GameApp 驱动)。
"""

from __future__ import annotations

import io
from pathlib import Path

import pygame

from ....schema.archive import GameArchive
from ....engine.score_store import ScoreStore
from .screens import (CHARACTERS, DIFFICULTIES, NAME_ALPHABET,
                      NAME_ALPHABET_COLS, NAME_LEN)
from ....engine.view.sprite_bank import SpriteBank
from .title_view import DEFAULT_DATA, TITLE_H, TITLE_W

_RATING_COLORS = [
    (100, 200, 255), (120, 255, 160), (255, 240, 140), (255, 160, 120),
]

_ASCII_ANM = "ascii.anm"
# 名字输入字表网格布局 (对照 OnDraw :2385-2430 的 6x16 网格, 适配本面板行距)
_GRID_POS = (160, 380)
_GRID_PITCH = (20, 16)
_NAME_POS = (180, 356)      # 名字槽起点
_NAME_STEP = 16
_CELL_SPRITE = {94: 127, 95: 128}  # 末行两格: ascii.anm 0x80(空格)/0x81(END)
# 选中字 0xffffffc0 脉动放大, 未选中 0xc0c0c0c0 (:2392-2413)
_SEL_COLOR = (255, 255, 192)
_GRID_COLOR = (192, 192, 192)


def _load_font(size: int):
    pygame.font.init()  # 幂等; 无 display 的 headless 测试也要能用字体
    for name in ("Microsoft YaHei", "SimHei", "SimSun", None):
        try:
            return pygame.font.SysFont(name, size)
        except Exception:
            continue
    return pygame.font.Font(None, size)


class ResultScreen:
    """结算画面渲染器: render(surf, result, frame) 画一帧。"""

    def __init__(self, data_path=DEFAULT_DATA) -> None:
        self._bg: pygame.Surface | None = None
        try:
            raw = GameArchive.open(Path(data_path)).load("result.jpg")
            self._bg = pygame.image.load(io.BytesIO(raw)).convert()
        except Exception:
            self._bg = None  # 缺资源时纯色底
        self._font = _load_font(22)
        self._font_big = _load_font(30)
        self._bank = SpriteBank(data_path)  # ascii.anm 字表贴字(懒加载)
        self._tint: dict[tuple[int, tuple[int, int, int]], pygame.Surface] = {}

    def _text(self, surf, font, s: str, x: int, y: int,
              color=(240, 240, 240)) -> None:
        t = font.render(s, True, color)
        surf.blit(t, (x, y))

    # ---- ascii.anm 贴字(同 hud_view: c → sprite ord(c)-1) ----
    def _glyph(self, ch: str, color: tuple[int, int, int]
               ) -> pygame.Surface | None:
        if ch == " ":
            return None
        return self._sprite_glyph(ord(ch) - 1, color)

    def _sprite_glyph(self, sid: int, color: tuple[int, int, int]
                      ) -> pygame.Surface | None:
        base = self._bank.sprite(_ASCII_ANM, sid)
        if base is None:
            return None
        key = (sid, color)
        out = self._tint.get(key)
        if out is None:
            out = base.copy()
            out.fill((*color, 255), special_flags=pygame.BLEND_RGBA_MULT)
            self._tint[key] = out
        return out

    def render(self, surf: pygame.Surface, result: dict, frame: int,
               store: ScoreStore | None = None,
               name_entry=None) -> None:
        if self._bg is not None:
            surf.blit(pygame.transform.scale(self._bg, (TITLE_W, TITLE_H)), (0, 0))
        else:
            surf.fill((16, 16, 40))
        # 半透明底衬提升可读性
        panel = pygame.Surface((520, 400), pygame.SRCALPHA)
        panel.fill((0, 0, 24, 170))
        surf.blit(panel, (60, 30))

        f, fb = self._font, self._font_big
        title = "STAGE CLEAR!!" if result.get("cleared") else "GAME OVER"
        self._text(surf, fb, title, 90, 50,
                   (255, 230, 130) if result.get("cleared") else (255, 120, 120))

        diff = result.get("difficulty", 1)
        char = result.get("character", 0)
        diff_name = DIFFICULTIES[diff] if 0 <= diff < len(DIFFICULTIES) else str(diff)
        char_name = CHARACTERS[char] if 0 <= char < len(CHARACTERS) else str(char)
        rows = [
            ("Difficulty", diff_name),
            ("Character", char_name),
            ("Score", f"{result.get('score', 0):,}"),
            ("Clear %", f"{result.get('clear_percent', 0.0):.2f}%"),
            ("Continue", f"{result.get('retries', 0)}"),
            ("Miss", f"{result.get('deaths', 0)}"),
            ("Bomb", f"{result.get('bombs', 0):g}"),
            ("Spellcard", f"{result.get('spellcards', 0)}"),
            ("Point Items", f"{result.get('point_items', 0)}"),
            ("Graze", f"{result.get('graze', 0)}"),
            ("Slow %", f"{result.get('slow_percent', 0.0):.2f}%"),
        ]
        step = 18 if name_entry is not None else 24  # 名字输入态压缩行距
        y = 96
        for label, value in rows:
            self._text(surf, f, label, 100, y, (190, 190, 220))
            self._text(surf, f, value, 360, y)
            y += step

        rating = result.get("rating", 0.0)
        color = _RATING_COLORS[min(3, max(0, int(rating // 25)))]
        if name_entry is not None:
            # 名字输入态: Rank/入榜行上移(下方留给 NAME 槽 + 字表网格)
            self._text(surf, fb, f"Rank  {rating:.1f}", 100, y + 2, color)
            rank = result.get("rank", -1)
            if rank >= 0:
                self._text(surf, f, f"Hi-Score 入榜: 第 {rank + 1} 名",
                           100, y + 36, (150, 255, 180))
            self._render_name_entry(surf, name_entry, frame)
            return
        self._text(surf, fb, f"Rank  {rating:.1f}", 100, y + 4, color)
        rank = result.get("rank", -1)
        if rank >= 0:
            self._text(surf, f, f"Hi-Score 入榜: 第 {rank + 1} 名",
                       100, y + 44, (150, 255, 180))
        else:
            self._text(surf, f, "未入榜", 100, y + 44, (150, 150, 170))
        if frame % 60 < 40:  # 闪烁提示
            self._text(surf, f, "Z/Enter: 保存并返回标题", 100, TITLE_H - 36,
                       (255, 255, 255))

    # ---- 名字输入态(HandleResultKeyboard + OnDraw :2385-2430) ----
    def _render_name_entry(self, surf, entry, frame: int) -> None:
        # 名字槽: 8 字符, 光标槽闪烁 '_' (:2231-2241 name[cursor]='_')
        self._text(surf, self._font, "NAME", 100, _NAME_POS[1],
                   (190, 190, 220))
        cur = min(entry.cursor, NAME_LEN - 1)
        for i in range(NAME_LEN):
            x = _NAME_POS[0] + i * _NAME_STEP
            img = self._glyph(entry.slots[i], (255, 255, 255))
            if img is not None:
                surf.blit(img, (x, _NAME_POS[1]))
            if i == cur and frame % 40 < 24:
                under = self._glyph("_", (255, 230, 130))
                if under is not None:
                    surf.blit(under, (x, _NAME_POS[1]))
        # 字表网格: 6x16, 选中字脉动放大(原版 1.2x..2.0x), 末两格图标
        phase = (frame % 64) / 32.0
        tri = phase if phase < 1.0 else 2.0 - phase
        scale = 1.2 + 0.8 * tri
        gx, gy = _GRID_POS
        px, py = _GRID_PITCH
        for idx, ch in enumerate(NAME_ALPHABET):
            row, col = divmod(idx, NAME_ALPHABET_COLS)
            cx, cy = gx + col * px, gy + row * py
            if idx == entry.selected:
                img = self._sprite_glyph(_CELL_SPRITE.get(idx, ord(ch) - 1),
                                         _SEL_COLOR) \
                    if idx in _CELL_SPRITE else self._glyph(ch, _SEL_COLOR)
                if img is None:
                    continue
                w = max(1, int(img.get_width() * scale))
                h = max(1, int(img.get_height() * scale))
                big = pygame.transform.scale(img, (w, h))
                surf.blit(big, (cx - (w - 16) // 2, cy - (h - 16) // 2))
            else:
                if idx in _CELL_SPRITE:
                    img = self._sprite_glyph(_CELL_SPRITE[idx], _GRID_COLOR)
                else:
                    img = self._glyph(ch, _GRID_COLOR)
                if img is not None:
                    surf.blit(img, (cx, cy))
