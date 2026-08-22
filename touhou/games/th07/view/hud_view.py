""" 右侧 HUD 面板 + 樱点槽渲染 —— 对照 Gui::DrawGameScene / AsciiManager::DrawPopups。

【th07 专属】本模块的坐标/贴图布局是妖妖梦窗口版实现, 不随 GameData 泛化;
新作品复用窗口版需自带 HUD view(名单/面数参数化见 view/impl.py 的
game_data 参数)。

布局数值全部来自反编译源码与 anm 脚本静态求值(工具 scratch_dbg/anm_layout.py);
坐标为 640x480 窗口坐标。锚点: front.anm/樱点槽脚本含 ANM_22 指令
(ExecuteScript: vm->anchor=3, AnmManager.cpp) → 左上锚, pos 即左上角;
ascii.anm 樱点数字(script 3 无 ANM_22)与标题系立绘/名字为默认中心锚
(AnmVmBase.Initialize, AnmManager.cpp:1019-1045); ascii 贴字左上锚
(vm0.anchor=3, AsciiManager.cpp:128/274):

- 窗口框(Gui.cpp:1446-1469): front.anm sprite12(32x32) 左列 x=0 与右栏
  x=416..624 平铺, sprite13(128x16) 上沿 y=0 / 下沿 y=464。
- 装饰: sprite0(128x256 竖排logo) 左上(480,208), sprite1(128x80 英文logo)
  左上(448,336) (front.anm script 0/1 稳态, Gui.cpp:1470-1473)。
- 行底衬: sprite13 左上 (496,48/64) 常画, (496,96/112/144/160/176) 与
  (512,464) 原版仅值刷新帧画(Gui.cpp:1481-1515), 这里常画(近似)。
- 标签(64x16, 左上 x=432): HiScore/Score/Player/Bomb/Power/Graze/Point
  y=48/64/96/112/144/160/176 (front.anm script 2-8 稳态;
  与各自数值同行, 右缘恰接数值列 496)。
- 数值(ascii.anm 16x16 字形, 字符 c → sprite ord(c)-1, 步进 14):
  HiScore (496,48) %.8d, Score (496,64) %.8d + numRetries (Gui.cpp:1545-1597);
  Graze (496,160) %d, Point (496,176) %d/%d (Gui.cpp:1598-1610)。
  原版 Score 后缀是 highScoreNumContinues, 逻辑层无此字段, 不画(报告注明)。
- 残机/炸弹星: sprite10/11(16x16) 左上 (496+i*16, 96/112), 数量取
  int(livesRemaining)/int(bombsRemaining) (Gui.cpp:1516-1535)。
- Power: 蓝白渐变条 (496,144)-(496+power,160) + 数字(power<128)或 "MAX"
  (Gui.cpp:1613-1661; 条顶点色 0xe0e0e0ff → 0x80e0e0ff)。
- 樱点槽(AsciiManager::DrawPopups, AsciiManager.cpp:1131-1284):
  ascii.anm sprite142(96x16) 左上 (32,464) (script 4 interrupt1 稳态,
  自机生成时滑入后常显, Player.cpp:2474); 数字 sprite 132+d(8x12, 中心锚,
  步进 7): 下行 (cherry-cherryStart) / (cherryMax-cherryStart) 从
  (78,475) 起, 上行 (cherryPlus-cherryStart) 从 (85,466) 起。
  结界 READY/ACTIVE (hasBorder != NONE): 上行数字变色/放大
  (r=255, g=b=cherry*192/50000+三角波(cherry%4000)*64/2000, scale 1.41,
  步进 10, 起点 +2/-2, AsciiManager.cpp:1230-1253); ACTIVE 再加
  cherryBorderActive (sprite 143, 槽位 +(24,8) 中心锚, script 5 的
  0.8↔1.2 呼吸缩放 60 帧循环, AsciiManager.cpp:1275-1281/140)。

数字用 ascii.anm 贴字(原版同款), 不用 pygame 字体。
"""

from __future__ import annotations

from pathlib import Path

import pygame

# BorderState(NONE/READY/ACTIVE)绑死 th07 的樱之结界行为, 属后端内聚的有意
# 耦合(见模块 docstring 的【th07 专属】标注; 注入参数化成本大于收益)。
from ..bomb import BorderState
from ....engine.view.sprite_bank import SpriteBank
from .sprite_view import WIN_H, WIN_W

_FRONT = "front.anm"
_ASCII = "ascii.anm"

# 标签: (sprite id, 左上 y); x 恒 432 (front.anm script 2-8 稳态, ANM_22 左上锚)
_LABELS = ((2, 48), (3, 64), (4, 96), (5, 112), (6, 144), (7, 160), (8, 176))
_LABEL_X = 432
_VALUE_X = 496

# 樱点数字颜色 (AsciiManager.cpp:1147-1171 / 1204 / 1256-1263)
_CHERRY_COLORS = ((255, 208, 128), (255, 255, 128), (255, 255, 255))
_CHERRY_MAX_COLOR = (240, 208, 224)
_CHERRY_PLUS_COLOR = (192, 128, 176)

# 原版樱点槽稳态左上角 (ascii.anm script 4 interrupt 1 终值, ANM_22 左上锚)
_GAUGE_POS = (32, 464)


class HudView:
    """右栏 HUD + 樱点槽渲染器。资源懒加载(SpriteBank 首次取值才开包)。"""

    def __init__(self, data_path: str | Path) -> None:
        self.bank = SpriteBank(data_path)
        self._power_bar: pygame.Surface | None = None
        self._tint: dict[tuple[int, tuple[int, int, int]], pygame.Surface] = {}
        # 静态窗口框缓存: 每帧 ~150 次 tile blit 不变内容, 合成一次后整面 blit
        self._chrome: pygame.Surface | None = None

    # ---- 贴图工具 ----
    @staticmethod
    def _blit(surf: pygame.Surface, img: pygame.Surface | None,
              x: float, y: float) -> None:
        """左上锚 blit(ANM_22, anchor=3)。"""
        if img is None:
            return
        surf.blit(img, (int(x), int(y)))

    def _blit_center(self, surf: pygame.Surface, img: pygame.Surface | None,
                     x: float, y: float) -> None:
        """中心锚 blit(默认 anchor=0; 樱点数字用)。"""
        if img is None:
            return
        surf.blit(img, (int(x) - img.get_width() // 2,
                        int(y) - img.get_height() // 2))

    def _glyph(self, ch: str, color: tuple[int, int, int],
               big: bool = True) -> pygame.Surface | None:
        """ascii 字形: c → sprite ord(c)-1 (AsciiManager.cpp:317);
        樱点数字 d → sprite 132+d (AsciiManager.cpp:1166)。按颜色缓存 tint。"""
        if big:
            if not ("0" <= ch <= "9" or ch in "/MAX"):
                return None
            sid = ord(ch) - 1
        else:
            sid = 132 + int(ch)
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

    def _draw_text(self, surf: pygame.Surface, x: float, y: float, s: str,
                   color: tuple[int, int, int] = (255, 255, 255)) -> None:
        """ascii 贴字(左上锚, 步进 14 = fontSpacing, AsciiManager.cpp:126/284)。"""
        for ch in s:
            if ch == " ":
                x += 14
                continue
            img = self._glyph(ch, color)
            if img is not None:
                surf.blit(img, (int(x), int(y)))
            x += 14

    # ---- 窗口框/装饰(Gui.cpp:1446-1479; front.anm 全部左上锚) ----
    def _render_chrome(self, surf: pygame.Surface) -> None:
        # 全部静态内容: 首次合成到缓存面, 之后整面 blit(内容/顺序不变,
        # 逐 tile src-over 与先合成再整体 src-over 结果一致)
        if self._chrome is not None:
            surf.blit(self._chrome, (0, 0))
            return
        cache = pygame.Surface((WIN_W, WIN_H), pygame.SRCALPHA)
        self._render_chrome_uncached(cache)
        self._chrome = cache
        surf.blit(cache, (0, 0))

    def _render_chrome_uncached(self, surf: pygame.Surface) -> None:
        tile = self.bank.sprite(_FRONT, 12)
        if tile is not None:
            for y in range(0, 465, 32):            # 左列
                self._blit(surf, tile, 0, y)
            for x in range(416, 625, 32):          # 右栏
                for y in range(16, 465, 32):
                    self._blit(surf, tile, x, y)
        strip = self.bank.sprite(_FRONT, 13)
        if strip is not None:
            for x in range(0, 625, 128):           # 上/下沿
                self._blit(surf, strip, x, 0)
                self._blit(surf, strip, x, 464)
            # 行底衬(原版仅值刷新帧画, 这里常画, 见模块 docstring);
            # 先画: 原版靠 z-buffer 让数值盖在条上(z=0 vs 0.49)
            for y in (48, 64, 96, 112, 144, 160, 176):
                self._blit(surf, strip, 496, y)
            self._blit(surf, strip, 512, 464)
        self._blit(surf, self.bank.sprite(_FRONT, 0), 480, 208)
        self._blit(surf, self.bank.sprite(_FRONT, 1), 448, 336)
        for sid, y in _LABELS:
            self._blit(surf, self.bank.sprite(_FRONT, sid), _LABEL_X, y)

    # ---- 数值行(Gui.cpp:1516-1661) ----
    def _render_stats(self, surf: pygame.Surface, game) -> None:
        g = game.globals
        # HiScore: 逻辑层 globals 无 highScore, 从 score_store 取(透出, 报告注明)
        high = 0
        store = getattr(game, "store", None)
        if store is not None:
            try:
                high = store.high_score(game.difficulty, game.character)
            except (AttributeError, TypeError):
                high = 0
        self._draw_text(surf, _VALUE_X, 48, f"{high:08d}")
        self._draw_text(surf, _VALUE_X, 64, f"{g.gui_score:08d}")
        # retries 后缀(Gui.cpp:1553)
        self._draw_text(surf, _VALUE_X + 112, 64, f"{g.num_retries % 10}")
        star = self.bank.sprite(_FRONT, 10)
        for i in range(max(0, int(g.lives_remaining))):
            self._blit(surf, star, _VALUE_X + i * 16, 96)
        bomb = self.bank.sprite(_FRONT, 11)
        for i in range(max(0, int(g.bombs_remaining))):
            self._blit(surf, bomb, _VALUE_X + i * 16, 112)
        # Power 渐变条 + 数字/MAX (Gui.cpp:1613-1661)
        power = max(0, min(128, int(g.current_power)))
        if power > 0:
            if self._power_bar is None:
                bar = pygame.Surface((128, 16), pygame.SRCALPHA)
                for x in range(128):
                    a = 224 + (128 - 224) * x // 127
                    pygame.draw.line(bar, (224, 224, 255, a), (x, 0), (x, 15))
                self._power_bar = bar
            surf.blit(self._power_bar, (_VALUE_X, 144),
                      (0, 0, power, 16))
        if power < 128:
            self._draw_text(surf, _VALUE_X, 144, f"{power}")
        else:
            self._draw_text(surf, _VALUE_X, 144, "MAX")
        self._draw_text(surf, _VALUE_X, 160, f"{g.graze_in_total}")
        self._draw_text(surf, _VALUE_X, 176,
                        f"{g.point_items_collected_for_extend}"
                        f"/{g.next_needed_point_items_for_extend}")

    # ---- 樱点槽(AsciiManager::DrawPopups 的 cherryGauge 段) ----
    def _render_cherry(self, surf: pygame.Surface, game) -> None:
        g = game.globals
        gauge = self.bank.sprite(_ASCII, 142)
        if gauge is None:
            return
        gx, gy = _GAUGE_POS
        self._blit(surf, gauge, gx, gy)
        cherry = max(0, g.cherry - g.cherry_start)
        # 下行: cherry / cherryMax (AsciiManager.cpp:1139-1223)
        if g.cherry >= g.cherry_max:
            color = _CHERRY_COLORS[0]
        elif cherry >= 50000:
            color = _CHERRY_COLORS[1]
        else:
            color = _CHERRY_COLORS[2]
        x = self._draw_gauge_num(surf, gx + 46, gy + 11, cherry, 6, color)
        self._draw_gauge_num(surf, x + 9, gy + 11,
                             max(0, g.cherry_max - g.cherry_start), 6,
                             _CHERRY_MAX_COLOR)
        # 上行: cherryPlus (AsciiManager.cpp:1225-1273); 结界 READY/ACTIVE
        # 时变色+放大 1.41 + 步进 10 (AsciiManager.cpp:1230-1253)
        plus = max(0, g.cherry_plus - g.cherry_start)
        border = getattr(getattr(game, "border", None), "has_border",
                         BorderState.NONE)
        if border != BorderState.NONE:
            tri = plus % 4000
            if tri >= 2000:
                tri = 4000 - tri
            gb = min(255, plus * 192 // 50000 + tri * 64 // 2000)
            self._draw_gauge_num(surf, gx + 53 + 2, gy + 2 - 2, plus, 5,
                                 (255, gb, gb), step=10, scale=1.41)
        else:
            self._draw_gauge_num(surf, gx + 53, gy + 2, plus, 5,
                                 _CHERRY_PLUS_COLOR)
        # ACTIVE: cherryBorderActive 呼吸标记 (AsciiManager.cpp:1275-1281)
        if border == BorderState.ACTIVE:
            self._render_border_mark(surf, game, gx, gy)

    def _render_border_mark(self, surf: pygame.Surface, game,
                            gx: float, gy: float) -> None:
        """ascii script 5 (sprite 143): 0.8↔1.2 呼吸, 60 帧循环
        (0→30 ease-out 到 1.2, 30→60 ease-in 回 0.8)。"""
        img = self.bank.sprite(_ASCII, 143)
        if img is None:
            return
        t = int(getattr(game, "frame", 0)) % 60
        if t < 30:
            u = t / 30.0
            s = 0.8 + 0.4 * (1.0 - (1.0 - u) ** 2)     # ease mode 4 (out)
        else:
            u = (t - 30) / 30.0
            s = 1.2 - 0.4 * u * u                       # ease mode 1 (in)
        w = max(1, round(img.get_width() * s))
        h = max(1, round(img.get_height() * s))
        out = pygame.transform.scale(img, (w, h))
        self._blit_center(surf, out, gx + 24, gy + 8)

    def _draw_gauge_num(self, surf: pygame.Surface, x: float, y: float,
                        value: int, max_digits: int,
                        color: tuple[int, int, int], *, step: int = 7,
                        scale: float = 1.0) -> float:
        """樱点数字(中心锚, 步进 step, 不补前导零); 返回画完后的 x。"""
        s = f"{value}"
        slots = max(max_digits, len(s))  # cherryMax 可超 6 位(AsciiManager.cpp:1190)
        # 右对齐到 slots 位宽(原版从左起画, 前导零跳过)
        x += (slots - len(s)) * step
        for ch in s:
            img = self._glyph(ch, color, big=False)
            if img is not None:
                if scale != 1.0:
                    img = pygame.transform.scale(
                        img, (max(1, round(img.get_width() * scale)),
                              max(1, round(img.get_height() * scale))))
                self._blit_center(surf, img, x, y)
            x += step
        return x

    # ---- 对外 ----
    def render(self, surf: pygame.Surface, game) -> None:
        """画窗口框 + 右栏(在游戏区 blit 之前调, 640x480 的 surf 上)。"""
        self._render_chrome(surf)
        self._render_stats(surf, game)

    def render_overlay(self, surf: pygame.Surface, game) -> None:
        """画樱点槽(在游戏区 blit 之后调; 原版在弹点层, 盖在游戏场景上)。"""
        self._render_cherry(surf, game)


__all__ = ["HudView", "WIN_W", "WIN_H"]
