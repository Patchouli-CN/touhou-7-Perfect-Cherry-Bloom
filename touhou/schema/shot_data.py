""" 射击数据 (.sht) 解析 —— Pythonic。

从游戏资源里解析每个角色/是否 focus 的射击数据:
等级(requiredPower 门槛) + 各等级射击条目(周期/偏移/命中盒/角度/速度/伤害/回调索引)。
"""

from __future__ import annotations

import struct
import msgspec
from typing import Optional


class ShotEntry(msgspec.Struct):
    """一条射击条目(某个火力等级下, 一次按下发射的若干弹之一)。"""

    fire_interval: int   # 射击周期(帧); <0 表示链尾
    fire_offset: int     # 周期内偏移
    offset: tuple[float, float]     # 相对发射点偏移
    hitbox: tuple[float, float]     # 弹判定盒(全宽/全高)
    angle: float         # 基准角(弧度)
    speed: float         # 速度
    damage: int
    option: int          # 0=本体 1/2=子机
    bullet_state2: int   # 3=穿透, 4/5=激光型
    fire_cb: int
    update_cb: int
    draw_cb: int
    hit_cb: int
    anm_file_idx: int = 0    # 动画 id(逻辑层仅 missile 爆炸变形查表用)
    sound_idx: int = -1      # 音效 id(Player.cpp:116-119, 发弹时 PlaySoundByIdx)

    @property
    def is_sentinel(self) -> bool:
        return self.fire_interval < 0


class ShotLevel(msgspec.Struct):
    """一个火力等级: 达到 requiredPower 时启用, 指向一组射击条目。"""

    required_power: int
    entries: list[ShotEntry] = msgspec.field(default_factory=list)


class ShotData(msgspec.Struct):
    """一份 .sht(某角色 one focus state) 的完整射击数据。"""

    initial_bombs: float
    initial_respawn_timer: int
    hitbox_radius: float
    grab_item_radius: float
    item_collect_speed: float
    item_collect_radius: float
    cherry_penalty_multiplier: float
    poc_y: float
    speed: float
    speed_focus: float
    speed_diagonal: float
    speed_diagonal_focus: float
    levels: list[ShotLevel] = msgspec.field(default_factory=list)

    def level_for_power(self, power: float) -> ShotLevel:
        """返回满足 currentPower>=requiredPower 的最高火力等级。"""
        chosen = self.levels[0]
        for lv in self.levels:
            if power >= lv.required_power:
                chosen = lv
        return chosen


def _f32(d: bytes, off: int) -> float:
    v: float = struct.unpack_from("<f", d, off)[0]
    return v


def _i32(d: bytes, off: int) -> int:
    v: int = struct.unpack_from("<i", d, off)[0]
    return v


def _i16(d: bytes, off: int) -> int:
    v: int = struct.unpack_from("<h", d, off)[0]
    return v


def parse_sht(data: bytes) -> ShotData:
    """解析一个 .sht。头部(Player.hpp ShtData, sizeof=0x3c 含首条 ShtLevel):
    i16 numLevels + u16 entryCount + f32 initialBombs + i32 initialRespawnTimer
    + 10 个 f32 参数, 共 52 字节。

    ShtLevel 表从头部之后起(每条 u32 entryRelOffset + i32 requiredPower),
    每个 level 指向一组 ShtEntry, 以 fireInterval<0 结尾。
    """
    num_levels = struct.unpack_from("<h", data, 0)[0]
    entry_count = struct.unpack_from("<H", data, 2)[0]
    initial_bombs = _f32(data, 4)
    initial_respawn_timer = _i32(data, 8)  # 注意是 i32, 不是 f32
    params = struct.unpack_from("<10f", data, 12)
    (
        hitbox_radius, grab_item_radius, item_collect_speed, item_collect_radius,
        cherry_penalty_multiplier, poc_y, speed, speed_focus,
        speed_diagonal, speed_diagonal_focus,
    ) = params

    levels: list[ShotLevel] = []
    lvl_tab = 4 + 48  # 头部 52 字节 (i16+u16 + f32 + i32 + 10f)
    for i in range(max(0, entry_count)):
        off = lvl_tab + i * 8
        if off + 8 > len(data):
            break
        rel, req_power = struct.unpack_from("<Ii", data, off)
        entries = _parse_entry_chain(data, rel)
        levels.append(ShotLevel(req_power, entries))

    return ShotData(
        initial_bombs, initial_respawn_timer, hitbox_radius, grab_item_radius,
        item_collect_speed, item_collect_radius, cherry_penalty_multiplier,
        poc_y, speed, speed_focus, speed_diagonal, speed_diagonal_focus, levels,
    )


def _parse_entry_chain(data: bytes, offset: int) -> list[ShotEntry]:
    """从 offset 开始解析 ShtEntry 链, 直到 fireInterval<0。"""
    out: list[ShotEntry] = []
    off = offset
    while off + 52 <= len(data):
        fi = _i16(data, off)
        fo = _i16(data, off + 2)
        ox, oy = _f32(data, off + 4), _f32(data, off + 8)
        hx, hy = _f32(data, off + 12), _f32(data, off + 16)
        angle = _f32(data, off + 20)
        speed = _f32(data, off + 24)
        damage = _i16(data, off + 28)
        option = data[off + 30]
        bs2 = data[off + 31]
        anm_idx = _i16(data, off + 32)
        snd_idx = _i16(data, off + 34)
        fire_cb = _i32(data, off + 36)
        update_cb = _i32(data, off + 40)
        draw_cb = _i32(data, off + 44)
        hit_cb = _i32(data, off + 48)
        out.append(ShotEntry(fi, fo, (ox, oy), (hx, hy), angle, speed,
                             damage, option, bs2, fire_cb, update_cb, draw_cb, hit_cb,
                             anm_file_idx=anm_idx, sound_idx=snd_idx))
        if fi < 0:
            break
        off += 52
    return out
