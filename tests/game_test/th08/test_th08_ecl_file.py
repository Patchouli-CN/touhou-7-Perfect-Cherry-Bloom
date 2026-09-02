"""EclFileTh08(games/th08/ecl_file.py)的 parse/serialize 测试。

手工构造 th08 布局(version 0x800 头 + 16 槽 timelineOffsets @0x8 +
u32 subOffsets @0x48, EclManager.hpp:181-190)钉字段映射与 round-trip;
@needs_data 用真实 th08.dat 的 ecldata*.ecl 钉 opcode 区间与逐字节还原。
"""

from __future__ import annotations

import struct

import pytest

import touhou  # noqa: F401  # import 即完成 th08 ECL 维度注册
from touhou.engine.ecl import EclInstr
from touhou.engine.ecl_codec import EclCodec
from touhou.exceptions import EclParseError
from touhou.games.th08.crypt import try_decrypt_from_table
from touhou.games.th08.ecl_file import EclFileTh08, EclTimelineInstrTh08
from touhou.paths import DEFAULT_DATA_PATHS
from touhou.schema.archive import open_archive

from .conftest import needs_data

TH08_DAT = DEFAULT_DATA_PATHS["th08"]


# ---- 手工构造 th08 .ecl 二进制 ----


def _instr(
    time: int,
    op: int,
    args: tuple = (),
    mask: int = 0,
    skip: int = 0xFF,
    unused: int = 0,
) -> bytes:
    """12 字节指令头(与 th07 逐字段同构, EclManager.hpp:147-156)。"""
    size = 12 + 4 * len(args)
    return struct.pack("<IhhBBH", time, op, size, unused, skip, mask) + b"".join(
        struct.pack("<I", a & 0xFFFFFFFF) for a in args
    )


def _tl_instr(
    time: int,
    op: int,
    args: tuple = (0,) * 7,
    diff: int = 0xFF,
) -> bytes:
    """时间轴指令: 8 字节头 + args(全 raw, EnemyManager.hpp:419-430)。"""
    size = 8 + 4 * len(args)
    return struct.pack("<ihBB", time, op, size, diff) + b"".join(
        struct.pack("<I", a & 0xFFFFFFFF) for a in args
    )


def _build_ecl(subs: list[list[bytes]], timelines: list[list[bytes]] = ()) -> bytes:
    """th08 布局: 0x800 头 + 16 槽偏移表 + sub/时间轴各自收尾。"""
    n = len(subs)
    header_size = 0x48 + 4 * n
    offsets, blobs = [], []
    off = header_size
    for s in subs:
        offsets.append(off)
        blob = b"".join(s) + _instr(0xFFFFFFFF, -1)
        blobs.append(blob)
        off += len(blob)
    tl_offsets = [0] * 16
    for i, t in enumerate(timelines):
        tl_offsets[i] = off
        blob = b"".join(t) + _tl_instr(-1, 0)  # time<0 终止
        blobs.append(blob)
        off += len(blob)
    header = (
        struct.pack("<Ihh", 0x800, n, len(timelines))
        + struct.pack("<16I", *tl_offsets)
        + struct.pack(f"<{n}I", *offsets)
    )
    return header + b"".join(blobs)


# ---- parse 字段映射 ----


def test_parse_synthetic_fields() -> None:
    """手工 th08 .ecl: sub/时间轴/指令字段映射全部正确。"""
    data = _build_ecl(
        [
            [_instr(0, 4, args=(0, 24))],
            [_instr(5, 6, args=(1, 2, 3), mask=0b101, skip=0x0F, unused=255)],
        ],
        timelines=[
            [_tl_instr(0, 0, args=(7, 1, 2, 3, 4, 5, 6), diff=0x01)],
        ],
    )
    ecl = EclFileTh08.parse(data)
    assert ecl.sub_count == 2 and ecl.timeline_count == 1

    ins = ecl.subs[0][0]
    assert isinstance(ins, EclInstr)
    assert (ins.time, ins.id, ins.size) == (0, 4, 20)
    assert ins.args == (0, 24)
    ins2 = ecl.subs[1][0]
    # nextOffset→size / reserved→unused / difficultyMask→skip_difficulty /
    # operandFlags→param_mask 的映射(指令头逐字段同构)
    assert (ins2.time, ins2.id, ins2.size) == (5, 6, 24)
    assert ins2.unused == 255 and ins2.skip_difficulty == 0x0F
    assert ins2.param_mask == 0b101 and ins2.args == (1, 2, 3)
    assert ecl.subs[0][-1].is_terminator
    # offset/sub_offset/instr_at 语义与 th07 一致(文件内绝对偏移)
    base = 0x48 + 4 * 2
    assert ecl.sub_offset(0) == base and ecl.sub_offset(1) == base + 32
    assert ecl.instr_at(base + 32) is ins2

    tl = ecl.timelines[0]
    assert len(tl) == 2  # 数据指令 + time<0 终止
    tins = tl[0]
    assert isinstance(tins, EclTimelineInstrTh08)
    assert (tins.time, tins.opcode, tins.size, tins.difficulty_mask) == (0, 0, 36, 0x01)
    assert tins.args == (7, 1, 2, 3, 4, 5, 6)
    assert tins.arg_int(0) == 7
    assert tl[-1].time < 0
    assert ecl._timeline_offsets[0] == tins.offset
    assert ecl._timeline_offsets[1:] == (0,) * 15  # 空槽原值保留


def test_serialize_roundtrip_synthetic() -> None:
    """serialize(parse(data)) == data(含非零保留字段/多时间轴槽)。"""
    data = _build_ecl(
        [
            [_instr(0, 4, args=(0x12345678,), unused=7)],
            [_instr(10, 2, args=(0, 24), skip=0x03)],
        ],
        timelines=[
            [_tl_instr(0, 1, args=(9, 8, 7, 6, 5, 4, 3))],
            [_tl_instr(60, 8, args=(0,) * 7, diff=0x0F)],
        ],
    )
    assert EclFileTh08.parse(data).serialize() == data


def test_parse_bad_version_rejected() -> None:
    """version != 0x800 拒绝(EclManager.cpp:38 的硬性校验)。"""
    data = bytearray(_build_ecl([[_instr(0, 1)]]))
    struct.pack_into("<I", data, 0, 0x700)
    with pytest.raises(EclParseError, match="0x800"):
        EclFileTh08.parse(bytes(data))


def test_parse_too_small_rejected() -> None:
    with pytest.raises(EclParseError, match="太小"):
        EclFileTh08.parse(b"\x00" * 0x20)


def test_truncated_zero_size_terminator() -> None:
    """size=0 的截短终止记录(真实 ecldata8.ecl tl1 形态): parse/serialize 还原。"""
    n = 1
    header_size = 0x48 + 4 * n
    sub_off = header_size
    sub = _instr(0, 1) + _instr(0xFFFFFFFF, -1)
    tl_off = sub_off + len(sub)
    timeline = _tl_instr(10, 0) + struct.pack("<ihBB", -1, 0, 0, 0)  # size=0 终止
    data = (
        struct.pack("<Ihh", 0x800, n, 1)
        + struct.pack("<16I", tl_off, *([0] * 15))
        + struct.pack("<I", sub_off)
        + sub
        + timeline
    )
    ecl = EclFileTh08.parse(data)
    term = ecl.timelines[0][-1]
    assert term.time < 0 and term.size == 0 and term.args == ()
    assert ecl.serialize() == data


# ---- 真实 th08.dat ----


@needs_data
def test_real_ecldata_roundtrip_and_histogram() -> None:
    """真实 ecldata*.ecl: EclCodec decode→encode 逐字节相等, opcode 全在 1-184。

    ecl 条目带 "edz" 内层加密(TryDecryptFromTable, Global.cpp:901-927),
    先经 games/th08/crypt.try_decrypt_from_table 解密再进格式层。
    """
    arc = open_archive(TH08_DAT, game="th08")
    codec = EclCodec("th08")
    names = sorted(
        n for n in arc.names() if n.startswith("ecldata") and n.endswith(".ecl")
    )
    assert names, "th08.dat 应含 ecldata*.ecl 条目"
    for name in names:
        data = try_decrypt_from_table(arc.load(name))
        ecl = codec.decode(data)
        assert isinstance(ecl, EclFileTh08)
        assert codec.encode(ecl) == data, name

    ecl1 = EclFileTh08.parse(try_decrypt_from_table(arc.load("ecldata1.ecl")))
    assert ecl1.sub_count > 0 and ecl1.timeline_count > 0
    hist = ecl1.opcode_histogram()
    assert hist
    # 真实数据含 opcode 0(编译器生成的时间同步 nop, 同 th07 的 case 0 现象),
    # 故合法区间是 0-184 而非 1-184
    assert all(0 <= op <= 184 for op in hist), hist
