"""th08 ANM v3 测试 —— 真实 th08.dat 核对(缺失自动 skip, 见 conftest.needs_data)。

格式事实(出处详见 scratch_dbg/investigation/th08-ref-facts.md §3):
entry 头 0x40 链式与 th07 同构, 唯一硬性差异是 version==3 校验
(th08-ref AnmManager.cpp:2457); 纹理三来源(:2558-2586) = 内嵌 THTX /
``@`` 空纹理 / 外链纹理(封包取文件 + TryDecryptFromTable 内层解密 +
colorKey 抠色)。实测本机 th08.dat 全 113 个 .anm 的 hasData=0 条目只有
4 条且全是 ``@``(capture/music00/resulttext/text.anm), 外链纹理用
合成 entry + 封包内真实 edz 加密 PNG(title00.png)端到端验证。
"""

from __future__ import annotations

import io
import struct

import pygame
import pytest

from touhou.games.th08.anm import Th08AnmFile
from touhou.games.th08.crypt import try_decrypt_from_table
from touhou.paths import DEFAULT_DATA_PATHS
from touhou.schema.archive import ArchiveBase, open_archive

from .conftest import needs_data

TH08_DAT = DEFAULT_DATA_PATHS["th08"]


@pytest.fixture(scope="module")
def arc() -> ArchiveBase:
    return open_archive(TH08_DAT)


def _load_anm(arc: ArchiveBase, name: str) -> bytes:
    """封包条目 + edz 内层解密(条目级加密是解析层的输入前提)。"""
    return try_decrypt_from_table(arc.load(name))


def _synth_external_entry(name: bytes, width: int = 64, height: int = 64) -> bytes:
    """合成 v3 单 entry(hasData=0 + 外部纹理名), 指向封包内真实文件。"""
    name_off = 64
    hdr = struct.pack("<13i", 0, 0, 0, width, height, 1, 0, name_off, 0, 0, 3, 0, 0)
    hdr += bytes([0]) + b"\0\0\0" + struct.pack("<i", 0) + b"\0\0\0\0"
    assert len(hdr) == 64
    return hdr + name + b"\0"


@needs_data
def test_v3_entry_chain(arc: ArchiveBase) -> None:
    """v3 entry 链解析: result00.anm 四 entry(结果画面四张内嵌图)。"""
    anm = Th08AnmFile.parse(_load_anm(arc, "result00.anm"))
    assert len(anm.entries) == 4
    assert [e.name for e in anm.entries] == [
        f"data/result/result0{i}.png" for i in range(4)
    ]
    for e in anm.entries:
        assert len(e.rgba) == e.tex_width * e.tex_height * 4
        assert e.sprites
    # 单 entry 的 enemy.anm: 157 sprites
    enemy = Th08AnmFile.parse(_load_anm(arc, "enemy.anm"))
    assert len(enemy.entries) == 1 and len(enemy.entries[0].sprites) == 157


@needs_data
def test_embedded_thtx_texture(arc: ArchiveBase) -> None:
    """纹理来源一: hasData=1 内嵌 THTX 解码出图。"""
    anm = Th08AnmFile.parse(_load_anm(arc, "enemy.anm"))
    e = anm.entries[0]
    assert (e.tex_width, e.tex_height) == (512, 512)
    assert any(e.rgba)
    w, h, rgba = anm.sprite_image(next(iter(e.sprites)), entry=0)
    assert len(rgba) == w * h * 4


@needs_data
def test_at_empty_texture(arc: ArchiveBase) -> None:
    """纹理来源二: hasData=0 + '@' 名 → 按 entry 头宽高的全透明空纹理。"""
    anm = Th08AnmFile.parse(_load_anm(arc, "text.anm"))  # 无需 loader
    e = anm.entries[0]
    assert e.name == "@" and (e.tex_width, e.tex_height) == (512, 256)
    assert not any(e.rgba)  # 全透明
    assert len(e.sprites) == 36


@needs_data
def test_external_texture_decrypts_real_png(arc: ArchiveBase) -> None:
    """纹理来源三: 外链纹理 —— 合成 entry 指向 title00.png(edz 加密的真实
    PNG), make_texture_loader 真解密出图(CreateTextureFromFile 链路)。"""
    # title00.png 在封包内带 edz 内层加密, 解密后是 PNG magic
    assert try_decrypt_from_table(arc.load("title00.png"))[:4] == b"\x89PNG"
    loader = Th08AnmFile.make_texture_loader(arc)
    anm = Th08AnmFile.parse(
        _synth_external_entry(b"title00.png"), texture_loader=loader
    )
    e = anm.entries[0]
    assert (e.tex_width, e.tex_height) == (640, 480)
    assert len(e.rgba) == 640 * 480 * 4 and any(e.rgba)


@needs_data
def test_f32_sprite_view_consistent(arc: ArchiveBase) -> None:
    """f32 sprite 视图: 真实 th08 精灵坐标均为整数值(实测 1917/1917),
    float 视图与 int 字段一致(半像素精度由 schema 合成测试钉住)。"""
    anm = Th08AnmFile.parse(_load_anm(arc, "enemy.anm"))
    for e in anm.entries:
        for s in e.sprites.values():
            assert abs(s.fx - s.x) <= 0.5 and abs(s.fy - s.y) <= 0.5
            assert abs(s.fw - s.w) <= 0.5 and abs(s.fh - s.h) <= 0.5


@needs_data
def test_parse_scripts_v3(arc: ArchiveBase) -> None:
    """脚本指令布局 v2/v3 同构 → parse_scripts 对 v3 零改动可用。"""
    per_entry = Th08AnmFile.parse_scripts(_load_anm(arc, "text.anm"))
    assert len(per_entry) == 1 and len(per_entry[0]) == 34
    assert all(instrs for instrs in per_entry[0].values())


def test_colorkey_loader() -> None:
    """colorKey 抠色(无需真实数据): 假封包 + 内存 PNG, 命中色变透明黑,
    colorKey=0 不抠色(D3DX 语义, AnmManager.cpp:2225-2235)。"""

    class _FakeArchive:
        def __init__(self, files: dict[str, bytes]) -> None:
            self._files = files

        def load(self, name: str) -> bytes:
            return self._files[name]

    surf = pygame.Surface((2, 2))
    surf.fill((255, 0, 255), (0, 0, 1, 1))
    surf.fill((10, 20, 30), (1, 0, 1, 1))
    surf.fill((255, 0, 255), (0, 1, 1, 1))
    surf.fill((1, 2, 3), (1, 1, 1, 1))
    buf = io.BytesIO()
    pygame.image.save(surf, buf, "PNG")
    arc = _FakeArchive({"key.png": buf.getvalue()})

    loader = Th08AnmFile.make_texture_loader(arc)  # type: ignore[arg-type]
    w, h, rgba = loader("key.png", 0xFFFF00FF, 1)
    assert (w, h) == (2, 2)
    px = [tuple(rgba[i * 4 : i * 4 + 4]) for i in range(4)]
    assert px[0] == (0, 0, 0, 0) and px[2] == (0, 0, 0, 0)  # 命中 colorKey
    assert px[1] == (10, 20, 30, 255) and px[3] == (1, 2, 3, 255)

    # colorKey=0 → 不抠色
    _, _, rgba0 = loader("key.png", 0, 1)
    assert tuple(rgba0[0:4]) == (255, 0, 255, 255)
    # 未命中签名(edz)的条目原样透传(try_decrypt_from_table passthrough),
    # 上面两次成功解码即证明; 缺文件报错
    with pytest.raises(ValueError, match="外链纹理"):
        loader("missing.png", 0, 1)
