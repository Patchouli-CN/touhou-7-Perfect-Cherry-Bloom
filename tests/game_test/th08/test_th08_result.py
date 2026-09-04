"""th08 Result 浏览面测试(C 期第 3 片) —— 纯逻辑 + 应用壳接线 + 真数据 smoke。

对照 th08-ref ResultScreen.cpp BROWSE 模式(HandleCategorySelectScreen
:544-691 / HandleHighScore* :694-1001 / HandleSpellCard* :1003-1291 /
HandleOtherStatsScreen :1988-2151); 行为细节见 games/th08/view/result_flow.py
与 result_view.py 的 docstring。
"""

from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402

from touhou.engine.render import FrameInput  # noqa: E402
from touhou.games.th07.view.screens import MenuAction, Screen  # noqa: E402
from touhou.games.th08.progress import (  # noqa: E402
    SHOT_ALL_ROW,
    load_score_store,
)
from touhou.games.th08.spellcards import (  # noqa: E402
    CARD_DIFFICULTY_LETTERS,
    SPELLCARDS_PER_DIFFICULTY,
    SPELLCARD_LAST_WORD_START,
)
from touhou.games.th08.view.result_data import (  # noqa: E402
    BOARD_ROWS,
    CHARACTER_ITEMS,
    NUM_CHARACTERS_SELECT,
    SPELLCARD_NAME_HIDDEN,
    highscore_rows,
    spellcard_header,
    spellcard_rows,
    stats_lines,
)
from touhou.games.th08.view.result_flow import (  # noqa: E402
    ResultBrowseState,
    ResultFlowTh08,
)

from .conftest import needs_data  # noqa: E402
from .test_th08_view import StubGame, StubRenderer  # noqa: E402

pygame.init()

_GATE_CATEGORY = 20
_GATE_SELECT = 6
_GATE_STATS = 40


def _hs_rows(flow: ResultFlowTh08):
    return highscore_rows(flow.store, flow.selected_difficulty, flow.selected_character)


def _sc_rows(flow: ResultFlowTh08):
    return spellcard_rows(
        flow.store, flow.selected_spellcard_difficulty, flow.page, flow.shot_type
    )


def _sc_header(flow: ResultFlowTh08) -> str:
    return spellcard_header(
        flow.store, flow.selected_spellcard_difficulty, flow.shot_type
    )


def _store(tmp_path):
    """th08 口径空存档(222 卡 × 13 槽双组 catk)。"""
    return load_score_store(tmp_path / "score.json")


def _flow(tmp_path) -> ResultFlowTh08:
    return ResultFlowTh08(store=_store(tmp_path))


def _pump(flow: ResultFlowTh08, n: int) -> None:
    for _ in range(n):
        flow.tick_frame()


def _stub_app(tmp_path):
    from touhou.games.th08.view import GameApp

    return GameApp(
        StubGame,
        config_path=tmp_path / "config.json",
        score_path=tmp_path / "score.json",
        renderer=StubRenderer(),
    )


def _enter_browse(app):
    """主菜单 Result 项(下标 5)确认进浏览面。"""
    app._flow.cursor.index = 5
    app._run_title_menu((MenuAction.CONFIRM,))
    assert app._screen == Screen.PLAYER_DATA


def _pump_app(app, n: int) -> None:
    for _ in range(n):
        app._run_result_browse(FrameInput())


def _walk_to_highscore(flow: ResultFlowTh08) -> None:
    """类别 → 高分榜难度 → 机体 → 榜单。"""
    _pump(flow, _GATE_CATEGORY)
    flow.handle(MenuAction.CONFIRM)
    _pump(flow, _GATE_SELECT)
    flow.handle(MenuAction.CONFIRM)
    _pump(flow, _GATE_SELECT)
    flow.handle(MenuAction.CONFIRM)
    _pump(flow, _GATE_SELECT)
    assert flow.state == ResultBrowseState.HIGHSCORE


# ---- 符卡分页表(spellcards.py; 生成时已验证划分性质, 这里钉关键值) ----


def test_spellcard_tables_shape() -> None:
    """6 页: 42/49/50/50/14/222(g_SpellcardCountsPerDifficulty, Spellcard.cpp:230-233)。"""
    counts = [len(p) for p in SPELLCARDS_PER_DIFFICULTY]
    assert counts == [42, 49, 50, 50, 14, 222]
    assert SPELLCARDS_PER_DIFFICULTY[5] == tuple(range(222))  # 全难度 = 恒等序
    main = set()
    for page in SPELLCARDS_PER_DIFFICULTY[:5]:
        main |= set(page)
    assert main == set(range(SPELLCARD_LAST_WORD_START))  # 五页并集 = 0..204
    # 难度字母: 卡 2 = ST1_BOSS_1E → E; 卡 0 = ST1_MBOSS_1H → H; EX/LW → "-"
    assert CARD_DIFFICULTY_LETTERS[2] == "E"
    assert CARD_DIFFICULTY_LETTERS[0] == "H"
    assert CARD_DIFFICULTY_LETTERS[191] == "-"
    assert CARD_DIFFICULTY_LETTERS[SPELLCARD_LAST_WORD_START] == "-"


# ---- 输入门 ----


def test_input_gates(tmp_path) -> None:
    """类别 20 帧(:576-579)/选择屏 6 帧(:723 等)/统计 40 帧(:2094)内不受理。"""
    flow = _flow(tmp_path)
    for _ in range(_GATE_CATEGORY - 1):
        flow.tick_frame()
        assert flow.handle(MenuAction.DOWN) is None
    flow.tick_frame()
    assert flow.input_enabled
    flow.handle(MenuAction.DOWN)
    assert flow.cursor == 1
    # 进难度选择: 6 帧门
    flow.cursor = 0
    flow.handle(MenuAction.CONFIRM)
    assert flow.state == ResultBrowseState.HIGHSCORE_DIFFICULTY
    assert not flow.input_enabled
    assert flow.handle(MenuAction.DOWN) is None
    _pump(flow, _GATE_SELECT)
    assert flow.input_enabled
    # 统计: 40 帧门
    flow2 = _flow(tmp_path)
    flow2.cursor = 2
    _pump(flow2, _GATE_CATEGORY)
    flow2.handle(MenuAction.CONFIRM)
    assert flow2.state == ResultBrowseState.STATS
    _pump(flow2, _GATE_STATS - 1)
    assert flow2.handle(MenuAction.CONFIRM) is None
    flow2.tick_frame()
    assert flow2.handle(MenuAction.CONFIRM) is not None


# ---- 类别选择(:544-691) ----


def test_category_navigation(tmp_path) -> None:
    """UP/DOWN 回绕带 SE; BACK 跳退出项, 再按退出(:602-623/:675-679)。"""
    flow = _flow(tmp_path)
    _pump(flow, _GATE_CATEGORY)
    assert flow.handle(MenuAction.UP) == {"action": "move", "se": "select"}
    assert flow.cursor == 3  # 顶行回绕到底
    assert flow.handle(MenuAction.DOWN)["se"] == "select"
    assert flow.cursor == 0
    # BACK: 跳到"タイトルに戻る"
    assert flow.handle(MenuAction.BACK) == {"action": "move", "se": "cancel"}
    assert flow.cursor == 3
    assert flow.handle(MenuAction.BACK) == {"action": "quit", "se": "cancel"}
    # CONFIRM 在退出项上 = 退出
    flow2 = _flow(tmp_path)
    _pump(flow2, _GATE_CATEGORY)
    flow2.cursor = 3
    assert flow2.handle(MenuAction.CONFIRM) == {"action": "quit", "se": "cancel"}


def test_category_confirm_dispatch(tmp_path) -> None:
    """确认分发: 0→高分榜难度 / 1→符卡难度 / 2→统计(:631-674)。"""
    for cursor, state in (
        (0, ResultBrowseState.HIGHSCORE_DIFFICULTY),
        (1, ResultBrowseState.SPELLCARD_DIFFICULTY),
        (2, ResultBrowseState.STATS),
    ):
        flow = _flow(tmp_path)
        flow.cursor = cursor
        _pump(flow, _GATE_CATEGORY)
        assert flow.handle(MenuAction.CONFIRM) == {"action": "enter", "se": "ok"}
        assert flow.state == state


# ---- 高分榜路径(:694-1001) ----


def test_highscore_select_flow(tmp_path) -> None:
    """难度初值 1(:3054); BACK 记忆所选 + 回类别落 0 行(:750-751);
    机体 12 项回绕; 榜单左右切机体, BACK 回机体选择(:988-995)。"""
    flow = _flow(tmp_path)
    _pump(flow, _GATE_CATEGORY)
    flow.handle(MenuAction.CONFIRM)
    assert flow.state == ResultBrowseState.HIGHSCORE_DIFFICULTY
    assert flow.cursor == 1  # selectedDifficulty 初值
    _pump(flow, _GATE_SELECT)
    flow.handle(MenuAction.DOWN)  # → Hard
    flow.handle(MenuAction.CONFIRM)
    assert flow.state == ResultBrowseState.HIGHSCORE_CHARACTER
    assert flow.selected_difficulty == 2
    _pump(flow, _GATE_SELECT)
    flow.handle(MenuAction.UP)  # 12 项回绕
    assert flow.cursor == NUM_CHARACTERS_SELECT - 1
    flow.handle(MenuAction.CONFIRM)
    assert flow.state == ResultBrowseState.HIGHSCORE
    assert flow.selected_character == 11
    _pump(flow, _GATE_SELECT)
    flow.handle(MenuAction.RIGHT)  # 榜单上左右切机体(:977, 切后也重置计时 :979)
    assert flow.cursor == 0 and flow.selected_character == 0
    _pump(flow, _GATE_SELECT)
    assert flow.handle(MenuAction.BACK) == {"action": "back", "se": "cancel"}
    assert flow.state == ResultBrowseState.HIGHSCORE_CHARACTER
    assert flow.cursor == 0
    # 退回类别选择: 落 0 行(高分榜类别)
    _pump(flow, _GATE_SELECT)
    flow.handle(MenuAction.BACK)  # 机体 → 难度
    _pump(flow, _GATE_SELECT)
    flow.handle(MenuAction.BACK)  # 难度 → 类别
    assert flow.state == ResultBrowseState.CATEGORY
    assert flow.cursor == 0
    assert flow.selected_difficulty == 2  # 选择记忆保留


def test_highscore_rows_defaults_and_merge(tmp_path) -> None:
    """空榜 = 10 条默认行(100000-10000k, "--------", "--/--", :2860-2883);
    真实记录按分降序混入(低于 10000 的记录被默认行挤出榜外)。"""
    store = _store(tmp_path)
    flow = ResultFlowTh08(store=store)
    rows = _hs_rows(flow)
    assert len(rows) == BOARD_ROWS
    assert [r.score for r in rows] == [100000 - 10000 * k for k in range(10)]
    assert rows[0].name == "--------" and rows[0].date == "--/--"
    assert rows[0].stage_label == "1"
    # 低分进不了榜(原作默认行占满 10 槽)
    store.insert_score(
        {
            "score": 5000,
            "character": 0,
            "difficulty": 1,
            "stage": 1,
            "name": "LOW",
            "numRetries": 0,
            "date": "2026-09-03T12:00:00+09:00",
        }
    )
    assert all(r.name != "LOW" for r in _hs_rows(flow))
    # 高分上榜: 名次/日期 MM/DD/续关截 9/面数字
    store.insert_score(
        {
            "score": 150000,
            "character": 0,
            "difficulty": 1,
            "stage": 7,  # 6A → 显示 6(g_ResultStageNumbers, :87)
            "name": "HIX",
            "numRetries": 12,
            "date": "2026-09-03T12:00:00+09:00",
        }
    )
    rows = _hs_rows(flow)
    assert rows[0].name == "HIX" and rows[0].rank == 1
    assert rows[0].date == "09/03" and rows[0].retries == 9
    assert rows[0].stage_label == "6"
    assert rows[1].score == 100000  # 默认行随之下移


# ---- 符卡战绩路径(:1003-1291) ----


def test_spellcard_select_flow(tmp_path) -> None:
    """符卡难度 6 项初值 5(:3025); BACK 回类别落 1 行(:1057-1058);
    机体 13 项初值 12(SHOT_ALL, :3026); 进战绩页页号归零(:1180-1181)。"""
    flow = _flow(tmp_path)
    flow.cursor = 1
    _pump(flow, _GATE_CATEGORY)
    flow.handle(MenuAction.CONFIRM)
    assert flow.state == ResultBrowseState.SPELLCARD_DIFFICULTY
    assert flow.cursor == 5  # 全难度
    _pump(flow, _GATE_SELECT)
    flow.handle(MenuAction.UP)
    assert flow.cursor == 4  # Extra
    flow.handle(MenuAction.CONFIRM)
    assert flow.state == ResultBrowseState.SPELLCARD_CHARACTER
    assert flow.cursor == SHOT_ALL_ROW
    _pump(flow, _GATE_SELECT)
    flow.handle(MenuAction.DOWN)  # 13 项回绕: 12 → 0
    assert flow.cursor == 0
    flow.handle(MenuAction.CONFIRM)
    assert flow.state == ResultBrowseState.SPELLCARD
    assert flow.shot_type == 0 and flow.page == 0
    _pump(flow, _GATE_SELECT)
    assert flow.handle(MenuAction.BACK)["action"] == "back"
    assert flow.state == ResultBrowseState.SPELLCARD_CHARACTER
    # 逐级退回: 机体 → 难度 → 类别, 落 1 行(符卡类别, :1057-1058)
    _pump(flow, _GATE_SELECT)
    flow.handle(MenuAction.BACK)
    assert flow.state == ResultBrowseState.SPELLCARD_DIFFICULTY
    _pump(flow, _GATE_SELECT)
    flow.handle(MenuAction.BACK)
    assert flow.state == ResultBrowseState.CATEGORY
    assert flow.cursor == 1


def test_spellcard_paging(tmp_path) -> None:
    """战绩页: 左右翻页回绕((count+9)/10 页, :1249), 全难度 222 卡 = 23 页;
    上下切机体 13 项回绕(:1256)。"""
    flow = _flow(tmp_path)
    flow.cursor = 1
    _pump(flow, _GATE_CATEGORY)
    flow.handle(MenuAction.CONFIRM)  # 符卡难度(全难度)
    _pump(flow, _GATE_SELECT)
    flow.handle(MenuAction.CONFIRM)  # 机体(ALL)
    _pump(flow, _GATE_SELECT)
    flow.handle(MenuAction.CONFIRM)  # 战绩页
    _pump(flow, _GATE_SELECT)
    assert len(_sc_rows(flow)) == 10
    flow.handle(MenuAction.LEFT)  # 回绕到末页
    assert flow.page == 22
    assert len(_sc_rows(flow)) == 2  # 220/221 两张
    _pump(flow, _GATE_SELECT)  # 翻页重置计时(:1250-1252)
    flow.handle(MenuAction.RIGHT)
    assert flow.page == 0
    _pump(flow, _GATE_SELECT)
    flow.handle(MenuAction.DOWN)
    assert flow.shot_type == 0  # 12 → 0 回绕
    _pump(flow, _GATE_SELECT)  # 切机体同(:1258)
    flow.handle(MenuAction.UP)
    assert flow.shot_type == SHOT_ALL_ROW
    # Extra 页 14 卡 = 2 页, 末页 4 行
    flow.selected_spellcard_difficulty = 4
    flow.page = 1
    rows = _sc_rows(flow)
    assert len(rows) == 4
    assert rows[0].card_no == 201


def test_spellcard_rows_data(tmp_path) -> None:
    """行数据: 未遭遇卡名隐藏(:1223) + "---/---(-)"; 遭遇后出名 +
    "  收/  挑(字母)"; MaxBonus 只在 inGame 组捕获过时给(:2659-2665);
    Last Word 读 practice 组(:2636-2650)。"""
    store = _store(tmp_path)
    flow = ResultFlowTh08(store=store)
    flow.page = 0  # 全难度页第 0 页 = 卡 0..9
    rows = _sc_rows(flow)
    assert all(r.name == SPELLCARD_NAME_HIDDEN for r in rows)
    assert all(r.stats == "---/---(-)" and r.max_bonus == 0 for r in rows)
    # 卡 2(ST1_BOSS_1E, 全难度页下标 2): 机体 0 两次挑战一次收取
    store.record_spellcard_attempt(2, "萤符「地上的恒星」", 0)
    store.record_spellcard_attempt(2, "萤符「地上的恒星」", 0)
    store.record_spellcard_success(2, 0, 123456)
    rows = _sc_rows(flow)
    row = rows[2]
    assert row.name == "萤符「地上的恒星」"
    assert row.stats == "  1/  2(E)" and row.attempted and row.captured
    assert row.max_bonus == 123456
    # 切到机体 1: 无记录 → 隐藏色档位 + 空 stats, 但卡名仍显示
    # (名隐藏只看 inGame attempts[SHOT_ALL], :1223)
    flow.shot_type = 1
    row = _sc_rows(flow)[2]
    assert row.name == "萤符「地上的恒星」"
    assert not row.attempted and row.stats == "---/---(-)" and row.max_bonus == 0
    # Last Word(卡 205 = SPELLCARD_LW_WRIGGLE): practice 组入账
    store.record_spellcard_attempt(205, "「弹幕的尽头」", 3, practice=True)
    store.record_spellcard_success(205, 3, 777, practice=True)
    flow.page = 20  # 卡 200..209
    flow.shot_type = 3
    lw = _sc_rows(flow)[5]
    assert lw.card_no == 205
    assert lw.stats == "  1/  1(-)"  # practice 组
    assert lw.max_bonus == 0  # inGame 组未捕获 → 不显示(:2659)
    # 名隐藏 quirk: LW 只打过练习 → inGame attempts[SHOT_ALL]==0 → 名隐藏
    assert lw.name == SPELLCARD_NAME_HIDDEN


def test_spellcard_header(tmp_path) -> None:
    """表头收取数 = 本页内 inGame 或 practice 组捕获非零的卡数
    (capturedSpellCards, :2999-3023 的 OR 两组合计)。"""
    store = _store(tmp_path)
    flow = ResultFlowTh08(store=store)
    assert _sc_header(flow) == "  0/222（キャラ切り替え↓↑）"
    store.record_spellcard_attempt(2, "a", 0)
    store.record_spellcard_success(2, 0, 10)
    store.record_spellcard_attempt(205, "b", 12, practice=True)
    store.record_spellcard_success(205, 12, 10, practice=True)  # practice 组也算
    flow.shot_type = 0
    assert _sc_header(flow) == "  1/222（キャラ切り替え↓↑）"
    flow.shot_type = SHOT_ALL_ROW
    assert _sc_header(flow) == "  2/222（キャラ切り替え↓↑）"
    flow.selected_spellcard_difficulty = 0  # Easy 页 42 张
    flow.shot_type = 0
    assert _sc_header(flow) == "  1/ 42（キャラ切り替え↓↑）"


# ---- 统计(:1988-2151) ----


def test_stats_lines(tmp_path) -> None:
    """统计 20 行: 时间表 + 13 行按机体/难度出击数 + 合计行 + 通关/续关
    总数 + 练习全零(score.json 无按难度细分, 细分列留空)。"""
    store = _store(tmp_path)
    store.record_play(0, 1)
    store.record_play(0, 1)
    store.record_play(3, 4)  # Extra
    store.record_run_end(0, 1, score=100, frames=3600 * 61, cleared=True, num_retries=2)
    lines = stats_lines(store)
    assert len(lines) == 20
    assert lines[0].startswith("総起動時間") and "01:01:00" in lines[0]
    assert lines[2].startswith("プレイ回数")
    assert lines[3].startswith(CHARACTER_ITEMS[0])  # 霊夢＆紫
    assert "     2" in lines[3]  # Normal 列 2 次
    assert "     1" in lines[6]  # 妖夢＆幽々子 Extra 列 1 次
    assert lines[15].startswith(CHARACTER_ITEMS[SHOT_ALL_ROW])  # 全主人公合計
    assert lines[16] == ""  # 空行(原作 +34 间隔)
    assert lines[17].startswith("クリア回数") and lines[17].rstrip().endswith("1")
    assert lines[18].startswith("コンティニュー") and lines[18].rstrip().endswith("2")
    assert lines[19].startswith("プラクティス")
    # 统计态任意确认/返回退回类别选择(:2124-2128)
    flow = ResultFlowTh08(store=store)
    flow.cursor = 2
    _pump(flow, _GATE_CATEGORY)
    flow.handle(MenuAction.CONFIRM)
    _pump(flow, _GATE_STATS)
    assert flow.handle(MenuAction.BACK) == {"action": "back", "se": "cancel"}
    assert flow.state == ResultBrowseState.CATEGORY


# ---- 应用壳接线(StubRenderer) ----


def test_app_enter_and_leave_result_browse(tmp_path) -> None:
    """主菜单 Result 项进 Screen.PLAYER_DATA; 类别屏 BACK 两次回标题,
    光标落 5(wantedState2 规则, TitleScreen.cpp:3689-3690)。"""
    app = _stub_app(tmp_path)
    _enter_browse(app)
    stub = app._renderer
    assert ("se", "ok") in stub.calls
    flow = app._result_flow
    assert flow is not None and flow.state == ResultBrowseState.CATEGORY
    _pump_app(app, _GATE_CATEGORY)
    assert ("player_data", ResultBrowseState.CATEGORY, 0, 0) in stub.calls
    app._run_result_browse(FrameInput(menu_actions=(MenuAction.BACK,)))
    assert app._screen == Screen.PLAYER_DATA  # 第一次 BACK 只跳退出项
    assert flow.cursor == 3
    assert ("se", "cancel") in stub.calls
    app._run_result_browse(FrameInput(menu_actions=(MenuAction.BACK,)))
    assert app._screen == Screen.MAIN_MENU
    assert app._flow.cursor.index == 5


def test_app_browse_full_walk(tmp_path) -> None:
    """类别 → 高分榜难度 → 机体 → 榜单 → 逐级退回; 各屏渲染调用落位。"""
    app = _stub_app(tmp_path)
    _enter_browse(app)
    flow = app._result_flow
    _pump_app(app, _GATE_CATEGORY)
    for _ in range(3):  # 难度 → 机体 → 榜单
        app._run_result_browse(FrameInput(menu_actions=(MenuAction.CONFIRM,)))
        _pump_app(app, _GATE_SELECT)
    assert flow.state == ResultBrowseState.HIGHSCORE
    stub = app._renderer
    assert any(
        c[0] == "player_data" and c[1] == ResultBrowseState.HIGHSCORE
        for c in stub.calls
    )
    app._run_result_browse(FrameInput(menu_actions=(MenuAction.RIGHT,)))
    assert flow.cursor == 1  # 榜单左右切机体
    _pump_app(app, _GATE_SELECT)  # 切机体重置计时(:979)
    app._run_result_browse(FrameInput(menu_actions=(MenuAction.BACK,)))
    assert flow.state == ResultBrowseState.HIGHSCORE_CHARACTER
    _pump_app(app, _GATE_SELECT)
    app._run_result_browse(FrameInput(menu_actions=(MenuAction.BACK,)))
    assert flow.state == ResultBrowseState.HIGHSCORE_DIFFICULTY


def test_app_browse_reads_score_snapshot(tmp_path) -> None:
    """进画面前的 score.json 内容出现在榜单/符卡/统计里(存档快照口径)。"""
    store = _store(tmp_path)
    store.insert_score(
        {
            "score": 150000,
            "character": 0,
            "difficulty": 1,
            "stage": 8,
            "name": "SNAP",
            "numRetries": 0,
            "date": "2026-09-04T00:00:00+09:00",
        }
    )
    store.record_spellcard_attempt(2, "快照卡", 0)
    store.record_play(0, 1)
    store.save(tmp_path / "score.json")
    app = _stub_app(tmp_path)
    _enter_browse(app)
    flow = app._result_flow
    assert _hs_rows(flow)[0].name == "SNAP"
    assert _sc_rows(flow)[2].name == "快照卡"
    assert "     1" in stats_lines(store)[3]


# ---- 真数据 smoke ----


@needs_data
def test_real_result_browse_render(tmp_path) -> None:
    """真实 th08.dat: ResultBrowseView 贴图渲染 8 个状态各跑若干帧不炸。"""
    from touhou.games.th08.view import PygameTh08Renderer
    from touhou.paths import DEFAULT_DATA_PATHS

    store = _store(tmp_path)
    store.record_spellcard_attempt(2, "萤符「地上的恒星」", 0)
    store.record_spellcard_success(2, 0, 123456)
    store.record_play(0, 1)
    flow = ResultFlowTh08(store=store)
    renderer = PygameTh08Renderer(DEFAULT_DATA_PATHS["th08"])
    renderer.open(scale=1)
    try:
        states = [
            ResultBrowseState.CATEGORY,
            ResultBrowseState.HIGHSCORE_DIFFICULTY,
            ResultBrowseState.HIGHSCORE_CHARACTER,
            ResultBrowseState.HIGHSCORE,
            ResultBrowseState.SPELLCARD_DIFFICULTY,
            ResultBrowseState.SPELLCARD_CHARACTER,
            ResultBrowseState.SPELLCARD,
            ResultBrowseState.STATS,
        ]
        for state in states:
            flow._enter(state, 0)
            for i in range(50):  # 跑过进场动画 + 统计 40 帧淡入
                renderer.render_player_data(flow, store, i)
                flow.tick_frame()
        assert renderer._result_view is not None  # 贴图视图加载成功(未回退文字)
    finally:
        renderer.close()
        pygame.init()  # close() 会 pygame.quit(), 恢复以免影响后续用例
