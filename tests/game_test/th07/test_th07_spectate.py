"""th07 GameApp 真实观战(插桩 renderer + spectate policy)与确定性复现。

通用观战契约(TouhouWorld 接线/录像 meta)用假作品在 tests/test_spectate.py
验证; 这里验证 th07 窗口 App 的观战行为: tick 输入来自策略、Esc 中止、
录像复用既有录制路径, 以及 headless 录像的同种子逐帧确定性复现。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from touhou.apis.basic import (
    Game,
    GamePhase,
    Input,
    TouhouWorld,
)
from touhou.engine.render import FrameInput
from touhou.engine.replay import decode_input, load_replay
from touhou.paths import DEFAULT_DATA

pytestmark = pytest.mark.skipif(not DEFAULT_DATA.exists(), reason="需要真实 th07.dat")


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


# ---- headless 录像: round-trip + 确定性复现 ----
def test_stream_save_replay_round_trip(tmp_path) -> None:
    tw = TouhouWorld(
        headless=True,
        seed=7,
        difficulty="Normal",
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
    g2 = Game(difficulty="Normal", character="ReimuA", seed=7, lives=3)
    for code in r["codes"]:
        keys, bomb, adv, skip = decode_input(code)
        g2._impl.tick(keys=keys, bomb=bomb, advance=adv, skip=skip)
    assert g2.frame == tw.game.frame
    assert g2.score == tw.game.score
    assert g2.lives == tw.game.lives
