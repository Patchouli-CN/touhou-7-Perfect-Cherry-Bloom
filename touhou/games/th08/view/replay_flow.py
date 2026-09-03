"""th08 Replay 菜单(录像列表)的纯逻辑 —— 目录扫描/翻页/选择, 无 pygame。

对照 th08-ref(@1861f88, 行号相对其 src/) TitleScreen.cpp
OnUpdateReplayMenu(:3213-3548) 的 state 0(扫描)/state 1(列表); DrawReplayMenu
(:2550-2677) 的行格式。原作的 state 2(选面)/state 3(播放方式)依赖逐面
stageReplayData 与 replayEventFlags(减速/只 boss 战), JSON 录像路线
(engine/replay.py, 决策见 scratch_dbg/investigation/th08-title-systems.md §19)
两者都不记录, 本片不支持 —— 确认即从头播放。

handle 返回 None 或 {"action": ..., "se": ...}: action ∈ move/play/quit,
se 由调用方播菜单音效(SOUND_MOVE_MENU/SELECT/BACK)。
"""

from __future__ import annotations

import msgspec

from ....engine import replay as replay_mod
from ...th07.view.screens import MenuAction

REPLAYS_PER_PAGE = 15  # TITLE_REPLAYS_PER_PAGE(TitleScreen.hpp:9)
MAX_REPLAYS = 60  # TITLE_MAX_REPLAYS(TitleScreen.hpp:59)
# 确认/返回的进场输入门(列表态 stateTimer<10 不受理, :3344-3347;
# 光标移动在原作不受门控 —— MoveCursorVertical 在门检查之前, :3321)
INPUT_GATE_FRAMES = 10
FILE_PREFIX = "th8_"  # 原作只枚举 th8_%.2d.rpy 与 th8_ud????.rpy(:3245-3308)

# 列表行 Player/Rank 列文本(g_ReplayCharacterNames :208-211 /
# g_ReplayDifficulties :213-215)
REPLAY_CHARACTER_NAMES = (
    "Rm & Yk",
    "Ms & Al",
    "Sk & Rr",
    "Ym & Yy",
    "Reimu  ",
    "Yukari ",
    "Marisa ",
    "Alice  ",
    "Sakuya ",
    "Remilia",
    "Youmu  ",
    "Yuyuko ",
)
REPLAY_DIFFICULTY_NAMES = (
    "Easy    ",
    "Normal  ",
    "Hard    ",
    "Lunatic ",
    "Extra   ",
)


def scan_replays(replay_dir) -> list[dict]:
    """扫录像目录的 th8_*.json(JSON 路线; 原作枚举 th8_NN.rpy 固定槽 +
    th8_ud????.rpy 用户档, 只读头校验坏档跳过, :3245-3308); 超 60 截断。"""
    entries = [
        e
        for e in replay_mod.list_replays(replay_dir)
        if e["path"].name.startswith(FILE_PREFIX)
    ]
    return entries[:MAX_REPLAYS]


def entry_tag(entry: dict) -> str:
    """行首编号: 固定槽命名 th8_NN.json → "No.NN", 用户命名 → "User "
    (:3263 sprintf "No.%.2d" / :3297 sprintf "User ")。"""
    digits = entry["path"].stem[len(FILE_PREFIX) :]
    if len(digits) == 2 and digits.isdigit():
        return f"No.{digits}"
    return "User "


def entry_line(entry: dict) -> str:
    """列表行文本(:2581-2584 的 "%s %8s  %6s %7s  %8s"; JSON meta 无玩家名
    字段, Name 列用文件名(不含扩展名)代替 —— 偏离注明)。"""
    meta = entry["meta"]
    ch = int(meta.get("character", 0))
    dif = int(meta.get("difficulty", 0))
    ch_name = (
        REPLAY_CHARACTER_NAMES[ch]
        if 0 <= ch < len(REPLAY_CHARACTER_NAMES)
        else "??????"
    )
    dif_name = (
        REPLAY_DIFFICULTY_NAMES[dif]
        if 0 <= dif < len(REPLAY_DIFFICULTY_NAMES)
        else "????????"
    )
    date = str(meta.get("created", ""))[:10]
    return (
        f"{entry_tag(entry)} {entry['path'].stem:<20} {date:>10} {ch_name} {dif_name}"
    )


class ReplayFlowTh08(msgspec.Struct):
    """Replay 录像列表的选择状态(OnUpdateReplayMenu state 0/1 的对应物)。

    entries = 进画面时扫目录的快照(原作 state 0 只扫一次, :3226-3312);
    frames = 列表态进场计时(确认/返回的 10 帧输入门; 移动不受门控)。
    """

    entries: list = msgspec.field(default_factory=list)  # [{"path", "meta"}, ...]
    cursor: int = 0
    frames: int = 0

    @property
    def input_enabled(self) -> bool:
        """确认/返回的进场门(:3344-3347 stateTimer<10)。"""
        return self.frames >= INPUT_GATE_FRAMES

    @property
    def page_start(self) -> int:
        """当前页首行下标(15/页, DrawReplayMenu :2558-2559)。"""
        return self.cursor - self.cursor % REPLAYS_PER_PAGE

    def tick_frame(self) -> None:
        """每帧推进进场计时(:3543-3545 stateTimer++)。"""
        self.frames += 1

    def handle(self, action: MenuAction) -> dict | None:
        """处理一次菜单按键; 移动即时受理, 确认/返回等进场门过后才受理。"""
        n = len(self.entries)
        if action in (MenuAction.UP, MenuAction.DOWN) and n:
            # MoveCursorVertical 回绕(:3321)
            self.cursor = (self.cursor + (1 if action == MenuAction.DOWN else -1)) % n
            return {"action": "move", "se": "select"}
        if action in (MenuAction.LEFT, MenuAction.RIGHT) and n > REPLAYS_PER_PAGE:
            # 整页 ±15 回绕, 仅当总数超一页(:3323-3341)
            d = REPLAYS_PER_PAGE if action == MenuAction.RIGHT else -REPLAYS_PER_PAGE
            self.cursor = (self.cursor + d) % n
            return {"action": "move", "se": "select"}
        if not self.input_enabled:
            return None
        if action == MenuAction.BACK:
            # BOMB/MENU → state 4 退幕回主菜单(:3410-3417/:3523-3531)
            return {"action": "quit", "se": "cancel"}
        if action == MenuAction.CONFIRM and n:
            # SELECTMENU 且列表非空(:3350-3355); 原作这里进 state 2 选面,
            # JSON 路线无逐面数据, 直接从头播放(见模块 docstring)
            return {"action": "play", "index": self.cursor, "se": "ok"}
        return None


__all__ = [
    "FILE_PREFIX",
    "INPUT_GATE_FRAMES",
    "MAX_REPLAYS",
    "REPLAYS_PER_PAGE",
    "REPLAY_CHARACTER_NAMES",
    "REPLAY_DIFFICULTY_NAMES",
    "ReplayFlowTh08",
    "entry_line",
    "entry_tag",
    "scan_replays",
]
