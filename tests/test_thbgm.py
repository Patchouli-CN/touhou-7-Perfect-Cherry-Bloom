"""thbgm.dat WAV BGM 测试: fmt 解析 / wav 封装 / SoundPlayer 音源路由与循环轮询。

对照 dsutil.hpp ThBgmFormat / Supervisor.cpp PlayAudio / dsutil.cpp CWaveFile::ResetFile。
"""

from __future__ import annotations

import io
import struct
import sys
import wave
from pathlib import Path

import pytest

sys.path.insert(0, r"D:\python_play\Touhou08")

from touhou.schema.thbgm import (  # noqa: E402
    ThbgmTrack,
    build_wav,
    check_thbgm_header,
    parse_fmt,
)
from touhou.engine.view.sound_player import SoundPlayer  # noqa: E402

DAT = Path(r"D:\TOUHOU_GAME\[th07] 东方妖妖梦 (日文版)\th07.dat")
THBGM = DAT.with_name("thbgm.dat")
NEEDS_DAT = pytest.mark.skipif(not DAT.exists(), reason="需要真实 th07.dat")
NEEDS_THBGM = pytest.mark.skipif(
    not (DAT.exists() and THBGM.exists()), reason="需要真实 thbgm.dat"
)


def _fmt_entry(
    name: str,
    start: int,
    preload: int,
    intro: int,
    total: int,
    ch: int = 2,
    rate: int = 44100,
    bits: int = 16,
) -> bytes:
    """造一条 52 字节 ThBgmFormat。"""
    raw = name.encode("latin-1").ljust(16, b"\x00")
    body = struct.pack("<iIii", start, preload, intro, total)
    wfx = struct.pack(
        "<HHIIHHH", 1, ch, rate, rate * ch * bits // 8, ch * bits // 8, bits, 0
    )
    entry = raw + body + wfx
    return entry.ljust(52, b"\x00")


def _synth_fmt() -> bytes:
    return (
        _fmt_entry("th07_02.wav", 16, 2000, 400, 1000)
        + _fmt_entry("th07_13b.wav", 1016, 3000, 0, 2000)
        + b"\x00" * 52
    )  # 空条目终止


def _synth_thbgm(tmp_path: Path, pcm: bytes) -> Path:
    """造一个带 ZWAV 头的迷你 thbgm.dat。"""
    p = tmp_path / "thbgm.dat"
    p.write_bytes(struct.pack("<4sIII", b"ZWAV", 1, 0x700, 0) + pcm)
    return p


# ---- fmt 解析(合成数据) ----


def test_parse_fmt_synthetic() -> None:
    tracks = parse_fmt(_synth_fmt())
    assert set(tracks) == {"th07_02.wav", "th07_13b.wav"}
    t = tracks["th07_02.wav"]
    assert t.start_offset == 16 and t.intro_length == 400 and t.total_length == 1000
    assert (t.channels, t.sample_rate, t.bits_per_sample) == (2, 44100, 16)
    assert t.preload_size == 2000
    # 派生时长: 1000B / 176400Bps
    assert t.total_seconds == pytest.approx(1000 / 176400)
    assert t.intro_seconds == pytest.approx(400 / 176400)
    assert t.loop_seconds == pytest.approx(600 / 176400)


def test_parse_fmt_stops_at_empty_and_garbage_tail() -> None:
    data = _fmt_entry("a.wav", 16, 0, 0, 100) + b"\x00" * 17 + b"garbage"
    assert set(parse_fmt(data)) == {"a.wav"}


def test_check_thbgm_header(tmp_path: Path) -> None:
    good = _synth_thbgm(tmp_path, b"\x00" * 8)
    assert check_thbgm_header(good)
    bad = tmp_path / "bad.dat"
    bad.write_bytes(b"NOPE" + b"\x00" * 12)
    assert not check_thbgm_header(bad)
    assert not check_thbgm_header(tmp_path / "missing.dat")


def test_build_wav_roundtrip() -> None:
    t = ThbgmTrack("t.wav", 16, 4, 8, 1, 8000, 8)
    wav_bytes = build_wav(t, bytes(range(8)) + b"extra")  # 超长截断
    with wave.open(io.BytesIO(wav_bytes)) as w:
        assert (w.getnchannels(), w.getframerate(), w.getsampwidth()) == (1, 8000, 1)
        assert w.readframes(100) == bytes(range(8))


# ---- 真实文件验证 ----


@NEEDS_THBGM
def test_real_fmt_and_header() -> None:
    from touhou.schema.archive import open_archive

    arc = open_archive(DAT)
    key = "bgm/thbgm.fmt" if "bgm/thbgm.fmt" in arc else "thbgm.fmt"
    tracks = parse_fmt(arc.load(key))
    assert len(tracks) == 20
    t = tracks["th07_02.wav"]
    assert t.start_offset == 16545584
    assert t.intro_length == 2858880 and t.total_length == 15499264
    assert (t.channels, t.sample_rate, t.bits_per_sample) == (2, 44100, 16)
    assert check_thbgm_header(THBGM)
    # 曲目串联: 上一曲 start+total == 下一曲 start
    ordered = sorted(tracks.values(), key=lambda x: x.start_offset)
    for a, b in zip(ordered, ordered[1:]):
        assert a.start_offset + a.total_length == b.start_offset


# ---- SoundPlayer 路由(fake mixer.music) ----


class _FakeMusic:
    """替身 pygame.mixer.music: 记录调用, 不碰音频设备。"""

    def __init__(self) -> None:
        self.loaded: list[bytes] = []
        self.plays: list[tuple] = []
        self.busy = True
        self.pos = 0
        self.stopped = False
        self.faded: list[int] = []
        self.volumes: list[float] = []

    def load(self, file) -> None:
        self.loaded.append(file.read())

    def set_volume(self, v: float) -> None:
        self.volumes.append(v)

    def play(self, loops=0, start=0.0) -> None:
        self.plays.append((loops, start))
        self.busy = True
        self.pos = 0

    def get_busy(self) -> bool:
        return self.busy

    def get_pos(self) -> int:
        return self.pos

    def fadeout(self, ms: int) -> None:
        self.faded.append(ms)
        self.busy = False

    def stop(self) -> None:
        self.stopped = True
        self.busy = False


class _FakeArchive:
    def __init__(self) -> None:
        self.loaded_names: list[str] = []

    def load(self, name: str) -> bytes:
        self.loaded_names.append(name)
        return b"MThd fakemidi"


def _player(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tracks: dict[str, ThbgmTrack] | None = None,
) -> tuple[SoundPlayer, _FakeMusic, _FakeArchive]:
    import pygame

    fake = _FakeMusic()
    monkeypatch.setattr(pygame.mixer, "music", fake)
    sp = SoundPlayer(tmp_path / "th07.dat")
    sp._enabled = True
    sp._loaded = True
    sp._archive = _FakeArchive()
    sp._thbgm_tracks = (
        tracks
        if tracks is not None
        else {
            "th07_02.wav": ThbgmTrack(
                "th07_02.wav", 16, 400_000, 1_000_000, 2, 44100, 16
            ),
        }
    )
    _synth_thbgm(tmp_path, b"\x00" * (16 + 1_000_000))
    sp._thbgm_path = tmp_path / "thbgm.dat"
    return sp, fake, sp._archive


def test_wav_preferred_when_track_in_fmt(tmp_path, monkeypatch) -> None:
    sp, fake, arc = _player(tmp_path, monkeypatch)
    sp.play_music("th07_02.mid")
    assert sp._current_bgm == "th07_02.mid"
    assert sp._wav_bgm is not None and sp._wav_bgm.name == "th07_02.wav"
    assert arc.loaded_names == []  # 没碰 MIDI
    assert fake.plays == [(0, 0.0)]  # 播一遍, 循环靠轮询回卷
    assert fake.loaded[0][:4] == b"RIFF"  # 封装成 wav 送出
    assert len(fake.loaded[0]) == 44 + 1_000_000  # 头 + total_length


def test_basename_and_ext_mapping(tmp_path, monkeypatch) -> None:
    """ "bgm/th07_02.mid" 按 basename + 换 .wav 扩展查 fmt (Supervisor.cpp:1397)。"""
    sp, fake, _ = _player(tmp_path, monkeypatch)
    sp.play_music("bgm/th07_02.mid")
    assert sp._wav_bgm is not None


def test_midi_fallback_when_track_missing(tmp_path, monkeypatch) -> None:
    sp, fake, arc = _player(tmp_path, monkeypatch)
    sp.play_music("init.mid")  # init.wav 不在 fmt
    assert sp._current_bgm == "init.mid"
    assert sp._wav_bgm is None
    assert arc.loaded_names == ["init.mid"]
    assert fake.plays == [(-1, 0.0)]  # MIDI 整曲循环


def test_midi_fallback_when_thbgm_read_fails(tmp_path, monkeypatch) -> None:
    sp, fake, arc = _player(tmp_path, monkeypatch)
    sp._thbgm_path = tmp_path / "gone.dat"  # thbgm.dat 读失败
    sp.play_music("th07_02.mid")
    assert sp._current_bgm == "th07_02.mid"
    assert sp._wav_bgm is None
    assert arc.loaded_names == ["th07_02.mid"]


def test_set_bgm_source_forces_midi(tmp_path, monkeypatch) -> None:
    sp, fake, arc = _player(tmp_path, monkeypatch)
    assert sp.bgm_source == "wav"
    sp.set_bgm_source("midi")
    assert sp.bgm_source == "midi"
    sp.play_music("th07_02.mid")
    assert sp._wav_bgm is None
    assert arc.loaded_names == ["th07_02.mid"]
    sp.set_bgm_source("wav")
    assert sp.bgm_source == "wav"
    with pytest.raises(ValueError):
        sp.set_bgm_source("ogg")


def test_bgm_source_property_without_thbgm(tmp_path, monkeypatch) -> None:
    sp, _, _ = _player(tmp_path, monkeypatch, tracks={})
    assert sp.bgm_source == "midi"


def test_wav_loop_rewind_on_position(tmp_path, monkeypatch) -> None:
    """get_pos 到段尾 → play(start=循环点) 回卷; 之后播段长 = 循环段。"""
    sp, fake, _ = _player(tmp_path, monkeypatch)
    sp.play_music("th07_02.mid")
    track = sp._wav_bgm
    fake.pos = int(track.total_seconds * 1000) - 10  # 距曲尾 <30ms
    sp._poll_wav_loop()
    assert fake.plays[-1] == (0, track.intro_seconds)
    assert sp._wav_pass_ms == pytest.approx(track.loop_seconds * 1000)
    # 循环段尾再回卷一次
    fake.pos = int(track.loop_seconds * 1000) - 10
    sp._poll_wav_loop()
    assert fake.plays[-1] == (0, track.intro_seconds)


def test_wav_loop_rewind_on_natural_end(tmp_path, monkeypatch) -> None:
    """轮询漏掉、自然播完(get_busy False) → 同样从循环点续播。"""
    sp, fake, _ = _player(tmp_path, monkeypatch)
    sp.play_music("th07_02.mid")
    fake.busy = False
    sp._poll_wav_loop()
    assert fake.plays[-1] == (0, sp._wav_bgm.intro_seconds)


def test_stop_and_fadeout_clear_wav_state(tmp_path, monkeypatch) -> None:
    sp, fake, _ = _player(tmp_path, monkeypatch)
    sp.play_music("th07_02.mid")
    sp.fadeout_music(0.5)
    assert sp._wav_bgm is None and sp._current_bgm == ""
    sp.play_music("th07_02.mid")
    sp.stop_music()
    assert sp._wav_bgm is None and sp._current_bgm == ""
    fake.busy = False
    sp._poll_wav_loop()  # 无活动 WAV: 不重播
    assert len(fake.plays) == 2


# ---- 真实文件 WAV 冒烟(dummy audio) ----


@NEEDS_THBGM
def test_real_wav_bgm_smoke() -> None:
    import pygame

    pygame.init()
    try:
        pygame.mixer.init()
    except pygame.error:
        pytest.skip("无 mixer")
    sp = SoundPlayer(DAT)
    sp.ensure_loaded()
    if not sp.silence:  # 静音豁免
        assert sp.enabled, "再未静音状态下不可用"
        assert sp.bgm_source == "wav" and len(sp._thbgm_tracks) == 20
        sp.play_music("th07_02.mid")
        assert sp._current_bgm == "th07_02.mid"
        assert sp._wav_bgm is not None and sp._wav_bgm.name == "th07_02.wav"
        assert pygame.mixer.music.get_busy()
        # 切歌: 上一首 WAV 内存被新曲替换(任意时刻至多一首)
        sp.play_music("bgm/th07_03.mid")
        assert sp._wav_bgm.name == "th07_03.wav"
        assert pygame.mixer.music.get_busy()
        # fmt 没有的曲 → MIDI 回退
        sp.play_music("init.mid")
        assert sp._current_bgm == "init.mid" and sp._wav_bgm is None
        sp.stop_music()
        assert not pygame.mixer.music.get_busy()
    pygame.mixer.quit()
