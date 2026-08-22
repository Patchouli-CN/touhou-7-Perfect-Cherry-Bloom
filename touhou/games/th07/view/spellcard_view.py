"""boss 符卡宣言 —— Gui::ShowSpellcard (Gui.cpp:361-397) + OnUpdate/OnDraw 段。

复用 bomb_view 的 cutin/横幅方案 (anm 脚本 VM 驱动, 文字走字体渲染):

- 触发: ECL BEGIN_SPELLCARD → EclManager::BeginSpellcard (EclManager.cpp:672
  g_Gui.ShowSpellcard, 音效 se_cat00 由逻辑层 impl 播); 退场:
  Gui::EndEnemySpellcard (Gui.cpp:55-60): 符卡名 interrupt 1, 底条/捕获分
  指示 interrupt 2, 各自脚本滑出/淡出收尾。
- 立绘: face 链脚本 1187 (ANM_OFFSET_FACE 空间): pos (272,-144)→(272,112)
  180 帧线性下滑, alpha 0→224 (60f), t=90 起 60f 淡出, t=150 EXIT_HIDE2;
  脚本带 ANM_22 anchor=3 → pos 是 quad 左上角 (AnmManager.cpp:1019-1046
  DrawNoRotation), 126x510 sprite 终占窗口 x[272,398] y[112,622]
  (贴游戏区右缘, 下缘出屏); sprite 换成 face_{stage:02d}_00.anm 的
  1197+spellcard_face (Gui.cpp:365-368, gui_id 即 ECL BEGIN_SPELLCARD
  arg0 原样透传, 4 面三姐妹 0/3/6/9); offset.x 按 sprite 宽分档
  -288/-112/0 (Gui.cpp:369-382), 绘制时 pos+offset (Gui.cpp:1714-1718)。
  替换失败只告警不静默 (立绘回落脚本默认 = 自机脸, 必属异常)。
- 左右装饰: 脚本 1189/1191 + sprite 1196 (Gui.cpp:385-388, 与 bomb 装饰
  同件); related1 DrawNoRotation / related2 Draw (Gui.cpp:1719-1720)。
- 符卡名: text.anm script 1797 (局部 5; 纹理外链只取运动, Gui.cpp:389-391
  DrawStringFormat 0xfff0f0 右对齐)。脚本自带全程: 3 倍放大缩回 (30 帧)
  → t=100 滑到窗口 (256,40) (游戏区右上) 常驻 → interrupt 1 滑出右缘。
  文字右缘 = pos.x + sprite 宽(320)/2 × scale (AnmManager.cpp:2357-2361
  右对齐 + 中心锚点 quad)。
- 底条 ascii.anm script 0 (sprite 129, 256x32), 每帧 bg.pos=name.pos
  (Gui.cpp:1730-1731); 捕获分指示 ascii script 2 (sprite 131) 画在自身
  脚本位 (Gui.cpp:1733)。捕获分数字 captureBonusVm (ascii script 3 载体,
  sprite 132+ 逐位): 8 位剩余捕获分 (captureScore+grazeBonusScore, 非捕获
  中=0, 前导零省略个位常显, Gui.cpp:1734-1758) + 历史两段 (catk 本机
  successes/attempts, 99 封顶, 十位 0 省略, Gui.cpp:1761-1795)。
- 绘制层: 本模块画在 640x480 窗口层 (Gui::OnDraw 原本就画全窗口
  framebuffer; 游戏区仅是其 (32,16) 子区域), 坐标直接用脚本窗口坐标,
  长符卡名滑入/滑出 (script 1797 退场到窗口 x=576) 不被游戏区右缘裁切。
"""
from __future__ import annotations

import pygame

from ....logger import logger as log
from ....schema.anm import parse_scripts
from ....engine.view.anm_fx import AnmScriptBank, TransformCache, Vm2d
from ....engine.view.anm_vm import AnmVm, ScriptRef, reset_and_run

_ANM_OFFSET_FACE = 0x4A0
_ANM_OFFSET_FACE_STAGE = 0x4AD

# ShowSpellcard 调用点实参 (Gui.cpp:365-394)
_SCR_PORTRAIT = 1187          # 立绘脚本 (face 链局部 3)
_SCR_REL1, _SCR_REL2 = 1189, 1191   # 左右装饰脚本
_SPR_DECOR = 1196             # 装饰 sprite (局部 12)
_SCR_NAME_BG = 0              # ascii.anm 符卡名底条 (Gui.cpp:659)
_SCR_INDICATOR = 2            # ascii.anm 捕获分指示 (Gui.cpp:660)
_SCR_NAME_TEXT = 5            # text.anm 局部 5 = 全局 1797 (Gui.cpp:389)
_NAME_SPRITE_W = 320          # text.anm sprite 5 宽 (右对齐基准)

_FACE_ANM = ("face_rm00.anm", "face_mr00.anm", "face_sk00.anm")

# 本模块画在 640x480 窗口层 (脚本坐标即窗口坐标, 不换算; 见模块 docstring)

_NAME_COLOR = (255, 240, 240)   # DrawStringFormat 0xfff0f0 (Gui.cpp:390)


def _load_text_scripts(bank) -> dict[int, list]:
    """text.anm 脚本表 (纹理外链, AnmFile.parse 不支持, 只取脚本数据)。"""
    scripts: dict[int, list] = {}
    try:
        arc = bank._archive()
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
    return scripts


class SpellcardView:
    """boss 符卡宣言: begin/end 边沿管理 + cutin/横幅/右上常驻的 VM 宿主。

    render() 每帧由 GameView 调用; 读 game.boss 的 spellcard_idx 边沿
    (begin: >=0 出现; end: <0 或 boss 撤掉 —— 捕获/超时/玩家死亡后逻辑层
    都会把 spellcard_idx 归 -1, boss.py:end_spellcard)。
    """

    def __init__(self, bank, tcache: TransformCache) -> None:
        self.bank = bank
        self.tcache = tcache
        self._sbanks: dict[tuple[str, int], AnmScriptBank] = {}
        self._text_scripts: dict[int, list] | None = None
        # ---- 运行期状态 (每次宣言重建) ----
        self._key: tuple[int, int] | None = None   # (id(boss), spellcard_idx)
        self._cutin: list[tuple[Vm2d, bool]] = []  # (vm, no_rotation)
        self._name: AnmVm | None = None            # text.anm 运动 VM (无贴图)
        self._name_bg: Vm2d | None = None
        self._indicator: Vm2d | None = None
        self._name_text = ""
        self._game = None                  # 捕获分数字数据源 (render 每帧更新)
        self._ascii_sb: AnmScriptBank | None = None  # 数字 sprite 132+ 取图
        self._sc_idx = -1                  # 宣言时的 catk 全局符卡号
        self._bonus_remaining = 0          # 剩余捕获分最后值 (boss 撤掉后沿用)
        # ---- 测试断言用: 本帧宣言绘制调用数 ----
        self.gui_draws = 0

    @property
    def gui_active(self) -> bool:
        """宣言 VM 仍在活动 (GameView 据此决定是否加载字体)。"""
        return (bool(self._cutin) or self._name is not None
                or self._name_bg is not None or self._indicator is not None)

    def _sbank(self, name: str, base: int) -> AnmScriptBank | None:
        key = (name, base)
        sb = self._sbanks.get(key)
        if sb is None:
            sb = AnmScriptBank(self.bank, name, base)
            self._sbanks[key] = sb
        return sb if sb.ok else None

    def _text_script(self, sid: int) -> list | None:
        if self._text_scripts is None:
            self._text_scripts = _load_text_scripts(self.bank)
        return self._text_scripts.get(sid)

    # ==================================================================
    # 主入口
    # ==================================================================
    def render(self, surf: pygame.Surface, game, font) -> None:
        """边沿管理 + 推进/绘制 (Gui::OnUpdate :1278-1284 + OnDraw :1712-1733)。
        surf 为 640x480 窗口面 (脚本窗口坐标直接绘制)。"""
        self.gui_draws = 0
        boss = getattr(game, "boss", None)
        cur = None
        if boss is not None and boss.spellcard_idx >= 0:
            cur = (id(boss), boss.spellcard_idx)
        if cur != self._key:
            if cur is None:
                self._end()
            else:
                self._start(game, boss)
            self._key = cur
        self._game = game     # 捕获分数字取 boss/store 数据 (Gui.cpp:1736-1739)
        if not self.gui_active:
            return
        self._tick(surf, font)

    # ---- 触发/结束 ----
    def _start(self, game, boss) -> None:
        """Gui::ShowSpellcard (Gui.cpp:361-397)。"""
        stage = getattr(game, "stage_no", 1)
        char = getattr(game, "character", 0)
        gui_id = getattr(boss, "spellcard_face", 0)
        self._sc_idx = boss.spellcard_idx   # catk 下标 (EndSpellcard 不清, 见
        # EclManager.cpp:837 只清 isActive; 滑出期间历史仍按本卡显示)
        self._cutin = []
        self._name = None
        self._name_bg = None
        self._indicator = None
        self._name_text = boss.name
        face = self._sbank(_FACE_ANM[char // 2], _ANM_OFFSET_FACE)
        face_st = self._sbank(f"face_{stage:02d}_00.anm", _ANM_OFFSET_FACE_STAGE)
        if face is None:
            log.warning("符卡宣言 face 链 anm 缺失: {}, cutin 整体跳过",
                        _FACE_ANM[char // 2])
        else:
            # 立绘 (Gui.cpp:363-383): sprite<0 时无立绘 (如非 boss 符卡)
            portrait = Vm2d(face, self.tcache)
            if gui_id >= 0:
                if not portrait.start(_SCR_PORTRAIT):
                    log.warning("符卡宣言立绘脚本 {} 在 {} 缺失, 跳过立绘",
                                _SCR_PORTRAIT, face.name)
                else:
                    gid = _ANM_OFFSET_FACE_STAGE + gui_id
                    if face_st is None:
                        log.warning(
                            "符卡宣言立绘 face_{:02d}_00.anm 加载失败, "
                            "sprite {} 替换跳过 (回落脚本默认=自机脸)",
                            stage, gid)
                    else:
                        pic = face_st.sprite_surf(gui_id)   # 全局 1197+gui_id
                        if pic is None:
                            log.warning(
                                "符卡宣言立绘 sprite {} (={}+{}) 在 {} 不存在, "
                                "回落脚本默认 sprite (自机脸)",
                                gid, _ANM_OFFSET_FACE_STAGE, gui_id,
                                face_st.name)
                        else:
                            portrait.surf = pic
                            portrait.vm.active_sprite_idx = gid
                    # offset.x 按 sprite 宽分档 (Gui.cpp:369-382)
                    w = portrait.surf.get_width() \
                        if portrait.surf is not None else 0
                    portrait.vm.offset[0] = \
                        -288.0 if w > 256 else (-112.0 if w > 128 else 0.0)
                    self._cutin.append((portrait, True))     # DrawNoRotation
            for gid, no_rot in ((_SCR_REL1, True), (_SCR_REL2, False)):
                vm = Vm2d(face, self.tcache)
                if vm.start(gid):
                    vm.set_sprite(_SPR_DECOR - _ANM_OFFSET_FACE)
                    self._cutin.append((vm, no_rot))
                else:
                    log.warning("符卡宣言装饰脚本 {} 在 {} 缺失", gid, face.name)
        ascii_sb = self._sbank("ascii.anm", 0)
        self._ascii_sb = ascii_sb
        if ascii_sb is not None:
            bg = Vm2d(ascii_sb, self.tcache)
            if bg.start(_SCR_NAME_BG):
                # 脚本开头等 interrupt 1 才入场 (Gui.cpp:394); 结束 interrupt 2
                bg.vm.pending_interrupt = 1
                self._name_bg = bg
            ind = Vm2d(ascii_sb, self.tcache)
            if ind.start(_SCR_INDICATOR):
                ind.vm.pending_interrupt = 1            # Gui.cpp:395
                self._indicator = ind
        instrs = self._text_script(_SCR_NAME_TEXT)
        if instrs:
            vm = AnmVm()
            reset_and_run(vm, ScriptRef(instrs, 0), lambda key: None)
            self._name = vm

    def _end(self) -> None:
        """Gui::EndEnemySpellcard (Gui.cpp:55-60); cutin VM 由自身脚本收尾。"""
        if self._name is not None:
            self._name.pending_interrupt = 1
        if self._name_bg is not None:
            self._name_bg.vm.pending_interrupt = 2
        if self._indicator is not None:
            self._indicator.vm.pending_interrupt = 2

    # ---- 每帧推进/绘制 ----
    def _draw(self, vm: Vm2d, surf: pygame.Surface, x: float, y: float,
              *, no_rotation: bool = False) -> None:
        if not vm.alive:
            return
        # ANM_22 anchor=3 (立绘脚本 1187/装饰脚本 1189 带): pos 是 quad 左上
        # 而非中心 (AnmManager.cpp:1019-1046 DrawNoRotation / :1117-1128 Draw;
        # 两者都是 anchor 位把中心锚的 (x,y) 平移半个缩放尺寸)。Vm2d.draw 是
        # 中心锚, 这里把锚点平移成中心等价坐标 (本模块这些 VM 旋转均为 0)。
        if vm.vm.anchor and vm.surf is not None:
            if vm.vm.anchor & 1:
                x += vm.surf.get_width() * abs(vm.vm.scale[0]) / 2.0
            if vm.vm.anchor & 2:
                y += vm.surf.get_height() * abs(vm.vm.scale[1]) / 2.0
        if no_rotation:
            saved = vm.vm.rotation[2]
            vm.vm.rotation[2] = 0.0
            vm.draw(surf, x, y)
            vm.vm.rotation[2] = saved
        else:
            vm.draw(surf, x, y)
        self.gui_draws += 1

    def _tick(self, surf: pygame.Surface, font) -> None:
        """窗口坐标直接绘制 (Gui::OnDraw 画全窗口 framebuffer)。"""
        alive = []
        for vm, no_rot in self._cutin:
            vm.execute()
            if vm.alive:
                alive.append((vm, no_rot))
            x = vm.vm.pos[0] + vm.vm.offset[0]
            y = vm.vm.pos[1] + vm.vm.offset[1]
            self._draw(vm, surf, x, y, no_rotation=no_rot)
        self._cutin = alive
        bg = self._name_bg
        if bg is not None:
            bg.execute()
            if not bg.alive:
                self._name_bg = None
                bg = None
        ind = self._indicator
        if ind is not None:
            ind.execute()
            if not ind.alive:
                self._indicator = None
                ind = None
        name = self._name
        if name is None:
            return
        name.execute()
        if name.pc < 0:
            self._name = None
            return
        if not name.visible:
            return
        nx = name.pos[0] + name.offset[0]
        ny = name.pos[1] + name.offset[1]
        if bg is not None:
            # bg.pos = name.pos, DrawNoRotation (Gui.cpp:1730-1731)
            self._draw(bg, surf, nx, ny, no_rotation=True)
        # DrawStringFormat (Gui.cpp:390-391): 字形纹理外链, 用字体渲染;
        # 右对齐: 文字右缘 = pos.x + sprite 宽/2 × scale (中心锚点 quad)
        if font is not None and self._name_text:
            img = font.render(self._name_text, True, _NAME_COLOR)
            sx, sy = name.scale[0], name.scale[1]
            if sx != 1.0 or sy != 1.0:
                img = self.tcache.get(img, sx, sy, 0.0)
            alpha = name.color[3]
            if alpha < 255:
                img = img.copy()
                img.set_alpha(alpha)
            right = nx + _NAME_SPRITE_W * sx / 2
            surf.blit(img, img.get_rect(midright=(int(right), int(ny))))
            self.gui_draws += 1
        if ind is not None:
            # DrawNoRotation(spellcardBonusIndicator) (Gui.cpp:1733), 自身脚本位
            self._draw(ind, surf, ind.vm.pos[0], ind.vm.pos[1],
                       no_rotation=True)
            self._draw_capture_bonus(surf, ind)

    # ---- 捕获分递减数字 + 历史 (Gui.cpp:1734-1795, captureBonusVm) ----
    def _digit(self, d: int) -> pygame.Surface | None:
        sb = self._ascii_sb
        return sb.sprite_surf(132 + d) if sb is not None else None

    def _blit_digit(self, surf: pygame.Surface, d: int, x: float,
                    y: float) -> None:
        img = self._digit(d)
        if img is not None:
            surf.blit(img, (int(x) - img.get_width() // 2,
                            int(y) - img.get_height() // 2))
            self.gui_draws += 1

    def _draw_capture_bonus(self, surf: pygame.Surface, ind: Vm2d) -> None:
        """剩余捕获分 8 位 + 历史 successes/attempts 各 2 位。

        captureBonusVm 是 ascii script 3 的载体 VM (中心锚, 无旋转);
        每帧 pos = indicator.pos, x-40 起逐位步进 7 (Gui.cpp:1743-1758)。
        """
        game = self._game
        boss = getattr(game, "boss", None) if game is not None else None
        if boss is not None:
            # EndSpellcard 只清 isActive/spellcard_idx, isCapturing/captureScore
            # 保留 (EclManager.cpp:837) → 滑出期间定格显示最终分; boss 对象
            # 撤掉后沿用最后值 (C++ 的 spellcardInfo 是独立常驻槽)。
            if getattr(boss, "is_capturing", False):
                self._bonus_remaining = (boss.capture_score
                                         + boss.graze_bonus_score)
            else:
                self._bonus_remaining = 0
        remaining = self._bonus_remaining
        x = ind.vm.pos[0] - 40.0
        y = ind.vm.pos[1]
        divisor = 10000000
        leading = False
        for _ in range(8):
            d, remaining = divmod(remaining, divisor)
            if d:
                leading = True
            if leading or divisor == 1:
                self._blit_digit(surf, d, x, y)
            x += 7.0
            divisor //= 10
        # 历史两段: catk[符卡].successes/attempts[本机], 99 封顶,
        # 十位 0 省略 (Gui.cpp:1761-1795)
        succ = atte = 0
        store = getattr(game, "store", None) if game is not None else None
        if store is not None and 0 <= self._sc_idx < len(store.catk):
            shot = getattr(game, "character", 0)
            entry = store.catk[self._sc_idx]
            succ = min(entry["successes"][shot], 99)
            atte = min(entry["attempts"][shot], 99)
        x += 36.0
        if succ // 10:
            self._blit_digit(surf, succ // 10, x, y)
        x += 7.0
        self._blit_digit(surf, succ % 10, x, y)
        x += 14.0
        if atte // 10:
            self._blit_digit(surf, atte // 10, x, y)
        x += 7.0
        self._blit_digit(surf, atte % 10, x, y)
