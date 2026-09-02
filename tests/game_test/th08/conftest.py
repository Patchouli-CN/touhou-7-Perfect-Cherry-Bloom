"""th08 专属 fixture/标记 —— game_test/th08/ 子树共享。"""

from __future__ import annotations

import pytest

from touhou.paths import DEFAULT_DATA_PATHS

#: 需要真实 th08.dat 的用例统一打这个标记(资源缺失环境自动 skip)
needs_data = pytest.mark.skipif(
    not DEFAULT_DATA_PATHS["th08"].exists(), reason="需要真实 th08.dat"
)
