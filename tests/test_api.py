"""公共 API(touhou/apis/basic.py)门面行为 + 打包/import 隔离测试。"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from touhou.apis.basic import (
    Game,
    GameEvent,
    GameEventKind,
    GamePhase,
    Input,
)
from touhou.paths import DEFAULT_DATA, ENV_DATA, resolve_data_path

pytestmark = pytest.mark.skipif(not DEFAULT_DATA.exists(), reason="需要真实 th07.dat")


def _run(game: Game, frames: int, inp: Input = Input.none()) -> list[GameEvent]:
    out: list[GameEvent] = []
    for _ in range(frames):
        out += game.step(inp)
    return out


# ---- 名单字符串映射 / 输入 ----
def test_character_difficulty_names_map_to_internal_ids() -> None:
    """character/difficulty 用作品数值表名单的字符串, 映射为内部 int id。"""
    game = Game(character="ReimuA", difficulty="Normal", seed=1)
    assert game._impl.character == 0 and game._impl.difficulty == 1
    game = Game(character="SakuyaB", difficulty="Lunatic", seed=1)
    assert game._impl.character == 5 and game._impl.difficulty == 3
    # 大小写不敏感: "lunatic"="Lunatic", "reimua"="ReimuA"
    game = Game(character="reimua", difficulty="lunatic", seed=1)
    assert game._impl.character == 0 and game._impl.difficulty == 3
    # 非法名 → 清晰中文 ValueError
    with pytest.raises(ValueError, match="不支持角色"):
        Game(character="Cirno")
    with pytest.raises(ValueError, match="不支持难度"):
        Game(difficulty="infinity")


def test_input_none_and_keys() -> None:
    assert Input.none() == Input()
    inp = Input(left=True, shoot=True, focus=True)
    assert inp._keys() == (True, False, False, False, True, True)


# ---- 开局 / step / 属性 ----
def test_game_start_and_step() -> None:
    game = Game(character="ReimuA", difficulty="Normal", seed=1)
    assert game.frame == 0
    assert game.phase == GamePhase.RUNNING
    assert game.lives == 3 and game.power == 0 and game.score == 0
    events = game.step(Input(shoot=True))
    assert game.frame == 1
    assert isinstance(events, list)


def test_properties_are_readonly() -> None:
    game = Game()
    for prop in (
        "score",
        "lives",
        "bombs",
        "power",
        "graze",
        "frame",
        "phase",
        "stage",
        "result",
    ):
        with pytest.raises(AttributeError):
            setattr(game, prop, 0)


def test_dialog_phase_detected() -> None:
    # stage1 对话(msg)在关卡时间轴上出现; 推进期间保持 DIALOG 相位
    game = Game(seed=1)
    game._impl.globals.lives_remaining = 99  # 站桩防 GameOver 干扰
    for _ in range(6500):
        game.step(Input(shoot=True))
        if game.phase == GamePhase.DIALOG:
            return
    pytest.fail("6500 帧内未检测到 DIALOG 相位")


# ---- 事件映射(演示 Boss 路径, 同引擎测试的用法) ----
def test_spellcard_events() -> None:
    game = Game(seed=1)
    _run(game, 200, Input(shoot=True))
    game._impl._spawn_demo_boss()
    events = game.step(Input(shoot=True))
    kinds = [e.kind for e in events]
    assert GameEventKind.SPELLCARD_BEGIN in kinds
    name = next(e.name for e in events if e.kind == GameEventKind.SPELLCARD_BEGIN)
    # 击破(未用 Bomb/未死亡 → 捕获)
    game._impl.boss.life = 0
    events = game.step(Input(shoot=True))
    captured = [e for e in events if e.kind == GameEventKind.SPELLCARD_CAPTURED]
    assert captured and captured[0].name == name


def test_bomb_and_extend_events() -> None:
    game = Game(seed=1)
    _run(game, 200, Input(shoot=True))  # 等出生无敌结束
    events = game.step(Input(bomb=True))
    assert GameEventKind.BOMB_START in [e.kind for e in events]
    # 残机增加 → EXTEND(引擎侧只有奖残会让残机变多)
    game._impl.globals.lives_remaining += 1
    events = game.step()
    assert GameEventKind.EXTEND in [e.kind for e in events]


def test_death_and_game_over_events() -> None:
    game = Game(seed=1)
    _run(game, 200, Input(shoot=True))
    game._impl.globals.lives_remaining = 0
    game._impl.player.die()
    events = _run(game, 600)
    kinds = [e.kind for e in events]
    assert GameEventKind.PLAYER_DEATH in kinds
    assert GameEventKind.GAME_OVER in kinds
    # Extra/Phantasm 以外的难度无残机 → 续关可用(冻结), phase=GAME_OVER
    assert game.phase in (GamePhase.GAME_OVER, GamePhase.RESULT)


# ---- 作品专属事件(th07 结界)经 EventBus 汇入 step 事件流 ----
def test_border_events_via_event_bus() -> None:
    from touhou.engine.player_base import PlayerState

    game = Game(seed=1)
    _run(game, 200, Input(shoot=True))  # 出生无敌结束, 玩家 ALIVE
    game._impl.player.state = PlayerState.ALIVE  # 出生无敌态不参与结界激活择时
    # 满樱信号 → 结界 READY(等价 mods 拉满 cherryPlus 的引擎入口)
    game._impl.border.ready_border()
    events = game.step(Input(shoot=True))  # 帧内自动激活 → border_start
    kinds = [e.kind for e in events]
    assert "border_start" in kinds
    ev = next(e for e in events if e.kind == "border_start")
    assert ev.frame == game.frame
    # 主动破(bomb 键同入口) → 下一次 step 收到 border_break
    game._impl._break_border(by_bomb_key=True)
    events = game.step(Input(shoot=True))
    kinds = [e.kind for e in events]
    assert "border_break" in kinds
    # READY 未激活时的破(死亡保命路径)不发事件(事件语义 = ACTIVE→破)
    game._impl.border.ready_border()
    game._impl._break_border()  # was_active=False → 不发布
    events = game.step(Input(shoot=True))
    assert "border_break" not in [e.kind for e in events]


# ---- 快照 ----
def test_snapshot_contents() -> None:
    game = Game(seed=1)
    _run(game, 300, Input(shoot=True))
    snap = game.snapshot()
    assert snap.frame == game.frame
    assert snap.player.state == "alive"
    assert 0 <= snap.player.x <= 384 and 0 <= snap.player.y <= 448
    for b in snap.bullets:
        assert isinstance(b.sprite, int)
    for e in snap.enemies:
        assert e.life >= 0 and e.radius >= 0
    for i in snap.items:
        assert i.type.isupper()
    # 快照不可变
    with pytest.raises(AttributeError):
        snap.player.x = 0  # type: ignore[misc]


def test_snapshot_after_boss() -> None:
    game = Game(seed=1)
    _run(game, 200, Input(shoot=True))
    game._impl._spawn_demo_boss()
    game.step()
    snap = game.snapshot()
    assert snap.boss is not None and snap.boss.spellcard_active
    assert snap.boss.max_life == 600


# ---- 资源路径解析 ----
def test_resolve_data_path_priority(tmp_path, monkeypatch) -> None:
    explicit = tmp_path / "explicit.dat"
    env = tmp_path / "env.dat"
    # 显式参数 > 环境变量 > 默认
    monkeypatch.setenv(ENV_DATA, str(env))
    assert resolve_data_path(explicit) == explicit
    assert resolve_data_path() == env
    monkeypatch.delenv(ENV_DATA)
    assert resolve_data_path() == DEFAULT_DATA


def test_env_var_drives_game(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(ENV_DATA, str(tmp_path / "nonexistent.dat"))
    with pytest.raises(OSError):
        Game()  # 环境变量指向不存在路径 → 开包失败
    monkeypatch.setenv(ENV_DATA, str(DEFAULT_DATA))
    assert Game().frame == 0  # 指回真实数据则正常


# ---- 打包形态(急切导出; 函数内 import 已禁止) ----
def _run_blocking_imports(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=Path(__file__).resolve().parent.parent,
    )


def test_top_level_exports_complete() -> None:
    # `import touhou` 急切导出全部公共类型(无函数内 import/惰性层)
    code = (
        "import touhou; "
        "names = ['Game', 'Input', "
        "'GamePhase', 'GameEvent', 'GameEventKind', 'Snapshot', 'WorldData', "
        "'TouhouWorld', 'TouhouWorldEventStream']; "
        "missing = [n for n in names if not hasattr(touhou, n)]; "
        "assert not missing, missing; "
        "assert touhou.__all__ and isinstance(touhou.__version__, str); "
        "print('ok')"
    )
    r = _run_blocking_imports(code)
    assert r.returncode == 0, r.stderr
    assert "ok" in r.stdout


def test_no_function_level_imports_in_package() -> None:
    # 包代码禁止函数内 import(用户规约; 测试目录豁免)
    pkg = Path(__file__).resolve().parent.parent / "touhou"
    offenders = []
    for f in pkg.rglob("*.py"):
        if "test" in f.parts:
            continue
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for sub in ast.walk(node):
                    if isinstance(sub, (ast.Import, ast.ImportFrom)):
                        offenders.append(f"{f}:{sub.lineno}")
    assert not offenders, offenders


def test_logic_layer_has_no_pygame_dependency() -> None:
    # engine(非 view)/schema/games/logger 不得 import pygame(AST 级检查;
    # 注释/docstring 提及不算)
    pkg = Path(__file__).resolve().parent.parent / "touhou"
    files = [*pkg.glob("*.py")]
    for sub in (pkg / "engine", pkg / "schema", pkg / "games"):
        files += [
            f
            for f in sub.rglob("*.py")
            if "view" not in f.parts and "__pycache__" not in f.parts
        ]
    for f in files:
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not [
                    a.name for a in node.names if a.name.split(".")[0] == "pygame"
                ], f
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] != "pygame", f


def _type_checking_node_ids(tree: ast.AST) -> set[int]:
    """``if TYPE_CHECKING:`` 块内全部节点的 id 集合(仅 mypy 可见的豁免区)。"""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        t = node.test
        if (isinstance(t, ast.Name) and t.id == "TYPE_CHECKING") or (
            isinstance(t, ast.Attribute) and t.attr == "TYPE_CHECKING"
        ):
            ids.update(id(s) for s in ast.walk(node))
    return ids


def test_apis_and_engine_do_not_import_games() -> None:
    # 架构铁律: 顶层框架(apis)与 engine、utils 不 import games.*(反向依赖已清零,
    # AST 级守住; 注释/docstring 提及不算)。唯一豁免: engine/render 在
    # TYPE_CHECKING 下引用 games.th07.view.screens 的菜单流类型(仅 mypy
    # 可见, 运行时不产生依赖, 见该模块注释)
    pkg = Path(__file__).resolve().parent.parent / "touhou"
    offenders = []
    for sub in (pkg / "apis", pkg / "engine", pkg / "utils"):
        for f in sub.rglob("*.py"):
            if "__pycache__" in f.parts:
                continue
            tree = ast.parse(f.read_text(encoding="utf-8"))
            exempt = _type_checking_node_ids(tree)
            for node in ast.walk(tree):
                if id(node) in exempt:
                    continue
                if isinstance(node, ast.Import):
                    hit = any(
                        a.name == "touhou.games" or a.name.startswith("touhou.games.")
                        for a in node.names
                    )
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    if node.level == 0:
                        hit = mod == "touhou.games" or mod.startswith("touhou.games.")
                    else:
                        # 相对 import: apis/engine 内的模块离 touhou 包 ≥2 层,
                        # "..games.…"/"...games.…" 均指向 touhou.games
                        hit = mod == "games" or mod.startswith("games.")
                else:
                    continue
                if hit:
                    offenders.append(f"{f}:{node.lineno}")
    assert not offenders, offenders


# ---- TouhouWorld / WorldData 入口 ----
def test_world_data_resolution(tmp_path) -> None:
    from touhou.apis.basic import WorldData

    wd = WorldData()
    assert wd.resolve_res() is None and wd.resolve_bgm() is None
    wd2 = WorldData(res_dat=tmp_path / "th07.dat", bgm_dat=tmp_path / "bgm.dat")
    assert wd2.resolve_res() == tmp_path / "th07.dat"
    assert wd2.resolve_bgm() == tmp_path / "bgm.dat"
    assert WorldData(res_dat="x.dat").resolve_res() == Path("x.dat")


def test_touhou_world_headless_stream() -> None:
    from touhou.apis.basic import TouhouWorld, WorldData

    tw = TouhouWorld(
        wd=WorldData(res_dat=DEFAULT_DATA),
        character="ReimuA",
        difficulty="Normal",
        lives=3,
        headless=True,
        seed=7,
    )
    assert tw.game.lives == 3
    events = []
    for ev in tw.events:  # 流式事件(终局自动收尾: GameOver→结算)
        events.append(ev)
        if tw.game.frame >= 7000:  # 有界截断: 不跑完整关
            break
    assert tw.game.frame > 600  # 世界确实在推进
    assert all(isinstance(e, GameEvent) for e in events)
    if tw.game.phase == GamePhase.RESULT:
        assert tw.game.result is not None  # 提前 GameOver 也已自动进结算


def test_touhou_world_custom_policy() -> None:
    from touhou.apis.basic import TouhouWorld

    tw = TouhouWorld(headless=True, difficulty="Normal", seed=1)
    seen = []
    for i, ev in enumerate(tw.stream(policy=lambda g: Input.none())):
        seen.append(ev)
        if i > 50 or tw.game.frame >= 300:
            break
    assert tw.game.frame >= 300  # policy 驱动的帧数在走


def test_touhou_world_lazy_game() -> None:
    from touhou.apis.basic import TouhouWorld

    tw = TouhouWorld(headless=False)  # 非 headless 不预建对局
    assert tw._game is None
    g = tw.game  # 首次访问才建
    assert g is tw.game


def test_touhou_world_run_returns_event_stream() -> None:
    from touhou.apis.basic import TouhouWorld, TouhouWorldEventStream

    tw = TouhouWorld(headless=True, difficulty="Normal", seed=3)
    stream = tw.run()
    assert isinstance(stream, TouhouWorldEventStream)
    assert stream.game is tw.game  # 流驱动的就是这局
    n = 0
    for ev in stream:
        assert isinstance(ev, GameEvent)
        n += 1
        if tw.game.frame >= 800:
            break  # 有界截断
    assert tw.game.frame >= 600  # 迭代在驱动世界推进


def test_event_stream_policy_takeover() -> None:
    from touhou.apis.basic import TouhouWorld

    tw = TouhouWorld(headless=True, difficulty="Normal", seed=5)
    stream = tw.run()
    stream.policy = lambda g: Input.none()  # 中途接管输入
    frames0 = tw.game.frame
    for _ in stream:
        if tw.game.frame > frames0 + 120:
            break
    assert tw.game.frame > frames0 + 100


def test_touhou_world_run_typing_narrows_on_headless(tmp_path) -> None:
    """run() 的返回类型随 headless 字面量收窄(overload):
    headless=True → TouhouWorldEventStream(可直接迭代);
    headless=False → None(迭代应被 mypy 拒绝)。"""
    ok_src = tmp_path / "ok.py"
    ok_src.write_text(
        "from touhou import TouhouWorld\n"
        "tw = TouhouWorld(headless=True)\n"
        "for ev in tw.run():\n"
        "    print(ev.kind)\n",
        encoding="utf-8",
    )
    err_src = tmp_path / "err.py"
    err_src.write_text(
        "from touhou import TouhouWorld\n"
        "tw = TouhouWorld(headless=False)\n"
        "for ev in tw.run():\n"
        "    print(ev.kind)\n",
        encoding="utf-8",
    )
    root = Path(__file__).resolve().parent.parent
    r_ok = subprocess.run(
        [sys.executable, "-m", "mypy", str(ok_src)],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=root,
    )
    assert "no issues found" in r_ok.stdout, r_ok.stdout + r_ok.stderr
    r_err = subprocess.run(
        [sys.executable, "-m", "mypy", str(err_src)],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=root,
    )
    assert "not iterable" in r_err.stdout or "has no attribute" in r_err.stdout, (
        r_err.stdout + r_err.stderr
    )


def test_environment_detection() -> None:
    """启动环境探测: 返回字段齐全, 探测失败不炸。"""
    from touhou.env import detect_environment

    info = detect_environment()
    for key in (
        "python",
        "platform",
        "pygame",
        "res_dat",
        "res_entries",
        "bgm_dat",
        "games",
        "renderers",
        "title",
    ):
        assert key in info and isinstance(info[key], str)
    assert info["title"] == "東方妖々夢 〜 Perfect Cherry Blossom"
    assert "th07" in info["games"]
    # 坏路径不炸
    bad = detect_environment(data_path="/nonexistent/th07.dat")
    assert bad["res_entries"] == "未找到"


# ---- 判定半径观测面 + numpy 快路径 ----
def test_snapshot_hitbox_matches_engine() -> None:
    from touhou.utils import Vec2

    game = Game(seed=1)
    _run(game, 200, Input(shoot=True))
    game._impl.bullets.spawn_demo_wave(Vec2(192, 100))  # 确定性造弹(环+扇)
    game.step(Input(shoot=True))
    snap = game.snapshot()
    assert snap.bullets
    r = game._impl.bullets.bullet_radius
    for b in snap.bullets:
        # 快照判定半径与引擎实际判定半宽(均匀 AABB 盒)同源
        assert b.hitbox == r
    # 已知弹型样本: demo wave 用 sprite=0(小弹), 判定半径同为世界半宽
    assert any(b.sprite == 0 and b.hitbox == r for b in snap.bullets)
    # 自机判定半宽: 作品常量(th07 约 1~2px), 与引擎玩家实例一致
    assert snap.player.hitbox == game._impl.player.hitbox_radius
    assert 0.0 < snap.player.hitbox <= 4.0


def test_bullets_array_matches_snapshot() -> None:
    import math

    from touhou.utils import Vec2

    game = Game(seed=1)
    _run(game, 200, Input(shoot=True))
    game._impl.bullets.spawn_demo_wave(Vec2(192, 100))
    arr = game.bullets_array()
    snap = game.snapshot()
    assert arr.shape == (len(snap.bullets), 6)
    for row, b in zip(arr, snap.bullets):
        assert (row[0], row[1]) == (b.x, b.y)
        # vx/vy: angle/speed 按屏幕系(y 向下)换算的速度向量
        assert row[2] == pytest.approx(b.speed * math.cos(b.angle))
        assert row[3] == pytest.approx(b.speed * math.sin(b.angle))
        assert row[4] == b.hitbox
        assert row[5] == b.sprite
    # 标量自机坐标口与快照一致
    assert game.player_pos == (snap.player.x, snap.player.y)


def test_bullets_array_empty_and_lasers_array() -> None:
    game = Game(seed=1)
    game._impl.bullets.clear()
    assert game.bullets_array().shape == (0, 6)  # 空场形状正确
    la = game.lasers_array()
    assert la.ndim == 2 and la.shape[1] == 5  # 开局无激光 → (0, 5)


def test_player_hitbox_capability_fallback() -> None:
    """玩家对象不带 hitbox_radius 的作品(协议外能力位) → snapshot 得 None。"""
    from types import SimpleNamespace

    from touhou.registry import get_game
    from touhou.utils import Vec2

    impl = SimpleNamespace(
        frame=0,
        stage_no=1,
        lives=3.0,
        game_over=False,
        cleared=False,
        result=None,
        stage_results=None,
        ending=None,
        boss=None,
        globals=SimpleNamespace(
            deaths=0, bombs_used=0.0, spell_cards_captured=0, score=0
        ),
        player=SimpleNamespace(
            pos=Vec2(192, 400),
            state=SimpleNamespace(name="ALIVE"),
            focus=False,
            invulnerability_timer=0,
        ),
        bullets=SimpleNamespace(alive=list),
        host=SimpleNamespace(alive=list),
        items=SimpleNamespace(alive=list),
        lasers=SimpleNamespace(lasers=[]),
    )
    game = Game._from_impl(impl, get_game("th07"), "stub")
    snap = game.snapshot()
    assert snap.player.hitbox is None
    assert game.bullets_array().shape == (0, 6)
