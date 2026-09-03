"""战斗区 2D anm VM 宿主 + EffectManager 特效子集 —— 对照 th07 反编译。

- `AnmScriptBank`: 一个 .anm 的脚本/sprite 表, 建在 LoadAnms 的链式偏移
  空间上(AnmManager.cpp: entry 的 spriteIdxOffset 按 max(sprite,script)+1
  累加); C++ 全局 id(ANM_OFFSET_*) → 本表 key = 全局 id - base。
- `Vm2d`: AnmVm(见 anm_vm.py) 的 2D 宿主, start(gid) = C++ 的
  SetAnmIdxAndExecuteScript; draw() 做旋转(z)/缩放(负值=翻转)/颜色调制/
  alpha/blend(1=加算), 中心锚点, 变换结果按量化键缓存。
- `EffectLayer`: EffectManager 子集(EffectManager.cpp g_EffectMapping 的
  战斗常用项): 击坠爆炸(0=0x2ab)、道具爆皮(7=0x2b6)、自机弹命中火花
  (5=0x2b4, Player.cpp:896)、玩家死亡大爆(12=0x2bb + 6=0x2b5×16,
  Player.cpp:1234-1235)、focus 判定点环(24=0x2c2 AttachToPlayer,
  Player.cpp:1438)、结界破裂樱点(29=0x2b2 Burst30Frames, Player.cpp:2181)。
  粒子物理照抄 InitDeceleratingBurst(Fast)/UpdatePhysics/UpdateBurst30Frames;
  回收口径 = OnUpdate: ExecuteScript 结束(pc<0)即释放。

近似项: 特效原为世界空间 3D quad, 这里按游戏平面 1:1 映射成屏幕 2D;
globalColorMultiplier(EffectManager.cpp:78-81)恒为 1, 不做全局调制。
"""

from __future__ import annotations

import math
import random

import numpy as np
import pygame

from .anm_vm import AnmVm, ScriptRef, chain_offsets, flat_chain_offsets, reset_and_run

# EffectManager.cpp g_EffectMapping 子集: effectId → (anm 全局 script id, 粒子物理)
_FX_STATIC, _FX_BURST, _FX_BURST_FAST, _FX_ATTACH, _FX_BURST30 = 0, 1, 2, 3, 4
EFFECT_TABLE: dict[int, tuple[int, int]] = {
    0: (0x2AB, _FX_STATIC),  # 击坠爆炸爆风环 (EnemyManager deathAnm1=0)
    1: (0x2AC, _FX_STATIC),
    2: (0x2AD, _FX_STATIC),
    3: (0x2AE, _FX_BURST),  # InitDeceleratingBurst
    4: (0x2B3, _FX_BURST_FAST),
    5: (0x2B4, _FX_BURST_FAST),  # 自机弹命中火花 (Player.cpp:896)
    6: (0x2B5, _FX_BURST_FAST),  # 玩家死亡 ×16 (Player.cpp:1235)
    7: (0x2B6, _FX_BURST_FAST),  # 道具爆皮 (deathAnm2+4=7)
    8: (0x2B7, _FX_BURST_FAST),  # 擦弹 (Player.cpp:1197)
    12: (0x2BB, _FX_STATIC),  # 玩家死亡大爆风 (Player.cpp:1234)
    24: (0x2C2, _FX_ATTACH),  # focus 判定点环 AttachToPlayer (Player.cpp:1438)
    29: (0x2B2, _FX_BURST30),  # 结界破裂樱点 ×32 (Player.cpp:2181)
}


class AnmScriptBank:
    """一个 .anm 的脚本/sprite 表(链式偏移空间; 配合 SpriteBank 取图)。"""

    def __init__(self, bank, name: str, base: int) -> None:
        self.bank = bank
        self.name = name
        self.base = base
        self.refs: dict[int, ScriptRef] = {}
        self._spr_loc: dict[int, tuple[int, int]] = {}  # key → (entry, 局部 id)
        raw = bank.raw(name)
        self.ok = raw is not None
        if raw is None:
            return
        # anm 走 SpriteBank 公开面(注册表解析结果); 脚本表用同一份 raw 经
        # 格式类的 parse_scripts(anm 对象自带的类即注册格式类, 鸭子接口)
        anm = bank.anm(name)
        assert anm is not None
        per_entry = anm.parse_scripts(raw)
        if getattr(anm, "ANM_FLAT_LAYOUT", False):
            # th08 扁平序号布局: 脚本/精灵各按 entry 数量累加, 脚本里的
            # sprite 参数已是扁平序号(ScriptRef.sprite_base=0 不加基址,
            # AnmManager.cpp:237 SetSprite 直接用实参当下标)
            spr_offs, scr_offs = flat_chain_offsets(anm, per_entry)
            for ei, (entry, escr) in enumerate(zip(anm.entries, per_entry)):
                for sid, instrs in escr.items():
                    self.refs[scr_offs[ei] + sid] = ScriptRef(instrs, 0)
                for sid in entry.sprites:
                    self._spr_loc[spr_offs[ei] + sid] = (ei, sid)
            return
        for ei, (entry, escr, off) in enumerate(
            zip(anm.entries, per_entry, chain_offsets(anm, per_entry))
        ):
            for sid, instrs in escr.items():
                self.refs[off + sid] = ScriptRef(instrs, off)
            for sid in entry.sprites:
                self._spr_loc[off + sid] = (ei, sid)

    def ref_global(self, gid: int) -> ScriptRef | None:
        """C++ 全局 script id(含 ANM_OFFSET) → ScriptRef。"""
        return self.refs.get(gid - self.base)

    def sprite_surf(self, key: int) -> pygame.Surface | None:
        """链式偏移空间的 sprite key → Surface(经 SpriteBank 缓存)。"""
        loc = self._spr_loc.get(key)
        if loc is None:
            return None
        return self.bank.sprite(self.name, loc[1], entry=loc[0])


class TransformCache:
    """旋转/缩放/翻转后的 Surface 缓存(量化键, FIFO 上限)。

    另有加算用预乘缓存: C++ 加算混合是 (SRCALPHA, ONE)
    (AnmManager.cpp:716-722 SetRenderStateForVm: blendMode!=0 →
    DESTBLEND=D3DBLEND_ONE, SRC 恒 SRCALPHA), 即 src.rgb*src.a 加到目标;
    pygame BLEND_ADD 不看源 alpha, 必须先把 rgb 按 alpha 预乘,
    否则 glow 贴图的透明区(RGB 常为白)加成不透明色块。
    """

    def __init__(self, limit: int = 1024) -> None:
        self._d: dict[tuple, pygame.Surface] = {}
        self._add: dict[tuple, tuple[pygame.Surface, pygame.Surface]] = {}
        # 颜色调制/alpha 调制缓存: (id(变换结果), r,g,b,a) → (持引用, 调制结果)
        # Vm2d.draw 的非加算路径原先逐 draw copy+fill/set_alpha(密集弹幕下
        # 每次 ~30µs, 实测 stage 6 高峰 152 次/帧 ≈ 5.5ms), 变换结果本身
        # 已被 _d 缓存且 id 稳定, 调制结果同样可缓存。
        self._mod: dict[tuple, tuple[pygame.Surface, pygame.Surface]] = {}
        self._limit = limit

    def get(
        self, img: pygame.Surface, sx: float, sy: float, rot_rad: float
    ) -> pygame.Surface:
        flipx, flipy = sx < 0, sy < 0
        ax, ay = abs(sx), abs(sy)
        deg = -math.degrees(rot_rad)  # D3D LH +z = 屏幕顺时针 → pygame 取负
        qdeg = round(deg / 3.0) * 3 % 360
        qsx, qsy = max(1, round(ax * 16)), max(1, round(ay * 16))
        key = (id(img), flipx, flipy, qdeg, qsx, qsy)
        out = self._d.get(key)
        if out is not None:
            return out
        out = img
        if flipx or flipy:
            out = pygame.transform.flip(out, flipx, flipy)
        w = max(1, round(out.get_width() * qsx / 16))
        h = max(1, round(out.get_height() * qsy / 16))
        if (w, h) != out.get_size():
            out = pygame.transform.scale(out, (w, h))
        if qdeg:
            out = pygame.transform.rotate(out, qdeg)
        if len(self._d) >= self._limit:
            self._d.pop(next(iter(self._d)))
        self._d[key] = out
        return out

    def get_additive(
        self,
        img: pygame.Surface,
        sx: float,
        sy: float,
        rot_rad: float,
        rgba: tuple[int, int, int, int],
    ) -> pygame.Surface:
        """加算绘制用: 变换 + vm 颜色调制 + alpha 预乘(src.rgb*src.a*a/255)。"""
        base = self.get(img, sx, sy, rot_rad)
        r, g, b, a = rgba
        qa = 255 if a >= 248 else a & 0xF8  # alpha 量化(保 255 不损失亮度)
        key = (id(base), r, g, b, qa)
        hit = self._add.get(key)
        if hit is not None and hit[0] is base:  # 持引用防 id 复用串键
            return hit[1]
        out = base.copy()
        rgb = pygame.surfarray.pixels3d(out)
        alpha = pygame.surfarray.pixels_alpha(out)
        w = alpha.astype(np.uint16) * qa // 255  # 有效 alpha/255 分子
        rgb[:] = (
            rgb.astype(np.uint16)
            * w[..., None]
            // 255
            * np.array((r, g, b), dtype=np.uint16)
        ) // 255
        del rgb, alpha
        if len(self._add) >= 256:
            self._add.pop(next(iter(self._add)))
        self._add[key] = (base, out)
        return out

    def get_modulated(
        self, base: pygame.Surface, r: int, g: int, b: int, a: int
    ) -> pygame.Surface:
        """非加算路径的颜色/alpha 调制缓存(与 Vm2d.draw 原逐帧 copy 同式)。

        调用顺序与原实现一致: 先 BLEND_MULT 颜色, 再 set_alpha。
        alpha 按 0xF8 量化(同 get_additive 的 qa 约定): 淡入淡出动画
        每帧变 alpha, 不量化则每帧 cache miss 一次 copy+fill(~70µs)。
        """
        qa = 255 if a >= 248 else a & 0xF8
        key = (id(base), r, g, b, qa)
        hit = self._mod.get(key)
        if hit is not None and hit[0] is base:  # 持引用防 id 复用串键
            return hit[1]
        out = base
        if (r, g, b) != (255, 255, 255):
            out = out.copy()
            out.fill((r, g, b, 255), special_flags=pygame.BLEND_MULT)
        if qa < 255:
            if out is base:
                out = base.copy()
            if out.get_flags() & pygame.SRCALPHA:
                # alpha 烘焙进像素(代替 set_alpha 表面级调制):
                # set_alpha 会逼 SDL 走"逐像素 alpha × 表面 alpha"慢路径,
                # 实测 blit 慢 2.4 倍; 烘焙等价于每像素 alpha*a//255
                # (≤1 LSB 截断差, 与 get_additive 的量化同量级)
                alpha = pygame.surfarray.pixels_alpha(out)
                alpha[:] = (alpha.astype(np.uint16) * qa // 255).astype(np.uint8)
                del alpha
            else:
                out.set_alpha(qa)
        if len(self._mod) >= 512:
            self._mod.pop(next(iter(self._mod)))
        self._mod[key] = (base, out)
        return out


class Vm2d:
    """2D 战斗实体的 AnmVm 宿主: start(gid) → 每帧 execute() → draw()。

    ``vm_cls`` 选 anm 脚本 VM 方言(th08 = AnmVmTh08, 指令集差集见
    games/th08/view/anm_vm.py); 默认 AnmVm(th07), 既有行为不变。
    """

    def __init__(
        self, sbank: AnmScriptBank, tcache: TransformCache, vm_cls=AnmVm
    ) -> None:
        self.sbank = sbank
        self.tcache = tcache
        self.vm = vm_cls()
        self.surf: pygame.Surface | None = None

    def _set_sprite(self, key: int) -> None:
        self.surf = self.sbank.sprite_surf(key)
        self.vm.active_sprite_idx = key

    def start(self, gid: int) -> bool:
        """SetAnmIdxAndExecuteScript; 脚本不存在返回 False。"""
        self.surf = None
        ref = self.sbank.ref_global(gid)
        if ref is None:
            self.vm.__init__()
            self.vm.pc = -1
            return False
        reset_and_run(self.vm, ref, self._set_sprite)
        return True

    def set_sprite(self, key: int) -> None:
        """SetActiveSprite 覆写(Gui::ShowBombNamePortrait 的立绘差分等,
        Gui.cpp:346); key 为脚本表链式偏移空间的 sprite id。"""
        self._set_sprite(key)

    def execute(self) -> None:
        self.vm.execute()

    @property
    def alive(self) -> bool:
        return self.vm.pc >= 0

    def draw(
        self, surf: pygame.Surface, x: float, y: float, tint_alpha: int | None = None
    ) -> None:
        vm = self.vm
        img = self.surf
        if not vm.visible or img is None:
            return
        # th08 flag17: 有效色取 color2 (DrawInner, AnmManager.cpp:1215);
        # th07 的 AnmVm 无该属性, getattr 回落 color1
        r, g, b, a = vm.color2 if getattr(vm, "flag17", 0) else vm.color
        if tint_alpha is not None:
            a = a * tint_alpha // 255
        if a <= 0 or vm.scale[0] == 0.0 or vm.scale[1] == 0.0:
            return
        if vm.blend_mode == 1:
            # 加算: 预乘后 BLEND_ADD(C++ DESTBLEND=ONE, 见 TransformCache)
            out = self.tcache.get_additive(
                img, vm.scale[0], vm.scale[1], vm.rotation[2], (r, g, b, a)
            )
            surf.blit(
                out,
                (int(x) - out.get_width() // 2, int(y) - out.get_height() // 2),
                special_flags=pygame.BLEND_ADD,
            )
            return
        out = self.tcache.get(img, vm.scale[0], vm.scale[1], vm.rotation[2])
        if (r, g, b) != (255, 255, 255) or a < 255:
            out = self.tcache.get_modulated(out, r, g, b, a)
        surf.blit(out, (int(x) - out.get_width() // 2, int(y) - out.get_height() // 2))


class Effect:
    """一个特效粒子(EffectManager.hpp Effect 的 2D 子集)。"""

    __slots__ = ("vm2d", "kind", "x", "y", "vx", "vy", "ax", "ay", "timer", "ex", "ey")

    def __init__(self, vm2d: Vm2d, kind: int, x: float, y: float) -> None:
        self.vm2d = vm2d
        self.kind = kind
        self.x, self.y = x, y
        self.vx = self.vy = self.ax = self.ay = 0.0
        self.ex, self.ey = x, y  # emitterPosition (burst30 的发射原点)
        self.timer = 0


class EffectLayer:
    """EffectManager 子集: spawn/update/draw; 回收 = 脚本结束(pc<0)。"""

    def __init__(
        self, sbank: AnmScriptBank | None, tcache: TransformCache, seed: int = 0xC0FFEE
    ) -> None:
        self.sbank = sbank
        self.tcache = tcache
        self.effects: list[Effect] = []
        self.rng = random.Random(seed)

    def __len__(self) -> int:
        return len(self.effects)

    def spawn(
        self,
        effect_id: int,
        x: float,
        y: float,
        count: int = 1,
        color: int = 0xFFFFFFFF,
        direction: tuple[float, float] | None = None,
    ) -> list[Effect]:
        """SpawnParticles(effectId, pos, count, color) 子集。

        color = D3DCOLOR 0xAARRGGBB, 直接进 vm.color
        (EffectManager.cpp:539/604/646)。direction: burst30 的定向
        (BreakBorder 均布 32 方向, Player.cpp:2178-2183 覆写
        effect->direction; None 时按 InitRandomDir 随机)。
        """
        spec = EFFECT_TABLE.get(effect_id)
        if spec is None or self.sbank is None or not self.sbank.ok:
            return []
        gid, kind = spec
        cr, cg, cb = (color >> 16) & 255, (color >> 8) & 255, color & 255
        ca = (color >> 24) & 255
        out = []
        for _ in range(count):
            vm2d = Vm2d(self.sbank, self.tcache)
            if not vm2d.start(gid):
                break
            vm2d.vm.color = [cr, cg, cb, ca]  # start 后设置(同 C++ 顺序)
            e = Effect(vm2d, kind, x, y)
            if kind == _FX_BURST:
                # InitDeceleratingBurst (EffectManager.cpp:111)
                e.vx = (self.rng.random() * 256.0 - 128.0) * 4.0 / 33.0
                e.vy = (self.rng.random() * 256.0 - 128.0) * 4.0 / 33.0
                e.ax, e.ay = -e.vx / 20.0, -e.vy / 20.0
            elif kind == _FX_BURST_FAST:
                # InitDeceleratingBurstFast (EffectManager.cpp:91)
                e.vx = (self.rng.random() * 256.0 - 128.0) / 12.0
                e.vy = (self.rng.random() * 256.0 - 128.0) / 12.0
                e.ax, e.ay = -e.vx / 19.0, -e.vy / 19.0
            elif kind == _FX_BURST30:
                if direction is None:
                    # InitRandomDir (EffectManager.cpp:189)
                    ang = self.rng.random() * 2.0 * math.pi - math.pi
                    e.vx, e.vy = math.cos(ang), math.sin(ang)
                else:
                    e.vx, e.vy = direction
            self.effects.append(e)
            out.append(e)
        return out

    @staticmethod
    def interrupt(e: Effect, intr: int = 1) -> None:
        """vm.SetInterrupt(focus 退场等; Player.cpp:1462)。"""
        e.vm2d.vm.pending_interrupt = intr

    def update(self, player_pos: tuple[float, float] | None = None) -> None:
        alive = []
        for e in self.effects:
            e.vm2d.execute()
            if e.kind in (_FX_BURST, _FX_BURST_FAST):
                # UpdatePhysics (EffectManager.cpp:103)
                e.x += e.vx
                e.y += e.vy
                e.vx += e.ax
                e.vy += e.ay
            elif e.kind == _FX_ATTACH and player_pos is not None:
                e.x, e.y = player_pos  # UpdateAttachToPlayer
            e.timer += 1
            if e.kind == _FX_BURST30:
                # UpdateBurst30Frames (EffectManager.cpp:232):
                # pos = direction * (timer*256/30) + emitterPosition
                d = e.timer * 256.0 / 30.0
                e.x = e.ex + e.vx * d
                e.y = e.ey + e.vy * d
            if e.vm2d.alive:
                alive.append(e)
        self.effects = alive

    def draw(self, surf: pygame.Surface) -> None:
        for e in self.effects:
            e.vm2d.draw(surf, e.x, e.y)
