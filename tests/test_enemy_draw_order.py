"""敌人 sub-anm (SET_SUB_ANM → enemy->vms[0..1]) 绘制顺序回归。

原版 EnemyManager::ActualOnDraw (EnemyManager.cpp:1172-1221):
  vms[0](z=0.3) → primaryVm(z=0.29) → vms[1](z=0.3),
各 VM 用自己的 offset。即 slot0 的 sub-anm 画在本体后面。

6 面幽幽子"扇子攻击"(ecldata6 sub34 t=30: SET_SUB_ANM(0,153) + SET_ANM(147),
stg6enm.anm idx153 = 512x256 墨染巨扇): 修复前我们把 sub-anm 画在本体之后,
巨扇整个盖住 48x80 的幽幽子 —— 场上"只见扇子不见人"。

本测试用真实 stg6enm.anm 渲染: 有扇帧 vs 无扇帧在本体不透明核心区域
必须几乎一致(扇子不盖住人), 同时扇子区域确实被画上(排除扇子没画的假绿)。
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

BOSS_X, BOSS_Y = 192.0, 112.0
# 本体(48x80, 中心 pos)不透明核心柱: 实测修复后该带与无扇帧逐像素一致;
# 两侧/裙摆是半透描边, 扇子(画在后面)会透出来 —— 那是正确行为, 不在此断言。
BODY_BOX = (int(BOSS_X) - 5, int(BOSS_Y) - 8, 11, 22)
# 扇子独占区(本体够不到, 扇子 512x256 必覆盖): 中心左 100..60, y -40..+40
FAN_BOX = (int(BOSS_X) - 110, int(BOSS_Y) - 40, 50, 80)


def _mk_game_view():
    import pygame

    from touhou.games.th07.world import PerfectCherryBloom
    from touhou.engine.view.sprite_view import GAME_H, GAME_W, GameView

    pygame.init()
    g = PerfectCherryBloom(data_path=DAT, character=0, difficulty=1)
    g.stage_no = 6
    g._load_ecl()
    view = GameView(DAT, character=0, stage=6)
    view._ensure_stage(6)
    view._bg3d = None
    view._bg3d_broken = True
    return g, view, pygame.Surface((GAME_W, GAME_H))


def _render_with_sub_anm(sub_anm: int, frames: int = 90):
    """独立 game+view 造一个 anm=147(幽幽子本体) 的敌人, sub_anm slot0 =
    sub_anm(-1=无), 渲染 frames 帧(让 anm 脚本时序走完: 扇子 scale 0→1
    需 ~60 帧), 返回末帧像素拷贝。
    每次全新 game/view: 复用会让 _enemy_vis 撞上 id 复用而继承旧 VM 相位。"""
    from touhou.engine.ecl import EclMachine
    from touhou.engine.enemies import EclEnemy

    g, view, surf = _mk_game_view()
    machine = EclMachine(g.ecl_file)      # 不 call_sub: 视图层只读 state
    e = EclEnemy(machine)
    st = e.state
    st.anm_idx = 147                       # 幽幽子站立
    st.sub_anm_idx[0] = sub_anm            # 153 = 墨染巨扇 / -1 = 无
    st.pos.set(BOSS_X, BOSS_Y, 0.0)
    g.host.add(e)
    for _ in range(frames):
        view.render(surf, g)
    return surf.copy()


def _diff_ratio(a, b, box) -> float:
    x0, y0, w, h = box
    diff = 0
    for x in range(x0, x0 + w):
        for y in range(y0, y0 + h):
            if a.get_at((x, y)) != b.get_at((x, y)):
                diff += 1
    return diff / (w * h)


@NEEDS_DAT
def test_sub_anm_slot0_drawn_behind_primary() -> None:
    with_fan = _render_with_sub_anm(153)
    no_fan = _render_with_sub_anm(-1)
    # 扇子确实画了: 扇子独占区有扇/无扇差异必须巨大
    fan_diff = _diff_ratio(with_fan, no_fan, FAN_BOX)
    assert fan_diff > 0.5, f"扇子没画出来? fan box diff={fan_diff:.3f}"
    # 本体不被扇子盖住: 本体核心柱有扇/无扇必须逐像素一致
    body_diff = _diff_ratio(with_fan, no_fan, BODY_BOX)
    assert body_diff < 0.02, \
        f"本体被巨扇盖住(扇子画在了本体之上): body box diff={body_diff:.3f}"
