"""ECL 编解码统一入口 —— 按作品名从注册表解析实现。

``EclCodec`` 是引擎层唯一的 ECL enc/dec 门面: 构造时经
``get_game(game).ecl``(registry 的 EclSpec)解析出该作品的文件格式类,
``decode``/``encode`` 委托其 ``parse``/``serialize``。数据形态是作品无关的
msgspec.Struct(th07 = engine/ecl.py 的 ``EclFile``; 新作品的格式类也应是
Struct, 并提供同样的 ``parse(data)`` classmethod 与 ``serialize() -> bytes``
实例方法, 鸭子满足即可)。

VM 构造(EclSpec.machine)不在本门面范围, 仍由对局实现侧直接取用
(th07 见 games/th07/world.py 的 _load_ecl)。
"""

from __future__ import annotations

from typing import Any, cast

from ..registry import EclSpec, get_game
from .ecl import EclFile


class EclCodec:
    """ECL 编解码器 —— 按作品名从注册表解析实现, 返回作品无关数据结构。

    用法::

        codec = EclCodec("th07")        # 默认 "th07"
        ecl = codec.decode(data)        # bytes -> EclFile
        data2 = codec.encode(ecl)       # EclFile -> bytes

    错误路径:
    - 作品未注册: ``get_game`` 抛 NotRegisteredError(带已注册列表);
    - 已注册但缺 ECL 维度: 构造期抛 ValueError;
    - 格式类缺序列化能力: ``encode`` 抛 NotImplementedError。
    """

    def __init__(self, game: str = "th07") -> None:
        spec = get_game(game)
        if spec.ecl is None:
            raise ValueError(
                f"作品 {game!r} 已注册, 但缺 ECL 维度"
                f"(需要 @register_ecl({game!r}, file_format=...) 装饰 ECL 虚拟机类)")
        self.game = game
        self._spec: EclSpec = spec.ecl

    def decode(self, data: bytes) -> EclFile:
        """按本作品的格式解析 .ecl 字节流(返回该格式类的 parse 产物)。"""
        # 注册表存的是裸 type(作品可带自己的格式类), 约定有 parse(data) classmethod
        fmt = cast(Any, self._spec.file_format)
        parsed: EclFile = fmt.parse(data)
        return parsed

    def encode(self, ecl: EclFile) -> bytes:
        """把解析产物写回 .ecl 字节流; 格式类未实现 serialize 时报错。"""
        fmt = self._spec.file_format
        serialize = getattr(fmt, "serialize", None)
        if serialize is None:
            raise NotImplementedError(
                f"作品 {self.game!r} 的 ECL 格式类 {fmt.__name__} "
                f"未实现 serialize(), 无法编码")
        out: bytes = serialize(ecl)
        return out
