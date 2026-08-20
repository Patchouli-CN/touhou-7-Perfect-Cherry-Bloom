"""游戏资源(th07.dat)路径解析。

解析顺序: 显式参数 > 环境变量 ``TOUHOU_DAT`` > 内置默认路径。
thbgm.dat(BGM)由播放层按 th07.dat 同目录推导, 不单独配置。
"""
from __future__ import annotations

import os
from pathlib import Path

# 内置默认数据源(本机游戏目录)
DEFAULT_DATA = Path(r"D:\TOUHOU_GAME\[th07] 东方妖妖梦 (日文版)\th07.dat")

ENV_DATA = "TOUHOU_DAT"


def resolve_data_path(data_path: str | Path | None = None) -> Path:
    """按 显式参数 > TOUHOU_DAT 环境变量 > 内置默认 的顺序解析 th07.dat 路径。"""
    if data_path is not None:
        return Path(data_path)
    env = os.environ.get(ENV_DATA)
    if env:
        return Path(env)
    return DEFAULT_DATA
