"""标题界面渲染(pygame) —— 对照 th07 MainMenu.cpp / title01.anm 还原。

布局数值来源(开发期对 title01.anm 脚本做静态求值得到, 对照
MainMenu.cpp OnUpdatePreInput 的 VM 使用):
- 背景: title00.jpg 全屏(640x480), 版权文字已烧进图里。
- logo: entry0(title02.png) 唯一 sprite, 位置 (-48, -32)(脚本 0)。
- 主菜单 8 项: entry1(title01.png) sprite 0..15, 偶数=选中(亮白),
  奇数=未选中(灰); 静止位置 x≈400, y=200+28*i(脚本 0-7)。
- 菜单位移动画照抄脚本时序: 滑入 432→400(60 帧线性) + 淡入(16 帧),
  之后 397↔410 缓慢摆动(每 96 帧减速插值, 周期 512 帧)。
- 原版标题画面没有花瓣系统(title01.anm 94 个 sprite 全是文字/数字贴图,
  MainMenu.cpp 无花瓣逻辑); 下面的花瓣是按需求加的装饰性简化版
  (随机 x、匀速下落 + 左右摆动 + 旋转), 贴图为程序生成的粉色椭圆。
- 版本号: 原版标题画面不画版本号(ver 1.00b 只在窗口标题, 见
  GameWindow.cpp:299); 这里按需求用小号字体画在右下角近似。

资源运行时从 th07.dat 解(open_archive), 不落盘。
"""

from __future__ import annotations

import io
import math
import random
from pathlib import Path

import pygame

from ....schema.anm import parse_cached
from ....schema.archive import open_archive
from .screens import MAIN_MENU_ITEMS

from ....paths import DEFAULT_DATA, resolve_data_path  # noqa: F401 (DEFAULT_DATA 再导出)

TITLE_W, TITLE_H = 640, 480

# --- 布局常量(title01.anm 脚本静态求值, 见模块 docstring) ---
LOGO_POS = (-48, -32)
MENU_X_REST = 400
MENU_X_INTRO = 432
MENU_Y0 = 200
MENU_DY = 28
MENU_SPRITE_BASE = [0, 2, 4, 6, 8, 10, 12, 14]  # 各项选中态 sprite id; +1 = 未选中

# 菜单项说明(语义对应 MainMenu.cpp g_MainMenuStrings; 用英文避免字体依赖)
DESCRIPTIONS = [
    "Start the game.",
    "Start the Extra stage.",
    "Select a stage and practice.",
    "Watch replays.",
    "View past scores and spell card history.",
    "Listen to the music.",
    "Change various settings.",
    "Quit the game.",
]

_PETAL_COUNT = 18


def _ease_out(u: float) -> float:
    """二次减速(近似原版 easeModes=4 的 decel 插值)。"""
    return 1.0 - (1.0 - u) * (1.0 - u)


def menu_offset_x(t: int) -> float:
    """菜单 x 位移, 照抄 title01.anm entry1 脚本 0-7 的位移指令时序。"""
    if t < 60:  # POS_TIME_LINEAR: 432 → 400, 60 帧滑入
        return MENU_X_INTRO + (MENU_X_REST - MENU_X_INTRO) * (t / 60)
    if t < 256:
        return float(MENU_X_REST)
    # 稳态摆动循环: 脚本时间 [256,768) 周期重复(JUMP 回 t=256 指令)
    s = 256 + (t - 256) % 512
    if s < 352:  # POS_TIME_DECEL 400→410(首轮) / 397→410(之后), 3px 差忽略
        return 400 + 10 * _ease_out((s - 256) / 96)
    if s < 512:
        return 410.0
    if s < 608:  # POS_TIME_DECEL 410→397
        return 410 - 13 * _ease_out((s - 512) / 96)
    return 397.0


def _make_petal_surface() -> pygame.Surface:
    """程序生成的花瓣贴图(原版标题无花瓣, 见模块 docstring)。"""
    surf = pygame.Surface((16, 10), pygame.SRCALPHA)
    pygame.draw.ellipse(surf, (255, 200, 215, 200), (0, 0, 16, 10))
    pygame.draw.ellipse(surf, (255, 235, 240, 220), (3, 2, 10, 6))
    return surf


class TitleScreen:
    """标题画面渲染器。资源懒加载(首次 render/ensure_loaded 时才开包)。"""

    def __init__(
        self, data_path: str | Path | None = None, *, seed: int | None = None
    ) -> None:
        self._data_path = resolve_data_path(data_path)
        self._loaded = False
        self.background: pygame.Surface | None = None
        self.logo: pygame.Surface | None = None
        self.menu_sprites: list[tuple[pygame.Surface, pygame.Surface]] = []
        self.sounds: dict[str, pygame.mixer.Sound] = {}
        self._font: pygame.font.Font | None = None
        self._font_small: pygame.font.Font | None = None
        self._petal_img: pygame.Surface | None = None
        self._petals: list[dict] = []
        self._rng = random.Random(seed)

    # ---- 资源 ----
    def ensure_loaded(self) -> None:
        if self._loaded:
            return
        arc = open_archive(self._data_path, game="th07")
        self.background = pygame.image.load(io.BytesIO(arc.load("title00.jpg")))
        # parse_cached: 标题/选人/设置三视图共享同一解码 (BUGS.md 增量#3)
        anm = parse_cached(arc.load("title01.anm"))

        def _surf(sprite_id: int, entry: int) -> pygame.Surface:
            w, h, rgba = anm.sprite_image(sprite_id, entry=entry)
            return pygame.image.fromstring(rgba, (w, h), "RGBA")

        self.logo = _surf(0, entry=0)
        self.menu_sprites = [
            (_surf(sid, entry=1), _surf(sid + 1, entry=1)) for sid in MENU_SPRITE_BASE
        ]
        if pygame.mixer.get_init():
            for key, name in (
                ("select", "se_select00.wav"),
                ("ok", "se_ok00.wav"),
                ("cancel", "se_cancel00.wav"),
            ):
                self.sounds[key] = pygame.mixer.Sound(file=io.BytesIO(arc.load(name)))
        self._font = pygame.font.Font(None, 20)
        self._font_small = pygame.font.Font(None, 16)
        self._petal_img = _make_petal_surface()
        self._petals = [self._new_petal(anywhere=True) for _ in range(_PETAL_COUNT)]
        self._loaded = True

    def play_sound(self, key: str) -> None:
        """没声卡/未初始化时静音跳过。"""
        snd = self.sounds.get(key)
        if snd is not None:
            try:
                snd.play()
            except pygame.error:
                pass

    # ---- 花瓣(装饰, 非原版) ----
    def _new_petal(self, *, anywhere: bool = False) -> dict:
        r = self._rng
        return {
            "x": r.uniform(0, TITLE_W),
            "y": r.uniform(0, TITLE_H) if anywhere else r.uniform(-40, -10),
            "speed": r.uniform(0.5, 1.3),
            "sway_amp": r.uniform(8.0, 24.0),
            "sway_phase": r.uniform(0, math.tau),
            "rot": r.uniform(0, 360.0),
            "rot_speed": r.uniform(-1.5, 1.5),
        }

    def _update_petals(self) -> None:
        for i, p in enumerate(self._petals):
            p["y"] += p["speed"]
            p["sway_phase"] += 0.02
            p["x"] += math.sin(p["sway_phase"]) * 0.3
            p["rot"] = (p["rot"] + p["rot_speed"]) % 360.0
            if p["y"] > TITLE_H + 16:
                self._petals[i] = self._new_petal()

    # ---- 渲染 ----
    def render(
        self,
        surf: pygame.Surface,
        cursor: int,
        frame: int,
        *,
        show_unimplemented: bool = False,
    ) -> None:
        """把标题画面画到 640x480 的 surf 上。"""
        self.ensure_loaded()
        surf.blit(self.background, (0, 0))

        logo_alpha = min(255, frame * 255 // 30)
        logo = self.logo
        if logo_alpha < 255:
            logo = self.logo.copy()
            logo.set_alpha(logo_alpha)
        surf.blit(logo, LOGO_POS)

        menu_x = menu_offset_x(frame)
        item_alpha = min(255, frame * 255 // 16)  # FADE 255, 16 帧
        for i, (selected, normal) in enumerate(self.menu_sprites):
            img = selected if i == cursor else normal
            if item_alpha < 255:
                img = img.copy()
                img.set_alpha(item_alpha)
            surf.blit(img, (int(menu_x), MENU_Y0 + i * MENU_DY))

        self._update_petals()
        for p in self._petals:
            img = pygame.transform.rotozoom(self._petal_img, p["rot"], 1.0)
            img.set_alpha(180)
            surf.blit(img, (int(p["x"]), int(p["y"])))

        # 说明文字(原版用 ascii 贴字 DrawStringFormat2, 这里用小号字体近似)
        desc = DESCRIPTIONS[cursor] if 0 <= cursor < len(DESCRIPTIONS) else ""
        if desc and self._font is not None:
            shadow = self._font.render(desc, True, (48, 0, 0))
            text = self._font.render(desc, True, (255, 240, 224))
            surf.blit(shadow, (17, TITLE_H - 29))
            surf.blit(text, (16, TITLE_H - 30))

        # 版本号(原版只在窗口标题, 见模块 docstring)
        if self._font_small is not None:
            ver = self._font_small.render("ver 1.00b", True, (230, 230, 240))
            surf.blit(ver, (TITLE_W - ver.get_width() - 6, TITLE_H - 18))

        if show_unimplemented and self._font is not None:
            # 未知菜单项的兜底提示(现菜单项均已接线, 正常不会触发)
            hint = self._font.render("Not implemented yet", True, (255, 120, 120))
            surf.blit(
                hint,
                hint.get_rect(center=(MENU_X_REST + 60, MENU_Y0 + 8 * MENU_DY + 20)),
            )
