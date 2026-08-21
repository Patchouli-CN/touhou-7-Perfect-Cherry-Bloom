"""关卡推进集成测试(真实 th07 数据): STAGERESULTS 结算 / NEXT_LEVEL 换关 /
6 面结局 / Extra·Phantasm 直接总结算。

对照: Gui.cpp RunMsg(MSG_STAGERESULTS/MSG_NEXT_LEVEL)、
GameManager.cpp AddedCallback(换关重置)、Ending.cpp(结局文件选择)。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, r"D:\python_play\Touhou08")

from touhou.games.th07.world import PerfectCherryBloom  # noqa: E402
from touhou.engine.ending import EndingData, ending_path, parse_end  # noqa: E402
from touhou.games.th07.player import PlayerState  # noqa: E402
from touhou.engine.score_store import ScoreStore  # noqa: E402

DAT = Path(r"D:\TOUHOU_GAME\[th07] 东方妖妖梦 (日文版)\th07.dat")
NEEDS_DAT = pytest.mark.skipif(not DAT.exists(), reason="需要真实 th07.dat")


def _make(stage: int = 1, difficulty: int = 1) -> PerfectCherryBloom:
    g = PerfectCherryBloom(data_path=DAT, character=0, difficulty=difficulty,
                           score_store=ScoreStore())
    if stage != 1:
        g.enter_stage(stage)
    return g


def _force_timelines_done(g: PerfectCherryBloom) -> None:
    for tl in g.ecl_timelines:
        tl.idx = len(tl.timelines)


# ---- STAGERESULTS 过关结算 (Gui.cpp:972-991 / :1357-1417) ----

@NEEDS_DAT
def test_stage_results_bonus_normal() -> None:
    """结算项与入账: Normal 1 面, lifeCount=3 → *0.5 (Gui.cpp 公式)。"""
    g = _make(1, 1)
    g.power = 128.0
    g.globals.point_items_collected_this_stage = 100
    g.globals.graze_in_stage = 200
    # cherry_max = cherry_start+200000 → cherry 项 200000
    before = g.globals.score
    g._on_stage_results()
    # bonus = 1*100000 + 200*50 + 100*5000 + 200000 = 810000 → *0.5 = 405000
    # 入账 = 10 次 AddScore(405000) = 10*(405000//10) = 405000
    assert g.globals.score - before == 405000
    sr = g.stage_results
    assert sr is not None and not sr["all_clear"]
    assert sr["lines"] == [("Clear", 1000000), ("Point", 5000000),
                           ("Graze", 100000), ("Cherry", 2000000)]
    assert sr["rank_line"] == "Normal Rank  *1.0"
    assert sr["penalty_line"] == "Player Penalty*0.5"
    assert sr["total"] == 405000
    assert sr["snapshot"]["clear_power"] == 128
    assert g.globals.extends_from_point_items != -1  # 1-5 面不封口


@NEEDS_DAT
def test_stage_results_bonus_final_stage() -> None:
    """6 面: 追加残机/炸弹奖 (stage>=6) + extendsFromPointItems=-1。"""
    g = _make(6, 1)
    g.globals.lives_remaining = 2.0
    g.globals.bombs_remaining = 3.0
    before = g.globals.score
    g._on_stage_results()
    # bonus = 6*100000 + 0 + 0 + 200000 + 2*2000000 + 3*400000 = 6000000
    # → Normal 不变 → lifeCount3 *0.5 = 3000000
    assert g.globals.score - before == 3000000
    sr = g.stage_results
    assert sr["all_clear"]
    assert ("Player", 40000000) in sr["lines"]
    assert ("Bomb", 12000000) in sr["lines"]
    assert g.globals.extends_from_point_items == -1


@NEEDS_DAT
def test_stage_results_difficulty_modifiers() -> None:
    """难度修正: Easy /2, Lunatic *1.5, Extra *2 (Extra 无 lifeCount 惩罚)。"""
    g = _make(1, 0)   # Easy
    g._on_stage_results()
    assert g.stage_results["total"] == 300000 // 2 * 5 // 10  # 75000 (难度+残机惩罚)
    g = _make(1, 3)   # Lunatic: cherryMax=+300000 → base 400000
    g._on_stage_results()
    assert g.stage_results["total"] == (400000 * 15 // 10) * 5 // 10  # 300000
    g = _make(7, 4)   # Extra: 7 面 → 残机/炸弹奖, difficulty>=4 无惩罚
    g.globals.lives_remaining = 2.0
    g.globals.bombs_remaining = 3.0
    g._on_stage_results()
    base = 7 * 100000 + 400000 + 2 * 2000000 + 3 * 400000  # cherryMax=+400000
    assert g.stage_results["total"] == base * 2
    assert g.stage_results["penalty_line"] is None


# ---- NEXT_LEVEL 换关 ----

@NEEDS_DAT
def test_next_level_event_advances() -> None:
    """msg 事件路径: stage_results → next_level → 次帧帧首换关到 2 面。"""
    g = _make(1, 1)
    g._on_stage_results()
    assert g.stage_results is not None
    g._on_next_level()
    assert g._pending_next_level
    g.tick()  # 帧首换关
    assert g.stage_no == 2
    assert g.stage_results is None       # 面板随换关撤下
    assert g.msg_vm is not None          # msg2.dat 已装载
    # 换关后玩家重建回出生点: SPAWNING(首帧即转 INVULNERABLE, C++ 同)
    assert g.player.state in (PlayerState.SPAWNING, PlayerState.INVULNERABLE)
    assert g.boss is None and not g.host.alive()
    assert not g.bullets.alive() and not g.lasers.alive()


@NEEDS_DAT
def test_next_level_repeat_reregisters() -> None:
    """pending 期间的重复事件被吞掉; 换关后再发则重新登记(每关脚本只发一次)。"""
    g = _make(1, 1)
    g._on_next_level()
    g._on_next_level()  # pending 期间重复 → 吞掉, 仍是单次换关
    g.tick()
    assert g.stage_no == 2
    g._on_next_level()  # 换关完成后再发: 重新登记(由 msg 脚本保证每关只发一次)
    assert g._pending_next_level


# ---- 6 面结局 / 7·8 面总结算 ----

@NEEDS_DAT
def test_stage6_clear_goes_ending_then_result() -> None:
    """6 面通关 → 结局(冻结) → finish_ending → 总结算 + CLRD。"""
    g = _make(6, 1)
    _force_timelines_done(g)
    g.tick()
    assert g.ending is not None and g.result is None
    assert not g.ending.bad and g.ending.path == "end00.end"  # ReimuA 正常结局
    assert g.ending.segments and g.ending.lines               # 文本解析出内容
    frame = g.frame
    g.tick()
    assert g.frame == frame  # 结局期间游戏冻结
    g.finish_ending()
    r = g.result
    assert r is not None and r["cleared"] and r["clear_percent"] == 100.0
    assert r["stage"] == 6
    assert g.store.clrd[0]["without_retries"][1] == 6
    assert g.store.plst["clear_count"] == 1
    # 结算幂等: 再调 final_result 不重复入榜
    n = len(g.store.entries(1, 0))
    assert g.final_result(cleared=True) is r
    assert len(g.store.entries(1, 0)) == n


@NEEDS_DAT
def test_stage6_ending_bad_on_retry() -> None:
    """numRetries!=0 → bad ending (Ending.cpp:499-501)。"""
    g = _make(6, 1)
    g.globals.num_retries = 1
    _force_timelines_done(g)
    g.tick()
    assert g.ending is not None and g.ending.bad
    assert g.ending.path == "end00b.end"


@NEEDS_DAT
def test_stage7_extra_clear_goes_result_directly() -> None:
    """7 面(Extra)通关 → 直接总结算(Gui.cpp NEXT_LEVEL difficulty>=4 分支)。"""
    g = _make(7, 4)
    assert g.lives == 2.0  # C: difficulty>=4 → lifeCount=2
    _force_timelines_done(g)
    g.tick()
    assert g.ending is None
    assert g.cleared and g.result is not None and g.result["cleared"]
    assert g.result["stage"] == 7


@NEEDS_DAT
def test_stage8_phantasm_clear_goes_result_directly() -> None:
    g = _make(8, 5)
    _force_timelines_done(g)
    g.tick()
    assert g.cleared and g.result is not None and g.result["stage"] == 8


# ---- .end 解析 ----

def test_ending_path_mapping() -> None:
    assert ending_path(0, bad=False) == "end00.end"
    assert ending_path(5, bad=False) == "end21.end"
    assert ending_path(2, bad=True) == "end10b.end"
    assert ending_path(5, bad=True) == "end20b.end"


def test_parse_end_minimal() -> None:
    data = (b"@mbgm/x.mid\x00\n@bdata/end/end00.jpg\x00\n"
            b"\x81@\x81@line1\x00\nline2\x00\n"
            b"@bdata/end/end03.jpg\x00\nline3\x00\n")
    segs = parse_end(data)
    assert [s.bg for s in segs] == ["end00.jpg", "end03.jpg"]
    assert segs[0].lines == ["line1", "line2"]
    assert segs[1].lines == ["line3"]


@NEEDS_DAT
def test_real_end_files_parse() -> None:
    """真实 end00/end10/end20b: 全部能解出文本与背景段。"""
    from touhou.schema.archive import GameArchive
    arc = GameArchive.open(DAT)
    for path in ("end00.end", "end10.end", "end20b.end"):
        segs = parse_end(arc.load(path))
        assert segs and sum(len(s.lines) for s in segs) >= 5
        assert any(s.bg for s in segs)


# ---- 1→2 面真连打(压血 harness, 与 test_stage_smoke 同语义) ----

@NEEDS_DAT
def test_stage1_to_2_continuous_run() -> None:
    """从 1 面真打到 3 面开场: msg STAGERESULTS/NEXT_LEVEL 驱动换关,
    timeline 重新驱动, score 全程只增(换关不丢分), 本关计数换关即重置。"""
    from touhou.utils import Vec2
    from touhou.test.test_stage_smoke import _bosses, _crush, _move_keys, _signature

    g = PerfectCherryBloom(data_path=DAT, character=0, difficulty=1,
                           score_store=ScoreStore())
    results_fired = 0
    orig = g._on_stage_results

    def counting() -> None:
        nonlocal results_fired
        results_fired += 1
        orig()

    g._on_stage_results = counting
    transitions = []  # (过关面, 累计帧, 过关时总分)
    cur = g.stage_no
    prev_score = 0
    last_sig = _signature(g)
    last_change = 0
    stalls = 0
    for _ in range(40000):
        g.tick(keys=_move_keys(g.frame), advance=(g.frame % 15 == 0))
        bs = _bosses(g)
        if bs:
            lead = min(bs, key=lambda e: max(e.state.life, 0))
            g.player.pos = Vec2(lead.pos.x, min(lead.pos.y + 200, 400))
        if g.game_over:
            g.game_over = False
            g.result = None
            g.lives = 3.0
        assert g.globals.score >= prev_score  # 分数只增: 换关经 guiScore 恢复
        prev_score = g.globals.score
        if g.stage_no != cur:
            transitions.append((cur, g.frame, g.globals.score))
            cur = g.stage_no
            # 换关瞬间: 新关 timeline 重新驱动(未跑完), 本关计数已清零
            assert not all(t.done for t in g.ecl_timelines)
            assert g.globals.graze_in_stage == 0
            assert g.globals.point_items_collected_this_stage == 0
            if cur == 3:
                break
            last_sig = _signature(g)
            last_change = g.frame
        sig = _signature(g)
        if sig != last_sig:
            last_sig = sig
            last_change = g.frame
        elif g.frame - last_change > 4800:
            stalls += 1
            _crush(g)
            last_change = g.frame
    assert [t[0] for t in transitions] == [1, 2]
    assert results_fired == 2              # 两面都走了 STAGERESULTS 结算
    assert stalls == 0
    assert g._point_items_prev_stages > 0  # 过关面的点道具累计入账
    assert g.result is None and not g.cleared  # 未到终面不结算
