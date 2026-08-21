"""游戏无关的纯工具 —— 叶子包(不 import engine/schema/games/view)。

- math:        Vec2 / 角度规范化 / ZUN 版 AddNormalizeAngle
- csemantics:  C 数值语义(cdiv/cmod/i32/i16/f32)
"""

from .csemantics import cdiv, cmod, f32, i16, i32
from .math import (
    ZUN_2PI,
    ZUN_PI,
    Vec2,
    add_normalize_angle,
    angle_to,
    normalize_angle,
    normalize_angle_diff,
)

__all__ = [
    "ZUN_2PI",
    "ZUN_PI",
    "Vec2",
    "add_normalize_angle",
    "angle_to",
    "cdiv",
    "cmod",
    "f32",
    "i16",
    "i32",
    "normalize_angle",
    "normalize_angle_diff",
]
