"""modding 教程示例: 无敌 + 满火力 + 每 3 秒以自机为中心放一圈自定义环形弹幕。

演示官方魔改入口 ModApi(touhou.apis.modding): 包住对局门面 Game 即得写操作面,
全程只用公共 API(touhou 顶层 + touhou.apis.modding), 不摸引擎内部成员。
注意: ModApi 的写操作绕过正常游戏规则, 仅供魔改/实验。

运行: uv run python examples/mod_fun.py
"""
from __future__ import annotations

from touhou import Difficulty, Input, TouhouWorld
from touhou.apis.modding import ModApi


def main() -> None:
    tw = TouhouWorld(difficulty=Difficulty.LUNATIC, headless=True, seed=7)
    stream = tw.run()
    mods = ModApi(tw.game)  # 官方魔改口子: 包住对局门面, 叠加写操作面

    def godmode_and_danmaku(game) -> Input:
        # 无敌挂: 无敌计时每帧被引擎递减, 故放在 policy 里每帧重置
        mods.god_mode()
        mods.set_power(mods.full_power)  # 火力拉满(上限取自作品数值表)
        mods.set_bombs(8)                # 炸弹管够
        # 每 180 帧(3 秒)以自机为中心放一圈 24 弹的环形弹幕
        if game.frame % 180 == 0 and game.phase.value == "running":
            x, y = mods.player_pos
            mods.fire_ring(x, y, arms=24, speed=1.5,
                           sprite=1, sprite_offset=6)  # 弹型号含义由作品定义(th07: 大玉·青)
        return Input(shoot=True, advance=True)

    stream.policy = godmode_and_danmaku
    for ev in stream:
        print(f"[f{ev.frame:6d}] {ev.kind.value} {ev.name or ''}")
        if tw.game.frame >= 3000:
            break
    g = tw.game
    # 无敌挂验证: 全程公共属性读回, lives 没掉过
    print(f"\nframe={g.frame} lives={g.lives}(没掉过) score={g.score} "
          f"场上弹幕数={mods.bullet_count}")


if __name__ == "__main__":
    main()
