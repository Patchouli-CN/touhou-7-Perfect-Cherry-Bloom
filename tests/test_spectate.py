"""观战模式(spectate)与 headless 录像(save_replay)的通用契约测试。

通用层: 只用假作品 "test00"(tests/conftest.py 注册的桩对局 + 捕获型假
App)验证 TouhouWorld 接线/门面观测面/录像 meta, 不 import games.*;
th07 GameApp 真实观战(插桩 renderer)与同种子确定性复现见
game_test/th07/test_th07_spectate.py。
"""

from __future__ import annotations

import dataclasses

from touhou.apis.basic import (
    Game,
    Input,
    TouhouWorld,
)
from touhou.engine.replay import load_replay

from tests.conftest import FAKE_GAME


def _fake_world(**kw) -> TouhouWorld:
    kw.setdefault("game", FAKE_GAME)
    kw.setdefault("character", "TestA")
    return TouhouWorld(**kw)


# ---- Game._from_impl ----
def test_from_impl_wraps_live_impl() -> None:
    """包现存 impl: 不重复构造对局, 观测面(属性/快照)与自建门面一致。"""
    game = Game(game=FAKE_GAME, character="TestA", difficulty="Normal", seed=1)
    impl = game._impl
    facade = Game._from_impl(impl, game.spec, FAKE_GAME)
    assert facade._impl is impl  # 同一个对局, 未重新构造
    assert facade.spec is game.spec and facade.game_name == FAKE_GAME
    for _ in range(300):
        game.step(Input(shoot=True))
    # 门面读的就是 live impl 的现况
    assert facade.frame == game.frame == 300
    assert facade.score == game.score and facade.lives == game.lives
    assert facade.phase == game.phase
    snap = facade.snapshot()
    assert snap.frame == game.frame
    assert 0 <= snap.player.x <= 384 and 0 <= snap.player.y <= 448


# ---- TouhouWorld 接线(假 App 捕获构造实参) ----
class _CaptureApp:
    captured: dict = {}

    def __init__(self, make_game, **kw):
        type(self).captured = {"make_game": make_game, **kw}

    def run(self):  # noqa: D102
        pass


def _tw_with_capture_app(**kw) -> TouhouWorld:
    _CaptureApp.captured = {}
    tw = _fake_world(**kw)
    tw.spec = dataclasses.replace(tw.spec, app=_CaptureApp)
    return tw


def test_spectate_kwarg_and_make_game_override() -> None:
    """callable auto_input → App 收到 spectate; make_game 忽略 App 实参,
    角色/难度/残机/种子以 TouhouWorld 自身属性为准。"""
    policy = lambda g: Input.none()
    tw = _tw_with_capture_app(
        character="TestB",
        difficulty="Hard",
        lives=5,
        seed=42,
        headless=False,
        auto_input=policy,
    )
    tw.run()
    cap = _CaptureApp.captured
    assert cap["spectate"] is policy
    impl = cap["make_game"](difficulty=0, character=0)  # App 实参被忽略
    assert impl.difficulty == 2 and impl.character == 1  # Hard=2, TestB=1
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
    tw = _fake_world(
        headless=True,
        seed=1,
        difficulty="Normal",
        auto_input=lambda g: Input(left=True, shoot=True),
    )
    frames0 = tw.game.frame
    for ev in tw.run():  # 不显式设 policy: 流默认用 auto_input
        if tw.game.frame >= frames0 + 120:
            break
    assert tw.game.frame >= frames0 + 100
    # policy 生效的旁证: 一直按左, 自机一路左移
    assert tw.game.snapshot().player.x < 192


# ---- headless 录像: meta 完整(th07 同种子确定性复现见 game_test/th07) ----
def test_stream_save_replay_meta(tmp_path) -> None:
    tw = _fake_world(
        headless=True,
        seed=7,
        difficulty="Normal",
        lives=3,
        auto_input=lambda g: Input(shoot=True, advance=True),
    )
    stream = tw.run()
    for ev in stream:
        if tw.game.frame >= 300:
            break
    path = stream.save_replay(tmp_path / "r.json")
    r = load_replay(path)
    meta = r["meta"]
    assert meta["difficulty"] == 1 and meta["character"] == 0  # Normal=1, TestA=0
    assert meta["stage"] == 1 and meta["seed"] == 7
    assert meta["initial_lives"] == 3
    assert meta["frames"] == len(r["codes"]) == tw.game.frame


def test_save_replay_before_iteration_is_empty(tmp_path) -> None:
    """未迭代即 save_replay: 存 0 帧录像, meta 仍完整。"""
    tw = _fake_world(headless=True, seed=None, difficulty="Easy")
    path = tw.events.save_replay(tmp_path / "empty.json")
    r = load_replay(path)
    assert r["codes"] == []
    assert r["meta"]["seed"] == 0x5EED  # seed=None → impl 默认种子
    assert r["meta"]["difficulty"] == 0  # Easy=0
