"""th07 注册事实测试 —— import touhou 后 th07 各维度的注册内容。

注册表机制本身的通用契约见 tests/test_registry.py(只用桩类/假作品)。
"""

from __future__ import annotations

import pytest

from touhou import TouhouWorld
from touhou.engine.ecl import EclFile
from touhou.engine.render import Renderer
from touhou.games.th07.ecl_host import GameEclHost
from touhou.games.th07.ecl_vm import EclMachineTh07 as EclMachine
from touhou.games.th07.view import GameApp
from touhou.games.th07.world import PerfectCherryBloom
from touhou.paths import DEFAULT_DATA
from touhou.registry import (
    GameData,
    get_archive_format,
    get_archive_spec,
    get_game,
    get_renderer,
    register_game_data,
    register_world_impl,
    registered_archives,
)
from touhou.schema.anm import AnmFile
from touhou.schema.archive import open_archive
from touhou.schema.archive.pbg4 import Pbg4Archive

needs_data = pytest.mark.skipif(not DEFAULT_DATA.exists(), reason="需要真实 th07.dat")


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
    assert spec.app is GameApp
    assert spec.archive is not None
    assert spec.archive.container_cls is Pbg4Archive
    assert spec.archive.format_name == "pbg4"


def test_th07_archive_format_registered() -> None:
    """th07 的资源包格式 = pbg4(容器实现不再由消费方直接 import)。"""
    assert get_archive_spec("th07").container_cls is Pbg4Archive
    assert get_archive_format("pbg4").container_cls is Pbg4Archive
    assert "pbg4" in registered_archives()


@needs_data
def test_th07_archive_opens_by_game_and_by_sniff() -> None:
    """真实 th07.dat: 按作品名取格式与按文件头认头得到同一个容器类。"""
    by_game = open_archive(DEFAULT_DATA, game="th07")
    by_sniff = open_archive(DEFAULT_DATA)
    assert isinstance(by_game, Pbg4Archive) and isinstance(by_sniff, Pbg4Archive)
    assert by_game.format_name == "pbg4"
    assert len(by_game) == len(by_sniff)
    # 解压缓存跨实例共享(BUGS.md 增量#3): 同条目同一 bytes 对象
    assert by_game.load("ecldata1.ecl") is by_sniff.load("ecldata1.ecl")


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


def test_th07_window_app_registered() -> None:
    """import touhou 即登记 th07 窗口 App(games.th07.view.GameApp)。"""
    assert get_game("th07").app is GameApp


def test_pygame_renderer_satisfies_renderer_protocol() -> None:
    """注册表取回的 "pygame" 后端类实现 Renderer 协议全部成员(运行时探测)。

    替代原 apis.basic 的 mypy 静态断言(_pygame_backend_satisfies_renderer):
    games.th07.view 是 mypy 豁免区, 断言落不了实现侧; apis 去 th07 耦合后
    由本测试兜底协议符合性(只查成员存在, 签名/语义由全量测试覆盖)。
    """
    cls = get_renderer("pygame")
    missing = [
        m
        for m in dir(Renderer)
        if not m.startswith("_") and not callable(getattr(cls, m, None))
    ]
    assert not missing, missing


# ---- GameData 数值表维度 ----
def test_th07_data_registered() -> None:
    """import touhou 即登记 th07 数值表(games.th07.data.TH07_DATA), 与作品包默认表同值。"""
    from touhou.games.th07.boss import SPELLCARD_SCORE
    from touhou.games.th07.items import DROP_TABLE, POWER_LEVELS
    from touhou.games.th07.data import TH07_DATA

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
        spellcard_scores=(100000,) * 8,  # 短表(impl 的 begin_spellcard 有钳位)
        drop_table=(1,) * 4,  # 小怪只掉点道具
    )
    register_game_data("th07fork", custom)
    register_world_impl("th07fork")(PerfectCherryBloom)
    spec = get_game("th07fork")
    assert spec.data is custom and spec.world is PerfectCherryBloom

    game = Game(game="th07fork", character="ForkA", difficulty="Only", seed=9)
    assert isinstance(game._impl, PerfectCherryBloom)
    assert game._impl.data is custom  # data 注入生效
    assert game._impl._drop_table == [1, 1, 1, 1]  # 掉落表走了注入值
    for _ in range(300):
        game.step(Input(shoot=True))
    assert game.frame == 300 and game.score >= 0  # 基本对局推进
    snap = game.snapshot()  # 门面协议面照常工作
    assert snap.frame == 300 and snap.player.state == "alive"


def test_engine_modules_default_to_th07_tables() -> None:
    """作品包模块的模块级表 = data.py 的 th07 表(单一来源, 不经注册表也能跑)。"""
    from touhou.games.th07 import bomb, boss, items
    from touhou.games.th07 import data as games_th07
    from touhou.games.th07.data import CHARACTER_SHT

    assert boss.SPELLCARD_SCORE is games_th07.SPELLCARD_SCORE
    assert items.DROP_TABLE is games_th07.DROP_TABLE
    assert items.POWER_LEVELS is games_th07.POWER_LEVELS
    assert items.FULL_POWER_SCORE_BONUS is games_th07.FULL_POWER_SCORE_BONUS
    raw = games_th07.BOMB_PARAMS
    assert set(bomb.BOMB_PARAMS) == set(raw)
    for key, params in bomb.BOMB_PARAMS.items():
        assert (
            params.duration,
            params.invulnerability,
            params.drain_min_cost,
            params.drain_scale,
        ) == raw[key]
    assert CHARACTER_SHT[5] == ("ply02b.sht", "ply02bs.sht")


def test_boss_spellcard_scores_override() -> None:
    """Boss 的 spellcard_scores 注入: 空 = th07 默认表, 非空 = 作品表。"""
    from touhou.games.th07.boss import SPELLCARD_SCORE, Boss

    b = Boss()
    b.begin_spellcard(0, 600)
    assert b.capture_score == SPELLCARD_SCORE[0]  # 默认表
    b2 = Boss(spellcard_scores=(777000,))
    b2.begin_spellcard(0, 600)
    assert b2.capture_score == 777000  # 注入表


# ---- mod 能力提供者维度 ----
def test_th07_mods_registered() -> None:
    """import touhou 即登记 th07 mod 能力提供者(games.th07.mods.Th07Mods)。"""
    from touhou.games.th07.mods import Th07Mods

    assert get_game("th07").mods is Th07Mods


def test_th07_title_constant() -> None:
    """th07 作品标题常量(环境探测的标题映射源; 测试进程注册了假作品,
    detect_environment 的 title 不写死 th07, 故标题事实在此锚定)。"""
    from touhou.registry import GAME_TITLES

    assert GAME_TITLES["th07"] == "東方妖々夢 〜 Perfect Cherry Blossom"
