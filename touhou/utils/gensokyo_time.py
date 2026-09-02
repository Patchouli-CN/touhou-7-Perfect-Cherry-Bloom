"""幻想乡时间模块（彩蛋模块）

历法设定考据（尊重 ZUN 设定，出处标注）：
- 幻想乡通用两套历法：居民主要用公历（太阳历，因外界使用而跟进），
  妖怪之山沿用妖怪太阴历（THBWiki「幻想乡」§历法，出典 东方香霖堂 第23话）：
  以月相周期为月、最小单位是"月"（不存在"日"，新月=午夜、满月=正午的感觉）、
  周期长达六十年；有不叫闰月的"十三月"，该年妖怪力量增强，
  故 13 在某些地方被视为不吉利。
- "第 ○○ 季"纪年：《文文。新闻》纪年法，很可能指博丽大结界张开后
  经过的年份，月份表述与日本旧历一致（THBWiki「幻想乡」§历法）。
  注意：第零季=1885 是**推算**（由 花映塚 2005年=第120季、60年周期回推，
  60×2=1885），官方从未明写 1885（Touhou Wiki Talk:Gensokyo Timeline、
  THBWiki「幻想乡年表」问题点节亦列有第零季/第一季之争）。
- 年属性：每季由 三精×四季×五行 组合命名（求闻史纪/花映塚设定：
  三系统每年各推进一位，3×4×5=60 年遍历全组合），锚点 第120季=
  「日与春与土」（六十年不见的紫香花，万物再生），推进规则经
  THBWiki「幻想乡年表」第118-126季逐年验证（如 第119季=「星与冬与金」）。

参考：
- https://thbwiki.cc/幻想乡年表
- https://en.touhouwiki.net/wiki/Gensokyo_Timeline
"""

from datetime import date, datetime, time
from typing import Self

import msgspec


# ── 常量 ──────────────────────────────────────────

HAKUREI_GREAT_BARRIER_BUILT_TIME: date = date(1885, 1, 1)
"""博丽大结界建立时间（第零季）。

注意：1885 是推算值（由 花映塚 2005年=第120季 按 60 年周期回推 60×2），
官方未明写，详见模块 docstring 的考据说明"""

INCIDENT_CYCLE_YEARS: int = 60
"""大结界异变周期：每60季一次"""

# ── 年属性：三精 × 四季 × 五行（求闻史纪/花映塚设定）──

SANSEI: tuple[str, str, str] = ("日", "月", "星")
"""三精（气质）：日(傲慢) → 月(协调) → 星(多样)"""

SHIKI: tuple[str, str, str, str] = ("春", "夏", "秋", "冬")
"""四季（生命）：春(诞生) → 夏(成长) → 秋(成果衰退) → 冬(死)"""

WUXING: tuple[str, str, str, str, str] = ("火", "水", "木", "金", "土")
"""五行（物质）：火(激情) → 水(归还于无) → 木(强大温柔) → 金(沉默) → 土(再生)"""

ATTRIBUTE_ANCHOR_SEASON: int = 120
"""年属性锚点：第120季 = 「日与春与土」（六十年不见的紫香花，万物再生）"""

# ── 日本旧历月名（《文文。新闻》纪年月份表述与之一致）──

TRADITIONAL_MONTH_NAMES: tuple[str, ...] = (
    "睦月", "如月", "弥生", "卯月", "皐月", "水無月",
    "文月", "葉月", "長月", "神無月", "霜月", "師走",
)
"""旧历十二月名（1月=睦月 … 12月=師走）"""

THIRTEENTH_MONTH_NAME: str = "十三月"
"""妖怪太阴历的"十三月"：不叫闰月；有十三月的年份妖怪力量增强，
故 13 在某些地方被视为不吉利（东方香霖堂 第23话，霖之助述）"""


# ── 内部工具 ──────────────────────────────────────


def _season_to_date(season: int, month: int = 1, day: int = 1) -> date:
    """第 N 季 M 月 D 日 → 外界日期"""
    return date(HAKUREI_GREAT_BARRIER_BUILT_TIME.year + season, month, day)


def _date_to_season(d: date) -> int:
    """外界日期 → 第 N 季（按年份算，一季=一年）"""
    return d.year - HAKUREI_GREAT_BARRIER_BUILT_TIME.year


# ── 基础函数 ──────────────────────────────────────


def gensokyo_current_season(today: date | None = None) -> int:
    """当前是第几季（第零季起算，只看年份）"""
    return _date_to_season(today or date.today())


def next_barrier_incident(today: date | None = None) -> tuple[int, date]:
    """
    返回 (下一次异变是第几季, 异变发生日期)
    每60季一次，第0、60、120、180...季
    """
    today = today or date.today()
    current = gensokyo_current_season(today)
    next_season = ((current // INCIDENT_CYCLE_YEARS) + 1) * INCIDENT_CYCLE_YEARS
    return next_season, _season_to_date(next_season)


def barrier_incident_number(today: date | None = None) -> int:
    """当前是第几次大结界异变周期（第0季算第0次）"""
    return gensokyo_current_season(today) // INCIDENT_CYCLE_YEARS


def year_attributes(season: int) -> tuple[str, str, str]:
    """指定季的三精×四季×五行年属性，如 第119季 → ("星", "冬", "金")。

    三系统每年各推进一位（3×4×5=60 年遍历全组合），锚点 第120季=
    （日, 春, 土）（六十年不见的紫香花）；推进规则经 THBWiki「幻想乡年表」
    第118-126季逐年验证（119=星冬金、121=月夏火、122=星秋水、123=日冬木…）。
    """
    offset = season - ATTRIBUTE_ANCHOR_SEASON
    return (
        SANSEI[offset % len(SANSEI)],
        SHIKI[offset % len(SHIKI)],
        # 锚点季=土，土在 WUXING 循环（火水木金土）中排第 5
        WUXING[(WUXING.index("土") + offset) % len(WUXING)],
    )


def year_attributes_str(season: int) -> str:
    """年属性的年表风格写法，如 "星与冬与金之年" """
    sansei, shiki, wuxing = year_attributes(season)
    return f"{sansei}与{shiki}与{wuxing}之年"


# ── 数据结构 ──────────────────────────────────────


class GensokyoSeasonTime(msgspec.Struct):
    """
    幻想乡季时间
    格式：第 N 季 MM 月 DD 日
    """

    season: int
    month: int = 1
    day: int = 1

    @classmethod
    def from_outside(cls, d: date | None = None) -> Self:
        """从外界日期构造完整的幻想乡历"""
        d = d or date.today()
        return cls(
            season=_date_to_season(d),
            month=d.month,
            day=d.day,
        )

    def to_outside(self) -> date:
        """转换为外界日期（YYYY-MM-DD）"""
        return _season_to_date(self.season, self.month, self.day)

    def at(self, hour: int, minute: int = 0, second: int = 0) -> datetime:
        """补上时分秒，转换为外界 datetime。

        datetime 必须年月日齐全才能携带时分秒——年月日由季历提供
        （这正是本模块存在的意义之一），时分秒由调用方给出
        （如永夜抄开局 23:00 = INCIDENT_ETERNAL_NIGHT.season_time.at(23)）。
        """
        return datetime.combine(self.to_outside(), time(hour, minute, second))

    @property
    def year_attributes(self) -> tuple[str, str, str]:
        """本季的三精×四季×五行年属性（见模块级 year_attributes）"""
        return year_attributes(self.season)

    @property
    def traditional_month_name(self) -> str:
        """旧历月名（睦月…師走，《文文。新闻》纪年用）"""
        return TRADITIONAL_MONTH_NAMES[self.month - 1]

    @property
    def barrier_cycle(self) -> int:
        """当前处于第几次大结界异变周期"""
        return self.season // INCIDENT_CYCLE_YEARS

    def __str__(self) -> str:
        return (
            f"第{self.season}季-{self.month:02d}月{self.day:02d}日"
            f"（{year_attributes_str(self.season)} / 外界{self.to_outside()}）"
        )

    def __repr__(self) -> str:
        return f"GensokyoSeasonTime(season={self.season}, month={self.month}, day={self.day})"


class GensokyoIncident(msgspec.Struct):
    """幻想乡异变记录"""

    name: str
    """异变名称"""
    season_time: GensokyoSeasonTime
    """发生时间（精确到月日）"""
    description: str = ""
    """备注"""

    @property
    def outside_date(self) -> date:
        """对应的外界日期"""
        return self.season_time.to_outside()

    @property
    def outside_year(self) -> int:
        """对应的外界年份"""
        return self.outside_date.year

    def __str__(self) -> str:
        return (
            f"「{self.name}」"
            f"第{self.season_time.season}季-{self.season_time.month:02d}月{self.season_time.day:02d}日 / "
            f"外界{self.outside_date}"
        )


# ── 已知异变（彩蛋数据）────────────────────────────
# 时间取世界观内时间（非发售日），参考 Touhou Wiki Gensokyo Timeline:
# https://en.touhouwiki.net/wiki/Gensokyo_Timeline
# 季 = 外界年份 - 1885；月日能确定的精确到日，只有季节的取该季首月。

INCIDENT_SCARLET_MIST = GensokyoIncident(
    name="红雾异变",
    season_time=GensokyoSeasonTime(season=118, month=8, day=12),  # 2003-08-12
    description="红魔乡。红雾笼罩幻想乡，首次使用符卡规则解决异变",
)

INCIDENT_SPRING_SNOW = GensokyoIncident(
    name="春雪异变",
    season_time=GensokyoSeasonTime(season=119, month=5, day=8),  # 2004-05-08
    description="妖妖梦。春天迟迟不来，西行妖满开",
)

INCIDENT_GATHERING_NIGHT_PARADE = GensokyoIncident(
    name="萃集宴会异变",
    season_time=GensokyoSeasonTime(season=119, month=8, day=1),  # 2004年夏
    description="萃梦想。宴会上聚集的妖气，萃香登场（时间线在永夜抄之前）",
)

INCIDENT_ETERNAL_NIGHT = GensokyoIncident(
    name="永夜异变",
    season_time=GensokyoSeasonTime(season=119, month=9, day=27),  # 2004-09-27/28
    description=(
        "永夜抄。伪月、竹取物语、蓬莱之药。"
        "当晚为迎接中秋名月之夜（2004年中秋=旧历8月15=新历9月28日，满月），"
        "23:00 开局、跨午夜至 9月28日 凌晨（THBWiki 幻想乡年表 第119季）"
    ),
)

INCIDENT_SIXTY_YEAR_CYCLE = GensokyoIncident(
    name="花映冢异变",
    season_time=GensokyoSeasonTime(season=120, month=5, day=1),  # 2005年春
    description="花映冢。六十年周期的大结界异变，紫香花盛开",
)

INCIDENT_FAITH = GensokyoIncident(
    name="风神录异变",
    season_time=GensokyoSeasonTime(season=122, month=10, day=1),  # 2007年秋
    description="风神录。守矢神社迁入幻想乡，信仰之争",
)

INCIDENT_SCARLET_WEATHER = GensokyoIncident(
    name="绯想天异变",
    season_time=GensokyoSeasonTime(season=123, month=7, day=1),  # 2008年夏
    description="绯想天。天气异变，博丽神社地震倒塌（又重建）",
)

INCIDENT_GEYSER = GensokyoIncident(
    name="间歇泉异变",
    season_time=GensokyoSeasonTime(season=123, month=12, day=1),  # 2008年冬
    description="地灵殿。间歇泉、灼热地狱、怨灵，圣辇船被喷出地面",
)

INCIDENT_PALANQUIN_SHIP = GensokyoIncident(
    name="宝船异变",
    season_time=GensokyoSeasonTime(season=124, month=4, day=1),  # 2009年春
    description="星莲船。追寻圣辇船，白莲复活",
)

INCIDENT_HISOUTENSOKU = GensokyoIncident(
    name="非想天则异变",
    season_time=GensokyoSeasonTime(season=124, month=7, day=1),  # 2009年夏
    description="非想天则。巨型人形机器人传闻（其实是核融合炉广告）",
)

INCIDENT_DIVINE_SPIRIT = GensokyoIncident(
    name="神灵异变",
    season_time=GensokyoSeasonTime(season=126, month=4, day=1),  # 2011年春
    description="神灵庙。大量神灵聚集，丰聪耳神子复活",
)

INCIDENT_HOPELESS_MASQUERADE = GensokyoIncident(
    name="宗教战争",
    season_time=GensokyoSeasonTime(season=128, month=7, day=1),  # 2013年夏
    description="心绮楼。希望之面丢失引发的人气争夺战",
)

INCIDENT_REVERSE_BOW = GensokyoIncident(
    name="逆弓异变",
    season_time=GensokyoSeasonTime(season=128, month=10, day=1),  # 2013年秋
    description="辉针城。万宝槌的魔力，道具暴动与小人族",
)

INCIDENT_URBAN_LEGEND = GensokyoIncident(
    name="都市传说异变",
    season_time=GensokyoSeasonTime(season=130, month=5, day=1),  # 2015年5月
    description="深秘录。都市传说具现化，外界与幻想乡的边界骚动",
)

INCIDENT_LUNATIC_KINGDOM = GensokyoIncident(
    name="纯狐侵攻异变",
    season_time=GensokyoSeasonTime(season=130, month=10, day=1),  # 2015年秋
    description="绀珠传。月都被纯狐围攻，铃仙求援",
)

INCIDENT_PERFECT_POSSESSION = GensokyoIncident(
    name="完全凭依异变",
    season_time=GensokyoSeasonTime(season=132, month=4, day=1),  # 2017年春
    description="凭依华。强制交换身体的凭依现象",
)

INCIDENT_FOUR_SEASONS = GensokyoIncident(
    name="季节异变",
    season_time=GensokyoSeasonTime(season=132, month=7, day=15),  # 2017年盛夏
    description="天空璋。妖精暴走、四季混乱，后门之神摩多罗",
)

INCIDENT_BEAST_SPIRIT = GensokyoIncident(
    name="动物灵侵攻异变",
    season_time=GensokyoSeasonTime(season=134, month=7, day=1),  # 2019年夏
    description="鬼形兽。地狱动物灵大举入侵地上",
)

INCIDENT_SUNKEN_FOSSIL = GensokyoIncident(
    name="刚欲异闻",
    season_time=GensokyoSeasonTime(season=135, month=4, day=1),  # 2020年春
    description="刚欲异闻。旧血池地狱的黑水（石油）喷涌",
)

INCIDENT_ABILITY_CARD = GensokyoIncident(
    name="能力卡牌异变",
    season_time=GensokyoSeasonTime(season=136, month=4, day=1),  # 2021年春
    description="虹龙洞。能力卡牌流通，龙珠采集，天弓千亦的市场",
)

INCIDENT_BEAST_KINGDOM = GensokyoIncident(
    name="地上争夺异变",
    season_time=GensokyoSeasonTime(season=138, month=7, day=1),  # 2023年夏
    description="兽王园。畜生界组长们的地上支配权争夺",
)

INCIDENT_FOSSILIZED_WONDERS = GensokyoIncident(
    name="化石异变",
    season_time=GensokyoSeasonTime(season=140, month=10, day=1),  # 2025年秋
    description="锦上京。圣域化石苏醒，记忆开始风化消失",
)


# 按季排序的完整异变年表
KNOWN_INCIDENTS: tuple[GensokyoIncident, ...] = (
    INCIDENT_SCARLET_MIST,
    INCIDENT_SPRING_SNOW,
    INCIDENT_GATHERING_NIGHT_PARADE,
    INCIDENT_ETERNAL_NIGHT,
    INCIDENT_SIXTY_YEAR_CYCLE,
    INCIDENT_FAITH,
    INCIDENT_SCARLET_WEATHER,
    INCIDENT_GEYSER,
    INCIDENT_PALANQUIN_SHIP,
    INCIDENT_HISOUTENSOKU,
    INCIDENT_DIVINE_SPIRIT,
    INCIDENT_HOPELESS_MASQUERADE,
    INCIDENT_REVERSE_BOW,
    INCIDENT_URBAN_LEGEND,
    INCIDENT_LUNATIC_KINGDOM,
    INCIDENT_PERFECT_POSSESSION,
    INCIDENT_FOUR_SEASONS,
    INCIDENT_BEAST_SPIRIT,
    INCIDENT_SUNKEN_FOSSIL,
    INCIDENT_ABILITY_CARD,
    INCIDENT_BEAST_KINGDOM,
    INCIDENT_FOSSILIZED_WONDERS,
)
"""已知异变年表（红雾异变 → 锦上京，按时间排序）"""


def incidents_in_season(season: int) -> list[GensokyoIncident]:
    """指定季发生的全部异变"""
    return [inc for inc in KNOWN_INCIDENTS if inc.season_time.season == season]


# ── 快速测试 ──────────────────────────────────────
if __name__ == "__main__":
    now = GensokyoSeasonTime.from_outside()
    print(f"当前时间：{now}")
    print(f"大结界异变周期：第{now.barrier_cycle}次")

    next_season, next_date = next_barrier_incident()
    print(f"下一次大结界异变：第{next_season}季（外界{next_date}）")

    current = incidents_in_season(now.season)
    if current:
        print(f"本季异变：{'、'.join(f'「{i.name}」' for i in current)}")

    print(f"\n已知异变（共{len(KNOWN_INCIDENTS)}起）：")
    for inc in KNOWN_INCIDENTS:
        print(f"  {inc}")
