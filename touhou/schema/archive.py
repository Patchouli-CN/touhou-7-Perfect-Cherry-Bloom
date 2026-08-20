""" 数据解包: LZSS 解压 + Pbg4 容器读取 —— Pythonic。

把两种格式封装成 GameArchive: 可以从 th0X.dat 按名字取出某个已解压的资源。
"""

from __future__ import annotations

import struct
import msgspec
from pathlib import Path

from ..exceptions import ArchiveFormatError

DICT_BITS = 13
LEN_BITS = 4
DICT_SIZE = 1 << DICT_BITS  # 8192
MASK = DICT_SIZE - 1
LOOKAHEAD = (1 << LEN_BITS) + 2


class LzssDecompressor:
    """把 LZSS 压缩字节流解成原字节。字典为 8192 字节环形缓冲。"""

    def __init__(self) -> None:
        self._dict = bytearray(DICT_SIZE + 1)

    def decompress(self, src: bytes, out_len: int | None = None) -> bytes:
        out = bytearray()
        dict_ = self._dict
        dict_head = 1
        pos = 0
        n = len(src)
        # 位缓冲: 与原逐字节 next_byte + MSB-first 掩码读取逐位等价,
        # 只是改成整数缓冲读位(纯性能重写, 输出不变)
        bitbuf = 0
        bitcnt = 0

        while True:
            if bitcnt < 1:
                bitbuf = (bitbuf << 8) | (src[pos] if pos < n else 0)
                pos += 1
                bitcnt += 8
            bitcnt -= 1
            literal = (bitbuf >> bitcnt) & 1
            bitbuf &= (1 << bitcnt) - 1   # 截断已消费位, 防 bigint 膨胀

            if literal:
                # 8 位字面字节
                while bitcnt < 8:
                    bitbuf = (bitbuf << 8) | (src[pos] if pos < n else 0)
                    pos += 1
                    bitcnt += 8
                bitcnt -= 8
                value = (bitbuf >> bitcnt) & 0xFF
                bitbuf &= (1 << bitcnt) - 1
                dict_[dict_head] = value
                dict_head = (dict_head + 1) & MASK
                out.append(value)
            else:
                # 13 位偏移 + 4 位长度(EOD 时 offset==0)
                while bitcnt < DICT_BITS:
                    bitbuf = (bitbuf << 8) | (src[pos] if pos < n else 0)
                    pos += 1
                    bitcnt += 8
                bitcnt -= DICT_BITS
                offset = (bitbuf >> bitcnt) & MASK
                bitbuf &= (1 << bitcnt) - 1
                if offset == 0:
                    break
                while bitcnt < LEN_BITS:
                    bitbuf = (bitbuf << 8) | (src[pos] if pos < n else 0)
                    pos += 1
                    bitcnt += 8
                bitcnt -= LEN_BITS
                length = (bitbuf >> bitcnt) & ((1 << LEN_BITS) - 1)
                bitbuf &= (1 << bitcnt) - 1
                run = length + 2  # 实际长度 = 编码 + 2
                for i in range(run + 1):
                    value = dict_[(offset + i) & MASK]
                    dict_[dict_head] = value
                    dict_head = (dict_head + 1) & MASK
                    out.append(value)
            if out_len is not None and len(out) >= out_len:
                break
        return bytes(out)


class ArchiveEntry(msgspec.Struct, frozen=True):
    name: str
    offset: int       # 数据在文件中的绝对偏移
    size: int         # 解压后大小


class GameArchive:
    """Pbg4 容器的只读视图。"""

    def __init__(self, entries: list[ArchiveEntry], bytes_: bytes) -> None:
        self._entries = entries
        self._by_name = {e.name: e for e in entries}
        self._data = bytes_

    @classmethod
    def open(cls, path: str | Path) -> "GameArchive":
        return cls._load(Path(path).read_bytes())

    @classmethod
    def _load(cls, data: bytes) -> "GameArchive":
        if data[:4] != b"PBG4":
            raise ArchiveFormatError("不是 Pbg4 容器")
        num_entries, header_size, decompressed_size = struct.unpack_from("<III", data, 4)
        # 尾部是一段 LZSS 压缩的条目表
        table = LzssDecompressor().decompress(data[header_size:], decompressed_size)
        entries: list[ArchiveEntry] = []
        pos = 0
        for _ in range(num_entries):
            end = table.index(b"\x00", pos)
            name = table[pos:end].decode("latin-1")
            pos = end + 1
            offset, size, _ = struct.unpack_from("<III", table, pos)
            pos += 12
            entries.append(ArchiveEntry(name, offset, size))
        return cls(entries, data)

    def names(self) -> list[str]:
        return [e.name for e in self._entries]

    def __contains__(self, name: str) -> bool:
        return name in self._by_name

    def __len__(self) -> int:
        return len(self._entries)

    def raw(self, name: str) -> bytes:
        """返回条目的原始(可能压缩)数据。"""
        e = self._by_name[name]
        return self._data[e.offset : e.offset + e.size]

    def load(self, name: str) -> bytes:
        """取出并 LZSS 解压为最终资源字节。"""
        e = self._by_name[name]
        return LzssDecompressor().decompress(self.raw(name), e.size)
