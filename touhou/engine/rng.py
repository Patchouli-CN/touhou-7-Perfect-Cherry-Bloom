""" 可复现的伪随机数(欧式弹幕回放用) —— Pythonic。 """

from __future__ import annotations


class Rng:
    """16 位种子的线性伪随机, 同一种子序列固定, 用于回放可复现性。"""

    __slots__ = ("seed", "gen")

    def __init__(self, seed: int = 0) -> None:
        self.seed = seed & 0xFFFF
        self.gen = 0  # 生成计数器

    # ---- 正整数区间 ----
    def u16(self) -> int:
        # 照抄 Rng::GetRandomU16 (TH07 0x00431870), 注意 C 运算符优先级:
        # seed = (((u & 0xC000) >> 14) + u * 4) & 0xFFFF
        u = ((self.seed ^ 0x9630) - 0x6553) & 0xFFFF
        self.seed = (((u & 0xC000) >> 14) + u * 4) & 0xFFFF
        self.gen += 1
        return self.seed

    def u32(self) -> int:
        return (self.u16() << 16) | self.u16()

    # ---- 浮点 [0,1) ----
    def unit(self) -> float:
        return self.u32() / 4294967296.0

    # ---- 区间 ----
    def int_below(self, bound: int) -> int:
        return self.u32() % bound if bound else 0

    def in_range(self, lo: float, hi: float) -> float:
        return self.unit() * (hi - lo) + lo

    def in_int_range(self, lo: int, hi: int) -> int:
        return lo + self.u32() % (hi - lo + 1) if hi >= lo else lo

    def sign(self) -> int:
        return 1 if self.u16() & 1 else -1
