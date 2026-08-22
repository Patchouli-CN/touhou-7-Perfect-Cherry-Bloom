"""boss 符卡宣言 (spellcard_view) + 关卡标题 (stage_title_view) smoke 测试。

宣言: ECL 桥接触发 (与 ecldata 的 BEGIN_SPELLCARD 同路径) → GameView.render
(战斗区) + render_gui (640x480 窗口层, Gui::OnDraw 段):
- 入场中: 立绘/装饰/符卡名 VM 确有绘制 (gui_draws > 0);
- begin+200: 符卡名滑到窗口 (256,40) (游戏区右上) 常驻, 底条/指示/捕获分
  数字仍绘制 (Gui.cpp:1712-1795; text.anm script 1797 的 POS_TIME_DECEL
  终点);
- end (Gui.cpp:55-60 EndEnemySpellcard interrupt): 名滑出到窗口 x=576
  (越过游戏区右缘 416, 窗口层绘制不被裁切), +80 帧全部收场
  (gui_draws == 0)。

标题: std{N}txt.anm 5 脚本 (Gui.cpp:655 vms1) 自时序入场/淡出:
- 开场 ~t=180 在画 (title_draws > 0), t=500 已退场 (== 0);
- enter_stage 换关后重新触发;
- MSG_MUSIC 事件 (frame_bgm ("music", idx)) 重触发 BGM 行
  (Gui.cpp:938-958, script 2052 + sprite 2051+musicIdx)。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

sys.path.insert(0, r"D:\python_play\Touhou08")

DAT = Path(r"D:\TOUHOU_GAME\[th07] 东方妖妖梦 (日文版)\th07.dat")
NEEDS_DAT = pytest.mark.skipif(not DAT.exists(), reason="需要真实 th07.dat")


def _mk_game_and_view():
    import pygame

    from touhou.games.th07.world import PerfectCherryBloom
    from touhou.games.th07.view.sprite_view import (GAME_H, GAME_W, WIN_H, WIN_W,
                                                GameView)

    pygame.init()
    g = PerfectCherryBloom(data_path=DAT, character=0, difficulty=1)
    view = GameView(DAT, character=0, stage=1)
    # 渲染 smoke 不关心背景: 跳过 3D 场景, 走 2D 平铺 fallback (快)
    view._ensure_stage(1)
    view._bg3d = None
    view._bg3d_broken = True
    surf = pygame.Surface((GAME_W, GAME_H))
    win = pygame.Surface((WIN_W, WIN_H))     # GUI 层渲染目标 (640x480 窗口层)
    return g, view, surf, win


def _render_frame(view, g, surf, win) -> None:
    view.render(surf, g)
    view.render_gui(win, g)


@NEEDS_DAT
def test_spellcard_declaration_lifecycle() -> None:
    import pygame  # noqa: F401

    from touhou.engine.ecl import EclEnemyState

    g, view, surf, win = _mk_game_and_view()
    sv = view._spellcard_view
    # 手工造一个 ECL 敌人状态走 begin/end 桥 (等价 ECL BEGIN_SPELLCARD 指令)
    st = EclEnemyState()
    st.boss_id = 0
    st.is_boss = True
    st.pos.set(192.0, 120.0, 0.0)
    st.life = st.max_life = 500
    st.timer_callback_threshold = 1800
    g._ecl_on_begin_spellcard(st, 0, 5, "寒符「リンガリングコールド」")
    assert g.boss is not None and g.boss.spellcard_idx >= 0
    # 入场中 (begin+20): cutin 立绘/装饰/符卡名在画
    for _ in range(20):
        _render_frame(view, g, surf, win)
    assert sv.gui_draws > 0
    assert sv._cutin, "宣言立绘/装饰 VM 未启动"
    # 常驻 (begin+200): 名滑到窗口 (256,40) = 游戏区右上, 横幅组仍在画
    for _ in range(180):
        _render_frame(view, g, surf, win)
    name = sv._name
    assert name is not None and name.visible
    assert abs(name.pos[0] - 256.0) < 1.0 and abs(name.pos[1] - 40.0) < 1.0
    # 底条+名+指示+捕获分数字 (个位/历史两段至少 3 位) 全在画
    assert sv._indicator is not None
    assert sv.gui_draws >= 6
    # 退场 (EndEnemySpellcard: 名 interrupt 1, 底条/指示 interrupt 2)
    g._ecl_on_end_spellcard(st)
    assert g.boss is None or g.boss.spellcard_idx < 0
    # 滑出途中: 名字越过游戏区右缘 (窗口 x>416, 原版画窗口层不被裁切)
    for _ in range(25):
        _render_frame(view, g, surf, win)
    name = sv._name
    assert name is not None and name.pos[0] > 416.0
    if view._font is not None:
        arr = pygame.surfarray.array3d(win)
        assert (arr[417:640, 20:60] > 0).any(), "窗口右区无符卡名像素"
    for _ in range(80):
        _render_frame(view, g, surf, win)
    assert sv._name is None
    assert sv.gui_draws == 0


@NEEDS_DAT
def test_spellcard_portrait_boss_face_and_path() -> None:
    """宣言立绘 (Gui.cpp:363-383 + face 链脚本 1187):
    sprite 必须取自本关 face_{stage:02d}_00.anm 的 1197+gui_id (boss 脸,
    不是脚本默认的自机脸); 轨迹 (272,-144)→(272,112) 180f 下滑, 脚本带
    ANM_22 anchor=3 (pos=quad 左上), alpha 60f 淡入到 224, t=150 收场。
    """
    from touhou.engine.ecl import EclEnemyState

    g, view, surf, win = _mk_game_and_view()
    sv = view._spellcard_view
    st = EclEnemyState()
    st.boss_id = 0
    st.is_boss = True
    st.pos.set(192.0, 120.0, 0.0)
    st.life = st.max_life = 500
    st.timer_callback_threshold = 1800
    g._ecl_on_begin_spellcard(st, 0, 5, "寒符「リンガリングコールド」")
    _render_frame(view, g, surf, win)          # 触发 _start
    assert sv._cutin, "宣言立绘 VM 未启动"
    portrait = sv._cutin[0][0]
    # sprite 来源: face_01_00.anm 全局 1197+0 (SpriteBank 缓存 → 同对象)
    assert ("face_01_00.anm", 0x4AD) in sv._sbanks, "face_01_00.anm 未加载"
    sb = sv._sbanks[("face_01_00.anm", 0x4AD)]
    assert portrait.vm.active_sprite_idx == 0x4AD + 0
    assert portrait.surf is sb.sprite_surf(0), "立绘不是本关 boss 脸"
    assert portrait.vm.anchor == 3             # ANM_22: pos = quad 左上
    # 轨迹端点 (脚本 1187 SET_POS + INTERP_POS): 起点 (272,-144)
    assert abs(portrait.vm.pos[0] - 272.0) < 1.0
    assert -144.0 - 3.0 < portrait.vm.pos[1] < -130.0
    for _ in range(59):                        # → begin+60: alpha 淡入满 224
        _render_frame(view, g, surf, win)
    assert portrait.vm.color[3] == 224
    y60 = portrait.vm.pos[1]
    assert -144.0 < y60 < 0.0                  # 下滑途中
    for _ in range(100):                       # → begin+160: t=150 EXIT_HIDE2
        _render_frame(view, g, surf, win)
    assert all(vm is not portrait for vm, _ in sv._cutin), "立绘未按脚本收场"


@NEEDS_DAT
def test_spellcard_portrait_multiboss_stage4() -> None:
    """4 面三姐妹: gui_id 即 ECL BEGIN_SPELLCARD arg0 原样透传
    (EclManager.cpp:672), 0/3/6/9 → face_04_00.anm 的 1197+gui_id
    (entry0/1/2 链空间, 分别露娜萨/梅露兰/莉莉卡/合葬三人)。"""
    from touhou.engine.ecl import EclEnemyState

    g, view, surf, win = _mk_game_and_view()
    view._ensure_stage(4)
    g.enter_stage(4)
    sv = view._spellcard_view
    for gui_id in (0, 3, 6, 9):                # ecldata4 实参 (静态解析核实)
        st = EclEnemyState()
        st.boss_id = 0
        st.is_boss = True
        st.pos.set(192.0, 120.0, 0.0)
        st.life = st.max_life = 500
        st.timer_callback_threshold = 1800
        g._ecl_on_begin_spellcard(st, gui_id, 48 + gui_id, "テスト")
        _render_frame(view, g, surf, win)
        assert sv._cutin, f"gui_id={gui_id} 立绘 VM 未启动"
        portrait = sv._cutin[0][0]
        sb = sv._sbanks[("face_04_00.anm", 0x4AD)]
        assert portrait.vm.active_sprite_idx == 0x4AD + gui_id
        assert portrait.surf is sb.sprite_surf(gui_id), \
            f"gui_id={gui_id} 立绘不是 face_04_00 的对应 sprite"
        g._ecl_on_end_spellcard(st)
        for _ in range(200):                   # 收场后再下一张
            _render_frame(view, g, surf, win)


@NEEDS_DAT
def test_capture_bonus_digits() -> None:
    """捕获分数字 (Gui.cpp:1734-1795): 剩余分 8 位 + 历史两段随横幅常显。"""
    from touhou.engine.ecl import EclEnemyState

    g, view, surf, win = _mk_game_and_view()
    st = EclEnemyState()
    st.boss_id = 0
    st.is_boss = True
    st.pos.set(192.0, 120.0, 0.0)
    st.life = st.max_life = 500
    st.timer_callback_threshold = 1800
    g._ecl_on_begin_spellcard(st, 0, 5, "寒符「リンガリングコールド」")
    for _ in range(200):
        _render_frame(view, g, surf, win)
    sv = view._spellcard_view
    assert sv._indicator is not None and sv._ascii_sb is not None
    base = sv.gui_draws
    assert base >= 6          # 底条+名+指示+个位+历史 2 位 (attempts 已记 1)
    # 非捕获中剩余分归 0 (Gui.cpp:1739-1742): 只剩个位 0, 绘制数减少
    assert g.boss is not None
    g.boss.capture_score = 87654321
    g.boss.is_capturing = True
    _render_frame(view, g, surf, win)
    assert sv.gui_draws > base   # 8 位全亮 → 更多数字 blit
    g.boss.is_capturing = False
    _render_frame(view, g, surf, win)
    assert sv.gui_draws < base + 8


@NEEDS_DAT
def test_stage_title_shows_and_fades() -> None:
    g, view, surf, win = _mk_game_and_view()
    title = view._stage_title
    draws_mid = None
    for f in range(520):
        _render_frame(view, g, surf, win)
        if f == 180:
            draws_mid = title.title_draws
    # 入场期: 5 脚本淡入中确有绘制; t=500: 全部 EXIT_HIDE2 收场
    assert draws_mid is not None and draws_mid > 0
    assert title.title_draws == 0


@NEEDS_DAT
def test_stage_title_retriggered_on_enter_stage() -> None:
    g, view, surf, win = _mk_game_and_view()
    title = view._stage_title
    for _ in range(520):                     # 1 面标题跑完
        _render_frame(view, g, surf, win)
    assert title.title_draws == 0
    g.enter_stage(2)                          # 换关 → std2txt.anm 标题
    for _ in range(180):
        _render_frame(view, g, surf, win)
    assert title.title_draws > 0


@NEEDS_DAT
def test_bgm_line_retriggered_on_msg_music() -> None:
    """MSG_MUSIC (Gui.cpp:938-958): 标题退场后 vms1[0] 重跑 BGM 行脚本,
    sprite 换成 2051+musicIdx 的曲名图。"""
    g, view, surf, win = _mk_game_and_view()
    title = view._stage_title
    for _ in range(520):                     # 标题全部退场
        _render_frame(view, g, surf, win)
    assert title.title_draws == 0 and title._music_vm is None
    g.frame_bgm = [("music", 1)]             # 对话切 boss 曲
    _render_frame(view, g, surf, win)
    mv = title._music_vm
    assert mv is not None and mv.alive, "MSG_MUSIC 未重触发 BGM 行"
    assert mv.vm.active_sprite_idx == 4      # 2051+1 的链空间 key
    g.frame_bgm = []
    seen = 0
    for _ in range(500):                     # 曲名行自时序淡入/退场
        _render_frame(view, g, surf, win)
        seen = max(seen, title.title_draws)
    assert seen > 0
    assert title._music_vm is None           # 脚本收场后槽位清空
