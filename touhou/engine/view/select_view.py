""" 选人/选难度/Extra 选关界面 —— 对照 MainMenu.cpp + title01.anm 脚本稳态布局。

布局数值(640x480)来自 title01.anm 脚本静态求值
(工具 tmp_title/anm_layout.py; interrupt 7/9/10/22 = 滑入段终值,
对照 MainMenu.cpp 的 SetInterruptActiveVms 调用点)。
锚点: 难度项/机型块/select02 的脚本含 ANM_22 → 左上锚;
立绘/名字/说明/页头无 ANM_22 → 默认中心锚(AnmVmBase.Initialize)。

- 难度页(MainMenu.cpp:1210-1230, vmHead[67..70], select01.png, 左上锚):
  Easy (252,96) / Normal (212,184) / Hard (172,272) / Lunatic (132,360),
  sprite 0/2/4/6 = 选中(亮), +1 = 未选中(暗);
  页头 sl_text sprite0 "Choose Level." 中心 (320,48)。
- 选人页(原版两页: 左右选自机 MainMenu.cpp:1481 MoveCursorHorizontal(3)
  → 上下选机型 OnUpdateSelectShotType MoveCursorVertical(2);
  本项目菜单逻辑是 6 项纵列表 ReimuA..SakuyaB(测试锁定), 渲染照选机型页
  (vmHead[71..73]: 立绘 alpha 128 衬底 + A/B 块亮/暗, 名字/说明 VM 在原版
  选机型页 active=0 不画, MainMenu.cpp:1648-1680), 立绘取 index//2,
  A/B 高亮取 index%2):
  立绘 sl_pl0N sprite0 中心 (448,240) (script interrupt 9 终值, 这里 alpha
  192 近似原版的 128, 单页兼顾可读性);
  机型块 sl_pl0N A 左上(368,192) B 左上(368,336), 选中 sprite 1/3, 未选 2/4
  (script interrupt 10 终值);
  页头 sl_text sprite1 "Choose Girl." 中心 (320,48)。
- Extra 选关页(MainMenu.cpp:1216-1237 extra 分支, vmHead[162..163],
  select02.png, 左上锚): Extra (212,184) / Phantasm (172,272),
  sprite 0/2 = 选中, +1 = 未选中; 页头同难度页(简化)。
- Practice 选关页(MainMenu.cpp:2375 DrawPracticeMenu): 文字列
  Stage1..6, 光标行白 / 已解锁灰(0xa0a0a0) / 未解锁暗灰(0x404040);
  原版附 HI-Score/playCount(pscr 每面), 本项目 pscr 无每面维度, 省略。
- 背景 select00.jpg (MainMenu.cpp:1142 LoadSurface)。

【th07 专属】本模块的贴图/坐标布局是妖妖梦窗口版实现, 不随 GameData 泛化;
新作品复用窗口版需自带选人界面 view。

资源运行时从 th07.dat 解(GameArchive), 不落盘。
"""

from __future__ import annotations

import io
from pathlib import Path

import pygame

from ...schema.anm import AnmFile
from ...schema.archive import GameArchive
from .screens import PRACTICE_STAGE_ITEMS
from .title_view import DEFAULT_DATA, TITLE_H, TITLE_W

# 难度项: (select01 sprite 基址, 中心 x, 中心 y); +1 = 未选中
_DIFF_ITEMS = ((0, 252, 96), (2, 212, 184), (4, 172, 272), (6, 132, 360))
# Extra 选关项: (select02 sprite 基址, 中心 x, 中心 y)
_EXTRA_ITEMS = ((0, 212, 184), (2, 172, 272))
# 选人页布局(见模块 docstring)
_PORTRAIT_POS = (448, 240)
_SHOT_A_POS = (368, 192)
_SHOT_B_POS = (368, 336)
_HEADER_POS = (320, 48)

_HINT_TEXT = "Up/Down: Select   Z/Enter: Confirm   X/Esc: Back"


class SelectView:
    """选人/选难度/Extra 选关渲染器。资源懒加载(首次 render 才开包)。"""

    def __init__(self, data_path: str | Path = DEFAULT_DATA) -> None:
        self._data_path = Path(data_path)
        self._loaded = False
        self.background: pygame.Surface | None = None
        self._diff_sprites: list[tuple[pygame.Surface, pygame.Surface]] = []
        self._extra_sprites: list[tuple[pygame.Surface, pygame.Surface]] = []
        self._headers: list[pygame.Surface] = []
        self._portraits: list[pygame.Surface] = []
        self._shot_blocks: list[tuple] = []   # per char: (A亮, A暗, B亮, B暗)
        self._font: pygame.font.Font | None = None
        self._font_mid: pygame.font.Font | None = None

    # ---- 资源 ----
    def ensure_loaded(self) -> None:
        if self._loaded:
            return
        arc = GameArchive.open(self._data_path)
        raw = None
        for key in ("select00.jpg", "title/select00.jpg",
                    "data/title/select00.jpg"):
            try:
                raw = arc.load(key)
                break
            except KeyError:
                continue
        if raw is not None:
            self.background = pygame.image.load(io.BytesIO(raw))
        anm = AnmFile.parse(arc.load("title01.anm"))

        def _surf(sprite_id: int, entry: int) -> pygame.Surface:
            w, h, rgba = anm.sprite_image(sprite_id, entry=entry)
            s = pygame.image.fromstring(rgba, (w, h), "RGBA")
            try:
                s = s.convert_alpha()
            except pygame.error:
                pass
            return s

        # entry2=select01(难度), entry3-5=sl_pl00-02(立绘/机型块),
        # entry7=sl_text(页头), entry9=select02(Extra)
        self._diff_sprites = [(_surf(s, 2), _surf(s + 1, 2))
                              for s, _x, _y in _DIFF_ITEMS]
        self._extra_sprites = [(_surf(s, 9), _surf(s + 1, 9))
                               for s, _x, _y in _EXTRA_ITEMS]
        self._headers = [_surf(i, 7) for i in range(2)]
        for ch in range(3):
            self._portraits.append(_surf(0, 3 + ch))
            self._shot_blocks.append(tuple(_surf(s, 3 + ch) for s in (1, 2, 3, 4)))
        try:
            self._font = pygame.font.Font(None, 20)
        except pygame.error:
            self._font = None
        try:
            self._font_mid = pygame.font.Font(None, 32)
        except pygame.error:
            self._font_mid = None
        self._loaded = True

    # ---- 工具 ----
    @staticmethod
    def _blit_center(surf: pygame.Surface, img: pygame.Surface | None,
                     x: float, y: float) -> None:
        """中心锚 blit(默认 anchor=0; 立绘/名字/说明/页头用)。"""
        if img is None:
            return
        surf.blit(img, (int(x) - img.get_width() // 2,
                        int(y) - img.get_height() // 2))

    @staticmethod
    def _blit(surf: pygame.Surface, img: pygame.Surface | None,
              x: float, y: float) -> None:
        """左上锚 blit(ANM_22, anchor=3; 难度项/机型块/Extra 项用)。"""
        if img is None:
            return
        surf.blit(img, (int(x), int(y)))

    def _begin(self, surf: pygame.Surface, header: int) -> None:
        self.ensure_loaded()
        if self.background is not None:
            surf.blit(self.background, (0, 0))
        else:
            surf.fill((10, 10, 30))
        if header < len(self._headers):
            self._blit_center(surf, self._headers[header], *_HEADER_POS)

    def _hint(self, surf: pygame.Surface) -> None:
        # 操作提示(原版无此行, 沿用旧文字菜单的引导, 小号字体近似)
        if self._font is not None:
            t = self._font.render(_HINT_TEXT, True, (200, 200, 220))
            surf.blit(t, t.get_rect(center=(TITLE_W // 2, TITLE_H - 20)))

    # ---- 三页 ----
    def render_difficulty(self, surf: pygame.Surface, cursor: int) -> None:
        """难度选择页; cursor 0..3 = Easy..Lunatic。"""
        self._begin(surf, header=0)
        for i, ((_s, x, y), (lit, dim)) in enumerate(
                zip(_DIFF_ITEMS, self._diff_sprites)):
            self._blit(surf, lit if i == cursor else dim, x, y)
        self._hint(surf)

    def render_character(self, surf: pygame.Surface, index: int) -> None:
        """选人页; index 0..5 = ReimuA..SakuyaB(立绘 index//2, 机型 index%2)。"""
        self._begin(surf, header=1)
        ch = max(0, min(2, index // 2))
        shot = index % 2
        # 立绘 alpha 衬底(原版选机型页 interrupt 10 = 128, 这里 192 近似)
        dim = self._portraits[ch].copy()
        dim.set_alpha(192)
        self._blit_center(surf, dim, *_PORTRAIT_POS)
        blocks = self._shot_blocks[ch]
        # A: 亮 sprite1 / 暗 sprite2; B: 亮 sprite3 / 暗 sprite4
        self._blit(surf, blocks[0] if shot == 0 else blocks[1], *_SHOT_A_POS)
        self._blit(surf, blocks[2] if shot == 1 else blocks[3], *_SHOT_B_POS)
        self._hint(surf)

    def render_extra(self, surf: pygame.Surface, cursor: int) -> None:
        """Extra/Phantasm 选关页(简化, 见 screens.py EXTRA_STAGES 注释)。"""
        self._begin(surf, header=0)
        for i, ((_s, x, y), (lit, dim)) in enumerate(
                zip(_EXTRA_ITEMS, self._extra_sprites)):
            self._blit(surf, lit if i == cursor else dim, x, y)
        self._hint(surf)

    def render_practice_stage(self, surf: pygame.Surface, cursor: int,
                              max_stage: int, *, difficulty: str = "",
                              character: str = "") -> None:
        """Practice 选关页(DrawPracticeMenu): 文字列 Stage1..6,
        未解锁(>= max_stage)暗灰不可选; cursor/max_stage 语义见
        screens.practice_max_stage。"""
        self._begin(surf, header=0)
        self.ensure_loaded()
        if self._font_mid is not None:
            t = self._font_mid.render(
                f"Practice  {difficulty} / {character}", True, (255, 255, 255))
            surf.blit(t, t.get_rect(center=(320, 96)))
            y = 160
            for i, name in enumerate(PRACTICE_STAGE_ITEMS):
                if i == cursor:
                    color = (255, 255, 255)      # 光标行白
                elif i < max_stage:
                    color = (160, 160, 160)      # 已解锁 0xa0a0a0
                else:
                    color = (64, 64, 64)         # 未解锁 0x404040
                row = self._font_mid.render(name, True, color)
                surf.blit(row, row.get_rect(center=(320, y)))
                y += 40
        self._hint(surf)
