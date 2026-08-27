"""EclCodec(engine/ecl_codec.py)统一 enc/dec 入口 + EclFile.serialize round-trip。"""
from __future__ import annotations

import struct

import pytest

import touhou  # noqa: F401  # import 即完成 th07 注册
from touhou.engine.ecl import EclFile
from touhou.engine.ecl_codec import EclCodec
from touhou.paths import DEFAULT_DATA
from touhou.registry import register_anm, register_ecl
from touhou.schema.anm import AnmFile
from touhou.schema.archive import GameArchive

needs_data = pytest.mark.skipif(not DEFAULT_DATA.exists(),
                                reason="需要真实 th07.dat")


def _instr(time: int, op: int, args: tuple = (), mask: int = 0,
           skip: int = 0xFF, unused: int = 0) -> bytes:
    size = 12 + 4 * len(args)
    return struct.pack("<IhhBBH", time, op, size, unused, skip, mask) + b"".join(
        struct.pack("<I", a & 0xFFFFFFFF) for a in args)


def _build_ecl(*subs: list[bytes]) -> bytes:
    """把若干 sub(每条一串指令字节)拼成合法 .ecl 字节流(无时间轴)。"""
    n = len(subs)
    header_size = 4 + 64 + 4 * n
    offsets, blobs = [], []
    off = header_size
    for s in subs:
        offsets.append(off)
        blob = b"".join(s) + _instr(0xFFFFFFFF, -1)
        blobs.append(blob)
        off += len(blob)
    header = struct.pack("<hh", n, 0) + struct.pack("<16i", *([0] * 16)) \
        + struct.pack(f"<{n}i", *offsets)
    return header + b"".join(blobs)


# ---- EclFile.serialize round-trip ----

def test_serialize_roundtrip_synthetic() -> None:
    """手工构造的 .ecl(含非零 unused 字节): serialize(parse(data)) == data。"""
    data = _build_ecl(
        [_instr(0, 4, args=(0x12345678,), unused=7), _instr(10, 2, args=(0, 24))],
        [_instr(5, 46, args=(1, 2, 3), mask=0b101, skip=0x0F, unused=255)],
    )
    ecl = EclFile.parse(data)
    assert ecl.serialize() == data
    assert ecl.subs[0][0].unused == 7 and ecl.subs[1][0].unused == 255


@needs_data
def test_serialize_roundtrip_real_ecldata() -> None:
    """真实 th07.dat 的全部 ecldata*.ecl: 逐字节相等。"""
    arc = GameArchive.open(DEFAULT_DATA)
    names = sorted(n for n in arc.names()
                   if n.startswith("ecldata") and n.endswith(".ecl"))
    assert len(names) >= 8  # ecldata1..8(本篇 6 面 + Extra/Phantasm)
    for name in names:
        data = arc.load(name)
        assert EclFile.parse(data).serialize() == data, name


# ---- EclCodec 入口 ----

def test_codec_default_game_is_th07() -> None:
    """默认作品名 th07; 经注册表拿到的格式类就是 engine 的 EclFile。"""
    codec = EclCodec()
    assert codec.game == "th07"
    assert codec._ecl_spec.file_format is EclFile
    assert codec._ecl_spec.machine is not None


def test_codec_decode_encode_synthetic() -> None:
    """decode/encode 委托注册表解析出的格式类(数据形态 = EclFile)。"""
    codec = EclCodec("th07")
    data = _build_ecl([_instr(0, 4, args=(42,))])
    ecl = codec.decode(data)
    assert isinstance(ecl, EclFile)
    assert ecl.subs[0][0].args == (42,)
    assert codec.encode(ecl) == data


@needs_data
def test_codec_roundtrip_real_ecldata() -> None:
    """经 EclCodec 对真实 ecldata 做 decode→encode, 逐字节相等。"""
    codec = EclCodec()
    arc = GameArchive.open(DEFAULT_DATA)
    for n in range(1, 9):
        data = arc.load(f"ecldata{n}.ecl")
        assert codec.encode(codec.decode(data)) == data


def test_codec_unknown_game_keyerror() -> None:
    """未注册作品: NotRegisteredError(KeyError 子类), 信息含作品名与已注册列表。"""
    with pytest.raises(KeyError, match="未注册的作品.*th00") as ei:
        EclCodec("th00")
    assert "th07" in str(ei.value)


def test_codec_registered_but_no_ecl_dimension() -> None:
    """只注册了其他维度的作品: 构造期报'缺 ECL 维度'的清晰 ValueError。"""
    register_anm("th92", version=2)(AnmFile)
    with pytest.raises(ValueError, match="缺 ECL 维度.*register_ecl.*th92"):
        EclCodec("th92")


def test_codec_encode_without_serialize() -> None:
    """格式类缺 serialize: decode 可用, encode 报带作品名/类名的 NotImplementedError。"""

    class StubEclFile:
        @classmethod
        def parse(cls, data: bytes) -> "StubEclFile":
            return cls()

    class StubMachine:
        pass

    register_ecl("th91", file_format=StubEclFile)(StubMachine)
    codec = EclCodec("th91")
    ecl = codec.decode(b"\x00" * 4)
    assert isinstance(ecl, StubEclFile)
    with pytest.raises(NotImplementedError,
                       match="th91.*StubEclFile.*serialize"):
        codec.encode(ecl)  # type: ignore[arg-type]
