"""作品注册表(touhou/registry.py)+ TouhouWorld/Game 的 game= 接缝测试。

通用层: 只用本地桩类与假作品 "test00"(tests/conftest.py 注册), 不 import
games.*; th07 各维度注册事实见 game_test/th07/test_th07_registry.py。
"""

from __future__ import annotations

import pytest

import touhou
from touhou import TouhouWorld
from touhou.engine.ecl import EclFile
from touhou.registry import (
    GameData,
    GameHooks,
    get_archive_format,
    get_archive_spec,
    get_game,
    register_anm,
    register_app,
    register_archive,
    register_ecl,
    register_game_data,
    register_game_hooks,
    register_mods,
    register_world_impl,
    registered_archives,
    registered_games,
)
from touhou.schema.anm import AnmFile
from touhou.schema.archive import (
    ArchiveBase,
    ArchiveEntry,
    open_archive,
    sniff_archive,
)


# ---- 注册/查找 ----
def test_register_stub_game_th99() -> None:
    """注册表可插桩: 假作品 th99 的最小桩实现能被查出来。"""

    class StubEclMachine:
        pass

    class StubAnmFile:
        pass

    class StubHost:
        pass

    class StubWorld:
        pass

    register_ecl("th99", file_format=EclFile)(StubEclMachine)
    register_anm("th99", version=3)(StubAnmFile)
    register_game_hooks("th99", stage_file="st{n}.std")(StubHost)
    register_world_impl("th99")(StubWorld)

    spec = get_game("th99")
    assert spec.ecl is not None and spec.ecl.machine is StubEclMachine
    assert spec.anm is not None and spec.anm.format is StubAnmFile
    assert spec.anm.version == 3
    assert spec.hooks.host is StubHost
    assert spec.hooks.stage_file == "st{n}.std"
    assert spec.hooks.ecl_file == "ecldata{n}.ecl"  # 未给则回落默认
    assert spec.world is StubWorld
    assert "th99" in registered_games()


def test_duplicate_registration_raises() -> None:
    """同一维度同名重复注册报 ValueError(防静默覆盖)。"""

    class Dummy:
        pass

    register_ecl("th89", file_format=EclFile)(Dummy)
    with pytest.raises(ValueError, match="重复注册.*th89"):
        register_ecl("th89", file_format=EclFile)(Dummy)

    register_world_impl("th98")(Dummy)
    with pytest.raises(ValueError, match="重复注册.*th98"):
        register_world_impl("th98")(Dummy)


def test_unknown_game_keyerror() -> None:
    """查找未注册名: KeyError 信息含作品名与已注册列表。"""
    with pytest.raises(KeyError, match="未注册的作品.*th00") as ei:
        get_game("th00")
    assert "th07" in str(ei.value)


def test_partial_registration_hooks_default() -> None:
    """只注册一个维度时 get_game 仍可用, hooks 回落默认规则。"""
    register_anm("th97", version=2)(AnmFile)
    spec = get_game("th97")
    assert spec.anm is not None
    assert spec.ecl is None and spec.world is None
    assert spec.hooks == GameHooks()


# ---- TouhouWorld/Game 的 game= 接缝 ----
def test_world_unknown_game_clear_error() -> None:
    """传未知作品名: 构造期即报清晰 KeyError(不依赖游戏资源)。"""
    with pytest.raises(KeyError, match="未注册的作品.*th99x"):
        TouhouWorld(headless=True, game="th99x")


def test_world_registered_but_no_world_impl() -> None:
    """只注册部分维度的作品: TouhouWorld 报'缺对局实现'的清晰错误。"""
    register_anm("th96", version=2)(AnmFile)
    with pytest.raises(ValueError, match="缺对局实现.*register_world_impl"):
        TouhouWorld(headless=True, game="th96")


def test_registry_exported_at_top_level() -> None:
    """注册表 API 从包顶层可拿(框架公共面)。"""
    assert touhou.get_game is get_game
    assert "register_ecl" in touhou.__all__
    assert "register_app" in touhou.__all__
    assert "GameSpec" in touhou.__all__


# ---- 窗口 App 维度 ----
def test_register_app_stub() -> None:
    """app 维度同样可插桩/防静默覆盖(契约: make_game + 关键字子集)。"""

    class StubApp:
        def __init__(self, make_game, *, data_path, bgm_path, game_data) -> None:
            self._make_game = make_game

        def run(self) -> None:
            pass

    register_app("th94")(StubApp)
    assert get_game("th94").app is StubApp
    assert "th94" in registered_games()
    with pytest.raises(ValueError, match="重复注册.*th94"):
        register_app("th94")(StubApp)


def test_world_registered_but_no_window_app() -> None:
    """有对局实现但没登记窗口 App 的作品: run()(headless=False) 报清晰错误。"""

    class StubWorld:
        pass

    register_world_impl("th93")(StubWorld)
    tw = TouhouWorld(headless=False, game="th93")
    with pytest.raises(ValueError, match="缺窗口 App.*register_app"):
        tw.run()


# ---- GameData 数值表维度 ----
def test_duplicate_data_registration_raises() -> None:
    """数值表维度同样防静默覆盖(同名重复注册报 ValueError)。"""
    register_game_data("th95", GameData())
    with pytest.raises(ValueError, match="重复注册.*th95"):
        register_game_data("th95", GameData())
    assert get_game("th95").data == GameData()  # 空表 = "未提供"语义


# ---- mod 能力提供者维度 ----
def test_register_mods_stub() -> None:
    """mods 维度同样可插桩/防静默覆盖(契约: provider(game) 构造)。"""
    from tests.conftest import FAKE_GAME, FakeMods

    class StubProvider:
        def __init__(self, game) -> None:
            self._game = game

    register_mods("th92")(StubProvider)
    assert get_game("th92").mods is StubProvider
    assert "th92" in registered_games()
    with pytest.raises(ValueError, match="重复注册.*th92"):
        register_mods("th92")(StubProvider)
    # 已登记作品(假作品 test00)同维度重复注册同样报错
    with pytest.raises(ValueError, match=f"重复注册.*{FAKE_GAME}"):
        register_mods(FAKE_GAME)(FakeMods)


# ---- 资源包格式维度(容器与作品解耦的接缝) ----
STUB_MAGIC = b"STUB"


def _stub_pack(items: dict[str, bytes], magic: bytes = STUB_MAGIC) -> bytes:
    """造一个假容器: 魔数 + 每条 (名字\\0, u32 长度, 明文数据)。"""
    out = bytearray(magic)
    for name, payload in items.items():
        out += name.encode() + b"\x00"
        out += len(payload).to_bytes(4, "little")
        out += payload
    return bytes(out)


class StubArchive(ArchiveBase):
    """不压缩的假容器格式: 证明新格式只要实现三件事就能接进来。

    魔数是类属性 —— 各测试的桩格式各用一个, 认头时互不抢(注册表是进程级的,
    多个桩格式会同时在册)。
    """

    format_name = "stubarc"
    magic = STUB_MAGIC

    @classmethod
    def sniff(cls, header: bytes) -> bool:
        return header[:4] == cls.magic

    @classmethod
    def from_bytes(cls, data: bytes, path=None):
        entries: list[ArchiveEntry] = []
        pos = len(cls.magic)
        while pos < len(data):
            end = data.index(b"\x00", pos)
            name = data[pos:end].decode()
            pos = end + 1
            size = int.from_bytes(data[pos : pos + 4], "little")
            pos += 4
            entries.append(ArchiveEntry(name, pos, size))
            pos += size
        return cls(entries, data, path=path)

    def decode(self, entry: ArchiveEntry, raw: bytes) -> bytes:
        return raw  # 明文, 不解压


def test_register_archive_stub_format() -> None:
    """archive 维度可插桩: 假格式按"作品名→规格""格式名→规格"双表查得到。"""
    register_archive("th91", format_name="stub91")(StubArchive)

    spec = get_game("th91")
    assert spec.archive is not None
    assert spec.archive.container_cls is StubArchive
    assert spec.archive.format_name == "stub91"
    assert get_archive_spec("th91").container_cls is StubArchive
    assert get_archive_format("stub91").container_cls is StubArchive
    assert "stub91" in registered_archives()
    assert "th91" in registered_games()


def test_register_archive_format_name_from_class() -> None:
    """不传 format_name 时取类的 format_name 属性。"""
    register_archive("th90")(StubArchive)
    assert get_archive_spec("th90").format_name == "stubarc"
    # 同格式服务多作: 格式表幂等(同名同类不报错), 作品表各自登记
    register_archive("th89arc")(StubArchive)
    assert get_archive_spec("th89arc").container_cls is StubArchive


def test_register_archive_multi_games_one_call() -> None:
    """一种格式服务多部作品(pbgz 之于 th08/th09): games 收序列。"""

    class MultiArchive(StubArchive):
        format_name = "multiarc"
        magic = b"MULT"

    register_archive(["th88", "th87"], format_name="multiarc")(MultiArchive)
    assert get_archive_spec("th88").container_cls is MultiArchive
    assert get_archive_spec("th87").container_cls is MultiArchive
    assert registered_archives().count("multiarc") == 1  # 格式表只一条


def test_register_archive_duplicates_raise() -> None:
    """作品名重复报错; 同格式名换实现类也报错(防静默覆盖)。"""

    class OtherArchive(StubArchive):
        format_name = "dup-fmt"
        magic = b"DUPF"

    register_archive("th86", format_name="dup-fmt")(OtherArchive)
    with pytest.raises(ValueError, match="重复注册.*th86"):
        register_archive("th86", format_name="dup-fmt")(OtherArchive)

    class Impostor(StubArchive):
        format_name = "dup-fmt"
        magic = b"IMPO"

    with pytest.raises(ValueError, match="格式重复注册.*dup-fmt"):
        register_archive("th85", format_name="dup-fmt")(Impostor)


def test_register_archive_needs_format_name() -> None:
    """类没声明 format_name 又不传参: 当场报错, 不留无名格式。"""

    class Nameless(StubArchive):
        format_name = ""
        magic = b"NONE"

    with pytest.raises(ValueError, match="未声明格式名"):
        register_archive("th84")(Nameless)


def test_unregistered_archive_lookups_raise() -> None:
    """未注册的作品/格式: KeyError 信息含已注册列表。"""
    with pytest.raises(KeyError, match="th00arc.*未注册资源包格式"):
        get_archive_spec("th00arc")
    with pytest.raises(KeyError, match="未注册的资源包格式.*nope") as ei:
        get_archive_format("nope")
    assert "pbg4" in str(ei.value)


def test_open_archive_sniffs_among_registered_formats(tmp_path) -> None:
    """认头: open_archive 不需要知道是哪部作品, 也不 import 具体格式类。

    这是解耦的实质 —— 通用层(engine/*)只给路径, 新格式注册进来就能被认出。
    """
    register_archive("th83", format_name="sniffable")(StubArchive)
    p = tmp_path / "stub.dat"
    p.write_bytes(_stub_pack({"a.txt": b"hello", "b.bin": b"\x01\x02\x03"}))

    arc = open_archive(p)  # 不传 game/format_name
    assert isinstance(arc, StubArchive)
    assert sorted(arc.names()) == ["a.txt", "b.bin"]
    assert arc.load("a.txt") == b"hello"
    assert arc.load("b.bin") == b"\x01\x02\x03"
    assert "a.txt" in arc and len(arc) == 2
    assert arc.path == p

    assert sniff_archive(STUB_MAGIC + b"xx") is StubArchive
    assert sniff_archive(b"NOPE....") is None


def test_open_archive_explicit_game_and_format(tmp_path) -> None:
    """显式指定作品/格式时按注册表直取, 不做认头兜底。"""
    register_archive("th82", format_name="explicit82")(StubArchive)
    p = tmp_path / "stub.dat"
    p.write_bytes(_stub_pack({"x": b"1"}))

    assert open_archive(p, game="th82").load("x") == b"1"
    assert open_archive(p, format_name="explicit82").load("x") == b"1"
    # 格式不符: 当场报 ArchiveFormatError, 不拖到缺条目才炸
    with pytest.raises(touhou.ArchiveFormatError):
        open_archive(p, game="th07")


def test_open_archive_unknown_format_raises(tmp_path) -> None:
    """认头认不出: 报错带文件头与已注册格式列表。"""
    p = tmp_path / "junk.dat"
    p.write_bytes(b"WHAT" + b"\x00" * 32)
    with pytest.raises(touhou.ArchiveFormatError, match="无法识别的资源包格式") as ei:
        open_archive(p)
    assert "pbg4" in str(ei.value)


def test_archive_decomp_cache_keyed_by_format(tmp_path) -> None:
    """解压缓存含格式名: 不同格式的同名条目互不串味。"""
    register_archive("th81", format_name="cachefmt")(StubArchive)
    p = tmp_path / "c.dat"
    p.write_bytes(_stub_pack({"same.bin": b"first"}))
    first = open_archive(p, format_name="cachefmt").load("same.bin")
    assert first == b"first"
    # 同路径同条目名再开: 命中缓存(同一 bytes 对象)
    assert open_archive(p, format_name="cachefmt").load("same.bin") is first


def test_archive_exported_at_top_level() -> None:
    """archive 维度的注册表 API 从包顶层可拿(框架公共面)。"""
    assert touhou.register_archive is register_archive
    assert touhou.get_archive_spec is get_archive_spec
    assert "ArchiveSpec" in touhou.__all__
    assert "registered_archives" in touhou.__all__
