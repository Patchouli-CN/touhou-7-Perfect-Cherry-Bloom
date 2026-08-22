""" 对话渲染 —— 对照 GuiImpl::DrawDialogue (Gui.cpp:1063-1165)。

- 对话框: y=384 起, 高 48, 前 60 帧渐高(timer*48/60); 黑色半透明,
  顶 alpha 0xd0 → 底 alpha 0x90; x 范围 16..368(arcade 区左右各留 16)。
- 立绘: portraits[0]=自机(左), portraits[1]=Boss(右), 画在对话框之下(先画),
  非说话方压暗(SWITCH interrupt 3=暗/4=亮)。
- 文本: fontSize=15 粗体, 颜色 textColorsA[0]=0xE8F0FF(自机) /
  [1]=0xFFE8F0(Boss), 黑描边; 逐字由 MsgVm 的 reveal 计数给出(近似;
  原版整行淡入且无打字机音, 考据见 schema/msg.py 模块 docstring)。
- TEXT_INTRODUCE(Boss 名)右对齐画在对话框上方右侧。

日文字体: 优先 pygame 系统字体(MS Gothic 系); 找不到时退化成占位块
(headless/无字体环境容错, 测试不依赖字体)。

资源在运行时从 th07.dat 解(face_rm/mr/sk00.anm + face_0{stage}_00.anm),
仓库不留二进制素材。
"""

from __future__ import annotations

import struct
from pathlib import Path

import pygame

from ....logger import logger as log
from ....schema.anm import AnmFile
from ....schema.archive import GameArchive
from ....schema.msg import MsgOpcode, MsgVm, TEXT_COLORS_A

# 角色 → 自机立绘 anm(C: character 0..2 → face_rm/mr/sk00.anm, Gui.cpp:421-456)
CHARACTER_FACE = ("face_rm00.anm", "face_mr00.anm", "face_sk00.anm")

# 立绘缩放/位置(视觉近似: 原版坐标在 face anm 脚本里, 未逐条还原)
PORTRAIT_SCALE = 0.75
PORTRAIT_X = (8, 384 - 8)      # 左/右立绘锚点 x(右侧为右缘)
PORTRAIT_BOTTOM = 448          # 底缘对齐屏幕底

BOX_X0, BOX_X1 = 16, 368       # arcadeTopLeft(32) 相对游戏面原点, 即 16..368
BOX_Y, BOX_H = 384, 48
BOX_FADEIN_FRAMES = 60
TEXT_X, TEXT_Y0, TEXT_LINE_H = 24, 388, 18
INTRO_RIGHT_X, INTRO_Y0, INTRO_LINE_H = 360, 336, 20

_FONT_CANDIDATES = ("msgothic", "ms gothic", "msmincho", "meiryo",
                    "yu gothic", "hiragino sans", "noto sans cjk jp",
                    "microsoft yahei", "simhei")
_FONT_SIZE = 15

_dim_cache: dict = {}


def _load_font():
    """日文字体; 找不到返回 None(调用方画占位块)。"""
    if not pygame.font.get_init():
        try:
            pygame.font.init()
        except pygame.error:
            return None
    for name in _FONT_CANDIDATES:
        try:
            path = pygame.font.match_font(name)
        except Exception:
            path = None
        if path:
            try:
                return pygame.font.Font(path, _FONT_SIZE)
            except pygame.error:
                continue
    try:
        return pygame.font.SysFont(None, _FONT_SIZE)
    except pygame.error:
        return None


class _FaceBook:
    """一个角色/Boss 的立绘集: face script idx → pygame Surface。

    msg 的 anmScriptIdx 是跨 entry 累积的全局 sprite 号(C LoadAnms:
    每个 entry 的 spriteIdxOffset 按 max(sprite id, script id)+1 累加)。
    face_rm00: entry0 脸 0-3(4=竖条), entry1 脸 0-2(3=竖条) → 全局 8 起。
    """

    def __init__(self, anm: AnmFile, raw: bytes) -> None:
        self._faces: dict[int, pygame.Surface] = {}
        # 走 nextOffset 链拿各 entry 的文件内基址(anm.py 未暴露)
        bases = []
        off = 0
        for _ in anm.entries:
            bases.append(off)
            off += struct.unpack_from("<i", raw, off + 56)[0]
        base = 0   # 全局 sprite 号起点(C 的 spriteIdxOffset 累加)
        for entry_idx, entry in enumerate(anm.entries):
            eb = bases[entry_idx]
            ns, nsc = struct.unpack_from("<2i", raw, eb)
            max_id = max(entry.sprites) if entry.sprites else -1
            # script id 也占号(AnmRawEntry 内联表: 64 + numSprites*4 + i*8)
            for i in range(nsc):
                sid = struct.unpack_from("<i", raw, eb + 64 + ns * 4 + i * 8)[0]
                max_id = max(max_id, sid)
            for sid in sorted(entry.sprites):
                try:
                    w, h, rgba = anm.sprite_image(sid, entry=entry_idx)
                except KeyError:
                    continue
                self._faces[base + sid] = pygame.image.frombuffer(
                    rgba, (w, h), "RGBA").copy()  # copy: 脱离 bytes 且无需显示模式
            base += max_id + 1

    def get(self, face_idx: int) -> pygame.Surface | None:
        if face_idx in self._faces:
            return self._faces[face_idx]
        return self._faces.get(0)  # 未知表情回落 0 号脸


class DialogueView:
    """把一个 MsgVm 的当前状态画到游戏面上。"""

    def __init__(self, data_path: str | Path, *, character: int = 0,
                 stage: int = 1) -> None:
        self._font = _load_font()
        self._text_cache: dict = {}
        self._faces: list[_FaceBook | None] = [None, None]
        try:
            arc = GameArchive.open(data_path)
            player_face = CHARACTER_FACE[min(character, 2)]
            raw = arc.load(player_face)
            self._faces[0] = _FaceBook(AnmFile.parse(raw), raw)
            stage_face = f"face_0{stage}_00.anm"
            if stage_face in arc:
                raw = arc.load(stage_face)
                self._faces[1] = _FaceBook(AnmFile.parse(raw), raw)
        except (KeyError, ValueError, OSError) as e:
            log.warning("对话立绘加载失败, 退化为无立绘: {}", e)
        # 预建对话框渐变纹理(宽 1, 逐行 alpha)
        grad = pygame.Surface((1, BOX_H), pygame.SRCALPHA)
        for y in range(BOX_H):
            a = 0xD0 + (0x90 - 0xD0) * y // max(BOX_H - 1, 1)
            grad.set_at((0, y), (0, 0, 0, a))
        self._grad = grad

    # ---- 文本(带缓存与无字体容错) ----
    def _text(self, text: str, color: int) -> pygame.Surface | None:
        if not text or self._font is None:
            return None
        key = (text, color)
        surf = self._text_cache.get(key)
        if surf is None:
            rgb = ((color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF)
            try:
                main = self._font.render(text, True, rgb)
            except pygame.error:
                return None
            shadow = self._font.render(text, True, (0, 0, 0))
            w, h = main.get_size()
            surf = pygame.Surface((w + 1, h + 1), pygame.SRCALPHA)
            surf.blit(shadow, (1, 1))
            surf.blit(main, (0, 0))
            if len(self._text_cache) > 256:
                self._text_cache.clear()
            self._text_cache[key] = surf
        return surf

    def _blit_text(self, surf: pygame.Surface, text: str, color: int,
                   pos: tuple[int, int], *, right: bool = False) -> None:
        img = self._text(text, color)
        if img is not None:
            rect = img.get_rect()
            if right:
                rect.topright = pos
            else:
                rect.topleft = pos
            surf.blit(img, rect)
            return
        # 无字体容错: 每个字符画一个占位块
        x, y = pos
        w = _FONT_SIZE
        if right:
            x -= w * len(text)
        for i, _ in enumerate(text):
            pygame.draw.rect(surf, (200, 200, 220, 160),
                             (x + i * w, y, w - 2, _FONT_SIZE))

    def _blit_portrait(self, surf: pygame.Surface, side: int,
                       face_idx: int, *, speaking: bool) -> None:
        book = self._faces[side]
        if book is None:
            return
        img = book.get(face_idx)
        if img is None:
            return
        w = int(img.get_width() * PORTRAIT_SCALE)
        h = int(img.get_height() * PORTRAIT_SCALE)
        img = pygame.transform.scale(img, (w, h))
        if not speaking:
            key = (side, face_idx, w, h)
            dim = _dim_cache.get(key)
            if dim is None:
                dim = img.copy()
                dim.fill((110, 110, 130), special_flags=pygame.BLEND_RGB_MULT)
                if len(_dim_cache) > 64:
                    _dim_cache.clear()
                _dim_cache[key] = dim
            img = dim
        x = PORTRAIT_X[side] if side == 0 else PORTRAIT_X[side] - w
        y = PORTRAIT_BOTTOM - h
        # 立绘在屏幕系只露到对话框底(原版画全高, 底部被对话框盖住)
        surf.blit(img, (x, y))

    # ---- 主入口 ----
    def render(self, surf: pygame.Surface, vm: MsgVm) -> None:
        """DrawDialogue: currentMsgIdx < 0(含 -2 转场)时不画。"""
        if vm is None or not vm.active:
            return
        # 立绘(先画, 垫在对话框下; interrupt 5=退场不画)
        for side in (0, 1):
            p = vm.portraits[side]
            if p.visible and not p.exited:
                self._blit_portrait(surf, side, p.face, speaking=p.speaking)
        # 对话框(前 60 帧渐高)
        height = BOX_H if vm.timer >= BOX_FADEIN_FRAMES \
            else vm.timer * BOX_H // BOX_FADEIN_FRAMES
        if height > 0:
            box = pygame.transform.scale(self._grad.subsurface(
                (0, 0, 1, height)), (BOX_X1 - BOX_X0, height))
            surf.blit(box, (BOX_X0, BOX_Y))
        # 对话两行 + 打字机 reveal
        for i, line in enumerate(vm.dialogue_lines):
            if line.visible and line.shown_text:
                color = TEXT_COLORS_A[line.color & 3]
                self._blit_text(surf, line.shown_text, color,
                                (TEXT_X, TEXT_Y0 + i * TEXT_LINE_H))
        # Boss 名(TEXT_INTRODUCE), 右对齐在对话框上方
        for i, line in enumerate(vm.intro_lines):
            if line.visible and line.shown_text:
                color = TEXT_COLORS_A[line.color & 3]
                self._blit_text(surf, line.shown_text, color,
                                (INTRO_RIGHT_X, INTRO_Y0 + i * INTRO_LINE_H),
                                right=True)
        # 推进提示: 停在 PAUSE 且当前行已全部显示时, 右下角画闪烁箭头
        if vm.active and vm.instr_idx < len(vm.msg_file.messages[vm.current_msg_idx]):
            if vm.msg_file.messages[vm.current_msg_idx][vm.instr_idx].opcode \
                    == MsgOpcode.PAUSE:
                lines_done = all(
                    (not l.visible) or l.reveal >= len(l.text)
                    for l in vm.dialogue_lines)
                if lines_done and (vm.timer // 16) % 2 == 0:
                    pygame.draw.polygon(
                        surf, (255, 255, 255),
                        [(BOX_X1 - 14, BOX_Y + BOX_H - 12),
                         (BOX_X1 - 6, BOX_Y + BOX_H - 12),
                         (BOX_X1 - 10, BOX_Y + BOX_H - 5)])
