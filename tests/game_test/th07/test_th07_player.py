"""玩家状态机/死亡重生/擦弹/咲夜B子机 测试(对照 th07 Player.cpp, §A.4/A.7)。"""

from __future__ import annotations

import math
import sys

sys.path.insert(0, r"D:\python_play\Touhou08")

from touhou.engine.bullets import Bullet  # noqa: E402
from touhou.games.th07.player import (  # noqa: E402
    DeathContext,
    KillResult,
    OptionState,
    Player,
    PlayerEvent,
    PlayerEventKind,
    PlayerState,
    OPTION_ANGLE_CENTER,
    OPTION_ANGLE_MAX,
    OPTION_ANGLE_MIN,
    OPTION_ANGLE_RETURN_STEP,
)
from touhou.schema.shot_data import ShotData, ShotLevel  # noqa: E402
from touhou.utils import Vec2  # noqa: E402

# 一份手工 .sht: 重生倒计时 5 帧, 判定半径 4, 擦弹半宽源 grabItemRadius=48,
# 樱罚系数 0.5, 速度 4.0/2.0, 一个 0 级空射击链
SD = ShotData(
    initial_bombs=3.0,
    initial_respawn_timer=5,
    hitbox_radius=4.0,
    grab_item_radius=48.0,
    item_collect_speed=4.0,
    item_collect_radius=16.0,
    cherry_penalty_multiplier=0.5,
    poc_y=128.0,
    speed=4.0,
    speed_focus=2.0,
    speed_diagonal=2.8,
    speed_diagonal_focus=1.4,
    levels=[ShotLevel(0, [])],
)


def make_player(**kw) -> Player:
    kw.setdefault("shot_data", SD)
    p = Player(**kw)
    p.step()  # SPAWNING(invuln 120>=30) → INVULNERABLE
    assert p.state == PlayerState.INVULNERABLE
    p.state = PlayerState.ALIVE
    p.invulnerability_timer = 0
    p.bullet_grace_period = 0
    p.events = []
    return p


def events_of(p: Player, kind: PlayerEventKind) -> list[PlayerEvent]:
    return [e for e in p.events if e.kind == kind]


# ---- 出生/无敌状态机 ----


def test_spawn_state_and_invulnerable_countdown() -> None:
    p = Player(shot_data=SD)
    assert p.state == PlayerState.SPAWNING
    assert p.invulnerability_timer == 120
    p.step()
    assert p.state == PlayerState.INVULNERABLE
    assert p.invulnerability_timer == 240
    for _ in range(240):
        p.step()
    assert p.state == PlayerState.ALIVE
    assert p.invulnerability_timer == 0


def test_killbox_ignored_while_invulnerable() -> None:
    p = make_player()
    p.state = PlayerState.INVULNERABLE
    p.invulnerability_timer = 10
    assert p.check_killbox(p.pos, (8.0, 8.0)) == KillResult.NONE
    assert p.state == PlayerState.INVULNERABLE


# ---- 死亡 → 倒计时 → 重生全流程 ----


def test_death_full_flow_with_lives() -> None:
    p = make_player()
    p.power = 100.0
    p.die()
    assert p.state == PlayerState.DEAD
    assert p.invulnerability_timer == 0
    assert p.respawn_timer == 5

    ctx = DeathContext(lives=2, cherry=40000, cherry_start=10000, is_sakuya=False)
    for _ in range(4):
        p.step(ctx)
        assert p.state == PlayerState.DEAD
        assert not events_of(p, PlayerEventKind.DEATH_SETTLE)
    p.step(ctx)  # 第 5 帧倒计时归 0 → 结算 + 重生

    settle = events_of(p, PlayerEventKind.DEATH_SETTLE)
    assert len(settle) == 1
    d = settle[0].data
    assert d.has_lives
    assert d.new_power == 84.0  # 100 - 16
    assert (d.drop_power_big, d.drop_power_small) == (1, 5)
    assert d.drop_full_power == 0
    # (40000-10000)*0.5 = 15000, 向下取整 10 不变
    assert d.cherry_penalty == 15000
    assert d.activate_all_items
    assert d.subrank_delta == -1600
    assert events_of(p, PlayerEventKind.RESPAWNED)

    # 重生: INVULNERABLE 240 + 清弹期 60
    assert p.state == PlayerState.INVULNERABLE
    assert p.invulnerability_timer == 240
    assert p.bullet_grace_period == 60
    assert p.power == 84.0
    assert p.respawn_timer == SD.initial_respawn_timer


def test_death_power_floor_branch() -> None:
    p = make_player()
    p.power = 16.0  # <=16 → 归 0
    p.die()
    for _ in range(5):
        p.step(DeathContext(lives=1))
    d = events_of(p, PlayerEventKind.DEATH_SETTLE)[0].data
    assert d.new_power == 0.0
    assert (d.drop_power_big, d.drop_power_small) == (1, 5)


def test_death_no_lives_drops_full_power() -> None:
    p = make_player()
    p.power = 80.0
    p.die()
    for _ in range(5):
        p.step(DeathContext(lives=0, cherry=99999, cherry_start=0))
    d = events_of(p, PlayerEventKind.DEATH_SETTLE)[0].data
    assert not d.has_lives
    assert d.new_power == 0.0
    assert d.drop_full_power == 5
    assert d.drop_power_big == 0 and d.drop_power_small == 0
    assert d.cherry_penalty == 0
    assert not d.activate_all_items
    assert d.subrank_delta == -1600


def test_cherry_penalty_caps() -> None:
    p = make_player()
    p.die()
    for _ in range(5):
        p.step(DeathContext(lives=1, cherry=1000000, cherry_start=0, is_sakuya=False))
    assert events_of(p, PlayerEventKind.DEATH_SETTLE)[0].data.cherry_penalty == 100000

    p = make_player()
    p.die()
    for _ in range(5):
        p.step(DeathContext(lives=1, cherry=1000000, cherry_start=0, is_sakuya=True))
    assert events_of(p, PlayerEventKind.DEATH_SETTLE)[0].data.cherry_penalty == 60000


def test_cherry_penalty_floored_to_10() -> None:
    p = make_player()
    p.die()
    # (20025-0)*0.5 = 10012.5 → i32 截断 10012 → 向下取整 10 → 10010
    for _ in range(5):
        p.step(DeathContext(lives=1, cherry=20025, cherry_start=0))
    assert events_of(p, PlayerEventKind.DEATH_SETTLE)[0].data.cherry_penalty == 10010


def test_bullet_grace_period_emits_each_frame() -> None:
    p = make_player()
    p.die()
    for _ in range(5):
        p.step(DeathContext(lives=1))
    assert p.bullet_grace_period == 60
    cleared = 0
    for _ in range(70):
        p.step()
        cleared += len(events_of(p, PlayerEventKind.REMOVE_ALL_BULLETS))
    assert cleared == 60
    assert p.bullet_grace_period == 0


# ---- 擦弹(§A.7 CheckGraze/ScoreGraze) ----


def test_graze_aabb_with_20px_expand() -> None:
    p = make_player()  # 擦弹半宽 24, pos=(192, 384)
    # 弹 (8x8) 中心贴边: |dx| = 24 + 4 + 20 = 48 → 相交(边相接算擦)
    assert p.check_graze(p.pos + Vec2(48, 0), (8.0, 8.0))
    ev = events_of(p, PlayerEventKind.GRAZE)
    assert len(ev) == 1 and ev[0].value == 200
    # 再远 1px 不擦
    assert not p.check_graze(p.pos + Vec2(49.0001, 0), (8.0, 8.0))
    # y 方向同理
    assert p.check_graze(p.pos + Vec2(0, -48), (8.0, 8.0))


def test_graze_each_bullet_only_once() -> None:
    p = make_player()
    b = Bullet(p.pos + Vec2(30, 0), 0.0, 0.0)
    assert p.graze_bullet(b, (7.0, 7.0))
    assert b.grazed
    assert not p.graze_bullet(b, (7.0, 7.0))  # 第二次不再触发
    assert len(events_of(p, PlayerEventKind.GRAZE)) == 1


def test_graze_blocked_when_dead_or_spawning() -> None:
    p = make_player()
    p.die()
    assert not p.check_graze(p.pos + Vec2(1, 0), (8.0, 8.0))
    p2 = Player(shot_data=SD)  # SPAWNING
    assert not p2.check_graze(p2.pos + Vec2(1, 0), (8.0, 8.0))


# ---- 命中判定(CalcKillboxCollision) ----


def test_killbox_aabb() -> None:
    p = make_player()  # 判定半宽 2 (hitboxRadius 4 / 2)
    # 弹 6x6: |dx| = 2 + 3 = 5 → 相交; 边相接算命中
    assert p.check_killbox(p.pos + Vec2(5, 0), (6.0, 6.0)) == KillResult.DEATH
    assert p.state == PlayerState.DEAD

    p = make_player()
    assert p.check_killbox(p.pos + Vec2(5.0001, 0), (6.0, 6.0)) == KillResult.NONE
    assert p.state == PlayerState.ALIVE


def test_killbox_border_breaks_instead_of_death() -> None:
    p = make_player()
    p.state = PlayerState.BORDER
    assert p.check_killbox(p.pos, (8.0, 8.0)) == KillResult.BORDER_BREAK
    assert p.state == PlayerState.BORDER  # 不死, 状态由上层接 BREAK_BORDER 后处理
    assert len(events_of(p, PlayerEventKind.BREAK_BORDER)) == 1


# ---- 咲夜B optionAngle(§A.4) ----


def test_sakuya_b_option_angle_swing_and_clamp() -> None:
    p = make_player(rotating_options=True)
    assert p.option_angle == OPTION_ANGLE_CENTER
    # 持续右移射击: optionAngle += (vx/4)*pi/50, vx=4 → +pi/50/帧, 夹到 MAX
    p.push_keys(right=True)
    for _ in range(30):
        p.step()
    assert p.option_angle == OPTION_ANGLE_MAX
    # 左移: 夹到 MIN
    p.push_keys(left=True)
    for _ in range(60):
        p.step()
    assert p.option_angle == OPTION_ANGLE_MIN
    # 松开方向: 每帧 ±0.06283 回中
    p.push_keys()
    p.step()
    assert p.option_angle == OPTION_ANGLE_MIN + OPTION_ANGLE_RETURN_STEP
    for _ in range(30):
        p.step()
    assert p.option_angle == OPTION_ANGLE_CENTER
    # focus 时不更新 optionAngle
    p.push_keys(right=True, focus=True)
    p.step()
    assert p.option_angle == OPTION_ANGLE_CENTER


def test_sakuya_b_options_rotate_radius_24() -> None:
    p = make_player(rotating_options=True)
    p.push_keys()  # 不移动, optionAngle 保持 -pi/2
    p.step()
    # 未 focus: base = angle+pi/2 = 0° 方向 → 子机在 (x∓24, y)
    # (容差 1e-4: C++ 字面量 -1.5707964 与 math.pi/2 有 float 精度差)
    assert p.options[0].x < p.pos.x < p.options[1].x
    assert abs(p.options[1].x - p.pos.x - 24.0) < 1e-4
    assert abs(p.options[0].y - p.pos.y) < 1e-4


def test_sakuya_b_focus_transition_endpoints() -> None:
    p = make_player(rotating_options=True)
    p.push_keys(focus=True)
    for _ in range(9):  # 8 帧过渡(首帧切入 FOCUSING 不计时)
        p.step()
    assert p.option_state == OptionState.FOCUSED
    # optionAngle=-pi/2: 子机 = pos + (cos,sin)(-pi/2±0.2244)*24
    tgt1 = Vec2.from_angle(-math.pi / 2 + 0.22439948, 24.0)
    tgt0 = Vec2.from_angle(-math.pi / 2 - 0.22439948, 24.0)
    assert (p.options[1] - p.pos).distance(tgt1) < 1e-4
    assert (p.options[0] - p.pos).distance(tgt0) < 1e-4


# ---- 非咲夜B 子机 8 帧过渡 ----


def test_plain_options_focus_transition_endpoints() -> None:
    p = make_player()
    p.push_keys()
    p.step()
    assert p.options[0] == p.pos + Vec2(-24, 0)
    assert p.options[1] == p.pos + Vec2(24, 0)
    p.push_keys(focus=True)
    for _ in range(9):
        p.step()
    assert p.option_state == OptionState.FOCUSED
    assert (p.options[0] - p.pos).distance(Vec2(-8, -32)) < 1e-9
    assert (p.options[1] - p.pos).distance(Vec2(8, -32)) < 1e-9
    # 过渡中点(t=4/8): y 线性 -16, x 二次 24-16*(0.5^2)=20
    p = make_player()
    p.push_keys(focus=True)
    for _ in range(4):
        p.step()
    assert abs((p.options[1].y - p.pos.y) - -16.0) < 1e-9
    assert abs((p.options[1].x - p.pos.x) - 20.0) < 1e-9


# ---- 体术命中 (check_contact = CalcKillboxCollision 返回 1 语义, Player.cpp:1014-1039) ----


def test_contact_hit_kills_alive_player() -> None:
    p = make_player()
    assert p.check_contact(p.pos, (16.0, 16.0)) is True
    assert p.state == PlayerState.DEAD


def test_contact_miss_returns_false() -> None:
    p = make_player()
    assert p.check_contact(p.pos + Vec2(100, 0), (16.0, 16.0)) is False
    assert p.state == PlayerState.ALIVE


def test_contact_hit_while_invulnerable_counts_but_no_death() -> None:
    """C++ 命中返回 1 与玩家状态无关: 无敌/出生中相交仍算命中(敌人侧据此
    life-=10, 见 EnemyManager.cpp:589-594), 玩家侧无效果。"""
    p = make_player()
    p.state = PlayerState.INVULNERABLE
    assert p.check_contact(p.pos, (16.0, 16.0)) is True
    assert p.state == PlayerState.INVULNERABLE
    p.state = PlayerState.SPAWNING
    assert p.check_contact(p.pos, (16.0, 16.0)) is True
    assert p.state == PlayerState.SPAWNING


def test_contact_border_breaks_instead_of_death() -> None:
    """BORDER 中体术命中 → 结界破事件保命(与子弹命中同路径)。"""
    p = make_player()
    p.state = PlayerState.BORDER
    assert p.check_contact(p.pos, (16.0, 16.0)) is True
    assert p.state == PlayerState.BORDER
    assert events_of(p, PlayerEventKind.BREAK_BORDER)
