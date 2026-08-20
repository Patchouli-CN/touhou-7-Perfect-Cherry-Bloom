""" score.json 持久化 —— 对照 ResultScreen.cpp / 规格 §F.3(JSON 简化版)。

原版 score.dat 是 LZSS 压缩 + 分块(TH7K/HSCR/CATK/CLRD/PSCR/PLST/LSNM)
+ XOR 校验的二进制格式; 本期简化为单个 JSON 文件, 字段语义对齐:

- highscores: Hscr 子集 —— 每 (难度, 角色) 一个 Top10 榜
  {score, character, difficulty, stage, name, numRetries, date(ISO 字符串)}。
  空位不入库, 展示时按默认分 100000-k*10000 补齐 (GetHighScore 的 100000 底线)。
- catk: 141 张符卡统计 {name, attempts[7], successes[7], highscore[7]},
  下标 0..5 = shotType, 6 = 合计 (EclManager BeginSpellcard/EndSpellcard)。
- clrd: 每角色 {with_retries[6], without_retries[6]}, 值为到达的最大面数
  (GameManager.cpp 过关时 currentStage-1 取 max)。
- pscr: 每 (难度, 角色) {play_count, highscore} (PSCR 简化)。
- plst: {play_count, total_frames, clear_count, retry_count} (PLST 简化:
  原版按难度/机型细分, 这里只留总数)。
- lsnm: 上次输入的名字 (LSNM 块, ResultScreen.cpp:2597 默认 8 空格);
  None = 从未输入过(本期初始默认名 DEFAULT_NAME, 输入后带出上次名字)。

读写容错: 文件缺失/损坏/字段类型不对一律回退默认值, 不抛异常
(对照 OpenScore 的 RECREATE_SCORE 分支)。
"""

from __future__ import annotations

import msgspec
from datetime import datetime, timezone
from pathlib import Path

SCORE_JSON_VERSION = 1
SPELLCARD_COUNT = 141
NUM_CHARACTERS = 6
NUM_DIFFICULTIES = 6
TOP_SIZE = 10
CATK_CAP = 9999          # attempts/successes 上限 (EclManager.cpp < 9999 才 ++)
DEFAULT_NAME = "PLAYER"  # 从未输入过名字时的初始默认名(原版 LSNM 缺省为 8 空格)


def default_score(slot: int) -> int:
    """空榜第 slot 名的默认分: 100000 - slot*10000。"""
    return max(0, 100000 - slot * 10000)


def make_highscore_record(score: int, character: int, difficulty: int,
                          stage: int, *, name: str = DEFAULT_NAME,
                          num_retries: int = 0,
                          date: str | None = None) -> dict:
    """一条 Hscr 子集记录; date 缺省取当前 UTC ISO 字符串。"""
    if date is None:
        date = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return {
        "score": int(score),
        "character": int(character),
        "difficulty": int(difficulty),
        "stage": int(stage),
        "name": str(name)[:9],          # 原版 name[9]
        "numRetries": int(num_retries),  # Hscr 字段名沿用原版拼写
        "date": date,
    }


def _new_catk_entry() -> dict:
    return {"name": "", "attempts": [0] * 7,
            "successes": [0] * 7, "highscore": [0] * 7}


def _is_int_list(v, n: int) -> bool:
    return (isinstance(v, list) and len(v) == n
            and all(isinstance(x, int) and not isinstance(x, bool) for x in v))


class ScoreStore:
    """内存中的成绩总库 + JSON 读写。纯逻辑, 不依赖 pygame。"""

    def __init__(self) -> None:
        self.highscores: dict[str, list[dict]] = {}   # "difficulty,character" -> [rec]
        self.catk: list[dict] = [_new_catk_entry() for _ in range(SPELLCARD_COUNT)]
        self.clrd = [{"with_retries": [0] * NUM_DIFFICULTIES,
                      "without_retries": [0] * NUM_DIFFICULTIES}
                     for _ in range(NUM_CHARACTERS)]
        self.pscr: dict[str, dict] = {}               # "difficulty,character" -> {...}
        self.plst = {"play_count": 0, "total_frames": 0,
                     "clear_count": 0, "retry_count": 0}
        self.lsnm: str | None = None  # LSNM: 上次输入的名字(None=从未输入)

    # ---- 键 ----
    @staticmethod
    def _key(difficulty: int, character: int) -> str:
        return f"{int(difficulty)},{int(character)}"

    # ---- Top10 (ResultScreen.cpp LinkScore / WriteScore 每槽最多 10 条) ----
    def entries(self, difficulty: int, character: int) -> list[dict]:
        """该 (难度, 角色) 的榜上真实记录(分数降序, 不含默认空位)。"""
        return list(self.highscores.get(self._key(difficulty, character), []))

    def insert_score(self, rec: dict) -> int:
        """插入一条成绩, 返回名次(0-based); 未进 Top10 返回 -1 且不入库。

        对照 LinkScore: 新记录插到第一个 score <= 自己的节点之前
        (同分新记录排前); 之后按 WriteScore 截断到 10 条。
        """
        key = self._key(rec["difficulty"], rec["character"])
        entries = self.highscores.setdefault(key, [])
        idx = len(entries)
        for i, e in enumerate(entries):
            if e["score"] <= rec["score"]:
                idx = i
                break
        entries.insert(idx, rec)
        del entries[TOP_SIZE:]
        return idx if idx < TOP_SIZE else -1

    def high_score(self, difficulty: int, character: int) -> int:
        """GetHighScore: 榜首分, 空榜/低分底线 100000。"""
        entries = self.highscores.get(self._key(difficulty, character), [])
        top = entries[0]["score"] if entries else 0
        return top if top > 100000 else 100000

    # ---- LSNM(上次输入的名字, ResultScreen.cpp lsnmHeader) ----
    @property
    def last_name(self) -> str:
        """入榜输入的初始名: 上次输入的名字; 从未输入过用 DEFAULT_NAME。"""
        return self.lsnm if self.lsnm is not None else DEFAULT_NAME

    def set_last_name(self, name: str) -> None:
        """名字输入完成后登记 LSNM (HandleResultKeyboard :1321-1322)。"""
        self.lsnm = str(name)[:8]  # 原版 LSNM 槽 8 字符(name[9]=8+NUL)

    def set_entry_name(self, difficulty: int, character: int, rank: int,
                       name: str) -> None:
        """改榜上第 rank 条记录的名字(名字输入态原地改, 同原版改链上的
        curScore.name); rank 越界/无该榜时静默忽略。"""
        entries = self.highscores.get(self._key(difficulty, character), [])
        if 0 <= int(rank) < len(entries):
            entries[int(rank)]["name"] = str(name)[:9]

    def display_entries(self, difficulty: int, character: int) -> list[dict]:
        """展示用 10 行: 真实记录 + 默认空位 (100000-k*10000) 补齐。"""
        rows = self.entries(difficulty, character)
        out = list(rows)
        for k in range(len(rows), TOP_SIZE):
            out.append(make_highscore_record(
                default_score(k), character, difficulty, 1, name="--------"))
        return out[:TOP_SIZE]

    # ---- catk 符卡统计 (EclManager Begin/EndSpellcard) ----
    def record_spellcard_attempt(self, idx: int, name: str, shot: int) -> None:
        """BeginSpellcard: attempts[shot]/attempts[6] ++ (封顶 9999), 记名字。"""
        if not 0 <= idx < SPELLCARD_COUNT:
            return
        e = self.catk[idx]
        e["name"] = str(name)[:48]
        for s in (int(shot), 6):
            if 0 <= s <= 6 and e["attempts"][s] < CATK_CAP:
                e["attempts"][s] += 1

    def record_spellcard_success(self, idx: int, shot: int, score: int) -> None:
        """EndSpellcard 捕获成功: successes ++, highscore 取 max (捕获分+擦弹加成)。"""
        if not 0 <= idx < SPELLCARD_COUNT:
            return
        e = self.catk[idx]
        for s in (int(shot), 6):
            if 0 <= s <= 6:
                if e["successes"][s] < CATK_CAP:
                    e["successes"][s] += 1
                if e["highscore"][s] < score:
                    e["highscore"][s] = int(score)

    # ---- CLRD 通关统计 (GameManager.cpp 过关时) ----
    def record_clear(self, character: int, difficulty: int, stage_reached: int,
                     num_retries: int) -> None:
        """stage_reached = 通过的面数 (C: currentStage-1, 取 max 不累加)。

        ZUN quirk: C++ 里 difficultyClearedWithRetries 反而被
        numRetries==0 门控(字段名与语义疑似写反), 这里照抄原逻辑。
        """
        c = self.clrd[int(character) % NUM_CHARACTERS]
        d = int(difficulty) % NUM_DIFFICULTIES
        if num_retries == 0 and c["with_retries"][d] < stage_reached:
            c["with_retries"][d] = stage_reached
        if c["without_retries"][d] < stage_reached:
            c["without_retries"][d] = stage_reached

    # ---- PSCR / PLST (简化) ----
    def record_play(self, character: int, difficulty: int) -> None:
        """开局计数: pscr[难度,角色].play_count++ 与 plst.play_count++。"""
        key = self._key(difficulty, character)
        p = self.pscr.setdefault(key, {"play_count": 0, "highscore": 0})
        p["play_count"] += 1
        self.plst["play_count"] += 1

    def record_run_end(self, character: int, difficulty: int, *, score: int,
                       frames: int, cleared: bool, num_retries: int) -> None:
        """一局结束: pscr highscore 取 max; plst 累加帧数/通关/续关次数。"""
        key = self._key(difficulty, character)
        p = self.pscr.setdefault(key, {"play_count": 0, "highscore": 0})
        if p["highscore"] < score:
            p["highscore"] = int(score)
        self.plst["total_frames"] += int(frames)
        if cleared:
            self.plst["clear_count"] += 1
        self.plst["retry_count"] += int(num_retries)

    # ---- JSON 读写(容错) ----
    def to_dict(self) -> dict:
        return {
            "version": SCORE_JSON_VERSION,
            "highscores": self.highscores,
            "catk": self.catk,
            "clrd": self.clrd,
            "pscr": self.pscr,
            "plst": self.plst,
            "lsnm": self.lsnm,
        }

    def save(self, path: str | Path) -> None:
        path = Path(path)
        tmp = path.with_suffix(path.suffix + ".tmp")
        # msgspec.json.format(indent=1) 与原 json.dumps(indent=1) 输出逐字节一致
        tmp.write_bytes(msgspec.json.format(
            msgspec.json.encode(self.to_dict()), indent=1))
        tmp.replace(path)  # 原子替换, 避免半截文件

    @classmethod
    def from_dict(cls, data) -> "ScoreStore":
        """从 JSON 对象恢复; 任何字段不对就回退该字段默认值。"""
        store = cls()
        if not isinstance(data, dict):
            return store
        hs = data.get("highscores")
        if isinstance(hs, dict):
            for key, rows in hs.items():
                if not isinstance(key, str) or not isinstance(rows, list):
                    continue
                good = [r for r in rows if _is_highscore_record(r)]
                good.sort(key=lambda r: r["score"], reverse=True)
                store.highscores[key] = good[:TOP_SIZE]
        catk = data.get("catk")
        if isinstance(catk, list):
            for i, e in enumerate(catk[:SPELLCARD_COUNT]):
                if _is_catk_entry(e):
                    store.catk[i] = e
        clrd = data.get("clrd")
        if isinstance(clrd, list):
            for i, c in enumerate(clrd[:NUM_CHARACTERS]):
                if (isinstance(c, dict)
                        and _is_int_list(c.get("with_retries"), NUM_DIFFICULTIES)
                        and _is_int_list(c.get("without_retries"), NUM_DIFFICULTIES)):
                    store.clrd[i] = c
        pscr = data.get("pscr")
        if isinstance(pscr, dict):
            for key, p in pscr.items():
                if (isinstance(key, str) and isinstance(p, dict)
                        and isinstance(p.get("play_count"), int)
                        and isinstance(p.get("highscore"), int)):
                    store.pscr[key] = {"play_count": p["play_count"],
                                       "highscore": p["highscore"]}
        plst = data.get("plst")
        if isinstance(plst, dict):
            for k in ("play_count", "total_frames", "clear_count", "retry_count"):
                if isinstance(plst.get(k), int):
                    store.plst[k] = plst[k]
        lsnm = data.get("lsnm")
        if isinstance(lsnm, str):
            store.lsnm = lsnm[:8]
        return store

    @classmethod
    def load(cls, path: str | Path) -> "ScoreStore":
        """读文件; 缺失/损坏/JSON 不合法 → 全新默认值 (不抛异常)。

        msgspec.DecodeError 是 ValueError 子类, 坏 UTF-8/坏 JSON 同样落网。
        """
        try:
            data = msgspec.json.decode(Path(path).read_bytes())
        except (OSError, ValueError):
            return cls()
        return cls.from_dict(data)


def _is_highscore_record(r) -> bool:
    return (isinstance(r, dict)
            and isinstance(r.get("score"), int) and not isinstance(r.get("score"), bool)
            and isinstance(r.get("character"), int)
            and isinstance(r.get("difficulty"), int)
            and isinstance(r.get("stage"), int)
            and isinstance(r.get("name"), str)
            and isinstance(r.get("numRetries"), int)
            and isinstance(r.get("date"), str))


def _is_catk_entry(e) -> bool:
    return (isinstance(e, dict)
            and isinstance(e.get("name"), str)
            and _is_int_list(e.get("attempts"), 7)
            and _is_int_list(e.get("successes"), 7)
            and _is_int_list(e.get("highscore"), 7))
