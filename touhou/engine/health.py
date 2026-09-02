"""运行健康监控 —— 帧预算超支累计与节流告警(引擎层, 与渲染后端无关)。

告警文案致敬 Minecraft 的经典日志
"Can't keep up! Is the server overloaded? Running Xms or Y ticks behind"。
首个使用者是 pygame 后端(games/th07/view/pygame_backend.py 的 present
帧末上报单帧耗时); 后续 ModernGL 后端或逻辑帧循环可直接复用。
"""

from __future__ import annotations

import time
from collections.abc import Callable

from ..logger import logger as log

__all__ = ["HealthCenter"]


class HealthCenter:
    """帧耗时健康监控: 超预算累计落后量, 落后超阈值时节流 WARNING 一次。

    单帧耗时超出预算(1000/fps ms)即累计落后量(快帧缓慢偿还, 不清零);
    累计落后超 ``warn_ticks`` 帧且距上次告警超过 ``throttle_s`` 秒时
    WARNING 一次, 告警后清零重新累计(节流防刷屏)。
    """

    __slots__ = (
        "subject",
        "budget_ms",
        "warn_ms",
        "throttle_s",
        "_now",
        "behind_ms",
        "_warned_at",
    )

    def __init__(
        self,
        subject: str = "renderer",
        *,
        fps: float = 60.0,
        warn_ticks: float = 40.0,
        throttle_s: float = 5.0,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.subject = subject  # 告警文案里的主体名(renderer/server/…)
        self.budget_ms = 1000.0 / fps  # 单帧预算
        self.warn_ms = warn_ticks * self.budget_ms  # 落后告警阈值(40 帧)
        self.throttle_s = throttle_s
        self._now = now
        self.behind_ms = 0.0  # 当前累计落后量
        self._warned_at = 0.0  # 上次告警时刻(0 = 首次告警不节流)

    def tick(self, elapsed_ms: float) -> bool:
        """上报一帧耗时; 触发告警时返回 True(并重置累计)。"""
        self.behind_ms = max(0.0, self.behind_ms + elapsed_ms - self.budget_ms)
        now = self._now()
        if self.behind_ms > self.warn_ms and now - self._warned_at > self.throttle_s:
            self._warned_at = now
            log.warning(
                "Can't keep up! Is the {} overloaded? "
                "Running {:.0f}ms ({:.1f} ticks) behind",
                self.subject,
                self.behind_ms,
                self.behind_ms / self.budget_ms,
            )
            self.behind_ms = 0.0
            return True
        return False
