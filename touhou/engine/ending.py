"""结局脚本(.end)解析与播放状态机 —— Ending.cpp ParseEndFile/OnUpdate 的移植。

.end 是文本脚本: '@' 指令行 + SJIS 文本行, 参数 \\0 分隔, 行 \\n 结尾。
指令集 (Ending.cpp ParseEndFile :171-429, 全覆盖):
- @b <path>     载入背景图 (LoadSurface(0), DrawEndingRect 全屏)
- @a vm s f     立绘/CG VM: sprites[vm] 执行 staff01.anm 脚本 ANM_OFFSET_STAFF+s,
                SetActiveSprite(ANM_OFFSET_STAFF+f)
- @V dist dur   背景滚动速度 = dist/dur (px/帧, 每帧 backgroundPos.y -= speed,
                夹到 0 停)
- @v y          直接设 backgroundPos.y
- @F <path>     载入另一份 .end 继续播 (end*.end 末尾接 staff00.end 进 staff
                roll); fallthrough 到 @R 清空立绘, LoadEnding 重置 line2Delay=8
- @R            清空全部立绘 VM
- @m <path>     LoadAudio(0)+PlayLoadedAudio(0) 切 BGM
- @M sec        FadeOutMusic(sec) (参数单位是秒, C++ 直接传给 FadeOutMusic(f32))
- @s d1 d2      行间隔: line2Delay=d1 (平常), topLineDelay=d2 (按住确认键)
- @c color      文字色 (atol 十进制, 0xRRGGBB 的十进制写法)
- @r t minw     等待 t 帧后清空已显示文本行 (timesFileParsed=0), minw 帧内
                不可跳过
- @w t minw     等待 t 帧再继续解析, minw 帧内不可跳过
- @0/@1/@2/@3 f 淡入淡出 (FadingEffect :99-165): @0 黑淡出(从黑场入) /
                @1 黑淡入(渐黑, 停在全黑) / @2 白淡出 / @3 白淡入(停在全白)
- @z            结束 (ParseEndFile 返回 ZUN_ERROR → 链移除 → curState=6 结算)
- 文本行        DrawVmTextFmt 画进 sprites[timesFileParsed] (槽位 y=392+i*16,
                x=64), 随后停 line2Delay 帧 (按住确认键时 topLineDelay)

解析: parse_end_ops (指令流); 旧简化接口 parse_end/parse_end_music 保留。
播放: EndingPlayer —— 每帧 tick() 一次的纯逻辑状态机 (OnUpdate 每帧一次
ParseEndFile + OnDraw 每帧 FadingEffect), 透出 bg/文本行/立绘/淡色/音乐事件,
渲染由 view/ending_view.py 消费。

结局文件选择 (Ending.cpp:499-505): numRetries!=0 → end{char}b.end(bad),
否则 end{shot}{type}.end (0=灵梦A 1=灵梦B ... 5=咲夜B);
staff roll 由结局文件末尾的 @Fdata/staff00.end 自动衔接 (staff01.anm CG,
BGM @m bgm/th07_15.mid)。
"""

from __future__ import annotations

import msgspec
from pathlib import PurePosixPath
from typing import Callable


class EndingSegment(msgspec.Struct):
    """一段结局: 背景图(archive 内名字, None=沿用上一段) + 文本行。"""

    bg: str | None = None
    lines: list[str] = msgspec.field(default_factory=list)


class EndingData(msgspec.Struct):
    """一份解析好的结局(渲染层只读)。"""

    character: int
    bad: bool
    path: str
    segments: list[EndingSegment]
    music: str = ""    # @m 指令的 BGM 文件名(如 th07_14.mid), 无则空
    ops: list[tuple] = msgspec.field(default_factory=list)  # 完整指令流(parse_end_ops)

    @property
    def lines(self) -> list[tuple[int, str]]:
        """全文本行: (段号, 文本), 供滚动定位背景段。"""
        return [(si, t) for si, seg in enumerate(self.segments) for t in seg.lines]

    # ---- 装载 ----
    @classmethod
    def load(cls, archive, character: int, *, bad: bool) -> "EndingData":
        path = ending_path(character, bad=bad)
        data = archive.load(path)
        return cls(character=character, bad=bad, path=path,
                   segments=parse_end(data), music=parse_end_music(data),
                   ops=parse_end_ops(data))

    @classmethod
    def generic(cls, character: int = 0) -> "EndingData":
        """结局资源缺失时的通用通关画面(简化兜底)。"""
        return cls(character=character, bad=False, path="",
                   segments=[EndingSegment(None, ["ALL CLEAR!!", "感谢游玩！"])],
                   ops=[("wait", 60, 60), ("text", "ALL CLEAR!!"),
                        ("text", "感谢游玩！"), ("wait", 600, 60), ("end",)])


def ending_path(character: int, *, bad: bool) -> str:
    """结局文件名 (Ending.cpp g_BadEndingPaths/g_NormalEndingPaths)。

    character 为 shotTypeAndCharacter(0..5); bad 结局按自机(//2)共用一份
    (end00b/end10b/end20b, 路径里机体号是 {char}0)。
    """
    if bad:
        return f"end{character // 2}0b.end"
    return f"end{character // 2}{character % 2}.end"


def parse_end_music(data: bytes) -> str:
    """提取首个 @m 音乐指令的文件名(Ending.cpp:298-301 'm': LoadAudio(0)+播放)。

    .end 行形如 ``@mbgm/th07_14.mid\\0``; 返回裸文件名(th07_14.mid), 无 @m 返回 ""。
    渲染层透出用(结局 BGM); @M 淡出由 EndingPlayer 的 music_events 透出。
    """
    for raw_line in data.split(b"\n"):
        first = raw_line.split(b"\0", 1)[0]
        if first.startswith(b"@m"):
            return PurePosixPath(first[2:].decode("ascii", "replace")).name
    return ""


def parse_end(data: bytes) -> list[EndingSegment]:
    """解析 .end: 提取背景图切换(@b)与文本行, 其余指令忽略。"""
    segments: list[EndingSegment] = []
    cur = EndingSegment()
    for raw_line in data.split(b"\n"):
        # 参数 \0 分隔; 首个 token 是指令或文本本体
        first = raw_line.split(b"\0", 1)[0]
        if first.startswith(b"@"):
            if first.startswith(b"@b"):  # @bdata/end/end00.jpg → 切背景开新段
                bg = PurePosixPath(first[2:].decode("ascii", "replace")).name
                if cur.lines or cur.bg is not None:
                    segments.append(cur)
                cur = EndingSegment(bg=bg)
            # @c/@w/@a 等其余指令: 旧简化视图用不上(完整指令流见 parse_end_ops)
            continue
        try:
            text = first.decode("cp932").strip("　 ")
        except UnicodeDecodeError:
            continue
        if text:
            cur.lines.append(text)
    if cur.lines or cur.bg is not None or not segments:
        segments.append(cur)
    return segments


# ---------------------------------------------------------------------------
# 完整指令流解析 (Ending.cpp ParseEndFile 指令 switch, :242-372)
# ---------------------------------------------------------------------------

def _basename(raw: bytes) -> str:
    """@b/@m/@F 的路径参数 → archive 内裸文件名 (data/end/end00.jpg → end00.jpg)。"""
    return PurePosixPath(raw.decode("ascii", "replace")).name


def _int_params(first: bytes, fields: list[bytes], count: int) -> list[int]:
    """ReadEndFileParameter 序列: 指令字符后的余串 + \\0 分隔的后续字段, atol。"""
    seq = [first[2:], *fields[1:]]
    out: list[int] = []
    for s in seq:
        s = s.strip()
        if not s:
            continue
        try:
            out.append(int(s))
        except ValueError:
            break  # atol 遇非数字即停
        if len(out) >= count:
            break
    while len(out) < count:
        out.append(0)
    return out


def parse_end_ops(data: bytes) -> list[tuple]:
    """把 .end 解析成指令流 (ParseEndFile 的指令全集; 文本行 → ("text", str))。

    op 元组: ("bg", name) ("face", vm, script, sprite) ("bg_scroll", dist, dur)
    ("bg_y", y) ("load", name) ("clear_faces",) ("music", name)
    ("music_fade", frames) ("line_speed", line2, top) ("color", 0xRRGGBB)
    ("wait_reset", t, minw) ("wait", t, minw) ("fade", type, frames)
    ("text", str) ("end",)。
    """
    ops: list[tuple] = []
    for raw_line in data.split(b"\n"):
        fields = raw_line.split(b"\0")
        first = fields[0]
        if first.startswith(b"@"):
            c = first[1:2]
            if c == b"b":
                ops.append(("bg", _basename(first[2:])))
            elif c == b"a":
                vm, script, sprite = _int_params(first, fields, 3)
                ops.append(("face", vm, script, sprite))
            elif c == b"V":
                dist, dur = _int_params(first, fields, 2)
                ops.append(("bg_scroll", dist, dur))
            elif c == b"v":
                (y,) = _int_params(first, fields, 1)
                ops.append(("bg_y", y))
            elif c == b"F":
                ops.append(("load", _basename(first[2:])))
            elif c == b"R":
                ops.append(("clear_faces",))
            elif c == b"m":
                ops.append(("music", _basename(first[2:])))
            elif c == b"M":
                (frames,) = _int_params(first, fields, 1)
                ops.append(("music_fade", frames))
            elif c == b"s":
                line2, top = _int_params(first, fields, 2)
                ops.append(("line_speed", line2, top))
            elif c == b"c":
                (color,) = _int_params(first, fields, 1)
                ops.append(("color", color))
            elif c == b"r":
                t, minw = _int_params(first, fields, 2)
                ops.append(("wait_reset", t, minw))
            elif c == b"w":
                t, minw = _int_params(first, fields, 2)
                ops.append(("wait", t, minw))
            elif c in (b"0", b"1", b"2", b"3"):
                (frames,) = _int_params(first, fields, 1)
                ops.append(("fade", int(c) + 1, frames))
            elif c == b"z":
                ops.append(("end",))
            continue
        try:
            text = first.decode("cp932").strip("　 ")
        except UnicodeDecodeError:
            continue
        if text:
            ops.append(("text", text))
    return ops


# ---------------------------------------------------------------------------
# 播放状态机 (Ending::OnUpdate/ParseEndFile/FadingEffect)
# ---------------------------------------------------------------------------

# fadeType (Ending.hpp; ParseEndFile @0..@3 → 1..4)
FADE_OUT_BLACK = 1   # @0: 黑幕淡出(从黑场进入)
FADE_IN_BLACK = 2    # @1: 黑幕淡入(渐黑, 停在全黑)
FADE_OUT_WHITE = 3   # @2: 白幕淡出
FADE_IN_WHITE = 4    # @3: 白幕淡入(渐白, 停在全白)

TEXT_MAX_SLOTS = 15      # sprites[15] (Ending.cpp:493-498)
TEXT_X = 64              # sprites[i].pos = (64, i*16+392)
TEXT_Y0 = 392
TEXT_LINE_H = 16


class EndingLine(msgspec.Struct):
    """一行已显示文本 (DrawVmTextFmt 画进 sprites[timesFileParsed])。"""

    text: str
    color: int           # 0xRRGGBB (@c)


class EndingPlayer:
    """.end 指令流的逐帧播放器(纯逻辑; 渲染/放音由 view 消费)。

    tick() 一帧 = OnUpdate 的一次 ParseEndFile + OnDraw 的 FadingEffect 推进。
    透出: bg_name/bg_y(背景与滚动) / texts(已显示文本行) / faces(立绘槽位)
    / fade_overlay()(淡色覆盖) / music_events / done(@z 或 @F 失败)。
    advance_held = 确认键按住(行间隔换 topLineDelay, ParseEndFile:399-408);
    advance_pressed = 确认键按下沿(minWait 耗尽后提前结束等待, :199-205/227-234)。
    """

    def __init__(self, data: "bytes | list[tuple]",
                 loader: "Callable[[str], bytes | None] | None" = None) -> None:
        self._ops = parse_end_ops(data) if isinstance(data, bytes) else list(data)
        self._loader = loader    # @F 续载用: (archive 裸文件名) -> bytes | None
        self._pc = 0
        # ParseEndFile 计时器/参数 (LoadEnding: line2Delay=8, timer2=0)
        self.timer2 = 0
        self.min_wait = 0        # minWaitFrames (文本行/@w)
        self.timer3 = 0
        self.min_wait_reset = 0  # minWaitResetFrames (@r)
        self.line2_delay = 8
        self.top_line_delay = 8
        self.text_color = 0xFFFFFF
        # 背景 (backgroundPos.y/backgroundScrollSpeed)
        self.bg_name: str | None = None
        self.bg_y = 0.0
        self.bg_scroll = 0.0
        # 立绘槽位: vm_idx → (anm_script_idx, anm_sprite_idx) (@a)
        self.faces: dict[int, tuple[int, int]] = {}
        self.faces_version = 0   # faces 变更计数(view 据此重建/清 VM)
        self.texts: list[EndingLine] = []
        # 淡入淡出 (FadingEffect)
        self.fade_type = 0
        self.fade_frames = 0
        self.time_fading = 0
        # 音乐事件: ("play", name) / ("fadeout", frames)
        self.music_events: list[tuple] = []
        self.done = False

    # ---- 每帧 ----
    def tick(self, *, advance_held: bool = False,
             advance_pressed: bool = False) -> None:
        if self.done:
            return
        self._advance_fade()     # OnDraw 的 FadingEffect (每帧, 与解析停止无关)
        self._parse_once(advance_held=advance_held,
                         advance_pressed=advance_pressed)

    def _advance_fade(self) -> None:
        """FadingEffect (Ending.cpp:99-165) 的计时推进; 颜色见 fade_overlay。"""
        if self.fade_type in (FADE_OUT_BLACK, FADE_OUT_WHITE):
            if self.time_fading >= self.fade_frames:
                self.fade_type = 0       # 淡出完成 → 无覆盖
            else:
                self.time_fading += 1
        elif self.fade_type in (FADE_IN_BLACK, FADE_IN_WHITE):
            if self.time_fading < self.fade_frames:
                self.time_fading += 1    # 淡入完成 → 停在不透明色

    def fade_overlay(self) -> tuple[int, int, int, int] | None:
        """本帧淡色覆盖 (r, g, b, a); None = 无覆盖 (endingFadeRectColor alpha==0)。"""
        ft = self.fade_type
        if ft == 0 or self.fade_frames <= 0:
            return None
        t, frames = self.time_fading, self.fade_frames
        if ft in (FADE_OUT_BLACK, FADE_OUT_WHITE):
            if t >= frames:
                return None
            a = 255 - t * 255 // frames
            return (0, 0, 0, a) if ft == FADE_OUT_BLACK else (255, 255, 255, a)
        a = min(255, t * 255 // frames)
        return (0, 0, 0, a) if ft == FADE_IN_BLACK else (255, 255, 255, a)

    def _scroll_bg(self) -> None:
        """ParseEndFile 的 stop 收尾 (:420-427): 背景上滚, 夹到 0 停。"""
        self.bg_y -= self.bg_scroll
        if self.bg_y <= 0.0:
            self.bg_y = 0.0
            self.bg_scroll = 0.0

    def _parse_once(self, *, advance_held: bool, advance_pressed: bool) -> None:
        # @r 等待 (timer3): 归零当帧清空文本行并继续解析 (:190-217)
        if self.timer3 > 0:
            self.timer3 -= 1
            if self.min_wait_reset > 0:
                self.min_wait_reset -= 1
            elif advance_pressed:
                self.timer3 = 0
            if self.timer3 <= 0:
                self.texts.clear()   # sprites[*].pendingInterrupt=2, timesFileParsed=0
            else:
                self._scroll_bg()
                return
        # @w/文本行等待 (timer2) (:219-236)
        if self.timer2 > 0:
            self.timer2 -= 1
            if self.min_wait > 0:
                self.min_wait -= 1
            elif advance_pressed:
                self.timer2 = 0
            self._scroll_bg()
            return
        while True:
            if self._pc >= len(self._ops):
                self.done = True     # 无 @z 兜底: 文件播完即结束
                return
            op = self._ops[self._pc]
            self._pc += 1
            kind = op[0]
            if kind == "bg":
                self.bg_name = op[1]
            elif kind == "face":
                self.faces[op[1]] = (op[2], op[3])
                self.faces_version += 1
            elif kind == "bg_scroll":
                self.bg_scroll = op[1] / op[2] if op[2] else 0.0
            elif kind == "bg_y":
                self.bg_y = float(op[1])
            elif kind == "load":
                data = self._loader(op[1]) if self._loader is not None else None
                if data is None:
                    self.done = True  # LoadEnding 失败 → ZUN_ERROR → 链移除
                    return
                self._ops = parse_end_ops(data)
                self._pc = 0
                self.line2_delay = 8       # LoadEnding 重置 (:446)
                self.timer2 = 0
                self.faces.clear()         # @F fallthrough @R (:292-297)
                self.faces_version += 1
            elif kind == "clear_faces":
                self.faces.clear()
                self.faces_version += 1
            elif kind == "music":
                self.music_events.append(("play", op[1]))
            elif kind == "music_fade":
                self.music_events.append(("fadeout", op[1]))
            elif kind == "line_speed":
                self.line2_delay, self.top_line_delay = op[1], op[2]
            elif kind == "color":
                self.text_color = op[1]
            elif kind == "wait_reset":
                self.timer3, self.min_wait_reset = op[1], op[2]
                self._scroll_bg()
                return
            elif kind == "wait":
                self.timer2, self.min_wait = op[1], op[2]
                self._scroll_bg()
                return
            elif kind == "fade":
                self.fade_type, self.time_fading, self.fade_frames = op[1], 0, op[2]
            elif kind == "text":
                self.texts.append(EndingLine(op[1], self.text_color))
                d = self.top_line_delay if advance_held else self.line2_delay
                self.timer2 = d
                self.min_wait = d
                self._scroll_bg()
                return
            elif kind == "end":
                self.done = True
                return
