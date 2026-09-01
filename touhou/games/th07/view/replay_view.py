"""Replay 选择画面渲染(pygame) —— 风格对齐 playerdata_view。

布局(640x480, 简化版 MainMenu.cpp STATE_SELECT_REPLAY 的列表):
- 背景 select00.jpg(缺失回退 title00.jpg, 再退纯色), 上盖半透明面板;
- 列表每行: No. + 机体/难度/起始关 + 帧数 + 录制时间;
- 操作: ↑↓ 选录像, Z 播放, X/Esc 返回(导航见 screens.ReplayFlow)。
"""

from __future__ import annotations

import io
from pathlib import Path

import pygame

from ....schema.archive import open_archive
from .result_view import _load_font
from .screens import CHARACTERS, DIFFICULTIES, ReplayFlow
from .title_view import DEFAULT_DATA, TITLE_H, TITLE_W

_LABEL = (190, 190, 220)
_VALUE = (240, 240, 240)
_HEADER = (255, 230, 130)
_ACCENT = (150, 255, 180)
_DIM = (120, 120, 150)


class ReplayView:
    """Replay 选择渲染器: render(surf, flow, frame) 画一帧。"""

    def __init__(self, data_path=DEFAULT_DATA) -> None:
        self._bg: pygame.Surface | None = None
        try:
            arc = open_archive(Path(data_path), game="th07")
            raw = None
            for key in ("select00.jpg", "title00.jpg"):
                try:
                    raw = arc.load(key)
                    break
                except KeyError:
                    continue
            if raw is not None:
                img = pygame.image.load(io.BytesIO(raw))
                try:
                    img = img.convert()
                except pygame.error:
                    pass  # headless 无 display 时用未转换面
                self._bg = img
        except Exception:
            self._bg = None  # 缺资源时纯色底
        self._font = _load_font(20)
        self._font_big = _load_font(28)

    def _text(self, surf, font, s: str, x: int, y: int, color=_VALUE) -> None:
        surf.blit(font.render(s, True, color), (x, y))

    def render(self, surf: pygame.Surface, flow: ReplayFlow, frame: int = 0) -> None:
        if self._bg is not None:
            surf.blit(pygame.transform.scale(self._bg, (TITLE_W, TITLE_H)), (0, 0))
        else:
            surf.fill((16, 16, 40))
        panel = pygame.Surface((560, 420), pygame.SRCALPHA)
        panel.fill((0, 0, 24, 170))
        surf.blit(panel, (40, 24))

        f, fb = self._font, self._font_big
        self._text(surf, fb, "Replay", 70, 40, _HEADER)

        if not flow.entries:
            self._text(
                surf,
                f,
                "(replays/ 下没有录像; 游戏内 Esc 暂停菜单 选 Save Replay 录制)",
                70,
                120,
                _DIM,
            )
        else:
            y = 96
            for i, e in enumerate(flow.entries[:16]):  # 首屏 16 条
                meta = e["meta"]
                ch = int(meta.get("character", 0)) % len(CHARACTERS)
                dif = int(meta.get("difficulty", 1)) % len(DIFFICULTIES)
                line = (
                    f"No.{i + 1:>2}  {CHARACTERS[ch]:<8} "
                    f"{DIFFICULTIES[dif]:<8} Stage {meta.get('stage', 1)}"
                    f"  {meta.get('frames', 0):>6}f  "
                    f"{str(meta.get('created', ''))[:16]}"
                )
                color = _ACCENT if i == flow.cursor else _VALUE
                if i == flow.cursor:
                    self._text(surf, f, ">", 60, y, _ACCENT)
                self._text(surf, f, line, 78, y, color)
                y += 26

        if frame % 60 < 45:  # 闪烁提示
            self._text(
                surf,
                f,
                "↑↓: 选择  Z: 播放 (播放中 Esc 退出)  X/Esc: 返回",
                70,
                TITLE_H - 36,
                _LABEL,
            )
