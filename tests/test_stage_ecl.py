"""ECL 接入游戏循环的整合测试: 真实 ecldata1 时间轴驱动 + 手工构造 sub 的桥接用例。

覆盖: 时间轴刷怪锚点(第 600 帧)、ECL 敌人发弹进 BulletWorld、自机弹击杀
ECL 敌人与道具掉落、Boss/符卡桥接(begin/end/超时)、确定性(同种子两遍一致)。
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, r"D:\python_play\Touhou08")

from touhou.games.th07.world import PerfectCherryBloom  # noqa: E402
from touhou.games.th07.bomb import BorderState  # noqa: E402
from touhou.engine.bullets import Bullet, Vec2  # noqa: E402
from touhou.engine.ecl import EclContextArgs, EclOpcode, Vec3  # noqa: E402
from touhou.engine.enemies import EclEnemy  # noqa: E402
from touhou.games.th07.items import ItemType  # noqa: E402
from touhou.games.th07.player import PlayerState  # noqa: E402
from tests.test_ecl import _f, _instr, build_ecl  # noqa: E402

DAT = Path(r"D:\TOUHOU_GAME\[th07] 东方妖妖梦 (日文版)\th07.dat")
NEEDS_DAT = pytest.mark.skipif(not DAT.exists(), reason="需要真实 th07.dat")

OP = EclOpcode
_STOP_KEYS = (False, False, False, False, False)


def _game() -> PerfectCherryBloom:
    return PerfectCherryBloom(data_path=DAT, character=0, difficulty=1)


def _move_keys(f: int, period: int = 40):
    """左右横移躲弹(瞄准弹打的是开火时的位置)。"""
    return ((f // period) % 2 == 1, (f // period) % 2 == 0, False, False, False)


def _tick_until_alive(g: PerfectCherryBloom) -> None:
    for f in range(600):
        if g.player.state == PlayerState.ALIVE:
            return
        g.tick(keys=_move_keys(f))
    raise AssertionError("玩家未能进入 ALIVE")


def _isolate(g: PerfectCherryBloom) -> None:
    """停掉真实时间轴并清场, 只留手工注入的 ECL 敌人(确定性)。"""
    g.ecl_timelines = []
    g.host.clear()
    g.bullets.clear()
    g.items.clear()
    for b in g.player.bullet_pool:
        b.bullet_state = 0
    for ts in g.player.timers:
        ts.timer = 0
        ts.bullet = None


def _spellcard_word0_and_name(gui_id: int, idx: int, name: bytes) -> tuple:
    """BEGIN_SPELLCARD 参数: word0 + 48 字节 XOR 0xaa 的符卡名。"""
    enc = bytes(b ^ 0xAA for b in name.ljust(48, b"\x00"))
    words = [struct.unpack_from("<I", enc, i * 4)[0] for i in range(12)]
    return (gui_id | (idx << 16), *words)


# ---- 真实数据: 时间轴驱动 ----

@NEEDS_DAT
def test_stage1_timeline_spawn_anchor() -> None:
    """真实 ecldata1: timeline1 在 frame 2 刷出关卡控制机(sub 1),
    timeline0 的首只杂兵在第 601 帧刷出(timeline time 600, op=2, sub 3, mirror=1)。"""
    g = _game()
    log = []
    orig_spawn = g.ecl_host.spawn_enemy

    def recording(sub_id, pos, life, item_drop, score, mirror, ctx):
        log.append((g.frame, sub_id, mirror))
        return orig_spawn(sub_id, pos, life, item_drop, score, mirror, ctx)

    g.ecl_host.spawn_enemy = recording
    for _ in range(610):
        g.tick()
    assert log[0] == (2, 1, 0), f"关卡控制机刷出异常: {log[:3]}"
    assert all(f <= 2 for f, sub, _ in log if sub == 1)
    assert not any(2 < f <= 600 for f, _, _ in log), f"600 帧前不应刷杂兵: {log}"
    assert (601, 3, 1) in log, f"601 帧未刷出首只杂兵: {log}"


@NEEDS_DAT
def test_stage1_ecl_enemy_fires_bullets() -> None:
    """ECL 敌人经 spawn_bullet_pattern 把弹打进 BulletWorld(全场唯一弹源)。
    (Normal 难度首弹在 ~1731 帧; 之前的杂兵弹幕指令带 0x8=Lunatic 难度掩码)"""
    g = _game()
    first = None
    for f in range(1800):
        g.tick(keys=_move_keys(f))
        if g.game_over:
            g.game_over = False
            g.lives = 3.0
        if len(g.bullets) > 0 and first is None:
            first = g.frame
            break
    assert first is not None, "1800 帧内 ECL 敌人未发弹"
    assert g.globals.score > 0


@NEEDS_DAT
def test_stage1_midboss_bridged() -> None:
    """中超 Boss(琪露诺): ~2668 帧 SET_BOSS 桥接出 Boss 对象, 状态逐帧同步。"""
    g = _game()
    for f in range(3400):
        g.tick(keys=_move_keys(f))
        if g.game_over:
            g.game_over = False
            g.lives = 3.0
        if g.boss is not None:
            break
    assert g.boss is not None, "中超 Boss 未桥接"
    st = g._boss_ecl_state
    assert st is not None and st.is_boss
    assert g.boss.pos.x == pytest.approx(st.pos.x)
    assert g.boss.pos.y == pytest.approx(st.pos.y)
    assert g.boss.life == max(st.life, 0)


@NEEDS_DAT
def test_stage4_healthbar_tracks_boss_slot0() -> None:
    """4 面三姐妹: 血条数据源必须是 bossId==0 主体, 不是最后 SET_BOSS 的卫星机。

    主体 sub42 SET_BOSS(0) 并 spawn 三姐妹卫星机(槽 1..3, SET_LIFE 999999,
    承伤经 GET_BOSS_INT(LAST_DAMAGE) 转嫁主体); C++ 血条每帧只取 bossId==0
    敌人的 life/maxLife (EnemyManager.cpp:1066-1068)。移植曾把 HUD 绑到最后
    一个 SET_BOSS 的卫星机 → 血条恒满 (BUGS.md 增量#1)。"""
    g = _game()
    g.enter_stage(4)
    for f in range(600):
        if g.player.state == PlayerState.ALIVE:
            break
        g.tick(keys=_move_keys(f))
    g.ecl_timelines = []
    e = g.ecl_host.spawn_enemy(42, Vec3(192.0, 100.0, 0.0), life=-1, item_drop=0,
                               score=1000, mirror=0, context_args=EclContextArgs())
    assert e is not None
    for f in range(30):
        g.tick(keys=_move_keys(f))
    st0 = g.ecl_world.bosses[0]
    assert st0 is not None and st0.boss_id == 0
    # 绑定落在卫星机(999999)上时, 血条也必须显示主体血量
    assert g.boss is not None
    assert g.boss.max_life == max(st0.max_life, 1)
    assert g.boss.life == max(st0.life, 0)
    st0.life -= 5000  # 模拟 ECL 伤害归集(主体掉血)
    g.tick(keys=_move_keys(0))
    assert g.boss.life == st0.life


@NEEDS_DAT
def test_stage1_letty_spellcard_bridge() -> None:
    """尾王(蕾蒂): 快进到 timeline1 的 5040, 验证真实 sub 的 begin_spellcard
    桥接(is_active/is_capturing 置位, 符卡名解密)。

    5042 起时间轴 op8/9 触发蕾蒂前置对话(msg1.dat msg0)并停轴;
    这里每 15 帧脉冲一次 Z(advance) 把 PAUSE 顶过去(原版玩家按 Z 推进),
    对话结束后 op10 的 runInterrupt 才把 Boss 切进符卡。"""
    g = _game()
    # 快进两条时间轴到尾王前(跳过中段刷怪与中超, 只测桥接)
    for tl in g.ecl_timelines:
        target = 5040 if tl is g.ecl_timelines[1] else 4558
        while not tl.done and tl.timelines[tl.idx].time < target:
            tl.idx += 1
        tl.time = target
    boss_frame = None
    for f in range(3000):
        g.tick(keys=_move_keys(f), advance=(f % 15 == 0))
        if g.game_over:
            g.game_over = False
            g.lives = 3.0
        if g.boss is not None and boss_frame is None:
            boss_frame = g.frame
        if g.boss is not None and g.boss.is_active and g.boss.spellcard_idx >= 0:
            break
    assert boss_frame is not None, "蕾蒂未出场"
    assert g.boss is not None and g.boss.is_active, "符卡未开始"
    assert g.boss.is_capturing or g.globals.spell_cards_captured > 0
    assert g.boss.name and not g.boss.name.startswith("boss"), \
        f"符卡名未桥接: {g.boss.name!r}"
    assert g.ecl_world.spellcard_active


# ---- 手工构造 sub: 击杀/掉落与符卡桥接 ----

def _inject_ecl(g: PerfectCherryBloom, *subs) -> None:
    """把手工 ecl 注入游戏(替换文件与时间轴, 停掉真实刷怪)。"""
    f = build_ecl(*subs)
    g.ecl_file = f
    g.ecl_host.file = f
    g.ecl_timelines = []


@NEEDS_DAT
def test_player_shots_kill_ecl_enemy_and_drop() -> None:
    """自机弹打死手工 ECL 敌人 → 按 itemDrop 掉点道具 + enemy->score 入账。"""
    g = _game()
    _tick_until_alive(g)
    _isolate(g)
    _inject_ecl(g, [
        # SET_ANM 不可省: C++ 无 sprite 的敌人 hasNoCollision=1
        # (EnemyManager.cpp:697-700), 无碰撞也不受击
        _instr(0, OP.SET_ANM, (4,)),
        _instr(0, OP.SET_HITBOX_SIZE, (_f(48.0), _f(48.0), _f(48.0))),
        _instr(0, OP.SET_LIFE, (30,)),
        _instr(9999, OP.UNIMP),
    ])
    px = g.player.pos.x
    e = g.ecl_host.spawn_enemy(0, Vec3(px, 100.0, 0.0), life=-1, item_drop=1,
                               score=1000, mirror=0, context_args=EclContextArgs())
    assert isinstance(e, EclEnemy) and e.alive
    assert e.life == 30 and e.radius == 24.0  # hitbox 48 → 半径 24
    score0 = g.globals.score
    for _ in range(300):
        g.tick(keys=_STOP_KEYS)
        if not e.alive:
            break
    assert not e.alive, "自机弹未击杀 ECL 敌人"
    assert g.globals.score >= score0 + 100, \
        "enemy->score 未入账(代码值 1000, AddScore 入账 //10)"
    assert any(it.type == ItemType.POINT for it in g.items.alive()), \
        "itemDrop=1(点道具)未掉落"


@NEEDS_DAT
def test_spellcard_begin_and_timeout_bridge() -> None:
    """手工 Boss sub: BEGIN_SPELLCARD 桥接(名解密/is_active/is_capturing);
    120 帧超时 → ECL timer callback → 捕获失败(is_active=2) + 清弹 + 樱罚
    → 超时 sub 里 END_SPELLCARD → 不计捕获, Boss 兜底清理。"""
    g = _game()
    _tick_until_alive(g)
    _isolate(g)
    _inject_ecl(
        g,
        [  # sub0: boss 本体
            _instr(0, OP.SET_BOSS, (0,)),
            _instr(0, OP.SET_HITBOX_SIZE, (_f(48.0), _f(48.0), _f(48.0))),
            _instr(0, OP.SET_LIFE, (100000,)),  # 打不动, 只能超时
            _instr(0, OP.SET_TIMER_CALLBACK_THRESHOLD, (120,)),
            _instr(0, OP.SET_TIMER_CALLBACK_SUB, (1,)),
            _instr(0, OP.BEGIN_SPELLCARD,
                   _spellcard_word0_and_name(1, 5, "TestCard".encode())),
            _instr(9999, OP.UNIMP),
        ],
        [  # sub1: 超时回调: 结束符卡后退场
            _instr(0, OP.END_SPELLCARD),
            _instr(0, OP.UNIMP),
        ],
    )
    e = g.ecl_host.spawn_enemy(0, Vec3(192.0, 120.0, 0.0), life=-1, item_drop=-1,
                               score=1000, mirror=0, context_args=EclContextArgs())
    assert g.boss is not None, "SET_BOSS 未桥接"
    assert g.boss.spellcard_idx == 5 and g.boss.is_active == 1
    assert g.boss.is_capturing and g.boss.name == "TestCard"
    assert g.ecl_world.spellcard_active
    # 摆几发弹 + 攒樱点, 验证超时清弹与樱罚
    g.globals.add_cherry(40000)
    g.tick(keys=_STOP_KEYS)
    for _ in range(200):
        g.tick(keys=_STOP_KEYS)
        if g.boss is None:
            break
    assert e.state.timer_callback_threshold == -1, "超时回调未触发"
    assert g.globals.spell_cards_captured == 0, "超时不算捕获"
    assert g.cherry < 40000, "超时樱点惩罚未生效"
    assert len(g.bullets) == 0, "超时未清弹"
    assert g.boss is None, "Boss 退场后未清理桥接对象"
    assert not g.ecl_world.spellcard_active


@NEEDS_DAT
def test_spellcard_capture_by_damage_bridge() -> None:
    """击破符卡(打空血): 敌人消亡且 ECL 未自己 END_SPELLCARD 时由
    _tick_boss_ecl 兜底结束 —— is_capturing 仍成立 → 计捕获 + 捕获得分。"""
    g = _game()
    _tick_until_alive(g)
    _isolate(g)
    _inject_ecl(g, [
        _instr(0, OP.SET_BOSS, (0,)),
        _instr(0, OP.SET_ANM, (4,)),  # 无 sprite → hasNoCollision (见上个用例)
        _instr(0, OP.SET_HITBOX_SIZE, (_f(48.0), _f(48.0), _f(48.0))),
        _instr(0, OP.SET_LIFE, (30,)),
        _instr(0, OP.BEGIN_SPELLCARD,
               _spellcard_word0_and_name(2, 7, "CapCard".encode())),
        _instr(9999, OP.UNIMP),
    ])
    px = g.player.pos.x
    g.ecl_host.spawn_enemy(0, Vec3(px, 120.0, 0.0), life=-1, item_drop=-1,
                           score=1000, mirror=0, context_args=EclContextArgs())
    assert g.boss is not None and g.boss.spellcard_idx == 7
    score0 = g.globals.score
    for _ in range(300):
        g.tick(keys=_STOP_KEYS)
        if g.globals.spell_cards_captured:
            break
    assert g.globals.spell_cards_captured == 1, "击破符卡未计捕获"
    assert g.globals.score > score0, "捕获得分未入账"
    assert g.boss is None, "Boss 消亡后未清理"


@NEEDS_DAT
def test_spellcard_begin_clears_bullets_to_items() -> None:
    """BEGIN_SPELLCARD 瞬间全屏弹转弹消点道具 (EclManager.cpp:673
    RemoveAllBullets(1), 走 ecl_host.remove_all_bullets 清弹路径)。"""
    g = _game()
    _tick_until_alive(g)
    _isolate(g)
    _inject_ecl(g, [
        _instr(0, OP.SET_BOSS, (0,)),
        _instr(0, OP.SET_HITBOX_SIZE, (_f(48.0), _f(48.0), _f(48.0))),
        _instr(0, OP.SET_LIFE, (100000,)),
        _instr(0, OP.BEGIN_SPELLCARD,
               _spellcard_word0_and_name(1, 5, "ClearCard".encode())),
        _instr(9999, OP.UNIMP),
    ])
    # 宣告前先摆几发敌弹: BEGIN_SPELLCARD 在 spawn 当帧同步执行
    for i in range(3):
        g.bullets._bullets.append(
            Bullet(Vec2(100.0 + i * 40.0, 200.0), 0.0, 2.0, sprite=0))
    assert len(list(g.bullets.alive())) == 3
    g.ecl_host.spawn_enemy(0, Vec3(192.0, 120.0, 0.0), life=-1, item_drop=-1,
                           score=1000, mirror=0, context_args=EclContextArgs())
    assert g.boss is not None and g.boss.spellcard_idx == 5
    assert all(b.dead for b in g.bullets.alive()), "宣告未击杀场上弹"
    points = [it for it in g.items.alive() if it.type == ItemType.POINT_BULLET]
    assert len(points) == 3, f"弹未转成弹消点道具: {len(points)}"
    g.tick(keys=_STOP_KEYS)  # 死弹在下一次 step 出列
    assert len(list(g.bullets.alive())) == 0, "宣告后场上弹未清空"


@NEEDS_DAT
def test_get_boss_int_reads_boss_context_args() -> None:
    """GET_BOSS_INT/FLOAT 的本地变量取自 boss 当前上下文的 args
    (EclManager.cpp:998-1007 → GetVarValue(boss, ...), :116+ 读
    boss->currentContext.eclContextArgs)。

    回归: 旧实现 _peer_int/_peer_float 只换 enemy 不换 args, 本地变量读到
    的是调用方自己的值 → 3 面爱丽丝人偶 sub43 轮询 boss LOCAL_INT2_3
    (10014) 恒得 0, sub44(ExIns 6 分裂弹)永远进不去。
    """
    g = _game()
    _tick_until_alive(g)
    _isolate(g)
    _inject_ecl(
        g,
        [  # sub0: boss 本体, 本地变量 LOCAL_INT2_3(10014)=42 / LOCAL_FLOAT1_1(10004)=2.5
            # (float 变量 id 按 f32 值存储, 见 ecl.py _float_target 注释)
            _instr(0, OP.SET_BOSS, (0,)),
            _instr(0, OP.SET_INT, (10014, 42), mask=0x1),
            _instr(0, OP.SET_FLOAT, (_f(10004.0), _f(2.5)), mask=0x1),
            _instr(9999, OP.UNIMP),
        ],
        [  # sub1: 轮询者: bosses[0] 的同名本地变量拷到自己上下文
            _instr(0, OP.GET_BOSS_INT, (10014, 10014, 0), mask=0x3),
            _instr(0, OP.GET_BOSS_FLOAT, (_f(10004.0), _f(10004.0), 0), mask=0x3),
            _instr(9999, OP.UNIMP),
        ],
    )
    g.ecl_host.spawn_enemy(0, Vec3(192.0, 100.0, 0.0), life=-1, item_drop=-1,
                           score=100, mirror=0, context_args=EclContextArgs())
    doll = g.ecl_host.spawn_enemy(1, Vec3(96.0, 200.0, 0.0), life=-1, item_drop=-1,
                                  score=100, mirror=0, context_args=EclContextArgs())
    assert doll is not None
    # spawn 当帧已跑过 t=0: 读到的是 boss 上下文的值, 不是自己的(0)
    assert doll.machine._get_int(10014) == 42
    assert doll.machine._get_float(10004) == pytest.approx(2.5)


# ---- 确定性 ----

@NEEDS_DAT
def test_determinism_same_seed() -> None:
    """同一 rng 种子跑两遍 1200 帧: 分数/樱点/弹数/敌机位置逐值一致。"""

    def run() -> tuple:
        g = _game()
        for f in range(1200):
            g.tick(keys=_move_keys(f))
            if g.game_over:
                g.game_over = False
                g.lives = 3.0
        positions = sorted(
            (round(e.pos.x, 4), round(e.pos.y, 4)) for e in g.host.all())
        return (g.globals.score, g.globals.cherry, g.globals.gui_score,
                len(g.bullets), len(g.items), positions,
                round(g.player.pos.x, 4), round(g.player.pos.y, 4))

    assert run() == run()


# ---- 体术判定集成 (EnemyManager.cpp:754-775 → Player::CalcKillboxCollision) ----

def _inject_rammer(g: PerfectCherryBloom):
    """注入一个钉在玩家位置的 ECL 敌人(SET_ANM 不可省: 无 sprite 即无碰撞)。"""
    _inject_ecl(g, [
        _instr(0, OP.SET_ANM, (4,)),
        _instr(0, OP.SET_HITBOX_SIZE, (_f(48.0), _f(48.0), _f(48.0))),
        _instr(0, OP.SET_LIFE, (10000,)),
        _instr(9999, OP.UNIMP),
    ])
    return g.ecl_host.spawn_enemy(0, Vec3(g.player.pos.x, g.player.pos.y, 0.0),
                                  life=-1, item_drop=-1, score=1000, mirror=0,
                                  context_args=EclContextArgs())


@NEEDS_DAT
def test_ecl_enemy_contact_kills_player() -> None:
    """ECL 敌人本体撞玩家: ALIVE → die → 重生扣残机 (与子弹命中同路径)。"""
    g = _game()
    _tick_until_alive(g)
    _isolate(g)
    e = _inject_rammer(g)
    assert e is not None and e.alive
    lives0 = g.lives
    g.tick(keys=_STOP_KEYS)
    assert g.player.state == PlayerState.DEAD, "体术命中未致死"
    assert g._death_pos is not None
    for _ in range(60):
        g.tick(keys=_STOP_KEYS)
        if g.player.state == PlayerState.INVULNERABLE:
            break
    assert g.lives == lives0 - 1, "重生未扣残机"


@NEEDS_DAT
def test_ecl_enemy_contact_breaks_border() -> None:
    """BORDER 中被撞 → 结界破保命(不死), 与子弹命中同一路径。"""
    g = _game()
    _tick_until_alive(g)
    _isolate(g)
    g.border.ready_border()
    g.tick(keys=_STOP_KEYS)  # READY + ALIVE → 激活, player.state=BORDER
    assert g.player.state == PlayerState.BORDER
    e = _inject_rammer(g)
    g.tick(keys=_STOP_KEYS)
    assert g.player.state != PlayerState.DEAD, "结界破不应致死"
    assert g.border.has_border == BorderState.NONE, "结界未破"
    assert e.state.life < 10000, "撞结界敌人未扣血(C++:593 life-=10)"


@NEEDS_DAT
def test_ecl_enemy_no_collision_flag_exempts_contact() -> None:
    """ECL SET_HAS_NO_COLLISION(132) → 体术豁免 (C++:754 门槛)。"""
    g = _game()
    _tick_until_alive(g)
    _isolate(g)
    _inject_ecl(g, [
        _instr(0, OP.SET_ANM, (4,)),
        _instr(0, OP.SET_HITBOX_SIZE, (_f(48.0), _f(48.0), _f(48.0))),
        _instr(0, OP.SET_LIFE, (10000,)),
        _instr(0, OP.SET_HAS_NO_COLLISION, (1,)),
        _instr(9999, OP.UNIMP),
    ])
    e = g.ecl_host.spawn_enemy(0, Vec3(g.player.pos.x, g.player.pos.y, 0.0),
                               life=-1, item_drop=-1, score=1000, mirror=0,
                               context_args=EclContextArgs())
    assert e is not None
    for _ in range(10):
        g.tick(keys=_STOP_KEYS)
    assert g.player.state == PlayerState.ALIVE, "hasNoCollision 敌人不应撞死玩家"
