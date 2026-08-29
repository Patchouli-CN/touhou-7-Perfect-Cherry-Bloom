"""
ECL编码/解码器，你们理解为Ecl可以被序列化/反序列化就可以了！
"""

from __future__ import annotations

from ..registry import EclSpec, get_game
from .ecl import EclFile


class EclCodec:
    """ECL 编解码器 —— 按作品名从注册表解析实现, 返回作品无关数据结构。

    用法::

        codec = EclCodec(game_name or None)  # None = 作品无关, 直用 engine 的 EclFile
        ecl = codec.decode(data)        # bytes -> EclFile
        data2 = codec.encode(ecl)       # EclFile -> bytes

    错误路径:
    - 作品未注册: ``get_game`` 抛 NotRegisteredError(带已注册列表);
    - 已注册但缺 ECL 维度: 构造期抛 ValueError;
    - 格式类缺序列化能力: ``encode`` 抛 NotImplementedError。
    """

    def __init__(self, game: str | None = None) -> None:
        self.game = game
        self._ecl_spec: EclSpec | None = None

        if game is not None:
            spec = get_game(game, report_err=True)

            if spec:
                self._ecl_spec = spec.ecl

            if self._ecl_spec is None:
                raise ValueError(
                    f"作品 {game!r} 已注册, 但缺 ECL 维度"
                    f"(需要 @register_ecl({game!r}, file_format=...) 装饰 ECL 虚拟机类)"
                )

    def decode(self, data: bytes) -> EclFile:
        """按本作品的格式解析 .ecl 字节流(返回该格式类的 parse 产物)。"""
        # 注册表存的是裸 type(作品可带自己的格式类), 约定有 parse(data) classmethod
        if self._ecl_spec:
            parsed: EclFile = self._ecl_spec.file_format.parse(data)
        else:
            parsed = EclFile.parse(data)
        return parsed

    def encode(self, ecl: EclFile) -> bytes:
        """把解析产物写回 .ecl 字节流; 格式类未实现 serialize 时报错。"""
        fmt = self._ecl_spec.file_format if self._ecl_spec else EclFile
        serialize = getattr(fmt, "serialize", None)
        if not callable(serialize):
            raise NotImplementedError(
                f"作品 {self.game!r} 的 ECL 格式类 {fmt.__name__} 未实现 "
                f"serialize(ECL 格式类需提供 parse/serialize 对, "
                f"见 touhou/registry.py register_ecl)"
            )
        out: bytes = serialize(ecl)
        return out
