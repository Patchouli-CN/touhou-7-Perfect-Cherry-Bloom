""" musiccmt.txt 解析 —— TH07 Music Room 曲目名与评论。

对照 MusicRoom.cpp AddedCallback (:233-383) 的解析语义:

- 文件 Shift-JIS, CRLF 行; 首个 '@' 之前的内容忽略(原版首行是一串占位数字);
- 每块以 "@bgm/th07_XX.mid" 开头: path 行 → title 行 → 评论行,
  评论最多 8 行(TrackDescriptor.description[8], 空行跳过, 遇下个 '@' 止);
- 播放路径即 '@' 行内容 (ProcessInput: PlayAudio(trackDescriptors[i].path),
  MusicRoom.cpp:113)。

thbgm.fmt 里 20 首 WAV 与 musiccmt.txt 的 20 块一一对应(th07_13b 是
3 面 Boss 曲后段, 不占 Music Room 槽位)。
"""

from __future__ import annotations

import msgspec

MAX_COMMENT_LINES = 8  # TrackDescriptor.description[8]


class TrackDescriptor(msgspec.Struct, frozen=True):
    """Music Room 一首曲目。"""

    path: str                        # "bgm/th07_01.mid" ('@' 行原文)
    title: str                       # 曲名(紧随 '@' 行的一行)
    comment: tuple[str, ...] = msgspec.field(default_factory=tuple)  # 评论行(≤8)

    @property
    def file_name(self) -> str:
        """播放用文件名(去目录前缀): "th07_01.mid"。"""
        return self.path.replace("\\", "/").split("/")[-1]


def parse_musiccmt(data: bytes) -> list[TrackDescriptor]:
    """解析 musiccmt.txt 字节流(Shift-JIS), 返回曲目表(文件序)。"""
    text = data.decode("shift_jis", errors="replace")
    tracks: list[TrackDescriptor] = []
    path = title = None
    comment: list[str] = []

    def flush() -> None:
        nonlocal path, title, comment
        if path is not None:
            tracks.append(TrackDescriptor(path, title or "", tuple(comment)))
        path = title = None
        comment = []

    for line in text.splitlines():
        if line.startswith("@"):
            flush()
            path = line[1:].strip()
        elif path is None:
            continue  # 首个 '@' 前的占位内容
        elif title is None:
            title = line.strip()
        elif len(comment) < MAX_COMMENT_LINES:
            line = line.strip()
            if line:  # 空行只是块间分隔, 不入评论
                comment.append(line)
    flush()
    return tracks
