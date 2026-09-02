"""gensokyo_time 彩蛋模块测试（通用层，只 import utils）。"""

from datetime import date, datetime

from touhou.utils.gensokyo_time import (
    ATTRIBUTE_ANCHOR_SEASON,
    HAKUREI_GREAT_BARRIER_BUILT_TIME,
    INCIDENT_ETERNAL_NIGHT,
    KNOWN_INCIDENTS,
    THIRTEENTH_MONTH_NAME,
    TRADITIONAL_MONTH_NAMES,
    GensokyoSeasonTime,
    incidents_in_season,
    year_attributes,
    year_attributes_str,
)


def test_season_conversion() -> None:
    """季 ↔ 外界年：第零季=1885（推算值），一季=一年。"""
    st = GensokyoSeasonTime.from_outside(date(1885, 6, 1))
    assert st.season == 0
    assert GensokyoSeasonTime(season=119, month=9, day=27).to_outside() == date(
        2004, 9, 27
    )
    assert ATTRIBUTE_ANCHOR_SEASON == 120
    assert HAKUREI_GREAT_BARRIER_BUILT_TIME.year == 1885


def test_year_attributes_against_thbwiki_timeline() -> None:
    """三精×四季×五行推进规则，与 THBWiki 幻想乡年表逐年核对。

    锚点 第120季=「日与春与土」（六十年不见的紫香花）；每年三系统各推进一位。
    """
    expected = {
        118: ("月", "秋", "木"),
        119: ("星", "冬", "金"),
        120: ("日", "春", "土"),
        121: ("月", "夏", "火"),
        122: ("星", "秋", "水"),
        123: ("日", "冬", "木"),
        124: ("月", "春", "金"),
        125: ("星", "夏", "土"),
        126: ("日", "秋", "火"),
    }
    for season, attrs in expected.items():
        assert year_attributes(season) == attrs, f"第{season}季"
    assert year_attributes_str(119) == "星与冬与金之年"
    # 60 年遍历全组合（3×4×5=60）
    combos = {year_attributes(s) for s in range(120, 180)}
    assert len(combos) == 60


def test_at_datetime_bridge() -> None:
    """at()：年月日由季历提供，时分秒由调用方补 —— datetime 必须年月日齐全。"""
    dt = GensokyoSeasonTime(season=119, month=9, day=27).at(23, 0)
    assert dt == datetime(2004, 9, 27, 23, 0)


def test_traditional_month_names() -> None:
    """旧历月名 + 妖怪太阴历十三月（香霖堂第23话）。"""
    assert len(TRADITIONAL_MONTH_NAMES) == 12
    assert TRADITIONAL_MONTH_NAMES[0] == "睦月"
    assert TRADITIONAL_MONTH_NAMES[11] == "師走"
    assert THIRTEENTH_MONTH_NAME == "十三月"
    st = GensokyoSeasonTime(season=121, month=1)
    assert st.traditional_month_name == "睦月"


def test_incidents_integrity() -> None:
    """异变年表：按季排序、永夜异变锚点正确。"""
    seasons = [inc.season_time.season for inc in KNOWN_INCIDENTS]
    assert seasons == sorted(seasons)
    assert INCIDENT_ETERNAL_NIGHT.outside_date == date(2004, 9, 27)
    assert INCIDENT_ETERNAL_NIGHT in incidents_in_season(119)
    # 永夜异变当季还有春雪(妖妖梦)与萃集宴会(萃梦想)
    assert len(incidents_in_season(119)) == 3
