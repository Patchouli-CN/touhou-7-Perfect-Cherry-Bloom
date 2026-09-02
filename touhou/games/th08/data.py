"""th08(东方永夜抄)数值表/名单 —— 全作品数据集中于此, 注册进 registry。

收录(数值全部原样照抄反编译源码, 行号注释保留在各表头):
- CHARACTERS:        12 机体名单(0-3 双人组, 4-11 单人, ScoreDat.hpp:54-69)
- DIFFICULTIES:      难度名单(ScoreDat.hpp:44-52)
- DROP_TABLE:        小怪掉落 32 项循环表(EnemyManager.cpp:24-29)
- POWER_LEVELS / FULL_POWER: 火力档位阈值(ItemManager.cpp:17), 满火力 128
- CHARACTER_SHT:     机体 → .sht 文件名(Player.cpp:35-43)
- TIME_ORB_THRESHOLDS: 时刻符点阈值表 g_TimeRequirementParams[9][4]
  (GameManager.cpp:42-52; 行 = C currentStage 0-8, 列 = 难度 E/N/H/L)
- YOUKAI_GAUGE_BOUNDS: 妖率槽界默认 6 槽(Player.cpp:1607-1612)
- RANK_TABLE:        动态难度 g_RankParams[6](GameManager.cpp:32-39)
- POINT_ITEM_VALUES: 难度 → 点道具初值(GameManagerSetup.cpp:149-161)
- STAGE_STD_FILES / STAGE_ECL_FILES: 关卡文件名表(Background.cpp:99-101/:113-115),
  下标 = C currentStage(0=1面 … 3=4A 4=4B 5=5面 6=6A 7=6B 8=EX)
- MSG_FILES:         对话文件名表 g_GuiMessagePaths(Gui.cpp:61-69),
  下标 = (C currentStage, shotType)

空字段与近似处理说明:
- spellcard_scores 留空: th08-ref 无符卡基础分值表(符卡分按剩余时间等动态算);
- bomb_params 留空: th08 炸弹为回调驱动(g_PlayerBombCallbacksByShotType,
  Player.cpp:79), 无 th07 式首帧参数表;
- stage_count=6 为近似: th08 实际 9 关含 4A/4B/6A/6B 分支(ScoreDat.hpp:71-85),
  分支按机体决定(GameManager.cpp:1483-1505), 精确分支语义留后续阶段。
"""

from __future__ import annotations

from ...registry import GameData, register_game_data

# ---- 名单(下标语义: shotType / difficulty) ----
# 机体名单(ScoreDat.hpp:54-69): 0=灵梦&紫 1=魔理沙&爱丽丝 2=咲夜&蕾米莉亚
# 3=妖梦&幽幽子 4-11=单人(灵梦/紫/魔理沙/爱丽丝/咲夜/蕾米莉亚/妖梦/幽幽子)
CHARACTERS = (
    "ReimuYukari",
    "MarisaAlice",
    "SakuyaRemilia",
    "YoumuYuyuko",
    "Reimu",
    "Yukari",
    "Marisa",
    "Alice",
    "Sakuya",
    "Remilia",
    "Youmu",
    "Yuyuko",
)
# 难度名单(ScoreDat.hpp:44-52; 0..4, th08 无 Phantasm)
DIFFICULTIES = ("Easy", "Normal", "Hard", "Lunatic", "Extra")
# Extra Start 后的关卡选择(th08 仅 Extra 一关, ScoreDat.hpp:71-85)
EXTRA_STAGES = ("Extra",)
STAGE_COUNT = 6  # 本篇面数(近似: 4A/4B/6A/6B 分支压成 6, 见模块 docstring)
PRACTICE_DIFFICULTY_COUNT = 4  # practice 难度页项数(E/N/H/L)

# 角色 -> .sht 文件名(非 focus / focus); 键 = shotType 0..11,
# 原样照抄 Player.cpp:35-43 的 g_Player1ShtFiles / g_Player2ShtFile
# (单人机体两个文件名相同: 非 focus 文件即 focus 文件)
CHARACTER_SHT: dict[int, tuple[str, str]] = {
    0: ("ply00a.sht", "ply00as.sht"),
    1: ("ply01a.sht", "ply01as.sht"),
    2: ("ply02a.sht", "ply02as.sht"),
    3: ("ply03a.sht", "ply03as.sht"),
    4: ("ply00a.sht", "ply00a.sht"),
    5: ("ply00as.sht", "ply00as.sht"),
    6: ("ply01a.sht", "ply01a.sht"),
    7: ("ply01as.sht", "ply01as.sht"),
    8: ("ply02a.sht", "ply02a.sht"),
    9: ("ply02as.sht", "ply02as.sht"),
    10: ("ply03a.sht", "ply03a.sht"),
    11: ("ply03as.sht", "ply03as.sht"),
}

# ---- 道具 / 火力 ----
# 小怪掉落 32 项循环表(itemDropType=-1 时每 3 掉 1 按此表,
# EnemyManager.cpp:24-29 的 g_EnemyDropSchedule; 0=小P 1=点)
DROP_TABLE = [
    0,
    0,
    1,
    0,
    1,
    0,
    0,
    0,
    1,
    1,
    0,
    0,
    1,
    1,
    1,
    0,
    1,
    0,
    1,
    0,
    1,
    0,
    1,
    0,
    1,
    0,
    0,
    1,
    1,
    1,
    0,
    0,
]

# 火力档位阈值(ItemManager.cpp:17 的 g_PowerUpThresholds)
POWER_LEVELS = [8, 24, 48, 80, 128, 999]
FULL_POWER = 128

# ---- th08 专属数值表 ----
# 时刻符点阈值表 g_TimeRequirementParams[9][4] (GameManager.cpp:42-52):
# 行 = C currentStage(0=1面 1=2面 2=3面 3=4A 4=4B 5=5面 6=6A 7=6B 8=EX),
# 列 = 难度 E/N/H/L; 0/9999 = 该关无 Last Spell 时刻符点判定
TIME_ORB_THRESHOLDS = (
    (2000, 2500, 2700, 3000),
    (6500, 7200, 7200, 7200),
    (7500, 8500, 8800, 8800),
    (9999, 9999, 9999, 9999),
    (7500, 8500, 8500, 8500),
    (9999, 9999, 9999, 9999),
    (0, 0, 0, 0),
    (0, 0, 0, 0),
    (0, 0, 0, 0),
)
# 妖率槽界默认 6 槽 (Player.cpp:1607-1612 的 g_PlayerGaugeBounds 初始化段):
# [0]人限(夹取下限) [1]妖限(夹取上限) [2]人特效阈 [3]妖特效阈 [4]人染色阈 [5]妖染色阈
YOUKAI_GAUGE_BOUNDS = (-10000, 10000, -8000, 8000, -2000, 2000)
# 动态难度表 g_RankParams[6] (GameManager.cpp:32-39): 难度 → (初始rank, min, max)
RANK_TABLE = (
    (10, 8, 16),  # Easy
    (10, 8, 16),  # Normal
    (8, 8, 12),  # Hard
    (8, 8, 12),  # Lunatic
    (16, 15, 16),  # Extra
    (16, 15, 16),  # (Phantasm 槽位, th08 无; 表照抄)
)
# 难度 → 点道具初值 pointItemValue (GameManagerSetup.cpp:149-161)
POINT_ITEM_VALUES = (60000, 100000, 200000, 300000, 300000)

# ---- 关卡资源文件名表(下标 = C currentStage 0-8: 1/2/3/4A/4B/5/6A/6B/EX) ----
# g_StageStdFiles (Background.cpp:99-101)
STAGE_STD_FILES = (
    "stage1.std",
    "stage2.std",
    "stage3.std",
    "stage4a.std",
    "stage4b.std",
    "stage5.std",
    "stage6.std",
    "stage7.std",
    "stage8.std",
)
# g_StageEclFiles (Background.cpp:113-115)
STAGE_ECL_FILES = (
    "ecldata1.ecl",
    "ecldata2.ecl",
    "ecldata3.ecl",
    "ecldata4a.ecl",
    "ecldata4b.ecl",
    "ecldata5.ecl",
    "ecldata6.ecl",
    "ecldata7.ecl",
    "ecldata8.ecl",
)
# 对话文件 g_GuiMessagePaths[currentStage][shotType] (Gui.cpp:61-69):
# 行 = C currentStage 0-8, 列 = shotType 0-11(双人组按队, 单人归属对应队;
# 4A/4B 按剧情对阵分 ab/ac/ba/bd/dm)
MSG_FILES = (
    ("msg1a.dat", "msg1b.dat", "msg1c.dat", "msg1d.dat", "msg1a.dat", "msg1a.dat",
     "msg1b.dat", "msg1b.dat", "msg1c.dat", "msg1c.dat", "msg1d.dat", "msg1d.dat"),
    ("msg2a.dat", "msg2b.dat", "msg2c.dat", "msg2d.dat", "msg2a.dat", "msg2a.dat",
     "msg2b.dat", "msg2b.dat", "msg2c.dat", "msg2c.dat", "msg2d.dat", "msg2d.dat"),
    ("msg3a.dat", "msg3b.dat", "msg3c.dat", "msg3d.dat", "msg3a.dat", "msg3a.dat",
     "msg3b.dat", "msg3b.dat", "msg3c.dat", "msg3c.dat", "msg3d.dat", "msg3d.dat"),
    ("msg4dm.dat", "msg4ab.dat", "msg4ac.dat", "msg4dm.dat", "msg4dm.dat", "msg4dm.dat",
     "msg4ab.dat", "msg4ab.dat", "msg4ac.dat", "msg4ac.dat", "msg4dm.dat", "msg4dm.dat"),
    ("msg4ba.dat", "msg4dm.dat", "msg4dm.dat", "msg4bd.dat", "msg4ba.dat", "msg4ba.dat",
     "msg4dm.dat", "msg4dm.dat", "msg4dm.dat", "msg4dm.dat", "msg4bd.dat", "msg4bd.dat"),
    ("msg5a.dat", "msg5b.dat", "msg5c.dat", "msg5d.dat", "msg5a.dat", "msg5a.dat",
     "msg5b.dat", "msg5b.dat", "msg5c.dat", "msg5c.dat", "msg5d.dat", "msg5d.dat"),
    ("msg6a.dat", "msg6b.dat", "msg6c.dat", "msg6d.dat", "msg6a.dat", "msg6a.dat",
     "msg6b.dat", "msg6b.dat", "msg6c.dat", "msg6c.dat", "msg6d.dat", "msg6d.dat"),
    ("msg7a.dat", "msg7b.dat", "msg7c.dat", "msg7d.dat", "msg7a.dat", "msg7a.dat",
     "msg7b.dat", "msg7b.dat", "msg7c.dat", "msg7c.dat", "msg7d.dat", "msg7d.dat"),
    ("msg8a.dat", "msg8b.dat", "msg8c.dat", "msg8d.dat", "msg8a.dat", "msg8a.dat",
     "msg8b.dat", "msg8b.dat", "msg8c.dat", "msg8c.dat", "msg8d.dat", "msg8d.dat"),
)

# ---- 注册(导入本模块即登记 th08 数值表; touhou/__init__ 保证导入) ----
TH08_DATA = register_game_data(
    "th08",
    GameData(
        characters=CHARACTERS,
        difficulties=DIFFICULTIES,
        extra_stages=EXTRA_STAGES,
        stage_count=STAGE_COUNT,
        practice_difficulty_count=PRACTICE_DIFFICULTY_COUNT,
        character_sht=dict(CHARACTER_SHT),
        # spellcard_scores / bomb_params / full_power_score_bonus:
        # th08 无对应静态表, 留 GameData 默认空值(见模块 docstring)
        drop_table=tuple(DROP_TABLE),
        power_levels=tuple(POWER_LEVELS),
        full_power=FULL_POWER,
        time_orb_thresholds=TIME_ORB_THRESHOLDS,
        youkai_gauge_bounds=YOUKAI_GAUGE_BOUNDS,
        rank_table=RANK_TABLE,
        point_item_values=POINT_ITEM_VALUES,
    ),
)
