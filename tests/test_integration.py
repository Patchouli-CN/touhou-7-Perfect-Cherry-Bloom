"""整合冒烟测试: PerfectCherryBloom 最小可玩闭环(真实 th07 数据, 确定性)。

覆盖环节: 计分/guiScore 追赶、击杀掉道具、道具收集(点/P/樱)、擦弹计数、
Boss 出场与符卡 begin/end(捕获+超时)、阶段切换、炸弹扣樱/清弹转道具、
死亡重生(扣残机/掉 P/樱罚)、满樱结界(激活/中弹破保命)。

注意: 瞄准弹是瞄玩家当前位置的, 站桩必死; 需要存活的段落用横移躲弹,
需要必中的段落(死亡/结界破)停下让弹打中。
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

# 让 `pytest` 从项目根 import 到 Touhou
sys.path.insert(0, r"D:\python_play\Touhou08")

from touhou.games.th07.world import PerfectCherryBloom  # noqa: E402
from touhou.engine.bullets import Aim, Burst  # noqa: E402
from touhou.games.th07.globals import STATUS_FULL_POWER  # noqa: E402
from touhou.games.th07.items import (  # noqa: E402
    FULL_POWER,
    STATE_ATTRACT,
    STATE_FALL,
    ItemType,
)
from touhou.games.th07.player import PlayerState  # noqa: E402
from touhou.utils import Vec2  # noqa: E402

DAT = Path(r"D:\TOUHOU_GAME\[th07] 东方妖妖梦 (日文版)\th07.dat")


def _game() -> PerfectCherryBloom:
    return PerfectCherryBloom(data_path=DAT, character=0, difficulty=1)


def _move_keys(f: int, period: int = 24):
    """左右横移躲弹(瞄准弹打的是开火时的位置)。"""
    return ((f // period) % 2 == 1, (f // period) % 2 == 0, False, False, False)


# 站桩(keys 不传时 _move 保持上次的值, 必须显式推 0 才能停)
_STOP_KEYS = (False, False, False, False, False)


def _tick_until_alive(g: PerfectCherryBloom) -> None:
    """横移跑过出生 SPAWNING/INVULNERABLE, 直到首次稳定 ALIVE。"""
    for f in range(600):
        if g.player.state == PlayerState.ALIVE:
            return
        g.tick(keys=_move_keys(f))
    raise AssertionError("玩家未能进入 ALIVE")


def _bullet_at(pos: Vec2, angle: float = math.pi / 2, speed: float = 2.0) -> Burst:
    return Burst(pos, angle, Aim.SPREAD_ABSOLUTE, 1, 1, speed, speed, 0.0)


def test_score_gui_and_boss_spellcard_begin() -> None:
    """3400 帧主流程: 分数/gui 分增长; ECL 时间轴驱动的中超 Boss 桥接出场
    (真实 ecldata1: 中超 Boss ~2668 帧 SET_BOSS, 无符卡 —— 符卡桥接见
    test_stage_ecl.py 的手工/真实 Boss 用例)。"""
    g = _game()
    for f in range(3400):
        g.tick(keys=_move_keys(f, 40))
        if g.game_over:  # 续关(C++ retry 菜单选 Yes)
            g.game_over = False
            g.lives = 3.0
    assert g.frame == 3400
    assert g.globals.score > 0
    assert 0 < g.globals.gui_score <= g.globals.score
    assert g.boss is not None, "ECL 中超 Boss 未桥接出场"
    assert g._boss_ecl_state is not None and g._boss_ecl_state.is_boss


def test_item_collect() -> None:
    """道具收集: 出生无敌窗内贴脸放点/小樱点 → 收集入账。"""
    g = _game()
    g.tick()  # SPAWNING → INVULNERABLE(无敌窗内不会死, 可正常收集)
    score0 = g.globals.score
    g.items.spawn(g.player.pos, ItemType.POINT)
    g.tick()
    assert g.globals.point_items_collected_for_extend == 1
    assert g.globals.point_items_collected_this_stage == 1
    assert g.globals.score > score0
    plus0 = g.globals.cherry_plus
    g.items.spawn(g.player.pos, ItemType.CHERRY_SMALL)
    g.tick()
    assert g.globals.cherry_plus > plus0  # delta_cherry_plus 轨
    # 火力道具: power +1
    p0 = g.power
    g.items.spawn(g.player.pos, ItemType.POWER_SMALL)
    g.tick()
    assert g.power == p0 + 1


def test_same_tick_point_items_extend_once() -> None:
    """同帧收 2 个点道具过奖残阈值: 只奖 1 次, 分母推进到 125 而非 200。

    C++ 逐道具结算并即时更新全局计数 (ItemManager.cpp:274-325); 移植的
    ctx 是同帧快照, 不同步的话两个道具按同一旧基线各判一次奖残,
    extends 由 1 变 2, HUD 分母 125 跳成 200 (BUGS.md 增量#3)。"""
    g = _game()
    g.tick()  # SPAWNING → INVULNERABLE
    g.globals.point_items_collected_for_extend = 49  # 差 1 个到首个阈值 50
    lives0 = g.lives
    g.items.spawn(g.player.pos, ItemType.POINT)
    g.items.spawn(g.player.pos, ItemType.POINT)
    g.tick()
    assert g.globals.point_items_collected_for_extend == 51
    assert g.globals.extends_from_point_items == 1
    assert g.lives == lives0 + 1
    assert g.globals.next_needed_point_items_for_extend == 125


def test_kill_drops_items() -> None:
    """自机弹击杀站桩敌人 → 掉道具 + 击杀分入账。"""
    g = _game()
    _tick_until_alive(g)
    # 站桩敌人放在玩家正上方(玩家朝上射击); 长路径慢速防止提前离场
    px = g.player.pos.x
    g.host.spawn(path=[Vec2(px, 100), Vec2(px, 300)], life=4, speed=0.01)
    g.items.clear()  # 清掉热身期可能残留的掉落
    score0 = g.globals.score
    for _ in range(180):
        g.tick(keys=_STOP_KEYS)
        if len(g.items) > 0:
            break
    assert len(g.items) > 0, "击杀未掉道具"
    assert g.globals.score > score0, "击杀分未入账"


def test_graze_counts() -> None:
    """擦弹: 弹从玩家身旁 16px 掠过(进擦弹盒不碰判定盒) → 计数 + 200 显示分。"""
    g = _game()
    g.tick()  # INVULNERABLE: 擦弹可用且不会死
    for _ in range(70):
        g.tick()  # 等出生 60 帧清弹期(bulletGracePeriod)结束
    p = g.player.pos
    g.bullets.fire(_bullet_at(Vec2(p.x + 16, p.y - 200)))
    score0 = g.globals.score
    for _ in range(120):
        g.tick()
    assert g.globals.graze_in_total >= 1
    assert g.globals.graze_in_stage >= 1
    assert g.globals.score >= score0 + 200


def test_bomb_drains_cherry_and_counts() -> None:
    """炸弹: 扣弹/计数/subrank 惩罚/每帧扣樱点/清弹转道具/期间无敌。"""
    g = _game()
    _tick_until_alive(g)
    g.globals.add_cherry(30000)  # 先攒樱点供 drain
    rank0 = g.globals.rank
    g.tick(bomb=True)
    assert g.bomb.is_in_use
    assert g.bombs == 2 and g.bombs_used_count == 1
    assert g.globals.rank <= rank0 - 2  # DecreaseSubrank(200)
    cherry0 = g.cherry
    for _ in range(10):
        g.tick()
    assert g.cherry < cherry0, "炸弹未扣樱点"
    # 炸弹清弹盒把弹转成弹消点道具 (CheckBombGraze)
    p = g.player.pos
    g.bullets.fire(_bullet_at(Vec2(p.x, p.y - 30), speed=0.5))
    converted = False
    for _ in range(20):
        g.tick()
        if any(it.type == ItemType.POINT_BULLET for it in g.items.alive()):
            converted = True
            break
    assert converted, "清弹盒未把弹转道具"
    # 炸弹期间不死于中弹
    lives0 = g.lives
    g.bullets.fire(_bullet_at(g.player.pos, speed=0.5))
    g.tick()
    assert g.lives == lives0


def test_deathbomb_cancels_miss() -> None:
    """决死B (BUGS.md 增量#7): 中弹后的死亡窗口 (respawnTimer 倒数,
    灵梦 15 帧 = .sht initialRespawnTimer) 内按 B → 消耗一枚 bomb 代替
    丢残机 (Player.cpp:1719-1755 触发; BombData *Calc 每帧
    playerState=INVULNERABLE, UpdateDeath 因此不再倒数结算)。"""
    g = _game()
    _tick_until_alive(g)
    lives0, deaths0, bombs0 = g.lives, g.globals.deaths, g.bombs
    g.items.clear()
    g.host.clear()
    g.bullets.fire(_bullet_at(Vec2(g.player.pos.x, g.player.pos.y - 60)))
    for _ in range(600):
        g.tick(keys=_STOP_KEYS)  # 站桩让弹打中
        if g.player.state == PlayerState.DEAD:
            break
    assert g.player.state == PlayerState.DEAD, "未被击中"
    g.tick(bomb=True)  # 死亡窗口内按 B
    assert g.bomb.is_in_use, "决死B 未触发 bomb"
    assert g.player.state != PlayerState.DEAD, "决死B 未取消死亡状态"
    for _ in range(600):
        g.tick(keys=_STOP_KEYS)
    assert g.lives == lives0, "决死B 不应丢残机"
    assert g.globals.deaths == deaths0, "决死B 不应计入死亡数"
    assert g.bombs == bombs0 - 1, "决死B 应消耗一枚 bomb"


def test_death_settle_and_respawn() -> None:
    """死亡: 掉 P/樱罚/power 扣 16; 重生时扣残机并重置炸弹数。"""
    g = _game()
    _tick_until_alive(g)
    lives0, deaths0 = g.lives, g.globals.deaths
    g.power = 50.0
    g.bombs = 1.0
    g.globals.add_cherry(20000)
    g.items.clear()
    g.host.clear()
    g.bullets.fire(_bullet_at(Vec2(g.player.pos.x, g.player.pos.y - 60)))
    for _ in range(600):
        g.tick(keys=_STOP_KEYS)  # 站桩让弹打中
        if g.lives == lives0 - 1:
            break
    assert g.lives == lives0 - 1, "重生未扣残机"
    assert g.globals.deaths == deaths0 + 1
    assert g.bombs == g.shot_data.initial_bombs, "重生未重置炸弹数"
    assert g.power == 34.0, "power 应 50-16"
    assert g.cherry < 20000, "死亡樱点惩罚未生效"
    # 死亡点掉出 1 大 P + 5 小 P(结算当帧可见, 次帧起才被吸走/下落)
    drops = [it for it in g.items.alive()
             if it.type in (ItemType.POWER_BIG, ItemType.POWER_SMALL)]
    assert len(drops) >= 1
    assert not g.game_over


def test_border_ready_activate_and_break_saves_life() -> None:
    """满樱 → READY → 自动激活; 结界中弹 → 破结界保命不死。"""
    g = _game()
    _tick_until_alive(g)
    lives0, deaths0 = g.lives, g.globals.deaths
    g.bullets.clear()
    g._add_cherry_plus(50000)  # 满樱信号 → READY
    g.tick(keys=_STOP_KEYS)
    assert g.border.active, "READY 未自动激活"
    assert g.player.state == PlayerState.BORDER
    g.bullets.fire(_bullet_at(Vec2(g.player.pos.x, g.player.pos.y - 40)))
    for _ in range(200):
        g.tick(keys=_STOP_KEYS)
        if not g.border.active:
            break
    assert not g.border.active, "结界未破"
    assert g.lives == lives0, "结界破应保命"
    assert g.globals.deaths == deaths0
    assert g.globals.cherry_plus == g.globals.cherry_start


def test_boss_phase_switch_and_spellcard_capture() -> None:
    """Boss 生命阈值 → 切阶段; 符卡捕获结算(代码值直接入账)与超时失败。
    用演示 Boss(_spawn_demo_boss)走 boss.py 状态机路径; ECL 驱动的
    Boss 桥接见 test_stage_ecl.py。"""
    g = _game()
    _tick_until_alive(g)
    _isolate(g)  # 停掉 ECL 时间轴, 只留演示 Boss
    g._spawn_demo_boss()
    g.tick()
    assert g.boss is not None and g.boss.is_capturing
    score0 = g.globals.score
    g.boss.life = 350.0  # 跌破阈值 400(伤害缩放由 settle_damage 负责, 不在此测)
    g.tick()
    assert g.globals.spell_cards_captured == 1, "首张符卡应捕获"
    assert g.globals.score > score0 + 100000  # 捕获分(约 20 万显示分)
    assert g.boss.phase == 1 and g.boss.spellcard_idx == 1
    assert g.boss.life == 400  # 阈值钉住生命
    # 超时: 缩短回调阈值, 验证超时失败 + 樱点惩罚 + 切下一张
    g.globals.add_cherry(40000)
    g.boss.set_timer_callback(2, 2)
    for _ in range(5):
        g.tick()
    assert g.globals.spell_cards_captured == 1, "超时不算捕获"
    assert g.cherry < 40000, "超时樱点惩罚未生效"
    assert g.boss.spellcard_idx == 2, "超时回调未切下一张"


def test_full_smoke_2000_frames_with_bombs() -> None:
    """2000+ 帧综合冒烟: 移动+射击+两次 bomb, 全程不崩且状态自洽。"""
    g = _game()
    for f in range(2200):
        keys = ((f // 37) % 2 == 1, (f // 37) % 2 == 0,
                False, (f // 120) % 2 == 1, (f // 90) % 3 == 2)
        g.tick(keys=keys, bomb=(f in (700, 1200)))
        if g.game_over:  # 续关
            g.game_over = False
            g.lives = 3.0
    assert g.frame == 2200
    assert g.globals.gui_score <= g.globals.score
    assert 0 <= g.lives <= 8
    assert 0 <= g.bombs <= 8
    assert g.globals.cherry_start <= g.cherry <= g.globals.cherry_max
    # 炸弹确实触发过(700 帧时残机/炸弹充足)
    assert g.bombs_used_count >= 1
    r = g.final_result(cleared=False)
    assert r["score"] == g.globals.score


# ---------------------------------------------------------------------------
# 自机弹 → 敌人伤害全管线 (CalcDamageToEnemy + settle_damage) 与炸弹
# ---------------------------------------------------------------------------

_FOCUS_STOP = (False, False, False, False, True)


def _isolate(g: PerfectCherryBloom) -> None:
    """清场并停掉波次编排(含 ECL 时间轴), 让测试只面对手动放的敌人(确定性)。"""
    g.host.clear()
    g.bullets.clear()
    g.items.clear()
    g.ecl_timelines = []
    for b in g.player.bullet_pool:  # 在飞的自机弹一并回池(防走位前发的弹干扰)
        b.bullet_state = 0
    for ts in g.player.timers:
        ts.timer = 0
        ts.bullet = None
    g._wave = lambda: None


def test_marisa_a_missile_sustained_damage() -> None:
    """魔理沙A 穿透导弹(bs2==3): 首中爆炸变形后仍逐(隔)帧结算伤害, 并走
    cherryGain 管线(非 focus 非 bomb → cherry_plus 入账)。"""
    g = PerfectCherryBloom(data_path=DAT, character=2, difficulty=1)
    _tick_until_alive(g)
    _isolate(g)
    px = g.player.pos.x
    # 判定半径 20, 罩住 ±24 的子机导弹
    e = g.host.spawn(path=[Vec2(px, 100), Vec2(px, 400)], life=2000,
                     speed=0.01, radius=20.0)
    saw_exploding = False
    dmg_frames = 0
    prev = e.life
    for _ in range(120):
        g.tick(keys=_STOP_KEYS)
        if e.life < prev:
            dmg_frames += 1
            prev = e.life
        if any(s.bullet_state == 2 for s in g.player.shots):
            saw_exploding = True
    assert saw_exploding, "导弹未进入爆炸态(bullet_state==2)"
    assert dmg_frames > 10, f"穿透爆炸弹应持续多帧伤害, 实际 {dmg_frames} 帧"
    assert g.globals.cherry_plus > 0, "非 focus 命中的 cherryGain 未入账"


def test_sakuya_a_focus_homing_hits() -> None:
    """咲夜A 集中(FIRE_HOMING): 索敌窗口 [-120°,-60°] 内的敌人成为
    sakuya_target_position, 出膛弹朝目标重定向(速度×1.5) → 侧上方敌人被命中。"""
    g = PerfectCherryBloom(data_path=DAT, character=4, difficulty=1)
    _tick_until_alive(g)
    _isolate(g)
    px = g.player.pos.x
    # 相对玩家角度 atan2(-184, 60) ≈ -72°, 在咲夜索敌窗口内; 直射打不中
    e = g.host.spawn(path=[Vec2(px + 60, 200), Vec2(px + 60, 400)], life=500,
                     speed=0.01, radius=14.0)
    saw_target = False
    for _ in range(150):
        g.tick(keys=_FOCUS_STOP)
        if g.player.sakuya_target_position.x > -100.0:
            saw_target = True
        if not e.alive:
            break
    assert saw_target, "咲夜索敌未设置 sakuya_target_position"
    assert not e.alive, "追踪弹未击杀侧上方敌人"


def test_marisa_b_laser_slot_bullet() -> None:
    """魔理沙B 集中: 激光槽弹(UPDATE_PLAYER_LASER, bs2==5)持续存活不爆炸,
    对正上方敌人连续出伤(激光弹不切 bullet_state==2)。"""
    g = PerfectCherryBloom(data_path=DAT, character=3, difficulty=1)
    _tick_until_alive(g)
    _isolate(g)
    px = g.player.pos.x
    e = g.host.spawn(path=[Vec2(px, 100), Vec2(px, 400)], life=2000,
                     speed=0.01, radius=20.0)
    laser = None
    for _ in range(30):  # 等 focus 过渡 8 帧 + 槽弹建立
        g.tick(keys=_FOCUS_STOP)
        lasers = [s for s in g.player.shots if s.update_cb == 5]
        if lasers:
            laser = lasers[0]
            break
    assert laser is not None, "激光槽弹未建立"
    dmg_frames = 0
    prev = e.life
    for _ in range(40):
        g.tick(keys=_FOCUS_STOP)
        if e.life < prev:
            dmg_frames += 1
            prev = e.life
        assert laser.bullet_state != 0, "激光槽弹应持续存活"
        assert laser.bullet_state == 1, "激光弹命中不进爆炸态"
    assert dmg_frames >= 15, f"激光应隔帧连续出伤, 实际 {dmg_frames}/40 帧"


def test_sakuya_b_bomb_stops_bullets() -> None:
    """咲夜B 炸弹(非 ReimuA 机体放 bomb): 停时事件接入 → 全场敌弹速度清零,
    位置定格(StopBulletMovement 是永久清零, 不是可逆冻结)。"""
    g = PerfectCherryBloom(data_path=DAT, character=5, difficulty=1)
    _tick_until_alive(g)
    _isolate(g)
    # 绝对角向下的弹(不瞄玩家), 先跑 20 帧让它们散开
    for x in (96.0, 192.0, 288.0):
        g.bullets.fire(_bullet_at(Vec2(x, 40), speed=2.0))
    for _ in range(20):
        g.tick(keys=_STOP_KEYS)
    assert len(g.bullets) >= 3
    g.tick(keys=_STOP_KEYS, bomb=True)
    assert g.bomb.is_in_use, "咲夜B 炸弹未触发(机体限制未解除?)"
    frozen = {id(b): (b.pos.x, b.pos.y) for b in g.bullets.alive()}
    assert all(b.speed == 0.0 and b.vel.x == 0.0 and b.vel.y == 0.0
               for b in g.bullets.alive()), "停时未清零弹速"
    for _ in range(10):
        g.tick(keys=_STOP_KEYS)
    for b in g.bullets.alive():
        assert (b.pos.x, b.pos.y) == frozen[id(b)], "停时期间弹仍在移动"


def test_bomb_spellcard_scaling_pipeline() -> None:
    """符卡中 bomb 伤害缩放走完整管线(魔理沙B 集中 Master Spark, 每帧盒伤 23):
    used_bomb 前 0 伤害; used_bomb 后 int(23/2.5)=9/帧 (cap70 先于缩放, 23<70)。"""
    g = PerfectCherryBloom(data_path=DAT, character=3, difficulty=1)
    _tick_until_alive(g)
    _isolate(g)
    g.player.pos = Vec2(40.0, g.player.pos.y)  # 挪到版左, 自机弹打不到 Boss
    # 先放 bomb(此时无 Boss → used_bomb 不会被置位), 再让演示 Boss 出场开符卡
    g.tick(keys=_FOCUS_STOP, bomb=True)
    assert g.bomb.is_in_use
    g._spawn_demo_boss()
    g.tick(keys=_FOCUS_STOP)
    assert g.boss is not None and g.boss.is_active and g.boss.spellcard_idx == 0
    assert not g.boss.used_bomb
    # bomb 持续期间(duration 340): 符卡 + 非 used_bomb 的 bomb 伤害 → 0
    for _ in range(60):
        g.tick(keys=_FOCUS_STOP)
        assert g.boss.life == 600, "used_bomb 前符卡中 bomb 伤害应为 0"
    while g.bomb.is_in_use:
        g.tick(keys=_FOCUS_STOP)
    # 第二颗 bomb: try_start_bomb → mark_bombed → used_bomb=True → 伤害 /2.5
    g.tick(keys=_FOCUS_STOP, bomb=True)
    assert g.bomb.is_in_use and g.boss.used_bomb
    # 触发帧即出伤(bomb.start 的 calc 后 bomb.tick 再 calc, timer=1 布盒): 600-9
    assert g.boss.life == 600 - 9
    for _ in range(6):  # 伤害帧: timer≡1,2,3 (mod 4) → 6 帧中 5 帧 × 9
        g.tick(keys=_FOCUS_STOP)
    assert g.boss.life == 600 - 9 - 45, \
        f"符卡 used_bomb 后 bomb 伤害应 9/帧, life={g.boss.life}"


def test_bomb_vacuums_items() -> None:
    """BUGS.md#3 按 B 吸取全屏道具: bomb 首帧 RemoveAllItems → 场上道具全转吸附
    (BombData.cpp:144 等各 *Calc timer==0 → ItemManager::RemoveAllItems)。"""
    g = _game()
    _tick_until_alive(g)
    _isolate(g)
    dropped = [g.items.spawn(Vec2(60 + i * 60, 120), ItemType.POINT)
               for i in range(4)]
    assert all(it.state == STATE_FALL for it in dropped)  # 前置: 下落中
    g.tick(keys=_STOP_KEYS, bomb=True)
    assert g.bomb.is_in_use, "炸弹未触发"
    assert all(it.state == STATE_ATTRACT for it in dropped), "按 B 未吸取场上道具"
    # 吸附道具最终进袋(分数增长)
    score0 = g.globals.score
    for _ in range(240):
        g.tick(keys=_STOP_KEYS)
        if all(it not in g.items.alive() for it in dropped):
            break
    assert all(it not in g.items.alive() for it in dropped), "吸附道具未被收进"
    assert g.globals.score > score0


def test_bullet_clear_spawns_attracted_items() -> None:
    """BUGS.md#5 道中/boss 出场与符卡开始的清弹: 弹转弹消点且出生即吸附
    (EclManager.cpp:673 BeginSpellcard → BulletManager.cpp:423-434
    RemoveAllBullets(1) → SpawnItem(…, state=1))。"""
    g = _game()
    _tick_until_alive(g)
    _isolate(g)
    assert g.ecl_host is not None
    g.bullets.fire(_bullet_at(Vec2(192, 100)))
    g.bullets.fire(_bullet_at(Vec2(150, 150)))
    g.ecl_host.remove_all_bullets(True)  # = BeginSpellcard / ins80 的清弹
    # 弹转道具立即发生(死弹在次帧 step 才清出列表, 见 BulletWorld.step 末尾)
    items = g.items.alive()
    assert len(items) == 2
    assert all(it.type == ItemType.POINT_BULLET for it in items)
    assert all(it.state == STATE_ATTRACT for it in items), "清弹星点未自动吸附"
    g.tick(keys=_STOP_KEYS)
    assert len(list(g.bullets.alive())) == 0, "清弹未生效"
    # 星点自动飞进自机(不按键也收进)
    plus0 = g.globals.cherry_plus
    for _ in range(120):
        g.tick(keys=_STOP_KEYS)
        if not g.items.alive():
            break
    assert not g.items.alive(), "星点未被自动收进"
    assert g.globals.cherry_plus > plus0  # 弹消点的 cherryPlus +20 入账


def test_full_power_banner_and_power_to_cherry() -> None:
    """BUGS.md#12 满火力: "Full Power Mode!" 横幅 (ItemManager.cpp:231
    Gui::ShowStatusPopup(0, 1)) + 满火力后 P 道具转樱点 (ItemManager.cpp:40-45)。"""
    g = _game()
    g.tick()  # SPAWNING → INVULNERABLE(可收集)
    g.power = FULL_POWER - 1
    g.items.spawn(g.player.pos, ItemType.POWER_SMALL, power=g.power)
    g.tick()
    assert g.power == FULL_POWER
    assert g.globals.status_popup == STATUS_FULL_POWER, "满火力横幅未弹出"
    # 满火力后新掉落的 P 转 CHERRY(spawn 转换)
    it = g.items.spawn(Vec2(100, 100), ItemType.POWER_BIG, power=g.power)
    assert it.type == ItemType.CHERRY
    # 横幅 180 帧后消失 (Gui.cpp:1343)
    for _ in range(180):
        g.tick(keys=_STOP_KEYS)
    assert g.globals.status_popup == 0


def test_point_item_collect_popup() -> None:
    """BUGS.md#16 收点得点弹字 (ItemManager.cpp:272 → AsciiManager::CreatePopup1):
    弹字登记在 globals.popups(渲染见 view/popup_view.py), 上浮 + 60 帧寿命。"""
    g = _game()
    g.tick()
    pos = Vec2(g.player.pos.x, g.player.pos.y)
    it = g.items.spawn(pos, ItemType.POINT)
    it.start = Vec2.zero()  # 定住道具, 让收集点 = 出生点(出生初速 -2.2 会上移)
    g.tick()
    assert len(g.globals.popups) == 1
    p = g.globals.popups[0]
    assert p.kind == 1
    assert p.value == 50000 - int(pos.y - g.shot_data.poc_y) * 100  # 自机位按 y 衰减
    # 弹字位置 = 道具收集点(同帧道具下落渐变 +0.03、弹字上浮 -0.5 的零头内)
    assert p.pos.x == pos.x and abs(p.pos.y - pos.y) < 1.0
    # 弹字上浮 (AsciiManager.cpp:55)
    g.tick(keys=_STOP_KEYS)
    assert g.globals.popups and g.globals.popups[0].pos.y < pos.y

def test_border_banners() -> None:
    """结界激活弹 "Supernatural Border!!" (Player.cpp:2138);
    自然破弹 "Border Bonus" (Player.cpp:2013) (BUGS.md#11)。"""
    from touhou.games.th07.globals import STATUS_BORDER, STATUS_BORDER_BONUS
    g = _game()
    _tick_until_alive(g)
    _isolate(g)  # 停波次防流弹提前破结界
    g._add_cherry_plus(50000)  # 满樱信号 → READY
    g.tick(keys=_STOP_KEYS)
    assert g.border.active, "READY 未自动激活"
    assert g.globals.status_popup == STATUS_BORDER
    # 结界自然破(倒计时走完, ~数百帧) → Border Bonus 横幅 + 得分
    broke = False
    for _ in range(900):
        g.tick(keys=_STOP_KEYS)
        if g.globals.status_popup == STATUS_BORDER_BONUS:
            broke = True
            break
    assert broke, "结界自然破未弹 Border Bonus 横幅"
    assert g.globals.status_popup_arg > 0  # fmtArg = (cherry-cherryStart)*10


def test_bomb_homing_targets_boss_via_targeting() -> None:
    """追踪炸弹目标 = 索敌系统的 positionOfLastEnemyHit, 无敌人时 (-999,-999)
    回落追玩家 (BUGS.md#10, BombData.cpp:390 / EnemyManager.cpp:894-938)。"""
    g = _game()
    _tick_until_alive(g)
    _isolate(g)
    ctx = g._bomb_ctx()
    assert ctx.last_enemy_hit is not None and ctx.last_enemy_hit.x <= -100
    g._spawn_demo_boss()
    g.tick(keys=_STOP_KEYS)  # 跑一帧让索敌扫描写回 player 字段
    ctx = g._bomb_ctx()
    assert ctx.last_enemy_hit == g.boss.pos


def test_bomb_damage_box_respects_hittable_gate() -> None:
    """bomb 伤害盒同走 canDie && isHittable 门控 (BUGS.md#10,
    EnemyManager.cpp:776-779): isHittable=0 的敌人不掉血。"""
    from touhou.utils import Vec2 as V2
    g = _game()
    _tick_until_alive(g)
    _isolate(g)
    e = g.host.spawn(path=[V2(192.0, 120.0)], life=1000, speed=0.0)
    # 手工放一个盖住敌人的 bomb 伤害盒
    g.bomb.is_in_use = True
    from touhou.engine.bomb_base import DamageBox
    g.bomb.damage_boxes.append(DamageBox(e.pos, V2(100.0, 100.0), 10))
    e.is_hittable = False
    g._apply_bomb_boxes()
    assert e.life == 1000, "isHittable=0 不应吃 bomb 伤害"
    e.is_hittable = True
    g._apply_bomb_boxes()
    assert e.life < 1000, "isHittable=1 应吃 bomb 伤害"
