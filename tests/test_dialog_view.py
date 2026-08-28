"""对话立绘渲染测试: 压暗缓存的跨关隔离 (BUGS.md 增量#2)。

模块级 _dim_cache 时代键 (side, face_idx, w, h) 不含关卡/角色维度, 而
th07 全部脸图都是 126×510 → 跨关必撞: 先打过 2 面再进 3 面, 轮到自机
说话(对侧压暗)时爱丽丝立绘闪成琪露诺。修复后压暗缓存收进 _FaceBook
按书隔离。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, r"D:\python_play\Touhou08")

import pygame  # noqa: E402

from touhou.games.th07.view.dialog_view import DialogueView  # noqa: E402

DAT = Path(r"D:\TOUHOU_GAME\[th07] 东方妖妖梦 (日文版)\th07.dat")
NEEDS_DAT = pytest.mark.skipif(not DAT.exists(), reason="需要真实 th07.dat")


@NEEDS_DAT
def test_dimmed_portrait_isolated_across_stages() -> None:
    """2 面/3 面 boss 侧压暗立绘不得共用缓存(像素必须不同)。"""
    pygame.init()
    v2 = DialogueView(DAT, character=0, stage=2)
    v3 = DialogueView(DAT, character=0, stage=3)
    s2 = pygame.Surface((384, 448), pygame.SRCALPHA)
    s3 = pygame.Surface((384, 448), pygame.SRCALPHA)
    # 轮到自机说话: boss 侧(side=1)压暗 —— BUG 的触发姿态
    v2._blit_portrait(s2, 1, 0, speaking=False)
    v3._blit_portrait(s3, 1, 0, speaking=False)
    assert pygame.image.tobytes(s2, "RGBA") != pygame.image.tobytes(s3, "RGBA"), (
        "压暗立绘跨关串台(缓存键缺关卡/角色维度)"
    )


@NEEDS_DAT
def test_dim_cache_still_shared_within_book() -> None:
    """同书内同脸同尺寸的压暗结果仍应命中缓存(对象复用)。"""
    pygame.init()
    v = DialogueView(DAT, character=0, stage=3)
    book = v._faces[1]
    assert book is not None
    img = book.get(0)
    assert img is not None
    assert book.get_dim(0, img) is book.get_dim(0, img)
