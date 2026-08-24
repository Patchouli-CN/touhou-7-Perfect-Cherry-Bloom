"""bomb 视觉还原 —— 对照 th07 BombData.cpp 的 12 个 *Draw + Gui cutin/横幅。

【th07 专属】本模块逐行对应妖妖梦 BombData.cpp/Gui.cpp, 机体常量 CHAR_* 与
符卡名/cutin 贴图表(_BOMB_CUTIN)都绑死 th07 的 bomb 行为与资源编号,
注入参数化的成本远大于收益 —— 从 games/th07/bomb import CHAR_* 属后端
内聚, 是有意保留的作品耦合(新作品复用窗口版需自带 bomb view)。

逻辑层 (games/th07/bomb.py) 只移植 *Calc 的伤害/清弹盒与 sub_info 运动状态,
anm VM 全部留在本模块 (C++ 里 subInfo->vms 由 *Calc 里 ExecuteAnmIdx 启动、
*Draw 里按公式摆位绘制; 这里由渲染层按相同条件自行持有 Vm2d):

- 位置/旋转/缩放公式逐条照抄各 *Draw (注释标 BombData.cpp 行号);
  透明/缩放/偏移的时序在 anm 脚本数据里, 由共享 AnmVm 解释器
  (anm_vm.py) 原样执行, 不手工近似。
- 每帧第一行的 DarkenViewport (BombData.cpp:31-61) 用黑色叠加近似
  (原版是 g_Stage.SmoothBlendColor 的全局色调平滑, 2D 层无对应管线)。
- SpawnBombInvulnEffect (BombData.cpp:63-85): etama script 0x2DA 红环,
  scale 插值到 0.0625 / 反转角速度照抄, 无敌中跟随自机、无敌结束即消
  (Player.cpp:1915-1930)。
- cutin 立绘 + 左右装饰 + 符卡名横幅: Gui::ShowBombNamePortrait
  (Gui.cpp:343-358) + OnDraw 段 (Gui.cpp:1705-1710); 符卡名本体
  (text.anm script 1796) 纹理外链无法取字形, 用日文字体渲染文字,
  位置/透明度/缩放仍由 text.anm 脚本 VM 驱动 (仅取运动, 不取贴图)。
- 画面震动 BombEffects::RegisterChain (ScreenEffect.cpp) 未移植。
- 绘制层: cutin/横幅画在 640x480 窗口层 (Gui::OnDraw 画全窗口
  framebuffer), 脚本窗口坐标直接绘制; 底条为 ascii script 1
  (无 ANM_22 → 默认中心锚, AnmVmBase.Initialize), bg.pos=name.pos
  直画 (Gui.cpp:1724-1725), 与 spellcard_view 同口径。
- 樱之结界 (Player.cpp 的 EffectManager 段, 见 _tick_border):
  ActivateBorder 的樱花圈 (effect 28 = etama script 0x2db, :2117-2136)
  与 BreakBorder 的扩散环+32 樱点粒子 (:2159-2183)。

原版语义说明: 清弹盒/伤害盒在原版**没有任何视觉** (纯逻辑盒),
bomb 结束后多活的盒 (如灵梦B 集中 210>190 的 20 帧) 也不该有画面,
所以本模块只画 VM; bomb 结束 (is_in_use 落下沿) 全部 VM 立即撤掉,
与 C++ 的 "calc/draw 停止调用即消失" 一致。
"""
from __future__ import annotations

import math

import pygame

from ....schema.anm import parse_scripts
from ..bomb import (CHAR_MARISA_A, CHAR_MARISA_B, CHAR_REIMU_A,
                                CHAR_REIMU_B, CHAR_SAKUYA_A, CHAR_SAKUYA_B,
                                BorderState)
from ....engine.view.anm_fx import AnmScriptBank, TransformCache, Vm2d
from ....engine.view.anm_vm import AnmVm, ScriptRef, reset_and_run

_ANM_OFFSET_PLAYER = 0x400
_ANM_OFFSET_BULLETS = 0x200
_ANM_OFFSET_FACE = 0x4A0

# bomb 脚本全局 id (AnmIdx.hpp:59-68)
_SCR_REIMU_A = 0x485         # 8 珠 × 4 vm
_SCR_REIMU_B = 0x489         # 4 结界光束
_SCR_REIMU_B_FOCUS = 0x48D   # 3 重结界
_SCR_MARISA_A = 0x405        # 星, i%3
_SCR_MARISA_B = 0x40C        # 3 旋转激光臂
_SCR_MARISA_B_FOCUS = 0x408  # 4 魔炮
_SCR_SAKUYA_A = 0x405        # 刀, +(i&1)
_SCR_SAKUYA_A_FOCUS = 0x407
_SCR_SAKUYA_A_HIT = 0x460    # 1120: 命中钉住后的刀 (BombData.cpp:1272/1442)
_SCR_SAKUYA_B = 0x409        # 4 方阵
_SCR_SAKUYA_B_FOCUS = 0x40D  # 2 时停领域
_SCR_INVULN_RING = 0x2DA     # SpawnBombInvulnEffect → SpawnEffect(25)
_SCR_BORDER_RING = 0x2DB     # 结界樱花圈 → SpawnEffect(28) (g_EffectMapping[28])
_FX_BORDER_PETAL = 29        # 破裂樱点粒子 SpawnParticles(29) (Player.cpp:2181)

# 符卡名 + cutin 立绘 sprite (ShowBombNamePortrait 调用点实参, 含原版 quirks:
# 魔理沙A 散传 1187 / 魔理沙B 散传 1185, 为同一 face 文件内的姿势差分)
_BOMB_CUTIN: dict[tuple[int, bool], tuple[int, str]] = {
    (CHAR_REIMU_A, False): (1185, "霊符「夢想封印　散」"),    # BombData.cpp:136
    (CHAR_REIMU_A, True): (1185, "霊符「夢想封印　集」"),     # :334
    (CHAR_REIMU_B, False): (1185, "夢符「封魔陣」"),          # :533
    (CHAR_REIMU_B, True): (1185, "夢符「二重結界」"),         # :644
    (CHAR_MARISA_A, False): (1187, "魔符「スターダストレヴァリエ」"),  # :722
    (CHAR_MARISA_A, True): (1186, "魔符「ミルキーウェイ」"),  # :831
    (CHAR_MARISA_B, False): (1185, "恋符「ノンディレクショナルレーザー」"),  # :979
    (CHAR_MARISA_B, True): (1186, "恋符「マスタースパーク」"),  # :1106
    (CHAR_SAKUYA_A, False): (1185, "幻符「インディスクリミネイト」"),  # :1206
    (CHAR_SAKUYA_A, True): (1185, "幻符「殺人ドール」"),      # :1338
    (CHAR_SAKUYA_B, False): (1187, "時符「パーフェクトスクウェア」"),  # :1507
    (CHAR_SAKUYA_B, True): (1187, "時符「プライベートスクウェア」"),   # :1632
}

_FACE_ANM = ("face_rm00.anm", "face_mr00.anm", "face_sk00.anm")
_SCR_PORTRAIT = 1185          # face 链空间局部 1
_SCR_DECOR_L, _SCR_DECOR_R = 1188, 1190
_SPR_DECOR = 1196             # 装饰 sprite (局部 12 = entry1 sprite 4)
_SCR_NAME_BG = 1              # ascii.anm 符卡名底条 (Gui.cpp:657)
_SCR_NAME_TEXT = 4            # text.anm 局部 4 = 全局 1796 (Gui.cpp:349)
_NAME_SPRITE_W = 320          # text.anm sprite 宽 (quad 左缘 = pos.x - w/2·scale)


def _darken_alpha(timer: int, duration: int) -> int:
    """DarkenViewport (BombData.cpp:31-61) 的明暗系数 → 黑色叠加 alpha 近似。

    原版 rgb = 128 - t*80/60 (淡入) / 48 (保持) / 对称淡出, alpha=128,
    经 Stage::SmoothBlendColor 平滑后作全局色调; 这里折成黑罩透明度
    (0 → 160 → 0), 视觉上等效于原版的压暗。
    """
    if timer < 60:
        c = 128 - timer * 80 // 60
    elif timer >= duration - 60:
        c = 128 - (duration - timer) * 80 // 60
    else:
        c = 48
    return max(0, min(255, (128 - c) * 2))


class _SubVmPool:
    """sub_info 驱动的 VM 池: state 0→非0 启动脚本, →0 撤掉。"""

    def __init__(self) -> None:
        self.vms: dict[int, list[Vm2d]] = {}

    def sync(self, bomb, count: int, mk) -> None:
        """mk(i) -> list[Vm2d]: 为 sub i 建 VM(可多个)。返回活跃的 sub 下标。"""
        for i in range(count):
            sub = bomb.sub_info[i]
            if sub.state and i not in self.vms:
                made = mk(i)
                if made:
                    self.vms[i] = made
            elif not sub.state and i in self.vms:
                del self.vms[i]


class BombView:
    """12 套 bomb 视觉 + 暗化 + cutin/横幅; render() 每帧由 GameView 调用。"""

    def __init__(self, bank, tcache: TransformCache) -> None:
        self.bank = bank
        self.tcache = tcache
        self._sbanks: dict[tuple[str, int], AnmScriptBank] = {}
        self._text_scripts: dict[int, list] | None = None
        self._dark = pygame.Surface((1, 1))  # 真正尺寸在 _draw_darken 惰性建
        # ---- bomb 运行期状态 (每次 bomb 重建) ----
        self._running = False
        self._key: tuple[int, bool] | None = None
        self._vms: list[Vm2d] = []            # 固定阵列 VM (非 sub 驱动)
        self._pool = _SubVmPool()
        self._sakuya_hit: set[int] = set()    # 咲夜A 已换 1120 的刀
        self._squares_started = False         # 咲夜B 散 timer==30 方阵
        self._trails: dict[int, list[tuple[float, float]]] = {}
        # ---- 无敌红环 (独立于 bomb, 无敌计时归零消失) ----
        self._ring: Vm2d | None = None
        self._ring_s0 = (1.0, 1.0)
        self._ring_frames = 0
        # ---- 樱之结界 (独立于 bomb, 由 game.border 状态驱动) ----
        self._border_ring: Vm2d | None = None          # ACTIVE 中跟随自机的圈
        self._border_break: list[tuple[Vm2d, float, float]] = []  # 破裂扩散环
        self._border_prev: tuple[int, int, int] = (0, 0, 0)
        # (id(border), has_border, invulnerability_timer) 上帧快照, 边沿判定用
        # ---- cutin/横幅 (Gui 层, 由自身脚本收尾) ----
        self._cutin: list[Vm2d] = []          # portrait/decorL/decorR/bg
        self._name_vm: AnmVm | None = None    # text.anm 运动 VM (无贴图)
        self._name_text = ""
        self._name_bg: Vm2d | None = None
        # ---- 测试断言用: 本帧 bomb 特效 / cutin 绘制调用数 ----
        self.effect_draws = 0
        self.gui_draws = 0

    # ---- 资源 ----
    def _sbank(self, name: str, base: int) -> AnmScriptBank | None:
        key = (name, base)
        sb = self._sbanks.get(key)
        if sb is None:
            sb = AnmScriptBank(self.bank, name, base)
            self._sbanks[key] = sb
        return sb if sb.ok else None

    def _text_script(self, sid: int) -> list | None:
        """text.anm 脚本表 (纹理外链, AnmFile.parse 不支持, 只取脚本数据)。"""
        if self._text_scripts is None:
            scripts: dict[int, list] = {}
            try:
                arc = self.bank._archive()
                raw = None
                for key in ("text.anm", "data/text.anm"):
                    try:
                        raw = arc.load(key)
                        break
                    except KeyError:
                        continue
                if raw is not None:
                    scripts = parse_scripts(raw)[0]
            except Exception:
                scripts = {}
            self._text_scripts = scripts
        return self._text_scripts.get(sid)

    # ---- 绘制小助手 ----
    def _draw(self, vm: Vm2d, surf: pygame.Surface, x: float, y: float,
              *, no_rotation: bool = False) -> None:
        if not vm.alive:
            return
        if no_rotation:
            saved = vm.vm.rotation[2]
            vm.vm.rotation[2] = 0.0
            vm.draw(surf, x, y)
            vm.vm.rotation[2] = saved
        else:
            vm.draw(surf, x, y)
        self.effect_draws += 1

    # ==================================================================
    # 主入口
    # ==================================================================
    def render(self, surf: pygame.Surface, game, fx) -> None:
        """bomb 特效相: 边沿管理 + 暗化 + 12 套本体 + 无敌环 (画在子弹之上)。"""
        bomb = game.bomb
        self.effect_draws = 0
        # 边沿: 触发 → 建 VM; 结束 → 全撤 (C++: calc/draw 停止调用即消失)
        if bomb.is_in_use and not self._running:
            self._start(game, fx)
        elif self._running and not bomb.is_in_use:
            self._finish()
        if self._running:
            self._draw_darken(surf, bomb.timer, bomb.duration)
            self._tick_run(surf, game, fx)
        self._tick_ring(surf, game)
        self._tick_border(surf, game, fx)

    def render_gui(self, surf: pygame.Surface, font) -> None:
        """cutin/横幅相: Gui 层, 画在战斗画面最顶 (Gui::OnDraw 段)。"""
        self.gui_draws = 0
        self._tick_cutin(surf, font)

    @property
    def gui_active(self) -> bool:
        """cutin/横幅仍在活动 (GameView 据此决定是否加载字体)。"""
        return bool(self._cutin) or self._name_vm is not None

    # ---- 触发/结束 ----
    def _start(self, game, fx) -> None:
        bomb = game.bomb
        self._running = True
        self._key = (bomb.character, bomb.is_focus)
        self._vms = []
        self._pool = _SubVmPool()
        self._sakuya_hit = set()
        self._squares_started = False
        self._trails = {}
        self._start_ring(game)
        self._start_cutin(game)
        char, focus = self._key
        sb = self._sbank(f"player0{char // 2}.anm", _ANM_OFFSET_PLAYER)

        def fixed(gids) -> None:
            if sb is None:
                return
            for gid in gids:
                vm = Vm2d(sb, self.tcache)
                if vm.start(gid):
                    self._vms.append(vm)

        if (char, focus) == (CHAR_REIMU_B, False):
            fixed(_SCR_REIMU_B + i for i in range(4))
        elif (char, focus) == (CHAR_REIMU_B, True):
            fixed(_SCR_REIMU_B_FOCUS + i for i in range(3))
        elif (char, focus) == (CHAR_MARISA_A, False):
            fixed(_SCR_MARISA_A + i % 3 for i in range(8))
        elif (char, focus) == (CHAR_MARISA_B, False):
            fixed(_SCR_MARISA_B + i for i in range(3))
        elif (char, focus) == (CHAR_MARISA_B, True):
            fixed(_SCR_MARISA_B_FOCUS + i for i in range(4))
        elif (char, focus) == (CHAR_SAKUYA_B, True):
            fixed(_SCR_SAKUYA_B_FOCUS + i for i in range(2))
            for i in range(2):
                # trails[32] 全填出发点 (BombData.cpp:1644-1648)
                p = game.bomb.sub_info[i].pos
                self._trails[i] = [(p.x, p.y)] * 32
        # ReimuA/MarisaA 集/SakuyaA/SakuyaB 散为 sub 驱动, 每帧 sync 启动

    def _finish(self) -> None:
        self._running = False
        self._key = None
        self._vms = []
        self._pool = _SubVmPool()
        self._trails = {}
        # EndPlayerSpellcard (Gui.cpp:50-53): 横幅名 interrupt 1, 底条 2
        if self._name_vm is not None:
            self._name_vm.pending_interrupt = 1
        if self._name_bg is not None:
            self._name_bg.vm.pending_interrupt = 2

    # ---- 无敌红环 (SpawnBombInvulnEffect, BombData.cpp:63-85) ----
    def _start_ring(self, game) -> None:
        sb = self._sbank("etama.anm", _ANM_OFFSET_BULLETS)
        if sb is None:
            return
        vm = Vm2d(sb, self.tcache)
        if not vm.start(_SCR_INVULN_RING):
            return
        vm.execute()  # 让脚本先设好初始 scale/angvel
        self._ring = vm
        self._ring_s0 = (vm.vm.scale[0], vm.vm.scale[1])
        self._ring_frames = max(1, game.bomb.invulnerability_timer)
        vm.vm.color = [255, 64, 64, 255]        # color.bytes r=255 g=64 b=64
        vm.vm.angle_vel[2] *= -1.0              # angleVel.z *= -1
        if not vm.alive:
            self._ring = None

    def _tick_ring(self, surf: pygame.Surface, game) -> None:
        """Player.cpp:1915-1930: 无敌中环跟随自机 (effect->pos1=positionCenter),
        无敌计时归零即销 (inUseFlag=0); 脚本是循环的, 不会自然结束。"""
        vm = self._ring
        if vm is None:
            return
        remaining = game.player.invulnerability_timer
        if remaining <= 0:
            self._ring = None
            return
        # scaleInterp: initial → 0.0625 历时 invulnerabilityTimer 帧
        f = 1.0 - remaining / self._ring_frames
        vm.vm.scale[0] = self._ring_s0[0] + (0.0625 - self._ring_s0[0]) * f
        vm.vm.scale[1] = self._ring_s0[1] + (0.0625 - self._ring_s0[1]) * f
        vm.execute()
        if not vm.alive:
            self._ring = None
            return
        # 不计入 effect_draws: 环的生命由无敌计时管理 (原版语义, 可超出 bomb)
        vm.draw(surf, game.player.pos.x, game.player.pos.y)

    # ==================================================================
    # 樱之结界特效 (Player.cpp 的 EffectManager 段; 逻辑在 games/th07/bomb.py
    # 的 Border, 本段只读 game.border 的既有字段做表现)
    # ==================================================================
    def _start_border_ring(self, border) -> Vm2d | None:
        """Player::ActivateBorder (Player.cpp:2117-2136): SpawnEffect(28)。

        脚本 (etama 链局部 0x2db): 256x256 樱花圈, alpha 30 帧淡到 160,
        ANGVEL 慢转, WAIT intvar 帧后自灭; start 已执行一帧
        (SetAnmIdxAndExecuteScript 语义), 之后照 C++ 逐字段覆写。
        """
        sb = self._sbank("etama.anm", _ANM_OFFSET_BULLETS)
        if sb is None:
            return None
        vm = Vm2d(sb, self.tcache)
        if not vm.start(_SCR_BORDER_RING):
            return None
        timer = max(1, int(border.invulnerability_timer))   # 激活帧定格 540
        v = vm.vm
        v.interp_start[4] = 0                # scaleInterp: 1.0 → 0.25, 全程线性
        v.interp_end[4] = timer
        v.ease[4] = 0
        v.scale_interp_initial = [1.0, 1.0]
        v.scale_interp_final = [0.25, 0.25]
        v.int_vars1[0] = timer               # 脚本 WAIT 的寿命 (= 结界剩余帧)
        v.angle_vel[2] *= -1.0               # angleVel.z *= -1 (:2135)
        return vm

    def _start_border_break(self, game, fx, x: float, y: float) -> None:
        """Player::BreakBorder (Player.cpp:2159-2183): 扩散环 + 32 樱点粒子。

        环复用 effect 28 脚本: scale 0.0625→1.3 / alpha →0 各 30 帧
        (alpha 初值取 spawn 帧 vm.color.a ≈ 5, 近乎不可见 —— 原版即如此,
        破裂的视觉主体是清弹圆与樱点粒子); 粒子 effect 29 (0x2b2)
        Burst30Frames: direction 均布 32 方向, 30 帧飞 256px。
        """
        sb = self._sbank("etama.anm", _ANM_OFFSET_BULLETS)
        if sb is not None:
            vm = Vm2d(sb, self.tcache)
            if vm.start(_SCR_BORDER_RING):
                v = vm.vm
                v.interp_start[4] = 0
                v.interp_end[4] = 30
                v.ease[4] = 0
                v.scale_interp_initial = [0.0625, 0.0625]
                v.scale_interp_final = [1.3, 1.3]
                v.interp_start[2] = 0        # colorInterp alpha → 0 (ease 1)
                v.interp_end[2] = 30
                v.ease[2] = 1
                v.color_interp_initial[3] = v.color[3]
                v.color_interp_final[3] = 0
                v.int_vars1[0] = 30
                self._border_break.append((vm, x, y))
        angle = -math.pi
        for _ in range(32):
            fx.spawn(_FX_BORDER_PETAL, x, y, 1,
                     direction=(math.cos(angle), math.sin(angle)))
            angle += 0.19634955

    def _tick_border(self, surf: pygame.Surface, game, fx) -> None:
        """结界圈生命周期: READY→ACTIVE 边沿起圈跟随自机 (:1948-1950
        borderEffect->pos1=positionCenter), ACTIVE→NONE 边沿按自然破/主动破
        分派; 破裂扩散环定格在破裂点 (UpdateNoOp, 不跟随)。
        """
        border = getattr(game, "border", None)
        if border is None:
            return
        prev_id, prev_state, prev_timer = self._border_prev
        cur_state = int(border.has_border)
        if cur_state == BorderState.ACTIVE:
            ring = self._border_ring
            if ring is None:
                ring = self._start_border_ring(border)
                self._border_ring = ring
            if ring is not None:
                ring.execute()
                if not ring.alive:
                    self._border_ring = None
                else:
                    ring.draw(surf, game.player.pos.x, game.player.pos.y)
        elif prev_state in (BorderState.ACTIVE, BorderState.READY) \
                and prev_id == id(border):
            # ACTIVE/READY→NONE: 自然破 (仅 ACTIVE 且计时耗尽, 上帧 timer<=1)
            # 只撤圈 (BreakBorderNaturally :2029-2033 仅 inUseFlag=0);
            # 主动破/中弹破/死亡破 (BreakBorder, READY 也走这里) → 扩散环+粒子。
            # id 守卫: 换关/重开时逻辑层整体换新 Border 对象, 不算破裂
            self._border_ring = None
            if prev_state == BorderState.READY or prev_timer > 1:
                self._start_border_break(game, fx, game.player.pos.x,
                                         game.player.pos.y)
        else:
            self._border_ring = None
        self._border_prev = (id(border), cur_state,
                             int(border.invulnerability_timer))
        alive = []
        for vm, x, y in self._border_break:
            vm.execute()
            if vm.alive:
                alive.append((vm, x, y))
                vm.draw(surf, x, y)
        self._border_break = alive

    # ---- 暗化 ----
    def _draw_darken(self, surf: pygame.Surface, timer: int,
                     duration: int) -> None:
        alpha = _darken_alpha(timer, duration)
        if alpha <= 0:
            return
        if self._dark.get_size() != surf.get_size():
            self._dark = pygame.Surface(surf.get_size())
        self._dark.fill((0, 0, 0))
        self._dark.set_alpha(alpha)
        surf.blit(self._dark, (0, 0))
        self.effect_draws += 1

    # ==================================================================
    # 12 套 bomb 本体 (对照各 *Draw)
    # ==================================================================
    def _tick_run(self, surf: pygame.Surface, game, fx) -> None:
        char, focus = self._key
        if char == CHAR_REIMU_A:
            self._reimu_a(surf, game)
        elif char == CHAR_REIMU_B:
            self._reimu_b(surf, game, focus)
        elif char == CHAR_MARISA_A:
            self._marisa_a(surf, game, focus)
        elif char == CHAR_MARISA_B:
            self._marisa_b(surf, game, focus)
        elif char == CHAR_SAKUYA_A:
            self._sakuya_a(surf, game, focus, fx)
        elif char == CHAR_SAKUYA_B:
            if focus:
                self._sakuya_b_focus(surf, game)
            else:
                self._sakuya_b(surf, game)

    def _reimu_a(self, surf: pygame.Surface, game) -> None:
        """BombReimuADraw/Focus (BombData.cpp:260-305/478-512):
        8 珠 × 4 vm, pos = 珠位 + vm->offset, DrawNoRotation。"""
        bomb = game.bomb
        sb = self._sbank("player00.anm", _ANM_OFFSET_PLAYER)

        def mk(i):
            out = []
            if sb is not None:
                for j in range(4):
                    vm = Vm2d(sb, self.tcache)
                    if vm.start(_SCR_REIMU_A + j):
                        out.append(vm)
            return out

        self._pool.sync(bomb, 8, mk)
        for i, vms in self._pool.vms.items():
            sub = bomb.sub_info[i]
            for vm in vms:
                vm.execute()
                self._draw(vm, surf, sub.pos.x + vm.vm.offset[0],
                           sub.pos.y + vm.vm.offset[1], no_rotation=True)

    def _reimu_b(self, surf: pygame.Surface, game, focus: bool) -> None:
        """BombReimuBDraw/Focus (BombData.cpp:607-622/685-700):
        锚点 (sub_info.pos 或 startPos) + vm->offset, Draw (带旋转)。"""
        bomb = game.bomb
        for i, vm in enumerate(self._vms):
            vm.execute()
            base = bomb.start_pos if focus else bomb.sub_info[i].pos
            self._draw(vm, surf, base.x + vm.vm.offset[0],
                       base.y + vm.vm.offset[1])

    def _marisa_a(self, surf: pygame.Surface, game, focus: bool) -> None:
        """BombMarisaADraw/Focus (BombData.cpp:769-805/912-951):
        每星同 vm 连画 3 次 (残影拖尾), scale 3.2/2.2/1.0(集 1.3)。"""
        bomb = game.bomb
        if not focus:
            for i, vm in enumerate(self._vms):
                vm.execute()
                sub = bomb.sub_info[i]
                p, v = sub.pos, sub.vel
                vm.vm.scale = [3.2, 3.2]
                self._draw(vm, surf, p.x, p.y)
                vm.vm.scale = [2.2, 2.2]
                self._draw(vm, surf, p.x - v.x * 6 - 32.0, p.y - v.y * 6 - 32.0)
                vm.vm.scale = [1.0, 1.0]
                self._draw(vm, surf, p.x - v.x * 10.0, p.y - v.y * 10.0)
            return
        sb = self._sbank("player01.anm", _ANM_OFFSET_PLAYER)

        def mk(i):
            if sb is None:
                return []
            vm = Vm2d(sb, self.tcache)
            if not vm.start(_SCR_MARISA_A + i % 3):
                return []
            p = bomb.sub_info[i].pos
            self._trails[i] = [(p.x, p.y)] * 8   # trails[8] 全填出发点
            return [vm]

        self._pool.sync(bomb, 24, mk)
        dead = [i for i in self._trails if i not in self._pool.vms]
        for i in dead:
            del self._trails[i]
        for i, vms in self._pool.vms.items():
            vm = vms[0]
            vm.execute()
            sub = bomb.sub_info[i]
            trail = self._trails[i]
            vm.vm.scale = [3.2, 3.2]
            self._draw(vm, surf, sub.pos.x, sub.pos.y)
            vm.vm.scale = [2.2, 2.2]
            self._draw(vm, surf, trail[3][0], trail[3][1])
            vm.vm.scale = [1.3, 1.3]
            self._draw(vm, surf, trail[7][0], trail[7][1])
            trail.insert(0, (sub.pos.x, sub.pos.y))
            del trail[8:]

    def _marisa_b(self, surf: pygame.Surface, game, focus: bool) -> None:
        """BombMarisaBDraw/Focus (BombData.cpp:1060-1084/1157-1183):
        以玩家为根, pos += dir(accel) * sprite 高 * scale.y / 2,
        rotation.z = accel + π/2。"""
        bomb = game.bomb
        p = game.player.pos
        for i, vm in enumerate(self._vms):
            vm.execute()
            if focus:
                accel = i * 0.62831855 / 3.0 - math.pi + 1.2566371
            else:
                accel = bomb.sub_info[i].accel  # accel 被复用为臂角度
            h = vm.surf.get_height() if vm.surf is not None else 0.0
            d = h * vm.vm.scale[1] / 2.0
            vm.vm.rotation[2] = accel + math.pi / 2
            self._draw(vm, surf, p.x + math.cos(accel) * d,
                       p.y + math.sin(accel) * d)

    def _sakuya_a(self, surf: pygame.Surface, game, focus: bool, fx) -> None:
        """BombSakuyaADraw/Focus (BombData.cpp:1290-1314/1457-1482):
        96 刀, state!=0 才画, rotation.z = angle + π/2; 命中换 anm 1120。"""
        bomb = game.bomb
        sb = self._sbank("player02.anm", _ANM_OFFSET_PLAYER)
        base = _SCR_SAKUYA_A_FOCUS if focus else _SCR_SAKUYA_A

        def mk(i):
            if sb is None:
                return []
            vm = Vm2d(sb, self.tcache)
            if not vm.start(base + (i & 1)):
                return []
            return [vm]

        self._pool.sync(bomb, 96, mk)
        for i, vms in self._pool.vms.items():
            vm = vms[0]
            sub = bomb.sub_info[i]
            # 命中钉住: ExecuteAnmIdx(1120) (BombData.cpp:1271-1273/1441-1445)
            if i not in self._sakuya_hit and sb is not None:
                dmg = bomb.damage_boxes[i].damage
                if (not focus and 30 <= dmg) or (focus and dmg > 0):
                    vm.start(_SCR_SAKUYA_A_HIT)
                    self._sakuya_hit.add(i)
                    if focus:
                        # SpawnParticles(0, pos, 1, 0xffff80ff) (:1443-1445)
                        fx.spawn(0, sub.pos.x, sub.pos.y, 1,
                                 color=0xFFFF80FF)
            vm.execute()
            vm.vm.rotation[2] = sub.angle + math.pi / 2
            self._draw(vm, surf, sub.pos.x, sub.pos.y)
        # 撤掉的刀清命中标记, 防槽位复用串状态
        self._sakuya_hit &= set(self._pool.vms)

    def _sakuya_b(self, surf: pygame.Surface, game) -> None:
        """BombSakuyaBDraw (BombData.cpp:1581-1602): 4 方阵, timer==30 启动,
        vm->pos 在 Calc 已设为 (192±128, 224±128) (:1537-1540), Draw。"""
        bomb = game.bomb
        if not self._squares_started and bomb.timer >= 30:
            sb = self._sbank("player02.anm", _ANM_OFFSET_PLAYER)
            if sb is not None:
                for i in range(4):
                    vm = Vm2d(sb, self.tcache)
                    if vm.start(_SCR_SAKUYA_B + i):
                        vm.vm.pos[0] = 192.0 + (128.0 if i & 1 else -128.0)
                        vm.vm.pos[1] = 224.0 + (128.0 if i // 2 else -128.0)
                        vm.vm.pos[2] = 0.0
                        self._vms.append(vm)
            self._squares_started = True
        for i, vm in enumerate(self._vms):
            if not bomb.sub_info[i].state:
                continue
            vm.execute()
            self._draw(vm, surf, vm.vm.pos[0], vm.vm.pos[1])

    def _sakuya_b_focus(self, surf: pygame.Surface, game) -> None:
        """BombSakuyaBDrawFocus (BombData.cpp:1711-1744): 2 领域 + 残影环
        (trails[3,7,…,31], alpha = old - old*j/32)。"""
        bomb = game.bomb
        for i, vm in enumerate(self._vms):
            sub = bomb.sub_info[i]
            if not sub.state:
                continue
            vm.execute()
            trail = self._trails[i]
            old_alpha = vm.vm.color[3]
            self._draw(vm, surf, sub.pos.x, sub.pos.y)
            for j in range(3, 32, 4):
                vm.vm.color[3] = old_alpha - old_alpha * j // 32
                self._draw(vm, surf, trail[j][0], trail[j][1])
            vm.vm.color[3] = old_alpha
            trail.insert(0, (sub.pos.x, sub.pos.y))
            del trail[32:]

    # ==================================================================
    # cutin 立绘 + 符卡名横幅 (Gui::ShowBombNamePortrait, Gui.cpp:343-358)
    # ==================================================================
    def _start_cutin(self, game) -> None:
        char, focus = self._key
        sprite_gid, name = _BOMB_CUTIN[(char, focus)]
        self._cutin = []
        self._name_vm = None
        self._name_bg = None
        self._name_text = name
        face = self._sbank(_FACE_ANM[char // 2], _ANM_OFFSET_FACE)
        if face is not None:
            portrait = Vm2d(face, self.tcache)
            if portrait.start(_SCR_PORTRAIT):
                # SetActiveSprite 立绘差分 (Gui.cpp:346)
                portrait.set_sprite(sprite_gid - _ANM_OFFSET_FACE)
                self._cutin.append(portrait)
            for gid in (_SCR_DECOR_L, _SCR_DECOR_R):
                vm = Vm2d(face, self.tcache)
                if vm.start(gid):
                    vm.set_sprite(_SPR_DECOR - _ANM_OFFSET_FACE)
                    self._cutin.append(vm)
        ascii_sb = self._sbank("ascii.anm", 0)
        if ascii_sb is not None:
            bg = Vm2d(ascii_sb, self.tcache)
            if bg.start(_SCR_NAME_BG):
                # 脚本开头等 interrupt 1 才入场 (ShowBombNamePortrait 的
                # SetInterrupt(1), Gui.cpp:355); 结束由 interrupt 2 推出
                bg.vm.pending_interrupt = 1
                self._name_bg = bg
        instrs = self._text_script(_SCR_NAME_TEXT)
        if instrs:
            vm = AnmVm()
            reset_and_run(vm, ScriptRef(instrs, 0), lambda key: None)
            self._name_vm = vm

    def _tick_cutin(self, surf: pygame.Surface, font) -> None:
        """Gui::OnDraw cutin 段 (Gui.cpp:1705-1710) + OnUpdate (:1282-1284)。

        画在 640x480 窗口层: 脚本坐标即窗口坐标, 直接绘制不换算。
        """
        if not self._cutin and self._name_vm is None and self._name_bg is None:
            return
        alive = []
        for k, vm in enumerate(self._cutin):
            vm.execute()
            if vm.alive:
                alive.append(vm)
            x = vm.vm.pos[0] + vm.vm.offset[0]
            y = vm.vm.pos[1] + vm.vm.offset[1]
            # portrait/decorL: DrawNoRotation; decorR: Draw (Gui.cpp:1707-1709)
            self._draw_gui(vm, surf, x, y, no_rotation=(k != 2))
        self._cutin = alive
        # 底条独立推进 (Gui.cpp:1284 ExecuteScript), 画时贴名字位置
        bg = self._name_bg
        if bg is not None:
            bg.execute()
            if not bg.alive:
                self._name_bg = None
                bg = None
        name = self._name_vm
        if name is not None:
            name.execute()
            if name.pc < 0:
                self._name_vm = None
            elif name.visible:
                nx = name.pos[0] + name.offset[0]
                ny = name.pos[1] + name.offset[1]
                if bg is not None:
                    # bg.pos = name.pos, DrawNoRotation (Gui.cpp:1724-1725);
                    # ascii script 1 无 ANM_22 → 中心锚, 与 spellcard 同口径
                    self._draw_gui(bg, surf, nx, ny, no_rotation=True)
                # DrawVmTextFmt (Gui.cpp:351-353): 字形纹理外链, 用字体渲染;
                # 文字从 sprite 左缘起画 → 左缘 = pos.x - w/2·scale
                # (AnmManager.cpp:2312-2341, 中心锚点 quad)
                if font is not None and self._name_text:
                    alpha = name.color[3]
                    img = font.render(self._name_text, True, (240, 240, 255))
                    sx, sy = name.scale[0], name.scale[1]
                    if sx != 1.0 or sy != 1.0:
                        img = self.tcache.get(img, sx, sy, 0.0)
                    if alpha < 255:
                        img = img.copy()
                        img.set_alpha(alpha)
                    left = nx - _NAME_SPRITE_W * sx / 2
                    rect = img.get_rect(midleft=(int(left), int(ny)))
                    surf.blit(img, rect)
                    self.gui_draws += 1

    def _draw_gui(self, vm: Vm2d, surf: pygame.Surface, x: float, y: float,
                  *, no_rotation: bool = False) -> None:
        if not vm.alive:
            return
        if no_rotation:
            saved = vm.vm.rotation[2]
            vm.vm.rotation[2] = 0.0
            vm.draw(surf, x, y)
            vm.vm.rotation[2] = saved
        else:
            vm.draw(surf, x, y)
        self.gui_draws += 1
