"""躲弹 baseline 示例: 最简势能场规避 —— 纯躲弹幕算法的起点(不是 SOTA)。

思路: 每帧读 ``game.bullets_array()`` 的 (x, y, vx, vy, hitbox, sprite)
与 ``game.player_pos``, 对每颗弹按速度向量线性外推若干帧后的位置, 距离
越近斥力越大(三次方衰减), 叠加版边回中力, 合力方向离散化成 8 向 Input;
最近威胁很近时按 focus 精控。

近似在哪(天花板, 不是 bug):
- 线性外推用的是子弹当前 angle/speed 的换算向量, 对 ECL 命令弹
  (变速 TARGET_VEL / 转向 DIR_CHANGE / 角速度 TARGET_ANGLE) 只是瞬时
  近似, 弹转向/加速后预测即失效;
- 只看敌弹, 不看激光与敌人体术;
- 8 向离散输出, 非连续控制。

运行(默认开窗口观战, 看 AI 打全流程; Esc 中止):
    uv run python examples/dodge_ai.py
headless 跑数据(测试/调参用, 无需窗口):
    DODGE_AI_HEADLESS=1 uv run python examples/dodge_ai.py
    帧数用环境变量 DODGE_AI_FRAMES 覆盖(默认 3600 ≈ 1 分钟)。
"""

from __future__ import annotations

import math
import os

import numpy as np

from touhou import Game, GameEventKind, GamePhase, Input, TouhouWorld

PREDICT_FRAMES = 16  # 线性外推帧数(视野)
THREAT_RADIUS = 96.0  # 只规避此半径内的威胁(px)
WALL_MARGIN = 48.0  # 距版边多近开始叠加回中力(px)
FOCUS_RADIUS = 32.0  # 最近威胁小于此值时按 focus 精控(px)
SCREEN_W, SCREEN_H = 384.0, 448.0

# 8 向单位向量 (dx, dy), 屏幕系 y 向下; 下标 = 八方离散角
_DIRS = ((1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1))


def dodge_policy(game: Game) -> Input:
    """势能场规避: 威胁弹斥力 + 版边回中, 合力方向 → 8 向按键。"""
    px, py = game.player_pos
    fx = fy = 0.0
    focus = False

    arr = game.bullets_array()
    if len(arr):
        future = arr[:, :2] + arr[:, 2:4] * PREDICT_FRAMES  # 线性外推落点
        rel = future - (px, py)
        # 净距离扣除弹判定半径(大弹更早产生斥力)
        dist = np.hypot(rel[:, 0], rel[:, 1]) - arr[:, 4]
        near = dist < THREAT_RADIUS
        if near.any():
            rel, dist = rel[near], np.maximum(dist[near], 1.0)
            w = 1.0 / dist**3
            fx -= float((rel[:, 0] * w).sum())
            fy -= float((rel[:, 1] * w).sum())
            focus = bool((dist < FOCUS_RADIUS).any())

    # 版边回中: 别把自己逼进死角
    if px < WALL_MARGIN:
        fx += 1.0
    elif px > SCREEN_W - WALL_MARGIN:
        fx -= 1.0
    if py < WALL_MARGIN:
        fy += 1.0
    elif py > SCREEN_H - WALL_MARGIN:
        fy -= 1.0

    if fx * fx + fy * fy < 1e-6:
        return Input(shoot=True, advance=True, focus=focus)
    octant = round(math.atan2(fy, fx) / (math.pi / 4)) % 8
    dx, dy = _DIRS[octant]
    return Input(
        left=dx < 0,
        right=dx > 0,
        up=dy < 0,
        down=dy > 0,
        shoot=True,
        advance=True,
        focus=focus,
    )


def main() -> None:
    if os.environ.get("DODGE_AI_HEADLESS") != "1":
        # 窗口观战: 跳过标题菜单直接进游戏, 每帧输入来自 dodge_policy;
        # Esc 中止, 终局(通关/GameOver)自动退出。观战自动录像(replays/)。
        tw = TouhouWorld(
            character="ReimuA",
            difficulty="Normal",
            seed=42,
            headless=False,
            auto_input=dodge_policy,
        )
        print("[dodge_ai] 窗口观战启动(Esc 中止)")
        tw.run()  # 阻塞至关窗/终局
        print("[dodge_ai] 观战结束")
        return

    frames = int(os.environ.get("DODGE_AI_FRAMES", "3600"))
    game = Game(character="ReimuA", difficulty="Normal", seed=42)
    deaths = 0
    for _ in range(frames):
        events = game.step(dodge_policy(game))
        deaths += sum(1 for e in events if e.kind == GameEventKind.PLAYER_DEATH)
        if game.phase == GamePhase.GAME_OVER:
            game.finalize_game_over()  # headless 无续关 UI, 直接收尾
            break
        if game.phase == GamePhase.RESULT:
            break
    print(
        f"[dodge_ai] 存活 {game.frame} 帧, 中弹 {deaths} 次, "
        f"残机 {game.lives}, phase={game.phase.value}"
    )


if __name__ == "__main__":
    main()
