""" C 数值语义 —— 整数除法/取余、位宽回绕、f32 截断。

对照反编译源码时 Python 与 C 的默认行为不同(// 向下取整而非向零截断,
int 无位宽回绕, float 是 f64), 这里集中提供与 C 一致的原语。
"""

from __future__ import annotations

import struct


def cdiv(a: int, b: int) -> int:
    """C 整数除法(向零截断, Python // 是向下取整)。"""
    q = abs(a) // abs(b)
    return -q if (a < 0) != (b < 0) else q


def cmod(a: int, b: int) -> int:
    """C 取余(符号跟被除数)。"""
    return a - cdiv(a, b) * b


def i32(v: int) -> int:
    """按 C i32 回绕。"""
    v &= 0xFFFFFFFF
    return v - 0x100000000 if v >= 0x80000000 else v


def i16(v: int) -> int:
    """按 C i16 回绕。"""
    v &= 0xFFFF
    return v - 0x10000 if v >= 0x8000 else v


def f32(v: float) -> float:
    """截断到 IEEE 单精度(C 里所有 float 字段都是 f32)。"""
    f: float = struct.unpack("<f", struct.pack("<f", v))[0]
    return f
