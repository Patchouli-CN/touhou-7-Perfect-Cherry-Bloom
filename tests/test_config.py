"""GameConfig 设置持久化测试: 默认值 / 读写回环 / 损坏容错 / 字段级回退。

对照 score_store 的容错语义(OpenScore RECREATE_SCORE 分支)。
"""
from __future__ import annotations

import sys

sys.path.insert(0, r"D:\python_play\Touhou08")

from touhou.engine.config import GameConfig  # noqa: E402


def test_defaults() -> None:
    cfg = GameConfig()
    assert cfg.bgm_volume == 100
    assert cfg.se_volume == 100
    assert cfg.bgm_source == "wav"
    assert cfg.window_scale == 2
    assert cfg.initial_lives == 3


def test_save_load_roundtrip(tmp_path) -> None:
    p = tmp_path / "config.json"
    cfg = GameConfig(bgm_volume=60, se_volume=30, bgm_source="midi",
                     window_scale=3, initial_lives=5)
    cfg.save(p)
    got = GameConfig.load(p)
    assert got == cfg


def test_load_missing_file_returns_defaults(tmp_path) -> None:
    got = GameConfig.load(tmp_path / "nonexistent.json")
    assert got == GameConfig()


def test_load_corrupted_file_returns_defaults(tmp_path) -> None:
    p = tmp_path / "config.json"
    p.write_text("{不是合法 JSON", encoding="utf-8")
    assert GameConfig.load(p) == GameConfig()
    p.write_text("[1, 2, 3]", encoding="utf-8")  # 合法但非 dict
    assert GameConfig.load(p) == GameConfig()


def test_from_dict_field_level_fallback() -> None:
    """单个字段类型不对只回退该字段, 其余保留。"""
    got = GameConfig.from_dict({
        "bgm_volume": "响一点",       # 非 int → 默认 100
        "se_volume": 42,              # 合法保留
        "bgm_source": "ogg",          # 非法枚举 → 默认 wav
        "window_scale": 3,
        "initial_lives": 2,
    })
    assert got.bgm_volume == 100
    assert got.se_volume == 42
    assert got.bgm_source == "wav"
    assert got.window_scale == 3
    assert got.initial_lives == 2


def test_from_dict_clamps_out_of_range() -> None:
    got = GameConfig.from_dict({
        "bgm_volume": 250,
        "se_volume": -10,
        "window_scale": 99,
        "initial_lives": 1,
    })
    assert got.bgm_volume == 100
    assert got.se_volume == 0
    assert got.window_scale == 3
    assert got.initial_lives == 2


def test_from_dict_bool_is_not_int() -> None:
    got = GameConfig.from_dict({"bgm_volume": True, "initial_lives": False})
    assert got.bgm_volume == 100
    assert got.initial_lives == 3
