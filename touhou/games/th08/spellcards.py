"""th08 符卡编号表 —— Result 浏览面的符卡战绩分页数据 + 符卡练习面卡表。

对照 th08-ref(@1861f88, 行号相对其 src/): g_SpellcardNumbersPerDifficulty
(Spellcard.cpp:42-228) —— 6 张难度页(Easy/Normal/Hard/Lunatic/Extra/全难度)
各自的卡号序列; 卡号 = SpellcardNumber 枚举(Spellcard.hpp:18-256)。
E/N/H/L/EX 五页互斥且并集 = 0..204(游戏内 205 张), 全难度页 = 0..221 恒等序
(含 Last Word 17 张 205..221) —— 生成时已断言验证。
符卡练习面卡表 STAGE_SPELLCARD_CARDS(g_SpellcardNumbersPerStage :235-360)、
Last Spell 表(:336-359)、Last Word 实战面映射(TitleScreen.cpp:2466-2510)与
挑战条件文本(TitleSpellCardData.inl)服务于 Practice/Spell Practice(C 期第 5 片)。
"""

from __future__ import annotations

SPELLCARD_LAST_WORD_START = 205  # SPELLCARD_COUNT_IN_GAME_SPELLCARDS

# ResultScreen.cpp:2554-2560 的难度字母(Extra/LastWord 不区分难度显 "-")
SPELLCARD_DIFFICULTY_LETTERS = ("E", "N", "H", "L", "-")

_EASY = (
    2,
    6,
    13,
    17,
    21,
    25,
    32,
    39,
    43,
    47,
    54,
    58,
    62,
    66,
    70,
    77,
    81,
    85,
    89,
    93,
    100,
    104,
    108,
    112,
    119,
    123,
    127,
    131,
    135,
    139,
    143,
    147,
    151,
    155,
    159,
    163,
    167,
    171,
    175,
    179,
    183,
    187,
)
_NORMAL = (
    3,
    7,
    10,
    14,
    18,
    22,
    26,
    29,
    33,
    36,
    40,
    44,
    48,
    51,
    55,
    59,
    63,
    67,
    71,
    74,
    78,
    82,
    86,
    90,
    94,
    97,
    101,
    105,
    109,
    113,
    116,
    120,
    124,
    128,
    132,
    136,
    140,
    144,
    148,
    152,
    156,
    160,
    164,
    168,
    172,
    176,
    180,
    184,
    188,
)
_HARD = (
    0,
    4,
    8,
    11,
    15,
    19,
    23,
    27,
    30,
    34,
    37,
    41,
    45,
    49,
    52,
    56,
    60,
    64,
    68,
    72,
    75,
    79,
    83,
    87,
    91,
    95,
    98,
    102,
    106,
    110,
    114,
    117,
    121,
    125,
    129,
    133,
    137,
    141,
    145,
    149,
    153,
    157,
    161,
    165,
    169,
    173,
    177,
    181,
    185,
    189,
)
_LUNATIC = (
    1,
    5,
    9,
    12,
    16,
    20,
    24,
    28,
    31,
    35,
    38,
    42,
    46,
    50,
    53,
    57,
    61,
    65,
    69,
    73,
    76,
    80,
    84,
    88,
    92,
    96,
    99,
    103,
    107,
    111,
    115,
    118,
    122,
    126,
    130,
    134,
    138,
    142,
    146,
    150,
    154,
    158,
    162,
    166,
    170,
    174,
    178,
    182,
    186,
    190,
)
_EXTRA = (
    191,
    192,
    193,
    194,
    195,
    196,
    197,
    198,
    199,
    200,
    201,
    202,
    203,
    204,
)

# 6 张难度页(ResultScreen 符卡难度选择 6 项 = EASY..EXTRA + 全难度)
SPELLCARDS_PER_DIFFICULTY = (
    _EASY,
    _NORMAL,
    _HARD,
    _LUNATIC,
    _EXTRA,
    tuple(range(222)),  # AllDifficulties = 恒等序(Spellcard.cpp:154-224)
)

# 卡号 → 原生难度字母: E/N/H/L 按页归属, Extra 页与 Last Word 显 "-"
# (偏离注明: 原作显示的是 catk.difficulty = 最后遭遇时的难度,
# Spellcard.cpp:877/:1086; 本篇卡只在本命难度出现, 两种口径一致)
_CARD_LETTER = ["-"] * 222
for _d, _page in enumerate(SPELLCARDS_PER_DIFFICULTY[:4]):
    for _card in _page:
        _CARD_LETTER[_card] = SPELLCARD_DIFFICULTY_LETTERS[_d]
CARD_DIFFICULTY_LETTERS = tuple(_CARD_LETTER)
del _CARD_LETTER


def spellcard_difficulty(card_no: int) -> int:
    """卡号 → 原生难度(GetDifficultyFromSpellCard, Spellcard.cpp:400-415):
    0..4 = E/N/H/L/EX, Last Word 查不到返回 5(>EXTRA)。"""
    for d, page in enumerate(SPELLCARDS_PER_DIFFICULTY[:5]):
        if card_no in page:
            return d
    return 5


# ---- 符卡练习面卡表(g_SpellcardNumbersPerStage, Spellcard.cpp:235-360) ----
# 行 = SpellStageSelect 10 行(0..7 = 1面..6B = C currentStage, 8 = EX, 9 = Last
# Word); 各行恰为连续区间
STAGE_SPELLCARD_CARDS = (
    tuple(range(0, 13)),
    tuple(range(13, 32)),
    tuple(range(32, 54)),
    tuple(range(54, 77)),
    tuple(range(77, 100)),
    tuple(range(100, 119)),
    tuple(range(119, 147)),
    tuple(range(147, 191)),
    tuple(range(191, 205)),
    tuple(range(205, 222)),
)
SPELL_STAGE_COUNT = 10  # 面选行数(g_StageNamesSpellPractice, TitleScreen.cpp:175-187)
SPELL_STAGE_EXTRA = 8  # Extra 行
SPELL_STAGE_LAST_WORD = 9  # Last Word 行(STAGE_LAST_WORD)

# Last Spell 卡号表(g_LastSpellNumbers, Spellcard.cpp:336-359, 43 张)
LAST_SPELL_CARDS = (
    10,
    11,
    12,
    29,
    30,
    31,
    51,
    52,
    53,
    74,
    75,
    76,
    97,
    98,
    99,
    116,
    117,
    118,
    143,
    144,
    145,
    146,
    171,
    172,
    173,
    174,
    175,
    176,
    177,
    178,
    179,
    180,
    181,
    182,
    183,
    184,
    185,
    186,
    187,
    188,
    189,
    190,
    204,
)

# Last Word 卡 → 实战面(TitleScreen.cpp:2466-2510 的 switch; C currentStage)
LAST_WORD_STAGE_MAP = {
    205: 0,
    206: 1,
    207: 2,
    208: 4,
    209: 6,
    210: 7,
    211: 8,
    212: 4,
    213: 8,
    214: 3,
    215: 4,
    216: 3,
    217: 3,
    218: 3,
    219: 3,
    220: 3,
    221: 3,
}

# Last Word 挑战条件文本(g_TitleLastWordCommentFormats, TitleSpellCardData.inl;
# 下标 = 卡号-204, TitleFormatSpellCardInfo.inl:104; %.3d 已按 args+1 代入)
LAST_WORD_COMMENTS = (
    (
        "挑戦可能条件：望月（エキストラ）をスペルカード７枚以上取得して",
        "　　　　　　　　クリアする。",
    ),
    (
        "挑戦可能条件：通常モードFinalBを２キャラ以上クリアする。",
        "　　　　　　　　難易度不問。",
    ),
    (
        "挑戦可能条件：通常モードFinalBを３キャラ以上クリアする。",
        "　　　　　　　　難易度不問。",
    ),
    (
        "挑戦可能条件：スペルカードモードでスペルカードを50枚以上取得する。",
        "　　　　　　　　全キャラ合計。",
    ),
    (
        "挑戦可能条件：通常モードFinalBを４キャラ以上クリアする。",
        "　　　　　　　　難易度不問。",
    ),
    ("挑戦可能条件：No.138を取得する。", " "),
    (
        "挑戦可能条件：スペルカードモードでラストスペルを15枚以上取得する。",
        "　　　　　　　　全キャラ合計。",
    ),
    (
        "挑戦可能条件：No.146,196を取得し",
        "　　　　　　　　かつ、No.205を一度でも見る。",
    ),
    (
        "挑戦可能条件：No.209,210,211を一度でも見る。",
        "　　　　　　　　取得の必要はない。",
    ),
    (
        "挑戦可能条件：No.206,207,208,212を一度でも見る。",
        "　　　　　　　　取得の必要はない。",
    ),
    (
        "挑戦可能条件：魔理沙（単独使用）で三日月（ノーマル）の",
        "　　　　　　　　スペルカードを全て取得。",
    ),
    (
        "挑戦可能条件：霊夢（単独使用）で上つ弓張（ハード）以上をクリア。",
        "　　　　　　　　最終面はFinalBのみ。",
    ),
    (
        "挑戦可能条件：スペルカードモードでスペルカードを120枚以上取得する",
        "　　　　　　　　全キャラ合計。",
    ),
    ("挑戦可能条件：通常モードFinalBを６キャラ以上クリアする。", "　　　　　　　　"),
    ("挑戦可能条件：望月（エキストラ）を３キャラ以上クリアする。", " "),
    (
        "挑戦可能条件：スペルカードモードでラストスペルを30枚以上取得する。",
        "　　　　　　　　全キャラ合計。",
    ),
    (
        "挑戦可能条件：待宵（ルナティック）をクリアする。",
        "　　　　　　　　コンティニュー可、使用キャラ不問。",
    ),
    (
        "挑戦可能条件：これ以外のラストワードを全て一度見る。",
        "　　　　　　　　取得の必要はない。",
    ),
)

__all__ = [
    "CARD_DIFFICULTY_LETTERS",
    "LAST_SPELL_CARDS",
    "LAST_WORD_COMMENTS",
    "LAST_WORD_STAGE_MAP",
    "SPELLCARDS_PER_DIFFICULTY",
    "SPELLCARD_DIFFICULTY_LETTERS",
    "SPELLCARD_LAST_WORD_START",
    "SPELL_STAGE_COUNT",
    "SPELL_STAGE_EXTRA",
    "SPELL_STAGE_LAST_WORD",
    "STAGE_SPELLCARD_CARDS",
    "spellcard_difficulty",
]
