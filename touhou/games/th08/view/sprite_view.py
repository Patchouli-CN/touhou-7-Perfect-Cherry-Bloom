"""th08 战斗画面渲染(贴图精灵层) —— 照 th07 sprite_view.py 改编的最小可用版。

与 th07 的结构差异(对照 th08-ref, 行号相对其 src/):
- anm 寻址是扁平序号(C++ 忽略文件里存的 id, AnmManager.cpp:2388-2389/
  2620-2624): 脚本/sprite 按装载序扁平数组下标, 无 th07 的 ANM_OFFSET
  全局空间; 经 AnmScriptBank 的 ANM_FLAT_LAYOUT 分支(engine/view/anm_fx.py)。
- 敌人两个 anm 银行(EnemyManager.cpp:1280/:1297): enemy.anm(通用)与
  stgNenm.anm(关卡, g_StageEnemyAnms Background.cpp:105-106); ECL 的
  SET_ANM/SET_ANM_ALT 选银行(ecl_state.anm_alt_bank, EclRunLow.inl:424-494)。
- 敌人移动 6 脚本(EclDependencies.cpp:448-460 SetPrimaryAnmScripts:
  idle/moveLeft/moveRight/idleFromLeft/idleFromRight/special), 按速度方向
  切换(:804-852; 这里用位置差近似 velocity.x, 镜像标志互换左右)。
- 自机: 咏唱组 focus 换人/妖形态(Player.cpp:671/:735: 脚本 5=妖 0=人),
  单人机体形态由 shotType 奇偶固定(:755-758); player anm 按
  Player.cpp:31-33 的表(单人两两共享)。
- 自机弹: 飞行脚本 = animationIndex+10, 命中 = +11 (Player.cpp:2605/:3337)。
- 敌弹: 弹型的活动脚本 = g_BulletSpriteScripts[type].scripts[0]
  (BulletManager.cpp:299-322), sprite = 脚本首帧 sprite + color 偏移
  (:192/:278 SetSprite(activeSpriteIndex + color))。
- 道具: etama 脚本 61+itemType (ItemManager.cpp:140)。

二期不做(本模块不含): EffectLayer 特效映射表(th08 的 g_EffectTemplates
值驻留二进制数据段, 未转写)、bomb cutin/符卡宣言/关卡标题 GUI、
敌弹出生/消散特效脚本、激光贴图(用色条近似)、使魔链子机渲染差分。
背景: bg3d StageScene.load(game="th08") 直跑(.std 与 th07 同构),
失败退回 stgNbg.anm 主纹理平铺竖滚(同 th07 近似)。
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pygame

from ....logger import logger as log
from ....engine.bullets import SCREEN
from ....engine.view.anm_fx import AnmScriptBank, TransformCache, Vm2d
from ....engine.view.bg3d_view import StageScene
from ....engine.view.sprite_bank import SpriteBank
from ..data import LAST_WORD_ENEMY_ANMS, STAGE_STD_FILES, STAGE_STD_FILES_SPELL
from .anm_vm import AnmVmTh08

GAME_W, GAME_H = int(SCREEN.x), int(SCREEN.y)  # 384x448 游戏区
GAME_X, GAME_Y = 32, 16  # 游戏区在 640x480 窗口的左上
WIN_W, WIN_H = 640, 480

# 自机 anm 文件 (Player.cpp:31-33 g_PlayerAnmFiles): 咏唱组 0-3 各一,
# 单人 4-11 两两共享 (4/5→player00 6/7→player01 8/9→player02 10/11→player03)
_PLAYER_ANM_FILES = tuple(
    f"player0{i}.anm" for i in (0, 1, 2, 3, 0, 0, 1, 1, 2, 2, 3, 3)
)

# 关卡敌人 anm (g_StageEnemyAnms, Background.cpp:105-106) 与背景 anm
# (g_StageAnmFiles, Background.cpp:96-97; 4B 与 4A 共用), 下标 = stage_no-1
_STAGE_ENEMY_ANMS = (
    "stg1enm.anm",
    "stg2enm.anm",
    "stg3enm.anm",
    "stg4aenm.anm",
    "stg4benm.anm",
    "stg5enm.anm",
    "stg6enm.anm",
    "stg7enm.anm",
    "stg8enm.anm",
)
_STAGE_BG_ANMS = (
    "stg1bg.anm",
    "stg2bg.anm",
    "stg3bg.anm",
    "stg4abg.anm",
    "stg4abg.anm",
    "stg5bg.anm",
    "stg6bg.anm",
    "stg7bg.anm",
    "stg8bg.anm",
)

# 敌弹弹型 → etama 活动脚本 (g_BulletSpriteScripts[i].scripts[0],
# BulletManager.cpp:299-322); 出生/消散脚本(scripts[1..4])二期不接
_BULLET_SCRIPTS = (
    0,
    1,
    2,
    3,
    4,
    5,
    6,
    7,
    8,
    9,
    25,
    106,
    107,
    108,
    109,
    110,
    111,
    112,
    113,
    114,
    115,
)

# 道具脚本 = itemType + 61 (ItemManager.cpp:140)
_ITEM_SCRIPT_BASE = 61

# 出生态敌弹本体透明度(spawn 特效 anm 二期不接, 只留压暗)
_SPAWN_ALPHA = 96


def _first_sprite(sb: AnmScriptBank, gid: int) -> int | None:
    """脚本代表帧的扁平 sprite 序号: 首段最后一条 SET_ACTIVE_SPRITE 的参数
    (th08 脚本 op3 参数已是扁平序号, AnmManager.cpp:237)。"""
    ref = sb.ref_global(gid)
    if ref is None:
        return None
    last = None
    for ins in ref.instrs[:64]:
        if ins.opcode in (-1, 1, 2, 4, 5):
            break
        if ins.opcode == 3:
            last = ins.args_i[0]
    return last


class GameView:
    """th08 战斗画面渲染器: render() 把一帧画到 384x448 的游戏区 surface 上。"""

    def __init__(
        self, data_path: str | Path, *, character: int = 0, stage: int = 1
    ) -> None:
        self.bank = SpriteBank(data_path, game="th08")
        self.character = character
        self._player_anm = _PLAYER_ANM_FILES[character % len(_PLAYER_ANM_FILES)]
        self._stage = 0
        self._enemy_anm = ""
        self._bg_tile: pygame.Surface | None = None
        self._bg_dark: pygame.Surface | None = None
        self._bg3d: StageScene | None = None
        self._bg3d_broken = False
        self._beam: dict[tuple, pygame.Surface] = {}
        self.frame = 0
        # anm VM 宿主(anm_fx.py): 脚本表/变换缓存 + 各实体 VM 状态
        self._tcache = TransformCache()
        self._sbanks: dict[str, AnmScriptBank] = {}
        self._enemy_vis: dict[int, dict] = {}  # id(EclEnemyState) → VM 状态
        self._player_vm: Vm2d | None = None
        self._player_script = -1  # 当前自机脚本(扁平 id)
        self._prev_player_dead = False
        self._prev_focus = False
        self._shot_vis: dict[int, Vm2d | None] = {}  # id(自机弹) → 命中 VM
        self._shot_fly: dict[int, Vm2d | None] = {}  # id(自机弹) → 飞行 VM
        self._bspr: dict[tuple[int, int], pygame.Surface | None] = {}
        # (弹型, color) → etama sprite(纯函数, 全局缓存一次)
        self._balpha: dict[tuple, tuple[pygame.Surface, pygame.Surface]] = {}
        self._item_imgs: dict[int, pygame.Surface | None] = {}  # itemType → sprite

    # ---- 关卡资源(换关时重载, 对照 Background.cpp:884 的按关切换) ----
    def _ensure_stage(self, stage_no: int, spell_card: int | None = None) -> None:
        if stage_no == self._stage:
            return
        self._stage = stage_no
        # 换关时宿主整体重建(world._advance_stage), 旧敌人不经 on_enemy_gone,
        # 残留条目会让 id 复用的新敌人继承旧 VM/贴图 —— 必须清掉
        self._enemy_vis.clear()
        idx = min(max(stage_no - 1, 0), len(_STAGE_ENEMY_ANMS) - 1)
        # 符卡练习: Last Word 敌人 anm 按卡表(g_SpellEnemyAnms,
        # Background.cpp:107-111), 其余复用面 anm(EnemyManager.cpp:1293-1311)
        enemy_anm = _STAGE_ENEMY_ANMS[idx]
        if spell_card is not None and spell_card >= 205:
            override = LAST_WORD_ENEMY_ANMS[spell_card - 205]
            if override is not None:
                enemy_anm = override
        self._enemy_anm = enemy_anm
        if not self.bank.has(self._enemy_anm):
            self._enemy_anm = ""
        self._bg_dark = None
        # 3D 背景(.std 场景): 加载失败留 None, _render_bg 退回 2D 平铺;
        # 符卡练习换 _s.std(g_StageStdFilesSpell, Background.cpp:894-906)
        self._bg3d = None
        self._bg3d_broken = False
        std_file = (
            STAGE_STD_FILES_SPELL[idx]
            if spell_card is not None
            else STAGE_STD_FILES[idx]
        )
        try:
            self._bg3d = StageScene.load(
                self.bank._archive(),
                stage_no,
                game="th08",
                vm_cls=AnmVmTh08,
                std_file=std_file,
                bg_anms=(_STAGE_BG_ANMS[idx],),
            )
        except Exception as e:
            log.warning("stage{} 3D 背景加载失败, 回退 2D 平铺: {}", stage_no, e)
            self._bg3d = None
        # 背景: stgNbg.anm 最大 sprite 作平铺块(2D 近似 fallback)
        self._bg_tile = None
        bg_name = _STAGE_BG_ANMS[idx]
        if self.bank.has(bg_name):
            anm = self.bank.anm(bg_name)
            sprites = anm.entries[0].sprites if anm is not None else {}
            if sprites:
                biggest = max(sprites.values(), key=lambda s: s.w * s.h)
                self._bg_tile = self.bank.sprite(bg_name, biggest.id)
        # 本关首用资源预载(进程级缓存, 命中即免费)
        self.bank.has("enemy.anm")
        self.bank.has("etama.anm")
        self.bank.has(self._player_anm)

    # ---- anm VM 宿主 ----
    def _sbank(self, name: str) -> AnmScriptBank | None:
        """按 anm 文件取脚本表(扁平序号空间, base=0; 缺资源返回 None)。"""
        if not name:
            return None
        sb = self._sbanks.get(name)
        if sb is None:
            sb = AnmScriptBank(self.bank, name, 0)
            self._sbanks[name] = sb
        return sb if sb.ok else None

    def _vm2d(self, sb: AnmScriptBank) -> Vm2d:
        return Vm2d(sb, self._tcache, vm_cls=AnmVmTh08)

    # ---- 小工具 ----
    def _blit_center(
        self,
        surf: pygame.Surface,
        img: pygame.Surface,
        x: float,
        y: float,
        alpha: int = 255,
    ) -> None:
        if alpha < 255:
            img = self._alpha_img(img, alpha)
        surf.blit(img, (int(x) - img.get_width() // 2, int(y) - img.get_height() // 2))

    def _alpha_img(self, img: pygame.Surface, alpha: int) -> pygame.Surface:
        """整体 alpha 调制的副本缓存(出生态弹); alpha 烘焙进像素而非
        set_alpha(表面级 alpha 逼 SDL 走慢路径)。"""
        key = (id(img), alpha)
        hit = self._balpha.get(key)
        if hit is not None and hit[0] is img:  # 持引用防 id 复用串键
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

    # ---- 背景(3D .std 场景优先, 失败退回 2D 平铺竖滚近似) ----
    def _render_bg(self, surf: pygame.Surface, game=None) -> None:
        if self._bg3d is not None and not self._bg3d_broken:
            try:
                wait = 0
                if game is not None:
                    ecl_world = getattr(game, "ecl_world", None)
                    wait = getattr(ecl_world, "script_wait_time", 0) or 0
                self._bg3d.tick(wait)
                self._bg3d.render_into(surf)
                return
            except Exception:
                # 渲染期异常: 永久退回 2D 近似, 不中断游戏
                self._bg3d_broken = True
                log.warning("bg3d 渲染异常, 退回 2D 平铺", exc_info=True)
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
        # 压暗保证弹幕可读性(原版 3D 背景本身较暗)
        if self._bg_dark is None:
            self._bg_dark = pygame.Surface((GAME_W, GAME_H), pygame.SRCALPHA)
            self._bg_dark.fill((0, 0, 16, 80))
        surf.blit(self._bg_dark, (0, 0))

    # ---- 敌人(含 boss; ECL SET_ANM → anm 脚本 VM, EclRunLow.inl:424-494) ----
    def _enemy_sb(self, st) -> AnmScriptBank | None:
        """按 flags2 bit2 选敌人 anm 银行 (EclDependencies.cpp:824-826:
        alternateEnemyAnm = stgNenm.anm, 否则 enemy.anm)。"""
        if getattr(st, "anm_alt_bank", 0):
            return self._sbank(self._enemy_anm)
        return self._sbank("enemy.anm")

    def _enemy_script(self, st, vis: dict) -> int:
        """敌人当前应跑的脚本(扁平 id)。

        有移动 6 脚本(move_anm, moveLeft>=0)按速度方向切换
        (EclDependencies.cpp:804-852; 这里用位置差近似 velocity.x,
        mirror 标志互换左右方向); 否则用 SET_ANM 的 anm_idx 直选。
        """
        ma = getattr(st, "move_anm", ())
        if len(ma) >= 6 and ma[1] >= 0:
            x = st.pos.x
            prev_x = vis.get("prev_x", x)
            vis["prev_x"] = x
            dx = x - prev_x
            direction = 0
            if dx < -0.01:
                direction = 1
            elif dx > 0.01:
                direction = 2
            if getattr(st, "mirror", 0):  # MIRROR_MOVEMENT_X: 左右互换 (:814-820)
                if direction:
                    direction = 3 - direction
            prev_dir = vis.get("dir", 0xFF)
            if direction != prev_dir:
                vis["dir"] = direction
                if direction == 1:
                    return ma[1]
                if direction == 2:
                    return ma[2]
                # 回正: 初登场 idleInitial, 否则按来向 idleFromLeft/Right (:830-839)
                if prev_dir == 0xFF:
                    return ma[0]
                return ma[3] if prev_dir == 1 else ma[4]
            return vis.get("script", ma[0])
        return st.anm_idx

    def _render_enemies(self, surf: pygame.Surface, game) -> None:
        for e in game.host.alive():
            st = e.state
            key = id(st)
            vis = self._enemy_vis.get(key)
            if vis is None:
                vis = {
                    "vm": None,
                    "gid": -1,
                    "subs": {},
                    "dir": 0xFF,
                    "intr": 0,
                    "rot_z": 0.0,
                    "script": -1,
                }
                self._enemy_vis[key] = vis
            sb = self._enemy_sb(st)
            gid = self._enemy_script(st, vis)
            vis["script"] = gid
            if gid < 0 or getattr(st, "no_sprite", 0):
                continue  # 无 sprite 的敌人不画 (op79-81 NO_SPRITE)
            if sb is None:
                continue
            if vis["vm"] is None or getattr(vis["vm"], "sbank") is not sb:
                vis["vm"] = self._vm2d(sb)
                vis["gid"] = -1
            if vis["gid"] != gid:  # SET_ANM 切换/方向切换 → 换脚本重跑
                vis["vm"].start(gid)
                vis["gid"] = gid
            vm = vis["vm"]
            # ECL 直写 VM 的字段(边沿应用)
            if st.primary_vm_interrupt != vis["intr"]:
                vis["intr"] = st.primary_vm_interrupt
                if st.primary_vm_interrupt:
                    vm.vm.pending_interrupt = st.primary_vm_interrupt
            if st.primary_vm_rot_z != vis["rot_z"]:
                vis["rot_z"] = st.primary_vm_rot_z
                vm.vm.rotation[2] = st.primary_vm_rot_z
            if st.primary_vm_auto_rotate:
                vm.vm.rotation[2] = st.angle
            vm.execute()
            # 位置 = 敌 pos + pos_offset(使魔链接位置, EclRun.cpp:54-56)
            # + vm offset
            po = getattr(st, "pos_offset", None)
            px = e.pos.x + (po.x if po is not None else 0.0)
            py = e.pos.y + (po.y if po is not None else 0.0)
            dx = px + vm.vm.offset[0]
            dy = py + vm.vm.offset[1]
            self._draw_sub_anm(surf, vis, sb, 0, st, px, py)
            vm.draw(surf, dx, dy)
            self._draw_sub_anm(surf, vis, sb, 1, st, px, py)
        self._drain_gone_events(game)

    def _draw_sub_anm(
        self, surf: pygame.Surface, vis: dict, sb, slot: int, st, px: float, py: float
    ) -> None:
        """sub anm(op57/61 SET_EXTRA_ANM → enemy->vms[slot], EclRunLow.inl
        :448-494 段; anmFileIdx=-1 停画)。"""
        slid = st.sub_anm_idx[slot]
        if slid < 0:
            vis["subs"].pop(slot, None)
            return
        sub = vis["subs"].get(slot)
        if sub is None or sub[1] != slid or sub[0].sbank is not sb:
            svm = self._vm2d(sb)
            svm.start(slid)
            sub = (svm, slid)
            vis["subs"][slot] = sub
        sub[0].execute()
        sub[0].draw(surf, px + sub[0].vm.offset[0], py + sub[0].vm.offset[1])

    def _drain_gone_events(self, game) -> None:
        """ecl_host.gone_events(敌退场) → 回收 _enemy_vis 条目(不回收会让
        id(EnemyState) 复用的新敌人继承旧 VM/贴图)。击坠爆炸特效二期
        (EffectLayer 的 th08 映射表), 这里只回收。"""
        host = getattr(game, "ecl_host", None) or getattr(game, "host", None)
        events = getattr(host, "gone_events", None)
        if not events:
            return
        for key, _x, _y, _life, _death_anm, _is_boss in events:
            self._enemy_vis.pop(key, None)
        events.clear()

    # ---- 道具(etama 脚本 61+itemType, ItemManager.cpp:140) ----
    def _item_img(self, t: int) -> pygame.Surface | None:
        img = self._item_imgs.get(t, False)
        if img is False:
            img = None
            sb = self._sbank("etama.anm")
            if sb is not None:
                spr = _first_sprite(sb, _ITEM_SCRIPT_BASE + t)
                if spr is not None:
                    img = sb.sprite_surf(spr)
            self._item_imgs[t] = img
        return img

    def _render_items(self, surf: pygame.Surface, game) -> None:
        for it in game.items.alive():
            t = int(it.type)
            if t > 8:
                continue  # TIME_APEX_REQUEST 等内部型不画(生成即转 TIME)
            img = self._item_img(t)
            if img is None:
                pygame.draw.rect(
                    surf, (90, 255, 90), (int(it.pos.x) - 5, int(it.pos.y) - 5, 10, 10)
                )
                continue
            self._blit_center(surf, img, it.pos.x, it.pos.y)

    # ---- 自机弹(脚本 = animationIndex+10 飞行 / +11 命中, Player.cpp:2605/:3337) ----
    def _render_player_shots(self, surf: pygame.Surface, game) -> None:
        sb = self._sbank(self._player_anm)
        alive_ids = set()
        fly_ids = set()
        for b in game.player.shots:
            if not b.anm_file_idx:
                pygame.draw.rect(
                    surf, (120, 220, 255), (int(b.pos.x) - 2, int(b.pos.y) - 6, 4, 12)
                )
                continue
            if b.bullet_state2 in (4, 5):
                # 激光型持续弹: hitbox 已是从子机/本体到版顶的长条, 拉伸近似
                w = max(2, int(b.hitbox[0]))
                h = max(2, int(b.hitbox[1]))
                pygame.draw.rect(
                    surf,
                    (180, 240, 255),
                    (int(b.pos.x) - w // 2, int(b.pos.y) - h // 2, w, h),
                )
                continue
            if sb is None:
                continue
            if b.bullet_state == 2:
                # 命中 (Player.cpp:3337: vm 切 animationIndex+11 脚本, 保 rotation.z)
                alive_ids.add(id(b))
                svm = self._shot_vis.get(id(b), False)
                if svm is False:
                    svm = None
                    cand = self._vm2d(sb)
                    if cand.start(b.anm_file_idx + 11):
                        svm = cand
                    self._shot_vis[id(b)] = svm
                if svm is not None:
                    svm.execute()
                    svm.draw(surf, b.pos.x, b.pos.y)
                continue
            # 飞行弹: 跑 anmFileIdx+10 脚本 (Player.cpp:2605)
            fly_ids.add(id(b))
            svm = self._shot_fly.get(id(b), False)
            if svm is False:
                svm = None
                cand = self._vm2d(sb)
                if cand.start(b.anm_file_idx + 10):
                    svm = cand
                self._shot_fly[id(b)] = svm
            if svm is not None:
                svm.execute()
                # 长条弹脚本无自转时按速度方向转
                if (
                    svm.vm.angle_vel[2] == 0.0
                    and svm.surf is not None
                    and svm.surf.get_height() > svm.surf.get_width() * 1.4
                    and b.velocity.length > 0.1
                ):
                    svm.vm.rotation[2] = (
                        math.atan2(b.velocity.y, b.velocity.x) + math.pi / 2
                    )
                svm.draw(surf, b.pos.x, b.pos.y)
        for key in list(self._shot_vis):
            if key not in alive_ids:
                del self._shot_vis[key]
        for key in list(self._shot_fly):
            if key not in fly_ids:
                del self._shot_fly[key]

    # ---- 敌弹(弹型活动脚本首帧 sprite + color 偏移; 长条弹按速度方向旋转) ----
    def _bullet_img(self, sprite: int, color: int) -> pygame.Surface | None:
        key = (sprite, color)
        img = self._bspr.get(key, False)
        if img is False:
            img = None
            sb = self._sbank("etama.anm")
            if sb is not None and 0 <= sprite < len(_BULLET_SCRIPTS):
                first = _first_sprite(sb, _BULLET_SCRIPTS[sprite])
                if first is not None:
                    img = sb.sprite_surf(first + color)
            self._bspr[key] = img
        return img

    def _render_bullets(self, surf: pygame.Surface, game) -> None:
        bank = self.bank
        blit = surf.blit
        for b in game.bullets.alive():
            img = self._bullet_img(b.sprite, b.sprite_offset)
            alpha = _SPAWN_ALPHA if b.spawn_state else 255
            if img is None:
                pygame.draw.circle(
                    surf, (235, 235, 90), (int(b.pos.x), int(b.pos.y)), 4
                )
                continue
            if img.get_height() > img.get_width() * 1.4 and b.vel.length > 0.05:
                ang = -math.degrees(math.atan2(b.vel.y, b.vel.x)) - 90
                img = bank.rotated(img, ang)
            if alpha < 255:
                img = self._alpha_img(img, alpha)
            blit(
                img,
                (
                    int(b.pos.x) - img.get_width() // 2,
                    int(b.pos.y) - img.get_height() // 2,
                ),
            )

    # ---- 激光(色条近似; 贴图 VM 二期) ----
    def _render_lasers(self, surf: pygame.Surface, game) -> None:
        for lz in game.lasers.alive():
            length = lz.offset_b - lz.offset_a
            if length <= 1.0:
                continue
            w = lz.width
            if lz.state == 0:  # SPAWNING
                w = max(1.0, lz.width * lz.timer / max(1, lz.start_time))
            elif lz.state == 2:  # DESPAWNING
                w = max(1.0, lz.width * (1.0 - lz.timer / max(1, lz.end_time)))
            dx, dy = math.cos(lz.angle), math.sin(lz.angle)
            sx = lz.pos.x + dx * lz.offset_a
            sy = lz.pos.y + dy * lz.offset_a
            if (
                lz.state == 0
                and lz.timer < lz.hitbox_start_time
                and not lz.hide_warning
            ):
                # 预警细线
                pygame.draw.line(
                    surf,
                    (255, 120, 160),
                    (int(sx), int(sy)),
                    (int(sx + dx * length), int(sy + dy * length)),
                    2,
                )
                continue
            pygame.draw.line(
                surf,
                (255, 80, 120),
                (int(sx), int(sy)),
                (int(sx + dx * length), int(sy + dy * length)),
                max(2, int(w)),
            )
            pygame.draw.line(
                surf,
                (255, 200, 220),
                (int(sx), int(sy)),
                (int(sx + dx * length), int(sy + dy * length)),
                max(1, int(w * 0.4)),
            )

    # ---- 自机(脚本 0=人/5=妖, Player.cpp:671/:735; 单人按 shotType 奇偶) ----
    def _render_player(self, surf: pygame.Surface, game) -> None:
        p = game.player
        if int(p.state) == 2:
            return  # 死亡不画(死亡特效二期)
        alpha = 255
        # 无敌帧闪烁
        if p.invulnerability_timer and p.invulnerability_timer % 8 < 2:
            alpha = 110
        sb = self._sbank(self._player_anm)
        if sb is None:
            pygame.draw.circle(surf, (255, 255, 255), (int(p.pos.x), int(p.pos.y)), 8)
        else:
            # 咏唱组: focus 切形态脚本; 单人: 形态固定(is_youkai 恒定),
            # 脚本 0/5 同一口径(Player.cpp:2605 段的 script 5 是妖形态)
            script = 5 if getattr(p, "is_youkai", False) else 0
            if self._player_vm is None:
                self._player_vm = self._vm2d(sb)
                self._player_vm.start(script)
                self._player_script = script
            if script != self._player_script:
                self._player_vm.start(script)
                self._player_script = script
            self._player_vm.execute()
            self._player_vm.draw(surf, p.pos.x, p.pos.y, tint_alpha=alpha)
        if p.focus:
            # focus 判定点中心(判定点环特效二期)
            pygame.draw.circle(surf, (255, 60, 60), (int(p.pos.x), int(p.pos.y)), 5, 1)
            pygame.draw.circle(surf, (255, 255, 255), (int(p.pos.x), int(p.pos.y)), 2)

    # ---- boss UI(血条/位置标记; 符卡名条二期) ----
    def _render_boss_ui(self, surf: pygame.Surface, game) -> None:
        boss = game.boss
        if boss is None:
            return
        # 顶部血条 (Gui.cpp:1413-1420: rect (64,19)-(64+320*frac,23),
        # 白→暗蓝渐变; 这里画到游戏区坐标 = 窗口坐标 - (32,16))
        if boss.max_life > 0 and boss.life > 0:
            frac = max(0.0, min(1.0, boss.life / boss.max_life))
            x0, y0 = 64 - GAME_X, 19 - GAME_Y
            w = int(320 * frac)
            pygame.draw.rect(surf, (32, 32, 96), (x0, y0, 320, 4))
            pygame.draw.rect(surf, (255, 255, 255), (x0, y0, w, 4))
        # boss 位置标记(游戏区底缘红三角)
        if boss.is_active or boss.life > 0:
            mx = int(boss.pos.x)
            pygame.draw.polygon(
                surf,
                (255, 50, 60),
                [(mx - 5, GAME_H - 6), (mx + 5, GAME_H - 6), (mx, GAME_H - 1)],
            )

    # ---- 一帧 ----
    def render(self, surf: pygame.Surface, game) -> None:
        """把战斗画面画到 384x448 的 surf(游戏区)上。"""
        self.frame += 1
        self.character = getattr(game, "character", self.character)
        self._player_anm = _PLAYER_ANM_FILES[self.character % len(_PLAYER_ANM_FILES)]
        self._ensure_stage(
            getattr(game, "stage_no", 1), getattr(game, "spell_practice_card", None)
        )
        self._render_bg(surf, game)
        self._render_items(surf, game)
        self._render_enemies(surf, game)
        self._render_player_shots(surf, game)
        self._render_lasers(surf, game)
        self._render_player(surf, game)
        self._render_bullets(surf, game)
        # bomb 视觉/符卡背景二期; bomb 进行中给个白闪提示(world 逻辑照常)
        if getattr(game.bomb, "is_in_use", False):
            flash = pygame.Surface((GAME_W, GAME_H), pygame.SRCALPHA)
            flash.fill((255, 255, 255, 40))
            surf.blit(flash, (0, 0))
        self._render_boss_ui(surf, game)
