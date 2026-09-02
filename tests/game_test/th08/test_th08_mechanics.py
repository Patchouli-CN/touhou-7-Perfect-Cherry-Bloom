"""th08 机制全量测试 —— 阶段 3 单 B。

覆盖: 符卡动态分(bonusProgress/bonusCounter 模型, Spellcard.cpp:728-737/
1293-1301)、生存符分上限(op155)、收取时刻符点档(Spellcard.cpp:1030-1050)、
决死窗公式(Player.cpp:535-557)与决死冻结(deathbombFreezeActive)、
时刻过面增量/Bad Ending(GameManager.cpp:1379-1470/:342-348)、
4A/4B 分支(:1483-1505)、妖率槽界夹取/射击坡道/形态切换、
使魔链死亡掉符点(EnemyManager.cpp:229-345)、结算评级
(ResultScreen.cpp:2153-2290)。

纯逻辑用例(boss/player/results)不打标记; world 层全打 @needs_data。
"""

from __future__ import annotations

import touhou  # noqa: F401  # import 即完成 th08 全维度注册
from touhou.engine.ecl import EclContextArgs, Vec3
from touhou.games.th08 import ecl_vm
from touhou.games.th08.boss import TIMEOUT_SPELL_SCORE_LIMIT, Th08Boss
from touhou.games.th08.ecl_vm import Th08EclOpcode as Op
from touhou.games.th08.globals import Th08Globals
from touhou.games.th08.items import ItemType
from touhou.games.th08.player import PlayerState, Th08Player
from touhou.games.th08.results import RunStats, clear_percent, rating
from touhou.games.th08.world import ImperishableNight
from touhou.schema.shot_data import ShotData, ShotLevel

from .conftest import needs_data
from .test_th08_ecl import _build_ecl, _f, _instr
from .test_th08_world import (
    _inject_ecl,
    _isolate,
    _move_keys,
    _spellcard_args,
    _tick_until_alive,
)


# ---- 纯逻辑: 符卡动态分(Spellcard bonusProgress/bonusCounter) ----


def test_boss_dynamic_score_decay() -> None:
    """bonusCounter = (bonus − bonus/7)/(时限秒) (Spellcard.cpp:735-736);
    每帧 bonusProgress -= bonusCounter/60 并向下取整 10 (:1299-1301)。
    7,000,000/3600 帧: bonusCounter=100000, 每帧 -1666 取整 10。"""
    b = Th08Boss()
    b.begin_spellcard(0, 3600, bonus=7_000_000)
    assert b.capture_score == 7_000_000
    assert b.bonus_counter == (7_000_000 - 7_000_000 // 7) // 60 == 100_000
    b.tick()
    assert b.capture_score == 6_998_330  # 7000000-1666=6998334 → 取整 10
    prev = b.capture_score
    for _ in range(599):
        b.tick()
        assert b.capture_score <= prev and b.capture_score % 10 == 0
        prev = b.capture_score
    assert b.capture_score == 5_998_000
    # 时限跑完 ≈ bonus/7 (每帧取整 10 的累计损失, C 同)
    for _ in range(3000):
        b.tick()
    assert b.capture_score == 988_000


def test_boss_timeout_spell_score_limit() -> None:
    """生存符(op155): scoreLimit=99999990 (Spellcard.cpp:733,
    EclRunHigh.inl:830), 不衰减(:1294-1296 的 TIMEOUT_SPELL 门控);
    AddBonusProgress 夹取到 scoreLimit, 未到时 bonusCounter += amount/120
    (:1248-1255); 收取符点恒 700 (:1032-1033)。"""
    b = Th08Boss()
    b.begin_spellcard(1, 3600, bonus=5_000_000, timeout_spell=True)
    assert b.score_limit == TIMEOUT_SPELL_SCORE_LIMIT == 99999990
    for _ in range(120):
        b.tick()
    assert b.capture_score == 5_000_000  # 不衰减
    b.add_bonus_progress(10**9)
    assert b.capture_score == 99999990  # 夹取
    r = b.end_spellcard()
    assert r["captured"] and r["score"] == 99999990
    assert r["pending_time_orbs"] == 700
    assert b.was_captured


def test_boss_capture_time_orb_tiers() -> None:
    """收取时刻符点 (Spellcard.cpp:1035-1049): 剩余 ≥ 时限−时限/7 → 1000;
    ≥180 → 900*(剩余−180)/(i−180)+100; 否则 100。"""
    b = Th08Boss()
    b.begin_spellcard(2, 3600, bonus=1_000_000)
    assert b._capture_time_orbs() == 1000  # tr=3600 ≥ 3600−514
    b.timer = 600  # tr=3000
    assert b._capture_time_orbs() == 900 * 2820 // 2906 + 100
    b.timer = 3500  # tr=100 < 180
    assert b._capture_time_orbs() == 100
    r = b.end_spellcard()
    assert r["captured"] and r["pending_time_orbs"] == 100
    # 收取奖 = bonusProgress (Spellcard.cpp:1029): 1 tick 后
    # bonusCounter=(1000000-142857)//60=14285, 1000000-238=999762 取整 10
    b2 = Th08Boss()
    b2.begin_spellcard(3, 3600, bonus=1_000_000)
    b2.tick()
    r2 = b2.end_spellcard()
    assert r2["score"] == 999_760


def test_boss_capture_score_is_bonus_progress() -> None:
    """收取分 = 当前 bonusProgress; AddBonusProgress 夹取 scoreLimit
    (=初值 bonus), 未到上限时 bonusCounter += amount/120
    (Spellcard.cpp:1243-1257)。"""
    b = Th08Boss()
    b.begin_spellcard(4, 3600, bonus=1_000_000)
    for _ in range(100):
        b.tick()
    decayed = b.capture_score
    assert decayed < 1_000_000
    b.add_bonus_progress(8000)
    b.add_bonus_progress(8000)
    assert b.capture_score == min(decayed + 16000, 1_000_000)
    if b.capture_score < 1_000_000:
        assert b.bonus_counter == 14285 + 2 * (8000 // 120)
    # 已达上限时只夹取, bonusCounter 不涨 (:1249-1255 的 else 分支)
    b.add_bonus_progress(10**9)
    assert b.capture_score == 1_000_000
    # 用弹/死亡 → 捕获失效, 收取分 0
    b3 = Th08Boss()
    b3.begin_spellcard(5, 3600, bonus=1_000_000)
    b3.mark_bombed()
    r3 = b3.end_spellcard()
    assert not r3["captured"] and r3["score"] == 0


def test_boss_bonus_updates_disabled() -> None:
    """op184: 禁更新时不衰减也不加 bonusProgress (Spellcard.cpp:1244/:1294)。"""
    b = Th08Boss()
    b.begin_spellcard(6, 3600, bonus=1_000_000)
    b.bonus_updates_disabled = 1
    for _ in range(120):
        b.tick()
    assert b.capture_score == 1_000_000
    b.add_bonus_progress(8000)
    assert b.capture_score == 1_000_000


# ---- 纯逻辑: 决死窗公式 (Player.cpp:535-557) ----


def _synth_player(shot_type: int = 0) -> Th08Player:
    sd = ShotData(
        initial_bombs=2.0,
        initial_respawn_timer=18,
        hitbox_radius=6.0,
        grab_item_radius=48.0,
        item_collect_speed=8.0,
        item_collect_radius=32.0,
        cherry_penalty_multiplier=0.0,
        poc_y=128.0,
        speed=4.0,
        speed_focus=2.0,
        speed_diagonal=4.0,
        speed_diagonal_focus=2.0,
        levels=[ShotLevel(0, [])],
    )
    return Th08Player(shot_data=sd, shot_type=shot_type)


def test_deathbomb_window_formula() -> None:
    """决死窗 = min(bombs×6+(符点达标?7:0), 15); 符卡战 ×2 封顶 30;
    灵梦系(0/4/5) 再 ×9/5; 无弹 = 2 (Player.cpp:535-557/:589)。"""
    p = _synth_player(0)  # 灵梦系
    p.ctx_bombs, p.ctx_time_orb_ready, p.ctx_spellcard_active = 2, False, False
    p.die()
    assert p.respawn_timer == min(2 * 6, 15) * 9 // 5 == 12 * 9 // 5
    p = _synth_player(1)  # 魔理沙系(无 ×9/5)
    p.ctx_bombs, p.ctx_time_orb_ready, p.ctx_spellcard_active = 2, True, False
    p.die()
    assert p.respawn_timer == min(2 * 6 + 7, 15) == 15
    p = _synth_player(1)
    p.ctx_bombs, p.ctx_time_orb_ready, p.ctx_spellcard_active = 3, True, True
    p.die()
    assert p.respawn_timer == min(min(3 * 6 + 7, 15) * 2, 30) == 30
    p = _synth_player(0)  # 灵梦系 + 符卡战: 30×9/5=54 (封顶在 ×9/5 前)
    p.ctx_bombs, p.ctx_time_orb_ready, p.ctx_spellcard_active = 3, True, True
    p.die()
    assert p.respawn_timer == 54
    p = _synth_player(1)
    p.ctx_bombs = 0  # 无弹 → 2
    p.die()
    assert p.respawn_timer == 2


def test_transform_flags_constants() -> None:
    """人妖门控 transformFlags 位 (BulletManager.hpp:147-148)。"""
    assert ecl_vm._BULLET_TRANSFORM_ONLY_WHEN_PLAYER_YOUKAI == 0x8000
    assert ecl_vm._BULLET_TRANSFORM_ONLY_WHEN_PLAYER_HUMAN == 0x10000


# ---- 纯逻辑: 结算评级 (ResultScreen.cpp:2153-2290) ----


def test_rating_shape() -> None:
    """评级: 通关 +70 / 难度权重 / 续死炸惩罚 / 符卡权重; 通关率基准
    195559(本篇)/80000(EX)。"""
    clear = RunStats(
        score=100_000_000, difficulty=1, cleared=True, spellcards_captured=10
    )
    fail = RunStats(score=100_000_000, difficulty=1, clear_percent=0.5)
    assert rating(clear) > rating(fail)
    lunatic = RunStats(
        score=100_000_000, difficulty=3, cleared=True, spellcards_captured=10
    )
    assert rating(lunatic) > rating(clear)  # Lunatic +30 vs Normal -10, 符卡 ×2
    dead = RunStats(
        score=100_000_000, difficulty=1, cleared=True, spellcards_captured=10,
        deaths=5, bombs_used=8, retries=1,
    )
    assert rating(clear) - rating(dead) == 5 * 5 + 8 * 2 + 1 * 10
    assert clear_percent(195559 / 60) == 0.99
    assert clear_percent(80000 / 60, extra=True) == 0.99
    assert clear_percent(100.0) < 0.99


# ---- world 层(needs_data): 符卡全流程 ----


def _inject_boss_sub(
    g: ImperishableNight,
    *,
    life: int,
    timeout: int,
    bonus: int,
    timeout_sub: int = 1,
) -> None:
    """注入一个 boss sub: SET_BOSS + 计时回调 + BEGIN_SPELLCARD 后空转;
    sub1 = 超时回调(END_SPELLCARD 后 STOP)。"""
    _inject_ecl(
        g,
        [
            _instr(0, int(Op.SET_ANM), (4,)),
            _instr(0, int(Op.SET_HITBOX_SIZE), (_f(48.0), _f(48.0))),
            _instr(0, int(Op.ENABLE_ENEMY_FLAGS), (0x27,)),
            _instr(0, int(Op.SET_LIFE), (life,)),
            _instr(0, int(Op.SET_BOSS), (0,)),
            _instr(0, int(Op.SET_TIMER_CALLBACK), (timeout, timeout_sub)),
            _instr(0, int(Op.BEGIN_SPELLCARD),
                   _spellcard_args(0, 5, bonus, "テスト".encode("shift_jis"))),
            _instr(0, int(Op.WAIT), (99999,)),
        ],
        [
            _instr(0, int(Op.END_SPELLCARD)),
            _instr(0, int(Op.STOP)),
        ],
    )


@needs_data
def test_spellcard_capture_flow() -> None:
    """符卡 begin → 击破捕获全流程: 变量 10099/10100 接通真实状态,
    动态分衰减, 收取入账(分 + 计数 + 时刻符点 1000)。"""
    g = ImperishableNight(character=0, difficulty=1, seed=42)
    _tick_until_alive(g)
    _isolate(g)
    _inject_boss_sub(g, life=40, timeout=3600, bonus=1_000_000)
    px = g.player.pos.x
    e = g.ecl_host.spawn_enemy(
        0, Vec3(px, 100.0, 0.0), life=-1, item_drop=-2, score=1000,
        mirror=0, context_args=EclContextArgs(),
    )
    assert e is not None
    assert g.boss is not None and g.boss.is_active
    orbs0 = g.globals.current_time_orbs
    timer_seen = None
    for _ in range(30):
        g.tick(keys=(False, False, False, False, False))
        if g.boss is not None and g.boss.is_active:
            assert g.ecl_world.spellcard_capture_status == 1  # 10099
            timer_seen = g.ecl_world.spellcard_timer_frames  # 10100
    assert timer_seen is not None and 3600 - 40 < timer_seen < 3600
    assert g.boss is not None and g.boss.capture_score < 1_000_000  # 衰减中
    # 击破 → 捕获
    score0 = g.globals.score
    for _ in range(600):
        g.tick(keys=(False, False, False, False, False))
        if g.boss is None:
            break
    assert g.boss is None, "boss 未被击破"
    assert g.globals.spell_cards_captured == 1
    assert g.globals.score > score0
    # 快速收取 → 时刻符点 +1000 (Spellcard.cpp:1038-1040)
    assert g.globals.current_time_orbs == orbs0 + 1000
    # WasCaptured: 符卡结束后变量 10099 仍读 1 (EclOperandsInt.cpp:145-147)
    g.tick(keys=(False, False, False, False, False))
    assert g.ecl_world.spellcard_capture_status == 1


@needs_data
def test_spellcard_timeout_flow() -> None:
    """符卡超时: 捕获失效(不收分/计数), 自机无敌 70 帧
    (EnemyManager.cpp:630-633), 清弹; 生存符超时则不失捕获。"""
    g = ImperishableNight(character=0, difficulty=1, seed=42)
    _tick_until_alive(g)
    _isolate(g)
    _inject_boss_sub(g, life=999999, timeout=120, bonus=1_000_000)
    px = g.player.pos.x
    g.ecl_host.spawn_enemy(
        0, Vec3(px, 100.0, 0.0), life=-1, item_drop=-2, score=1000,
        mirror=0, context_args=EclContextArgs(),
    )
    assert g.boss is not None and g.boss.is_active
    invuln_seen = False
    for _ in range(300):
        g.tick(keys=(False, False, False, False, False))
        if g.player.state == PlayerState.INVULNERABLE and g.player.invuln >= 60:
            invuln_seen = True
        if g.boss is None:
            break
    assert g.boss is None, "超时后 boss 应退场"
    assert g.globals.spell_cards_captured == 0
    assert invuln_seen, "超时未给自机 70 帧无敌"
    g.tick(keys=(False, False, False, False, False))  # 再刷一帧世界快照
    assert g.ecl_world.spellcard_capture_status == 0  # 未捕获
    assert g.ecl_world.spellcard_timer_frames == 0


@needs_data
def test_last_spell_timeout_keeps_capture() -> None:
    """生存符(op155): 超时不失捕获 (EnemyManager.cpp:626-628),
    符分上限 99999990 同步到 boss。"""
    g = ImperishableNight(character=0, difficulty=1, seed=42)
    _tick_until_alive(g)
    _isolate(g)
    _inject_ecl(
        g,
        [
            _instr(0, int(Op.SET_ANM), (4,)),
            _instr(0, int(Op.SET_HITBOX_SIZE), (_f(48.0), _f(48.0))),
            _instr(0, int(Op.ENABLE_ENEMY_FLAGS), (0x27,)),
            _instr(0, int(Op.SET_LIFE), (999999,)),
            _instr(0, int(Op.SET_BOSS), (0,)),
            _instr(0, int(Op.SET_TIMER_CALLBACK), (120, 1)),
            _instr(0, int(Op.BEGIN_SPELLCARD),
                   _spellcard_args(0, 5, 5_000_000, "ラスト".encode("shift_jis"))),
            _instr(0, int(Op.SET_TIMEOUT_SPELL), (1,)),
            _instr(0, int(Op.WAIT), (99999,)),
        ],
        [
            _instr(0, int(Op.END_SPELLCARD)),
            _instr(0, int(Op.STOP)),
        ],
    )
    px = g.player.pos.x
    g.ecl_host.spawn_enemy(
        0, Vec3(px, 100.0, 0.0), life=-1, item_drop=-2, score=1000,
        mirror=0, context_args=EclContextArgs(),
    )
    for _ in range(10):
        g.tick(keys=(False, False, False, False, False))
    assert g.boss is not None
    assert g.boss.score_limit == TIMEOUT_SPELL_SCORE_LIMIT  # op155 同步
    assert g.boss.capture_score == 5_000_000  # 生存符不衰减
    for _ in range(300):
        g.tick(keys=(False, False, False, False, False))
        if g.boss is None:
            break
    assert g.boss is None
    # 生存符超时仍捕获(捕获标志未被超时清掉), 符点 +700
    assert g.globals.spell_cards_captured == 1
    assert g.globals.current_time_orbs >= 700


# ---- world 层(needs_data): 决死/冻结 ----


@needs_data
def test_deathbomb_window_freeze_and_cost() -> None:
    """符卡战中被弹: 决死窗按动态公式, deathbombFreezeActive 冻结
    ECL/符卡计时/自机弹; 窗内按 B → 决死耗 2 弹, 冻结解除, 残机不扣。"""
    g = ImperishableNight(character=0, difficulty=1, seed=42)
    _tick_until_alive(g)
    _isolate(g)
    _inject_boss_sub(g, life=999999, timeout=3600, bonus=1_000_000)
    px, py = g.player.pos.x, g.player.pos.y
    g.ecl_host.spawn_enemy(
        0, Vec3(px, 100.0, 0.0), life=-1, item_drop=-2, score=1000,
        mirror=0, context_args=EclContextArgs(),
    )
    g.tick(keys=(False, False, False, False, False))
    assert g.boss is not None and g.boss.is_active
    # 体术撞死: 在自机位置生成碰撞敌
    lives0 = g.globals.lives_remaining
    bombs0 = g.globals.bombs_remaining
    g.ecl_host.spawn_enemy(
        0, Vec3(px, py, 0.0), life=-1, item_drop=-2, score=100,
        mirror=0, context_args=EclContextArgs(),
    )
    g.tick(keys=(False, False, False, False, False))
    assert g.player.state == PlayerState.DEAD
    # 决死窗 = min(bombs×6(+7), 15)×2(符卡) 封顶 30, 灵梦系 ×9/5
    bombs = int(bombs0)
    w = min(bombs * 6 + (7 if g.globals.current_time_orbs >= 2500 else 0), 15)
    w = min(w * 2, 30) * 9 // 5
    assert g.player.respawn_timer in (w, w - 1)  # 当帧可能已倒数
    assert g._deathbomb_freeze, "符卡战被弹应置决死冻结"
    # 冻结: boss 计时停
    t0 = g.boss.timer
    g.tick(keys=(False, False, False, False, False))
    assert g.boss is not None and g.boss.timer == t0
    # 决死: 耗 2 弹, 复活无敌, 冻结解除
    g.tick(keys=(False, False, False, False, False), bomb=True)
    assert g.player.state == PlayerState.INVULNERABLE
    assert g.globals.bombs_remaining == bombs0 - 2
    assert g.globals.lives_remaining == lives0  # 残机不扣
    assert not g._deathbomb_freeze
    # 冻结解除后符卡计时恢复
    g.tick(keys=(False, False, False, False, False))
    g.tick(keys=(False, False, False, False, False))
    assert g.boss is not None and g.boss.timer > t0


@needs_data
def test_death_without_bomb_short_window() -> None:
    """无弹被弹: 决死窗 = 2 (Player.cpp:589), 无冻结, 正常丢残机。"""
    g = ImperishableNight(character=1, difficulty=1, seed=42)
    _tick_until_alive(g)
    _isolate(g)
    g.globals.bombs_remaining = 0
    _inject_boss_sub(g, life=999999, timeout=3600, bonus=1_000_000)
    px, py = g.player.pos.x, g.player.pos.y
    g.ecl_host.spawn_enemy(
        0, Vec3(px, 100.0, 0.0), life=-1, item_drop=-2, score=1000,
        mirror=0, context_args=EclContextArgs(),
    )
    g.tick(keys=(False, False, False, False, False))
    assert g.boss is not None and g.boss.is_active
    lives0 = g.globals.lives_remaining
    g.ecl_host.spawn_enemy(
        0, Vec3(px, py, 0.0), life=-1, item_drop=-2, score=100,
        mirror=0, context_args=EclContextArgs(),
    )
    g.tick(keys=(False, False, False, False, False))
    assert g.player.state == PlayerState.DEAD
    assert not g._deathbomb_freeze  # 无弹 → 不冻结
    for _ in range(200):
        g.tick(keys=(False, False, False, False, False))
        if g.player.state == PlayerState.INVULNERABLE:
            break
    assert g.player.state == PlayerState.INVULNERABLE, "决死窗耗尽未重生"
    assert g.globals.lives_remaining == lives0 - 1


# ---- world 层(needs_data): 时刻/结局/换关分支 ----


@needs_data
def test_clock_increment_and_bad_ending() -> None:
    """过面时刻增量: 符点达标 +1 / 未达 +2 (GameManager.cpp:1379-1470);
    时刻 ≥12 过面 → Bad Ending (:342-348), 结局文件 end00a.end。"""
    g = ImperishableNight(character=0, difficulty=1, seed=42)
    assert g.ecl_host is not None
    clock = g.ecl_host.clock
    # 达标 → +1
    g.globals.current_time_orbs = 9999
    g._on_stage_results()
    assert clock.units == 1
    assert g.stage_results["snapshot"]["clock_increment"] == 1
    # 未达 → +2
    g.globals.current_time_orbs = 0
    g._on_stage_results()
    assert clock.units == 3
    # 时刻到顶 → Bad Ending
    clock.units = 12
    g._on_next_level()
    assert g.ending is not None and g.ending.bad
    assert not g.cleared
    g.finish_ending()
    assert g.result is not None
    assert g.result["bad_ending"] and not g.result["cleared"]
    # 时刻 11 → 正常换关
    g2 = ImperishableNight(character=0, difficulty=1, seed=42)
    g2.ecl_host.clock.units = 11
    g2._on_next_level()
    assert g2.ending is None and g2._pending_next_level


@needs_data
def test_final_route_endings() -> None:
    """6A(=7) 通关 → 结局 b; 6B(=8) 通关 → 结局 c (Ending.cpp:567-577);
    EX(=9) → 直接总结算(GameManager.cpp:357-362)。"""
    g = ImperishableNight(character=1, difficulty=1, seed=42)  # 魔理沙队 → 01
    g.stage_no = 7
    g._on_next_level()
    assert g.ending is not None and not g.ending.bad
    assert "end01b" in (g.ending.path or "end01b")  # 资源缺失时 path 记录意图
    g2 = ImperishableNight(character=1, difficulty=1, seed=42)
    g2.stage_no = 8
    g2._on_next_level()
    assert g2.ending is not None and "end01c" in (g2.ending.path or "end01c")
    g2.finish_ending()
    assert g2.result is not None and g2.result["cleared"]
    g3 = ImperishableNight(character=0, difficulty=4, seed=42)
    g3.stage_no = 9
    g3._on_next_level()
    assert g3.ending is None and g3.result is not None and g3.result["cleared"]


@needs_data
def test_stage4_branch_by_character() -> None:
    """3 面后分支: 灵梦系(0/4/5)/妖梦系(3/10/11) → 4B(=5),
    魔理沙系(1/6/7)/咲夜系(2/8/9) → 4A(=4) (GameManager.cpp:1483-1505)。"""
    for c, want in [(0, 5), (1, 4), (2, 4), (3, 5), (4, 5), (5, 5), (6, 4),
                    (7, 4), (8, 4), (9, 4), (10, 5), (11, 5)]:
        g = ImperishableNight(character=c, difficulty=1, seed=42)
        g.stage_no = 3
        assert g._next_stage_no() == want, f"character={c}"


@needs_data
def test_stage_results_bonus() -> None:
    """过关奖励 (Gui.cpp:1032-1087): Clear 表 + Graze*50 + Point*5000 +
    符点*100, Normal 无难度修正, lifeCount=3 → ×0.5, AddScore 合计 = bonus。"""
    g = ImperishableNight(character=0, difficulty=1, seed=42)
    g.globals.graze_in_stage = 10
    g.globals.point_items_collected_this_stage = 20
    g.globals.current_time_orbs = 30
    score0 = g.globals.score
    g._on_stage_results()
    # (1000000 + 500 + 100000 + 3000) * 5 // 10 = 551750
    assert g.stage_results["total"] == 551750
    assert g.globals.score - score0 == 551750  # AddScore ×10 内部 //10


# ---- world 层(needs_data): 妖率计 ----


@needs_data
def test_youkai_gauge_bounds_clamp() -> None:
    """槽界夹取按机体 (Player.cpp:1607-1639): 单人人类妖侧封顶 2000,
    咏唱妖梦半幅 -5000。"""
    g = ImperishableNight(character=0, difficulty=1, seed=42)
    g.globals.add_to_youkai_gauge(99999)
    assert g.globals.youkai_gauge == 10000
    g.globals.add_to_youkai_gauge(-999999)
    assert g.globals.youkai_gauge == -10000
    g4 = ImperishableNight(character=4, difficulty=1, seed=42)  # 单人灵梦
    g4.globals.add_to_youkai_gauge(99999)
    assert g4.globals.youkai_gauge == 2000  # IsSoloHuman 妖侧封顶
    g3 = ImperishableNight(character=3, difficulty=1, seed=42)  # 咏唱妖梦
    g3.globals.add_to_youkai_gauge(-99999)
    assert g3.globals.youkai_gauge == -5000  # 人侧半幅


@needs_data
def test_youkai_gauge_shooting_ramp() -> None:
    """射击坡道 (Player.cpp:898-935): 非 focus 持续射击 gauge 渐减(人向),
    停火 30 帧后回中。"""
    g = ImperishableNight(character=0, difficulty=1, seed=42)
    _tick_until_alive(g)
    _isolate(g)
    for f in range(120):
        g.tick(keys=_move_keys(f))  # 默认 firing
    assert g.globals.youkai_gauge < 0, "非 focus 射击应把人向拉"
    low = g.globals.youkai_gauge
    for _ in range(120):
        # 6 元组末位 False = 停火(5 元组缺省 firing=True)
        g.tick(keys=(False, False, False, False, False, False))
    # 停火回中(向 0)
    assert g.globals.youkai_gauge > low


@needs_data
def test_youkai_form_switching() -> None:
    """人妖形态 (PlayerBomb.cpp:31-34): 双人组 focus=妖; 单人人类恒人,
    单人妖怪恒妖; 变量 10097 接 world 快照。"""
    g = ImperishableNight(character=0, difficulty=1, seed=42)
    _tick_until_alive(g)
    g.tick(keys=(False, False, False, False, True))  # focus
    assert g.player.is_youkai
    assert g.ecl_world.player_is_youkai == 1
    g.tick(keys=(False, False, False, False, False))
    assert not g.player.is_youkai
    assert g.ecl_world.player_is_youkai == 0
    g4 = ImperishableNight(character=4, difficulty=1, seed=42)  # 单人灵梦
    assert not g4.player.is_youkai
    g4.player.focus = True
    assert not g4.player.is_youkai
    g5 = ImperishableNight(character=5, difficulty=1, seed=42)  # 单人紫
    assert g5.player.is_youkai


# ---- world 层(needs_data): 使魔链掉符点 ----


@needs_data
def test_familiar_chain_death_time_orbs() -> None:
    """使魔链父被击坠: 拆链, 每子机散 2n+10(咏唱组 n<8) 个上升符点 +
    1 小点, 父掉 2n 个吸附符点 (EnemyManager.cpp:229-330)。"""
    g = ImperishableNight(character=0, difficulty=1, seed=42)
    _tick_until_alive(g)
    _isolate(g)
    _inject_ecl(
        g,
        [
            _instr(0, int(Op.SET_ANM), (4,)),
            _instr(0, int(Op.SET_HITBOX_SIZE), (_f(48.0), _f(48.0))),
            _instr(0, int(Op.ENABLE_ENEMY_FLAGS), (0x27,)),
            _instr(0, int(Op.SET_LIFE), (30,)),
            _instr(0, int(Op.WAIT), (99999,)),
        ],
        [
            _instr(0, int(Op.SET_ANM), (4,)),
            _instr(0, int(Op.WAIT), (99999,)),
        ],
    )
    px = g.player.pos.x
    parent = g.ecl_host.spawn_enemy(
        0, Vec3(px, 100.0, 0.0), life=-1, item_drop=-2, score=1000,
        mirror=0, context_args=EclContextArgs(),
    )
    assert parent is not None
    children = []
    for i in range(3):
        c = g.ecl_host.spawn_familiar(
            90, 1, Vec3(px + 40.0 * (i + 1), 100.0, 0.0), 500, -2, 100,
            EclContextArgs(), parent=parent.state,
        )
        assert c is not None
        children.append(c)
    assert g.ecl_host.count_parent_chain(parent.state) == 3
    for _ in range(600):
        g.tick(keys=(False, False, False, False, False))
        if not parent.alive:
            break
    assert not parent.alive, "链父未被击坠"
    time_items = [it for it in g.items.alive() if it.type == ItemType.TIME]
    # 3 子机 × 16 (2*3+10) + 父 6 (2*3) = 54; 符点在场上(上升/吸附中)
    assert len(time_items) + g.globals.current_time_orbs >= 54
    # 子机拆链后仍存活(独立敌人)
    assert all(c.alive for c in children)
    assert g.ecl_host.count_parent_chain(children[0].state) == 0


@needs_data
def test_chain_child_death_gauge_penalty() -> None:
    """链上子机被击坠: 妖率 -gauge/12 + 掉 1 吸附符点 + 符点妖率抑制 50
    + 掉落清零 (EnemyManager.cpp:332-345)。直接调结算路径(打枪击杀的
    集成覆盖见 test_familiar_chain_death_time_orbs)。"""
    g = ImperishableNight(character=0, difficulty=1, seed=42)
    _tick_until_alive(g)
    _isolate(g)
    _inject_ecl(
        g,
        [
            _instr(0, int(Op.SET_ANM), (4,)),
            _instr(0, int(Op.SET_HITBOX_SIZE), (_f(48.0), _f(48.0))),
            _instr(0, int(Op.ENABLE_ENEMY_FLAGS), (0x27,)),
            _instr(0, int(Op.SET_LIFE), (30,)),
            _instr(0, int(Op.WAIT), (99999,)),
        ],
    )
    px = g.player.pos.x
    parent = g.ecl_host.spawn_enemy(
        0, Vec3(px, 60.0, 0.0), life=-1, item_drop=-2, score=1000,
        mirror=0, context_args=EclContextArgs(),
    )
    child = g.ecl_host.spawn_enemy(
        0, Vec3(px, 100.0, 0.0), life=-1, item_drop=1, score=1000,
        mirror=0, context_args=EclContextArgs(),
    )
    assert parent is not None and child is not None
    # 手工挂链(模拟使魔附着): child 挂在 parent 下
    g.ecl_host._attach_parent[id(child.state)] = parent.state
    g.ecl_host._attach_next[id(parent.state)] = child.state
    parent.state.linked_child_count += 1
    g.globals.youkai_gauge = 1200
    g._detach_chain_rewards(child.state)
    assert g.globals.youkai_gauge == 1200 - 1200 // 12 == 1100
    assert g.player.time_orb_gauge_suppression == 50
    assert child.state.item_drop == -2  # 掉落清零
    time_items = [
        it for it in g.items.alive() if it.type == ItemType.TIME
    ]
    assert len(time_items) == 1  # 1 个吸附时刻符点


# ---- world 层(needs_data): 结算 ----


@needs_data
def test_final_result_rating_keys() -> None:
    """final_result: 评级/clear_percent/bad_ending/时刻等键齐备且幂等。"""
    g = ImperishableNight(character=0, difficulty=1, seed=42)
    for f in range(300):
        g.tick(keys=_move_keys(f))
    r = g.final_result(cleared=False)
    assert "rating" in r and "clear_percent" in r and "bad_ending" in r
    assert "clock" in r and "time_orbs" in r
    assert not r["cleared"] and not r["bad_ending"]
    assert g.final_result(cleared=True) is r  # 幂等: 一局只结算一次


# ---- world 层(needs_data): 真实数据长帧冒烟 ----


@needs_data
def test_stage1_long_smoke_spellcard_endings() -> None:
    """1 面真实数据长帧: 至少见到一张符卡的收束(收取或超时);
    若全程跑完则换关进 2 面。"""
    g = ImperishableNight(character=0, difficulty=1, seed=42)
    was_active = False
    endings = 0
    for f in range(14000):
        g.tick(keys=_move_keys(f), advance=True, bomb=(f % 800 == 0))
        if g.game_over:
            g.game_over = False
            g.lives = 3.0
        active = g.spellcard_active()
        if was_active and not active:
            endings += 1
        was_active = active
        if g.stage_no >= 2:
            break
    assert endings >= 1 or g.stage_no >= 2, "14000 帧内无符卡收束"
