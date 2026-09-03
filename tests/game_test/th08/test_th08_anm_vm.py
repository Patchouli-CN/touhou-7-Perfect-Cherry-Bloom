"""th08 anm 脚本 VM(AnmVmTh08)测试 —— 合成指令测 v3 指令集差集。

对照 th08-ref AnmManager.cpp:226-748 ExecuteScript; 覆盖:
- 新增 op82-89 (BlendMode/Ins83/Color2/Alpha2/Color2Time/Alpha2Time/Ins88/
  ReturnFromInterrupt);
- 同号不同义 op9/16/25/26/27/31/33(与 th07 基类语义差);
- interrupt 返回点(op89 跳回 Stop 重停)、framerateMultiplier、
  7 插值槽(color2 的 RGB2/Alpha2)、uv 单次 ±1.0 回绕。

模式照 tests/game_test/th07/test_th07_anm_vm.py(合成 AnmInstr 直喂 VM)。
"""

from __future__ import annotations

import math
import os
import struct

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pytest  # noqa: E402

from touhou.schema.anm import AnmInstr  # noqa: E402
from touhou.engine.view.anm_vm import ScriptRef, reset_and_run  # noqa: E402
from touhou.games.th08.view.anm_vm import AnmVmTh08  # noqa: E402


def I(opcode: int, *args, time: int = 0, flags: int = 0) -> AnmInstr:
    """合成指令: args 按 int 给。"""
    args_i = tuple(int(a) for a in args)
    args_f = tuple(struct.unpack("<f", struct.pack("<i", a))[0] for a in args_i)
    return AnmInstr(opcode, time, flags, args_i, args_f)


def IF(opcode: int, *args, time: int = 0, flags: int = 0) -> AnmInstr:
    """合成指令: args 按 float 给。"""
    args_f = tuple(float(a) for a in args)
    args_i = tuple(struct.unpack("<i", struct.pack("<f", a))[0] for a in args_f)
    return AnmInstr(opcode, time, flags, args_i, args_f)


def IM(opcode: int, *args, time: int = 0, flags: int = 0) -> AnmInstr:
    """合成指令: 混合参数(int 按 int 视图, float 按 float 视图)。"""
    args_i, args_f = [], []
    for a in args:
        if isinstance(a, float):
            args_f.append(a)
            args_i.append(struct.unpack("<i", struct.pack("<f", a))[0])
        else:
            args_i.append(int(a))
            args_f.append(struct.unpack("<f", struct.pack("<i", int(a)))[0])
    return AnmInstr(opcode, time, flags, tuple(args_i), tuple(args_f))


def run(instrs, frames: int = 1, cb=None) -> AnmVmTh08:
    """th08 脚本 sprite_base=0(op3 参数即扁平 sprite 序号, 不加基址)。"""
    vm = AnmVmTh08()
    reset_and_run(vm, ScriptRef(list(instrs), 0), cb or (lambda gid: None))
    for _ in range(frames - 1):
        vm.execute()
    return vm


# ---- 同号不同义 ----


def test_color_takes_3_int_args() -> None:
    """op9 Color: 拆 3 个 int 参 (AnmManager.cpp:249-253);
    th07 是打包 0xRRGGBB 单参 —— 同号不同义。"""
    vm = run([I(9, 0x80, 0xFF, 0x40), I(2)])
    assert vm.color[:3] == [0x80, 0xFF, 0x40]


def test_additive_blend_mode_booleanized() -> None:
    """op16 AdditiveBlendMode: 布尔化 (:326-328); th07 是原值。"""
    vm = run([I(16, 5), I(2)])
    assert vm.blend_mode == 1  # 5 != 0 → True
    vm2 = run([I(16, 0), I(2)])
    assert vm2.blend_mode == 0


def test_blend_mode_raw() -> None:
    """op82 BlendMode (th08 新增): 原生值不布尔化 (:329-331)。"""
    vm = run([I(82, 5), I(2)])
    assert vm.blend_mode == 5


def test_ins25_sets_type_untruncated() -> None:
    """op25 Ins25: 设 type 不截断 (:436-438); th07 是 auto_rotate & 0xFFFF。"""
    vm = run([I(25, 0x12345), I(2)])
    assert vm.type == 0x12345


def test_adduv_single_wrap_not_modulo() -> None:
    """op26/27 AddU/AddV: 单次 ±1.0 回绕非取模 (:439-467) —
    加 2.5 得 2.3(可超 1), th07 取模会得 0.3。"""
    vm = run([IF(26, 0.8), IF(26, 2.5), I(2)])
    assert vm.uv_scroll[0] == pytest.approx(2.3)
    vm2 = run([IF(27, -0.3), I(2)])
    assert vm2.uv_scroll[1] == pytest.approx(0.7)  # 负值 +1.0 回绕


def test_ins31_sets_flag15() -> None:
    """op31 Ins31: 设 flag15 (:477-479); th07 是 SET_CAMERA_MODE 空操作。"""
    vm = run([I(31, 1), I(2)])
    assert vm.flag15 == 1


def test_color_time_split_args() -> None:
    """op33 ColorTime: 拆参 dur,mode,r,g,b (:498-511) —
    th07 是打包 b[0..2]。4 帧从白插到 (10,20,30)。"""
    vm = run([IM(33, 4, 0, 10, 20, 30), I(20)])
    for _ in range(3):  # 首帧已执行 1 次, 共 4 帧到 t=1
        vm.execute()
    assert vm.color[:3] == [10, 20, 30]


# ---- op83-85 直写状态 ----


def test_ins83_hit_anim_type() -> None:
    """op83 Ins83: playerBulletHitAnimationType (:567-569)。"""
    vm = run([I(83, 3), I(2)])
    assert vm.hit_anim_type == 3


def test_color2_alpha2_set() -> None:
    """op84 Color2 / op85 Alpha2: color2 直写 (:254-261)。"""
    vm = run([I(84, 11, 22, 33), I(85, 44), I(2)])
    assert vm.color2 == [11, 22, 33, 44]


def test_ins88_flag17_byte_arg() -> None:
    """op88 Ins88: flag17 = byteArgs[1] (:732-734) = intArgs[0] 的次低字节。"""
    vm = run([I(88, 0x100), I(2)])
    assert vm.flag17 == 1
    vm2 = run([I(88, 0x01), I(2)])
    assert vm2.flag17 == 0  # 最低字节不算


# ---- color2 插值(7 插值槽的 RGB2/Alpha2) ----


def test_color2_time_interpolates() -> None:
    """op86 Color2Time: 槽 5 (RGB2) 插值 (:520-533)。"""
    vm = run([I(84, 100, 100, 100), IM(86, 4, 0, 200, 150, 50), I(20)])
    for _ in range(3):
        vm.execute()
    assert vm.color2[:3] == [200, 150, 50]


def test_alpha2_time_interpolates() -> None:
    """op87 Alpha2Time: 槽 6 (Alpha2) 插值 (:534-541)。"""
    vm = run([I(85, 200), IM(87, 4, 0, 20), I(20)])
    for _ in range(3):
        vm.execute()
    assert vm.color2[3] == 20


def test_color1_interp_unaffected_by_color2_slots() -> None:
    """op33/34(color1)与 op86/87(color2)走不同插值槽, 互不串。"""
    vm = run(
        [
            I(9, 255, 255, 255),
            I(84, 10, 10, 10),
            IM(33, 4, 0, 50, 60, 70),
            IM(86, 4, 0, 90, 80, 70),
            I(20),
        ]
    )
    for _ in range(3):
        vm.execute()
    assert vm.color[:3] == [50, 60, 70]
    assert vm.color2[:3] == [90, 80, 70]


# ---- interrupt 返回点 / ReturnFromInterrupt ----


def test_interrupt_saves_return_point() -> None:
    """interrupt 跳标签前存返回点 (:419-420): 返回指令 = Stop, 返回时刻 =
    进入 interrupt 前的 currentTimeInScript。"""
    # STOP(time0); label(1): SET_ALPHA 44; RETURN; EXIT
    instrs = [I(20), I(21, 1), I(8, 44), I(89), I(2)]
    vm = run(instrs)
    assert vm.is_stopped and vm.color[3] == 255
    stop_pc, stop_time = vm.pc, vm.time
    vm.pending_interrupt = 1
    vm.execute()
    # op89 跳回 Stop 重新执行 → 无 interrupt → 再停 (:426-429 → :382-391)
    assert vm.interrupt_return_pc == stop_pc
    assert vm.interrupt_return_time == stop_time
    assert vm.is_stopped
    assert vm.color[3] == 44  # 标签段执行过
    # 再停后 execute 幂等(停在 Stop, 每次 execute 原地重停)
    vm.execute()
    assert vm.is_stopped and vm.color[3] == 44


def test_return_from_interrupt_restores_time() -> None:
    """op89 恢复时刻: 标签段推进的时间在返回后回到返回点时刻。"""
    # WAIT(5); STOP; label(1): SET_ALPHA 7; RETURN —— interrupt 后 time 跳回
    instrs = [I(79, 5), I(20), I(21, 1), I(8, 7), I(89), I(2)]
    vm = run(instrs, frames=6)  # WAIT 5 帧后停在 STOP
    assert vm.is_stopped
    vm.pending_interrupt = 1
    vm.execute()
    assert vm.color[3] == 7
    assert vm.is_stopped  # 回到 Stop 重停


# ---- framerateMultiplier / uv epilogue / flip / timeOfLastSpriteSet ----


def test_framerate_multiplier_scales_epilogue() -> None:
    """epilogue 的角速度/缩放增速乘 framerateMultiplier
    (AnmManager.cpp:750-874; 决死冻结帧慢放补偿)。"""
    vm = run([IF(13, 0.0, 0.0, 0.5), IF(14, 0.25, 0.0), I(20)])
    vm.framerate_multiplier = 2.0
    vm2 = AnmVmTh08()
    reset_and_run(
        vm2,
        ScriptRef([IF(13, 0.0, 0.0, 0.5), IF(14, 0.25, 0.0), I(20)], 0),
        lambda gid: None,
    )
    vm2.framerate_multiplier = 1.0
    # reset_and_run 首帧已按 fm=1 推进; 再走一帧对比增量
    r0, s0 = vm.rotation[2], vm.scale[0]
    r1, s1 = vm2.rotation[2], vm2.scale[0]
    vm.execute()
    vm2.execute()
    assert vm.rotation[2] - r0 == pytest.approx(1.0)  # 0.5 * 2
    assert vm2.rotation[2] - r1 == pytest.approx(0.5)
    assert vm.scale[0] - s0 == pytest.approx(0.5)  # 0.25 * 2
    assert vm2.scale[0] - s1 == pytest.approx(0.25)


def test_uv_scroll_epilogue_single_wrap() -> None:
    """epilogue 的 uv 滚动也是单次 ±1.0 回绕 (:876-901): 速度 2.5 → 1.5
    (只减一次 1.0; 取模语义(th07)会得 0.5)。"""
    vm = run([IF(80, 2.5), I(20)])
    assert vm.uv_scroll[0] == pytest.approx(1.5)  # 单次 -1.0, 非取模


def test_flip_mask_toggles() -> None:
    """op10/11 FlipX/Y: flip 掩码翻转 + 缩放取负 (:276-287)。"""
    vm = run([I(10), I(10), I(11), I(2)])
    assert vm.flip == 2  # X 翻两次回 0, Y 翻一次
    assert vm.scale == [1.0, -1.0]


def test_time_of_last_sprite_set() -> None:
    """op3 记录 timeOfLastSpriteSet (:238): time=2 的 op3 在第 2 帧执行。"""
    got = []
    vm = run([I(3, 7, time=2), I(2)], frames=3, cb=got.append)
    assert got == [7]
    assert vm.time_of_last_sprite_set == 2


def test_flag19_gates_execute() -> None:
    """flag19 置位时 ExecuteScript 入口直接返回 (:203-206)。"""
    vm = run([IF(13, 0.0, 0.0, 1.0), I(20)])
    r0 = vm.rotation[2]
    vm.flag19 = 1
    vm.execute()
    assert vm.rotation[2] == r0  # 完全冻结


# ---- 回归: 与基类一致的共性指令(抽查) ----


def test_common_ops_still_work() -> None:
    """变量运算/条件跳/WAIT 等共性路径与基类同语义(指令布局两作同构)。"""
    setv = I(37, 10000, 2, flags=1)  # var10000 = 2
    decj = I(5, 10000, 16, 0, flags=1)  # DEC_JUMP(var10000, 自身偏移 16)
    vm = run([setv, decj, I(2)])
    assert vm.pc == -1
    assert vm.int_vars1[0] == 0
    vm2 = run([IF(61, 10004, math.pi / 2, flags=1), I(2)])
    assert vm2.float_vars[0] == pytest.approx(1.0)
