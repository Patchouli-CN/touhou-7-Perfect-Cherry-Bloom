"""ECL 翻译基类(engine/translate)通用测试 —— stub VM + stub 格式注册假作品。

铁律: 通用层禁止 import games.*(AST 守护, 见 test_api.py); 这里用最小
stub(EclSpec.machine/file_format 鸭子类型)钉 record/translate 模板方法、
激光假句柄追踪与错误路径; 真 th07 回放见 game_test/th07/test_th07_ecl_translate.py。

CONTROL 模式(静态控制流)测试用假作品 test02(file_format=真 EclFile):
ECL 字节流手工构造(_instr/_build_ecl 写法参考 tests/test_ecl_codec.py),
不走 VM, 机器类只是注册占位。

AUTO 模式测试用假作品 test03(真 EclFile + StubAutoMachine): stub VM 按
字节流里 fire 指令的下标打 executing_instr 溯源标记, 模拟"静态已覆盖 /
静态跳过 / 运行时内部触发(自动射击)"三种事件来源。
"""

from __future__ import annotations

import math
import struct
from types import SimpleNamespace

import pytest

from touhou.engine.ecl import EclFile, EnemyBulletShooter, EnemyLaserShooter, EclOpcode
from touhou.engine.translate import (
    EclTranslatorBase,
    IrIf,
    IrLoop,
    IrOp,
    IrSeq,
    TranslateMode,
    YoukaiDanmakuTranslator,
    decode_spellcard_name,
    list_spellcards,
    spellcard_name,
)
from touhou.registry import register_ecl, registered_games

FAKE_ECL_GAME = "test01"
FAKE_IR_GAME = "test02"  # CONTROL 模式用: 真 EclFile 格式 + 占位机器
FAKE_AUTO_GAME = "test03"  # AUTO 模式用: 真 EclFile + 打溯源标记的 stub VM


class StubEclFile:
    """stub 格式类: parse 原样持有字节; subs 长度供 record 的 sub_id 边界检查。"""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.subs = [b""] * 4

    @classmethod
    def parse(cls, data: bytes) -> "StubEclFile":
        return cls(data)


class StubMachine:
    """stub VM: 按帧脚本驱动宿主回调(符卡/周期弹/激光三段+旋转后停)。"""

    def __init__(self, ecl_file, enemy=None, world=None, host=None) -> None:
        self.host = host
        self.finished = False
        self._t = 0
        self._laser = None

    def start(self, sub_id: int) -> None:
        self.sub_id = sub_id

    def step(self) -> bool:
        t = self._t
        self._t += 1
        h = self.host
        if t == 0:
            h.begin_spellcard(None, 7, 3, "氷符「テスト」")
        if t in (0, 4, 8, 12):  # 固定间隔 4 的同构 pattern(折叠靶子)
            h.spawn_bullet_pattern(
                EnemyBulletShooter(
                    sprite=0,
                    count1=8,
                    count2=1,
                    speed1=2.0,
                    aim_mode=2,
                    angle1=t * 0.1,
                )
            )
        if t == 1:  # 单发(one-shot 靶子)
            h.spawn_bullet_pattern(
                EnemyBulletShooter(sprite=7, count1=1, count2=1, speed1=1.0)
            )
        if t == 2:
            self._laser = h.spawn_laser_pattern(
                EnemyLaserShooter(
                    duration=100,
                    start_time=10,
                    end_time=5,
                    width=8.0,
                    sprite_offset=1,
                    type=1,
                    angle1=0.5,
                )
            )
        if 3 <= t <= 6:
            h.laser_add_angle(self._laser, 0.01)
        if t == 7:
            h.laser_stop(self._laser)
        if t == 9:
            h.spawn_item(None, 1)  # 不可翻译回调: 跳过不炸
        if t >= 20:
            self.finished = True
            return False
        return True


if FAKE_ECL_GAME not in registered_games():
    register_ecl(FAKE_ECL_GAME, file_format=StubEclFile)(StubMachine)

if FAKE_IR_GAME not in registered_games():
    register_ecl(FAKE_IR_GAME, file_format=EclFile)(StubMachine)


class StubAutoMachine:
    """AUTO 模式 stub VM: 模拟三种事件来源(对应真 VM 的 executing_instr 语义)。

    - fires[0](静态可翻的 fire 指令): 回调期间挂 executing_instr → 事件
      origin = (sub_id, offset), AUTO 应去重;
    - fires[1](静态跳过的 fire 指令, 变量依赖): 同样挂 executing_instr,
      origin 指向被跳过指令 → AUTO 应补回;
    - 无 executing_instr 的发射: 模拟 SET_SHOOT_INTERVAL 自动射击
      (_frame_update 帧收尾, 不归属任何指令) → origin=None, AUTO 应补回。
    """

    def __init__(self, ecl_file, enemy=None, world=None, host=None) -> None:
        self.host = host
        self.file = ecl_file
        self.current = SimpleNamespace(sub_id=-1)
        self.executing_instr = None
        self.finished = False
        self._t = 0
        self._fires: list = []

    def start(self, sub_id: int) -> None:
        self.current.sub_id = sub_id
        self._fires = [
            i for i in self.file.subs[sub_id] if 64 <= i.id <= 72 and not i.is_terminator
        ]

    def _shoot(self, count1: int, speed1: float) -> None:
        self.host.spawn_bullet_pattern(
            EnemyBulletShooter(sprite=0, count1=count1, count2=1, speed1=speed1, aim_mode=2)
        )

    def step(self) -> bool:
        t = self._t
        self._t += 1
        if t == 0:
            self.host.begin_spellcard(None, 5, 0, "氷符「テスト」")
        if t in (0, 3, 6):  # 静态已覆盖(fires[0]) → 去重靶子(count 8)
            self.executing_instr = self._fires[0]
            self._shoot(8, 2.0)
            self.executing_instr = None
        if t in (1, 4, 7):  # 运行时内部(自动射击) → origin None(count 4)
            self._shoot(4, 1.0)
        if t in (2, 5, 8):  # 静态跳过的指令(fires[1]) → 补回靶子(count 2)
            self.executing_instr = self._fires[1]
            self._shoot(2, 3.0)
            self.executing_instr = None
        if t >= 10:
            self.finished = True
            return False
        return True


if FAKE_AUTO_GAME not in registered_games():
    register_ecl(FAKE_AUTO_GAME, file_format=EclFile)(StubAutoMachine)


# ---- CONTROL 模式: 手工构造 ECL 字节流 ----


def _f(x: float) -> int:
    """float → u32 位型(指令字里的 float 操作数)。"""
    return struct.unpack("<I", struct.pack("<f", x))[0]


def _instr(time: int, op: int, args: tuple = (), mask: int = 0) -> bytes:
    size = 12 + 4 * len(args)
    return struct.pack("<IhhBBH", time, op, size, 0, 0xFF, mask) + b"".join(
        struct.pack("<I", a & 0xFFFFFFFF) for a in args
    )


class _J:
    """跳转目标占位(指令下标); _mk 两遍扫描时解成相对偏移。"""

    def __init__(self, idx: int) -> None:
        self.idx = idx


def _mk(*entries: tuple) -> bytes:
    """把 (time, op, args, mask?) 指令序列拼成单 sub 的 .ecl 字节流。"""
    sizes = [12 + 4 * len(e[2]) for e in entries]
    offsets = []
    off = 4 + 64 + 4  # 单 sub 头长
    for s in sizes:
        offsets.append(off)
        off += s
    blob = b""
    for entry, o in zip(entries, offsets):
        time, op, args = entry[0], entry[1], entry[2]
        mask = entry[3] if len(entry) > 3 else 0
        resolved = tuple((offsets[a.idx] - o) if isinstance(a, _J) else a for a in args)
        blob += _instr(time, op, resolved, mask)
    terminator = _instr(0xFFFFFFFF, -1)
    header = struct.pack("<hh", 1, 0) + struct.pack("<16i", *([0] * 16))
    header += struct.pack("<i", 4 + 64 + 4)
    return header + blob + terminator


def _spellcard_instr(gui_id: int, name: str) -> tuple:
    """BEGIN_SPELLCARD(op 90): word0 = gui_id|idx<<16, word1..12 = 名 XOR 0xAA。"""
    raw = name.encode("shift_jis")[:48].ljust(48, b"\x00")
    words = [gui_id & 0xFFFF] + [
        struct.unpack_from("<I", bytes(b ^ 0xAA for b in raw), i * 4)[0] for i in range(12)
    ]
    return (0, EclOpcode.BEGIN_SPELLCARD, tuple(words))


def _fire_ring(
    time: int, *, angle: float = 0.0, angle_var: bool = False, count_var: bool = False
) -> tuple:
    """SPAWN_BULLET_PATTERN_RING_AIMED: sprite0 offset1, 8×1, speed 2.0。"""
    mask = (64 if angle_var else 0) | (4 if count_var else 0)
    return (
        time,
        66,
        (
            1 << 16,
            10000 if count_var else 8,
            1,
            _f(2.0),
            _f(2.0),
            _f(10004.0) if angle_var else _f(angle),
            _f(0.0),
            0,
        ),
        mask,
    )


def _nodes_of(ir: IrSeq) -> list:
    return ir.nodes


class StubTranslator(EclTranslatorBase):
    """模板方法验证用: compile 只数事件。"""

    def compile(self, trace) -> dict:
        return {"events": len(trace)}


# ---- 构造错误路径 ----


def test_unregistered_game_keyerror() -> None:
    """未注册作品: NotRegisteredError(KeyError 子类), 含作品名。"""
    with pytest.raises(KeyError, match="未注册的作品.*th00"):
        StubTranslator("th00")


def test_registered_but_no_ecl_dimension() -> None:
    """只注册其他维度的作品(conftest 的 test00): 报'缺 ECL 维度'。"""
    with pytest.raises(ValueError, match="缺 ECL 维度.*register_ecl.*test00"):
        StubTranslator("test00")


def test_sub_id_out_of_range() -> None:
    """sub_id 越界: 清晰 ValueError。"""
    with pytest.raises(ValueError, match="sub_id 99 越界"):
        StubTranslator(FAKE_ECL_GAME).record(b"\x00" * 8, 99)


# ---- record: 录制与激光句柄追踪 ----


def test_record_captures_frames_and_kinds() -> None:
    tr = StubTranslator(FAKE_ECL_GAME)
    trace = tr.record(b"\x00" * 8, 1)
    kinds = [ev.kind for ev in trace]
    assert kinds.count("bullets") == 5  # 4 周期 + 1 单发
    assert kinds.count("laser") == 1
    assert kinds[0] == "spellcard"
    # frame 戳: 周期弹在 0/4/8/12, 单发在 1, 激光在 2
    frames = {(ev.kind, ev.frame) for ev in trace}
    assert ("bullets", 12) in frames and ("laser", 2) in frames
    # 弹幕快照字段完整(msgspec 快照是纯 JSON 数据)
    shot = next(ev for ev in trace if ev.kind == "bullets").data
    assert shot["sprite"] == 0 and shot["count1"] == 8 and shot["speed1"] == 2.0


def test_record_laser_handle_tracking() -> None:
    """spawn_laser_pattern 的假句柄把后续操作追进同一条 laser trace。"""
    tr = StubTranslator(FAKE_ECL_GAME)
    trace = tr.record(b"\x00" * 8, 1)
    laser = next(ev for ev in trace if ev.kind == "laser")
    assert laser.data["handle"] == 1
    adds = [u for u in laser.data["updates"] if u["op"] == "add_angle"]
    assert len(adds) == 4 and all(u["delta"] == 0.01 for u in adds)
    assert [u["frame"] for u in adds] == [3, 4, 5, 6]
    assert laser.data["stop_frame"] == 7


def test_record_max_frames_truncates() -> None:
    """max_frames 截断: 脚本未结束时按上限停。"""
    tr = StubTranslator(FAKE_ECL_GAME)
    trace = tr.record(b"\x00" * 8, 1, max_frames=5)
    assert tr.last_finished is False
    assert tr.last_frame_count == 5
    assert max(ev.frame for ev in trace) < 5


def test_record_context_override() -> None:
    """context 覆盖 EclWorld 字段; 未知键报错。"""
    tr = StubTranslator(FAKE_ECL_GAME)
    tr.record(b"\x00" * 8, 1, context={"difficulty": 3})
    assert tr.world.difficulty == 3
    with pytest.raises(ValueError, match="未知 EclWorld 上下文字段"):
        tr.record(b"\x00" * 8, 1, context={"nope": 1})


# ---- translate 模板方法 ----


def test_translate_template_method() -> None:
    out = StubTranslator(FAKE_ECL_GAME).translate(b"\x00" * 8, 1)
    assert out == {"events": 7}  # 4 周期弹 + 1 单发 + 1 激光 + 1 符卡


# ---- 妖归编译(stub trace 端到端结构) ----


def test_youkai_compile_structure_and_folding() -> None:
    """周期链折叠成 tick_interval 门控; 单发走 compare one-shot; 激光三段时序。"""
    out = YoukaiDanmakuTranslator(FAKE_ECL_GAME).translate(b"\x00" * 8, 1)
    assert out["id"] == "youkaishomecoming:ecl_test01_card7"
    assert out["display"]["name"] == "氷符「テスト」"
    assert out["custom_names"] == {"phase:main": "氷符「テスト」"}
    phase = out["phases"][out["entry_phase"]]
    actions = phase["on_tick"]

    folded = next(
        a for a in actions if a.get("condition", {}).get("type") == "tick_interval"
    )
    assert folded["type"] == "conditional"
    assert folded["condition"]["interval"] == 4
    fire = folded["if_true"][0]
    assert fire["type"] == "fire_danmaku"
    assert fire["pattern"] == "ring" and fire["count"] == 8
    assert fire["aim_mode"] == "direction_to_target"

    single = next(a for a in actions if a.get("condition", {}).get("type") == "compare")
    assert single["condition"]["right"] == 1

    laser = next(a for a in actions if a["if_true"][0]["type"] == "fire_laser")[
        "if_true"
    ][0]
    assert laser["color"] == "red"
    assert laser["setup_prepare"] == 10 and laser["setup_end"] == 5
    assert laser["lifetime"] == 5  # stop_frame(7) - spawn(2), 收紧常驻占位 duration
    # 旋转后停 → composite 前 rotate 后 zero
    mover = laser["mover"]
    assert mover["type"] == "composite"
    assert mover["segments"][0]["mover"]["type"] == "rotate"
    assert mover["segments"][1]["mover"] == {"type": "zero"}


def test_decode_spellcard_name() -> None:
    """XOR 0xAA + Shift-JIS 解码(对照 ecl_vm.py _begin_spellcard)。"""
    raw = bytes(b ^ 0xAA for b in "氷符".encode("shift_jis")) + b"\xaa\xaa"
    assert decode_spellcard_name(raw) == "氷符"


# ==================== CONTROL 模式 ====================


def test_translate_mode_dispatch_and_unsupported_control() -> None:
    """模式分发: 默认/显式 DIRECT 走回放; stub 未实现 compile_ir → 中文报错。"""
    tr = StubTranslator(FAKE_IR_GAME)
    data = _mk(_fire_ring(0))
    assert tr.translate(data, 0) == {"events": 7}  # stub VM 脚本帧
    assert tr.translate(data, 0, mode=TranslateMode.DIRECT) == {"events": 7}
    with pytest.raises(NotImplementedError, match="不支持 CONTROL 模式"):
        tr.translate(data, 0, mode=TranslateMode.CONTROL)


def test_parse_ir_sub_id_out_of_range() -> None:
    with pytest.raises(ValueError, match="sub_id 3 越界"):
        StubTranslator(FAKE_IR_GAME).parse_ir(_mk(_fire_ring(0)), 3)


def test_ir_unconditional_back_edge_is_infinite_loop() -> None:
    """无条件 JUMP 回边 → IrLoop(condition=None), 保留 loop_time/period 时间语义。"""
    data = _mk(
        (0, EclOpcode.SET_INT, (10000, 3), 1),
        _fire_ring(10),
        (20, EclOpcode.JUMP, (10, _J(1))),
    )
    ir = StubTranslator(FAKE_IR_GAME).parse_ir(data, 0)
    nodes = _nodes_of(ir)
    assert len(nodes) == 2 and isinstance(nodes[0], IrOp)
    loop = nodes[1]
    assert isinstance(loop, IrLoop)
    assert loop.condition is None and loop.counter_var == -1
    assert loop.loop_time == 10 and loop.period == 10  # 20 - 10
    assert len(loop.body) == 1 and isinstance(loop.body[0], IrOp)
    assert loop.body[0].instr.id == 66 and loop.body[0].instr.time == 10


def test_ir_dec_jump_back_edge_is_counter_loop() -> None:
    """DEC_JUMP 回边 → 计数循环(counter_var + >0 条件)。"""
    data = _mk(
        (0, EclOpcode.SET_INT, (10000, 5), 1),
        _fire_ring(10),
        (30, EclOpcode.DEC_JUMP, (10, _J(1), 10000), 4),
    )
    loop = _nodes_of(StubTranslator(FAKE_IR_GAME).parse_ir(data, 0))[1]
    assert isinstance(loop, IrLoop)
    assert loop.counter_var == 10000
    assert loop.condition is not None and loop.condition.op == ">"
    assert loop.condition.lhs.is_var and loop.condition.lhs.value == 10000
    assert loop.period == 20  # 30 - 10


def test_ir_conditional_back_edge_is_conditional_loop() -> None:
    """JUMP_IF_* 回边 → 条件循环(比较操作数带变量标记)。"""
    data = _mk(
        _fire_ring(10),
        (20, EclOpcode.JUMP_IF_LT, (10000, 10, 10, _J(0)), 1),
    )
    loop = _nodes_of(StubTranslator(FAKE_IR_GAME).parse_ir(data, 0))[0]
    assert isinstance(loop, IrLoop)
    assert loop.condition is not None and loop.condition.op == "<"
    assert loop.condition.lhs.is_var and loop.condition.lhs.value == 10000
    assert not loop.condition.rhs.is_var and loop.condition.rhs.value == 10


def test_ir_conditional_forward_jump_is_if() -> None:
    """条件前跳 → 单臂 IrIf。"""
    data = _mk(
        (0, EclOpcode.JUMP_IF_EQ, (3, 3, 0, _J(2))),
        _fire_ring(10),
        _fire_ring(20),
    )
    nodes = _nodes_of(StubTranslator(FAKE_IR_GAME).parse_ir(data, 0))
    assert len(nodes) == 2
    if_node = nodes[0]
    assert isinstance(if_node, IrIf)
    assert if_node.condition.op == "==" and not if_node.condition.lhs.is_var
    assert len(if_node.if_true) == 1 and isinstance(if_node.if_true[0], IrOp)
    assert if_node.if_true[0].instr.time == 10 and not if_node.if_false
    assert isinstance(nodes[1], IrOp) and nodes[1].instr.time == 20


def test_ir_if_else_double_arm() -> None:
    """if_true 末尾是无条件 JUMP 且跳到更后面 → 双臂 IrIf。"""
    data = _mk(
        (0, EclOpcode.JUMP_IF_NEQ, (1, 2, 0, _J(3))),
        _fire_ring(10),
        (0, EclOpcode.JUMP, (0, _J(4))),
        _fire_ring(20),
        _fire_ring(30),
    )
    nodes = _nodes_of(StubTranslator(FAKE_IR_GAME).parse_ir(data, 0))
    assert len(nodes) == 2
    if_node = nodes[0]
    assert isinstance(if_node, IrIf) and if_node.condition.op == "!="
    assert [n.instr.time for n in if_node.if_true] == [10]
    assert [n.instr.time for n in if_node.if_false] == [20]
    assert isinstance(nodes[1], IrOp) and nodes[1].instr.time == 30


def test_ir_nested_loops() -> None:
    """嵌套回边 → 嵌套 IrLoop。"""
    data = _mk(
        _fire_ring(5),
        _fire_ring(10),
        (10, EclOpcode.JUMP, (5, _J(1))),  # 内层: body=[fireB], period 5
        (30, EclOpcode.JUMP, (0, _J(0))),  # 外层: 包住前 3 条
    )
    nodes = _nodes_of(StubTranslator(FAKE_IR_GAME).parse_ir(data, 0))
    assert len(nodes) == 1
    outer = nodes[0]
    assert isinstance(outer, IrLoop) and outer.period == 30
    assert isinstance(outer.body[0], IrOp)
    inner = outer.body[1]
    assert isinstance(inner, IrLoop) and inner.period == 5
    assert len(inner.body) == 1


def test_ir_irreducible_escape_jump_stays_flat() -> None:
    """循环体内有逃逸跳转 → 不可归约, 全部平铺为 IrOp(不炸不挂)。"""
    data = _mk(
        _fire_ring(10),
        (0, EclOpcode.JUMP_IF_EQ, (1, 1, 0, _J(4))),  # 逃逸出循环
        _fire_ring(20),
        (30, EclOpcode.JUMP, (10, _J(0))),  # 回边
        _fire_ring(40),
        (0, EclOpcode.JUMP, (0, 9999)),  # 目标不在 sub 内
    )
    nodes = _nodes_of(StubTranslator(FAKE_IR_GAME).parse_ir(data, 0))
    assert len(nodes) == 6 and all(isinstance(n, IrOp) for n in nodes)


# ---- 符卡名 helper(spellcard_name / list_spellcards) ----


def test_spellcard_name_and_list_spellcards() -> None:
    """字节切片/XOR 解码收进 helper: 调用方只给指令/EclFile, 不碰布局。"""
    data = _mk(
        _fire_ring(30),
        _spellcard_instr(7, "氷符「テスト」"),
        _spellcard_instr(8, "霜符「フロスト」"),
    )
    ecl_file = EclFile.parse(data)
    cards = list_spellcards(ecl_file)
    assert cards == [(0, "氷符「テスト」"), (0, "霜符「フロスト」")]
    # 单指令 helper 与列表 helper 同源
    instr = [ins for ins in ecl_file.subs[0] if ins.id == EclOpcode.BEGIN_SPELLCARD][0]
    assert spellcard_name(instr) == "氷符「テスト」"


# ---- 妖归 CONTROL 端到端(合成字节流) ----


def test_youkai_control_counter_loop_with_angle_expr() -> None:
    """计数循环 + 仿射角度变量 → repeat(count) + delay + angle_offset='$i * k'。"""
    data = _mk(
        _spellcard_instr(7, "氷符「テスト」"),
        (0, EclOpcode.SET_INT, (10000, 3), 1),
        (0, EclOpcode.SET_FLOAT, (_f(10004.0), _f(0.0)), 1),
        _fire_ring(60, angle_var=True),
        (60, EclOpcode.ADD_FLOAT, (_f(10004.0), _f(10004.0), _f(0.5)), 3),
        (60, EclOpcode.DEC_JUMP, (60, _J(3), 10000), 4),
    )
    out = YoukaiDanmakuTranslator(FAKE_IR_GAME).translate(
        data, 0, mode=TranslateMode.CONTROL
    )
    assert out["display"]["name"] == "氷符「テスト」"
    assert out["id"] == "youkaishomecoming:ecl_test02_card7"
    actions = out["phases"][out["entry_phase"]]["on_tick"]
    assert len(actions) == 1
    repeat = actions[0]
    assert repeat["type"] == "repeat" and repeat["count"] == 3
    delay = repeat["body"][0]
    assert delay["type"] == "delay" and delay["delay_ticks"] == "$i + 60"
    fire = delay["body"][0]
    assert fire["type"] == "fire_danmaku" and fire["pattern"] == "ring"
    assert fire["count"] == 8 and fire["angle_offset"] == f"$i * {round(math.degrees(0.5), 4)}"


def test_youkai_control_infinite_loop_and_if() -> None:
    """无限循环 → repeat(count=近似上限); 常量条件 IrIf → conditional。"""
    data = _mk(
        (0, EclOpcode.JUMP_IF_GEQ, (5, 3, 0, _J(2))),
        _fire_ring(30),
        _fire_ring(100),
        (100, EclOpcode.JUMP, (100, _J(2))),
    )
    out = YoukaiDanmakuTranslator(FAKE_IR_GAME).translate(
        data, 0, mode=TranslateMode.CONTROL
    )
    actions = out["phases"][out["entry_phase"]]["on_tick"]
    cond_action, repeat = actions
    assert cond_action["type"] == "conditional"
    assert cond_action["condition"] == {
        "type": "compare",
        "left": 5,
        "op": ">=",
        "right": 3,
    }
    one_shot = cond_action["if_true"][0]
    assert one_shot["condition"]["right"] == 30
    assert repeat["type"] == "repeat" and repeat["count"] == 100000
    assert repeat["body"][0]["delay_ticks"] == "$i + 100"


def test_youkai_control_unmappable_var_operand_skipped() -> None:
    """带步进的非角度变量操作数(count)不可映射 → 跳过指令, 空循环体不产出。"""
    data = _mk(
        (0, EclOpcode.SET_INT, (10000, 8), 1),
        _fire_ring(10, count_var=True),
        (10, EclOpcode.INC, (10000,), 1),
        (10, EclOpcode.JUMP, (10, _J(1))),
    )
    out = YoukaiDanmakuTranslator(FAKE_IR_GAME).translate(
        data, 0, mode=TranslateMode.CONTROL
    )
    assert out["phases"][out["entry_phase"]]["on_tick"] == []


# ==================== AUTO 模式 ====================


def _auto_data() -> bytes:
    """fires[0] 静态可翻; fires[1] 的 count 依赖不可映射变量 → 静态跳过。"""
    return _mk(
        _spellcard_instr(5, "氷符「テスト」"),
        _fire_ring(0),  # fires[0]: 静态可翻
        (0, EclOpcode.SET_INT, (10000, 10001), 3),  # count 变量值来自变量 → unknown
        _fire_ring(10, count_var=True),  # fires[1]: 静态跳过
    )


def _auto_fire_offsets() -> list[int]:
    ecl_file = EclFile.parse(_auto_data())
    return [
        i.offset for i in ecl_file.subs[0] if 64 <= i.id <= 72 and not i.is_terminator
    ]


def test_record_provenance() -> None:
    """provenance: 指令触发的回调带 (sub_id, offset); 运行时内部触发 → None。"""
    tr = YoukaiDanmakuTranslator(FAKE_AUTO_GAME)
    trace = tr.record(_auto_data(), 0)
    covered, auto_shoot, skipped = _auto_fire_offsets()[0], None, _auto_fire_offsets()[1]
    by_frame = {ev.frame: ev for ev in trace if ev.kind == "bullets"}
    assert len(by_frame) == 9
    for f in (0, 3, 6):  # fires[0] 触发(静态已覆盖)
        assert by_frame[f].origin == (0, covered)
    for f in (1, 4, 7):  # 运行时内部触发(模拟自动射击)
        assert by_frame[f].origin is auto_shoot
    for f in (2, 5, 8):  # fires[1] 触发(静态跳过)
        assert by_frame[f].origin == (0, skipped)


def test_auto_mode_dedup_and_blind_spot_fill() -> None:
    """AUTO: 静态已覆盖的事件不进补充段; origin None / 静态跳过的事件补回。"""
    tr = YoukaiDanmakuTranslator(FAKE_AUTO_GAME)
    out = tr.translate(_auto_data(), 0, mode=TranslateMode.AUTO)
    assert out["display"]["name"] == "氷符「テスト」"
    actions = out["phases"][out["entry_phase"]]["on_tick"]
    assert len(actions) == 3
    # 静态骨架: fires[0] → time 0 的裸 fire(count 8)
    static_fires = [a for a in actions if a["type"] == "fire_danmaku"]
    assert len(static_fires) == 1 and static_fires[0]["count"] == 8
    # 动态补充段: 两组折叠(tick_interval) —— 自动射击(count 4) + 静态跳过补回(count 2)
    folded = [a for a in actions if a.get("condition", {}).get("type") == "tick_interval"]
    assert len(folded) == 2
    assert sorted(f["if_true"][0]["count"] for f in folded) == [2, 4]
    # 去重: 静态已覆盖的 count-8 周期组(t 0/3/6)不进补充段
    assert all(f["if_true"][0]["count"] != 8 for f in folded)


class StubIrOnlyTranslator(EclTranslatorBase):
    """compile_ir 有、merge 无: AUTO 合并报错靶子。"""

    def compile(self, trace) -> dict:
        return {"events": len(trace)}

    def compile_ir(self, ir) -> dict:
        return {"static": True}


def test_auto_merge_not_implemented() -> None:
    """未实现 merge 的翻译器跑 AUTO: 中文 NotImplementedError。"""
    tr = StubIrOnlyTranslator(FAKE_IR_GAME)
    with pytest.raises(NotImplementedError, match="不支持 AUTO 模式"):
        tr.translate(_mk(_fire_ring(0)), 0, mode=TranslateMode.AUTO)


class StubBranchCardMachine(StubAutoMachine):
    """符卡宣言归属静态已覆盖指令的场景(难度分支运行时宣言的模拟)。

    静态 compile_ir 拿文本序第一条 BEGIN_SPELLCARD(氷符「テスト」), 运行时
    实际宣言另一张(反魂蝶 -八分咲-), 且该宣言的 origin 指向静态已覆盖指令
    —— 旧 AUTO 过滤会把这条事件丢掉, display 名错用静态首条。
    """

    def step(self) -> bool:
        if self._t == 0:
            self.executing_instr = self._fires[0]
            self.host.begin_spellcard(None, 9, 0, "反魂蝶 -八分咲-")
            self.executing_instr = None
        return super().step()


FAKE_BRANCH_GAME = "test04"  # 难度分支运行时宣言覆盖静态名
if FAKE_BRANCH_GAME not in registered_games():
    register_ecl(FAKE_BRANCH_GAME, file_format=EclFile)(StubBranchCardMachine)


def test_auto_display_name_prefers_runtime_declaration() -> None:
    """AUTO: display 名以运行时实际宣言为准(难度分支是静态盲区)。"""
    tr = YoukaiDanmakuTranslator(FAKE_BRANCH_GAME)
    out = tr.translate(_auto_data(), 0, mode=TranslateMode.AUTO)
    assert out["display"]["name"] == "反魂蝶 -八分咲-"
    assert out["custom_names"]["phase:main"] == "反魂蝶 -八分咲-"
