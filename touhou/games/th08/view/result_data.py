"""th08 Result 浏览面的取数/展示格式化 —— 高分榜/符卡战绩/统计的文本行生成, 无 pygame。

对照 th08-ref(@1861f88, 行号相对其 src/) ResultScreen.cpp: OnDraw 榜单段
(:2475-2549)与符卡段(:2552-2689)、HandleSpellCardScreen 的卡名隐藏/表头
(:1206-1242)、HandleOtherStatsScreen(:1988-2151)、AddedCallback 的默认行
预插(:2860-2883)与收取数统计(:2999-3023)。状态机见同包 result_flow.py。

score.json 缺口导致的偏离(数据层 B1 期既定, 本片只读):
- Hscr 无 lagPercentage → 榜单 Slow 列恒 "--";
- Hscr 无"通关"标记(原作 stage=99 → "(C)") → Stage 列恒面数字;
- plst 只存总游戏时间/通关/续关总数 → 统计屏的总起動時間与総プレイ時間
  同值、通关/续关的按难度细分列留空、练习行恒零(练习模式未实装)。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import msgspec

from ..progress import SHOT_ALL_ROW
from ..spellcards import (
    CARD_DIFFICULTY_LETTERS,
    SPELLCARD_LAST_WORD_START,
    SPELLCARDS_PER_DIFFICULTY,
)

if TYPE_CHECKING:
    from ....engine.score_store import ScoreStore

# 机体名单(g_CharacterList, ResultScreen.cpp:109-114; 文本 = config/i18n.csv:176-188
# 的 RESULT_SHOT_* 日文原文, 全角空格补齐是统计表对齐用途, 原样保留)
CHARACTER_ITEMS = (
    "霊夢＆紫　　　　",
    "魔理沙＆アリス　",
    "咲夜＆レミリア　",
    "妖夢＆幽々子　　",
    "博麗　霊夢　　　",
    "八雲　紫　　　　",
    "霧雨　魔理沙　　",
    "アリス・Ｍ　　　",
    "十六夜　咲夜　　",
    "レミリア・Ｓ　　",
    "魂魄　妖夢　　　",
    "西行寺　幽々子　",
    "全主人公合計　　",
)
NUM_CHARACTERS_SELECT = 12  # 高分榜机体选择 12 项(MoveCursor(SHOT_ALL), :897)
NUM_SHOT_TYPES = 13  # 符卡战绩 13 项(12 机体 + 全てのキャラクター, :1128/:1256)

BOARD_ROWS = 10  # 榜单行数(RESULT_SCREEN_MAX_DISPLAYED_RESULTS, :2436)
SPELLCARD_PAGE_ROWS = 10  # 符卡每页行数(:1214/:2572)

# 未遭遇卡名占位(TH_RESULT_SPELLCARD_NOT_UNLOCKED, config/i18n.csv:189)
SPELLCARD_NAME_HIDDEN = "？？？？？"

# 统计表头(TH_RESULT_PLAYCOUNT_INFO, config/i18n.csv:193)
_STATS_PLAYCOUNT_HEADER = "プレイ回数　　　 　Easy 　Norm 　Hard 　Luna  Extra  Total"

# 面上榜显示数字(g_ResultStageNumbers, :87; 入参 = 我们的 1 起 stage_no:
# 4=4A 5=4B 6=5面 7=6A 8=6B 9=EX; C 侧 stage 99=通关的 "(C)" 标记无存档字段)
_STAGE_DISPLAY = {1: 1, 2: 2, 3: 3, 4: 4, 5: 4, 6: 5, 7: 6, 8: 6, 9: 1}

# 空位默认行(AddedCallback :2860-2883: 100000-10000*k, 名 "--------",
# date "--/--", stage=STAGE1 → 显示 1)
_DEFAULT_BOARD_SCORE = 100000
_DEFAULT_BOARD_STEP = 10000
_DEFAULT_NAME = "--------"
_DEFAULT_DATE = "--/--"


class HighscoreRow(msgspec.Struct, frozen=True):
    """榜单一行(展示用已格式化字段)。"""

    rank: int  # 1 起名次
    name: str
    score: int
    retries: int  # 显示截到 9(:2532)
    stage_label: str  # 面数字(通关 "(C)" 无字段, 恒数字)
    date: str  # "MM/DD"; 空位 "--/--"


class SpellcardRow(msgspec.Struct, frozen=True):
    """符卡战绩一行(展示用)。"""

    card_no: int  # 卡号(0 起)
    number_label: str  # "No.42"(:2612, 卡号+1)
    name: str  # 卡名; 未遭遇 = SPELLCARD_NAME_HIDDEN
    attempted: bool  # attempts[shotType]>0(颜色分档用, :2597-2610)
    captured: bool  # captures[shotType]>0
    stats: str  # "  3/  5(N)" 或 "---/---(-)"(:2623-2650)
    max_bonus: int  # 0 = 不显示 MaxBonus 行(:2659-2665)


def _format_date(iso: str) -> str:
    """存档 ISO 日期 → 原作 "MM/DD"(FormatDate :1522-1532 的 %m/%d)。"""
    if len(iso) >= 10 and iso[4] == "-" and iso[7] == "-":
        # 例: "2026-09-03T..." → "09/03"
        return f"{iso[5:7]}/{iso[8:10]}"
    return _DEFAULT_DATE


def _catk_group(store: "ScoreStore", card_no: int) -> dict:
    """该行战绩所属组: Last Word 读 spellPractice 组(:2636-2650), 其余 inGame。"""
    entry = store.catk[card_no]
    if card_no >= SPELLCARD_LAST_WORD_START:
        practice = entry.get("practice")
        if isinstance(practice, dict):
            return practice
    return entry


# ---- 高分榜(OnDraw :2475-2549) ----
def highscore_rows(
    store: "ScoreStore", difficulty: int, character: int
) -> list[HighscoreRow]:
    """该 (难度, 机体) 的 10 行榜: 真实记录与 10 条空位默认行按分降序
    混排取前 10(AddedCallback 每榜预插 10 条默认分, :2860-2883 —— 低于
    10000 的真实记录被默认行挤出榜外; 同分新记录排前 = 真实记录先于
    同分默认行, InsertScore :534-537)。"""
    real = store.entries(difficulty, character)
    rows: list[HighscoreRow] = [
        HighscoreRow(
            rank=0,
            name=e["name"],
            score=e["score"],
            retries=min(e["numRetries"], 9),
            stage_label=str(_STAGE_DISPLAY.get(e["stage"], e["stage"])),
            date=_format_date(e["date"]),
        )
        for e in real
    ]
    for k in range(BOARD_ROWS):
        rows.append(
            HighscoreRow(
                rank=0,
                name=_DEFAULT_NAME,
                score=_DEFAULT_BOARD_SCORE - _DEFAULT_BOARD_STEP * k,
                retries=0,
                stage_label="1",
                date=_DEFAULT_DATE,
            )
        )
    # 稳定降序: 同分时真实记录(先入列)排在默认行前
    rows.sort(key=lambda r: -r.score)
    return [
        HighscoreRow(
            rank=i + 1,
            name=r.name,
            score=r.score,
            retries=r.retries,
            stage_label=r.stage_label,
            date=r.date,
        )
        for i, r in enumerate(rows[:BOARD_ROWS])
    ]


# ---- 符卡战绩(:1206-1242/:2552-2689) ----
def spellcard_rows(
    store: "ScoreStore", page_idx: int, page: int, shot_type: int
) -> list[SpellcardRow]:
    """该难度页(page_idx = 0..5)第 page 页的至多 10 行。卡名隐藏判定看
    inGame 组的 attempts[SHOT_ALL](:1223 —— 只打过练习的 Last Word 名也
    隐藏, 原作 asymmetric quirk 照抄); 数字组: Last Word 读 practice 组。"""
    cards = SPELLCARDS_PER_DIFFICULTY[page_idx]
    start = page * SPELLCARD_PAGE_ROWS
    rows = []
    for card_no in cards[start : start + SPELLCARD_PAGE_ROWS]:
        entry = store.catk[card_no]
        seen = entry["attempts"][SHOT_ALL_ROW] > 0
        group = _catk_group(store, card_no)
        attempts = group["attempts"][shot_type]
        captures = group["successes"][shot_type]
        if attempts == 0:
            stats = "---/---(-)"
        else:
            stats = "%3d/%3d(%s)" % (
                captures,
                attempts,
                CARD_DIFFICULTY_LETTERS[card_no],
            )
        # MaxBonus: inGame 组捕获过才显示(:2659-2665)
        ingame_captures = entry["successes"][shot_type]
        max_bonus = entry["highscore"][shot_type] if ingame_captures else 0
        rows.append(
            SpellcardRow(
                card_no=card_no,
                number_label="No.%02d" % (card_no + 1),
                name=entry["name"] if seen else SPELLCARD_NAME_HIDDEN,
                attempted=attempts > 0,
                captured=captures > 0,
                stats=stats,
                max_bonus=max_bonus,
            )
        )
    return rows


def spellcard_header(store: "ScoreStore", page_idx: int, shot_type: int) -> str:
    """表头 "取得数/総数（キャラ切り替え↓↑）"(TH_RESULT_SPELLCARD_NAME,
    config/i18n.csv:190); 收取数 = 本难度页内 inGame 或 practice 组捕获
    非零的卡数(capturedSpellCards 统计, :2999-3023)。"""
    captured = 0
    cards = SPELLCARDS_PER_DIFFICULTY[page_idx]
    for card_no in cards:
        entry = store.catk[card_no]
        if entry["successes"][shot_type] != 0:
            captured += 1
            continue
        practice = entry.get("practice")
        if isinstance(practice, dict) and practice["successes"][shot_type] != 0:
            captured += 1
    return "%3d/%3d（キャラ切り替え↓↑）" % (captured, len(cards))


# ---- 其他统计(:1988-2151) ----
def stats_lines(store: "ScoreStore") -> tuple[str, ...]:
    """统计屏 20 行文本(末行 = 原作未写字的空 vm, :2090-2092, 不返回 ——
    返回 19 条 + 对齐用空行含在内)。score.json 只有一个时间计数与通关/
    续关总数(无按难度细分/练习计数), 细分列留空(偏离见模块 docstring)。"""

    def clock(frames: int) -> str:
        sec = frames // 60
        return "%02d:%02d:%02d" % (sec // 3600, sec // 60 % 60, sec % 60)

    def play_count(difficulty: int, character: int) -> int:
        p = store.pscr.get(f"{difficulty},{character}")
        return p["play_count"] if p is not None else 0

    lines = [
        "総起動時間   " + clock(store.plst["total_frames"]),
        "総プレイ時間 " + clock(store.plst["total_frames"]),
        _STATS_PLAYCOUNT_HEADER,
    ]
    for ch in range(NUM_CHARACTERS_SELECT):
        counts = [play_count(d, ch) for d in range(5)]
        lines.append(
            "%s %6d %6d %6d %6d %6d %6d" % (CHARACTER_ITEMS[ch], *counts, sum(counts))
        )
    totals = [sum(play_count(d, ch) for ch in range(12)) for d in range(5)]
    lines.append(
        "%s %6d %6d %6d %6d %6d %6d"
        % (CHARACTER_ITEMS[SHOT_ALL_ROW], *totals, sum(totals))
    )
    lines.append("")
    blank5 = ("",) * 5  # 按难度细分无存档字段, 留空; 总计列给真值
    lines.append(
        "クリア回数  　　 %6s %6s %6s %6s %6s %6d"
        % (blank5 + (store.plst["clear_count"],))
    )
    lines.append(
        "コンティニュー   %6s %6s %6s %6s %6s %6d"
        % (blank5 + (store.plst["retry_count"],))
    )
    lines.append("プラクティス　   %6d %6d %6d %6d %6d %6d" % ((0,) * 6))
    return tuple(lines)


__all__ = [
    "BOARD_ROWS",
    "CHARACTER_ITEMS",
    "HighscoreRow",
    "NUM_CHARACTERS_SELECT",
    "NUM_SHOT_TYPES",
    "SPELLCARD_NAME_HIDDEN",
    "SPELLCARD_PAGE_ROWS",
    "SpellcardRow",
    "highscore_rows",
    "spellcard_header",
    "spellcard_rows",
    "stats_lines",
]
