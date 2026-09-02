"""运行健康监控 —— 滑动窗口实测帧率告警(引擎层, 与渲染后端无关)。

告警文案致敬 Minecraft 的经典日志
"Can't keep up! Is the server overloaded? Running Xms or Y ticks behind"。
首个使用者是 pygame 后端(games/th07/view/pygame_backend.py 的 present
帧末上报单帧耗时); 后续 ModernGL 后端或逻辑帧循环可直接复用。

判据是窗口实测帧率而非逐帧预算超支: 持续轻微超速(如稳定 58fps,
肉眼无感)不报警; 只有窗口平均帧率跌破 ``warn_fps``(肉眼可见卡顿)
才告警 —— 告警条件与玩家感知对齐。
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable

from ..logger import logger as log

__all__ = ["HealthCenter"]


class HealthCenter:
    """帧率健康监控: 滑动窗口实测 FPS 跌破阈值时节流 WARNING 一次。

    每帧耗时进滑动窗口(``window_s`` 秒), 窗口平均帧率低于 ``warn_fps``
    且距上次告警超过 ``throttle_s`` 秒时 WARNING 一次。窗口未满(启动初期)
    不告警。落后量(窗口总耗时 − 窗口帧数 × 帧预算)随告警一并报告。
    """

    __slots__ = (
        "subject",
        "budget_ms",
        "warn_fps",
        "throttle_s",
        "window_size",
        "_now",
        "_window",
        "_window_ms",
        "_warned_at",
    )

    def __init__(
        self,
        subject: str = "renderer",
        *,
        fps: float = 60.0,
        warn_fps: float = 50.0,
        window_s: float = 1.0,
        throttle_s: float = 5.0,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.subject = subject  # 告警文案里的主体名(renderer/server/…)
        self.budget_ms = 1000.0 / fps  # 单帧预算(落后量报告用)
        self.warn_fps = warn_fps
        self.throttle_s = throttle_s
        self._now = now
        self.window_size = max(1, round(fps * window_s))
        self._window: deque[float] = deque(maxlen=self.window_size)
        self._window_ms = 0.0  # 窗口内耗时总和(与 deque 同步维护, 免每次 sum)
        self._warned_at = 0.0  # 上次告警时刻(0 = 首次告警不节流)

    def tick(self, elapsed_ms: float) -> bool:
        """上报一帧耗时; 触发告警时返回 True。"""
        if len(self._window) == self.window_size:
            self._window_ms -= self._window[0]
        self._window.append(elapsed_ms)
        self._window_ms += elapsed_ms

        n = len(self._window)
        if n < self.window_size or self._window_ms <= 0.0:
            return False  # 窗口未满(启动初期)不告警
        now = self._now()
        if self._window_ms / n <= 1000.0 / self.warn_fps:
            return False  # 平均帧率仍在阈值之上
        if now - self._warned_at <= self.throttle_s:
            return False  # 节流防刷屏
        self._warned_at = now
        behind_ms = self._window_ms - n * self.budget_ms
        log.warning(
            "Can't keep up! Is the {} overloaded? "
            "Averaging {:.1f}fps, running {:.0f}ms ({:.1f} ticks) behind",
            self.subject,
            1000.0 * n / self._window_ms,
            behind_ms,
            behind_ms / self.budget_ms,
        )
        return True
