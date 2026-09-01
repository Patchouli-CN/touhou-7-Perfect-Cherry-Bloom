"""启动环境探测 —— 收集当前作品环境, 供启动日志(INFO 级)打印。

叶子模块: 只依赖标准库 + paths/registry(均为叶子)。
"""

from __future__ import annotations

import platform
import sys
from pathlib import Path

from .paths import resolve_data_path
from .registry import registered_games, registered_renderers, GAME_TITLES
from .schema.archive import open_archive


def detect_environment(data_path: str | Path | None = None) -> dict[str, str]:
    """探测运行环境。失败项记"未找到/未知", 不抛异常。"""
    info: dict[str, str] = {
        "python": sys.version.split()[0],
        "platform": f"{platform.system()} {platform.release()}",
        "pygame": "未知",
    }
    # pygame 版本从已加载的模块里取(env.py 保持逻辑层纯净, 不直接 import pygame;
    # __main__ 启动链已加载过它, 探测不到就记"未加载")
    try:
        pg = sys.modules.get("pygame")
        info["pygame"] = pg.version.ver if pg is not None else "未加载"
    except Exception:  # noqa: BLE001 - 探测不炸是硬性要求
        pass

    res = resolve_data_path(data_path)
    info["res_dat"] = str(res)
    info["res_format"] = "未知"
    if res.exists():
        info["res_entries"] = "?"
        try:
            # 只读目录头不解压: open_archive 只认头 + 解析文件表, 开销小
            arc = open_archive(res)
            info["res_entries"] = str(len(arc))
            info["res_format"] = arc.format_name
        except Exception:  # noqa: BLE001 - 探测不炸是硬性要求
            info["res_entries"] = "无法读取"
    else:
        info["res_entries"] = "未找到"

    bgm = res.with_name("thbgm.dat")
    info["bgm_dat"] = str(bgm) if bgm.exists() else "未找到(回退 MIDI)"

    games = registered_games()
    info["games"] = ", ".join(games) or "无"
    info["renderers"] = ", ".join(registered_renderers()) or "无"
    info["title"] = GAME_TITLES.get(games[0], games[0]) if games else "未知"
    return info


def log_environment(
    log, data_path: str | Path | None = None, game: str | None = None
) -> None:
    """把环境探测结果写进 INFO 日志。

    ``game`` 是本次请求启动的作品名(如 CLI 的 --game): 已注册则显示作品
    标题, 未注册则明确标注——避免"请求 th08 却打印 th07 标题"的误导。
    不传时回落旧行为(显示首个已注册作品的标题)。
    """
    info = detect_environment(data_path)
    if game is not None:
        if game in registered_games():
            log.info("启动作品: {} — {}", game, GAME_TITLES.get(game, game))
        else:
            log.info("启动作品: {} (未注册! 已注册: {})", game, info["games"])
    else:
        log.info("作品: {}", info["title"])
    log.info(
        "环境: Python {} | pygame {} | {}",
        info["python"],
        info["pygame"],
        info["platform"],
    )
    log.info(
        "资源包: {} ({} 格式, {} 个条目)",
        info["res_dat"],
        info["res_format"],
        info["res_entries"],
    )
    log.info("BGM 包: {}", info["bgm_dat"])
    log.info("注册作品: {} | 渲染后端: {}", info["games"], info["renderers"])
