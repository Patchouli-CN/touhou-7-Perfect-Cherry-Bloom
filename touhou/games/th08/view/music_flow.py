"""th08 Music Room 的纯逻辑(曲目表/滚动窗口/选曲/解锁隐藏/按键语义) —— 无 pygame。

对照 th08-ref MusicRoom.cpp(行号相对其 src/): ProcessInput(:51-238)、
AddedCallback(:392-553)、OnDraw(:318-389); 颜色/贴图细节见
scratch_dbg/investigation/th08-title-systems.md §13。
"""

from __future__ import annotations

from pathlib import Path

import msgspec

from ....schema.archive import open_archive
from ....schema.musiccmt import TrackDescriptor, parse_musiccmt
from ..crypt import try_decrypt_from_table
from ...th07.view.screens import MenuAction

MUSIC_ROOM_VISIBLE = 10  # 一屏 10 行(OnDraw :325 的 listingOffset..+10)
DESC_LINES = 7  # 简介行数(AddedCallback :481 每曲读 7 行)
INPUT_DELAY_FRAMES = 8  # 进场 8 帧后才受理输入(CheckInputEnable :41)
DESC_REVEAL_START = 10  # 简介逐行重绘起点(ProcessInput :137 frameCount==10)
DESC_REVEAL_STEP = 2  # 每 2 帧一行(:137-152 的 10,12,...,22)
DESC_FADE_FRAMES = 8  # 行淡入帧数(SetInterrupt(1) → text.anm op34 8 帧)

# 未解锁占位曲名(i18n.csv:121 SONG_NAME_NOT_UNLOCKED)
LOCKED_TITLE = "？？？？？？？？"
# 未解锁警告文(i18n.csv:122-128 WARN_BGM_NOT_UNLOCKED0-6;
# g_BgmNotUnlockedWarning 8 槽, 末槽重复 [4], MusicRoom.cpp:13-16; 只画前 7 行)
LOCKED_WARNING = (
    "＊＊＊注意＊＊＊",
    "　この曲はまだ、ゲーム中で流れておりません",
    "　自分でプレイする前に聴きたくない、コメントを見たくない、",
    "　という方は一刻も早くここから立ち去る事をお奨めします。",
    "　",
    "　（なお、ここで決定ボタンを押せば、他の曲と同様、",
    "　　曲の再生とコメント表示が行われますので、注意）",
    "　",
)


def load_tracks(data_path) -> list[TrackDescriptor]:
    """从 th08.dat 解 musiccmt.txt → 曲目表; 失败返回空表(容错同 th07)。

    封包内条目名是扁平的 "musiccmt.txt"(C++ 侧 "sprt/musiccmt.txt",
    AddedCallback :413), edz 内层加密需 try_decrypt_from_table。
    """
    try:
        arc = open_archive(Path(data_path), game="th08")
        return parse_musiccmt(try_decrypt_from_table(arc.load("musiccmt.txt")))
    except Exception:
        return []


class MusicRoomFlowTh08(msgspec.Struct):
    """th08 Music Room 的选曲状态(ProcessInput :51-238 的对应物)。

    handle 返回 None 或结果 dict(播放/淡出由调用方走 SoundPlayer):
    - {"action": "play", "index"} SELECTMENU 播光标曲(:190-209);
    - {"action": "replay", "index"} RESET 重播 selected(:232-238);
    - {"action": "fadeout"} SKIP → FadeOutMusic(8.0)(:228-230);
    - {"action": "quit"} BOMB/MENU 回标题(:212-219)。
    frames = 原作的 frameCount: 进场满 8 帧才受理输入(:41, 只锁进场 ——
    inputState 一旦变 1 不再回 0), 移动/播放时清零驱动简介逐行重绘
    (:137-152)。播放/移动/淡出原作均无菜单 SE(ProcessInput 全文无
    PlaySoundByIdx), 结果 dict 也不带 "se" 键。
    """

    tracks: list = msgspec.field(default_factory=list)  # list[TrackDescriptor]
    unlocked: list[bool] = msgspec.field(default_factory=list)
    # bgmUnlocked 进场快照(AddedCallback :528 从 plst 拷贝; 当次会话内不刷新)
    cursor: int = 0
    listing_offset: int = 0
    selected: int = 0  # selectedSongIndex(RESET 重播对象 + 简介显示规则)
    played: int | None = None  # Now Playing 行的曲目(None=未播过; RESET 不更新)
    frames: int = 0  # frameCount(:50; 移动/播放清零)
    input_enabled: bool = False  # inputState==1 的锁存(:41 只进不退)

    def is_unlocked(self, index: int) -> bool:
        """该曲是否已解锁(快照内; 未解锁曲名/简介隐藏, :522-536)。"""
        return 0 <= index < len(self.unlocked) and bool(self.unlocked[index])

    def display_title(self, index: int) -> str:
        """列表行曲名: 未解锁显示占位(:534 TH_SONG_NAME_NOT_UNLOCKED)。"""
        if not 0 <= index < len(self.tracks):
            return ""
        return self.tracks[index].title if self.is_unlocked(index) else LOCKED_TITLE

    def description_lines(self) -> tuple[str, ...]:
        """光标曲的 7 行简介: selected==cursor 或已解锁给真简介, 否则警告文
        (:162-169; 进场 selected=0 → 0 号曲锁定也显示真简介, 原作 quirk 照抄)。"""
        n = len(self.tracks)
        if not 0 <= self.cursor < n:
            return ("",) * DESC_LINES
        if self.selected == self.cursor or self.is_unlocked(self.cursor):
            lines = list(self.tracks[self.cursor].comment[:DESC_LINES])
        else:
            lines = list(LOCKED_WARNING[:DESC_LINES])
        lines += [""] * (DESC_LINES - len(lines))
        return tuple(lines)

    def now_playing_line(self) -> str | None:
        """Now Playing 行内容(:371-381 播曲时烘的简介第 0 行); 未播过 → None。"""
        if self.played is None or not 0 <= self.played < len(self.tracks):
            return None
        comment = self.tracks[self.played].comment
        return comment[0] if comment else ""

    def handle(self, action: MenuAction) -> dict | None:
        """处理一次菜单按键(ProcessInput 的按键分支; 进场 8 帧内不受理)。"""
        if not self.input_enabled:
            return None
        n = len(self.tracks)
        if action == MenuAction.BACK:
            return {"action": "quit"}  # BOMB/MENU → 回标题(:212-219)
        if n == 0:
            return None
        if action == MenuAction.UP:
            # :56-67 回绕 + 窗口跟随
            self.cursor -= 1
            if self.cursor < 0:
                self.cursor = n - 1
                self.listing_offset = max(0, n - MUSIC_ROOM_VISIBLE)
            elif self.listing_offset > self.cursor:
                self.listing_offset = self.cursor
            self.frames = 0  # :104 简介重绘计时清零
        elif action == MenuAction.DOWN:
            # :107-118
            self.cursor += 1
            if self.cursor >= n:
                self.cursor = 0
                self.listing_offset = 0
            elif self.listing_offset <= self.cursor - MUSIC_ROOM_VISIBLE:
                self.listing_offset = self.cursor - MUSIC_ROOM_VISIBLE + 1
            self.frames = 0
        elif action == MenuAction.CONFIRM:
            # SELECTMENU(shoot|enter): 播光标曲(:190-209)
            self.selected = self.played = self.cursor
            self.frames = 0
            return {"action": "play", "index": self.cursor}
        elif action == MenuAction.SKIP:
            return {"action": "fadeout"}  # FadeOutMusic(8.0)(:228-230)
        elif action == MenuAction.RESET:
            # 重播 selected(:232-238; 未播过时 = 0 号曲, 原作 quirk 照抄)
            return {"action": "replay", "index": self.selected}
        return None

    def tick_frame(self) -> None:
        """每帧推进 frameCount(OnUpdate :291-298); 满 8 帧锁存输入受理。"""
        self.frames += 1
        if self.frames >= INPUT_DELAY_FRAMES:
            self.input_enabled = True


__all__ = [
    "DESC_FADE_FRAMES",
    "DESC_LINES",
    "DESC_REVEAL_START",
    "DESC_REVEAL_STEP",
    "INPUT_DELAY_FRAMES",
    "LOCKED_TITLE",
    "LOCKED_WARNING",
    "MUSIC_ROOM_VISIBLE",
    "MusicRoomFlowTh08",
    "load_tracks",
]
