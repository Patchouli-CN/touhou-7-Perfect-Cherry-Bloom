"""3D 背景(.std 场景 + bg3d_view 软件渲染)测试: 真实 th07 数据。"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

sys.path.insert(0, r"D:\python_play\Touhou08")

import numpy as np  # noqa: E402
import pygame  # noqa: E402
import pytest  # noqa: E402

from touhou.schema.archive import GameArchive  # noqa: E402
from touhou.schema.stage import Stage  # noqa: E402
from touhou.schema.anm import parse_scripts  # noqa: E402
from touhou.engine.view.bg3d_view import (  # noqa: E402
    StageScene,
    GAME_W,
    GAME_H,
)

DAT = Path(r"D:\TOUHOU_GAME\[th07] 东方妖妖梦 (日文版)\th07.dat")


@pytest.fixture(scope="module")
def arc() -> GameArchive:
    return GameArchive.open(DAT)


# ---- .std 解析(schema/stage.py) ----


def test_std_parse_all_stages(arc: GameArchive) -> None:
    """8 关全部解析: quad 数与头一致, 实例引用合法, 脚本指令非空。"""
    expect_quads = {1: 19, 2: 35, 3: 5, 4: 10, 5: 29, 6: 4, 7: 30, 8: 30}
    for n in range(1, 9):
        st = Stage.read(arc.load(f"stage{n}.std"), n)
        assert st.quad_count == expect_quads[n]
        assert sum(len(o.quads) for o in st.objects) == st.quad_count
        assert len(st.instances) > 0
        assert len(st.instrs) > 0
        for inst in st.instances:
            assert 0 <= inst.object_idx < len(st.objects)
        for obj in st.objects:
            assert 0 <= obj.z_level <= 3
            for q in obj.quads:
                assert q.type == 0 and q.anm_script >= 0


def test_std_instr_distribution(arc: GameArchive) -> None:
    """关键指令分布: 每关都有相机 pos/插值指令; stage4 有 up 贝塞尔;
    stage5 有 pos 贝塞尔; stage6 有 wait 标记 + 全屏 VM。"""
    ops = {}
    for n in range(1, 9):
        st = Stage.read(arc.load(f"stage{n}.std"), n)
        ops[n] = {i.opcode for i in st.instrs}
        assert 5 in ops[n]  # cam_pos
        assert 6 in ops[n]  # cam_pos_interp
        assert 1 in ops[n] or n in (4, 5, 6)  # set_fog(4/5/6 面例外查表)
    assert {24, 25, 26, 27, 28} <= ops[4]  # cam up 贝塞尔(魔法森林翻转)
    assert {14, 15, 16, 17, 18} <= ops[5]  # cam pos 贝塞尔
    assert 31 in ops[6] and (29 in ops[6] or 30 in ops[6])  # 冥界阶梯
    # 帧轴单调不减(除 jump 目标外顺序解析保持原序)
    st1 = Stage.read(arc.load("stage1.std"), 1)
    frames = [i.frame for i in st1.instrs]
    assert frames == sorted(frames)


def test_anm_parse_scripts(arc: GameArchive) -> None:
    """stg1bg.anm: 3 个 script, 首指令 SET_ACTIVE_SPRITE, 以 EXIT 结尾。"""
    scripts = parse_scripts(arc.load("stg1bg.anm"))
    assert len(scripts) == 1
    assert sorted(scripts[0].keys()) == [0, 1, 2]
    for sid, instrs in scripts[0].items():
        assert instrs[0].opcode == 3  # SET_ACTIVE_SPRITE
        assert instrs[0].args_i[0] == sid  # sprite id 与 script id 对应
        assert instrs[-1].opcode == 2  # EXIT


# ---- 渲染 smoke(bg3d_view) ----


def _render_frames(scene: StageScene, frames: int) -> np.ndarray:
    fb = None
    for _ in range(frames):
        scene.tick(0)
        fb = scene.render()
    assert fb is not None
    return fb


def test_bg3d_renders_and_evolves(arc: GameArchive) -> None:
    """stage1: 渲出非全黑画面, 且背景随时间轴变化。"""
    scene = StageScene.load(arc, 1)
    assert scene is not None
    fb1 = _render_frames(scene, 60)
    assert fb1.shape == (scene.buf_h, scene.buf_w, 3)
    coverage = (fb1 > 0).any(axis=2).mean()
    assert coverage > 0.5, f"覆盖率异常: {coverage}"
    fb2 = _render_frames(scene, 540)
    diff = np.abs(fb2.astype(int) - fb1.astype(int)).mean()
    assert diff > 1.0, f"背景未随时间变化: mean|diff|={diff}"


def test_bg3d_all_stages_render(arc: GameArchive) -> None:
    """8 关: 加载 + 推进不炸; 多个时刻采样, 至少一个画面非全黑
    (部分关卡开场雾色近黑, 如 4 面魔法森林)。"""
    for n in range(1, 9):
        scene = StageScene.load(arc, n)
        assert scene is not None, f"stage{n} 加载失败"
        best = 0.0
        fb = None
        for f in range(1, 2401):
            scene.tick(0)
            if f % 600 == 0:
                fb = scene.render()
                best = max(best, (fb > 0).any(axis=2).mean())
        assert best > 0.3, f"stage{n} 近全黑 (best={best:.2f})"


def test_bg3d_render_into_surface(arc: GameArchive) -> None:
    """dummy driver 下 render_into(pygame.Surface) 不炸。"""
    pygame.init()
    scene = StageScene.load(arc, 2)
    assert scene is not None
    surf = pygame.Surface((GAME_W, GAME_H))
    for _ in range(30):
        scene.tick(0)
        scene.render_into(surf)
    arr = pygame.surfarray.array3d(surf)
    assert (arr > 0).any()


def test_bg3d_perf(arc: GameArchive) -> None:
    """性能: tick+render 均耗应远低于预算(打印实测, 宽松断言仅防失控)。

    stage1 为轻负载基线; stage7 是最重关卡之一(历史实测 ~28-35 ms/帧,
    优化目标 ≤16 ms/帧即 60fps)。阈值放到 45ms 只防灾难性回退。
    """
    pygame.init()
    for stage_no in (1, 7):
        scene = StageScene.load(arc, stage_no)
        assert scene is not None
        surf = pygame.Surface((GAME_W, GAME_H))
        for _ in range(60):  # 预热(纹理/脚本稳定)
            scene.tick(0)
            scene.render_into(surf)
        t0 = time.perf_counter()
        frames = 120
        for _ in range(frames):
            scene.tick(0)
            scene.render_into(surf)
        ms = (time.perf_counter() - t0) * 1000 / frames
        print(f"\nbg3d stage{stage_no}: {ms:.2f} ms/帧 (tick+render+blit)")
        assert ms < 45.0, f"stage{stage_no} 背景渲染过慢: {ms:.2f} ms/帧"


def test_gameview_bg_fallback() -> None:
    """GameView: 3D 背景加载失败/渲染异常时退回 2D 平铺, 不中断渲染。"""
    pygame.init()
    from touhou.games.th07.view.sprite_view import GameView

    view = GameView(DAT, character=0, stage=1)
    view._ensure_stage(1)
    assert view._bg3d is not None  # 正常加载
    surf = pygame.Surface((GAME_W, GAME_H))

    class _Game:
        character = 0
        stage_no = 1
        ecl_world = None

        def __getattr__(self, name):
            raise AttributeError(name)

    g = _Game()
    # host/items/bullets 等缺失会在 _render_bg 之后才用到, 这里只验背景段
    view._ensure_stage(1)
    view._render_bg(surf, g)
    assert view._bg3d_broken is False
    # 渲染期异常 → 永久 fallback
    view._bg3d = None
    view._render_bg(surf, g)  # 2D 平铺路径不炸
