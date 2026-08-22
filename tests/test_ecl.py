"""ECL 解析器/解释器测试: 真实 .ecl + 手工构造指令流。"""
from __future__ import annotations

import math
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, r"D:\python_play\Touhou08")

from touhou.schema.archive import GameArchive  # noqa: E402
from touhou.engine.ecl import (  # noqa: E402
    EclFile, EclHost, EclOpcode, EclTimelineRunner, EclWorld, Vec3,
)
from touhou.games.th07.ecl_vm import EclMachineTh07 as EclMachine  # noqa: E402
from touhou.games.th07.ecl_vm import EclVarId  # noqa: E402

DAT = Path(r"D:\TOUHOU_GAME\[th07] 东方妖妖梦 (日文版)\th07.dat")
NEEDS_DAT = pytest.mark.skipif(not DAT.exists(), reason="需要真实 th07.dat")

V = EclVarId
OP = EclOpcode


# ---- 手工构造 .ecl 二进制 ----

def _f(x: float) -> int:
    """float → u32 位型。"""
    return struct.unpack("<I", struct.pack("<f", x))[0]


def _instr(time: int, op: int, args: tuple = (), mask: int = 0, skip: int = 0xFF) -> bytes:
    size = 12 + 4 * len(args)
    return struct.pack("<IhhBBH", time, op, size, 0, skip, mask) + b"".join(
        struct.pack("<I", a & 0xFFFFFFFF) for a in args)


def build_ecl(*subs: list[bytes]) -> EclFile:
    """把若干 sub(每条一串指令字节)拼成合法 .ecl 并解析。"""
    n = len(subs)
    header_size = 4 + 64 + 4 * n
    offsets, blobs = [], []
    off = header_size
    for s in subs:
        offsets.append(off)
        blob = b"".join(s) + _instr(0xFFFFFFFF, -1)
        blobs.append(blob)
        off += len(blob)
    header = struct.pack("<hh", n, 0) + struct.pack("<16i", *([0] * 16)) \
        + struct.pack(f"<{n}i", *offsets)
    return EclFile.parse(header + b"".join(blobs))


class RecordingHost(EclHost):
    """把宿主回调全部记成 (名字, 参数) 事件。"""

    def __init__(self) -> None:
        self.events: list[tuple] = []

    def spawn_bullet_pattern(self, props) -> None:
        self.events.append(("bullets", props.sprite, props.count1, props.count2,
                            props.aim_mode, round(props.speed1, 4),
                            round(props.speed2, 4), props.flags))

    def spawn_laser_pattern(self, props):
        self.events.append(("laser", props.type, props.sprite))
        return ("laser-handle", len(self.events))

    def spawn_enemy(self, sub_id, pos, life, item_drop, score, mirror, context_args) -> None:
        self.events.append(("spawn_enemy", sub_id, round(pos.x, 2), round(pos.y, 2),
                            life, item_drop, score, mirror))

    def spawn_item(self, pos, item_type) -> None:
        self.events.append(("item", item_type))

    def begin_spellcard(self, enemy, gui_id, spellcard_idx, name) -> None:
        self.events.append(("spellcard", gui_id, spellcard_idx, name))

    def remove_all_bullets(self, spawn_items) -> None:
        self.events.append(("clear_bullets", spawn_items))


def make_machine(*subs: list[bytes], difficulty: int = 1, life: int = 10):
    host = RecordingHost()
    world = EclWorld(difficulty=difficulty)
    m = EclMachine(build_ecl(*subs), world=world, host=host)
    m.enemy.life = life
    m.start(0)
    return m, host, world


# ---- 真实文件解析 ----

@NEEDS_DAT
def test_parse_all_real_ecl_files() -> None:
    arch = GameArchive.open(DAT)
    total_instr = 0
    for n in range(1, 9):
        f = EclFile.parse(arch.load(f"ecldata{n}.ecl"))
        assert f.sub_count == len(f.subs)
        assert f.timeline_count == len(f.timelines)
        for sub in f.subs:
            assert sub[-1].is_terminator  # 每个 sub 都以 id=-1 收尾
        total_instr += sum(f.opcode_histogram().values())
    assert total_instr > 15000


@NEEDS_DAT
def test_parse_ecldata1_layout() -> None:
    """ecldata1 的头/sub 表/时间轴与 EclManager.cpp Load 的读法一致。"""
    arch = GameArchive.open(DAT)
    data = arch.load("ecldata1.ecl")
    assert len(data) == 44920
    f = EclFile.parse(data)
    assert f.sub_count == 58 and f.timeline_count == 2
    # sub0 第一条: t=0 SPAWN_ITEMS(119) size=16 skip=0xFF mask=0x1
    i0 = f.subs[0][0]
    assert (i0.time, i0.id, i0.size, i0.skip_difficulty, i0.param_mask) \
        == (0, 119, 16, 0xFF, 0x1)
    assert i0.args == (0x2713,)  # 变量 10003 = LOCAL_INT1_4
    # timeline0 第一条: t=600 arg0=3 op=2(spawn+mirror) size=32
    t0 = f.timelines[0][0]
    assert (t0.time, t0.arg0, t0.opcode, t0.size) == (600, 3, 2, 32)
    # 指令 id 分布: 与反编译 EclOpcode 对得上(只有 0 和 141 不在枚举里)
    known = {o.value for o in EclOpcode}
    unknown = {i for i in f.opcode_histogram() if i not in known}
    assert unknown <= {0, 141}


# ---- 变量与数学 ----

def test_int_math_c_semantics() -> None:
    """SET_INT/ADD/SUB/MUL/DIV/MOD: C 整除向零截断、取余符号跟被除数。"""
    m, _, _ = make_machine([
        _instr(0, OP.SET_INT, (V.LOCAL_INT1_1, -7), mask=0b01),
        _instr(0, OP.SET_INT, (V.LOCAL_INT1_2, 2), mask=0b01),
        # DIV: -7 / 2 = -3(C 截断, Python // 得 -4)
        _instr(0, OP.DIV, (V.LOCAL_INT1_3, V.LOCAL_INT1_1, V.LOCAL_INT1_2), mask=0b111),
        # MOD: -7 % 2 = -1
        _instr(0, OP.MOD, (V.LOCAL_INT1_4, V.LOCAL_INT1_1, V.LOCAL_INT1_2), mask=0b111),
        _instr(0, OP.INC, (V.LOCAL_INT1_2,), mask=0b1),
        _instr(0, OP.UNIMP),
    ])
    assert m.step() is False
    a = m.current.args
    assert a.int_vars1[2] == -3
    assert a.int_vars1[3] == -1
    assert a.int_vars1[1] == 3


def test_float_math_and_var_refs() -> None:
    """SET_FLOAT/乘除/LERP + paramMask 选择立即数还是变量。

    float 指令里的变量 id 一律按 f32 值存储(如 10004.0f), 与真实 .ecl 一致。
    """
    vf = lambda v: _f(float(v))  # noqa: E731
    m, _, _ = make_machine([
        _instr(0, OP.SET_FLOAT, (vf(V.LOCAL_FLOAT1_1), _f(1.5)), mask=0b1),
        # arg1 立即数(位1=0): 100.0
        _instr(0, OP.SET_FLOAT, (vf(V.LOCAL_FLOAT1_2), _f(100.0)), mask=0b1),
        # arg1 变量引用(位1=1): 复制 float1_1
        _instr(0, OP.SET_FLOAT, (vf(V.LOCAL_FLOAT1_3), vf(V.LOCAL_FLOAT1_1)),
               mask=0b11),
        _instr(0, OP.MUL_FLOAT,
               (vf(V.LOCAL_FLOAT1_4), vf(V.LOCAL_FLOAT1_1), vf(V.LOCAL_FLOAT1_1)),
               mask=0b111),
        # LERP: (a-b)*t+b, a=100(float1_2) b=0 t=0.25 → 25
        _instr(0, OP.LERP, (vf(V.LOCAL_FLOAT1_5), vf(V.LOCAL_FLOAT1_2),
                            _f(0.0), _f(0.25)), mask=0b011),
        _instr(0, OP.UNIMP),
    ])
    assert m.step() is False
    a = m.current.args
    assert a.float_vars1[0] == 1.5
    assert a.float_vars1[2] == 1.5
    assert a.float_vars1[3] == pytest.approx(2.25)
    assert a.float_vars1[4] == pytest.approx(25.0)


def test_global_vs_context_global_snapshot() -> None:
    """GLOBAL_INT 直通世界; LOCAL_INT3 读 sub 调用时的快照。"""
    m, _, world = make_machine([
        _instr(0, OP.SET_INT, (V.GLOBAL_INT_1, 99), mask=0b01),
        _instr(0, OP.UNIMP),
    ])
    m.step()
    assert world.global_ints[0] == 99
    assert m.current.args.global_ints[0] == 0  # 快照不受活动全局影响


# ---- 跳转 / 时间 / 难度 ----

def test_dec_jump_loop() -> None:
    """DEC_JUMP 计数循环: 循环体跑 3 次后落到 UNIMP。"""
    body_inc = _instr(0, OP.INC, (V.LOCAL_INT1_2,), mask=0b1)
    dec = _instr(0, OP.DEC_JUMP, (0, -len(body_inc), V.LOCAL_INT1_1), mask=0b100)
    m, _, _ = make_machine([
        _instr(0, OP.SET_INT, (V.LOCAL_INT1_1, 3), mask=0b01),
        body_inc,
        dec,
        _instr(0, OP.UNIMP),
    ])
    assert m.step() is False
    assert m.current.args.int_vars1[1] == 3  # INC 执行 3 次
    assert m.current.args.int_vars1[0] == 0


def test_conditional_jump_and_time() -> None:
    """JUMP_IF_EQ 成立跳走(并重置 context time), 不成立顺序前进。"""
    # 成立: var==5 → 跳到 UNIMP, 跳过 SET_INT
    m1, _, _ = make_machine([
        _instr(0, OP.SET_INT, (V.LOCAL_INT1_1, 5), mask=0b01),
        _instr(0, OP.JUMP_IF_EQ, (V.LOCAL_INT1_1, 5, 0, 24 + 20), mask=0b0011),
        _instr(0, OP.SET_INT, (V.LOCAL_INT1_2, 1), mask=0b01),
        _instr(0, OP.UNIMP),
    ])
    m1.step()
    assert m1.current.args.int_vars1[1] == 0  # 被跳过
    # 不成立: var!=5 → 顺序执行
    m2, _, _ = make_machine([
        _instr(0, OP.SET_INT, (V.LOCAL_INT1_1, 4), mask=0b01),
        _instr(0, OP.JUMP_IF_EQ, (V.LOCAL_INT1_1, 5, 0, 24 + 20), mask=0b0011),
        _instr(0, OP.SET_INT, (V.LOCAL_INT1_2, 1), mask=0b01),
        _instr(0, OP.UNIMP),
    ])
    m2.step()
    assert m2.current.args.int_vars1[1] == 1


def test_time_driven_execution() -> None:
    """instr.time 到点才执行; wait timer 期间指令停摆。"""
    m, _, _ = make_machine([
        _instr(0, OP.SET_WAIT_TIMER, (3,)),          # t0: 等 3 帧
        _instr(0, OP.SET_INT, (V.LOCAL_INT1_1, 7), mask=0b01),  # 等完才跑
        _instr(9, OP.SET_INT, (V.LOCAL_INT1_2, 9), mask=0b01),  # t9
        _instr(0, OP.UNIMP),
    ])
    m.step()
    assert m.current.args.int_vars1[0] == 0  # wait 立刻生效, 同帧后续指令不执行
    m.step(); m.step()
    assert m.current.args.int_vars1[0] == 0
    m.step()                                # 第 4 帧: wait 耗尽
    assert m.current.args.int_vars1[0] == 7
    assert m.current.args.int_vars1[1] == 0
    for _ in range(9):                      # 推进到 time=9
        m.step()
    assert m.current.args.int_vars1[1] == 9


def test_difficulty_skip() -> None:
    """skipOnDifficulty 位掩码: 当前难度位为 0 的指令被跳过。"""
    subs = [[
        _instr(0, OP.SET_INT, (V.LOCAL_INT1_1, 1), mask=0b01, skip=0b0001),  # 仅 Easy
        _instr(0, OP.SET_INT, (V.LOCAL_INT1_2, 1), mask=0b01, skip=0xFF),    # 全难度
        _instr(0, OP.UNIMP),
    ]]
    m, _, _ = make_machine(*subs, difficulty=1)  # Normal: 位1
    m.step()
    assert m.current.args.int_vars1[0] == 0
    assert m.current.args.int_vars1[1] == 1
    m, _, _ = make_machine(*subs, difficulty=0)  # Easy: 位0
    m.step()
    assert m.current.args.int_vars1[0] == 1


# ---- sub 调用栈 ----

def test_sub_call_and_ret() -> None:
    """SUB_CALL 压栈进 sub; SUB_RET 恢复现场(sub 里写的局部变量随上下文回滚)。"""
    sub0 = [
        _instr(0, OP.SET_INT, (V.LOCAL_INT1_1, 10), mask=0b01),
        _instr(0, OP.SUB_CALL, (1,)),
        _instr(0, OP.SET_INT, (V.LOCAL_INT1_2, 42), mask=0b01),
        _instr(0, OP.UNIMP),
    ]
    sub1 = [
        _instr(0, OP.SET_INT, (V.LOCAL_INT1_3, 7), mask=0b01),
        _instr(0, OP.SUB_RET),
    ]
    m, _, _ = make_machine(sub0, sub1)
    assert m.step() is False
    a = m.current.args
    assert a.int_vars1[0] == 10
    assert a.int_vars1[1] == 42
    assert a.int_vars1[2] == 0   # sub 里的写入随 savedContext 恢复而回滚(与 C 一致)
    assert m.current.sub_id == 0


# ---- 移动 ----

def test_move_axis_speed_integrates() -> None:
    """SET_POS + SET_AXIS_SPEED: 位置按速度逐帧积分(Enemy::Move)。"""
    m, _, _ = make_machine([
        _instr(0, OP.SET_POS, (_f(100.0), _f(50.0), _f(0.0))),
        _instr(0, OP.SET_AXIS_SPEED, (_f(2.0), _f(1.0), _f(0.0))),
        _instr(9999, OP.UNIMP),  # 结束帧不移动(C: RunEcl 失败直接 despawn)
    ])
    m.step()  # 执行指令, 本帧 Move 应用一次速度
    assert m.enemy.pos.x == pytest.approx(102.0)
    assert m.enemy.pos.y == pytest.approx(51.0)


def test_interp_lerp_moves_pos() -> None:
    """INIT_INTERP: 10 帧 lerp 把 POS_X 从 0 推到 100。"""
    m, _, _ = make_machine([
        _instr(0, OP.INIT_INTERP,
               (_f(float(V.POS_X)), 10, 0, 0, _f(0.0), _f(100.0), _f(0.0), _f(0.0)),
               mask=0),
        _instr(9999, OP.UNIMP),
    ])
    for _ in range(10):
        m.step()
    assert m.enemy.pos.x == pytest.approx(100.0, abs=1e-3)


# ---- 弹幕桥接 ----

def test_spawn_bullet_pattern_outputs_shooter() -> None:
    """SPAWN_BULLET_PATTERN_RING_AIMED: 填好 EnemyBulletShooter 交给宿主。"""
    word0 = 5 | (2 << 16)  # sprite=5, spriteOffset=2
    m, host, _ = make_machine([
        _instr(0, OP.SPAWN_BULLET_PATTERN_RING_AIMED,
               (word0, 3, 2, _f(2.0), _f(1.0), _f(0.5), _f(0.1), 0x40)),
        _instr(0, OP.UNIMP),
    ])
    m.enemy.pos = Vec3(120.0, 80.0, 0.0)
    m.step()
    assert host.events == [("bullets", 5, 3, 2, 2, 2.0, 1.0, 0x40)]


def test_disable_bullets_blocks_spawn() -> None:
    word0 = 5 | (2 << 16)
    m, host, _ = make_machine([
        _instr(0, OP.DISABLE_BULLETS),
        _instr(0, OP.SPAWN_BULLET_PATTERN_RING_AIMED,
               (word0, 3, 2, _f(2.0), _f(1.0), _f(0.5), _f(0.1), 0)),
        _instr(0, OP.UNIMP),
    ])
    m.step()
    assert host.events == []


def test_shoot_interval_respawns_prev_pattern() -> None:
    """SET_SHOOT_INTERVAL 后每间隔自动重放上一条 pattern。"""
    word0 = 5 | (2 << 16)
    m, host, _ = make_machine([
        _instr(0, OP.SPAWN_BULLET_PATTERN_RING_AIMED,
               (word0, 3, 2, _f(2.0), _f(1.0), _f(0.5), _f(0.1), 0)),
        _instr(0, OP.SET_SHOOT_INTERVAL, (4,)),
        _instr(9999, OP.UNIMP),
    ], life=10)
    m.step()          # 帧1: pattern ×1 + interval=4(rank 修正=0), timer 同帧走到 1
    assert len(host.events) == 1
    for _ in range(2):  # 帧2..3: timer 2..3, 不放
        m.step()
    assert len(host.events) == 1
    m.step()          # 帧4: timer 到 4 → 自动放一发
    assert len(host.events) == 2


def test_spellcard_name_decryption() -> None:
    """BEGIN_SPELLCARD: 48 字节名 XOR 0xaa 解密, idx 取 word0 高 u16。"""
    name = b"TestCard\x00".ljust(48, b"\x00")
    enc = bytes(b ^ 0xAA for b in name)
    words = [struct.unpack_from("<I", enc, i * 4)[0] for i in range(12)]
    word0 = 7 | (100 << 16)  # gui_id=7, spellcardIdx=100
    m, host, _ = make_machine([
        _instr(0, OP.BEGIN_SPELLCARD, (word0, *words)),
        _instr(0, OP.UNIMP),
    ])
    m.step()
    # 宣告瞬间先全屏清弹(弹转弹消点, EclManager.cpp:673), 再交接宿主
    assert host.events == [("clear_bullets", True), ("spellcard", 7, 100, "TestCard")]


# ---- 真实 sub 重放 ----

@NEEDS_DAT
def test_replay_real_sub0_death_drop() -> None:
    """ecldata1 sub0(死亡掉落): SPAWN_ITEMS → DIV → SPAWN_POINT_ITEMS → UNIMP。"""
    arch = GameArchive.open(DAT)
    f = EclFile.parse(arch.load("ecldata1.ecl"))
    host = RecordingHost()
    m = EclMachine(f, host=host)
    m.enemy.life = 10
    m.enemy.pos = Vec3(192.0, 100.0, 0.0)
    m.current.args.int_vars1[3] = 4  # LOCAL_INT1_4 = 掉落数(宿主预设, 如 itemDrop)
    m.start(0)
    m.trace = []
    assert m.step() is False  # 全部 time=0, 一帧跑完到 UNIMP
    assert m.trace == [OP.SPAWN_ITEMS, OP.DIV, OP.SPAWN_POINT_ITEMS, OP.UNIMP]
    # 火力未满: 第 1 个掉大P(2), 其余小P(0); int1_4 经 DIV/2=2 → 再掉 2 个点(1)
    assert [e[1] for e in host.events if e[0] == "item"] == [2, 0, 0, 0, 1, 1]
    assert m.current.args.int_vars1[3] == 2


@NEEDS_DAT
def test_replay_real_sub2_callback_loop() -> None:
    """ecldata1 sub2(粒子回调): 逐帧验证 DEC_JUMP 循环的执行序列。"""
    arch = GameArchive.open(DAT)
    f = EclFile.parse(arch.load("ecldata1.ecl"))
    m = EclMachine(f, host=RecordingHost())
    m.enemy.life = 10
    m.current.args.global_ints[0] = 2  # LOCAL_INT3_1 = 循环次数(快照变量)
    m.start(2)
    m.trace = []
    frames = []
    for _ in range(9):
        m.trace.clear()
        ok = m.step()
        frames.append((ok, list(m.trace)))
    assert frames[0] == (True, [OP.SET_INT, OP.PLAY_SOUND, OP.SPAWN_PARTICLES])
    assert frames[1] == (True, []) and frames[2] == (True, []) and frames[3] == (True, [])
    assert frames[4] == (True, [OP.DEC_JUMP, OP.SPAWN_PARTICLES])
    assert frames[5][1] == [] and frames[6][1] == [] and frames[7][1] == []
    # 第 9 帧: 计数归零 → SUB_RET; 裸跑没压栈 → 栈下溢, 脚本结束
    assert frames[8] == (False, [OP.DEC_JUMP, OP.SUB_RET])
    assert m.finished


# ---- 时间轴 ----

@NEEDS_DAT
def test_timeline_spawns_at_scheduled_time() -> None:
    """真实 timeline0: 第 600 帧刷出第一条(op=2: 显式参数 + mirror)。"""
    arch = GameArchive.open(DAT)
    f = EclFile.parse(arch.load("ecldata1.ecl"))
    host = RecordingHost()
    world = EclWorld()
    tl = EclTimelineRunner(f, 0, world, host)
    first = f.timelines[0][0]
    for _ in range(600):
        tl.step()
        assert host.events == []
    tl.step()  # time==600
    assert len(host.events) == 1
    name, sub_id, x, y, life, drop, score, mirror = host.events[0]
    assert (name, sub_id, mirror) == ("spawn_enemy", 3, 1)
    assert (x, y) == (round(first.arg_float(0), 2), round(first.arg_float(1), 2))
    assert (life, drop, score) == (first.arg_int(3), first.arg_int(4), first.arg_int(5))


def test_timeline_msg_wait_stalls() -> None:
    """op=9: msg_wait 为 True 时时间轴暂停(time 不前进)。"""
    # 手工拼一个带时间轴的 ecl
    sub = [_instr(0xFFFFFFFF, -1)]
    n = 1
    header_size = 4 + 64 + 4 * n
    tl_off = header_size + len(sub[0])
    header = struct.pack("<hh", n, 1) + struct.pack(
        "<16i", tl_off, *([0] * 15)) + struct.pack("<i", header_size)
    tl = struct.pack("<hhhh", 0, 0, 9, 8) + struct.pack("<hhhh", -1, 0, 0, 8)
    f = EclFile.parse(header + b"".join(sub) + tl)

    class MsgHost(RecordingHost):
        waiting = True

        def msg_wait(self) -> bool:
            return self.waiting

    host = MsgHost()
    runner = EclTimelineRunner(f, 0, EclWorld(), host)
    for _ in range(5):
        runner.step()
        assert runner.time == 0  # 一直停着
    host.waiting = False
    runner.step()
    assert runner.time == 1
    assert runner.done
