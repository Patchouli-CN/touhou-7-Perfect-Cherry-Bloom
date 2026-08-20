""" Player Data(Result 画面)显示模型装配 —— 纯逻辑, 不依赖 pygame。

对照 ResultScreen.cpp(原版 Player Data 由 Supervisor curState=5 进
ResultScreen, MainMenu.cpp:430-433 case 4 切入):

- 分数榜: 原版 6 难度页 × 6 机体 Hscr 链表(每页 Top10);
  这里取 score_store.display_entries(难度,机体)(真实记录 + 默认空位补齐)。
- 符卡: 原版符卡列表(ResultScreen.cpp:1051-1072)按 shotType 分页(0..5 +
  合计页 6), 每页 141 张遇过的卡名 + 捕获数/挑战数, 未遇到显示 "丠丠丠丠丠";
  这里装配某机体(或合计)的汇总 + 遇过的卡列表。
- 统计: 原版 DrawStats(ResultScreen.cpp:1121-1128 分支, 总游玩次数/时间等)
  + clrd 通关到达面数; 这里取 plst 总数 + clrd 6x6 表。

空记录一律回退默认值(score_store 本身容错), 装配函数不抛异常。
"""

from __future__ import annotations

from .score_store import SPELLCARD_COUNT, ScoreStore

# 未遇到符卡的名字占位(原版 GBK 字 "丠丠丠丠丠", 显示为问号列)
UNKNOWN_CARD_NAME = "?????"


def highscore_rows(store: ScoreStore, difficulty: int,
                   character: int) -> list[dict]:
    """该(难度,机体) Top10 展示行(含默认空位, score_store.display_entries)。"""
    return store.display_entries(difficulty, character)


def spellcard_page(store: ScoreStore, shot: int) -> dict:
    """某机体(0..5)或合计(6)的符卡页模型。

    返回 {attempted, captured, attempts, successes, cards}:
    attempted/captured = 遇到/捕获的符卡张数; attempts/successes = 挑战/捕获
    总次数; cards = 遇到的卡 [{"idx","name","attempts","successes"}], 编号序。
    """
    shot = int(shot)
    if not 0 <= shot <= 6:
        shot = 6
    cards = []
    attempted = captured = attempts = successes = 0
    for i, e in enumerate(store.catk[:SPELLCARD_COUNT]):
        a, s = e["attempts"][shot], e["successes"][shot]
        if a <= 0:
            continue
        attempted += 1
        attempts += a
        successes += s
        if s > 0:
            captured += 1
        cards.append({"idx": i, "name": e["name"] or UNKNOWN_CARD_NAME,
                      "attempts": a, "successes": s})
    return {"attempted": attempted, "captured": captured,
            "attempts": attempts, "successes": successes, "cards": cards}


def play_stats(store: ScoreStore) -> dict:
    """统计页模型: plst 总次数/时间/通关/续关 + clrd 每机体各难度到达面数。

    play_seconds = total_frames / 60(固定 60fps); clrd 照抄 score_store
    的 with/without_retries 两组(原版 DrawStats 亦分列)。
    """
    return {
        "play_count": store.plst["play_count"],
        "clear_count": store.plst["clear_count"],
        "retry_count": store.plst["retry_count"],
        "play_seconds": store.plst["total_frames"] / 60.0,
        "clrd": [{"with_retries": list(c["with_retries"]),
                  "without_retries": list(c["without_retries"])}
                 for c in store.clrd],
    }
