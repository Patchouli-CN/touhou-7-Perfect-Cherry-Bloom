"""数据解包: LZSS 解压 + Pbg4 容器读取 —— Pythonic。

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
    """把 LZSS 压缩字节流解成原字节。字典为 8192 字节环形缓冲。

    性能重写 (BUGS.md 增量#3): 纯 Python 逐位实现只有 ~2MB/s, 大贴图包
    首用解压 200-900ms 是开局/换关/首发 bomb 卡顿的主因。本实现:
    - 环形字典不显式维护 —— 它逐字节镜像输出流(写头偏移 1), 匹配等价于
      输出流上距离 d0 的自复制, 用切片倍增一次拷贝;
    - 位缓冲按需批量补字节(源尽补零, 与原实现 pos 越界读 0 一致);
    - 快径: 连续 8 个字面量(flag 全 1 的 72bit 组)一次提取。
    已按 th07.dat 全部 197 条目与原实现逐字节比对一致(实测 ~1.9x)。
    """

    # 8 个 9bit 字面量组的 flag 位掩码(快径全 1 判定用)
    _LIT8_FLAGS = sum(1 << (8 + 9 * k) for k in range(8))

    def decompress(self, src: bytes, out_len: int | None = None) -> bytes:
        out = bytearray()
        pos = 0
        n = len(src)
        buf = 0  # 位缓冲, 下一位在 MSB 侧
        cnt = 0  # 缓冲有效位数
        mask = MASK
        lit8 = self._LIT8_FLAGS
        while True:
            while cnt < 72:
                take = src[pos : pos + 9]
                pos += len(take)
                if take:
                    buf = (buf << (8 * len(take))) | int.from_bytes(take, "big")
                    cnt += 8 * len(take)
                else:
                    buf <<= 8
                    cnt += 8
            buf &= (1 << cnt) - 1  # 截断陈旧高位, 防 bigint 膨胀
            chunk = (buf >> (cnt - 72)) & ((1 << 72) - 1)
            if (chunk & lit8) == lit8:
                # 快径: 8 个连续字面量 (flag=1 + 8bit, 共 72bit)
                cnt -= 72
                out += bytes((chunk >> (9 * k)) & 0xFF for k in range(7, -1, -1))
            else:
                cnt -= 9
                tok = (buf >> cnt) & 0x1FF
                if tok & 0x100:
                    out.append(tok & 0xFF)
                else:
                    # 匹配: flag=0 已随高 9bit 读出 (offset 高 8 位),
                    # 再取 9bit = offset 低 5 位 + 4bit 长度
                    cnt -= 9
                    tok2 = (buf >> cnt) & 0x1FF
                    off = ((tok & 0xFF) << 5) | (tok2 >> 4)
                    if off == 0:  # EOD
                        break
                    run = (tok2 & 0xF) + 3  # 实际长度 = 编码 + 2, 含端点 +1
                    w = len(out)
                    # 环读位置 off 对应的输出流距离 (写头 = w%8192+1);
                    # d0=0 即整环 8192; 首 8192 字节内引用未写区 → 零填充
                    d0 = ((w % DICT_SIZE) + 1 - off) & mask
                    if d0 == 0:
                        d0 = DICT_SIZE
                    if d0 > w:
                        pat = b"\x00" * (d0 - w) + bytes(out)
                    else:
                        pat = bytes(out[w - d0 :])
                    out += (pat * (run // d0 + 1))[:run]
            if out_len is not None and len(out) >= out_len:
                break
        return bytes(out)


class ArchiveEntry(msgspec.Struct, frozen=True):
    name: str
    offset: int  # 数据在文件中的绝对偏移
    size: int  # 解压后大小


class GameArchive:
    """Pbg4 容器的只读视图。

    load() 的解压结果走进程级共享缓存 (BUGS.md 增量#3): 每个视图各自
    GameArchive.open 同一 .dat, 同一条目此前每实例重复 LZSS 解压
    (~2MB/s); th07.dat 整包解压后仅 ~65MB, 缓存有界不淘汰。
    """

    # (dat 路径, 条目名) → 解压后字节
    _DECOMP_CACHE: dict[tuple[str, str], bytes] = {}

    def __init__(
        self, entries: list[ArchiveEntry], bytes_: bytes, path: Path | None = None
    ) -> None:
        self._entries = entries
        self._by_name = {e.name: e for e in entries}
        self._data = bytes_
        self._path = str(path) if path is not None else None

    @classmethod
    def open(cls, path: str | Path) -> "GameArchive":
        path = Path(path)
        return cls._load(path.read_bytes(), path=path)

    @classmethod
    def _load(cls, data: bytes, path: Path | None = None) -> "GameArchive":
        if data[:4] != b"PBG4":
            raise ArchiveFormatError("不是 Pbg4 容器")
        num_entries, header_size, decompressed_size = struct.unpack_from(
            "<III", data, 4
        )
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
        return cls(entries, data, path=path)

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
        """取出并 LZSS 解压为最终资源字节(共享缓存, 见类 docstring)。"""
        path = self._path
        if path is not None:
            out = self._DECOMP_CACHE.get((path, name))
            if out is not None:
                return out
        e = self._by_name[name]
        out = LzssDecompressor().decompress(self.raw(name), e.size)
        if path is not None:
            self._DECOMP_CACHE[(path, name)] = out
        return out
