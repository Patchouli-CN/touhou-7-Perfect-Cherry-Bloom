"""Music Room 渲染(pygame) —— 风格对齐 playerdata_view。

布局(640x480, 简化版 MusicRoom.cpp OnDraw :189-229):
- 背景 music.jpg(缺失回退 result.jpg/title00.jpg, 再退纯色), 上盖半透明面板;
- 左栏曲目表: 一屏 10 首(OnDraw listingOffset..+10), 序号 "%2d." + 曲名,
  光标行高亮 + 左侧 ">", 播放中的曲目标 "♪";
- 右栏评论: 光标曲的评论(musiccmt.txt, ≤8 行, TrackDescriptor.description);
- 操作: ↑↓ 选曲, Z 播放/停止, X/Esc 返回(导航见 screens.MusicRoomFlow)。

资源运行时从 th07.dat 解(open_archive), 不落盘。
"""

from __future__ import annotations

import io
from pathlib import Path

import pygame

from ....schema.archive import open_archive
from .result_view import _load_font
from .screens import MUSIC_ROOM_VISIBLE, MusicRoomFlow, load_tracks  # noqa: F401 (load_tracks 再导出, 单一来源在 screens)
from .title_view import DEFAULT_DATA, TITLE_H, TITLE_W

_LABEL = (190, 190, 220)
_VALUE = (240, 240, 240)
_HEADER = (255, 230, 130)
_ACCENT = (150, 255, 180)
_DIM = (120, 120, 150)
_PLAYING = (255, 190, 220)


class MusicRoomView:
    """Music Room 渲染器: render(surf, flow, frame) 画一帧。"""

    def __init__(self, data_path=DEFAULT_DATA) -> None:
        self._bg: pygame.Surface | None = None
        try:
            arc = open_archive(Path(data_path), game="th07")
            raw = None
            for key in ("music.jpg", "result.jpg", "title00.jpg"):
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
        self._font_small = _load_font(16)

    def _text(self, surf, font, s: str, x: int, y: int, color=_VALUE) -> None:
        surf.blit(font.render(s, True, color), (x, y))

    @staticmethod
    def _fit(font, s: str, max_w: int) -> str:
        """按像素宽截断(超出补 …) —— 日文全角宽, 按字符数切会撞栏。"""
        if font.size(s)[0] <= max_w:
            return s
        while s and font.size(s + "…")[0] > max_w:
            s = s[:-1]
        return s + "…"

    def _wrap(self, font, s: str, max_w: int) -> list[str]:
        """按像素宽折行(评论栏用)。"""
        out: list[str] = []
        while s:
            if font.size(s)[0] <= max_w:
                out.append(s)
                break
            cut = s
            while cut and font.size(cut)[0] > max_w:
                cut = cut[:-1]
            if not cut:
                break
            out.append(cut)
            s = s[len(cut) :]
        return out

    def render(self, surf: pygame.Surface, flow: MusicRoomFlow, frame: int = 0) -> None:
        if self._bg is not None:
            surf.blit(pygame.transform.scale(self._bg, (TITLE_W, TITLE_H)), (0, 0))
        else:
            surf.fill((16, 12, 32))
        panel = pygame.Surface((580, 420), pygame.SRCALPHA)
        panel.fill((0, 0, 24, 170))
        surf.blit(panel, (30, 24))

        f, fb = self._font, self._font_big
        self._text(surf, fb, "Music Room", 60, 40, _HEADER)

        if not flow.tracks:
            self._text(surf, f, "(无曲目数据 musiccmt.txt)", 60, 100, _DIM)
        else:
            self._render_track_list(surf, f, flow)
            self._render_comment(surf, f, flow)

        if frame % 60 < 45:  # 闪烁提示
            self._text(
                surf, f, "↑↓: 选曲  Z: 播放/停止  X/Esc: 返回", 60, TITLE_H - 36, _LABEL
            )

    # ---- 左栏: 曲目表(一屏 MUSIC_ROOM_VISIBLE 首) ----
    def _render_track_list(self, surf, f, flow: MusicRoomFlow) -> None:
        n = len(flow.tracks)
        y = 92
        for i in range(
            flow.listing_offset, min(flow.listing_offset + MUSIC_ROOM_VISIBLE, n)
        ):
            track = flow.tracks[i]
            lit = i == flow.cursor
            playing = i == flow.playing
            color = _ACCENT if lit else (_PLAYING if playing else _VALUE)
            if lit:
                self._text(surf, f, ">", 44, y, _ACCENT)
            mark = "*" if playing else " "  # 播放中标记(♪ 字体缺字形, 用 *)
            title = self._fit(f, f"{mark} {i + 1:>2}. {track.title}", 290)
            self._text(surf, f, title, 60, y, color)
            y += 28
        if n > MUSIC_ROOM_VISIBLE:
            self._text(surf, f, f"({flow.cursor + 1}/{n})", 60, y + 4, _DIM)

    # ---- 右栏: 光标曲评论(musiccmt.txt, 像素宽折行) ----
    def _render_comment(self, surf, f, flow: MusicRoomFlow) -> None:
        track = flow.tracks[flow.cursor]
        fs = self._font_small
        x = 372
        self._text(surf, f, f"No.{flow.cursor + 1}", x, 92, _HEADER)
        y = 126
        lines: list[str] = []
        for line in track.comment:
            lines.extend(self._wrap(fs, line, 250))
        for line in lines[:15]:  # 栏高有限, 超出截断
            self._text(surf, fs, line, x, y)
            y += 21
