"""bomb 视觉层 (engine/view/bomb_view.py) smoke + 残留回归测试。

12 套 (6 机体 × focus/unfocus) 实跑逻辑 + GameView.render:
- 全程渲染不抛异常, bomb 进行中确有特效绘制 (effect_draws > 0);
- bomb 结束 +30 帧: 逻辑盒无残留 active (impl.py 无条件 tick, 对照
  Player.cpp:2231 UpdateBombProjectiles 每帧执行), 视觉层无 bomb 特效
  绘制调用 (effect_draws == 0) —— 即用户报告的"bomb 放完特效残留"回归断言;
- bomb 结束 +150 帧: cutin/横幅脚本收尾 (gui_draws == 0), 无敌红环
  随无敌计时消失 (Player.cpp:1923-1929);
- 灵梦B 集中: 结束瞬间大清弹圆仍 active (lifetime=210 > duration=190,
  原版语义的 20 帧延续, BombData.cpp:659), end+30 衰减完毕。
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

BOMB_AT = 100
AFTER_END = 150  # 结束后继续跑的帧数

CASES = [(c, f) for c in range(6) for f in (False, True)]


def _run_bomb(character: int, focus: bool):
    """放一发 bomb 并跑完后段, 返回 (game, view, t0, t_end, 记录)。"""
    import pygame

    from touhou.core.impl import PerfectCherryBloom
    from touhou.engine.view.sprite_view import (GAME_H, GAME_W, WIN_H, WIN_W,
                                                GameView)

    pygame.init()
    g = PerfectCherryBloom(data_path=DAT, character=character, difficulty=1)
    g.stage_no = 1
    g._load_ecl()
    view = GameView(DAT, character=character, stage=1)
    # bomb 渲染 smoke 不关心背景: 跳过 3D 场景, 走 2D 平铺 fallback (快)
    view._ensure_stage(1)
    view._bg3d = None
    view._bg3d_broken = True
    surf = pygame.Surface((GAME_W, GAME_H))
    win = pygame.Surface((WIN_W, WIN_H))     # cutin/横幅画在窗口层
    t0 = t_end = None
    draws_mid = None
    clear_active_at_end = False
    while g.frame < BOMB_AT + 800:
        keys = (False, False, False, False, focus, False)
        g.tick(keys=keys, bomb=(g.frame == BOMB_AT), advance=False)
        view.render(surf, g)
        view.render_gui(win, g)
        b = g.bomb
        if b.is_in_use and t0 is None:
            t0 = g.frame
        if t0 is not None and t_end is None:
            if b.is_in_use and b.timer == b.duration // 2:
                draws_mid = view._bomb_view.effect_draws
            if not b.is_in_use:
                t_end = g.frame
                clear_active_at_end = any(cb.active for cb in b.clear_boxes)
        if t_end is not None and g.frame >= t_end + AFTER_END:
            break
    assert t0 is not None, "bomb 未触发"
    assert t_end is not None, "bomb 未结束"
    return g, view, t0, t_end, draws_mid, clear_active_at_end


@NEEDS_DAT
@pytest.mark.parametrize("character,focus", CASES,
                         ids=[f"c{c}{'f' if f else 'u'}" for c, f in CASES])
def test_bomb_render_and_no_residue(character: int, focus: bool) -> None:
    g, view, t0, t_end, draws_mid, _ = _run_bomb(character, focus)
    del t0, t_end
    bv = view._bomb_view
    # 进行中: 有 bomb 特效绘制 (至少暗化 + 本体 VM)
    assert draws_mid is not None and draws_mid > 0
    # end+150 帧: 逻辑盒全部 inactive (原版延续如灵梦B 集 20 帧也已衰减完)
    assert not any(b.active for b in g.bomb.damage_boxes)
    assert not any(b.active for b in g.bomb.clear_boxes)
    # 视觉无残留: 无 bomb 特效/cutin 绘制调用, 无敌环已随无敌结束消失
    assert bv.effect_draws == 0
    assert bv.gui_draws == 0
    assert bv._ring is None


@NEEDS_DAT
def test_reimu_b_focus_clear_outlives_bomb() -> None:
    """灵梦B 集中清弹圆比 bomb 多活 ~20 帧是原版语义 (BombData.cpp:659),
    结束帧必须仍 active, end+150 衰减完 —— 防止"修残留"误杀原版延续。"""
    g, view, t0, t_end, _, clear_active_at_end = _run_bomb(1, True)
    del view, t0, t_end
    assert g.bomb.duration == 190
    assert clear_active_at_end          # 结束帧清弹圆仍在 (lifetime=210)
    assert not any(cb.active for cb in g.bomb.clear_boxes)  # end+150 已清零


@NEEDS_DAT
def test_bomb_banner_window_layer() -> None:
    """cutin/横幅画在 640x480 窗口层 (Gui::OnDraw): 宣言 3 倍期画面内容
    越过游戏区左缘 (窗口 x<32), 不被游戏画布裁切。"""
    import pygame

    from touhou.core.impl import PerfectCherryBloom
    from touhou.engine.view.sprite_view import (GAME_H, GAME_W, WIN_H, WIN_W,
                                                GameView)

    pygame.init()
    g = PerfectCherryBloom(data_path=DAT, character=0, difficulty=1)
    g.stage_no = 1
    g._load_ecl()
    view = GameView(DAT, character=0, stage=1)
    view._ensure_stage(1)
    view._bg3d = None
    view._bg3d_broken = True
    surf = pygame.Surface((GAME_W, GAME_H))
    win = pygame.Surface((WIN_W, WIN_H))
    while g.frame < 300:
        keys = (False, False, False, False, False, False)
        g.tick(keys=keys, bomb=(g.frame == 100), advance=False)
        view.render(surf, g)
        view.render_gui(win, g)
        if g.bomb.is_in_use and g.bomb.timer == 10:
            break
    assert g.bomb.is_in_use and g.bomb.timer == 10
    assert view._bomb_view.gui_draws > 0
    # 窗口左缘条 (游戏区外) 出现像素 → 证明画在窗口层而非 384 画布
    arr = pygame.surfarray.array3d(win)
    assert (arr[0:32, :] > 0).any(), "窗口左缘无像素: 横幅仍被游戏区裁切"
