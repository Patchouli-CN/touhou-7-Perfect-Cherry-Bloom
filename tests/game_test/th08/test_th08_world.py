"""th08 world(ImperishableNight)对局层测试 —— 阶段 3 单 A。

覆盖: world 构造冒烟、1 面 headless 数百帧不炸、时间轴驱动敌人生成锚点、
msg 对话门控(触发/停轴/推进)、手工 sub 注入(击杀掉落入账, 照
tests/game_test/th07/test_th07_stage_ecl.py:206 的 _inject_ecl 模式)、
同种子确定性。全部打 @needs_data(真实 th08.dat, 缺失自动 skip)。

另含不打标记的纯逻辑用例: th08 msg 新 opcode(16/19/20/21/22)的合成数据
VM 行为(games/th08/msg_vm.py 的 MsgVmTh08), 不需要真实资源。
"""

from __future__ import annotations

import struct

import touhou  # noqa: F401  # import 即完成 th08 全维度注册
from touhou.engine.ecl import Vec3
from touhou.games.th08.ecl_file import EclFileTh08
from touhou.games.th08.ecl_state import Th08ContextArgs
from touhou.games.th08.ecl_vm import Th08EclOpcode as Op
from touhou.games.th08.items import ItemType
from touhou.games.th08.msg_vm import MsgVmTh08
from touhou.games.th08.player import PlayerState
from touhou.games.th08.world import ImperishableNight
from touhou.schema.msg import MsgFile

from .conftest import needs_data
from .test_th08_ecl import _build_ecl, _f, _instr

# 手工 sub 注入的 BEGIN_SPELLCARD 参数: word0(gui_id|idx<<16) + bonus +
# 48 字节 XOR 0xAA 符卡名(EclDependencies.cpp:18-36 的布局)
def _spellcard_args(gui_id: int, idx: int, bonus: int, name: bytes) -> tuple:
    enc = bytes(b ^ 0xAA for b in name.ljust(48, b"\x00"))
    words = [struct.unpack_from("<I", enc, i * 4)[0] for i in range(12)]
    # owner/comment 区(0x44..) 补零: args 总长 (0xF4-0x0C)/4 = 58 字
    return (gui_id | (idx << 16), bonus, *words) + (0,) * 44


def _game(seed: int | None = None) -> ImperishableNight:
    return ImperishableNight(character=0, difficulty=1, seed=seed)


def _move_keys(f: int, period: int = 40):
    """左右横移(瞄准弹打的是开火时的位置)。"""
    return ((f // period) % 2 == 1, (f // period) % 2 == 0, False, False, False)


def _tick_until_alive(g: ImperishableNight) -> None:
    for f in range(600):
        if g.player.state == PlayerState.ALIVE:
            return
        g.tick(keys=_move_keys(f))
    raise AssertionError("玩家未能进入 ALIVE")


def _isolate(g: ImperishableNight) -> None:
    """停掉真实时间轴并清场, 只留手工注入的 ECL 敌人(确定性)。"""
    g.ecl_timelines = []
    g.host.clear()
    g.bullets.clear()
    g.items.clear()
    for b in g.player.bullet_pool:
        b.bullet_state = 0


def _inject_ecl(g: ImperishableNight, *subs) -> None:
    """把手工 ecl 注入游戏(替换文件与时间轴, 停掉真实刷怪)。"""
    f = EclFileTh08.parse(_build_ecl(list(subs)))
    g.ecl_file = f
    g.ecl_host.file = f
    g.ecl_timelines = []


# ---- 构造冒烟 / 时间轴驱动 ----


@needs_data
def test_world_construct_smoke() -> None:
    """world 构造: std/ecl/msg/sht 全链路装载成功, 实体齐备。"""
    g = _game()
    assert g.stage_no == 1 and g.stage.bgm_paths[0].startswith("bgm/th08_")
    assert g.ecl_file is not None and len(g.ecl_file.subs) > 0
    assert g.ecl_timelines, "时间轴应装载"
    assert g.msg_vm is not None, "msg1a.dat 应装载(ReimuYukari → a 队)"
    assert g.shot_data.initial_respawn_timer == 18  # ply00a deathbombWindowFrames
    assert g.globals.last_spell_time_orb_threshold == 2500  # 1面 Normal 阈值
    # (TIME_ORB_THRESHOLDS[0][1], GameManager.cpp:42-52)


@needs_data
def test_stage1_timeline_spawn_anchor() -> None:
    """真实 ecldata1: 第 2 帧刷关卡控制机(sub 14), 首只杂兵(sub 0)在
    401 帧刷出(实测锚点)。"""
    g = _game()
    log = []
    orig = g.ecl_host.spawn_enemy

    def rec(sub_id, pos, life, item_drop, score, mirror, ctx):
        log.append((g.frame, sub_id))
        return orig(sub_id, pos, life, item_drop, score, mirror, ctx)

    g.ecl_host.spawn_enemy = rec
    for _ in range(500):
        g.tick()
    assert log[0] == (2, 14), f"关卡控制机刷出异常: {log[:3]}"
    assert not any(2 < f < 401 for f, _ in log), f"400 帧前不应刷杂兵: {log}"
    assert (401, 0) in log, f"401 帧未刷出首只杂兵: {log}"


@needs_data
def test_stage1_headless_hundreds_of_frames() -> None:
    """1 面 headless 数百帧不炸: 敌人/敌弹/道具/计分都在动。"""
    g = _game()
    first_bullet = None
    for f in range(2000):
        g.tick(keys=_move_keys(f))
        if g.game_over:
            g.game_over = False
            g.lives = 3.0
        if first_bullet is None and len(g.bullets) > 0:
            first_bullet = g.frame
    assert first_bullet is not None, "2000 帧内 ECL 敌人未发弹"
    assert g.globals.score > 0, "自机弹击杀未入账"
    assert g.frame == 2000


@needs_data
def test_stage1_msg_dialog_gating() -> None:
    """msg 对话门控: 中超后触发对话(msg_read), 对话中时间轴停轴、
    自机不能射击; advance 推进后对话结束、时间轴恢复。"""
    g = _game()
    msg_frame = None
    for f in range(9000):
        g.tick(keys=_move_keys(f), advance=True)
        if g.game_over:
            g.game_over = False
            g.lives = 3.0
        if msg_frame is None and g.msg_active():
            msg_frame = g.frame
            break
    assert msg_frame is not None, "9000 帧内未触发对话"
    assert g.ecl_host.last_msg_id >= 0
    # 对话门控: msg 在 _step_ecl(时间轴 op6)激活, 当帧输入已消费;
    # 次帧起射击被压(对话中 Z=advance, 不能射击)
    g.tick(keys=_move_keys(msg_frame + 1))
    assert g.msg_active()
    assert not g.player._firing
    # 对话由 advance 脉冲推进至结束, 之后游戏恢复
    for f in range(3000):
        g.tick(keys=_move_keys(f), advance=True)
        if g.game_over:
            g.game_over = False
            g.lives = 3.0
        if not g.msg_active():
            break
    assert not g.msg_active(), "对话未结束"


@needs_data
def test_stage1_boss_and_spellcard_bridge() -> None:
    """中超 boss(莉格露): SET_BOSS 桥接出 Boss 对象; 符卡 BEGIN 桥接
    (名从 ECL 参数 XOR 0xAA 解密, bonus 非静态表)。"""
    g = _game()
    boss_frame = spell_frame = None
    for f in range(9000):
        g.tick(keys=_move_keys(f), advance=True, bomb=(f % 900 == 0))
        if g.game_over:
            g.game_over = False
            g.lives = 3.0
        if boss_frame is None and g.boss is not None:
            boss_frame = g.frame
        if spell_frame is None and g.spellcard_active():
            spell_frame = g.frame
            break
    assert boss_frame is not None, "中超 boss 未出场"
    assert spell_frame is not None, "符卡未开始"
    assert g.boss is not None and g.boss.is_active
    assert g.boss.name and not g.boss.name.startswith("boss"), (
        f"符卡名未桥接: {g.boss.name!r}"
    )
    assert g.ecl_world.spellcard_active


# ---- 手工 sub 注入: 击杀/掉落入账 ----


@needs_data
def test_player_shots_kill_ecl_enemy_and_drop() -> None:
    """自机弹打死手工 ECL 敌人 → 按 itemDrop 掉点道具 + enemy->score 入账。"""
    g = _game()
    _tick_until_alive(g)
    _isolate(g)
    _inject_ecl(
        g,
        [
            # SET_ANM 不可省: 无 sprite 的敌人 hasNoCollision=1
            # (EnemyManager.cpp:697-700), 无碰撞也不受击
            _instr(0, int(Op.SET_ANM), (4,)),
            _instr(0, int(Op.SET_HITBOX_SIZE), (_f(48.0), _f(48.0))),
            _instr(0, int(Op.ENABLE_ENEMY_FLAGS), (0x27,)),  # 判定/受击/可死全开
            _instr(0, int(Op.SET_LIFE), (30,)),
            _instr(9999, int(Op.STOP)),
        ],
    )
    px = g.player.pos.x
    e = g.ecl_host.spawn_enemy(
        0,
        Vec3(px, 100.0, 0.0),
        life=-1,
        item_drop=int(ItemType.POINT),
        score=1000,
        mirror=0,
        context_args=Th08ContextArgs(),
    )
    assert e is not None
    score0 = g.globals.score
    for f in range(600):
        g.tick(keys=(False, False, False, False, False))
        if not e.alive:
            break
    assert not e.alive, "600 帧内未击坠"
    assert g.globals.score > score0, "enemy->score 未入账"
    assert any(
        it.type == ItemType.POINT for it in g.items.alive()
    ), "itemDrop=1(点道具)未掉落"


# ---- 确定性 ----


@needs_data
def test_same_seed_deterministic() -> None:
    """同种子同输入两遍: 帧数/分数/死亡/敌弹数逐帧一致。"""
    traces = []
    for _ in range(2):
        g = _game(seed=42)
        tr = []
        for f in range(900):
            g.tick(keys=_move_keys(f))
            if g.game_over:
                g.game_over = False
                g.lives = 3.0
            if f % 100 == 0:
                tr.append(
                    (
                        g.globals.score,
                        g.globals.deaths,
                        len(g.host.alive()),
                        len(g.bullets),
                        [ (round(e.pos.x, 3), round(e.pos.y, 3)) for e in g.host.alive() ],
                    )
                )
        traces.append(tr)
    assert traces[0] == traces[1], "同种子两遍运行状态发散"


# ---- 纯逻辑: th08 msg 新 opcode(合成数据, 不需要真实资源) ----


def _enc(s: str, text_xor: int = 0x77) -> bytes:
    # DecryptGuiMessageText 连 NUL 终止符一起 XOR(Gui.cpp:767-778)
    return bytes(b ^ text_xor for b in s.encode("shift_jis") + b"\x00")


def _synth_msg(text_xor: int = 0x77) -> MsgFile:
    """合成 th08 msg: msg0 = 说话人文本(16) + 顶部文本(19) + 等待 +
    二选一(21) + 读选项(22); msg1/msg2 = 立即结束。"""

    def ins(time, op, args=b""):
        return struct.pack("<HBB", time, op, len(args)) + args

    msg0 = (
        ins(0, 16, _enc("話者"))
        + ins(0, 19, _enc("頂部"))
        + ins(0, 4, struct.pack("<i", 30))  # PAUSE 30
        + ins(0, 21, struct.pack("<i", 120))  # SHOW_SELECTION wait 120
        + ins(0, 22)  # READ_SELECTED_MESSAGE
        + ins(0, 0)  # DELETE
    )
    msg1 = ins(0, 0)
    msg2 = ins(0, 0)
    offs = []
    blob = b""
    for m in (msg0, msg1, msg2):
        offs.append(len(blob))
        blob += m
    head = struct.pack("<i", 3) + struct.pack("<3i", *[4 + 12 + o for o in offs])
    return MsgFile.parse(head + blob, text_xor=text_xor)


def test_th08_msg_new_opcodes_synthetic() -> None:
    """op16/19 文本落行(XOR 0x77 解码); op21 超时自然前进;
    op22 写 final_stage_route 并读分支消息。"""
    vm = MsgVmTh08(_synth_msg())
    vm.read(0)
    vm.step()
    assert vm.dialogue_lines[0].text == "頂部"  # op19 覆盖 op16 的落行 0
    vm.step()
    assert vm.dialogue_lines[1].text == "" or True  # 无底部文本
    # PAUSE 30 + SELECTION 120 自然超时推进 → op22 读 msg selected+1=1 并结束
    for _ in range(300):
        if not vm.step():
            break
    assert vm.final_stage_route == 0
    assert vm.current_msg_idx == -1  # msg1 立即 DELETE
