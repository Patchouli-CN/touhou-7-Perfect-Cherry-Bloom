"""Touhou: .anm 解析测试(用真实 th07.dat 里的 title01.anm 核对)。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, r"D:\python_play\Touhou08")

from touhou.schema.anm import AnmFile  # noqa: E402
from touhou.schema.archive import open_archive  # noqa: E402

DAT = Path(r"D:\TOUHOU_GAME\[th07] 东方妖妖梦 (日文版)\th07.dat")
NEEDS_DAT = pytest.mark.skipif(not DAT.exists(), reason="需要真实 th07.dat")


@pytest.fixture(scope="module")
def anm() -> AnmFile:
    return AnmFile.parse(open_archive(DAT).load("title01.anm"))


@NEEDS_DAT
def test_entry_chain(anm: AnmFile) -> None:
    # title01.anm 共 10 个 entry(nextOffset 链), 主纹理在 entry1
    assert len(anm.entries) == 10
    names = [e.name for e in anm.entries]
    assert names[0] == "data/title/title02.png"
    assert names[1] == "data/title/title01.png"


@NEEDS_DAT
def test_main_entry_sprites(anm: AnmFile) -> None:
    e = anm.entries[1]
    assert (e.tex_width, e.tex_height) == (512, 512)
    assert len(e.sprites) == 94
    # 主菜单 8 项的选中/未选中贴图(偶=选中, 奇=未选中)
    for sid in range(16):
        assert sid in e.sprites
    # Start 选中态: rect (0,0,80,32)
    s = e.sprites[0]
    assert (s.x, s.y, s.w, s.h) == (0, 0, 80, 32)


@NEEDS_DAT
def test_sprite_image_decode(anm: AnmFile) -> None:
    # logo(entry0 唯一 sprite): 全图 512x256
    w, h, rgba = anm.sprite_image(0, entry=0)
    assert (w, h) == (512, 256)
    assert len(rgba) == w * h * 4
    alphas = rgba[3::4]
    assert max(alphas) > 200  # logo 主体不透明
    assert min(alphas) == 0  # 边角全透明
    # "Start" 菜单贴图非全透明
    w, h, rgba = anm.sprite_image(0, entry=1)
    assert (w, h) == (80, 32)
    assert max(rgba[3::4]) > 200
    # 白色文字: 取亮色像素(排除不透明描边), RGB 都应接近白(验证 BGRA 没翻)
    opaque = [i for i in range(w * h) if rgba[4 * i + 3] > 200]
    best = max(opaque, key=lambda i: rgba[4 * i] + rgba[4 * i + 1] + rgba[4 * i + 2])
    r, g, b = rgba[4 * best : 4 * best + 3]
    assert r > 200 and g > 200 and b > 200


@NEEDS_DAT
def test_argb8888_entry(anm: AnmFile) -> None:
    # entry3 (sl_pl00.png) 是 format 1 (A8R8G8B8)
    e = anm.entries[3]
    assert e.format == 1
    w, h, rgba = anm.sprite_image(0, entry=3)
    assert (w, h) == (256, 480)
    assert max(rgba[3::4]) > 200


# ---- v3 泛化(schema 机制层, 合成数据; th08 的注册装配与真实数据测试见
#      games/th08/anm.py 与 tests/game_test/th08/test_th08_anm.py) ----

import struct  # noqa: E402


class _AnmFileV3(AnmFile):
    """version 校验参数化的测试替身(th08 = 3)。"""

    _EXPECTED_VERSION = 3


def _synth_entry(
    *,
    version: int = 3,
    name: bytes = b"@",
    width: int = 8,
    height: int = 4,
    fmt: int = 5,
    color_key: int = 0,
    has_data: int = 0,
    sprites: tuple[tuple[int, float, float, float, float], ...] = (),
) -> bytes:
    """合成单 entry .anm 最小帧: 64 字节头 + sprite 偏移表 + 名串 + sprite 记录。"""
    ns = len(sprites)
    name_off = 64 + ns * 4
    rec_base = name_off + len(name) + 1
    hdr = struct.pack(
        "<13i", ns, 0, 0, width, height, fmt, color_key, name_off, 0, 0, version, 0, 0
    )
    hdr += bytes([has_data]) + b"\0\0\0" + struct.pack("<i", 0) + b"\0\0\0\0"
    assert len(hdr) == 64
    table = b"".join(struct.pack("<i", rec_base + i * 20) for i in range(ns))
    recs = b"".join(struct.pack("<iffff", *s) for s in sprites)
    return hdr + table + name + b"\0" + recs


def test_v3_version_check_parameterized() -> None:
    """version 校验随类属性走: v3 类吃 v3 帧, v2 类拒 v3 帧(反之亦然)。"""
    data = _synth_entry(version=3)
    assert len(_AnmFileV3.parse(data).entries) == 1
    with pytest.raises(ValueError, match="版本不符"):
        AnmFile.parse(data)
    with pytest.raises(ValueError, match="版本不符"):
        _AnmFileV3.parse(_synth_entry(version=2))


def test_v3_at_empty_texture() -> None:
    """hasData=0 + '@' 名 → 按 entry 头宽高建透明 RGBA 空纹理(CreateEmptyTexture)。"""
    e = _AnmFileV3.parse(_synth_entry(name=b"@", width=8, height=4)).entries[0]
    assert (e.tex_width, e.tex_height) == (8, 4)
    assert len(e.rgba) == 8 * 4 * 4 and not any(e.rgba)  # 全透明


def test_v3_external_texture_loader() -> None:
    """hasData=0 + 外部名: 无 loader 保持 raise; 注入 loader 后走 loader 结果。"""
    data = _synth_entry(name=b"foo.png", sprites=((7, 1.0, 2.0, 4.0, 2.0),))
    with pytest.raises(ValueError, match="外链纹理"):
        _AnmFileV3.parse(data)
    seen = []

    def loader(name: str, color_key: int, fmt: int) -> tuple[int, int, bytes]:
        seen.append((name, color_key, fmt))
        return 16, 8, bytes(16 * 8 * 4)

    e = _AnmFileV3.parse(data, texture_loader=loader).entries[0]
    assert seen == [("foo.png", 0, 5)]  # (名, colorKey, format) 透传
    assert (e.tex_width, e.tex_height) == (16, 8)
    # sprite 逻辑坐标 × (16/8, 8/4) 缩放到纹理像素
    s = e.sprites[7]
    assert (s.x, s.y, s.w, s.h) == (2, 4, 8, 4)
    assert (s.fx, s.fy, s.fw, s.fh) == (2.0, 4.0, 8.0, 4.0)


def test_f32_sprite_precision() -> None:
    """f32 视图保留半像素精度, int 字段照旧 round; sprite_image 裁切走 int。"""
    data = _synth_entry(
        name=b"@", width=16, height=16, sprites=((1, 3.25, 0.5, 5.75, 2.0),)
    )
    s = _AnmFileV3.parse(data).entries[0].sprites[1]
    assert (s.fx, s.fy, s.fw, s.fh) == (3.25, 0.5, 5.75, 2.0)
    assert (s.x, s.y, s.w, s.h) == (3, 0, 6, 2)
    w, h, rgba = _AnmFileV3.parse(data).sprite_image(1, entry=0)
    assert (w, h) == (6, 2) and len(rgba) == 6 * 2 * 4


def test_parse_cached_loader_key_isolation() -> None:
    """带 loader 的解析按 (bytes, 类, cache_tag) 分键, 不与裸 bytes 键互串。"""
    data = _synth_entry(name=b"foo.png")
    calls = []

    def loader(name: str, color_key: int, fmt: int) -> tuple[int, int, bytes]:
        calls.append(name)
        return 1, 1, bytes(4)

    a = _AnmFileV3.parse_cached(data, texture_loader=loader, cache_tag="t1")
    b = _AnmFileV3.parse_cached(data, texture_loader=loader, cache_tag="t1")
    assert a is b and calls == ["foo.png"]  # 同键命中缓存, loader 只调一次
    c = _AnmFileV3.parse_cached(data, texture_loader=loader, cache_tag="t2")
    assert c is not a and calls == ["foo.png", "foo.png"]  # 换 tag 不串键
