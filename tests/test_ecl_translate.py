"""ECL 翻译基类(engine/translate)通用测试 —— stub VM + stub 格式注册假作品。

铁律: 通用层禁止 import games.*(AST 守护, 见 test_api.py); 这里用最小
stub(EclSpec.machine/file_format 鸭子类型)钉 record/translate 模板方法、
激光假句柄追踪与错误路径; 真 th07 回放见 game_test/th07/test_th07_ecl_translate.py。
"""

from __future__ import annotations

import pytest

from touhou.engine.ecl import EnemyBulletShooter, EnemyLaserShooter
from touhou.engine.translate import (
    EclTranslatorBase,
    YoukaiDanmakuTranslator,
    decode_spellcard_name,
)
from touhou.registry import register_ecl, registered_games

FAKE_ECL_GAME = "test01"


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
