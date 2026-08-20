""" 结算与评级 —— 移植自 ResultScreen.cpp / 规格 §F。

ScoreKeeper 累计一局(或一个 playthrough)的运行时统计,
到结束(通关/结算)时用 rating() 算出综合评价, 并可插入 Top10 榜。
"""

from __future__ import annotations

import msgspec

# 难度权重: {Easy, Normal, Hard, Lunatic, Extra}(Phantasm 复用 Extra)
DIFFICULTY_WEIGHTS = (-30, -10, 20, 30, 30)
# 符卡权重: 每张捕获的分值倍率
SPELLCARD_WEIGHTS = (1, 1.5, 1.5, 2, 2.5)
SCORE_MAX = 999_999_999


class RunStats(msgspec.Struct):
    """一局的运行时累计统计。"""

    score: int = 0
    difficulty: int = 1          # 0..5
    deaths: float = 0.0          # Miss 数
    bombs_used: float = 0.0
    retries: int = 0             # Continue 数
    spellcards_captured: int = 0
    graze_total: int = 0
    point_items_collected: int = 0
    cleared: bool = False
    clear_percent: float = 0.0   # 通关率(未通关时 <1)
    play_time_frames: int = 0    # 帧计时(用于 slow%)

    # ---- 运行时累加 ----
    def add_score(self, points: int) -> None:
        self.score = min(self.score + points, SCORE_MAX)

    def add_graze(self, n: int = 1) -> None:
        self.graze_total = min(self.graze_total + n, 999999)

    def add_point_item(self, n: int = 1) -> None:
        self.point_items_collected += n

    def add_spellcard(self, n: int = 1) -> None:
        self.spellcards_captured += n


def rating(stats: RunStats, *, slow_percent: float = 0.0,
           total_play_frames: int = 180621) -> float:
    """综合评价 `rankingProbably`。slow_percent 为减速百分比(0..100)。"""
    d = min(stats.difficulty, 4)  # Phantasm 复用 Extra 权重
    score = stats.score
    r = 0.0

    # 分数段
    if score < 2_000_000:
        r -= 20
    elif score < 200_000_000:
        r += -20 + (score - 2_000_000) / 198_000_000 * 60
    else:
        r += 40
    r += DIFFICULTY_WEIGHTS[d]

    # 通关率
    if stats.cleared:
        r += 70
    else:
        r += stats.clear_percent * 70

    # 续关/死亡/炸弹
    r -= stats.retries * 10
    r += -stats.deaths * 5 + 10
    r += -stats.bombs_used * 2 + 10
    # 符卡
    r += stats.spellcards_captured * SPELLCARD_WEIGHTS[d]

    # 减速(作弊/超减速 → -999)
    if slow_percent < 50:
        r += -70 * slow_percent / 100
    else:
        return -999.0

    # 点道具/擦弹
    r += (0.01 * stats.point_items_collected) if stats.point_items_collected < 800 else 8
    r += (0.0025 * stats.graze_total) if stats.graze_total < 5000 else 12.5
    return r


def clear_percent(stage_seconds: float, *, extra: bool = False, phantasm: bool = False) -> float:
    """由通关用时换算通关率(基于规格中的时间基准)。"""
    if phantasm:
        base = 85000.0
    elif extra:
        base = 80000.0
    else:
        base = 180621.0
    return min(0.99, stage_seconds * 60.0 / base)


class ScoreRecord(msgspec.Struct):
    """一条排行榜记录。"""

    score: int
    character: int
    difficulty: int
    stage: int
    retries: int = 0
    slow_percent: float = 0.0
    name: str = "YOU"

    def key(self) -> int:
        return self.score


class TopList:
    """Top10 排行榜(按分数降序)。"""

    def __init__(self, size: int = 10) -> None:
        self._records: list[ScoreRecord] = []
        self._size = size
        # 默认"空榜"分数: 100000 - k*10000
        self._records = [
            ScoreRecord(score=max(0, 100000 - k * 10000), character=0, difficulty=1, stage=1)
            for k in range(size)
        ]

    def insert(self, rec: ScoreRecord) -> int:
        """插入一条, 返回名次(0-based); 若未进榜返回 -1。"""
        self._records.append(rec)
        self._records.sort(key=lambda x: x.score, reverse=True)
        self._records = self._records[: self._size]
        try:
            return self._records.index(rec)
        except ValueError:
            return -1

    def top(self, n: int | None = None) -> list[ScoreRecord]:
        return self._records[: (n if n is not None else self._size)]

    def __len__(self) -> int:
        return len(self._records)
