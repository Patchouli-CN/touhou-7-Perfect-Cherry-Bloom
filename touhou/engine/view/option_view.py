""" Option 设置界面 + 游戏内暂停面板 —— 对照 MainMenu.cpp + title01.anm。

Option 页(MainMenu.cpp OnUpdateOptionsMenu, :503-848):
- 背景 title00.jpg(原版 Option 叠在标题画面上, 同一条 Chain);
- 条目贴图取 title01.anm entry1 的亮/暗 sprite 对(偶数=选中亮, 奇数=暗):
  初始残机=Player(16/17), BGM 音量=BGM(20/21), SE 音量=Sound(22/23),
  窗口缩放=Mode(24/25), Key Config=(30/31), 退出=Quit(32/33);
  "音源"无对应贴图, 用文字;
- 值: 音量/缩放/残机用文字, 音源用 Wav(62/63)/Midi(64/65) 贴图;
- 底部说明文字对应 g_OptionsStrings(:132) 的语义, 用英文避免字体依赖。

KeyConfig 页(MainMenu.cpp OnUpdateKeyConfig, :891-1088): 同款背景 +
Key Config 贴图(30)当标题; 动作/键名用文字(原版是手柄按钮号数字,
本项目是 pygame 键盘键名)。确认进入"按新键"捕获状态(行尾提示),
Esc/X 取消; Reset to Default 恢复默认(engine/config.py DEFAULT_KEYMAP)。

暂停面板(游戏内 Esc): 半透明遮罩 + Pause 贴图(entry1 sprite 82)
+ Resume/Retry/Quit to Title 文字项。暂停时 BGM 继续(原版如此)。

资源运行时从 th07.dat 解(GameArchive), 不落盘。
"""

from __future__ import annotations

import io
from pathlib import Path

import pygame

from ...schema.anm import AnmFile
from ...schema.archive import GameArchive
from .screens import (KEYCONFIG_ITEMS, KEYCONFIG_LABELS, OPTION_ITEMS,
                      PAUSE_ITEMS, OptionFlow)  # PAUSE_ITEMS 再导出(单一来源 screens)
from .title_view import DEFAULT_DATA, TITLE_H, TITLE_W

# 条目布局: x=条目贴图左上, y0=首项, dy=行距; 值列在右侧
_ITEM_X = 160
_ITEM_Y0 = 128
_ITEM_DY = 36
_VALUE_X = 420

# 条目名 → (亮 sprite, 暗 sprite); "音源"无贴图用文字
_ITEM_SPRITES = {
    "初始残机": (16, 17),
    "BGM 音量": (20, 21),
    "SE 音量": (22, 23),
    "窗口缩放": (24, 25),
    "Key Config": (30, 31),
    "退出": (32, 33),
}
# 音源值贴图: (亮, 暗)
_SOURCE_SPRITES = {"wav": (62, 63), "midi": (64, 65)}

# 条目说明(语义对应 g_OptionsStrings, MainMenu.cpp:132; 英文避免字体依赖)
DESCRIPTIONS = {
    "BGM 音量": "Change BGM volume.",
    "SE 音量": "Change sound effect volume.",
    "音源": "Switch BGM source (WAV / MIDI).",
    "窗口缩放": "Change window scale.",
    "初始残机": "Change initial player count.",
    "Key Config": "Customize key bindings.",
    "退出": "Back to the title menu.",
}

_HINT_TEXT = "Up/Down: Select   Left/Right: Adjust   X/Esc: Back"

# KeyConfig 页布局(10 行: 8 动作 + Reset + Back)
_KC_ITEM_X = 140
_KC_ITEM_Y0 = 118
_KC_ITEM_DY = 32
_KC_VALUE_X = 360
_KC_HINT_TEXT = "Up/Down: Select   Z/Enter: Rebind   X/Esc: Cancel/Back"

# 暂停面板(非原版贴图布局, 文字菜单; 菜单项 PAUSE_ITEMS 见 screens.py)
_PAUSE_PANEL_W = 300
_PAUSE_PANEL_H = 200


class OptionView:
    """Option 设置页 + 暂停面板渲染器。资源懒加载(首次 render 才开包)。"""

    def __init__(self, data_path: str | Path = DEFAULT_DATA) -> None:
        self._data_path = Path(data_path)
        self._loaded = False
        self.background: pygame.Surface | None = None
        self._item_sprites: dict[str, tuple[pygame.Surface, pygame.Surface]] = {}
        self._source_sprites: dict[str, tuple[pygame.Surface, pygame.Surface]] = {}
        self._pause_sprite: pygame.Surface | None = None
        self._font: pygame.font.Font | None = None
        self._font_big: pygame.font.Font | None = None
        self._font_small: pygame.font.Font | None = None

    # ---- 资源 ----
    def ensure_loaded(self) -> None:
        if self._loaded:
            return
        arc = GameArchive.open(self._data_path)
        self.background = pygame.image.load(io.BytesIO(arc.load("title00.jpg")))
        anm = AnmFile.parse(arc.load("title01.anm"))

        def _surf(sprite_id: int) -> pygame.Surface:
            w, h, rgba = anm.sprite_image(sprite_id, entry=1)
            s = pygame.image.fromstring(rgba, (w, h), "RGBA")
            try:
                s = s.convert_alpha()
            except pygame.error:
                pass
            return s

        for name, (lit, dim) in _ITEM_SPRITES.items():
            self._item_sprites[name] = (_surf(lit), _surf(dim))
        for name, (lit, dim) in _SOURCE_SPRITES.items():
            self._source_sprites[name] = (_surf(lit), _surf(dim))
        self._pause_sprite = _surf(82)
        try:
            self._font = pygame.font.Font(None, 28)
            self._font_big = pygame.font.Font(None, 36)
            self._font_small = pygame.font.Font(None, 20)
        except pygame.error:
            self._font = self._font_big = self._font_small = None
        self._loaded = True

    # ---- 工具 ----
    def _text(self, surf: pygame.Surface, s: str, x: int, y: int, *,
              lit: bool, big: bool = False, center: bool = False) -> None:
        font = self._font_big if big else self._font
        if font is None:
            return
        color = (255, 255, 255) if lit else (140, 140, 150)
        shadow = font.render(s, True, (48, 0, 0))
        text = font.render(s, True, color)
        if center:
            surf.blit(shadow, shadow.get_rect(center=(x + 1, y + 1)))
            surf.blit(text, text.get_rect(center=(x, y)))
        else:
            surf.blit(shadow, (x + 1, y + 1))
            surf.blit(text, (x, y))

    # ---- Option 页 ----
    def render(self, surf: pygame.Surface, flow: OptionFlow) -> None:
        """把 Option 设置页画到 640x480 的 surf 上。"""
        self.ensure_loaded()
        if self.background is not None:
            surf.blit(self.background, (0, 0))
        else:
            surf.fill((10, 10, 30))
        self._text(surf, "Option", TITLE_W // 2, 56, lit=True, big=True,
                   center=True)
        cfg = flow.config
        for i, name in enumerate(OPTION_ITEMS):
            lit = i == flow.cursor.index
            y = _ITEM_Y0 + i * _ITEM_DY
            pair = self._item_sprites.get(name)
            if pair is not None:
                surf.blit(pair[0] if lit else pair[1], (_ITEM_X, y))
            else:  # "音源"无贴图, 文字条目
                self._text(surf, "Source", _ITEM_X, y, lit=lit)
            self._render_value(surf, name, cfg, lit, y)
        # 说明文字(底部, 同标题主菜单风格)
        desc = DESCRIPTIONS.get(flow.cursor.current or "", "")
        if desc:
            self._text(surf, desc, 16, TITLE_H - 30, lit=True)
        if self._font_small is not None:
            hint = self._font_small.render(_HINT_TEXT, True, (200, 200, 220))
            surf.blit(hint, hint.get_rect(center=(TITLE_W // 2, TITLE_H - 52)))

    def _render_value(self, surf: pygame.Surface, name: str, cfg, lit: bool,
                      y: int) -> None:
        if name == "BGM 音量":
            self._text(surf, f"{cfg.bgm_volume}", _VALUE_X, y, lit=lit)
        elif name == "SE 音量":
            self._text(surf, f"{cfg.se_volume}", _VALUE_X, y, lit=lit)
        elif name == "音源":
            pair = self._source_sprites.get(cfg.bgm_source)
            if pair is not None:
                surf.blit(pair[0] if lit else pair[1], (_VALUE_X, y))
            else:
                self._text(surf, cfg.bgm_source.upper(), _VALUE_X, y, lit=lit)
        elif name == "窗口缩放":
            self._text(surf, f"x{cfg.window_scale}", _VALUE_X, y, lit=lit)
        elif name == "初始残机":
            self._text(surf, f"{cfg.initial_lives}", _VALUE_X, y, lit=lit)

    # ---- KeyConfig 页 ----
    def render_keyconfig(self, surf: pygame.Surface, flow) -> None:
        """把 KeyConfig 键位设置页画到 640x480 的 surf 上。

        标题用 Key Config 贴图(entry1 sprite 30); 动作行左标签右键名,
        捕获中的行尾显示 "<press a key>"; 键名原样显示(pygame.key.name)。
        """
        self.ensure_loaded()
        if self.background is not None:
            surf.blit(self.background, (0, 0))
        else:
            surf.fill((10, 10, 30))
        title_pair = self._item_sprites.get("Key Config")
        if title_pair is not None:
            surf.blit(title_pair[0],
                      title_pair[0].get_rect(center=(TITLE_W // 2, 64)))
        else:
            self._text(surf, "Key Config", TITLE_W // 2, 64, lit=True,
                       big=True, center=True)
        cfg = flow.config
        for i, item in enumerate(KEYCONFIG_ITEMS):
            lit = i == flow.cursor.index
            y = _KC_ITEM_Y0 + i * _KC_ITEM_DY
            self._text(surf, KEYCONFIG_LABELS[item], _KC_ITEM_X, y, lit=lit)
            if item in ("reset", "back"):
                continue
            if flow.capturing == item:
                value = "<press a key>"
            else:
                value = " / ".join(cfg.keymap.get(item, []))
            self._text(surf, value, _KC_VALUE_X, y, lit=lit)
        if self._font_small is not None:
            hint = self._font_small.render(_KC_HINT_TEXT, True, (200, 200, 220))
            surf.blit(hint, hint.get_rect(center=(TITLE_W // 2, TITLE_H - 30)))

    # ---- 暂停面板 ----
    def render_pause(self, surf: pygame.Surface, cursor: int) -> None:
        """把暂停面板画到 640x480 的 SRCALPHA surf 上(叠加在游戏画面上)。

        半透明遮罩 + 面板 + Pause 贴图(entry1 sprite 82) + 文字菜单项。
        """
        self.ensure_loaded()
        veil = pygame.Surface((TITLE_W, TITLE_H), pygame.SRCALPHA)
        veil.fill((0, 0, 16, 128))
        surf.blit(veil, (0, 0))
        panel = pygame.Surface((_PAUSE_PANEL_W, _PAUSE_PANEL_H), pygame.SRCALPHA)
        panel.fill((16, 16, 48, 220))
        px, py = (TITLE_W - _PAUSE_PANEL_W) // 2, (TITLE_H - _PAUSE_PANEL_H) // 2
        pygame.draw.rect(panel, (200, 200, 220, 255), panel.get_rect(), 1)
        surf.blit(panel, (px, py))
        if self._pause_sprite is not None:
            surf.blit(self._pause_sprite,
                      self._pause_sprite.get_rect(center=(TITLE_W // 2, py + 48)))
        else:
            self._text(surf, "PAUSE", TITLE_W // 2, py + 48, lit=True,
                       big=True, center=True)
        for i, name in enumerate(PAUSE_ITEMS):
            self._text(surf, name, TITLE_W // 2, py + 96 + i * 32,
                       lit=i == cursor, center=True)
