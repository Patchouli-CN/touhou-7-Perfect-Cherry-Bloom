"""事件总线(touhou/engine/events.py)与门面总线订阅行为测试。

EventBus 本体是纯引擎层(作品无关, 不需要 th07.dat);
门面行为用 stub impl(Game._from_impl)验证: 有 event_bus 的作品, 帧内
发布的专属事件包成 GameEvent 排在通用 diff 事件前; 无 event_bus 的
作品(协议可选能力位缺失)行为不变。th07 真实结界事件流见
tests/game_test/th07/test_th07_api.py 的 test_border_events_via_event_bus。
"""

from __future__ import annotations

from types import SimpleNamespace

from touhou.apis.basic import Game
from touhou.engine.events import EventBus
from touhou.registry import get_game
from touhou.utils import Vec2


# ---- EventBus 本体 ----
def test_publish_subscribe_broadcasts_in_order() -> None:
    bus = EventBus()
    got: list = []
    bus.subscribe(lambda kind, **f: got.append(("a", kind, f)))
    bus.subscribe(lambda kind, **f: got.append(("b", kind, f)))
    bus.publish("border_start", stage=1)
    bus.publish("border_break")
    assert got == [
        ("a", "border_start", {"stage": 1}),
        ("b", "border_start", {"stage": 1}),
        ("a", "border_break", {}),
        ("b", "border_break", {}),
    ]


def test_unsubscribe() -> None:
    bus = EventBus()
    got: list = []
    cb = lambda kind, **f: got.append(kind)  # noqa: E731
    bus.subscribe(cb)
    bus.unsubscribe(cb)
    bus.unsubscribe(cb)  # 未登记静默忽略
    bus.publish("x")
    assert got == []


def test_subscriber_error_isolated() -> None:
    """单个订阅者抛异常: 记 log.warning, 其余订阅者与发布方不受影响。"""
    bus = EventBus()
    got: list = []

    def bad(kind, **f):
        raise RuntimeError("boom")

    bus.subscribe(bad)
    bus.subscribe(lambda kind, **f: got.append(kind))
    bus.publish("border_start")  # 不抛
    assert got == ["border_start"]


# ---- 门面总线订阅(Game._from_impl + stub impl) ----
def _stub_impl(**extra):
    ns = SimpleNamespace(
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
        **extra,
    )
    ns.tick = lambda **kw: setattr(ns, "frame", ns.frame + 1)
    return ns


def test_facade_without_event_bus_unchanged() -> None:
    """impl 无 event_bus(能力位缺失): step 只回通用 diff 事件, 不炸。"""
    game = Game._from_impl(_stub_impl(), get_game("th07"), "stub")
    assert game.step() == []
    impl = game._impl
    impl.globals.deaths = 1  # 帧间状态差 → 通用事件照常工作
    kinds = [e.kind for e in game.step()]
    assert kinds == ["player_death"]


def test_facade_bus_events_precede_diff_events() -> None:
    """有 event_bus 的 impl: 帧内发布的事件包成 GameEvent, 排 diff 事件前。"""
    bus = EventBus()
    impl = _stub_impl(event_bus=bus)

    def tick_publishing(**kw):
        impl.frame += 1
        if impl.frame == 1:
            bus.publish("border_start")  # 帧内发布(引擎侧语义)
            impl.globals.deaths = 1  # 同帧的状态差

    impl.tick = tick_publishing
    game = Game._from_impl(impl, get_game("th07"), "stub")
    events = game.step()
    assert [(e.kind, e.frame) for e in events] == [
        ("border_start", 1),  # 总线事件排前(帧内即时, 时序先于帧末状态差)
        ("player_death", 1),
    ]
    assert game.step() == []  # 总线事件消费一次即清空, 不重复出现
