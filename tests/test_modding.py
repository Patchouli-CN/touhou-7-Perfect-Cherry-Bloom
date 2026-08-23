"""公共魔改 API(touhou/apis/modding.py)门面行为测试。

夹具模式照 test_api.py: 真实 th07.dat headless 开局; 引擎内部状态读回验证
时允许摸 game._impl(测试本就需要校验 Mods 的写入落到了引擎里)。
"""
from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from touhou.apis.basic import Difficulty, Game, Input, ShotType
from touhou.apis.modding import Aim, Burst, Mods
from touhou.paths import DEFAULT_DATA
from touhou.utils import Vec2

pytestmark = pytest.mark.skipif(not DEFAULT_DATA.exists(),
                                reason="需要真实 th07.dat")


def _mods(seed: int = 1) -> tuple[Game, Mods]:
    game = Game(character=ShotType.REIMU_A, difficulty=Difficulty.NORMAL,
                seed=seed)
    return game, Mods(game)


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
    mods.god_mode(timer=42)
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
    mods = Mods(_duck_game())
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
        Mods(game).set_power(1)
