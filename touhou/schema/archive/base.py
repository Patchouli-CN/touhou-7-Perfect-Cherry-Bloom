"""资源容器抽象基类 —— 各作 .dat 包的公共只读视图。

具体格式(pbg4/pbgz/tha1…)只需实现三件事: 认头(``sniff``)、解析条目表
(``from_bytes``)、解一条(``decode``); 取名/查在不在/取原始字节/解压缓存
都在本层, 与作品无关。格式实现经 ``registry.register_archive`` 登记,
调用方用同包 ``open_archive`` 拿视图, 不 import 具体格式类。
"""

from __future__ import annotations

import msgspec
from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar, Self


class ArchiveEntry(msgspec.Struct, frozen=True):
    """容器内一条资源的目录项。"""

    name: str
    offset: int  # 数据在文件中的绝对偏移
    size: int  # 解压后大小


class ArchiveBase(ABC):
    """资源容器的只读视图基类。

    ``load()`` 的解压结果走进程级共享缓存 (BUGS.md 增量#3): 每个视图各自
    打开同一 .dat, 同一条目此前每实例重复 LZSS 解压 (~2MB/s); th07.dat
    整包解压后仅 ~65MB, 缓存有界不淘汰。缓存键含格式名, 不同格式读同名
    条目互不串味。
    """

    #: 格式标识(登记名); 子类必须覆盖, ``register_archive`` 缺省取此值
    format_name: ClassVar[str] = ""

    #: (格式名, dat 路径, 条目名) → 解压后字节; 全格式共享一张表
    _DECOMP_CACHE: ClassVar[dict[tuple[str, str, str], bytes]] = {}

    def __init__(
        self,
        entries: list[ArchiveEntry],
        bytes_: bytes,
        path: Path | None = None,
    ) -> None:
        self._entries = entries
        self._by_name = {e.name: e for e in entries}
        self._data = bytes_
        self._path = str(path) if path is not None else None

    # ---- 子类实现 ----
    @classmethod
    @abstractmethod
    def sniff(cls, header: bytes) -> bool:
        """凭文件开头几十字节判断是不是本格式(不抛异常, 只回真假)。"""

    @classmethod
    @abstractmethod
    def from_bytes(cls, data: bytes, path: Path | None = None) -> Self:
        """解析整包字节为视图(读目录表); 头不对报 ArchiveFormatError。"""

    @abstractmethod
    def decode(self, entry: ArchiveEntry, raw: bytes) -> bytes:
        """把一条的原始(压缩/加密)字节解成最终资源字节。"""

    # ---- 通用只读面 ----
    @classmethod
    def open(cls, path: str | Path) -> Self:
        """按路径打开(格式已知时的直接入口; 格式待定用 open_archive)。"""
        path = Path(path)
        return cls.from_bytes(path.read_bytes(), path=path)

    @property
    def path(self) -> Path | None:
        """来源 .dat 路径(从字节构造时为 None)。"""
        return Path(self._path) if self._path is not None else None

    def entries(self) -> list[ArchiveEntry]:
        """全部目录项(容器内顺序)。"""
        return list(self._entries)

    def names(self) -> list[str]:
        return [e.name for e in self._entries]

    def __contains__(self, name: str) -> bool:
        return name in self._by_name

    def __len__(self) -> int:
        return len(self._entries)

    def __repr__(self) -> str:
        src = self._path or "<bytes>"
        return f"<{type(self).__name__} {self.format_name} {src} {len(self)} 条目>"

    def raw(self, name: str) -> bytes:
        """返回条目的原始(可能压缩)数据。"""
        e = self._by_name[name]
        return self._data[e.offset : e.offset + e.size]

    def load(self, name: str) -> bytes:
        """取出并解压为最终资源字节(共享缓存, 见类 docstring)。"""
        path = self._path
        key = (self.format_name, path or "", name)
        if path is not None:
            out = self._DECOMP_CACHE.get(key)
            if out is not None:
                return out
        e = self._by_name[name]
        out = self.decode(e, self.raw(name))
        if path is not None:
            self._DECOMP_CACHE[key] = out
        return out
