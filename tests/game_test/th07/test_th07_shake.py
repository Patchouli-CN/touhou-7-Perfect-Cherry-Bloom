"""画面震动(震屏)测试: 引擎透出 (bomb.py/ecl_host.py/impl.py) + view 衰减 (shake_view.py)。

数值权威来源: th07/src/th07/ScreenEffect.cpp (OnUpdateScreenShake) 与
BombData.cpp / EnemyEclInstr.cpp 的 BombEffects::RegisterChain(1, ...) 注册点。
"""

from __future__ import annotations

import random
import sys

import pytest

sys.path.insert(0, r"D:\python_play\Touhou08")

from touhou.games.th07.bomb import (  # noqa: E402
    CHAR_MARISA_A,
    CHAR_MARISA_B,
    CHAR_REIMU_A,
    CHAR_REIMU_B,
    CHAR_SAKUYA_A,
    CHAR_SAKUYA_B,
    DIFF_NORMAL,
    Bomb,
    BombContext,
)
from touhou.utils import Vec2  # noqa: E402
from touhou.engine.view.shake_view import ScreenShake  # noqa: E402
from tests.game_test.th07.test_th07_exins import _ex_machine, _fire  # noqa: E402

CTX = BombContext(
    player_pos=Vec2(100.0, 300.0),
    cherry=101000.0,
    cherry_start=1000.0,
    difficulty=DIFF_NORMAL,
)


def _run(character: int, focus: bool, frames: int) -> list[list[tuple]]:
    """跑一次炸弹, 返回每帧 bomb.shakes 的快照列表。"""
    b = Bomb(character=character)
    b.start(focus=focus, ctx=CTX)
    out = [list(b.shakes)]
    for _ in range(frames):
        b.shakes.clear()
        b.tick(CTX)
        out.append(list(b.shakes))
    return out


# ---- BombData.cpp 各注册点 (type=1, duration, amp_start, amp_end) ----


def test_shake_reimu_b_unfocused() -> None:
    """灵梦B 非集中: 首帧 (60,2,6) (BombData.cpp:559) + timer==60 (80,20,0) (:566)。"""
    frames = _run(CHAR_REIMU_B, False, 70)
    assert frames[0] == [(60, 2, 6)]
    assert frames[60] == [(80, 20, 0)]


def test_shake_reimu_b_focused() -> None:
    """灵梦B 集中: 首帧 (60,2,6) (:654) + timer==60 (80,20,0) (:666)。"""
    frames = _run(CHAR_REIMU_B, True, 70)
    assert frames[0] == [(60, 2, 6)]
    assert frames[60] == [(80, 20, 0)]


def test_shake_marisa_a_unfocused_first_frame() -> None:
    """魔理沙A 非集中: 首帧 (120,4,1) (:738)。"""
    assert _run(CHAR_MARISA_A, False, 1)[0] == [(120, 4, 1)]


def test_shake_marisa_a_focused_per_star() -> None:
    """魔理沙A 集中: 每颗星出生帧 (120,4,1) (:867, timer%6==0 且 i<24)。"""
    frames = _run(CHAR_MARISA_A, True, 20)
    assert frames[0] == [(120, 4, 1)]  # 首帧即放 i=0
    assert frames[6] == [(120, 4, 1)]
    assert frames[12] == [(120, 4, 1)]
    assert frames[1] == []  # 非 6 倍数帧不震


def test_shake_marisa_b_unfocused() -> None:
    """魔理沙B 非集中: timer==20 (60,1,7) (:1047) / timer==80 (100,24,0) (:1051)。"""
    frames = _run(CHAR_MARISA_B, False, 90)
    assert frames[20] == [(60, 1, 7)]
    assert frames[80] == [(100, 24, 0)]


def test_shake_marisa_b_focused() -> None:
    """魔理沙B 集中: timer==60 (60,1,7) (:1126) / timer==120 (200,24,0) (:1130)。"""
    frames = _run(CHAR_MARISA_B, True, 130)
    assert frames[60] == [(60, 1, 7)]
    assert frames[120] == [(200, 24, 0)]


def test_shake_sakuya_a_focused_first_frame() -> None:
    """咲夜A 集中: 首帧 (120,4,1) (:1388); 非集中 C++ 无震屏注册点。"""
    assert _run(CHAR_SAKUYA_A, True, 1)[0] == [(120, 4, 1)]
    assert not any(_run(CHAR_SAKUYA_A, False, 140))


def test_shake_sakuya_b_time_stop() -> None:
    """咲夜B 停时: timer==40 (60,1,7) / timer==100 (70,24,0) (:1559/:1563, :1672/:1678)。"""
    for focus in (False, True):
        frames = _run(CHAR_SAKUYA_B, focus, 110)
        assert frames[40] == [(60, 1, 7)]
        assert frames[100] == [(70, 24, 0)]


def test_shake_reimu_a_orb_explosion() -> None:
    """灵梦A 非集中: 珠爆开 (speed < -10) 帧 (16,8,0) (BombData.cpp:218)。"""
    frames = _run(CHAR_REIMU_A, False, 90)
    # 首颗珠 timer=8 出生, 速度 15 每帧 -0.4 → 约 63 帧后爆开
    assert any((16, 8, 0) in f for f in frames)


# ---- ECL ExIns 注册点 (EnemyEclInstr.cpp) ----


def test_shake_exins_effect1e_accel() -> None:
    """ExIns idx9 effect1e 加速: (80,8,0) (EnemyEclInstr.cpp:551)。"""
    m, host, _world = _ex_machine(9)
    m.step()
    assert host.shake_events == [(80, 8, 0)]


def test_shake_exins_alice_curve_and_turn() -> None:
    """ExIns idx1 爱丽丝曲弹 (30,12,0) (:73) / idx2 sel==0 弹转化 (32,12,0) (:139)。"""
    m, host, _world = _ex_machine(1)
    _fire(host, (192.0, 200.0), sprite_offset=2)
    m.step()
    assert host.shake_events == [(30, 12, 0)]
    m2, host2, _w2 = _ex_machine(2, 0)
    _fire(host2, (192.0, 200.0), sprite_offset=2)
    m2.step()
    assert host2.shake_events == [(32, 12, 0)]


# ---- view 侧衰减 (ScreenEffect.cpp:249-293 OnUpdateScreenShake) ----


def test_screen_shake_decay_linear() -> None:
    """振幅从 amp_start 线性插值到 amp_end (:267-269), 偏移 ∈ {0,±amp}。"""
    sh = ScreenShake(random.Random(42))
    sh.register(60, 2, 6)  # 灵梦B 首帧: 渐强
    amps = set()
    for _ in range(59):
        dx, dy = sh.tick()
        amps.add(max(abs(dx), abs(dy)))
        assert abs(dx) <= 6 and abs(dy) <= 6
    assert sh.active
    sh.tick()
    assert not sh.active  # timer >= duration 移除 (:262-265)


def test_screen_shake_offsets_are_pick_of_three() -> None:
    """每轴偏移恒为 {0, +amp, -amp} 之一 (:270-291)。"""
    sh = ScreenShake(random.Random(7))
    sh.register(100, 24, 0)  # 魔理沙B 大震: 渐弱
    seen = set()
    for _ in range(99):
        dx, dy = sh.tick()
        seen.add(dx)
        seen.add(dy)
        for v in (dx, dy):
            assert v == 0 or abs(v) <= 24
    assert len(seen) > 1  # 确实有抖动


def test_screen_shake_latest_wins() -> None:
    """多个并存: 后注册者覆写偏移 (C++ 各链元素依次写 g_AnmManager->offset)。"""
    sh = ScreenShake(random.Random(1))
    sh.register(100, 24, 0)
    sh.register(16, 8, 0)
    for _ in range(15):
        dx, dy = sh.tick()
        assert abs(dx) <= 8 and abs(dy) <= 8  # 生效的是 (16,8,0)


# ---- impl 帧末快照 ----


def test_impl_frame_shakes_snapshot() -> None:
    """bomb 触发帧的震屏事件收进 game.frame_shakes (引擎最小透出)。"""
    from touhou.paths import DEFAULT_DATA

    if not DEFAULT_DATA.exists():
        pytest.skip("th07.dat 不在默认路径")
    from touhou.games.th07.world import PerfectCherryBloom

    g = PerfectCherryBloom(data_path=DEFAULT_DATA, character=1)  # 灵梦B
    g.tick(keys=(False,) * 6, bomb=True)
    assert (60, 2, 6) in g.frame_shakes


# ---- 渲染后端合成路径 (整帧位移 blit + 快照去重) ----


class _ShakeStubGame:
    """render_game 最小夹具: 无贴图视图(None 走 fill 兜底) + frame_shakes。"""

    def __init__(self) -> None:
        self.frame = 1
        self.frame_shakes: list[tuple] = [(60, 2, 6)]
        self.stage_no = 1
        self.msg_vm = None
        self.stage_results = None

    def tick(self, **kw):  # noqa: D102
        pass


def test_app_render_game_consumes_shakes_once() -> None:
    """render_game: frame_shakes 注册进 ScreenShake; 同帧重复渲染 (暂停冻结)
    不重复注册 (按 (id(game), frame) 去重)。"""
    pygame = pytest.importorskip("pygame")
    import os

    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    pygame.init()
    from touhou.games.th07.view import GameApp

    app = GameApp(lambda **kw: _ShakeStubGame())
    game = _ShakeStubGame()
    app._renderer.render_game(game)
    assert app._renderer._shake.active
    app._renderer.render_game(game)  # 同帧再次 blit(暂停路径): 不得重复注册
    n_shakes = len(app._renderer._shake._active)
    assert n_shakes == 1
