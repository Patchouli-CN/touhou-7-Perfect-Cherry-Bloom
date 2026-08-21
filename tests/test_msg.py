"""对话消息系统测试: msg 解析 / MsgVm 状态机 / 时间轴停轴 / 世界门控。

对照 Gui.cpp LoadMsg/MsgRead/RunMsg/MsgWait/HasCurrentMsgIdx 与
EnemyManager.cpp:332-336(时间轴 op8/9)。真实数据用例需要 th07.dat。
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, r"D:\python_play\Touhou08")

from touhou.games.th07.world import PerfectCherryBloom  # noqa: E402
from touhou.schema.msg import (  # noqa: E402
    MsgFile,
    MsgOpcode,
    MsgParseError,
    MsgVm,
)

DAT = Path(r"D:\TOUHOU_GAME\[th07] 东方妖妖梦 (日文版)\th07.dat")
NEEDS_DAT = pytest.mark.skipif(not DAT.exists(), reason="需要真实 th07.dat")

_OP = MsgOpcode


# ---- 手工构造 msg 二进制(不依赖 th07.dat) ----

def _instr(time: int, opcode: int, args: bytes = b"") -> bytes:
    return struct.pack("<HBB", time, opcode, len(args)) + args


def _dialogue(time: int, color: int, line: int, text: str) -> bytes:
    sjis = text.encode("shift_jis") + b"\x00"
    return _instr(time, _OP.DIALOGUE, struct.pack("<hh", color, line) + sjis)


def _build_msg(*messages: list[bytes]) -> bytes:
    """每条消息是 instr 字节串列表(需自带 DELETE)。"""
    num = len(messages)
    header = struct.pack("<i", num)
    offsets = b""
    body = b""
    base = 4 + 4 * num
    for m in messages:
        blob = b"".join(m)
        offsets += struct.pack("<i", base + len(body))
        body += blob
    return header + offsets + body


def _sample_msg() -> bytes:
    """msg0: 立绘进 → 两行对话 → PAUSE → 结束; msg1: 空消息直接 DELETE。"""
    msg0 = [
        _instr(0, _OP.SHOW_PORTRAIT, struct.pack("<hh", 0, 0)),
        _instr(0, _OP.SWITCH, struct.pack("<hB", 0, 1)),
        _dialogue(10, 0, 0, "さむ〜"),
        _instr(10, _OP.PAUSE, struct.pack("<i", 30)),
        _dialogue(20, 1, 0, "かい？"),
        _instr(20, _OP.PAUSE, struct.pack("<i", 30)),
        _instr(30, _OP.DELETE),
    ]
    msg1 = [_instr(0, _OP.DELETE)]
    return _build_msg(msg0, msg1)


# ---- 解析器(合成数据) ----

def test_parse_synthetic() -> None:
    f = MsgFile.parse(_sample_msg())
    assert f.num_messages == 2
    m0 = f.messages[0]
    assert m0[0].opcode == _OP.SHOW_PORTRAIT and m0[0].portrait == (0, 0)
    assert m0[1].switch == (0, 1)
    assert m0[2].dialogue == (0, 0, "さむ〜")
    assert m0[3].pause_duration == 30
    assert m0[-1].opcode == _OP.DELETE
    assert f.messages[1][0].opcode == _OP.DELETE


def test_parse_errors() -> None:
    with pytest.raises(MsgParseError):
        MsgFile.parse(b"\x01\x02")  # 太小
    with pytest.raises(MsgParseError):
        MsgFile.parse(struct.pack("<i", -3))  # 非法 numInstrs
    # 缺 DELETE: 指令区耗尽
    bad = struct.pack("<2i", 1, 8) + _instr(0, _OP.PAUSE, struct.pack("<i", 5))
    with pytest.raises(MsgParseError):
        MsgFile.parse(bad)


def test_shift_jis_bad_bytes_tolerated() -> None:
    blob = _build_msg([
        _instr(0, _OP.DIALOGUE, struct.pack("<hh", 0, 0) + b"\x82\xff\x82\x20\x00"),
        _instr(0, _OP.DELETE),
    ])
    f = MsgFile.parse(blob)
    text = f.messages[0][0].dialogue[2]
    assert isinstance(text, str) and text  # 坏字节容错替换, 不炸


# ---- MsgVm 状态机(合成数据) ----

def test_vm_pause_and_advance() -> None:
    vm = MsgVm(MsgFile.parse(_sample_msg()))
    vm.read(0)
    assert vm.active and vm.msg_wait()
    # 跑到 t=10 的 DIALOGUE+PAUSE(PAUSE 中 timer 钉住, frames_elapsed 走)
    while vm.timer < 10:
        vm.step()
    vm.step()  # 执行 DIALOGUE + PAUSE 第 1 帧
    assert vm.dialogue_lines[0].text == "さむ〜"
    assert vm.timer == 10 and vm.frames_elapsed_during_pause == 1
    # Z 提前结束: 停满 12 帧前按 Z 无效
    while vm.frames_elapsed_during_pause < 11:
        vm.step()
    vm.step(advance_pressed=True)   # 11<12 → 仍按住
    assert vm.timer == 10 and vm.frames_elapsed_during_pause == 12
    vm.step(advance_pressed=True)   # elapsed>=12 + Z 新按下 → 结束 PAUSE
    assert vm.timer == 11
    # 跑完整个消息
    frames = 0
    while vm.step(advance_pressed=(frames % 15 == 0)):
        frames += 1
        assert frames < 1000
    assert not vm.active and vm.current_msg_idx == -1


def test_vm_typewriter_reveal() -> None:
    vm = MsgVm(MsgFile.parse(_sample_msg()))
    vm.read(0)
    for _ in range(12):
        vm.step()
    line = vm.dialogue_lines[0]
    assert 0 < line.reveal < len(line.text)  # 打字机推进中
    for _ in range(20):
        vm.step()
    assert line.reveal == len(line.text)     # 全部显示
    assert line.shown_text == "さむ〜"


def test_vm_skip_held_fast_forward() -> None:
    vm = MsgVm(MsgFile.parse(_sample_msg()))
    vm.read(0)
    frames = 0
    while vm.step(skip_held=True):
        frames += 1
        assert frames < 100
    assert frames <= 31  # 30+ 帧的消息被 Ctrl 快进到指令时刻之和内


def test_vm_read_out_of_range_noop() -> None:
    vm = MsgVm(MsgFile.parse(_sample_msg()))
    vm.read(99)
    assert not vm.active and not vm.has_current_msg_idx()
    assert not vm.step()  # 非活动帧安全返回


def test_vm_switch_bright_dim_exit() -> None:
    """SWITCH interrupt: 3=说话方(亮), 4=非说话方(暗), 5=退场。"""
    blob = _build_msg([
        _instr(0, _OP.SHOW_PORTRAIT, struct.pack("<hh", 0, 0)),
        _instr(0, _OP.SHOW_PORTRAIT, struct.pack("<hh", 1, 0)),
        _instr(0, _OP.SWITCH, struct.pack("<hB", 0, 4)),   # 灵梦暗
        _instr(0, _OP.SWITCH, struct.pack("<hB", 1, 3)),   # Boss 亮
        _instr(1, _OP.SWITCH, struct.pack("<hB", 1, 5)),   # Boss 退场
        _instr(2, _OP.DELETE),
    ])
    vm = MsgVm(MsgFile.parse(blob))
    vm.read(0)
    vm.step()
    assert not vm.portraits[0].speaking and vm.portraits[1].speaking
    vm.step()
    assert vm.portraits[1].exited


# ---- 真实 msg1.dat ----

@NEEDS_DAT
def test_parse_msg1_real() -> None:
    from touhou.schema.archive import GameArchive
    a = GameArchive.open(DAT)
    f = MsgFile.parse(a.load("msg1.dat"))
    assert f.num_messages == 22
    m0 = f.messages[0]
    assert m0[-1].opcode == _OP.DELETE
    # 首句: 灵梦(色 0, 行 0) "さむ〜"
    dlg = [i for i in m0 if i.opcode == _OP.DIALOGUE]
    assert dlg[0].dialogue == (0, 0, "さむ〜")
    # Boss 名(TEXT_INTRODUCE): レティ・ホワイトロック
    intro = [i for i in m0 if i.opcode == _OP.TEXT_INTRODUCE]
    assert intro[1].dialogue[2].startswith("レティ・ホワイトロック")
    # 音乐指令 / ALLOW_SKIP / APPEAR_ENEMY 存在
    assert any(i.opcode == _OP.MUSIC and i.music_idx == 1 for i in m0)
    assert any(i.opcode == _OP.ALLOW_SKIP and i.allow_skip == 0 for i in m0)
    assert any(i.opcode == _OP.APPEAR_ENEMY for i in m0)
    # 魔理沙前置对话在 idx 10(character*10 偏移)
    m10_dlg = next(i for i in f.messages[10] if i.opcode == _OP.DIALOGUE)
    assert m10_dlg.dialogue[2].startswith("なんで")


@NEEDS_DAT
def test_vm_msg1_appear_enemy_window() -> None:
    """APPEAR_ENEMY: 当帧 MsgWait 放行(ignoreWaitCounter), 次帧恢复停轴。"""
    from touhou.schema.archive import GameArchive
    a = GameArchive.open(DAT)
    vm = MsgVm(MsgFile.parse(a.load("msg1.dat")))
    vm.read(0)
    released = 0
    for _ in range(4000):
        if not vm.step():
            break
        if not vm.msg_wait():
            released += 1
    assert released == 1  # 恰好一帧放行窗(t=62 的 APPEAR_ENEMY)
    assert vm.current_msg_idx == -1  # 读完 DELETE


@NEEDS_DAT
def test_vm_msg1_next_level_end() -> None:
    """msg1(过关结算): NEXT_LEVEL → currentMsgIdx=-2, HasCurrentMsgIdx 仍真,
    MsgWait 放行(时间轴不再停)。"""
    from touhou.schema.archive import GameArchive
    a = GameArchive.open(DAT)
    vm = MsgVm(MsgFile.parse(a.load("msg1.dat")))
    vm.read(1)
    frames = 0
    while vm.step(advance_pressed=(frames % 15 == 0)):
        frames += 1
        assert frames < 20000
    assert vm.current_msg_idx == -2
    assert vm.has_current_msg_idx()       # 世界仍门控(C: -2 也算)
    assert not vm.msg_wait()              # 但时间轴放行
    events = vm.take_events()
    assert "stage_results" in events and "next_level" in events


# ---- 接入: 时间轴停轴与世界门控(真实 ecldata1) ----

def _game() -> PerfectCherryBloom:
    return PerfectCherryBloom(data_path=DAT, character=0, difficulty=1)


def _ff_to_pre_boss(g: PerfectCherryBloom) -> None:
    """快进两条时间轴到尾王对话前(同 test_stage_ecl 的跳段手法)。"""
    for tl in g.ecl_timelines:
        target = 5040 if tl is g.ecl_timelines[1] else 4558
        while not tl.done and tl.timelines[tl.idx].time < target:
            tl.idx += 1
        tl.time = target


_STOP_KEYS = (False, False, False, False, False)


@NEEDS_DAT
def test_timeline_halt_and_resume_with_dialogue() -> None:
    """tl1 t=5042 op8 → MsgRead(0) 对话开始; t=5043/5044 op9 停轴
    (timeline time 钉住); Z 脉冲读完对话后时间轴恢复(过 5045 op10)。"""
    g = _game()
    _ff_to_pre_boss(g)
    start = None
    for f in range(200):
        g.tick(keys=_STOP_KEYS)
        if g.msg_vm is not None and g.msg_vm.active:
            start = f
            break
    assert start is not None, "对话未在快进后触发"
    assert g.msg_vm.current_msg_idx == 0  # ReimuA: arg0 + 0*10
    tl1 = g.ecl_timelines[1]
    # 不推对话: 时间轴钉死
    frozen_time = tl1.time
    for _ in range(50):
        g.tick(keys=_STOP_KEYS)
    assert tl1.time == frozen_time, "对话中时间轴未停住"
    assert g.msg_vm.active
    # Z 脉冲推进对话直到结束
    for f in range(1000):
        g.tick(keys=_STOP_KEYS, advance=(f % 15 == 0))
        if not g.msg_vm.active:
            break
    assert not g.msg_vm.active, "Z 脉冲 1000 帧内对话未读完"
    # 恢复: 再跑若干帧, 时间轴必须越过 op9/op10 (5045)
    for _ in range(30):
        g.tick(keys=_STOP_KEYS)
    assert tl1.time > 5045, "对话结束后时间轴未恢复"


@NEEDS_DAT
def test_dialogue_world_gating() -> None:
    """对话门控(HasCurrentMsgIdx): 玩家可移动但不能射击/炸弹;
    MsgRead 清场(敌人清空); 对话中每帧清道具; 结束后射击恢复。"""
    g = _game()
    _ff_to_pre_boss(g)
    for _ in range(200):
        g.tick(keys=_STOP_KEYS)
        if g.msg_vm.active:
            break
    assert g.msg_vm.active
    # MsgRead 清场: 非 boss 敌人清空(C: RemoveAllEnemies 跳过 boss)
    assert not any(e.alive and not e.is_boss for e in g.host.alive())
    # 移动照常, 射击门控(发射周期 30 帧滚动放完后不再重启; 已在飞的弹不回收,
    # 与 C 一致 —— Player.cpp:1616 门控的是 StartFireBulletTimer)
    x0 = g.player.pos.x
    for _ in range(40):
        g.tick(keys=(True, False, False, False, False))
    assert g.player.pos.x != x0, "对话中玩家不能移动"
    assert not g.player._firing, "对话中射击键未门控"
    assert g.player.fire_time == -1, "对话中发射周期未停摆"
    # 炸弹门控
    g.tick(keys=_STOP_KEYS, bomb=True)
    assert not g.bomb.is_in_use, "对话中炸弹未门控"
    # 道具每帧清(RunMsg: playerState != DEAD → RemoveAllItems)。
    # 先等 MsgRead 清场时的敌机死亡动画/掉落走完(死亡掉落在当帧后段产生,
    # 次帧被清), 再验证: 手动放的道具次帧即清, 且连续多帧无道具存活。
    for _ in range(120):
        g.tick(keys=_STOP_KEYS)
    g.items.spawn(g.player.pos, 1)  # ItemType.POINT
    for _ in range(5):
        g.tick(keys=_STOP_KEYS)
    assert len(g.items) == 0, "对话中道具未清"
    # 读完对话 → 射击恢复
    for f in range(1000):
        g.tick(keys=_STOP_KEYS, advance=(f % 15 == 0))
        if not g.msg_vm.active:
            break
    for _ in range(60):
        g.tick(keys=_STOP_KEYS)
    assert len(g.player.shots) > 0, "对话结束后射击未恢复"


@NEEDS_DAT
def test_msg_character_offset() -> None:
    """C: MsgRead(arg0 + character*10) —— 魔理沙(character 2/3 → 1) 读 msg10。"""
    g = PerfectCherryBloom(data_path=DAT, character=2, difficulty=1)
    _ff_to_pre_boss(g)
    for _ in range(200):
        g.tick(keys=_STOP_KEYS)
        if g.msg_vm.active:
            break
    assert g.msg_vm.current_msg_idx == 10
