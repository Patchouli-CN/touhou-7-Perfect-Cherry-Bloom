"""日志实现 - 支持动态配置，并桥接标准 logging 到 Loguru"""

# 用于记录游戏运行日志

import asyncio
import contextlib
import inspect
import logging as std_logging
import os
import sys
from pathlib import Path

from loguru import logger

# 默认关闭完整堆栈，避免日志被异常 traceback 刷屏；可通过环境变量开启
_LOGURU_FULL_TRACEBACK = os.environ.get("LOGURU_FULL_TRACEBACK", "0").lower() in (
    "1",
    "true",
    "yes",
)

# 默认抑制部分底层库的低级别日志，避免污染终端/文件
_SUPPRESSED_LOW_LEVEL_LOGGERS = {"httpcore", "asyncio", "aiohttp"}
_SUPPRESSED_THIRD_PARTY_LOGGERS = {"nonebot", "uvicorn", "websockets"}


def _third_party_noise_filter(record) -> bool:
    name = (record["name"] or "").split(".")[0]
    if name in _SUPPRESSED_THIRD_PARTY_LOGGERS:
        return bool(record["level"].no >= logger.level("WARNING").no)
    return True


logger.remove()

_handlers = {"console": None, "file": None}


class LoguruHandler(std_logging.Handler):
    def emit(self, record: std_logging.LogRecord):
        if (
            record.name.split(".")[0] in _SUPPRESSED_LOW_LEVEL_LOGGERS
            and record.levelno < std_logging.WARNING
        ):
            return
        if record.name.split(".")[0] == "uvicorn":
            exc_type = record.exc_info[0] if record.exc_info else None
            if exc_type is not None and issubclass(
                exc_type, (KeyboardInterrupt, asyncio.CancelledError)
            ):
                return
            message = record.getMessage()
            if message.startswith("Traceback") and (
                "KeyboardInterrupt" in message or "asyncio.exceptions.CancelledError" in message
            ):
                return
        if record.levelno == std_logging.DEBUG:
            level: str | int = "TRACE"
        else:
            try:
                level = logger.level(record.levelname).name
            except ValueError:
                level = record.levelno  # 未知级别名, 直接传数值(loguru 支持)
        frame, depth = inspect.currentframe(), 0
        while frame and (depth == 0 or frame.f_code.co_filename == std_logging.__file__):
            frame = frame.f_back
            depth += 1
        exc = (
            record.exc_info
            if _LOGURU_FULL_TRACEBACK or record.levelno >= std_logging.ERROR
            else False
        )
        logger.opt(depth=depth, exception=exc, colors=False).log(level, "{}", record.getMessage())


def setup_logging(
    log_level: str = "INFO",
    log_console: bool = True,
    log_file: Path | None = None,
    log_format: str | None = None,
    log_format_console: str | None = None,
    intercept_standard_logging: bool = True,
) -> None:
    global _handlers

    if _handlers["console"] is not None:
        with contextlib.suppress(ValueError):
            logger.remove(_handlers["console"])
        _handlers["console"] = None
    if _handlers["file"] is not None:
        with contextlib.suppress(ValueError):
            logger.remove(_handlers["file"])
        _handlers["file"] = None

    if log_format is None:
        log_format = (
            "[{thread.name:^12}] {time:HH:mm:ss} | {level:<8} | "
            "{name}.{function}:{line:03d} | {message}"
        )
    if log_format_console is None:
        log_format_console = (
            "<level>[{thread.name:^12}] {time:HH:mm:ss} | {level:<8} | "
            "{name}.{function}:{line:03d} | {message}</level>"
        )

    if log_console:
        _handlers["console"] = logger.add(  # type: ignore
            sys.stderr,
            format=log_format_console,
            level=log_level,
            colorize=True,
            filter=_third_party_noise_filter,
        )

    if log_file is not None:
        log_file = Path(log_file)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        _handlers["file"] = logger.add(  # type: ignore
            str(log_file),
            format=log_format,
            level=log_level,
            rotation="10 MB",
            compression="zip",
            backtrace=_LOGURU_FULL_TRACEBACK,
            diagnose=_LOGURU_FULL_TRACEBACK,
            enqueue=True,
            filter=_third_party_noise_filter,
        )

    if intercept_standard_logging:
        root_logger = std_logging.getLogger()
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)
        root_logger.addHandler(LoguruHandler())
        root_logger.setLevel(std_logging.DEBUG)


__all__ = [
    "logger",
    "setup_logging",
    "LoguruHandler",
]


if __name__ == "__main__":
    setup_logging(log_level="DEBUG", log_console=True)
    logger.info("日志系统已就绪")