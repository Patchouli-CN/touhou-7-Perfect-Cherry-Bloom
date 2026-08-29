"""公共 API(touhou/apis/basic.py)门面行为 + 打包/import 隔离测试。

通用层: 只用假作品 "test00"(tests/conftest.py 注册的最小桩对局)验证
门面契约, 不 import games.*(本文件尾部的 AST 守护钉死); th07 真实引擎
行为(对话相位/符卡事件/结界总线/demo 弹幕)见
game_test/th07/test_th07_api.py。
"""

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

from tests.conftest import FAKE_GAME


def _fake(**kw) -> Game:
    """假作品对局门面(默认 TestA/Normal; 无需任何游戏资源)。"""
    kw.setdefault("game", FAKE_GAME)
    kw.setdefault("character", "TestA")
    kw.setdefault("difficulty", "Normal")
    return Game(**kw)


def _run(game: Game, frames: int, inp: Input = Input.none()) -> list[GameEvent]:
    out: list[GameEvent] = []
    for _ in range(frames):
        out += game.step(inp)
    return out


# ---- 名单字符串映射 / 输入 ----
def test_character_difficulty_names_map_to_internal_ids() -> None:
    """character/difficulty 用作品数值表名单的字符串, 映射为内部 int id。"""
    game = _fake(character="TestA", difficulty="Normal", seed=1)
    assert game._impl.character == 0 and game._impl.difficulty == 1
    game = _fake(character="TestB", difficulty="Hard", seed=1)
    assert game._impl.character == 1 and game._impl.difficulty == 2
    # 大小写不敏感: "hard"="Hard", "testa"="TestA"
    game = _fake(character="testa", difficulty="hard", seed=1)
    assert game._impl.character == 0 and game._impl.difficulty == 2
    # 非法名 → 清晰中文 ValueError
    with pytest.raises(ValueError, match="不支持角色"):
        _fake(character="Cirno")
    with pytest.raises(ValueError, match="不支持难度"):
        _fake(difficulty="infinity")


def test_input_none_and_keys() -> None:
    assert Input.none() == Input()
    inp = Input(left=True, shoot=True, focus=True)
    assert inp._keys() == (True, False, False, False, True, True)


# ---- 开局 / step / 属性 ----
def test_game_start_and_step() -> None:
    game = _fake(seed=1)
    assert game.frame == 0
    assert game.phase == GamePhase.RUNNING
    assert game.lives == 3 and game.power == 0 and game.score == 0
    events = game.step(Input(shoot=True))
    assert game.frame == 1
    assert isinstance(events, list)


def test_properties_are_readonly() -> None:
    game = _fake()
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


# ---- 通用状态差事件(diff 契约, 与作品机制无关) ----
def test_bomb_and_extend_events() -> None:
    game = _fake(seed=1)
    events = game.step(Input(bomb=True))  # bomb 键 → 桩对局记账 bombs_used
    assert GameEventKind.BOMB_START in [e.kind for e in events]
    # 残机增加 → EXTEND(差分语义: 残机变多即奖残)
    game._impl.lives += 1
    events = game.step()
    assert GameEventKind.EXTEND in [e.kind for e in events]


def test_death_and_game_over_events() -> None:
    game = _fake(seed=1)
    game._impl.globals.deaths = 1  # 帧间状态差(测试直改 impl 制造)
    events = game.step()
    assert GameEventKind.PLAYER_DEATH in [e.kind for e in events]
    game._impl.game_over = True
    events = game.step()
    assert GameEventKind.GAME_OVER in [e.kind for e in events]
    assert game.phase == GamePhase.GAME_OVER
    game.finalize_game_over()  # 不续关 → 总结算
    assert game.phase == GamePhase.RESULT and game.result is not None


def test_ending_events() -> None:
    game = _fake(seed=1)
    game._impl.ending = object()  # 结局开始(桩对象占位)
    events = game.step()
    assert GameEventKind.ENDING_START in [e.kind for e in events]
    assert game.phase == GamePhase.ENDING
    game.finish_ending()  # 结局看完 → 总结算
    assert game.phase == GamePhase.RESULT


# ---- 快照 ----
def test_snapshot_contents() -> None:
    game = _fake(seed=1)
    _run(game, 10, Input(shoot=True))
    snap = game.snapshot()
    assert snap.frame == game.frame
    assert snap.player.state == "alive"
    assert 0 <= snap.player.x <= 384 and 0 <= snap.player.y <= 448
    assert snap.boss is None  # 桩对局无 Boss
    assert snap.bullets == () and snap.enemies == () and snap.items == ()
    # 快照不可变
    with pytest.raises(AttributeError):
        snap.player.x = 0  # type: ignore[misc]


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


def test_root_tests_do_not_import_games() -> None:
    # 测试目录分层铁律: tests/ 根下的 test_*.py 是通用层测试(只用假作品
    # "test00"), 禁止 import games.*; 作品专属测试住 tests/game_test/
    # 子树(该子树整体豁免, 不在本检查范围)
    root = Path(__file__).resolve().parent
    offenders = []
    for f in sorted(root.glob("test_*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                hit = any(
                    a.name == "touhou.games" or a.name.startswith("touhou.games.")
                    for a in node.names
                )
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                hit = node.level == 0 and (
                    mod == "touhou.games" or mod.startswith("touhou.games.")
                )
            else:
                continue
            if hit:
                offenders.append(f"{f.name}:{node.lineno}")
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


def _fake_world(**kw):
    from touhou.apis.basic import TouhouWorld

    kw.setdefault("game", FAKE_GAME)
    kw.setdefault("character", "TestA")
    return TouhouWorld(**kw)


def test_touhou_world_headless_stream() -> None:
    tw = _fake_world(difficulty="Normal", lives=3, headless=True, seed=7)
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
    tw = _fake_world(headless=True, difficulty="Normal", seed=1)
    seen = []
    for i, ev in enumerate(tw.stream(policy=lambda g: Input.none())):
        seen.append(ev)
        if i > 50 or tw.game.frame >= 300:
            break
    assert tw.game.frame >= 300  # policy 驱动的帧数在走


def test_touhou_world_lazy_game() -> None:
    tw = _fake_world(headless=False)  # 非 headless 不预建对局
    assert tw._game is None
    g = tw.game  # 首次访问才建
    assert g is tw.game


def test_touhou_world_run_returns_event_stream() -> None:
    from touhou.apis.basic import TouhouWorldEventStream

    tw = _fake_world(headless=True, difficulty="Normal", seed=3)
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
    tw = _fake_world(headless=True, difficulty="Normal", seed=5)
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
    from touhou.registry import GAME_TITLES, registered_games

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
    # title 取已注册列表首部作品经 GAME_TITLES 的映射(测试进程里注册了
    # 假作品 test00, 故不写死 th07 标题; th07 标题事实见 game_test/th07)
    first = registered_games()[0]
    assert info["title"] == GAME_TITLES.get(first, first)
    assert "th07" in info["games"]
    # 坏路径不炸
    bad = detect_environment(data_path="/nonexistent/th07.dat")
    assert bad["res_entries"] == "未找到"


# ---- 实体 numpy 快路径(假作品桩对局; th07 实弹对照见 game_test/th07) ----
def test_bullets_array_matches_snapshot() -> None:
    import math

    from touhou.apis.modding import ModApi

    game = _fake(seed=1)
    mods = ModApi(game)
    mods.bullets.fire_ring(192.0, 100.0, arms=8)  # 桩容器按 arms 造弹
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
    game = _fake(seed=1)
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
    game = Game._from_impl(impl, get_game(FAKE_GAME), "stub")
    snap = game.snapshot()
    assert snap.player.hitbox is None
    assert game.bullets_array().shape == (0, 6)
