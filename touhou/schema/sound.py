"""音效索引表与发声队列 —— 移植自 SoundPlayer.cpp/SoundPlayer.hpp。

SE 枚举值 = C++ PlaySoundByIdx 的 idx(SoundIdx 枚举), 即
SOUND_BUFFER_IDX_VOL[38] 的下标; 每项再经 bufferIdx 映射到 g_SFXList[30]
的 se_*.wav (SoundPlayer.cpp:10-77)。音量是 DirectSound 百分之一分贝
(0=满音量), 播放层自行换算。

节流照抄 PlaySoundByIdx (SoundPlayer.cpp:597-623): 每帧一个 5 槽队列,
同帧同 idx 去重, 满 5 个直接丢弃; ProcessQueues 每帧播完清空(本模块
take() 对应)。
"""

from __future__ import annotations

from enum import IntEnum


# C SoundIdx (SoundPlayer.hpp:20-45) + 未命名槽位按 SOUND_n 补齐 0..37。
# 注释为实际 wav(经 SOUND_BUFFER_IDX_VOL.bufferIdx 解析)。
class SE(IntEnum):
    SOUND_0 = 0  # se_plst00 (自机射击; 菜单 tick 也用它)
    SOUND_1 = 1  # se_plst00 (小声)
    SOUND_2 = 2  # se_enep00 (敌死亡)
    SOUND_3 = 3  # se_enep00 (小声)
    PICHUN = 4  # se_pldead00 (玩家死亡)
    BOMB_SAKUYA_A = 5  # se_power0
    BOMB_REIMARI = 6  # se_power1 (灵梦B/魔理沙A 非 focus 炸弹)
    BOMB_MARISA_A_FOCUS = 7  # se_tan00 (魔理沙A focus 炸弹; 默认敌弹发弹音)
    SOUND_8 = 8  # se_tan01
    SOUND_9 = 9  # se_tan02
    SELECT = 10  # se_ok00
    BACK = 11  # se_cancel00
    MOVE_MENU = 12  # se_select00
    BOMB_REIMU_A = 13  # se_gun00 (灵梦A 炸弹)
    BOMB = 14  # se_cat00 (符卡宣告/炸弹横幅, Gui::ShowSpellcard)
    ENEMY_SPELLCARD_END = 15  # se_tan00 (符卡结束, EclManager::EndSpellcard)
    SOUND_16 = 16  # se_lazer00
    SOUND_17 = 17  # se_lazer01
    SOUND_18 = 18  # se_enep01
    BOMB_SAKUMARI = 19  # se_nep00 (魔理沙B/咲夜B 炸弹)
    SOUND_20 = 20  # se_damage00 (敌受击)
    SOUND_21 = 21  # se_item00 (吃道具, 每帧至多一次)
    SOUND_22 = 22  # se_tan00 (大声)
    SOUND_23 = 23  # se_tan01
    SOUND_24 = 24  # se_tan02
    SOUND_25 = 25  # se_kira00 (星弹等发弹, bulletProps.soundOverride)
    SOUND_26 = 26  # se_kira01
    SOUND_27 = 27  # se_kira02
    EXTEND = 28  # se_extend (奖残, GameManager::ExtendFromPoints)
    SOUND_29 = 29  # se_timeout (符卡倒计时 <10 秒警告)
    GRAZE = 30  # se_graze (擦弹, Player.cpp:1210)
    POWERUP = 31  # se_powerup (火力升档/满火力)
    BORDER_ACTIVATE = 32  # se_border (结界激活)
    BORDER_BREAK = 33  # se_bonus (结界破, 含自然破)
    SOUND_34 = 34  # se_graze (小声)
    SOUND_35 = 35  # se_kira00 (满音量)
    BORDER_ACTIVATE2 = 36  # se_bonus2 (结界激活叠音)
    SOUND_37 = 37  # se_pause (暂停菜单)


# g_SFXList[30] (SoundPlayer.cpp:14-77): 音效 buffer 索引 → wav 文件名
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
    "se_border.wav",
    "se_bonus.wav",
    "se_bonus2.wav",
    "se_pause.wav",
)

# SOUND_BUFFER_IDX_VOL[38] (SoundPlayer.cpp:10-11): (bufferIdx, 音量/百分贝)
_BUFFER_IDX_VOL = (
    (0, -2000),
    (0, -2500),
    (1, -1200),
    (1, -1500),
    (2, -1000),
    (3, -400),
    (4, -400),
    (5, -1500),
    (6, -1700),
    (7, -1900),
    (8, -1000),
    (9, -1000),
    (10, -1700),
    (11, -1200),
    (12, -900),
    (5, -1500),
    (13, -900),
    (14, -900),
    (15, -900),
    (16, -200),
    (17, -1400),
    (18, -1300),
    (5, -100),
    (6, -1800),
    (7, -1800),
    (19, -800),
    (20, -1000),
    (21, -1300),
    (22, -300),
    (23, -900),
    (24, -900),
    (25, -500),
    (26, -300),
    (27, -300),
    (24, -300),
    (19, 0),
    (28, -300),
    (29, -300),
)

# SE → wav 文件名 / 音量(百分之一分贝, DirectSound 语义)
SE_FILES = {SE(i): _SFX_LIST[b] for i, (b, _v) in enumerate(_BUFFER_IDX_VOL)}
SE_VOLUMES = {SE(i): v for i, (_b, v) in enumerate(_BUFFER_IDX_VOL)}

# PlaySoundByIdx 的每帧队列槽数 (SoundPlayer.cpp:565 soundQueue[5])
SOUND_QUEUE_SLOTS = 5


class SoundQueue:
    """每帧发声队列(PlaySoundByIdx + ProcessQueues 的语义)。

    引擎各模块在帧内 play(idx); 帧末由整合层 take() 取走并清空,
    交给播放层(C++ 主循环每帧 ProcessQueues 对应)。
    """

    def __init__(self) -> None:
        self._queue: list[int] = []

    def play(self, idx: int) -> None:
        """PlaySoundByIdx (SoundPlayer.cpp:597-623): 同帧同音去重, 满 5 槽丢弃。"""
        if idx < 0 or idx >= len(_BUFFER_IDX_VOL):
            return
        if idx in self._queue:
            return
        if len(self._queue) >= SOUND_QUEUE_SLOTS:
            return
        self._queue.append(idx)

    def take(self) -> list[int]:
        """取走本帧队列并清空(ProcessQueues 的消费侧)。"""
        q, self._queue = self._queue, []
        return q

    def __len__(self) -> int:
        return len(self._queue)
