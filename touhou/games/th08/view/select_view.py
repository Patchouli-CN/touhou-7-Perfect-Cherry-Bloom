"""th08 难度选择/机体选择画面的原作版渲染 —— select00.png 背景 + title01.anm vm。

对照 th08-ref TitleScreen.cpp(行号相对其 src/):
- 背景: 进难度选择时 LoadSurface(0, "title/select00.png")(:1421), 机体选择
  沿用同一背景; 回主菜单时才换回 title00.png(:304)。
- 难度选择(OnUpdateDifficultySelect :1405-1598): vms[131..135] = 5 槽难度项
  (TITLE_SPRITE_DIFFICULTY_START=131, :47-48), 标题带 = script 136
  (sprite 152 "難易度選択の刻 Choose Level.")。进场 SetInterruptArray(7)
  (:1431; Extra 流是 12, :1436-1440), 随后选中项 SetSprite(base)+interrupt 24 /
  未选中 base+1 + interrupt 25(:1450-1459; SetInterrupt 覆写 pending,
  AnmManager.hpp:318-321, 所以 131..134 实际只跑 24/25 路径), 光标移动时
  重发(:1496-1505)。贴图成对: base=彩色(选中), base+1=灰(未选中)。
- Extra 流(DifficultySelectExtra): menuLength=1(:1491), 只亮 vms[135]
  (sprite 142 = 望月 Extra Level "月は人を狂わす"; th08 无 Phantasm,
  IsPhantasmUnlocked 恒 FALSE(GameManager.cpp:1367), 走 :1436-1440 的
  interrupt 12 分支 + :1468-1469 SetSprite(base))。
- 机体选择(OnUpdateCharacterSelect :1601-1854): vms[111..130] 头像/名牌
  (interrupt 8=退隐, 9=选中, 23=灰显队友), 映射表
  g_TitleCharacterSpriteIndices[12][4](:126-162) 的值 = vm 下标(= script 号);
  标题带 = script 137(sprite 153 "人と妖怪の選択の刻", 进场 interrupt 8 显示,
  :1617); 当前难度角标 = vms[131+difficulty] interrupt 9 滑到 (16,384)(:1638;
  落点为本机真 title01.anm 实测, op18 运行值在 args_f, args_i 只是位模式);
  label 9 路径不设 alpha, 新建 vm alpha=0, 需先手动补 255)。
- 通关标记(DrawCompletionStatusText, TitleCompletionStatus.inl:12-67):
  无脚本 vm 直摆 sprite 145-148 于 (400,170) 左上锚点(:56-65), 进场
  stateTimer2>8 才画(:16); 只在主 CharacterSelect 画(OnDraw :3594-3596),
  Extra/Practice 变体不画(调用方传 None)。
- 画面切换即时(无退出动画), 与 A 期主菜单一致; 每屏重进重放进场动画
  (调用方传 frame = 进屏起的渲染帧数, frame==0 = 进场, 对照 Init 的
  SetInterruptArray 时机)。

绘制锚点: anchor & 1 = 左锚 / & 2 = 顶锚(AnmManager.cpp:1303-1323);
难度项脚本带 op22 AnchorTopLeft(anchor=3), 头像/名牌/标题带/标记是默认
中心锚点(标记手动 anchor=3)。flag1 门控(:1290)不写: 进场/移动路径里
非选中 vm 已由 interrupt 8 的 AlphaTime→0 + StopHide 隐藏, 视觉效果等价。
"""

from __future__ import annotations

import io

import pygame

from ....engine.view.anm_fx import TransformCache, Vm2d
from ....engine.view.sprite_bank import SpriteBank
from ..crypt import try_decrypt_from_table
from .anm_vm import AnmVmTh08
from .title_flow import CharacterFlowTh08
from .title_view import _TITLE_ANM, _TitleScriptBank

_SELECT_BG = "select00.png"  # 封包内条目名(C++ 侧路径 "title/select00.png")
_W, _H = 640, 480

# vms 槽位(TitleScreen.cpp:45-48; script 号 = vm 下标)
_DIFF_SCRIPTS = (131, 132, 133, 134, 135)  # E/N/H/L/Extra 位
_DIFF_CAPTION_SCRIPT = 136  # "難易度選択の刻 Choose Level."
_CHAR_SCRIPTS = tuple(range(111, 131))  # TITLE_SPRITE_CHARACTER_START..END
_CHAR_CAPTION_SCRIPT = 137  # "人と妖怪の選択の刻 Choose Girl."
_CHAR_VM_BASE = 111  # 映射表值 → _char_vms 下标的偏移

# g_TitleCharacterSpriteIndices[12][4](:126-162): 每机体的 vm 行 —
# 前 3 槽选中显示(interrupt 9), 第 4 槽灰显(interrupt 23), -1 = 空
_CHAR_VM_TABLE = (
    (0x77, 0x6F, 0x70, -1),
    (0x78, 0x72, 0x71, -1),
    (0x79, 0x73, 0x74, -1),
    (0x7A, 0x76, 0x75, -1),
    (0x7B, 0x6F, -1, 0x70),
    (0x7C, 0x70, -1, 0x6F),
    (0x7D, 0x72, -1, 0x71),
    (0x7E, 0x71, -1, 0x72),
    (0x7F, 0x73, -1, 0x74),
    (0x80, 0x74, -1, 0x73),
    (0x81, 0x76, -1, 0x75),
    (0x82, 0x75, -1, 0x76),
)

_MARK_POS = (400.0, 170.0)  # 通关标记落位(TitleCompletionStatus.inl:63-64)
_MARK_DELAY = 8  # 进场 stateTimer2 > 8 才画(TitleCompletionStatus.inl:16)


class _SelectViewBase:
    """两屏共用: 资源装载(select00.png + title01.anm 脚本表) + 锚点感知绘制。

    构造失败(无数据/资源损坏)抛异常, 由后端回退文字菜单(同 TitleView 口径)。
    """

    def __init__(self, data_path) -> None:
        self._bank = SpriteBank(data_path, game="th08")
        self._tcache = TransformCache()
        self._sb = _TitleScriptBank(self._bank, _TITLE_ANM)
        if not self._sb.ok:
            raise FileNotFoundError(_TITLE_ANM)
        # 背景(LoadSurface :1421; 封包内 edz 加密 PNG → 解密 → 解码)
        raw = try_decrypt_from_table(self._bank._archive().load(_SELECT_BG))
        self._bg = pygame.image.load(io.BytesIO(raw))
        try:
            self._bg = self._bg.convert()  # 快速 blit 路径(需 display 初始化)
        except pygame.error:
            pass
        self._frame = pygame.Surface((_W, _H))

    def _start_vms(self, gids) -> list[Vm2d]:
        """建 vm 并跑脚本(= ExecuteAnmIdxArray; 初始路径均停在隐藏态)。"""
        out = []
        for gid in gids:
            v = Vm2d(self._sb, self._tcache, vm_cls=AnmVmTh08)
            if not v.start(gid):
                raise ValueError(f"{_TITLE_ANM} 缺脚本 {gid}")
            out.append(v)
        return out

    def _draw_vm(self, surf: pygame.Surface, v: Vm2d) -> None:
        """按 vm 状态 blit; anchor &1=左锚 &2=顶锚(AnmManager.cpp:1303-1323),
        否则中心锚点。"""
        vm = v.vm
        img = v.surf
        if not vm.visible or img is None:
            return
        r, g, b, a = vm.color2 if vm.flag17 else vm.color  # :987 的二选一
        if a <= 0 or vm.scale[0] == 0.0 or vm.scale[1] == 0.0:
            return
        out = self._tcache.get(img, vm.scale[0], vm.scale[1], vm.rotation[2])
        if (r, g, b) != (255, 255, 255) or a < 255:
            out = self._tcache.get_modulated(out, r, g, b, a)
        x = int(vm.pos[0] + vm.offset[0])
        y = int(vm.pos[1] + vm.offset[1])
        if not vm.anchor & 1:
            x -= out.get_width() // 2
        if not vm.anchor & 2:
            y -= out.get_height() // 2
        surf.blit(out, (x, y))

    def _new_frame(self) -> pygame.Surface:
        surf = self._frame
        surf.blit(self._bg, (0, 0))
        return surf


class DifficultySelectView(_SelectViewBase):
    """难度选择渲染(DifficultySelect / DifficultySelectExtra 两变体)。

    render(extra, cursor, frame): frame==0 = 进屏(重放进场), 否则光标变化
    时重发 24/25(:1496-1505)。构造失败抛异常(后端回退文字菜单)。
    """

    def __init__(self, data_path) -> None:
        super().__init__(data_path)
        self._diff_vms = self._start_vms(_DIFF_SCRIPTS)
        # baseSpriteIndex = 脚本首帧 sprite(AnmManager.cpp:956)
        self._bases = [v.vm.active_sprite_idx for v in self._diff_vms]
        self._caption = self._start_vms((_DIFF_CAPTION_SCRIPT,))[0]
        self._last: tuple[bool, int] | None = None  # (extra, cursor)

    def _enter(self, extra: bool, cursor: int) -> None:
        # 进场 SetInterruptArray(:1431 普通=7 / :1438 Extra=12; 无对应 label
        # 的 vm 落 Label -1 保持隐藏 —— 135 无 label 7, 131..134 无 label 12)
        intr = 12 if extra else 7
        for v in self._diff_vms:
            v.vm.pending_interrupt = intr
        self._caption.vm.pending_interrupt = intr
        if extra:
            # :1468-1469: 单项 Extra 亮彩色贴图(menuLength=1, 无光标态)
            self._diff_vms[4].set_sprite(self._bases[4])
        else:
            self._apply_cursor(cursor)

    def _apply_cursor(self, cursor: int) -> None:
        """选中 = base + interrupt 24, 未选中 = base+1 + interrupt 25
        (:1450-1459 进场 / :1496-1505 移动)。"""
        for i in range(4):
            v = self._diff_vms[i]
            v.set_sprite(self._bases[i] + 1)
            v.vm.pending_interrupt = 25
        v = self._diff_vms[cursor]
        v.set_sprite(self._bases[cursor])
        v.vm.pending_interrupt = 24

    def render(self, extra: bool, cursor: int, frame: int) -> pygame.Surface:
        state = (extra, cursor)
        if frame == 0:
            self._enter(extra, cursor)
        elif state != self._last and not extra:
            self._apply_cursor(cursor)
        self._last = state
        surf = self._new_frame()
        vms = (*self._diff_vms, self._caption)
        for v in vms:
            v.execute()
        for v in vms:
            self._draw_vm(surf, v)
        return surf


class CharacterSelectView(_SelectViewBase):
    """机体选择渲染(CharacterSelect / CharacterSelectExtra 两变体)。

    render(flow, completion, frame): flow = CharacterFlowTh08(光标/变体/难度);
    completion = 通关标记 sprite 号(145-148)或 None; frame==0 = 进屏。
    构造失败抛异常(后端回退文字菜单)。
    """

    def __init__(self, data_path) -> None:
        super().__init__(data_path)
        self._char_vms = self._start_vms(_CHAR_SCRIPTS)
        self._caption = self._start_vms((_CHAR_CAPTION_SCRIPT,))[0]
        self._diff_vms = self._start_vms(_DIFF_SCRIPTS)
        # 通关标记 vm: 无脚本, 直摆状态(InitializeTitleCompletionVmAndSetSprite
        # + :56-65; 每帧重设 sprite 是原作原样, :24 每帧重建)
        self._mark = Vm2d(self._sb, self._tcache, vm_cls=AnmVmTh08)
        mark_vm = self._mark.vm
        mark_vm.visible = True
        mark_vm.anchor = 3
        mark_vm.pos[0], mark_vm.pos[1] = _MARK_POS
        self._last: tuple[bool, int, int] | None = None  # (extra, cursor, difficulty)

    def _apply_cursor(self, cursor: int) -> None:
        """全员 interrupt 8(退隐) → 命中行前 3 槽 9(选中) / 第 4 槽 23(灰显)
        (:1652-1669 进场 / :1738-1753 移动; SetInterrupt 覆写 pending)。"""
        row = _CHAR_VM_TABLE[cursor]
        for v in self._char_vms:
            v.vm.pending_interrupt = 8
        for k in range(3):
            if row[k] >= 0:
                self._char_vms[row[k] - _CHAR_VM_BASE].vm.pending_interrupt = 9
        if row[3] >= 0:
            self._char_vms[row[3] - _CHAR_VM_BASE].vm.pending_interrupt = 23

    def _enter(self, flow: CharacterFlowTh08) -> None:
        # 标题带 script 137 label 8 = 显示(进机体选择的 SetInterruptArray(8),
        # :1617); 难度角标 :1638 —— label 9 路径不设 alpha, 新 vm 补 255
        self._caption.vm.pending_interrupt = 8
        dv = self._diff_vms[flow.difficulty]
        dv.vm.color[3] = 255
        dv.vm.pending_interrupt = 9
        self._apply_cursor(flow.cursor.index)

    def render(
        self, flow: CharacterFlowTh08, completion: int | None, frame: int
    ) -> pygame.Surface:
        state = (flow.extra, flow.cursor.index, flow.difficulty)
        if frame == 0:
            self._enter(flow)
        elif state != self._last:
            self._apply_cursor(flow.cursor.index)
        self._last = state
        surf = self._new_frame()
        for v in (*self._char_vms, self._caption, *self._diff_vms):
            v.execute()
        for v in (*self._char_vms, self._caption, *self._diff_vms):
            self._draw_vm(surf, v)
        if completion is not None and frame > _MARK_DELAY:
            self._mark.set_sprite(completion)
            self._draw_vm(surf, self._mark)
        return surf


__all__ = ["CharacterSelectView", "DifficultySelectView"]
