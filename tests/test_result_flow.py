"""结算触发路径集成测试(真实 th07 数据): GameOver / 通关 → result, store 入账。"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, r"D:\python_play\Touhou08")

from touhou.games.th07.world import PerfectCherryBloom  # noqa: E402
from touhou.engine.bullets import Aim, Burst  # noqa: E402
from touhou.games.th07.player import PlayerState  # noqa: E402
from touhou.engine.score_store import ScoreStore  # noqa: E402
from touhou.utils import Vec2  # noqa: E402

DAT = Path(r"D:\TOUHOU_GAME\[th07] 东方妖妖梦 (日文版)\th07.dat")

_STOP_KEYS = (False, False, False, False, False)


def _tick_until_alive(g: PerfectCherryBloom) -> None:
    for f in range(600):
        if g.player.state == PlayerState.ALIVE:
            return
        g.tick(keys=((f // 24) % 2 == 1, (f // 24) % 2 == 0, False, False, False))
    raise AssertionError("玩家未能进入 ALIVE")


def _tick_until_game_over(g: PerfectCherryBloom) -> None:
    """无残机状态在自机正上方压一颗弹,  tick 到死透(game_over)。"""
    _tick_until_alive(g)
    g.globals.lives_remaining = 0
    g.bullets.fire(Burst(Vec2(g.player.pos.x, g.player.pos.y - 60),
                         math.pi / 2, Aim.SPREAD_ABSOLUTE, 1, 1, 2.0, 2.0, 0.0))
    for _ in range(600):
        g.tick(keys=_STOP_KEYS)
        if g.game_over:
            return
    raise AssertionError("无残机死亡未触发 game_over")


def test_game_over_waits_for_continue_choice() -> None:
    """无残机死亡 → game_over → 待续关(不自动结算); 选 No 才进结算。"""
    g = PerfectCherryBloom(data_path=DAT, character=0, difficulty=1,
                       score_store=ScoreStore(spellcard_count=141))
    _tick_until_game_over(g)
    assert g.game_over and g.result is None
    assert g.continue_available, "难度<4 且次数未尽 → 应可续关"
    # 待续关中 tick 冻结: 不结算, 帧不前进
    frame = g.frame
    g.tick()
    assert g.result is None and g.frame == frame
    assert len(g.store.entries(1, 0)) == 0  # 未入榜
    # 选 No → 结算 (RetryMenu case 4 → curState=6)
    g.finalize_game_over()
    r = g.result
    assert r is not None and not r["cleared"]
    assert r["clear_percent"] < 100.0
    assert r["deaths"] >= 1
    assert r["retries"] == 0
    assert g.store.plst["total_frames"] > 0
    assert len(g.store.entries(1, 0)) == 1  # 已入榜(内存)
    # 结算后游戏冻结
    g.tick()
    assert g.frame == frame


def test_continue_play_resets_per_source() -> None:
    """续关 Yes 的重置清单 (AsciiManager.cpp:955-976): 分数清零/残机回满/
    bomb 回满/power=0/cherry=cherryStart/本关计数清, 总擦弹与死亡数保留。"""
    g = PerfectCherryBloom(data_path=DAT, character=0, difficulty=1,
                       score_store=ScoreStore(spellcard_count=141))
    _tick_until_game_over(g)
    # 弄脏状态(验证重置清单)
    gl = g.globals
    gl.score = 1234560
    gl.gui_score = 1234560
    gl.current_power = 128.0
    gl.cherry = gl.cherry_start + 50000
    gl.graze_in_stage = 55
    gl.graze_in_total = 300
    gl.point_items_collected_this_stage = 42
    gl.point_items_collected_for_extend = 30
    gl.extends_from_point_items = 1
    gl.next_needed_point_items_for_extend = 100
    gl.bombs_remaining = 0.0
    g.continue_play()
    assert not g.game_over and g.result is None and g._result_cache is None
    assert gl.num_retries == 1
    assert gl.score == 1 and gl.gui_score == 1  # C: score=guiScore=numRetries
    assert g.lives == float(g.initial_lives)
    assert gl.bombs_remaining == g.shot_data.initial_bombs
    assert gl.current_power == 0.0
    assert gl.cherry == gl.cherry_start
    assert gl.graze_in_stage == 0
    assert gl.point_items_collected_this_stage == 0
    assert gl.point_items_collected_for_extend == 0
    assert gl.extends_from_point_items == 0
    assert gl.next_needed_point_items_for_extend == 50
    # 保留项
    assert gl.graze_in_total == 300
    assert gl.deaths >= 1
    # 当场复活接着玩(不重开本关): 帧继续前进, 玩家活着
    assert g.stage_no == 1
    for _ in range(600):
        g.tick(keys=_STOP_KEYS)
        if g.player.state == PlayerState.ALIVE:
            break
    assert g.player.state == PlayerState.ALIVE
    assert g.frame > 0


def test_continue_gating() -> None:
    """续关门控 (AsciiManager.cpp:839-846): Extra/Phantasm 与次数用尽
    不出现续关菜单, game_over 次帧直接进结算。"""
    # Extra (difficulty>=4): 不可续关
    g = PerfectCherryBloom(data_path=DAT, character=0, difficulty=4,
                           score_store=ScoreStore(spellcard_count=141))
    g.game_over = True
    assert not g.continue_available
    g.continue_play()  # 无效
    assert g.game_over and g.globals.num_retries == 0
    g.tick()
    assert g.result is not None and not g.result["cleared"]
    # 次数用尽 (numRetries >= maxRetries): 不可续关
    g2 = PerfectCherryBloom(data_path=DAT, character=0, difficulty=1,
                            score_store=ScoreStore(spellcard_count=141))
    assert g2.max_retries == 3  # plst.total_frames=0 → <7h → 3
    g2.globals.num_retries = g2.max_retries
    g2.game_over = True
    assert not g2.continue_available
    g2.tick()
    assert g2.result is not None
    # 续关后次数-1: 用尽前仍可续
    g3 = PerfectCherryBloom(data_path=DAT, character=0, difficulty=1,
                            score_store=ScoreStore(spellcard_count=141))
    g3.globals.num_retries = g3.max_retries - 1
    g3.game_over = True
    assert g3.continue_available
    g3.continue_play()
    assert g3.globals.num_retries == g3.max_retries
    g3.game_over = True
    assert not g3.continue_available  # 再死则不可续


def test_stage_clear_advances_to_next_stage() -> None:
    """1 面通关判定: ECL 时间轴全部跑完 + Boss 退场 → 换关进 2 面(不再直接结算)。

    换关保留/重置清单见 PerfectCherryBloom._advance_stage
    (GameManager.cpp AddedCallback): score 经 guiScore 单调追赶恢复,
    power/lives/grazeInTotal 带走, subrank/本关计数清零, 玩家回 SPAWNING。
    """
    g = PerfectCherryBloom(data_path=DAT, character=0, difficulty=1,
                       score_store=ScoreStore(spellcard_count=141))
    assert g.ecl_file is not None and g.ecl_timelines, "需要真实 ECL 数据"
    g.globals.score = 1234567
    g.globals.gui_score = 1234567
    g.power = 100.0
    g.globals.graze_in_stage = 55
    g.globals.graze_in_total = 300
    g.globals.point_items_collected_this_stage = 42
    for tl in g.ecl_timelines:
        tl.idx = len(tl.timelines)  # 构造: 时间轴全部跑完
    g.tick()
    assert g._pending_next_level       # 当帧登记换关, 尚未结算
    assert g.result is None and not g.cleared
    g.tick()                            # 次帧帧首换关
    assert g.stage_no == 2
    assert g.ecl_file is not None and not all(t.done for t in g.ecl_timelines)
    assert g.globals.score == 1234567   # guiScore 对齐 → tick_gui_score 恢复
    assert g.power == 100.0 and g.globals.graze_in_total == 300
    assert g.globals.graze_in_stage == 0
    assert g.globals.point_items_collected_this_stage == 0
    assert g.globals.subrank == 0
    # 玩家重建回出生点: SPAWNING(首帧即转 INVULNERABLE, C++ Respawn 同)
    assert g.player.state in (PlayerState.SPAWNING, PlayerState.INVULNERABLE)
    assert g.result is None and not g.cleared


def test_final_result_fields() -> None:
    """结算字段齐全(渲染层依赖): 分数/难度/各项计数/评级/名次/Slow%。"""
    g = PerfectCherryBloom(data_path=DAT, character=2, difficulty=3,
                       score_store=ScoreStore(spellcard_count=141))
    r = g.final_result(cleared=False)
    for k in ("score", "rating", "rank", "cleared", "clear_percent",
              "difficulty", "character", "stage", "name", "retries",
              "deaths", "bombs", "spellcards", "graze", "point_items",
              "slow_percent", "high_score"):
        assert k in r, k
    assert r["difficulty"] == 3 and r["character"] == 2
    assert r["slow_percent"] == 0.0  # 固定 60fps 恒 0
    assert r["name"] == "PLAYER"
    # 空榜 → 名次 0; high_score 底线 100000
    assert r["rank"] == 0 and r["high_score"] == 100000


def test_catk_recorded_on_real_ecl_spellcard() -> None:
    """真实 ECL 符卡 begin/end → catk attempts/successes/highscore 入账。

    用 ECL 桥接回调手工触发(与 ecldata1 的 BeginSpellcard 指令同路径):
    演示 Boss 路径(_spawn_demo_boss)不统计。
    """
    g = PerfectCherryBloom(data_path=DAT, character=0, difficulty=1,
                       score_store=ScoreStore(spellcard_count=141))
    _tick_until_alive(g)
    # 手工造一个 ECL 敌人状态走 begin/end 桥(等价 ECL BEGIN_SPELLCARD 指令)
    from touhou.engine.ecl import EclEnemyState
    st = EclEnemyState()
    st.boss_id = 0
    st.is_boss = True
    st.pos.set(192.0, 120.0, 0.0)
    st.life = st.max_life = 500
    st.timer_callback_threshold = 1800
    g._ecl_on_begin_spellcard(st, 0, 5, "测试符卡")
    assert g.store.catk[5]["attempts"][0] == 1
    assert g.store.catk[5]["attempts"][6] == 1
    assert g.store.catk[5]["name"] == "测试符卡"
    assert g.boss is not None and g.boss.is_capturing
    # 击破 → 捕获成功入账
    g.boss.life = 0
    g._apply_spellcard_end(g.boss.end_spellcard())
    assert g.store.catk[5]["successes"][0] == 1
    assert g.store.catk[5]["highscore"][0] > 0
    # 演示 Boss 不统计(无 _catk_idx)
    idx_before = sum(e["attempts"][6] for e in g.store.catk)
    g._spawn_demo_boss()
    g.boss.life = 0
    g._apply_spellcard_end(g.boss.end_spellcard())
    assert sum(e["attempts"][6] for e in g.store.catk) == idx_before


def test_spellcard_capture_bonus_score_and_banners() -> None:
    """符卡捕获: 捕获分入账 + "Spell Card Bonus!" 横幅; 清弹累计分(2000 起
    +20)入账 + "BONUS" 横幅 + 逐弹弹字 (BUGS.md#6, EclManager.cpp:770-783,
    BulletManager.cpp:486-523)。"""
    from touhou.engine.ecl import EclEnemyState
    g = PerfectCherryBloom(data_path=DAT, character=0, difficulty=1,
                           score_store=ScoreStore(spellcard_count=141))
    _tick_until_alive(g)
    st = EclEnemyState()
    st.boss_id = 0
    st.is_boss = True
    st.pos.set(192.0, 120.0, 0.0)
    st.life = st.max_life = 500
    st.timer_callback_threshold = 1800
    g._ecl_on_begin_spellcard(st, 0, 5, "测试符卡")
    # 压 3 颗静止弹在屏上(远离自机)
    g.bullets.fire(Burst(Vec2(96.0, 60.0), math.pi / 2, Aim.SPREAD_ABSOLUTE,
                         3, 1, 0.0, 0.0, 0.0))
    assert len(list(g.bullets.alive())) >= 3
    score_before = g.globals.score
    g.boss.life = 0
    res = g.boss.end_spellcard()
    assert res["captured"]
    g._apply_spellcard_end(res)
    # 捕获分入账 + "Spell Card Bonus!" 横幅 (EclManager.cpp:780-783)
    assert g.globals.spellcard_bonus == res["score"]
    # 清弹逐弹弹字(2000 起 +20/弹; 场上原有弹数不定, 至少含前三档)
    popups = [p.value for p in g.globals.popups]
    assert 2000 in popups and 2020 in popups and 2040 in popups
    # "BONUS" 横幅 = 清弹+清敌累计(场上 ECL 敌或有, 下界为清弹段)
    assert g.globals.bonus_score >= 2000 + 2020 + 2040
    assert g.globals.score - score_before >= res["score"] // 10 + 6060 // 10
    # 弹已转弹消点道具(标记 dead, 次帧清扫; 出生即吸附)
    assert all(b.dead for b in g.bullets.alive())
