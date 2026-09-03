"""th08 标题系菜单的纯逻辑(名单/光标/锁定跳过/初始光标/解锁判定) —— 无 pygame。

对照 th08-ref TitleScreen.cpp(行号相对其 src/):
- 主菜单 OnUpdateStartMenu(:280-643): 9 项(TITLE_MENU_ITEM_START_* 枚举
  :79-91), 光标 0-8; 移动 = MoveCursorVertical(9) 回绕(:3128-3160) +
  锁定项顺向跳过(:383-404 的 goto back 循环 —— Extra Start=1 /
  Spell Practice=2 未解锁时); BACK 光标直接跳到 Quit + SOUND_BACK(:601-608);
  确认按项分发(:499-598); 锁定项确认无效(:531-558 的解锁守卫落空时
  switch 直通, 无操作无音效); 初始光标 = ActualAddedCallback 按
  wantedState2 分支(:3682-3698)。
- 机体选择 OnUpdateCharacterSelect(:1601-1854, CharacterFlowTh08):
  menuLength = IsExtraUnlockedWithAllTeams ? 12 : 4(:1618/:1697);
  移动 = MoveCursorHorizontal 回绕(:3171-3205; 原作只认 LEFT/RIGHT,
  这里保留一期 UP/DOWN 同效的超集); Extra 变体跳过
  IsExtraUnlockedForCharacter 为 False 的机体(:1641-1648/:1722-1737);
  cursor >= menuLength → 0(:1698-1701)。
- 通关标记 DrawCompletionStatusText(TitleCompletionStatus.inl:12-67) =
  completion_mark_sprite。

菜单项标签原作是 title01.anm 贴图(无文本), 本模块的名单文本只用于
无资源环境的文字回退菜单与日志。
"""

from __future__ import annotations

import msgspec
from typing import TYPE_CHECKING

from ...th07.view.screens import MenuAction, MenuCursor
from ....engine.config import (
    KEYMAP_ACTIONS,
    LIVES_MAX,
    LIVES_MIN,
    SCALE_MAX,
    SCALE_MIN,
    VOLUME_MAX,
    VOLUME_MIN,
    GameConfig,
)
from ..progress import (
    STAGE_6A,
    STAGE_6B,
    is_extra_unlocked,
    is_spell_practice_unlocked,
    stage_cleared_with_retries,
    stage_cleared_without_retries,
)

if TYPE_CHECKING:
    from ....engine.score_store import ScoreStore

# 主菜单 9 项(TitleScreen.cpp:79-91 的枚举序)
TITLE_MENU_ITEMS = (
    "Start",
    "Extra Start",
    "Spell Practice",
    "Practice Start",
    "Replay",
    "Result",
    "Music Room",
    "Option",
    "Quit",
)

# 菜单项下标(同上枚举)
ITEM_START = 0
ITEM_EXTRA_START = 1
ITEM_SPELL_PRACTICE = 2
ITEM_PRACTICE_START = 3
ITEM_REPLAY = 4
ITEM_RESULT = 5
ITEM_MUSIC_ROOM = 6
ITEM_OPTION = 7
ITEM_QUIT = 8

# 底部帮助行 9 条(g_StartMenuHelpText, TitleScreen.cpp:187-191;
# 文本 = config/i18n.csv:129-137 的 TITLE_STARTMENU_HELPTEXT0-8 日文原文)
HELP_TEXTS = (
    "ゲームを開始します",
    "エキストラステージを開始します",
    "敵にスペルカード戦をお願いします",
    "ステージを選択し、練習を開始します",
    "リプレイを鑑賞できます",
    "過去のスコアやスペルカードの取得歴を見られます",
    "音楽を聴けます",
    "各種設定できます",
    "いろいろと終了します",
)

# 初始光标规则(ActualAddedCallback 的 wantedState2 分支, :3682-3698)
CURSOR_ON_BOOT = 0  # 启动(默认分支 :3695-3696)
CURSOR_FROM_GAME = 1  # 游戏中途 Quit to Title 回来(:3684-3687; 原作条件
# 实为 difficulty>=EXTRA ? 1 : 0, 这里按 A 期规格简化为退出即 1)
CURSOR_FROM_RESULT = 5  # 结算回来(ResultScreen → Result 项, :3689-3690)

# 通关标记 sprite 号(TitleCompletionStatus.inl:24-52; 贴图内容实测:
# 145=FinalB 決戦可能 / 146=FinalA,B クリア / 147=Final 選択可能 /
# 148=FinalB クリア; 149=FinalA クリア 原作未使用)
MARK_FINALB_AVAILABLE = 145
MARK_ALL_CLEARED = 146
MARK_FINAL_SELECTABLE = 147
MARK_FINALB_CLEARED = 148


def completion_mark_sprite(
    store: ScoreStore, character: int, difficulty: int
) -> int | None:
    """机体选择的通关标记 → title01.anm sprite 号, 无标记 → None。

    DrawCompletionStatusText(TitleCompletionStatus.inl:12-67)的四档,
    判定轴 = 当前光标机体 + cfg.defaultDifficulty(难度选择写入, :1512):
    1. 当前难度 6B 无续关 + 6A 有续关 → 146(FinalA,B クリア);
    2. 当前难度 6B 无续关 → 148(FinalB クリア);
    3. 任一本篇难度 6B 无续关 或 单人机体(cursor>3) → 147(Final 選択可能);
    4. 任一本篇难度 6A 有续关 → 145(FinalB 決戦可能)。
    注意 1/2 只看当前难度, 3/4 才扫 EASY..LUNATIC(0..3); c_stage 轴
    6A=6/6B=7(progress.STAGE_6A/6B)。只在主 CharacterSelect 画
    (OnDraw :3594-3596), Extra/Practice 变体不画(调用方不传)。
    """
    if stage_cleared_without_retries(store, character, difficulty, STAGE_6B):
        if stage_cleared_with_retries(store, character, difficulty, STAGE_6A):
            return MARK_ALL_CLEARED
        return MARK_FINALB_CLEARED
    if character > 3 or any(
        stage_cleared_without_retries(store, character, d, STAGE_6B) for d in range(4)
    ):
        return MARK_FINAL_SELECTABLE
    if any(stage_cleared_with_retries(store, character, d, STAGE_6A) for d in range(4)):
        return MARK_FINALB_AVAILABLE
    return None


def unlock_flags(store: ScoreStore) -> tuple[bool, bool]:
    """score.json → (Extra 解锁, Spell Practice 解锁)。

    原作语义(B 期起, 实现见 games/th08/progress.py):
    IsExtraUnlocked (GameManager.cpp:1337-1343) = 4 组队伍任一 CLRD
    WithoutRetries 带 EXTRA_UNLOCKED_FLAG (bit14, GameManager.hpp:14);
    IsSpellPracticeUnlocked (:1356-1362) = 4 组任一 WithRetries 带
    SPELL_PRACTICE_UNLOCKED_FLAG (bit15) —— 菜单置灰用的就是这两个全局
    判定 (ActualAddedCallback 的 extraUnlocked/spellPracticeUnlocked,
    TitleScreen.cpp:3651-3674)。
    """
    return is_extra_unlocked(store), is_spell_practice_unlocked(store)


class TitleFlowTh08(msgspec.Struct):
    """th08 标题主菜单的选择状态(OnUpdateStartMenu 的 Ready 态对应物)。

    handle 返回 None(继续)或选择结果 dict:
    - {"action": "select_difficulty"} Start → 选难度(:501-514);
    - {"action": "extra_start"} Extra Start(已解锁)→ Extra 流(:531-544);
    - {"action": "quit"} Quit(:589-597);
    - {"action": 未实装项名} Spell Practice/Practice/Replay/Result/
      Music Room/Option —— A 期维持一期口径(调用方给提示不跳转);
    - 锁定项(Extra/Spell Practice 未解锁)确认 → None(无操作)。
    """

    cursor: MenuCursor = msgspec.field(
        default_factory=lambda: MenuCursor(list(TITLE_MENU_ITEMS), index=CURSOR_ON_BOOT)
    )
    extra_unlocked: bool = False
    spell_practice_unlocked: bool = False

    def locked(self, index: int) -> bool:
        """该项是否锁定(置灰 + 光标跳过): Extra Start/Spell Practice 未解锁
        (:356-365 置灰 color=0xff404040, :388-404 跳过)。"""
        if index == ITEM_EXTRA_START:
            return not self.extra_unlocked
        if index == ITEM_SPELL_PRACTICE:
            return not self.spell_practice_unlocked
        return False

    @property
    def help_text(self) -> str:
        """当前光标项的底部帮助行(g_StartMenuHelpText[cursor], :370-371)。"""
        return HELP_TEXTS[self.cursor.index]

    def handle(self, action: MenuAction) -> dict | None:
        """处理一次菜单按键。返回非空 dict = 当前选择(由调用方执行)。"""
        if action == MenuAction.UP:
            self._move(-1)
        elif action == MenuAction.DOWN:
            self._move(1)
        elif action == MenuAction.BACK:
            self.cursor.index = ITEM_QUIT  # :601-608 光标跳 Quit
        elif action == MenuAction.CONFIRM:
            return self._confirm()
        return None

    def _move(self, delta: int) -> None:
        """回绕移动一格 + 锁定项顺向再跳(MoveCursorVertical :3128-3160 的
        回绕, 然后 :383-404 的 goto back 循环同向继续走; 锁定最多 2 项,
        步数上界只是兜底防死循环)。"""
        self.cursor.move(delta)
        for _ in range(len(self.cursor.items)):
            if not self.locked(self.cursor.index):
                break
            self.cursor.move(delta)

    def _confirm(self) -> dict | None:
        """确认分发(:499-598); 锁定项确认无效(:531-558 守卫落空 = 直通)。"""
        idx = self.cursor.index
        if idx == ITEM_START:
            return {"action": "select_difficulty"}
        if idx == ITEM_EXTRA_START:
            return {"action": "extra_start"} if self.extra_unlocked else None
        if idx == ITEM_SPELL_PRACTICE:
            return (
                {"action": "spell_practice"} if self.spell_practice_unlocked else None
            )
        if idx == ITEM_PRACTICE_START:
            return {"action": "practice"}
        if idx == ITEM_REPLAY:
            return {"action": "replay"}
        if idx == ITEM_RESULT:
            return {"action": "result"}
        if idx == ITEM_MUSIC_ROOM:
            return {"action": "music_room"}
        if idx == ITEM_OPTION:
            return {"action": "option"}
        if idx == ITEM_QUIT:
            return {"action": "quit"}
        return None


class CharacterFlowTh08(msgspec.Struct):
    """th08 机体选择的选择状态(OnUpdateCharacterSelect :1601-1854 的对应物)。

    cursor.items 即 menuLength 规则(:1618/:1697)的物化: 调用方按
    is_extra_unlocked_with_all_teams 给 12 项(4 组+8 单人)或 4 项(只有组队)
    —— items 是名单前缀切片, cursor.index 始终 = 绝对机体下标(0..11)。
    """

    cursor: MenuCursor
    extra: bool = False  # CharacterSelectExtra 变体(:1639-1648)
    difficulty: int = 1  # 通关标记/难度角标的难度(= 难度选择写入的
    # cfg.defaultDifficulty; Extra 流 = 4, :1516)
    extra_unlocked: list[bool] = msgspec.field(default_factory=list)
    # 按绝对机体下标的 is_extra_unlocked_for_character 快照(仅 extra 流用)

    def locked(self, index: int) -> bool:
        """Extra 流的锁定机体(光标跳过; 单人 4..11 恒 True 不会被锁,
        GameManager.cpp:1329)。"""
        return (
            self.extra
            and index < len(self.extra_unlocked)
            and not self.extra_unlocked[index]
        )

    def move(self, delta: int) -> None:
        """回绕移动一格(MoveCursorHorizontal :3171-3205; 一期的 UP/DOWN
        同效是超集), Extra 流锁定机顺向再跳(:1722-1737 的 while; 步数上界
        兜底防全锁死循环)。"""
        self.cursor.move(delta)
        for _ in range(len(self.cursor.items)):
            if not self.locked(self.cursor.index):
                break
            self.cursor.move(delta)

    def clamp_cursor(self) -> None:
        """:1698-1701: cursor >= menuLength → 0(menuLength 随解锁态变化时)。"""
        if self.cursor.index >= len(self.cursor.items):
            self.cursor.index = 0

    def skip_locked_forward(self) -> None:
        """进 Extra 机体选择的初始顺向跳过(:1641-1648 的 while 循环)。"""
        n = len(self.cursor.items)
        for _ in range(n):
            if not self.locked(self.cursor.index):
                break
            self.cursor.index = (self.cursor.index + 1) % n


# ---- Option 画面(OnUpdateOptions, TitleScreen.cpp:644-1153) ----
# 10 项 = TITLE_MENU_ITEM_OPTION_* 枚举序(:93-106); 名单文本只用于日志与
# 无资源环境的文字回退菜单, 原作条目是 title01.anm 贴图(option_view.py)。
OPTION_ITEMS = (
    "Player",
    "Graphic",
    "BGM",
    "Vol",
    "S.E.Vol",
    "Mode",
    "SlowMode",
    "Reset",
    "KeyConfig",
    "Quit",
)
# 行下标(:93-106)
OPTION_ROW_PLAYER = 0
OPTION_ROW_GRAPHIC = 1
OPTION_ROW_BGM = 2
OPTION_ROW_VOL = 3
OPTION_ROW_SE_VOL = 4
OPTION_ROW_MODE = 5
OPTION_ROW_SLOWMODE = 6
OPTION_ROW_RESET = 7
OPTION_ROW_KEYCONFIG = 8
OPTION_ROW_EXIT = 9

# 底部帮助行(g_OptionsHelpText, :193-197; 文本 = config/i18n.csv:138-147 的
# TITLE_OPTIONS_HELPTEXT0-9 日文原文)
OPTION_HELP_TEXTS = (
    "プレイヤーの初期数を変更します。（初期設定　３）",
    "画面の色数を変更します。３２ＢＩＴだと最も綺麗に表示されます。",
    "ＢＧＭの再生方法を変更します。（初期設定　ＷＡＶ）",
    "ＢＧＭの音量を調整します",
    "効果音の音量を調整します",
    "ウィンドウかフルスクリーンか選択します",
    "弾が多い場面でわざと処理落ちさせます(スコア、リプレイ記録不可)",
    "全て初期設定にします",
    "パッド操作のボタン配置を変更します",
    "おいそれと終了します",
)

_OPTION_VOLUME_STEP = 4  # 音量 ±4 步进(:906/:914/:929/:937)
_OPTION_HOLD_ACCEL = 30  # 按住超 30 帧后每帧 ±1(:947-988)
_OPTION_IDLE_TIMEOUT = 3600  # 3600 帧(60 秒)无输入自动退回主菜单(:1086-1092)

# 残机高档位锁定(:699-707 显示层锁 vms[25]/vms[26] + :826-844/:997-1015 调值
# 上限 i=7 按 attemptsTotal<30/<60 递减): attemptsTotal(= plst.play_count)
# < 30 → 上限 5 架; < 60 → 上限 6 架; 否则 7 架
_LIVES_UNLOCK_6 = 30  # attemptsTotal 达到 30 解锁 6 架档
_LIVES_UNLOCK_7 = 60  # 达到 60 解锁 7 架档

# Graphic(16bit 色)/SlowMode(低速模式)在我们引擎无对应物(渲染位深不可调、
# 固定 60fps 无慢速模式) —— 两行保留贴图但置灰, 光标跳过不可调(偏离原作:
# 原作两项可调, :846-853/:882-890)
_OPTION_LOCKED_ROWS = (OPTION_ROW_GRAPHIC, OPTION_ROW_SLOWMODE)


class OptionFlowTh08(msgspec.Struct):
    """th08 Option 画面的选择状态(OnUpdateOptions :644-1153 的对应物)。

    handle 返回 None(继续)或结果 dict(均带 "se" 键 = 原作该处的菜单音效,
    "select"/"ok"/"cancel"/None, 由调用方播放):
    - {"action": "changed", "item", "value"} 左右调值(:822-1065);
    - {"action": "reset"} Reset 恢复默认(:1095-1104);
    - {"action": "keyconfig"} 进 KeyConfig 子画面(:1105-1110);
    - {"action": "quit"} 退回主菜单(:1111-1126);
    - {"action": "back"} BACK 光标跳 Quit 行(:1130-1145)。
    play_count = attemptsTotal 快照(= store.plst["play_count"], 进标题时
    由调用方从 _title_store 喂入, 不每帧读盘)。
    """

    config: GameConfig = msgspec.field(default_factory=GameConfig)
    play_count: int = 0
    cursor: MenuCursor = msgspec.field(
        default_factory=lambda: MenuCursor(list(OPTION_ITEMS), index=0)
    )
    idle_frames: int = 0  # 无输入帧计数(:1081-1092 的 idleFrames)
    hold_left: int = 0  # LEFT 按住帧数(长按加速用, :948)
    hold_right: int = 0

    @property
    def max_lives(self) -> int:
        """残机调值上限(:828-835/:999-1006 的 i-1; 引擎上限 LIVES_MAX=7)。"""
        n = 5
        if self.play_count >= _LIVES_UNLOCK_6:
            n += 1
        if self.play_count >= _LIVES_UNLOCK_7:
            n += 1
        return min(LIVES_MAX, n)

    @property
    def is_volume_row(self) -> bool:
        """光标在音量行(Vol/S.E.Vol; :1070-1076 的定时 tick 音效判定用)。"""
        return self.cursor.index in (OPTION_ROW_VOL, OPTION_ROW_SE_VOL)

    @property
    def help_text(self) -> str:
        """当前光标行的底部帮助行(g_OptionsHelpText[cursor], :670/:686-688)。"""
        return OPTION_HELP_TEXTS[self.cursor.index]

    def locked(self, index: int) -> bool:
        """该行是否锁定(置灰 + 光标跳过): Graphic/SlowMode 无引擎对应物恒锁。"""
        return index in _OPTION_LOCKED_ROWS

    def handle(self, action: MenuAction) -> dict | None:
        """处理一次菜单按键(结果 dict 的 "se" 键见类 docstring)。"""
        if action == MenuAction.UP:
            self._move(-1)
        elif action == MenuAction.DOWN:
            self._move(1)
        elif action in (MenuAction.LEFT, MenuAction.RIGHT):
            # 新按下重置按住计数(长按加速从本次按下重新计 30 帧)
            self.hold_left = self.hold_right = 0
            return self._adjust(1 if action == MenuAction.RIGHT else -1)
        elif action == MenuAction.BACK:
            if self.cursor.index == OPTION_ROW_EXIT:
                return {"action": "quit", "se": "cancel"}  # :1132-1135
            self.cursor.index = OPTION_ROW_EXIT  # 光标跳 Quit(:1137-1141)
            return {"action": "back", "se": "cancel"}  # :1143 SOUND_BACK
        elif action == MenuAction.CONFIRM:
            return self._confirm()
        return None

    def _move(self, delta: int) -> None:
        """回绕移动一格 + 锁定行顺向再跳(MoveCursorVertical :3128-3160 +
        主菜单锁定项跳过的同款口径 :383-404)。"""
        self.cursor.move(delta)
        for _ in range(len(self.cursor.items)):
            if not self.locked(self.cursor.index):
                break
            self.cursor.move(delta)

    def _adjust(self, delta: int) -> dict | None:
        """左右调值(:822-1065): 音量 ±4 截断, 枚举回绕; 锁定行不可调。"""
        row = self.cursor.index
        cfg = self.config
        if row == OPTION_ROW_PLAYER:
            # :826-844/:997-1015: 在 [0, 上限] 内回绕(原作下限 0 = 1 架;
            # 我们引擎下限 LIVES_MIN=2, 偏离注明)
            v = cfg.initial_lives
            if delta > 0:
                v = LIVES_MIN if v >= self.max_lives else v + 1
            else:
                v = self.max_lives if v <= LIVES_MIN else v - 1
            cfg.initial_lives = v
            return {"action": "changed", "item": "Player", "value": v, "se": "select"}
        if row == OPTION_ROW_BGM:
            # 原作三态 OFF/WAV/MIDI 循环(:855-871/:1026-1038); 我们无 OFF,
            # wav/midi 互换(偏离注明)
            cfg.bgm_source = "midi" if cfg.bgm_source == "wav" else "wav"
            return {
                "action": "changed",
                "item": "BGM",
                "value": cfg.bgm_source,
                "se": "select",
            }
        if row == OPTION_ROW_VOL:
            cfg.bgm_volume = max(
                VOLUME_MIN, min(VOLUME_MAX, cfg.bgm_volume + delta * _OPTION_VOLUME_STEP)
            )
            # 音量步进原作无逐次音效(:901-945 只有 QueueSetVolumeCommand)
            return {
                "action": "changed",
                "item": "Vol",
                "value": cfg.bgm_volume,
                "se": None,
            }
        if row == OPTION_ROW_SE_VOL:
            cfg.se_volume = max(
                VOLUME_MIN, min(VOLUME_MAX, cfg.se_volume + delta * _OPTION_VOLUME_STEP)
            )
            return {
                "action": "changed",
                "item": "S.E.Vol",
                "value": cfg.se_volume,
                "se": None,
            }
        if row == OPTION_ROW_MODE:
            # 原作 Mode = 窗口/全屏切换且退出时检查需重启进程(:1118-1122);
            # 我们无全屏支持, 映射为 window_scale 1-3 即时 resize(偏离注明)
            lo, hi = SCALE_MIN, SCALE_MAX
            cfg.window_scale = lo + (cfg.window_scale - lo + delta) % (hi - lo + 1)
            return {
                "action": "changed",
                "item": "Mode",
                "value": cfg.window_scale,
                "se": "select",
            }
        # Graphic/SlowMode(锁定)/Reset/KeyConfig/Quit: 左右无效
        return None

    def _confirm(self) -> dict | None:
        """确认分发(:1091-1127): 只有 Reset/KeyConfig/Exit 响应。"""
        row = self.cursor.index
        if row == OPTION_ROW_RESET:
            # :1096-1100: lifeCount=2(= 显示 3 架)/musicMode=WAV/bombCount=3/
            # playSounds=1/slowMode=0 —— 音量与窗口原作不动, 我们也不动;
            # bombCount/playSounds/slowMode 无 config 对应物
            self.config.initial_lives = 3
            self.config.bgm_source = "wav"
            return {"action": "reset", "se": "ok"}  # :1102 SOUND_SELECT
        if row == OPTION_ROW_KEYCONFIG:
            return {"action": "keyconfig", "se": "ok"}  # :1108 SOUND_SELECT
        if row == OPTION_ROW_EXIT:
            return {"action": "quit", "se": "cancel"}  # :1115 SOUND_BACK
        return None

    def tick_held(self, left: bool, right: bool) -> dict | None:
        """长按加速(:947-988): 按住超 30 帧后每帧 ±1, 仅音量行响应。

        每帧由调用方喂当前按住状态; 返回 changed 结果(无 "se" —— 原作此处
        也只有 QueueSetVolumeCommand)。注: 原作首帧是 ±4 与 ±1 同帧叠加
        (净 ±5, :901-967 两块都跑), 这里按 §10 摘要取净 ±4 步进(偏离注明)。
        """
        self.hold_left = self.hold_left + 1 if left else 0
        self.hold_right = self.hold_right + 1 if right else 0
        delta = 0
        if self.hold_left >= _OPTION_HOLD_ACCEL:
            delta -= 1
        if self.hold_right >= _OPTION_HOLD_ACCEL:
            delta += 1
        if delta == 0:
            return None
        row = self.cursor.index
        cfg = self.config
        if row == OPTION_ROW_VOL:
            v = max(VOLUME_MIN, min(VOLUME_MAX, cfg.bgm_volume + delta))
            if v == cfg.bgm_volume:
                return None  # :953/:974 到顶/底不变(原作此时仍发 volume 命令, 忽略)
            cfg.bgm_volume = v
            return {"action": "changed", "item": "Vol", "value": v, "se": None}
        if row == OPTION_ROW_SE_VOL:
            v = max(VOLUME_MIN, min(VOLUME_MAX, cfg.se_volume + delta))
            if v == cfg.se_volume:
                return None
            cfg.se_volume = v
            return {"action": "changed", "item": "S.E.Vol", "value": v, "se": None}
        return None

    def tick_idle(self, had_input: bool) -> bool:
        """无输入计时(:1081-1092): 有任何输入(含按住)清零; 满 3600 帧
        返回 True(调用方退回主菜单, :1088 → exit_options)。"""
        if had_input:
            self.idle_frames = 0
        else:
            self.idle_frames += 1
        return self.idle_frames >= _OPTION_IDLE_TIMEOUT


# ---- KeyConfig 画面(OnUpdateKeyConfig, TitleScreen.cpp:1156-1402) ----
# 原作 12 项 = 9 手柄键(shot/bomb/focus/menu/up/down/left/right/skip,
# TITLE_MENU_ITEM_KEYCONFIG_* 枚举 :108-122)+ ShotSlow + Reset + Quit;
# 我们是键盘映射 config.keymap(KEYMAP_ACTIONS 8 动作; menu 动作由 Esc/Enter
# 固定承担, shotSlow 无对应物) —— 名单 = 8 动作 + Reset + Quit, 设备语义
# 对齐原作流程(偏离原作的两行在画面上置灰保留贴图, 见 option_view.py)。
KEYCONFIG_ITEMS = list(KEYMAP_ACTIONS) + ["reset", "quit"]

# 底部帮助行(g_KeyConfigHelpText, :201-206; 文本 = config/i18n.csv:148-159
# 的 TITLE_KEYCONFIG_HELPTEXT0-11 日文原文; 下标 = 原作 12 行行号)
KEYCONFIG_HELP_TEXTS = (
    "ショット、決定ボタンを設定します",
    "ボム、キャンセルボタンを設定します",
    "低速移動ボタンを設定します",
    "メッセージスキップボタンを設定します",
    "ポーズボタンを設定します",
    "上移動ボタンを設定します",
    "下移動ボタンを設定します",
    "左移動ボタンを設定します",
    "右移動ボタンを設定します",
    "ショット押しっぱなしで低速移動になるようにします",
    "初期設定に戻します",
    "おおよそ終了します",
)
# 我们的动作 → 原作 12 行下标(帮助行/贴图行映射; 跳过 4=Pause 与 9=ShotSlow)
KEYCONFIG_ROW_MAP = {
    "shoot": 0,
    "bomb": 1,
    "focus": 2,
    "skip": 3,
    "up": 5,
    "down": 6,
    "left": 7,
    "right": 8,
    "reset": 10,
    "quit": 11,
}

_KEYCONFIG_IDLE_TIMEOUT = 3600  # 无输入退回 Option(:1345-1347)


class KeyConfigFlowTh08(msgspec.Struct):
    """th08 KeyConfig 画面的选择状态(OnUpdateKeyConfig :1156-1402 的键盘化对应物)。

    原作是手柄 32 按钮即时扫描改键(:1258-1314, 光标所在行收到第一个新按钮
    即写入, g_LastKeyChanged 防同帧连吃两键 :1268/:1318); 我们走 th07 的
    键盘口径(一致性优先): 确认进入"按新键"捕获态(后端
    poll_input(capturing=True) 把下一个 KEYDOWN 喂给 capture()), 键盘事件
    天然无边沿重触发, 防抖等价(偏离注明)。

    handle 返回 None 或结果 dict("se" 键同 OptionFlowTh08):
    - {"action": "capture", "item"} 进入捕获态;
    - {"action": "changed", "item": "reset"} Reset 恢复默认(:1354-1360);
    - {"action": "quit"} 回 Option(:1361-1371);
    - {"action": "back"} BACK 光标跳 Quit 行(th07 口径, 原作 KeyConfig
      无 RETURNMENU 处理 —— 只能选 Quit 行或等 3600 帧超时退出)。
    capture() 收捕获键: Esc/X = 取消(th07 口径: Esc 固定承担暂停/返回,
    防锁死); 其余写入 set_keymap_primary 并即时生效落盘(th07 口径; 原作是
    进画面备份、Exit 才写回 cfg 落盘 :1179/:1368, 偏离注明)。
    """

    config: GameConfig = msgspec.field(default_factory=GameConfig)
    cursor: MenuCursor = msgspec.field(
        default_factory=lambda: MenuCursor(list(KEYCONFIG_ITEMS), index=0)
    )
    capturing: str | None = None  # 正在捕获按键的动作名(None=非捕获状态)
    idle_frames: int = 0

    @property
    def help_text(self) -> str:
        """当前光标行的底部帮助行(g_KeyConfigHelpText[原作行号], :1224-1228)。"""
        return KEYCONFIG_HELP_TEXTS[KEYCONFIG_ROW_MAP[self.cursor.current]]

    def handle(self, action: MenuAction) -> dict | None:
        """处理一次菜单按键(捕获态下按键全被捕获路径收走, 菜单动作无效)。"""
        if self.capturing is not None:
            return None
        if action == MenuAction.UP:
            self.cursor.move(-1)
        elif action == MenuAction.DOWN:
            self.cursor.move(1)
        elif action == MenuAction.BACK:
            if self.cursor.current == "quit":
                return {"action": "quit", "se": "cancel"}
            self.cursor.index = len(self.cursor) - 1  # 跳 Quit 行(th07 口径)
            return {"action": "back", "se": "cancel"}
        elif action == MenuAction.CONFIRM:
            item = self.cursor.current
            if item == "quit":
                return {"action": "quit", "se": "cancel"}  # :1363 SOUND_BACK
            if item == "reset":
                self.config.reset_keymap()  # :1358 恢复默认
                return {"action": "changed", "item": "reset", "se": "ok"}
            self.capturing = item
            return {"action": "capture", "item": item, "se": "ok"}
        return None

    def capture(self, key_name: str) -> dict:
        """捕获态收一个键名(pygame.key.name): Esc/X = 取消; 其余设为主键。"""
        action = self.capturing
        self.capturing = None
        if action is None:
            return {"action": "cancel", "item": None, "se": None}
        if key_name in ("escape", "x"):
            return {"action": "cancel", "item": action, "se": "cancel"}
        self.config.set_keymap_primary(action, key_name)
        return {"action": "changed", "item": action, "value": key_name, "se": "ok"}

    def tick_idle(self, had_input: bool) -> bool:
        """无输入计时(:1340-1347): 满 3600 帧返回 True(调用方退回 Option,
        原作超时走 exit_keyconfig = 写回映射 + 回 Option, :1361-1370)。"""
        if had_input:
            self.idle_frames = 0
        else:
            self.idle_frames += 1
        return self.idle_frames >= _KEYCONFIG_IDLE_TIMEOUT


__all__ = [
    "CURSOR_FROM_GAME",
    "CURSOR_FROM_RESULT",
    "CURSOR_ON_BOOT",
    "CharacterFlowTh08",
    "HELP_TEXTS",
    "KEYCONFIG_HELP_TEXTS",
    "KEYCONFIG_ITEMS",
    "KEYCONFIG_ROW_MAP",
    "KeyConfigFlowTh08",
    "MARK_ALL_CLEARED",
    "MARK_FINAL_SELECTABLE",
    "MARK_FINALB_AVAILABLE",
    "MARK_FINALB_CLEARED",
    "OPTION_HELP_TEXTS",
    "OPTION_ITEMS",
    "OPTION_ROW_EXIT",
    "OPTION_ROW_KEYCONFIG",
    "OptionFlowTh08",
    "TITLE_MENU_ITEMS",
    "TitleFlowTh08",
    "completion_mark_sprite",
    "unlock_flags",
]
