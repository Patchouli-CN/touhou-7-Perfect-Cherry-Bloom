"""得分弹字 + 状态横幅渲染 —— 对照 AsciiManager::DrawPopups / Gui::OnDraw。

【th07 专属】逻辑层(games/th07/globals.py 的 ScorePopup / ZunGlobals.status_popup)
只管数值/计时/生命周期, 本模块只管画:

- 得分弹字 (AsciiManager.cpp:1052-1129 DrawPopups): 收点/清弹得分在道具位置
  跳出的小数字 (8x8, ascii.anm sprite 0-30)。弹字位置为游戏区坐标(逻辑层
  未加窗口偏移), 这里加 (GAME_X, GAME_Y); 每数字起点 = pos.x - 位数*4,
  步进 8; 字形按 timer 三段切换: <52 → sprite d, 52..55 → sprite d+11,
  ≥56 → sprite d+21; value==-1 恒 sprite 10 (48x8 "PowerUp" 字形,
  C++ digits[0]='\n' → sprites[10])。透明度按到自机的距离平方:
  >4096 → 208, 1024..4096 → 80 + (d²-1024)*128/3072, ≤1024 → 80
  (AsciiManager.cpp:1085-1103)。
- 状态横幅 (Gui.cpp:277-317 OnDraw + :1329-1347 滑入): "Full Power Mode!" /
  "CherryPoint Max!" 等, 16x16 ascii 字形 (c → sprite ord(c)-1), 步进 14
  (结界系 0.9 倍横向缩放 + 步进 11, Gui.cpp:286-296); 前 30 帧从 x=416
  滑入到 x=104, y=168 (Gui.cpp:1331-1341)。

数字/文字均用 ascii.anm 贴字(原版同款), 不用 pygame 字体。
"""

from __future__ import annotations

from pathlib import Path

import pygame

from ..globals import (
    BONUS_SCORE_SLIDE_FRAMES,
    STATUS_BORDER,
    STATUS_BORDER_BONUS,
    STATUS_CHERRY_MAX,
    STATUS_FULL_POWER,
)
from ....engine.view.sprite_bank import SpriteBank
from .sprite_view import GAME_X, GAME_Y

_ASCII = "ascii.anm"

# 状态横幅文本/颜色 (Gui.cpp:277-317; 颜色 ARGB 取 RGB)
_STATUS_TEXT = {
    STATUS_FULL_POWER: ("Full Power Mode!", (192, 176, 255), 14, 1.0),
    STATUS_BORDER: ("Supernatural Border!!", (224, 176, 255), 11, 0.9),
    STATUS_CHERRY_MAX: ("CherryPoint Max!", (192, 176, 255), 14, 1.0),
    STATUS_BORDER_BONUS: ("Border Bonus ", (224, 176, 255), 11, 0.9),
}

# 横幅滑入 (Gui.cpp:1331-1341): timer<30 时 x = 416 - timer*312/30, 之后 104
_STATUS_Y = 168.0
_STATUS_X_START = 416.0
_STATUS_X_END = 104.0
_STATUS_SLIDE_FRAMES = 30


class PopupView:
    """得分弹字 + 状态横幅渲染器。资源懒加载(SpriteBank 首次取值才开包)。"""

    def __init__(self, data_path: str | Path) -> None:
        self.bank = SpriteBank(data_path)
        self._tint: dict[tuple[int, tuple[int, int, int]], pygame.Surface] = {}

    # ---- 贴图工具 ----
    def _glyph(self, sid: int, color: tuple[int, int, int]) -> pygame.Surface | None:
        """按 (sprite id, RGB) 缓存的着色字形 (BLEND_RGBA_MULT)。"""
        base = self.bank.sprite(_ASCII, sid)
        if base is None:
            return None
        key = (sid, color)
        out = self._tint.get(key)
        if out is None:
            out = base.copy()
            out.fill((*color, 255), special_flags=pygame.BLEND_RGBA_MULT)
            self._tint[key] = out
        return out

    # ---- 得分弹字 (AsciiManager::DrawPopups) ----
    def _render_popups(self, surf: pygame.Surface, game) -> None:
        g = game.globals
        if not g.popups:
            return
        # 距离衰减用自机窗口坐标 (AsciiManager.cpp:1085-1087)
        px = game.player.pos.x + GAME_X
        py = game.player.pos.y + GAME_Y
        for p in g.popups:
            rgb = ((p.color >> 16) & 0xFF, (p.color >> 8) & 0xFF, p.color & 0xFF)
            digits = str(p.value) if p.value >= 0 else "\n"  # -1 → PowerUp 字形
            x = p.pos.x + GAME_X - (len(digits) << 2)  # :1081 count<<2
            y = p.pos.y + GAME_Y
            dx, dy = px - (p.pos.x + GAME_X), py - (p.pos.y + GAME_Y)
            d2 = dx * dx + dy * dy
            if d2 > 4096:
                alpha = 208
            elif d2 > 1024:
                alpha = int(80 + (d2 - 1024) * 128 / 3072)
            else:
                alpha = 80
            for ch in digits:
                d = 10 if ch == "\n" else ord(ch) - ord("0")
                # 字形三段切换 (:1109-1123); PowerUp 字形恒 sprite 10
                if p.timer < 52 or d == 10:
                    sid = d
                elif p.timer < 56:
                    sid = d + 11
                else:
                    sid = d + 21
                img = self._glyph(sid, rgb)
                if img is not None:
                    img.set_alpha(alpha)
                    surf.blit(img, (int(x), int(y)))
                x += 8.0

    # ---- 状态横幅 (Gui::OnDraw 的 statusPopup 段) ----
    def _render_status_popup(self, surf: pygame.Surface, game) -> None:
        g = game.globals
        entry = _STATUS_TEXT.get(g.status_popup)
        if entry is None:
            return
        text, rgb, step, xscale = entry
        if g.status_popup == STATUS_BORDER_BONUS:
            text = f"Border Bonus {g.status_popup_arg:7d}"  # "Border Bonus %7d"
        t = g.status_popup_timer
        if t < _STATUS_SLIDE_FRAMES:
            x = (
                _STATUS_X_START
                - t * (_STATUS_X_START - _STATUS_X_END) / _STATUS_SLIDE_FRAMES
            )
        else:
            x = _STATUS_X_END
        for ch in text:
            if ch == " ":
                x += step
                continue
            img = self._glyph(ord(ch) - 1, rgb)  # c → sprite ord(c)-1 (同 hud_view)
            if img is not None:
                if xscale != 1.0:
                    img = pygame.transform.scale(
                        img, (max(1, round(img.get_width() * xscale)), img.get_height())
                    )
                surf.blit(img, (int(x), int(_STATUS_Y)))
            x += step

    # ---- 清场奖励横幅 (Gui bonusScore 段, Gui.cpp:270-275 + :1309-1328) ----
    def _render_bonus_score(self, surf: pygame.Surface, game) -> None:
        """ "BONUS %8d": 前 30 帧从 x=416 滑入到 104, y=48, 白字 16px。"""
        g = game.globals
        if not getattr(g, "bonus_score", 0):
            return
        t = g.bonus_score_timer
        if t < BONUS_SCORE_SLIDE_FRAMES:
            x = 416.0 - t * 312.0 / BONUS_SCORE_SLIDE_FRAMES  # Gui.cpp:1311-1319
        else:
            x = 104.0
        for ch in f"BONUS {g.bonus_score:8d}":
            if ch == " ":
                x += 14.0
                continue
            img = self._glyph(ord(ch) - 1, (255, 255, 255))
            if img is not None:
                surf.blit(img, (int(x), 48))
            x += 14.0

    # ---- 符卡捕获奖励横幅 (Gui spellCardBonus 段, Gui.cpp:318-335) ----
    def _render_spellcard_bonus(self, surf: pygame.Surface, game) -> None:
        """ "Spell Card Bonus!"(红) + "+%d"(2 倍粉字), 居中, 无滑入。"""
        g = game.globals
        if not getattr(g, "spellcard_bonus", 0):
            return
        title = "Spell Card Bonus!"
        x = (384.0 - len(title) * 16.0) / 2.0 + 32.0  # Gui.cpp:321
        for ch in title:
            img = self._glyph(ord(ch) - 1, (255, 0, 0))  # 0xffff0000
            if img is not None:
                surf.blit(img, (int(x), 80))
            x += 14.0
        num = f"+{g.spellcard_bonus}"  # Gui.cpp:327 "+%d"
        x = (384.0 - len(num) * 32.0) / 2.0 + 32.0  # :328-330 (2 倍宽)
        for ch in num:
            img = self._glyph(ord(ch) - 1, (255, 128, 128))  # 0xffff8080
            if img is not None:
                img = pygame.transform.scale(
                    img, (img.get_width() * 2, img.get_height() * 2)
                )
                surf.blit(img, (int(x), 96))
            x += 32.0

    # ---- 对外 ----
    def render(self, surf: pygame.Surface, game) -> None:
        """画得分弹字 + 状态横幅(640x480 窗口面; 樱点槽之后调用)。"""
        self._render_popups(surf, game)
        self._render_status_popup(surf, game)
        self._render_bonus_score(surf, game)
        self._render_spellcard_bonus(surf, game)


__all__ = ["PopupView"]
