"""AnmVm 脚本 VM 公共模块 —— 对照 AnmManager.cpp::ExecuteScript 全指令。

从 bg3d_view.py 抽出的共享实现(3D 背景与战斗实体共用):
- `AnmVm`: ExecuteScript 语义全集(SET_ACTIVE_SPRITE/平移/旋转/缩放/alpha/
  颜色/FADE/5 路插值/uv 滚动/blend/变量运算/RAND/三角/条件跳转/WAIT/STOP/
  interrupt 等, opcode -1..81 对照 AnmManager.hpp)。
- `SpriteTex`: 一个 sprite 的纹理采样信息(bg3d 软件光栅用)。
- `ScriptRef`: 全局 script id → (指令列表, sprite 基址, 跳转偏移表)。
- 辅助: `chain_offsets`(LoadAnms 链式 entry 偏移)、`offsets_to_entry`
  (全局 id → (entry, 局部 id))、`reset_and_run`(SetAnmIdxAndExecuteScript
  模式: memset VM + 挂脚本 + 立即执行一帧)。

渲染方注入 `_set_sprite_cb`(sprite 表查询)后每帧 `execute()` 推进。
"""

from __future__ import annotations

import math
import random

import numpy as np

from ...schema.anm import AnmFile


def _add_norm_angle(a: float, b: float) -> float:
    """utils::AddNormalizeAngle: a+b 包到 [-pi, pi]。"""
    a += b
    while a > math.pi:
        a -= 2 * math.pi
    while a < -math.pi:
        a += 2 * math.pi
    return a


def _anm_ease(t: float, mode: int) -> float:
    """AnmManager::ExecuteScript 的 ease(1..3=in, 4..6=out)。"""
    if mode == 1:
        return t * t
    if mode == 2:
        return t * t * t
    if mode == 3:
        t = t * t
        return t * t
    if mode == 4:
        t = 1.0 - t
        t = t * t
        return 1.0 - t
    if mode == 5:
        t = 1.0 - t
        t = t * t * t
        return 1.0 - t
    if mode == 6:
        t = 1.0 - t
        t = t * t * t
        return 1.0 - t
    return t


class SpriteTex:
    """一个 sprite 的采样信息(像素坐标已换算到纹理, 见 schema/anm.py)。"""

    __slots__ = ("tex", "u0", "v0", "u1", "v1", "w", "h", "opaque")

    def __init__(self, tex: np.ndarray, x: int, y: int, w: int, h: int) -> None:
        self.tex = tex
        th, tw = tex.shape[:2]
        self.u0 = x / tw
        self.v0 = y / th
        self.u1 = (x + w) / tw
        self.v1 = (y + h) / th
        self.w = float(w)  # widthPx
        self.h = float(h)  # heightPx
        # 区域 alpha 全 255 → 可用于遮挡标记(带孔的 sprite 不标记, 防误剔除)
        self.opaque = bool((tex[y : y + h, x : x + w, 3] == 255).all())


class ScriptRef:
    """全局 script id → (局部脚本, sprite 基址)。"""

    __slots__ = ("instrs", "offsets", "sprite_base")

    def __init__(self, instrs, sprite_base: int) -> None:
        self.instrs = instrs
        self.sprite_base = sprite_base
        # 字节偏移 → instr 下标(JUMP/条件跳的目标是脚本起点相对偏移)
        self.offsets: dict[int, int] = {}
        off = 0
        for i, ins in enumerate(instrs):
            self.offsets[off] = i
            off += 8 + 4 * len(ins.args_i)


def chain_offsets(anm: AnmFile, per_entry_scripts) -> list[int]:
    """LoadAnms 链式 entry 偏移: max(sprite id, script id)+1 累加。"""
    offs: list[int] = []
    cur = 0
    for entry, escr in zip(anm.entries, per_entry_scripts):
        offs.append(cur)
        hi = max(list(entry.sprites.keys()) + list(escr.keys()) + [0])
        cur += hi + 1
    return offs


def offsets_to_entry(offs: list[int], local: int) -> tuple[int, int]:
    """链式偏移空间的位置 → (entry, entry 内局部 id)。"""
    entry, lid = 0, local
    for i, off in enumerate(offs):
        if local < off:
            break
        entry, lid = i, local - off
    return entry, lid


def reset_and_run(vm: "AnmVm", ref: "ScriptRef | None", sprite_cb) -> None:
    """AnmManager::ExecuteAnmIdx + SetAndExecuteScript: 重置 VM 并立即跑一帧。"""
    vm.__init__()
    vm._set_sprite_cb = sprite_cb
    if ref is None:
        return
    vm.script = ref
    vm.pc = 0
    vm.time = 0
    vm.visible = False
    vm.execute()


class AnmVm:
    """AnmVm 的 Python 移植(AnmManager::ExecuteScript 语义)。"""

    def __init__(self) -> None:
        self.rotation = [0.0, 0.0, 0.0]
        self.angle_vel = [0.0, 0.0, 0.0]
        self.scale = [1.0, 1.0]
        self.scale_growth = [0.0, 0.0]
        self.uv_scroll = [0.0, 0.0]
        self.uv_scroll_vel = [0.0, 0.0]
        self.time = 0
        self.wait_timer = 0
        self.interp_start = [0] * 5
        self.interp_end = [0] * 5
        self.ease = [0] * 5
        self.int_vars1 = [0] * 4
        self.float_vars = [0.0] * 4
        self.int_vars2 = [0] * 2
        self.color = [255, 255, 255, 255]  # r,g,b,a
        self.visible = False
        self.active = True
        self.blend_mode = 0
        self.use_offset = False
        self.anchor = 0
        self.zwrite_disable = 0
        self.is_stopped = False
        self.auto_rotate = 0
        self.pending_interrupt = 0
        self.pos = np.zeros(3)
        self.offset = np.zeros(3)
        self.sprite: SpriteTex | None = None
        self.active_sprite_idx = -1
        self.script: ScriptRef | None = None
        self.pc = -1  # currentInstruction 下标, -1=结束
        self.pos_interp_initial = np.zeros(3)
        self.pos_interp_final = np.zeros(3)
        self.rot_interp_initial = [0.0, 0.0, 0.0]
        self.rot_interp_final = [0.0, 0.0, 0.0]
        self.scale_interp_initial = [1.0, 1.0]
        self.scale_interp_final = [1.0, 1.0]
        self.color_interp_initial = [255, 255, 255, 255]
        self.color_interp_final = [255, 255, 255, 255]
        self.rng = random.Random(0)

    # ---- 变量寻址(AnmVm::GetVarValue 等; flags 位标记参数是变量 id) ----
    def _int_value(self, arg: int, indirect: bool) -> int:
        if not indirect:
            return arg
        if 10000 <= arg <= 10003:
            return self.int_vars1[arg - 10000]
        if 10004 <= arg <= 10007:
            return int(self.float_vars[arg - 10004])
        if 10008 <= arg <= 10009:
            return self.int_vars2[arg - 10008]
        return arg

    def _float_value(self, arg: float, indirect: bool) -> float:
        if not indirect:
            return arg
        a = int(arg)
        if 10000 <= a <= 10003:
            return float(self.int_vars1[a - 10000])
        if 10004 <= a <= 10007:
            return self.float_vars[a - 10004]
        if 10008 <= a <= 10009:
            return float(self.int_vars2[a - 10008])
        return arg

    def _int_store(self, arg: int, indirect: bool, value: int) -> None:
        if indirect and 10000 <= arg <= 10003:
            self.int_vars1[arg - 10000] = value
        elif indirect and 10008 <= arg <= 10009:
            self.int_vars2[arg - 10008] = value

    def _int_load_ptr(self, arg: int, indirect: bool) -> int:
        if indirect and 10000 <= arg <= 10003:
            return self.int_vars1[arg - 10000]
        if indirect and 10008 <= arg <= 10009:
            return self.int_vars2[arg - 10008]
        return arg

    def _float_store(self, arg: float, indirect: bool, value: float) -> None:
        a = int(arg)
        if indirect and 10004 <= a <= 10007:
            self.float_vars[a - 10004] = value

    # ---- ExecuteScript ----
    def execute(self) -> None:
        ref = self.script
        if ref is None or self.pc < 0:
            return
        instrs = ref.instrs
        if self.pending_interrupt != 0:
            self._handle_interrupt()
        while self.pc < len(instrs) and instrs[self.pc].time <= self.time:
            ins = instrs[self.pc]
            op = ins.opcode
            ai = ins.args_i
            af = ins.args_f
            fl = ins.flags
            iv = lambda k: self._int_value(ai[k], fl >> k & 1)  # noqa: E731
            fv = lambda k: self._float_value(af[k], fl >> k & 1)  # noqa: E731
            advance = True
            if op in (-1, 1):  # EXIT_HIDE / EXIT_HIDE2
                self.visible = False
                self.pc = -1
                return
            if op == 2:  # EXIT
                self.pc = -1
                return
            if op == 3:  # SET_ACTIVE_SPRITE
                self.visible = True
                # C: SetActiveSprite(vm, arg + spriteIndices[anmFileIdx])
                self._set_sprite_cb(iv(0) + ref.sprite_base)
            elif op == 7:  # SET_SCALE
                self.scale = [fv(0), fv(1)]
            elif op == 8:  # SET_ALPHA
                self.color[3] = ai[0] & 255
            elif op == 9:  # SET_COLOR
                self.color[0] = (ai[0] >> 16) & 255
                self.color[1] = (ai[0] >> 8) & 255
                self.color[2] = ai[0] & 255
            elif op == 4:  # JUMP
                self.time = ai[1]
                self.pc = ref.offsets.get(ai[0], -1)
                continue
            elif op == 5:  # DEC_JUMP
                cur = self._int_load_ptr(ai[0], fl & 1) - 1
                self._int_store(ai[0], fl & 1, cur)
                if self._int_value(ai[0], fl & 1) > 0:
                    self.time = ai[2]
                    self.pc = ref.offsets.get(ai[1], -1)
                    continue
            elif op == 6:  # SET_TRANSLATION
                v = np.array([fv(0), fv(1), fv(2)])
                if self.use_offset:
                    self.offset = v
                else:
                    self.pos = v
            elif op == 10:  # FLIP_X
                self.scale[0] *= -1.0
            elif op == 11:  # FLIP_Y
                self.scale[1] *= -1.0
            elif op == 12:  # SET_ROTATION
                self.rotation = [fv(0), fv(1), fv(2)]
            elif op == 13:  # SET_ANGLE_VEL
                self.angle_vel = [fv(0), fv(1), fv(2)]
            elif op == 14:  # SET_SCALE_SPEED
                self.scale_growth = [fv(0), fv(1)]
            elif op == 29:  # INTERP_SCALE
                self.interp_start[4] = 0
                self.interp_end[4] = iv(2)
                self.ease[4] = 0
                self.scale_interp_initial = list(self.scale)
                self.scale_interp_final = [fv(0), fv(1)]
            elif op == 15:  # FADE
                self.color_interp_initial[3] = self.color[3]
                self.color_interp_final[3] = ai[0] & 255
                self.interp_start[2] = 0
                self.interp_end[2] = iv(1)
                self.ease[2] = 0
            elif op == 16:  # SET_BLEND
                self.blend_mode = ai[0]
            elif op in (17, 18, 19):  # POS_TIME_LINEAR/DECEL/ACCEL
                self.ease[0] = {17: 0, 18: 4, 19: 6}[op]
                self._pos_interp_setup(fv, iv, 0)
            elif op == 32:  # INTERP_POS
                self.ease[0] = ai[1] & 255
                self._pos_interp_setup(fv, iv, 0, arg_base=2, dur_arg=0)
            elif op == 79:  # WAIT
                if self.wait_timer == 0:
                    self.wait_timer = iv(0)
                else:
                    self.wait_timer -= 1
                if self.wait_timer <= 0:
                    self.wait_timer = 0
                else:
                    self.time -= 1
                    advance = False
                    self._epilogue()
                    return
            elif op in (20, 23):  # STOP / STOP_HIDE
                if op == 23:
                    self.visible = False
                if not self.pending_interrupt:
                    self.is_stopped = True
                    self.time -= 1
                    self._epilogue()
                    return
                self._handle_interrupt()
                continue
            elif op == 28:  # SET_VISIBILITY
                self.visible = bool(ai[0])
            elif op == 22:  # ANM_22: anchor=3
                self.anchor = 3
            elif op == 24:  # SET_USE_OFFSET
                self.use_offset = bool(ai[0])
            elif op == 25:  # SET_AUTO_ROTATE
                self.auto_rotate = ai[0] & 0xFFFF
            elif op == 26:  # SET_SCROLL_POS_X
                self.uv_scroll[0] = (self.uv_scroll[0] + fv(0)) % 1.0
            elif op == 27:  # SET_SCROLL_POS_Y
                self.uv_scroll[1] = (self.uv_scroll[1] + fv(0)) % 1.0
            elif op == 80:  # SET_SCROLLVEL_X
                self.uv_scroll_vel[0] = fv(0)
            elif op == 81:  # SET_SCROLLVEL_Y
                self.uv_scroll_vel[1] = fv(0)
            elif op == 30:  # SET_ZWRITE_DISABLE
                self.zwrite_disable = ai[0]
            elif op == 31:  # SET_CAMERA_MODE
                pass
            elif op == 33:  # INTERP_COLOR
                self.interp_start[1] = 0
                self.interp_end[1] = iv(0)
                self.ease[1] = ai[1] & 255
                self.color_interp_initial[:3] = self.color[:3]
                # C 端参数是 b[0],b[1],b[2] 连续 3 字节 = 0xBBGGRR 小端
                self.color_interp_final[:3] = [
                    ai[2] & 255,
                    (ai[2] >> 8) & 255,
                    (ai[2] >> 16) & 255,
                ]
            elif op == 34:  # INTERP_ALPHA
                self.interp_start[2] = 0
                self.interp_end[2] = iv(0)
                self.ease[2] = ai[1] & 255
                self.color_interp_initial[3] = self.color[3]
                self.color_interp_final[3] = ai[2] & 255
            elif op == 35:  # INTERP_ROTATE
                self.interp_start[3] = 0
                self.interp_end[3] = iv(0)
                self.ease[3] = ai[1] & 255
                self.rot_interp_initial = list(self.rotation)
                self.rot_interp_final = [fv(2), fv(3), fv(4)]
            elif op == 36:  # INTERP_SCALE_2
                self.interp_start[4] = 0
                self.interp_end[4] = iv(0)
                self.ease[4] = ai[1] & 255
                self.scale_interp_initial = list(self.scale)
                self.scale_interp_final = [fv(2), fv(3)]
            elif op in (
                37,
                39,
                41,
                43,
                45,
                47,
                49,
                51,
                53,
                55,
                57,
                59,
                67,
                69,
                71,
                73,
                75,
                77,
            ):
                self._int_op(op, ai, fl, ref)
                if op in (67, 69, 71, 73, 75, 77) and self._jumped:
                    continue
            elif op in (
                38,
                40,
                42,
                44,
                46,
                48,
                50,
                52,
                54,
                56,
                58,
                60,
                61,
                62,
                63,
                64,
                65,
                66,
                68,
                70,
                72,
                74,
                76,
                78,
            ):
                self._float_op(op, ai, af, fl, ref)
                if op in (68, 70, 72, 74, 76, 78) and self._jumped:
                    continue
            # default: 未知指令忽略
            if advance:
                self.pc += 1
        self._epilogue()

    _jumped = False

    def _pos_interp_setup(
        self, fv, iv, _unused, arg_base: int = 0, dur_arg: int = 3
    ) -> None:
        src = self.offset if self.use_offset else self.pos
        self.pos_interp_initial = np.array(src, dtype=float)
        self.pos_interp_final = np.array(
            [fv(arg_base), fv(arg_base + 1), fv(arg_base + 2)]
        )
        self.interp_end[0] = iv(dur_arg)
        self.interp_start[0] = 0

    def _handle_interrupt(self) -> None:
        ref = self.script
        assert ref is not None
        nxt = None
        target = self.pending_interrupt
        idx = 0
        instrs = ref.instrs
        while idx < len(instrs) and instrs[idx].opcode != -1:
            ins = instrs[idx]
            if ins.opcode == 21 and ins.args_i and ins.args_i[0] == target:
                break
            if ins.opcode == 21 and ins.args_i and ins.args_i[0] == -1:
                nxt = idx
            idx += 1
        self.pending_interrupt = 0
        self.is_stopped = False
        if idx >= len(instrs) or instrs[idx].opcode != 21:
            if nxt is None:
                self.time -= 1
                self._epilogue()
                self.pc = len(instrs)  # 停住
                return
            idx = nxt
        self.pc = idx + 1
        if self.pc < len(instrs):
            self.time = instrs[self.pc].time
        self.visible = True

    def _int_op(self, op: int, ai, fl: int, ref: ScriptRef) -> None:
        self._jumped = False
        iv = lambda k: self._int_value(ai[k], fl >> k & 1)  # noqa: E731
        dst_i = ai[0]
        ind = fl & 1
        cur = self._int_load_ptr(dst_i, ind)
        if op == 37:
            self._int_store(dst_i, ind, iv(1))
            return
        if op == 59:
            self._int_store(dst_i, ind, self.rng.randrange(max(1, iv(1))))
            return
        a, b = cur, iv(1)
        if op in (49, 51, 53, 55, 57):
            a, b = iv(1), iv(2)
        if op == 39 or op == 49:
            self._int_store(dst_i, ind, a + b)
        elif op == 41 or op == 51:
            self._int_store(dst_i, ind, a - b)
        elif op == 43 or op == 53:
            self._int_store(dst_i, ind, a * b)
        elif op == 45 or op == 55:
            self._int_store(dst_i, ind, a // b if b else 0)
        elif op == 47 or op == 57:
            self._int_store(dst_i, ind, a % b if b else 0)
        elif op in (67, 69, 71, 73, 75, 77):
            x, y = iv(0), iv(1)
            cond = {
                67: x == y,
                69: x != y,
                71: x < y,
                73: x <= y,
                75: x > y,
                77: x >= y,
            }[op]
            if cond:
                self.time = ai[3]
                self.pc = ref.offsets.get(ai[2], -1)
                self._jumped = True

    def _float_op(self, op: int, ai, af, fl: int, ref: ScriptRef) -> None:
        self._jumped = False
        fv = lambda k: self._float_value(af[k], fl >> k & 1)  # noqa: E731
        dst = af[0]
        ind = fl & 1
        cur = self._float_value(dst, ind)
        if op == 38:
            self._float_store(dst, ind, fv(1))
            return
        if op == 60:
            self._float_store(dst, ind, self.rng.random() * fv(1))
            return
        if op == 61:
            self._float_store(dst, ind, math.sin(fv(1)))
            return
        if op == 62:
            self._float_store(dst, ind, math.cos(fv(1)))
            return
        if op == 63:
            self._float_store(dst, ind, math.tan(fv(1)))
            return
        if op == 64:
            self._float_store(dst, ind, math.acos(max(-1.0, min(1.0, fv(1)))))
            return
        if op == 65:
            self._float_store(dst, ind, math.atan(fv(1)))
            return
        if op == 66:
            self._float_store(dst, ind, _add_norm_angle(fv(0), 0.0))
            return
        a, b = cur, fv(1)
        if op in (50, 52, 54, 56, 58):
            a, b = fv(1), fv(2)
        if op == 40 or op == 50:
            self._float_store(dst, ind, a + b)
        elif op == 42 or op == 52:
            self._float_store(dst, ind, a - b)
        elif op == 44 or op == 54:
            self._float_store(dst, ind, a * b)
        elif op == 46 or op == 56:
            self._float_store(dst, ind, a / b if b else 0.0)
        elif op == 48 or op == 58:
            self._float_store(dst, ind, math.fmod(a, b) if b else 0.0)
        elif op in (68, 70, 72, 74, 76, 78):
            x, y = fv(0), fv(1)
            cond = {
                68: x == y,
                70: x != y,
                72: x < y,
                74: x <= y,
                76: x > y,
                78: x >= y,
            }[op]
            if cond:
                self.time = ai[3]
                self.pc = ref.offsets.get(ai[2], -1)
                self._jumped = True

    def _epilogue(self) -> None:
        """ExecuteScript 的 stop: 段(角速度/插值/uv 滚动) + time++。"""
        for k in range(3):
            if self.angle_vel[k] != 0.0:
                self.rotation[k] = _add_norm_angle(self.rotation[k], self.angle_vel[k])
        for i in range(5):
            end = self.interp_end[i]
            if end > 0:
                self.interp_start[i] += 1
                if self.interp_start[i] >= end:
                    t = 1.0
                    self.interp_end[i] = 0
                else:
                    t = _anm_ease(self.interp_start[i] / end, self.ease[i])
                if i == 0:
                    dst = self.offset if self.use_offset else self.pos
                    dst[:] = (
                        self.pos_interp_final - self.pos_interp_initial
                    ) * t + self.pos_interp_initial
                elif i == 1:
                    for c in range(3):
                        self.color[c] = int(
                            (self.color_interp_final[c] - self.color_interp_initial[c])
                            * t
                            + self.color_interp_initial[c]
                        )
                elif i == 2:
                    self.color[3] = int(
                        (self.color_interp_final[3] - self.color_interp_initial[3]) * t
                        + self.color_interp_initial[3]
                    )
                elif i == 3:
                    for c in range(3):
                        self.rotation[c] = _add_norm_angle(
                            (self.rot_interp_final[c] - self.rot_interp_initial[c]) * t,
                            self.rot_interp_initial[c],
                        )
                elif i == 4:
                    for c in range(2):
                        self.scale[c] = (
                            self.scale_interp_final[c] - self.scale_interp_initial[c]
                        ) * t + self.scale_interp_initial[c]
        if self.scale_growth[1] != 0.0:
            self.scale[1] += self.scale_growth[1]
        if self.scale_growth[0] != 0.0:
            self.scale[0] += self.scale_growth[0]
        self.uv_scroll[0] = (self.uv_scroll[0] + self.uv_scroll_vel[0]) % 1.0
        self.uv_scroll[1] = (self.uv_scroll[1] + self.uv_scroll_vel[1]) % 1.0
        self.time += 1

    # 由场景注入(sprite 表查询)
    _set_sprite_cb = lambda self, gid: None  # noqa: E731
