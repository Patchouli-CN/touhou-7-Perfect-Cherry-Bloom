"""examples/dodge_ai.py 冒烟: headless(SDL dummy)短跑若干帧不炸且有输出。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from touhou.paths import DEFAULT_DATA

pytestmark = pytest.mark.skipif(not DEFAULT_DATA.exists(), reason="需要真实 th07.dat")


def test_dodge_ai_smoke() -> None:
    root = Path(__file__).resolve().parent.parent
    env = dict(
        os.environ,
        SDL_VIDEODRIVER="dummy",
        SDL_AUDIODRIVER="dummy",
        DODGE_AI_HEADLESS="1",
        DODGE_AI_FRAMES="600",
    )
    r = subprocess.run(
        [sys.executable, "examples/dodge_ai.py"],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=root,
        env=env,
    )
    assert r.returncode == 0, r.stderr
    assert "[dodge_ai]" in r.stdout
