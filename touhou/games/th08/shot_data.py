"""TH08(东方永夜抄)的射击数据 (.sht) 解析 —— parse_sht_th08 + Th08ShotEntry。

th08 的 .sht 布局(PlayerRawShtFile/PlayerShotDescriptor)与 th07 不同,
且条目多一个 extremeGaugeBehavior 字段; 从 schema/shot_data.py 下沉
(那里只留作品无关的 ShotData/ShotLevel/ShotEntry 与 th07 布局的
``parse_sht``)。
"""

from __future__ import annotations

import struct

from ...schema.shot_data import ShotData, ShotEntry, ShotLevel, _f32, _i16, _i32


class Th08ShotEntry(ShotEntry):
    """th08 射击条目: 追加 extremeGaugeBehavior。"""

    gauge_behavior: int = 0  # extremeGaugeBehavior(极限人/妖时的行为,
    # Player.hpp PlayerShotDescriptor @0x1E; th07 无此字段)


def parse_sht_th08(data: bytes) -> ShotData:
    """解析 th08(东方永夜抄)的 .sht(PlayerRawShtFile, th08-ref Player.hpp:22-56)。

    头 0x38 字节: u16 reserved @0 + u16 shotPowerLevelCount @2 +
    f32 initialBombCount @4 + i32 deathbombWindowFrames @8 +
    f32 hurtboxSize/grazeBoxSize/itemAutoCollectSpeed/itemCollectionBoxSize/
    pointItemValueLine @0xC..0x1F + u32 reserved @0x20 +
    f32 normalAxisSpeed/focusedAxisSpeed/normalDiagonalSpeed/focusedDiagonalSpeed/
    itemMovementSpeed @0x24..0x37; 火力档表(PlayerShotPowerLevel: u32 偏移 +
    i32 minimumPower)从 0x38 起。
    条目链(PlayerShotDescriptor, 56 字节, Player.hpp:247-266):
    i16 fireInterval/fireFrame + Float2 positionOffset/hitboxSize +
    f32 angle/speed + i16 damage/extremeGaugeBehavior/sourceOptionIndex/shotType/
    animationIndex/soundIndex + 4×i32 回调(spawn/update/draw/collision,
    回调表 Player.cpp:186-196), 以 fireInterval<0 结尾。

    与 th07 头的字段映射: deathbombWindowFrames → initial_respawn_timer
    (同为决死窗/死亡倒计时初值); th08 无樱点惩罚系数 → cherry_penalty_multiplier=0;
    itemMovementSpeed 无对应字段(吸附速度用 itemAutoCollectSpeed), 不解析。
    """
    num_levels = struct.unpack_from("<H", data, 2)[0]
    initial_bombs = _f32(data, 4)
    deathbomb_window = _i32(data, 8)
    hitbox_radius, grab_item_radius, item_collect_speed, item_collect_radius, poc_y = (
        struct.unpack_from("<5f", data, 0xC)
    )
    speed, speed_focus, speed_diagonal, speed_diagonal_focus, _item_move = (
        struct.unpack_from("<5f", data, 0x24)
    )

    levels: list[ShotLevel] = []
    for i in range(max(0, num_levels)):
        off = 0x38 + i * 8
        if off + 8 > len(data):
            break
        rel, req_power = struct.unpack_from("<Ii", data, off)
        levels.append(ShotLevel(req_power, _parse_entry_chain_th08(data, rel)))

    return ShotData(
        initial_bombs,
        deathbomb_window,
        hitbox_radius,
        grab_item_radius,
        item_collect_speed,
        item_collect_radius,
        0.0,  # cherry_penalty_multiplier: th08 无樱点
        poc_y,
        speed,
        speed_focus,
        speed_diagonal,
        speed_diagonal_focus,
        levels,
    )


def _parse_entry_chain_th08(data: bytes, offset: int) -> list[ShotEntry]:
    """th08 条目链(PlayerShotDescriptor 56 字节), 直到 fireInterval<0。"""
    out: list[ShotEntry] = []
    off = offset
    while off + 0x38 <= len(data):
        fi = _i16(data, off)
        fo = _i16(data, off + 2)
        ox, oy = _f32(data, off + 4), _f32(data, off + 8)
        hx, hy = _f32(data, off + 12), _f32(data, off + 16)
        angle = _f32(data, off + 20)
        speed = _f32(data, off + 24)
        damage = _i16(data, off + 28)
        gauge_behavior = _i16(data, off + 30)
        option = _i16(data, off + 32)
        bs2 = _i16(data, off + 34)  # shotType: 3=穿透 4/5=激光型(同 th07 语义,
        # 命中分支 Player.cpp:3304-3344)
        anm_idx = _i16(data, off + 36)
        snd_idx = _i16(data, off + 38)
        fire_cb = _i32(data, off + 40)
        update_cb = _i32(data, off + 44)
        draw_cb = _i32(data, off + 48)
        hit_cb = _i32(data, off + 52)
        out.append(
            Th08ShotEntry(
                fi,
                fo,
                (ox, oy),
                (hx, hy),
                angle,
                speed,
                damage,
                option,
                bs2,
                fire_cb,
                update_cb,
                draw_cb,
                hit_cb,
                anm_file_idx=anm_idx,
                sound_idx=snd_idx,
                gauge_behavior=gauge_behavior,
            )
        )
        if fi < 0:
            break
        off += 0x38
    return out
