"""魔改示例: 无敌 + 满火力 + 每 3 秒以自机为中心放一圈自定义环形弹幕。

演示怎么通过 tw.game 拿到引擎 internals 做 mod。
运行: uv run python examples/mod_fun.py
"""
from __future__ import annotations

import math

from touhou import Difficulty, Input, TouhouWorld
from touhou.engine.bullets import Aim, Burst
from touhou.utils import Vec2


def godmode_and_danmaku(game) -> Input:
    impl = game._impl                     # 引擎内部(魔改入口)
    g = impl.globals
    # 无敌挂: 每帧重置无敌计时
    impl.player.invulnerability_timer = 999
    g.current_power = 128.0               # 火力拉满
    g.bombs_remaining = 8.0               # 炸弹管够
    # 每 180 帧(3 秒)以自机为中心放一圈 24 弹的环形弹幕
    if game.frame % 180 == 0 and game.phase.value == "running":
        p = impl.player.pos
        impl.bullets.fire(Burst(
            path=Vec2(p.x, p.y), base_angle=math.pi / 2,
            aim=Aim.RING_ABSOLUTE, arms=24, rings=1,
            speed_a=1.5, speed_b=1.5, angle_step=0.0,
            sprite=1, sprite_offset=6,    # 大玉·青色
        ))
    return Input(shoot=True, advance=True)


def main() -> None:
    tw = TouhouWorld(character=0, difficulty=Difficulty.LUNATIC,
                     headless=True, seed=7)
    stream = tw.run()
    stream.policy = godmode_and_danmaku
    for ev in stream:
        print(f"[f{ev.frame:6d}] {ev.kind.value} {ev.name or ''}")
        if tw.game.frame >= 3000:
            break
    g = tw.game
    # 无敌挂验证: 被打中也死不了
    print(f"\nframe={g.frame} lives={g.lives}(没掉过) score={g.score} "
          f"场上弹幕数={len(g._impl.bullets.alive())}")


if __name__ == "__main__":
    main()
