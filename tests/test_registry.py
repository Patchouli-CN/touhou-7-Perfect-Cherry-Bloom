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
    get_game,
    register_anm,
    register_app,
    register_ecl,
    register_game_data,
    register_game_hooks,
    register_mods,
    register_world_impl,
    registered_games,
)
from touhou.schema.anm import AnmFile


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
