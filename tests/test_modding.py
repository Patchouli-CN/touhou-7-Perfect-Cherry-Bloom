"""公共魔改 API(touhou/apis/modding.py)门面行为测试 —— 分层命名空间结构。

夹具模式照 test_api.py: 真实 th07.dat headless 开局; 引擎内部状态读回验证
时允许摸 game._impl(测试本就需要校验 ModApi 的写入落到了引擎里)。
"""
from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from touhou.apis.basic import Difficulty, Game, Input, ShotType
from touhou.apis.modding import Aim, Burst, GuiMods, ModApi
from touhou.engine.render import overlay
from touhou.games.th07.bomb import BorderState
from touhou.games.th07.boss import Boss
from touhou.paths import DEFAULT_DATA
from touhou.registry import (
    GameHooks,
    GameSpec,
    get_game,
    mod_namespace,
    register_mods,
)
from touhou.utils import Vec2

pytestmark = pytest.mark.skipif(not DEFAULT_DATA.exists(),
                                reason="需要真实 th07.dat")


def _mods(seed: int = 1) -> tuple[Game, ModApi]:
    game = Game(character=ShotType.REIMU_A, difficulty=Difficulty.NORMAL,
                seed=seed)
    return game, ModApi(game)


# ---- player 命名空间: 资源 setter(写入后经 Game 只读属性读回验证) ----
def test_resource_setters() -> None:
    game, mods = _mods()
    mods.player.set_power(128)
    mods.player.set_bombs(8)
    mods.player.set_lives(5)
    mods.score.add(10000)
    assert game.power == 128
    assert game.bombs == 8
    assert game.lives == 5
    assert game.score == 10000
    # 满火力上限来自注册表数值表(th07 = 128), 不是 modding 写死的常量
    assert mods.player.full_power == 128
    # 自机坐标观测与 Game 只读面一致
    assert mods.player.pos == game.player_pos


def test_set_power_range_check() -> None:
    _, mods = _mods()
    with pytest.raises(ValueError, match="超出"):
        mods.player.set_power(mods.player.full_power + 1)
    with pytest.raises(ValueError, match="超出"):
        mods.player.set_power(-1)


def test_set_bombs_lives_range_check() -> None:
    _, mods = _mods()
    with pytest.raises(ValueError, match="非法"):
        mods.player.set_bombs(-1)
    with pytest.raises(ValueError, match="非法"):
        mods.player.set_lives(-1)


# ---- 无敌挂 ----
def test_god_mode_resets_invulnerability() -> None:
    game, mods = _mods()
    game._impl.player.invulnerability_timer = 0   # 先清零(测试摸内部)
    mods.player.god_mode()
    # 计时被重置, 且快照的 invulnerable 观测同步为真
    assert game._impl.player.invulnerability_timer == 999
    assert game.snapshot().player.invulnerable
    mods.player.set_invulnerability_time(timer=42)
    assert game._impl.player.invulnerability_timer == 42


# ---- bullets 命名空间: 自定义弹幕/清屏 ----
def test_fire_ring_increases_bullet_count() -> None:
    _, mods = _mods()
    before = mods.bullets.count
    n = mods.bullets.fire_ring(192.0, 224.0, arms=8)
    assert n == 8
    assert mods.bullets.count == before + 8


def test_fire_burst_passthrough() -> None:
    _, mods = _mods()
    before = mods.bullets.count
    n = mods.bullets.fire(Burst(path=Vec2(192, 100), base_angle=math.pi / 2,
                                aim=Aim.RING_ABSOLUTE, arms=6, rings=2,
                                speed_a=2.0, speed_b=1.0, angle_step=0.0))
    assert n == 12   # arms * rings
    assert mods.bullets.count == before + 12


def test_bullets_clear() -> None:
    """clear(): 清屏全部敌弹(直清, 不动引擎清弹记账)。"""
    _, mods = _mods()
    mods.bullets.fire_ring(192.0, 224.0, arms=8)
    assert mods.bullets.count > 0
    mods.bullets.clear()
    assert mods.bullets.count == 0


# ---- boss 命名空间 ----
def test_boss_absent_at_stage_start() -> None:
    """开局无 Boss: exists=False, 写操作报中文错(先判 exists 再写)。"""
    _, mods = _mods()
    assert not mods.boss.exists
    with pytest.raises(ValueError, match="没有 Boss"):
        mods.boss.set_life(100)
    with pytest.raises(ValueError, match="没有 Boss"):
        mods.boss.set_pos(192.0, 100.0)


def test_boss_setters_on_real_boss() -> None:
    """真实 Boss 对象(th07 Boss 是 msgspec.Struct): 生命/位置直改读回一致。"""
    game, mods = _mods()
    game._impl.boss = Boss(name="test")             # 测试摸内部塞一个 Boss
    assert mods.boss.exists
    mods.boss.set_life(30)
    assert game._impl.boss.life == 30.0
    assert game._impl.boss.max_life == 0.0          # 不改上限
    mods.boss.set_pos(192.0, 96.0)
    assert game._impl.boss.pos == Vec2(192.0, 96.0)


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
        mods.player.god_mode()
    with pytest.raises(NotImplementedError, match="不支持火力改写"):
        mods.player.set_power(1)
    with pytest.raises(NotImplementedError, match="不支持分数改写"):
        mods.score.add(1)
    with pytest.raises(NotImplementedError, match="不支持自定义弹幕"):
        mods.bullets.fire_ring(0.0, 0.0)
    with pytest.raises(NotImplementedError, match="不支持清屏"):
        mods.bullets.clear()
    # 鸭子引擎连 boss 成员都没有: exists 探测为 False(不炸), 写操作报"没有 Boss"
    assert not mods.boss.exists
    with pytest.raises(ValueError, match="没有 Boss"):
        mods.boss.set_life(1)


def test_duck_boss_missing_writable_members() -> None:
    """有 boss 槽但对象缺可写成员: 逐个能力位探测报缺失成员名。"""
    game = _duck_game()
    game._impl.boss = SimpleNamespace()             # 空壳 Boss(无 life/pos)
    mods = ModApi(game)
    assert mods.boss.exists
    with pytest.raises(NotImplementedError, match="不支持Boss 生命改写.*life"):
        mods.boss.set_life(1)
    with pytest.raises(NotImplementedError, match="不支持Boss 位置改写.*pos"):
        mods.boss.set_pos(0.0, 0.0)


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
        ModApi(game).player.set_power(1)


# ---- 作品能力的命名空间归属(@mod_namespace 声明, 收割进分层结构) ----
def test_capability_namespaces_and_readback() -> None:
    """注册链验证: set_cherry/set_cherry_max → api.player; border_break →
    api.border(作品注册的整棵新命名空间)。"""
    game, mods = _mods()
    assert mods.is_capabilities_exist("player.set_cherry")
    assert mods.is_capabilities_exist("player.set_cherry_max")
    assert mods.is_capabilities_exist("border.border_break")
    assert mods.is_capabilities_exist("player.set_power")   # 通用核同口径
    assert not mods.is_capabilities_exist("player.nope")
    assert not mods.is_capabilities_exist("set_cherry")     # 裸名(无点号)恒 False
    assert not mods.is_capabilities_exist("nope.nope")
    assert callable(mods.player.set_cherry)
    assert callable(mods.border.border_break)
    mods.player.set_cherry(50000)
    assert game.cherry == 50000          # 写入落到引擎, 只读面读回一致
    assert game._impl.globals.cherry == 50000


def test_unknown_attribute_error() -> None:
    """未知名抛 AttributeError(hasattr/getattr 语义正确), 信息列命名空间清单。"""
    _, mods = _mods()
    assert hasattr(mods, "player") and hasattr(mods, "border")
    assert not hasattr(mods, "set_cherry")   # 平铺入口已删, 走命名空间
    assert not hasattr(mods, "nope")
    with pytest.raises(AttributeError, match="没有成员 'nope'.*命名空间"):
        mods.nope()
    with pytest.raises(AttributeError, match="没有成员 'set_cherry'"):
        mods.set_cherry(1)


def test_available_layered_listing() -> None:
    """available() 分层: 命名空间 → {能力名: 一句话说明}(通用核手写映射 +
    作品能力 docstring 首行, 按归属并入)。"""
    _, mods = _mods()
    avail = mods.available()
    # 通用核五个命名空间
    for ns in ("player", "boss", "bullets", "score", "gui"):
        assert ns in avail
    assert "god_mode" in avail["player"] and "set_power" in avail["player"]
    assert "clear" in avail["bullets"] and "add" in avail["score"]
    assert "line" in avail["gui"]
    # 作品能力按归属并入: 樱点系进 player, 结界系自成 border
    assert avail["player"]["set_cherry"].startswith("樱点直改")
    assert "set_cherry_max" in avail["player"]
    assert avail["border"]["border_break"].startswith("强制破裂")


def test_capability_name_conflict_fails_fast() -> None:
    """作品能力与目标命名空间既有成员重名: ModApi 构造即 ValueError。"""

    class BadProvider:
        def __init__(self, game: Game) -> None:
            self._game = game

        @mod_namespace("player")
        def set_power(self, power: int) -> None:
            pass

    register_mods("th91")(BadProvider)
    game, _ = _mods()
    game.spec = get_game("th91")
    with pytest.raises(ValueError, match="重名"):
        ModApi(game)


def test_namespace_name_conflict_fails_fast() -> None:
    """作品新命名空间名与 ModApi 既有成员重名: 构造即 ValueError。"""

    class BadProvider:
        def __init__(self, game: Game) -> None:
            self._game = game

        @mod_namespace("available")
        def whatever(self) -> None:
            pass

    register_mods("th92")(BadProvider)
    game, _ = _mods()
    game.spec = get_game("th92")
    with pytest.raises(ValueError, match="命名空间.*重名"):
        ModApi(game)


def test_undeclared_capability_lands_in_game_namespace() -> None:
    """未声明归属的作品能力: 默认挂到以作品名命名的命名空间(api.th93)。"""

    class PlainProvider:
        def __init__(self, game: Game) -> None:
            self._game = game

        def frobnicate(self) -> int:
            """作品小工具。"""
            return 42

    register_mods("th93")(PlainProvider)
    game, _ = _mods()
    game.spec = get_game("th93")
    mods = ModApi(game)
    assert mods.th93.frobnicate() == 42
    assert mods.is_capabilities_exist("th93.frobnicate")
    assert mods.available()["th93"]["frobnicate"].startswith("作品小工具")


def test_game_without_mods_dimension() -> None:
    """未注册 mods 维度的作品: 只有通用核命名空间, 调作品能力给登记提示。"""
    game, _ = _mods()
    game.spec = GameSpec(name="thXX", ecl=None, anm=None,
                         hooks=GameHooks(), world=None)     # mods 缺省 None
    mods = ModApi(game)
    assert not hasattr(mods, "border")
    assert not mods.is_capabilities_exist("player.set_cherry")
    assert mods.is_capabilities_exist("player.set_power")   # 通用核不受影响
    with pytest.raises(AttributeError, match="未注册 mod 能力.*register_mods"):
        mods.set_cherry(1)
    mods.player.set_power(128)
    assert game.power == 128
    assert "set_cherry" not in mods.available().get("player", {})


# ---- gui 覆盖层(立即模式; 产消语义见 engine/render/overlay.py) ----
def test_gui_commands_buffered_and_drained() -> None:
    """ModApi.gui 推入命令到汇聚点: 缓冲语义正确, drain 取走即清空。"""
    overlay.SINK.drain()                     # 清掉其他用例的残留(进程级单例)
    _, mods = _mods()
    mods.gui.line(0, 0, 384, 448, color=(255, 0, 0), width=2)
    mods.gui.circle(192, 224, 32)
    mods.gui.polyline([(0, 0), (10, 10), (20, 0)], closed=True)
    mods.gui.text(8, 8, "安全区", size=24)
    assert len(overlay.SINK) == 4
    cmds = overlay.SINK.drain()
    assert len(overlay.SINK) == 0            # 消费即清空(命令只活一帧)
    assert isinstance(cmds[0], overlay.OverlayLine)
    assert cmds[0].x2 == 384 and cmds[0].color == (255, 0, 0) \
        and cmds[0].width == 2
    assert isinstance(cmds[1], overlay.OverlayCircle)
    assert isinstance(cmds[2], overlay.OverlayPolyline)
    assert cmds[2].points == ((0.0, 0.0), (10.0, 10.0), (20.0, 0.0)) \
        and cmds[2].closed
    assert isinstance(cmds[3], overlay.OverlayText)
    assert cmds[3].content == "安全区" and cmds[3].size == 24


def test_gui_headless_is_silent_noop() -> None:
    """headless 无消费者: push 静默丢弃(缓冲有上限), 不报错不炸。"""
    sink = overlay.OverlaySink(capacity=3)
    gui = GuiMods(sink)
    for i in range(10):
        gui.line(0, 0, i, i)
    assert len(sink) == 3                    # 上限兜底, 丢弃最旧
    cmds = sink.drain()
    assert [c.x2 for c in cmds] == [7.0, 8.0, 9.0]


# ---- th07 首批能力实效(樱点系/结界系, 新命名空间路径) ----
def test_set_cherry_range_check() -> None:
    """set_cherry 域校验: 上限读引擎实况 cherryMax, 不写死魔法数。"""
    game, mods = _mods()
    cherry_max = game._impl.globals.cherry_max
    mods.player.set_cherry(cherry_max)
    assert game.cherry == cherry_max
    with pytest.raises(ValueError, match="超出"):
        mods.player.set_cherry(cherry_max + 1)
    with pytest.raises(ValueError, match="超出"):
        mods.player.set_cherry(-1)


def test_set_cherry_max() -> None:
    game, mods = _mods()
    g = game._impl.globals
    mods.player.set_cherry_max(g.cherry_start + 123456)
    assert g.cherry_max == g.cherry_start + 123456
    with pytest.raises(ValueError, match="超出"):
        mods.player.set_cherry_max(g.cherry_start - 1)


def test_border_break() -> None:
    """border_break: 有结界强制破裂(has_border→NONE), 无结界中文报错。"""
    game, mods = _mods()
    with pytest.raises(ValueError, match="没有结界可破"):
        mods.border.border_break()
    game._impl.border.ready_border()                       # 满樱信号 → READY
    assert game._impl.border.has_border == BorderState.READY
    mods.border.border_break()
    assert game._impl.border.has_border == BorderState.NONE
