"""th08 标题画面(主菜单)的原作版渲染 —— title00.png 背景 + title01.anm 菜单 vm。

对照 th08-ref TitleScreen.cpp(行号相对其 src/):
- vms[0] = 右侧标题 logo 带(script 0 → sprite 0, title02.png 256x480,
  落位 (384,0)); vms[1..9] = 主菜单 9 项(script 1..9) —— 即
  ExecuteAnmIdxArray(vms, 0, 142)(:312-314)的前 10 槽, 其余槽是
  Option/KeyConfig/Replay 等子画面贴图(A 期不接)。
- 菜单项贴图成对: 选中 = baseSpriteIndex, 未选中 = baseSpriteIndex+1
  (:349-353/:406-413); baseSpriteIndex = 脚本首帧 sprite
  (AnmLoaded::ExecuteAnmIdxArray, AnmManager.cpp:956)。
- Extra Start/Spell Practice 未解锁置灰 color=0xff404040(:356-365/:415-425)。
- 背景 = PreloadSurface(0, "title/title00.png")(:3786) + OnDraw 的
  CopySurfaceToBackbuffer(0)(:3553-3612)。
- 进标题 70 帧白淡入(TitleSetupThread 注册 SCREEN_EFFECT_FULL_FADE_IN,
  :3800-3807; alpha = 255-255*timer/70, CalcFadeIn ScreenEffect.cpp:109-127)。
- 底部帮助行 = helpTextVms[9](:368-375, :481-485 随光标切换并重淡入):
  text.anm script 9 落位 (320,448), interrupt 1 = alpha 0→255 共 20 帧;
  文字色 0xfff0e0 / 描边色 0x300000(DrawTextCentered :370-371,
  COLORREF 0x00BBGGRR → RGB(224,240,255)/RGB(0,0,48))。原作把文本烘成
  贴图(TextHelper), 这里直接绘字(引擎对照 §19.1 认可的路线)。

菜单脚本本体自带入场演出: alpha 0→255(16f) + 右滑到位(30f), 逐项错开 6f,
之后 x 在 ±6px 间 512f 循环缓动 —— 逐帧跑脚本即得原作动效, 不另写动画。
"""

from __future__ import annotations

import io

import pygame

from ....engine.view.anm_fx import AnmScriptBank, TransformCache, Vm2d
from ....engine.view.sprite_bank import SpriteBank
from ..crypt import try_decrypt_from_table
from .anm_vm import AnmVmTh08
from .title_flow import (
    HELP_TEXTS,
    ITEM_EXTRA_START,
    ITEM_SPELL_PRACTICE,
    TitleFlowTh08,
)

_TITLE_ANM = "title01.anm"
_TITLE_BG = "title00.png"  # 封包内条目名(C++ 侧路径 "title/title00.png")
_W, _H = 640, 480  # 标题画面分辨率(ScreenEffect.cpp:100-101 的 viewport)

_MENU_VM_COUNT = 10  # logo(1) + 主菜单 9 项(vms[0..9])
_FADE_FRAMES = 70  # 白淡入帧数(:3800-3807)
_GRAY = (0x40, 0x40, 0x40)  # 锁定项置灰 0xff404040(:356-365)
_HELP_POS = (320, 448)  # text.anm script 9 的 pos
_HELP_FONT_SIZE = 15  # fontWidth/fontHeight=15(DrawTextCentered 缺省档)
_HELP_COLOR = (224, 240, 255)
_HELP_SHADOW = (0, 0, 48)
_HELP_FADE_FRAMES = 20  # 帮助行换项重淡入(script 9 interrupt 1)


class _TitleScriptBank(AnmScriptBank):
    """title01.anm 的扁平 sprite 定位修正: 按装载序(entry 内出现序)建表。

    引擎扁平分支(ANM_FLAT_LAYOUT, engine/view/anm_fx.py)按
    "entry 前缀 + 文件存储 id" 定位 sprite, 前提是存储 id 各 entry 内 0 起
    连续; 实测 title01.anm 的存储 id 是全文件连续编号(entry0=[0],
    entry1=[1..100], entry2=[101..113], …), 与 C++ 的装载序扁平下标
    (AnmManager.cpp:2388-2389)错开 entry 前缀, 菜单脚本的 sprite 参数
    (1..18)会落空/错位 —— 这里按装载序重建 _spr_loc。
    """

    def __init__(self, bank: SpriteBank, name: str) -> None:
        super().__init__(bank, name, 0)
        if not self.ok:
            return
        anm = bank.anm(name)
        assert anm is not None
        loc: dict[int, tuple[int, int]] = {}
        flat = 0
        for ei, entry in enumerate(anm.entries):
            for sid in entry.sprites:
                loc[flat] = (ei, sid)
                flat += 1
        self._spr_loc = loc


def _load_font(size: int):
    for name in ("Microsoft YaHei", "SimHei", "SimSun", None):
        try:
            return pygame.font.SysFont(name, size)
        except Exception:
            continue
    return pygame.font.Font(None, size)


class TitleView:
    """标题画面渲染器: render() 把一帧画到内部 640x480 surface 并返回。

    构造失败(无数据/资源损坏)抛异常, 由后端回退文字菜单(容错口径同
    begin_game 的降级)。
    """

    def __init__(self, data_path) -> None:
        self._bank = SpriteBank(data_path, game="th08")
        self._tcache = TransformCache()
        self._sb = _TitleScriptBank(self._bank, _TITLE_ANM)
        if not self._sb.ok:
            raise FileNotFoundError(_TITLE_ANM)
        # 背景(PreloadSurface :3786; 封包内 edz 加密 PNG → 解密 → 解码)
        raw = try_decrypt_from_table(self._bank._archive().load(_TITLE_BG))
        self._bg = pygame.image.load(io.BytesIO(raw))
        try:
            self._bg = self._bg.convert()  # 快速 blit 路径(需 display 初始化)
        except pygame.error:
            pass
        # logo + 主菜单 9 项的 vm(脚本 0..9; start 即 ExecuteAnmIdx,
        # active_sprite_idx = baseSpriteIndex 对应物, AnmManager.cpp:956)
        self._vms: list[Vm2d] = []
        self._base_sprites: list[int] = []
        for gid in range(_MENU_VM_COUNT):
            v = Vm2d(self._sb, self._tcache, vm_cls=AnmVmTh08)
            if not v.start(gid):
                raise ValueError(f"{_TITLE_ANM} 缺脚本 {gid}")
            self._vms.append(v)
            self._base_sprites.append(v.vm.active_sprite_idx)
        self._frame = pygame.Surface((_W, _H))
        self._fade_overlay = pygame.Surface((_W, _H))
        self._fade_overlay.fill((255, 255, 255))
        self._help_font = None
        self._last_state: tuple | None = None  # (光标, extra 解锁, spell 解锁)
        self._last_help_idx = -1
        self._help_fade = _HELP_FADE_FRAMES

    # ---- 菜单 sprite 对/置灰同步(:346-365/:406-425) ----
    def _sync_menu(self, flow: TitleFlowTh08) -> None:
        state = (flow.cursor.index, flow.extra_unlocked, flow.spell_practice_unlocked)
        if state == self._last_state:
            return
        self._last_state = state
        cursor = flow.cursor.index
        for i in range(9):
            v = self._vms[1 + i]
            base = self._base_sprites[1 + i]
            v.set_sprite(base if i == cursor else base + 1)
            gray = (i == ITEM_EXTRA_START and not flow.extra_unlocked) or (
                i == ITEM_SPELL_PRACTICE and not flow.spell_practice_unlocked
            )
            v.vm.color[:3] = _GRAY if gray else [255, 255, 255]

    def _draw_vm(self, surf: pygame.Surface, v: Vm2d) -> None:
        """按 vm 状态 blit; 菜单脚本带 op22 AnchorTopLeft → pos 是左上顶点。"""
        vm = v.vm
        img = v.surf
        if not vm.visible or img is None:
            return
        r, g, b, a = vm.color2 if vm.flag17 else vm.color  # :987 的二选一
        if a <= 0 or vm.scale[0] == 0.0 or vm.scale[1] == 0.0:
            return
        out = self._tcache.get(img, vm.scale[0], vm.scale[1], vm.rotation[2])
        if (r, g, b) != (255, 255, 255) or a < 255:
            out = self._tcache.get_modulated(out, r, g, b, a)
        surf.blit(out, (int(vm.pos[0] + vm.offset[0]), int(vm.pos[1] + vm.offset[1])))

    # ---- 底部帮助行(:368-375/:481-485; 原作烘贴图, 这里直接绘字) ----
    def _draw_text(
        self, surf: pygame.Surface, text: str, center_x: int, y: int, alpha: int = 255
    ) -> None:
        if self._help_font is None:
            self._help_font = _load_font(_HELP_FONT_SIZE)
        img = self._help_font.render(text, True, _HELP_COLOR)
        shadow = self._help_font.render(text, True, _HELP_SHADOW)
        if alpha < 255:
            img.set_alpha(alpha)
            shadow.set_alpha(alpha)
        rect = img.get_rect(center=(center_x, y))
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):  # 4 向描边
            surf.blit(shadow, rect.move(dx, dy))
        surf.blit(img, rect)

    def _draw_help(self, surf: pygame.Surface, flow: TitleFlowTh08) -> None:
        idx = flow.cursor.index
        if idx != self._last_help_idx:
            self._last_help_idx = idx
            self._help_fade = 0  # 换项重淡入(:483-484 SetInterrupt(1))
        alpha = min(255, self._help_fade * 255 // _HELP_FADE_FRAMES)
        if self._help_fade < _HELP_FADE_FRAMES:
            self._help_fade += 1
        if idx < len(HELP_TEXTS):
            self._draw_text(surf, HELP_TEXTS[idx], _HELP_POS[0], _HELP_POS[1], alpha)

    # ---- 一帧 ----
    def render(
        self,
        flow: TitleFlowTh08,
        *,
        show_unimplemented: bool = False,
        fade_frame: int | None = None,
    ) -> pygame.Surface:
        surf = self._frame
        surf.blit(self._bg, (0, 0))
        self._sync_menu(flow)
        for v in self._vms:
            v.execute()
        for v in self._vms:
            self._draw_vm(surf, v)
        self._draw_help(surf, flow)
        if show_unimplemented:
            # A 期未实装项提示(一期口径的保留; 原作无此元素)
            self._draw_text(surf, "(未实装 — 二期)", _W // 2, 424)
        if fade_frame is not None and 0 <= fade_frame < _FADE_FRAMES:
            # 白淡入(CalcFadeIn: overlayAlpha = 255 - 255*timer/duration)
            alpha = int(255.0 - 255.0 * fade_frame / _FADE_FRAMES)
            self._fade_overlay.set_alpha(max(0, alpha))
            surf.blit(self._fade_overlay, (0, 0))
        return surf


__all__ = ["TitleView"]
