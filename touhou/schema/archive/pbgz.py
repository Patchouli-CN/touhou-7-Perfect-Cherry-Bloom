"""Pbgz 容器 —— th08《东方永夜抄》的资源包格式(与 th07 Pbg4 的差异见下)。

布局(PbgArchive.cpp:138-239): 头 ``"PBGZ" + 12 字节加密头``, 加密头先整体
``decrypt(key=0x1b, inc=0x37, chunk=12, max=0x400)`` 再分别减 123456/345678/567891,
得 条目数/文件表偏移/文件表解压后大小(PbgArchive.cpp:177,181-183); 文件表从
fileTableOffset 到文件尾, 先 ``decrypt(0x3e, 0x9b, 0x80, 0x400)`` 再 LZSS 解压
(PbgArchive.cpp:209), 解压后是变长记录流 ``名字\\0 + u32 dataOffset + u32
decompressedSize + u32 _(不读)``(PbgArchive.cpp:242-269)。
条目数据紧接文件表之前连续存放, compressedSize = next.dataOffset - cur.dataOffset,
哨兵条目 dataOffset = fileTableOffset(PbgArchive.cpp:85,267)。
条目内容仅 LZSS 解压, 无条目级加密(th08 的内层签名探测 TryDecryptFromTable,
Global.cpp:901-927, 是 anm/std 解析层的事, 本层不做)。
LZSS 参数与 pbg4 同参(13/4, Lzss.cpp:301-333)。
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import ClassVar, Self

from ...exceptions import ArchiveFormatError
from ...registry import register_archive
from .base import ArchiveBase, ArchiveEntry
from .lzss import LzssDecompressor

MAGIC = b"PBGZ"
DICT_BITS = 13
LEN_BITS = 4

# 加密头/文件表的 decrypt 参数(PbgArchive.cpp:177 / :209)
_HEADER_KEY, _HEADER_INC, _HEADER_CHUNK, _HEADER_MAX = 0x1B, 0x37, 12, 0x400
_TABLE_KEY, _TABLE_INC, _TABLE_CHUNK, _TABLE_MAX = 0x3E, 0x9B, 0x80, 0x400
# 加密头三字段的解偏码常量(PbgArchive.cpp:181-183)
_HEADER_BIAS = (123456, 345678, 567891)


def decrypt(data: bytes, key: int, inc: int, chunk: int, max_bytes: int) -> bytes:
    """FileSystem::Decrypt 的逐行移植(Global.cpp:821-879)。

    按 chunk 分块, 块内逆序写回(字节对调): 输入字节按序 ^= key(每字节 key += inc,
    u8 回绕), 前 (chunk+1)/2 个写到块尾起隔位向前, 后 chunk/2 个写到块尾-1 起隔位
    向前。尾部余数规则: ``numUnencrypted = (size%chunk < chunk/4) ? size%chunk : 0``
    再加 ``size & 1``; 余数与超过 maxBytes 的部分原样拷贝。
    """
    size = len(data)
    num_unencrypted = (size % chunk) if size % chunk < chunk // 4 else 0
    num_unencrypted += size & 1
    size -= num_unencrypted
    out = bytearray(len(data))
    in_pos = 0
    out_pos = 0
    while size > 0 and max_bytes > 0:
        if size < chunk:
            chunk = size
        p = out_pos + chunk - 1
        for _ in range((chunk + 1) // 2):
            out[p] = data[in_pos] ^ key
            key = (key + inc) & 0xFF
            p -= 2
            in_pos += 1
        p = out_pos + chunk - 2
        for _ in range(chunk // 2):
            out[p] = data[in_pos] ^ key
            key = (key + inc) & 0xFF
            p -= 2
            in_pos += 1
        size -= chunk
        out_pos += chunk
        max_bytes -= chunk
    rest = size + num_unencrypted
    if rest > 0:
        out[out_pos : out_pos + rest] = data[in_pos : in_pos + rest]
    return bytes(out)


@register_archive("th08", format_name="pbgz")
class PbgzArchive(ArchiveBase):
    """Pbgz 容器的只读视图(通用面见 ArchiveBase)。

    条目数据区连续存放且长度不由条目表给出, 故本类额外记下每条的压缩长度,
    并覆盖 ``raw()`` 按"下一条 dataOffset(哨兵 = fileTableOffset)"切片
    (PbgArchive.cpp:85,267); ArchiveEntry.size 保持"解压后大小"语义不变。
    """

    format_name: ClassVar[str] = "pbgz"

    #: 本格式的 LZSS 参数(条目数据与文件表同参, 与 pbg4 一致)
    _LZSS: ClassVar[LzssDecompressor] = LzssDecompressor(
        dict_bits=DICT_BITS, len_bits=LEN_BITS
    )

    def __init__(
        self,
        entries: list[ArchiveEntry],
        bytes_: bytes,
        path: Path | None = None,
        comp_sizes: dict[str, int] | None = None,
    ) -> None:
        super().__init__(entries, bytes_, path=path)
        self._comp_sizes = comp_sizes or {}

    @classmethod
    def sniff(cls, header: bytes) -> bool:
        return header[:4] == MAGIC

    @classmethod
    def from_bytes(cls, data: bytes, path: Path | None = None) -> Self:
        if not cls.sniff(data):
            raise ArchiveFormatError(f"不是 Pbgz 容器，识别到的格式：{data[:4]!r}")
        # 12 字节加密头: 先整体 decrypt 再解偏码(PbgArchive.cpp:177,181-183)
        header = decrypt(data[4:16], _HEADER_KEY, _HEADER_INC, _HEADER_CHUNK, _HEADER_MAX)
        num_entries, table_offset, table_size = (
            v - bias
            for v, bias in zip(struct.unpack("<iii", header), _HEADER_BIAS)
        )
        if num_entries <= 0 or table_offset >= len(data):
            raise ArchiveFormatError(
                f"Pbgz 头损坏: 条目数 {num_entries}, 文件表偏移 {table_offset}"
            )
        # 文件表: 从 fileTableOffset 到文件尾, 先 decrypt 再 LZSS(PbgArchive.cpp:209,213)
        table_lzss = decrypt(
            data[table_offset:], _TABLE_KEY, _TABLE_INC, _TABLE_CHUNK, _TABLE_MAX
        )
        table = cls._LZSS.decompress(table_lzss, table_size)
        # 变长记录: 名字\0 + u32 dataOffset + u32 decompressedSize + u32 不读
        # (PbgArchive.cpp:242-269)
        entries: list[ArchiveEntry] = []
        offsets: list[int] = []
        pos = 0
        for _ in range(num_entries):
            end = table.index(b"\x00", pos)
            name = table[pos:end].decode("latin-1")
            pos = end + 1
            offset, size, _ = struct.unpack_from("<III", table, pos)
            pos += 12
            entries.append(ArchiveEntry(name, offset, size))
            offsets.append(offset)
        # compressedSize = next.dataOffset - cur.dataOffset, 哨兵 = fileTableOffset
        # (PbgArchive.cpp:85,267)
        comp_sizes = {
            e.name: nxt - e.offset for e, nxt in zip(entries, offsets[1:] + [table_offset])
        }
        return cls(entries, data, path=path, comp_sizes=comp_sizes)

    def raw(self, name: str) -> bytes:
        """按压缩长度切片(条目数据连续存放, 见类 docstring)。"""
        e = self._by_name[name]
        return self._data[e.offset : e.offset + self._comp_sizes[name]]

    def decode(self, entry: ArchiveEntry, raw: bytes) -> bytes:
        return self._LZSS.decompress(raw, entry.size)
