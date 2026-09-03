"""标题/菜单场景 —— 纯逻辑层(不含渲染)。

用带键盘选择的场景状态机组织:
  Title(原版 8 项主菜单) → (Difficulty → Character → 开始游戏 | 退出)

名单/面数的默认来源是**注册表**里 th07 登记的数值表(``get_game("th07").data``,
延迟到本模块 import 时查询; registry 是叶子模块, 无循环 import; 引擎层不
import 作品包)。注册表缺失时回落到内置兜底常量(与 th07 表同值, 仅保证
模块可独立使用)。GameApp 实例级名单另经 GameSpec.data 参数化
(见 view/impl.py 的 game_data 参数)。
"""

from __future__ import annotations

import msgspec
from enum import IntEnum
from pathlib import Path

from ....paths import DEFAULT_DATA
from ....registry import get_game
from ....schema.archive import open_archive
from ....schema.musiccmt import parse_musiccmt
from ....engine.config import (
    LIVES_MIN,
    SCALE_MAX,
    SCALE_MIN,
    VOLUME_MAX,
    VOLUME_MIN,
    GameConfig,
)

# 内置兜底名单(与 games/th07/data.py 同值; 仅在注册表未登记 th07 时使用,
# 正常经 ``import touhou`` 链注册表必命中 —— touhou/__init__ 先登记 th07
# 数值表再 import api→view 链)
_FALLBACK_CHARACTERS = ("ReimuA", "ReimuB", "MarisaA", "MarisaB", "SakuyaA", "SakuyaB")
_FALLBACK_DIFFICULTIES = ("Easy", "Normal", "Hard", "Lunatic", "Extra", "Phantasm")
_FALLBACK_EXTRA_STAGES = ("Extra", "Phantasm")
_FALLBACK_STAGE_COUNT = 6
_FALLBACK_PRACTICE_DIFF_COUNT = 4


def _default_game_data():
    """默认名单来源: 注册表里 th07 登记的 GameData; 未登记返回 None(走兜底)。"""
    try:
        return get_game("th07").data
    except KeyError:  # NotRegisteredError(未注册 th07)
        return None


_gd = _default_game_data()

# 默认名单 = 注册表 th07 表(作品级覆盖走 GameApp(game_data=...))
DIFFICULTIES = (
    list(_gd.difficulties)
    if _gd is not None and _gd.difficulties
    else list(_FALLBACK_DIFFICULTIES)
)
CHARACTERS = (
    list(_gd.characters)
    if _gd is not None and _gd.characters
    else list(_FALLBACK_CHARACTERS)
)
# Extra Start 后的关卡选择(简化: 原版 Phantasm 需 Extra 通关后才会出现)
EXTRA_STAGES = (
    list(_gd.extra_stages)
    if _gd is not None and _gd.extra_stages
    else list(_FALLBACK_EXTRA_STAGES)
)
_TH07_PRACTICE_DIFF_COUNT = (
    _gd.practice_difficulty_count if _gd is not None else _FALLBACK_PRACTICE_DIFF_COUNT
)
_TH07_MAIN_DIFF_COUNT = (
    _gd.main_difficulty_count if _gd is not None else _FALLBACK_PRACTICE_DIFF_COUNT
)
_TH07_STAGE_COUNT = _gd.stage_count if _gd is not None else _FALLBACK_STAGE_COUNT

# 本篇 Start 的难度名单(不含 Extra/Phantasm —— 那是额外关卡, 走 Extra Start
# 流程; 光标若在全名单上回绕会选中不可见的第 5/6 项, 即 BUGS.md#1 的出界 bug)
MAIN_DIFFICULTIES = DIFFICULTIES[:_TH07_MAIN_DIFF_COUNT]

# 暂停面板菜单项(游戏内 Esc; Save Replay 见 engine/replay.py)。
# 纯菜单数据, 归纯逻辑层; 绘制在 option_view.render_pause。
PAUSE_ITEMS = ["Resume", "Retry", "Save Replay", "Quit to Title"]

# Retry/Quit to Title 的二次确认项(AsciiManager.cpp PauseMenu case 5-8:
# 确认子菜单 sprite[5]=Yes / sprite[6]=No, 进入时默认停在 No)。
# 确认态下只有 Yes/No —— 原版此处没有也不能 Save Replay。
PAUSE_CONFIRM_ITEMS = ["Yes", "No"]

# 结算画面 "Save Replay?" 确认项(ResultScreen.cpp HandleReplaySaveKeyboard
# state 11: cursor 0=Yes(默认) 1=No, MoveCursorHorizontally 左右切换)。
RESULT_SAVE_ITEMS = ["Yes", "No"]


def load_tracks(data_path=DEFAULT_DATA) -> list:
    """从 th07.dat 解 musiccmt.txt → Music Room 曲目表; 失败返回空表(容错)。"""
    try:
        arc = open_archive(Path(data_path), game="th07")
        return parse_musiccmt(arc.load("musiccmt.txt"))
    except Exception:
        return []


# 原版主菜单 8 项(MainMenu.cpp g_MainMenuStrings 对应的菜单项, 见 title01.anm 贴图)。
# index 0 / 7 必须保持 "开始游戏" / "退出"(测试依赖)。
MAIN_MENU_ITEMS = [
    "开始游戏",
    "Extra Start",
    "Practice Start",
    "Replay",
    "Player Data",
    "Music Room",
    "Option",
    "退出",
]


class MenuAction(IntEnum):
    NONE = 0
    UP = 1
    DOWN = 2
    CONFIRM = 3
    BACK = 4
    LEFT = 5  # Option 菜单左右调值
    RIGHT = 6
    SKIP = 7  # th08 Music Room 的 SKIP 键(淡出; TH_BUTTON_SKIP, Global.hpp:157)
    RESET = 8  # th08 Music Room 的 RESET 键(重播; 固定 R 键, Global.cpp:802)


class Screen(IntEnum):
    TITLE = 0
    MAIN_MENU = 1
    DIFFICULTY = 2
    CHARACTER = 3
    PLAYING = 4
    RESULT = 5
    EXTRA_LEVEL = 6  # Extra Start 后选 Extra/Phantasm(简化, 原版无此页)
    ENDING = 7  # 6 面通关后的结局画面
    OPTION = 8  # Option 设置页(MainMenu.cpp STATE_OPTIONS)
    PLAYER_DATA = 9  # Player Data(Result 画面, MainMenu.cpp:430 curState=5)
    PRACTICE_STAGE = 10  # Practice 选关(MainMenu.cpp STATE_SELECT_PRACTICE_STAGE)
    MUSIC_ROOM = 11  # Music Room(MusicRoom.cpp RegisterChain)
    REPLAY = 12  # Replay 选择(MainMenu.cpp STATE_SELECT_REPLAY)
    KEY_CONFIG = 13  # 键位设置(MainMenu.cpp STATE_KEY_CONFIG)


class MenuCursor(msgspec.Struct):
    """一套选项 + 当前光标。"""

    items: list[str]
    index: int = 0
    wrap: bool = True

    def move(self, delta: int) -> None:
        n = len(self.items)
        if n == 0:
            return
        if self.wrap:
            self.index = (self.index + delta) % n
        else:
            self.index = max(0, min(self.index + delta, n - 1))

    @property
    def current(self) -> str | None:
        return self.items[self.index] if self.items else None

    def __len__(self) -> int:
        return len(self.items)


class TitleFlow(msgspec.Struct):
    """驱动标题主菜单的选择状态。emit 出选择结果。

    对照 MainMenu.cpp OnUpdatePreInput:
    - Start → 选难度流; Extra Start → 选 Extra/Phantasm → 选机体(顺序与
      本篇"先难度后选人"一致, BUGS.md 增量#1; 简化: 不做通关解锁判定,
      原版 Extra 需通关解锁、Phantasm 需 Extra 通关);
    - Practice Start → practice 难度流(:384-399, g_GameManager.practice=1);
      Player Data → Result 画面(:430-433 case 4, curState=5 ResultScreen);
      Music Room → 音乐室(MusicRoom.cpp, :434-437 case 5);
      Replay → 录像选择(:418-421 case 3, STATE_SELECT_REPLAY);
    - Option → 设置页(OptionFlow); Quit → 退出。
    - 按取消(BACK)光标直接跳到最后一项 Quit(MainMenu.cpp:455-466)。
    """

    cursor: MenuCursor = msgspec.field(
        default_factory=lambda: MenuCursor(MAIN_MENU_ITEMS, index=0)
    )
    step: MenuCursor = msgspec.field(
        default_factory=lambda: MenuCursor(
            ["Normal", "Hard", "Lunatic", "Extra"], index=0
        )
    )

    def handle(self, action: MenuAction) -> "dict | None":
        """处理一次按键。返回非空 dict = 得出当前选择(由调用方决定是否开始)。"""
        if action == MenuAction.UP:
            self.cursor.move(-1)
        elif action == MenuAction.DOWN:
            self.cursor.move(1)
        elif action == MenuAction.BACK:
            self.cursor.index = len(self.cursor) - 1  # 跳到 Quit
        elif action == MenuAction.CONFIRM:
            item = self.cursor.current
            if item == "退出":
                return {"action": "quit"}
            if item == "开始游戏":
                return {"action": "select_difficulty"}
            if item == "Extra Start":
                return {"action": "extra_start"}
            if item == "Practice Start":
                return {"action": "practice"}
            if item == "Player Data":
                return {"action": "player_data"}
            if item == "Music Room":
                return {"action": "music_room"}
            if item == "Replay":
                return {"action": "replay"}
            if item == "Option":
                return {"action": "option"}
            return {"action": "unimplemented", "item": item}
        return None


# ---- Option 设置菜单(MainMenu.cpp OnUpdateOptionsMenu, :503-848) ----
# 原版 9 项见 engine/config.py docstring; 本期接 5 项 + KeyConfig + 退出
# (KeyConfig 贴图 = title01.anm entry1 sprite 30/31)。
OPTION_ITEMS = [
    "BGM 音量",
    "SE 音量",
    "音源",
    "窗口缩放",
    "初始残机",
    "Key Config",
    "退出",
]

_OPTION_VOLUME_STEP = 10

# 本篇 Option 残机调值上限保持原作 2-5(MainMenu.cpp lifeCount 0-4);
# 引擎 LIVES_MAX 已扩到 7(th08 Option 档位解锁用), 与本篇 UI 无关。
_OPTION_LIVES_MAX = 5


def _wrap(v: int, lo: int, hi: int, delta: int) -> int:
    """枚举值回绕(原版 lifeCount/musicMode 等的左右调值语义)。"""
    return lo + (v - lo + delta) % (hi - lo + 1)


class OptionFlow(msgspec.Struct):
    """Option 设置页的选择状态。emit 调值/退出结果, 由调用方实时应用+落盘。

    对照 OnUpdateOptionsMenu:
    - 上下移动光标(MoveCursorVertical);
    - 左右调值(:620-775): 音量 ±10 截断(原版无音量项, 截断同常识),
      音源/缩放/残机枚举回绕(原版 lifeCount/musicMode 等均回绕);
    - 按取消(BACK)光标跳到"退出"(:834-846), 已在"退出"上再按取消 = 退出;
    - 确认在"退出"(离开)与"Key Config"(进键位页, :803-808 case 7)上生效,
      其余项靠左右调值(同原版枚举项)。
    """

    config: "GameConfig" = msgspec.field(default_factory=GameConfig)
    cursor: MenuCursor = msgspec.field(
        default_factory=lambda: MenuCursor(OPTION_ITEMS, index=0)
    )

    def handle(self, action: MenuAction) -> "dict | None":
        """处理一次按键。changed = 值被改(调用方应用+落盘), quit = 离开本页。"""
        if action == MenuAction.UP:
            self.cursor.move(-1)
        elif action == MenuAction.DOWN:
            self.cursor.move(1)
        elif action in (MenuAction.LEFT, MenuAction.RIGHT):
            delta = 1 if action == MenuAction.RIGHT else -1
            return self._adjust(delta)
        elif action == MenuAction.BACK:
            if self.cursor.current == "退出":
                return {"action": "quit"}
            self.cursor.index = len(self.cursor) - 1  # 跳到"退出"
        elif action == MenuAction.CONFIRM:
            if self.cursor.current == "退出":
                return {"action": "quit"}
            if self.cursor.current == "Key Config":
                # → STATE_KEY_CONFIG (MainMenu.cpp:803-808 case 7)
                return {"action": "keyconfig"}
        return None

    def _adjust(self, delta: int) -> "dict | None":
        item = self.cursor.current
        cfg = self.config
        if item == "BGM 音量":
            cfg.bgm_volume = max(
                VOLUME_MIN,
                min(VOLUME_MAX, cfg.bgm_volume + delta * _OPTION_VOLUME_STEP),
            )
            value = cfg.bgm_volume
        elif item == "SE 音量":
            cfg.se_volume = max(
                VOLUME_MIN, min(VOLUME_MAX, cfg.se_volume + delta * _OPTION_VOLUME_STEP)
            )
            value = cfg.se_volume
        elif item == "音源":
            cfg.bgm_source = "midi" if cfg.bgm_source == "wav" else "wav"
            value = cfg.bgm_source
        elif item == "窗口缩放":
            cfg.window_scale = _wrap(cfg.window_scale, SCALE_MIN, SCALE_MAX, delta)
            value = cfg.window_scale
        elif item == "初始残机":
            cfg.initial_lives = _wrap(
                cfg.initial_lives, LIVES_MIN, _OPTION_LIVES_MAX, delta
            )
            value = cfg.initial_lives
        else:
            return None
        return {"action": "changed", "item": item, "value": value}


# ---- KeyConfig 键位设置(MainMenu.cpp OnUpdateKeyConfig, :891-1088) ----
# 原版改的是手柄按钮号(controlMapping), 本项目改 pygame 键盘键名。
# 条目 = 8 个动作(顺序同原版: shoot/bomb/focus/skip + 方向; 原版还有
# menu 键与 shotSlow 开关, 这里 menu 语义已由 Esc/Enter 固定承担, 不接)
# + 恢复默认(原版 cursor10: controlMapping = g_ControllerMapping) + 返回。
KEYCONFIG_ACTIONS = ["shoot", "bomb", "focus", "skip", "up", "down", "left", "right"]
KEYCONFIG_ITEMS = KEYCONFIG_ACTIONS + ["reset", "back"]
# 显示标签(英文, 避免字体依赖, 同 option_view 说明文字风格)
KEYCONFIG_LABELS = {
    "shoot": "Shot",
    "bomb": "Bomb",
    "focus": "Focus",
    "skip": "Skip Msg",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "reset": "Reset to Default",
    "back": "Back",
}


class KeyConfigFlow(msgspec.Struct):
    """KeyConfig 页的选择状态。上下选动作; 确认进入"按新键"捕获状态
    (capturing), 之后由调用方把下一个 KEYDOWN 的键名喂给 capture()。

    - capturing 中 handle() 不再响应菜单动作(按键全被捕获路径收走);
    - capture(): Esc/X = 取消(不改动); 其余键设为该动作主键(备用键保留);
    - "恢复默认"(reset) → config.reset_keymap(); "返回"(back) → quit。
    改键后的即时落盘/键表重建由调用方负责(收到 changed 时)。
    """

    config: "GameConfig" = msgspec.field(default_factory=GameConfig)
    cursor: MenuCursor = msgspec.field(
        default_factory=lambda: MenuCursor(KEYCONFIG_ITEMS, index=0)
    )
    capturing: str | None = None  # 正在捕获按键的动作名(None=非捕获状态)

    def handle(self, action: MenuAction) -> "dict | None":
        """处理一次菜单按键。capture=进入捕获, changed=键位被改, quit=离开。"""
        if self.capturing is not None:
            return None  # 捕获中: 按键走 capture(), 菜单动作无效
        if action == MenuAction.UP:
            self.cursor.move(-1)
        elif action == MenuAction.DOWN:
            self.cursor.move(1)
        elif action == MenuAction.BACK:
            if self.cursor.current == "back":
                return {"action": "quit"}
            self.cursor.index = len(self.cursor) - 1  # 跳到"返回"(同 OptionFlow)
        elif action == MenuAction.CONFIRM:
            item = self.cursor.current
            if item == "back":
                return {"action": "quit"}
            if item == "reset":
                self.config.reset_keymap()
                return {"action": "changed", "item": "reset"}
            self.capturing = item
            return {"action": "capture", "item": item}
        return None

    def capture(self, key_name: str) -> dict:
        """捕获状态收一个键名(pygame.key.name)。Esc/X = 取消(防锁死:
        Esc 固定承担暂停/返回, 不许当动作键); 其余设为主键。"""
        action = self.capturing
        self.capturing = None
        if action is None:
            return {"action": "cancel", "item": None}
        if key_name in ("escape", "x"):
            return {"action": "cancel", "item": action}
        self.config.set_keymap_primary(action, key_name)
        return {"action": "changed", "item": action, "value": key_name}


def build_character_cursor() -> MenuCursor:
    return MenuCursor(CHARACTERS, index=0)


def build_difficulty_cursor() -> MenuCursor:
    return MenuCursor(MAIN_DIFFICULTIES, index=1)  # 默认 Normal; 本篇 4 难度


def character_index(name: str) -> int:
    return CHARACTERS.index(name) if name in CHARACTERS else 0


def difficulty_index(name: str) -> int:
    return DIFFICULTIES.index(name) if name in DIFFICULTIES else 1


# ---- Player Data(Result 画面)导航 ----
# 原版 ResultScreen.cpp OnUpdate: 主菜单 6 难度纵列(MoveCursor, :827) → 进
# 难度页后左右切机体(MoveCursorHorizontally(6), :1012); 符卡页左右翻卡页
# (:1078) 上下切机体页(MoveCursor2(7), :1087); Esc 回主菜单(:854)。
# 本期简化: 单页三板块(分数榜/符卡/统计), ↑↓ 切难度(6), ←→ 切机体(6),
# CONFIRM 切板块, BACK 返回标题。
PLAYERDATA_SECTIONS = ["分数榜", "符卡", "统计"]


class PlayerDataFlow(msgspec.Struct):
    """Player Data 画面的翻页状态。emit quit(返回标题), 其余只改页。"""

    section: int = 0  # PLAYERDATA_SECTIONS 下标
    difficulty: int = 1  # 0..5, 默认 Normal(同 build_difficulty_cursor)
    character: int = 0  # 0..5

    def handle(self, action: MenuAction) -> "dict | None":
        if action == MenuAction.UP:
            self.difficulty = (self.difficulty - 1) % len(DIFFICULTIES)
        elif action == MenuAction.DOWN:
            self.difficulty = (self.difficulty + 1) % len(DIFFICULTIES)
        elif action == MenuAction.LEFT:
            self.character = (self.character - 1) % len(CHARACTERS)
        elif action == MenuAction.RIGHT:
            self.character = (self.character + 1) % len(CHARACTERS)
        elif action == MenuAction.CONFIRM:
            self.section = (self.section + 1) % len(PLAYERDATA_SECTIONS)
        elif action == MenuAction.BACK:
            return {"action": "quit"}
        return None


# ---- Practice Start(MainMenu.cpp practice 流) ----
# 流程: 难度(STATE_PRACTICE_SELECT_DIFFICULTY) → 机体 → 选关
# (STATE_SELECT_PRACTICE_STAGE, OnUpdateSelectPracticeStage :1859) → 进关。
# 原版 practice 难度页只有 4 项 E/N/H/L(:1210 numDifficulties=4),
# Extra/Phantasm 不可 practice。
PRACTICE_DIFFICULTIES = DIFFICULTIES[:_TH07_PRACTICE_DIFF_COUNT]
# 选关项(MainMenu.cpp:33 g_StagePracticeStrings "Stage1".."Stage6")
PRACTICE_STAGE_ITEMS = [f"Stage {i}" for i in range(1, _TH07_STAGE_COUNT + 1)]


def practice_max_stage(store, character: int, difficulty: int) -> int:
    """Practice 可选到第几面(MainMenu.cpp:1912-1926):
    clrd[机体].without_retries[难度] = 已到达的最大面数, <1 → 1, >=99 → 满
    (原版 unsigned 无 <0, ZUN bloat; v=0 时 MoveCursorVertical(0) 实际只能
    选 Stage1, 故下限 1)。超界输入按 1 处理。"""
    max_stage = len(PRACTICE_STAGE_ITEMS)
    try:
        v = store.clrd[int(character) % len(CHARACTERS)]["without_retries"][
            int(difficulty) % len(DIFFICULTIES)
        ]
    except (TypeError, IndexError, KeyError, AttributeError):
        return 1
    if v >= 99:
        return max_stage
    return max(1, min(max_stage, v))


# ---- Music Room(MusicRoom.cpp ProcessInput :44-141) ----
MUSIC_ROOM_VISIBLE = 10  # 原版一屏 10 首(OnDraw: listingOffset..+10)


class MusicRoomFlow(msgspec.Struct):
    """Music Room 选曲状态。emit play/stop/quit, 播放由调用方(SoundPlayer)做。

    对照 ProcessInput:
    - UP/DOWN 移动光标(回绕), 同步 listingOffset 保持光标在 10 行窗口内
      (:51-104);
    - CONFIRM = 播放光标曲(:106-113, PlayAudio); 简化: 在播放中的曲目上
      再按 CONFIRM = 停止(原版无停止, 只有换曲);
    - BACK = 退出 Music Room(:134-138, curState=1 回标题)。
    """

    tracks: list = msgspec.field(default_factory=list)  # list[TrackDescriptor]
    cursor: int = 0
    listing_offset: int = 0
    playing: int | None = None  # 正在播放的曲目下标(None=未播/已停)

    def handle(self, action: MenuAction) -> "dict | None":
        n = len(self.tracks)
        if n == 0:
            return {"action": "quit"} if action == MenuAction.BACK else None
        if action == MenuAction.UP:
            self.cursor -= 1
            if self.cursor < 0:
                self.cursor = n - 1
                self.listing_offset = max(0, n - MUSIC_ROOM_VISIBLE)
            elif self.listing_offset > self.cursor:
                self.listing_offset = self.cursor
        elif action == MenuAction.DOWN:
            self.cursor += 1
            if self.cursor >= n:
                self.cursor = 0
                self.listing_offset = 0
            elif self.listing_offset <= self.cursor - MUSIC_ROOM_VISIBLE:
                self.listing_offset = self.cursor - MUSIC_ROOM_VISIBLE + 1
        elif action == MenuAction.CONFIRM:
            if self.playing == self.cursor:
                self.playing = None
                return {"action": "stop"}
            self.playing = self.cursor
            return {"action": "play", "index": self.cursor}
        elif action == MenuAction.BACK:
            return {"action": "quit"}
        return None


# ---- Replay 选择(MainMenu.cpp OnUpdateSelectReplay :1974-) ----
class ReplayFlow(msgspec.Struct):
    """Replay 列表的选择状态。emit play(选录像)/quit(返回标题)。

    简化: 不分页(原版 15/页 + 左右翻页), 单列表上下回绕; entries 由
    调用方扫 replays/ 目录填入(engine/replay.list_replays)。
    """

    entries: list = msgspec.field(default_factory=list)  # [{"path", "meta"}, ...]
    cursor: int = 0

    def handle(self, action: MenuAction) -> "dict | None":
        n = len(self.entries)
        if action == MenuAction.BACK:
            return {"action": "quit"}
        if n == 0:
            return None
        if action == MenuAction.UP:
            self.cursor = (self.cursor - 1) % n
        elif action == MenuAction.DOWN:
            self.cursor = (self.cursor + 1) % n
        elif action == MenuAction.CONFIRM:
            return {"action": "play", "index": self.cursor}
        return None


# ---- 结算入榜名字输入(ResultScreen.cpp HandleResultKeyboard :1141-1326) ----
# 字表 g_AlphabetList (:24) 96 字 = 6 行 x 16 列; 末行空格(93)不可停,
# 94 = 输入空格(贴字 0x80), 95 = 结束输入 END(贴字 0x81)。
NAME_ALPHABET = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ.,:;_@"
    "abcdefghijklmnopqrstuvwxyz+-/*=%"
    "0123456789#!?'\"$(){}[]<>&\\|~^ --"
)
NAME_ALPHABET_COLS = 16
NAME_LEN = 8  # Hscr name[9] = 8 字符 + NUL
NAME_CELL_SPACE = 94  # 确认 = 该槽写空格
NAME_CELL_END = 95  # 确认 = 完成输入


class NameEntryFlow(msgspec.Struct):
    """入榜名字输入状态机(纯逻辑, 对照 HandleResultKeyboard)。

    - slots: 8 字符槽(cursor 0..7), 初始带出 LSNM(curScore.name =
      lsnmHeader.name, :1184); cursor 到 8 = 输完待确认态。
    - selected: 字表光标。有 LSNM 时初始停在 END(:1203-1205,
      isClearingReplayName), 否则 0('A')。
    - 上下 ±16 回绕、左右 ±1 行内回绕, 均跳过字表中的空格格(:1215-1269);
    - CONFIRM: 写当前槽(94=空格/95=完成), 未满 8 槽 cursor++, 到 8 时
      字表光标自动跳到 END(:1290-1294);
    - BACK: 退一格并清掉当前槽与前一槽(:1300-1308)。
    handle 返回 None(继续)或 {"action": "finish", "name": 8 字符名字}。
    """

    initial: str = ""  # 初始名(LSNM 或默认名)
    has_lsnm: bool = False  # 是否有上次输入的名字(定字表光标初始位)
    # 以下三个由 __post_init__ 按 initial/has_lsnm 派生(msgspec 无 init=False,
    # 声明为带默认值的字段再覆盖; 外部不应显式传入)
    slots: list = msgspec.field(default_factory=list)
    cursor: int = 0
    selected: int = 0

    def __post_init__(self) -> None:
        padded = (str(self.initial) + " " * NAME_LEN)[:NAME_LEN]
        self.slots = list(padded)
        self.selected = NAME_CELL_END if self.has_lsnm else 0

    @property
    def name(self) -> str:
        return "".join(self.slots)

    def handle(self, action: MenuAction) -> "dict | None":
        if action == MenuAction.UP:
            self._move_vertical(-NAME_ALPHABET_COLS)
            return {"action": "move"}
        if action == MenuAction.DOWN:
            self._move_vertical(NAME_ALPHABET_COLS)
            return {"action": "move"}
        if action == MenuAction.LEFT:
            self._move_left()
            return {"action": "move"}
        if action == MenuAction.RIGHT:
            self._move_right()
            return {"action": "move"}
        if action == MenuAction.CONFIRM:
            return self._confirm()
        if action == MenuAction.BACK:
            self._delete()
            return {"action": "delete"}
        return None

    # ---- 字表光标(±16 回绕 / 行内 ±1 回绕, 跳过 ' ' 格) ----
    def _move_vertical(self, delta: int) -> None:
        while True:
            self.selected += delta
            if self.selected < 0:
                self.selected += len(NAME_ALPHABET)
            if self.selected >= len(NAME_ALPHABET):
                self.selected -= len(NAME_ALPHABET)
            if NAME_ALPHABET[self.selected] != " ":
                return

    def _move_left(self) -> None:
        while True:
            self.selected -= 1
            if self.selected % NAME_ALPHABET_COLS == NAME_ALPHABET_COLS - 1:
                self.selected += NAME_ALPHABET_COLS
            if self.selected < 0:
                self.selected = NAME_ALPHABET_COLS - 1
            if NAME_ALPHABET[self.selected] != " ":
                return

    def _move_right(self) -> None:
        while True:
            self.selected += 1
            if self.selected % NAME_ALPHABET_COLS == 0:
                self.selected -= NAME_ALPHABET_COLS
            if NAME_ALPHABET[self.selected] != " ":
                return

    # ---- 确认 / 删除 ----
    def _confirm(self) -> "dict | None":
        cur = min(self.cursor, NAME_LEN - 1)  # cursor==8 时改写末槽(:1273)
        if self.selected < NAME_CELL_SPACE:
            self.slots[cur] = NAME_ALPHABET[self.selected]
        elif self.selected == NAME_CELL_SPACE:
            self.slots[cur] = " "
        else:  # END → 完成(同 TH_BUTTON_MENU 出口, :1310-1323)
            return {"action": "finish", "name": self.name}
        if self.cursor < NAME_LEN:
            self.cursor += 1
            if self.cursor == NAME_LEN:
                self.selected = NAME_CELL_END  # 输满自动跳到 END(:1293)
        return {"action": "input"}

    def _delete(self) -> None:
        cur2 = min(self.cursor, NAME_LEN - 1)
        if self.cursor > 0:
            self.cursor -= 1
            self.slots[cur2] = " "
            self.slots[self.cursor] = " "
