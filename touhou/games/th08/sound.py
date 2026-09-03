"""th08(东方永夜抄)的 SE 索引表 —— 对照 th08-ref SoundPlayer.cpp/:hpp。

- 索引 = SoundIdx 枚举(SoundPlayer.hpp:17-65), 46 槽(0..45);
- 槽 → wav/音量 = g_SoundBufferIdxVol[46] (SoundPlayer.cpp:20-29),
  第三列是并发副本数(本实现不接, pygame mixer 自行多开);
- wav 名 = g_SFXList[36] (SoundPlayer.cpp:30-37);
- 音量是 DirectSound 百分之一分贝(0=满音量), 换算在 engine/view/
  sound_player.py 的 _db_to_gain。

th07 对应表在 schema/sound.py(SE/SE_FILES/SE_VOLUMES, 38 槽); 两表
相互独立, 别串。模块不含 pygame, view/逻辑两侧都可用。
"""

from __future__ import annotations

from enum import IntEnum


# SoundIdx (SoundPlayer.hpp:17-65) 的命名项; 未命名槽位照 C 枚举名 SOUND_n。
class SE(IntEnum):
    SOUND_SHOOT = 0  # se_plst00 (自机射击)
    SOUND_1 = 1  # se_plst00 (小声)
    SOUND_2 = 2  # se_enep00 (敌死亡)
    SOUND_3 = 3  # se_enep00 (小声)
    SOUND_PICHUN = 4  # se_pldead00 (玩家死亡)
    SOUND_5 = 5  # se_power0
    SOUND_6 = 6  # se_power1
    SOUND_7 = 7  # se_tan00
    SOUND_8 = 8  # se_tan01
    SOUND_9 = 9  # se_tan02
    SOUND_SELECT = 10  # se_ok00 (菜单确认)
    SOUND_BACK = 11  # se_cancel00 (菜单取消)
    SOUND_MOVE_MENU = 12  # se_select00 (菜单移动)
    SOUND_D = 13  # se_gun00
    SOUND_E = 14  # se_cat00 (符卡宣告/炸弹横幅)
    SOUND_F = 15  # se_tan00
    SOUND_10 = 16  # se_lazer00
    SOUND_11 = 17  # se_lazer01
    SOUND_TOTAL_BOSS_DEATH = 18  # se_enep01
    SOUND_13 = 19  # se_nep00
    SOUND_DAMAGE = 20  # se_damage00 (敌受击)
    SOUND_ITEM = 21  # se_item00 (吃道具)
    SOUND_16 = 22  # se_tan00 (大声)
    SOUND_17 = 23  # se_tan01
    SOUND_18 = 24  # se_tan02
    SOUND_19 = 25  # se_kira00
    SOUND_1A = 26  # se_kira01
    SOUND_1B = 27  # se_kira02
    SOUND_1UP = 28  # se_extend (奖残)
    SOUND_TIMEOUT = 29  # se_timeout (符卡倒计时警告)
    SOUND_GRAZE = 30  # se_graze (擦弹)
    SOUND_POWERUP = 31  # se_powerup (火力升档)
    SOUND_20 = 32  # se_graze (小声)
    SOUND_21 = 33  # se_kira00 (满音量)
    SOUND_PAUSE = 34  # se_pause (暂停菜单)
    SOUND_SPELL_CAPTURE = 35  # se_cardget (符卡捕获)
    SOUND_FAMILIAR_SPAWN = 36  # se_option (使魔生成)
    SOUND_DAMAGE_LOW_HEALTH = 37  # se_damage01 (低血量受击)
    SOUND_TIMEOUT_2 = 38  # se_timeout2
    SOUND_FAMILIAR_UNHIDE = 39  # se_opshow (使魔现身)
    SOUND_FAMILIAR_HIDE = 40  # se_ophide (使魔隐去)
    SOUND_INVALID_ACTION = 41  # se_invalid
    SOUND_2A = 42  # se_slash
    SOUND_2B = 43  # se_slash (小声)
    SOUND_2C = 44  # se_item01
    SOUND_2D = 45  # se_ok00 (满音量)


#: SE 表槽数 (g_SoundBufferIdxVol[46], SoundPlayer.cpp:20)
SE_TABLE_SIZE = 46

# g_SFXList[36] (SoundPlayer.cpp:30-37): 音效 buffer 索引 → wav 文件名
_SFX_LIST = (
    "se_plst00.wav",
    "se_enep00.wav",
    "se_pldead00.wav",
    "se_power0.wav",
    "se_power1.wav",
    "se_tan00.wav",
    "se_tan01.wav",
    "se_tan02.wav",
    "se_ok00.wav",
    "se_cancel00.wav",
    "se_select00.wav",
    "se_gun00.wav",
    "se_cat00.wav",
    "se_lazer00.wav",
    "se_lazer01.wav",
    "se_enep01.wav",
    "se_nep00.wav",
    "se_damage00.wav",
    "se_item00.wav",
    "se_kira00.wav",
    "se_kira01.wav",
    "se_kira02.wav",
    "se_extend.wav",
    "se_timeout.wav",
    "se_graze.wav",
    "se_powerup.wav",
    "se_pause.wav",
    "se_cardget.wav",
    "se_option.wav",
    "se_damage01.wav",
    "se_timeout2.wav",
    "se_opshow.wav",
    "se_ophide.wav",
    "se_invalid.wav",
    "se_slash.wav",
    "se_item01.wav",
)

# g_SoundBufferIdxVol[46] (SoundPlayer.cpp:20-29): (bufferIdx, 音量/百分贝, 并发)
_BUFFER_IDX_VOL = (
    (0, -1900, 0),
    (0, -2100, 0),
    (1, -1200, 5),
    (1, -1500, 5),
    (2, -1100, 100),
    (3, -700, 100),
    (4, -700, 100),
    (5, -1900, 50),
    (6, -2200, 50),
    (7, -2400, 50),
    (8, -1100, 100),
    (9, -1100, 100),
    (10, -1500, 10),
    (11, -1500, 10),
    (12, -1000, 100),
    (5, -1100, 50),
    (13, -1300, 50),
    (14, -1400, 50),
    (15, -900, 100),
    (16, -400, 100),
    (17, -880, 0),
    (18, -1500, 0),
    (5, -300, 20),
    (6, -1800, 20),
    (7, -1800, 20),
    (19, -1100, 50),
    (20, -1300, 50),
    (21, -1500, 50),
    (22, -500, 140),
    (23, -500, 100),
    (24, -1100, 20),
    (25, -800, 90),
    (24, -1200, 20),
    (19, -500, 50),
    (26, -800, 100),
    (27, -800, 100),
    (28, -800, 100),
    (29, -700, 0),
    (30, -300, 100),
    (31, -800, 100),
    (32, -800, 100),
    (33, -200, 100),
    (34, 0, 100),
    (34, -600, 100),
    (35, -800, 0),
    (8, -100, 100),
)

# SE → wav 文件名 / 音量(百分之一分贝, DirectSound 语义)
SE_FILES = {SE(i): _SFX_LIST[b] for i, (b, _v, _c) in enumerate(_BUFFER_IDX_VOL)}
SE_VOLUMES = {SE(i): v for i, (_b, v, _c) in enumerate(_BUFFER_IDX_VOL)}

__all__ = ["SE", "SE_FILES", "SE_TABLE_SIZE", "SE_VOLUMES"]
