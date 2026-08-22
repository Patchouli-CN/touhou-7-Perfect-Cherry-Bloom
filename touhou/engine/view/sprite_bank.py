"""anm sprite 缓存(通用基建) —— 从 sprite_view.py 拆出的作品无关部分。

`SpriteBank`: 懒加载数据包(GameArchive), 按 (anm, entry, id) 缓存 sprite
Surface, 附带链式 entry 偏移表(LoadAnms: max(sprite id, script id)+1 累加)
与旋转/翻转变换缓存。th07 的布局常量与 GameView(战斗画面渲染)在
games/th07/view/sprite_view.py; 本模块不 import 任何作品包。
"""
from __future__ import annotations

import struct
from pathlib import Path

import pygame

from ...schema.anm import AnmFile
from ...schema.archive import GameArchive


def _parse_first_sprites(data: bytes) -> list[dict[int, int]]:
    """按 entry 解析 script 表: [{script_id: 首条 SET_ACTIVE_SPRITE 的 sprite id}]。

    AnmRawEntry: 64 字节头 + spriteOffsets[numSprites] + (id, offset)[numScripts];
    指令 AnmRawInstr(i16 opcode, u16 size, i16 time, u16 flags, args...),
    opcode 3 = ANM_SET_ACTIVE_SPRITE (AnmManager.hpp)。
    """
    out: list[dict[int, int]] = []
    offset = 0
    while True:
        num_sprites, num_scripts = struct.unpack_from("<2i", data, offset)
        table = offset + 64 + num_sprites * 4
        scripts: dict[int, int] = {}
        for i in range(num_scripts):
            sid, soff = struct.unpack_from("<2i", data, table + i * 8)
            p = offset + soff
            last = -1
            for _ in range(64):  # 代表帧: 看脚本开头一段
                opcode, size, _time = struct.unpack_from("<hHh", data, p)
                # EXIT*/JUMP/DEC_JUMP: 脚本段结束/回跳, 停止扫描
                if opcode in (-1, 1, 2, 4, 5):
                    break
                if opcode == 3:
                    # 记录最后一个 set-sprite: 出场特效(如妖精的漩涡帧)
                    # 在脚本开头, 代表帧取后面的本体帧
                    last = struct.unpack_from("<i", data, p + 8)[0]
                if size <= 0:
                    break
                p += size
            if last >= 0:
                scripts[sid] = last
        out.append(scripts)
        next_offset = struct.unpack_from("<i", data, offset + 56)[0]
        if next_offset == 0:
            break
        offset += next_offset
    return out


class SpriteBank:
    """anm sprite 缓存: 懒加载数据包, 按 (anm, entry, id) 缓存 Surface。"""

    def __init__(self, data_path: str | Path) -> None:
        self._data_path = Path(data_path)
        self._arc: GameArchive | None = None
        self._anms: dict[str, AnmFile] = {}
        self._raws: dict[str, bytes] = {}
        self._first: dict[str, list[dict[int, int]]] = {}
        self._chain: dict[str, list[int]] = {}
        self._surfs: dict[tuple[str, int, int], pygame.Surface | None] = {}
        self._rot: dict[tuple[int, int], pygame.Surface] = {}
        self._flip: dict[int, pygame.Surface] = {}

    # ---- 资源 ----
    def _archive(self) -> GameArchive:
        if self._arc is None:
            self._arc = GameArchive.open(self._data_path)
        return self._arc

    def _load(self, name: str) -> bool:
        if name in self._anms:
            return True
        arc = self._archive()
        raw = None
        for key in (name, f"data/{name}"):
            try:
                raw = arc.load(key)
                break
            except KeyError:
                continue
        if raw is None:
            return False
        self._anms[name] = AnmFile.parse(raw)
        self._raws[name] = raw
        self._first[name] = _parse_first_sprites(raw)
        # 链式 entry 偏移(LoadAnms: max(sprite id, script id)+1 累加)
        offs, cur = [], 0
        for entry, scripts in zip(self._anms[name].entries, self._first[name]):
            offs.append(cur)
            hi = max(entry.sprites.keys(), default=0)
            hi = max(hi, max(scripts.keys(), default=0))
            cur += hi + 1
        self._chain[name] = offs
        return True

    def has(self, name: str) -> bool:
        return self._load(name)

    def raw(self, name: str) -> bytes | None:
        """anm 文件原始字节(脚本解析用); 不存在返回 None。"""
        if not self._load(name):
            return None
        return self._raws[name]

    def sprite(self, name: str, sprite_id: int, entry: int = 0
               ) -> pygame.Surface | None:
        """取 sprite Surface(缓存); 不存在返回 None。"""
        key = (name, entry, sprite_id)
        if key in self._surfs:
            return self._surfs[key]
        surf = None
        if self._load(name):
            anm = self._anms[name]
            if 0 <= entry < len(anm.entries) \
                    and sprite_id in anm.entries[entry].sprites:
                w, h, rgba = anm.sprite_image(sprite_id, entry=entry)
                surf = pygame.image.fromstring(rgba, (w, h), "RGBA")
                try:
                    surf = surf.convert_alpha()  # 快速 blit 路径(需 display 初始化)
                except pygame.error:
                    pass
        self._surfs[key] = surf
        return surf

    def script_sprite(self, name: str, script_id: int, entry: int = 0
                      ) -> int | None:
        """script 首帧 sprite 的局部 id; 未知返回 None。"""
        if not self._load(name):
            return None
        if 0 <= entry < len(self._first[name]):
            return self._first[name][entry].get(script_id)
        return None

    def global_to_local(self, name: str, global_id: int, anm_offset: int
                        ) -> tuple[int, int] | None:
        """全局 sprite/script idx → (entry, 局部 id)(链式偏移反查)。"""
        if not self._load(name):
            return None
        local = global_id - anm_offset
        if local < 0:
            return None
        for i, off in enumerate(self._chain[name]):
            if local < off:
                break
            entry, lid = i, local - off
        else:
            entry, lid = len(self._chain[name]) - 1, local - self._chain[name][-1]
        if lid < 0:
            return None
        return entry, lid

    # ---- 变换(缓存) ----
    def rotated(self, surf: pygame.Surface, angle_deg: float) -> pygame.Surface:
        """按 6° 量化缓存的旋转。"""
        q = int(round(angle_deg / 6.0)) * 6 % 360
        key = (id(surf), q)
        out = self._rot.get(key)
        if out is None:
            out = pygame.transform.rotate(surf, q)
            self._rot[key] = out
        return out

    def flipped(self, surf: pygame.Surface) -> pygame.Surface:
        out = self._flip.get(id(surf))
        if out is None:
            out = pygame.transform.flip(surf, True, False)
            self._flip[id(surf)] = out
        return out
