"""anm 脚本 VM(anm_vm.py)+ 2D 宿主/特效层(anm_fx.py)测试。

合成指令测 VM 语义(对照 AnmManager.cpp::ExecuteScript), 真实 th07.dat
测特效生命周期/敌人动画帧推进/自机脚本。
"""
from __future__ import annotations

import math
import os
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

sys.path.insert(0, r"D:\python_play\Touhou08")

import pygame  # noqa: E402
import pytest  # noqa: E402

from touhou.schema.anm import AnmInstr  # noqa: E402
from touhou.engine.view.anm_vm import AnmVm, ScriptRef, reset_and_run  # noqa: E402
from touhou.engine.view.anm_fx import (  # noqa: E402
    AnmScriptBank, EffectLayer, TransformCache, Vm2d)

DAT = Path(r"D:\TOUHOU_GAME\[th07] 东方妖妖梦 (日文版)\th07.dat")
NEEDS_DAT = pytest.mark.skipif(not DAT.exists(), reason="需要真实 th07.dat")

pygame.init()


def I(opcode: int, *args, time: int = 0, flags: int = 0) -> AnmInstr:
    """合成指令: args 按 int 给; args_f 视图由 VM 侧分别解读, 这里仅 int 指令用。"""
    args_i = tuple(int(a) for a in args)
    import struct
    args_f = tuple(struct.unpack("<f", struct.pack("<i", a))[0] for a in args_i)
    return AnmInstr(opcode, time, flags, args_i, args_f)


def IF(opcode: int, *args, time: int = 0, flags: int = 0) -> AnmInstr:
    """合成指令: args 按 float 给。"""
    import struct
    args_f = tuple(float(a) for a in args)
    args_i = tuple(struct.unpack("<i", struct.pack("<f", a))[0] for a in args_f)
    return AnmInstr(opcode, time, flags, args_i, args_f)


def IM(opcode: int, *args, time: int = 0, flags: int = 0) -> AnmInstr:
    """合成指令: 混合参数(int 按 int 视图, float 按 float 视图)。"""
    import struct
    args_i, args_f = [], []
    for a in args:
        if isinstance(a, float):
            args_f.append(a)
            args_i.append(struct.unpack("<i", struct.pack("<f", a))[0])
        else:
            args_i.append(int(a))
            args_f.append(struct.unpack("<f", struct.pack("<i", int(a)))[0])
    return AnmInstr(opcode, time, flags, tuple(args_i), tuple(args_f))


def run(instrs, frames: int = 1, sprite_base: int = 0, cb=None) -> AnmVm:
    vm = AnmVm()
    reset_and_run(vm, ScriptRef(list(instrs), sprite_base), cb or (lambda gid: None))
    for _ in range(frames - 1):
        vm.execute()
    return vm


# ---- 基本指令 ----

def test_set_active_sprite_visible_and_base() -> None:
    got = []
    # SET_ACTIVE_SPRITE(7) + EXIT; sprite_base=100 → cb(107)
    vm = run([I(3, 7), I(2)], sprite_base=100, cb=got.append)
    assert got == [107]
    assert vm.visible


def test_scale_alpha_color_rotation_flip() -> None:
    vm = run([
        IF(7, 2.0, 0.5),          # SET_SCALE
        I(8, 128),                # SET_ALPHA
        I(9, 0x0080FF40),         # SET_COLOR 0x00RRGGBB → r=0x80 g=0xff b=0x40
        IF(12, 0.0, 0.0, 1.0),    # SET_ROTATION z=1.0
        I(10),                    # FLIP_X
        I(11),                    # FLIP_Y
        I(2),
    ])
    assert vm.scale == [-2.0, -0.5]
    assert vm.color == [0x80, 0xFF, 0x40, 128]
    assert vm.rotation[2] == pytest.approx(1.0)


def test_blend_mode() -> None:
    vm = run([I(16, 1), I(2)])
    assert vm.blend_mode == 1


def test_exit_hides_and_stops() -> None:
    vm = run([I(3, 0), I(1)])     # EXIT_HIDE
    assert vm.pc == -1 and not vm.visible
    pc_before = vm.pc
    vm.execute()                  # 结束后 execute 无副作用
    assert vm.pc == pc_before


# ---- 插值 ----

def test_fade_interpolation() -> None:
    # SET_ALPHA 200; FADE(0, 10) → 每帧插值, 第 10 帧到 0
    # (reset_and_run 立即执行一帧, FADE 当帧 epilogue 已步进 1 步)
    vm = run([I(8, 200), I(15, 0, 10), I(20)])
    a0 = vm.color[3]
    assert 0 < a0 < 200
    for _ in range(9):
        vm.execute()
    assert vm.color[3] == 0


def test_interp_scale_and_rotate() -> None:
    # INTERP_SCALE_2(dur=4, ease=0, sx 1→3, sy 1→1); INTERP_ROTATE(dur=4, z 0→pi)
    vm = run([IM(36, 4, 0, 3.0, 1.0), IM(35, 4, 0, 0.0, 0.0, math.pi), I(20)])
    for _ in range(3):                      # 首帧已执行 1 次, 共 4 帧到 t=1
        vm.execute()
    assert vm.scale[0] == pytest.approx(3.0)
    assert vm.scale[1] == pytest.approx(1.0)
    assert abs(vm.rotation[2]) == pytest.approx(math.pi)


def test_pos_time_linear() -> None:
    # POS_TIME_LINEAR(x=8, dur=4) → 4 帧后 pos.x=8
    vm = run([IM(17, 8.0, 0.0, 0.0, 4), I(20)])
    for _ in range(3):
        vm.execute()
    assert vm.pos[0] == pytest.approx(8.0)


def test_angle_vel_normalizes() -> None:
    vm = run([IF(13, 0.0, 0.0, 2.0), I(20)])   # ANGVEL z=2.0
    for _ in range(3):                          # 共 4 帧累计 8.0 > pi → 包回 [-pi,pi]
        vm.execute()
    assert -math.pi <= vm.rotation[2] <= math.pi
    assert vm.rotation[2] == pytest.approx(8.0 - 2 * math.pi)


# ---- 控制流 ----

def test_wait_holds_pc() -> None:
    # SET_ALPHA 10; WAIT(3); SET_ALPHA 99; EXIT
    vm = run([I(8, 10), I(79, 3), I(8, 99), I(2)])
    assert vm.color[3] == 10
    vm.execute()
    assert vm.color[3] == 10      # 等待中
    vm.execute()
    vm.execute()
    assert vm.color[3] == 99      # 3 帧后放行
    assert vm.pc == -1


def test_dec_jump_loop() -> None:
    # var10000=2; L: DEC_JUMP(var10000, L, 0); EXIT → 减 2 次后退出
    setv = I(37, 10000, 2, flags=1)          # INT_STORE var10000=2
    decj = I(5, 10000, 16, 0, flags=1)       # 跳到自身(offset 16)
    vm = run([setv, decj, I(2)])
    assert vm.pc == -1
    assert vm.int_vars1[0] == 0


def test_cond_jump_int_eq() -> None:
    # IJEQ(5,5,→EXIT) 跳过 SET_ALPHA; 不等则顺序执行
    # instr0: op67, 4 args → 8+16=24B; instr1: SET_ALPHA 1 arg → 12B; EXIT 在 24+12=36
    off_exit = 36
    vm = run([I(67, 5, 5, off_exit, 0), I(8, 77), I(2)])
    assert vm.pc == -1
    assert vm.color[3] == 255                # 被跳过, 保持初始
    vm2 = run([I(67, 5, 6, off_exit, 0), I(8, 77), I(2)])
    assert vm2.color[3] == 77


def test_int_float_var_ops() -> None:
    # var10000 = 3+4; fvar10004 = 1.5*2.0
    vm = run([
        I(49, 10000, 3, 4, flags=1),         # INT_ADD3 var=3+4
        IF(50, 10004, 1.5, 2.0, flags=1),    # FLOAT_ADD3? op50: a,b=fv(1),fv(2) → 3.5
        I(2),
    ])
    assert vm.int_vars1[0] == 7
    assert vm.float_vars[0] == pytest.approx(3.5)


def test_interrupt_jumps_to_label() -> None:
    # STOP; label(1): SET_ALPHA 44; EXIT
    instrs = [I(20), I(21, 1), I(8, 44), I(2)]
    vm = run(instrs)
    assert vm.is_stopped and vm.color[3] == 255
    vm.pending_interrupt = 1
    vm.execute()
    assert vm.color[3] == 44
    assert vm.pc == -1


def test_rand_and_trig() -> None:
    vm = run([
        I(59, 10000, 100, flags=1),          # RAND_INT var=rand%100
        IF(61, 10004, math.pi / 2, flags=1), # SIN(pi/2)
        I(2),
    ])
    assert 0 <= vm.int_vars1[0] < 100
    assert vm.float_vars[0] == pytest.approx(1.0)


# ---- TransformCache ----

def test_transform_cache_hit_and_quantize() -> None:
    tc = TransformCache()
    img = pygame.Surface((8, 8), pygame.SRCALPHA)
    a = tc.get(img, 1.0, 1.0, 0.0)
    assert tc.get(img, 1.0, 1.0, 0.0) is a          # 命中: 同一对象
    b = tc.get(img, 1.0, 1.0, math.radians(1.0))    # 量化到同一 3° 桶
    assert b is a
    c = tc.get(img, 1.0, 1.0, math.radians(4.0))    # 相邻桶 → 新对象
    assert c is not a
    d = tc.get(img, -1.0, 1.0, 0.0)                 # 翻转是不同键
    assert d is not a


def test_additive_premultiplies_alpha() -> None:
    """C++ 加算=(SRCALPHA, ONE): 透明区(rgb 常为白)不得贡献颜色。"""
    tc = TransformCache()
    img = pygame.Surface((4, 1), pygame.SRCALPHA)
    img.fill((255, 255, 255, 0))                    # 白 rgb + 全透明
    img.set_at((0, 0), (200, 100, 50, 255))         # 一个不透明像素
    vm2d = Vm2d(None, tc)                           # draw 不触 sbank
    vm2d.surf = img
    vm2d.vm.visible = True
    vm2d.vm.blend_mode = 1
    dst = pygame.Surface((4, 1))
    dst.fill((10, 10, 10))
    vm2d.draw(dst, 2, 0)
    # 透明像素: 不变(不预乘会被加成 265→255 的白块)
    assert dst.get_at((3, 0)).r == 10
    # 不透明像素: 10+200=210 / 10+100=110 / 10+50=60
    p = dst.get_at((0, 0))
    assert (p.r, p.g, p.b) == (210, 110, 60)


def test_additive_vm_color_modulates() -> None:
    tc = TransformCache()
    img = pygame.Surface((1, 1), pygame.SRCALPHA)
    img.fill((200, 200, 200, 255))
    vm2d = Vm2d(None, tc)
    vm2d.surf = img
    vm2d.vm.visible = True
    vm2d.vm.blend_mode = 1
    vm2d.vm.color = [128, 255, 255, 128]            # r 调制 + 半透明
    dst = pygame.Surface((1, 1))
    dst.fill((0, 0, 0))
    vm2d.draw(dst, 0, 0)
    p = dst.get_at((0, 0))
    # 有效 alpha=128/255, r 再乘 128/255
    assert p.r == pytest.approx(200 * (128 / 255) * (128 / 255), abs=2)
    assert p.g == pytest.approx(200 * 128 / 255, abs=2)


# ---- 真实数据: 特效生命周期 / 敌人动画 / 自机脚本 ----

@pytest.fixture(scope="module")
def bank():
    from touhou.games.th07.world import DEFAULT_DATA
    from touhou.engine.view.sprite_view import SpriteBank
    return SpriteBank(DEFAULT_DATA)


@NEEDS_DAT
def test_effect_lifecycle(bank) -> None:
    """击坠爆炸(0): 生成→播放→脚本结束回收。"""
    sb = AnmScriptBank(bank, "etama.anm", 0x200)
    assert sb.ok
    fx = EffectLayer(sb, TransformCache())
    fx.spawn(0, 100.0, 100.0, 1)
    assert len(fx) == 1
    frames = 0
    while len(fx) and frames < 600:
        fx.update()
        frames += 1
    assert len(fx) == 0          # 回收
    assert 0 < frames < 600      # 确实播放了若干帧


@NEEDS_DAT
def test_effect_burst_moves_and_focus_attach(bank) -> None:
    sb = AnmScriptBank(bank, "etama.anm", 0x200)
    fx = EffectLayer(sb, TransformCache())
    # 爆皮(7, BURST_FAST): 粒子有初速度且减速
    es = fx.spawn(7, 50.0, 50.0, 4)
    assert len(es) == 4
    assert any(e.vx != 0.0 or e.vy != 0.0 for e in es)
    x0 = [e.x for e in es]
    fx.update()
    assert any(e.x != a for e, a in zip(es, x0))
    # focus 环(24, ATTACH): 跟随自机; interrupt 后退场回收
    h = fx.spawn(24, 10.0, 10.0, 1)
    assert len(h) == 1
    fx.update((200.0, 300.0))
    assert (h[0].x, h[0].y) == (200.0, 300.0)
    EffectLayer.interrupt(h[0], 1)
    frames = 0
    while h[0] in fx.effects and frames < 600:
        fx.update((200.0, 300.0))
        frames += 1
    assert h[0] not in fx.effects


@NEEDS_DAT
def test_effect_color_param(bank) -> None:
    """SpawnParticles 的 D3DCOLOR 进 vm.color(EffectManager.cpp:539)。"""
    sb = AnmScriptBank(bank, "etama.anm", 0x200)
    fx = EffectLayer(sb, TransformCache())
    es = fx.spawn(12, 0.0, 0.0, 1, color=0xFF4040FF)
    assert es[0].vm2d.vm.color == [0x40, 0x40, 0xFF, 0xFF]


@NEEDS_DAT
def test_enemy_animation_frames_advance(bank) -> None:
    """stg1enm 妖精脚本(局部 0/5/10): 逐帧执行 sprite 会切换, 脚本循环不结束。"""
    sb = AnmScriptBank(bank, "stg1enm.anm", 0x900)
    assert sb.ok
    animated = 0
    for sid in (0, 5, 10):
        vm = Vm2d(sb, TransformCache())
        assert vm.start(0x900 + sid)
        seen = {vm.vm.active_sprite_idx}
        for _ in range(180):
            vm.execute()
            seen.add(vm.vm.active_sprite_idx)
        if len(seen) > 1:
            animated += 1
        assert vm.alive                       # 敌动画是循环脚本
    assert animated >= 2


@NEEDS_DAT
def test_player_idle_script_sways(bank) -> None:
    """自机静止脚本(局部 0): sprite 在 0..7 间慢摇。"""
    sb = AnmScriptBank(bank, "player00.anm", 0x400)
    assert sb.ok
    vm = Vm2d(sb, TransformCache())
    assert vm.start(0x400 + 0)
    seen = {vm.vm.active_sprite_idx}
    for _ in range(300):
        vm.execute()
        seen.add(vm.vm.active_sprite_idx)
    assert len(seen) > 1
    assert vm.alive


@NEEDS_DAT
def test_hit_flash_script_is_additive(bank) -> None:
    """自机弹命中脚本(anmFileIdx+32, 如局部 96): 加算 + 淡出(白闪而非白块)。"""
    sb = AnmScriptBank(bank, "player00.anm", 0x400)
    vm = Vm2d(sb, TransformCache())
    assert vm.start(0x400 + 96)
    assert vm.vm.blend_mode == 1
    assert vm.vm.color[3] < 255               # SET_ALPHA 96
    frames = 0
    while vm.alive and frames < 120:
        vm.execute()
        frames += 1
    assert not vm.alive                       # FADE 到 0 后 EXIT_HIDE
