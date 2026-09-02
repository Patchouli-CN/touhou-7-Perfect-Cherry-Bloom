"""TH08(东方永夜抄)的时刻(时钟)值对象 —— 永夜异变当晚的时间推进。

对照 th08 反编译源码(Reference/th08-ref/src/):
- 开局 0 单位 = 23:00, EX 面从 6 单位 = 2:00 开始
  (GameManagerSetup.cpp:101-105: currentStage==8 → stageMode=6);
- 1 单位 = 30 分钟, 上限 12 = 5:00am(GameManager.cpp:342-348 的
  Bad Ending 路线判定阈值 —— 判定本身不进本轮, 见阶段 3);
- op181 面内 +1 单位封顶 12(EclRunHigh.inl:957-967), op180 隐藏表盘
  (EclRunHigh.inl:956); VM handler 只经 host 方法路由(clock_advance/
  clock_hide), 时刻状态由本对象承载。

日期锚点从 utils.gensokyo_time 导入(永夜异变 = 第 119 季 9 月 27 日夜),
尊重 ZUN 设定; 时分秒的表达直接用 py 内置 datetime —— 年月日经
``GensokyoSeasonTime.at()`` 桥由季历提供, 时钟概念由本类补充。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import msgspec

from ...utils.gensokyo_time import INCIDENT_ETERNAL_NIGHT, GensokyoIncident

#: 时刻所属的异变(第 119 季 9 月 27 日夜, 永夜抄当晚)
INCIDENT: GensokyoIncident = INCIDENT_ETERNAL_NIGHT

MINUTES_PER_UNIT = 30  # 1 单位 = 30 分钟
START_UNITS = 0  # 本篇开局 23:00
EXTRA_START_UNITS = 6  # EX 面开局 2:00(GameManagerSetup.cpp:101-105)
MAX_UNITS = 12  # 5:00am 封顶(GameManager.cpp:343)


class Th08Clock(msgspec.Struct):
    """时刻值对象: units(0..12) × 30 分钟, 从 23:00 起算。"""

    units: int = START_UNITS  # 0=23:00; 6=2:00(EX 面); 12=5:00(上限)
    hidden: bool = False  # op180 HideClockTime(EclRunHigh.inl:956)

    @classmethod
    def for_stage(cls) -> "Th08Clock":
        """本篇开局(23:00)。"""
        return cls(START_UNITS)

    @classmethod
    def for_extra(cls) -> "Th08Clock":
        """EX 面开局(2:00, GameManagerSetup.cpp:101-105)。"""
        return cls(EXTRA_START_UNITS)

    def advance(self) -> bool:
        """+1 单位(30 分钟), 封顶 MAX_UNITS; 返回是否真的推进了。"""
        if self.units >= MAX_UNITS:
            return False
        self.units += 1
        return True

    def hide(self) -> None:
        self.hidden = True

    @property
    def moment(self) -> datetime:
        """当前时刻的外界 datetime: 开局 9月27日 23:00 + units×30 分钟,
        跨午夜自然进入 9月28日(中秋名月当天)。年月日经 gensokyo_time 的
        ``at()`` 桥提供, 时分秒由本时钟推进 —— datetime 原生处理。"""
        return INCIDENT.season_time.at(23, 0) + timedelta(
            minutes=self.units * MINUTES_PER_UNIT
        )

    @property
    def time_of_day(self) -> tuple[int, int]:
        """(时, 分): 23:00 + units×30 分钟, 跨午夜回绕到次日(9 月 28 日)。"""
        m = self.moment
        return m.hour, m.minute

    @property
    def next_day(self) -> bool:
        """是否已跨午夜(进入 9 月 28 日)。"""
        return self.moment.date() > INCIDENT.outside_date

    def __str__(self) -> str:
        h, m = self.time_of_day
        return f"{h}:{m:02d}"
