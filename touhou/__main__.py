"""进入游戏: 标题界面 → 主菜单 → 选难度/角色 → 游玩。

运行:  touhou07 (安装后)  或  uv run python -m touhou [--game th07]
日志:  控制台 + 仓库根 touhou.log (默认 TRACE, TOUHOU_LOG_LEVEL 可改)
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from types import ModuleType

from .apis.basic import TouhouWorld
from .env import log_environment
from .logger import logger, setup_logging

pyfiglet: ModuleType | None  # 缺依赖时回落 None(跳过横幅)
try:
    import pyfiglet
except ImportError:  # 没装也能跑, 跳过横幅
    pyfiglet = None

# ANSI 红白(终端真彩)
_RED = "\033[38;2;255;60;60m"
_WHITE = "\033[38;2;255;255;255m"
_DIM = "\033[38;2;120;120;130m"
_RESET = "\033[0m"


def _print_banner() -> None:
    """pyfiglet 红白 ASCII 横幅, 逐行淡入动画; 非 TTY/缺 pyfiglet 时跳过。"""
    if not sys.stderr.isatty() or pyfiglet is None:
        return
    art = pyfiglet.figlet_format("Touhou World", font="slant").rstrip("\n")
    lines = art.splitlines()
    for i, line in enumerate(lines):
        # 红白交替(中间一行红字白边的错觉靠交替行实现)
        color = _RED if i % 3 == 1 else _WHITE
        print(f"{color}{line}{_RESET}", file=sys.stderr)
        time.sleep(0.035)
    print(f"{_DIM}{'─' * max(len(l) for l in lines)}{_RESET}", file=sys.stderr)
    time.sleep(0.05)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m touhou", description="东方引擎: 进入游戏"
    )
    parser.add_argument(
        "--game", default="th07", help="作品名(默认 th07; 须在注册表已登记)"
    )
    args = parser.parse_args()

    setup_logging(
        log_level=os.environ.get("TOUHOU_LOG_LEVEL", "TRACE"),
        log_console=True,
        log_file=Path(__file__).resolve().parent.parent / "touhou.log",
    )
    _print_banner()
    logger.info("=== 游戏启动 ===")
    log_environment(logger)
    try:
        # 窗口版: 弹出游戏界面, 阻塞至关窗
        TouhouWorld(game=args.game, headless=False).run()
    except Exception:
        logger.exception("游戏异常退出")
        raise
    logger.info("=== 游戏正常退出 ===")


if __name__ == "__main__":
    main()
