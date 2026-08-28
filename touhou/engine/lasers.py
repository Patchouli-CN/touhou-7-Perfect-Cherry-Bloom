"""激光 —— 移植自 BulletManager.cpp 的 Laser 三态机 + 旋转命中判定。

状态: SPAWNING(出现,窄命中) / ACTIVE(全宽命中) / DESPAWNING(消散)。
命中: 把玩家位置旋转到激光局部坐标, 与激光盒 AABB 相交。
擦激光: 盒外扩 48px, 每 12 帧节流一次。
"""

from __future__ import annotations

import math
import msgspec
from enum import IntEnum

from ..utils import Vec2, angle_to


class LaserState(IntEnum):
    SPAWNING = 0
    ACTIVE = 1
    DESPAWNING = 2


class Laser(msgspec.Struct):
    """一条激光。"""

    pos: Vec2
    angle: float
    width: float = 8.0
    speed: float = 0.0
    start_time: int = 0  # 出现完成帧
    hitbox_start_time: int = 0
    duration: int = 0  # 全宽保持帧
    end_time: int = 0  # 消散完成帧
    hitbox_end_time: int = 0
    start_length: float = 0.0  # 长度上限(0=不限)
    flags: int = 0
    color: int = 0
    hide_warning: bool = False

    state: LaserState = LaserState.SPAWNING
    offset_a: float = 0.0
    offset_b: float = 0.0
    target_width: float = 8.0
    timer: int = 0
    in_use: bool = True

    def __post_init__(self) -> None:
        self.target_width = self.width
        if self.start_time == 0:
            self.state = LaserState.ACTIVE

    # ---- 每帧 ----
    def step(self, dt: float = 1.0) -> None:
        self._geometry(dt)
        if self.state == LaserState.SPAWNING:
            if self.timer >= self.hitbox_start_time:
                self._is_throttle_frame()  # 命中节流判定在外部由 player 调用
            if self.timer >= self.start_time:
                self.timer = 0
                self.state = LaserState.ACTIVE
        elif self.state == LaserState.ACTIVE:
            if self.timer >= self.duration:
                self.timer = 0
                self.state = LaserState.DESPAWNING
                if self.end_time == 0:
                    self.in_use = False
        elif self.state == LaserState.DESPAWNING:
            if self.timer >= self.end_time:
                self.in_use = False
        self.timer += 1

    def _geometry(self, dt: float) -> None:
        self.offset_b += self.speed * dt
        if self.start_length and self.offset_b - self.offset_a > self.start_length:
            self.offset_a = self.offset_b - self.start_length
        self.offset_a = max(0.0, self.offset_a)
        if self.offset_a >= 640:  # 超长销毁
            self.in_use = False

    # ---- 几何访问器 ----
    @property
    def hitbox(self) -> tuple[Vec2, Vec2]:
        """返回 (center, half_size) 的激光命中盒(局部坐标)。"""
        half_w = self.width / 2
        length = self.offset_b - self.offset_a
        center = Vec2(self.offset_a + length / 2, 0)
        return center, Vec2(length / 2, half_w)

    def _is_throttle_frame(self) -> bool:
        return self.timer % 12 == 0

    def graze_frame(self) -> bool:
        """本帧是否允许擦激光(每 12 帧一次)。"""
        return self.timer % 12 == 0

    def localize(self, world: Vec2) -> Vec2:
        """把世界坐标旋到激光局部坐标(激光沿 +x, 原点在 pos)。"""
        relative = world - self.pos
        return relative.rotated(-self.angle)


def laser_hits_player(
    laser: Laser,
    player_pos: Vec2,
    player_r: float,
    graze_extra: float = 48.0,
    can_graze: bool = True,
) -> tuple[bool, bool]:
    """返回 (命中判定点?, 擦激光?)。laser 需在 ACTIVE/命中时段。"""
    center, half = laser.hitbox
    local = laser.localize(player_pos)

    def aabb(center_: Vec2, half_: Vec2, point: Vec2) -> bool:
        return (
            abs(point.x - center_.x) <= half_.x and abs(point.y - center_.y) <= half_.y
        )

    hit = aabb(Vec2(center.x, center.y), Vec2(half.x, half.y + player_r), local)
    graze = False
    if not hit and can_graze:
        gx, gy = half.x + graze_extra, half.y + graze_extra + player_r
        graze = aabb(Vec2(center.x, center.y), Vec2(gx, gy), local)
    return hit, graze


class LaserWorld(msgspec.Struct):
    """管理一批激光。"""

    lasers: list[Laser] = msgspec.field(default_factory=list)

    def spawn(
        self,
        pos: Vec2,
        angle: float,
        *,
        aimed: bool = True,
        width: float = 8.0,
        speed: float = 0.0,
        player_pos: Vec2 = Vec2(192, 400),
        duration: int = 120,
        start_time: int = 20,
        hitbox_start_time: int = 20,
        end_time: int = 40,
        hitbox_end_time: int = 40,
        start_length: float = 160.0,
    ) -> Laser | None:
        if len(self.lasers) >= 64:
            return None
        if aimed:
            angle = angle_to(pos, player_pos) + angle
        laser = Laser(
            pos=pos,
            angle=angle,
            width=width,
            speed=speed,
            start_time=start_time,
            hitbox_start_time=hitbox_start_time,
            duration=duration,
            end_time=end_time,
            hitbox_end_time=hitbox_end_time,
            start_length=start_length,
        )
        # 激光初始长度(来自 shooter 的 endOffset; 之后随 speed 增长)
        laser.offset_b = start_length
        self.lasers.append(laser)
        return laser

    def step(self, dt: float = 1.0) -> None:
        for l in self.lasers:
            if l.in_use:
                l.step(dt)
        self.lasers = [l for l in self.lasers if l.in_use]

    def check_player(self, player_pos: Vec2, player_r: float) -> tuple[bool, bool]:
        """返回 (被命中?, 擦到激光[本帧])。"""
        hit = False
        grazed = False
        for l in self.lasers:
            if not l.in_use:
                continue
            # 只在外观有效且处于命中窗口期间判定
            if l.state == LaserState.SPAWNING and l.timer < l.hitbox_start_time:
                continue
            if l.state == LaserState.DESPAWNING and l.timer >= l.hitbox_end_time:
                continue
            lhit, lgraze = laser_hits_player(
                l, player_pos, player_r, can_graze=l.graze_frame()
            )
            hit = hit or lhit
            grazed = grazed or lgraze
        return hit, grazed

    def remove_all(
        self,
        *,
        spawn_items: bool,
        skip_flag4: bool = True,
        spawn_at_pos: bool = False,
        spawn_item=None,
    ) -> None:
        """清弹连带激光 (BulletManager.cpp:439-471 RemoveAllBullets 激光段 /
        :524-550 DespawnBullets 激光段)。

        flags&4 的激光在 RemoveAllBullets(param!=10) 时豁免(skip_flag4=True);
        DespawnBullets 与 RemoveAllBullets(10) 不豁免(skip_flag4=False)。
        state<DESPAWNING 的进 DESPAWNING (timer=0, width=targetWidth);
        spawn_items 时自 startOffset 起沿线每 32px 经 spawn_item 出一个道具
        (spawn_at_pos 另在激光原点先出一个, 仅 DespawnBullets 有);
        hitbox_end_time 清零(含已在 DESPAWNING 的)。
        """
        for l in self.lasers:
            if not l.in_use:
                continue
            if skip_flag4 and (l.flags & 4):
                continue
            if l.state < LaserState.DESPAWNING:
                l.state = LaserState.DESPAWNING
                l.timer = 0
                l.width = l.target_width
                if spawn_items and spawn_item is not None:
                    if spawn_at_pos:
                        spawn_item(l.pos)
                    dx, dy = math.cos(l.angle), math.sin(l.angle)
                    off = l.offset_a
                    while l.offset_b > off:
                        spawn_item(Vec2(l.pos.x + dx * off, l.pos.y + dy * off))
                        off += 32.0
            l.hitbox_end_time = 0

    def clear(self) -> None:
        self.lasers.clear()

    def alive(self) -> list[Laser]:
        return [l for l in self.lasers if l.in_use]

    def __len__(self) -> int:
        return len([l for l in self.lasers if l.in_use])
