"""Pbgz 容器(touhou/schema/archive/pbgz.py)测试 —— 合成迷你 PBGZ 包。

分层守护: tests 根不 import games.*, 本文件只碰 schema/registry 层。
真实 th08.dat 的认头/解压验证见 game_test/th08/test_th08_archive.py。

fixture 构造:
- decrypt(Global.cpp:821-879)不是对合 —— 块内置换 σ 连自身复合都不是恒等
  (chunk=12 时 σ(1)=9 而 σ(9)=4), 异或密钥流又按输入序推进, 故不能拿
  decrypt 自造密文; 本文件实现独立的逆运算 _encrypt(按同一参数把明文字节
  放回密文位置)构造 fixture, 往返一致性由 decrypt(_encrypt(x)) == x 钉住。
- LZSS 侧没有编码器, 用全字面量流(每字节 9bit = flag 1 | 字节, 末尾
  flag=0 + offset 0 的 EOD token)绕过 —— 解压器认这个合法子集。
"""

from __future__ import annotations

import struct

import pytest

from touhou.exceptions import ArchiveFormatError
from touhou.schema.archive import open_archive
from touhou.schema.archive.pbgz import MAGIC, PbgzArchive, decrypt

# 与 pbgz.py 内部一致的容器参数(合成 fixture 需要显式给出)
_HEADER_PARAMS = (0x1B, 0x37, 12, 0x400)
_TABLE_PARAMS = (0x3E, 0x9B, 0x80, 0x400)
_HEADER_BIAS = (123456, 345678, 567891)


def _encrypt(data: bytes, key: int, inc: int, chunk: int, max_bytes: int) -> bytes:
    """pbgz.decrypt 的逆运算(仅测试构造密文用)。

    decrypt = 输入序 XOR 密钥流 + 块内定位置换, 逐位可逆; 本函数按同一
    参数推导置换像(与 decrypt 相同的游标走法), 把明文字节放回密文位置。
    """
    size = len(data)
    num_unencrypted = (size % chunk) if size % chunk < chunk // 4 else 0
    num_unencrypted += size & 1
    remaining = size - num_unencrypted
    out = bytearray(size)
    in_pos = 0  # 密文游标(= decrypt 的输入)
    out_pos = 0  # 明文游标(= decrypt 的输出)
    budget = max_bytes
    while remaining > 0 and budget > 0:
        c = min(chunk, remaining)
        p = out_pos + c - 1
        for _ in range((c + 1) // 2):
            out[in_pos] = data[p] ^ key
            key = (key + inc) & 0xFF
            p -= 2
            in_pos += 1
        p = out_pos + c - 2
        for _ in range(c // 2):
            out[in_pos] = data[p] ^ key
            key = (key + inc) & 0xFF
            p -= 2
            in_pos += 1
        remaining -= c
        out_pos += c
        budget -= c
    # 尾部(余数 + 超出 max_bytes 的部分)原样, 与 decrypt 的拷贝规则对称
    rest = remaining + num_unencrypted
    if rest > 0:
        out[in_pos : in_pos + rest] = data[out_pos : out_pos + rest]
    return bytes(out)


def _lzss_encode_literals(data: bytes) -> bytes:
    """全字面量 LZSS 流(13/4 参数): 每字节 9bit(1|b), 末尾 EOD(flag=0, offset=0)。"""
    out = bytearray()
    acc = 0
    nbits = 0

    def push(value: int, n: int) -> None:
        nonlocal acc, nbits
        acc = (acc << n) | value
        nbits += n
        while nbits >= 8:
            nbits -= 8
            out.append((acc >> nbits) & 0xFF)

    for b in data:
        push(0x100 | b, 9)
    push(0, 9)  # flag=0, offset 高 8 位 = 0
    push(0, 9)  # offset 余 5 位 + 长度 4 位 = 0 → offset==0 即 EOD
    if nbits:
        out.append((acc << (8 - nbits)) & 0xFF)
    return bytes(out)


def _build_pbgz(items: dict[str, bytes]) -> bytes:
    """合成一个迷你 PBGZ 包: 头 + 连续条目数据区 + 加密压缩文件表。"""
    comp = [_lzss_encode_literals(payload) for payload in items.values()]
    offsets = []
    pos = 16  # MAGIC + 12 字节加密头
    for c in comp:
        offsets.append(pos)
        pos += len(c)
    table_offset = pos
    table = bytearray()
    for (name, payload), offset in zip(items.items(), offsets):
        table += name.encode("latin-1") + b"\x00"
        table += struct.pack("<III", offset, len(payload), 0)
    table_blob = _encrypt(_lzss_encode_literals(bytes(table)), *_TABLE_PARAMS)
    header = struct.pack(
        "<iii",
        len(items) + _HEADER_BIAS[0],
        table_offset + _HEADER_BIAS[1],
        len(table) + _HEADER_BIAS[2],
    )
    return MAGIC + _encrypt(header, *_HEADER_PARAMS) + b"".join(comp) + table_blob


_ITEMS = {
    "alpha.txt": b"hello pbgz world",
    "beta.bin": bytes(range(64)),
    "ecldata1.ecl": b"\x00" * 7 + b"fake-ecl",
}


# ---- decrypt 算法本体 ----
@pytest.mark.parametrize(
    "size", [0, 1, 2, 3, 7, 12, 13, 31, 32, 33, 100, 255, 256, 1024, 1500]
)
def test_decrypt_inverts_encrypt(size: int) -> None:
    """往返: decrypt(_encrypt(x)) == x, 覆盖奇偶/余数规则/超 max_bytes 各分支。"""
    plain = bytes((i * 37 + 11) & 0xFF for i in range(size))
    for params in (_HEADER_PARAMS, _TABLE_PARAMS):
        assert decrypt(_encrypt(plain, *params), *params) == plain


def test_decrypt_not_involution() -> None:
    """decrypt 不是对合: 自造密文必须走 _encrypt 而不是再 decrypt 一次。"""
    plain = bytes(range(24))
    once = decrypt(plain, *_HEADER_PARAMS)
    assert decrypt(once, *_HEADER_PARAMS) != plain


def test_decrypt_respects_max_bytes() -> None:
    """超过 maxBytes 的部分原样不动(Global.cpp:841,872-876)。"""
    plain = bytes(range(64))
    out = decrypt(plain, 0x1B, 0x37, 12, 24)
    assert out[24:] == plain[24:]  # 预算只够两块, 其后原样


# ---- 容器解析 ----
def test_sniff() -> None:
    assert PbgzArchive.sniff(MAGIC + b"\x00" * 8)
    assert not PbgzArchive.sniff(b"PBG4" + b"\x00" * 8)
    assert not PbgzArchive.sniff(b"")


def test_from_bytes_entries_and_roundtrip() -> None:
    """合成包: 条目清单/解压内容往返一致。"""
    arc = PbgzArchive.from_bytes(_build_pbgz(_ITEMS))
    assert sorted(arc.names()) == sorted(_ITEMS)
    assert len(arc) == len(_ITEMS)
    for name, payload in _ITEMS.items():
        assert arc.load(name) == payload
        entry = next(e for e in arc.entries() if e.name == name)
        assert entry.size == len(payload)  # ArchiveEntry.size = 解压后大小
        assert len(arc.raw(name)) == len(_lzss_encode_literals(payload))


def test_from_bytes_wrong_magic_raises() -> None:
    with pytest.raises(ArchiveFormatError, match="不是 Pbgz 容器"):
        PbgzArchive.from_bytes(b"PBG4" + b"\x00" * 32)


def test_open_archive_sniffs_pbgz(tmp_path) -> None:
    """认头: open_archive 不传 game/format 也能认出 PBGZ。"""
    p = tmp_path / "mini.dat"
    p.write_bytes(_build_pbgz(_ITEMS))
    arc = open_archive(p)
    assert isinstance(arc, PbgzArchive)
    assert arc.format_name == "pbgz"
    assert arc.path == p
    assert arc.load("alpha.txt") == _ITEMS["alpha.txt"]
