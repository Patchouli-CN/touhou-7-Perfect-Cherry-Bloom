"""Touhou: Boss 阶段 / 符卡状态机测试。"""
from __future__ import annotations

import sys

sys.path.insert(0, r"D:\python_play\Touhou08")

from touhou.engine.boss import SPELLCARD_SCORE, Boss  # noqa: E402
from touhou.engine.enemies import settle_damage, stage_factor  # noqa: E402


def test_spellcard_score_table_matches_cpp() -> None:
    """g_SpellcardScore[141] (EnemyManager.cpp:16-37) 抽样核对。"""
    assert len(SPELLCARD_SCORE) == 141
    assert SPELLCARD_SCORE[0] == SPELLCARD_SCORE[1] == 0x1E8480      # 2000000
    assert SPELLCARD_SCORE[2] == SPELLCARD_SCORE[3] == 0x2191C0      # 2200000
    assert SPELLCARD_SCORE[9] == 0x249F00                            # 2400000
    assert SPELLCARD_SCORE[10] == SPELLCARD_SCORE[25] == 0x27AC40    # 2600000
    assert SPELLCARD_SCORE[26] == SPELLCARD_SCORE[43] == 0x2DC6C0    # 3000000
    assert SPELLCARD_SCORE[44] == SPELLCARD_SCORE[67] == 0x3567E0    # 3500000
    assert SPELLCARD_SCORE[68] == SPELLCARD_SCORE[87] == 0x3D0900    # 4000000
    assert SPELLCARD_SCORE[88] == SPELLCARD_SCORE[111] == 0x4C4B40   # 5000000
    assert SPELLCARD_SCORE[112] == SPELLCARD_SCORE[115] == 0x2DC6C0
    assert SPELLCARD_SCORE[116] == SPELLCARD_SCORE[117] == 0x5B8D80  # 6000000
    assert SPELLCARD_SCORE[118] == SPELLCARD_SCORE[125] == 0x6ACFC0  # 7000000
    assert SPELLCARD_SCORE[126] == 0x3D0900
    assert SPELLCARD_SCORE[130] == SPELLCARD_SCORE[137] == 0x7A1200  # 8000000
    assert SPELLCARD_SCORE[139] == 0x7A1200
    assert SPELLCARD_SCORE[140] == 0x3D0900


def test_life_threshold_switches_phase() -> None:
    b = Boss()
    b.set_life(1000)
    b.life_thresholds = [(800, 1), (400, 2)]
    # 生命未跌破任一阈值 → 无回调
    b.life = 900
    assert b.check_life_threshold() == 0
    # 跌破 800 → 切阶段1, 生命钉在 800
    b.life = 700
    fired = b.check_life_threshold()
    assert fired == 1
    assert b.life == 800
    assert b.phase == 1
    # 再跌破 400 → 阶段2
    b.life = 300
    assert b.check_life_threshold() == 2


def test_spellcard_capture_on_time() -> None:
    b = Boss()
    b.begin_spellcard(0, 60 * 60)  # 60 秒
    assert b.is_active == 1
    assert b.is_capturing
    r = b.end_spellcard()
    assert r["ended"] and r["captured"]
    assert r["score"] == SPELLCARD_SCORE[0]
    assert r["spell_cards_captured"] == 1
    assert r["despawn_bullets"] == (8000, 1)
    assert r["remove_all_enemies"] == (8000, 0)
    assert b.is_active == 0
    assert b.spellcard_idx == -1


def test_spellcard_fail_on_bomb() -> None:
    b = Boss()
    b.begin_spellcard(0, 60 * 60)
    b.mark_bombed()          # 玩家用弹
    assert not b.is_capturing
    assert b.capture_score == 0
    assert b.used_bomb       # usedBomb = isActive
    r = b.end_spellcard()
    assert r["ended"] and not r["captured"]


def test_capture_score_decays_linearly() -> None:
    """每帧由基础分重算: captureScore = base - timer*drain/60, 向下取整 10。"""
    b = Boss()
    b.begin_spellcard(0, 60 * 60)          # base=2000000, drain=2000000//70=28571
    assert b.score_drain_rate == 28571
    base = SPELLCARD_SCORE[0]
    prev = b.capture_score
    for t in range(1, 4201):               # 推进到 时间限制+10秒
        b.tick()
        expect = max(0, int(base - b.timer * 28571 / 60.0))
        if expect > 0:
            expect -= expect % 10
        assert b.capture_score == expect
        assert b.capture_score <= prev     # 单调不增
        prev = b.capture_score
    assert b.capture_score < 100           # int 截断余量, 实际衰减到底


def test_handle_timer_callback_timeout_fails_capture() -> None:
    """B.5: 非 survival 超时 → 捕获失败, is_active=2, 清弹信号+樱点惩罚。"""
    b = Boss()
    b.set_life(1000)
    b.life = 300
    b.life_thresholds = [(500, 7)]         # 比当前生命高的阈值 → 先钉生命
    b.begin_spellcard(0, 600, timeout_sub=5)
    b.seconds_remaining = 0
    cleared = []
    for _ in range(600):
        b.tick()
        ev = b.handle_timer_callback(cherry_above_start=40000,
                                     clear_field_cb=lambda: cleared.append(1))
    assert ev["fired"] and ev["callback"] == 5
    assert ev["clear_field"] and cleared == [1]
    assert ev["remove_all_bullets"]        # RemoveAllBullets(10) 信号
    assert ev["cherry_penalty"] == 10000   # 40000*0.25, 向下取整 10
    assert not b.is_capturing
    assert b.capture_score == 0
    assert b.is_active == 2                # 超时失败标记
    assert b.timer == 0
    assert b.timer_callback_threshold == -1
    assert b.life == 500                   # 更高生命阈值已钉住并清掉
    assert b.life_thresholds == []
    r = b.end_spellcard()
    assert r["ended"] and r["timed_out"] and not r["captured"]
    assert r["score"] == 0 and r["despawn_bullets"] is None


def test_handle_timer_callback_survival_keeps_capture() -> None:
    """survival 符卡超时不掉 is_capturing, 无惩罚, is_active 不置 2。"""
    b = Boss()
    b.set_life(500)
    b.is_survival_spellcard = True
    b.begin_spellcard(0, 600, timeout_sub=5)
    for _ in range(600):
        b.tick()
    assert b.capture_score == SPELLCARD_SCORE[0]   # survival 不衰减
    ev = b.handle_timer_callback(cherry_above_start=40000)
    assert ev["fired"] and ev["callback"] == 5
    assert ev["cherry_penalty"] == 0
    assert not ev["remove_all_bullets"]
    assert b.is_capturing
    assert b.is_active == 1
    r = b.end_spellcard()
    assert r["captured"] and r["score"] == SPELLCARD_SCORE[0]


def test_seconds_remaining_display() -> None:
    b = Boss()
    b.boss_id = 0
    b.begin_spellcard(0, 600)
    for _ in range(60):
        b.tick()
    b.handle_timer_callback()
    assert b.seconds_remaining == 9        # (600-60)/60


def test_capture_score_includes_graze_bonus() -> None:
    b = Boss()
    b.begin_spellcard(0, 60 * 60)
    for _ in range(600):
        b.tick()
    b.add_graze_bonus(0)                   # +2500
    b.add_graze_bonus(30000)               # +2500 + 30000//1500*20 = +2900
    r = b.end_spellcard()
    assert r["captured"]
    expect_capture = int(SPELLCARD_SCORE[0] - 600 * 28571 / 60.0)
    expect_capture -= expect_capture % 10
    assert r["score"] == expect_capture + 5400


def test_bomb_damage_to_boss_requires_used_bomb_in_spellcard() -> None:
    b = Boss()
    b.set_life(500)
    b.begin_spellcard(0, 60 * 60)
    # 符卡中未用炸弹 → bomb 伤 0
    before = b.life
    r = b.damage(100, from_bomb=True)
    assert r.damage == 0
    assert b.life == before
    # 用过炸弹 → 先封顶 70 再 max(damage/2.5, 1) = 28
    b.used_bomb = True
    r = b.damage(100, from_bomb=True)
    assert r.damage == 28
    assert b.life == before - 28


def test_boss_damage_outside_spellcard_caps_at_70() -> None:
    b = Boss()
    b.set_life(500)
    r = b.damage(100)
    assert r.damage == 70
    assert r.score_code == 140             # 70//5*10
    assert b.life == 430


def test_spellcard_damage_scaling_normal_shot() -> None:
    b = Boss()
    b.set_life(500)
    b.begin_spellcard(0, 60 * 60)
    assert b.damage(70).damage == 10       # 70//7
    assert b.damage(7).damage == 1         # ≤7 → 1
    assert b.damage(0).damage == 0


def test_stage_factor() -> None:
    assert [stage_factor(s) for s in (1, 2, 3, 4, 5, 6, 7)] == \
        [2, 4, 6, 8, 10, 10, 10]


def test_settle_damage_cherry_gain() -> None:
    # boss 且未 focus: damage//(10-sf//3)*10, stage1 → //10*10
    r = settle_damage(50, is_boss=True, is_focus=False, stage=1)
    assert r.cherry_gain == 50
    # 封顶 70
    r = settle_damage(100, is_boss=True, is_focus=False, stage=1)
    assert r.cherry_gain == 70
    # 普通敌人未 focus: damage//(30-sf)*10, stage1 → //28*10
    r = settle_damage(70, is_boss=False, is_focus=False, stage=1)
    assert r.cherry_gain == 20
    # 普通敌人 focus → 无樱点
    r = settle_damage(70, is_boss=False, is_focus=True, stage=1)
    assert r.cherry_gain == 0
    # bomb 中 → 无樱点
    r = settle_damage(70, is_boss=True, is_focus=False, bomb_in_use=True, stage=1)
    assert r.cherry_gain == 0
    # 为 0 时按奇偶补 10: boss+focus, timer 奇 → 10, 偶 → 0
    r = settle_damage(5, is_boss=True, is_focus=True, stage=1, enemy_timer=1)
    assert r.cherry_gain == 10
    r = settle_damage(5, is_boss=True, is_focus=True, stage=1, enemy_timer=2)
    assert r.cherry_gain == 0


def test_settle_damage_score_and_cap() -> None:
    r = settle_damage(69, is_boss=False, is_focus=True, stage=1)
    assert r.damage == 69 and r.score_code == 130   # 69//5*10
    r = settle_damage(70, is_boss=False, is_focus=True, stage=1)
    assert r.damage == 70 and r.score_code == 140
    # cherryGain 用未封顶的原始伤害
    r = settle_damage(100, is_boss=False, is_focus=False, stage=1)
    assert r.damage == 70 and r.cherry_gain == 30   # 100//28*10


def test_settle_damage_spellcard_scaling() -> None:
    # 非 bomb: max(damage/7, 1)
    r = settle_damage(70, is_boss=True, is_focus=False, stage=1,
                      spellcard_active=True)
    assert r.damage == 10
    r = settle_damage(5, is_boss=True, is_focus=False, stage=1,
                      spellcard_active=True)
    assert r.damage == 1
    # bomb 且 used_bomb: 先封顶 70 再 max(damage/2.5, 1)
    r = settle_damage(100, is_boss=True, is_focus=False, stage=1,
                      spellcard_active=True, bomb_damage=True, used_bomb=True)
    assert r.damage == 28                   # int(70/2.5)
    r = settle_damage(2, is_boss=True, is_focus=False, stage=1,
                      spellcard_active=True, bomb_damage=True, used_bomb=True)
    assert r.damage == 1
    # bomb 未 used_bomb: 0
    r = settle_damage(100, is_boss=True, is_focus=False, stage=1,
                      spellcard_active=True, bomb_damage=True, used_bomb=False)
    assert r.damage == 0


def test_settle_damage_invincibility() -> None:
    # boss 无敌: /9
    r = settle_damage(63, is_boss=True, is_focus=True, stage=1,
                      invincibility_timer=10)
    assert r.damage == 7
    # 非 boss 无敌: 0
    r = settle_damage(63, is_boss=False, is_focus=True, stage=1,
                      invincibility_timer=10)
    assert r.damage == 0


def test_settle_damage_graze_extra() -> None:
    # grazeSize 额外伤害: 无 bomb 时 +grazeDamage/2.5
    r = settle_damage(10, is_boss=False, is_focus=True, stage=1, graze_damage=25)
    assert r.damage == 20
    # bomb 伤害时不加
    r = settle_damage(10, is_boss=True, is_focus=True, stage=1,
                      bomb_damage=True, spellcard_active=True, used_bomb=True,
                      graze_damage=25)
    assert r.damage == 4                     # int(10/2.5), 未加 graze


def test_settle_damage_reimu_a_cherry() -> None:
    """ReimuA cherryGain 20/30 隔帧 -10 (EnemyManager.cpp:815-821)。"""
    # stage1 普通敌未 focus: 70//28*10 = 20 → timer 奇 → 10, 偶 → 20
    r = settle_damage(70, is_boss=False, is_focus=False, stage=1,
                      enemy_timer=1, is_reimu_a=True)
    assert r.cherry_gain == 10
    r = settle_damage(70, is_boss=False, is_focus=False, stage=1,
                      enemy_timer=2, is_reimu_a=True)
    assert r.cherry_gain == 20
    # 非 ReimuA 不减
    r = settle_damage(70, is_boss=False, is_focus=False, stage=1,
                      enemy_timer=1, is_reimu_a=False)
    assert r.cherry_gain == 20
    # 30 同理: 100//28*10 = 30 → 奇帧 20
    r = settle_damage(100, is_boss=False, is_focus=False, stage=1,
                      enemy_timer=3, is_reimu_a=True)
    assert r.cherry_gain == 20
    # 非 20/30 不动: 50//28*10 = 10
    r = settle_damage(50, is_boss=False, is_focus=False, stage=1,
                      enemy_timer=1, is_reimu_a=True)
    assert r.cherry_gain == 10


def test_settle_damage_reimu_a_stage_reduction() -> None:
    """ReimuA stage 4/5-6 非 boss 伤害减免 (EnemyManager.cpp:822-834)。

    整段在樱点块内: 仅 (boss 或未 focus) 且非 bomb 时生效。
    """
    # stage4 非 boss: damage -= damage//4 + damage//16 (40 → 40-10-2=28)
    r = settle_damage(40, is_boss=False, is_focus=False, stage=4,
                      enemy_timer=2, is_reimu_a=True)
    assert r.damage == 28 and r.score_code == 50  # 28//5*10
    # 减免在 70 封顶之前: 100 → 100-25-6=69
    r = settle_damage(100, is_boss=False, is_focus=False, stage=4,
                      enemy_timer=2, is_reimu_a=True)
    assert r.damage == 69
    # stage5/6 非 boss: 减半 (40 → 20, 100 → 50)
    for st in (5, 6):
        r = settle_damage(40, is_boss=False, is_focus=False, stage=st,
                          enemy_timer=2, is_reimu_a=True)
        assert r.damage == 20
        r = settle_damage(100, is_boss=False, is_focus=False, stage=st,
                          enemy_timer=2, is_reimu_a=True)
        assert r.damage == 50
    # stage7 不减
    r = settle_damage(40, is_boss=False, is_focus=False, stage=7,
                      enemy_timer=2, is_reimu_a=True)
    assert r.damage == 40
    # boss 不减 (isBoss 排除)
    r = settle_damage(40, is_boss=True, is_focus=False, stage=5,
                      enemy_timer=2, is_reimu_a=True)
    assert r.damage == 40
    # focus 的普通敌: 樱点块不进 → 不减
    r = settle_damage(40, is_boss=False, is_focus=True, stage=5,
                      enemy_timer=2, is_reimu_a=True)
    assert r.damage == 40
    # bomb 中: 樱点块不进 → 不减
    r = settle_damage(40, is_boss=False, is_focus=False, stage=5,
                      enemy_timer=2, is_reimu_a=True, bomb_in_use=True)
    assert r.damage == 40
    # 非 ReimuA stage5 不减
    r = settle_damage(40, is_boss=False, is_focus=False, stage=5,
                      enemy_timer=2, is_reimu_a=False)
    assert r.damage == 40
