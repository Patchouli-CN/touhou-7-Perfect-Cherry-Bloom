"""Touhou Pythonic 引擎测试: 用真实 th07 数据验证。"""
from __future__ import annotations

import math
import sys
import os
from pathlib import Path

# 让 `pytest` 从项目根 import 到 Touhou
sys.path.insert(0, r"D:\python_play\Touhou08")

from touhou.utils import Vec2, angle_to  # noqa: E402
from touhou.engine.bullets import BulletWorld, SCREEN  # noqa: E402
from touhou.engine.player import Player  # noqa: E402
from touhou.schema.stage import Stage  # noqa: E402
from touhou.schema.archive import GameArchive  # noqa: E402

DAT = Path(r"D:\TOUHOU_GAME\[th07] 东方妖妖梦 (日文版)\th07.dat")


def test_archive_loads_real_data() -> None:
    arch = GameArchive.open(DAT)
    assert "etama.anm" in arch
    assert len(arch) == 197
    ecl = arch.load("ecldata1.ecl")
    assert len(ecl) == 44920


def test_vec2_ops() -> None:
    a = Vec2(3, 4)
    assert a.length == 5
    assert (a + Vec2(1, -1)) == Vec2(4, 3)
    assert (a * 2) == Vec2(6, 8)
    assert (SCREEN / 2) == Vec2(192, 224)
    assert abs(angle_to(Vec2(0, 0), Vec2(10, 0))) < 1e-6


def test_stage_title_and_bgm() -> None:
    arch = GameArchive.open(DAT)
    stage = Stage.read(arch.load("stage1.std"), 1)
    assert "Pseudo Winter" in stage.title
    assert stage.main_bgm == "bgm/th07_02.mid"


def test_bullet_world_ring_aimed() -> None:
    w = BulletWorld()
    w.player_pos = Vec2(48, 400)
    n = w.ring(Vec2(120, 80), 12, 2.0)
    assert n == 12
    assert len(w) == 12
    first = w.alive()[0]
    x0 = first.pos.x
    w.step()
    assert w.alive()[0].pos.x < x0  # 朝左运动(玩家在左)


def test_player_moves_and_shoots() -> None:
    arch = GameArchive.open(DAT)
    from touhou.schema.shot_data import parse_sht

    sd = parse_sht(arch.load("ply00a.sht"))
    p = Player(shot_data=sd)
    p.step()  # SPAWNING → INVULNERABLE(AddedCallback/Respawn)
    x0 = p.pos.x
    p.push_keys(right=True)
    p.step()
    assert p.pos.x > x0
    # 打若干帧理应产生自机弹(按 sht 周期调度)
    for _ in range(10):
        p.step()
    assert len(p.shots) > 0
