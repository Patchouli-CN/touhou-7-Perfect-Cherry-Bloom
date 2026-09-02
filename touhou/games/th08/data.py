"""th08(东方永夜抄)数值表/名单 —— 全作品数据集中于此, 注册进 registry。

收录(数值全部原样照抄反编译源码, 行号注释保留在各表头):
- CHARACTERS:        12 机体名单(0-3 双人组, 4-11 单人, ScoreDat.hpp:54-69)
- DIFFICULTIES:      难度名单(ScoreDat.hpp:44-52)
- DROP_TABLE:        小怪掉落 32 项循环表(EnemyManager.cpp:24-29)
- POWER_LEVELS / FULL_POWER: 火力档位阈值(ItemManager.cpp:17), 满火力 128
- CHARACTER_SHT:     机体 → .sht 文件名(Player.cpp:35-43)

GameData 现有字段表达不了的 th08 机制(时刻阈值表/妖率槽界/Last Spell/rank 表
GameManager.cpp:32-39/点道具初值 GameManagerSetup.cpp:149-161)本轮不填,
world 阶段再扩 GameData(msgspec Struct 可加带默认值字段)。
空字段与近似处理说明:
- spellcard_scores 留空: th08-ref 无符卡基础分值表(符卡分按剩余时间等动态算);
- bomb_params 留空: th08 炸弹为回调驱动(g_PlayerBombCallbacksByShotType,
  Player.cpp:79), 无 th07 式首帧参数表;
- stage_count=6 为近似: th08 实际 9 关含 4A/4B/6A/6B 分支(ScoreDat.hpp:71-85),
  分支按机体决定(GameManager.cpp:1483-1505), 精确分支语义留 world 阶段。
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
    ),
)
