"""SE/BGM 播放层 —— pygame.mixer 包装, 消费 impl 透出的音效/BGM 事件。

- 无声卡/headless 容错: mixer 未初始化或 dummy 声卡(SDL_AUDIODRIVER=dummy,
  其 music 原生调用会间歇死锁)则整体静音(ensure_loaded 不炸);
- wav 运行时从 th07.dat 解到内存(io.BytesIO + pygame.mixer.Sound),
  仓库不留二进制资源;
- 引擎侧已完成同帧同音去重/5 槽上限(schema/sound.SoundQueue,
  SoundPlayer.cpp PlaySoundByIdx), 这里只按帧播放 frame_sounds;
- 每个 SE 索引有独立音量(SOUND_BUFFER_IDX_VOL 的百分之一分贝 → 线性增益);
- BGM 双音源(Supervisor.cpp PlayAudio / cfg.musicMode):
  WAV(thbgm.dat, 原版默认) 优先, 缺失/失败自动回退 MIDI(.mid);
  WAV 循环 = intro 播一遍 + loop 段无限循环(CWaveFile::ResetFile,
  dsutil.cpp:1071-1112), 用 mixer.music.get_pos() 逐帧轮询、到段尾
  play(start=循环点) 回卷 —— 有 ≤1 帧的接缝, 见 _poll_wav_loop 注释;
  轮询由应用壳每帧调用 poll_loop()(不限于对局场景, 否则标题/结算等
  画面 WAV 曲播完一遍就停, BUGS.md#4);
- BGM 暂停/恢复(AUDIO_PAUSE/AUDIO_UNPAUSE, SoundPlayer.cpp:846-868):
  原版仅 WAV 音源暂停(MIDI 走 mci 不暂停), pause_music/unpause_music
  同此门控; pygame 2.6 实测暂停时 get_busy()=False, 轮询须跳过暂停态,
  否则误判"播完"回卷并解除暂停。
"""

from __future__ import annotations

import io
import os
from pathlib import Path

import pygame

from ...exceptions import ThbgmFormatError
from ...logger import logger as log
from ...schema.archive import GameArchive
from ...schema.sound import SE_FILES, SE_VOLUMES
from ...schema.thbgm import ThbgmTrack, build_wav, check_thbgm_header, parse_fmt


def _db_to_gain(db_hundredths: int) -> float:
    """DirectSound 百分之一分贝 → pygame 线性音量 (0dB=1.0, -2000=0.1)。"""
    return max(0.0, min(1.0, 10.0 ** (db_hundredths / 2000.0)))


class SoundPlayer:
    """游戏内 SE/BGM 播放器。资源懒加载(首次 ensure_loaded 才开包)。"""

    def __init__(
        self, data_path: str | Path, bgm_path: str | Path | None = None
    ) -> None:
        self._data_path = Path(data_path)
        # 显式 thbgm.dat 路径(WorldData.bgm_dat); None = 与 th07.dat 同目录推导
        self._bgm_path_override = Path(bgm_path) if bgm_path else None
        self._loaded = False
        self._enabled = False
        self._archive: GameArchive | None = None
        self.sounds: dict[int, pygame.mixer.Sound] = {}
        self._current_bgm = ""
        self._bgm_volume = 1.0  # BGM 主音量(Option, 0-1)
        self._se_volume = 1.0  # SE 主音量(Option, 0-1)
        # ---- WAV BGM (thbgm.dat) ----
        self._bgm_source = "wav"  # 音源偏好: "wav"(优先) / "midi"(强制)
        self._thbgm_tracks: dict[str, ThbgmTrack] = {}
        self._thbgm_path: Path | None = None
        self._wav_bgm: ThbgmTrack | None = None  # 当前 WAV 曲(循环轮询用)
        self._wav_pass_ms = 0.0  # 本播段长度 ms(首遍=全曲, 之后=循环段)
        self._music_paused = False  # BGM 暂停态(AUDIO_PAUSE)

    # ---- 资源 ----
    def ensure_loaded(self) -> None:
        """加载全部 SE wav; mixer 不可用/资源缺失时静默降级为静音。"""
        if self._loaded:
            return
        self._loaded = True
        # dummy 声卡(headless/CI/测试, SDL_AUDIODRIVER=dummy): mixer 能 init
        # 成功, 但 mixer.music 原生调用(pause 等)会间歇死锁 —— 视同无声卡静音
        if os.environ.get("SDL_AUDIODRIVER") == "dummy":
            return
        if not pygame.mixer.get_init():
            return  # 无声卡/headless: 静音
        try:
            arc = GameArchive.open(self._data_path)
            loaded: dict[str, bytes] = {}
            for idx, name in SE_FILES.items():
                if name not in loaded:
                    loaded[name] = arc.load(name)
                snd = pygame.mixer.Sound(file=io.BytesIO(loaded[name]))
                snd.set_volume(_db_to_gain(SE_VOLUMES[idx]) * self._se_volume)
                self.sounds[int(idx)] = snd
        except (OSError, KeyError, pygame.error) as e:
            log.warning("音效资源加载失败, 静音运行: {}", e)
            self.sounds = {}
            return
        try:
            pygame.mixer.set_num_channels(16)
        except pygame.error:
            pass
        self._archive = arc
        self._setup_thbgm(arc)
        self._enabled = True

    def _setup_thbgm(self, arc: GameArchive) -> None:
        """探测 thbgm.dat + 解析 thbgm.fmt; 任一失败则留空, 走 MIDI 回退。"""
        thbgm_path = self._bgm_path_override or self._data_path.with_name("thbgm.dat")
        if not check_thbgm_header(thbgm_path):
            return
        try:
            # C++ LoadFmt("bgm/thbgm.fmt") (Supervisor.cpp:732);
            # FileSystem 会去掉目录前缀, 实包内条目名为 "thbgm.fmt"
            for key in ("bgm/thbgm.fmt", "thbgm.fmt"):
                if key in arc:
                    self._thbgm_tracks = parse_fmt(arc.load(key))
                    break
        except (OSError, KeyError, ValueError) as e:
            log.warning("thbgm.fmt 解析失败, BGM 回退 MIDI: {}", e)
            return
        if self._thbgm_tracks:
            self._thbgm_path = thbgm_path

    # ---- 每帧 ----
    def poll_loop(self) -> None:
        """WAV 循环点回卷轮询(应用壳每帧调用, 与场景无关)。

        只在 play_frame(对局内)轮询会让标题/结算/音乐室等场景的 WAV 曲
        播完一遍就停(BUGS.md#4); 提到应用壳主循环逐帧调用。
        """
        if self._enabled:
            self._poll_wav_loop()

    def play_frame(
        self,
        sounds: list[int],
        bgm_events: list[tuple],
        bgm_paths: tuple[str, ...] = (),
    ) -> None:
        """消费 impl 的一帧事件: frame_sounds 逐个播, bgm_events 切歌/淡出。"""
        if not self._enabled:
            return
        self._poll_wav_loop()
        for idx in sounds:
            snd = self.sounds.get(int(idx))
            if snd is not None:
                try:
                    snd.play()
                except pygame.error:
                    pass
        for ev in bgm_events:
            if ev[0] == "music":
                idx = ev[1]
                if 0 <= idx < len(bgm_paths) and bgm_paths[idx]:
                    # "bgm/th07_02.mid" → "th07_02.mid" (Gui.cpp:952-957)
                    self.play_music(bgm_paths[idx].split("/")[-1])
            elif ev[0] == "music_file":
                self.play_music(ev[1])
            elif ev[0] == "fadeout":
                self.fadeout_music(ev[1])

    # ---- BGM ----
    def set_bgm_source(self, source: str) -> None:
        """音源选择(供 Option 菜单): "wav" 优先 thbgm.dat, "midi" 强制 MIDI。"""
        if source not in ("wav", "midi"):
            raise ValueError(f"未知音源: {source!r}")
        self._bgm_source = source

    @property
    def bgm_source(self) -> str:
        """实际生效的音源: 偏好 wav 但 thbgm.dat 不可用时回落 midi。"""
        return "wav" if (self._bgm_source == "wav" and self._thbgm_tracks) else "midi"

    @property
    def current_bgm(self) -> str:
        """当前曲目名(切音源后重播用); 未在播 = ""。"""
        return self._current_bgm

    # ---- 主音量(Option 菜单; 0-1 线性) ----
    def set_bgm_volume(self, v: float) -> None:
        """BGM 主音量。实时作用于 mixer.music; 之后每次 play 也会带上。"""
        self._bgm_volume = max(0.0, min(1.0, float(v)))
        if not self._enabled:
            return
        try:
            pygame.mixer.music.set_volume(self._bgm_volume)
        except pygame.error:
            pass

    def set_se_volume(self, v: float) -> None:
        """SE 主音量: 在各 SE 独立音量(SE_VOLUMES)基础上整体缩放。"""
        self._se_volume = max(0.0, min(1.0, float(v)))
        for idx, snd in self.sounds.items():
            try:
                snd.set_volume(_db_to_gain(SE_VOLUMES[idx]) * self._se_volume)
            except (pygame.error, KeyError):
                pass

    def play_music(self, name: str) -> None:
        """播 BGM (循环); WAV(thbgm.dat) 优先, 失败回退 .mid; 失败仅记日志。"""
        if not self._enabled:
            return
        if name == self._current_bgm:
            # 暂停中再点同名曲 = 恢复播放(防御: 正常由 unpause_music 负责)
            self.unpause_music()
            return
        if self.bgm_source == "wav" and self._play_wav(name):
            self._current_bgm = name
            return
        self._play_midi(name)

    # ---- BGM 暂停/恢复(AUDIO_PAUSE/AUDIO_UNPAUSE, SoundPlayer.cpp:846-868) ----
    def pause_music(self) -> None:
        """暂停 BGM。原版仅 WAV 音源响应 AUDIO_PAUSE
        (cfg.musicMode == MUSIC_WAV 才 Pause, MIDI 继续), 同此门控。"""
        if not self._enabled or self._music_paused:
            return
        if self.bgm_source != "wav":
            return  # MIDI 模式原版不暂停 (SoundPlayer.cpp:847)
        try:
            pygame.mixer.music.pause()
        except pygame.error:
            pass
        self._music_paused = True

    def unpause_music(self) -> None:
        """恢复 BGM (AUDIO_UNPAUSE, SoundPlayer.cpp:861-868)。"""
        if not self._enabled or not self._music_paused:
            return
        self._music_paused = False
        try:
            pygame.mixer.music.unpause()
        except pygame.error:
            pass

    def _play_midi(self, name: str) -> None:
        try:
            data = self._archive.load(name)
            pygame.mixer.music.load(io.BytesIO(data))
            pygame.mixer.music.set_volume(self._bgm_volume)
            pygame.mixer.music.play(-1)
            self._wav_bgm = None
            self._music_paused = False  # play() 会解除暂停态, 标志同步
            self._current_bgm = name
        except (OSError, KeyError, pygame.error) as e:
            log.warning("BGM 播放失败({}): {}", name, e)

    def _play_wav(self, name: str) -> bool:
        """从 thbgm.dat 按偏移取整曲 PCM 包成 wav 播放; 曲目不在 fmt/读失败返回 False。"""
        # PlayAudio: "bgm/th07_02.mid" 扩展名换 .wav 按 basename 查 fmt
        # (Supervisor.cpp:1397-1424, GetFmtIndexByName SoundPlayer.cpp:198)
        base = name.replace("\\", "/").split("/")[-1]
        stem = base.rsplit(".", 1)[0] if "." in base else base
        track = self._thbgm_tracks.get(stem + ".wav")
        if track is None:
            return False
        try:
            # 整曲单段载入(10~43MB), 内存里至多当前一首
            with open(self._thbgm_path, "rb") as f:
                f.seek(track.start_offset)
                pcm = f.read(track.total_length)
            if len(pcm) < track.total_length:
                raise ThbgmFormatError("thbgm.dat 截断")
            pygame.mixer.music.load(io.BytesIO(build_wav(track, pcm)))
            pygame.mixer.music.set_volume(self._bgm_volume)
            pygame.mixer.music.play(0)
            self._wav_bgm = track
            self._music_paused = False  # play() 会解除暂停态, 标志同步
            self._wav_pass_ms = track.total_seconds * 1000.0
            return True
        except (OSError, pygame.error) as e:
            log.warning("WAV BGM 播放失败({}), 回退 MIDI: {}", name, e)
            self._wav_bgm = None
            return False

    def _poll_wav_loop(self) -> None:
        """WAV 循环点回卷: 每帧轮询, 到段尾从循环点重播。

        mixer.music 不支持段内循环, 只能在段尾 play(start=intro_seconds)
        重定位 —— 接缝 ≤ 轮询间隔(1 帧) + 重定位延迟, 曲目 loop 段边界
        本身是乐句头, 实际不可闻; get_busy() 兜底轮询漏掉的自然播完。
        """
        if self._wav_bgm is None:
            return
        if self._music_paused:
            return  # 暂停中 get_busy()=False(pygame 2.6 实测), 防误判播完回卷
        try:
            if not pygame.mixer.music.get_busy():
                self._rewind_wav()
                return
            pos = pygame.mixer.music.get_pos()
            if pos >= 0 and pos >= self._wav_pass_ms - 30:
                self._rewind_wav()
        except pygame.error:
            pass

    def _rewind_wav(self) -> None:
        track = self._wav_bgm
        pygame.mixer.music.play(0, start=track.intro_seconds)
        self._wav_pass_ms = track.loop_seconds * 1000.0

    def fadeout_music(self, seconds: float) -> None:
        """FadeOutMusic (Supervisor.cpp:1456); seconds 为秒。"""
        if not self._enabled:
            return
        try:
            pygame.mixer.music.fadeout(int(seconds * 1000))
        except pygame.error:
            pass
        self._wav_bgm = None
        self._music_paused = False
        self._current_bgm = ""

    def stop_music(self) -> None:
        if not self._enabled:
            return
        try:
            pygame.mixer.music.stop()
        except pygame.error:
            pass
        self._wav_bgm = None
        self._music_paused = False
        self._current_bgm = ""
