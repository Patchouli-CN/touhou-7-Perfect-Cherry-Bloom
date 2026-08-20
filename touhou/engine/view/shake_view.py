"""画面震动(震屏) —— BombEffects type=1 的 view 侧衰减 (ScreenEffect.cpp)。

C++ 侧: BombEffects::RegisterChain(1, duration, ampStart, ampEnd) 在
BombData.cpp(炸弹演出)与 EnemyEclInstr.cpp(爱丽丝曲弹/弹转化/effect1e)
注册; OnUpdateScreenShake (ScreenEffect.cpp:249-293) 每帧:

- timer++, timer >= duration 则移除 (:261-265);
- 振幅 amp = (ampEnd - ampStart) * timer / duration + ampStart (:267-269);
- x/y 各由 g_Rng 三选一取 {0, +amp, -amp} 写入 g_AnmManager->offset (:270-291);
- 多个并存时各链元素依次覆写 offset, 后注册者生效(同优先度按注册序)。

offset 作用于 AnmManager 全部绘制(2D quad + 3D world 矩阵,
AnmManager.cpp:831-838/:1474-1475), 帧首由 Supervisor 清零
(Supervisor.cpp:166-167), Gui 绘制前再清零(Gui.cpp:159-160) ——
即只有游戏区画面抖动, 右栏 HUD 不动。

本模块: 引擎侧只在 C++ 注册点透出 (duration, amp_start, amp_end) 事件
(impl.frame_shakes), ScreenShake 在 view 层维护衰减并给出整帧位移。
RNG 用本地独立源(random.Random), 不消耗游戏 rng(回放确定性)。
"""

from __future__ import annotations

import random


class ScreenShake:
    """震屏状态: register() 登记事件, tick() 推进一帧并返回 (dx, dy) 偏移。"""

    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng if rng is not None else random.Random()
        # 元素 [duration, amp_start, amp_end, timer]
        self._active: list[list[float]] = []

    def register(self, duration: int, amp_start: int, amp_end: int) -> None:
        """登记一次震动 (BombEffects::RegisterChain type=1 的参数原样)。"""
        if duration <= 0:
            return
        self._active.append([duration, amp_start, amp_end, 0])

    @property
    def active(self) -> bool:
        return bool(self._active)

    def _pick(self, amp: float) -> float:
        """g_Rng.GetRandomU32InRange(3): 0→0 / 1→+amp / 2→-amp。"""
        r = self._rng.randrange(3)
        return 0.0 if r == 0 else (amp if r == 1 else -amp)

    def tick(self) -> tuple[int, int]:
        """推进一帧, 返回本帧整帧位移 (dx, dy)。"""
        dx = dy = 0.0
        alive: list[list[float]] = []
        for s in self._active:
            s[3] += 1
            if s[3] >= s[0]:
                continue  # timer >= duration → 移除 (ScreenEffect.cpp:262-265)
            alive.append(s)
            amp = (s[2] - s[1]) * s[3] / s[0] + s[1]  # :267-269
            dx, dy = self._pick(amp), self._pick(amp)  # 后注册者覆写, 同 C++
        self._active = alive
        return int(dx), int(dy)
