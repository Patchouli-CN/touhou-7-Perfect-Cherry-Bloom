"""作品注册表(touhou/registry.py)+ TouhouWorld/Game 的 game= 接缝测试。"""
from __future__ import annotations

import pytest

import touhou
from touhou import TouhouWorld
from touhou.core.impl import PerfectCherryBloom
from touhou.engine.ecl import EclFile, EclMachine
from touhou.engine.ecl_host import GameEclHost
from touhou.paths import DEFAULT_DATA
from touhou.registry import (
    GameData,
    GameHooks,
    get_game,
    register_anm,
    register_ecl,
    register_game_data,
    register_game_hooks,
    register_world_impl,
    registered_games,
)
from touhou.schema.anm import AnmFile

needs_data = pytest.mark.skipif(not DEFAULT_DATA.exists(),
                                reason="需要真实 th07.dat")


# ---- 注册/查找 ----
def test_th07_registered_on_import() -> None:
    """import touhou 即完成 th07 全维度注册(引用注册, 不移动文件)。"""
    spec = get_game("th07")
    assert spec.name == "th07"
    assert spec.ecl is not None
    assert spec.ecl.machine is EclMachine and spec.ecl.file_format is EclFile
    assert spec.anm is not None
    assert spec.anm.format is AnmFile and spec.anm.version == 2
    assert spec.hooks.host is GameEclHost
    assert spec.hooks.stage_file == "stage{n}.std"
    assert spec.hooks.ecl_file == "ecldata{n}.ecl"
    assert spec.hooks.msg_file == "msg{n}.dat"
    assert spec.world is PerfectCherryBloom
    assert "th07" in registered_games()


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
    with pytest.raises(ValueError, match="重复注册.*th07"):
        register_ecl("th07", file_format=EclFile)(EclMachine)

    class Dummy:
        pass

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
@needs_data
def test_world_game_th07_equivalent_to_default() -> None:
    """TouhouWorld(game='th07') 与不传等价: 同一实现对局, 帧 0 状态一致。"""
    a = TouhouWorld(headless=True, seed=1)
    b = TouhouWorld(headless=True, game="th07", seed=1)
    assert b.game_name == "th07"
    assert b.spec.world is PerfectCherryBloom
    assert isinstance(b.game._impl, PerfectCherryBloom)
    assert a.game.frame == b.game.frame == 0
    assert a.game.score == b.game.score


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
    assert "GameSpec" in touhou.__all__


# ---- GameData 数值表维度 ----
def test_th07_data_registered() -> None:
    """import touhou 即登记 th07 数值表(games_th07.TH07_DATA), 与引擎默认表同值。"""
    from touhou.engine.boss import SPELLCARD_SCORE
    from touhou.engine.items import DROP_TABLE, POWER_LEVELS
    from touhou.games_th07 import TH07_DATA

    spec = get_game("th07")
    assert spec.data is TH07_DATA
    assert len(spec.data.spellcard_scores) == 141
    assert spec.data.spellcard_scores == tuple(SPELLCARD_SCORE)
    assert spec.data.characters[0] == "ReimuA" and len(spec.data.characters) == 6
    assert spec.data.difficulties[1] == "Normal"
    assert spec.data.extra_stages == ("Extra", "Phantasm")
    assert spec.data.stage_count == 6
    assert tuple(spec.data.drop_table) == tuple(DROP_TABLE)
    assert tuple(spec.data.power_levels) == tuple(POWER_LEVELS)
    assert spec.data.character_sht[0] == ("ply00a.sht", "ply00as.sht")
    assert len(spec.data.bomb_params) == 12


def test_duplicate_data_registration_raises() -> None:
    """数值表维度同样防静默覆盖(同名重复注册报 ValueError)。"""
    register_game_data("th95", GameData())
    with pytest.raises(ValueError, match="重复注册.*th95"):
        register_game_data("th95", GameData())
    assert get_game("th95").data == GameData()  # 空表 = "未提供"语义


@needs_data
def test_stub_game_with_custom_data_reuses_th07_engine() -> None:
    """假作品桩: 自定义 GameData + 复用 th07 对局实现, 能跑通基本对局。

    这是 th08 接入路径的最小演示: 不改引擎, 只注册自己的数值表(短符卡表/
    自定义掉落表/单机体 sht 映射), 经 Game(game=...) 注入对局。
    """
    from touhou import Game, Input

    custom = GameData(
        characters=("ForkA",),
        difficulties=("Only",),
        stage_count=6,
        character_sht={0: ("ply00a.sht", "ply00as.sht")},  # 复用 th07 资源文件
        spellcard_scores=(100000,) * 8,   # 短表(impl 的 begin_spellcard 有钳位)
        drop_table=(1,) * 4,              # 小怪只掉点道具
    )
    register_game_data("th07fork", custom)
    register_world_impl("th07fork")(PerfectCherryBloom)
    spec = get_game("th07fork")
    assert spec.data is custom and spec.world is PerfectCherryBloom

    game = Game(game="th07fork", seed=9)
    assert isinstance(game._impl, PerfectCherryBloom)
    assert game._impl.data is custom                # data 注入生效
    assert game._impl._drop_table == [1, 1, 1, 1]   # 掉落表走了注入值
    for _ in range(300):
        game.step(Input(shoot=True))
    assert game.frame == 300 and game.score >= 0    # 基本对局推进
    snap = game.snapshot()                          # 门面协议面照常工作
    assert snap.frame == 300 and snap.player.state == "alive"


def test_engine_modules_default_to_th07_tables() -> None:
    """引擎模块的模块级表 = games_th07 的 th07 表(单一来源, 不经注册表也能跑)。"""
    from touhou import games_th07
    from touhou.engine import bomb, boss, items
    from touhou.games_th07 import CHARACTER_SHT

    assert boss.SPELLCARD_SCORE is games_th07.SPELLCARD_SCORE
    assert items.DROP_TABLE is games_th07.DROP_TABLE
    assert items.POWER_LEVELS is games_th07.POWER_LEVELS
    assert items.FULL_POWER_SCORE_BONUS is games_th07.FULL_POWER_SCORE_BONUS
    raw = games_th07.BOMB_PARAMS
    assert set(bomb.BOMB_PARAMS) == set(raw)
    for key, params in bomb.BOMB_PARAMS.items():
        assert (params.duration, params.invulnerability,
                params.drain_min_cost, params.drain_scale) == raw[key]
    assert CHARACTER_SHT[5] == ("ply02b.sht", "ply02bs.sht")


def test_boss_spellcard_scores_override() -> None:
    """Boss 的 spellcard_scores 注入: 空 = th07 默认表, 非空 = 作品表。"""
    from touhou.engine.boss import SPELLCARD_SCORE, Boss

    b = Boss()
    b.begin_spellcard(0, 600)
    assert b.capture_score == SPELLCARD_SCORE[0]      # 默认表
    b2 = Boss(spellcard_scores=(777000,))
    b2.begin_spellcard(0, 600)
    assert b2.capture_score == 777000                 # 注入表
