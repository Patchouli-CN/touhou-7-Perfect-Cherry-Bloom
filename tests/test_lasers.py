"""Touhou: 激光三态机 + 命中/擦激光测试。"""
from __future__ import annotations

import math
import sys

sys.path.insert(0, r"D:\python_play\Touhou08")

from touhou.engine.lasers import LaserState, LaserWorld, laser_hits_player  # noqa: E402
from touhou.utils import Vec2  # noqa: E402


def _aimed_world(player: Vec2 = Vec2(192, 400)) -> LaserWorld:
    w = LaserWorld()
    w.spawn(Vec2(192, 100), 0.0, aimed=True, width=8.0, player_pos=Vec2(192, 400))
    return w


def test_three_state_machine() -> None:
    w = LaserWorld()
    l = w.spawn(Vec2(100, 100), 0.0, aimed=False, duration=60, start_time=20, end_time=30)
    assert l.state == LaserState.SPAWNING
    for _ in range(25):
        l.step()
    assert l.state == LaserState.ACTIVE
    for _ in range(70):
        l.step()
    assert l.state == LaserState.DESPAWNING
    assert l.in_use  # end_time=30 未耗尽
    for _ in range(40):
        l.step()
    assert not l.in_use


def test_aimed_laser_rotates_toward_player() -> None:
    w = LaserWorld()
    player = Vec2(384, 400)  # 玩家在右侧
    l = w.spawn(Vec2(192, 100), 0.0, aimed=True, player_pos=player)
    # 朝向玩家的角 = atan2(400-100, 384-192) > 0 (右下方)
    assert l.angle > 0


def test_hit_when_on_laser() -> None:
    # 竖激光: 自 (0,0) 沿 +x 长 160, 玩家在激光轨迹上(x=100)
    w = LaserWorld()
    l = w.spawn(Vec2(0, 0), 0.0, aimed=False, start_length=160.0)  # 角度0 = 沿+x
    hit, _ = laser_hits_player(l, Vec2(100, 0), 4.0)
    assert hit  # 玩家在 laser 盒内(长度内、沿中线)


def test_graze_expands_box() -> None:
    w = LaserWorld()
    l = w.spawn(Vec2(0, 0), 0.0, aimed=False, width=8.0, start_length=160.0)
    # 玩家在激光一侧, y 偏差 30: 命中带=半宽4+玩家半径4=8, 擦带外扩到 8+48=56
    hit, graze = laser_hits_player(l, Vec2(80, 30), 4.0, graze_extra=48.0)
    assert not hit       # 30 > 8
    assert graze         # 30 < 56


def _remove_all_laser(*, flags: int = 0, offset_a: float = 0.0,
                      offset_b: float = 100.0, angle: float = 0.0,
                      state: LaserState = LaserState.ACTIVE):
    w = LaserWorld()
    l = w.spawn(Vec2(50, 50), angle, aimed=False, duration=999,
                start_time=20, end_time=30)
    l.flags = flags
    l.offset_a = offset_a
    l.offset_b = offset_b
    l.state = state
    return w, l


def test_remove_all_despawns_lasers() -> None:
    """RemoveAllBullets 激光段 (BulletManager.cpp:439-471): 进 DESPAWNING,
    timer=0, width=targetWidth, hitboxEndTime=0。"""
    w, l = _remove_all_laser()
    l.width = 3.0
    w.remove_all(spawn_items=False)
    assert l.state == LaserState.DESPAWNING
    assert l.timer == 0
    assert l.width == l.target_width
    assert l.hitbox_end_time == 0


def test_remove_all_flag4_exemption() -> None:
    """flags&4 的激光在 RemoveAllBullets(param!=10) 时豁免; DespawnBullets /
    RemoveAllBullets(10) (skip_flag4=False) 不豁免。"""
    w, l = _remove_all_laser(flags=4)
    w.remove_all(spawn_items=False, skip_flag4=True)
    assert l.state == LaserState.ACTIVE
    assert l.hitbox_end_time == 40
    w2, l2 = _remove_all_laser(flags=4)
    w2.remove_all(spawn_items=False, skip_flag4=False)
    assert l2.state == LaserState.DESPAWNING
    assert l2.hitbox_end_time == 0


def test_remove_all_spawns_items_along_laser() -> None:
    """spawn_items: 自 startOffset 起沿线每 32px 一个; spawn_at_pos 另加原点
    (仅 DespawnBullets 路径)。已在 DESPAWNING 的不出道具但仍清 hitboxEndTime。"""
    w, l = _remove_all_laser(offset_a=0.0, offset_b=100.0, angle=0.0)
    got = []
    w.remove_all(spawn_items=True, spawn_item=got.append)
    # angle=0 → 沿 +x: x = 50+0/32/64/96, y = 50
    assert [(round(p.x), round(p.y)) for p in got] == \
        [(50, 50), (82, 50), (114, 50), (146, 50)]
    w, l = _remove_all_laser(offset_a=0.0, offset_b=100.0,
                             angle=math.pi / 2)  # 沿 +y
    got = []
    w.remove_all(spawn_items=True, spawn_at_pos=True, spawn_item=got.append)
    assert [(round(p.x), round(p.y)) for p in got] == \
        [(50, 50), (50, 50), (50, 82), (50, 114), (50, 146)]
    # 已 DESPAWNING: 不出道具, hitboxEndTime 照样清零
    w, l = _remove_all_laser(state=LaserState.DESPAWNING)
    got = []
    w.remove_all(spawn_items=True, spawn_item=got.append)
    assert got == []
    assert l.hitbox_end_time == 0
