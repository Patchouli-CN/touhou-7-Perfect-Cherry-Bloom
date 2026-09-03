"""th08 右侧 HUD 面板 + 时刻表盘 + 妖率计 —— 对照 th08-ref Gui/AsciiManager。

【th08 专属】布局数值来自反编译源码 + anm 脚本静态求值(AnmVmTh08 跑到稳态,
interrupt 1 滑入后取值); 坐标为 640x480 窗口坐标。

- 窗口框(Gui.cpp:1189-1230): front.anm sprite13(32x32) 左列 x=0 与右栏
  x=416..624 平铺, sprite14(128x16) 上沿 y=0 / 下沿 y=464。
- 行底衬: sprite15(160x16) 左上 (480,40/56/88/104/136/152/168/184) 与
  (512,464) (Gui.cpp:1149-1186; 原版部分行仅值刷新帧画, 这里常画, 近似)。
- 标签(front.anm script 2-9 稳态, ANM_22 左上锚): HiScore/Score/Player/
  Bomb/Power/Graze/Point/Time 左上 (432,40/56/88/104/136/152/168/184)。
- 装饰: sprite0 左上 (480,208), sprite1 中心锚 (512,416) scale 1.12
  (front.anm script 0/1 稳态, Gui.cpp:1213-1214)。
- 数值(ascii.anm 16x16 字形, 字符 c → 扁平 sprite ord(c),
  AsciiManager.cpp:1592 GetSprite(*charPtr); 步进 13 = spaceWidth,
  :240): HiScore (488,40) %.9d + continues 后缀 +117, Score (488,56)
  %.9d + retries 后缀 (Gui.cpp:1264-1278); Graze (488,152) %d;
  Point (488,168) %d/%d; Time (488,184) 时刻符点 %d/%d, 达标变色
  0xfff0c0 (Gui.cpp:1295-1307)。
- 残机/炸弹星: sprite11/12(16x16) 左上 (488+i*16, 88/104)
  (Gui.cpp:1232-1248)。
- Power: 渐变条 (488,136)-(488+power,152), 顶点色 0xe0e0e0ff →
  0x80e0e0ff (Gui.cpp:1311-1346); 数字(power<128)或 "MAX" (488,136)。
- 时刻表盘: times.anm script 2 稳态中心锚 (320,96), sprite = 时刻单位
  (Gui.cpp:2006-2007 StartStageBackgroundSequence SetSprite(GetClockTime));
  op180 隐藏(clock.hidden)时不画 (Gui.cpp:2032-2035 HideClockTime)。
- 妖率计(AsciiManager::OnDrawHighPrioImpl, AsciiManager.cpp:1737-1807):
  ascii.anm script 5 槽(128x16, 左上锚 (32,449), interrupt 1 滑入稳态),
  script 6/7 人/妖图标(左上 (88,449) 再按槽界偏移 :256-257),
  script 8 游标(8x12, 中心锚 (144,453)), 游标 x = gauge*112/2/10000
  + 槽x+64 (:1739-1740); 槽/百分比按区间变色 (:1749-1778);
  槽下方点道具分值(sprite 136+d 的 8x12 小数字, 去前导零, :1789-1806)。

数字用 ascii.anm 贴字(原版同款), 不用 pygame 字体。
"""

from __future__ import annotations

from pathlib import Path

import pygame

from ....engine.view.anm_fx import AnmScriptBank
from ....engine.view.anm_vm import reset_and_run
from ....engine.view.sprite_bank import SpriteBank
from .anm_vm import AnmVmTh08
from .sprite_view import WIN_H, WIN_W

_FRONT = "front.anm"
_ASCII = "ascii.anm"
_TIMES = "times.anm"

# 右栏数值行 (Gui.cpp:1264-1307)
_VALUE_X = 488
_ROW_HISCORE, _ROW_SCORE = 40, 56
_ROW_LIVES, _ROW_BOMBS, _ROW_POWER = 88, 104, 136
_ROW_GRAZE, _ROW_POINT, _ROW_TIME = 152, 168, 184
# 行底衬 sprite15 的左上 y (Gui.cpp:1149-1186)
_UNDERLINE_YS = (40, 56, 88, 104, 136, 152, 168, 184)

# 妖率计区间色 (AsciiManager.cpp:1749-1778)
_GAUGE_COLORS = {
    "extreme_human": (112, 112, 255),
    "moderate_human": (176, 176, 255),
    "extreme_youkai": (255, 112, 112),
    "moderate_youkai": (255, 176, 176),
    "neutral": (255, 255, 255),
}
# 时刻符点达标色 (Gui.cpp:1298 SetColor(0xfffff0c0))
_TIME_READY_COLOR = (255, 240, 192)

_FPS_POS = (190, 466)

# 妖率计稳态布局(脚本 interrupt 1 滑入后静态求值; 见模块 docstring)
_GAUGE_POS = (32.0, 449.0)  # script 5 槽左上
_GAUGE_ICON_BASE_X = 88.0  # script 6/7 图标滑入稳态 x (再按槽界偏移)
_GAUGE_CURSOR_POS = (144.0, 453.0)  # script 8 游标中心锚稳态
_GAUGE_ICON_OFFSET_SCALE = 56.0 / 10000.0  # 槽界→像素 (AsciiManager.cpp:256-257)
_GAUGE_CURSOR_SCALE = 112.0 / 2.0 / 10000.0  # 游标 (:1739-1740)


class HudView:
    """th08 右栏 HUD + 时刻表盘 + 妖率计渲染器。资源懒加载。"""

    def __init__(self, data_path: str | Path) -> None:
        self.bank = SpriteBank(data_path, game="th08")
        self._sbanks: dict[str, AnmScriptBank] = {}
        self._tint: dict[tuple, pygame.Surface] = {}
        # 静态窗口框缓存: 每帧不变的 tile/标签合成一次后整面 blit
        self._chrome: pygame.Surface | None = None
        self._power_bar: pygame.Surface | None = None
        self._fps_font: pygame.font.Font | None = None
        # 脚本稳态求值缓存: (anm, 扁平脚本 id, interrupt) → (img, pos, anchor, scale)
        self._steady: dict[tuple, tuple | None] = {}

    # ---- 脚本表(扁平序号空间) ----
    def _sbank(self, name: str) -> AnmScriptBank | None:
        sb = self._sbanks.get(name)
        if sb is None:
            sb = AnmScriptBank(self.bank, name, 0)
            self._sbanks[name] = sb
        return sb if sb.ok else None

    def _script_steady(self, name: str, gid: int, interrupt: int = 0):
        """把 anm 脚本跑到稳态, 返回 (sprite Surface, pos, anchor, scale)。

        布局求值工具(对照 scratch_dbg/anm_layout.py 的手工程序): interrupt
        非 0 时先发 interrupt 再跑(滑入类脚本, 如妖率计,
        AsciiManager.cpp:245-259)。结果按 (name, gid, interrupt) 缓存。
        """
        key = (name, gid, interrupt)
        if key in self._steady:
            return self._steady[key]
        out = self._script_steady_uncached(name, gid, interrupt)
        self._steady[key] = out
        return out

    def _script_steady_uncached(self, name: str, gid: int, interrupt: int):
        sb = self._sbank(name)
        if sb is None:
            return None
        ref = sb.ref_global(gid)
        if ref is None:
            return None
        vm = AnmVmTh08()
        seen: list[int] = []

        def cb(g: int) -> None:
            seen.append(g)

        reset_and_run(vm, ref, cb)
        if interrupt:
            vm.pending_interrupt = interrupt
        for _ in range(150):
            vm.execute()
        if not seen:
            return None
        img = sb.sprite_surf(seen[-1])
        if img is None:
            return None
        return img, (float(vm.pos[0]), float(vm.pos[1])), vm.anchor, list(vm.scale)

    # ---- 贴图工具 ----
    @staticmethod
    def _blit_at(
        surf: pygame.Surface,
        img: pygame.Surface | None,
        x: float,
        y: float,
        anchor: int = 3,
    ) -> None:
        """锚点 blit: anchor=3 左上 (ANM_22), 否则中心锚。"""
        if img is None:
            return
        if anchor == 3:
            surf.blit(img, (int(x), int(y)))
        else:
            surf.blit(
                img, (int(x) - img.get_width() // 2, int(y) - img.get_height() // 2)
            )

    def _glyph(
        self, ch: str, color: tuple[int, int, int] = (255, 255, 255)
    ) -> pygame.Surface | None:
        """ascii 字形: 扁平 sprite ord(c) (AsciiManager.cpp:1592); 按颜色缓存。"""
        if not (32 <= ord(ch) < 160):
            return None
        sb = self._sbank(_ASCII)
        if sb is None:
            return None
        base = sb.sprite_surf(ord(ch))
        if base is None:
            return None
        key = (ord(ch), color)
        out = self._tint.get(key)
        if out is None:
            out = base.copy()
            out.fill((*color, 255), special_flags=pygame.BLEND_RGBA_MULT)
            self._tint[key] = out
        return out

    def _small_glyph(
        self, ch: str, color: tuple[int, int, int]
    ) -> pygame.Surface | None:
        """8x12 小数字: 扁平 sprite 136+d (AsciiManager.cpp:1800 DrawPercentage
        系); '-'=148 '%'=146 (AsciiManager.cpp:1849/:1940)。"""
        if ch.isdigit():
            sid = 136 + int(ch)
        elif ch == "-":
            sid = 148
        elif ch == "%":
            sid = 146
        else:
            return None
        sb = self._sbank(_ASCII)
        if sb is None:
            return None
        base = sb.sprite_surf(sid)
        if base is None:
            return None
        key = (sid, color)
        out = self._tint.get(key)
        if out is None:
            out = base.copy()
            out.fill((*color, 255), special_flags=pygame.BLEND_RGBA_MULT)
            self._tint[key] = out
        return out

    def _draw_text(
        self,
        surf: pygame.Surface,
        x: float,
        y: float,
        s: str,
        color: tuple[int, int, int] = (255, 255, 255),
    ) -> None:
        """ascii 贴字(左上锚, 步进 13 = spaceWidth, AsciiManager.cpp:240)。"""
        for ch in s:
            if ch == " ":
                x += 13
                continue
            img = self._glyph(ch, color)
            if img is not None:
                surf.blit(img, (int(x), int(y)))
            x += 13

    # ---- 窗口框/标签(Gui.cpp:1189-1230; front.anm 全部左上锚) ----
    def _render_chrome(self, surf: pygame.Surface) -> None:
        if self._chrome is not None:
            surf.blit(self._chrome, (0, 0))
            return
        cache = pygame.Surface((WIN_W, WIN_H), pygame.SRCALPHA)
        self._render_chrome_uncached(cache)
        self._chrome = cache
        surf.blit(cache, (0, 0))

    def _render_chrome_uncached(self, surf: pygame.Surface) -> None:
        sb = self._sbank(_FRONT)
        if sb is None:
            return
        tile = sb.sprite_surf(13)
        if tile is not None:
            for y in range(0, 465, 32):  # 左列 (Gui.cpp:1192-1196)
                self._blit_at(surf, tile, 0, y)
            for x in range(416, 625, 32):  # 右栏 (:1197-1204)
                for y in range(16, 465, 32):
                    self._blit_at(surf, tile, x, y)
        strip = sb.sprite_surf(14)
        if strip is not None:
            for x in range(0, 625, 128):  # 上/下沿 (:1205-1212)
                self._blit_at(surf, strip, x, 0)
                self._blit_at(surf, strip, x, 464)
            self._blit_at(surf, strip, 512, 464)
        underline = sb.sprite_surf(15)
        if underline is not None:
            for y in _UNDERLINE_YS:  # 行底衬 (:1149-1186)
                self._blit_at(surf, underline, 480, y)
        # 标签 script 2-9 (稳态左上锚 x=432)
        for gid in range(2, 10):
            st = self._script_steady(_FRONT, gid)
            if st is not None:
                img, (x, y), anchor, _scale = st
                self._blit_at(surf, img, x, y, anchor)
        # 装饰 logo (script 0/1)
        st0 = self._script_steady(_FRONT, 0)
        if st0 is not None:
            img, (x, y), anchor, _ = st0
            self._blit_at(surf, img, x, y, anchor)
        st1 = self._script_steady(_FRONT, 1)
        if st1 is not None:
            img, (x, y), anchor, scale = st1
            if scale != [1.0, 1.0]:
                img = pygame.transform.scale(
                    img,
                    (
                        max(1, round(img.get_width() * scale[0])),
                        max(1, round(img.get_height() * scale[1])),
                    ),
                )
            self._blit_at(surf, img, x, y, anchor)

    # ---- 数值行(Gui.cpp:1232-1355) ----
    def _render_stats(self, surf: pygame.Surface, game) -> None:
        g = game.globals
        # HiScore: globals.high_score 随显示分实时同步 (tick_high_score)
        high = getattr(g, "high_score", 0)
        if not high:
            store = getattr(game, "store", None)
            if store is not None:
                try:
                    high = store.high_score(game.difficulty, game.character)
                except (AttributeError, TypeError):
                    high = 0
        self._draw_text(surf, _VALUE_X, _ROW_HISCORE, f"{high:09d}")
        self._draw_text(
            surf, _VALUE_X + 117, _ROW_HISCORE, f"{g.high_score_num_continues % 10}"
        )
        self._draw_text(surf, _VALUE_X, _ROW_SCORE, f"{g.gui_score:09d}")
        self._draw_text(surf, _VALUE_X + 117, _ROW_SCORE, f"{g.num_retries % 10}")
        sb = self._sbank(_FRONT)
        if sb is not None:
            star = sb.sprite_surf(11)
            for i in range(max(0, int(g.lives_remaining))):
                self._blit_at(surf, star, _VALUE_X + i * 16, _ROW_LIVES)
            bomb = sb.sprite_surf(12)
            for i in range(max(0, int(g.bombs_remaining))):
                self._blit_at(surf, bomb, _VALUE_X + i * 16, _ROW_BOMBS)
        # Power 渐变条 + 数字/MAX (Gui.cpp:1311-1355; 顶点色 ARGB e0e0e0ff→80e0e0ff)
        power = max(0, min(128, int(g.current_power)))
        if power > 0:
            if self._power_bar is None:
                bar = pygame.Surface((128, 16), pygame.SRCALPHA)
                for x in range(128):
                    a = 224 + (128 - 224) * x // 127
                    pygame.draw.line(bar, (224, 224, 255, a), (x, 0), (x, 15))
                self._power_bar = bar
            surf.blit(self._power_bar, (_VALUE_X, _ROW_POWER), (0, 0, power, 16))
        if power < 128:
            self._draw_text(surf, _VALUE_X, _ROW_POWER, f"{power}")
        else:
            self._draw_text(surf, _VALUE_X, _ROW_POWER, "MAX")
        self._draw_text(surf, _VALUE_X, _ROW_GRAZE, f"{g.graze_in_total}")
        self._draw_text(
            surf,
            _VALUE_X,
            _ROW_POINT,
            f"{g.point_items_collected}/{g.next_point_item_extend_threshold}",
        )
        # Time 行: 时刻符点/阈值, 达标变色 (Gui.cpp:1295-1307)
        ready = g.current_time_orbs >= g.last_spell_time_orb_threshold > 0
        self._draw_text(
            surf,
            _VALUE_X,
            _ROW_TIME,
            f"{g.current_time_orbs}/{g.last_spell_time_orb_threshold}",
            _TIME_READY_COLOR if ready else (255, 255, 255),
        )

    # ---- 时刻表盘(times.anm script 2, sprite = 时刻单位) ----
    def _render_clock(self, surf: pygame.Surface, game) -> None:
        host = getattr(game, "ecl_host", None)
        clock = getattr(host, "clock", None)
        if clock is None or clock.hidden:
            return
        sb = self._sbank(_TIMES)
        if sb is None:
            return
        st = self._script_steady(_TIMES, 2)
        if st is None:
            return
        _img, (x, y), anchor, _scale = st
        dial = sb.sprite_surf(min(12, max(0, clock.units)))
        self._blit_at(surf, dial, x, y, anchor)

    # ---- 妖率计(AsciiManager.cpp:1737-1807) ----
    def _gauge_zone_color(self, g) -> tuple[int, int, int]:
        """区间色 (AsciiManager.cpp:1749-1778; 槽界判定在 globals)。"""
        if g.gauge_is_extremely_human():
            return _GAUGE_COLORS["extreme_human"]
        if g.gauge_is_moderately_human():
            return _GAUGE_COLORS["moderate_human"]
        if g.gauge_is_extremely_youkai():
            return _GAUGE_COLORS["extreme_youkai"]
        if g.gauge_is_moderately_youkai():
            return _GAUGE_COLORS["moderate_youkai"]
        return _GAUGE_COLORS["neutral"]

    def _render_gauge(self, surf: pygame.Surface, game) -> None:
        g = game.globals
        sb = self._sbank(_ASCII)
        if sb is None:
            return
        color = self._gauge_zone_color(g)
        gx, gy = _GAUGE_POS
        bar = sb.sprite_surf(155)
        if bar is not None:
            # 槽体按区间变色 (:1780 color1 = percentageText 的区间色)
            key = (155, color)
            tinted = self._tint.get(key)
            if tinted is None:
                tinted = bar.copy()
                tinted.fill((*color, 255), special_flags=pygame.BLEND_RGBA_MULT)
                self._tint[key] = tinted
            self._blit_at(surf, tinted, gx, gy)
        # 人/妖图标: 槽界偏移 (:256-257)
        icon_h = sb.sprite_surf(153)
        self._blit_at(
            surf,
            icon_h,
            _GAUGE_ICON_BASE_X + g.gauge_bounds[0] * _GAUGE_ICON_OFFSET_SCALE,
            gy,
        )
        icon_y = sb.sprite_surf(154)
        self._blit_at(
            surf,
            icon_y,
            _GAUGE_ICON_BASE_X + g.gauge_bounds[1] * _GAUGE_ICON_OFFSET_SCALE,
            gy,
        )
        # 游标: x = gauge*112/2/10000 + 槽x+64 (:1739-1740)
        cursor = sb.sprite_surf(152)
        if cursor is not None:
            key = (152, color)
            tinted = self._tint.get(key)
            if tinted is None:
                tinted = cursor.copy()
                tinted.fill((*color, 255), special_flags=pygame.BLEND_RGBA_MULT)
                self._tint[key] = tinted
            cx = g.youkai_gauge * _GAUGE_CURSOR_SCALE + gx + 64
            self._blit_at(surf, tinted, cx, _GAUGE_CURSOR_POS[1], anchor=0)
        # 百分比: gauge/100, 小数字贴字 (:1786 DrawPercentage, 简化定步进)
        pct = g.youkai_gauge // 100
        self._draw_small(surf, cx - 14, gy + 11, f"{pct}%", color)
        # 槽下方点道具分值 (:1789-1806: 去前导零, sprite 136+d, 步进 7)
        self._draw_small(
            surf,
            gx + 62 - 14,
            gy + 3 + 8,
            f"{g.point_item_value}",
            color,
            skip_leading_zeros=True,
        )

    def _draw_small(
        self,
        surf: pygame.Surface,
        x: float,
        y: float,
        s: str,
        color: tuple[int, int, int],
        *,
        skip_leading_zeros: bool = False,
    ) -> None:
        """8x12 小数字贴字(左上锚, 步进 7, AsciiManager.cpp:1729/:1802)。"""
        if skip_leading_zeros:
            s = s.lstrip("0") or "0"
        for ch in s:
            img = self._small_glyph(ch, color)
            if img is not None:
                surf.blit(img, (int(x), int(y)))
            x += 7

    # ---- 对外 ----
    def render(self, surf: pygame.Surface, game) -> None:
        """画窗口框 + 右栏(在游戏区 blit 之前调, 640x480 的 surf 上)。"""
        self._render_chrome(surf)
        self._render_stats(surf, game)

    def render_overlay(self, surf: pygame.Surface, game) -> None:
        """画时刻表盘 + 妖率计(在游戏区 blit 之后调; 原版 Gui/AsciiManager
        画在全窗口 framebuffer 高层, 盖在游戏场景上)。"""
        self._render_clock(surf, game)
        self._render_gauge(surf, game)

    def render_fps(self, surf: pygame.Surface, fps: float) -> None:
        """帧率显示(ascii 贴字无 '.'/字母字形, 用小号字体; 位置同 th07 惯例)。"""
        if fps <= 0.0:
            return
        if self._fps_font is None:
            if not pygame.font.get_init():
                pygame.font.init()
            try:
                self._fps_font = pygame.font.Font(None, 16)
            except pygame.error:
                return
        img = self._fps_font.render(f"{fps:.2f}fps", True, (255, 255, 255))
        surf.blit(img, _FPS_POS)


__all__ = ["HudView", "WIN_W", "WIN_H"]
