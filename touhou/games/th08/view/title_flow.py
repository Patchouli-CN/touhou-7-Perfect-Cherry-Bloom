"""th08 标题主菜单的纯逻辑(名单/光标/锁定跳过/初始光标/解锁判定) —— 无 pygame。

对照 th08-ref TitleScreen.cpp::OnUpdateStartMenu(:280-643, 行号相对其 src/):
- 主菜单 9 项(TITLE_MENU_ITEM_START_* 枚举 :79-91), 光标 0-8;
- 移动 = MoveCursorVertical(9) 回绕(:3128-3160) + 锁定项顺向跳过
  (:383-404 的 goto back 循环 —— Extra Start=1 / Spell Practice=2 未解锁时);
- BACK 光标直接跳到 Quit + SOUND_BACK(:601-608);
- 确认按项分发(:499-598); 锁定项确认无效(:531-558 的解锁守卫落空时
  switch 直通, 无操作无音效);
- 初始光标 = ActualAddedCallback 按 wantedState2 分支(:3682-3698)。

菜单项标签原作是 title01.anm 贴图(无文本), 本模块的名单文本只用于
无资源环境的文字回退菜单与日志。
"""

from __future__ import annotations

import msgspec
from typing import TYPE_CHECKING

from ...th07.view.screens import MenuAction, MenuCursor
from ..progress import is_extra_unlocked, is_spell_practice_unlocked

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


__all__ = [
    "CURSOR_FROM_GAME",
    "CURSOR_FROM_RESULT",
    "CURSOR_ON_BOOT",
    "HELP_TEXTS",
    "TITLE_MENU_ITEMS",
    "TitleFlowTh08",
    "unlock_flags",
]
