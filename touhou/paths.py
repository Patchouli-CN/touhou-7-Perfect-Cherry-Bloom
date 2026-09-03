"""游戏资源(.dat 数据包)路径解析 —— 内置默认按作品分表(DEFAULT_DATA_PATHS)。

解析顺序: 显式参数 > 环境变量 ``TOUHOU_DAT`` > 内置默认路径(按作品查表)。
thbgm.dat(BGM)由播放层按 .dat 同目录推导, 不单独配置。
"""

from __future__ import annotations

import os
from pathlib import Path

from .registry import default_game

# 内置默认数据源(本机游戏目录, 按作品名查表; 新作品接入时在此补默认路径)
DEFAULT_DATA_PATHS = {
    "th07": Path(r"D:\TOUHOU_GAME\[th07] 东方妖妖梦 (日文版)\th07.dat"),
    "th08": Path(r"D:\TOUHOU_GAME\[th08] 东方永夜抄 (日文版)\th08.dat"),
}

#: th07 默认路径别名(既有调用方不破坏; 等价 DEFAULT_DATA_PATHS["th07"])
DEFAULT_DATA = DEFAULT_DATA_PATHS["th07"]

ENV_DATA = "TOUHOU_DAT"

# score.json 默认位置: exe 同目录语义 → 仓库根(原版 score.dat 在 exe 旁)。
# 路径默认值(非游戏逻辑), 集中在本模块; world.py 与 games/th07/view/impl.py 都从这里取。
DEFAULT_SCORE_PATH = Path(__file__).resolve().parent.parent / "score.json"


def resolve_data_path(
    data_path: str | Path | None = None, *, game: str | None = None
) -> Path:
    """按 显式参数 > TOUHOU_DAT 环境变量 > 内置默认(按 ``game`` 查表) 解析 .dat 路径。

    环境变量是全局覆盖, 不分作品(单包调试场景); ``game=None`` 用框架默认
    作品(registry.default_game()); 未登记的作品名回落默认作品的路径。
    """
    if data_path is not None:
        return Path(data_path)
    env = os.environ.get(ENV_DATA)
    if env:
        return Path(env)
    game = game if game is not None else default_game()
    return DEFAULT_DATA_PATHS.get(game, DEFAULT_DATA)
