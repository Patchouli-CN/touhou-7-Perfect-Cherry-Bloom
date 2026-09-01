"""资源包解包 —— 容器格式与作品解耦。

分层:
- ``lzss``: 可参数化 LZSS 解压(不认容器头)
- ``base``: ``ArchiveBase``/``ArchiveEntry`` —— 只读视图通用面 + 进程级解压缓存
- 各格式模块: 认头/读目录表/解一条, 用 ``registry.register_archive`` 登记
  自己服务的作品(th07 = ``pbg4``, 见 ``pbg4.Pbg4Archive``)

调用方入口是 ``open_archive(path)``: 不传 ``game``/``format_name`` 时按文件头
自动识别已注册格式, 因此通用层(engine/*)不需要知道当前是哪部作品; 作品包
内可传 ``game="th07"`` 让格式不符的 .dat 立刻失败, 而不是拖到缺条目才报错。
新增作品只需在本包加一个格式模块并注册, 消费方一行不改。
"""

from __future__ import annotations

from pathlib import Path

from ...exceptions import ArchiveFormatError
from ...registry import get_archive_format, get_archive_spec, registered_archives
from .base import ArchiveBase, ArchiveEntry
from .lzss import LzssDecompressor
from .pbg4 import Pbg4Archive

__all__ = [
    "ArchiveBase",
    "ArchiveEntry",
    "LzssDecompressor",
    "Pbg4Archive",
    "open_archive",
    "sniff_archive",
]

# 认头需要读的字节数(各格式 sniff 只看开头几字节)
_SNIFF_BYTES = 64


def sniff_archive(data: bytes) -> type[ArchiveBase] | None:
    """按文件头在已注册格式里找容器类; 都不认返回 None。"""
    for name in registered_archives():
        cls = get_archive_format(name).container_cls
        if issubclass(cls, ArchiveBase) and cls.sniff(data[:_SNIFF_BYTES]):
            return cls
    return None


def open_archive(
    path: str | Path,
    *,
    game: str | None = None,
    format_name: str | None = None,
) -> ArchiveBase:
    """打开资源包, 返回格式无关的只读视图。

    格式解析顺序: ``format_name`` > ``game`` 的注册格式 > 按文件头自动识别。
    显式指定时不做认头兜底 —— 拿 th08 的包按 th07 打开会当场报
    ArchiveFormatError, 这正是要的行为。

    Args:
        path: .dat 路径
        game: 作品名(如 "th07"), 按注册表取该作的容器格式
        format_name: 直接指定格式名(如 "pbg4"), 优先级最高

    Raises:
        NotRegisteredError: game/format_name 未注册
        ArchiveFormatError: 自动识别时文件头不属于任何已注册格式
    """
    path = Path(path)
    data = path.read_bytes()
    if format_name is not None:
        cls = get_archive_format(format_name).container_cls
    elif game is not None:
        cls = get_archive_spec(game).container_cls
    else:
        found = sniff_archive(data)
        if found is None:
            raise ArchiveFormatError(
                f"无法识别的资源包格式: {path} (头 {data[:4]!r}; "
                f"已注册格式: {registered_archives()})"
            )
        cls = found
    return cls.from_bytes(data, path=path)
