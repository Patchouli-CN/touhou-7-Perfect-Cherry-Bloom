"""结局/staff roll 画面渲染(pygame) —— Ending.cpp 的移植(OnDraw 对应)。

演出由 engine/ending.py 的 EndingPlayer 逐帧驱动(指令集见该模块文件头):
- 背景: @b 载入的整图按 DrawEndingRect 画源矩形 (0, backgroundPos.y, 640, 480)
  (AnmManager.cpp:2535), @v/@V 数据驱动滚动;
- 立绘/CG: @a → staff01.anm 的脚本 VM (ANM_OFFSET_STAFF=0x600, AnmIdx.hpp:48),
  每槽一个 Vm2d (anm_fx.py), 脚本自带位置/淡入淡出/插值, @R/@F 清空;
- 文本: 逐行显示在 (TEXT_X, TEXT_Y0+i*TEXT_LINE_H) (Ending.cpp:496-497),
  颜色随 @c, @r 到时清屏;
- 淡入淡出: @0..@3 全屏单色覆盖 (FadingEffect, Ending.cpp:99-165);
- 音乐: @m/@M 事件由 GameApp 消费 (pending_music);
- @z 播完 → finished=True → GameApp 进结算 (Ending::DeletedCallback
  curState=6); Z/Enter = 跳过整段(简化: 原版为快进/跳行)。

staff roll: 结局 .end 末尾 @Fdata/staff00.end 自动续播 (staff00.jpg 滚动 +
staff01.anm CG + BGM th07_15.mid), 无需特判。
"""

from __future__ import annotations

import io
from pathlib import Path

import pygame

from ....schema.archive import GameArchive
from ....engine.ending import (
    TEXT_LINE_H,
    TEXT_MAX_SLOTS,
    TEXT_X,
    TEXT_Y0,
    EndingData,
    EndingPlayer,
)
from ....engine.view.anm_fx import AnmScriptBank, TransformCache, Vm2d
from ....engine.view.sprite_bank import SpriteBank
from .title_view import DEFAULT_DATA, TITLE_H, TITLE_W

ANM_OFFSET_STAFF = 0x600  # AnmIdx.hpp:48 (staff01.anm 载入偏移)


def _load_font(size: int):
    pygame.font.init()  # 幂等; headless 测试也要能用字体
    for name in ("Microsoft YaHei", "SimHei", "SimSun", None):
        try:
            return pygame.font.SysFont(name, size)
        except Exception:
            continue
    return pygame.font.Font(None, size)


class EndingView:
    """结局画面渲染器: render(surf, ending, frame) 画一帧。"""

    def __init__(self, data_path=DEFAULT_DATA) -> None:
        self._data_path = Path(data_path)
        self._archive = None  # 懒开(渲染第一帧才解包)
        self._bg_cache: dict[str, pygame.Surface | None] = {}
        self._font = _load_font(15)
        self._hint_font = _load_font(18)
        # ---- 播放器/立绘 VM 状态 ----
        self._player: EndingPlayer | None = None
        self._player_for: int | None = None  # id(EndingData), 换结局重建
        self._sprite_bank: SpriteBank | None = None
        self._staff_sbank: AnmScriptBank | None = None
        self._tcache = TransformCache()
        self._face_vms: dict[int, Vm2d] = {}
        self._face_keys: dict[int, tuple[int, int]] = {}
        self._faces_version = -1
        self.pending_music: list[tuple] = []  # ("play", name)/("fadeout", 秒)

    # ---- 资源 ----
    def _arch(self) -> GameArchive:
        if self._archive is None:
            self._archive = GameArchive.open(self._data_path)
        return self._archive

    def _bg(self, name: str | None) -> pygame.Surface | None:
        if not name:
            return None
        if name not in self._bg_cache:
            try:
                raw = self._arch().load(name)
                img = pygame.image.load(io.BytesIO(raw))
                try:
                    img = img.convert()  # 有显示模式时转格式(快)
                except pygame.error:
                    pass  # headless(无 set_mode)直接用原图
                self._bg_cache[name] = img
            except Exception:
                self._bg_cache[name] = None  # 缺资源 → 纯色底
        return self._bg_cache[name]

    def _load_end_file(self, name: str) -> bytes | None:
        """EndingPlayer 的 @F 续载回调(LoadEnding; 失败 → 结局结束)。"""
        try:
            return self._arch().load(name)
        except Exception:
            return None

    def _staff(self) -> AnmScriptBank | None:
        if self._staff_sbank is None:
            if self._sprite_bank is None:
                self._sprite_bank = SpriteBank(self._data_path)
            sbank = AnmScriptBank(self._sprite_bank, "staff01.anm", ANM_OFFSET_STAFF)
            self._staff_sbank = sbank if sbank.ok else None
        return self._staff_sbank

    # ---- 播放器 ----
    def _get_player(self, ending: EndingData) -> EndingPlayer:
        if self._player is None or self._player_for != id(ending):
            self._player = EndingPlayer(ending.ops, loader=self._load_end_file)
            self._player_for = id(ending)
            self._face_vms = {}
            self._face_keys = {}
            self._faces_version = -1
            self.pending_music = []
        return self._player

    @property
    def finished(self) -> bool:
        """脚本播完 (@z / @F 载入失败) → GameApp 进结算。"""
        return self._player is not None and self._player.done

    # ---- 立绘 VM 同步 (@a/@R/@F) ----
    def _sync_faces(self, player: EndingPlayer) -> None:
        if player.faces_version == self._faces_version:
            return
        self._faces_version = player.faces_version
        sbank = self._staff()
        for vm_idx in set(self._face_vms) - set(player.faces):
            del self._face_vms[vm_idx]  # @R/@F 清掉的槽位
            self._face_keys.pop(vm_idx, None)
        if sbank is None:
            return
        for vm_idx, (script, sprite) in player.faces.items():
            if self._face_keys.get(vm_idx) == (script, sprite):
                continue
            vm = Vm2d(sbank, self._tcache)
            # ExecuteAnmIdx(sprites[vm], script+ANM_OFFSET_STAFF) +
            # SetActiveSprite(sprite+ANM_OFFSET_STAFF) (Ending.cpp:258-259)
            if vm.start(ANM_OFFSET_STAFF + script):
                vm.set_sprite(sprite)
                self._face_vms[vm_idx] = vm
                self._face_keys[vm_idx] = (script, sprite)
            else:
                self._face_vms.pop(vm_idx, None)
                self._face_keys.pop(vm_idx, None)

    # ---- 渲染 ----
    def render(
        self,
        surf: pygame.Surface,
        ending: EndingData,
        frame: int,
        *,
        advance_held: bool = False,
        advance_pressed: bool = False,
    ) -> None:
        player = self._get_player(ending)
        player.tick(advance_held=advance_held, advance_pressed=advance_pressed)
        if player.music_events:
            self.pending_music += player.music_events
            player.music_events.clear()
        self._sync_faces(player)

        # 背景: DrawEndingRect(0, 0, 0, bgPos.x, bgPos.y, 640, 480)
        bg = self._bg(player.bg_name)
        if bg is not None:
            y = max(0, min(int(player.bg_y), bg.get_height() - TITLE_H))
            surf.blit(bg, (0, 0), area=pygame.Rect(0, y, TITLE_W, TITLE_H))
        else:
            surf.fill((8, 8, 24))
        # 立绘/CG (OnDraw: Draw(&sprites[i]))
        for vm_idx in sorted(self._face_vms):
            vm = self._face_vms[vm_idx]
            vm.execute()
            if not vm.alive:
                continue
            pos = vm.vm.pos
            vm.draw(surf, float(pos[0]), float(pos[1]))
        # 文本行 (sprites[i].pos = (64, i*16+392), 色随 @c)
        for i, line in enumerate(player.texts[:TEXT_MAX_SLOTS]):
            color = (
                (line.color >> 16) & 255,
                (line.color >> 8) & 255,
                line.color & 255,
            )
            t = self._font.render(line.text, True, color)
            surf.blit(t, (TEXT_X, TEXT_Y0 + i * TEXT_LINE_H))
        # 淡入淡出覆盖 (FadingEffect → DrawSquare 全屏)
        overlay = player.fade_overlay()
        if overlay is not None:
            r, g, b, a = overlay
            veil = pygame.Surface((TITLE_W, TITLE_H), pygame.SRCALPHA)
            veil.fill((r, g, b, a))
            surf.blit(veil, (0, 0))
        if frame % 60 < 40:  # 闪烁提示
            hint = self._hint_font.render("Z/Enter: 跳过", True, (255, 255, 255))
            surf.blit(hint, hint.get_rect(center=(TITLE_W // 2, TITLE_H - 24)))
