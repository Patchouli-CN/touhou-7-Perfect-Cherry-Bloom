"""thbgm.dat / thbgm.fmt 解析 —— TH07 高音质 WAV BGM 流。

对照反编译源码:

- ThBgmFormat (dsutil.hpp:47-57, sizeof == 0x34)::

    char name[16];          // "th07_02.wav"
    i32  startOffset;       // PCM 数据在 thbgm.dat 里的绝对偏移
    DWORD preloadAllocSize; // 原版整曲预读缓冲大小(本实现不预读, 仅透传)
    i32  introLength;       // 前奏 PCM 字节数; 循环段 = [introLength, totalLength)
    i32  totalLength;       // 整曲 PCM 字节数
    WAVEFORMATEX format;    // 18 字节 + 2 字节对齐

  fmt 文件是 ThBgmFormat 数组, 以 name[0] == 0 的空条目终止
  (SoundPlayer::GetFmtIndexByName, SoundPlayer.cpp:198-230)。
- thbgm.dat 头部 16 字节: "ZWAV" + u32 版本(1) + u32 游戏 id(0x700) + u32 保留
  (Supervisor.cpp:1183-1230)。首曲 startOffset == 16, 即头之后全是裸 PCM 串联。
- 循环语义 (CWaveFile::ResetFile, dsutil.cpp:1071-1112): 播完 totalLength 后
  回到 startOffset + introLength 继续读, 即 intro 只播一遍, loop 段无限循环。
- 曲目查找 (Supervisor::PlayAudio, Supervisor.cpp:1397-1424): 把 "bgm/th07_02.mid"
  的扩展名换成 ".wav" 再按 basename 与 fmt name 精确匹配。
"""

from __future__ import annotations

import struct
import msgspec
from pathlib import Path

FMT_ENTRY_SIZE = 0x34  # sizeof(ThBgmFormat)
THBGM_HEADER_SIZE = 16
THBGM_MAGIC = b"ZWAV"
THBGM_VERSION = 1
THBGM_GAME_ID = 0x700


class ThbgmTrack(msgspec.Struct, frozen=True):
    """thbgm.fmt 里的一首曲目。"""

    name: str  # "th07_02.wav"
    start_offset: int  # PCM 在 thbgm.dat 的绝对偏移
    intro_length: int  # 前奏字节数(只播一遍)
    total_length: int  # 整曲 PCM 字节数
    channels: int
    sample_rate: int
    bits_per_sample: int
    preload_size: int = 0  # 原版预读大小, 仅记录未使用

    @property
    def bytes_per_second(self) -> float:
        return self.sample_rate * self.channels * self.bits_per_sample / 8

    @property
    def intro_seconds(self) -> float:
        """循环起点(秒) —— mixer.music.play(start=...) 用。"""
        return self.intro_length / self.bytes_per_second

    @property
    def total_seconds(self) -> float:
        return self.total_length / self.bytes_per_second

    @property
    def loop_seconds(self) -> float:
        """循环段时长(秒)。"""
        return (self.total_length - self.intro_length) / self.bytes_per_second


def parse_fmt(data: bytes) -> dict[str, ThbgmTrack]:
    """解析解压后的 thbgm.fmt 字节流, 返回 {曲目名: ThbgmTrack}。"""
    tracks: dict[str, ThbgmTrack] = {}
    pos = 0
    while pos + FMT_ENTRY_SIZE <= len(data):
        raw_name = data[pos : pos + 16]
        if raw_name[0] == 0:
            break  # 空条目终止 (SoundPlayer.cpp:218)
        name = raw_name.split(b"\x00")[0].decode("latin-1")
        start, preload, intro, total = struct.unpack_from("<iIii", data, pos + 16)
        _tag, ch, rate, _avg, _align, bits = struct.unpack_from(
            "<HHIIHH", data, pos + 32
        )
        tracks[name] = ThbgmTrack(name, start, intro, total, ch, rate, bits, preload)
        pos += FMT_ENTRY_SIZE
    return tracks


def check_thbgm_header(path: str | Path) -> bool:
    """校验 thbgm.dat 头部 ("ZWAV"/v1/game 0x700, Supervisor.cpp:1183-1199)。"""
    try:
        with open(path, "rb") as f:
            header = f.read(THBGM_HEADER_SIZE)
    except OSError:
        return False
    if len(header) < THBGM_HEADER_SIZE:
        return False
    magic, version, game_id, _ = struct.unpack("<4sIII", header)
    return bool(
        magic == THBGM_MAGIC and version == THBGM_VERSION and game_id == THBGM_GAME_ID
    )


def build_wav(track: ThbgmTrack, pcm: bytes) -> bytes:
    """把 thbgm.dat 里读出的裸 PCM 包成完整 RIFF/WAVE (供 pygame 解码)。"

    pcm 长度以 track.total_length 为准(截断/不足容忍)。
    """
    pcm = pcm[: track.total_length]
    byte_rate = int(track.bytes_per_second)
    block_align = track.channels * track.bits_per_sample // 8
    fmt_chunk = struct.pack(
        "<HHIIHH",
        1,  # WAVE_FORMAT_PCM
        track.channels,
        track.sample_rate,
        byte_rate,
        block_align,
        track.bits_per_sample,
    )
    data_size = len(pcm)
    riff_size = 4 + (8 + len(fmt_chunk)) + (8 + data_size)
    return (
        b"RIFF"
        + struct.pack("<I", riff_size)
        + b"WAVE"
        + b"fmt "
        + struct.pack("<I", len(fmt_chunk))
        + fmt_chunk
        + b"data"
        + struct.pack("<I", data_size)
        + pcm
    )
