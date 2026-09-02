"""TH08(东方永夜抄) .ecl 文件格式 —— EclFile 的 th08 变体。

对照 th08 反编译源码(Reference/th08-ref/src/):
- 文件头(EclManager.hpp:181-190): ``version u32``(必须 == 0x800,
  EclManager.cpp:38) + ``subCount i16`` + ``timelineCount i16`` +
  ``timelineOffsets[16] u32`` @0x8 + ``subOffsets[] u32`` @0x48;
  偏移都是文件相对值(Load 时加基址, EclManager.hpp:179-180 注释)。
- 指令头(EclManager.hpp:147-156): 12 字节, 与 th07 逐字段同构
  (time i32 / opcode i16 / nextOffset i16 / reserved u8 / difficultyMask u8 /
  operandFlags u16) —— 直接复用 engine 的 ``EclInstr``: size=nextOffset
  (到下一指令的步长)、unused=reserved、skip_difficulty=difficultyMask、
  param_mask=operandFlags。offset/size 语义与 th07 完全一致
  (ecl_base._advance/_do_jump 依赖, 不许变)。
- 时间轴指令(EnemyManager.hpp:419-430): 与 ECL 本体不同 —— time i32 /
  opcode i16 / size u8 / difficultyMask u8 / args[7]×4 字节, 操作数全 raw
  (无 operandFlags), 新建 ``EclTimelineInstrTh08`` 表示。

round-trip 承诺同 th07(见 engine/ecl.py EclFile docstring):
``serialize(parse(data)) == data`` 逐字节成立。version 只接受 0x800,
故 serialize 恒定写回 0x800; 16 槽 timelineOffsets 原值/时间轴截短终止
记录由基类字段(_timeline_offsets/_timeline_trailing)保留并原样写回。
"""

from __future__ import annotations

import struct

from typing import cast

import msgspec

from ...engine.ecl import EclFile, EclInstr, EclTimelineInstr
from ...exceptions import EclParseError
from ...utils import i32

# 指令头(与 th07 同构, EclManager.hpp:147-156):
# time, opcode, nextOffset, reserved, difficultyMask, operandFlags
_INSTR_HEADER = struct.Struct("<IhhBBH")
_INSTR_HEADER_SIZE = _INSTR_HEADER.size  # 12
# 时间轴指令头(EnemyManager.hpp:419-430): time, opcode, size, difficultyMask
_TIMELINE_HEADER = struct.Struct("<ihBB")
_TIMELINE_HEADER_SIZE = _TIMELINE_HEADER.size  # 8

_ECL_VERSION = 0x800  # EclManager.cpp:38 的硬性校验
_HEADER_SIZE = 0x48  # version(4) + counts(4) + timelineOffsets[16](64)

# sub 指令流终止记录(同 th07)
_TERMINATOR_ID = -1


class EclTimelineInstrTh08(msgspec.Struct, frozen=True):
    """一条 th08 时间轴指令(36 字节: 8 字节头 + args[7], 操作数全 raw)。"""

    offset: int  # 文件内绝对偏移
    time: int  # i32: 到点执行; <0 表示时间轴结束
    opcode: int  # i16: 0-16(EnemyTimeline.cpp:134-283)
    size: int  # u8: 整条字节数(含 8 字节头)
    difficulty_mask: int  # u8 位掩码(同 ECL 指令的 difficultyMask)
    args: tuple[int, ...]  # u32 字(固定 7 个, int/float 混合按 opcode 解释)

    def arg_int(self, idx: int) -> int:
        return i32(self.args[idx])

    def arg_float(self, idx: int) -> float:
        f: float = struct.unpack("<f", struct.pack("<I", self.args[idx]))[0]
        return f


class EclFileTh08(EclFile):
    """th08 的 .ecl: 0x800 版本头 + 16 槽时间轴偏移表 + u32 偏移。

    继承 EclFile 的字段/契约(subs/timelines/instr_at/sub_offset/
    opcode_histogram), 只重写 parse/serialize; 时间轴条目是
    ``EclTimelineInstrTh08``(基类字段类型注记的是 th07 形态, 消费方按
    作品各自取用 —— th08 的时间轴 runner 是阶段 2 单 B 的工作)。
    """

    @classmethod
    def parse(cls, data: bytes) -> "EclFileTh08":
        if len(data) < _HEADER_SIZE:
            raise EclParseError("文件太小, 没有完整 EclRawHeader")
        version, sub_count, timeline_count = struct.unpack_from("<Ihh", data, 0)
        if version != _ECL_VERSION:
            raise EclParseError(
                f"非法 ECL version: {version:#x} (期望 0x800, EclManager.cpp:38)"
            )
        if not (0 <= sub_count <= 4096 and 0 <= timeline_count <= 16):
            raise EclParseError(
                f"非法 header: subCount={sub_count} timelineCount={timeline_count}"
            )
        timeline_offsets = struct.unpack_from("<16I", data, 8)
        sub_offsets = struct.unpack_from(f"<{sub_count}I", data, _HEADER_SIZE)

        # sub 指令流解析与 th07 逐字节同构(指令头同为 12 字节,
        # time=0xFFFFFFFF/opcode=-1 的记录结尾)
        subs: list[tuple[EclInstr, ...]] = []
        instr_at: dict[int, EclInstr] = {}
        for sub_id, off in enumerate(sub_offsets):
            instrs: list[EclInstr] = []
            while True:
                if off + _INSTR_HEADER_SIZE > len(data):
                    raise EclParseError(f"sub {sub_id}: 指令越界 (off={off})")
                time, op_id, size, unused, skip, mask = _INSTR_HEADER.unpack_from(
                    data, off
                )
                if size < _INSTR_HEADER_SIZE or (size - _INSTR_HEADER_SIZE) % 4 != 0:
                    raise EclParseError(f"sub {sub_id}: 非法 size={size} (off={off})")
                if off + size > len(data):
                    raise EclParseError(f"sub {sub_id}: 指令截断 (off={off})")
                n_args = (size - _INSTR_HEADER_SIZE) // 4
                args = struct.unpack_from(f"<{n_args}I", data, off + _INSTR_HEADER_SIZE)
                instr = EclInstr(off, time, op_id, size, unused, skip, mask, args)
                instrs.append(instr)
                instr_at[off] = instr
                off += size
                if instr.is_terminator:
                    break
            subs.append(tuple(instrs))

        timelines: list[tuple[EclTimelineInstrTh08, ...]] = []
        trailing: list[bytes] = []
        for i in range(timeline_count):
            off = timeline_offsets[i]
            tl: list[EclTimelineInstrTh08] = []
            while off < len(data):  # 时间轴可以没有终止符, 直接延伸到 EOF
                if off + _TIMELINE_HEADER_SIZE > len(data):
                    # 尾部可能有截短终止记录(time<0), 同 th07 的保留策略
                    tail = (
                        struct.unpack_from("<h", data, off)[0]
                        if off + 2 <= len(data)
                        else -1
                    )
                    if tail < 0:
                        break
                    raise EclParseError(f"timeline {i}: 越界 (off={off})")
                time, opcode, size, diff_mask = _TIMELINE_HEADER.unpack_from(data, off)
                if size == 0 and time < 0:
                    # 截短终止记录: 只有 8 字节头, size 字段为 0(真实数据
                    # ecldata8.ecl tl1; 终止符另有 size=8/32 形态, 走正常分支)
                    tl.append(
                        EclTimelineInstrTh08(off, time, opcode, size, diff_mask, ())
                    )
                    off += _TIMELINE_HEADER_SIZE
                    break
                if (
                    size < _TIMELINE_HEADER_SIZE
                    or (size - _TIMELINE_HEADER_SIZE) % 4 != 0
                ):
                    raise EclParseError(f"timeline {i}: 非法 size={size} (off={off})")
                if off + size > len(data):
                    raise EclParseError(f"timeline {i}: 指令截断 (off={off})")
                n_args = (size - _TIMELINE_HEADER_SIZE) // 4
                args = struct.unpack_from(
                    f"<{n_args}I", data, off + _TIMELINE_HEADER_SIZE
                )
                tl.append(EclTimelineInstrTh08(off, time, opcode, size, diff_mask, args))
                off += size
                if time < 0:
                    break
            timelines.append(tuple(tl))
            # 解析未消费的字节(截短终止记录)原样保留, 供 serialize 还原
            nxt = timeline_offsets[i + 1] if i + 1 < timeline_count else len(data)
            trailing.append(data[off:nxt])

        return cls(
            sub_count,
            timeline_count,
            subs,
            # 基类 timelines 注记的是 th07 形态(engine 层按分层纪律无法引用
            # 本模块的 EclTimelineInstrTh08), 消费方按作品各自取用, 此处收窄
            cast("list[tuple[EclTimelineInstr, ...]]", timelines),
            instr_at,
            tuple(timeline_offsets),
            trailing,
        )

    def serialize(self) -> bytes:
        """把解析结果写回 th08 .ecl 二进制(parse 的逆运算)。

        承诺 ``serialize(parse(data)) == data`` 逐字节成立(偏移修正模式对照
        engine/ecl.py EclFile.serialize): 各段写回各自记录的绝对偏移,
        16 槽 timelineOffsets 原值与截短终止记录按 parse 保留字段写回。
        """
        header_size = _HEADER_SIZE + 4 * self.sub_count
        end = header_size
        for sub in self.subs:
            for ins in sub:
                end = max(end, ins.offset + ins.size)
        for i, tl in enumerate(self.timelines):
            tail_start = self._timeline_offsets[i]
            for tins in tl:
                # size=0 的截短终止记录实际占位 8 字节头(见 parse)
                stride = max(tins.size, _TIMELINE_HEADER_SIZE)
                end = max(end, tins.offset + stride)
                tail_start = tins.offset + stride
            end = max(end, tail_start + len(self._timeline_trailing[i]))
        buf = bytearray(end)

        struct.pack_into("<Ihh", buf, 0, _ECL_VERSION, self.sub_count, self.timeline_count)
        struct.pack_into("<16I", buf, 8, *self._timeline_offsets)
        for sub_id, sub in enumerate(self.subs):
            if not sub:
                continue  # 空 sub 无偏移可还原(手工构造的边界情形)
            struct.pack_into("<I", buf, _HEADER_SIZE + 4 * sub_id, sub[0].offset)
            for ins in sub:
                _INSTR_HEADER.pack_into(
                    buf,
                    ins.offset,
                    ins.time,
                    ins.id,
                    ins.size,
                    ins.unused,
                    ins.skip_difficulty,
                    ins.param_mask,
                )
                if ins.args:
                    struct.pack_into(
                        f"<{len(ins.args)}I",
                        buf,
                        ins.offset + _INSTR_HEADER_SIZE,
                        *ins.args,
                    )
        for i, tl in enumerate(self.timelines):
            tail_start = self._timeline_offsets[i]
            # 本类 timelines 实际元素恒为 EclTimelineInstrTh08(见 parse 末注释);
            # 循环变量另起名, 避免与上文 th07 形态的 tins 推断类型冲突
            for tins08 in cast(tuple[EclTimelineInstrTh08, ...], tl):
                _TIMELINE_HEADER.pack_into(
                    buf,
                    tins08.offset,
                    tins08.time,
                    tins08.opcode,
                    tins08.size,
                    tins08.difficulty_mask,
                )
                if tins08.args:
                    struct.pack_into(
                        f"<{len(tins08.args)}I",
                        buf,
                        tins08.offset + _TIMELINE_HEADER_SIZE,
                        *tins08.args,
                    )
                tail_start = tins08.offset + max(tins08.size, _TIMELINE_HEADER_SIZE)
            tail = self._timeline_trailing[i]
            buf[tail_start : tail_start + len(tail)] = tail
        return bytes(buf)
