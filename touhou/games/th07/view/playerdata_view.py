"""Player Data(Result 画面)渲染(pygame) —— 风格对齐 result_view。

【th07 专属】数据装配直接调 games/th07/playerdata.py(符卡页/统计页语义绑死
th07 的 catk/clrd 口径), 属后端内聚的有意耦合; 新作品复用窗口版需自带
playerdata view。

布局(640x480, 简化版 ResultScreen):
- 背景 result.jpg(缺失回退 title00.jpg, 再退纯色), 上盖半透明面板;
- 页头 "Player Data" + 板块名(分数榜/符卡/统计) + 当前难度/机体页;
- 三板块数据由 games/th07/playerdata.py 装配(纯逻辑), 本层只画:
  分数榜 = 难度×机体 Top10(名次/名字/分数/到达面);
  符卡   = 某机体捕获汇总 + 遇过的符卡捕获数/挑战数(超出首屏截断);
  统计   = 总游玩次数/时间/通关/续关 + clrd 每机体各难度到达面数表;
- 操作: ↑↓ 难度, ←→ 机体, Z 切板块, X/Esc 返回(导航见 screens.PlayerDataFlow)。

资源运行时从 th07.dat 解(GameArchive), 不落盘。
"""

from __future__ import annotations

import io
from pathlib import Path

import pygame

from ....schema.archive import GameArchive
from .. import playerdata
from ....engine.score_store import ScoreStore
from .result_view import _load_font
from .screens import CHARACTERS, DIFFICULTIES, PLAYERDATA_SECTIONS, PlayerDataFlow
from .title_view import DEFAULT_DATA, TITLE_H, TITLE_W

_LABEL = (190, 190, 220)
_VALUE = (240, 240, 240)
_HEADER = (255, 230, 130)
_ACCENT = (150, 255, 180)
_DIM = (120, 120, 150)

# 统计页 clrd 表的难度列缩写
_CLRD_COLS = ["E", "N", "H", "L", "Ex", "Ph"]


class PlayerDataView:
    """Player Data 渲染器: render(surf, flow, store, frame) 画一帧。"""

    def __init__(self, data_path=DEFAULT_DATA) -> None:
        self._bg: pygame.Surface | None = None
        try:
            arc = GameArchive.open(Path(data_path))
            raw = None
            for key in ("result.jpg", "title00.jpg"):
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

    def render(
        self,
        surf: pygame.Surface,
        flow: PlayerDataFlow,
        store: ScoreStore | None,
        frame: int = 0,
    ) -> None:
        if self._bg is not None:
            surf.blit(pygame.transform.scale(self._bg, (TITLE_W, TITLE_H)), (0, 0))
        else:
            surf.fill((16, 16, 40))
        panel = pygame.Surface((560, 420), pygame.SRCALPHA)
        panel.fill((0, 0, 24, 170))
        surf.blit(panel, (40, 24))

        f, fb = self._font, self._font_big
        section = PLAYERDATA_SECTIONS[flow.section]
        diff_name = DIFFICULTIES[flow.difficulty]
        char_name = CHARACTERS[flow.character]
        self._text(surf, fb, f"Player Data - {section}", 70, 40, _HEADER)
        self._text(surf, f, f"难度 {diff_name}    机体 {char_name}", 70, 76, _ACCENT)

        if store is None:
            self._text(surf, f, "(无记录)", 70, 120, _DIM)
        elif flow.section == 0:
            self._render_highscore(surf, f, flow, store)
        elif flow.section == 1:
            self._render_spellcard(surf, f, flow, store)
        else:
            self._render_stats(surf, f, store)

        if frame % 60 < 45:  # 闪烁提示
            self._text(
                surf,
                f,
                "↑↓: 难度  ←→: 机体  Z: 切板块  X/Esc: 返回",
                70,
                TITLE_H - 36,
                _LABEL,
            )

    # ---- 分数榜: 难度×机体 Top10 ----
    def _render_highscore(self, surf, f, flow, store) -> None:
        rows = playerdata.highscore_rows(store, flow.difficulty, flow.character)
        y = 108
        self._text(surf, f, "Rank  Name       Score        Stage", 90, y, _LABEL)
        y += 26
        for i, r in enumerate(rows):
            line = (
                f"{i + 1:>2}.   {r['name']:<9} {r['score']:>12,}   Stage {r['stage']}"
            )
            self._text(surf, f, line, 90, y)
            y += 26

    # ---- 符卡: 捕获汇总 + 遇过的卡 ----
    def _render_spellcard(self, surf, f, flow, store) -> None:
        page = playerdata.spellcard_page(store, flow.character)
        rate = page["successes"] / page["attempts"] * 100.0 if page["attempts"] else 0.0
        self._text(
            surf,
            f,
            f"捕获 {page['captured']}/{page['attempted']} 张"
            f"    次数 {page['successes']}/{page['attempts']}"
            f" ({rate:.1f}%)",
            90,
            108,
            _ACCENT,
        )
        y = 140
        if not page["cards"]:
            self._text(surf, f, "(尚未遇到符卡)", 90, y, _DIM)
            return
        for c in page["cards"][:13]:  # 首屏 13 行(简化: 不做卡页滚动)
            name = c["name"][:24]
            self._text(surf, f, f"No.{c['idx'] + 1:>3}  {name}", 90, y)
            self._text(surf, f, f"{c['successes']}/{c['attempts']}", 480, y)
            y += 22
        if len(page["cards"]) > 13:
            self._text(surf, f, f"... 共 {len(page['cards'])} 张", 90, y, _DIM)

    # ---- 统计: plst 总数 + clrd 通关表 ----
    def _render_stats(self, surf, f, store) -> None:
        st = playerdata.play_stats(store)
        secs = int(st["play_seconds"])
        h, m, s = secs // 3600, secs % 3600 // 60, secs % 60
        rows = [
            ("总游玩次数", f"{st['play_count']}"),
            ("总游玩时间", f"{h:02d}:{m:02d}:{s:02d}"),
            ("通关次数", f"{st['clear_count']}"),
            ("续关次数", f"{st['retry_count']}"),
        ]
        y = 108
        for label, value in rows:
            self._text(surf, f, label, 90, y, _LABEL)
            self._text(surf, f, value, 280, y)
            y += 26
        # clrd 通关到达面数(without_retries; 0 = 未到过, 显示 "-")
        y += 14
        self._text(surf, f, "通关情况(到达面数):", 90, y, _LABEL)
        y += 26
        self._text(
            surf, f, "        " + "  ".join(f"{c:>3}" for c in _CLRD_COLS), 90, y, _DIM
        )
        y += 24
        for ch, c in enumerate(st["clrd"]):
            cells = "  ".join(f"{v:>3}" if v else "  -" for v in c["without_retries"])
            self._text(surf, f, f"{CHARACTERS[ch]:<8}{cells}", 90, y)
            y += 24
