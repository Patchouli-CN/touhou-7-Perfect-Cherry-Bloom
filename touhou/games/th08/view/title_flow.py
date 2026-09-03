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


__all__ = [
    "CURSOR_FROM_GAME",
    "CURSOR_FROM_RESULT",
    "CURSOR_ON_BOOT",
    "CharacterFlowTh08",
    "HELP_TEXTS",
    "MARK_ALL_CLEARED",
    "MARK_FINAL_SELECTABLE",
    "MARK_FINALB_AVAILABLE",
    "MARK_FINALB_CLEARED",
    "TITLE_MENU_ITEMS",
    "TitleFlowTh08",
    "completion_mark_sprite",
    "unlock_flags",
]
