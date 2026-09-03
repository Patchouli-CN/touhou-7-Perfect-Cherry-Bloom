"""TH08(东方永夜抄)的对话 VM 扩展 —— MsgVmTh08(MsgVm)。

th08 的 msg opcode 扩到 0-22(Gui.hpp:67-74); 扩展字段与 op15-22 分支
自 schema/msg.py 下沉(纯搬运, C 行号注释随行), 经基类的
``_handle_extra_op`` 扩展点接入:
- op15/17/18 立绘/文本框配置: 纯视觉, 忽略(落普通前进);
- op16 SHOW_SPEAKER_TEXT(Gui.cpp:486-512)/op19/op20: 纯文本落对话行
  (GuiMessagePlainTextArgs, Gui.hpp:121-124);
- op21 SHOW_SELECTION(Gui.cpp:540-573): 二选一, wait 式停留;
- op22 READ_SELECTED_MESSAGE(Gui.cpp:574-578): finalStageRoute =
  selectedOption 并 MsgRead(selectedOption+1);
- PAUSE 的 Z 提前结束最短停留 waitThreshold=6(Gui.cpp:241) 经构造参数
  ``pause_min_frames=6`` 传入。
"""

from __future__ import annotations

from typing import Optional

from ...schema.msg import MsgFile, MsgInstr, MsgOpcode, MsgVm


class MsgVmTh08(MsgVm):
    """th08 对话 VM: 基类 + 扩展字段(Gui.hpp GuiMsgVm 尾部) + op15-22。"""

    def __init__(
        self,
        msg_file: Optional[MsgFile] = None,
        *,
        num_portraits: int = 4,
        pause_min_frames: int = 6,  # th08 MsgRead: waitThreshold=6 (Gui.cpp:241)
    ) -> None:
        super().__init__(
            msg_file, num_portraits=num_portraits, pause_min_frames=pause_min_frames
        )
        # ---- th08 扩展(Gui.hpp GuiMsgVm 尾部字段) ----
        self.dialogue_line_index = 0  # op16 说话人文本的落行游标
        self.selected_option = 0  # op21 二选一的当前选项(0/1)
        self.final_stage_route: int | None = None  # op22 写出(Gui.cpp:574-578)

    def read(self, msg_idx: int) -> None:
        """MsgRead: 基类清零后补清 th08 扩展字段(越界无操作同基类)。"""
        if self.msg_file is None or self.msg_file.num_messages <= msg_idx:
            return
        super().read(msg_idx)
        self.dialogue_line_index = 0

    def _handle_extra_op(self, cur: MsgInstr, advance_pressed: bool) -> Optional[bool]:
        """op15-22(Gui.cpp RunMsg; 15/17/18 纯视觉配置, 忽略)。"""
        op = cur.opcode
        if op == MsgOpcode.SHOW_SPEAKER_TEXT:
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
                # read 已把 instr_idx 清 0; 置 -1 抵消基类链尾的普通前进,
                # 等效 C 的 continue(新消息当帧继续跑)
                self.instr_idx = -1
        return None
