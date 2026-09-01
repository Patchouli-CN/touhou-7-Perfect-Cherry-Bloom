"""ECL 翻译基类 —— 回放录制(record) + 模板方法(translate)。

对照 EclManager.cpp 的逐帧执行语义: 本模块把 ``EclHost`` 的弹幕相关回调
实现成**录制** —— 不生成真实弹, 而是把每次回调的结构化参数快照 append 进
逐帧 trace(``TraceEvent``, frame 戳 + kind + 纯 JSON 可序列化的 data)。
子类实现 ``compile(trace)`` 把 trace 编译成目标格式(如妖归符卡 JSON,
见 engine/translate/youkai.py)。

record() 的执行口径与 engine/ecl_codec.EclCodec 一致(注册表拿作品 EclSpec),
VM/enemy/world 用默认值 —— 翻译场景没有真实对局, 玩家位置取引擎默认
(192, 400)(EclManager 的 g_Player 初始位), 可经 ``context`` 覆盖。

激光录制: spawn_laser_pattern 返回自增 int 假句柄(对应 C 的激光槽位),
后续 laser_set_angle/laser_add_angle/laser_stop 等按句柄追进同一条
laser trace 事件的 ``updates``/``stop_frame`` 字段。

AUTO 模式(compile_auto): 静态 compile_ir 出骨架, trace 按 TraceEvent.origin
溯源去重(指向已静态翻译指令的事件丢弃, origin=None 或指向被跳过指令的
保留)后经 compile() 编成动态补充段, 由子类 merge 合并 —— 静态出骨架,
动态补盲区, 已覆盖的不重复。

架构铁律: 本模块在 engine 层, 禁止 import games.* —— 作品数据只经
注册表(``get_game`` → EclSpec)获取。
"""

from __future__ import annotations

import abc
from enum import Enum
from typing import Any, Iterator

import msgspec

from ...logger import logger as log
from ...registry import get_game
from ..ecl import (
    BulletCommandData,
    EclEnemyState,
    EclFile,
    EclHost,
    EclInstr,
    EclOpcode,
    EclWorld,
    EnemyBulletShooter,
    EnemyLaserShooter,
    Vec3,
)
from ..ecl_codec import EclCodec
from .ir import IrIf, IrLoop, IrNode, IrOp, IrSeq, build_ir

__all__ = [
    "TraceEvent",
    "TranslateMode",
    "EclTranslatorBase",
    "decode_spellcard_name",
    "list_spellcards",
    "spellcard_name",
]


class TranslateMode(Enum):
    """ECL 翻译模式(translate 的 mode 参数)。

    - ``DIRECT``: 直接翻 —— 回放作品 VM 录逐帧 trace(record)再编译,
      忠实的运行时快照, 但循环/条件被展开压扁;
    - ``CONTROL``: 控制流翻 —— 不走 VM, 对指令流做静态控制流重建
      (parse_ir → IrNode 树)再编译, 保留循环/条件结构, 但是静态近似
      (依赖 ECL 变量的操作数可能丢失);
    - ``AUTO``: 静态骨架 + 动态补盲 —— compile_ir(ir) 出骨架, 再把 trace
      里**未被静态覆盖**的事件(溯源 provenance 为 None 或指向被静态跳过
      指令的)交给 compile() 编成补充段, merge 合并(见 compile_auto)。
    """

    DIRECT = "direct"
    CONTROL = "control"
    AUTO = "auto"


class TraceEvent(msgspec.Struct):
    """一条 trace 记录: 某帧发生的一次宿主回调(结构化参数快照)。

    kind: ``"bullets"`` / ``"laser"`` / ``"spellcard"`` / ``"spellcard_end"``;
    data 是纯 JSON 可序列化 dict(int/float/str/bool/list/dict)。
    laser 事件的 data 额外带 ``handle``/``updates``/``stop_frame``(见
    EclTranslatorBase 的激光录制)。

    origin 是溯源(AUTO 模式去重的地基): ``(sub_id, 指令文件 offset)`` =
    本事件由哪条指令触发; ``None`` = 运行时内部触发(如
    SET_SHOOT_INTERVAL 的自动射击, 不是某条发射指令产生的) —— 这正是
    静态翻译的盲区标记。DIRECT 模式不使用该字段(默认 None 兼容)。
    """

    frame: int
    kind: str
    data: dict[str, Any]
    origin: tuple[int, int] | None = None


def decode_spellcard_name(raw: bytes) -> str:
    """BeginSpellcard 内联名字节串 → 名字(XOR 0xAA + Shift-JIS)。

    对照 EclManager.cpp BeginSpellcard: 指令字 1..12 共 48 字节, 每字节
    XOR 0xAA, NUL 截断。th07 VM(games/th07/ecl_vm.py _begin_spellcard)
    已内联同款解码 —— 宿主回调收到的 name 是解码后的; 本 helper 供需要
    直接解原始指令字节的场景(或其他作品 VM 未解码时)。

    一般不需要直接调本函数: 拿整条指令用 ``spellcard_name(instr)``,
    列全文件符卡用 ``list_spellcards(ecl_file)``。
    """
    name_bytes = bytes(b ^ 0xAA for b in raw)
    return name_bytes.split(b"\x00", 1)[0].decode("shift_jis", errors="replace")


#: BEGIN_SPELLCARD 指令的内联名字段(字 1..12 = 原始字节 4..52, th07 布局,
#: EclManager.cpp BeginSpellcard)。布局知识收在本模块, 调用方不该自己切片
_NAME_SLICE = slice(4, 52)


def spellcard_name(instr: EclInstr) -> str:
    """BEGIN_SPELLCARD 指令 → 符卡名(内联名字段切片 + 解码一步到尾)。"""
    return decode_spellcard_name(instr.raw_arg_bytes()[_NAME_SLICE])


def list_spellcards(ecl_file: EclFile) -> list[tuple[int, str]]:
    """列出 ECL 文件里全部符卡 ``[(sub_id, 符卡名)]`` —— 找翻译目标用::

    for sub_id, name in list_spellcards(EclFile.parse(ecl_data)):
        print(sub_id, name)
    """
    out: list[tuple[int, str]] = []
    for sub_id, sub in enumerate(ecl_file.subs):
        for ins in sub:
            if ins.id == EclOpcode.BEGIN_SPELLCARD:
                out.append((sub_id, spellcard_name(ins)))
    return out


def _cmd_snapshot(c: BulletCommandData) -> dict[str, Any]:
    """BulletCommandData → 纯 JSON dict(C BulletCommand, ecl.py §C.2)。"""
    return {
        "type": c.type,
        "flag": c.flag,
        "duration": c.duration,
        "loop": c.loop_count,
        "speed": c.speed,
        "angle": c.angle,
    }


def _bullet_snapshot(props: EnemyBulletShooter) -> dict[str, Any]:
    """EnemyBulletShooter 参数快照(C EnemyBulletShooter, EnemyEclInstr.cpp)。"""
    return {
        "sprite": props.sprite,
        "sprite_offset": props.sprite_offset,
        "pos": [props.pos.x, props.pos.y, props.pos.z],
        "angle1": props.angle1,
        "angle2": props.angle2,
        "speed1": props.speed1,
        "speed2": props.speed2,
        "count1": props.count1,
        "count2": props.count2,
        "aim_mode": props.aim_mode,
        "flags": props.flags,
        "commands": [_cmd_snapshot(c) for c in props.commands if c.type],
    }


def _laser_snapshot(props: EnemyLaserShooter) -> dict[str, Any]:
    """EnemyLaserShooter 参数快照(C EnemyLaserShooter)。"""
    return {
        "sprite": props.sprite,
        "sprite_offset": props.sprite_offset,
        "pos": [props.pos.x, props.pos.y, props.pos.z],
        "angle1": props.angle1,
        "angle2": props.angle2,
        "speed1": props.speed1,
        "speed2": props.speed2,
        "start_offset": props.start_offset,
        "end_offset": props.end_offset,
        "start_length": props.start_length,
        "width": props.width,
        "start_time": props.start_time,
        "duration": props.duration,
        "end_time": props.end_time,
        "hitbox_start_time": props.hitbox_start_time,
        "hitbox_end_time": props.hitbox_end_time,
        "laser_type": props.type,
        "flags": props.flags,
        "commands": [_cmd_snapshot(c) for c in props.commands if c.type],
    }


def _iter_ir_ops(nodes: list[IrNode]) -> Iterator[IrOp]:
    """按文本序展开 IR 树的所有 IrOp(AUTO 统计静态指令覆盖面用)。"""
    for node in nodes:
        if isinstance(node, IrOp):
            yield node
        elif isinstance(node, IrSeq):
            yield from _iter_ir_ops(node.nodes)
        elif isinstance(node, IrLoop):
            yield from _iter_ir_ops(node.body)
        elif isinstance(node, IrIf):
            yield from _iter_ir_ops(node.if_true)
            yield from _iter_ir_ops(node.if_false)


class EclTranslatorBase(EclHost, abc.ABC):
    """ECL → 目标格式的翻译基类: 录制宿主 + 模板方法。

    用法::

        class MyTranslator(EclTranslatorBase):
            def compile(self, trace): ...

        out = MyTranslator("th07").translate(ecl_bytes, sub_id)

    构造经注册表拿作品 EclSpec(file_format + machine 类); 作品未注册抛
    NotRegisteredError(带已注册列表), 已注册但缺 ECL 维度抛 ValueError
    (与 EclCodec 同口径)。
    """

    def __init__(self, game: str) -> None:
        self.game = game
        spec = get_game(game, report_err=True)
        ecl_spec = spec.ecl
        if ecl_spec is None:
            raise ValueError(
                f"作品 {game!r} 已注册, 但缺 ECL 维度"
                f"(需要 @register_ecl({game!r}, file_format=...) 装饰 ECL 虚拟机类)"
            )
        self._ecl_spec = ecl_spec
        self._codec = EclCodec(game)
        # 录制状态(record() 时重置)
        self.frame = 0
        self.trace: list[TraceEvent] = []
        self._laser_events: dict[int, TraceEvent] = {}  # 假句柄 → laser 事件
        self._next_handle = 1
        self.world: EclWorld | None = None  # 回放期世界(回调快照用)
        self.last_sub_id: int = -1  # 最近一次 record 的 sub id(compile 命名用)
        self.last_frame_count: int = 0  # 最近一次 record 的总帧数
        self.last_finished: bool = False  # 脚本是否自然结束(否则被 max_frames 截断)
        # 回放期的 VM 实例(record 期间持有, 结束后清 None): 宿主回调经它读
        # executing_instr 得到 TraceEvent 的 origin 溯源(EclHost 回调拿不到
        # machine, 但本类是 host 的拥有者)
        self._machine: Any = None
        # compile_ir 实现登记的"静态未覆盖"指令 offset 集合(每次 compile_ir
        # 开头重置): 静态跳过/求值失败/不可翻译的指令。AUTO 去重用 —— trace
        # 里 origin 指向这些 offset 的事件要保留给动态补充段
        self._skipped_offsets: set[int] = set()

    # ---- 模板方法 ----

    def record(
        self,
        ecl_data: bytes,
        sub_id: int,
        *,
        max_frames: int = 7200,
        context: dict[str, Any] | None = None,
    ) -> list[TraceEvent]:
        """回放 sub_id 并录制逐帧 trace。

        ``context``: 覆盖默认上下文 —— 普通键写到 EclWorld 字段(如
        ``{"difficulty": 2, "rank": 16}``), ``"enemy."`` 前缀键写到
        EclEnemyState(如 ``{"enemy.life": 5000}``); 未知键报 ValueError。
        翻译场景没有真实对局: 玩家位置取引擎默认 (192, 400), enemy.life
        默认给 100000(回放里 SET_SHOOT_INTERVAL 的自动射击只在 life>0
        时触发, 见 ecl_base.py _frame_update; sub 自己的 SET_LIFE 会覆盖)。
        脚本自然结束(VM step 返回 False)或到达 max_frames 停止。
        """
        ecl_file: EclFile = self._codec.decode(ecl_data)
        if not (0 <= sub_id < len(ecl_file.subs)):
            raise ValueError(
                f"sub_id {sub_id} 越界(该 ECL 共 {len(ecl_file.subs)} 个 sub)"
            )
        world = EclWorld()
        enemy = EclEnemyState()
        enemy.life = 100_000
        for key, value in (context or {}).items():
            if key.startswith("enemy."):
                field = key[6:]
                if not hasattr(enemy, field):
                    raise ValueError(f"未知 EclEnemyState 上下文字段: {field!r}")
                setattr(enemy, field, value)
            elif hasattr(world, key):
                setattr(world, key, value)
            else:
                raise ValueError(f"未知 EclWorld 上下文字段: {key!r}")

        self.frame = 0
        self.trace = []
        self._laser_events = {}
        self._next_handle = 1
        self.world = world
        self.last_sub_id = sub_id

        machine = self._ecl_spec.machine(ecl_file, enemy=enemy, world=world, host=self)
        self._machine = machine
        machine.start(sub_id)
        frames = 0
        for _ in range(max_frames):
            if not machine.step():
                break
            self.frame += 1
            frames += 1
        self._machine = None
        self.last_frame_count = frames
        self.last_finished = machine.finished
        if not machine.finished:
            log.debug(
                "ECL 回放到达 max_frames={} 未自然结束(sub {}), trace 截断",
                max_frames,
                sub_id,
            )
        return self.trace

    def translate(
        self,
        ecl_data: bytes,
        sub_id: int,
        *,
        mode: TranslateMode = TranslateMode.DIRECT,
        **kw: Any,
    ) -> dict:
        """模板方法: 按模式分发 → 可 json.dumps 的 dict。

        - ``TranslateMode.DIRECT``: record() → compile(trace)(回放录制,
          ``**kw`` 透传给 record: max_frames/context);
        - ``TranslateMode.CONTROL``: parse_ir() → compile_ir(ir)(静态控制流,
          不走 VM, record 参数不适用, 传了记 debug 忽略);
        - ``TranslateMode.AUTO``: parse_ir() + record() → compile_auto(ir,
          trace)(静态骨架 + 动态补盲, ``**kw`` 透传给 record)。
        """
        if mode is TranslateMode.CONTROL:
            if kw:
                log.debug("CONTROL 模式不走 VM 回放, record 参数被忽略: {}", sorted(kw))
            return self.compile_ir(self.parse_ir(ecl_data, sub_id))
        if mode is TranslateMode.AUTO:
            ir = self.parse_ir(ecl_data, sub_id)
            trace = self.record(ecl_data, sub_id, **kw)
            return self.compile_auto(ir, trace)
        trace = self.record(ecl_data, sub_id, **kw)
        return self.compile(trace)

    def parse_ir(self, ecl_data: bytes, sub_id: int) -> IrSeq:
        """CONTROL 模式前半: 静态解析 sub 指令流 → 控制流 IR(ir.py build_ir)。"""
        ecl_file: EclFile = self._codec.decode(ecl_data)
        if not (0 <= sub_id < len(ecl_file.subs)):
            raise ValueError(
                f"sub_id {sub_id} 越界(该 ECL 共 {len(ecl_file.subs)} 个 sub)"
            )
        self.last_sub_id = sub_id
        return build_ir(ecl_file, sub_id)

    @abc.abstractmethod
    def compile(self, trace: list[TraceEvent]) -> dict:
        """把 trace 编译成目标格式(dict, 必须可 json.dumps)。"""

    def compile_ir(self, ir: IrSeq) -> dict:
        """把控制流 IR 编译成目标格式(CONTROL 模式; 子类按需重写)。"""
        raise NotImplementedError(
            f"{type(self).__name__} 未实现 compile_ir: "
            "该翻译器不支持 CONTROL 模式(静态控制流翻译)"
        )

    def compile_auto(self, ir: IrSeq, trace: list[TraceEvent]) -> dict:
        """AUTO 模式编排: 静态出骨架, 动态补盲区, 已覆盖的不重复。

        - 静态: compile_ir(ir) 得骨架; 静态未覆盖的指令 offset 集合由
          compile_ir 实现登记到 ``self._skipped_offsets``;
        - 动态: trace 过滤 —— origin 指向"本 sub 且已被静态翻译的指令"的
          事件丢弃; origin 为 None(运行时内部触发, 如自动射击)、指向被
          跳过指令、或来自其他 sub(中断/周期回调, parse_ir 看不到)的事件
          保留, 交给 compile()(DIRECT 的折叠逻辑)编成补充段;
        - 合并: merge(static, residual)(目标格式相关, 子类实现)。
        """
        static_result = self.compile_ir(ir)
        skipped = self._skipped_offsets
        all_offsets = {op.instr.offset for op in _iter_ir_ops(ir.nodes)}
        residual = [
            ev
            for ev in trace
            # spellcard 事件是运行时身份元数据(难度分支宣言哪张卡只有跑起来
            # 才知道, 静态 compile_ir 拿的是文本序第一条)——始终保留,
            # 由 merge 决定 display 名以运行时宣言为准
            if ev.kind == "spellcard"
            or ev.origin is None
            or ev.origin[0] != self.last_sub_id
            or ev.origin[1] in skipped
        ]
        residual_result = self.compile(residual)
        result = self.merge(static_result, residual_result)
        log.info(
            "AUTO 翻译覆盖: 静态翻译 {} 条指令 / 动态补 {} 条事件 / 静态未覆盖 {} 条",
            len(all_offsets - skipped),
            len(residual),
            len(skipped),
        )
        return result

    def merge(self, static_result: dict, residual_result: dict) -> dict:
        """AUTO 模式: 合并静态骨架与动态补充段(目标格式相关; 子类实现)。"""
        raise NotImplementedError(
            f"{type(self).__name__} 未实现 merge: "
            "该翻译器不支持 AUTO 模式(静态骨架与动态补充段的合并是目标格式相关的)"
        )

    def _provenance(self) -> tuple[int, int] | None:
        """当前宿主回调的溯源: (sub_id, 指令 offset); 非指令触发 → None。

        回放期间 translator 持有 VM 实例(record 挂到 self._machine):
        VM 在 _execute 里把当前指令挂上 executing_instr, 帧收尾
        (_frame_update: 自动射击/ex 指令)期间为 None(ecl_base.py)。
        """
        m = self._machine
        instr = getattr(m, "executing_instr", None) if m is not None else None
        if instr is None:
            return None
        sub_id = getattr(getattr(m, "current", None), "sub_id", -1)
        return (sub_id, instr.offset)

    # ---- 弹幕录制(EclHost 回调面, ecl.py EclHost) ----

    def spawn_bullet_pattern(self, props: EnemyBulletShooter) -> None:
        self.trace.append(
            TraceEvent(
                self.frame, "bullets", _bullet_snapshot(props), self._provenance()
            )
        )

    # ---- 激光录制(假句柄追踪) ----

    def spawn_laser_pattern(self, props: EnemyLaserShooter) -> int:
        handle = self._next_handle
        self._next_handle += 1
        data = _laser_snapshot(props)
        data["handle"] = handle
        data["updates"] = []
        data["stop_frame"] = None
        event = TraceEvent(self.frame, "laser", data, self._provenance())
        self.trace.append(event)
        self._laser_events[handle] = event
        return handle

    def _laser_update(self, handle: Any, op: str, **params: Any) -> None:
        event = self._laser_events.get(handle)
        if event is None:
            log.debug("激光操作落到未知句柄 {} (op={}), 已跳过", handle, op)
            return
        event.data["updates"].append({"frame": self.frame, "op": op, **params})

    def laser_set_angle(self, handle: Any, angle: float) -> None:
        self._laser_update(handle, "set_angle", angle=angle)

    def laser_add_angle(self, handle: Any, delta: float) -> None:
        self._laser_update(handle, "add_angle", delta=delta)

    def laser_aim_at_player(self, handle: Any, offset: float) -> None:
        self._laser_update(handle, "aim_at_player", offset=offset)

    def laser_set_pos(self, handle: Any, pos: Vec3) -> None:
        self._laser_update(handle, "set_pos", pos=[pos.x, pos.y, pos.z])

    def laser_set_hide_warning(self, handle: Any, v: int) -> None:
        self._laser_update(handle, "hide_warning", value=v)

    def laser_stop(self, handle: Any) -> None:
        event = self._laser_events.get(handle)
        if event is None:
            log.debug("laser_stop 落到未知句柄 {}, 已跳过", handle)
            return
        event.data["stop_frame"] = self.frame

    def laser_set_start_length(self, handle: Any, v: float) -> None:
        self._laser_update(handle, "set_start_length", value=v)

    def laser_set_offsets(self, handle: Any, start: float, end: float) -> None:
        self._laser_update(handle, "set_offsets", start=start, end=end)

    # ---- 结构事件 ----

    def begin_spellcard(
        self, enemy: EclEnemyState, gui_id: int, spellcard_idx: int, name: str
    ) -> None:
        # name 已由作品 VM 解码(th07: XOR 0xAA + Shift-JIS, ecl_vm.py
        # _begin_spellcard); 这里直接快照
        self.trace.append(
            TraceEvent(
                self.frame,
                "spellcard",
                {"gui_id": gui_id, "spellcard_idx": spellcard_idx, "name": name},
                self._provenance(),
            )
        )

    def end_spellcard(self, enemy: EclEnemyState) -> None:
        self.trace.append(
            TraceEvent(self.frame, "spellcard_end", {}, self._provenance())
        )

    # ---- 其余回调: 翻译场景无关, 跳过并记 debug(不无声丢弃) ----

    def spawn_enemy(self, sub_id, pos, life, item_drop, score, mirror, context_args):
        log.debug("翻译回放: spawn_enemy(sub={}) 不可翻译, 已跳过", sub_id)
        return None

    def spawn_item(self, pos: Vec3, item_type: int) -> None:
        log.debug("翻译回放: spawn_item(type={}) 不可翻译, 已跳过", item_type)

    def remove_all_bullets(self, spawn_items: bool) -> None:
        log.debug("翻译回放: remove_all_bullets 不可翻译, 已跳过")

    def play_sound(self, idx: int) -> None:
        log.debug("翻译回放: play_sound({}) 不可翻译, 已跳过", idx)
