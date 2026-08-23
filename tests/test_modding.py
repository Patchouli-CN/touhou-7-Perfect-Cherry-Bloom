"""公共魔改 API(touhou/apis/modding.py)门面行为测试。

夹具模式照 test_api.py: 真实 th07.dat headless 开局; 引擎内部状态读回验证
时允许摸 game._impl(测试本就需要校验 ModApi 的写入落到了引擎里)。
"""
from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from touhou.apis.basic import Difficulty, Game, Input, ShotType
from touhou.apis.modding import Aim, Burst, ModApi
from touhou.games.th07.bomb import BorderState
from touhou.paths import DEFAULT_DATA
from touhou.registry import GameHooks, GameSpec, get_game, register_mods
from touhou.utils import Vec2

pytestmark = pytest.mark.skipif(not DEFAULT_DATA.exists(),
                                reason="需要真实 th07.dat")


def _mods(seed: int = 1) -> tuple[Game, ModApi]:
    game = Game(character=ShotType.REIMU_A, difficulty=Difficulty.NORMAL,
                seed=seed)
    return game, ModApi(game)


# ---- 资源 setter(写入后经 Game 只读属性读回验证) ----
def test_resource_setters() -> None:
    game, mods = _mods()
    mods.set_power(128)
    mods.set_bombs(8)
    mods.set_lives(5)
    mods.add_score(10000)
    assert game.power == 128
    assert game.bombs == 8
    assert game.lives == 5
    assert game.score == 10000
    # 满火力上限来自注册表数值表(th07 = 128), 不是 modding 写死的常量
    assert mods.full_power == 128


def test_set_power_range_check() -> None:
    _, mods = _mods()
    with pytest.raises(ValueError, match="超出"):
        mods.set_power(mods.full_power + 1)
    with pytest.raises(ValueError, match="超出"):
        mods.set_power(-1)


def test_set_bombs_lives_range_check() -> None:
    _, mods = _mods()
    with pytest.raises(ValueError, match="非法"):
        mods.set_bombs(-1)
    with pytest.raises(ValueError, match="非法"):
        mods.set_lives(-1)


# ---- 无敌挂 ----
def test_god_mode_resets_invulnerability() -> None:
    game, mods = _mods()
    game._impl.player.invulnerability_timer = 0   # 先清零(测试摸内部)
    mods.god_mode()
    # 计时被重置, 且快照的 invulnerable 观测同步为真
    assert game._impl.player.invulnerability_timer == 999
    assert game.snapshot().player.invulnerable
    mods.set_invulnerability_time(timer=42)
    assert game._impl.player.invulnerability_timer == 42


# ---- 自定义弹幕 ----
def test_fire_ring_increases_bullet_count() -> None:
    _, mods = _mods()
    before = mods.bullet_count
    n = mods.fire_ring(192.0, 224.0, arms=8)
    assert n == 8
    assert mods.bullet_count == before + 8


def test_fire_burst_passthrough() -> None:
    _, mods = _mods()
    before = mods.bullet_count
    n = mods.fire(Burst(path=Vec2(192, 100), base_angle=math.pi / 2,
                        aim=Aim.RING_ABSOLUTE, arms=6, rings=2,
                        speed_a=2.0, speed_b=1.0, angle_step=0.0))
    assert n == 12   # arms * rings
    assert mods.bullet_count == before + 12


# ---- 能力位探测: 不满足可变协议的鸭子引擎报清晰中文错误, 不静默失败 ----
def _duck_game() -> Game:
    """只 read 得出只读面、缺全部可写成员的鸭子引擎。"""
    game = Game(seed=1)
    game._impl = SimpleNamespace(
        player=SimpleNamespace(pos=SimpleNamespace(x=0.0, y=0.0)),
        globals=SimpleNamespace(),
        bullets=SimpleNamespace(alive=lambda: []),
    )
    return game


def test_duck_engine_missing_capabilities() -> None:
    mods = ModApi(_duck_game())
    with pytest.raises(NotImplementedError, match="不支持无敌改写"):
        mods.god_mode()
    with pytest.raises(NotImplementedError, match="不支持火力改写"):
        mods.set_power(1)
    with pytest.raises(NotImplementedError, match="不支持分数改写"):
        mods.add_score(1)
    with pytest.raises(NotImplementedError, match="不支持自定义弹幕"):
        mods.fire_ring(0.0, 0.0)


def test_readonly_property_reported() -> None:
    # 成员存在但类上是只读 property(无 setter) → 同样清晰报错
    game = _duck_game()

    class _Duck(SimpleNamespace):
        @property
        def power(self) -> float:
            return 0.0

    game._impl = _Duck(player=game._impl.player, globals=game._impl.globals,
                       bullets=game._impl.bullets)
    with pytest.raises(NotImplementedError, match="只读"):
        ModApi(game).set_power(1)


# ---- 作品专属能力表(register_mods 维度收割, __getattr__ 查表分发) ----
def test_capability_dispatch_and_readback() -> None:
    """注册链验证: import touhou 后 th07 能力进表, 调用经 __getattr__ 分发。"""
    game, mods = _mods()
    assert mods.is_capabilities_exist("set_cherry")
    assert not mods.is_capabilities_exist("set_power")  # 通用核真方法不在表内
    assert not mods.is_capabilities_exist("nope")
    assert callable(mods.capabilities["set_cherry"])
    mods.set_cherry(50000)
    assert game.cherry == 50000          # 写入落到引擎, 只读面读回一致
    assert game._impl.globals.cherry == 50000


def test_unknown_capability_attribute_error() -> None:
    """未知名抛 AttributeError(hasattr/getattr 语义正确), 信息列已注册能力。"""
    _, mods = _mods()
    assert hasattr(mods, "set_cherry")
    assert not hasattr(mods, "nope")
    with pytest.raises(AttributeError, match="nope.*已注册能力"):
        mods.nope()


def test_available_lists_core_and_game_capabilities() -> None:
    """available() = 通用核(手写映射) + 作品能力(docstring 首行)。"""
    _, mods = _mods()
    avail = mods.available()
    assert "set_power" in avail and "god_mode" in avail      # 通用核
    assert avail["set_cherry"].startswith("樱点直改")          # 作品能力(docstring 首行)
    assert "border_break" in avail and "set_cherry_max" in avail


def test_capability_name_conflict_fails_fast() -> None:
    """作品能力与通用核真方法重名: ModApi 构造即 ValueError(不许静默被核覆盖)。"""

    class BadProvider:
        def __init__(self, game: Game) -> None:
            self._game = game

        def set_power(self, power: int) -> None:
            pass

    register_mods("th91")(BadProvider)
    game, _ = _mods()
    game.spec = get_game("th91")
    with pytest.raises(ValueError, match="重名"):
        ModApi(game)


def test_game_without_mods_dimension() -> None:
    """未注册 mods 维度的作品: capabilities 空表, 调作品能力给登记提示。"""
    game, _ = _mods()
    game.spec = GameSpec(name="thXX", ecl=None, anm=None,
                         hooks=GameHooks(), world=None)     # mods 缺省 None
    mods = ModApi(game)
    assert mods.capabilities == {}
    assert not mods.is_capabilities_exist("set_cherry")
    assert not hasattr(mods, "set_cherry")
    with pytest.raises(AttributeError, match="未注册 mod 能力.*register_mods"):
        mods.set_cherry(1)
    # 通用核不受影响
    mods.set_power(128)
    assert game.power == 128


# ---- th07 首批能力实效(樱点系/结界系) ----
def test_set_cherry_range_check() -> None:
    """set_cherry 域校验: 上限读引擎实况 cherryMax, 不写死魔法数。"""
    game, mods = _mods()
    cherry_max = game._impl.globals.cherry_max
    mods.set_cherry(cherry_max)
    assert game.cherry == cherry_max
    with pytest.raises(ValueError, match="超出"):
        mods.set_cherry(cherry_max + 1)
    with pytest.raises(ValueError, match="超出"):
        mods.set_cherry(-1)


def test_set_cherry_max() -> None:
    game, mods = _mods()
    g = game._impl.globals
    mods.set_cherry_max(g.cherry_start + 123456)
    assert g.cherry_max == g.cherry_start + 123456
    with pytest.raises(ValueError, match="超出"):
        mods.set_cherry_max(g.cherry_start - 1)


def test_border_break() -> None:
    """border_break: 有结界强制破裂(has_border→NONE), 无结界中文报错。"""
    game, mods = _mods()
    with pytest.raises(ValueError, match="没有结界可破"):
        mods.border_break()
    game._impl.border.ready_border()                       # 满樱信号 → READY
    assert game._impl.border.has_border == BorderState.READY
    mods.border_break()
    assert game._impl.border.has_border == BorderState.NONE
