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
