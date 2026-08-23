"""观战/AI 示例: headless 跑一局, 打印流式事件, 并用自定义策略开车。

运行: uv run python examples/auto_play.py

观战变体(窗口里看 AI 打): 去掉 headless=True 改为
``TouhouWorld(..., headless=False, auto_input=my_policy)`` 再 ``tw.run()``
—— 窗口照开但跳过标题菜单直接进游戏, 每帧输入来自策略, Esc 中止观战。
headless 侧顺带录像: ``stream.save_replay()`` 存下喂过的每帧输入
(engine/replay.py 格式, 可在窗口版 Replay 菜单播放)。
"""
from __future__ import annotations

import random

from touhou import Difficulty, GameEventKind, Input, TouhouWorld, Game


def my_policy(game: Game) -> Input:
    """每帧输入策略(AI 的入口)。这里演示: 按住射击 + 蛇皮走位。"""
    f = game.frame
    return Input(
        left=(f // 90) % 2 == 0, right=(f // 90) % 2 == 1,
        up=(f // 150) % 3 == 0, shoot=True,
        advance=True,            # 对话自动推进
        bomb=(f % 1800 == 0),    # 每 30 秒扔一发 bomb(壕)
    )


def main() -> None:
    tw = TouhouWorld(difficulty=Difficulty.NORMAL,
                     lives=3, headless=True, seed=42)
    stream = tw.run()
    stream.policy = my_policy   # 接管输入
    for ev in stream:
        extra = f" {ev.name}" if ev.name else ""
        print(f"[f{ev.frame:6d}] {ev.kind.value}{extra}")
        if tw.game.frame >= 6000:   # 演示只跑 100 秒
            break
    g = tw.game
    print(f"\n结算: score={g.score} lives={g.lives} "
          f"graze={g.graze} phase={g.phase.value}")


if __name__ == "__main__":
    random.seed(0)
    main()
