"""th08 progress 语义层测试 —— 位掩码 CLRD/解锁判定/通关写回/曲目解锁/旧档迁移。

对照 th08-ref(@1861f88, 行号相对其 src/): Clrd 布局 ScoreDat.hpp:213-223、
解锁判定 GameManager.cpp:1327-1362、通关写回 GameManager.cpp:297-308/:357-363
与 Ending.cpp:504-557、曲解锁下标表 Gui.cpp:39-50。
纯逻辑用例经 progress.load_score_store 建 th08 口径库(222 卡 × 13 槽双组
catk / clrd 13 行 × 5 难度位掩码 / plst.bgmUnlocked[32]); world 接线用例
打 needs_data(真 th08.dat), 对局模式照 test_th08_world.py。
"""

from __future__ import annotations

import msgspec

from touhou.engine.score_store import ScoreStore
from touhou.games.th08 import progress
from touhou.games.th08.progress import (
    BAD_ENDING_BGM_VALUE,
    EXTRA_UNLOCKED_FLAG,
    NUM_CHARACTERS,
    SHOT_ALL_ROW,
    SPELL_PRACTICE_UNLOCKED_FLAG,
    is_extra_unlocked,
    is_extra_unlocked_for_character,
    is_extra_unlocked_with_all_teams,
    is_spell_practice_unlocked,
    is_spell_practice_unlocked_for_character,
    load_score_store,
    migrate_legacy_clrd,
    record_bad_ending,
    record_ending_clear,
    record_extra_clear,
    record_stage_clear,
    stage_cleared_with_retries,
    stage_cleared_without_retries,
    unlock_bgm,
    unlock_stage_bgm,
)
from touhou.games.th08.world import ImperishableNight

from .conftest import needs_data
from .test_th08_world import _move_keys  # 复用横移输入桩

SPELL = SPELL_PRACTICE_UNLOCKED_FLAG
EXTRA = EXTRA_UNLOCKED_FLAG


def _store(tmp_path) -> ScoreStore:
    """缺文件 → 全新 th08 口径库。"""
    return load_score_store(tmp_path / "score.json")


# ---- 库形状 ----
def test_th08_store_shape(tmp_path) -> None:
    s = _store(tmp_path)
    assert len(s.catk) == progress.SPELLCARD_COUNT == 222
    assert len(s.catk[0]["attempts"]) == 13  # 12 机体 + SHOT_ALL 合计槽
    assert s.catk[0]["practice"]["successes"] == [0] * 13
    assert len(s.clrd) == 13  # 12 机体 + SHOT_ALL 合计行(GameManager.hpp:337)
    assert all(len(r["without_retries"]) == 5 for r in s.clrd)
    assert s.plst["bgmUnlocked"] == [0] * 32


# ---- 解锁判定 ----
def test_unlock_judges_default_locked(tmp_path) -> None:
    s = _store(tmp_path)
    assert not is_extra_unlocked(s) and not is_spell_practice_unlocked(s)
    assert not is_extra_unlocked_with_all_teams(s)
    for c in range(4):
        assert not is_extra_unlocked_for_character(s, c)
        assert not is_spell_practice_unlocked_for_character(s, c)
    for c in range(4, NUM_CHARACTERS):  # 单人机体恒 True(GameManager.cpp:1329)
        assert is_extra_unlocked_for_character(s, c)
        assert is_spell_practice_unlocked_for_character(s, c)


def test_unlock_flags_scan_main_difficulties_only(tmp_path) -> None:
    """判定只扫槽 0..3(EASY..LUNATIC, GameManager.cpp:1331): EX 槽(4)的
    flag 不算数; all_teams 要 4 组全真(Supervisor.cpp:1526-1532)。"""
    s = _store(tmp_path)
    s.clrd[0]["without_retries"][4] |= EXTRA
    s.clrd[0]["with_retries"][4] |= SPELL
    assert not is_extra_unlocked(s) and not is_spell_practice_unlocked(s)
    s.clrd[1]["without_retries"][3] |= EXTRA
    s.clrd[1]["with_retries"][0] |= SPELL
    assert is_extra_unlocked(s) and is_spell_practice_unlocked(s)
    assert not is_extra_unlocked_with_all_teams(s)
    for c in range(4):
        s.clrd[c]["without_retries"][2] |= EXTRA
    assert is_extra_unlocked_with_all_teams(s)


# ---- 通关写回 ----
def test_record_stage_clear_bit_and_mirror(tmp_path) -> None:
    """过关置面位 (GameManager.cpp:297-308): 无续关进 without 表, with 表
    无条件; SHOT_ALL 合计行同步镜像; 越界静默。"""
    s = _store(tmp_path)
    record_stage_clear(s, 2, 1, 0, num_retries=0)
    assert s.clrd[2]["without_retries"][1] == 0x01
    assert s.clrd[2]["with_retries"][1] == 0x01
    assert s.clrd[SHOT_ALL_ROW]["without_retries"][1] == 0x01
    record_stage_clear(s, 2, 1, 1, num_retries=3)
    assert s.clrd[2]["without_retries"][1] == 0x01  # 有续关不进 without 表
    assert s.clrd[2]["with_retries"][1] == 0x03
    # 越界静默: character 12(合计行不是机体) / difficulty 5 / c_stage 9
    record_stage_clear(s, 12, 1, 2, num_retries=0)
    assert s.clrd[SHOT_ALL_ROW]["without_retries"][1] == 0x01  # 未被直接写
    record_stage_clear(s, 0, 5, 0, num_retries=0)
    record_stage_clear(s, 0, 1, 9, num_retries=0)
    assert s.clrd[0]["without_retries"][1] == 0


def test_record_ending_clear_6a(tmp_path) -> None:
    """6A 通关 (Ending.cpp:504-550): SPELL flag(bit15)两表 + 结局曲 18/19。"""
    s = _store(tmp_path)
    record_ending_clear(s, 0, 1, cleared_6b=False, num_retries=0)
    assert s.clrd[0]["without_retries"][1] == SPELL
    assert s.clrd[0]["with_retries"][1] == SPELL
    assert s.clrd[SHOT_ALL_ROW]["with_retries"][1] == SPELL
    assert s.plst["bgmUnlocked"][18] == 1 and s.plst["bgmUnlocked"][19] == 1
    assert is_spell_practice_unlocked(s) and not is_extra_unlocked(s)


def test_record_ending_clear_6b(tmp_path) -> None:
    """6B 无续关: EXTRA flag(bit14)进 without 表 → Extra 解锁。"""
    s = _store(tmp_path)
    record_ending_clear(s, 1, 2, cleared_6b=True, num_retries=0)
    assert s.clrd[1]["without_retries"][2] == EXTRA
    assert is_extra_unlocked(s) and not is_spell_practice_unlocked(s)


def test_record_ending_clear_with_retries(tmp_path) -> None:
    """有续关通关: flag 只进 with 表 → Spell 解锁(判定读 with)、Extra 不解锁
    (判定读 without); 结局曲解锁不受续关影响(Ending.cpp:549-550 无条件)。"""
    s = _store(tmp_path)
    record_ending_clear(s, 0, 1, cleared_6b=True, num_retries=2)
    assert s.clrd[0]["without_retries"][1] == 0
    assert s.clrd[0]["with_retries"][1] == EXTRA
    assert not is_extra_unlocked(s)
    record_ending_clear(s, 0, 1, cleared_6b=False, num_retries=2)
    assert is_spell_practice_unlocked(s)
    assert s.plst["bgmUnlocked"][18] == 1


def test_record_bad_ending(tmp_path) -> None:
    """Bad Ending (Ending.cpp:556): bgmUnlocked[18] = 0x12(照抄原作值)。"""
    s = _store(tmp_path)
    record_bad_ending(s)
    assert s.plst["bgmUnlocked"][18] == BAD_ENDING_BGM_VALUE == 0x12
    assert s.plst["bgmUnlocked"][19] == 0


def test_record_extra_clear_asymmetric_quirk(tmp_path) -> None:
    """EX 通关追加 (GameManager.cpp:357-363): 0x8000 只写 without[本机][EX]
    与 with[SHOT_ALL][EX] —— 两表不对称是原作原样(疑似 ZUN 笔误, 照抄)。"""
    s = _store(tmp_path)
    record_extra_clear(s, 3, 1)  # difficulty != EXTRA → no-op
    assert s.clrd[3]["without_retries"][4] == 0
    record_extra_clear(s, 3, 4)
    assert s.clrd[3]["without_retries"][4] == 0x8000
    assert s.clrd[3]["with_retries"][4] == 0  # 不对称 quirk
    assert s.clrd[SHOT_ALL_ROW]["with_retries"][4] == 0x8000
    assert s.clrd[SHOT_ALL_ROW]["without_retries"][4] == 0
    assert not is_extra_unlocked(s)  # EX 槽的 flag 不影响判定


# ---- 面通关读取(选关画面接口) ----
def test_stage_cleared_readers(tmp_path) -> None:
    s = _store(tmp_path)
    record_stage_clear(s, 0, 1, 3, num_retries=0)
    record_stage_clear(s, 0, 1, 4, num_retries=1)
    assert stage_cleared_without_retries(s, 0, 1, 3)
    assert stage_cleared_with_retries(s, 0, 1, 3)
    assert not stage_cleared_without_retries(s, 0, 1, 4)
    assert stage_cleared_with_retries(s, 0, 1, 4)
    # 越界 → False
    assert not stage_cleared_with_retries(s, 99, 1, 3)
    assert not stage_cleared_with_retries(s, 0, 9, 3)


# ---- 曲目解锁 ----
def test_unlock_stage_bgm_table(tmp_path) -> None:
    """g_GuiStageMusicContexts (Gui.cpp:39-50): (面曲, boss 曲, LS 曲)。"""
    s = _store(tmp_path)
    unlock_stage_bgm(s, 0, 0)  # 1 面面曲
    unlock_stage_bgm(s, 0, 1)  # 1 面 boss 曲
    unlock_stage_bgm(s, 6, 2)  # 6A Last Spell 曲
    unlock_stage_bgm(s, 8, 1)  # EX boss 曲
    bgm = s.plst["bgmUnlocked"]
    assert bgm[1] == 1 and bgm[2] == 1 and bgm[15] == 1 and bgm[17] == 1
    unlock_stage_bgm(s, -1, 0)  # 越界静默
    unlock_stage_bgm(s, 9, 0)
    unlock_stage_bgm(s, 0, 5)
    unlock_bgm(s, 99)
    assert sum(1 for v in bgm if v) == 4


# ---- 旧档迁移(A 期 max-stage 整数 → 位掩码) ----
def _legacy_row(no_retry_gated: int, always: int) -> dict:
    """A 期旧档的一行 clrd: 6 难度槽的 max-stage 整数, 键名语义与 th08 反
    (旧 with_retries 被 numRetries==0 门控 = th08 的 without 语义)。"""
    return {
        "with_retries": [0, no_retry_gated, 0, 0, 0, 0],
        "without_retries": [0, always, 0, 0, 0, 0],
    }


def test_migrate_legacy_6a_clear(tmp_path) -> None:
    """旧档 6A 无续关通关(stage_no 7) → 面位 0x67 + SPELL flag, 键名互换,
    SHOT_ALL 行按 OR 重建; 4A/4B 歧义位不置(保守)。"""
    p = tmp_path / "score.json"
    p.write_bytes(msgspec.json.encode({"clrd": [_legacy_row(7, 7)]}))
    s = load_score_store(p)
    expect = 0x67 | SPELL
    assert s.clrd[0]["without_retries"][1] == expect
    assert s.clrd[0]["with_retries"][1] == expect
    assert s.clrd[SHOT_ALL_ROW]["without_retries"][1] == expect
    assert is_spell_practice_unlocked(s) and not is_extra_unlocked(s)
    assert stage_cleared_without_retries(s, 0, 1, 6)  # 6A 面位
    assert not stage_cleared_without_retries(s, 0, 1, 3)  # 4A/4B 歧义不置


def test_migrate_legacy_6b_and_retry_clear(tmp_path) -> None:
    """6B 无续关(stage_no 8) → EXTRA flag; 有续关通关的旧档(旧 with=0,
    旧 without=7) → flag 只进新 with 表。"""
    p = tmp_path / "score.json"
    p.write_bytes(msgspec.json.encode({"clrd": [_legacy_row(8, 8), _legacy_row(0, 7)]}))
    s = load_score_store(p)
    assert s.clrd[0]["without_retries"][1] == 0xA7 | EXTRA
    assert is_extra_unlocked(s)
    assert s.clrd[1]["without_retries"][1] == 0
    assert s.clrd[1]["with_retries"][1] == 0x67 | SPELL
    assert is_spell_practice_unlocked(s)


def test_migrate_legacy_conservative_cases(tmp_path) -> None:
    """保守点: 止于 5 面(6)不置 flag; EX 槽(4)的旧值与 th07 存档(同文件
    共用, EX 通关也记 7)歧义不置 flag; >9 的值原样保留不猜测。"""
    row = _legacy_row(6, 6)
    row["with_retries"][4] = 7
    row["without_retries"][4] = 7
    row["with_retries"][3] = 99
    row["without_retries"][3] = 99
    p = tmp_path / "score.json"
    p.write_bytes(msgspec.json.encode({"clrd": [row]}))
    s = load_score_store(p)
    assert s.clrd[0]["without_retries"][1] == 0x27  # 无 flag
    assert not is_spell_practice_unlocked(s) and not is_extra_unlocked(s)
    assert s.clrd[0]["with_retries"][4] == 0x67  # EX 槽不置 SPELL flag
    assert s.clrd[0]["with_retries"][3] == 99  # >9 原样保留


def test_migrate_skips_new_format_and_roundtrip(tmp_path) -> None:
    """5 槽新格式不迁移; save → load 幂等。"""
    s = _store(tmp_path)
    record_ending_clear(s, 2, 3, cleared_6b=True, num_retries=0)
    s.catk[100]["attempts"][5] = 4
    p = tmp_path / "score.json"
    s.save(p)
    s2 = load_score_store(p)
    assert s2.clrd == s.clrd
    assert s2.catk[100]["attempts"][5] == 4
    assert s2.plst["bgmUnlocked"] == s.plst["bgmUnlocked"]
    data = msgspec.json.decode(p.read_bytes())
    assert not migrate_legacy_clrd(data)  # 新格式不发生二次迁移


def test_migrate_shot_all_row_rebuilt_as_or(tmp_path) -> None:
    """合计行不信任旧档值, 迁移后按 0..11 行 OR 重建(GameManager.cpp:303/:307
    的写入镜像语义)。"""
    rows = [_legacy_row(0, 0) for _ in range(13)]
    rows[0] = _legacy_row(7, 7)
    rows[5]["without_retries"][1] = 16  # >9 原样保留 → 新 with 得 0x10(4B 位)
    rows[12] = {"with_retries": [9] * 6, "without_retries": [9] * 6}  # 旧合计行
    p = tmp_path / "score.json"
    p.write_bytes(msgspec.json.encode({"clrd": rows}))
    s = load_score_store(p)
    assert s.clrd[SHOT_ALL_ROW]["with_retries"][1] == (0x67 | SPELL) | 0x10
    assert s.clrd[SHOT_ALL_ROW]["without_retries"][1] == 0x67 | SPELL


def test_load_missing_and_corrupt(tmp_path) -> None:
    s = load_score_store(tmp_path / "nope.json")
    assert len(s.clrd) == 13 and not is_extra_unlocked(s)
    p = tmp_path / "bad.json"
    p.write_bytes(b"{not json")
    s2 = load_score_store(p)
    assert len(s2.catk) == 222


# ---- world 接线(真 th08.dat) ----
def _game(
    store: ScoreStore, character: int = 0, difficulty: int = 1
) -> ImperishableNight:
    return ImperishableNight(
        character=character, difficulty=difficulty, seed=1, score_store=store
    )


@needs_data
def test_world_catk_wiring(tmp_path) -> None:
    """ECL 符卡 begin/end → catk 入账: attempts/successes 双槽(shotType +
    SHOT_ALL), practice 组不动; highscore = 显示分(代码值 // 10)。"""
    s = _store(tmp_path)
    g = _game(s)
    for f in range(9000):
        g.tick(keys=_move_keys(f), advance=True)
        if g.game_over:
            g.game_over = False
            g.lives = 3.0
        if g.spellcard_active():
            break
    assert g.spellcard_active(), "9000 帧内符卡未开始"
    assert g.boss is not None
    idx = g._catk_idx
    assert idx is not None
    e = s.catk[idx]
    assert e["name"], "符卡名未入账"
    assert e["attempts"][0] == 1 and e["attempts"][SHOT_ALL_ROW] == 1
    assert e["practice"]["attempts"][0] == 0  # 练习组不动
    # 捕获路径(自机可能死过导致 is_capturing 已清, 测试里强制回捕获态)
    g.boss.is_capturing = True
    res = g.boss.end_spellcard()
    assert res["captured"]
    g._apply_spellcard_end(res)
    assert e["successes"][0] == 1 and e["successes"][SHOT_ALL_ROW] == 1
    assert e["highscore"][0] == res["score"] // 10
    assert e["highscore"][SHOT_ALL_ROW] == res["score"] // 10
    assert e["practice"]["successes"][0] == 0


@needs_data
def test_world_clrd_and_ending_wiring(tmp_path) -> None:
    """过关/结局 → CLRD 位掩码: 面位在结局写回之前先入账; 6A→SPELL、
    6B 无续关→EXTRA、6B 有续关→只进 with 表; good ending 解锁曲 18/19。"""
    s = _store(tmp_path)
    # 1 面过关(无续关): bit0 两表 + 合计行
    g = _game(s, character=0, difficulty=1)
    g._advance_or_ending()
    assert g._pending_next_level
    assert s.clrd[0]["without_retries"][1] == 0x01
    assert s.clrd[0]["with_retries"][1] == 0x01
    assert s.clrd[SHOT_ALL_ROW]["without_retries"][1] == 0x01
    # 6A 通关(无续关): bit6 + SPELL flag
    g2 = _game(s, character=1, difficulty=2)
    g2.stage_no = 7
    g2._advance_or_ending()
    assert g2.ending is not None
    assert s.clrd[1]["without_retries"][2] == 0x40 | SPELL
    assert s.clrd[1]["with_retries"][2] == 0x40 | SPELL
    assert s.plst["bgmUnlocked"][18] == 1 and s.plst["bgmUnlocked"][19] == 1
    assert is_spell_practice_unlocked(s) and not is_extra_unlocked(s)
    # 6B 通关但有续关: flag 只进 with 表
    g3 = _game(s, character=2, difficulty=3)
    g3.globals.num_retries = 1
    g3.stage_no = 8
    g3._advance_or_ending()
    assert s.clrd[2]["without_retries"][3] == 0
    assert s.clrd[2]["with_retries"][3] == 0x80 | EXTRA
    assert not is_extra_unlocked(s)
    # 6B 无续关: Extra 解锁(4 组任一), 但非全组
    g4 = _game(s, character=3, difficulty=0)
    g4.stage_no = 8
    g4._advance_or_ending()
    assert s.clrd[3]["without_retries"][0] == 0x80 | EXTRA
    assert is_extra_unlocked(s)
    assert not is_extra_unlocked_with_all_teams(s)


@needs_data
def test_world_extra_clear_wiring(tmp_path) -> None:
    """EX 通关: 面位 bit8 两表 + 0x8000 不对称追加; 构造时初始面面曲已解锁。"""
    s = _store(tmp_path)
    g = _game(s, character=0, difficulty=1)
    assert s.plst["bgmUnlocked"][1] == 1  # 构造置位 1 面面曲
    g.difficulty = 4
    g.stage_no = 9
    g._advance_or_ending()
    assert g.cleared and g.result is not None
    assert s.clrd[0]["without_retries"][4] == 0x100 | 0x8000
    assert s.clrd[0]["with_retries"][4] == 0x100  # quirk: 无 0x8000
    assert s.clrd[SHOT_ALL_ROW]["with_retries"][4] == 0x100 | 0x8000
    assert s.clrd[SHOT_ALL_ROW]["without_retries"][4] == 0x100
    assert not is_extra_unlocked(s)  # 判定不扫 EX 槽
