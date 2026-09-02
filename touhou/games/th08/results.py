"""结算与评级(th08 东方永夜抄) —— 移植自 th08-ref ResultScreen.cpp。

照 th07(games/th07/results.py)改编; 差异(ResultScreen.cpp:2153-2290
DrawFinalStats):
- 通关率基准: 本篇 stagePlayTimeAll/195559.0, Extra /80000.0
  (th07 是 180621/85000/80000, th08 无 Phantasm);
- 难度权重 g_DifficultyWeightList {-30,-10,20,30,30} 与符卡权重
  g_SpellcardsWeightList {1,1.5,1.5,2,2.5} 与 th07 同值(ResultScreen.cpp:96/
  DrawFinalStats 局部表, :2156/:2202/:2239)。
"""

from __future__ import annotations

import msgspec

# 难度权重 (DrawFinalStats 局部 g_DifficultyWeightList, ResultScreen.cpp:2156)
DIFFICULTY_WEIGHTS = (-30, -10, 20, 30, 30)
# 符卡权重 (g_SpellcardsWeightList, ResultScreen.cpp:96)
SPELLCARD_WEIGHTS = (1, 1.5, 1.5, 2, 2.5)
SCORE_MAX = 999_999_999


class RunStats(msgspec.Struct):
    """一局的运行时累计统计。"""

    score: int = 0
    difficulty: int = 1  # 0..4
    deaths: float = 0.0  # Miss 数
    bombs_used: float = 0.0
    retries: int = 0  # Continue 数
    spellcards_captured: int = 0
    graze_total: int = 0
    point_items_collected: int = 0
    cleared: bool = False
    clear_percent: float = 0.0  # 通关率(未通关时 <1)
    play_time_frames: int = 0

    # ---- 运行时累加 ----
    def add_score(self, points: int) -> None:
        self.score = min(self.score + points, SCORE_MAX)

    def add_graze(self, n: int = 1) -> None:
        self.graze_total = min(self.graze_total + n, 999999)

    def add_point_item(self, n: int = 1) -> None:
        self.point_items_collected += n

    def add_spellcard(self, n: int = 1) -> None:
        self.spellcards_captured += n


def rating(
    stats: RunStats, *, slow_percent: float = 0.0, total_play_frames: int = 195559
) -> float:
    """综合评价 unconsumedPerformanceRating (ResultScreen.cpp:2168-2290)。

    slow_percent 为减速百分比(0..100; headless 固定 60fps 恒 0)。
    """
    d = min(stats.difficulty, 4)
    score = stats.score
    r = 0.0

    # 分数段 (displayScore: <200万 -20; <2亿 线性到 +40; 以上 +40)
    if score < 2_000_000:
        r -= 20
    elif score < 200_000_000:
        r += -20 + (score - 2_000_000) / 198_000_000 * 60
    else:
        r += 40
    r += DIFFICULTY_WEIGHTS[d]

    # 通关率 (completion = stagePlayTimeAll/195559, 封顶 0.99)
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

    # 减速(≥50% → -999)
    if slow_percent < 50:
        r += -70 * slow_percent / 100
    else:
        return -999.0

    # 点道具/擦弹
    r += (
        (0.01 * stats.point_items_collected) if stats.point_items_collected < 800 else 8
    )
    r += (0.0025 * stats.graze_total) if stats.graze_total < 5000 else 12.5
    return r


def clear_percent(stage_seconds: float, *, extra: bool = False) -> float:
    """由通关用时换算通关率 (ResultScreen.cpp:2170-2171 的 completion)。"""
    base = 80000.0 if extra else 195559.0
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
        self._records = [
            ScoreRecord(
                score=max(0, 100000 - k * 10000), character=0, difficulty=1, stage=1
            )
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
