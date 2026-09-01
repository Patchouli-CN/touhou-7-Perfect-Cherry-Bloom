"""LZSS 解压 —— 与容器格式解耦的可参数化实现。

ZUN 各作的资源包都用同一族 LZSS(环形字典 + 9bit 字面量 token), 只有字典
位宽/长度位宽不同(pbg3/pbg4 = 13/4)。本模块只管"压缩字节流 → 原字节",
不认识任何容器头, 由 base/各格式模块按自己的参数实例化。
"""

from __future__ import annotations

# 匹配长度的编码偏置: 实际长度 = 编码值 + 2, 含端点再 +1
MIN_MATCH = 3


class LzssDecompressor:
    """把 LZSS 压缩字节流解成原字节。字典为 ``1 << dict_bits`` 字节环形缓冲。

    性能重写 (BUGS.md 增量#3): 纯 Python 逐位实现只有 ~2MB/s, 大贴图包
    首用解压 200-900ms 是开局/换关/首发 bomb 卡顿的主因。本实现:
    - 环形字典不显式维护 —— 它逐字节镜像输出流(写头偏移 1), 匹配等价于
      输出流上距离 d0 的自复制, 用切片倍增一次拷贝;
    - 位缓冲按需批量补字节(源尽补零, 与原实现 pos 越界读 0 一致);
    - 快径: 连续 8 个字面量(flag 全 1 的 72bit 组)一次提取。
    已按 th07.dat 全部 197 条目与原实现逐字节比对一致(实测 ~1.9x)。

    token 布局(与位宽无关的部分): 首 9bit = flag(1) + 字面量/offset 高 8 位;
    flag=0 时再读 ``dict_bits + len_bits - 8`` bit = offset 余位 + 长度。
    故要求 ``dict_bits >= 8``(所有实际变体都满足)。
    """

    # 8 个 9bit 字面量组的 flag 位掩码(快径全 1 判定用)
    _LIT8_FLAGS = sum(1 << (8 + 9 * k) for k in range(8))

    def __init__(self, dict_bits: int = 13, len_bits: int = 4) -> None:
        if dict_bits < 8:
            raise ValueError(f"dict_bits 至少 8 位(token 布局要求), 收到 {dict_bits}")
        self.dict_bits = dict_bits
        self.len_bits = len_bits
        self.dict_size = 1 << dict_bits  # 环形字典字节数(pbg4 = 8192)
        self.mask = self.dict_size - 1
        self.lookahead = (1 << len_bits) + 2  # 最大匹配长度
        # flag=0 后的第二次读取位数: offset 余位 + 长度位 (pbg3/pbg4 = 9)
        self._read2 = dict_bits + len_bits - 8
        self._len_mask = (1 << len_bits) - 1

    def decompress(self, src: bytes, out_len: int | None = None) -> bytes:
        out = bytearray()
        pos = 0
        buf = 0  # 位缓冲, 下一位在 MSB 侧
        cnt = 0  # 缓冲有效位数
        dict_size = self.dict_size
        mask = self.mask
        read2 = self._read2
        len_mask = self._len_mask
        len_bits = self.len_bits
        lit8 = self._LIT8_FLAGS
        while True:
            while cnt < 72:
                take = src[pos : pos + 9]
                pos += len(take)
                if take:
                    buf = (buf << (8 * len(take))) | int.from_bytes(take, "big")
                    cnt += 8 * len(take)
                else:
                    buf <<= 8
                    cnt += 8
            buf &= (1 << cnt) - 1  # 截断陈旧高位, 防 bigint 膨胀
            chunk = (buf >> (cnt - 72)) & ((1 << 72) - 1)
            if (chunk & lit8) == lit8:
                # 快径: 8 个连续字面量 (flag=1 + 8bit, 共 72bit)
                cnt -= 72
                out += bytes((chunk >> (9 * k)) & 0xFF for k in range(7, -1, -1))
            else:
                cnt -= 9
                tok = (buf >> cnt) & 0x1FF
                if tok & 0x100:
                    out.append(tok & 0xFF)
                else:
                    # 匹配: flag=0 已随高 9bit 读出 (offset 高 8 位),
                    # 再取 read2 bit = offset 余位 + 长度位
                    cnt -= read2
                    tok2 = (buf >> cnt) & ((1 << read2) - 1)
                    off = ((tok & 0xFF) << (read2 - len_bits)) | (tok2 >> len_bits)
                    if off == 0:  # EOD
                        break
                    run = (tok2 & len_mask) + MIN_MATCH
                    w = len(out)
                    # 环读位置 off 对应的输出流距离 (写头 = w%dict_size+1);
                    # d0=0 即整环; 首 dict_size 字节内引用未写区 → 零填充
                    d0 = ((w % dict_size) + 1 - off) & mask
                    if d0 == 0:
                        d0 = dict_size
                    if d0 > w:
                        pat = b"\x00" * (d0 - w) + bytes(out)
                    else:
                        pat = bytes(out[w - d0 :])
                    out += (pat * (run // d0 + 1))[:run]
            if out_len is not None and len(out) >= out_len:
                break
        return bytes(out)
