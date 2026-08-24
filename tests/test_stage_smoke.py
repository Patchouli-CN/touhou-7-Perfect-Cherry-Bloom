"""8 关 E2E 冒烟: 真实 ecldata1..8 全关(或中段锚点)跑通。

harness(与 tmp_title/exins_smoke3.py 同语义):
- 无限命(game_over 即重置), Z 脉冲推对话, 左右横移;
- 玩家每帧吸附到主攻 boss(血最少的存活 boss)正下方, 保证命中收阶段;
- 卡死兜底: 进度签名(boss sub/血/回调阈值 + 时间轴位置) stall_frames 无变化
  → 压血(life>1 → 1, 无敌帧清零; life>=900000 的"无敌僚机"不压, 见 4 面三姐妹)。
- 确定性: Rng 种子固定(impl 0x5EED / EclWorld 0), 输入序列确定 → 可重复。

帧数预算按锚点数据(tmp_title/smoke_b*.log)裁剪, 慢关只跑到中段锚点,
整体耗时控制在 ~2 分钟内。
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, r"D:\python_play\Touhou08")

from touhou.games.th07.world import PerfectCherryBloom  # noqa: E402
import touhou.engine.enemies as en_mod  # noqa: E402
from touhou.engine.ecl import EclOpcode  # noqa: E402
from touhou.games.th07.ecl_vm import EclMachineTh07 as EclMachine  # noqa: E402
from touhou.engine.enemies import EclEnemy  # noqa: E402
from touhou.utils import Vec2  # noqa: E402
from tests.test_ecl import _instr, build_ecl  # noqa: E402

DAT = Path(r"D:\TOUHOU_GAME\[th07] 东方妖妖梦 (日文版)\th07.dat")
NEEDS_DAT = pytest.mark.skipif(not DAT.exists(), reason="需要真实 th07.dat")
OP = EclOpcode


def _move_keys(f: int, period: int = 40):
    return ((f // period) % 2 == 1, (f // period) % 2 == 0, False, False, False)


def _bosses(g: PerfectCherryBloom):
    return [e for e in g.host.all()
            if getattr(e, "is_boss", False) and e.alive]


def _crush(g: PerfectCherryBloom) -> None:
    for e in _bosses(g):
        st = e.state
        if st.invincibility_timer > 0:
            st.invincibility_timer = 0
        if 1 < st.life < 900000:
            st.life = 1


def _signature(g: PerfectCherryBloom) -> tuple:
    sig = tuple(sorted(
        (id(e.state), e.machine.current.sub_id, e.state.life,
         e.state.timer_callback_threshold,
         tuple(e.state.life_callback_threshold))
        for e in _bosses(g)))
    return sig + tuple((t.idx, t.time, t.done) for t in g.ecl_timelines)


class SmokeResult:
    def __init__(self) -> None:
        self.ex: Counter = Counter()
        self.ex_first: dict[int, int] = {}
        self.spell_frames: list[int] = []
        self.stalls = 0
        self.max_bullets = 0
        self.cleared = False
        self.frames = 0
        self.saw_boss = False
        self.boss_kills: Counter = Counter()  # death_type → 次数
        self.min_time_scale = 1.0   # 减速场(ex10)生效证据
        self.slow_restored = False  # 减速后恢复 1.0 (ex11) 证据


def run_stage(stage: int, frames: int, *, stall_frames: int = 4800,
              difficulty: int = 1) -> tuple[PerfectCherryBloom, SmokeResult]:
    """跑到过关(1-5 面=换关, 6/7/8 面=结局/结算)或帧数上限。返回 (game, 统计)。"""
    g = PerfectCherryBloom(data_path=DAT, character=0, difficulty=difficulty)
    g.stage_no = stage
    g._load_ecl()
    r = SmokeResult()
    orig_ex = g.ecl_host.run_ex_instr

    def counting(idx, enemy, instr, ctx=None):
        ret = orig_ex(idx, enemy, instr, ctx=ctx)
        if ret:
            r.ex[idx] += 1
            r.ex_first.setdefault(idx, g.frame)
        return ret

    g.ecl_host.run_ex_instr = counting
    orig_begin = g.ecl_host.on_begin_spellcard

    def on_begin(st, gid, idx, name):
        r.spell_frames.append(g.frame)
        if orig_begin is not None:
            orig_begin(st, gid, idx, name)

    g.ecl_host.on_begin_spellcard = on_begin
    orig_kill = en_mod.EclEnemy.kill

    def kill_spy(self):
        if self.state.is_boss:
            r.boss_kills[self.state.death_type] += 1
        return orig_kill(self)

    en_mod.EclEnemy.kill = kill_spy
    last_sig = _signature(g)
    last_change = 0
    try:
        for _ in range(frames):
            g.tick(keys=_move_keys(g.frame), advance=(g.frame % 15 == 0))
            bs = _bosses(g)
            if bs:
                r.saw_boss = True
                # C++:754 起 hasNoCollision/isHittable 的 boss 不吃弹 —
                # 吸附目标优先选当前可受击者(如 4 面三姐妹轮换位)
                dmg = [e for e in bs
                       if not e.state.has_no_collision and e.state.is_hittable]
                lead = min(dmg or bs, key=lambda e: max(e.state.life, 0))
                g.player.pos = Vec2(lead.pos.x, min(lead.pos.y + 200, 400))
            if g.game_over:
                g.game_over = False
                g.result = None
                g.lives = 3.0
            r.max_bullets = max(r.max_bullets, len(g.bullets))
            ts = g.bullets.time_scale
            if ts < r.min_time_scale:
                r.min_time_scale = ts
            elif r.min_time_scale < 1.0 and ts == 1.0:
                r.slow_restored = True
            # 关卡推进后: 1-5 面过关 = 换关(stage_no 增加);
            # 6/7/8 面过关 = 结局/总结算
            if g.stage_no != stage or g.cleared or g.ending is not None:
                r.cleared = True
                break
            sig = _signature(g)
            if sig != last_sig:
                last_sig = sig
                last_change = g.frame
            elif g.frame - last_change > stall_frames:
                r.stalls += 1
                _crush(g)
                last_change = g.frame
    finally:
        en_mod.EclEnemy.kill = orig_kill
    r.frames = g.frame
    return g, r


# ---- kill() 死亡分支回归 (EnemyManager.cpp:943 OnUpdate life<=0 && canDie) ----

def _make_enemy(death_type: int, death_cb_sub: int = 1) -> EclEnemy:
    """裸 EclEnemy: sub0 主体(立即结束), sub1 死亡回调(SET_LIFE 500 复活)。"""
    f = build_ecl(
        [_instr(0, OP.SET_LIFE, (100,))],
        [_instr(0, OP.SET_LIFE, (500,))],
    )
    e = EclEnemy(EclMachine(f))
    st = e.state
    st.death_type = death_type
    st.death_callback_sub = death_cb_sub
    st.life = 0
    return e


def test_kill_death_type_0_despawns():
    """type 0 正常击坠: despawn + 计分; C 里死亡回调照样 CallEclSub(无害)。"""
    e = _make_enemy(0)
    assert e.kill() is True
    assert not e.alive and e.state.active == 0
    assert not e._kill_no_score


def test_kill_death_type_1_stays_active():
    """type 1: 计分, canDie=0, 保持 active 跑死亡 sub (C case 1)。"""
    e = _make_enemy(1)
    assert e.kill() is True
    assert e.alive and e.state.can_die == 0 and e.state.life == 0
    assert e.machine.current.sub_id == 1  # 死亡回调已入场
    assert not e._kill_no_score


def test_kill_death_type_2_phase_kill_stays_active():
    """type 2 阶段击破(4 面三姐妹): 保持 active + 跑死亡回调复活, 不计分。

    回归: 旧实现把 type 2 当普通击坠 despawn 且不跑回调, 三姐妹 lead
    直接消失, 阶段链断裂 → stage 4 卡死 (C case 2: 无 AddScore, active 不变)。
    """
    e = _make_enemy(2)
    assert e.kill() is True
    assert e.alive and e.state.active != 0
    assert e.machine.current.sub_id == 1  # 回调(sub 45 式复活)已入场
    assert e.state.life == 0
    assert e._kill_no_score
    # 回调 sub 下一帧 SET_LIFE 复活
    assert e.machine.step() is True
    assert e.state.life == 500


def test_kill_death_type_3_escape():
    """type 3 boss 离场: 不计分不掉落, 钉 life=1 跑死亡 sub (C case 3)。"""
    e = _make_enemy(3)
    assert e.kill() is False
    assert e.alive and e.state.life == 1
    assert e.state.can_be_damaged == 0 and e.state.death_type == 0
    assert e.machine.current.sub_id == 1


# ---- 8 关逐关冒烟 (锚点帧数来自 tmp_title/smoke_b*.log, 确定性可复现) ----

@NEEDS_DAT
def test_stage1_full_clear():
    """1 面全程: ~10187 帧通关, 2 张符卡(中超 6455 / 尾王 8491)。
    (体术判定加入后 10067→10187: hasNoCollision 门控伤害, C++:754)"""
    _, r = run_stage(1, 12000)
    assert r.cleared
    assert len(r.spell_frames) == 2
    assert r.stalls == 0


@NEEDS_DAT
def test_stage2_full_clear():
    """2 面(橙)全程: ~16232 帧通关, 4 张符卡, ExIns 0/1/2 实弹触发。

    (体术判定加入后 ~15857→16232: hasNoCollision 现在正确门控伤害
    (C++:754), 部分 boss 阶段不再被提前削血。)"""
    _, r = run_stage(2, 17000)
    assert r.cleared
    assert len(r.spell_frames) == 4
    assert all(idx in r.ex for idx in (0, 1, 2))
    assert r.stalls == 0


@NEEDS_DAT
def test_stage3_midboss_to_spell3():
    """3 面(爱丽丝)到第 3 张符卡(12754): ExIns 5(随从同步移动)持续生效。"""
    _, r = run_stage(3, 13000)
    assert len(r.spell_frames) >= 3
    assert r.ex[5] > 0
    assert r.stalls == 0


@NEEDS_DAT
def test_stage4_prismriver_phase_chain():
    """4 面(三姐妹)全程通关(40997): 多 boss 阶段链推进到 4 张符卡并击破。

    回归锚点: 阶段链不断裂(旧 bug: death_type==2 被当普通击坠 despawn)。
    体术判定加入后 18455→22713(符卡 1)/全程 25172→40997: 非主攻姐妹的
    hasNoCollision 现在正确门控伤害 (C++:754), harness 改为主攻可受击者,
    阶段推进改走 ECL 生命/超时回调; type-2 阶段击破不再被误伤路径提前触发
    (该分支由 test_kill_death_type_2_phase_kill_stays_active 单元覆盖)。
    """
    _, r = run_stage(4, 43000)
    assert r.saw_boss
    assert r.cleared
    assert len(r.spell_frames) == 4
    assert r.stalls == 0


@NEEDS_DAT
def test_stage5_youmu_slow_field():
    """5 面(妖梦)到符卡 1(6332)后: ExIns 9/10/11, 减速场生效且恢复。"""
    _, r = run_stage(5, 7000)
    assert len(r.spell_frames) >= 1
    assert all(idx in r.ex for idx in (9, 10, 11))
    assert r.min_time_scale < 1.0    # 减速场确实生效 (ExInsYoumuSetGameSpeed)
    assert r.slow_restored           # 且确实恢复 (ExInsYoumuRestoreGameSpeed)
    assert r.stalls == 0


@NEEDS_DAT
def test_stage6_yuyuko_to_spell2():
    """6 面(幽幽子)到第 2 张符卡(7519): 妖梦减速指令(10/11)与闪屏(15)触发。

    注: sub24 的 ex10 是 arg=1 的恒等调用(1/1, C++ 同样无实际减速),
    真减速窗在更晚的 sub73; 减速场"生效+恢复"的断言由 5 面用例覆盖。
    """
    _, r = run_stage(6, 10000)
    assert len(r.spell_frames) >= 2
    assert all(idx in r.ex for idx in (10, 11, 15))
    assert r.stalls == 0


@NEEDS_DAT
def test_stage7_extra_ran_early_spells():
    """7 面(Extra 蓝)到第 2 张符卡(6785): ExIns 22/0 实弹触发。"""
    _, r = run_stage(7, 8000)
    assert len(r.spell_frames) >= 2
    assert all(idx in r.ex for idx in (0, 22))
    assert r.stalls == 0


@NEEDS_DAT
def test_stage8_phantasm_yukari_early_spells():
    """8 面(Phantasm 紫)到第 2 张符卡: ExIns 23/0 实弹触发。

    帧数预算 7600: 符卡中满火力不再清弹(还原 ItemManager.cpp:227/345 的
    !spellcardInfo.isActive 分支)后, 屏上弹更多 → 玩家死亡/掉火力更勤,
    第 2 张符卡从 ~6504 推迟到 ~7118(行为按 C++ 权威, 锚点顺延)。"""
    _, r = run_stage(8, 7600)
    assert len(r.spell_frames) >= 2
    assert all(idx in r.ex for idx in (0, 23))
    assert r.stalls == 0


@NEEDS_DAT
def test_stage3_alice_ex6_split_bullets_limited_damage():
    """3 面爱丽丝 1 卡(操符, Normal idx 29, sub42→43→44)ExIns 6 实关覆盖。

    RUN_EX_INS(6, 0) 在 sub44 t=90 (EnemyEclInstr.cpp:242
    ExInsSplitBulletsOrShootBackwards): 选中 sprite_offset==6 的弹,
    分裂出 sprite_offset==15 的子弹群并杀掉原弹。常规 harness 输出太强,
    人偶敌(sub43)活不到自身 t=90 就被流弹收掉 → ex6 打不进。
    限伤模式: 符卡宣言后每帧把玩家拉到离主攻 boss 最远的底边角,
    直射打不到 boss 与人偶(零伤害), 让卡活过 begin+390
    (boss var10014 脉冲(begin+300) → 人偶轮询进 sub44 → 自身 t=90 分裂;
    依赖 GET_BOSS_INT 读 boss 上下文修复, 见 ecl.py _peer_int)。
    """
    g = PerfectCherryBloom(data_path=DAT, character=0, difficulty=1)
    g.stage_no = 3
    g._load_ecl()
    ex6_frames: list[int] = []
    orig_ex = g.ecl_host.run_ex_instr

    def counting(idx, enemy, instr, ctx=None):
        ret = orig_ex(idx, enemy, instr, ctx=ctx)
        if ret and idx == 6:
            ex6_frames.append(g.frame)
        return ret

    g.ecl_host.run_ex_instr = counting
    spell_begin: list[int] = []
    orig_begin = g.ecl_host.on_begin_spellcard

    def on_begin(st, gid, idx, name):
        if idx == 29 and not spell_begin:
            spell_begin.append(g.frame)
        if orig_begin is not None:
            orig_begin(st, gid, idx, name)

    g.ecl_host.on_begin_spellcard = on_begin
    split_seen = 0
    for _ in range(9600):
        g.tick(keys=_move_keys(g.frame), advance=(g.frame % 15 == 0))
        bs = _bosses(g)
        if bs:
            dmg = [e for e in bs
                   if not e.state.has_no_collision and e.state.is_hittable]
            lead = min(dmg or bs, key=lambda e: max(e.state.life, 0))
            if not spell_begin:
                # 限伤前: 与 run_stage 相同的吸附, 保证阶段按时推进
                g.player.pos = Vec2(lead.pos.x, min(lead.pos.y + 200, 400))
            else:
                # 限伤: 拉到离 boss 最远的底边角, 直射不再命中 boss/人偶
                g.player.pos = Vec2(40.0 if lead.pos.x >= 192.0 else 344.0, 420.0)
        if g.game_over:
            g.game_over = False
            g.result = None
            g.lives = 3.0
        split_seen = max(
            split_seen,
            sum(1 for b in g.bullets.alive() if b.sprite_offset == 15))
        if ex6_frames and g.frame > ex6_frames[0] + 5:
            break
    assert spell_begin, "3 面 1 卡(Normal idx 29)未宣言"
    assert ex6_frames, "限伤后 ex6(t=90) 仍未触发"
    # 实测锚点: boss var10014 脉冲(begin+300) → 人偶进 sub44, 自身 t=90 分裂
    assert ex6_frames[0] - spell_begin[0] >= 380, \
        f"ex6 触发过早({ex6_frames[0] - spell_begin[0]}), 不像 sub44 t=90 路径"
    assert split_seen > 0, "分裂弹(sprite_offset==15)未生成"
