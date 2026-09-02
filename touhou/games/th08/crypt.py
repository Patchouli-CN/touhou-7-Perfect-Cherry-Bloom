"""TH08 条目内层加密 —— FileSystem::TryDecryptFromTable(Global.cpp:901-927)。

pbgz 解包出的部分条目(anm/std/ecl/msg…)还带一层签名加密: 前缀 "edz"
(g_CryptSignature 各减 0x20/0x40/0x60, Global.cpp:898,906-908), 第 4 字节
选 g_DecryptParams 参数行, 跳过 4 字节签名后按行参数 Decrypt(算法本体是
schema/archive/pbgz.py 的 ``decrypt``, 与容器头/文件表同一个
FileSystem::Decrypt)。未命中签名或参数行时原样返回(明文条目直接透传)。
"""

from __future__ import annotations

from ...schema.archive.pbgz import decrypt

# g_DecryptParams(Global.cpp:891-896): (key, xorValue, xorValueInc, chunkSize, maxBytes)
_DECRYPT_PARAMS: tuple[tuple[int, int, int, int, int], ...] = (
    (0x5D, 0x1B, 0x37, 0x0040, 0x2800),
    (0x74, 0x51, 0xE9, 0x0040, 0x3000),
    (0x71, 0xC1, 0x51, 0x1400, 0x2000),
    (0x8A, 0x03, 0x19, 0x1400, 0x7800),
    (0x95, 0xAB, 0xCD, 0x0200, 0x1000),
    (0xB7, 0x12, 0x34, 0x0400, 0x2800),
    (0x9D, 0x35, 0x97, 0x0080, 0x2800),
    (0xAA, 0x99, 0x37, 0x0400, 0x1000),
)
# g_CryptSignature 各减 0x20/0x40/0x60(Global.cpp:898,906-908) = "edz"
_SIGNATURE = (0x85 - 0x20, 0xA4 - 0x40, 0xDA - 0x60)


def try_decrypt_from_table(data: bytes) -> bytes:
    """TryDecryptFromTable: 命中 "edz" 签名则解密(去掉 4 字节签名), 否则原样返回。"""
    if len(data) < 4 or tuple(data[:3]) != _SIGNATURE:
        return data
    # C 的循环是 int 比较(key - (i<<4) - 0x10 可为负, 负值永远不等 u8 字节)
    for i, (key, xor, inc, chunk, max_bytes) in enumerate(_DECRYPT_PARAMS):
        if data[3] == key - (i << 4) - 0x10:
            return decrypt(data[4:], xor, inc, chunk, max_bytes)
    return data
