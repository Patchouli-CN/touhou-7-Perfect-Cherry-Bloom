"""音效系统测试: SE 索引表 / SoundQueue 节流 / 引擎播放点透出 / 播放层容错。

对照 SoundPlayer.cpp(SOUND_BUFFER_IDX_VOL/g_SFXList/PlaySoundByIdx)。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, r"D:\python_play\Touhou08")

from touhou.games.th07.world import PerfectCherryBloom  # noqa: E402
from touhou.engine.bullets import BulletWorld  # noqa: E402
from touhou.engine.ecl import EclMachine, EclOpcode, EclWorld  # noqa: E402
from touhou.games.th07.ecl_host import GameEclHost  # noqa: E402
from touhou.engine.enemies import EnemyHost  # noqa: E402
from touhou.games.th07.items import ItemType, ItemWorld  # noqa: E402
from touhou.engine.lasers import LaserWorld  # noqa: E402
from touhou.games.th07.player import Player, PlayerState  # noqa: E402
from touhou.schema.shot_data import ShotData, ShotEntry, ShotLevel  # noqa: E402
from touhou.schema.sound import SE, SE_FILES, SE_VOLUMES, SoundQueue  # noqa: E402
from touhou.utils import Vec2  # noqa: E402
from touhou.engine.view.sound_player import SoundPlayer, _db_to_gain  # noqa: E402
from tests.test_ecl import _f, _instr, build_ecl  # noqa: E402

DAT = Path(r"D:\TOUHOU_GAME\[th07] 东方妖妖梦 (日文版)\th07.dat")
NEEDS_DAT = pytest.mark.skipif(not DAT.exists(), reason="需要真实 th07.dat")

OP = EclOpcode


# ---- SE 索引表(SoundPlayer.cpp:10-77) ----

def test_se_table_covers_38_slots() -> None:
    assert len(SE) == 38
    assert len(SE_FILES) == 38 and len(SE_VOLUMES) == 38
    assert all(int(k) == i for i, k in enumerate(SE_FILES))


def test_se_file_mapping_spot_checks() -> None:
    assert SE_FILES[SE.SOUND_0] == "se_plst00.wav"
    assert SE_FILES[SE.PICHUN] == "se_pldead00.wav"
    assert SE_FILES[SE.BOMB_MARISA_A_FOCUS] == "se_tan00.wav"
    assert SE_FILES[SE.SELECT] == "se_ok00.wav"
    assert SE_FILES[SE.BACK] == "se_cancel00.wav"
    assert SE_FILES[SE.MOVE_MENU] == "se_select00.wav"
    assert SE_FILES[SE.BOMB] == "se_cat00.wav"
    assert SE_FILES[SE.ENEMY_SPELLCARD_END] == "se_tan00.wav"
    assert SE_FILES[SE.SOUND_16] == "se_lazer00.wav"
    assert SE_FILES[SE.SOUND_17] == "se_lazer01.wav"
    assert SE_FILES[SE.SOUND_18] == "se_enep01.wav"
    assert SE_FILES[SE.BOMB_SAKUMARI] == "se_nep00.wav"
    assert SE_FILES[SE.SOUND_20] == "se_damage00.wav"
    assert SE_FILES[SE.SOUND_21] == "se_item00.wav"
    assert SE_FILES[SE.SOUND_25] == "se_kira00.wav"
    assert SE_FILES[SE.EXTEND] == "se_extend.wav"
    assert SE_FILES[SE.SOUND_29] == "se_timeout.wav"
    assert SE_FILES[SE.GRAZE] == "se_graze.wav"
    assert SE_FILES[SE.POWERUP] == "se_powerup.wav"
    assert SE_FILES[SE.BORDER_ACTIVATE] == "se_border.wav"
    assert SE_FILES[SE.BORDER_BREAK] == "se_bonus.wav"
    assert SE_FILES[SE.BORDER_ACTIVATE2] == "se_bonus2.wav"
    assert SE_FILES[SE.SOUND_37] == "se_pause.wav"


# ---- SoundQueue 节流(PlaySoundByIdx, SoundPlayer.cpp:597-623) ----

def test_queue_dedupes_same_idx_within_frame() -> None:
    q = SoundQueue()
    q.play(SE.SOUND_20)
    q.play(SE.SOUND_20)
    q.play(int(SE.SOUND_20))  # int 与枚举等价去重
    assert q.take() == [SE.SOUND_20]


def test_queue_caps_at_5_per_frame() -> None:
    q = SoundQueue()
    for i in range(8):
        q.play(i)
    assert q.take() == [0, 1, 2, 3, 4]


def test_queue_take_clears_and_ignores_out_of_range() -> None:
    q = SoundQueue()
    q.play(-1)
    q.play(38)
    q.play(SE.PICHUN)
    assert q.take() == [SE.PICHUN]
    assert q.take() == []


# ---- 引擎播放点(无 dat, 手工对象) ----

_SD_SND = ShotData(
    initial_bombs=3.0, initial_respawn_timer=5, hitbox_radius=4.0,
    grab_item_radius=48.0, item_collect_speed=4.0, item_collect_radius=16.0,
    cherry_penalty_multiplier=0.5, poc_y=128.0,
    speed=4.0, speed_focus=2.0, speed_diagonal=2.8, speed_diagonal_focus=1.4,
    levels=[ShotLevel(0, [
        ShotEntry(fire_interval=4, fire_offset=0, offset=(0.0, -16.0),
                  hitbox=(6.0, 6.0), angle=-1.5707964, speed=10.0, damage=1,
                  option=0, bullet_state2=0, fire_cb=1, update_cb=0,
                  draw_cb=0, hit_cb=0, sound_idx=0),
        ShotEntry(fire_interval=-1, fire_offset=0, offset=(0.0, 0.0),
                  hitbox=(0.0, 0.0), angle=0.0, speed=0.0, damage=0,
                  option=0, bullet_state2=0, fire_cb=0, update_cb=0,
                  draw_cb=0, hit_cb=0),
    ])],
)


def _alive_player() -> Player:
    p = Player(shot_data=_SD_SND)
    p.sound = SoundQueue()
    p.step()  # SPAWNING → INVULNERABLE
    p.state = PlayerState.ALIVE
    p.invulnerability_timer = 0
    p.events = []
    return p


def test_player_shot_sound_from_sht_entry() -> None:
    """发弹音: shtEntry->soundIdx>=0 时播放 (Player.cpp:116-119)。"""
    p = _alive_player()
    p.push_keys(firing=True)
    p.step()
    assert SE.SOUND_0 in p.sound.take()


def test_player_shot_sound_dedupes_within_frame() -> None:
    """同帧多条 entry/多弹同 idx 只入队一次(节流语义)。"""
    p = _alive_player()
    p.push_keys(firing=True)
    for _ in range(3):
        p.step()
    assert p.sound.take().count(SE.SOUND_0) == 1


def test_player_death_sound() -> None:
    """Die → SOUND_PICHUN (Player.cpp:1238)。"""
    p = _alive_player()
    p.die()
    assert p.sound.take() == [SE.PICHUN]


def test_player_graze_sound() -> None:
    """ScoreGraze → SOUND_GRAZE (Player.cpp:1210)。"""
    p = _alive_player()
    assert p.check_graze(Vec2(p.pos.x + 10, p.pos.y), (4.0, 4.0))
    assert p.sound.take() == [SE.GRAZE]


def _host_with_sound(*subs: list[bytes]) -> tuple[EclMachine, GameEclHost]:
    f = build_ecl(*subs)
    world = EclWorld(difficulty=1)
    host = GameEclHost(f, world, enemies=EnemyHost(), bullets=BulletWorld(),
                       lasers=LaserWorld(), items=ItemWorld())
    host.sound = SoundQueue()
    m = EclMachine(f, world=world, host=host)
    m.enemy.life = 10
    m.start(0)
    return m, host


def test_ecl_play_sound_passthrough() -> None:
    """ECL PLAY_SOUND(105) 原样透传 idx (EclManager.cpp:1662-1664)。"""
    m, host = _host_with_sound(
        [_instr(0, OP.PLAY_SOUND, (16,)), _instr(9999, OP.UNIMP)])
    m.step()
    assert host.sound.take() == [16]


def test_ecl_bullet_spawn_sound_gated_by_flag_0x200() -> None:
    """敌弹发弹音: flags&0x200 才播 sound_idx (BulletManager.cpp:611-615)。

    spawn 指令 args[7] 覆写 flags (EclManager.cpp:1318); SET_BULLET_SOUND(81)
    只设 sound_idx 并置 0x200 (EclManager.cpp:1866-1878), 被后续 spawn 覆写。
    """
    m, host = _host_with_sound(
        [_instr(0, OP.SET_BULLET_SOUND, (8, -1)),
         _instr(1, OP.SPAWN_BULLET_PATTERN_RING_ABS,
                (0, 1, 1, _f(2.0), _f(2.0), 0, 0, 0x200)),
         _instr(2, OP.SPAWN_BULLET_PATTERN_RING_ABS,
                (0, 1, 1, _f(2.0), _f(2.0), 0, 0, 0)),
         _instr(9999, OP.UNIMP)])
    m.step()  # t0: SET_BULLET_SOUND(sound_idx=8, 置 0x200)
    m.step()  # t1: 发弹 flags=0x200 → 播 8
    assert host.sound.take() == [8]
    m.step()  # t2: 发弹 flags=0 → 无声
    assert host.sound.take() == []


def test_ex19_ex20_bgm_events() -> None:
    """幽幽子终符: ex19 → FadeOutMusic(3.0), ex20 → th07_13b.mid
    (EnemyEclInstr.cpp:919/:925)。"""
    m, host = _host_with_sound(
        [_instr(0, OP.RUN_EX_INS, (19, 0)), _instr(1, OP.RUN_EX_INS, (20, 0)),
         _instr(9999, OP.UNIMP)])
    m.step()
    m.step()
    assert host.bgm_events == [("fadeout", 3.0), ("music_file", "th07_13b.mid")]


# ---- impl 整合(真实 dat) ----

def _game() -> PerfectCherryBloom:
    return PerfectCherryBloom(data_path=DAT, character=0, difficulty=1)


def _enter_field(g: PerfectCherryBloom, limit: int = 900) -> None:
    """Z 脉冲推对话, 直到玩家 ALIVE 且无对话门控。"""
    for _ in range(limit):
        if g.player.state == PlayerState.ALIVE and not g._msg_active():
            return
        g.tick(advance=(g.frame % 15 == 0))
    raise AssertionError("玩家未能进入可行动状态")


def _collect_sounds(g: PerfectCherryBloom, frames: int) -> list[int]:
    """跑 N 帧, 汇总出现过的音效 idx。"""
    seen: list[int] = []
    for _ in range(frames):
        g.tick(advance=(g.frame % 15 == 0))
        for s in g.frame_sounds:
            if s not in seen:
                seen.append(s)
    return seen


@NEEDS_DAT
def test_impl_shot_sound_in_frame_events() -> None:
    g = _game()
    _enter_field(g)
    # 发射周期 fire_interval 帧才出一组(ply00a Lv0 = 5 帧), 多跑几帧收集
    assert SE.SOUND_0 in _collect_sounds(g, 8)


@NEEDS_DAT
def test_impl_item_collect_sounds() -> None:
    """吃道具 → item00; 火力跨档 → powerup (ItemManager.cpp:243/:482)。"""
    g = _game()
    _enter_field(g)
    g.ecl_timelines = []  # 停时间轴, 免干扰
    g.items.spawn(Vec2(g.player.pos.x, g.player.pos.y), ItemType.POWER_BIG)
    g.tick()
    assert SE.SOUND_21 in g.frame_sounds
    assert SE.POWERUP in g.frame_sounds  # 0 → 8 跨档


@NEEDS_DAT
def test_impl_bomb_sounds() -> None:
    """灵梦A 炸弹 → SOUND_BOMB_REIMU_A + 横幅音 SOUND_BOMB
    (BombData.cpp:180 + Gui.cpp:356)。"""
    g = _game()
    _enter_field(g)
    g.ecl_timelines = []
    g.tick(bomb=True)
    assert SE.BOMB_REIMU_A in g.frame_sounds
    assert SE.BOMB in g.frame_sounds


@NEEDS_DAT
def test_impl_spellcard_declare_and_timeout_warn() -> None:
    """演示 Boss 符卡宣告 → SOUND_BOMB; 倒计时 <10s 逐秒 → SOUND_29
    (Gui.cpp:396 / Gui.cpp:1888-1892)。"""
    g = _game()
    _enter_field(g)
    g.ecl_timelines = []
    g._spawn_demo_boss()
    g._drain_frame_events()
    assert SE.BOMB in g.frame_sounds
    boss = g.boss
    boss.timer = boss.timer_callback_threshold - 9 * 60  # 剩 9 秒
    g.tick()
    assert SE.SOUND_29 in g.frame_sounds
    # 同一秒不重复响(去抖)
    g.tick()
    assert SE.SOUND_29 not in g.frame_sounds


@NEEDS_DAT
def test_impl_enemy_hit_and_kill_sounds() -> None:
    """敌受击 → damage00, 击坠 → enep00 (EnemyManager.cpp:1052/:1016)。"""
    g = _game()
    _enter_field(g)
    g.ecl_timelines = []
    g.host.clear()
    g.bullets.clear()
    # 慢速离场路径(speed 需 < 距终点的距离, 否则立刻 done 被 host.step 移出)
    at = Vec2(g.player.pos.x, g.player.pos.y - 100)
    e = g.host.spawn(path=[at, Vec2(at.x, -200.0)], life=1, speed=0.01)
    seen = _collect_sounds(g, 60)
    assert not e.alive
    assert SE.SOUND_20 in seen
    assert SE.SOUND_2 in seen or SE.SOUND_3 in seen


@NEEDS_DAT
def test_impl_player_death_sound() -> None:
    """中弹 → SOUND_PICHUN (Player.cpp:1238, 经 check_killbox → die)。"""
    import math

    from touhou.engine.bullets import Aim, Burst
    g = _game()
    _enter_field(g)
    g.ecl_timelines = []
    g.host.clear()
    g.bullets.clear()
    g.bullets.fire(Burst(Vec2(g.player.pos.x, g.player.pos.y - 8),
                         math.pi / 2, Aim.RING_ABSOLUTE, 1, 1, 4.0, 4.0, 0.0))
    seen = _collect_sounds(g, 60)
    assert SE.PICHUN in seen
    assert g.player.state != PlayerState.ALIVE


# ---- 播放层容错 ----

def test_sound_player_silent_without_mixer() -> None:
    """mixer 未初始化: ensure_loaded/play_frame/play_music 全部静默不炸。"""
    import pygame
    was_init = pygame.mixer.get_init()
    if was_init:
        pygame.mixer.quit()
    try:
        sp = SoundPlayer(DAT)
        sp.ensure_loaded()
        assert not sp._enabled
        sp.play_frame([0, 4, 30], [("music", 0), ("fadeout", 4.0)],
                      ("bgm/th07_02.mid",))
        sp.play_music("th07_02.mid")
        sp.stop_music()
    finally:
        if was_init:
            try:
                pygame.mixer.init()
            except pygame.error:
                pass


def test_sound_player_bad_path_degrades() -> None:
    """dat 路径无效: 加载失败静音降级, 不抛异常。"""
    import pygame
    if not pygame.mixer.get_init():
        try:
            pygame.mixer.init()
        except pygame.error:
            pytest.skip("无声卡")
    sp = SoundPlayer(Path("nonexistent_th07.dat"))
    sp.ensure_loaded()
    assert not sp._enabled
    sp.play_frame([0], [], ())


def test_db_to_gain() -> None:
    assert _db_to_gain(0) == 1.0
    assert _db_to_gain(-2000) == pytest.approx(0.1)
    assert _db_to_gain(-4000) == pytest.approx(0.01)
    assert _db_to_gain(100) == 1.0  # 正向截到满音量


# ---- 主音量(Option 菜单) ----

def test_set_se_volume_scales_individual_volumes() -> None:
    """SE 主音量在各 SE 独立音量(SE_VOLUMES)基础上整体缩放。"""
    class FakeSound:
        def __init__(self):
            self.v = None

        def set_volume(self, v):  # noqa: D102
            self.v = v

    sp = SoundPlayer(DAT)
    fakes = {0: FakeSound(), 4: FakeSound()}
    sp.sounds = fakes
    sp.set_se_volume(0.5)
    assert fakes[0].v == _db_to_gain(SE_VOLUMES[0]) * 0.5
    assert fakes[4].v == _db_to_gain(SE_VOLUMES[4]) * 0.5
    sp.set_se_volume(2.0)  # 截断到 1.0
    assert fakes[0].v == _db_to_gain(SE_VOLUMES[0])


def test_set_bgm_volume_calls_mixer(monkeypatch) -> None:
    """BGM 主音量实时作用于 mixer.music; 未启用时只存系数。"""
    import pygame

    calls = []
    monkeypatch.setattr(pygame.mixer.music, "set_volume",
                        lambda v: calls.append(v))
    sp = SoundPlayer(DAT)
    sp.set_bgm_volume(0.3)          # _enabled=False: 不触 mixer, 只存
    assert calls == []
    assert sp._bgm_volume == 0.3
    sp._enabled = True
    sp.set_bgm_volume(0.7)
    assert calls == [0.7]


def test_bgm_source_validation_and_current_bgm() -> None:
    sp = SoundPlayer(DAT)
    assert sp.current_bgm == ""
    with pytest.raises(ValueError):
        sp.set_bgm_source("ogg")
    sp.set_bgm_source("midi")
    assert sp.bgm_source == "midi"
