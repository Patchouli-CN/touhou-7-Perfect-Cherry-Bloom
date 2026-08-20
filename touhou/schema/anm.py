""" .anm 贴图包解析 —— Pythonic。

对照 th07 反编译源码 `AnmManager.cpp/.hpp` 还原:
entry 头(AnmRawEntry) / sprite 表(AnmRawSprite) / 内嵌纹理(ZunImageInfoEmbedded)。

格式要点(以 AnmManager.cpp:34-44 为准):
- 纹理 format: 1=A8R8G8B8, 2=A1R5G5B5, 3=R5G6B5, 4=R8G8B8, 5=A4R4G4B4
  (文件内字节序均为 D3D 小端, 即 32 位格式在文件里是 B,G,R,A)。
- `hasData=1` 时纹理由 `textureOffset` 指向内嵌数据, 16 字节头
  (i16 magic "TH"/"THTX"…, imageType, format, width, height, unused) 后跟像素。
- 一个 .anm 可有多个 entry, 用 `nextOffset` 链式相连(AnmManager::LoadAnms)。
- sprite 偏移表紧接 entry 头 64 字节处, 共 numSprites 项;
  之后是 numScripts 对 (id, 指令偏移)。脚本本期只解析不执行。

本模块不依赖 pygame; 调用方自行把 RGBA 字节转成 Surface/Texture。
"""

from __future__ import annotations

import struct
import msgspec

from ..registry import register_anm

# AnmManager.cpp:44 g_TextureBytesPerPixel(索引即 format)
_BYTES_PER_PIXEL = {1: 4, 2: 2, 3: 2, 4: 3, 5: 2}

_ENTRY_HEADER_SIZE = 64      # AnmRawEntry 到 spriteOffsets 之前
_SPRITE_SIZE = 20            # AnmRawSprite: i32 id + f32 x,y,w,h
_EMBEDDED_HEADER_SIZE = 16   # ZunImageInfoEmbedded 到 data 之前


class AnmSprite(msgspec.Struct, frozen=True):
    """一个 sprite: 纹理内的像素矩形。"""

    id: int
    x: int
    y: int
    w: int
    h: int


class AnmEntry(msgspec.Struct):
    """一个 .anm entry: 一张纹理 + 若干 sprite。"""

    name: str
    format: int
    width: int                   # 逻辑宽(entry 头), 内嵌纹理时等于纹理宽
    height: int
    tex_width: int
    tex_height: int
    rgba: bytes   # 整图 RGBA, 长 tex_width*tex_height*4
    sprites: dict[int, AnmSprite] = msgspec.field(default_factory=dict)

    def __repr__(self) -> str:
        # rgba 是整图字节串, 不进 repr (对照原 dataclass 的 field(repr=False))
        return (f"AnmEntry(name={self.name!r}, format={self.format!r}, "
                f"width={self.width!r}, height={self.height!r}, "
                f"tex_width={self.tex_width!r}, tex_height={self.tex_height!r}, "
                f"sprites={self.sprites!r})")


def _decode_texture(fmt: int, width: int, height: int, data: bytes) -> bytes:
    """把 D3D 小端像素解码成 RGBA 字节串。"""
    n = width * height
    if fmt == 1:  # A8R8G8B8, 文件内 B,G,R,A
        out = bytearray(n * 4)
        out[0::4] = data[2::4]
        out[1::4] = data[1::4]
        out[2::4] = data[0::4]
        out[3::4] = data[3::4]
        return bytes(out)
    out = bytearray(n * 4)
    if fmt == 5:  # A4R4G4B4: b:4 g:4 r:4 a:4 (低位起)
        for i in range(n):
            v = data[2 * i] | (data[2 * i + 1] << 8)
            b = v & 0xF
            g = (v >> 4) & 0xF
            r = (v >> 8) & 0xF
            a = (v >> 12) & 0xF
            o = 4 * i
            out[o] = r * 17
            out[o + 1] = g * 17
            out[o + 2] = b * 17
            out[o + 3] = a * 17
    elif fmt == 2:  # A1R5G5B5: b:5 g:5 r:5 a:1
        for i in range(n):
            v = data[2 * i] | (data[2 * i + 1] << 8)
            out[4 * i] = ((v >> 10) & 0x1F) * 255 // 31
            out[4 * i + 1] = ((v >> 5) & 0x1F) * 255 // 31
            out[4 * i + 2] = (v & 0x1F) * 255 // 31
            out[4 * i + 3] = 255 if v & 0x8000 else 0
    elif fmt == 3:  # R5G6B5: b:5 g:6 r:5, 无 alpha
        for i in range(n):
            v = data[2 * i] | (data[2 * i + 1] << 8)
            out[4 * i] = ((v >> 11) & 0x1F) * 255 // 31
            out[4 * i + 1] = ((v >> 5) & 0x3F) * 255 // 63
            out[4 * i + 2] = (v & 0x1F) * 255 // 31
            out[4 * i + 3] = 255
    elif fmt == 4:  # R8G8B8, 文件内 B,G,R, 无 alpha
        out[0::4] = data[2::3]
        out[1::4] = data[1::3]
        out[2::4] = data[0::3]
        out[3::4] = b"\xff" * n
    else:
        raise ValueError(f"未知 anm 纹理 format: {fmt}")
    return bytes(out)


@register_anm("th07", version=2)
class AnmFile:
    """解析后的 .anm: 多个 entry, 提供按 sprite id 取图。

    sprite id 在不同 entry 间可能重复(各自独立编号), 因此
    `sprite_image(sprite_id)` 默认在含纹理 sprite 最多的 entry
    (主纹理)里查; 也可显式传 entry 索引。
    """

    def __init__(self, entries: list[AnmEntry]) -> None:
        self.entries = entries

    @classmethod
    def parse(cls, data: bytes) -> "AnmFile":
        entries: list[AnmEntry] = []
        offset = 0
        while True:
            entries.append(cls._parse_entry(data, offset))
            next_offset = struct.unpack_from("<i", data, offset + 56)[0]
            if next_offset == 0:
                break
            offset += next_offset
        return cls(entries)

    @staticmethod
    def _parse_entry(data: bytes, base: int) -> AnmEntry:
        (num_sprites, _num_scripts, _tex_idx, width, height, fmt, _color_key,
         name_offset, _sprite_idx_offset, _mipmap_name_offset, version,
         _priority, texture_offset) = struct.unpack_from("<13i", data, base)
        if version != 2:
            raise ValueError(f"anm 版本不符: {version}")
        has_data = data[base + 52]   # AnmRawEntry.hasData(13×i32 之后的 u8)
        end = data.index(b"\0", base + name_offset)
        name = data[base + name_offset:end].decode("latin-1")

        if not has_data:
            raise ValueError(
                f"{name}: 外链纹理暂不支持(需要连同外部图像文件一起解析)")
        # ZunImageInfoEmbedded: magic 等 6 个 i16 + i32 unused, 像素从 +16 起
        t = base + texture_offset
        img_fmt, tex_w, tex_h = struct.unpack_from("<3h", data, t + 6)
        raw = data[t + _EMBEDDED_HEADER_SIZE:
                   t + _EMBEDDED_HEADER_SIZE + tex_w * tex_h * _BYTES_PER_PIXEL[img_fmt]]
        rgba = _decode_texture(img_fmt, tex_w, tex_h, raw)

        sprites: dict[int, AnmSprite] = {}
        # sprite 像素坐标 = 逻辑坐标 * (纹理宽 / entry 逻辑宽)
        sx = tex_w / width
        sy = tex_h / height
        for i in range(num_sprites):
            so = struct.unpack_from("<i", data, base + _ENTRY_HEADER_SIZE + i * 4)[0]
            sid, x, y, w, h = struct.unpack_from("<iffff", data, base + so)
            sprites[sid] = AnmSprite(
                sid, round(x * sx), round(y * sy), round(w * sx), round(h * sy))
        return AnmEntry(name, fmt, width, height, tex_w, tex_h, rgba, sprites)

    def sprite_image(self, sprite_id: int, entry: int | None = None
                     ) -> tuple[int, int, bytes]:
        """取 sprite 图像: (w, h, rgba_bytes)。纹理只解码一次, 这里只裁剪。"""
        if entry is None:
            # 默认选 sprite 数最多的 entry(标题素材的主纹理)
            entry = max(range(len(self.entries)),
                        key=lambda i: len(self.entries[i].sprites))
        e = self.entries[entry]
        spr = e.sprites[sprite_id]
        out = bytearray(spr.w * spr.h * 4)
        stride = e.tex_width * 4
        for row in range(spr.h):
            src = ((spr.y + row) * e.tex_width + spr.x) * 4
            out[row * spr.w * 4:(row + 1) * spr.w * 4] = e.rgba[src:src + spr.w * 4]
        return spr.w, spr.h, bytes(out)


class AnmInstr(msgspec.Struct, frozen=True):
    """一条 anm 脚本指令(AnmRawInstr): i16 opcode + u16 size + i16 time +
    u16 flags + args。args_i/args_f 是参数区的 i32/f32 视图。"""

    opcode: int
    time: int
    flags: int
    args_i: tuple[int, ...]
    args_f: tuple[float, ...]


def parse_scripts(data: bytes) -> list[dict[int, list[AnmInstr]]]:
    """解析 .anm 全部脚本: [{script_id: [AnmInstr, ...]}, ...](按 entry)。

    指令流以 opcode -1 (ANM_EXIT_HIDE) 结束(AnmManager.cpp interrupt 扫描
    同样以 -1 为脚本段结尾)。JUMP/条件跳的 args[0] 目标是相对脚本起点的
    字节偏移, 这里保留原值, 由执行方按 instr 列表重建偏移映射。
    """
    out: list[dict[int, list[AnmInstr]]] = []
    offset = 0
    while True:
        num_sprites, num_scripts = struct.unpack_from("<2i", data, offset)
        table = offset + _ENTRY_HEADER_SIZE + num_sprites * 4
        scripts: dict[int, list[AnmInstr]] = {}
        for i in range(num_scripts):
            sid, soff = struct.unpack_from("<2i", data, table + i * 8)
            p = offset + soff
            instrs: list[AnmInstr] = []
            while p + 8 <= len(data):
                opcode, size, time, flags = struct.unpack_from("<hHhH", data, p)
                if size < 8 or p + size > len(data):
                    break
                nargs = (size - 8) // 4
                args_i = struct.unpack_from(f"<{nargs}i", data, p + 8)
                args_f = struct.unpack_from(f"<{nargs}f", data, p + 8)
                instrs.append(AnmInstr(opcode, time, flags, args_i, args_f))
                if opcode == -1:
                    break
                p += size
            scripts[sid] = instrs
        out.append(scripts)
        next_offset = struct.unpack_from("<i", data, offset + 56)[0]
        if next_offset == 0:
            break
        offset += next_offset
    return out
