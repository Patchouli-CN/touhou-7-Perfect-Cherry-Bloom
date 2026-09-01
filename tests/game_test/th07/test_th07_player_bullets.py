"""自机弹(PlayerBullet)系统测试 —— 对照 th07 Player.cpp
g_ShtFireFuncs / g_ShtUpdateFuncs / g_ShtHitFuncs / SpawnBullets / UpdateShots /
CalcDamageToEnemy(§A.1/A.5/A.6)。

回调索引分布用真实 .sht(th07.dat)核对; 各回调行为用手工构造 entry 测。
"""

from __future__ import annotations

import math
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, r"D:\python_play\Touhou08")

from touhou.schema.archive import open_archive  # noqa: E402
from touhou.games.th07.player import (  # noqa: E402
    BULLET_POOL_SIZE,
    OptionState,
    Player,
    PlayerState,
    FIRE_DEFAULT,
    FIRE_HOMING,
    FIRE_ORB_FOCUSED,
    FIRE_ORB_UNFOCUSED,
    FIRE_ROTATING_ORB,
    HIT_MISSILE,
    UPDATE_HOMING,
    UPDATE_HOMING_FOCUSED,
    UPDATE_ORB_LASER,
    UPDATE_PLAYER_LASER,
    UPDATE_UPWARD_ACCEL,
    OPTION_ANGLE_CENTER,
)
from touhou.schema.shot_data import ShotData, ShotEntry, ShotLevel, parse_sht  # noqa: E402
from touhou.utils import Vec2  # noqa: E402

DAT = Path(r"D:\TOUHOU_GAME\[th07] 东方妖妖梦 (日文版)\th07.dat")

# 手工 .sht: 速度 4.0/2.0, 判定 4, 一个 0 级射击链(测试里替换)
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


def E(**kw) -> ShotEntry:
    """构造一条射击条目(默认: 每 5 帧, 朝上直飞, 伤害 10, 全 default 回调)。"""
    d = dict(
        fire_interval=5,
        fire_offset=0,
        offset=(0.0, 0.0),
        hitbox=(12.0, 12.0),
        angle=-math.pi / 2,
        speed=12.0,
        damage=10,
        option=0,
        bullet_state2=0,
        fire_cb=0,
        update_cb=0,
        draw_cb=0,
        hit_cb=0,
    )
    d.update(kw)
    return ShotEntry(**d)


def with_entries(*entries: ShotEntry) -> ShotData:
    return ShotData(
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
        levels=[ShotLevel(0, list(entries))],
    )


def make_player(*entries: ShotEntry, **kw) -> Player:
    kw.setdefault("shot_data", with_entries(*entries) if entries else SD)
    p = Player(**kw)
    p.step()  # SPAWNING → INVULNERABLE
    p.state = PlayerState.ALIVE
    p.invulnerability_timer = 0
    p.bullet_grace_period = 0
    p.events = []
    return p


def live(p: Player):
    return [b for b in p.bullet_pool if b.bullet_state != 0]


def fire_frames(p: Player, n: int, **keys) -> None:
    keys.setdefault("firing", True)
    for _ in range(n):
        p.push_keys(**keys)
        p.step()


# ---- 真实 .sht: 回调索引分布核对(g_ShtFireFuncs 等数组下标) ----


def test_real_sht_callback_index_distribution() -> None:
    arch = open_archive(DAT)
    expect = {
        # shotType: 0/1=ReimuA/B, 2/3=MarisaA/B, 4/5=SakuyaA/B; s 后缀=focus
        "ply00a.sht": {"upd": {UPDATE_HOMING}},  # 灵梦A 追踪符
        "ply00as.sht": {"upd": {UPDATE_HOMING_FOCUSED}},
        "ply00b.sht": {"upd": set()},  # 灵梦B 全 default
        "ply00bs.sht": {"upd": set()},
        "ply01a.sht": {
            "upd": {UPDATE_UPWARD_ACCEL},
            "hit": {HIT_MISSILE},
        },  # 魔理沙A 导弹
        "ply01as.sht": {
            "upd": {UPDATE_UPWARD_ACCEL},
            "hit": {HIT_MISSILE},
            "fire": {0, FIRE_DEFAULT},
        },  # 含显式 default(1)
        "ply01b.sht": {"fire": {0, FIRE_ORB_UNFOCUSED}, "upd": {0, UPDATE_ORB_LASER}},
        "ply01bs.sht": {
            "fire": {0, FIRE_ORB_FOCUSED},
            "upd": {0, UPDATE_PLAYER_LASER},
            "draw": {0, 1},
        },  # 拖尾
        "ply02a.sht": {"fire": {0}},
        "ply02as.sht": {"fire": {FIRE_HOMING}},  # 咲夜A focus 追踪
        "ply02b.sht": {"fire": {FIRE_ROTATING_ORB}},  # 咲夜B 旋转子机
        "ply02bs.sht": {"fire": {0, FIRE_ROTATING_ORB}},
    }
    for name, want in expect.items():
        sd = parse_sht(arch.load(name))
        got = {"fire": Counter(), "upd": Counter(), "draw": Counter(), "hit": Counter()}
        for lv in sd.levels:
            for e in lv.entries:
                if e.is_sentinel:
                    continue
                got["fire"][e.fire_cb] += 1
                got["upd"][e.update_cb] += 1
                got["draw"][e.draw_cb] += 1
                got["hit"][e.hit_cb] += 1
        for kind, keys in want.items():
            assert keys <= set(got[kind]), f"{name} {kind}: {set(got[kind])}"
        # 所有索引都在已知范围内
        assert set(got["fire"]) <= {0, 1, 2, 3, 4, 5}, name
        assert set(got["upd"]) <= {0, 1, 2, 3, 4, 5}, name
        assert set(got["draw"]) <= {0, 1}, name
        assert set(got["hit"]) <= {0, 1, 2}, name


def test_real_sht_marisa_b_persistent_slots() -> None:
    """魔理沙B: 非 focus 两条 orb 激光占槽 0/1(周期=持续时间), focus 激光占槽 2。"""
    arch = open_archive(DAT)
    sd = parse_sht(arch.load("ply01b.sht"))
    orbs = [
        e
        for lv in sd.levels
        for e in lv.entries
        if not e.is_sentinel and e.fire_cb == FIRE_ORB_UNFOCUSED
    ]
    assert orbs and all(e.fire_offset in (0, 1) for e in orbs)
    assert all(e.option in (1, 2) and e.bullet_state2 == 4 for e in orbs)
    sdf = parse_sht(arch.load("ply01bs.sht"))
    lasers = [
        e
        for lv in sdf.levels
        for e in lv.entries
        if not e.is_sentinel and e.fire_cb == FIRE_ORB_FOCUSED
    ]
    assert lasers and all(e.fire_offset == 2 for e in lasers)
    assert all(e.bullet_state2 == 5 and e.draw_cb == 1 for e in lasers)


# ---- 池化与 default 发射调度 ----


def test_pool_first_free_slot_and_fields() -> None:
    p = make_player(E())
    fire_frames(p, 1)
    b = p.bullet_pool[0]
    assert b.bullet_state == 1
    assert b.pos == p.pos
    assert b.velocity.distance(Vec2(0, -12)) < 1e-9
    assert (b.damage, b.timer, b.bullet_state2) == (10, 0, 0)
    assert len(live(p)) == 1
    # 每 5 帧一发, 占连续的池位
    fire_frames(p, 5)
    assert len(live(p)) == 2
    assert p.bullet_pool[1].bullet_state == 1


def test_pool_exhaustion_caps_at_96() -> None:
    p = make_player(E(fire_interval=1, speed=0.0))  # 每帧一发, 不移动不消失
    fire_frames(p, BULLET_POOL_SIZE + 20)
    assert len(live(p)) == BULLET_POOL_SIZE


def test_fire_timer_stops_when_not_firing_and_restarts() -> None:
    p = make_player(E())
    fire_frames(p, 1)
    assert p.fire_time == 1
    p.push_keys(firing=False)
    for _ in range(30):
        p.step()
    assert p.fire_time == -1  # 一轮 30 帧跑完归零(C++ 的 -1)
    n = len(live(p))
    p.step()
    assert len(live(p)) == n  # 不按射击不发射
    fire_frames(p, 1)  # 重新按下: 从 0 重启 → 立刻发射
    assert len(live(p)) == n + 1


def test_marisa_b_no_fire_during_bomb() -> None:
    p = make_player(E())
    p.is_marisa_b = True
    p.bomb_active = True
    fire_frames(p, 10)
    assert not live(p)
    p.bomb_active = False
    fire_frames(p, 1)
    assert len(live(p)) == 1


# ---- fire 回调: orb 持续弹(槽占用/中断/状态要求) ----


def test_orb_unfocused_occupies_timer_slot() -> None:
    p = make_player(
        E(
            fire_cb=FIRE_ORB_UNFOCUSED,
            fire_interval=100,
            fire_offset=0,
            option=1,
            update_cb=UPDATE_ORB_LASER,
            bullet_state2=4,
            speed=0.0,
        )
    )
    fire_frames(p, 1)
    ts = p.timers[0]
    assert ts.bullet is not None and ts.timer == 100
    assert ts.bullet.option_id == 1 and ts.bullet.timer_idx == 0
    fire_frames(p, 1)
    assert len(live(p)) == 1  # 槽占用中不重复发射
    assert ts.timer == 99  # 槽计时每帧递减
    assert p.sht_entries[0] is not None


def test_orb_requires_matching_option_state() -> None:
    # FOCUSED 型在 UNFOCUSED 时不发射
    p = make_player(
        E(
            fire_cb=FIRE_ORB_FOCUSED,
            fire_interval=4,
            fire_offset=2,
            option=0,
            update_cb=UPDATE_PLAYER_LASER,
            bullet_state2=5,
            draw_cb=1,
        )
    )
    fire_frames(p, 5)
    assert not live(p)
    # UNFOCUSED 型在 FOCUSED 时不发射
    p2 = make_player(
        E(
            fire_cb=FIRE_ORB_UNFOCUSED,
            fire_interval=100,
            fire_offset=0,
            option=1,
            update_cb=UPDATE_ORB_LASER,
            bullet_state2=4,
        )
    )
    fire_frames(p2, 12, focus=True)  # 8 帧过渡到 FOCUSED
    assert p2.option_state == OptionState.FOCUSED
    assert not live(p2)


def test_orb_entry_change_interrupts_old_bullet() -> None:
    e1 = E(
        fire_cb=FIRE_ORB_UNFOCUSED,
        fire_interval=100,
        fire_offset=0,
        option=1,
        update_cb=UPDATE_ORB_LASER,
        bullet_state2=4,
        speed=0.0,
    )
    p = make_player(e1)
    fire_frames(p, 1)
    old = p.timers[0].bullet
    # 换 entry(同槽): 旧弹中断, 本帧不发射, 下一帧发新弹
    e2 = E(
        fire_cb=FIRE_ORB_UNFOCUSED,
        fire_interval=80,
        fire_offset=0,
        option=1,
        update_cb=UPDATE_ORB_LASER,
        bullet_state2=4,
        speed=0.0,
    )
    p.shot_data = with_entries(e2)
    fire_frames(p, 1)
    assert old.bullet_state == 0
    assert len(live(p)) == 0
    fire_frames(p, 1)
    assert len(live(p)) == 1
    assert p.timers[0].timer == 80


def test_focus_transition_kills_unfocused_orbs() -> None:
    p = make_player(
        E(
            fire_cb=FIRE_ORB_UNFOCUSED,
            fire_interval=100,
            fire_offset=0,
            option=1,
            update_cb=UPDATE_ORB_LASER,
            bullet_state2=4,
            speed=0.0,
        )
    )
    fire_frames(p, 1)
    b = p.timers[0].bullet
    p.push_keys(focus=True)  # 进入 FOCUSING != UNFOCUSED → 槽 0/1 立即中断
    p.step()
    assert b.bullet_state == 0
    assert p.timers[0].bullet is None


def test_unfocus_kills_focused_laser_slot2() -> None:
    p = make_player(
        E(
            fire_cb=FIRE_ORB_FOCUSED,
            fire_interval=4,
            fire_offset=2,
            option=0,
            update_cb=UPDATE_PLAYER_LASER,
            bullet_state2=5,
            draw_cb=1,
            speed=0.0,
        )
    )
    fire_frames(p, 12, focus=True)  # FOCUSED 后发射
    assert p.option_state == OptionState.FOCUSED
    assert p.timers[2].bullet is not None
    b = p.timers[2].bullet
    p.push_keys(focus=False)  # UNFOCUSING != FOCUSED → 槽 2 立即消
    p.step()
    assert b.bullet_state == 0
    assert p.timers[2].bullet is None


def test_orb_timer_expires_bullet_dies() -> None:
    p = make_player(
        E(
            fire_cb=FIRE_ORB_UNFOCUSED,
            fire_interval=10,
            fire_offset=0,
            option=1,
            update_cb=UPDATE_ORB_LASER,
            bullet_state2=4,
            speed=0.0,
        )
    )
    fire_frames(p, 1)
    assert len(live(p)) == 1
    # 松开射击且一轮计时结束(fireBulletTimer=-1)后不再补发
    p.push_keys(firing=False)
    p.fire_time = -1
    for _ in range(10):  # 槽计时 10→0, update 回调收尸
        p.step()
    assert not live(p)
    assert p.timers[0].bullet is None


def test_orb_refires_when_slot_freed_while_firing() -> None:
    """orb 发射不看 fireTime: 按住射击时槽一空立即补发(C++ FireOrbBulletUnfocused)。"""
    p = make_player(
        E(
            fire_cb=FIRE_ORB_UNFOCUSED,
            fire_interval=10,
            fire_offset=0,
            option=1,
            update_cb=UPDATE_ORB_LASER,
            bullet_state2=4,
            speed=0.0,
        )
    )
    fire_frames(p, 1)
    fire_frames(p, 10)  # 旧弹计时归 0 死亡, 同帧补发新弹
    assert len(live(p)) == 1
    assert p.timers[0].timer == 10


# ---- update 回调: orbLaser / playerLaser 几何 ----


def test_orb_laser_follows_option_geometry() -> None:
    p = make_player(
        E(
            fire_cb=FIRE_ORB_UNFOCUSED,
            fire_interval=100,
            fire_offset=0,
            option=2,
            update_cb=UPDATE_ORB_LASER,
            bullet_state2=4,
            speed=0.0,
        )
    )
    fire_frames(p, 1)
    b = p.timers[0].bullet
    fire_frames(p, 1)  # 这一帧跑 update 回调
    opt = p.options[1]  # option=2 → 右子机
    assert b.pos.x == opt.x
    assert b.pos.y == opt.y / 2  # pos.y /= 2
    assert b.hitbox == (12.0, opt.y)  # hitbox 高 = 子机 y(到版顶)


def test_player_laser_follows_player_and_history_shifts() -> None:
    p = make_player(
        E(
            fire_cb=FIRE_ORB_FOCUSED,
            fire_interval=4,
            fire_offset=2,
            option=0,
            update_cb=UPDATE_PLAYER_LASER,
            bullet_state2=5,
            draw_cb=1,
            speed=0.0,
            offset=(0.0, -24.0),
        )
    )
    fire_frames(p, 12, focus=True)
    b = p.timers[2].bullet
    assert b is not None
    assert p.timers[2].timer == 999  # focus 型槽计时恒 999
    assert b.trail_length == 4  # trailLength = fireInterval
    p.push_keys(right=True, focus=True)
    p.step()
    # 跟随本体: hitbox 高 = 本体 y+64, pos.y = 本体 y/2-32, x 加 offset.x
    assert b.hitbox == (12.0, p.pos.y + 64.0)
    assert abs(b.pos.y - (p.pos.y / 2 - 32.0)) < 1e-9
    assert b.pos.x == p.pos.x + 0.0
    # pos_history 每帧右移, [0] 是上一帧弹位
    p.step()
    assert b.pos_history[0].x != -999.0
    assert b.pos_history[1] != b.pos_history[0] or b.pos_history[1].x != -999.0


def test_death_kills_persistent_bullets() -> None:
    p = make_player(
        E(
            fire_cb=FIRE_ORB_UNFOCUSED,
            fire_interval=100,
            fire_offset=0,
            option=1,
            update_cb=UPDATE_ORB_LASER,
            bullet_state2=4,
            speed=0.0,
        )
    )
    fire_frames(p, 1)
    old = p.timers[0].bullet
    p.push_keys(firing=False)  # 松开射击, 排除死亡帧补发的干扰
    p.fire_time = -1
    p.die()
    p.step()
    assert old.bullet_state == 0
    assert all(ts.bullet is None for ts in p.timers)
    p.step()
    assert not live(p)  # DEAD 中不再重启射击计时


def test_dialog_and_bomb_clamp_persistent_timer_to_20() -> None:
    for flag in ("dialog_active", "bomb_active"):
        p = make_player(
            E(
                fire_cb=FIRE_ORB_UNFOCUSED,
                fire_interval=100,
                fire_offset=0,
                option=1,
                update_cb=UPDATE_ORB_LASER,
                bullet_state2=4,
                speed=0.0,
            )
        )
        fire_frames(p, 1)
        old = p.timers[0].bullet
        setattr(p, flag, True)
        fire_frames(p, 1)  # 槽计时 99 → 压到 20
        assert p.timers[0].timer == 20
        p.push_keys(firing=False)  # 停止补发后数完 20 帧
        p.fire_time = -1
        for _ in range(20):
            p.step()
        assert old.bullet_state == 0, flag


def test_release_fire_clamps_persistent_timer_to_50() -> None:
    p = make_player(
        E(
            fire_cb=FIRE_ORB_UNFOCUSED,
            fire_interval=100,
            fire_offset=0,
            option=1,
            update_cb=UPDATE_ORB_LASER,
            bullet_state2=4,
            speed=0.0,
        )
    )
    fire_frames(p, 1)
    p.push_keys(firing=False)
    p.fire_time = -1  # 模拟射击已松开一轮(fireBulletTimer<0)
    p.step()
    assert p.timers[0].timer == 50  # 100→99 递减后压到 50


# ---- fire/update 回调: homing(咲夜A 发射 + 灵梦A 更新) ----


def test_homing_fire_redirects_to_sakuya_target() -> None:
    p = make_player(E(fire_cb=FIRE_HOMING, speed=10.0))
    p.sakuya_target_position = p.pos + Vec2(100, -100)
    fire_frames(p, 1)
    b = p.bullet_pool[0]
    # 朝目标重定向, 速度×1.5
    want = (p.sakuya_target_position - b.pos).normalized() * 15.0
    assert b.velocity.distance(want) < 1e-6
    assert b.speed == 10.0  # bullet.speed 字段保持 entry 值(C++ 行为)
    # 无目标(x<=-100)时直飞
    p2 = make_player(E(fire_cb=FIRE_HOMING, speed=10.0))
    fire_frames(p2, 1)
    assert p2.bullet_pool[0].velocity.distance(Vec2(0, -10)) < 1e-9


def test_homing_update_steers_toward_last_enemy_hit() -> None:
    p = make_player(E(update_cb=UPDATE_HOMING, speed=4.0))
    fire_frames(p, 1)
    b = p.bullet_pool[0]
    p.position_of_last_enemy_hit = b.pos + Vec2(100, 0)  # 正右方
    p.step()
    assert b.velocity.x > 0  # 向右转向
    assert b.speed <= 10.0  # 转向分支 cap 10
    # 无目标时沿当前方向 +0.3333 加速; 达到 cap 后停止(注意 C++ 加速分支不夹 cap)
    p.position_of_last_enemy_hit = Vec2(-999.0, -999.0)
    s0 = b.speed
    p.step()
    assert abs(b.speed - (s0 + 0.33333334)) < 1e-6
    assert abs(b.velocity.length - b.speed) < 1e-6
    for _ in range(25):  # 4.1 + n*0.333 ≥ 10 → 约 18 帧
        p.step()
        if b.speed >= 10.0:
            break
    assert b.bullet_state == 1 and b.speed >= 10.0
    s1 = b.speed
    p.step()
    assert b.speed == s1  # ≥cap 后不再加速


def test_homing_update_focused_cap_18() -> None:
    p = make_player(E(update_cb=UPDATE_HOMING_FOCUSED, speed=17.0))
    fire_frames(p, 1)
    b = p.bullet_pool[0]
    p.step()
    assert abs(b.speed - 17.6) < 1e-6  # +0.6
    p.step()
    assert abs(b.speed - 18.2) < 1e-6  # 超过 cap 即停(C++ 加速分支不把速度夹回 cap)
    p.step()
    assert abs(b.speed - 18.2) < 1e-6


def test_upward_accel_uses_injected_rng() -> None:
    p = make_player(E(update_cb=UPDATE_UPWARD_ACCEL, speed=0.0, angle=0.0))
    p.rand_float = lambda r: 0.0  # 注入: 恒 0 → 每帧 vy -= 0.27
    fire_frames(p, 1)
    b = p.bullet_pool[0]
    p.step()
    assert abs(b.velocity.y - (-0.27)) < 1e-9
    p.step()
    assert abs(b.velocity.y - (-0.54)) < 1e-9


# ---- fire 回调: rotatingOrb(咲夜B) ----


def test_rotating_orb_angle_includes_option_zero_entries() -> None:
    p = make_player(
        E(fire_cb=FIRE_ROTATING_ORB, angle=0.0, speed=12.0, option=0),
        rotating_options=True,
    )
    assert p.option_angle == OPTION_ANGLE_CENTER  # -pi/2
    fire_frames(p, 1)
    b = p.bullet_pool[0]
    # 发射角 = optionAngle + entry.angle + pi/2 = 0 → 朝 +x
    assert b.velocity.distance(Vec2(12, 0)) < 1e-4
    assert abs(b.angle - 0.0) < 1e-4
    # optionAngle 摆动后跟随(无横向移动时每帧向 -pi/2 回中, 同帧内先摆动后发射,
    # 故期望值用发射后读到的 option_angle 推算)
    p.option_angle = -1.0
    p.fire_time = -1  # 强制从 0 重启, 本帧即发射
    p.push_keys(firing=True)
    p.step()
    b2 = live(p)[-1]
    want = p.option_angle + 0.0 + math.pi / 2  # entry.angle = 0
    assert b2.velocity.distance(Vec2.from_angle(want, 12.0)) < 1e-4


# ---- 命中语义(CalcDamageToEnemy) ----


def test_hit_normal_bullet_explodes_and_slows() -> None:
    p = make_player(E(speed=0.0))
    fire_frames(p, 1)
    b = p.bullet_pool[0]
    b.velocity = Vec2(0, -8)
    dmg = p.calc_damage_to_enemy(b.pos, (20.0, 20.0))
    assert dmg == 10
    assert b.bullet_state == 2  # 命中爆炸
    assert b.velocity.distance(Vec2(0, -1)) < 1e-9  # 速度/8
    # 爆炸后非穿透弹不再造成伤害
    assert p.calc_damage_to_enemy(b.pos, (20.0, 20.0)) == 0


def test_hit_piercing_bullet_keeps_speed_and_damages_every_frame() -> None:
    p = make_player(E(bullet_state2=3, speed=0.0))
    fire_frames(p, 1)
    b = p.bullet_pool[0]
    b.velocity = Vec2(0, -8)
    assert p.calc_damage_to_enemy(b.pos, (20.0, 20.0)) == 10
    assert b.bullet_state == 2
    assert b.velocity.distance(Vec2(0, -8)) < 1e-9  # 穿透不减速
    assert p.calc_damage_to_enemy(b.pos, (20.0, 20.0)) == 10  # 下帧仍判定


def test_hit_laser_types_parity_halving_and_no_explosion() -> None:
    for bs2 in (4, 5):
        p = make_player(E(bullet_state2=bs2, speed=0.0))
        fire_frames(p, 1)
        b = p.bullet_pool[0]
        b.timer = 0  # 偶 → 出伤害
        assert p.calc_damage_to_enemy(b.pos, (20.0, 20.0)) == 10
        assert b.bullet_state == 1  # 激光型不进爆炸态
        b.timer = 1  # 奇 → 跳过
        assert p.calc_damage_to_enemy(b.pos, (20.0, 20.0)) == 0


def test_hit_during_bomb_damage_third_min_one() -> None:
    p = make_player(E(speed=0.0, damage=10))
    fire_frames(p, 2)  # 两发(间隔 5 帧内只发一发? → 用两弹位)
    # 直接摆两发弹分别验证
    bs = live(p)
    assert bs
    b = bs[0]
    assert p.calc_damage_to_enemy(b.pos, (20.0, 20.0), bomb_active=True) == 3
    b2 = p.bullet_pool[1]
    if b2.bullet_state == 0:
        b2.bullet_state = 1
        b2.damage = 2
        b2.bullet_state2 = 3  # 穿透, 免得首发结算互相影响
    b2.pos = b.pos
    b2.hitbox = (12.0, 12.0)
    assert p.calc_damage_to_enemy(b2.pos, (20.0, 20.0), bomb_active=True) >= 1


def test_missile_hit_transforms_then_decays_every_other_frame() -> None:
    p = make_player(
        E(hit_cb=HIT_MISSILE, bullet_state2=3, anm_file_idx=1090, speed=0.0, damage=9)
    )
    p.rand_float = lambda r: 0.0  # 爆炸角 = -3pi/4
    fire_frames(p, 1)
    b = p.bullet_pool[0]
    p.step()  # timer=1
    # 首中: 判定盒扩到 42x42, 速度改为爆炸速度 4, 全额伤害, 穿透不减速
    assert p.calc_damage_to_enemy(b.pos, (20.0, 20.0)) == 9
    assert b.bullet_state == 2
    assert b.hitbox == (42.0, 42.0)
    assert abs(b.velocity.length - 4.0) < 1e-6
    p.step()  # timer=2 (偶): 伤害 9//3=3, 速度×0.88
    assert p.calc_damage_to_enemy(b.pos, (60.0, 60.0)) == 3
    assert abs(b.velocity.length - 4.0 * 0.88) < 1e-6
    p.step()  # timer=3 (奇): 隔帧跳过
    assert p.calc_damage_to_enemy(b.pos, (60.0, 60.0)) == 0
    p.step()  # timer=4 (偶): 3//3=1 (最低 1)
    assert p.calc_damage_to_enemy(b.pos, (60.0, 60.0)) == 1


def test_iter_hits_yields_per_bullet_damage() -> None:
    p = make_player(E(speed=0.0, damage=7), E(speed=0.0, damage=5))
    fire_frames(p, 1)
    hits = list(p.iter_hits(p.pos, (30.0, 30.0)))
    assert sorted(d for _, d in hits) == [5, 7]


def test_player_laser_trail_segments_add_damage() -> None:
    p = make_player(
        E(
            fire_cb=FIRE_ORB_FOCUSED,
            fire_interval=4,
            fire_offset=2,
            option=0,
            update_cb=UPDATE_PLAYER_LASER,
            bullet_state2=5,
            draw_cb=1,
            speed=0.0,
            damage=2,
        )
    )
    fire_frames(p, 12, focus=True)
    b = p.timers[2].bullet
    # 往右移动若干帧, 留下横向拖尾
    fire_frames(p, 6, right=True, focus=True)
    # 在拖尾历史点放敌人: 主激光(细条)打不到, 历史段补 1 点/段
    hp = [h for h in b.pos_history[: b.trail_length] if h.x >= -900.0]
    assert hp, "拖尾历史应已填上真实位置"
    target = hp[-1]
    # 敌人偏离主激光条(主条在弹 x 附近, 宽 12): 用窄判定盒避开主条
    hits = list(p.iter_hits(Vec2(target.x, target.y), (4.0, 4.0)))
    assert any(d == 1 for _, d in hits)
