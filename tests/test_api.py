"""公共 API(touhou/apis/basic.py)门面行为 + 打包/import 隔离测试。"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from touhou.apis.basic import (
    Difficulty,
    Game,
    GameEvent,
    GameEventKind,
    GamePhase,
    Input,
    ShotType,
)
from touhou.paths import DEFAULT_DATA, ENV_DATA, resolve_data_path

pytestmark = pytest.mark.skipif(not DEFAULT_DATA.exists(),
                                reason="需要真实 th07.dat")


def _run(game: Game, frames: int, inp: Input = Input.none()) -> list[GameEvent]:
    out: list[GameEvent] = []
    for _ in range(frames):
        out += game.step(inp)
    return out


# ---- 枚举 / 输入 ----
def test_enums_map_internal_ints() -> None:
    assert [int(s) for s in ShotType] == [0, 1, 2, 3, 4, 5]
    assert [int(d) for d in Difficulty] == [0, 1, 2, 3, 4, 5]
    assert ShotType.REIMU_A.name == "REIMU_A"
    assert Difficulty.PHANTASM.value == 5


def test_input_none_and_keys() -> None:
    assert Input.none() == Input()
    inp = Input(left=True, shoot=True, focus=True)
    assert inp._keys() == (True, False, False, False, True, True)


# ---- 开局 / step / 属性 ----
def test_game_start_and_step() -> None:
    game = Game(character=ShotType.REIMU_A, difficulty=Difficulty.NORMAL, seed=1)
    assert game.frame == 0
    assert game.phase == GamePhase.RUNNING
    assert game.lives == 3 and game.power == 0 and game.score == 0
    events = game.step(Input(shoot=True))
    assert game.frame == 1
    assert isinstance(events, list)


def test_properties_are_readonly() -> None:
    game = Game()
    for prop in ("score", "lives", "bombs", "power", "cherry", "graze",
                 "frame", "phase", "stage", "result"):
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
        capture_output=True, text=True, timeout=120,
        cwd=Path(__file__).resolve().parent.parent)


def test_top_level_exports_complete() -> None:
    # `import touhou` 急切导出全部公共类型(无函数内 import/惰性层)
    code = (
        "import touhou; "
        "names = ['Game', 'Input', 'ShotType', 'Character', 'Difficulty', "
        "'GamePhase', 'GameEvent', 'GameEventKind', 'Snapshot', 'WorldData', "
        "'TouhouWorld', 'TouhouWorldEventStream']; "
        "missing = [n for n in names if not hasattr(touhou, n)]; "
        "assert not missing, missing; "
        "assert touhou.__all__ and isinstance(touhou.__version__, str); "
        "print('ok')")
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
        files += [f for f in sub.rglob("*.py")
                  if "view" not in f.parts and "__pycache__" not in f.parts]
    for f in files:
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not [a.name for a in node.names
                            if a.name.split(".")[0] == "pygame"], f
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] != "pygame", f


def _type_checking_node_ids(tree: ast.AST) -> set[int]:
    """``if TYPE_CHECKING:`` 块内全部节点的 id 集合(仅 mypy 可见的豁免区)。"""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        t = node.test
        if (isinstance(t, ast.Name) and t.id == "TYPE_CHECKING") or \
                (isinstance(t, ast.Attribute) and t.attr == "TYPE_CHECKING"):
            ids.update(id(s) for s in ast.walk(node))
    return ids


def test_apis_and_engine_do_not_import_games() -> None:
    # 架构铁律: 顶层框架(apis)与 engine 不 import games.*(反向依赖已清零,
    # AST 级守住; 注释/docstring 提及不算)。唯一豁免: engine/render 在
    # TYPE_CHECKING 下引用 games.th07.view.screens 的菜单流类型(仅 mypy
    # 可见, 运行时不产生依赖, 见该模块注释)
    pkg = Path(__file__).resolve().parent.parent / "touhou"
    offenders = []
    for sub in (pkg / "apis", pkg / "engine"):
        for f in sub.rglob("*.py"):
            if "__pycache__" in f.parts:
                continue
            tree = ast.parse(f.read_text(encoding="utf-8"))
            exempt = _type_checking_node_ids(tree)
            for node in ast.walk(tree):
                if id(node) in exempt:
                    continue
                if isinstance(node, ast.Import):
                    hit = any(a.name == "touhou.games"
                              or a.name.startswith("touhou.games.")
                              for a in node.names)
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


def test_character_is_shottype_alias() -> None:
    from touhou.apis.basic import Character
    assert Character is ShotType
    assert Character.REIMU_A.value == 0


def test_touhou_world_headless_stream() -> None:
    from touhou.apis.basic import TouhouWorld, WorldData

    tw = TouhouWorld(wd=WorldData(res_dat=DEFAULT_DATA),
                     character=ShotType.REIMU_A, difficulty=Difficulty.NORMAL,
                     lives=3, headless=True, seed=7)
    assert tw.game.lives == 3
    events = []
    for ev in tw.events:             # 流式事件(终局自动收尾: GameOver→结算)
        events.append(ev)
        if tw.game.frame >= 7000:    # 有界截断: 不跑完整关
            break
    assert tw.game.frame > 600       # 世界确实在推进
    assert all(isinstance(e, GameEvent) for e in events)
    if tw.game.phase == GamePhase.RESULT:
        assert tw.game.result is not None   # 提前 GameOver 也已自动进结算


def test_touhou_world_custom_policy() -> None:
    from touhou.apis.basic import TouhouWorld

    tw = TouhouWorld(headless=True, difficulty=Difficulty.NORMAL, seed=1)
    seen = []
    for i, ev in enumerate(tw.stream(policy=lambda g: Input.none())):
        seen.append(ev)
        if i > 50 or tw.game.frame >= 300:
            break
    assert tw.game.frame >= 300     # policy 驱动的帧数在走


def test_touhou_world_lazy_game() -> None:
    from touhou.apis.basic import TouhouWorld

    tw = TouhouWorld(headless=False)   # 非 headless 不预建对局
    assert tw._game is None
    g = tw.game                        # 首次访问才建
    assert g is tw.game


def test_touhou_world_run_returns_event_stream() -> None:
    from touhou.apis.basic import TouhouWorld, TouhouWorldEventStream

    tw = TouhouWorld(headless=True, difficulty=Difficulty.NORMAL, seed=3)
    stream = tw.run()
    assert isinstance(stream, TouhouWorldEventStream)
    assert stream.game is tw.game          # 流驱动的就是这局
    n = 0
    for ev in stream:
        assert isinstance(ev, GameEvent)
        n += 1
        if tw.game.frame >= 800:
            break                          # 有界截断
    assert tw.game.frame >= 600            # 迭代在驱动世界推进


def test_event_stream_policy_takeover() -> None:
    from touhou.apis.basic import TouhouWorld

    tw = TouhouWorld(headless=True, difficulty=Difficulty.NORMAL, seed=5)
    stream = tw.run()
    stream.policy = lambda g: Input.none()   # 中途接管输入
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
        "    print(ev.kind)\n", encoding="utf-8")
    err_src = tmp_path / "err.py"
    err_src.write_text(
        "from touhou import TouhouWorld\n"
        "tw = TouhouWorld(headless=False)\n"
        "for ev in tw.run():\n"
        "    print(ev.kind)\n", encoding="utf-8")
    root = Path(__file__).resolve().parent.parent
    r_ok = subprocess.run(
        [sys.executable, "-m", "mypy", str(ok_src)],
        capture_output=True, text=True, timeout=300, cwd=root)
    assert "no issues found" in r_ok.stdout, r_ok.stdout + r_ok.stderr
    r_err = subprocess.run(
        [sys.executable, "-m", "mypy", str(err_src)],
        capture_output=True, text=True, timeout=300, cwd=root)
    assert "not iterable" in r_err.stdout or "has no attribute" in r_err.stdout, \
        r_err.stdout + r_err.stderr


def test_environment_detection() -> None:
    """启动环境探测: 返回字段齐全, 探测失败不炸。"""
    from touhou.env import detect_environment

    info = detect_environment()
    for key in ("python", "platform", "pygame", "res_dat", "res_entries",
                "bgm_dat", "games", "renderers", "title"):
        assert key in info and isinstance(info[key], str)
    assert info["title"] == "東方妖々夢 〜 Perfect Cherry Blossom"
    assert "th07" in info["games"]
    # 坏路径不炸
    bad = detect_environment(data_path="/nonexistent/th07.dat")
    assert bad["res_entries"] == "未找到"
