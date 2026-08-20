"""进入游戏: 标题界面 → 主菜单 → 选难度/角色 → 游玩。

运行:  touhou07 (安装后)  或  uv run python -m touhou
日志:  控制台 + 仓库根 touhou.log (默认 TRACE, TOUHOU_LOG_LEVEL 可改)
"""
from __future__ import annotations

import os
from pathlib import Path

from .api import TouhouWorld
from .logger import logger, setup_logging


def main() -> None:
    setup_logging(
        log_level=os.environ.get("TOUHOU_LOG_LEVEL", "TRACE"),
        log_console=True,
        log_file=Path(__file__).resolve().parent.parent / "touhou.log",
    )
    logger.info("=== 游戏启动 ===")
    try:
        # 窗口版: 弹出游戏界面, 阻塞至关窗
        TouhouWorld(headless=False).run()
    except Exception:
        logger.exception("游戏异常退出")
        raise
    logger.info("=== 游戏正常退出 ===")


if __name__ == "__main__":
    main()
