"""th08 score.json 持久化语义层 —— CLRD 位掩码/解锁判定/通关写回/曲目解锁/旧档迁移。

对照 th08-ref(@1861f88, 行号相对其 src/):

- Clrd 结构 (ScoreDat.hpp:213-223): u16 difficultiesClearedWithoutRetries[5]
  (@0xC) 在前、u16 difficultiesClearedWithRetries[5] (@0x16) 在后, 每难度一个
  u16 位掩码; bit i = ZUN_BIT(currentStage) 面通关位 (IS_STAGE_CLEARED,
  GameManager.hpp:17), bit14/bit15 = 解锁 flag (GameManager.hpp:14-15)。
  score.json 侧键名与 th08 语义一致: "without_retries" = 无续关表(被
  numRetries==0 门控), "with_retries" = 含续关表(无条件写) —— th07 的
  字段名/语义写反 quirk 在 th08 不存在(GameManager.cpp:299-307 为证)。
- clrdData[SHOT_ALL+1] = 13 行 (GameManager.hpp:337): 0..11 = 12 机体
  (0..3 双人组, 4..11 单人), 12 = SHOT_ALL 合计行, 所有写入同步镜像
  (GameManager.cpp:303/:307, Ending.cpp:533/:547)。
- catk: 222 张符卡 (SPELLCARD_COUNT_SPELLCARDS, Spellcard.hpp:255 枚举
  计数 = 游戏内 205 张 + Last Word 17 张 205..221); 每卡 inGame/
  spellPractice 两组 CatkHistory {maxBonus, attempts, captures}[13]
  (ScoreDat.hpp:164-211) —— 13 槽的轴是 shotType(12 机体 + SHOT_ALL),
  不是难度。
- 曲目解锁: plst.bgmUnlocked[32] (ScoreDat.hpp:150), 播曲即置位
  (Supervisor.PlayMusic/PlayAudio, Supervisor.cpp:1579/:1592/:1617/:1632);
  结局另置 18/19 (Ending.cpp:549-556)。
- Last Word 解锁: plst.lastWordUnlocked[17] (FLSP 章对应物,
  ScoreDat.hpp:155-160; 17 条条件判定 = UnlockLastWordSpellCards,
  TitleUnlockLastWords.inl:13-172)。

调查笔记修正(th08-title-systems.md, 2026-09-03 核实): §7 原记解锁判定读
[EXTRA..LUNATIC+EXTRA](下标 4..7) 有误, 实为 [EASY..LUNATIC] = 0..3,
无越界读; §15.2 的 Clrd 字段序颠倒(实为 WithoutRetries 在前)。
"""

from __future__ import annotations

from pathlib import Path
from typing import TypeGuard

import msgspec

from ...engine.score_store import ScoreStore
from .spellcards import LAST_SPELL_CARDS, SPELLCARDS_PER_DIFFICULTY

# ---- 常量(出处见各注) ----
EXTRA_UNLOCKED_FLAG = 1 << 14  # ZUN_BIT(14), GameManager.hpp:14
SPELL_PRACTICE_UNLOCKED_FLAG = 1 << 15  # ZUN_BIT(15), GameManager.hpp:15
NUM_CHARACTERS = 12  # 机体数(0..3 双人组 + 4..11 单人, ScoreDat.hpp:54-69)
NUM_TEAMS = 4  # 双人组数; 单人机体解锁判定恒 True(GameManager.cpp:1329)
NUM_DIFFICULTIES = 5  # EASY..EXTRA (ScoreDat.hpp:44-52)
EXTRA_DIFFICULTY = 4  # EXTRA (ScoreDat.hpp:50)
MAIN_DIFFICULTIES = 4  # 解锁判定只扫 EASY..LUNATIC = 槽 0..3
SHOT_ALL_ROW = 12  # SHOT_ALL 合计行下标 (GameManager.hpp:337)
CLRD_ROWS = 13  # SHOT_ALL + 1
SPELLCARD_COUNT = 222  # SPELLCARD_COUNT_SPELLCARDS (Spellcard.hpp:255)
CATK_SLOT_COUNT = 13  # CatkHistory 槽数 = SHOT_ALL+1 (ScoreDat.hpp:166-168)

# C currentStage 下标 (Stage 枚举, ScoreDat.hpp:71-85)
STAGE_6A = 6
STAGE_6B = 7
STAGE_EXTRA = 8

# 面 → 曲解锁下标表 g_GuiStageMusicContexts (Gui.cpp:39-50):
# 行 = C currentStage 0..8, 列 = {面曲, boss 曲, Last Spell 曲(仅 6A/6B 用)};
# 置位点 = 播曲 (GameManager.cpp:408-410 进关面曲 / Gui.cpp:624-633 msg 切曲)
STAGE_BGM_UNLOCK_INDICES = (
    (1, 2, 0),
    (3, 4, 0),
    (5, 6, 0),
    (7, 8, 0),
    (7, 9, 0),
    (10, 11, 0),
    (12, 13, 15),
    (12, 14, 15),
    (16, 17, 0),
)
TITLE_BGM_INDEX = 0  # 标题曲解锁下标 (TitleScreen.cpp:293 PlayMusic(8, 0))
ENDING_BGM_INDICES = (18, 19)  # good ending 置位 (Ending.cpp:549-550)
BAD_ENDING_BGM_VALUE = 0x12  # bad ending 写 [18]=0x12 (Ending.cpp:556, 照抄;
# 0x12 真值即解锁, MusicRoom 按真假值判断, MusicRoom.cpp:534)


# ---- 内部小工具 ----
def _is_int_list(v, n: int) -> TypeGuard[list[int]]:
    return (
        isinstance(v, list)
        and len(v) == n
        and all(isinstance(x, int) and not isinstance(x, bool) for x in v)
    )


def _clrd_row(store: ScoreStore, character: int) -> dict | None:
    if 0 <= int(character) < len(store.clrd):
        return store.clrd[int(character)]
    return None


def _write_rows(store: ScoreStore, character: int) -> list[dict]:
    """本机体行 + SHOT_ALL 合计行(存在时); 越界/行数不足 → 空列表。"""
    if not 0 <= int(character) < NUM_CHARACTERS:
        return []
    row = _clrd_row(store, character)
    if row is None:
        return []
    rows = [row]
    agg = _clrd_row(store, SHOT_ALL_ROW)
    if agg is not None:
        rows.append(agg)
    return rows


def _or_bit(row: dict, key: str, difficulty: int, bit: int) -> None:
    v = row.get(key)
    if isinstance(v, list) and 0 <= difficulty < len(v):
        v[difficulty] |= bit


# ---- 解锁判定 (GameManager.cpp:1327-1362 / Supervisor.cpp:1526-1532) ----
def is_extra_unlocked_for_character(store: ScoreStore, character: int) -> bool:
    """IsExtraUnlockedForCharacter (GameManager.cpp:1327-1334):
    单人机体(>3)恒 True; 4 组队伍看 without_retries[EASY..LUNATIC]
    任一带 EXTRA_UNLOCKED_FLAG。"""
    if int(character) >= NUM_TEAMS:
        return True
    row = _clrd_row(store, character)
    if row is None:
        return False
    return any(
        m & EXTRA_UNLOCKED_FLAG for m in row["without_retries"][:MAIN_DIFFICULTIES]
    )


def is_spell_practice_unlocked_for_character(store: ScoreStore, character: int) -> bool:
    """IsSpellPracticeUnlockedForCharacter (GameManager.cpp:1346-1353):
    单人机体恒 True; 4 组看 with_retries[EASY..LUNATIC] 任一带
    SPELL_PRACTICE_UNLOCKED_FLAG(含续关通关也算)。"""
    if int(character) >= NUM_TEAMS:
        return True
    row = _clrd_row(store, character)
    if row is None:
        return False
    return any(
        m & SPELL_PRACTICE_UNLOCKED_FLAG
        for m in row["with_retries"][:MAIN_DIFFICULTIES]
    )


def is_extra_unlocked(store: ScoreStore) -> bool:
    """IsExtraUnlocked (GameManager.cpp:1337-1343): 4 组队伍任一解锁
    (菜单 Extra Start 置灰依据, TitleScreen.cpp:3651/:3673)。"""
    return any(is_extra_unlocked_for_character(store, c) for c in range(NUM_TEAMS))


def is_spell_practice_unlocked(store: ScoreStore) -> bool:
    """IsSpellPracticeUnlocked (GameManager.cpp:1356-1362): 4 组任一
    (菜单 Spell Practice 置灰依据, TitleScreen.cpp:3655/:3674)。"""
    return any(
        is_spell_practice_unlocked_for_character(store, c) for c in range(NUM_TEAMS)
    )


def is_extra_unlocked_with_all_teams(store: ScoreStore) -> bool:
    """IsExtraUnlockedWithAllTeams (Supervisor.cpp:1526-1532): 4 组全真
    (机体选择 12 项解锁依据, TitleScreen.cpp:1604)。"""
    return all(is_extra_unlocked_for_character(store, c) for c in range(NUM_TEAMS))


# ---- 面通关读取(B2 选关画面的接口; GameManager.hpp:236-247 内联同构) ----
def stage_cleared_without_retries(
    store: ScoreStore, character: int, difficulty: int, c_stage: int
) -> bool:
    """StageClearedWithoutRetries: without_retries[难度] 的面位。"""
    row = _clrd_row(store, character)
    if row is None or not 0 <= difficulty < len(row["without_retries"]):
        return False
    return bool(row["without_retries"][difficulty] & (1 << int(c_stage)))


def stage_cleared_with_retries(
    store: ScoreStore, character: int, difficulty: int, c_stage: int
) -> bool:
    """StageClearedWithRetries: with_retries[难度] 的面位
    (PracticeStageSelect 的 clearInfo, TitleScreen.cpp:1899-1900)。"""
    row = _clrd_row(store, character)
    if row is None or not 0 <= difficulty < len(row["with_retries"]):
        return False
    return bool(row["with_retries"][difficulty] & (1 << int(c_stage)))


def practice_clear_info(store: ScoreStore, character: int, difficulty: int) -> int:
    """Practice 面选的 clearInfo 位掩码(TitleScreen.cpp:1899-1912):
    with_retries[难度] 原值; 为 0 保底置 1(可练 1 面); 6B 已通则 4A/4B 全开。"""
    row = _clrd_row(store, character)
    info = 0
    if row is not None and 0 <= difficulty < len(row["with_retries"]):
        info = row["with_retries"][difficulty]
    if info == 0:
        info = 1
    if info & (1 << STAGE_6B):
        info |= (1 << 3) | (1 << 4)  # STAGE4A/STAGE4B 位(:1906-1909)
    return info


# ---- 通关写回 ----
def record_stage_clear(
    store: ScoreStore, character: int, difficulty: int, c_stage: int, num_retries: int
) -> None:
    """过关置面位 (GameManager.cpp:297-308, stageTransitionState==2 时无论去向
    先写; currentStageClearFlag = 1 << currentStage, GameManagerSetup.cpp:61):
    无续关 → without_retries 置位, 含续关表无条件置位; SHOT_ALL 行同步。"""
    if not (0 <= difficulty < NUM_DIFFICULTIES and 0 <= c_stage <= STAGE_EXTRA):
        return
    bit = 1 << c_stage
    for row in _write_rows(store, character):
        if num_retries == 0:
            _or_bit(row, "without_retries", difficulty, bit)
        _or_bit(row, "with_retries", difficulty, bit)


def record_ending_clear(
    store: ScoreStore,
    character: int,
    difficulty: int,
    *,
    cleared_6b: bool,
    num_retries: int,
) -> None:
    """6A/6B 通关的 Ending 写回 (Ending.cpp:504-551 AddedCallback):
    stageBit = 6B ? EXTRA_UNLOCKED_FLAG : SPELL_PRACTICE_UNLOCKED_FLAG (:509);
    无续关 → without_retries 两行置位(:532-533), 含续关表无条件两行
    置位(:546-547); good ending 解锁结局曲 18/19 (:549-550)。
    (面位本身已在 record_stage_clear 入账; clears++ 由 record_run_end 管)"""
    bit = EXTRA_UNLOCKED_FLAG if cleared_6b else SPELL_PRACTICE_UNLOCKED_FLAG
    if 0 <= difficulty < NUM_DIFFICULTIES:
        for row in _write_rows(store, character):
            if num_retries == 0:
                _or_bit(row, "without_retries", difficulty, bit)
            _or_bit(row, "with_retries", difficulty, bit)
    for idx in ENDING_BGM_INDICES:
        unlock_bgm(store, idx)


def record_bad_ending(store: ScoreStore) -> None:
    """Bad Ending 写回 (Ending.cpp:556): bgmUnlocked[18] = 0x12。
    (pendingEndingSkip 在 th08-ref 全源码只读不写, 僵尸字段不建模)"""
    bgm = store.plst.get("bgmUnlocked")
    if isinstance(bgm, list) and len(bgm) > 18:
        bgm[18] = BAD_ENDING_BGM_VALUE


def record_extra_clear(store: ScoreStore, character: int, difficulty: int) -> None:
    """EX 通关的追加写 (GameManager.cpp:357-363): difficulty==EXTRA 时
    without_retries[本机][EX] |= 0x8000 且 with_retries[SHOT_ALL][EX] |= 0x8000
    —— 两表不对称是原作原样(疑似 ZUN 笔误, 照抄)。
    (EX 面位 bit8 已在 record_stage_clear 入账; 0x8000 = bit15)"""
    if difficulty != EXTRA_DIFFICULTY:
        return
    row = _clrd_row(store, character)
    if row is not None:
        _or_bit(row, "without_retries", EXTRA_DIFFICULTY, 0x8000)
    agg = _clrd_row(store, SHOT_ALL_ROW)
    if agg is not None:
        _or_bit(agg, "with_retries", EXTRA_DIFFICULTY, 0x8000)


# ---- catk 判定(Last Word 解锁/符卡练习显示用; ScoreDat.hpp:188-205 内联同构) ----
def _catk_groups(store: ScoreStore, card_no: int) -> tuple[dict | None, dict | None]:
    """(inGame 组, spellPractice 组); 卡号越界 → (None, None)。"""
    if not 0 <= int(card_no) < len(store.catk):
        return None, None
    entry = store.catk[int(card_no)]
    practice = entry.get("practice")
    return entry, practice if isinstance(practice, dict) else None


def _slot(group: dict | None, key: str, shot: int) -> int:
    if group is None:
        return 0
    v = group.get(key)
    if isinstance(v, list) and 0 <= int(shot) < len(v):
        x = v[int(shot)]
        if isinstance(x, int) and not isinstance(x, bool):
            return x
    return 0


def card_attempted_any(store: ScoreStore, card_no: int, shot: int) -> bool:
    """Catk.AttemptedAny (ScoreDat.hpp:199-204): 任一组 attempts[shot]>0。"""
    ingame, practice = _catk_groups(store, card_no)
    return _slot(ingame, "attempts", shot) > 0 or _slot(practice, "attempts", shot) != 0


def card_captured_any(store: ScoreStore, card_no: int, shot: int) -> bool:
    """Catk.CapturedAny (ScoreDat.hpp:192-197): 任一组 captures[shot]>0。"""
    ingame, practice = _catk_groups(store, card_no)
    return (
        _slot(ingame, "successes", shot) > 0 or _slot(practice, "successes", shot) != 0
    )


def spell_practice_captured(store: ScoreStore, card_no: int, shot: int) -> bool:
    """Catk.SpellPracticeCaptured (ScoreDat.hpp:188-191): practice 组捕获。"""
    _, practice = _catk_groups(store, card_no)
    return _slot(practice, "successes", shot) > 0


# ---- Last Word 解锁(FLSP; C 期第 5 片) ----
LAST_WORD_COUNT = 17  # SPELLCARD_COUNT_LAST_WORD_SPELLCARDS (ScoreDat.hpp:256)
LAST_WORD_START = 205  # SPELLCARD_LAST_WORD_START (Spellcard.hpp:235)
SHOT_REIMU = 4  # 单人灵梦 shotType (ScoreDat.hpp:58)
SHOT_MARISA = 6  # 单人魔理沙 shotType (ScoreDat.hpp:60)
# score.json 落点: plst["lastWordUnlocked"] = 17 槽(值 = 卡号或 0)。
# 引擎 from_dict 只回读已知 plst 键, load_score_store 从原始 JSON 补注;
# to_dict 整体倒出 plst, 保存自然回写(对照 FLSP 章, ScoreDat.hpp:155-160)
_FLSP_KEY = "lastWordUnlocked"


def _flsp(store: ScoreStore) -> list[int]:
    v = store.plst.get(_FLSP_KEY)
    if not _is_int_list(v, LAST_WORD_COUNT):
        v = [0] * LAST_WORD_COUNT
        store.plst[_FLSP_KEY] = v
    return v


def last_word_unlocked(store: ScoreStore, card_no: int) -> bool:
    """flsp.unlockedLastWordSpellCards[i] == 卡号 (TitleUnlockLastWords.inl:7-10)。"""
    if not LAST_WORD_START <= int(card_no) < LAST_WORD_START + LAST_WORD_COUNT:
        return False
    v = store.plst.get(_FLSP_KEY)
    return _is_int_list(v, LAST_WORD_COUNT) and v[
        int(card_no) - LAST_WORD_START
    ] == int(card_no)


def is_last_word_spellcard_attempted(store: ScoreStore, card_no: int) -> bool:
    """IsLastWordSpellCardAttempted (TitleScreen.cpp:2996-3002): 卡 <205 看
    两组 attempts[SHOT_ALL], 否则看 flsp 解锁字节。"""
    if int(card_no) < LAST_WORD_START:
        return card_attempted_any(store, card_no, SHOT_ALL_ROW)
    return last_word_unlocked(store, card_no)


def _extra_clear_count(store: ScoreStore) -> int:
    """extraClearCount: 机体 0..11 中 without_retries[E..L] 任一带
    EXTRA_UNLOCKED_FLAG 的机体数(TitleUnlockLastWords.inl:38-45)。"""
    n = 0
    for ch in range(NUM_CHARACTERS):
        row = _clrd_row(store, ch)
        if row is not None and any(
            m & EXTRA_UNLOCKED_FLAG for m in row["without_retries"][:MAIN_DIFFICULTIES]
        ):
            n += 1
    return n


def _total_practice_captures(store: ScoreStore, cards=None) -> int:
    """spellPractice 组 captures[SHOT_ALL]>0 的卡数(:31-36/:65-70);
    cards=None 全 222 张, 否则指定卡号集。"""
    n = 0
    for c in cards if cards is not None else range(len(store.catk)):
        if spell_practice_captured(store, c, SHOT_ALL_ROW):
            n += 1
    return n


def unlock_last_words(store: ScoreStore) -> list[int]:
    """UnlockLastWordSpellCards (TitleUnlockLastWords.inl:13-172): 评估 17 条
    解锁条件, 新达成的写 flsp(值 = 卡号); 返回本次新解锁的卡号列表(调用方
    提示用; 条件细节见 th08-title-systems.md §8.3)。"""
    out: list[int] = []

    def grant(card: int, ok: bool) -> None:
        if ok and not last_word_unlocked(store, card):
            _flsp(store)[card - LAST_WORD_START] = card
            out.append(card)

    extra_clear = _extra_clear_count(store)
    grant(205, extra_clear >= 2)
    grant(206, extra_clear >= 3)
    total_captures = _total_practice_captures(store)
    grant(207, total_captures >= 50)
    grant(208, extra_clear >= 4)
    grant(209, card_captured_any(store, 137, SHOT_ALL_ROW))
    ls_captures = _total_practice_captures(store, LAST_SPELL_CARDS)
    grant(210, ls_captures >= 15)
    grant(
        211,
        card_captured_any(store, 145, SHOT_ALL_ROW)
        and card_captured_any(store, 195, SHOT_ALL_ROW)
        and card_attempted_any(store, 204, SHOT_ALL_ROW),
    )
    grant(
        212,
        all(card_attempted_any(store, c, SHOT_ALL_ROW) for c in (208, 209, 210)),
    )
    grant(
        213,
        all(card_attempted_any(store, c, SHOT_ALL_ROW) for c in (205, 206, 207, 211)),
    )
    normal_cards = SPELLCARDS_PER_DIFFICULTY[1]
    grant(
        214,
        all(card_captured_any(store, c, SHOT_MARISA) for c in normal_cards),
    )
    reimu = _clrd_row(store, SHOT_REIMU)
    grant(
        215,
        reimu is not None
        and any(reimu["without_retries"][d] & EXTRA_UNLOCKED_FLAG for d in (2, 3)),
    )
    grant(216, total_captures >= 120)
    grant(217, extra_clear >= 6)
    grant(
        218,
        sum(
            1
            for ch in range(NUM_CHARACTERS)
            if (row := _clrd_row(store, ch)) is not None
            and row["without_retries"][EXTRA_DIFFICULTY] & (1 << STAGE_EXTRA)
        )
        >= 3,
    )
    grant(219, ls_captures >= 30)
    agg = _clrd_row(store, SHOT_ALL_ROW)
    grant(
        220,
        agg is not None and (agg["with_retries"][3] & 0xC000) != 0,  # LUNATIC 槽
    )
    grant(
        221,
        all(
            card_attempted_any(store, c, SHOT_ALL_ROW)
            for c in range(LAST_WORD_START, LAST_WORD_START + LAST_WORD_COUNT - 1)
        ),
    )
    return out


# ---- 曲目解锁 ----
def unlock_bgm(store: ScoreStore, index: int) -> None:
    """播曲即解锁 (Supervisor.PlayMusic/PlayAudio 的 bgmUnlockIndex 置位,
    Supervisor.cpp:1579/:1592/:1617/:1632; replay/demo 不置 —— 本期无此模式)。"""
    bgm = store.plst.get("bgmUnlocked")
    if isinstance(bgm, list) and 0 <= int(index) < len(bgm):
        bgm[int(index)] = 1


def unlock_stage_bgm(store: ScoreStore, c_stage: int, music_idx: int) -> None:
    """关卡曲解锁: 下标 = STAGE_BGM_UNLOCK_INDICES[c_stage][music_idx]
    (Gui.cpp:39-50; music_idx 0=面曲 1=boss 曲 2=Last Spell 曲)。"""
    if 0 <= c_stage < len(STAGE_BGM_UNLOCK_INDICES):
        row = STAGE_BGM_UNLOCK_INDICES[c_stage]
        if 0 <= music_idx < len(row):
            unlock_bgm(store, row[music_idx])


# ---- 旧档迁移(A 期 max-stage 整数 → 位掩码) ----
# 旧档 clrd 值 = 通关结算时的 stage_no(1..9; 7=6A 8=6B 9=EX, 只在通关写),
# 且键名语义与 th08 相反(引擎 record_clear 照抄 th07 quirk: "with_retries"
# 被 numRetries==0 门控 = th08 的 WithoutRetries 语义; "without_retries"
# 无条件写 = th08 的 WithRetries 语义) —— 迁移时两键互换并转位掩码。
# 旧档难度槽 6 个(引擎旧默认), 新格式 5 个: 行内列表长度即格式判别
# (5 = 新格式不动, 6 = 旧档迁移, 其他长度交给引擎校验回退默认)。
_LEGACY_DIFFICULTY_SLOTS = 6

# 旧 stage_no → 已通面位掩码(保守启发式, 歧义处宁可少解锁):
# 4A/4B 分支(bit3/4)旧档无法区分 → 一律不置; 6B 线不经 6A; EX 只有 EX 面
_LEGACY_STAGE_BITS = {
    1: 0x01,  # 1 面
    2: 0x03,
    3: 0x07,
    4: 0x07,  # 止于 4A/4B → 分支位不置
    5: 0x07,
    6: 0x27,  # + 5 面(bit5)
    7: 0x67,  # + 6A(bit6)
    8: 0xA7,  # + 6B(bit7)
    9: 0x100,  # EX 通关(bit8)
}


def _legacy_ending_flag(v: int, difficulty: int) -> int:
    """旧 stage_no → 解锁 flag: 7(6A)=SPELL bit15, 8(6B)=EXTRA bit14
    (Ending.cpp:509)。只认正篇难度槽 0..3: EX 槽(4)的旧值与 th07 存档
    (同文件共用, EX 通关也记 7)歧义, 保守不置。"""
    if difficulty >= EXTRA_DIFFICULTY:
        return 0
    if v == 7:
        return SPELL_PRACTICE_UNLOCKED_FLAG
    if v == 8:
        return EXTRA_UNLOCKED_FLAG
    return 0


def _legacy_to_mask(v: int, difficulty: int) -> int:
    if v <= 0:
        return 0
    if v > 9:
        return v  # 已像位掩码(或 th07 的 99 类值), 原样保留不猜测
    return _LEGACY_STAGE_BITS.get(v, 0) | _legacy_ending_flag(v, difficulty)


def migrate_legacy_clrd(data: dict) -> bool:
    """把 data["clrd"] 的旧版 max-stage 整数原地迁成位掩码 dict。
    返回是否有行被迁移(供调用方决定是否重建 SHOT_ALL 行)。"""
    clrd = data.get("clrd")
    if not isinstance(clrd, list):
        return False
    migrated = False
    rows = []
    for entry in clrd:
        # 旧档键名语义反(见模块注释): 旧 with = 新 without, 旧 without = 新 with
        old_no_retry_gated = (
            entry.get("with_retries") if isinstance(entry, dict) else None
        )
        old_always = entry.get("without_retries") if isinstance(entry, dict) else None
        if not (
            _is_int_list(old_no_retry_gated, _LEGACY_DIFFICULTY_SLOTS)
            and _is_int_list(old_always, _LEGACY_DIFFICULTY_SLOTS)
        ):
            rows.append(entry)  # 新格式/坏行不动, 交给引擎校验
            continue
        rows.append(
            {
                # 无续关门控一侧 ← 旧 "with_retries"(th08 WithoutRetries)
                "without_retries": [
                    _legacy_to_mask(old_no_retry_gated[d], d)
                    for d in range(NUM_DIFFICULTIES)
                ],
                # 无条件写一侧 ← 旧 "without_retries"(th08 WithRetries)
                "with_retries": [
                    _legacy_to_mask(old_always[d], d) for d in range(NUM_DIFFICULTIES)
                ],
            }
        )
        migrated = True
    if migrated:
        data["clrd"] = rows
    return migrated


def _rebuild_shot_all_row(store: ScoreStore) -> None:
    """SHOT_ALL 合计行 = 0..11 各机体两表按位 OR(原作写入时同步镜像,
    迁移后按此重建; GameManager.cpp:303/:307)。"""
    agg = _clrd_row(store, SHOT_ALL_ROW)
    if agg is None:
        return
    for key in ("with_retries", "without_retries"):
        for d in range(len(agg[key])):
            mask = 0
            for ch in range(NUM_CHARACTERS):
                row = _clrd_row(store, ch)
                if row is not None and d < len(row[key]):
                    mask |= row[key][d]
            agg[key][d] = mask


def load_score_store(path: str | Path) -> ScoreStore:
    """读 score.json → th08 口径 ScoreStore: 222 卡 × 13 槽(shotType 轴 +
    SHOT_ALL 合计)双组 catk / clrd 13 行位掩码 / plst.bgmUnlocked。

    旧档(clrd 6 难度槽的 max-stage 整数)先经 migrate_legacy_clrd 转位掩码
    再校验, 迁移发生时 SHOT_ALL 行按各行 OR 重建; 文件缺失/损坏 → 全新默认
    (读盘与容错骨架同 ScoreStore.load, 这里自带是因为迁移必须先于校验 —
    6 槽旧行过不了 5 槽校验, 会被容错丢弃)。
    """
    try:
        data = msgspec.json.decode(Path(path).read_bytes())
    except (OSError, ValueError):
        data = None
    migrated = isinstance(data, dict) and migrate_legacy_clrd(data)
    store = ScoreStore.from_dict(
        data,
        spellcard_count=SPELLCARD_COUNT,
        num_characters=CLRD_ROWS,
        num_difficulties=NUM_DIFFICULTIES,
        catk_slot_count=CATK_SLOT_COUNT,
        catk_practice_group=True,
    )
    if migrated:
        _rebuild_shot_all_row(store)
    # FLSP 补注: 引擎 from_dict 只回读已知 plst 键, Last Word 解锁表从原始
    # JSON 捡回(保存时 to_dict 整体倒出 plst, 自然回写)
    if isinstance(data, dict):
        plst = data.get("plst")
        if isinstance(plst, dict) and _is_int_list(
            plst.get(_FLSP_KEY), LAST_WORD_COUNT
        ):
            store.plst[_FLSP_KEY] = list(plst[_FLSP_KEY])
    return store


__all__ = [
    "BAD_ENDING_BGM_VALUE",
    "CATK_SLOT_COUNT",
    "CLRD_ROWS",
    "ENDING_BGM_INDICES",
    "EXTRA_DIFFICULTY",
    "EXTRA_UNLOCKED_FLAG",
    "LAST_WORD_COUNT",
    "LAST_WORD_START",
    "NUM_CHARACTERS",
    "NUM_DIFFICULTIES",
    "NUM_TEAMS",
    "SHOT_ALL_ROW",
    "SHOT_MARISA",
    "SHOT_REIMU",
    "SPELLCARD_COUNT",
    "SPELL_PRACTICE_UNLOCKED_FLAG",
    "STAGE_6A",
    "STAGE_6B",
    "STAGE_BGM_UNLOCK_INDICES",
    "STAGE_EXTRA",
    "TITLE_BGM_INDEX",
    "card_attempted_any",
    "card_captured_any",
    "is_extra_unlocked",
    "is_extra_unlocked_for_character",
    "is_extra_unlocked_with_all_teams",
    "is_last_word_spellcard_attempted",
    "is_spell_practice_unlocked",
    "is_spell_practice_unlocked_for_character",
    "last_word_unlocked",
    "load_score_store",
    "migrate_legacy_clrd",
    "practice_clear_info",
    "record_bad_ending",
    "record_ending_clear",
    "record_extra_clear",
    "record_stage_clear",
    "spell_practice_captured",
    "stage_cleared_with_retries",
    "stage_cleared_without_retries",
    "unlock_bgm",
    "unlock_last_words",
    "unlock_stage_bgm",
]
