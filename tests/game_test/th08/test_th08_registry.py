"""th08 注册事实测试 —— import touhou 后 th08 各维度(data + archive + ecl)的注册内容。

照 game_test/th07/test_th07_registry.py 的模式; 通用契约见 tests/test_registry.py。
"""

from __future__ import annotations

from touhou.paths import DEFAULT_DATA_PATHS, resolve_data_path
from touhou.registry import (
    GAME_TITLES,
    get_archive_format,
    get_archive_spec,
    get_game,
    registered_archives,
    registered_games,
)
from touhou.schema.archive.pbgz import PbgzArchive


def test_th08_data_registered() -> None:
    """import touhou 即登记 th08 数值表(games.th08.data.TH08_DATA), 字段值抽查。"""
    from touhou.games.th08.data import TH08_DATA

    spec = get_game("th08")
    assert spec.data is TH08_DATA
    data = spec.data
    assert len(data.characters) == 12  # 0-3 双人组 4-11 单人(ScoreDat.hpp:54-69)
    assert data.characters[0] == "ReimuYukari" and data.characters[11] == "Yuyuko"
    assert data.difficulties == ("Easy", "Normal", "Hard", "Lunatic", "Extra")
    assert data.extra_stages == ("Extra",)
    assert data.stage_count == 6
    assert data.full_power == 128
    assert data.power_levels == (8, 24, 48, 80, 128, 999)  # ItemManager.cpp:17
    assert len(data.drop_table) == 32  # EnemyManager.cpp:24-29
    assert data.character_sht[0] == ("ply00a.sht", "ply00as.sht")  # Player.cpp:35-43
    assert len(data.character_sht) == 12


def test_th08_archive_format_registered() -> None:
    """th08 的资源包格式 = pbgz(按作品名/格式名双表可查)。"""
    assert get_archive_spec("th08").container_cls is PbgzArchive
    assert get_archive_spec("th08").format_name == "pbgz"
    assert get_archive_format("pbgz").container_cls is PbgzArchive
    assert "pbgz" in registered_archives()
    assert "th08" in registered_games()


def test_th08_ecl_registered() -> None:
    """th08 的 ECL 维度(阶段 2 单 A): 占位 VM + th08 文件格式类。"""
    from touhou.games.th08.ecl_file import EclFileTh08
    from touhou.games.th08.ecl_vm import EclMachineTh08

    spec = get_game("th08")
    assert spec.ecl is not None
    assert spec.ecl.machine is EclMachineTh08 and spec.ecl.file_format is EclFileTh08


def test_th08_title_constant() -> None:
    """th08 作品标题常量(环境探测的标题映射源)。"""
    assert GAME_TITLES["th08"] == "東方永夜抄 〜 Imperishable Night."


def test_th08_default_data_path() -> None:
    """默认数据路径按作品化: th08 查 DEFAULT_DATA_PATHS, 环境变量覆盖链路不变。"""
    assert DEFAULT_DATA_PATHS["th08"].name == "th08.dat"
    assert resolve_data_path(game="th08") == DEFAULT_DATA_PATHS["th08"]
    # 显式参数优先(既有覆盖链路的头部不动)
    assert resolve_data_path("x.dat", game="th08").name == "x.dat"
