"""th08 Music Room 的原作版渲染 —— music.jpg 背景 + music00.anm 主装饰 vm + 直接绘字。

对照 th08-ref MusicRoom.cpp(行号相对其 src/) OnDraw(:318-389)/
AddedCallback(:392-553); 原作曲名/简介是 DrawTextLeft 烘贴图进 vm,
这里直接绘字(§19.1 路线, 同 title_view/option_view 的帮助行):

- 背景 = LoadSurface(0, "result/music.jpg")(:394; 封包内条目名扁平 "music.jpg");
  主装饰 = music00.anm 槽 23 script 0(:405, 256x64 音符条 60 帧弹跳入场)。
- 曲目表 10 行窗口(:325): 行位 (93, (i+1-listingOffset)*18+84) 左上锚(:548
  anchor=3), 序号 "%2d." 在 x-45, 光标行箭头(ascii 0x7f)在 x-60;
  曲名色: 解锁 0xc0e0ff/影 0x302080, 未解锁 0x80a0c0/影 0x100040 占位
  (:524-536); 光标行全亮, 其余行脚本 interrupt 2 → 灰 128 调制 + alpha 224
  (music00.anm scripts 1..30 的 op33/34, 8 帧插值 —— 这里瞬时切换, 偏离注明)。
- 简介 7 行落位 (64, 320+16i)(text.anm scripts 10..16), 色 0xffe0c0/影
  0x300000; 换曲/进场后 frameCount 10,12,...,22 逐行重绘 + 8 帧淡入(:137-169)。
- "Now Playing" 白字 (320,32) 常显(:373-378); 播曲后该行内容 = 所选曲简介
  第 0 行, 落位 (320,52)(:380-388)。
- 颜色常量按 COLORREF 0x00BBGGRR 解读(同 title_view.py 的 :370-371 口径)。
"""

from __future__ import annotations

import io

import pygame

from ....engine.view.anm_fx import TransformCache, Vm2d
from ....engine.view.sprite_bank import SpriteBank
from ..crypt import try_decrypt_from_table
from .anm_vm import AnmVmTh08
from .music_flow import (
    DESC_FADE_FRAMES,
    DESC_LINES,
    DESC_REVEAL_START,
    DESC_REVEAL_STEP,
    MUSIC_ROOM_VISIBLE,
    MusicRoomFlowTh08,
)
from .title_view import _TitleScriptBank, _load_font

_W, _H = 640, 480

_MUSIC_ANM = "music00.anm"  # 槽 23(:399)
_MUSIC_BG = "music.jpg"  # 封包内条目名(C++ 侧 "result/music.jpg", :394)

_ROW_X = 93  # 曲名 x(:329)
_ROW_Y0 = 102  # 首行 y(:330 的 (i+1-listingOffset)*18+104-20)
_ROW_H = 18
_NUM_X = 48  # 序号 x(:329-45, :351)
_ARROW_X = 33  # 光标箭头 x(:329-60, :344)
_ARROW = "→"  # ascii 0x7f 字形(:337)
_SONG_COLOR = (255, 224, 192)  # 0xc0e0ff(:526)
_SONG_SHADOW = (128, 32, 48)  # 0x302080
_SONG_LOCKED = (192, 160, 128)  # 0x80a0c0(:533)
_SONG_LOCKED_SHADOW = (64, 0, 16)  # 0x100040
_DIM_RGB = 0.5  # 非光标行 interrupt 2 的 color 128 调制(music00.anm op33)
_DIM_ALPHA = 224  # 同 op34
_DESC_X = 64  # 简介 x(text.anm scripts 10..16 的 op6)
_DESC_Y0 = 320
_DESC_H = 16
_DESC_COLOR = (192, 224, 255)  # 0xffe0c0(:168)
_DESC_SHADOW = (0, 0, 48)  # 0x300000
_NP_POS = (320, 32)  # "Now Playing"(:375-376)
_NP_LINE_POS = (320, 52)  # 播放中行(:382-384)
_WHITE = (255, 255, 255)
_FONT_SIZE = 15  # ascii 字体 fontHeight=15(DrawTextLeft 缺省档)
_ENTRY_FADE = 8  # 进场曲名淡入帧数(interrupt 1/2 的 8 帧插值)


def _dim(color: tuple[int, int, int]) -> tuple[int, int, int]:
    return tuple(int(c * _DIM_RGB) for c in color)  # type: ignore[return-value]


def _blit_text(
    surf: pygame.Surface,
    font: pygame.font.Font | None,
    text: str,
    x: int,
    y: int,
    color: tuple[int, int, int],
    shadow: tuple[int, int, int],
    alpha: int = 255,
) -> None:
    """左对齐绘字(4 向描边; 原作烘贴图进 vm, 这里直接绘字)。"""
    if font is None or not text:
        return
    img = font.render(text, True, color)
    sh = font.render(text, True, shadow)
    if alpha < 255:
        img.set_alpha(alpha)
        sh.set_alpha(alpha)
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        surf.blit(sh, (x + dx, y + dy))
    surf.blit(img, (x, y))


class MusicRoomView:
    """Music Room 渲染器: render(flow, frame) 画一帧到 640x480 surface。

    构造失败(无数据/资源损坏)抛异常, 由后端回退文字菜单(口径同
    TitleView/_OptionViewBase)。frame==0 = 进屏(曲名 8 帧淡入)。
    """

    def __init__(self, data_path) -> None:
        self._bank = SpriteBank(data_path, game="th08")
        self._tcache = TransformCache()
        self._sb = _TitleScriptBank(self._bank, _MUSIC_ANM)
        # music00.anm 与 title01.anm 同病: 扁平存储 id 全文件连续编号,
        # 引擎扁平分支按 entry 前缀定位会错位 —— 复用 _TitleScriptBank 的
        # 装载序重建(title_view.py:57)
        if not self._sb.ok:
            raise FileNotFoundError(_MUSIC_ANM)
        raw = try_decrypt_from_table(self._bank._archive().load(_MUSIC_BG))
        self._bg = pygame.image.load(io.BytesIO(raw))
        try:
            self._bg = self._bg.convert()  # 快速 blit 路径(需 display 初始化)
        except pygame.error:
            pass
        self._main = Vm2d(self._sb, self._tcache, vm_cls=AnmVmTh08)
        if not self._main.start(0):
            raise ValueError(f"{_MUSIC_ANM} 缺脚本 0")
        self._frame = pygame.Surface((_W, _H))
        self._font = None

    def _draw_vm(self, surf: pygame.Surface, v: Vm2d) -> None:
        """按 vm 状态 blit(锚点感知; 与 _OptionViewBase._draw_vm 同逻辑)。"""
        vm = v.vm
        img = v.surf
        if not vm.visible or img is None:
            return
        r, g, b, a = vm.color2 if vm.flag17 else vm.color
        if a <= 0 or vm.scale[0] == 0.0 or vm.scale[1] == 0.0:
            return
        out = self._tcache.get(img, vm.scale[0], vm.scale[1], vm.rotation[2])
        if (r, g, b) != (255, 255, 255) or a < 255:
            out = self._tcache.get_modulated(out, r, g, b, a)
        x = int(vm.pos[0] + vm.offset[0])
        y = int(vm.pos[1] + vm.offset[1])
        if not vm.anchor & 1:
            x -= out.get_width() // 2
        if not vm.anchor & 2:
            y -= out.get_height() // 2
        surf.blit(out, (x, y))

    def _draw_list(
        self, surf: pygame.Surface, flow: MusicRoomFlowTh08, frame: int
    ) -> None:
        """曲目表 10 行窗口(:325-353): 光标行全亮, 其余灰调制; 进场 8 帧淡入。"""
        if self._font is None:
            self._font = _load_font(_FONT_SIZE)
        n = len(flow.tracks)
        fade = min(255, (frame + 1) * 255 // _ENTRY_FADE)
        for i in range(
            flow.listing_offset, min(flow.listing_offset + MUSIC_ROOM_VISIBLE, n)
        ):
            y = _ROW_Y0 + (i - flow.listing_offset) * _ROW_H
            lit = i == flow.cursor
            if flow.is_unlocked(i):
                color, shadow = _SONG_COLOR, _SONG_SHADOW
            else:
                color, shadow = _SONG_LOCKED, _SONG_LOCKED_SHADOW
            alpha = min(fade, 255 if lit else _DIM_ALPHA)
            if not lit:
                color, shadow = _dim(color), _dim(shadow)
            if lit:
                _blit_text(surf, self._font, _ARROW, _ARROW_X, y, color, shadow, alpha)
            _blit_text(
                surf, self._font, f"{i + 1:>2}.", _NUM_X, y, color, shadow, alpha
            )
            _blit_text(
                surf, self._font, flow.display_title(i), _ROW_X, y, color, shadow, alpha
            )

    def _draw_description(self, surf: pygame.Surface, flow: MusicRoomFlowTh08) -> None:
        """简介 7 行(:356-358 + :137-169 的逐行重绘淡入; frameCount=flow.frames)。"""
        if self._font is None:
            self._font = _load_font(_FONT_SIZE)
        lines = flow.description_lines()
        for i in range(DESC_LINES):
            reveal = DESC_REVEAL_START + i * DESC_REVEAL_STEP
            if flow.frames < reveal or not lines[i]:
                continue
            alpha = min(255, (flow.frames - reveal + 1) * 255 // DESC_FADE_FRAMES)
            _blit_text(
                surf,
                self._font,
                lines[i],
                _DESC_X,
                _DESC_Y0 + i * _DESC_H,
                _DESC_COLOR,
                _DESC_SHADOW,
                alpha,
            )

    def render(self, flow: MusicRoomFlowTh08, frame: int = 0) -> pygame.Surface:
        surf = self._frame
        surf.blit(self._bg, (0, 0))
        self._main.execute()
        self._draw_vm(surf, self._main)
        self._draw_list(surf, flow, frame)
        self._draw_description(surf, flow)
        _blit_text(surf, self._font, "Now Playing", *_NP_POS, _WHITE, _DESC_SHADOW)
        np_line = flow.now_playing_line()
        if np_line:
            _blit_text(
                surf, self._font, np_line, *_NP_LINE_POS, _DESC_COLOR, _DESC_SHADOW
            )
        return surf


__all__ = ["MusicRoomView"]
