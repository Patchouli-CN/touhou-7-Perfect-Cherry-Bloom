""" 战斗画面渲染(贴图精灵层) —— 对照 th07 反编译的绘制段做 2D 近似。

实体动画跑 anm 脚本 VM(AnmVm, 见 anm_vm.py/anm_fx.py, 对照
AnmManager.cpp::ExecuteScript): 敌人按 ECL SET_ANM 的 script 从第 0 帧
执行(EclManager.cpp:1196 primaryVm = anm_idx + 2304), 自机静止/侧移
过渡脚本(Player.cpp:2424/1368-1384), 子机旋转脚本(1152/1153),
自机弹命中爆发动画(vm.anmFileIdx+32, Player.cpp:895)。死亡/命中特效走
EffectLayer(EffectManager.cpp g_EffectMapping 子集): 击坠爆炸 deathAnm1、
道具爆皮 deathAnm2+4(EnemyManager.cpp:1017-1020)、自机弹命中火花
(Player.cpp:896)、玩家死亡大爆(Player.cpp:1234-1235)、focus 判定点环
(Player.cpp:1438)。背景为 .std 驱动的 3D 场景, 由 bg3d_view.py 软件渲染;
加载或渲染失败时退回 stgNbg.anm 主纹理平铺竖滚近似。

sprite id 映射结论(开发期 dump th07.dat + 目检, 工具 scratch_dbg/anm_dump.py):

- 全局编号 = ANM_OFFSET + entry 链式偏移 + 文件内局部 id
  (AnmManager::LoadAnms: 链式 entry 的 spriteIdxOffset 按
  max(sprite id, script id)+1 累加; ANM_OFFSET_PLAYER=0x400,
  BULLETS=0x200, ENEMY=0x900, FRONT=0x600, 见 AnmIdx.hpp)。
- 敌人: ECL SET_ANM 存进 EclEnemyState.anm_idx 的是 stgNenm.anm 的
  **局部** script id(实测 stage 1: 0/5/10=妖精, 128+=boss, 见
  sheet_stg1enm_anm_e0.png); script 首帧 sprite 即代表帧。
- 敌弹: engine/bullets.py 的 bullet_active_sprite_idx() 已给出全局
  sprite idx(0x200 基址 + 模板基址 + spriteOffset); 每弹型 16 色连续,
  spriteOffset 直接是颜色变体(etama.anm 内是同色不同贴图, 非调色脚本)。
- 道具: ItemManager.cpp:91 SetAnmIdxAndExecuteScript(itemType + 708)
  → etama.anm entry1(链式偏移 168, 全局 680)局部 script 28+itemType
  → sprite 4..12(小P/点/大P/B/F/1up/弹消点/樱/小樱/星→同樱)。
- 自机: player0{character//2}.anm; 静止帧 sprite 0..7(慢摇),
  侧移倾斜帧 sprite 12(右移水平翻转; 原版 script 1025-1028 是过渡动画,
  这里取代表帧); 子机 sprite 128(16x16, script 1152/1153 首帧)。
- 自机弹: .sht 的 anmFileIdx 是全局 script id(0x400 基)→ 局部 script
  = idx - 0x400 → 脚本 VM 逐帧执行(Player.cpp:119 同口径; 脚本带
  SET_ALPHA/SCALE/ANGVEL, 札弹半透明旋转由此而来)。
- 激光: etama.anm entry0 sprite 152+color(16 色渐变段)拉伸旋转。
- front.anm: 0=右侧栏 logo, 2..8=HiScore/Score/Player/Bomb/Power/Graze/
  Point 标签, 10/11=残机/炸弹星, 12=红点标记, 13=boss 血条
  (右栏 HUD 全貌见 hud_view.py)。

近似项(与原版有差距, 均在此标注): bomb 视觉已按 BombData.cpp *Draw 用
anm 脚本 VM 还原(见 bomb_view.py; 画面震动 BombEffects 未移植)、boss 符卡
宣言(立绘+符卡名横幅+右上常驻+捕获分数字)与关卡标题(含 MSG_MUSIC 的
boss 曲名行重触发)按 Gui.cpp 的 VM 构图还原(见 spellcard_view.py/
stage_title_view.py; GUI 层画在 640x480 窗口层, 见 render_gui)、
敌弹本体保持单帧(弹脚本
仅 SET_ACTIVE_SPRITE+AUTOROT 一次性指令, 无帧动画; AUTOROT 长条弹按速度
方向旋转, 与脚本标记一致)、boss 魔法阵(etama 符文圈旋转代替 per-stage
贴图)、ECL deathType=3(boss 离场)的 ×3 爆炸未接(逻辑层不区分离场与超
时)、敌人 trail 残影未移植。
"""

from __future__ import annotations

import math
import struct
import time
from pathlib import Path

import numpy as np
import pygame

from ...logger import logger as log
from ...schema.anm import AnmFile
from ...schema.archive import GameArchive
from ..bullets import SCREEN, bullet_active_sprite_idx
from .anm_fx import AnmScriptBank, EffectLayer, TransformCache, Vm2d
from .bg3d_view import StageScene
from .bomb_view import BombView
from .spellcard_view import SpellcardView
from .stage_title_view import StageTitleView

GAME_W, GAME_H = int(SCREEN.x), int(SCREEN.y)   # 384x448 游戏区
GAME_X, GAME_Y = 32, 16                         # 游戏区在 640x480 窗口的左上
WIN_W, WIN_H = 640, 480

_ANM_OFFSET_PLAYER = 0x400
_ANM_OFFSET_BULLETS = 0x200
_ANM_OFFSET_ENEMY = 0x900

# 自机脚本(player0N.anm 局部 id; Player.cpp:2424 idle=1024,
# Player.cpp:1368-1384 侧移过渡 1025-1028 → 局部 0..4)
_PLAYER_SCRIPT_IDLE = 0
_PLAYER_SCRIPT_LEFT, _PLAYER_SCRIPT_LEFT_END = 1, 2
_PLAYER_SCRIPT_RIGHT, _PLAYER_SCRIPT_RIGHT_END = 3, 4
_PLAYER_OPTION_SCRIPTS = (128, 129)             # 子机(Player.cpp:2451-2452)

# 敌弹出生特效脚本(g_BulletTypeInfos spawnFast/Normal/SlowIdx,
# BulletManager.cpp:16-28; spawn_state 2/4/8 → fast/normal/slow)
_SPAWN_FX_GIDS: dict[int, tuple[int, int, int]] = {
    0: (0x212, 0x213, 0x214),
    1: (0x215, 0x216, 0x217), 2: (0x215, 0x216, 0x217),
    3: (0x215, 0x216, 0x217), 4: (0x215, 0x216, 0x217),
    5: (0x215, 0x216, 0x217), 6: (0x215, 0x216, 0x217),
    7: (0x218, 0x218, 0x218), 8: (0x218, 0x218, 0x218),
    9: (0x218, 0x218, 0x218),
    10: (0x2AA, 0x2AA, 0x2AA),
}
_SPAWN_FX_IDX = {2: 0, 4: 1, 8: 2}

# 特效 effectId(EffectManager.cpp g_EffectMapping; 语义见 anm_fx.py)
_FX_ENEMY_DEATH = 0        # deathAnm1=0 → 0x2ab 爆风环
_FX_HIT_SPARK = 5          # 自机弹命中 (Player.cpp:896)
_FX_PLAYER_DEATH_BURST = 6
_FX_PLAYER_DEATH_RING = 12
_FX_FOCUS_RING = 24

# 敌弹弹型: 需要按速度方向旋转的长条弹(米/滴/针/箭/刀), 圆弹不转
_ROTATE_BULLET_TYPES = (2, 4, 5, 6, 8)
# etama entry0 的激光段 sprite 基址(16 色)
_LASER_SPRITE_BASE = 152

# 出生态敌弹本体透明度(spawn 特效 anm 叠加其上, 见 _SPAWN_FX_GIDS)
_SPAWN_ALPHA = 96

_FONT_CANDIDATES = ("msgothic", "ms gothic", "msmincho", "meiryo",
                    "yu gothic", "hiragino sans", "noto sans cjk jp",
                    "microsoft yahei", "simhei")


def _load_font(size: int):
    """日文字体(符卡名用); 找不到退化成默认字体(同 dialog_view 容错)。"""
    if not pygame.font.get_init():
        try:
            pygame.font.init()
        except pygame.error:
            return None
    for name in _FONT_CANDIDATES:
        try:
            path = pygame.font.match_font(name)
        except Exception:
            path = None
        if path:
            try:
                return pygame.font.Font(path, size)
            except pygame.error:
                continue
    try:
        return pygame.font.SysFont(None, size)
    except pygame.error:
        return None


def _parse_first_sprites(data: bytes) -> list[dict[int, int]]:
    """按 entry 解析 script 表: [{script_id: 首条 SET_ACTIVE_SPRITE 的 sprite id}]。

    AnmRawEntry: 64 字节头 + spriteOffsets[numSprites] + (id, offset)[numScripts];
    指令 AnmRawInstr(i16 opcode, u16 size, i16 time, u16 flags, args...),
    opcode 3 = ANM_SET_ACTIVE_SPRITE (AnmManager.hpp)。
    """
    out: list[dict[int, int]] = []
    offset = 0
    while True:
        num_sprites, num_scripts = struct.unpack_from("<2i", data, offset)
        table = offset + 64 + num_sprites * 4
        scripts: dict[int, int] = {}
        for i in range(num_scripts):
            sid, soff = struct.unpack_from("<2i", data, table + i * 8)
            p = offset + soff
            last = -1
            for _ in range(64):  # 代表帧: 看脚本开头一段
                opcode, size, _time = struct.unpack_from("<hHh", data, p)
                # EXIT*/JUMP/DEC_JUMP: 脚本段结束/回跳, 停止扫描
                if opcode in (-1, 1, 2, 4, 5):
                    break
                if opcode == 3:
                    # 记录最后一个 set-sprite: 出场特效(如妖精的漩涡帧)
                    # 在脚本开头, 代表帧取后面的本体帧
                    last = struct.unpack_from("<i", data, p + 8)[0]
                if size <= 0:
                    break
                p += size
            if last >= 0:
                scripts[sid] = last
        out.append(scripts)
        next_offset = struct.unpack_from("<i", data, offset + 56)[0]
        if next_offset == 0:
            break
        offset += next_offset
    return out


class SpriteBank:
    """anm sprite 缓存: 懒加载 th07.dat, 按 (anm, entry, id) 缓存 Surface。"""

    def __init__(self, data_path: str | Path) -> None:
        self._data_path = Path(data_path)
        self._arc: GameArchive | None = None
        self._anms: dict[str, AnmFile] = {}
        self._raws: dict[str, bytes] = {}
        self._first: dict[str, list[dict[int, int]]] = {}
        self._chain: dict[str, list[int]] = {}
        self._surfs: dict[tuple[str, int, int], pygame.Surface | None] = {}
        self._rot: dict[tuple[int, int], pygame.Surface] = {}
        self._flip: dict[int, pygame.Surface] = {}

    # ---- 资源 ----
    def _archive(self) -> GameArchive:
        if self._arc is None:
            self._arc = GameArchive.open(self._data_path)
        return self._arc

    def _load(self, name: str) -> bool:
        if name in self._anms:
            return True
        arc = self._archive()
        raw = None
        for key in (name, f"data/{name}"):
            try:
                raw = arc.load(key)
                break
            except KeyError:
                continue
        if raw is None:
            return False
        self._anms[name] = AnmFile.parse(raw)
        self._raws[name] = raw
        self._first[name] = _parse_first_sprites(raw)
        # 链式 entry 偏移(LoadAnms: max(sprite id, script id)+1 累加)
        offs, cur = [], 0
        for entry, scripts in zip(self._anms[name].entries, self._first[name]):
            offs.append(cur)
            hi = max(entry.sprites.keys(), default=0)
            hi = max(hi, max(scripts.keys(), default=0))
            cur += hi + 1
        self._chain[name] = offs
        return True

    def has(self, name: str) -> bool:
        return self._load(name)

    def raw(self, name: str) -> bytes | None:
        """anm 文件原始字节(脚本解析用); 不存在返回 None。"""
        if not self._load(name):
            return None
        return self._raws[name]

    def sprite(self, name: str, sprite_id: int, entry: int = 0
               ) -> pygame.Surface | None:
        """取 sprite Surface(缓存); 不存在返回 None。"""
        key = (name, entry, sprite_id)
        if key in self._surfs:
            return self._surfs[key]
        surf = None
        if self._load(name):
            anm = self._anms[name]
            if 0 <= entry < len(anm.entries) \
                    and sprite_id in anm.entries[entry].sprites:
                w, h, rgba = anm.sprite_image(sprite_id, entry=entry)
                surf = pygame.image.fromstring(rgba, (w, h), "RGBA")
                try:
                    surf = surf.convert_alpha()  # 快速 blit 路径(需 display 初始化)
                except pygame.error:
                    pass
        self._surfs[key] = surf
        return surf

    def script_sprite(self, name: str, script_id: int, entry: int = 0
                      ) -> int | None:
        """script 首帧 sprite 的局部 id; 未知返回 None。"""
        if not self._load(name):
            return None
        if 0 <= entry < len(self._first[name]):
            return self._first[name][entry].get(script_id)
        return None

    def global_to_local(self, name: str, global_id: int, anm_offset: int
                        ) -> tuple[int, int] | None:
        """全局 sprite/script idx → (entry, 局部 id)(链式偏移反查)。"""
        if not self._load(name):
            return None
        local = global_id - anm_offset
        if local < 0:
            return None
        for i, off in enumerate(self._chain[name]):
            if local < off:
                break
            entry, lid = i, local - off
        else:
            entry, lid = len(self._chain[name]) - 1, local - self._chain[name][-1]
        if lid < 0:
            return None
        return entry, lid

    # ---- 变换(缓存) ----
    def rotated(self, surf: pygame.Surface, angle_deg: float) -> pygame.Surface:
        """按 6° 量化缓存的旋转。"""
        q = int(round(angle_deg / 6.0)) * 6 % 360
        key = (id(surf), q)
        out = self._rot.get(key)
        if out is None:
            out = pygame.transform.rotate(surf, q)
            self._rot[key] = out
        return out

    def flipped(self, surf: pygame.Surface) -> pygame.Surface:
        out = self._flip.get(id(surf))
        if out is None:
            out = pygame.transform.flip(surf, True, False)
            self._flip[id(surf)] = out
        return out


class GameView:
    """战斗画面渲染器: render() 把一帧画到 384x448 的游戏区 surface 上。"""

    def __init__(self, data_path: str | Path, *, character: int = 0,
                 stage: int = 1) -> None:
        self.bank = SpriteBank(data_path)
        self.character = character
        self._player_anm = f"player0{character // 2}.anm"
        self._stage = 0
        self._enemy_anm = ""
        self._bg_tile: pygame.Surface | None = None
        self._bg_dark: pygame.Surface | None = None
        self._bg3d: StageScene | None = None
        self._bg3d_broken = False
        self._beam: dict[tuple[int, int, int, int], pygame.Surface] = {}
        self._font = None
        self.frame = 0
        # anm VM 宿主(anm_fx.py): 脚本表/变换缓存/特效层 + 各实体 VM 状态
        self._tcache = TransformCache()
        self._sbanks: dict[str, AnmScriptBank] = {}
        self._fx: EffectLayer | None = None
        self._enemy_vis: dict[int, dict] = {}       # id(EclEnemyState) → VM 状态
        self._player_vm: Vm2d | None = None
        self._player_script = -1                    # 当前自机脚本(局部 id)
        self._prev_move_sign = 0                    # C previousHorizontalSpeed 符号
        self._option_vms: list[Vm2d] = []
        self._focus_fx = None                       # focus 判定点环 Effect 句柄
        self._prev_focus = False
        self._prev_player_dead = False
        self._shot_vis: dict[int, dict] = {}        # id(自机弹) → 命中爆炸 VM
        self._shot_fly: dict[int, Vm2d | None] = {}  # id(自机弹) → 飞行弹 VM
        self._spawn_fx: dict[tuple[int, int], Vm2d | None] = {}
        # (特效 gid, 出生剩余帧) → 共享 VM(同脚本同出生帧的弹共用, 见 _spawn_fx_vm)
        self._spawn_fx_done: dict[tuple[int, int], int] = {}  # key → 本帧已 execute
        self._bomb_view = BombView(self.bank, self._tcache)  # bomb 视觉层
        self._spellcard_view = SpellcardView(self.bank, self._tcache)  # 符卡宣言
        self._stage_title = StageTitleView(self.bank, self._tcache)  # 关卡标题
        self._bspr: dict[tuple[int, int], pygame.Surface | None] = {}
        # (弹型, sprite_offset) → etama sprite(纯函数, 全局缓存一次)
        self._balpha: dict[tuple[int, int], tuple[pygame.Surface, pygame.Surface]] = {}
        # 出生态半透明弹: (id(img), alpha) → (原图, 调 alpha 后的副本)
        self._item_imgs: dict[int, pygame.Surface | None] = {}  # itemType → sprite
        self._bg_ema_ms: float | None = None   # bg3d 耗时 EMA(动态降载用)
        self._bg_cool = 0                      # 降载调整冷却(帧)
        self._bg_period = 1                    # bg3d 渲染周期(>1 = 隔帧)
        self._bg_cache: pygame.Surface | None = None  # 隔帧复用的上一帧背景

    # ---- 关卡资源(换关时重载, 对照 Stage.cpp 的 stgNenm/stgNbg 切换) ----
    def _ensure_stage(self, stage_no: int) -> None:
        if stage_no == self._stage:
            return
        self._stage = stage_no
        self._stage_title.set_stage(stage_no)  # 关卡标题 (Gui.cpp:655 vms1)
        # 换关时 game.host 整体重建(impl._advance_stage), 旧敌人不经过
        # on_enemy_gone → gone_events 随旧 ecl_host 丢弃; 残留的 _enemy_vis
        # 条目会在 id(EnemyState) 复用时让新敌人继承旧关卡的 VM/贴图, 必须清掉
        self._enemy_vis.clear()
        self._enemy_anm = f"stg{stage_no}enm.anm"
        if not self.bank.has(self._enemy_anm):
            self._enemy_anm = ""
        self._bg_dark = None
        # 3D 背景(.std 场景): 加载失败留 None, _render_bg 退回 2D 平铺
        self._bg3d = None
        self._bg3d_broken = False
        self._bg_period = 1          # 降载状态随场景重建重置
        self._bg_ema_ms = None
        self._bg_cache = None
        try:
            self._bg3d = StageScene.load(self.bank._archive(), stage_no)
        except Exception:
            self._bg3d = None
        # 背景: stgNbg.anm entry0 最大 sprite 作平铺块(2D 近似 fallback)
        self._bg_tile = None
        bg_name = f"stg{stage_no}bg.anm"
        if self.bank.has(bg_name):
            anm = self.bank._anms[bg_name]
            sprites = anm.entries[0].sprites
            if sprites:
                biggest = max(sprites.values(), key=lambda s: s.w * s.h)
                self._bg_tile = self.bank.sprite(bg_name, biggest.id)

    def _fonts(self) -> None:
        if self._font is None:
            self._font = _load_font(15)   # 符卡名字号 (Gui.cpp:682-683 15x15)

    # ---- anm VM 宿主 ----
    def _sbank(self, name: str, base: int) -> AnmScriptBank | None:
        """按 anm 文件取脚本表(懒加载; 缺资源返回 None)。"""
        if not name:
            return None
        sb = self._sbanks.get(name)
        if sb is None:
            sb = AnmScriptBank(self.bank, name, base)
            self._sbanks[name] = sb
        return sb if sb.ok else None

    def _effects(self) -> EffectLayer:
        """特效层(etama.anm 的特效段, ANM_OFFSET_BULLETS 基址)。"""
        if self._fx is None:
            self._fx = EffectLayer(
                self._sbank("etama.anm", _ANM_OFFSET_BULLETS), self._tcache)
        return self._fx

    # ---- 小工具 ----
    def _blit_center(self, surf: pygame.Surface, img: pygame.Surface,
                     x: float, y: float, alpha: int = 255) -> None:
        if alpha < 255:
            img = self._alpha_img(img, alpha)
        surf.blit(img, (int(x) - img.get_width() // 2,
                        int(y) - img.get_height() // 2))

    # ---- 背景(3D .std 场景优先, 失败退回 2D 平铺竖滚近似) ----
    def _render_bg(self, surf: pygame.Surface, game=None) -> None:
        if self._bg3d is not None and not self._bg3d_broken:
            try:
                wait = 0
                if game is not None:
                    ecl_world = getattr(game, "ecl_world", None)
                    wait = getattr(ecl_world, "script_wait_time", 0) or 0
                # 重负载降载: 动态分辨率到底后仍超预算 → 隔帧渲染(复用上一帧;
                # tick 照常推进, 相机/脚本不停, 只是画面 30fps 刷新)
                if self._bg_period > 1 and self.frame % self._bg_period != 0 \
                        and self._bg_cache is not None:
                    surf.blit(self._bg_cache, (0, 0))
                    return
                t0 = time.perf_counter()
                self._bg3d.tick(wait)
                if self._bg_cache is None:
                    self._bg_cache = pygame.Surface((GAME_W, GAME_H))
                self._bg3d.render_into(self._bg_cache)
                surf.blit(self._bg_cache, (0, 0))
                # EMA 只喂真实渲染帧(跳帧帧成本≈0, 喂了会误判降载成功)
                self._adapt_bg_resolution((time.perf_counter() - t0) * 1000.0)
                return
            except Exception:
                # 渲染期异常: 永久退回 2D 近似, 不中断游戏
                self._bg3d_broken = True
        tile = self._bg_tile
        if tile is None:
            surf.fill((8, 12, 30))
            return
        tw, th = tile.get_size()
        # 横向居中平铺, 竖向缓慢下滚(1px/帧)
        scroll = int(self.frame * 1.0) % th
        for y in range(scroll - th, GAME_H, th):
            for x in range((GAME_W - tw) // 2 % tw - tw, GAME_W, tw):
                surf.blit(tile, (x, y))
        # 压暗保证弹幕可读性(原版 3D 背景本身较暗); 暗化层按关卡缓存
        if self._bg_dark is None:
            self._bg_dark = pygame.Surface((GAME_W, GAME_H), pygame.SRCALPHA)
            self._bg_dark.fill((0, 0, 16, 80))
        surf.blit(self._bg_dark, (0, 0))

    # bg3d 降载(纯视觉取舍, 不动逻辑): EMA 超 8ms 先降内部分辨率
    # (0.35→0.25 封顶), 到底仍超再隔帧渲染; 低于 3.5ms 逐步恢复
    # (先回帧率再升分辨率); 冷却防抖动。
    _BG_EMA_HI = 8.0
    _BG_EMA_LO = 3.5
    _BG_SCALE_MIN = 0.25
    _BG_SCALE_MAX = 0.35

    def _adapt_bg_resolution(self, dt_ms: float) -> None:
        ema = dt_ms if self._bg_ema_ms is None \
            else self._bg_ema_ms * 0.9 + dt_ms * 0.1
        self._bg_ema_ms = ema
        if self._bg_cool > 0:
            self._bg_cool -= 1
            return
        bg = self._bg3d
        assert bg is not None  # 仅从 _render_bg 的 bg3d 成功路径调用
        rs = bg.render_scale
        if ema > self._BG_EMA_HI:
            if rs > self._BG_SCALE_MIN + 1e-9:
                new = max(self._BG_SCALE_MIN, round(rs - 0.05, 2))
                log.debug("bg3d 降内部分辨率 {:.2f} → {:.2f} (EMA {:.1f}ms)",
                          rs, new, ema)
                bg.set_render_scale(new)
                self._bg_cool = 30
            elif self._bg_period < 2:
                log.debug("bg3d 隔帧渲染开启 (EMA {:.1f}ms)", ema)
                self._bg_period = 2
                self._bg_cool = 60
        elif ema < self._BG_EMA_LO:
            if self._bg_period > 1:
                log.debug("bg3d 隔帧渲染关闭 (EMA {:.1f}ms)", ema)
                self._bg_period = 1
                self._bg_cool = 60
            elif rs < self._BG_SCALE_MAX - 1e-9:
                new = min(self._BG_SCALE_MAX, round(rs + 0.05, 2))
                log.debug("bg3d 升内部分辨率 {:.2f} → {:.2f} (EMA {:.1f}ms)",
                          rs, new, ema)
                bg.set_render_scale(new)
                self._bg_cool = 90

    # ---- 敌人(含 boss; ECL SET_ANM → anm 脚本 VM, EclManager.cpp:1196) ----
    def _enemy_death_fx(self, x: float, y: float, death_anm) -> None:
        """EnemyManager.cpp:1017-1020: deathAnm1 爆炸 + deathAnm2+4 爆皮。"""
        fx = self._effects()
        if death_anm[0] >= 0:
            fx.spawn(death_anm[0], x, y, 1)
            fx.spawn(death_anm[1] + 4, x, y, 4)

    def _drain_gone_events(self, game) -> None:
        """ecl_host.gone_events(敌退场) → 击坠爆炸(life<=0 才爆, 超时/离场不爆)。

        事件由 GameEclHost.on_enemy_gone 累积在 ecl_host 上(ecl_host.py:234),
        game.host(EnemyHost) 没有该属性 —— 读错对象会导致:
        1) 击坠爆炸永不触发; 2) _enemy_vis 条目永不回收, id(EnemyState) 复用后
        新敌人继承旧 VM(gid 相同则不重启脚本, 跨关时甚至画上一关 stgNenm 的
        sprite) —— 表现为贴图偶发错误/叠影。
        """
        host = getattr(game, "ecl_host", None) or getattr(game, "host", None)
        events = getattr(host, "gone_events", None)
        if not events:
            return
        for key, x, y, life, death_anm, _is_boss in events:
            vis = self._enemy_vis.pop(key, None)
            if life <= 0 and not (vis and vis["fx_done"]):
                self._enemy_death_fx(x, y, death_anm)
        events.clear()

    def _render_enemies(self, surf: pygame.Surface, game) -> None:
        anm = self._enemy_anm
        sb = self._sbank(anm, _ANM_OFFSET_ENEMY)
        for e in game.host.alive():
            st = e.state
            sid = st.anm_idx
            if sid < 0:
                continue  # 无 sprite 的敌人原版也不画(EnemyManager.cpp:697)
            key = id(st)
            vis = self._enemy_vis.get(key)
            if vis is None:
                vis = {"vm": None, "gid": -1, "subs": {}, "fx_done": False,
                       "intr": 0, "rot_z": 0.0}
                self._enemy_vis[key] = vis
            if st.life > 0:
                vis["fx_done"] = False      # 复活(dt2 切阶段)后允许再爆
            if sb is not None:
                gid = _ANM_OFFSET_ENEMY + sid
                if vis["vm"] is None:
                    vis["vm"] = Vm2d(sb, self._tcache)
                if vis["gid"] != gid:      # SET_ANM 切换 → 换脚本重跑
                    vis["vm"].start(gid)
                    vis["gid"] = gid
                vm = vis["vm"]
                # ECL 直写 VM 的字段(边沿应用; EclManager.cpp:1859/1955/1795)
                if st.primary_vm_interrupt != vis["intr"]:
                    vis["intr"] = st.primary_vm_interrupt
                    if st.primary_vm_interrupt:
                        vm.vm.pending_interrupt = st.primary_vm_interrupt
                if st.primary_vm_rot_z != vis["rot_z"]:
                    vis["rot_z"] = st.primary_vm_rot_z
                    vm.vm.rotation[2] = st.primary_vm_rot_z
                if st.primary_vm_auto_rotate:
                    vm.vm.rotation[2] = st.angle   # EnemyManager.cpp:1194
                vm.execute()
                if e.is_boss:
                    # boss 魔法阵(近似: etama 符文圈慢转, 非 per-stage 原贴图)
                    circle = self.bank.sprite("etama.anm", 45, entry=1)
                    if circle is not None:
                        rot = self.bank.rotated(circle, self.frame * 0.8)
                        self._blit_center(surf, rot, e.pos.x, e.pos.y, alpha=170)
                # 位置 = 敌 pos + vm offset(EnemyManager.cpp:1197)
                dx = e.pos.x + vm.vm.offset[0]
                dy = e.pos.y + vm.vm.offset[1]
                # 原版绘制顺序 (EnemyManager.cpp:1172-1221):
                # vms[0](z=0.3) → primaryVm(z=0.29) → vms[1](z=0.3),
                # 各 VM 用自己的 offset —— 6 面幽幽扇(SET_SUB_ANM(0,153),
                # 512x256 巨扇)靠 slot0 先画保证人在扇前。
                self._draw_sub_anm(surf, vis, sb, 0, st, e.pos)
                vm.draw(surf, dx, dy)
                self._draw_sub_anm(surf, vis, sb, 1, st, e.pos)
            else:
                img = None
                if anm:
                    spr = self.bank.script_sprite(anm, sid)
                    if spr is not None:
                        img = self.bank.sprite(anm, spr)
                if img is None:
                    # 兜底: 未知贴图画原版的"红方块"近似
                    pygame.draw.rect(surf, (250, 40, 220),
                                     (int(e.pos.x) - 12, int(e.pos.y) - 12, 24, 24))
                    continue
                if getattr(st, "mirror", 0):
                    img = self.bank.flipped(img)
                self._blit_center(surf, img, e.pos.x, e.pos.y)
            # deathType 1/2(死亡回调期间仍存活): life 归零当帧即爆
            if st.life <= 0 and not vis["fx_done"]:
                vis["fx_done"] = True
                self._enemy_death_fx(e.pos.x, e.pos.y, st.death_anm)
        self._drain_gone_events(game)

    def _draw_sub_anm(self, surf: pygame.Surface, vis: dict, sb, slot: int,
                      st, pos) -> None:
        """sub anm(SET_SUB_ANM → enemy->vms[slot], EclManager.cpp:1208)。
        位置 = 敌 pos + 该 sub VM 自己的 offset(EnemyManager.cpp:1183)。"""
        slid = st.sub_anm_idx[slot]
        if slid < 0:
            vis["subs"].pop(slot, None)   # C: anmFileIdx=-1 停画
            return
        sgid = _ANM_OFFSET_ENEMY + slid
        sub = vis["subs"].get(slot)
        if sub is None or sub[1] != sgid:
            svm = Vm2d(sb, self._tcache)
            svm.start(sgid)
            sub = (svm, sgid)
            vis["subs"][slot] = sub
        sub[0].execute()
        sub[0].draw(surf, pos.x + sub[0].vm.offset[0],
                    pos.y + sub[0].vm.offset[1])

    # ---- 道具(etama entry1 script 28+itemType → sprite 4..12) ----
    def _item_img(self, t: int) -> pygame.Surface | None:
        """itemType → sprite Surface(纯函数, 缓存; 密集道具雨下逐道具
        script_sprite+sprite 双查是热点)。"""
        img = self._item_imgs.get(t, False)
        if img is False:
            spr = self.bank.script_sprite("etama.anm", 28 + t, entry=1)
            img = self.bank.sprite("etama.anm", spr, entry=1) \
                if spr is not None else None
            self._item_imgs[t] = img
        return img

    def _render_items(self, surf: pygame.Surface, game) -> None:
        for it in game.items.alive():
            t = int(it.type)
            if t > 9:
                continue
            img = self._item_img(t)
            if img is None:
                pygame.draw.rect(surf, (90, 255, 90),
                                 (int(it.pos.x) - 5, int(it.pos.y) - 5, 10, 10))
                continue
            self._blit_center(surf, img, it.pos.x, it.pos.y)

    # ---- 自机弹(.sht anmFileIdx → anm 脚本 VM; 激光型拉伸) ----
    def _render_player_shots(self, surf: pygame.Surface, game) -> None:
        anm = self._player_anm
        sb = self._sbank(anm, _ANM_OFFSET_PLAYER)
        alive_ids = set()
        fly_ids = set()
        for b in game.player.shots:
            img = None
            if b.anm_file_idx:
                spr = self.bank.script_sprite(anm, b.anm_file_idx - _ANM_OFFSET_PLAYER)
                if spr is not None:
                    img = self.bank.sprite(anm, spr)
            if b.bullet_state2 in (4, 5):
                # 激光型持续弹: hitbox 已是从子机/本体到版顶的长条
                # (UPDATE_ORB_LASER/UPDATE_PLAYER_LASER), 拉伸 sprite 近似
                w = max(2, int(b.hitbox[0]))
                h = max(2, int(b.hitbox[1]))
                if img is not None:
                    beam = pygame.transform.scale(img, (w, h))
                    surf.blit(beam, (int(b.pos.x) - w // 2, int(b.pos.y) - h // 2))
                    # playerLaser 拖尾段(pos_history, 伤害见 iter_hits)
                    for i in range(min(b.trail_length, 16)):
                        hp = b.pos_history[i]
                        if hp.x < -900.0:
                            break
                        ghost = beam.copy()
                        ghost.set_alpha(max(30, 120 - i * 8))
                        surf.blit(ghost, (int(hp.x) - w // 2, int(hp.y) - h // 2))
                else:
                    pygame.draw.rect(surf, (180, 240, 255),
                                     (int(b.pos.x) - w // 2, int(b.pos.y) - h // 2, w, h))
                continue
            if b.bullet_state == 2:
                # 命中爆炸(Player.cpp:895: vm 切 anmFileIdx+32 脚本;
                # :896 同时 SpawnParticles(5) 命中火花)
                alive_ids.add(id(b))
                vis = self._shot_vis.get(id(b))
                if vis is None:
                    vis = {"vm": None}
                    if sb is not None and b.anm_file_idx:
                        svm = Vm2d(sb, self._tcache)
                        if svm.start(b.anm_file_idx + 32):
                            vis["vm"] = svm
                    self._shot_vis[id(b)] = vis
                    self._effects().spawn(_FX_HIT_SPARK, b.pos.x, b.pos.y, 1)
                svm = vis["vm"]
                if svm is not None:
                    svm.execute()
                    svm.draw(surf, b.pos.x, b.pos.y)
                else:
                    # 无 +32 脚本时退回光晕近似
                    r = int(max(b.hitbox) / 2)
                    glow = pygame.Surface((r * 2, r * 2), pygame.SRCALPHA)
                    pygame.draw.circle(glow, (255, 240, 200, 110), (r, r), r)
                    surf.blit(glow, (int(b.pos.x) - r, int(b.pos.y) - r))
                continue
            # 飞行弹: 跑 anmFileIdx 脚本(Player.cpp:119 SetAnmIdxAndExecuteScript;
            # 脚本带 SET_ALPHA/SCALE/ANGVEL, 只画首帧会丢半透明和旋转)
            if sb is not None and b.anm_file_idx:
                fly_ids.add(id(b))
                svm = self._shot_fly.get(id(b), False)
                if svm is False:
                    svm = None
                    cand = Vm2d(sb, self._tcache)
                    if cand.start(b.anm_file_idx):
                        svm = cand
                    self._shot_fly[id(b)] = svm
                if svm is not None:
                    svm.execute()
                    # 长条弹(针/导弹)脚本无自转时按速度方向转(同原近似)
                    if svm.vm.angle_vel[2] == 0.0 and svm.surf is not None \
                            and svm.surf.get_height() > svm.surf.get_width() * 1.4 \
                            and b.velocity.length > 0.1:
                        svm.vm.rotation[2] = math.atan2(b.velocity.y,
                                                        b.velocity.x) \
                            + math.pi / 2
                    svm.draw(surf, b.pos.x, b.pos.y)
                    continue
            if img is None:
                pygame.draw.rect(surf, (120, 220, 255),
                                 (int(b.pos.x) - 2, int(b.pos.y) - 6, 4, 12))
                continue
            # 长条弹(针/导弹)按速度方向旋转, 方形弹(札/环)不转
            if img.get_height() > img.get_width() * 1.4 \
                    and b.velocity.length > 0.1:
                ang = -math.degrees(math.atan2(b.velocity.y, b.velocity.x)) - 90
                img = self.bank.rotated(img, ang)
            self._blit_center(surf, img, b.pos.x, b.pos.y)
        for key in list(self._shot_vis):
            if key not in alive_ids:
                del self._shot_vis[key]
        for key in list(self._shot_fly):
            if key not in fly_ids:
                del self._shot_fly[key]

    # ---- 敌弹(全局 sprite idx 反查 etama; 长条弹旋转; 出生态半透明 +
    #      出生特效 anm, g_BulletTypeInfos spawnFast/Normal/SlowIdx) ----
    def _bullet_img(self, sprite: int, sprite_offset: int
                    ) -> pygame.Surface | None:
        """(弹型, sprite_offset) → etama sprite Surface(纯函数, 全帧缓存)。

        替代逐弹的 bullet_active_sprite_idx + global_to_local + sprite
        三连查(profile: 密集帧 ~600 弹 × 3 次调用占弹渲染 ~15%)。
        """
        key = (sprite, sprite_offset)
        img = self._bspr.get(key, False)
        if img is False:
            idx = bullet_active_sprite_idx(sprite, sprite_offset)
            loc = self.bank.global_to_local("etama.anm", idx, _ANM_OFFSET_BULLETS) \
                if idx >= 0 else None
            img = self.bank.sprite("etama.anm", loc[1], entry=loc[0]) \
                if loc is not None else None
            self._bspr[key] = img
        return img

    def _alpha_img(self, img: pygame.Surface, alpha: int) -> pygame.Surface:
        """整体 alpha 调制的副本缓存(出生态弹/boss 魔法阵/激光)。

        alpha 烘焙进像素而非 set_alpha: 表面级 alpha 逼 SDL 走慢路径,
        实测 blit 慢 2.4 倍; 烘焙 = 每像素 alpha*a//255(≤1 LSB 截断差)。
        """
        key = (id(img), alpha)
        hit = self._balpha.get(key)
        if hit is not None and hit[0] is img:    # 持引用防 id 复用串键
            return hit[1]
        out = img.copy()
        if out.get_flags() & pygame.SRCALPHA:
            pa = pygame.surfarray.pixels_alpha(out)
            pa[:] = (pa.astype(np.uint16) * alpha // 255).astype(np.uint8)
            del pa
        else:
            out.set_alpha(alpha)
        self._balpha[key] = (img, out)
        return out

    def _spawn_fx_vm(self, gid: int, frames_left: int) -> Vm2d | None:
        """出生特效 VM(同 gid 同出生帧的弹共享一个实例)。

        共享合法性: 出生脚本(etama 0x212-0x218/0x2aa)无 RAND 指令, 纯
        SET_SPRITE/ALPHA/FADE/SCALE —— 同 (gid, spawn_frames) 的弹脚本
        状态逐帧一致。创建当帧 start(立即执行一次)+execute 一次, 与旧
        逐弹 VM 的推进次数完全相同。
        """
        key = (gid, frames_left)
        svm = self._spawn_fx.get(key, False)
        if svm is False:
            svm = None
            sb = self._sbank("etama.anm", _ANM_OFFSET_BULLETS)
            if sb is not None:
                cand = Vm2d(sb, self._tcache)
                if cand.start(gid):
                    svm = cand
            self._spawn_fx[key] = svm
            if svm is not None:
                svm.execute()          # 与旧路径一致: 创建当帧再推进一次
                self._spawn_fx_done[key] = self.frame
            return svm
        if svm is not None and self._spawn_fx_done.get(key) != self.frame:
            # 每组每帧只 execute 一次(同帧后续同组弹直接复用状态)
            self._spawn_fx_done[key] = self.frame
            svm.execute()
        return svm

    def _render_bullets(self, surf: pygame.Surface, game) -> None:
        bank = self.bank
        rotate_types = _ROTATE_BULLET_TYPES
        rotated = bank.rotated
        blit = surf.blit
        seen_fx: set[tuple[int, int]] = set()
        for b in game.bullets.alive():
            img = self._bullet_img(b.sprite, b.sprite_offset)
            alpha = _SPAWN_ALPHA if b.spawn_state else 255
            if b.spawn_state:
                # 出生特效(BulletManager.cpp:1025-1051: spawn 期间跑特效 VM)
                gids = _SPAWN_FX_GIDS.get(b.sprite)
                fi = _SPAWN_FX_IDX.get(b.spawn_state)
                if gids is not None and fi is not None:
                    key = (gids[fi], b.spawn_frames)
                    seen_fx.add(key)
                    svm = self._spawn_fx_vm(gids[fi], b.spawn_frames)
                    if svm is not None:
                        svm.draw(surf, b.pos.x, b.pos.y)
            if img is None:
                pygame.draw.circle(surf, (235, 235, 90),
                                   (int(b.pos.x), int(b.pos.y)), 4)
                continue
            if b.sprite in rotate_types and b.vel.length > 0.05:
                ang = -math.degrees(math.atan2(b.vel.y, b.vel.x)) - 90
                img = rotated(img, ang)
            if alpha < 255:
                img = self._alpha_img(img, alpha)
            blit(img, (int(b.pos.x) - img.get_width() // 2,
                       int(b.pos.y) - img.get_height() // 2))
        for key in list(self._spawn_fx):
            if key not in seen_fx:
                del self._spawn_fx[key]
                self._spawn_fx_done.pop(key, None)

    # ---- 激光(etama 激光段拉伸旋转 + 内芯; 出现/消散宽度插值) ----
    def _render_lasers(self, surf: pygame.Surface, game) -> None:
        for l in game.lasers.alive():
            length = l.offset_b - l.offset_a
            if length <= 1.0:
                continue
            color = max(0, min(15, int(l.color)))
            strip = self.bank.sprite("etama.anm", _LASER_SPRITE_BASE + color)
            # 视觉宽度: SPAWNING 渐宽 / DESPAWNING 渐窄(原版由 anm 脚本驱动)
            w = l.width
            if l.state == 0:  # SPAWNING
                w = max(1.0, l.width * l.timer / max(1, l.start_time))
            elif l.state == 2:  # DESPAWNING
                w = max(1.0, l.width * (1.0 - l.timer / max(1, l.end_time)))
            dx, dy = math.cos(l.angle), math.sin(l.angle)
            sx = l.pos.x + dx * l.offset_a
            sy = l.pos.y + dy * l.offset_a
            cx = sx + dx * length / 2
            cy = sy + dy * length / 2
            if l.state == 0 and l.timer < l.hitbox_start_time \
                    and not l.hide_warning:
                # 预警细线(原版 SPAWNING 前段的细激光)
                pygame.draw.line(surf, (255, 120, 160),
                                 (int(sx), int(sy)),
                                 (int(sx + dx * length), int(sy + dy * length)), 2)
                continue
            deg = -math.degrees(l.angle) - 90
            if strip is not None:
                # 拉伸+旋转缓存(长度量化 4px, 角度量化 6°)
                qlen = int(length) // 4
                key = (color, int(w * 2), qlen, int(round(deg / 6.0)) * 6 % 360)
                beam = self._beam.get(key)
                if beam is None:
                    beam = pygame.transform.scale(strip, (int(w * 2), qlen * 4))
                    beam = self.bank.rotated(beam, deg)
                    self._beam[key] = beam
                self._blit_center(surf, beam, cx, cy, alpha=190)
                core_key = (color + 16, max(2, int(w * 0.7)), qlen,
                            int(round(deg / 6.0)) * 6 % 360)
                core = self._beam.get(core_key)
                if core is None:
                    core = pygame.transform.scale(
                        strip, (max(2, int(w * 0.7)), qlen * 4))
                    core = self.bank.rotated(core, deg)
                    self._beam[core_key] = core
                self._blit_center(surf, core, cx, cy, alpha=235)
            else:
                pygame.draw.line(surf, (255, 80, 120), (int(sx), int(sy)),
                                 (int(sx + dx * length), int(sy + dy * length)),
                                 max(2, int(w)))

    # ---- bomb 视觉(BombData.cpp 12 套 *Draw + cutin/横幅, 见 bomb_view.py) ----
    def _render_bomb(self, surf: pygame.Surface, game) -> None:
        self._bomb_view.render(surf, game, self._effects())

    # ---- 自机(anm 脚本: 静止慢摇/侧移过渡/子机旋转/focus 环/死亡大爆) ----
    def _render_player(self, surf: pygame.Surface, game) -> None:
        p = game.player
        fx = self._effects()
        dead = int(p.state) == 2
        # 玩家死亡大爆(Player::Die, Player.cpp:1234-1235)
        if dead and not self._prev_player_dead:
            fx.spawn(_FX_PLAYER_DEATH_RING, p.pos.x, p.pos.y, 1,
                     color=0xFF4040FF)      # Player.cpp:1234 的 D3DCOLOR
            fx.spawn(_FX_PLAYER_DEATH_BURST, p.pos.x, p.pos.y, 16)
        self._prev_player_dead = dead
        # focus 判定点环(Player.cpp:1438 SpawnEffect(24); :1462 退场 interrupt)
        if p.focus != self._prev_focus:
            if p.focus:
                h = fx.spawn(_FX_FOCUS_RING, p.pos.x, p.pos.y, 1)
                self._focus_fx = h[0] if h else None
            elif self._focus_fx is not None:
                EffectLayer.interrupt(self._focus_fx, 1)
                self._focus_fx = None
            self._prev_focus = p.focus
        if dead:
            return
        alpha = 255
        # 无敌帧闪烁(任务口径: invulnerability_timer %8<2 变暗)
        if p.invulnerability_timer and p.invulnerability_timer % 8 < 2:
            alpha = 110
        sb = self._sbank(self._player_anm, _ANM_OFFSET_PLAYER)
        if sb is not None:
            # 侧移过渡脚本(Player.cpp:1368-1384, previousHorizontalSpeed 边沿)
            vx = p.velocity.x
            sign = -1 if vx < -0.05 else (1 if vx > 0.05 else 0)
            script = None
            if sign < 0 and self._prev_move_sign >= 0:
                script = _PLAYER_SCRIPT_LEFT
            elif sign == 0 and self._prev_move_sign < 0:
                script = _PLAYER_SCRIPT_LEFT_END
            elif sign > 0 and self._prev_move_sign <= 0:
                script = _PLAYER_SCRIPT_RIGHT
            elif sign == 0 and self._prev_move_sign > 0:
                script = _PLAYER_SCRIPT_RIGHT_END
            self._prev_move_sign = sign
            if self._player_vm is None:
                self._player_vm = Vm2d(sb, self._tcache)
                self._player_vm.start(_ANM_OFFSET_PLAYER + _PLAYER_SCRIPT_IDLE)
                self._player_script = _PLAYER_SCRIPT_IDLE
            if script is not None and script != self._player_script:
                self._player_vm.start(_ANM_OFFSET_PLAYER + script)
                self._player_script = script
            self._player_vm.execute()
            # 子机(Player.cpp:2451-2452: 脚本 1152/1153, ANGVEL 旋转)
            while len(self._option_vms) < 2:
                svm = Vm2d(sb, self._tcache)
                svm.start(_ANM_OFFSET_PLAYER
                          + _PLAYER_OPTION_SCRIPTS[len(self._option_vms)])
                self._option_vms.append(svm)
            if int(p.option_state) != 0:
                for svm, op in zip(self._option_vms, p.options):
                    svm.execute()
                    svm.draw(surf, op.x, op.y, tint_alpha=alpha)
            self._player_vm.draw(surf, p.pos.x, p.pos.y, tint_alpha=alpha)
        else:
            pygame.draw.circle(surf, (255, 255, 255),
                               (int(p.pos.x), int(p.pos.y)), 8)
        if p.focus:
            # focus 判定点中心(红环白点; 旋转环由特效层脚本 0x2c2 画)
            pygame.draw.circle(surf, (255, 60, 60),
                               (int(p.pos.x), int(p.pos.y)), 5, 1)
            pygame.draw.circle(surf, (255, 255, 255),
                               (int(p.pos.x), int(p.pos.y)), 2)

    # ---- boss UI(血条/位置标记/符卡横幅) ----
    def _render_boss_ui(self, surf: pygame.Surface, game) -> None:
        boss = game.boss
        if boss is None:
            return
        # 顶部血条(近似: 原版为 front.anm 贴图拼装, 这里色条+阈值刻度)
        if boss.max_life > 0 and boss.life > 0:
            frac = max(0.0, min(1.0, boss.life / boss.max_life))
            pygame.draw.rect(surf, (60, 20, 30), (2, 2, GAME_W - 4, 4))
            pygame.draw.rect(surf, (255, 60, 80),
                             (2, 2, int((GAME_W - 4) * frac), 4))
            for threshold, _cb in boss.life_thresholds:
                if boss.max_life > 0:
                    tx = 2 + int((GAME_W - 4) * threshold / boss.max_life)
                    pygame.draw.line(surf, (255, 255, 255), (tx, 1), (tx, 7))
        # boss 位置标记(原版在屏幕 y=472 的红三角, 即游戏区底缘)
        if boss.is_active or boss.life > 0:
            mx = int(boss.pos.x)
            pygame.draw.polygon(surf, (255, 50, 60),
                                [(mx - 5, GAME_H - 6), (mx + 5, GAME_H - 6),
                                 (mx, GAME_H - 1)])
        # 符卡剩余秒数(近似; 符卡名横幅已升级为 spellcard_view 的原版构图)
        if boss.is_active == 1 and boss.spellcard_idx >= 0:
            self._fonts()
            remaining = max(0, (boss.spellcard_time_limit - boss.timer) // 60)
            if self._font is not None:
                sec = self._font.render(f"{remaining:02d}", True, (255, 255, 255))
                surf.blit(sec, (GAME_W - sec.get_width() - 6, 32))

    # ---- 一帧 ----
    def render(self, surf: pygame.Surface, game) -> None:
        """把战斗画面画到 384x448 的 surf(游戏区)上。"""
        self.frame += 1
        self.character = getattr(game, "character", self.character)
        self._player_anm = f"player0{self.character // 2}.anm"
        self._ensure_stage(getattr(game, "stage_no", 1))
        self._render_bg(surf, game)
        self._render_items(surf, game)
        self._render_enemies(surf, game)
        self._render_player_shots(surf, game)
        self._render_lasers(surf, game)
        # 特效层(爆炸/命中/focus 环): 推进 + 画在敌弹之下、自机之下
        fx = self._effects()
        fx.update((game.player.pos.x, game.player.pos.y))
        fx.draw(surf)
        self._render_player(surf, game)
        self._render_bullets(surf, game)
        self._render_bomb(surf, game)
        self._render_boss_ui(surf, game)

    # ---- GUI 层: 关卡标题/符卡宣言/bomb cutin (Gui::OnDraw 段) ----
    def render_gui(self, win: pygame.Surface, game) -> None:
        """画在 640x480 窗口层 (原版画全窗口 framebuffer; 游戏区 blit 之后调)。
        脚本坐标即窗口坐标, 长符卡名滑入/出不被游戏区右缘裁切。"""
        # 关卡标题 (Gui.cpp:1702-1704 vms1; MSG_MUSIC 的 BGM 行重触发在内)
        self._stage_title.render(win, game)
        if self._spellcard_view.gui_active:
            self._fonts()
        self._spellcard_view.render(win, game, self._font)
        # bomb cutin 立绘/符卡名横幅: Gui 层最顶 (Gui.cpp:1705-1710)
        if game.bomb.is_in_use or self._bomb_view.gui_active:
            self._fonts()
        self._bomb_view.render_gui(win, self._font)
