"""数学与向量工具 —— Pythonic 风格。"""

from __future__ import annotations

import math
import msgspec

ZUN_PI = 3.1415927  # f32 版 pi, 与 ZunMath.hpp 一致
ZUN_2PI = ZUN_PI * 2.0


class Vec2(msgspec.Struct, frozen=True):
    """二维向量/坐标点。不可变, 支持常见向量运算与旋转。"""

    x: float
    y: float

    # ---- 构造 ----
    @classmethod
    def from_angle(cls, angle: float, length: float = 1.0) -> "Vec2":
        """按极坐标(角度弧度, 长度)构造: (cos*l, sin*l)。 y 轴向下的屏幕系。"""
        return cls(math.cos(angle) * length, math.sin(angle) * length)

    @classmethod
    def zero(cls) -> "Vec2":
        return cls(0.0, 0.0)

    # ---- 运算 ----
    def __add__(self, o: "Vec2") -> "Vec2":
        return Vec2(self.x + o.x, self.y + o.y)

    def __sub__(self, o: "Vec2") -> "Vec2":
        return Vec2(self.x - o.x, self.y - o.y)

    def __neg__(self) -> "Vec2":
        return Vec2(-self.x, -self.y)

    def __mul__(self, s: float) -> "Vec2":
        return Vec2(self.x * s, self.y * s)

    __rmul__ = __mul__

    def __truediv__(self, s: float) -> "Vec2":
        return Vec2(self.x / s, self.y / s)

    # ---- 几何 ----
    @property
    def length(self) -> float:
        return math.hypot(self.x, self.y)

    def distance(self, o: "Vec2") -> float:
        return (self - o).length

    def dot(self, o: "Vec2") -> float:
        return self.x * o.x + self.y * o.y

    def angle(self) -> float:
        """向量方位角(弧度)。"""
        return math.atan2(self.y, self.x)

    def rotated(self, angle: float) -> "Vec2":
        return Vec2(
            self.x * math.cos(angle) - self.y * math.sin(angle),
            self.x * math.sin(angle) + self.y * math.cos(angle),
        )

    def normalized(self) -> "Vec2":
        d = self.length or 1.0
        return Vec2(self.x / d, self.y / d)

    def lerp(self, other: "Vec2", t: float) -> "Vec2":
        return Vec2(self.x + (other.x - self.x) * t, self.y + (other.y - self.y) * t)

    def to_tuple(self) -> tuple[float, float]:
        return (self.x, self.y)

    # 允许解包成 (x, y), 便于传入 pygame 等
    def __iter__(self):
        yield self.x
        yield self.y

    def __repr__(self) -> str:
        return f"({self.x:.1f}, {self.y:.1f})"


def angle_to(from_pos: Vec2, to_pos: Vec2) -> float:
    """从 from_pos 指向 to_pos 的方位角。重合时返回 pi/2。"""
    if from_pos == to_pos:
        return math.pi / 2
    return math.atan2(to_pos.y - from_pos.y, to_pos.x - from_pos.x)


def normalize_angle_diff(angle: float) -> float:
    """把角度规范化到 (-pi, pi]。"""
    return (angle + math.pi) % math.tau - math.pi


def normalize_angle(angle: float) -> float:
    """把角度规范化到 [0, 2pi)。"""
    return angle % math.tau


def add_normalize_angle(a: float, b: float = 0.0) -> float:
    """utils::AddNormalizeAngle: 相加后规范到 (-pi, pi](最多 16 次循环)。"""
    a += b
    n = 0
    while a > ZUN_PI:
        a -= ZUN_2PI
        n += 1
        if n > 16:
            break
    n = 0
    while a < -ZUN_PI:
        a += ZUN_2PI
        n += 1
        if n > 16:
            break
    return a
