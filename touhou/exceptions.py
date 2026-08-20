"""统一异常层级 —— 叶子模块(不 import 包内其他模块)。

只收编自定义异常与"调用方需要捕获"的语义错误; 内置异常
(ValueError/KeyError 等)的一般使用点保持现状。收编的异常一律
多继承对应内建异常, 既有 ``except ValueError`` / ``pytest.raises(KeyError)``
等捕获点不受影响。

层级::

    TouhouError
    ├── ParseError(TouhouError, ValueError)          # 资源格式解析
    │   ├── ArchiveFormatError                       # PBG4 容器
    │   ├── EclParseError                            # ecl 文件
    │   └── MsgParseError                            # msg 文件
    ├── NotImplementedEclError(TouhouError, NotImplementedError)
    ├── RegistryError(TouhouError)                   # 注册表
    │   ├── DuplicateRegistrationError(RegistryError, ValueError)
    │   └── NotRegisteredError(RegistryError, KeyError)
    └── ThbgmFormatError(TouhouError, OSError)       # thbgm.dat 数据
"""

from __future__ import annotations


class TouhouError(Exception):
    """touhou 包语义错误基类。"""


class ParseError(TouhouError, ValueError):
    """游戏资源文件格式解析错误基类。"""


class ArchiveFormatError(ParseError):
    """dat 容器(PBG4)结构损坏/不符。"""


class EclParseError(ParseError):
    """ecl 文件结构损坏。"""


class MsgParseError(ParseError):
    """msg 文件结构损坏。"""


class NotImplementedEclError(TouhouError, NotImplementedError):
    """遇到未实现的 ECL 指令(strict 模式下抛出)。"""


class RegistryError(TouhouError):
    """注册表语义错误基类。"""


class DuplicateRegistrationError(RegistryError, ValueError):
    """同名重复注册(防静默覆盖)。"""


class NotRegisteredError(RegistryError, KeyError):
    """按名查找未注册的条目。"""


class ThbgmFormatError(TouhouError, OSError):
    """thbgm.dat 数据不符(截断等)。"""


__all__ = [
    "ArchiveFormatError",
    "DuplicateRegistrationError",
    "EclParseError",
    "MsgParseError",
    "NotImplementedEclError",
    "NotRegisteredError",
    "ParseError",
    "RegistryError",
    "ThbgmFormatError",
    "TouhouError",
]
