"""敌人 VM 宿主生命周期回归 —— sprite_view._enemy_vis / _drain_gone_events。

回归背景(实机截图发现的渲染 bug):
- `_drain_gone_events` 之前读 `game.host.gone_events`, 但事件实际由
  GameEclHost.on_enemy_gone 累积在 `game.ecl_host`(ecl_host.py:234),
  EnemyHost 没有该属性 → drain 恒为空操作:
  1) 击坠爆炸(deathAnm1/2, EnemyManager.cpp:1017-1020)永不触发;
  2) `_enemy_vis`(键 = id(EnemyState))永不回收 → id 复用后新敌人继承旧 VM
     (gid 相同不重启脚本, 跨关时甚至继续画上一关 stgNenm 的 sprite),
     表现为敌人/boss 贴图偶发错误(竖条/错帧/叠影)。
- 换关(_advance_stage)静默重建 host, 旧敌人不经 on_enemy_gone →
  `_ensure_stage` 必须清 `_enemy_vis`。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

sys.path.insert(0, r"D:\python_play\Touhou08")

import pygame  # noqa: E402
import pytest  # noqa: E402

from touhou.engine.view.anm_fx import AnmScriptBank, TransformCache, Vm2d  # noqa: E402
from touhou.engine.view.dialog_view import _FaceBook  # noqa: E402
from touhou.engine.view.sprite_view import (  # noqa: E402
    GameView, GAME_H, GAME_W, SpriteBank, _ANM_OFFSET_ENEMY)
from touhou.schema.anm import AnmFile  # noqa: E402
from touhou.schema.archive import GameArchive  # noqa: E402

DAT = Path(r"D:\TOUHOU_GAME\[th07] 东方妖妖梦 (日文版)\th07.dat")
NEEDS_DAT = pytest.mark.skipif(not DAT.exists(), reason="需要真实 th07.dat")

pygame.init()


def _vis_entry() -> dict:
    return {"vm": None, "gid": -1, "subs": {}, "fx_done": False,
            "intr": 0, "rot_z": 0.0}


# ---- _drain_gone_events: 事件源是 ecl_host ----

@NEEDS_DAT
def test_drain_pops_vis_and_spawns_death_fx() -> None:
    """击坠(life<=0): 弹掉 vis 条目 + 触发 deathAnm1/2 爆炸, 事件清空。"""
    view = GameView(DAT, character=0, stage=1)
    key = 0xDEAD
    view._enemy_vis[key] = _vis_entry()
    game = SimpleNamespace(
        ecl_host=SimpleNamespace(
            gone_events=[(key, 100.0, 120.0, 0, (0, 0, 0), False)]),
        host=SimpleNamespace())  # host 上无 gone_events(旧 bug 读的就是它)
    view._drain_gone_events(game)
    assert key not in view._enemy_vis
    assert game.ecl_host.gone_events == []
    assert len(view._fx.effects) > 0  # deathAnm1=0 爆炸环已生成


@NEEDS_DAT
def test_drain_timeout_leaves_no_fx() -> None:
    """超时/离场(life>0): 弹掉 vis 但不爆炸 (EnemyManager.cpp:1017 口径)。"""
    view = GameView(DAT, character=0, stage=1)
    key = 0xBEEF
    view._enemy_vis[key] = _vis_entry()
    game = SimpleNamespace(
        ecl_host=SimpleNamespace(
            gone_events=[(key, 50.0, 60.0, 100, (0, 0, 0), False)]),
        host=SimpleNamespace())
    view._drain_gone_events(game)
    assert key not in view._enemy_vis
    assert view._fx is None or len(view._fx.effects) == 0


@NEEDS_DAT
def test_drain_without_ecl_host_is_noop() -> None:
    """无 ECL 的演示路径(host 无 gone_events): 不炸不崩。"""
    view = GameView(DAT, character=0, stage=1)
    game = SimpleNamespace(ecl_host=None, host=SimpleNamespace())
    view._drain_gone_events(game)


# ---- 换关清 _enemy_vis ----

@NEEDS_DAT
def test_ensure_stage_clears_enemy_vis() -> None:
    """换关(_advance_stage 静默重建 host, 无 gone 事件)时清掉旧关 VM 条目。"""
    view = GameView(DAT, character=0, stage=1)
    view._ensure_stage(1)
    view._enemy_vis[123] = _vis_entry()
    view._ensure_stage(1)
    assert len(view._enemy_vis) == 1   # 同关不清
    view._ensure_stage(2)
    assert view._enemy_vis == {}


# ---- stg1enm 关键 script → sprite 断言(实机错位 bug 的实体) ----

@NEEDS_DAT
@pytest.mark.parametrize("sid, expected", [
    (0, 0),      # 道中妖精
    (5, 8),      # 道中妖精(第二色)
    (10, 16),    # 出生漩涡
    (12, 46),    # 妖霊条(auto-rotate, 加算)
    (128, 24),   # 中boss 琪露诺
    (132, 30),   # 蕾蒂行走
    (137, 38),   # 蕾蒂攻击
])
def test_stg1enm_script_first_sprite(sid: int, expected: int) -> None:
    """ECL SET_ANM 的 script 首帧 sprite (EclManager.cpp:1196 + 2304;
    SET_ACTIVE_SPRITE 基址 = script 所在 entry 的链式偏移,
    AnmManager.cpp:1684/560)。stg1enm 单 entry, 基址 0。"""
    bank = SpriteBank(DAT)
    sb = AnmScriptBank(bank, "stg1enm.anm", _ANM_OFFSET_ENEMY)
    vm = Vm2d(sb, TransformCache())
    assert vm.start(_ANM_OFFSET_ENEMY + sid)
    assert vm.vm.active_sprite_idx == expected
    assert vm.surf is not None


@NEEDS_DAT
def test_stg1enm_ghost_script_sprite_range() -> None:
    """script 11(幽灵敌) RAND 选 sprite 42..45 —— 脚本内 SET_ACTIVE_SPRITE
    的参数集合必须正好落在幽灵帧区间(竖条 bug 的回归锚点)。"""
    bank = SpriteBank(DAT)
    sb = AnmScriptBank(bank, "stg1enm.anm", _ANM_OFFSET_ENEMY)
    ref = sb.ref_global(_ANM_OFFSET_ENEMY + 11)
    assert ref is not None
    sprites = {ins.args_i[0] for ins in ref.instrs if ins.opcode == 3}
    assert sprites == {42, 43, 44, 45}


@NEEDS_DAT
@pytest.mark.parametrize("seed, expected", [(2, 42), (1, 43), (5, 44)])
def test_stg1enm_ghost_script_execution(seed: int, expected: int) -> None:
    """script 11 实际执行: var=0/1/2 → sprite 42/43/44 (AnmManager.cpp:1684
    SET_ACTIVE_SPRITE 基址 = spriteIndices[anmFileIdx], 单 entry 基址 0)。

    注: var==3 时原始数据不设 sprite(第三路 JNE 跳到 156 的 JUMP, 与 C++
    逐字节一致); 且 reset_and_run 会把 rng 重置为 Random(0) → 固定 var=3,
    幽灵敌当前全部隐身 —— 与 C++ 全局 g_Rng 的差异, 留作后续修。"""
    import random
    from touhou.engine.view.anm_vm import AnmVm
    bank = SpriteBank(DAT)
    sb = AnmScriptBank(bank, "stg1enm.anm", _ANM_OFFSET_ENEMY)
    vm2d = Vm2d(sb, TransformCache())
    ref = sb.ref_global(_ANM_OFFSET_ENEMY + 11)
    vm = vm2d.vm
    vm.__init__()
    vm.rng = random.Random(seed)     # __init__ 之后播种(C++ 用全局 g_Rng)
    vm._set_sprite_cb = vm2d._set_sprite
    vm.script = ref
    vm.pc = 0
    vm.time = 0
    vm.visible = False
    vm.execute()
    assert vm.active_sprite_idx == expected
    assert vm2d.surf is not None


@NEEDS_DAT
def test_effect_gids_resolve() -> None:
    """EFFECT_TABLE 的全部全局 script id 在 etama 脚本表(0x200 基)可解析
    (链式偏移: entry1 在 168, AnmManager.cpp:430)。"""
    from touhou.engine.view.anm_fx import EFFECT_TABLE
    bank = SpriteBank(DAT)
    sb = AnmScriptBank(bank, "etama.anm", 0x200)
    for effect_id, (gid, _kind) in EFFECT_TABLE.items():
        assert sb.ref_global(gid) is not None, f"effect {effect_id} gid {gid:#x}"


# ---- 对话立绘 face 映射(msg1 实际用到的 face 号) ----

@NEEDS_DAT
def test_face_book_chain_keys() -> None:
    """face_rm00: entry0 脸 0-4 + entry1 脸 8-12(链式偏移 = max(sprite,script)+1
    = 8, LoadAnms AnmManager.cpp:430/564); msg1 用的 0,1,2,3,8,9 全部命中且
    entry1 的脸不是 entry0 的回落。"""
    arc = GameArchive.open(DAT)
    raw = arc.load("face_rm00.anm")
    book = _FaceBook(AnmFile.parse(raw), raw)
    for k in (0, 1, 2, 3, 4, 8, 9, 10, 11, 12):
        assert k in book._faces
    assert book._faces[8] is not book._faces[0]  # entry1 独立表情, 不回落


# ---- 集成: 渲染全程 _enemy_vis 与存活敌人一致(无泄漏/无 id 复用串图) ----

@NEEDS_DAT
def test_stage1_render_vis_matches_alive() -> None:
    """渲染 stage 1 前 1500 帧: 每帧 render 后 _enemy_vis 键集 ==
    存活且 anm_idx>=0 的敌人 id 集(旧 bug: 条目永不回收, 228 次 id 复用命中)。"""
    from touhou.games.th07.world import PerfectCherryBloom
    from touhou.utils import Vec2
    g = PerfectCherryBloom(data_path=DAT, character=0, difficulty=1)
    g.stage_no = 1
    g._load_ecl()
    view = GameView(DAT, character=0, stage=1)
    surf = pygame.Surface((GAME_W, GAME_H))

    def keys(f):
        return ((f // 40) % 2 == 1, (f // 40) % 2 == 0, False, False, False)

    while g.frame < 1500:
        g.tick(keys=keys(g.frame), advance=(g.frame % 15 == 0))
        bosses = [e for e in g.host.all()
                  if getattr(e, "is_boss", False) and e.alive]
        if bosses:
            lead = min(bosses, key=lambda e: max(e.state.life, 0))
            g.player.pos = Vec2(lead.pos.x, min(lead.pos.y + 200, 400))
        if g.game_over:
            g.game_over = False
            g.result = None
            g.lives = 3.0
        view.render(surf, g)
        if g.frame % 100 == 0:
            want = {id(e.state) for e in g.host.alive()
                    if e.state.anm_idx >= 0}
            assert set(view._enemy_vis) == want
