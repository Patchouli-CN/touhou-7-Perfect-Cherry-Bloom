"""th07 真实引擎的门面行为测试 —— 对话相位/符卡事件/结界总线/demo 弹幕。

通用契约(事件 diff/快照形状/数组列义)用假作品在 tests/test_api.py 验证;
这里验证的是 th07 实现确实兑现契约(需要真实 th07.dat)。
"""

from __future__ import annotations

import pytest

from touhou.apis.basic import (
    Game,
    GameEventKind,
    GamePhase,
    Input,
)
from touhou.paths import DEFAULT_DATA, ENV_DATA

pytestmark = pytest.mark.skipif(not DEFAULT_DATA.exists(), reason="需要真实 th07.dat")


def _run(game: Game, frames: int, inp: Input = Input.none()) -> list:
    out: list = []
    for _ in range(frames):
        out += game.step(inp)
    return out


def test_dialog_phase_detected() -> None:
    # stage1 对话(msg)在关卡时间轴上出现; 推进期间保持 DIALOG 相位
    game = Game(seed=1)
    game._impl.globals.lives_remaining = 99  # 站桩防 GameOver 干扰
    for _ in range(6500):
        game.step(Input(shoot=True))
        if game.phase == GamePhase.DIALOG:
            return
    pytest.fail("6500 帧内未检测到 DIALOG 相位")


# ---- 事件映射(演示 Boss 路径, 同引擎测试的用法) ----
def test_spellcard_events() -> None:
    game = Game(seed=1)
    _run(game, 200, Input(shoot=True))
    game._impl._spawn_demo_boss()
    events = game.step(Input(shoot=True))
    kinds = [e.kind for e in events]
    assert GameEventKind.SPELLCARD_BEGIN in kinds
    name = next(e.name for e in events if e.kind == GameEventKind.SPELLCARD_BEGIN)
    # 击破(未用 Bomb/未死亡 → 捕获)
    game._impl.boss.life = 0
    events = game.step(Input(shoot=True))
    captured = [e for e in events if e.kind == GameEventKind.SPELLCARD_CAPTURED]
    assert captured and captured[0].name == name


def test_bomb_and_extend_events() -> None:
    game = Game(seed=1)
    _run(game, 200, Input(shoot=True))  # 等出生无敌结束
    events = game.step(Input(bomb=True))
    assert GameEventKind.BOMB_START in [e.kind for e in events]
    # 残机增加 → EXTEND(引擎侧只有奖残会让残机变多)
    game._impl.globals.lives_remaining += 1
    events = game.step()
    assert GameEventKind.EXTEND in [e.kind for e in events]


def test_death_and_game_over_events() -> None:
    game = Game(seed=1)
    _run(game, 200, Input(shoot=True))
    game._impl.globals.lives_remaining = 0
    game._impl.player.die()
    events = _run(game, 600)
    kinds = [e.kind for e in events]
    assert GameEventKind.PLAYER_DEATH in kinds
    assert GameEventKind.GAME_OVER in kinds
    # Extra/Phantasm 以外的难度无残机 → 续关可用(冻结), phase=GAME_OVER
    assert game.phase in (GamePhase.GAME_OVER, GamePhase.RESULT)


# ---- 作品专属事件(th07 结界)经 EventBus 汇入 step 事件流 ----
def test_border_events_via_event_bus() -> None:
    from touhou.engine.player_base import PlayerState

    game = Game(seed=1)
    _run(game, 200, Input(shoot=True))  # 出生无敌结束, 玩家 ALIVE
    game._impl.player.state = PlayerState.ALIVE  # 出生无敌态不参与结界激活择时
    # 满樱信号 → 结界 READY(等价 mods 拉满 cherryPlus 的引擎入口)
    game._impl.border.ready_border()
    events = game.step(Input(shoot=True))  # 帧内自动激活 → border_start
    kinds = [e.kind for e in events]
    assert "border_start" in kinds
    ev = next(e for e in events if e.kind == "border_start")
    assert ev.frame == game.frame
    # 主动破(bomb 键同入口) → 下一次 step 收到 border_break
    game._impl._break_border(by_bomb_key=True)
    events = game.step(Input(shoot=True))
    kinds = [e.kind for e in events]
    assert "border_break" in kinds
    # READY 未激活时的破(死亡保命路径)不发事件(事件语义 = ACTIVE→破)
    game._impl.border.ready_border()
    game._impl._break_border()  # was_active=False → 不发布
    events = game.step(Input(shoot=True))
    assert "border_break" not in [e.kind for e in events]


# ---- 快照 ----
def test_snapshot_after_boss() -> None:
    game = Game(seed=1)
    _run(game, 200, Input(shoot=True))
    game._impl._spawn_demo_boss()
    game.step()
    snap = game.snapshot()
    assert snap.boss is not None and snap.boss.spellcard_active
    assert snap.boss.max_life == 600


# ---- 环境变量驱动资源路径 ----
def test_env_var_drives_game(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(ENV_DATA, str(tmp_path / "nonexistent.dat"))
    with pytest.raises(OSError):
        Game()  # 环境变量指向不存在路径 → 开包失败
    monkeypatch.setenv(ENV_DATA, str(DEFAULT_DATA))
    assert Game().frame == 0  # 指回真实数据则正常


# ---- 判定半径观测面 + numpy 快路径(th07 实弹对照) ----
def test_snapshot_hitbox_matches_engine() -> None:
    from touhou.utils import Vec2

    game = Game(seed=1)
    _run(game, 200, Input(shoot=True))
    game._impl.bullets.spawn_demo_wave(Vec2(192, 100))  # 确定性造弹(环+扇)
    game.step(Input(shoot=True))
    snap = game.snapshot()
    assert snap.bullets
    r = game._impl.bullets.bullet_radius
    for b in snap.bullets:
        # 快照判定半径与引擎实际判定半宽(均匀 AABB 盒)同源
        assert b.hitbox == r
    # 已知弹型样本: demo wave 用 sprite=0(小弹), 判定半径同为世界半宽
    assert any(b.sprite == 0 and b.hitbox == r for b in snap.bullets)
    # 自机判定半宽: 作品常量(th07 约 1~2px), 与引擎玩家实例一致
    assert snap.player.hitbox == game._impl.player.hitbox_radius
    assert 0.0 < snap.player.hitbox <= 4.0


def test_bullets_array_matches_snapshot() -> None:
    import math

    from touhou.utils import Vec2

    game = Game(seed=1)
    _run(game, 200, Input(shoot=True))
    game._impl.bullets.spawn_demo_wave(Vec2(192, 100))
    arr = game.bullets_array()
    snap = game.snapshot()
    assert arr.shape == (len(snap.bullets), 6)
    for row, b in zip(arr, snap.bullets):
        assert (row[0], row[1]) == (b.x, b.y)
        # vx/vy: angle/speed 按屏幕系(y 向下)换算的速度向量
        assert row[2] == pytest.approx(b.speed * math.cos(b.angle))
        assert row[3] == pytest.approx(b.speed * math.sin(b.angle))
        assert row[4] == b.hitbox
        assert row[5] == b.sprite
    # 标量自机坐标口与快照一致
    assert game.player_pos == (snap.player.x, snap.player.y)
