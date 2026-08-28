""".std 驱动的 3D 关卡背景 —— 场景层(脚本推进/资源装配), 对照 th07 反编译还原。

渲染(相机/投影/剔除/光栅化/雾/帧缓冲)全部在
``engine.render.d3dx_render.D3DXLikeRender``(D3DX 工具库 + D3D8 固定管
线的 Python 复刻, 行向量/左手系/D3D8 布局); 本模块只保留场景内容层:

- .std 场景脚本推进(Stage.cpp UpdateScriptAndCamera): tick/_dispatch/
  相机 4 路插值(ease/hermite)/雾插值; 指令改写的相机/雾/清屏色/世界
  原点直接写到渲染器对象的字段上, 渲染时由渲染器自取。
- 场景实体管理: stage.objects 的 quad VM 创建与脚本执行(LoadStageData:
  ExecuteAnmIdx(anmScript + 0x300)), vm1/vm2(指令 29/30 的屏幕空间 VM),
  每帧 object 存活判定(UpdateObjects)。
- load(): stage{no}.std + stg{no}bg*.anm 资源装配。

权威参考:
- `Stage.cpp/.hpp`: .std 场景脚本(相机/雾/世界原点/全屏 VM)。
- `AnmManager.cpp`: AnmVm 脚本 VM(ExecuteScript 全指令)。
- 渲染侧对照(RenderObjects 实例剔除/两阶段、UpdateCamera、Draw3/
  DrawFacingCamera/DrawInner)见 d3dx_render 模块 docstring。

用法: 每帧 tick(script_wait_time) 后 render() → 内部缓冲 uint8 RGB;
render_into(surf) 负责放大到 384x448。render_scale<1 是性能折中(原版
POINT 采样+雾本身较糊, 降采样平滑放大后观感接近; 默认 0.45, 配合
sprite_view 的动态降载 EMA 在重负载关卡自动回落)。

与原版差距(本层; 渲染近似均已在 d3dx_render 标注):
- EffectManager 特效未移植; 符卡背景 (spellCardState 黑罩/3D 停画/符卡
  背景 VM) 在 games/th07/view/spellcard_view.py 的 SpellcardBgView。
- ECL script_wait_time 用边沿触发消费(引擎侧 EclWorld.script_wait_time 只写
  不清, 原版 g_Stage.scriptWaitTime 消费后清零; 这里记录上次值, 变化时才跳)。
"""

from __future__ import annotations

import time

import numpy as np
import pygame

from ...logger import logger as log
from ...schema.anm import AnmFile, parse_cached, parse_scripts
from ...schema.stage import Stage
from ..render.d3dx_render import GAME_H, GAME_W, D3DXLikeRender
from .anm_vm import AnmVm, ScriptRef, SpriteTex, chain_offsets, reset_and_run

_ANM_OFFSET_BG1 = 0x300  # ANM_OFFSET_STAGE_BG1 (AnmIdx.hpp)

# StageEaseMode (Stage.hpp): 注意与 anm ease 编号不同
_EASE_OUT_QUAD, _EASE_OUT_CUBIC, _EASE_OUT_QUART = 1, 2, 3
_EASE_IN_QUAD, _EASE_IN_CUBIC, _EASE_IN_QUART = 4, 5, 6
_EASE_CUBIC_INTERP = 7


def _stage_ease(t: float, mode: int) -> float:
    """Stage::UpdateScriptAndCamera 的 ease 变换(编号与 anm 不同)。"""
    if mode == _EASE_OUT_QUAD:
        t = 1.0 - t
        return 1.0 - t * t
    if mode == _EASE_OUT_CUBIC:
        t = 1.0 - t
        return 1.0 - t * t * t
    if mode == _EASE_OUT_QUART:
        t = 1.0 - t
        return 1.0 - t * t * t * t
    if mode == _EASE_IN_QUAD:
        return t * t
    if mode == _EASE_IN_CUBIC:
        return t * t * t
    if mode == _EASE_IN_QUART:
        return t * t * t * t
    return t


def _interp_cubic(p0: float, p1: float, p2: float, p3: float, t: float) -> float:
    """Stage.cpp InterpCubic (hermite)。"""
    v0 = (t - 1.0) * (t - 1.0) * (2.0 * t + 1.0)
    v2 = t * t * (3.0 - 2.0 * t)
    v1 = (1.0 - t) * (1.0 - t) * t
    v3 = (t - 1.0) * t * t
    return v0 * p0 + v2 * p1 + v1 * p2 + v3 * p3


class _CameraChannel:
    """相机一路(pos/lookAt/up/fov)的插值状态。"""

    def __init__(self) -> None:
        self.start = np.zeros(3)
        self.end = np.zeros(3)
        self.tan_start = np.zeros(3)
        self.tan_end = np.zeros(3)


class StageScene:
    """一关的 3D 背景场景: .std 脚本推进 + VM 生命周期(渲染委托渲染器)。

    每帧 tick(script_wait_time) 推进脚本/VM, render()/render_into(surf)
    委托 self._renderer(D3DXLikeRender) 产出画面; 脚本指令改写的相机/
    雾/清屏色/世界原点即渲染器同名字段。
    """

    def __init__(
        self,
        stage: Stage,
        scripts: dict[int, ScriptRef],
        sprites: dict[int, SpriteTex],
        render_scale: float = 0.45,
    ) -> None:
        self.stage = stage
        self._scripts = scripts
        self._sprites = sprites
        instrs = stage.instrs
        self._instrs = instrs
        self.script_time = 0
        self.instr_idx = 0
        self.wait_time = 0
        self._seen_wait = 0
        # 渲染器(D3DX 固定管线复刻): 持有相机/雾/视口/帧缓冲状态,
        # .std 指令直写其字段(cam_*/sky_fog_*/world_origin/clear_color)
        self._renderer = D3DXLikeRender(render_scale)
        # 雾插值(opcode 2 的 duration 帧渐变; 当前值在渲染器字段上)
        self._fog_start = (self._renderer.sky_fog_rgba, 200.0, 500.0)
        self._fog_end = (self._renderer.sky_fog_rgba, 200.0, 500.0)
        self._fog_interp_duration = 0
        self._fog_interp_timer = 0
        # 相机 4 路(pos/lookAt/up/fov)插值通道(UpdateScriptAndCamera)
        self._ch = [_CameraChannel() for _ in range(4)]
        self._ch[0].start = self._ch[0].end = self._renderer.cam_pos.copy()
        self._ch[3].start = self._ch[3].end = np.array([self._renderer.cam_fov, 0, 0])
        self._timers_max = [0, 0, 0, 0]
        self._timers = [0, 0, 0, 0]
        self._ease_modes = [0, 0, 0, 0]
        # quad VM(LoadStageData: ExecuteAnmIdx(anmScript + 0x300))
        self._obj_vms: list[list[AnmVm]] = []  # per object quads
        self._obj_active: list[bool] = []
        for obj in stage.objects:
            vms = []
            for q in obj.quads:
                vm = AnmVm()
                self._exec_anm_idx(vm, q.anm_script + _ANM_OFFSET_BG1)
                vms.append(vm)
            self._obj_vms.append(vms)
            self._obj_active.append(True)
        self.vm1 = AnmVm()
        self.vm2 = AnmVm()
        self.vm1.active_sprite_idx = -1
        self.vm2.active_sprite_idx = -1
        for vm in (self.vm1, self.vm2):
            vm._set_sprite_cb = self._make_sprite_cb(vm)
        # 场景几何 + quad VM 绑定进渲染器(RenderObjects 剔除预计算)
        self._renderer.set_stage(stage, self._obj_vms)

    # ---- 渲染状态代理(实际值在渲染器上; 动态降载等调用方读这里) ----
    @property
    def render_scale(self) -> float:
        return self._renderer.render_scale

    @property
    def buf_w(self) -> int:
        return self._renderer.buf_w

    @property
    def buf_h(self) -> int:
        return self._renderer.buf_h

    # ---- 动态分辨率(帧超预算时降档; 纯视觉取舍, 不影响逻辑) ----
    def set_render_scale(self, scale: float) -> None:
        """切换内部光栅分辨率(委托渲染器; 无随分辨率持久的场景缓存)。"""
        self._renderer.set_render_scale(scale)

    # ---- 资源表 ----
    @classmethod
    def load(
        cls, archive, stage_no: int, render_scale: float = 0.45
    ) -> "StageScene | None":
        """从 GameArchive 加载 stage{no}.std + stg{no}bg*.anm; 缺资源返回 None。"""
        t0 = time.perf_counter()
        try:
            std_raw = None
            for key in (f"stage{stage_no}.std", f"data/stage{stage_no}.std"):
                try:
                    std_raw = archive.load(key)
                    break
                except KeyError:
                    continue
            if std_raw is None:
                return None
            stage = Stage.read(std_raw, stage_no)
        except Exception as e:
            log.warning("stage{} 背景(std)解析失败, 回退无 3D 背景: {}", stage_no, e)
            return None
        names = [f"stg{stage_no}bg.anm"]
        if stage_no == 4:
            names += [f"stg4bg{k}.anm" for k in range(2, 6)]
        scripts: dict[int, ScriptRef] = {}
        sprites: dict[int, SpriteTex] = {}
        base = _ANM_OFFSET_BG1
        for name in names:
            raw = None
            for key in (name, f"data/{name}"):
                try:
                    raw = archive.load(key)
                    break
                except KeyError:
                    continue
            if raw is None:
                if name == names[0]:
                    return None
                break
            anm = parse_cached(raw)  # 进程级缓存 (BUGS.md 增量#3)
            per_entry_scripts = parse_scripts(raw)
            for entry, escr, off in zip(
                anm.entries, per_entry_scripts, chain_offsets(anm, per_entry_scripts)
            ):
                tex = np.frombuffer(entry.rgba, dtype=np.uint8).reshape(
                    entry.tex_height, entry.tex_width, 4
                )
                for sid, spr in entry.sprites.items():
                    sprites[base + off + sid] = SpriteTex(
                        tex, spr.x, spr.y, spr.w, spr.h
                    )
                for sid, instrs in escr.items():
                    scripts[base + off + sid] = ScriptRef(instrs, base + off)
            base += 0x10
        scene = cls(stage, scripts, sprites, render_scale)
        ms = (time.perf_counter() - t0) * 1000
        if ms >= 30.0:
            log.debug("stage{} 3D 背景装配耗时 {:.1f}ms", stage_no, ms)
        return scene

    def _make_sprite_cb(self, vm: AnmVm):
        def cb(gid: int) -> None:
            spr = self._sprites.get(gid)
            if spr is None:
                return
            vm.sprite = spr
            vm.active_sprite_idx = gid

        return cb

    def _exec_anm_idx(self, vm: AnmVm, gid: int) -> None:
        """AnmManager::ExecuteAnmIdx + SetAndExecuteScript。"""
        reset_and_run(vm, self._scripts.get(gid), self._make_sprite_cb(vm))

    # ---- 每帧推进(Stage::OnUpdate + UpdateObjects) ----
    def tick(self, script_wait_time: int = 0) -> None:
        if script_wait_time != self._seen_wait:
            # 原版 g_Stage.scriptWaitTime 由 ECL 设置、消费后清零;
            # Python 引擎侧只写不清, 这里边沿触发消费(见模块 docstring)
            self._seen_wait = script_wait_time
            if script_wait_time != 0:
                self.wait_time = script_wait_time
        if self.wait_time != 0:
            for i, ins in enumerate(self._instrs):
                if ins.opcode == 31 and ins.args_i[0] == self.wait_time:
                    self.instr_idx = i + 1
                    self.script_time = ins.frame
                    self.wait_time = 0
                    break
        instrs = self._instrs
        while (
            self.instr_idx < len(instrs)
            and self.script_time >= instrs[self.instr_idx].frame
        ):
            ins = instrs[self.instr_idx]
            if ins.opcode == 3 and self.wait_time == 0:
                break  # C: goto LAB(不推进 instructionIndex, 脚本停轴)
            self._dispatch(ins)
            self.instr_idx += 1
        # 相机 4 路插值(UpdateScriptAndCamera)
        for idx in range(4):
            if self._timers_max[idx] != 0:
                self._update_channel(idx)
        # 雾插值
        if self._fog_interp_duration != 0:
            self._fog_interp_timer += 1
            t = min(1.0, self._fog_interp_timer / self._fog_interp_duration)
            c0, n0, f0 = self._fog_start
            c1, n1, f1 = self._fog_end
            interp_c = 0
            for shift in (16, 8, 0):
                a = (c0 >> shift) & 255
                b = (c1 >> shift) & 255
                interp_c |= (int((a - b) * t + b) & 255) << shift
            alpha = (
                int((((c0 >> 24) & 255) - ((c1 >> 24) & 255)) * t + ((c1 >> 24) & 255))
                & 255
            )
            r = self._renderer
            r.sky_fog_rgba = (alpha << 24) | interp_c
            r.sky_fog_color = np.array(
                [(interp_c >> 16) & 255, (interp_c >> 8) & 255, interp_c & 255],
                dtype=float,
            )
            r.sky_fog_near = (n0 - n1) * t + n1
            r.sky_fog_far = (f0 - f1) * t + f1
            if self._fog_interp_timer >= self._fog_interp_duration:
                self._fog_interp_duration = 0
        # 脚本时间(opcode 3 停轴)
        cur_op = instrs[self.instr_idx].opcode if self.instr_idx < len(instrs) else -1
        if cur_op != 3:
            self.script_time += 1
        # UpdateObjects
        for oi, obj in enumerate(self.stage.objects):
            if not self._obj_active[oi]:
                continue
            alive = 0
            for vm in self._obj_vms[oi]:
                vm.execute()
                if vm.pc >= 0:
                    alive += 1
            if alive == 0:
                self._obj_active[oi] = False
        if self.vm1.active_sprite_idx > 0:
            self.vm1.execute()
        if self.vm2.active_sprite_idx > 0:
            self.vm2.execute()

    def _dispatch(self, ins) -> None:
        op = ins.opcode
        vec = np.array(ins.args_f)
        r = self._renderer
        if op == 0:
            r.world_origin = vec.copy()
        elif op == 1:
            r.sky_fog_rgba = ins.args_i[0] & 0xFFFFFFFF
            c = r.sky_fog_rgba
            r.sky_fog_color = np.array(
                [(c >> 16) & 255, (c >> 8) & 255, c & 255], dtype=float
            )
            r.sky_fog_near = ins.args_f[1]
            r.sky_fog_far = ins.args_f[2]
            self._fog_start = (c, r.sky_fog_near, r.sky_fog_far)
        elif op == 2:
            self._fog_end = (r.sky_fog_rgba, r.sky_fog_near, r.sky_fog_far)
            self._fog_interp_duration = ins.args_i[0]
            self._fog_interp_timer = 0
        elif op == 3:
            # C: scriptWaitTime!=0 时清零并继续(随后 instructionIndex++)
            if self.wait_time != 0:
                self.wait_time = 0
        elif op == 4:
            self.instr_idx = ins.args_i[0] - 1  # tick 循环会 +1
            self.script_time = ins.args_i[1]
            self._timers_max[0] = 0
        elif op == 5:
            self._ch[0].start = self._ch[0].end.copy()
            self._ch[0].end = vec.copy()
            if self._timers_max[0] == 0:
                r.cam_pos = vec.copy()
        elif op == 6:
            self._timers_max[0] = ins.args_i[0]
            self._timers[0] = 0
            self._ease_modes[0] = ins.args_i[1]
        elif op == 7:
            self._ch[1].start = self._ch[1].end.copy()
            self._ch[1].end = vec.copy()
            if self._timers_max[1] == 0:
                r.cam_lookat = vec.copy()
        elif op == 8:
            self._timers_max[1] = ins.args_i[0]
            self._timers[1] = 0
            self._ease_modes[1] = ins.args_i[1]
        elif op == 9:
            self._ch[2].start = self._ch[2].end.copy()
            self._ch[2].end = vec.copy()
            if self._timers_max[2] == 0:
                r.cam_up = vec.copy()
        elif op == 10:
            self._timers_max[2] = ins.args_i[0]
            self._timers[2] = 0
            self._ease_modes[2] = ins.args_i[1]
        elif op == 11:
            self._ch[3].start = self._ch[3].end.copy()
            self._ch[3].end = np.array([ins.args_f[0], 0, 0])
            if self._timers_max[3] == 0:
                r.cam_fov = ins.args_f[0]
        elif op == 12:
            self._timers_max[3] = ins.args_i[0]
            self._timers[3] = 0
            self._ease_modes[3] = ins.args_i[1]
        elif op == 13:
            r.clear_color = ins.args_i[0] & 0xFFFFFFFF
        elif op in (14, 19, 24):
            self._ch[{14: 0, 19: 1, 24: 2}[op]].start = vec.copy()
        elif op in (15, 20, 25):
            self._ch[{15: 0, 20: 1, 25: 2}[op]].end = vec.copy()
        elif op in (16, 21, 26):
            self._ch[{16: 0, 21: 1, 26: 2}[op]].tan_start = vec.copy()
        elif op in (17, 22, 27):
            self._ch[{17: 0, 22: 1, 27: 2}[op]].tan_end = vec.copy()
        elif op in (18, 23, 28):
            idx = {18: 0, 23: 1, 28: 2}[op]
            self._timers_max[idx] = ins.args_i[0]
            self._timers[idx] = 0
            self._ease_modes[idx] = _EASE_CUBIC_INTERP
        elif op == 29:
            if ins.args_i[0] >= 0:
                self._exec_anm_idx(self.vm1, ins.args_i[0] + _ANM_OFFSET_BG1)
            else:
                self.vm1.active_sprite_idx = -1
        elif op == 30:
            if ins.args_i[0] >= 0:
                self._exec_anm_idx(self.vm2, ins.args_i[0] + _ANM_OFFSET_BG1)
            else:
                self.vm2.active_sprite_idx = -1
        # opcode 31: wait 标记, 顺序执行到时跳过(C switch 无 case 31)

    def _update_channel(self, idx: int) -> None:
        if self._timers[idx] < self._timers_max[idx]:
            self._timers[idx] += 1
            t = self._timers[idx] / self._timers_max[idx]
        else:
            self._timers[idx] = self._timers_max[idx]
            t = 1.0
            self._timers_max[idx] = 0
        ch = self._ch[idx]
        if self._ease_modes[idx] != _EASE_CUBIC_INTERP:
            t = _stage_ease(t, self._ease_modes[idx])
            cur = (ch.end - ch.start) * t + ch.start
        else:
            cur = np.array(
                [
                    _interp_cubic(
                        ch.start[k], ch.end[k], ch.tan_start[k], ch.tan_end[k], t
                    )
                    for k in range(3)
                ]
            )
        if idx == 0:
            self._renderer.cam_pos = cur
        elif idx == 1:
            self._renderer.cam_lookat = cur
        elif idx == 2:
            self._renderer.cam_up = cur
        else:
            self._renderer.cam_fov = float(cur[0])

    # ---- 渲染(委托 D3DXLikeRender; pygame 放大 blit 留在本层,
    #      engine/render 的 AST 守护不许 import pygame) ----
    def render(self) -> np.ndarray:
        """渲染一帧 → 内部缓冲 (buf_h, buf_w, 3) uint8 RGB。"""
        return self._renderer.render(self.vm1, self.vm2)

    def render_into(self, surf: pygame.Surface) -> None:
        """渲染一帧并(按需放大)blit 到 384x448 的游戏区 surface。"""
        fb = self._renderer.render(self.vm1, self.vm2)
        img = pygame.image.frombuffer(
            fb.tobytes(), (self._renderer.buf_w, self._renderer.buf_h), "RGB"
        )
        if (self._renderer.buf_w, self._renderer.buf_h) != (GAME_W, GAME_H):
            img = pygame.transform.smoothscale(img, (GAME_W, GAME_H))
        surf.blit(img, (0, 0))
