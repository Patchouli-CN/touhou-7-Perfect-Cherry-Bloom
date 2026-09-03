"""th08(东方永夜抄)的 anm 脚本 VM —— AnmVm 子类, 覆写 v3 指令集差集。

对照 th08-ref AnmManager.cpp:226-748 ExecuteScript(行号相对其 src/)。
与 th07(engine/view/anm_vm.py 的 AnmVm)的差异:

- 新增指令 op82-89 (AnmManager.hpp:208-215): 82 BlendMode(原生值) /
  83 Ins83(playerBulletHitAnimationType) / 84 Color2 / 85 Alpha2 /
  86 Color2Time / 87 Alpha2Time / 88 Ins88(flag17, byte 参数) /
  89 ReturnFromInterrupt(interrupt 返回点)。
- 同号不同义: op9 Color 拆 3 个 int 参(:249-253, th07 是打包 0xRRGGBB);
  op16 AdditiveBlendMode 布尔化(:326-328); op25 Ins25 设 type 不截断
  (:436-438); op26/27 AddU/AddV 单次 ±1.0 回绕非取模(:439-467);
  op31 Ins31 设 flag15(:477-479); op33 ColorTime 拆参 dur,mode,r,g,b
  (:498-511, th07 是打包 b[0..2])。
- 状态面: color2 + 7 插值槽(AnmInterp_Last=7, AnmManager.hpp:100-109,
  多 RGB2/Alpha2 两槽)/flip 2 位掩码/op3 记 timeOfLastSpriteSet(:238)/
  flag15/flag17/flag19/ interrupt 返回点(:419-420)/framerateMultiplier
  (epilogue 的角速度/缩放增速乘它, :750-874 —— 决死冻结帧用)。
- interrupt 处理保存返回点(:419-420), op89 返回后重新执行原 Stop
  指令(:426-429 → 回Stop → 无 interrupt 时 stopped, :382-391)。
- epilogue 的 uv 滚动也是单次 ±1.0 回绕(:876-901)。

绘制侧语义(SetRenderStateForVm3D, :987): flag17 置位时有效色取 color2,
否则 color1 —— color2 不是叠乘, 是二选一。

th07 侧逐字节不动; 未覆写的指令(变量运算/跳转/RAND/三角/WAIT 等)
共用基类实现(变量寻址 10000-10009 两作相同, AnmManager.hpp:86-98)。
"""

from __future__ import annotations

import numpy as np

from ....engine.view.anm_vm import AnmVm, ScriptRef, _add_norm_angle, _anm_ease


class AnmVmTh08(AnmVm):
    """AnmVm 的 th08 指令集变体(差集覆写, 共性继承)。"""

    def __init__(self) -> None:
        super().__init__()
        # 插值槽扩到 7 (AnmInterp_Last): 5=RGB2 6=Alpha2
        self.interp_start = [0] * 7
        self.interp_end = [0] * 7
        self.ease = [0] * 7
        # color2 (AnmVmBase.color1/color2, AnmManager.hpp:348-349);
        # AnmVmBase::Initialize 源码未转写(th08-ref 未实现), 取恒等白
        self.color2 = [255, 255, 255, 255]  # r,g,b,a
        self.color2_interp_initial = [255, 255, 255, 255]
        self.color2_interp_final = [255, 255, 255, 255]
        self.flip = 0  # flip 掩码 bit0=X bit1=Y (AnmManager.hpp:363)
        self.type = 0  # op25 Ins25 (i16, AnmManager.hpp:374)
        self.flag15 = 0  # op31 (3D 相机模式, :477-479; 2D 视图只记状态)
        self.flag17 = 0  # op88: 置位时绘制有效色取 color2 (:987)
        self.flag19 = 0  # ExecuteScript 入口闸 (:203-206)
        self.hit_anim_type = 0  # op83 playerBulletHitAnimationType (:567-569)
        self.time_of_last_sprite_set = 0  # op3 记录 (:238)
        # interrupt 返回点 (:402-403): op89 ReturnFromInterrupt 跳回
        self.interrupt_return_time = 0
        self.interrupt_return_pc = -1
        # g_Supervisor.framerateMultiplier: epilogue 的角速度/缩放增速乘它
        # (:750-874; 决死冻结帧 >1 的慢放补偿), 正常帧恒 1.0
        self.framerate_multiplier = 1.0

    # ---- ExecuteScript (AnmManager.cpp:193-748) ----
    def execute(self) -> None:
        if self.flag19:
            return  # :203-206
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
            if op in (-1, 1):  # EndOfScript / Delete (:228-230)
                self.visible = False
                self.pc = -1
                return
            if op == 2:  # Static (:231-233)
                self.pc = -1
                return
            if op == 3:  # Sprite (:234-238)
                self.visible = True
                self._set_sprite_cb(iv(0) + ref.sprite_base)
                self.time_of_last_sprite_set = self.time
            elif op == 7:  # Scale
                self.scale = [fv(0), fv(1)]
            elif op == 8:  # Alpha
                self.color[3] = iv(0) & 255
            elif op == 9:  # Color: 拆 3 个 int 参 (:249-253)
                self.color[0] = iv(0) & 255
                self.color[1] = iv(1) & 255
                self.color[2] = iv(2) & 255
            elif op == 85:  # Alpha2 (:254-256)
                self.color2[3] = iv(0) & 255
            elif op == 84:  # Color2 (:257-261)
                self.color2[0] = iv(0) & 255
                self.color2[1] = iv(1) & 255
                self.color2[2] = iv(2) & 255
            elif op == 4:  # Jmp
                self.time = ai[1]
                self.pc = ref.offsets.get(ai[0], -1)
                continue
            elif op == 5:  # JmpDec
                cur = self._int_load_ptr(ai[0], fl & 1) - 1
                self._int_store(ai[0], fl & 1, cur)
                if self._int_value(ai[0], fl & 1) > 0:
                    self.time = ai[2]
                    self.pc = ref.offsets.get(ai[1], -1)
                    continue
            elif op == 10:  # FlipX (:276-280): flip 掩码 + 缩放取负
                self.flip ^= 1
                self.scale[0] *= -1.0
            elif op == 24:  # PosMode
                self.use_offset = bool(ai[0])
            elif op == 11:  # FlipY (:284-287)
                self.flip ^= 2
                self.scale[1] *= -1.0
            elif op == 12:  # Rotate
                self.rotation = [fv(0), fv(1), fv(2)]
            elif op == 13:  # AngularVelocity
                self.angle_vel = [fv(0), fv(1), fv(2)]
            elif op == 14:  # ScaleGrowth
                self.scale_growth = [fv(0), fv(1)]
            elif op == 29:  # ScaleTimeLinear (:307-317)
                self.interp_start[4] = 0
                self.interp_end[4] = iv(2)
                self.ease[4] = 0
                self.scale_interp_initial = list(self.scale)
                self.scale_interp_final = [fv(0), fv(1)]
            elif op == 15:  # AlphaTimeLinear (:318-325)
                self.color_interp_initial[3] = self.color[3]
                self.color_interp_final[3] = ai[0] & 255
                self.interp_start[2] = 0
                self.interp_end[2] = iv(1)
                self.ease[2] = 0
            elif op == 16:  # AdditiveBlendMode: 布尔化 (:326-328)
                self.blend_mode = 1 if ai[0] != 0 else 0
            elif op == 82:  # BlendMode: 原生值 (:329-331)
                self.blend_mode = ai[0]
            elif op == 6:  # Pos
                v = np.array([fv(0), fv(1), fv(2)])
                if self.use_offset:
                    self.offset = v
                else:
                    self.pos = v
            elif op in (17, 18, 19):  # PosTimeLinear/Decel/Decel2 (:342-364)
                self.ease[0] = {17: 0, 18: 4, 19: 6}[op]
                self._pos_interp_setup(fv, iv, 0)
            elif op == 79:  # Wait (:365-381)
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
            elif op in (20, 23):  # Stop / StopHide (:382-391)
                if op == 23:
                    self.visible = False
                if not self.pending_interrupt:
                    self.is_stopped = True
                    self.time -= 1
                    self._epilogue()
                    return
                self._handle_interrupt()
                continue
            elif op == 89:  # ReturnFromInterrupt (:426-429)
                self.time = self.interrupt_return_time
                self.pc = self.interrupt_return_pc
                continue
            elif op == 28:  # Visible
                self.visible = bool(ai[0])
            elif op == 22:  # AnchorTopLeft
                self.anchor = 3
            elif op == 25:  # Ins25: 设 type, 不截断 (:436-438)
                self.type = ai[0]
            elif op == 26:  # AddU: 单次 ±1.0 回绕 (:439-453)
                self.uv_scroll[0] = _wrap_uv_th08(self.uv_scroll[0] + fv(0))
            elif op == 27:  # AddV (:454-467)
                self.uv_scroll[1] = _wrap_uv_th08(self.uv_scroll[1] + fv(0))
            elif op == 80:  # UScroll
                self.uv_scroll_vel[0] = fv(0)
            elif op == 81:  # VScroll
                self.uv_scroll_vel[1] = fv(0)
            elif op == 30:  # ZWriteDisable
                self.zwrite_disable = ai[0]
            elif op == 31:  # Ins31: flag15 (:477-479)
                self.flag15 = ai[0]
            elif op == 32:  # PosTime (:480-497)
                self.ease[0] = ai[1] & 0xFF  # interpModes 是 u8 (:333)
                self._pos_interp_setup(fv, iv, 0, arg_base=2, dur_arg=0)
            elif op == 33:  # ColorTime: 拆参 dur,mode,r,g,b (:498-511)
                self.interp_start[1] = 0
                self.interp_end[1] = iv(0)
                self.ease[1] = ai[1] & 0xFF
                self.color_interp_initial[:3] = self.color[:3]
                self.color_interp_final[:3] = [iv(2) & 255, iv(3) & 255, iv(4) & 255]
            elif op == 34:  # AlphaTime (:512-519)
                self.interp_start[2] = 0
                self.interp_end[2] = iv(0)
                self.ease[2] = ai[1] & 0xFF
                self.color_interp_initial[3] = self.color[3]
                self.color_interp_final[3] = iv(2) & 255
            elif op == 86:  # Color2Time (:520-533)
                self.interp_start[5] = 0
                self.interp_end[5] = iv(0)
                self.ease[5] = ai[1] & 0xFF
                self.color2_interp_initial[:3] = self.color2[:3]
                self.color2_interp_final[:3] = [
                    iv(2) & 255,
                    iv(3) & 255,
                    iv(4) & 255,
                ]
            elif op == 87:  # Alpha2Time (:534-541)
                self.interp_start[6] = 0
                self.interp_end[6] = iv(0)
                self.ease[6] = ai[1] & 0xFF
                self.color2_interp_initial[3] = self.color2[3]
                self.color2_interp_final[3] = iv(2) & 255
            elif op == 35:  # RotateTime (:542-555)
                self.interp_start[3] = 0
                self.interp_end[3] = iv(0)
                self.ease[3] = ai[1] & 0xFF
                self.rot_interp_initial = list(self.rotation)
                self.rot_interp_final = [fv(2), fv(3), fv(4)]
            elif op == 36:  # ScaleTime (:556-566)
                self.interp_start[4] = 0
                self.interp_end[4] = iv(0)
                self.ease[4] = ai[1] & 0xFF
                self.scale_interp_initial = list(self.scale)
                self.scale_interp_final = [fv(2), fv(3)]
            elif op == 83:  # Ins83: playerBulletHitAnimationType (:567-569)
                self.hit_anim_type = ai[0]
            elif op == 88:  # Ins88: flag17 = byteArgs[1] (:732-734)
                self.flag17 = (ai[0] >> 8) & 0xFF
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
            # default: 未知指令忽略 (:739-740)
            if advance:
                self.pc += 1
        self._epilogue()

    def _handle_interrupt(self) -> None:
        """Stop/StopHide 的 interrupt 分支 (:392-425); 跳标签前存返回点
        (:419-420) 供 op89 ReturnFromInterrupt 跳回。"""
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
                self.pc = len(instrs)  # 停住(同基类口径)
                return
            idx = nxt
        # 返回点 = 进入 interrupt 前的当前指令/时刻 (:419-420)
        self.interrupt_return_time = self.time
        self.interrupt_return_pc = self.pc
        self.pc = idx + 1
        if self.pc < len(instrs):
            self.time = instrs[self.pc].time
        self.visible = True

    def _epilogue(self) -> None:
        """ExecuteScript 的 stop: 段 (:749-906): 角速度/插值/uv 滚动 +
        time++; 角速度与缩放增速乘 framerateMultiplier, uv 单次 ±1.0 回绕。"""
        fm = self.framerate_multiplier
        for k in range(3):
            if self.angle_vel[k] != 0.0:
                self.rotation[k] = _add_norm_angle(
                    self.rotation[k], fm * self.angle_vel[k]
                )
        for i in range(7):
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
                elif i == 5:  # RGB2 (:837-841)
                    for c in range(3):
                        self.color2[c] = int(
                            (
                                self.color2_interp_final[c]
                                - self.color2_interp_initial[c]
                            )
                            * t
                            + self.color2_interp_initial[c]
                        )
                elif i == 6:  # Alpha2 (:842-844)
                    self.color2[3] = int(
                        (self.color2_interp_final[3] - self.color2_interp_initial[3])
                        * t
                        + self.color2_interp_initial[3]
                    )
        if self.scale_growth[1] != 0.0:
            self.scale[1] += fm * self.scale_growth[1]
        if self.scale_growth[0] != 0.0:
            self.scale[0] += fm * self.scale_growth[0]
        self.uv_scroll[0] = _wrap_uv_th08(self.uv_scroll[0] + self.uv_scroll_vel[0])
        self.uv_scroll[1] = _wrap_uv_th08(self.uv_scroll[1] + self.uv_scroll_vel[1])
        self.time += 1


def _wrap_uv_th08(v: float) -> float:
    """th08 的 uv 回绕: 单次 ±1.0 (AnmManager.cpp:442-452/876-901), 非取模。"""
    if v >= 1.0:
        return v - 1.0
    if v < 0.0:
        return v + 1.0
    return v


__all__ = ["AnmVmTh08", "ScriptRef"]
