""" 关卡数据(.std) —— Pythonic。

对照 th07 反编译 `Stage.cpp/.hpp` 还原 .std 全格式:

- 头(StdRawHeader, 1168 字节): objectsCount/quadCount(i16×2) +
  facesOffset/scriptOffset(u32×2) + unused + 关卡名[128] +
  bgmNames[4][128] + bgmPaths[4][128]。
- 头后紧跟 objectsCount 个 i32 偏移表(相对文件头), 各指向一个
  StdRawObject: u16 id + i8 zLevel + i8 flags + Float3 pos + Float3 size,
  之后内联 quad 链(StdRawQuadBasic: i16 type/byteSize/anmScript/vmIndex +
  Float3 pos + Float2 size, 28 字节), 以 type<0 结束。
- facesOffset 处是实例表(StdRawInstance: i16 id + i16 pad + Float3 pos,
  16 字节), 以 id<0 结束; id 是 objects 数组下标(非 object.id)。
- scriptOffset 处是场景脚本(StdRawInstr: i32 frame + i16 opcode +
  i16 size + 12 字节参数, 定长 20 字节), 以 frame==-1 结束。

脚本指令集(opcode, 语义见 Stage::OnUpdate):
0=世界原点pos(Float3) 1=雾设置(u32色,f32近,f32远) 2=雾插值(i32帧数)
3=停脚本 4=跳转(i32指令idx,i32时刻) 5=相机pos目标(Float3)
6/8/10/12=pos/lookAt/up/fov 插值时长+ease 7=lookAt目标 9=up目标
11=fov目标(f32) 13=清屏色(u32) 14..18=相机pos贝塞尔(起点/终点/
切线起点/切线终点/时长) 19..23=lookAt 同 24..28=up 同
29/30=全屏背景VM1/2 的 anm script(i32, 负=隐藏) 31=等待标记(i32)
"""

from __future__ import annotations

import struct
import msgspec

_HEADER_SIZE = 1168      # StdRawHeader
_OBJECT_HEAD_SIZE = 28   # StdRawObject 到 firstQuad 之前
_QUAD_SIZE = 28          # StdRawQuadBasic
_INSTANCE_SIZE = 16      # StdRawInstance
_INSTR_SIZE = 20         # StdRawInstr(8 字节头 + 3×AnyArg)

#: 脚本指令名(调试用; 语义见模块 docstring)
INSTR_NAMES = {
    0: "set_pos", 1: "set_fog", 2: "fog_interp", 3: "stop", 4: "jump",
    5: "cam_pos", 6: "cam_pos_interp", 7: "cam_lookat", 8: "cam_lookat_interp",
    9: "cam_up", 10: "cam_up_interp", 11: "cam_fov", 12: "cam_fov_interp",
    13: "clear_color", 14: "cam_pos_start", 15: "cam_pos_end",
    16: "cam_pos_tan_start", 17: "cam_pos_tan_end", 18: "cam_pos_bezier",
    19: "cam_lookat_start", 20: "cam_lookat_end", 21: "cam_lookat_tan_start",
    22: "cam_lookat_tan_end", 23: "cam_lookat_bezier", 24: "cam_up_start",
    25: "cam_up_end", 26: "cam_up_tan_start", 27: "cam_up_tan_end",
    28: "cam_up_bezier", 29: "bg_vm1", 30: "bg_vm2", 31: "wait_label",
}


def _sjis(raw: bytes) -> str:
    try:
        return raw.decode("cp932", "replace")
    except Exception:
        return raw.decode("utf-8", "replace")


class StdQuad(msgspec.Struct, frozen=True):
    """一个 3D quad(StdRawQuadBasic)。

    type: 0=世界空间 quad(Draw3); anm_script: stgNbg.anm 局部 script id;
    pos: 相对实例原点的偏移; size: 世界尺寸(0 表示用 sprite 原尺寸)。
    """

    type: int
    anm_script: int
    pos: tuple[float, float, float]
    size: tuple[float, float]


class StdObject(msgspec.Struct, frozen=True):
    """一个场景物件(StdRawObject): 一组 quad + 剔除参数。"""

    id: int
    z_level: int                 # 0..3, 两个渲染 pass(0/1 高, 2/3 低)
    pos: tuple[float, float, float]
    size: tuple[float, float, float]
    quads: tuple[StdQuad, ...]


class StdInstance(msgspec.Struct, frozen=True):
    """物件实例(StdRawInstance): object_idx 是 objects 数组下标。"""

    object_idx: int
    pos: tuple[float, float, float]


class StdInstr(msgspec.Struct, frozen=True):
    """一条场景脚本指令(StdRawInstr); args_i/args_f 是 12 字节参数的两种视图。"""

    frame: int
    opcode: int
    args_i: tuple[int, int, int]
    args_f: tuple[float, float, float]

    @property
    def name(self) -> str:
        return INSTR_NAMES.get(self.opcode, f"op{self.opcode}")

    def vec(self) -> tuple[float, float, float]:
        return self.args_f


class ScriptEvent(msgspec.Struct):
    """一条关卡脚本事件(按帧触发)。兼容旧解析(保留)。"""

    at_frame: int
    opcode: int
    args: tuple[int, ...]

    @property
    def slot(self) -> int:
        return self.args[0] if self.args else 0


class Stage(msgspec.Struct):
    """一个关。"""

    index: int
    title: str
    bgm_names: tuple[str, ...]
    bgm_paths: tuple[str, ...]
    script: list[ScriptEvent] = None  # type: ignore[assignment]
    objects: list[StdObject] = msgspec.field(default_factory=list)
    instances: list[StdInstance] = msgspec.field(default_factory=list)
    instrs: list[StdInstr] = msgspec.field(default_factory=list)
    quad_count: int = 0

    @classmethod
    def read(cls, data: bytes, index: int) -> "Stage":
        if len(data) < _HEADER_SIZE:
            raise ValueError("std 过短")
        objects_count, quad_count, faces_off, script_off = struct.unpack_from(
            "<hhII", data, 0)
        name = _sjis(data[16:144].split(b"\x00")[0])
        bgm_names = tuple(_sjis(data[144 + i * 128: 144 + i * 128 + 128].split(b"\x00")[0]) for i in range(4))
        bgm_paths = tuple(_sjis(data[656 + i * 128: 656 + i * 128 + 128].split(b"\x00")[0]) for i in range(4))
        st = cls(index, name, bgm_names, bgm_paths, [])
        st.quad_count = quad_count
        st.objects = cls._objects(data, objects_count)
        st.instances = cls._instances(data, faces_off)
        st.instrs = cls._instrs(data, script_off)
        # 兼容旧字段: 同一脚本区的扁平视图
        st.script = [ScriptEvent(i.frame, i.opcode, i.args_i) for i in st.instrs]
        return st

    @staticmethod
    def _objects(data: bytes, count: int) -> list[StdObject]:
        out: list[StdObject] = []
        for i in range(count):
            off = struct.unpack_from("<i", data, _HEADER_SIZE + i * 4)[0]
            if not (0 < off + _OBJECT_HEAD_SIZE <= len(data)):
                continue
            oid, z_level, _flags = struct.unpack_from("<Hbb", data, off)
            pos = struct.unpack_from("<3f", data, off + 4)
            size = struct.unpack_from("<3f", data, off + 16)
            quads: list[StdQuad] = []
            qoff = off + _OBJECT_HEAD_SIZE
            while qoff + _QUAD_SIZE <= len(data):
                qtype, qsize, anm_script, _vm = struct.unpack_from("<4h", data, qoff)
                if qtype < 0 or qsize <= 0:
                    break
                qpos = struct.unpack_from("<3f", data, qoff + 8)
                qsz = struct.unpack_from("<2f", data, qoff + 20)
                quads.append(StdQuad(qtype, anm_script, qpos, qsz))
                qoff += qsize
            out.append(StdObject(oid, z_level, pos, size, tuple(quads)))
        return out

    @staticmethod
    def _instances(data: bytes, off: int) -> list[StdInstance]:
        out: list[StdInstance] = []
        while 0 < off + _INSTANCE_SIZE <= len(data):
            oid, _pad = struct.unpack_from("<2h", data, off)
            if oid < 0:
                break
            pos = struct.unpack_from("<3f", data, off + 4)
            out.append(StdInstance(oid, pos))
            off += _INSTANCE_SIZE
        return out

    @staticmethod
    def _instrs(data: bytes, off: int) -> list[StdInstr]:
        out: list[StdInstr] = []
        while 0 < off + _INSTR_SIZE <= len(data):
            frame, opcode, size = struct.unpack_from("<ihh", data, off)
            if frame == -1:
                break
            args_i = struct.unpack_from("<3i", data, off + 8)
            args_f = struct.unpack_from("<3f", data, off + 8)
            out.append(StdInstr(frame, opcode, args_i, args_f))
            off += size if size >= _INSTR_SIZE else _INSTR_SIZE
        return out

    @property
    def main_bgm(self) -> str:
        """主 BGM 路径(取第一个非空的)。"""
        return next((p for p in self.bgm_paths if p), "")

    def __repr__(self) -> str:
        return f"<Stage{self.index} {self.title!r} bgm={self.main_bgm!r}>"
