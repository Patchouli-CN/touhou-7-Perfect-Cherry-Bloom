"""Touhou: 射击数据 .sht 解析测试(用真实 th07 资源)。"""

from __future__ import annotations

import sys

sys.path.insert(0, r"D:\python_play\Touhou08")

from touhou.schema.archive import GameArchive  # noqa: E402
from touhou.schema.shot_data import parse_sht  # noqa: E402

DAT = r"D:\TOUHOU_GAME\[th07] 东方妖妖梦 (日文版)\th07.dat"


def _sd(name: str):
    arch = GameArchive.open(DAT)
    return parse_sht(arch.load(name))


def test_reimu_a_parse() -> None:
    sd = _sd("ply00a.sht")
    assert abs(sd.speed - 4.0) < 1e-4
    assert abs(sd.speed_focus - 1.6) < 1e-4
    assert sd.hitbox_radius > 0 and sd.grab_item_radius > 0
    assert sd.levels, "应有火力等级表"
    # 等级按 requiredPower 递增
    powers = [l.required_power for l in sd.levels]
    assert powers == sorted(powers)
    # 首级应包含有效射击条目
    entries = sd.levels[0].entries
    assert entries and entries[0].damage > 0


def test_sakuya_b_distinct() -> None:
    reimu = _sd("ply00a.sht")
    sakuya = _sd("ply02b.sht")
    # 咲夜B 擦弹半径/速度应不同于灵梦A
    assert abs(sakuya.grab_item_radius - reimu.grab_item_radius) > 1e-6
    assert abs(sakuya.speed_focus - reimu.speed_focus) > 1e-6


def test_focus_vs_unfocused_entry_tables() -> None:
    # 未 focus / focus 的 sht 是两份独立文件, 条目构成不同
    unf = _sd("ply00a.sht")
    foc = _sd("ply00as.sht")
    # 两份的等级条目链应有差异(focus 集中射击, 条目更少/更聚合)
    a = [len(l.entries) for l in unf.levels]
    b = [len(l.entries) for l in foc.levels]
    assert a != b, f"focus 与 unfocus 的条目表应不同: {a} vs {b}"


def test_level_for_power_selects_highest() -> None:
    sd = _sd("ply00a.sht")
    top = sd.levels[-1]
    chosen = sd.level_for_power(top.required_power + 1)
    assert chosen is top
