""" 弹幕 —— Pythonic。

用 enum 表示瞄准方式, Burst 描述"一批子弹如何发散"(对照 EnemyBulletShooter),
Bullet 用 Vec2 表示位置/速度并继承 BulletState 获得命令系统,
BulletWorld 统一管理、批量更新(对照 BulletManager::OnUpdate 的每帧顺序)。

数值/语义权威: th07/src/th07/BulletManager.cpp。
"""

from __future__ import annotations

import math
import msgspec
from enum import IntEnum

from .bullet_commands import (
    OFFSCREEN_GRACE,
    OFFSCREEN_GRACE_FRAMES,
    BulletCommand,
    BulletState,
)
from .rng import Rng
from ..utils import Vec2, angle_to, normalize_angle_diff

# 可视区
SCREEN = Vec2(384, 448)


class Aim(IntEnum):
    """敌弹的瞄准/发散方式。值对应 BulletAimMode (BulletManager.hpp)。"""

    SPREAD_AIMED = 0          # 扇形, 对准玩家
    SPREAD_ABSOLUTE = 1       # 扇形, 绝对角
    RING_AIMED = 2            # 环形, 对准玩家
    RING_ABSOLUTE = 3         # 环形, 绝对角
    RING_SHIFT_AIMED = 4      # 环形错半格, 对准玩家
    RING_SHIFT_ABSOLUTE = 5   # 环形错半格, 绝对角
    ANGLE_RANDOM = 6          # 角度随机(速度按层插值)
    RING_SPEED_RANDOM = 7     # 环形 + 速度随机
    RANDOM = 8                # 角度+速度全随机


# ---- §0.5 rank 插值 (EnemyManager.hpp BulletRank*Inner) ----
def rank_lerp(low: float, high: float, scale: float) -> float:
    """弹速插值: scale*(high-low)/32 + low。scale=subrank/rank, 0..32。"""
    return scale * (high - low) / 32 + low


def rank_lerp_int(low: int, high: int, scale: int) -> int:
    """弹量/射击间隔插值(整数, C++ int 除法向零截断)。"""
    d = scale * (high - low)
    return (d // 32 if d >= 0 else -((-d) // 32)) + low


# ---- 弹型模板 (BulletManager.cpp AddedCallback + g_BulletTypeInfos) ----
class BulletTypeSpec(msgspec.Struct, frozen=True):
    """一种敌弹弹型: 判定/擦弹尺寸与碰撞分层。

    width/height 取自 etama.anm 精灵尺寸(从 th07.dat 的 data/etama.anm 提取,
    见表头注释); graze_size/collision_type 按 AddedCallback 的分档判定树得出。
    """

    anm_file_idx: int     # etama.anm 脚本索引 (g_BulletTypeInfos)
    width: float          # 精灵宽(px)
    height: float         # 精灵高(px) = bulletHeight
    graze_size: Vec2      # 擦弹/命中判定半宽
    collision_type: int   # 绘制分层 0..5


# 16 个模板槽。TH07 只初始化前 11 个(g_BulletTypeInfos), 后 5 个槽未用(全 0)。
# 精灵尺寸提取自游戏数据 data/etama.anm(th07.dat):
#   0x200→8px, 0x201→16, 0x202→16, 0x203→16, 0x204→16, 0x205→16, 0x206→16,
#   0x207→32, 0x208→32, 0x209→32, 0x2a8(=chunk2, 链式偏移 168)→8
# graze/collision 按 AddedCallback 判定树: ≤8→(4,4,type5); ≤16→514/516/517/518
# 特判(4,4,type4)否则(6,6,type3); ≤32→520:(5,5,type1) 521:(8,8,type2)
# 否则(10,10,type2); >32→(24,24,type0)。
BULLET_TYPE_SPECS: tuple[BulletTypeSpec, ...] = (
    BulletTypeSpec(0x200, 8.0, 8.0, Vec2(4, 4), 5),    # 0: 小弹
    BulletTypeSpec(0x201, 16.0, 16.0, Vec2(6, 6), 3),  # 1: 中弹
    BulletTypeSpec(0x202, 14.0, 16.0, Vec2(4, 4), 4),  # 2: 米弹(514 特判)
    BulletTypeSpec(0x203, 16.0, 16.0, Vec2(6, 6), 3),  # 3
    BulletTypeSpec(0x204, 14.0, 16.0, Vec2(4, 4), 4),  # 4 (516 特判)
    BulletTypeSpec(0x205, 14.0, 16.0, Vec2(4, 4), 4),  # 5 (517 特判)
    BulletTypeSpec(0x206, 14.0, 16.0, Vec2(4, 4), 4),  # 6 (518 特判)
    BulletTypeSpec(0x207, 32.0, 32.0, Vec2(10, 10), 2),  # 7: 大弹
    BulletTypeSpec(0x208, 32.0, 32.0, Vec2(5, 5), 1),    # 8: 刀弹(520 特判)
    BulletTypeSpec(0x209, 32.0, 32.0, Vec2(8, 8), 2),    # 9: 札弹(521 特判)
    BulletTypeSpec(0x2A8, 8.0, 8.0, Vec2(4, 4), 5),      # 10: 光弹(链式 chunk)
    # 11..15: TH07 未初始化(AddedCallback 只循环 i<11)
    BulletTypeSpec(0, 0.0, 0.0, Vec2(0, 0), 0),
    BulletTypeSpec(0, 0.0, 0.0, Vec2(0, 0), 0),
    BulletTypeSpec(0, 0.0, 0.0, Vec2(0, 0), 0),
    BulletTypeSpec(0, 0.0, 0.0, Vec2(0, 0), 0),
    BulletTypeSpec(0, 0.0, 0.0, Vec2(0, 0), 0),
)

_DEFAULT_BULLET_SIZE = Vec2(16, 16)

# ---- spawn 特效态 (BulletManager.cpp:255-283 出生 / :1022-1047 每帧) ----
# shooter flags 2/4/8 = SPAWNING_FAST/NORMAL/SLOW: 出生 pos -= vel*4, 出生态以
# vel/2 | vel/2.5 | vel/3 移动, 不跑命令/不吃判定/不做出界; spawn 特效 anm
# 脚本播完的当帧转 NORMAL 并落入 NORMAL 分支(当帧再全速位移一次)。
# 出生态帧数由 etama.anm 的 spawn 特效脚本时长决定(ExecuteScript 在脚本结束
# 帧返回 1): 这些脚本都是 t=T 时 EXIT_HIDE2, 即 T 帧纯出生态 + 第 T+1 帧转变。
# 下表 T 值从真实 th07.dat 的 etama.anm 解出(脚本 0x212-0x218 / 0x2aa):
#   弹型 0   : 0x212/0x213/0x214 → T = 10/16/32
#   弹型 1-6 : 0x215/0x216/0x217 → T = 10/16/32
#   弹型 7-9 : 0x218 三态共用    → T = 32
#   弹型 10  : 0x2aa 三态共用    → T = 24
_SPAWN_MOVE_DIV = {2: 2.0, 4: 2.5, 8: 3.0}
_SPAWN_SCRIPT_T = {0: (10, 16, 32), 1: (10, 16, 32), 2: (10, 16, 32),
                   3: (10, 16, 32), 4: (10, 16, 32), 5: (10, 16, 32),
                   6: (10, 16, 32), 7: (32, 32, 32), 8: (32, 32, 32),
                   9: (32, 32, 32), 10: (24, 24, 24)}


def spawn_state_spec(sprite: int, flags: int) -> tuple[int, int]:
    """shooter flags/弹型 → (spawn_state, 转变帧数)。

    spawn_state = 触发的 flag 位 (2/4/8, 优先级同 C++ 的 if/elif 链);
    帧数 = 特效脚本 T + 1 (第 T+1 次 ExecuteScript 返回 1 → 当帧转 NORMAL)。
    无 spawn 位或未知弹型返回 (0, 0)。
    """
    if flags & 2:
        bit, idx = 2, 0
    elif flags & 4:
        bit, idx = 4, 1
    elif flags & 8:
        bit, idx = 8, 2
    else:
        return 0, 0
    t = _SPAWN_SCRIPT_T.get(sprite)
    if t is None:
        return 0, 0  # 11-15 槽 TH07 未初始化 (g_BulletTypeInfos 只有 11 项)
    return bit, t[idx] + 1


def bullet_type_size(bullet_type: int) -> Vec2:
    """弹型 → 精灵尺寸(出界/反弹判定用)。未知弹型给 16px 默认。"""
    if 0 <= bullet_type < len(BULLET_TYPE_SPECS):
        spec = BULLET_TYPE_SPECS[bullet_type]
        if spec.width > 0:
            return Vec2(spec.width, spec.height)
    return _DEFAULT_BULLET_SIZE


# 弹型模板的活动 sprite 基址 (etama.anm 全局 sprite 索引)。
# 提取口径: LoadAnms(11, "data/etama.anm", ANM_OFFSET_BULLETS=0x200) 链式加载,
# 每条脚本(0x200..0x209, 0x2a8)的首个 set-sprite + 链式偏移;
# C++ SpawnSingleBullet 的活动 sprite = 基址 + spriteOffset (直接相加, 无分辨率映射)。
_BULLET_BASE_SPRITE_IDX: tuple[int, ...] = (
    512, 528, 544, 560, 576, 592, 608, 624, 632, 640, 680,
)


def bullet_active_sprite_idx(sprite: int, sprite_offset: int) -> int:
    """活动 sprite 索引 (SpawnSingleBullet: template.activeSpriteIdx + spriteOffset)。"""
    if 0 <= sprite < len(_BULLET_BASE_SPRITE_IDX):
        return _BULLET_BASE_SPRITE_IDX[sprite] + sprite_offset
    return -1


def bullet_sprite_height(sprite: int, sprite_offset: int) -> float:
    """活动 sprite 的 heightPx —— ExIns 大弹判定(C++ spriteBullet.sprite->heightPx)。

    与 BULLET_TYPE_SPECS 的差异只在模板 10 (脚本 0x2a8): 其 offset 0..3 是
    64px 大玉(sprite 680-683), offset 4+ 是 16px(684-695); 其余模板的
    offset 变体同尺寸(实测 608-647 区间等宽等高)。
    """
    if sprite == 10:
        return 64.0 if 0 <= sprite_offset <= 3 else 16.0
    return bullet_type_size(sprite).y


class Burst(msgspec.Struct, frozen=True):
    """一次"按 pattern 发散出的若干弹参数"(对照 EnemyBulletShooter)。

    base_angle: 基准角 —— 非随机模式下相当于 angle1, aimed 模式调用方应传入
    angle_to(发射点, 玩家) + angle1; 随机模式(ANGLE_RANDOM/RANDOM)下即 angle1,
    angle_step 即 angle2(随机区间为 [angle2, angle1))。
    """

    path: Vec2          # 发射起点(通常是敌人位置)
    base_angle: float   # 基准角(见上)
    aim: Aim
    arms: int           # count1: 每次发几颗(环的份数/扇的颗数)
    rings: int          # count2: 几层(环数)
    speed_a: float      # speed1
    speed_b: float      # speed2(层间插值低端/随机低端)
    angle_step: float   # angle2: 层间角差 / 扇间隔
    sprite: int = 0     # 弹型(= bulletTypeTemplates 下标)
    sprite_offset: int = 0  # spriteOffset: 颜色/变体偏移 (活动 sprite = 基址+offset)
    commands: tuple[BulletCommand, ...] = ()  # 出生即挂的命令队列
    flags: int = 0      # moreFlags(命令位 + 2/4/8 spawn 特效态 + 0x200 音效 + 0x1000 不清屏…)

    def angle_speed(self, arm: int, ring: int, rng: Rng) -> tuple[float, float]:
        """第 (arm=x, ring=y) 颗弹的发射角/速度 —— SpawnSingleBullet 的 switch。"""
        mode = self.aim
        count1, count2 = self.arms, self.rings
        port = self.base_angle
        if count2 > 1:
            speed = self.speed_a - (self.speed_a - self.speed_b) * ring / count2
        else:
            speed = self.speed_a

        if mode in (Aim.SPREAD_AIMED, Aim.SPREAD_ABSOLUTE):
            # 对称扇: 奇数颗从 0 偏移, 偶数颗错开半步; 奇数下标取负
            if count1 & 1:
                off = self.angle_step * ((arm + 1) // 2)
            else:
                off = self.angle_step * (arm // 2) + self.angle_step * 0.5
            if arm & 1:
                off = -off
            return port + off, speed
        if mode in (Aim.RING_AIMED, Aim.RING_ABSOLUTE):
            return port + arm * math.tau / count1 + ring * self.angle_step, speed
        if mode in (Aim.RING_SHIFT_AIMED, Aim.RING_SHIFT_ABSOLUTE):
            return port + math.pi / count1 + arm * math.tau / count1, speed
        if mode is Aim.ANGLE_RANDOM:
            return rng.in_range(self.angle_step, port), speed
        if mode is Aim.RING_SPEED_RANDOM:
            speed = rng.in_range(self.speed_b, self.speed_a)
            return port + arm * math.tau / count1 + ring * self.angle_step, speed
        # Aim.RANDOM
        return (rng.in_range(self.angle_step, port),
                rng.in_range(self.speed_b, self.speed_a))


class Bullet(BulletState):
    __slots__ = ("sprite", "sprite_offset", "state2", "age", "dead", "grazed",
                 "out_of_bounds_time", "spawn_state", "spawn_frames", "hitbox")

    def __init__(self, pos: Vec2, angle: float, speed: float, sprite: int = 0,
                 size: Vec2 | None = None, sprite_offset: int = 0,
                 hitbox: float = 3.5) -> None:
        # C++ spawn 时 angle = AddNormalizeAngle(bulletAngle, 0)
        super().__init__(pos, normalize_angle_diff(angle), speed,
                         size=size if size is not None else _DEFAULT_BULLET_SIZE)
        self.sprite = sprite
        self.sprite_offset = sprite_offset  # C bullet->spriteOffset
        # 判定半径(碰撞盒半宽, 观测面用): 本引擎的擦弹/命中盒是
        # pos±BulletWorld.bullet_radius 的均匀 AABB (见 world 的判定管线),
        # fire() 生成时把世界当前值物化到实例上; 默认 3.5 与该字段默认一致
        self.hitbox = hitbox
        self.state2 = 0      # C bullet->state2 (ExIns 的每弹标记位)
        self.age = 0
        self.dead = False
        self.grazed = False  # 每颗弹只擦一次(§A.7, 由 Player.check_graze 置位)
        self.out_of_bounds_time = 0
        # spawn 特效态: 0=NORMAL; 2/4/8 = SPAWNING_FAST/NORMAL/SLOW
        # (C bullet->state; spawn_frames 倒计时 = 特效 anm 脚本剩余帧数)
        self.spawn_state = 0
        self.spawn_frames = 0

    def step(self) -> None:
        """推进一帧(位移; 命令更新在 BulletWorld.step 里先于本调用)。"""
        self.pos = self.pos + self.vel
        self.age += 1

    def off_screen(self) -> bool:
        """完全出屏(GameManager::IsInBounds 的否: 以精灵半宽/半高为边距)。"""
        hw, hh = self.size.x / 2.0, self.size.y / 2.0
        return not (self.pos.x + hw >= 0.0 and self.pos.x - hw <= SCREEN.x
                    and self.pos.y + hh >= 0.0 and self.pos.y - hh <= SCREEN.y)


class BulletWorld(msgspec.Struct):
    """弹幕世界: 持有全部敌弹, 负责生成/更新/碰撞。"""

    rng: Rng = msgspec.field(default_factory=Rng)
    player_pos: Vec2 = msgspec.field(default_factory=lambda: SCREEN / 2)
    player_radius: float = 2.0
    # 敌弹判定半宽(擦弹/命中盒 = 弹 pos±bullet_radius 的均匀 AABB; 作品层判定
    # 管线消费本字段, fire() 生成子弹时把它物化到 Bullet.hitbox 供观测面读取)
    bullet_radius: float = 3.5
    _bullets: list[Bullet] = msgspec.field(default_factory=list)
    # g_Supervisor.effectiveFramerateMultiplier 的弹幕侧 (ExIns 10/11 妖梦减速):
    # C++ 在"每次算 velocity"时乘上它 —— 出生速度/命令更新器的 dt, 位移本身不二次缩放
    time_scale: float = 1.0

    # ---- 生成 ----
    def fire(self, burst: Burst) -> int:
        """把一发 Burst 展开成实际子弹(SpawnBulletPattern 的双层循环)。
        返回生成颗数。"""
        count = 0
        size = bullet_type_size(burst.sprite)
        for ring in range(burst.rings):
            for arm in range(burst.arms):
                angle, speed = burst.angle_speed(arm, ring, self.rng)
                b = Bullet(burst.path, angle, speed, sprite=burst.sprite, size=size,
                           sprite_offset=burst.sprite_offset,
                           hitbox=self.bullet_radius)
                if self.time_scale != 1.0:
                    # SpawnSingleBullet: velocity = speed * effectiveFramerateMultiplier
                    b.vel = b.vel * self.time_scale
                b.commands = list(burst.commands)
                # moreFlags = shooter flags; 命令位由 AddCommand 记入(这里补 OR)
                b.more_flags = burst.flags
                for c in b.commands:
                    b.more_flags |= c.type
                # SpawnSingleBullet:255-283: flags 2/4/8 → 出生态 + pos -= vel*4
                # (在 RunCommands 之前, 用出生速度回退)
                st, frames = spawn_state_spec(burst.sprite, burst.flags)
                if st:
                    b.spawn_state = st
                    b.spawn_frames = frames
                    b.pos = b.pos - b.vel * 4.0
                b.run_commands()  # SpawnSingleBullet 末尾立即跑一次
                self._bullets.append(b)
                count += 1
        return count

    def ring(self, at: Vec2, arms: int, speed: float, *, aimed: bool = True,
             angle_step: float = 0.12, speed_b: float | None = None) -> int:
        port = angle_to(at, self.player_pos) if aimed else 0.0
        return self.fire(Burst(at, port, Aim.RING_AIMED if aimed else Aim.RING_ABSOLUTE,
                               arms, 1, speed, speed_b if speed_b is not None else speed,
                               angle_step))

    def spread(self, at: Vec2, arms: int, speed: float, spread: float, *,
               aimed: bool = True, speed_b: float | None = None) -> int:
        port = angle_to(at, self.player_pos) if aimed else 0.0
        return self.fire(Burst(at, port, Aim.SPREAD_AIMED if aimed else Aim.SPREAD_ABSOLUTE,
                               arms, 1, speed, speed_b if speed_b is not None else speed, spread))

    # ---- 更新 ----
    def step(self) -> None:
        """每帧 —— 对照 OnUpdate: SPAWNING_* 分支(:1022-1047) 减速移动且不跑
        命令/不吃判定/不做出界, 特效脚本播完当帧转 NORMAL (switch_break) 落入
        BULLET_NORMAL 分支: RunCommands → exFlags 更新器 → spawnDelay 递减 →
        位移 → 出界判定。
        """
        for b in self._bullets:
            if b.spawn_state:
                # 出生态: pos += vel/2 | /2.5 | /3; 倒计时未归 0 本帧到此为止
                # (C++ timer2-- 后被 update_timers 的 timer2++ 抵消, 等效 age 冻结)
                b.pos = b.pos + b.vel / _SPAWN_MOVE_DIV[b.spawn_state]
                b.spawn_frames -= 1
                if b.spawn_frames > 0:
                    continue
                b.spawn_state = 0  # switch_break: state=NORMAL, 当帧落入正常分支
            b.run_commands()
            b.step_commands(self.player_pos, self.time_scale)
            if b.spawn_delay != 0:
                b.spawn_delay -= 1
            b.step()
            if b.spawn_delay == 0:
                if b.off_screen():
                    if b.ex_flags & OFFSCREEN_GRACE:
                        # 带转向/反弹命令的弹出界后宽限 128 帧(可以回来)
                        b.out_of_bounds_time += 1
                        if b.out_of_bounds_time >= OFFSCREEN_GRACE_FRAMES:
                            b.dead = True
                    else:
                        b.dead = True
                else:
                    b.out_of_bounds_time = 0
        self._bullets = [b for b in self._bullets if not b.dead]

    def hits_player(self) -> bool:
        r = self.player_radius + self.bullet_radius
        return any(self.player_pos.distance(b.pos) <= r for b in self._bullets
                   if not b.spawn_state)  # 出生态无判定 (同 OnUpdate)

    def clear(self) -> None:
        self._bullets.clear()

    def stop_bullet_movement(self) -> None:
        """BulletManager::StopBulletMovement (BulletManager.cpp:1476-1500, 咲夜B 炸弹):
        全场活弹 velocity/speed 清零 —— 永久停住, 非可逆冻结(速度不存不留)。
        C++ 另清 angularVelocity/acceleration; 本模型角速度/加速度在命令槽里,
        speed=0 使后续命令重算出的速度亦为 0, 语义等价。"""
        for b in self._bullets:
            b.vel = Vec2.zero()
            b.speed = 0.0

    def alive(self) -> list[Bullet]:
        return self._bullets

    def __len__(self) -> int:
        return len(self._bullets)

    def spawn_demo_wave(self, center: Vec2) -> int:
        """放一小波(环+扇)方便肉眼/测试。"""
        n = self.ring(center, 16, 2.0)
        n += self.spread(SCREEN / 2, 7, 3.0, 0.15)
        return n
