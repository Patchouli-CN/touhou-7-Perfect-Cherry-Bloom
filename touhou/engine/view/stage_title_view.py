"""每面开场的关卡标题 —— Gui::Initialize 的 vms1 + OnUpdate/OnDraw 段。

- 素材: std{N}txt.anm (Gui.cpp:503-622 按 currentStage 加载,
  ANM_FILE_STAGE_TEXT=24, ANM_OFFSET_STAGE_TEXT=0x800); 5 个脚本在关卡
  装载时一并启动 (Gui.cpp:655 ExecuteVmsAnms(vms1, 2048, 5)), 每帧推进
  (Gui.cpp:1273 ExecuteScripts), 绘制 (Gui.cpp:1702-1704 Draw, 含旋转):
    script 2048: 魔法阵装饰 (sprite 5, 旋转, t=200 淡入 → t=400 退场)
    script 2049: 关卡名 (sprite 0, 竖排滑入)
    script 2050: "Stage N" (sprite 1)
    script 2051: 英文副标题 (sprite 2)
    script 2052: 舞台 BGM 行 (sprite 3)
  全部自时序入场/淡出 (t≈400-460 EXIT_HIDE2), 换关 (enter_stage) 重建。
- MSG_MUSIC 的 boss BGM 行重触发 (Gui.cpp:938-958): 对话切曲时 vms1[0]
  重跑 script 2052 (currentStage==6 即 EX 面用 2053) 并换 sprite
  2051+musicIdx (曲名图)。C++ 直接复用 vms1[0] 槽位 (此时标题早已退场),
  这里用独立 _music_vm 槽位等效; 事件源是 impl 帧末收口的 frame_bgm
  (core/impl.py 的 msg "music:" 事件 → ("music", musicIdx))。
- 绘制层: 画在 640x480 窗口层 (Gui::OnDraw 画全窗口 framebuffer),
  脚本窗口坐标直接绘制, 不换算进游戏区。
"""
from __future__ import annotations

import pygame

from .anm_fx import AnmScriptBank, TransformCache, Vm2d

_ANM_OFFSET_STAGE_TEXT = 0x800   # AnmIdx.hpp:53
_TITLE_SCRIPTS = 5               # ExecuteVmsAnms(vms1, 2048, 5) (Gui.cpp:655)

_SCR_BGM_LINE = 2052             # BGM 行脚本 (Gui.cpp:942)
_SCR_BGM_LINE_EX = 2053          # EX 面 (currentStage==6) 用 (Gui.cpp:947)
_SPR_BGM_BASE = 2051             # sprite = 2051 + musicIdx (Gui.cpp:950-951)


class StageTitleView:
    """关卡标题 VM 组: set_stage(换关重建) + render(每帧推进/绘制)。"""

    def __init__(self, bank, tcache: TransformCache) -> None:
        self.bank = bank
        self.tcache = tcache
        self._stage = 0
        self._sbank: AnmScriptBank | None = None
        self._vms: list[Vm2d] = []
        self._music_vm: Vm2d | None = None   # MSG_MUSIC 重触发的 BGM 行
        # ---- 测试断言用: 本帧标题绘制调用数 ----
        self.title_draws = 0

    def set_stage(self, stage_no: int) -> None:
        """换关重建 5 个标题 VM (Gui::Initialize 的按关加载段)。"""
        if stage_no == self._stage:
            return
        self._stage = stage_no
        self._vms = []
        self._music_vm = None
        sb = AnmScriptBank(self.bank, f"std{stage_no}txt.anm",
                           _ANM_OFFSET_STAGE_TEXT)
        self._sbank = sb if sb.ok else None
        if self._sbank is None:
            return
        for i in range(_TITLE_SCRIPTS):
            vm = Vm2d(self._sbank, self.tcache)
            if vm.start(_ANM_OFFSET_STAGE_TEXT + i):
                self._vms.append(vm)

    def _retrigger_bgm_line(self, music_idx: int) -> None:
        """MSG_MUSIC (Gui.cpp:938-958): BGM 行重入场 + 曲名 sprite。"""
        sb = self._sbank
        if sb is None:
            return
        # C++ currentStage 0-based, ==6 即 EX 面 → script 2053
        gid = _SCR_BGM_LINE_EX if self._stage == 7 else _SCR_BGM_LINE
        vm = Vm2d(sb, self.tcache)
        if not vm.start(gid):
            return
        vm.set_sprite(_SPR_BGM_BASE + music_idx - _ANM_OFFSET_STAGE_TEXT)
        self._music_vm = vm

    def render(self, surf: pygame.Surface, game=None) -> None:
        """Gui::OnDraw vms1 段 (Gui.cpp:1702-1704): Draw (含旋转);
        先扫本帧 msg BGM 事件做 boss 曲名重触发。"""
        self.title_draws = 0
        if game is not None:
            for ev in getattr(game, "frame_bgm", ()):
                if ev[0] == "music":
                    self._retrigger_bgm_line(int(ev[1]))
        alive = []
        for vm in self._vms:
            vm.execute()
            if not vm.alive:
                continue
            alive.append(vm)
            vm.draw(surf, vm.vm.pos[0] + vm.vm.offset[0],
                    vm.vm.pos[1] + vm.vm.offset[1])
            self.title_draws += 1
        self._vms = alive
        mv = self._music_vm
        if mv is not None:
            mv.execute()
            if mv.alive:
                mv.draw(surf, mv.vm.pos[0] + mv.vm.offset[0],
                        mv.vm.pos[1] + mv.vm.offset[1])
                self.title_draws += 1
            else:
                self._music_vm = None
