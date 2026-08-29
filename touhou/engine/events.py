"""轻量事件总线(发布/订阅) —— 作品专属事件透出引擎层的通用通道。

分层纪律: 本模块作品无关, 只定义通道不定义事件表。作品实现(games/thXX/
的主逻辑类)在自身持有 ``EventBus`` 实例并以 ``event_bus`` 属性透出
(GameEngine 协议的可选能力位, 见 touhou/types.py); apis 的 Game 门面
构造时自动订阅, 把帧内发布的事件包成 GameEvent 并入 ``step()`` 的事件流
(排在通用状态差事件之前, 保持时序语义)。th07 的用法: 结界激活/破裂时
发布 ``border_start``/``border_break``(见 games/th07/world.py)。

契约:
- ``publish(kind, **fields)``: kind 为事件名字符串, fields 为附带字段
  (门面只识别 GameEvent 已有的 name/stage, 其余字段忽略)。
- 错误隔离: 单个订阅者抛异常记 log.warning 后继续广播, 不影响其他
  订阅者与发布方(引擎主循环不被观察者拖炸)。
"""

from __future__ import annotations

from typing import Any, Callable

from ..logger import logger as log

__all__ = ["EventBus", "EventCallback"]

#: 订阅者回调形态: (事件名, **附带字段)。
EventCallback = Callable[..., None]


class EventBus:
    """作品无关的轻量事件总线(同步广播, 单线程语义)。"""

    def __init__(self) -> None:
        self._subscribers: list[EventCallback] = []

    def subscribe(self, callback: EventCallback) -> None:
        """登记订阅者(发布时按登记顺序同步调用)。"""
        self._subscribers.append(callback)

    def unsubscribe(self, callback: EventCallback) -> None:
        """摘除订阅者(未登记时静默忽略)。"""
        try:
            self._subscribers.remove(callback)
        except ValueError:
            pass

    def publish(self, kind: str, **fields: Any) -> None:
        """广播一个事件; 订阅者异常隔离(记 log.warning, 不中断广播)。"""
        for callback in list(self._subscribers):
            try:
                callback(kind, **fields)
            except Exception:
                log.opt(exception=True).warning(
                    "事件总线订阅者处理 {!r} 时抛异常(已隔离)", kind
                )
