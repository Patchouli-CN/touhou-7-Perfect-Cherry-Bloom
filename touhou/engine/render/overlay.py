"""立即模式(immediate mode)GUI 覆盖层 —— 画面绘制命令的产消汇聚点。

分层约定(与 engine/render/__init__.py 同一铁律):

- 本模块是**纯数据 + 缓冲**模块: 只定义覆盖层命令结构(msgspec.Struct)
  与命令汇聚点 ``OverlaySink``, **不 import pygame**, 不 import 任何渲染
  后端实现, 不 import games.*(AST 守护钉死);
- 数据流: **生产者**(``apis.modding.ModApi.gui`` 或任意脚本)每帧 push
  绘制命令 → **消费者**(渲染后端, 现唯一实现是 games/th07/view/
  pygame_backend.py 的 PygameRenderer.render_game)每帧 drain 取走并画到
  游戏区画面, 命令**只活一帧**, 消费即清空;
- **headless 下是 no-op**: 没有后端 drain 时命令静默丢弃(缓冲有容量上限,
  不无限堆积), 不产生任何画面副作用, 也不报错 —— 同一 policy 脚本可在
  headless/窗口/观战模式间直接复用。

坐标系约定(所有命令统一):

- **游戏区像素坐标系**, 原点左上, **y 轴向下**(th07: 384x448 游戏区) ——
  与 ``Game.player_pos`` / ``bullets_array()`` / ``snapshot()`` 同一坐标系,
  AI 画安全区导航线可直接使用观测坐标, 无需任何换算;
- 颜色为 RGB 三元组 (r, g, b), 各通道 0..255;
- 线宽/字号单位为像素。
"""
from __future__ import annotations

from typing import TypeAlias, Union

import msgspec

__all__ = [
    "OverlayCircle",
    "OverlayCommand",
    "OverlayLine",
    "OverlayPolyline",
    "OverlaySink",
    "OverlayText",
    "SINK",
]

#: 命令颜色的公共形态(RGB, 0..255)。
Color: TypeAlias = tuple[int, int, int]

#: 默认命令颜色(白)。
_DEFAULT_COLOR: Color = (255, 255, 255)


class OverlayLine(msgspec.Struct, frozen=True):
    """一条线段 (x1, y1)-(x2, y2)(游戏区像素系, y 向下)。"""
    x1: float
    y1: float
    x2: float
    y2: float
    color: Color = _DEFAULT_COLOR
    width: int = 1


class OverlayCircle(msgspec.Struct, frozen=True):
    """一个圆 (x, y) 半径 radius; width=0 为实心填充(pygame.draw 语义)。"""
    x: float
    y: float
    radius: float
    color: Color = _DEFAULT_COLOR
    width: int = 1


class OverlayPolyline(msgspec.Struct, frozen=True):
    """一条折线(导航路线等); closed=True 时首尾相连成多边形。"""
    points: tuple[tuple[float, float], ...]
    color: Color = _DEFAULT_COLOR
    width: int = 1
    closed: bool = False


class OverlayText(msgspec.Struct, frozen=True):
    """一段文字, 左上角锚在 (x, y)(自定义弹出提示等)。"""
    x: float
    y: float
    content: str
    color: Color = _DEFAULT_COLOR
    size: int = 16


#: 覆盖层命令的联合类型(drain 的消费方按 isinstance 分发)。
OverlayCommand: TypeAlias = Union[
    OverlayLine, OverlayCircle, OverlayPolyline, OverlayText]


class OverlaySink:
    """覆盖层命令汇聚点 —— 生产者 push, 渲染后端每帧 drain。

    与游戏主循环同线程使用(不做线程同步)。缓冲容量有上限: 无消费者
    (headless)持续 push 时丢弃最旧命令, 不无限占用内存 —— 这是 headless
    no-op 语义的实现(见模块 docstring)。
    """

    def __init__(self, capacity: int = 4096) -> None:
        self._capacity = capacity
        self._pending: list[OverlayCommand] = []

    def push(self, cmd: OverlayCommand) -> None:
        """推入一条本帧命令; 缓冲满(无消费者)时丢弃最旧的一条。"""
        if len(self._pending) >= self._capacity:
            self._pending.pop(0)
        self._pending.append(cmd)

    def drain(self) -> list[OverlayCommand]:
        """取走并清空当前全部待绘命令(渲染后端每帧调用一次)。"""
        cmds, self._pending = self._pending, []
        return cmds

    def __len__(self) -> int:
        """当前待绘命令数(测试/调试用)。"""
        return len(self._pending)


#: 进程级汇聚点单例: ModApi.gui 默认推到这里, pygame 后端从这里取。
SINK = OverlaySink()
