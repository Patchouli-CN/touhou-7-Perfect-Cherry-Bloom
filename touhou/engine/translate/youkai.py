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
"""

from __future__ import annotations

import math
from typing import Any, Optional

from ..bullet_commands import CmdFlag
from ...logger import logger as log
from .base import EclTranslatorBase, TraceEvent

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

        spell_id = self.spell_id or self._suggest_id(gui_id)
        phase_id = f"{spell_id}/main"
        display_name = spell_name or f"{self.game} ECL sub {self.last_sub_id}"
        description = (
            f"{self.game} ECL sub {self.last_sub_id} 的声明式近似翻译"
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

    def _fire_laser(self, data: dict[str, Any], spawn_frame: int = 0) -> dict:
        color = self.laser_colors.get(data["sprite_offset"], "white")
        # ECL 常驻激光的 duration 是"开到被停"的占位大数; 取 stop_frame/回放界收紧
        lifetime = max(1, data["duration"])
        if data["stop_frame"] is not None:
            lifetime = min(lifetime, max(1, data["stop_frame"] - spawn_frame))
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
