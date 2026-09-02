"""对话消息系统(msg1.dat..msg8.dat) —— Pythonic。

对照 th07 反编译源码 `Gui.cpp/.hpp` 还原:

文件格式(MsgRawHeader/MsgRawInstr, Gui.hpp:73-85):
- 头: `i32 numInstrs` + numInstrs 个 i32 指令流偏移(相对文件头,
  C 里 LoadMsg 时加 `&msgFile->numInstrs` 基址, 即文件起点)。
- 每条指令: `u16 time; u8 opcode; u8 argsize; args[argsize]`,
  一条消息以 MSG_DELETE(0) 结束。
- 指令参数布局(Gui.hpp:36-63):
  SHOW_PORTRAIT/CHANGE_FACE: i16 portraitIdx, i16 anmScriptIdx
  DIALOGUE/TEXT_INTRODUCE:   i16 textColor, i16 textLine, char text[](NUL 结尾, Shift-JIS)
  PAUSE:                     i32 duration
  SWITCH:                    i16 unkIdx, u8 interrupt
  MUSIC:                     i32 musicIdx
  ALLOW_SKIP:                u8 skippable(直接读 args 首字节, RunMsg 的 `*(u8*)&args`)

运行时(GuiImpl::MsgRead/RunMsg, Gui.cpp:735-1060):
- MsgRead(idx): 越界则无操作; 否则整个 VM 清零重来, currentMsgIdx=idx,
  timer=0, dialogueSkippable=1, 字号 15, 颜色 [0xe8f0ff, 0xffe8f0]。
  (C 还在此清场: RemoveAllBullets(1)/RemoveAllEnemies(0,0)/RemoveAllItems,
  世界侧副作用由宿主接, 见 ecl_host.GameEclHost.msg_read)
- RunMsg() 每帧一次: timer 从 0 逐帧推进, `while timer >= cur.time` 连续
  执行到点指令; PAUSE 不计 timer 而是数 framesElapsedDuringPause,
  按 Z(SHOOT, 新按下且已停满 12 帧)可提前结束, 按住 Ctrl(SKIP)且
  dialogueSkippable=1 时直接跳过; MSG_DELETE 结束消息(currentMsgIdx=-1)。
- APPEAR_ENEMY: ignoreWaitCounter++ → MsgWait 当帧返回 False(放时间轴
  过去刷 Boss, 对话继续)。MsgWait = currentMsgIdx>=0 且 ignoreWaitCounter==0。
- NEXT_LEVEL(非 practice/replay, stage<6): currentMsgIdx=-2 + 透出事件;
  HasCurrentMsgIdx 对 -2 仍返回 True(世界保持门控)。

打字机逐字是视觉效果(原版由 text.anm 脚本 + GDI 贴字实现, 这里在 VM 里
对每条已亮文本行做 reveal 计数, 每 2 帧 1 字), 不影响指令推进语义。

打字机音考据结论: 原版**没有**对话打字音效 —— RunMsg 的
MSG_DIALOGUE/MSG_TEXT_INTRODUCE 路径 (Gui.cpp:879-905/961-970) 只建 VM
贴字不播 SE; AnmManager::ExecuteScript 全部 opcode 无音效指令;
SoundPlayer.hpp 音效表也无对话项。原版文本行是整行 GDI 贴进纹理后由
text.anm script 1792/1793 做 12 帧 alpha 淡入(无逐字 reveal, 本层逐字
计数本身是近似)。故无音可接, 注释留证。

本模块纯逻辑, 不碰 pygame/渲染; 输入(Z 新按下/Ctrl 按住)由调用方注入。

th08(东方永夜抄)兼容(Reference/th08-ref/src/Gui.hpp:52-74/158-175,
Gui.cpp:2178-2194 LoadMsg / :767-778 DecryptGuiMessageText):
- 文件头/指令布局与 th07 逐字节同构(头=count i32+偏移表; 指令 time u16/
  opcode u8/instructionSize u8/args), ``MsgFile.parse`` 直接复用;
- 差异一: 文本逐字节 XOR 0x77(DecryptGuiMessageText) —— parse 的
  ``text_xor`` 参数, MsgInstr.text_xor 随指令携带, dialogue/plain_text
  视图解码时应用(th07 = 0);
- 差异二: opcode 扩到 0-22(Gui.hpp:52-74): 15/17 立绘配置、16 说话人
  文本、18 文本框显隐、19/20 顶/底部文本(均纯视觉或落文本行)、
  21 二选一(GUI_MSG_SHOW_SELECTION, Gui.cpp:540-573)/22 读选项分支
  (GUI_MSG_READ_SELECTED_MESSAGE, Gui.cpp:574-578: finalStageRoute =
  selectedOption 并 MsgRead(selectedOption+1));
- 差异三: 立绘 4 槽(th07 为 2) —— MsgVm 的 ``num_portraits`` 参数,
  SWITCH 的 idx<num_portraits → 立绘。
"""

from __future__ import annotations

import struct
import msgspec
from enum import IntEnum
from typing import Optional

from ..exceptions import MsgParseError

# ---- 指令集(照抄 Gui.hpp MsgOpcode) ----


class MsgOpcode(IntEnum):
    DELETE = 0
    SHOW_PORTRAIT = 1
    CHANGE_FACE = 2
    DIALOGUE = 3
    PAUSE = 4
    SWITCH = 5
    APPEAR_ENEMY = 6
    MUSIC = 7
    TEXT_INTRODUCE = 8
    STAGERESULTS = 9
    FREEZE = 10
    NEXT_LEVEL = 11
    FADEOUT_MUSIC = 12
    ALLOW_SKIP = 13
    FADE_IN_EFFECT = 14
    # ---- th08 新增(opcode 15-22, Gui.hpp:67-74; th07 数据中不出现) ----
    CONFIGURE_ALL_PORTRAITS = 15  # 全立绘配置(纯视觉)
    SHOW_SPEAKER_TEXT = 16  # 说话人文本(落对话行, dialogueLineIndex 自增)
    CONFIGURE_PORTRAIT = 17  # 单立绘配置(纯视觉)
    SET_TEXT_BOX_VISIBLE = 18  # 文本框显隐(纯视觉)
    SHOW_TOP_TEXT = 19  # 顶部文本(落对话行 0)
    SHOW_BOTTOM_TEXT = 20  # 底部文本(落对话行 1)
    SHOW_SELECTION = 21  # 二选一(wait 式 + 选项)
    READ_SELECTED_MESSAGE = 22  # 按选项读消息分支


# ---- 解析 ----


class MsgInstr(msgspec.Struct, frozen=True):
    """一条 msg 指令。args 保留原始字节, 按 opcode 提供解析视图。

    text_xor: 文本解码 XOR 值(th08=0x77, th07=0), parse 时随文件参数带入。
    """

    time: int
    opcode: int
    args: bytes
    text_xor: int = 0

    @property
    def portrait(self) -> tuple[int, int]:
        """SHOW_PORTRAIT/CHANGE_FACE: (portraitIdx, anmScriptIdx)。"""
        return struct.unpack_from("<hh", self.args, 0)

    def _decode_text(self, raw: bytes) -> str:
        """文本字节 → 字符串: XOR(th08) → NUL 截断 → Shift-JIS 容错解码。"""
        if self.text_xor:
            raw = bytes(b ^ self.text_xor for b in raw)
        return raw.split(b"\x00")[0].decode("shift_jis", errors="replace")

    @property
    def dialogue(self) -> tuple[int, int, str]:
        """DIALOGUE/TEXT_INTRODUCE: (textColor, textLine, text)。

        文本是 Shift-JIS, NUL 结尾; 坏字节容错替换不炸; th08 另有 XOR 0x77。
        """
        color, line = struct.unpack_from("<hh", self.args, 0)
        return color, line, self._decode_text(self.args[4:])

    @property
    def plain_text(self) -> str:
        """th08 纯文本指令(SHOW_SPEAKER_TEXT/SHOW_TOP_TEXT/SHOW_BOTTOM_TEXT):
        args 整体即加密文本(GuiMessagePlainTextArgs, Gui.hpp:121-124)。"""
        return self._decode_text(self.args)

    @property
    def pause_duration(self) -> int:
        v: int = struct.unpack_from("<i", self.args, 0)[0]
        return v

    @property
    def switch(self) -> tuple[int, int]:
        """SWITCH: (unkIdx, interrupt)。unkIdx<2 → 立绘, 否则文本行 unkIdx-2。"""
        return struct.unpack_from("<hB", self.args, 0)

    @property
    def music_idx(self) -> int:
        v: int = struct.unpack_from("<i", self.args, 0)[0]
        return v

    @property
    def allow_skip(self) -> int:
        """ALLOW_SKIP: C 直接读 args 首字节 (`*(u8*)&args`)。"""
        return self.args[0] if self.args else 0


class MsgFile(msgspec.Struct):
    """解析后的 msg 文件: messages[i] = 第 i 条消息的指令序列(含 DELETE)。"""

    messages: list[tuple[MsgInstr, ...]]

    @classmethod
    def parse(cls, data: bytes, *, text_xor: int = 0) -> "MsgFile":
        """解析 msg 文件; text_xor = 文本解码 XOR 值(th08=0x77, th07=0)。"""
        if len(data) < 4:
            raise MsgParseError("文件太小, 没有 numInstrs")
        (num,) = struct.unpack_from("<i", data, 0)
        if not (0 <= num <= 4096) or 4 + 4 * num > len(data):
            raise MsgParseError(f"非法 numInstrs={num} (size={len(data)})")
        offsets = struct.unpack_from(f"<{num}i", data, 4)
        messages: list[tuple[MsgInstr, ...]] = []
        for idx, off in enumerate(offsets):
            if not (0 <= off < len(data)):
                raise MsgParseError(f"msg {idx}: 偏移越界 (off={off})")
            instrs: list[MsgInstr] = []
            pos = off
            while pos + 4 <= len(data):
                time, opcode, argsize = struct.unpack_from("<HBB", data, pos)
                if pos + 4 + argsize > len(data):
                    raise MsgParseError(f"msg {idx}: 指令截断 (pos={pos})")
                instrs.append(
                    MsgInstr(time, opcode, data[pos + 4 : pos + 4 + argsize], text_xor)
                )
                pos += 4 + argsize
                if opcode == MsgOpcode.DELETE:
                    break
            else:
                raise MsgParseError(f"msg {idx}: 缺 DELETE 终止 (off={off})")
            messages.append(tuple(instrs))
        return cls(messages)

    @property
    def num_messages(self) -> int:
        return len(self.messages)


# ---- 运行时状态 ----

FONT_SIZE = 15  # MsgRead: fontSize=15
TEXT_COLORS_A = (0xE8F0FF, 0xFFE8F0, 0, 0)  # 前景色(AARRGGBB 低 24 位作 RGB)
TEXT_COLORS_B = (0, 0, 0, 0)  # 描边色
PAUSE_MIN_FRAMES = 12  # Z 键提前结束 PAUSE 的最短停留(RunMsg: <12 不响应)
TYPEWRITER_FRAMES_PER_CHAR = 2  # 打字机速度(视觉近似, 原版由 anm 脚本控制)

# SWITCH 的 interrupt 约定(由 msg 脚本实际用法归纳):
# 立绘 1=入场, 3=亮(说话方), 4=暗(非说话方), 5=退场; 文本行同组值控制显隐。


class MsgPortraitState(msgspec.Struct):
    """一侧立绘的渲染状态(C 里是 AnmVm, 这里只留渲染需要的最小字段)。"""

    visible: bool = False
    face: int = 0  # CHANGE_FACE/SHOW_PORTRAIT 的 anmScriptIdx
    pending_interrupt: int = 0

    @property
    def speaking(self) -> bool:
        """亮暗: SWITCH interrupt 3=说话方(亮), 4=非说话方(暗)。"""
        return self.pending_interrupt != 4

    @property
    def exited(self) -> bool:
        """interrupt 5 = 退场(滑出), 不再绘制。"""
        return self.pending_interrupt == 5


class MsgLineState(msgspec.Struct):
    """一行对话/介绍文本的渲染状态。"""

    visible: bool = False
    text: str = ""
    color: int = 0
    reveal: int = 0  # 打字机已显示字符数
    pending_interrupt: int = 0

    def set_text(self, text: str, color: int) -> None:
        self.visible = True
        self.text = text
        self.color = color
        self.reveal = 0

    def clear(self) -> None:
        self.visible = False
        self.text = ""
        self.reveal = 0

    @property
    def shown_text(self) -> str:
        return self.text[: self.reveal]


class MsgVm:
    """对话 VM(C GuiMsgVm + GuiImpl::RunMsg 的每帧语义)。

    num_portraits: 立绘槽数(th07=2, th08=4, Gui.hpp GuiMsgVm.portraits[4]);
    SWITCH 的 idx<num_portraits → 立绘, 否则文本行 idx-num_portraits。
    """

    def __init__(
        self, msg_file: Optional[MsgFile] = None, *, num_portraits: int = 2
    ) -> None:
        self.msg_file = msg_file
        self.num_portraits = num_portraits
        self.current_msg_idx = -1
        self.instr_idx = 0  # 当前指令下标(模拟 curInstr 指针)
        self.timer = 0
        self.frames_elapsed_during_pause = 0
        self.ignore_wait_counter = 0
        self.dialogue_skippable = 1
        self.font_size = FONT_SIZE
        self.portraits = [MsgPortraitState() for _ in range(num_portraits)]
        self.dialogue_lines = [MsgLineState(), MsgLineState()]
        self.intro_lines = [MsgLineState(), MsgLineState()]
        self.finished_stage = 0  # STAGERESULTS 置 1
        self.events: list[str] = []  # 透出事件: "music:idx"/"next_level" 等
        # ---- th08 扩展(Gui.hpp GuiMsgVm 尾部字段) ----
        self.dialogue_line_index = 0  # op16 说话人文本的落行游标
        self.selected_option = 0  # op21 二选一的当前选项(0/1)
        self.final_stage_route: int | None = None  # op22 写出(Gui.cpp:574-578)
        # PAUSE 的 Z 提前结束最短停留(th08 MsgRead 置 waitThreshold=6,
        # Gui.cpp:241; th07 恒 12)
        self.pause_min_frames = PAUSE_MIN_FRAMES
        self._type_timer = 0

    # ---- C 访问器 ----
    def msg_wait(self) -> bool:
        """Gui::MsgWait: True = 消息仍在显示(时间轴应停)。"""
        if self.ignore_wait_counter > 0:
            return False
        return self.current_msg_idx >= 0

    def has_current_msg_idx(self) -> bool:
        """Gui::HasCurrentMsgIdx: 对话门控(-2 也算)。"""
        return self.current_msg_idx >= 0 or self.current_msg_idx == -2

    def is_dialogue_skippable(self) -> int:
        return self.dialogue_skippable

    @property
    def active(self) -> bool:
        return self.current_msg_idx >= 0

    @property
    def cur(self) -> MsgInstr:
        # 仅在 read() 成功后有意义(active 为真时 msg_file 必已加载)
        assert self.msg_file is not None
        return self.msg_file.messages[self.current_msg_idx][self.instr_idx]

    # ---- MsgRead ----
    def read(self, msg_idx: int) -> None:
        """GuiImpl::MsgRead: 越界无操作; 否则整个 VM 清零重来。"""
        if self.msg_file is None or self.msg_file.num_messages <= msg_idx:
            return
        self.current_msg_idx = msg_idx
        self.instr_idx = 0
        self.timer = 0
        self.frames_elapsed_during_pause = 0
        self.ignore_wait_counter = 0
        self.dialogue_skippable = 1
        self.font_size = FONT_SIZE
        self.portraits = [MsgPortraitState() for _ in range(self.num_portraits)]
        self.dialogue_lines = [MsgLineState(), MsgLineState()]
        self.intro_lines = [MsgLineState(), MsgLineState()]
        self.finished_stage = 0
        self.events = []
        self.dialogue_line_index = 0
        self._type_timer = 0

    # ---- RunMsg ----
    def step(self, *, advance_pressed: bool = False, skip_held: bool = False) -> bool:
        """每帧一次。advance_pressed = Z 新按下(WAS_PRESSED), skip_held = Ctrl 按住。
        返回 True = 消息仍活动。"""
        if self.current_msg_idx < 0:
            return False
        if self.ignore_wait_counter > 0:
            self.ignore_wait_counter -= 1
        cur = self.cur
        if self.dialogue_skippable and skip_held:
            self.timer = cur.time
        while self.timer >= cur.time:
            op = cur.opcode
            if op == MsgOpcode.DELETE:
                self.current_msg_idx = -1
                return False
            if op == MsgOpcode.SHOW_PORTRAIT:
                idx, face = cur.portrait
                p = self.portraits[idx]
                p.visible = True
                p.face = face
            elif op == MsgOpcode.CHANGE_FACE:
                idx, face = cur.portrait
                self.portraits[idx].face = face
            elif op == MsgOpcode.DIALOGUE:
                color, line, text = cur.dialogue
                if line == 0 and self.dialogue_lines[1].visible:
                    # RunMsg: 新顶行时把第 2 行清成 " "
                    self.dialogue_lines[1].clear()
                self.dialogue_lines[line].set_text(text, color)
                self.frames_elapsed_during_pause = 0
            elif op == MsgOpcode.PAUSE:
                if self.dialogue_skippable == 0 or not skip_held:
                    if (
                        not advance_pressed
                        or self.frames_elapsed_during_pause < self.pause_min_frames
                    ):
                        if self.frames_elapsed_during_pause < cur.pause_duration:
                            self.frames_elapsed_during_pause += 1
                            self._post_step()
                            return True  # SKIP_TIME_INCREMENT: 停在该指令
                    # Z 提前结束 / 时长到: 落到循环底前进
            elif op == MsgOpcode.SWITCH:
                idx, interrupt = cur.switch
                if idx < self.num_portraits:
                    self.portraits[idx].pending_interrupt = interrupt
                else:
                    self.dialogue_lines[idx - self.num_portraits].pending_interrupt = (
                        interrupt
                    )
            elif op == MsgOpcode.APPEAR_ENEMY:
                self.ignore_wait_counter += 1
            elif op == MsgOpcode.MUSIC:
                self.events.append(f"music:{cur.music_idx}")
            elif op == MsgOpcode.TEXT_INTRODUCE:
                color, line, text = cur.dialogue
                self.intro_lines[line].set_text(text, color)
                self.frames_elapsed_during_pause = 0
            elif op == MsgOpcode.STAGERESULTS:
                self.finished_stage = 1
                self.events.append("stage_results")
            elif op == MsgOpcode.FREEZE:
                self._post_step()
                return True  # 永久停在该指令(msg 保持活动)
            elif op == MsgOpcode.NEXT_LEVEL:
                # stage<6 非 practice/replay 路径: 转场结算, msg 置 -2
                self.current_msg_idx = -2
                self.events.append("next_level")
                self._post_step()
                return False
            elif op == MsgOpcode.FADEOUT_MUSIC:
                self.events.append("fadeout_music")
            elif op == MsgOpcode.ALLOW_SKIP:
                self.dialogue_skippable = cur.allow_skip
            # FADE_IN_EFFECT: 演出, 逻辑侧忽略
            # ---- th08 新增(Gui.cpp RunMsg; 15/17/18 纯视觉配置, 忽略) ----
            elif op == MsgOpcode.SHOW_SPEAKER_TEXT:
                # Gui.cpp:486-512: 落 dialogueLines[dialogueLineIndex] 并自增
                line_state = self.dialogue_lines[
                    min(self.dialogue_line_index, len(self.dialogue_lines) - 1)
                ]
                line_state.set_text(cur.plain_text, 0)
                self.frames_elapsed_during_pause = 0
                self.dialogue_line_index += 1
            elif op == MsgOpcode.SHOW_TOP_TEXT:
                # Gui.cpp:514-525: 落对话行 0
                self.dialogue_lines[0].set_text(cur.plain_text, 0)
                self.frames_elapsed_during_pause = 0
            elif op == MsgOpcode.SHOW_BOTTOM_TEXT:
                # Gui.cpp:527-538: 落对话行 1
                self.dialogue_lines[1].set_text(cur.plain_text, 0)
                self.frames_elapsed_during_pause = 0
            elif op == MsgOpcode.SHOW_SELECTION:
                # 二选一 (Gui.cpp:540-573): wait 式停留; Z 新按下(停满 60 帧)
                # 提前确认, 否则停满 args.wait.frames 自然前进; 上下键改
                # selected_option 是输入侧职责(headless 由上层直写字段)。
                if (
                    not advance_pressed
                    or self.frames_elapsed_during_pause < 60
                ):
                    if self.frames_elapsed_during_pause < cur.pause_duration:
                        self.frames_elapsed_during_pause += 1
                        self._post_step()
                        return True  # 停在该指令
            elif op == MsgOpcode.READ_SELECTED_MESSAGE:
                # Gui.cpp:574-578: finalStageRoute=selectedOption,
                # MsgRead(selectedOption+1) 后 continue(新消息当帧继续跑);
                # 越界时 MsgRead 无操作, 原地 continue 会死循环, 按普通前进兜底
                self.final_stage_route = self.selected_option
                prev_idx = self.current_msg_idx
                self.read(self.selected_option + 1)
                if self.current_msg_idx != prev_idx:
                    cur = self.cur
                    continue
            self.instr_idx += 1
            cur = self.cur
        self.timer += 1
        self._post_step()
        # RunMsg 尾部: 按住 SKIP 时跳过对话框淡入(前 60 帧)
        if self.timer < 60 and self.dialogue_skippable and skip_held:
            self.timer = 60
        return True

    def _post_step(self) -> None:
        """SKIP_TIME_INCREMENT 之后: 打字机推进(C 里是 ExecuteScript 驱动 VMs)。"""
        self._type_timer += 1
        if self._type_timer % TYPEWRITER_FRAMES_PER_CHAR == 0:
            for line in (*self.dialogue_lines, *self.intro_lines):
                if line.visible and line.reveal < len(line.text):
                    line.reveal += 1

    def take_events(self) -> list[str]:
        ev, self.events = self.events, []
        return ev
