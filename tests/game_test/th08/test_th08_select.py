"""th08 难度/机体选择(B2 期原作化)测试 —— flow 纯逻辑 + 解锁规则 + 真数据渲染。

对照 th08-ref TitleScreen.cpp(行号相对其 src/):
- menuLength = IsExtraUnlockedWithAllTeams ? 12 : 4(:1604/:1618/:1697);
- Extra 流机体锁定跳过(:1641-1648 进场 / :1722-1737 移动);
- 通关标记四档(TitleCompletionStatus.inl:12-67) = completion_mark_sprite;
- 初始光标: 难度屏保持 _diff.index(原作是 cfg.defaultDifficulty, :1427 ——
  我们没有 cfg 难度持久化, 用会话内保持代替, 差异注明), 机体屏保持上次
  选择(原作 = g_GameManager.shotType, :1616)。
"""

from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402

from touhou.games.th07.view.screens import MenuAction, MenuCursor, Screen  # noqa: E402
from touhou.games.th08.progress import (  # noqa: E402
    STAGE_6A,
    STAGE_6B,
    load_score_store,
    record_ending_clear,
    record_stage_clear,
)
from touhou.games.th08.view.title_flow import (  # noqa: E402
    CharacterFlowTh08,
    completion_mark_sprite,
)

from .conftest import needs_data  # noqa: E402
from .test_th08_view import StubGame, StubRenderer  # noqa: E402 桩复用

pygame.init()

_CHAR_NAMES = [
    "ReimuYukari",
    "MarisaAlice",
    "SakuyaRemilia",
    "YoumuYuyuko",
    "Reimu",
    "Yukari",
    "Marisa",
    "Alice",
    "Sakuya",
    "Remilia",
    "Youmu",
    "Yuyuko",
]


def _stub_app(tmp_path, score_path=None):
    from touhou.games.th08.view import GameApp

    stub = StubRenderer()
    app = GameApp(
        StubGame,
        config_path=tmp_path / "config.json",
        score_path=score_path or tmp_path / "score.json",
        renderer=stub,
    )
    return app, stub


def _char_flow(extra=False, unlocked=(), index=0, length=4) -> CharacterFlowTh08:
    return CharacterFlowTh08(
        cursor=MenuCursor(_CHAR_NAMES[:length], index=index),
        extra=extra,
        extra_unlocked=list(unlocked),
    )


# ---- menuLength 规则(:1604/:1618; 经 _reload_title_unlocks 重建 items) ----
def test_menu_length_4_on_fresh_store(tmp_path) -> None:
    """全新存档(无全 4 组 Extra 解锁) → 机体选择只有 4 组队伍。"""
    app, _stub = _stub_app(tmp_path)
    assert len(app._char_flow.cursor.items) == 4
    assert app._char_flow.cursor.items == _CHAR_NAMES[:4]


def test_menu_length_12_when_all_teams_extra_unlocked(tmp_path) -> None:
    """全 4 组 6B 无续关通关(Extra 解锁)→ 12 项(4 组 + 8 单人)。"""
    score_path = tmp_path / "score.json"
    store = load_score_store(score_path)
    for c in range(4):
        record_ending_clear(store, c, 1, cleared_6b=True, num_retries=0)
    store.save(score_path)
    app, _stub = _stub_app(tmp_path, score_path=score_path)
    assert len(app._char_flow.cursor.items) == 12


def test_menu_length_12_released_mid_session(tmp_path) -> None:
    """会话内回标题重读存档(:3664-3675 时机)→ 名单从 4 变 12。"""
    score_path = tmp_path / "score.json"
    app, _stub = _stub_app(tmp_path, score_path=score_path)
    assert len(app._char_flow.cursor.items) == 4
    store = load_score_store(score_path)
    for c in range(4):
        record_ending_clear(store, c, 1, cleared_6b=True, num_retries=0)
    store.save(score_path)
    app._reload_title_unlocks()
    assert len(app._char_flow.cursor.items) == 12


def test_clamp_cursor_on_shrink() -> None:
    """cursor >= menuLength → 0(:1698-1701)。"""
    flow = _char_flow(index=5, length=12)
    flow.cursor.items = _CHAR_NAMES[:4]  # 名单缩回 4 项
    flow.clamp_cursor()
    assert flow.cursor.index == 0


# ---- Extra 流锁定跳过(:1641-1648/:1722-1737; 单人 4..11 恒 True) ----
def test_extra_move_skips_locked() -> None:
    """Extra 变体: 移动顺向跳过锁定机体(双向), 非 Extra 变体不跳。"""
    flow = _char_flow(extra=True, unlocked=[True, False, False, True], length=4)
    flow.move(1)  # 0 → 1(锁) → 2(锁) → 3
    assert flow.cursor.index == 3
    flow.move(1)  # 3 → 回绕 0
    assert flow.cursor.index == 0
    flow.move(-1)  # 0 → 回绕 3
    assert flow.cursor.index == 3
    # 非 Extra: 同一存档布局不跳过
    flow = _char_flow(extra=False, unlocked=[True, False, False, True], length=4)
    flow.move(1)
    assert flow.cursor.index == 1


def test_extra_entry_skip_locked() -> None:
    """进 Extra 机体选择时从当前光标顺向跳到首个解锁机体(:1641-1648)。"""
    flow = _char_flow(
        extra=True, unlocked=[False, False, True, True], index=0, length=4
    )
    flow.skip_locked_forward()
    assert flow.cursor.index == 2


def test_extra_flow_through_app(tmp_path) -> None:
    """应用壳串起来的 Extra 流: Extra Start → 单项画面 → 机体(锁定跳过)。"""
    score_path = tmp_path / "score.json"
    store = load_score_store(score_path)
    record_ending_clear(store, 0, 1, cleared_6b=True, num_retries=0)  # 队 0 解锁
    record_ending_clear(store, 3, 1, cleared_6b=True, num_retries=0)  # 队 3 解锁
    store.save(score_path)
    app, stub = _stub_app(tmp_path, score_path=score_path)
    app._run_title_menu((MenuAction.DOWN,))  # 0 → 1 (Extra Start, 已解锁)
    app._run_title_menu((MenuAction.CONFIRM,))
    assert app._screen == Screen.EXTRA_LEVEL
    # UP/DOWN 只播音效不动光标(menuLength=1, :1491)
    app._run_menu((MenuAction.DOWN,))
    assert ("extra", 0, 0) in stub.calls  # 单项画面首帧(frame=0 进场)
    assert app._extra_stage.index == 0
    app._char_flow.cursor.index = 1  # 锁定队(队 1 未解锁 Extra)
    app._run_menu((MenuAction.CONFIRM,))  # → CharacterSelectExtra
    assert app._screen == Screen.CHARACTER
    assert app._char_flow.extra and app._char_flow.difficulty == 4
    assert app._char_flow.cursor.index == 3  # 顺向跳过锁定的 1/2
    # Extra 变体不画通关标记(OnDraw :3594-3596 只认主 CharacterSelect)
    app._run_menu(())  # _run_menu 先渲染后处理输入: 切屏后空转一帧才有首帧
    assert ("character", 3, None, 0) in stub.calls
    # 移动跳过: 3 → DOWN 回绕到 0
    app._run_menu((MenuAction.DOWN,))
    assert app._char_flow.cursor.index == 0
    # 确认 → 直接进 EX 面(StubGame 无 enter_stage, 不炸即可)
    app._run_menu((MenuAction.CONFIRM,))
    assert app._screen == Screen.PLAYING
    assert app._game.kw["difficulty"] == 4  # Extra 固定难度(:1516 + EXTRA)
    assert app._game.kw["character"] == 0


# ---- 通关标记四档映射(TitleCompletionStatus.inl:12-67) ----
def test_completion_mark_none_on_fresh() -> None:
    """无任何通关记录 → 无标记(队伍机体)。"""
    store = load_score_store("/nonexistent/score.json")
    assert completion_mark_sprite(store, 0, 1) is None


def test_completion_mark_solo_always_selectable() -> None:
    """单人机体(cursor>3)→ 恒 147(Final 選択可能, inl:39)。"""
    store = load_score_store("/nonexistent/score.json")
    assert completion_mark_sprite(store, 5, 1) == 147


def test_completion_mark_tiers(tmp_path) -> None:
    """四档: 146 当前难度 6B 无续关+6A 有续关 / 148 当前难度 6B 无续关 /
    147 其他难度 6B 无续关 / 145 任一难度 6A 有续关。"""
    store = load_score_store(tmp_path / "a.json")
    # 只有 6A 有续关(d=1) → 145
    record_stage_clear(store, 0, 1, STAGE_6A, num_retries=1)
    assert completion_mark_sprite(store, 0, 1) == 145
    # 其他难度(d=2)6B 无续关 → 当前难度(1)看是 147, 切到 d=2 是 148
    record_stage_clear(store, 0, 2, STAGE_6B, num_retries=0)
    assert completion_mark_sprite(store, 0, 1) == 147
    assert completion_mark_sprite(store, 0, 2) == 148
    # 当前难度(d=2)补 6A 有续关 → 146(优先于 148)
    record_stage_clear(store, 0, 2, STAGE_6A, num_retries=1)
    assert completion_mark_sprite(store, 0, 2) == 146
    # 6B 有续关不算数(判定读 without_retries 表)
    store2 = load_score_store(tmp_path / "b.json")
    record_stage_clear(store2, 0, 1, STAGE_6B, num_retries=1)
    assert completion_mark_sprite(store2, 0, 1) is None


# ---- 初始光标(差异项: 原作走 cfg.defaultDifficulty 持久化) ----
def test_difficulty_cursor_kept_across_entries(tmp_path) -> None:
    """难度屏光标会话内保持(_diff.index; 原作 = cfg.defaultDifficulty :1427,
    我们不落 cfg —— 差异注明)。"""
    app, _stub = _stub_app(tmp_path)
    assert app._diff.index == 1  # 默认 Normal(同 cfg 默认值)
    app._run_title_menu((MenuAction.CONFIRM,))  # Start → 难度
    app._run_menu((MenuAction.DOWN,))  # 1 → 2
    app._run_menu((MenuAction.BACK,))  # 回主菜单
    app._run_title_menu((MenuAction.CONFIRM,))  # 再进难度
    assert app._diff.index == 2


def test_character_cursor_kept(tmp_path) -> None:
    """机体屏光标 = 上次选择(原作 = g_GameManager.shotType, :1616/:1798)。"""
    app, _stub = _stub_app(tmp_path)
    app._run_title_menu((MenuAction.CONFIRM,))  # → 难度
    app._run_menu((MenuAction.CONFIRM,))  # → 机体
    app._run_menu((MenuAction.DOWN,))  # 0 → 1
    app._run_menu((MenuAction.BACK,))  # → 难度
    app._run_menu((MenuAction.CONFIRM,))  # → 机体
    assert app._char_flow.cursor.index == 1
    assert app._char_flow.difficulty == 1  # 难度角标/标记用的当前难度


# ---- 真机渲染 smoke(真 th08.dat + SDL dummy) ----
@needs_data
def test_difficulty_view_real_data() -> None:
    """DifficultySelectView 真资源: base sprite 对/选中切换/Extra 单项。"""
    from touhou.games.th08.view.select_view import DifficultySelectView
    from touhou.paths import DEFAULT_DATA_PATHS

    view = DifficultySelectView(DEFAULT_DATA_PATHS["th08"])
    # baseSpriteIndex = 脚本首帧 sprite: 131..135 → 134/136/138/140/142
    # (E/N/H/L 彩色选中帧 + Extra 位 = 望月 Extra Level)
    assert view._bases == [134, 136, 138, 140, 142]
    assert view._bg.get_size() == (640, 480)
    # 进场(frame=0): 选中 Normal(1) = base, 其余 base+1(:1450-1459)
    view.render(False, 1, 0)
    for frame in range(1, 40):  # 跑过入场演出
        view.render(False, 1, frame)
    assert view._diff_vms[1].vm.active_sprite_idx == 136
    assert view._diff_vms[0].vm.active_sprite_idx == 135
    assert view._diff_vms[1].vm.visible and view._diff_vms[1].vm.color[3] == 255
    assert view._caption.vm.visible  # "難易度選択の刻" 标题带
    # Extra 位(135)在本篇画面隐藏(无 label 7 → 落 Label -1)
    assert not view._diff_vms[4].vm.visible
    # 移动(:1496-1505): cursor 2 = Hard 亮
    view.render(False, 2, 40)
    assert view._diff_vms[2].vm.active_sprite_idx == 138
    assert view._diff_vms[1].vm.active_sprite_idx == 137
    # Extra 流: 单项画面, 只亮 vms[135](interrupt 12, :1436-1440/:1468-1469)
    view.render(True, 0, 0)
    for frame in range(1, 40):
        view.render(True, 0, frame)
    vm = view._diff_vms[4].vm
    assert vm.active_sprite_idx == 142
    assert vm.visible and vm.color[3] == 255
    for i in range(4):  # E/N/H/L 在 Extra 画面隐藏(无 label 12)
        assert not view._diff_vms[i].vm.visible


@needs_data
def test_character_view_real_data(tmp_path) -> None:
    """CharacterSelectView 真资源: 头像/名牌/灰队友/难度角标/通关标记。"""
    from touhou.games.th08.view.select_view import CharacterSelectView
    from touhou.paths import DEFAULT_DATA_PATHS

    view = CharacterSelectView(DEFAULT_DATA_PATHS["th08"])
    flow = CharacterFlowTh08(cursor=MenuCursor(_CHAR_NAMES[:4], index=0))
    flow.difficulty = 1  # Normal
    for frame in range(40):
        surf = view.render(flow, None, frame)
    # 队伍 0 = {0x77 名牌, 0x6f/0x70 头像}(g_TitleCharacterSpriteIndices :128-133)
    assert view._char_vms[0x77 - 111].vm.visible  # 队伍名牌(119)
    assert view._char_vms[0x6F - 111].vm.visible  # 灵梦头像(111)
    assert view._char_vms[0x70 - 111].vm.visible  # 紫头像(112)
    assert not view._char_vms[0x72 - 111].vm.visible  # 非命中(114)
    assert view._caption.vm.visible  # "人と妖怪の選択の刻"
    # 难度角标: vms[131+1] 滑到 (16,384)(:1638; 落点 = 脚本 label 9 实测值)
    dv = view._diff_vms[1].vm
    assert dv.visible and abs(dv.pos[0] - 16.0) < 1.0 and abs(dv.pos[1] - 384.0) < 1.0
    # 单人(下标 4) = {0x7B 名牌, 0x6F 头像, -1, 0x70 灰队友}(:154)
    flow.cursor.index = 4
    flow.cursor.items = _CHAR_NAMES  # menuLength 12
    for frame in range(41, 80):
        surf = view.render(flow, 146, frame)
    assert view._char_vms[0x7B - 111].vm.visible  # 单人名牌(123)
    assert view._char_vms[0x6F - 111].vm.visible
    partner = view._char_vms[0x70 - 111].vm  # 灰显队友(interrupt 23)
    assert partner.visible and partner.color[3] == 128
    assert partner.color[:3] == [64, 64, 64]
    # 通关标记: frame > 8 才画(:16), sprite 146 落 (400,170)
    assert view._mark.vm.active_sprite_idx == 146
    view.render(flow, 148, 5)
    assert view._mark.vm.active_sprite_idx == 146  # frame<=8 不换不画
    surf = view.render(flow, None, 80)  # 无标记不炸
    assert surf.get_size() == (640, 480)


@needs_data
def test_backend_select_screens_smoke(tmp_path) -> None:
    """自持后端 + 真数据: 三屏渲染路径(贴图视图懒加载 + 缩放 blit)不炸。"""
    from touhou.games.th08.view import PygameTh08Renderer
    from touhou.paths import DEFAULT_DATA_PATHS

    renderer = PygameTh08Renderer(DEFAULT_DATA_PATHS["th08"])
    renderer.open(scale=1)
    try:
        for frame in range(30):
            renderer.render_difficulty(1, items=["Easy", "Normal"], frame=frame)
            renderer.render_extra(0, items=["Extra"], frame=frame)
        flow = CharacterFlowTh08(cursor=MenuCursor(_CHAR_NAMES[:4], index=2))
        for frame in range(30):
            renderer.render_character(flow, completion=145, frame=frame)
        # 贴图视图加载成功(未回退文字)
        assert renderer._difficulty_view is not None
        assert renderer._character_view is not None
        renderer.present()
    finally:
        renderer.close()
        pygame.init()  # close() 会 pygame.quit(), 恢复以免影响后续用例
