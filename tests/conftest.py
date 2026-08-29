"""通用层测试的假作品 —— 注册最小假作品 "test00"(tests 全树共享)。

铁律: tests/ 根下的 test_*.py 是**通用层**测试, 只用本模块注册的假作品
验证 apis/engine 的通用契约, 禁止 import games.*(AST 守护钉死, 见
test_api.py); 作品专属测试住 tests/game_test/thXX/ 子树(豁免)。

假作品不是第二个 th07: 不模拟弹幕/结界/符卡, 只提供 GameEngine/
ModdableEngine 协议形状 + 可控的状态差(tick 按 keys 移动自机、按 bomb
记账), 让门面的事件 diff/快照/数组观测面有可验证的输入。

注册是进程级一次性(conftest 模块级); registry 对重复注册报错, 故用
registered_games() 判重, 防止任何重复收集场景下炸。
"""

from __future__ import annotations

from enum import Enum
from typing import Any

import pytest

from touhou.apis.basic import Game
from touhou.engine.events import EventBus
from touhou.registry import (
    GameData,
    mod_namespace,
    register_app,
    register_game_data,
    register_mods,
    register_world_impl,
    registered_games,
)
from touhou.utils import Vec2

#: 假作品名(通用层测试一律经 ``game=FAKE_GAME`` 构造门面)
FAKE_GAME = "test00"

#: 假作品数值表: 角色/难度名单(下标 = 内部 id), 满火力取 GameData 默认 128
FAKE_DATA = GameData(
    characters=("TestA", "TestB"),
    difficulties=("Easy", "Normal", "Hard"),
)


class FakePlayerState(Enum):
    """假自机状态(快照只读 name; 不模拟真实状态机)。"""

    ALIVE = 0


class _FakeBullets:
    """假敌弹容器: alive/fire/clear(ModdableEngine 写入面 + 快照观测面)。"""

    def __init__(self) -> None:
        self._items: list[Any] = []

    def alive(self) -> list[Any]:
        return list(self._items)

    def fire(self, burst: Any) -> int:
        """按 Burst 的 arms*rings 生成占位弹(只有快照/数组需要的字段)。"""
        from types import SimpleNamespace

        n = burst.arms * burst.rings
        for i in range(n):
            self._items.append(
                SimpleNamespace(
                    pos=Vec2(burst.path.x, burst.path.y),
                    angle=burst.base_angle,
                    speed=burst.speed_a,
                    sprite=burst.sprite,
                    hitbox=2.0,
                )
            )
        return n

    def clear(self) -> None:
        self._items.clear()


class FakeWorld:
    """满足 GameEngine 协议的最小桩对局(假作品 test00 的对局实现)。

    协议面: frame/stage_no/game_over/cleared/result/stage_results/ending、
    globals(score/deaths/bombs_used/spell_cards_captured/graze_in_total/
    lives_remaining)、lives/bombs/power、player(pos/state/focus/
    invulnerability_timer/hitbox_radius)、boss、bullets/host/items/lasers
    容器、tick(keys,bomb,advance,skip)、enter_stage/finalize_game_over/
    finish_ending; 挂 event_bus(EventBus 实例)供门面总线订阅测试用。

    可控状态差: tick 按 keys 移动自机(左/右/上/下, focus 半速)、bomb 键
    记账 bombs_used; 测试也可直改 impl 字段制造事件差(死亡/EXTEND 等)。
    """

    def __init__(
        self,
        *,
        data_path: Any = None,
        character: int = 0,
        difficulty: int = 1,
        seed: int | None = None,
        score_path: Any = None,
        initial_lives: int | None = None,
        hooks: Any = None,
        data: GameData | None = None,
    ) -> None:
        from types import SimpleNamespace

        self.frame = 0
        self.stage_no = 1
        self.character = character
        self.difficulty = difficulty
        self.seed = 0x5EED if seed is None else seed  # 与 th07 默认种子同形
        self.lives = float(initial_lives if initial_lives is not None else 3)
        self.bombs = 3.0
        self.power = 0.0
        self.game_over = False
        self.cleared = False
        self.result: dict | None = None
        self.stage_results: Any = None
        self.ending: Any = None
        self.boss: Any = None
        self.globals = SimpleNamespace(
            score=0,
            deaths=0,
            bombs_used=0.0,
            spell_cards_captured=0,
            graze_in_total=0,
            lives_remaining=self.lives,
            luck=0,  # 假作品 mod 能力的写入靶子(FakeMods.set_luck)
        )
        self.player = SimpleNamespace(
            pos=Vec2(192.0, 400.0),
            state=FakePlayerState.ALIVE,
            focus=False,
            invulnerability_timer=0,
            hitbox_radius=1.0,
        )
        self.bullets = _FakeBullets()
        self.host = SimpleNamespace(alive=list)
        self.items = SimpleNamespace(alive=list)
        self.lasers = SimpleNamespace(lasers=[])
        self.event_bus = EventBus()
        self._lifespan = 5000  # 桩对局寿命(帧): 到此自动 GameOver

    def tick(
        self,
        *,
        keys: tuple[bool, ...],
        bomb: bool = False,
        advance: bool = False,
        skip: bool = False,
    ) -> None:
        """推进一帧: 按 keys 移动自机(游戏区内钳位), bomb 键记账。

        寿命上限: frame 到 ``_lifespan`` 自动 GameOver —— 桩对局自身不产生
        任何事件, 没有终结条件的话事件流(``TouhouWorld.run()``)迭代会空转
        到天荒地老; stream 的终局自动收尾(GAME_OVER→结算)也据此可测。
        """
        self.frame += 1
        if self.frame >= self._lifespan and not self.game_over:
            self.game_over = True
        left, right, up, down, _shoot, focus = keys
        speed = 2.0 if focus else 4.0
        x, y = self.player.pos.x, self.player.pos.y
        if left:
            x -= speed
        if right:
            x += speed
        if up:
            y -= speed
        if down:
            y += speed
        self.player.pos = Vec2(min(max(x, 0.0), 384.0), min(max(y, 0.0), 448.0))
        self.player.focus = focus
        if self.player.invulnerability_timer > 0:
            self.player.invulnerability_timer -= 1
        if bomb and self.bombs > 0:
            self.bombs -= 1.0
            self.globals.bombs_used += 1

    def enter_stage(self, stage: int) -> None:
        self.stage_no = stage

    def finalize_game_over(self) -> None:
        """GameOver 不续关 → 总结算(result 非 None, phase=RESULT)。"""
        self.result = {"score": self.globals.score, "cleared": False}

    def finish_ending(self) -> None:
        """结局看完 → 总结算。"""
        self.result = {"score": self.globals.score, "cleared": True}


class FakeMods:
    """假作品 test00 的最小 mod 能力提供者(供 ModApi 收割/归属测试)。

    两个写操作: set_luck 并入核心命名空间 player(写 globals.luck),
    wish_clear 自成新命名空间 wish(直置 cleared)。
    """

    def __init__(self, game: Game) -> None:
        self._game = game

    @mod_namespace("player")
    def set_luck(self, value: int) -> None:
        """运直改(0..100)。"""
        if not 0 <= value <= 100:
            raise ValueError(f"运 {value} 超出合法范围 0..100")
        self._game._impl.globals.luck = value

    @mod_namespace("wish")
    def wish_clear(self) -> None:
        """祈愿通关(直置 cleared)。"""
        self._game._impl.cleared = True


class FakeApp:
    """假作品 test00 的最小窗口 App: 记录构造参数, run() 立刻返回。"""

    captured: dict[str, Any] = {}

    def __init__(self, make_game: Any, **kw: Any) -> None:
        type(self).captured = {"make_game": make_game, **kw}

    def run(self) -> None:
        pass


# ---- 进程级一次性注册(判重防重复收集炸 registry) ----
if FAKE_GAME not in registered_games():
    register_world_impl(FAKE_GAME)(FakeWorld)
    register_game_data(FAKE_GAME, FAKE_DATA)
    register_mods(FAKE_GAME)(FakeMods)
    register_app(FAKE_GAME)(FakeApp)


@pytest.fixture
def fake_game() -> Game:
    """假作品对局门面(通用层测试的标准开局: TestA/Normal/seed=1)。"""
    return Game(game=FAKE_GAME, character="TestA", difficulty="Normal", seed=1)
