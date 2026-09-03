""".anm 贴图包解析 —— Pythonic。

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

v3(th08, 东方永夜抄)泛化(th08-ref AnmManager.cpp; v3 专属装配在
games/th08/anm.py, 本模块只做机制):
- entry 头/sprite 二进制/脚本指令布局与 v2 逐字节同构, 唯一硬性差异是
  version 校验(v2=2, v3=3; AnmManager.cpp:2457,2552) → 类属性
  ``_EXPECTED_VERSION``, 子类覆写即可。
- ``hasData=0`` 两分支(LoadTextureData, AnmManager.cpp:2558-2586):
  名字 ``@`` 开头 → 按 entry 头宽高给透明 RGBA 空纹理(CreateEmptyTexture
  :2309 只分配不初始化, 这里初始化为全透明); 否则为外链纹理, 由调用方
  注入 ``texture_loader(name, color_key, fmt) -> (width, height, rgba)``
  (CreateTextureFromFile :2225-2235 的 D3DX 加载 + colorKey 抠色;
  color_key=0 表示不抠色)。无 loader 时保持 v2 旧行为 raise。
- sprite 像素坐标文件里就是 f32(两作同构 20 字节); int 字段照旧 round,
  未取整的 float 视图另存 ``fx/fy/fw/fh``(th08 的 AnmLoadedSprite 保留
  float 精度, AnmManager.hpp:267-284)。

本模块不依赖 pygame; 调用方自行把 RGBA 字节转成 Surface/Texture。
"""

from __future__ import annotations

import struct
from collections.abc import Callable

import msgspec
import numpy as np

from ..registry import register_anm

# AnmManager.cpp:44 g_TextureBytesPerPixel(索引即 format)
_BYTES_PER_PIXEL = {1: 4, 2: 2, 3: 2, 4: 3, 5: 2}

_ENTRY_HEADER_SIZE = 64  # AnmRawEntry 到 spriteOffsets 之前
_SPRITE_SIZE = 20  # AnmRawSprite: i32 id + f32 x,y,w,h
_EMBEDDED_HEADER_SIZE = 16  # ZunImageInfoEmbedded 到 data 之前

#: 外链纹理加载器(调用方注入): (entry 名, colorKey, format) → (w, h, RGBA 字节)
TextureLoader = Callable[[str, int, int], tuple[int, int, bytes]]

# 进程级解析缓存 (BUGS.md 增量#3): 同一 .anm 被多个视图/SpriteBank 实例
# 各自重复解码(实测 ascii.anm 被 4 个实例各解一次, 单次 ~300ms)。
# key: 无 texture_loader 时是 raw bytes 本身(不可变; AnmFile 解析后只读,
# 全仓库无 entries/rgba 原地写, 共享安全); 带 loader 时结果依赖 loader,
# 用 (bytes, 格式类, cache_tag) 分键防串。游戏资产集合有限, 不淘汰。
_PARSE_CACHE: dict[object, "AnmFile"] = {}


def parse_cached(data: bytes) -> "AnmFile":
    """AnmFile.parse 的进程级缓存版; 缓存未命中才真实解码。"""
    return AnmFile.parse_cached(data)


class AnmSprite(msgspec.Struct, frozen=True):
    """一个 sprite: 纹理内的像素矩形。

    x/y/w/h 是取整后的 int(原 v2 行为); fx/fy/fw/fh 是未取整的 f32 视图
    (文件原始值 × 纹理缩放比, AnmManager.cpp:2604-2611 的 scaleFactor 换算;
    th08 需要半像素精度时读 float 视图)。"""

    id: int
    x: int
    y: int
    w: int
    h: int
    fx: float = 0.0
    fy: float = 0.0
    fw: float = 0.0
    fh: float = 0.0


class AnmEntry(msgspec.Struct):
    """一个 .anm entry: 一张纹理 + 若干 sprite。"""

    name: str
    format: int
    width: int  # 逻辑宽(entry 头), 内嵌纹理时等于纹理宽
    height: int
    tex_width: int
    tex_height: int
    rgba: bytes  # 整图 RGBA, 长 tex_width*tex_height*4
    sprites: dict[int, AnmSprite] = msgspec.field(default_factory=dict)

    def __repr__(self) -> str:
        # rgba 是整图字节串, 不进 repr (对照原 dataclass 的 field(repr=False))
        return (
            f"AnmEntry(name={self.name!r}, format={self.format!r}, "
            f"width={self.width!r}, height={self.height!r}, "
            f"tex_width={self.tex_width!r}, tex_height={self.tex_height!r}, "
            f"sprites={self.sprites!r})"
        )


def _decode_texture(fmt: int, width: int, height: int, data: bytes) -> bytes:
    """把 D3D 小端像素解码成 RGBA 字节串。

    fmt 2/3/5 用 numpy 向量化(与逐像素整数运算逐位等价) —— 纯 Python 逐
    像素循环解码 1024x1024 纹理要 ~150ms, 是首用懒加载卡顿的主因之一
    (BUGS.md 增量#3)。
    """
    n = width * height
    if fmt == 1:  # A8R8G8B8, 文件内 B,G,R,A
        out = bytearray(n * 4)
        out[0::4] = data[2::4]
        out[1::4] = data[1::4]
        out[2::4] = data[0::4]
        out[3::4] = data[3::4]
        return bytes(out)
    if fmt in (2, 3, 5):
        v = np.frombuffer(data, dtype="<u2", count=n).astype(np.uint32)
        if fmt == 5:  # A4R4G4B4: b:4 g:4 r:4 a:4 (低位起)
            r = ((v >> 8) & 0xF) * 17
            g = ((v >> 4) & 0xF) * 17
            b = (v & 0xF) * 17
            a = ((v >> 12) & 0xF) * 17
        elif fmt == 2:  # A1R5G5B5: b:5 g:5 r:5 a:1
            r = ((v >> 10) & 0x1F) * 255 // 31
            g = ((v >> 5) & 0x1F) * 255 // 31
            b = (v & 0x1F) * 255 // 31
            a = np.where(v & 0x8000, 255, 0)
        else:  # fmt == 3, R5G6B5: b:5 g:6 r:5, 无 alpha
            r = ((v >> 11) & 0x1F) * 255 // 31
            g = ((v >> 5) & 0x3F) * 255 // 63
            b = (v & 0x1F) * 255 // 31
            a = np.full(n, 255, dtype=np.uint32)
        return np.stack([r, g, b, a], axis=1).astype(np.uint8).tobytes()
    out = bytearray(n * 4)
    if fmt == 4:  # R8G8B8, 文件内 B,G,R, 无 alpha
        out[0::4] = data[2::3]
        out[1::4] = data[1::3]
        out[2::4] = data[0::3]
        out[3::4] = b"\xff" * n
    else:
        raise ValueError(f"未知 anm 纹理 format: {fmt}")
    return bytes(out)


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

    指令布局 v2/v3 完全相同(th08-ref AnmManager.hpp:288-299), 版本无关。
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


@register_anm("th07", version=2)
class AnmFile:
    """解析后的 .anm: 多个 entry, 提供按 sprite id 取图。

    sprite id 在不同 entry 间可能重复(各自独立编号), 因此
    `sprite_image(sprite_id)` 默认在含纹理 sprite 最多的 entry
    (主纹理)里查; 也可显式传 entry 索引。

    注册表鸭子接口(get_game(game).anm.format 的消费面): parse /
    parse_cached / parse_scripts / entries / sprite_image。
    """

    #: 期望的 anm version(entry 头校验, AnmManager.cpp:2457; th07=2, th08=3)
    _EXPECTED_VERSION = 2

    # 版本无关, 提到类上让注册表消费方只认格式类一个入口
    parse_scripts = staticmethod(parse_scripts)

    def __init__(self, entries: list[AnmEntry]) -> None:
        self.entries = entries

    @classmethod
    def parse(
        cls, data: bytes, *, texture_loader: TextureLoader | None = None
    ) -> "AnmFile":
        entries: list[AnmEntry] = []
        offset = 0
        while True:
            entries.append(cls._parse_entry(data, offset, texture_loader))
            next_offset = struct.unpack_from("<i", data, offset + 56)[0]
            if next_offset == 0:
                break
            offset += next_offset
        return cls(entries)

    @classmethod
    def parse_cached(
        cls,
        data: bytes,
        *,
        texture_loader: TextureLoader | None = None,
        cache_tag: object = None,
    ) -> "AnmFile":
        """parse 的进程级缓存版。

        无 loader 时以 raw bytes 为 key(与旧模块级 parse_cached 同键);
        带 loader 时结果依赖 loader, 按 (bytes, 格式类, cache_tag) 分键防串。
        """
        key: object = data if texture_loader is None else (data, cls, cache_tag)
        out = _PARSE_CACHE.get(key)
        if out is None:
            out = cls.parse(data, texture_loader=texture_loader)
            _PARSE_CACHE[key] = out
        return out

    @classmethod
    def _parse_entry(
        cls, data: bytes, base: int, texture_loader: TextureLoader | None
    ) -> AnmEntry:
        (
            num_sprites,
            _num_scripts,
            _tex_idx,
            width,
            height,
            fmt,
            color_key,
            name_offset,
            _sprite_idx_offset,
            _mipmap_name_offset,
            version,
            _priority,
            texture_offset,
        ) = struct.unpack_from("<13i", data, base)
        if version != cls._EXPECTED_VERSION:
            raise ValueError(f"anm 版本不符: {version}")
        has_data = data[base + 52]  # AnmRawEntry.hasData(13×i32 之后的 u8)
        end = data.index(b"\0", base + name_offset)
        name = data[base + name_offset : end].decode("latin-1")

        if not has_data:
            # LoadTextureData (th08-ref AnmManager.cpp:2558-2576) 的 hasData=0 两分支
            if name.startswith("@"):
                # CreateEmptyTexture(:2309): 按 entry 头宽高建空纹理;
                # C++ 只分配不初始化, 这里初始化为全透明 RGBA
                tex_w, tex_h = width, height
                rgba = bytes(width * height * 4)
            else:
                if texture_loader is None:
                    raise ValueError(
                        f"{name}: 外链纹理暂不支持(需要连同外部图像文件一起解析)"
                    )
                # CreateTextureFromFile(:2225): 外部图像解码 + colorKey 抠色
                # 是 loader 的职责(games/th08/anm.py 接线)
                tex_w, tex_h, rgba = texture_loader(name, color_key, fmt)
        else:
            # ZunImageInfoEmbedded: magic 等 6 个 i16 + i32 unused, 像素从 +16 起
            t = base + texture_offset
            img_fmt, tex_w, tex_h = struct.unpack_from("<3h", data, t + 6)
            raw = data[
                t + _EMBEDDED_HEADER_SIZE : t
                + _EMBEDDED_HEADER_SIZE
                + tex_w * tex_h * _BYTES_PER_PIXEL[img_fmt]
            ]
            rgba = _decode_texture(img_fmt, tex_w, tex_h, raw)

        sprites: dict[int, AnmSprite] = {}
        # sprite 像素坐标 = 逻辑坐标 * (纹理宽 / entry 逻辑宽)
        sx = tex_w / width
        sy = tex_h / height
        for i in range(num_sprites):
            so = struct.unpack_from("<i", data, base + _ENTRY_HEADER_SIZE + i * 4)[0]
            sid, x, y, w, h = struct.unpack_from("<iffff", data, base + so)
            fx, fy, fw, fh = x * sx, y * sy, w * sx, h * sy
            sprites[sid] = AnmSprite(
                sid, round(fx), round(fy), round(fw), round(fh), fx, fy, fw, fh
            )
        return AnmEntry(name, fmt, width, height, tex_w, tex_h, rgba, sprites)

    def sprite_image(
        self, sprite_id: int, entry: int | None = None
    ) -> tuple[int, int, bytes]:
        """取 sprite 图像: (w, h, rgba_bytes)。纹理只解码一次, 这里只裁剪。"""
        if entry is None:
            # 默认选 sprite 数最多的 entry(标题素材的主纹理)
            entry = max(
                range(len(self.entries)), key=lambda i: len(self.entries[i].sprites)
            )
        e = self.entries[entry]
        spr = e.sprites[sprite_id]
        out = bytearray(spr.w * spr.h * 4)
        stride = e.tex_width * 4
        for row in range(spr.h):
            src = ((spr.y + row) * e.tex_width + spr.x) * 4
            out[row * spr.w * 4 : (row + 1) * spr.w * 4] = e.rgba[src : src + spr.w * 4]
        return spr.w, spr.h, bytes(out)

