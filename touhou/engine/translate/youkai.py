"""ECL → Youkai-Homecoming 妖归符卡 JSON 的编译器。

``YoukaiDanmakuTranslator.compile(trace)`` 把 base.py 录制的逐帧 trace 折叠成
SpellDefinition dict(格式规范见妖归 skill 的 references/schema.md; 映射依据
references/ecl_migration_subskill.md)。

核心思路 —— **声明式近似命令式**: ECL 是逐帧命令式回放(同样的 pattern 按
固定间隔一帧一帧发), 妖归是声明式动作 + 条件门控。compile 把相邻多帧以
固定间隔重复发射的同构 pattern 折叠成 ``conditional``(``type`` 必须写
``"conditional"``, DFU codec 靠 type 分派) + ``tick_interval`` 门控的一条
fire 动作, 而不是一帧一条平铺。

映射要点(subskill 对照):
- sprite/颜色 → bullet/color: 默认表 = th07 弹型(etama.anm 实测主色提取,
  工具 scratch_dbg/extract_bullet_colors.py); 其他作品经构造参数覆盖。
- arms/rings(count1/count2) → count/pattern(ring); 多层环速度插值近似为
  ``rand(lo, hi)``(声明式没有逐层速度的对应物)。
- 速度单位换算: 引擎像素/帧 ≠ 妖归单位(subskill 参考 引擎 0.8 ≈ 妖归 0.4),
  构造参数 ``speed_scale`` 默认 0.5, 需按作品手感微调。
- flags/commands: TARGET_ANGLE(0x20, 每 tick 沿自身方向加减速+旋转,
  BulletManager.cpp UpdateBulletTargetAngle) → ``formula``/``polar`` mover
  (自身坐标系 x=forward, y=right; 三角函数必须 ``sin_rad``/``cos_rad``);
  TARGET_VEL(0x10, 每帧叠加世界坐标速度矢量) → ``acceleration`` mover
  (世界坐标, 恰好同语义)。命令 duration 有限时包 ``composite`` 分段。
- 激光三段时序(start_time/duration/end_time) →
  ``setup_prepare``/``lifetime``/``setup_end``; 旋转后停(laser_stop) →
  ``composite`` mover 前 ``rotate`` 后 ``zero``。
- 单次事件(非重复)用 ``compare``(phase_tick == 帧号)门控 —— 妖归没有
  真正的 one-shot 条件, tick_interval 会按周期重复。

翻译不了的 trace 事件(spellcard_end 等)记 log.debug 跳过; begin_spellcard
的符卡名进 display.name(名字由作品 VM 解码, th07 为 XOR 0xAA + Shift-JIS,
见 base.py decode_spellcard_name)。

CONTROL 模式(compile_ir, 静态控制流): 不走 VM, 把 ir.py 重建的控制流 IR
直接映射成妖归动作 —— IrLoop → ``repeat``(无限/次数未知的循环 count 取
_INFINITE_LOOP_COUNT 近似上限), IrIf → ``conditional``, IrOp 里的弹幕/
激光指令 → ``fire_danmaku``/``fire_laser``(常量操作数直接翻, 映射表与
compile 共用同一套 helper)。循环体的时间语义经 ``delay`` 表达: 迭代 k
里 time=T 的发射在绝对帧 T + k*period 触发 →
``delay_ticks = "$i * period + T"``。依赖 ECL 变量的操作数: 能识别成
"初值 + 每轮步进"仿射形式的映射成 NumberExpr 简写(``$i * k + b``, 仅
angle1; SET_FLOAT 初值 + ADD_FLOAT 步进的经典旋转发射器模式), 映射不了
的 log.warning 并跳过该指令。静态近似的边界见 docs/ecl_to_youkai_migration.md
"翻译模式"一节。

AUTO 模式(compile_auto 编排 + 本类 merge): 静态骨架 + 动态补盲。compile_ir
期间静态未覆盖的指令 offset 登记进 ``self._skipped_offsets``(变量求值
失败的弹幕/激光指令 + 不可静态翻译的指令); trace 里 origin 指向已静态
翻译指令的事件被去重, 其余(origin=None 的运行时内部触发、指向被跳过
指令、其他 sub 来的)经 compile() 折成补充段, merge 追加进静态骨架的
phase on_tick。
"""

from __future__ import annotations

import math
from typing import Any, Iterator, Optional

from ..bullet_commands import CmdFlag
from ..ecl import EclInstr, EclOpcode
from ...logger import logger as log
from .base import EclTranslatorBase, TraceEvent, spellcard_name
from .ir import IrCond, IrIf, IrLoop, IrNode, IrOp, IrOperand, IrSeq

__all__ = ["YoukaiDanmakuTranslator"]

# ---- 默认映射表(= th07 弹型; 其他作品经构造参数覆盖) ----
# 弹型下标 = engine/bullets.py BULLET_TYPE_SPECS 槽位(C g_BulletTypeInfos);
# 中文名注释对照 BulletManager.cpp AddedCallback 特判与 etama.anm 实测。
DEFAULT_BULLET_TYPES: dict[int, str] = {
    0: "ball",  # 小弹
    1: "scale",  # 中弹(鳞弹)
    2: "mentos",  # 米弹
    3: "circle",  # 小光弹
    4: "knife",  # 刀弹
    5: "star",  # 星弹
    6: "circle",  # 椭圆弹
    7: "ball",  # 大弹
    8: "butterfly",  # 蝶弹(活动 sprite 632-639, etama.anm 实测)
    9: "talisman",  # 札弹
    10: "bubble",  # 光弹/大玉(subskill: sprite 10 → bubble)
}

# 标准 16 色调色板(sprite 0..6 共用; etama.anm 逐 offset 主色实测, 饱和像素均值
# 匹配妖归 16 dye 色)。offset 0/15 为灰/白系。
_STANDARD_PALETTE: dict[int, str] = {
    0: "gray",
    1: "red",
    2: "red",
    3: "purple",
    4: "magenta",
    5: "blue",
    6: "blue",
    7: "cyan",
    8: "light_blue",
    9: "green",
    10: "lime",
    11: "lime",
    12: "yellow",
    13: "yellow",
    14: "orange",
    15: "white",
}

# 弹型专属配色(sprite 基址重叠段的实测颜色; 未列 offset 回落标准板)
# subskill 锚点: sprite 8 offset 1/2/3 = butterfly red/purple/blue(实测 RGB
# (235,79,79)/(144,71,186)/(81,81,221)); sprite 10 大玉 offset 0..3 =
# red/blue(紫青)/green/yellow。
_OFFSET_PALETTES: dict[int, dict[int, str]] = {
    7: {
        1: "red",
        2: "magenta",
        3: "blue",
        4: "light_blue",
        5: "lime",
        6: "yellow",
        9: "red",
        10: "purple",
        11: "blue",
        12: "blue",
        13: "green",
        14: "yellow",
        15: "gray",
    },
    8: {
        0: "gray",
        1: "red",
        2: "purple",
        3: "blue",
        4: "blue",
        5: "green",
        6: "yellow",
        7: "gray",
        8: "brown",
        9: "red",
        10: "purple",
        11: "blue",
        12: "light_blue",
        13: "green",
        14: "yellow",
        15: "brown",
    },
    9: {
        0: "brown",
        1: "red",
        2: "purple",
        3: "blue",
        4: "light_blue",
        5: "green",
        6: "yellow",
        7: "brown",
        9: "red",
        10: "blue",
        11: "green",
        12: "yellow",
        14: "red",
        15: "blue",
    },
    10: {
        0: "red",
        1: "blue",
        2: "green",
        3: "yellow",
        4: "red",
        5: "blue",
        6: "red",
        7: "green",
        8: "orange",
        9: "magenta",
        11: "pink",
        12: "magenta",
        13: "pink",
        14: "red",
        15: "blue",
    },
}

# 激光颜色(sprite_offset → dye 名; etama.anm sprite 152..167 渐变段实测)
DEFAULT_LASER_COLORS: dict[int, str] = {
    0: "gray",
    1: "red",
    2: "red",
    3: "magenta",
    4: "magenta",
    5: "blue",
    6: "blue",
    7: "light_blue",
    8: "light_blue",
    9: "cyan",
    10: "cyan",
    11: "lime",
    12: "yellow",
    13: "yellow",
    14: "yellow",
    15: "white",
}

# 妖归 aim 模式对照 engine/bullets.py Aim(= ECL aim_mode, opcode - 64)
_AIMED_MODES = (0, 2, 4)  # 对准玩家(SPREAD/RING/RING_SHIFT AIMED)
_RING_MODES = (2, 3, 4, 5)  # 环形(含绝对角与错半格变体)
_RING_SHIFT_MODES = (4, 5)  # 环形错半格
_RANDOM_MODES = (6, 7, 8)  # 随机系

# 角度等差判定容差(rad)
_ANGLE_TOL = 1e-3

# ---- CONTROL 模式常量 ----

# 无限/次数未知循环的 repeat count 近似上限(≈27.8 分钟 @60fps, 覆盖任意真实符卡)
_INFINITE_LOOP_COUNT = 100000

# 嵌套循环的 repeat index_variable 名(妖归默认 "i", 嵌套层显式写)
_IVARS = ("i", "j", "k", "l")

# 变量环境: ("i"/"f", var_id) → (初值, 每轮步进, 步进所属循环的 ivar 名)。
# 循环迭代 k 里的值 = 初值 + 步进*k; 步进 0 = 常量。ECL 新上下文变量默认 0,
# 未写过的变量按常量 0 处理; 被不可静态求值的指令写过进 unknown 集合。
_VarKey = tuple[str, int]
_VarValue = tuple[float, float, Optional[str]]
_VarEnv = dict[_VarKey, _VarValue]

# 写单个目标变量的 opcode → (目标参数位, 是否 float 变量);
# 布局对照作品 VM handler(th07 见 games/th07/ecl_vm.py)。
_VAR_WRITE_OPS: dict[int, tuple[int, bool]] = {
    EclOpcode.SET_INT: (0, False),
    EclOpcode.SET_FLOAT: (0, True),
    EclOpcode.RAND: (0, False),
    EclOpcode.RAND_ADD: (0, False),
    EclOpcode.RAND_FLOAT: (0, True),
    EclOpcode.RAND_FLOAT_ADD: (0, True),
    EclOpcode.RAND_SIGN: (0, False),
    EclOpcode.RAND_SIGN_FLOAT: (0, True),
    EclOpcode.ADD: (0, False),
    EclOpcode.SUB: (0, False),
    EclOpcode.MUL: (0, False),
    EclOpcode.DIV: (0, False),
    EclOpcode.MOD: (0, False),
    EclOpcode.INC: (0, False),
    EclOpcode.DEC: (0, False),
    EclOpcode.ADD_FLOAT: (0, True),
    EclOpcode.SUB_FLOAT: (0, True),
    EclOpcode.MUL_FLOAT: (0, True),
    EclOpcode.DIV_FLOAT: (0, True),
    EclOpcode.MOD_FLOAT: (0, True),
    EclOpcode.SIN: (0, True),
    EclOpcode.COS: (0, True),
    EclOpcode.ATAN2: (0, True),
    EclOpcode.INIT_INTERP: (7, True),
    EclOpcode.NORMALIZE_ANGLE: (0, True),
    EclOpcode.GET_BOSS_INT: (0, False),
    EclOpcode.GET_BOSS_FLOAT: (0, True),
    EclOpcode.GET_EXIT_ANGLE: (0, True),
    EclOpcode.RAND_EXIT_ANGLE: (0, True),
    EclOpcode.LERP: (0, True),
    EclOpcode.DEC_JUMP: (2, False),
}

_AFFINE_ADD_OPS = (
    EclOpcode.ADD,
    EclOpcode.SUB,
    EclOpcode.ADD_FLOAT,
    EclOpcode.SUB_FLOAT,
)


def _iter_ops(nodes: list[IrNode]) -> Iterator[IrOp]:
    """按文本序展开 IR 树的所有 IrOp。"""
    for node in nodes:
        if isinstance(node, IrOp):
            yield node
        elif isinstance(node, IrSeq):
            yield from _iter_ops(node.nodes)
        elif isinstance(node, IrLoop):
            yield from _iter_ops(node.body)
        elif isinstance(node, IrIf):
            yield from _iter_ops(node.if_true)
            yield from _iter_ops(node.if_false)


def _num(x: float) -> int | float:
    """JSON 数值美化: 整数化 + 4 位小数截断。"""
    r = round(x, 4)
    if r == int(r):
        return int(r)
    return r


def _deg(rad: float) -> float:
    return math.degrees(rad)


def _wrap_angle(a: float) -> float:
    """归一到 (-π, π](utils.normalize_angle_diff 同语义)。"""
    return (a + math.pi) % (2 * math.pi) - math.pi


class YoukaiDanmakuTranslator(EclTranslatorBase):
    """ECL → 妖归 SpellDefinition JSON 的翻译器。

    参数(均有 th07 默认值; 其他作品经参数覆盖, 不 import games.*):
    - ``speed_scale``: 引擎像素/帧 → 妖归速度的比例(subskill 参考
      0.8≈0.4, 默认 0.5, 按手感微调);
    - ``default_lifetime``: 敌弹 lifetime(tick), ECL 弹自然飞出场,
      妖归必须声明寿命;
    - ``min_fold``: 折叠阈值 —— 同构 pattern 至少重复几次才折成
      tick_interval 门控(默认 3);
    - ``bullet_types`` / ``offset_palettes`` / ``laser_colors``:
      sprite→bullet、(sprite,offset)→颜色、激光颜色表(默认 = th07
      etama.anm 实测表, 见模块头注);
    - ``namespace`` / ``spell_id``: 输出 ResourceLocation 的命名空间/完整 id;
    - ``laser_length`` / ``laser_thickness_scale``: 激光长度(格)与
      宽度换算比例(ECL width 是像素)。
    """

    def __init__(
        self,
        game: str = "th07",
        *,
        speed_scale: float = 0.5,
        default_lifetime: int = 200,
        min_fold: int = 3,
        namespace: str = "youkaishomecoming",
        spell_id: Optional[str] = None,
        bullet_types: Optional[dict[int, str]] = None,
        offset_palettes: Optional[dict[int, dict[int, str]]] = None,
        laser_colors: Optional[dict[int, str]] = None,
        laser_length: int = 100,
        laser_thickness_scale: float = 0.1,
    ) -> None:
        super().__init__(game)
        self.speed_scale = speed_scale
        self.default_lifetime = default_lifetime
        self.min_fold = min_fold
        self.namespace = namespace
        self.spell_id = spell_id
        self.bullet_types = bullet_types or DEFAULT_BULLET_TYPES
        self.offset_palettes = offset_palettes or _OFFSET_PALETTES
        self.laser_colors = laser_colors or DEFAULT_LASER_COLORS
        self.laser_length = laser_length
        self.laser_thickness_scale = laser_thickness_scale

    # ---- 主入口 ----

    def compile(self, trace: list[TraceEvent]) -> dict:
        spell_name = ""
        gui_id = -1
        bullet_events: list[TraceEvent] = []
        laser_events: list[TraceEvent] = []
        for ev in trace:
            if ev.kind == "spellcard":
                if not spell_name:
                    spell_name = ev.data["name"]
                    gui_id = ev.data["gui_id"]
                else:
                    log.debug("一张 sub 多次 begin_spellcard, 只取首个: {}", ev.data)
            elif ev.kind == "bullets":
                bullet_events.append(ev)
            elif ev.kind == "laser":
                laser_events.append(ev)
            else:
                log.debug("trace 事件 {} 不可翻译, 已跳过", ev.kind)

        actions: list[dict] = []
        actions.extend(self._compile_bullets(bullet_events))
        actions.extend(self._compile_lasers(laser_events))
        return self._assemble_spell(spell_name, gui_id, actions, "近似")

    def _assemble_spell(
        self, spell_name: str, gui_id: int, actions: list[dict], flavor: str
    ) -> dict:
        """两模式共用的 SpellDefinition 装配(display/id/phase 骨架)。"""
        spell_id = self.spell_id or self._suggest_id(gui_id)
        phase_id = f"{spell_id}/main"
        display_name = spell_name or f"{self.game} ECL sub {self.last_sub_id}"
        description = (
            f"{self.game} ECL sub {self.last_sub_id} 的声明式{flavor}翻译"
            f"(speed_scale={self.speed_scale}, 详见 docs/ecl_to_youkai_migration.md)"
        )
        spell: dict[str, Any] = {
            "id": spell_id,
            "display": {
                "name": display_name,
                "description": description,
            },
            "entry_phase": phase_id,
            "phases": {
                phase_id: {
                    "id": phase_id,
                    "on_tick": actions,
                }
            },
        }
        if spell_name:
            spell["custom_names"] = {"phase:main": spell_name}
        return spell

    def _suggest_id(self, gui_id: int) -> str:
        """由 begin_spellcard 的 gui_id / sub id 拼 ResourceLocation id。"""
        if gui_id >= 0:
            return f"{self.namespace}:ecl_{self.game}_card{gui_id}"
        return f"{self.namespace}:ecl_{self.game}_sub{self.last_sub_id}"

    # ---- 颜色/弹型查表 ----

    def _bullet_of(self, sprite: int) -> str:
        b = self.bullet_types.get(sprite)
        if b is None:
            log.debug("未知弹型 sprite={}, 回落 ball", sprite)
            return "ball"
        return b

    def _color_of(self, sprite: int, offset: int) -> str:
        table = self.offset_palettes.get(sprite, _STANDARD_PALETTE)
        color = table.get(offset)
        if color is None:
            color = _STANDARD_PALETTE.get(offset & 0xF, "white")
        return color

    # ---- 弹幕: 折叠 + fire_danmaku ----

    @staticmethod
    def _signature(data: dict[str, Any]) -> tuple:
        """同构判定键: 除 frame/angle1/pos 外的全部发射参数。"""
        cmds = tuple(
            (c["type"], c["flag"], c["duration"], c["loop"], c["speed"], c["angle"])
            for c in data["commands"]
        )
        return (
            data["sprite"],
            data["sprite_offset"],
            data["count1"],
            data["count2"],
            data["aim_mode"],
            round(data["speed1"], 6),
            round(data["speed2"], 6),
            round(data["angle2"], 6),
            data["flags"],
            cmds,
        )

    def _compile_bullets(self, events: list[TraceEvent]) -> list[dict]:
        """按签名分组 → 等间隔链折叠成 tick_interval 门控; 落单的走 one-shot。

        同帧同签名多次发射(ECL 一条指令展开多批/同帧多 sub)按"车道"(lane)
        处理: 折叠时每车道一条 fire, 完全相同的同帧 fire 合并 count。
        """
        groups: dict[tuple, list[TraceEvent]] = {}
        for ev in events:
            groups.setdefault(self._signature(ev.data), []).append(ev)

        actions: list[dict] = []
        for group in groups.values():
            by_frame: dict[int, list[TraceEvent]] = {}
            for ev in group:
                by_frame.setdefault(ev.frame, []).append(ev)
            for chain in self._split_chains(sorted(by_frame)):
                lanes = [by_frame[f] for f in chain]
                if len(chain) >= self.min_fold:
                    actions.append(self._folded_action(chain, lanes))
                else:
                    for f in chain:
                        for ev in by_frame[f]:
                            fire = self._fire_danmaku(ev.data, ev.data["angle1"])
                            actions.append(self._one_shot(f, fire))
        # 输出按首发帧排序, 阅读顺序=时间顺序
        actions.sort(key=self._action_first_frame)
        return actions

    @staticmethod
    def _split_chains(frames: list[int]) -> list[list[int]]:
        """递增帧序列切成"间隔恒定"的链(greedy; 断链重开)。"""
        chains: list[list[int]] = []
        chain = [frames[0]]
        for f in frames[1:]:
            d = f - chain[-1]
            if len(chain) == 1 or d == chain[-1] - chain[-2]:
                chain.append(f)
            else:
                chains.append(chain)
                chain = [f]
        chains.append(chain)
        return chains

    @staticmethod
    def _action_first_frame(action: dict) -> int:
        cond = action.get("condition", {})
        if cond.get("type") == "tick_interval":
            return int(cond.get("offset", 0))
        right = cond.get("right", 0)
        return int(right) if isinstance(right, (int, float)) else 0

    def _one_shot(self, frame: int, body: dict) -> dict:
        """单发事件门控: phase_tick == frame(妖归无 one-shot 条件, 见模块头注)。"""
        if frame == 0:
            return body  # 第 0 帧即 on_tick 首帧, 无需门控
        return {
            "type": "conditional",
            "condition": {
                "type": "compare",
                "left": {"type": "phase_tick"},
                "op": "=",
                "right": frame,
            },
            "if_true": [body],
        }

    def _folded_action(self, frames: list[int], lanes: list[list[TraceEvent]]) -> dict:
        """等间隔同构链 → conditional + tick_interval 门控(每车道一条 fire)。"""
        f0, interval = frames[0], frames[1] - frames[0]
        fires: list[dict] = []
        for lane_idx in range(len(lanes[0])):
            lane_events = [frame_events[lane_idx] for frame_events in lanes]
            angles = [ev.data["angle1"] for ev in lane_events]
            data = lane_events[0].data
            if data["aim_mode"] in _RANDOM_MODES:
                # 随机系的角度本来就是区间采样, 不追演进(取首波)
                angle_offset: int | float | str | None = None
            else:
                angle_offset = self._angle_progression(angles, f0, interval)
            fires.append(self._fire_danmaku(data, None, angle_offset=angle_offset))
        # 同帧完全相同的 fire 合并 count(如两条车道参数一致)
        merged: list[dict] = []
        for fire in fires:
            dup = next(
                (
                    m
                    for m in merged
                    if {k: v for k, v in m.items() if k != "count"}
                    == {k: v for k, v in fire.items() if k != "count"}
                ),
                None,
            )
            if dup is not None and isinstance(dup["count"], int):
                dup["count"] += fire["count"]
            else:
                merged.append(fire)
        return {
            "type": "conditional",
            "condition": {
                "type": "tick_interval",
                "interval": interval,
                # 校验器 schema 的 offset 是 oneOf(integer, numberProvider),
                # 裸 int 同时命中两边必然报错(schema 怪癖, 引擎 DFU 才是权威);
                # 写 NumberExprParser 简写字符串规避, 运行时等价常量
                "offset": str(f0),
            },
            "if_true": merged,
        }

    def _angle_progression(
        self, angles: list[float], f0: int, interval: int
    ) -> int | float | str:
        """angle1 逐波等差 → phase_tick 表达式; 非等差取首波并记日志。"""
        steps = [_wrap_angle(angles[i + 1] - angles[i]) for i in range(len(angles) - 1)]
        if steps and all(abs(s - steps[0]) < _ANGLE_TOL for s in steps[1:]):
            rate = steps[0] / interval  # rad/帧
            base = angles[0] - rate * f0
            return self._angle_expr(base, rate)
        if steps:
            log.debug("帧 {} 起的链角演进非等差, 取首波角 {:.4f} 近似", f0, angles[0])
        return _num(_deg(angles[0]))

    @staticmethod
    def _angle_expr(base: float, rate: float) -> int | float | str:
        """angle = base + rate*tick → NumberProvider 简写(度制)。"""
        b, r = _deg(base), _deg(rate)
        if abs(r) < 1e-6:
            return _num(b)
        if abs(b) < 1e-6:
            return f"phase_tick * {_num(r)}"
        sign = "+" if b >= 0 else "-"
        return f"phase_tick * {_num(r)} {sign} {_num(abs(b))}"

    def _fire_danmaku(
        self,
        data: dict[str, Any],
        angle1: Optional[float],
        *,
        angle_offset: int | float | str | None = None,
    ) -> dict:
        """一次 spawn_bullet_pattern 快照 → fire_danmaku 动作。"""
        sprite, offset = data["sprite"], data["sprite_offset"]
        aim = data["aim_mode"]
        count1, count2 = data["count1"], data["count2"]
        fire: dict[str, Any] = {
            "type": "fire_danmaku",
            "bullet": self._bullet_of(sprite),
            "color": self._color_of(sprite, offset),
            "count": count1 * max(1, count2),
            "speed": self._speed_of(data),
            "lifetime": self.default_lifetime,
        }
        if aim in _RING_MODES:
            fire["pattern"] = "ring"
        elif aim in _RANDOM_MODES:
            fire["pattern"] = "random"
            fire["spread"] = _num(_deg(abs(data["angle1"] - data["angle2"])))
        else:  # 扇形 0/1
            fire["pattern"] = "line"
            if count1 > 1:
                fire["spread"] = _num(_deg(abs(data["angle2"]) * (count1 - 1)))

        # 角偏移: 调用方给的表达式优先, 否则取本次 angle1(随机系取区间中点)
        if angle_offset is None:
            if aim in _RANDOM_MODES:
                angle_offset = _num(_deg((data["angle1"] + data["angle2"]) / 2))
            elif angle1 is not None:
                angle_offset = _num(_deg(angle1))
            else:
                angle_offset = 0
        # 环形错半格: 基准角错开 π/count1 (engine/bullets.py RING_SHIFT 语义)
        if aim in _RING_SHIFT_MODES and count1 > 0:
            half = _deg(math.pi / count1)
            if isinstance(angle_offset, (int, float)):
                angle_offset = _num(angle_offset + half)
            else:
                angle_offset = f"({angle_offset}) + {_num(half)}"
        if angle_offset != 0:
            fire["angle_offset"] = angle_offset

        if aim in _AIMED_MODES or aim in _RANDOM_MODES:
            fire["aim_mode"] = "direction_to_target"
        else:  # 绝对角: 固定方向 = 首波 angle1(屏幕系 y 向下 → z 向前)
            a = angle1 if angle1 is not None else data["angle1"]
            fire["aim_mode"] = {
                "type": "fixed",
                "direction": {
                    "x": _num(math.cos(a)),
                    "y": 0,
                    "z": _num(math.sin(a)),
                },
            }

        mover = self._mover_of(data)
        if mover is not None:
            fire["mover"] = mover
        return fire

    def _speed_of(self, data: dict[str, Any]) -> int | float | str:
        """速度换算; 多层环(count2>1)层间插值近似为 rand(lo, hi)。"""
        s = self.speed_scale
        v1, v2 = data["speed1"] * s, data["speed2"] * s
        if data["count2"] > 1 and abs(v1 - v2) > 1e-6:
            lo, hi = sorted((v1, v2))
            log.debug(
                "多层环(count2={})速度插值近似为 rand({}, {})",
                data["count2"],
                _num(lo),
                _num(hi),
            )
            return f"rand({_num(lo)}, {_num(hi)})"
        return _num(v1)

    # ---- 命令 → mover(subskill 映射表) ----

    def _mover_of(self, data: dict[str, Any]) -> Optional[dict]:
        cmds = data["commands"]
        ta = next((c for c in cmds if c["type"] == CmdFlag.TARGET_ANGLE), None)
        tv = next((c for c in cmds if c["type"] == CmdFlag.TARGET_VEL), None)
        known = {int(CmdFlag.TARGET_ANGLE), int(CmdFlag.TARGET_VEL)}
        for c in cmds:
            if c["type"] not in known:
                log.debug("子弹命令 {:#x} 未覆盖, 已跳过", c["type"])
        if ta is not None:
            if tv is not None:
                log.debug("TARGET_ANGLE 与 TARGET_VEL 并存, 只翻前者")
            return self._target_angle_mover(data, ta)
        if tv is not None:
            return self._target_vel_mover(tv)
        return None

    def _target_angle_mover(self, data: dict[str, Any], cmd: dict) -> Optional[dict]:
        """TARGET_ANGLE(每 tick 沿自身方向加减速+旋转) → formula/polar。

        自身坐标系 x=forward, y=right(subskill); 三角函数必须 sin_rad/cos_rad。
        """
        s = self.speed_scale
        v0 = data["speed1"] * s
        a = cmd["speed"] * s  # 每帧加速度
        w = cmd["angle"]  # rad/帧
        dur = cmd["duration"]
        if abs(w) < 1e-9 and abs(a) < 1e-9:
            return None
        if abs(w) < 1e-9:
            # 纯线性加减速: x = (v0 + tick*a) * tick (subskill)
            mover: dict = {
                "type": "formula",
                "x": f"({_num(v0)} + tick * {_num(a)}) * tick",
            }
        else:
            radius = abs(v0 / w)
            if abs(a) < 1e-9:
                # 纯旋转: polar(radius=v/ω, angular_speed=deg(ω)/帧)
                mover = {
                    "type": "polar",
                    "radius": _num(radius),
                    "angular_speed": _num(_deg(w)),
                }
            else:
                # 加减速+旋转: x 线性加速, y 极坐标摆动(subskill 近似)
                mover = {
                    "type": "formula",
                    "x": f"({_num(v0)} + tick * {_num(a)}) * tick",
                    "y": f"sin_rad(tick * {_num(w)}) * {_num(radius)}",
                }
        return self._limit_duration(mover, dur, v0 + a * dur)

    def _target_vel_mover(self, cmd: dict) -> dict:
        """TARGET_VEL(每帧叠加世界坐标速度矢量) → acceleration(同世界系)。"""
        s = self.speed_scale
        vx = math.cos(cmd["angle"]) * cmd["speed"] * s
        vz = math.sin(cmd["angle"]) * cmd["speed"] * s
        mover: dict = {"type": "acceleration", "x": _num(vx), "z": _num(vz)}
        return self._limit_duration(mover, cmd["duration"], 0.0)

    def _limit_duration(self, mover: dict, duration: int, v_final: float) -> dict:
        """命令 duration 有限时包 composite: 前 mover 后匀速直行。"""
        if duration <= 0 or duration >= self.default_lifetime:
            return mover
        rest = self.default_lifetime - duration
        tail: dict = {"type": "zero"}
        if v_final > 1e-9:
            tail = {"type": "translate", "speed": _num(v_final), "aim": "forward"}
        return {
            "type": "composite",
            "segments": [
                {"duration": duration, "mover": mover},
                {"duration": rest, "mover": tail},
            ],
        }

    # ---- 激光 → fire_laser ----

    def _compile_lasers(self, events: list[TraceEvent]) -> list[dict]:
        actions = []
        for ev in events:
            fire = self._fire_laser(ev.data, ev.frame)
            actions.append(self._one_shot(ev.frame, fire))
        return actions

    def _fire_laser(
        self,
        data: dict[str, Any],
        spawn_frame: int = 0,
        *,
        clamp_to_recording: bool = True,
    ) -> dict:
        color = self.laser_colors.get(data["sprite_offset"], "white")
        # ECL 常驻激光的 duration 是"开到被停"的占位大数; 取 stop_frame/回放界收紧
        lifetime = max(1, data["duration"])
        if data["stop_frame"] is not None:
            lifetime = min(lifetime, max(1, data["stop_frame"] - spawn_frame))
        if clamp_to_recording:
            lifetime = min(lifetime, max(1, self.last_frame_count - spawn_frame))
        fire: dict[str, Any] = {
            "type": "fire_laser",
            "color": color,
            "lifetime": lifetime,
            "length": self.laser_length,
        }
        # 三段时序(subskill): 出现→持续→消失
        if data["start_time"]:
            fire["setup_prepare"] = data["start_time"]
        if data["end_time"]:
            fire["setup_end"] = data["end_time"]
        if data["width"]:
            fire["thickness"] = _num(
                max(0.2, data["width"] * self.laser_thickness_scale)
            )

        # 角度: type 0 = 出生即瞄玩家(GameEclHost.spawn_laser_pattern);
        # set_angle 更新覆盖; aim_at_player 更新切瞄准
        angle = data["angle1"]
        aimed = data["laser_type"] == 0
        for up in data["updates"]:
            if up["op"] == "set_angle":
                angle, aimed = up["angle"], False
            elif up["op"] == "aim_at_player":
                aimed = True
        if aimed:
            fire["aim_mode"] = "direction_to_target"
            if abs(angle) > 1e-9:
                fire["angle_offset"] = _num(_deg(angle))
        else:
            fire["aim_mode"] = {
                "type": "fixed",
                "direction": {
                    "x": _num(math.cos(angle)),
                    "y": 0,
                    "z": _num(math.sin(angle)),
                },
            }

        mover = self._laser_mover(data, lifetime)
        if mover is not None:
            fire["mover"] = mover
        return fire

    def _laser_mover(self, data: dict[str, Any], lifetime: int) -> Optional[dict]:
        """add_angle 序列 → rotate; stop_frame 截断 → composite rotate→zero。"""
        adds = [up for up in data["updates"] if up["op"] == "add_angle"]
        if not adds:
            return None
        total = sum(up["delta"] for up in adds)
        span = max(1, adds[-1]["frame"] - adds[0]["frame"] + 1)
        per_tick = _deg(total) / span
        rotate: dict = {"type": "rotate", "degrees_per_tick": _num(per_tick)}
        # 旋转后停(subskill): composite 前 rotate 后 zero
        end = data["stop_frame"]
        if end is None:
            return rotate
        seg = max(1, end - adds[0]["frame"])
        return {
            "type": "composite",
            "segments": [
                {"duration": seg, "mover": rotate},
                {"duration": lifetime, "mover": {"type": "zero"}},
            ],
        }

    # ==================== CONTROL 模式(静态控制流 IR → 妖归 JSON) ====================

    def compile_ir(self, ir: IrSeq) -> dict:
        """CONTROL 模式: IrLoop→repeat / IrIf→conditional / 弹幕 IrOp→fire。

        变量操作数经仿射环境(初值+每轮步进)映射; 映射不了的 log.warning 跳过
        (见模块头注与 docs "翻译模式"一节)。静态未覆盖的指令 offset 登记到
        ``self._skipped_offsets``(AUTO 模式 compile_auto 的去重依据)。
        """
        self._skipped_offsets = set()
        spell_name = ""
        gui_id = -1
        for op in _iter_ops(ir.nodes):
            if op.instr.id == EclOpcode.BEGIN_SPELLCARD:
                ins = op.instr
                spell_name = spellcard_name(ins)
                gui_id = ins.arg_i16(0, 0)
                break
        env: _VarEnv = {}
        unknown: set[_VarKey] = set()
        cmds: dict[int, dict[str, Any]] = {}  # INIT_BULLET_CMD 槽位状态(文本序)
        actions = self._compile_ir_nodes(ir.nodes, env, unknown, cmds, "", False, 0)
        return self._assemble_spell(
            spell_name, gui_id, actions, "静态控制流(CONTROL 模式)近似"
        )

    def merge(self, static_result: dict, residual_result: dict) -> dict:
        """AUTO 合并: 动态补充段追加进静态骨架同一 SpellDefinition 的 phase。

        动态段动作自带 tick_interval/compare 门控(绝对帧), 与静态段的
        repeat/delay 结构并存不冲突; 追加在静态段之后。静态骨架全空的边界
        (如寒符: 全靠 SET_SHOOT_INTERVAL 自动射击)由动态段兜底, phases
        不会输出空。**display 名以运行时宣言为准**: 静态 compile_ir 拿的是
        文本序第一条 BEGIN_SPELLCARD, 而难度分支(如反魂蝶一分咲..八分咲)
        实际宣言哪张只有跑起来才知道——动态段真宣言了卡就覆盖静态名。
        """
        phase_id = static_result["entry_phase"]
        residual_phase = residual_result["phases"][residual_result["entry_phase"]]
        static_result["phases"][phase_id]["on_tick"].extend(residual_phase["on_tick"])
        runtime_name = residual_result.get("custom_names", {}).get("phase:main")
        if runtime_name and runtime_name != static_result["display"]["name"]:
            static_result["display"]["name"] = runtime_name
            static_result["custom_names"] = dict(residual_result["custom_names"])
        static_result["display"]["description"] = (
            f"{self.game} ECL sub {self.last_sub_id} 的声明式 AUTO 翻译"
            f"(静态骨架+动态补盲, speed_scale={self.speed_scale}, "
            "详见 docs/ecl_to_youkai_migration.md)"
        )
        return static_result

    # ---- IR 树遍历 ----

    def _compile_ir_nodes(
        self,
        nodes: list[IrNode],
        env: _VarEnv,
        unknown: set[_VarKey],
        cmds: dict[int, dict[str, Any]],
        prefix: str,
        add_time: bool,
        depth: int,
    ) -> list[dict]:
        """编译一段节点序列。

        ``prefix``/``add_time`` 是循环时间语义的表达式上下文: 空 prefix =
        顶层(发射按 instr.time 走 one-shot 门控); 非空 = 循环体内(发射包
        ``delay``, delay_ticks = prefix[+instr.time]); add_time 仅最外层
        循环体叠加指令绝对时间(嵌套循环里时间是静态近似, 见 ir.py 头注)。
        """
        actions: list[dict] = []
        for node in nodes:
            if isinstance(node, IrOp):
                a = self._compile_ir_op(
                    node.instr, env, unknown, cmds, prefix, add_time
                )
                if a is not None:
                    actions.append(a)
            elif isinstance(node, IrSeq):
                actions.extend(
                    self._compile_ir_nodes(
                        node.nodes, env, unknown, cmds, prefix, add_time, depth
                    )
                )
            elif isinstance(node, IrIf):
                actions.extend(
                    self._compile_ir_if(
                        node, env, unknown, cmds, prefix, add_time, depth
                    )
                )
                # 分支内的变量写不可静态确定 → 保守失效
                for key in self._writes_in(node):
                    env.pop(key, None)
                    unknown.add(key)
            elif isinstance(node, IrLoop):
                actions.extend(
                    self._compile_ir_loop(
                        node, env, unknown, cmds, prefix, add_time, depth
                    )
                )
                for key in self._writes_in(node):
                    env.pop(key, None)
                    unknown.add(key)
        return actions

    def _compile_ir_op(
        self,
        ins: EclInstr,
        env: _VarEnv,
        unknown: set[_VarKey],
        cmds: dict[int, dict[str, Any]],
        prefix: str,
        add_time: bool,
    ) -> Optional[dict]:
        op = ins.id
        if (
            EclOpcode.SPAWN_BULLET_PATTERN_SPREAD_AIMED
            <= op
            <= EclOpcode.SPAWN_BULLET_PATTERN_RANDOM
        ):
            res = self._bullet_data_static(ins, env, unknown, cmds)
            if res is None:
                return None
            data, angle_expr = res
            # angle_expr 非空时镜像 DIRECT 折叠的约定: angle1=None,
            # 完整表达式进 angle_offset(固定方向取初值 data["angle1"])
            fire = self._fire_danmaku(
                data,
                None if angle_expr is not None else data["angle1"],
                angle_offset=angle_expr,
            )
            return self._wrap_time(ins, fire, prefix, add_time)
        if op in (
            EclOpcode.SPAWN_LASER_PATTERN_FIXED,
            EclOpcode.SPAWN_LASER_PATTERN_MOVING,
        ):
            laser_data = self._laser_data_static(ins, env, unknown)
            if laser_data is None:
                return None
            fire = self._fire_laser(laser_data, clamp_to_recording=False)
            return self._wrap_time(ins, fire, prefix, add_time)
        if op == EclOpcode.INIT_BULLET_CMD:
            self._update_command(cmds, ins, env, unknown)
            return None
        if op in (EclOpcode.BEGIN_SPELLCARD, EclOpcode.END_SPELLCARD):
            return None  # 符卡名已在 compile_ir 头部提取
        if op in _VAR_WRITE_OPS:
            self._env_update(env, unknown, ins)
            return None
        # 静态未覆盖(移动/音效/SET_SHOOT_INTERVAL/SPAWN_PREV 等): 登记 offset,
        # AUTO 模式里这些指令触发的运行时事件会进动态补充段
        self._skipped_offsets.add(ins.offset)
        log.debug(
            "CONTROL: 指令 id={} offset={:#x} 不可静态翻译, 已跳过", op, ins.offset
        )
        return None

    def _wrap_time(
        self, ins: EclInstr, fire: dict, prefix: str, add_time: bool
    ) -> dict:
        """按时间上下文包装 fire: 顶层 one-shot; 循环体 delay(迭代表达式)。"""
        if not prefix:
            return self._one_shot(ins.time, fire)
        delay = prefix
        if add_time and ins.time:
            delay = f"{prefix} + {ins.time}"
        return {"type": "delay", "delay_ticks": delay, "body": [fire]}

    # ---- 循环/条件 → repeat/conditional ----

    def _compile_ir_loop(
        self,
        loop: IrLoop,
        env: _VarEnv,
        unknown: set[_VarKey],
        cmds: dict[int, dict[str, Any]],
        prefix: str,
        add_time: bool,
        depth: int,
    ) -> list[dict]:
        ivar = _IVARS[depth] if depth < len(_IVARS) else f"i{depth}"
        # 迭代次数: DEC_JUMP 计数器初值可静态确定 → 有限; 否则无限近似
        count: Optional[int] = None
        counter_init: Optional[float] = None
        if loop.counter_var >= 0:
            key = ("i", loop.counter_var)
            if key not in unknown:
                v = env.get(key, (0.0, 0.0, None))
                if v[1] == 0:
                    counter_init = v[0]
                    count = max(1, int(v[0]))  # DEC_JUMP: 初值 N → N 轮(0 → 1 轮)
        if count is None:
            if loop.condition is not None:
                log.warning(
                    "CONTROL: 循环次数不可静态确定(计数器/条件依赖运行时变量), "
                    "以无限循环近似(count={})",
                    _INFINITE_LOOP_COUNT,
                )
            else:
                log.debug(
                    "CONTROL: 无限循环 → repeat count={} 近似", _INFINITE_LOOP_COUNT
                )
            count = _INFINITE_LOOP_COUNT

        # 仿射环境: 第一遍净步进/失效扫描, 第二遍编译时按文本序修正基值
        loop_env = dict(env)
        loop_unknown = set(unknown)
        steps, invalid = self._loop_steps(loop.body)
        for key in invalid:
            loop_env.pop(key, None)
            loop_unknown.add(key)
        for key, st in steps.items():
            if key in loop_unknown:
                continue
            base = loop_env.get(key, (0.0, 0.0, None))
            if base[1] != 0:
                # 外层循环仿射 + 内层再叠加 → 双 ivar 表达式, v1 不映射
                loop_env.pop(key, None)
                loop_unknown.add(key)
                continue
            loop_env[key] = (base[0], st, ivar)
        if counter_init is not None and loop.counter_var >= 0:
            # DEC_JUMP 语义: 迭代 k 的循环体看到 counter = init - k
            loop_env[("i", loop.counter_var)] = (counter_init, -1.0, ivar)

        period = max(1, loop.period)
        pfx = f"${ivar} * {period}" if period != 1 else f"${ivar}"
        if prefix:
            pfx = f"{prefix} + {pfx}"
            add_time = False  # 嵌套循环不再叠加绝对时间(静态近似)
        body = self._compile_ir_nodes(
            loop.body,
            loop_env,
            loop_unknown,
            cmds,
            pfx,
            add_time or not prefix,
            depth + 1,
        )
        if not body:
            log.debug("CONTROL: 循环体无可翻译动作, 已跳过")
            return []
        repeat: dict[str, Any] = {"type": "repeat", "count": count, "body": body}
        if depth > 0:
            repeat["index_variable"] = ivar
        return [repeat]

    def _compile_ir_if(
        self,
        node: IrIf,
        env: _VarEnv,
        unknown: set[_VarKey],
        cmds: dict[int, dict[str, Any]],
        prefix: str,
        add_time: bool,
        depth: int,
    ) -> list[dict]:
        cond = self._youkai_condition(node.condition, env, unknown)
        if cond is None:
            log.warning(
                "CONTROL: IrIf 条件({} {} {})依赖不可映射的 ECL 变量, "
                "if_true 内联近似(if_false 丢弃)",
                node.condition.lhs.value,
                node.condition.op,
                node.condition.rhs.value,
            )
            return self._compile_ir_nodes(
                node.if_true, env, unknown, cmds, prefix, add_time, depth
            )
        out: dict[str, Any] = {
            "type": "conditional",
            "condition": cond,
            "if_true": self._compile_ir_nodes(
                node.if_true, env, unknown, cmds, prefix, add_time, depth
            ),
        }
        if node.if_false:
            out["if_false"] = self._compile_ir_nodes(
                node.if_false, env, unknown, cmds, prefix, add_time, depth
            )
        if not out["if_true"] and not out.get("if_false"):
            return []  # 双臂皆空(体内指令全被跳过) → 不产出
        return [out]

    def _youkai_condition(
        self, cond: IrCond, env: _VarEnv, unknown: set[_VarKey]
    ) -> Optional[dict]:
        """IrCond → 妖归 compare 条件; 任一侧是不可映射变量 → None。"""

        def side(operand: IrOperand) -> Optional[float]:
            if not operand.is_var:
                return float(operand.value)
            key: _VarKey = ("f" if operand.is_float else "i", int(operand.value))
            v = self._var_value(key, env, unknown)
            if v is None or v[1] != 0:
                return None
            return v[0]

        lhs, rhs = side(cond.lhs), side(cond.rhs)
        if lhs is None or rhs is None:
            return None
        return {"type": "compare", "left": _num(lhs), "op": cond.op, "right": _num(rhs)}

    # ---- 静态操作数解析(仿射环境) ----

    @staticmethod
    def _var_value(
        key: _VarKey, env: _VarEnv, unknown: set[_VarKey]
    ) -> Optional[_VarValue]:
        """变量当前仿射值; unknown → None, 未写过 → ECL 默认常量 0。"""
        if key in unknown:
            return None
        return env.get(key, (0.0, 0.0, None))

    def _int_operand(
        self, ins: EclInstr, idx: int, bit: int, env: _VarEnv, unknown: set[_VarKey]
    ) -> Optional[int]:
        """int 操作数(仅常量可映射; 变量带步进 → None)。"""
        if ins.param_mask & (1 << bit):
            v = self._var_value(("i", ins.arg_int(idx)), env, unknown)
            if v is None or v[1] != 0:
                return None
            return int(v[0])
        return ins.arg_int(idx)

    def _float_operand(
        self,
        ins: EclInstr,
        idx: int,
        bit: int,
        env: _VarEnv,
        unknown: set[_VarKey],
        *,
        angle_expr: bool = False,
    ) -> Optional[tuple[float, Optional[str]]]:
        """float 操作数 → (基值, 表达式或 None)。

        angle_expr=True(angle1 专用)时, 仿射变量映射成 NumberExpr 简写
        ``$ivar * deg(步进) + deg(初值)``(度制); 否则带步进即不可映射。
        """
        if ins.param_mask & (1 << bit):
            v = self._var_value(("f", int(ins.arg_float(idx))), env, unknown)
            if v is None:
                return None
            base, step, ivar = v
            if step == 0 or ivar is None:
                return base, None
            if not angle_expr:
                return None
            expr = f"${ivar} * {_num(_deg(step))}"
            if abs(base) > 1e-9:
                expr = f"{expr} + {_num(_deg(base))}"
            return base, expr
        return ins.arg_float(idx), None

    def _bullet_data_static(
        self,
        ins: EclInstr,
        env: _VarEnv,
        unknown: set[_VarKey],
        cmds: dict[int, dict[str, Any]],
    ) -> Optional[tuple[dict[str, Any], Optional[str]]]:
        """弹幕 spawn 指令 → (_fire_danmaku 同款 data, angle1 表达式或 None)。

        操作数布局对照 VM _spawn_bullet_pattern; 除 angle1 外的变量操作数
        只接受常量(带步进 → warning + 跳过整条指令)。
        """

        def fail(what: str) -> None:
            self._skipped_offsets.add(ins.offset)
            log.warning(
                "CONTROL: 弹幕指令 offset={:#x} 的{}依赖不可映射的 ECL 变量, 已跳过",
                ins.offset,
                what,
            )

        sprite: int = ins.arg_i16(0, 0)
        if ins.param_mask & 1:
            v = self._var_value(("i", sprite), env, unknown)
            if v is None or v[1] != 0:
                fail("sprite")
                return None
            sprite = int(v[0])
        offset: int = ins.arg_i16(0, 1)
        if ins.param_mask & 2:
            v = self._var_value(("i", offset), env, unknown)
            if v is None or v[1] != 0:
                fail("sprite_offset")
                return None
            offset = int(v[0])

        count1 = self._int_operand(ins, 1, 2, env, unknown)
        count2 = self._int_operand(ins, 2, 3, env, unknown)
        speed1 = self._float_operand(ins, 3, 4, env, unknown)
        speed2 = self._float_operand(ins, 4, 5, env, unknown)
        angle1 = self._float_operand(ins, 5, 6, env, unknown, angle_expr=True)
        angle2 = self._float_operand(ins, 6, 7, env, unknown)
        if (
            count1 is None
            or count2 is None
            or speed1 is None
            or speed2 is None
            or angle1 is None
            or angle2 is None
        ):
            fail("操作数")
            return None
        data: dict[str, Any] = {
            "sprite": sprite,
            "sprite_offset": offset,
            "angle1": angle1[0],
            "angle2": angle2[0],
            "speed1": speed1[0],
            "speed2": speed2[0],
            "count1": count1,
            "count2": count2,
            "aim_mode": ins.id - 64,
            "flags": ins.args[7] if len(ins.args) > 7 else 0,
            "commands": [c for c in cmds.values() if c["type"]],
        }
        return data, angle1[1]

    def _laser_data_static(
        self, ins: EclInstr, env: _VarEnv, unknown: set[_VarKey]
    ) -> Optional[dict[str, Any]]:
        """激光 spawn 指令 → _fire_laser 同款 data(v1 仅常量操作数)。"""

        def fail(what: str) -> None:
            self._skipped_offsets.add(ins.offset)
            log.warning(
                "CONTROL: 激光指令 offset={:#x} 的{}依赖不可映射的 ECL 变量, 已跳过",
                ins.offset,
                what,
            )

        offset: int = ins.arg_i16(0, 1)
        if ins.param_mask & 2:
            v = self._var_value(("i", offset), env, unknown)
            if v is None or v[1] != 0:
                fail("sprite_offset")
                return None
            offset = int(v[0])
        angle1 = self._float_operand(ins, 1, 2, env, unknown)
        if angle1 is None:
            fail("angle1")
            return None
        data: dict[str, Any] = {
            "sprite_offset": offset,
            "angle1": angle1[0],
            "width": ins.arg_float(6),
            "start_time": ins.arg_int(7),
            "duration": ins.arg_int(8),
            "end_time": ins.arg_int(9),
            "laser_type": 0 if ins.id == EclOpcode.SPAWN_LASER_PATTERN_MOVING else 1,
            "updates": [],
            "stop_frame": None,
        }
        return data

    def _update_command(
        self,
        cmds: dict[int, dict[str, Any]],
        ins: EclInstr,
        env: _VarEnv,
        unknown: set[_VarKey],
    ) -> None:
        """INIT_BULLET_CMD → 子弹命令槽位状态(供后续 fire 的 mover 映射)。"""
        slot = self._int_operand(ins, 0, 0, env, unknown)
        cmd_type = self._int_operand(ins, 1, 1, env, unknown)
        flag = self._int_operand(ins, 2, 2, env, unknown)
        duration = self._int_operand(ins, 3, 3, env, unknown)
        loop = self._int_operand(ins, 4, 4, env, unknown)
        speed = self._float_operand(ins, 5, 5, env, unknown)
        angle = self._float_operand(ins, 6, 6, env, unknown)
        if (
            slot is None
            or cmd_type is None
            or flag is None
            or duration is None
            or loop is None
            or speed is None
            or angle is None
        ):
            log.warning(
                "CONTROL: INIT_BULLET_CMD offset={:#x} 依赖不可映射的 ECL 变量, 已跳过",
                ins.offset,
            )
            return
        cmds[slot] = {
            "type": cmd_type,
            "flag": flag,
            "duration": duration,
            "loop": loop,
            "speed": speed[0],
            "angle": angle[0],
        }

    # ---- 变量环境维护 ----

    @staticmethod
    def _target_key(ins: EclInstr, bit: int, is_float: bool) -> Optional[_VarKey]:
        """指令的可写目标变量(mask 置位才有; C 里写进指令内存被丢弃)。"""
        if not (ins.param_mask & (1 << bit)):
            return None
        if is_float:
            return ("f", int(ins.arg_float(bit)))
        return ("i", ins.arg_int(bit))

    def _writes_of(self, ins: EclInstr) -> list[_VarKey]:
        """指令写到的变量(key 列表; 用于失效扫描)。"""
        op = ins.id
        if op == EclOpcode.VEC_FROM_ANGLE_MAG:  # 写 arg0/arg1 两个 float 变量
            return [
                k
                for k in (
                    self._target_key(ins, 0, True),
                    self._target_key(ins, 1, True),
                )
                if k
            ]
        spec = _VAR_WRITE_OPS.get(op)
        if spec is None:
            return []
        key = self._target_key(ins, spec[0], spec[1])
        return [key] if key else []

    def _writes_in(self, node: IrNode) -> list[_VarKey]:
        """节点(子树)内写到的全部变量。"""
        return [key for op in _iter_ops([node]) for key in self._writes_of(op.instr)]

    def _env_update(self, env: _VarEnv, unknown: set[_VarKey], ins: EclInstr) -> None:
        """顺序数据流: SET_*/自增减/自加减退仿射更新, 其余写 → unknown。"""
        op = ins.id
        if op in (EclOpcode.SET_INT, EclOpcode.SET_FLOAT):
            is_f = op == EclOpcode.SET_FLOAT
            key = self._target_key(ins, 0, is_f)
            if key is None:
                return
            if ins.param_mask & 2:  # 值来自变量 → 不可静态确定
                env.pop(key, None)
                unknown.add(key)
                return
            val = ins.arg_float(1) if is_f else float(ins.arg_int(1))
            env[key] = (val, 0.0, None)
            unknown.discard(key)
            return
        delta: Optional[float] = None
        wkey: Optional[_VarKey] = None
        if op in (EclOpcode.INC, EclOpcode.DEC):
            wkey = self._target_key(ins, 0, False)
            delta = 1.0 if op == EclOpcode.INC else -1.0
        elif op in _AFFINE_ADD_OPS:
            is_f = op in (EclOpcode.ADD_FLOAT, EclOpcode.SUB_FLOAT)
            # 自加减形式 ADD(v, v, k): arg0/arg1 同变量引用, arg2 立即数
            if (ins.param_mask & 3) == 3 and not (ins.param_mask & 4):
                k0 = self._target_key(ins, 0, is_f)
                k1 = self._target_key(ins, 1, is_f)
                if k0 is not None and k0 == k1:
                    wkey = k0
                    k = ins.arg_float(2) if is_f else float(ins.arg_int(2))
                    delta = -k if op in (EclOpcode.SUB, EclOpcode.SUB_FLOAT) else k
        if wkey is not None and delta is not None:
            if wkey not in unknown:
                base, step, ivar = env.get(wkey, (0.0, 0.0, None))
                env[wkey] = (base + delta, step, ivar)
            return
        for wkey in self._writes_of(ins):
            env.pop(wkey, None)
            unknown.add(wkey)

    def _loop_steps(
        self, body: list[IrNode]
    ) -> tuple[dict[_VarKey, float], set[_VarKey]]:
        """循环体第一遍扫描: 每轮净步进(自增减/自加减法) + 不可映射写集合。

        嵌套结构(IrIf/IrLoop)里的写一律按不可映射处理(条件执行/内层迭代
        的净效果无法静态确定)。
        """
        steps: dict[_VarKey, float] = {}
        invalid: set[_VarKey] = set()
        for node in body:
            if not isinstance(node, IrOp):
                invalid.update(self._writes_in(node))
                continue
            ins = node.instr
            op = ins.id
            if op in (EclOpcode.INC, EclOpcode.DEC):
                key = self._target_key(ins, 0, False)
                if key is not None:
                    steps[key] = steps.get(key, 0.0) + (
                        1.0 if op == EclOpcode.INC else -1.0
                    )
                continue
            if op in _AFFINE_ADD_OPS:
                is_f = op in (EclOpcode.ADD_FLOAT, EclOpcode.SUB_FLOAT)
                affine = False
                if (ins.param_mask & 3) == 3 and not (ins.param_mask & 4):
                    k0 = self._target_key(ins, 0, is_f)
                    k1 = self._target_key(ins, 1, is_f)
                    if k0 is not None and k0 == k1:
                        k = ins.arg_float(2) if is_f else float(ins.arg_int(2))
                        sign = (
                            -1.0 if op in (EclOpcode.SUB, EclOpcode.SUB_FLOAT) else 1.0
                        )
                        steps[k0] = steps.get(k0, 0.0) + sign * k
                        affine = True
                if not affine:
                    key = self._target_key(ins, 0, is_f)
                    if key is not None:
                        invalid.add(key)
                continue
            if op in (EclOpcode.SET_INT, EclOpcode.SET_FLOAT):
                continue  # 常量重置, 第二遍 _env_update 处理
            invalid.update(self._writes_of(ins))
        return steps, invalid
