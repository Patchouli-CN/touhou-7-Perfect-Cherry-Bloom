"""th08 真实资源包(th08.dat, PBGZ)测试 —— 缺失自动 skip(见 conftest.needs_data)。

条目内容只有 LZSS 一层(本层职责); ecl/anm/std 等条目解压后还带 "edz"
内层签名加密(TryDecryptFromTable, Global.cpp:901-927), 那是解析层的事
(games/th08/crypt.py), 本层只验证解压成功且大小与目录表吻合。
"""

from __future__ import annotations

from touhou.paths import DEFAULT_DATA_PATHS
from touhou.schema.archive import open_archive
from touhou.schema.archive.pbgz import PbgzArchive

from .conftest import needs_data

TH08_DAT = DEFAULT_DATA_PATHS["th08"]


@needs_data
def test_th08_archive_opens_by_game_and_by_sniff() -> None:
    """真实 th08.dat: 按作品名取格式与按文件头认头得到同一个容器类。"""
    by_game = open_archive(TH08_DAT, game="th08")
    by_sniff = open_archive(TH08_DAT)
    assert isinstance(by_game, PbgzArchive) and isinstance(by_sniff, PbgzArchive)
    assert by_game.format_name == "pbgz"
    assert len(by_game) == len(by_sniff) > 0


@needs_data
def test_th08_archive_ecl_entries_roundtrip() -> None:
    """抽 ecl 类明文条目: 解压成功且大小与目录表吻合。"""
    arc = open_archive(TH08_DAT, game="th08")
    ecl_entries = [e for e in arc.entries() if e.name.endswith(".ecl")]
    assert ecl_entries, "th08.dat 应含 ecldata*.ecl 条目"
    for entry in ecl_entries[:3]:
        data = arc.load(entry.name)
        assert len(data) == entry.size > 0
