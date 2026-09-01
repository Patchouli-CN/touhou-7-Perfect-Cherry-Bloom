"""Pbg4 容器 —— th07《东方妖妖梦》的资源包格式。

布局: 头 ``"PBG4" + u32 条目数 + u32 目录偏移 + u32 目录解压后大小``,
尾部是一段 LZSS 压缩的条目表(``名字\\0 + u32 offset + u32 size + u32 _``),
每条数据本身也是 LZSS(字典 13 位/长度 4 位)。
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import ClassVar, Self

from ...exceptions import ArchiveFormatError
from ...registry import register_archive
from .base import ArchiveBase, ArchiveEntry
from .lzss import LzssDecompressor

MAGIC = b"PBG4"
DICT_BITS = 13
LEN_BITS = 4


@register_archive("th07")
class Pbg4Archive(ArchiveBase):
    """Pbg4 容器的只读视图(通用面见 ArchiveBase)。"""

    format_name: ClassVar[str] = "pbg4"

    #: 本格式的 LZSS 参数(条目数据与目录表同参)
    _LZSS: ClassVar[LzssDecompressor] = LzssDecompressor(
        dict_bits=DICT_BITS, len_bits=LEN_BITS
    )

    @classmethod
    def sniff(cls, header: bytes) -> bool:
        return header[:4] == MAGIC

    @classmethod
    def from_bytes(cls, data: bytes, path: Path | None = None) -> Self:
        if not cls.sniff(data):
            raise ArchiveFormatError(f"不是 Pbg4 容器，识别到的格式：{data[:4]!r}")
        num_entries, header_size, decompressed_size = struct.unpack_from(
            "<III", data, 4
        )
        # 尾部是一段 LZSS 压缩的条目表
        table = cls._LZSS.decompress(data[header_size:], decompressed_size)
        entries: list[ArchiveEntry] = []
        pos = 0
        for _ in range(num_entries):
            end = table.index(b"\x00", pos)
            name = table[pos:end].decode("latin-1")
            pos = end + 1
            offset, size, _ = struct.unpack_from("<III", table, pos)
            pos += 12
            entries.append(ArchiveEntry(name, offset, size))
        return cls(entries, data, path=path)

    def decode(self, entry: ArchiveEntry, raw: bytes) -> bytes:
        return self._LZSS.decompress(raw, entry.size)
