"""观战模式(spectate)与 headless 录像(save_replay)测试。

- Game._from_impl: 包 live impl 的门面观测面
- GameApp 观战: 插桩 renderer + spectate policy, 断言 tick 输入来自策略;
  Esc 中止观战退出
- TouhouWorld 接线: callable auto_input → 观战契约 kwarg; 普通 Input 不变
- headless: callable auto_input 直接作流默认 policy; save_replay 录像
  round-trip + 同种子逐帧喂回的确定性复现
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest

from touhou.apis.basic import (
    Difficulty,
    Game,
    GamePhase,
    Input,
    ShotType,
    TouhouWorld,
)
from touhou.engine.render import FrameInput
from touhou.engine.replay import decode_input, load_replay
from touhou.paths import DEFAULT_DATA

pytestmark = pytest.mark.skipif(not DEFAULT_DATA.exists(), reason="需要真实 th07.dat")


# ---- Game._from_impl ----
def test_from_impl_wraps_live_impl() -> None:
    """包现存 impl: 不重复构造对局, 观测面(属性/快照)与自建门面一致。"""
    game = Game(seed=1)
    impl = game._impl
    facade = Game._from_impl(impl, game.spec, "th07")
    assert facade._impl is impl  # 同一个对局, 未重新构造
    assert facade.spec is game.spec and facade.game_name == "th07"
    for _ in range(300):
        game.step(Input(shoot=True))
    # 门面读的就是 live impl 的现况
    assert facade.frame == game.frame == 300
    assert facade.score == game.score and facade.lives == game.lives
    assert facade.phase == game.phase
    snap = facade.snapshot()
    assert snap.frame == game.frame
    assert 0 <= snap.player.x <= 384 and 0 <= snap.player.y <= 448


# ---- GameApp 观战(插桩 renderer + policy) ----
class ProbeableStubGame:
    """满足 Game._probe/phase 观测面的假游戏(_from_impl 能包, tick 记账)。"""

    def __init__(self, **kw):
        self.kw = kw
        self.frame = 0
        self.stage_no = 1
        self.lives = 3.0
        self.seed = 0x5EED
        self.globals = SimpleNamespace(
            deaths=0,
            bombs_used=0.0,
            spell_cards_captured=0,
            lives_remaining=3.0,
            score=0,
        )
        self.game_over = False
        self.cleared = False
        self.ending = None
        self.result = None
        self.stage_results = None
        self.boss = None
        self.player = SimpleNamespace(fire_time=0)  # 射击键沿检测日志用
        self.ticks: list[dict] = []

    def tick(self, **kw):  # noqa: D102
        self.ticks.append(kw)
        self.frame += 1


class StubRenderer:
    """插桩渲染后端: 按脚本喂 FrameInput, 渲染全 no-op。"""

    def __init__(self, script):
        self._script = script  # list[FrameInput], 用尽后循环最后一帧
        self._idx = 0

    def open(self, *, scale):  # noqa: D102
        pass

    def close(self):  # noqa: D102
        pass

    def resize(self, screen, scale):  # noqa: D102
        pass

    def present(self):  # noqa: D102
        pass

    def set_keymap(self, keymap):  # noqa: D102
        pass

    def play_menu_se(self, key):  # noqa: D102
        pass

    def begin_game(self, game, *, character):  # noqa: D102
        pass

    def render_game(self, game):  # noqa: D102
        pass

    def poll_input(self, *, capturing=False):  # noqa: D102
        inp = self._script[min(self._idx, len(self._script) - 1)]
        self._idx += 1
        return inp


def _spectate_app(tmp_path, policy, script):
    from touhou.games.th07.view import GameApp

    game = ProbeableStubGame()
    app = GameApp(
        lambda **kw: game,
        config_path=tmp_path / "config.json",
        renderer=StubRenderer(script),
        spectate=policy,
    )
    return app, game


def test_spectate_input_comes_from_policy(tmp_path) -> None:
    """观战: tick 收到的 keys/bomb 来自 policy 而非 poll_input。"""
    policy_calls = []

    def policy(game):
        policy_calls.append(game.frame)
        return Input(left=True, down=True)  # 与键盘脚本(右+射击)刻意不同

    script = [FrameInput(held=frozenset({"right", "shoot"}))] * 5 + [
        FrameInput(quit=True)
    ]
    app, game = _spectate_app(tmp_path, policy, script)
    app.run()
    assert len(game.ticks) >= 5
    assert len(policy_calls) == len(game.ticks)  # 每帧一次策略调用
    for kw in game.ticks:
        assert kw["keys"] == (True, False, False, True, False, False)
        assert kw["bomb"] is False
    assert app._screen.name == "PLAYING"  # 跳过标题直接进游戏


def test_spectate_esc_aborts(tmp_path) -> None:
    """观战中 Esc 直接退出(不弹暂停菜单, 不被关死在窗口里)。"""
    script = [FrameInput(), FrameInput(), FrameInput(esc=True)]
    app, game = _spectate_app(tmp_path, lambda g: Input(shoot=True), script)
    app.run()
    assert len(game.ticks) == 2  # 第 3 帧 Esc 中止, 不再 tick
    assert app._finished and not app._paused


def test_spectate_records_replay(tmp_path) -> None:
    """观战复用既有录制路径: _start_game 建的录像器记下策略输入帧。"""
    script = [FrameInput()] * 4 + [FrameInput(quit=True)]
    app, game = _spectate_app(tmp_path, lambda g: Input(shoot=True), script)
    app.run()
    assert app._recorder is not None
    assert app._recorder.frames == len(game.ticks)
    assert app._recorder.meta["seed"] == 0x5EED  # 构造注入的默认种子


# ---- TouhouWorld 接线(假 App 捕获构造实参) ----
class _CaptureApp:
    captured: dict = {}

    def __init__(self, make_game, **kw):
        type(self).captured = {"make_game": make_game, **kw}

    def run(self):  # noqa: D102
        pass


def _tw_with_capture_app(**kw) -> TouhouWorld:
    _CaptureApp.captured = {}
    tw = TouhouWorld(**kw)
    tw.spec = dataclasses.replace(tw.spec, app=_CaptureApp)
    return tw


def test_spectate_kwarg_and_make_game_override() -> None:
    """callable auto_input → App 收到 spectate; make_game 忽略 App 实参,
    角色/难度/残机/种子以 TouhouWorld 自身属性为准。"""
    policy = lambda g: Input.none()
    tw = _tw_with_capture_app(
        character=ShotType.MARISA_A,
        difficulty=Difficulty.HARD,
        lives=5,
        seed=42,
        headless=False,
        auto_input=policy,
    )
    tw.run()
    cap = _CaptureApp.captured
    assert cap["spectate"] is policy
    impl = cap["make_game"](difficulty=0, character=0)  # App 实参被忽略
    assert impl.difficulty == 2 and impl.character == 2
    assert impl.seed == 42
    assert impl.globals.lives_remaining == 5


def test_plain_input_auto_input_keeps_keyboard_path() -> None:
    """auto_input 是普通 Input(非 callable) → 不传 spectate, 键盘游玩不变;
    make_game 也不再覆写 App 实参。"""
    tw = _tw_with_capture_app(headless=False, auto_input=Input(shoot=True))
    tw.run()
    cap = _CaptureApp.captured
    assert "spectate" not in cap
    impl = cap["make_game"](difficulty=3, character=4)
    assert impl.difficulty == 3 and impl.character == 4


# ---- headless: callable auto_input 作流默认 policy ----
def test_headless_callable_auto_input_is_stream_policy() -> None:
    tw = TouhouWorld(
        headless=True,
        seed=1,
        difficulty=Difficulty.NORMAL,
        auto_input=lambda g: Input(left=True, shoot=True),
    )
    frames0 = tw.game.frame
    for ev in tw.run():  # 不显式设 policy: 流默认用 auto_input
        if tw.game.frame >= frames0 + 120:
            break
    assert tw.game.frame >= frames0 + 100
    # policy 生效的旁证: 一直按左, 自机一路左移
    assert tw.game.snapshot().player.x < 192


# ---- headless 录像: round-trip + 确定性复现 ----
def test_stream_save_replay_round_trip(tmp_path) -> None:
    tw = TouhouWorld(
        headless=True,
        seed=7,
        difficulty=Difficulty.NORMAL,
        lives=3,
        auto_input=lambda g: Input(shoot=True, advance=True),
    )
    stream = tw.run()
    for ev in stream:
        if tw.game.frame >= 300 or tw.game.phase != GamePhase.RUNNING:
            break
    path = stream.save_replay(tmp_path / "r.json")
    r = load_replay(path)
    meta = r["meta"]
    assert meta["difficulty"] == 1 and meta["character"] == 0
    assert meta["stage"] == 1 and meta["seed"] == 7
    assert meta["initial_lives"] == 3
    assert meta["frames"] == len(r["codes"]) == tw.game.frame

    # 确定性复现: 同种子新对局逐帧喂回输入, 终态一致
    g2 = Game(difficulty=Difficulty.NORMAL, character=ShotType.REIMU_A, seed=7, lives=3)
    for code in r["codes"]:
        keys, bomb, adv, skip = decode_input(code)
        g2._impl.tick(keys=keys, bomb=bomb, advance=adv, skip=skip)
    assert g2.frame == tw.game.frame
    assert g2.score == tw.game.score
    assert g2.lives == tw.game.lives


def test_save_replay_before_iteration_is_empty(tmp_path) -> None:
    """未迭代即 save_replay: 存 0 帧录像, meta 仍完整。"""
    tw = TouhouWorld(headless=True, seed=None, difficulty=Difficulty.EASY)
    path = tw.events.save_replay(tmp_path / "empty.json")
    r = load_replay(path)
    assert r["codes"] == []
    assert r["meta"]["seed"] == 0x5EED  # seed=None → impl 默认种子
    assert r["meta"]["difficulty"] == 0
