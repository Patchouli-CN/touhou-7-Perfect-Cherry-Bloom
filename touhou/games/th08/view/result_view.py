"""th08 Result 浏览面的原作版渲染 —— result.jpg 背景 + result00.anm 贴图 vm + 直接绘字。

对照 th08-ref(@1861f88, 行号相对其 src/) ResultScreen.cpp: OnDraw(:2440-2840)
/ 各 Handle* 的 vm interrupt 驱动; 原作榜单/符卡/统计文本是 AsciiManager/
TextHelper 烘贴图或直绘, 这里统一直接绘字(§19.1 路线, 同 music_view)。

- 背景 = LoadSurface(0, "result/result.jpg")(:2888; 封包内条目名扁平
  "result.jpg", edz 加密); result00.anm 槽 21(:2893)。
- vm 组(script 号 = RESULT_SCRIPT_* 常量, :25-48; 贴图内容为本机真 th08.dat
  实测): 0..3 = 类别 4 项(最高記録一覧/スペルカード一覧/その他の状態一覧/
  タイトルに戻る), 4..8 = 高分榜难度 5 项, 9..14 = 符卡难度 6 项,
  15..26 = 高分榜机体 12 名牌, 27..39 = 符卡机体 13 名牌(末 = 全ての
  キャラクター), 40 = 榜单底板(listing, 640 外滑入)。
- interrupt(:54-82): 1/2 = 退隐(脚本无显式 1/2 处理器, 落 -1 默认滑出左屏),
  3+难度 = 底板入场换色, 10 = 翻页, 20/21 = 选中/未选中(4 帧色变 +
  选中项 pos2 偏移 -4,-4), 22 = 滑入, 23 = 确认滑定, 24/25 = 名牌淡出/淡入。
  驱动时序照抄各 Handle* 的转移分支(见 _on_state_change 注释)。
- 榜单/符卡行文本落位相对底板 vm 的 pos(OnDraw :2475-2684); 统计 19 行
  落位 (56, 64+17i)(:1997-2092), 40 帧淡入(:2094-2101)。
- 颜色常量是 AsciiManager.SetColor 的 0xAARRGGBB(:2486-2664 段)。
"""

from __future__ import annotations

import io

import pygame

from ....engine.view.anm_fx import TransformCache, Vm2d
from ....engine.view.sprite_bank import SpriteBank
from ..crypt import try_decrypt_from_table
from .anm_vm import AnmVmTh08
from .result_data import highscore_rows, spellcard_header, spellcard_rows, stats_lines
from .result_flow import ResultBrowseState, ResultFlowTh08
from .title_view import _TitleScriptBank, _load_font

_W, _H = 640, 480

_RESULT_ANM = "result00.anm"  # 槽 21(:2893)
_RESULT_BG = "result.jpg"  # 封包内条目名(C++ 侧 "result/result.jpg", :2888)

# vm 组(script 号区间, :25-48)
_GROUP_CATEGORY = range(0, 4)
_GROUP_HS_DIFF = range(4, 9)
_GROUP_SC_DIFF = range(9, 15)
_GROUP_HS_CHAR = range(15, 27)
_GROUP_SC_CHAR = range(27, 40)
_LISTING = 40
_DIVIDER_SPRITE = 32  # listingDividerSprite(:2913); 行分隔细条

# interrupt 号(:54-82)
_INT_HIDE = 1
_INT_LISTING_APPEAR = 3  # +难度 = 各难度色(:980-981)
_INT_LISTING_MOVE_PAGE = 10
_INT_SELECTED = 20
_INT_NOT_SELECTED = 21
_INT_APPEAR = 22
_INT_CHOSEN = 23
_INT_CHAR_DISAPPEAR = 24
_INT_CHAR_APPEAR = 25

# 榜单文本(OnDraw :2483-2546; 落位相对底板 pos)
_HS_HEADER_OFF = (24, 18)
_HS_ROW_Y0 = 36
_HS_ROW_H = 18
_HS_RANK_X = 24
_HS_NAME_X = 72  # :2513 pos.x += 48
_HS_DATE_X = 392  # :2541 pos.x += 320
_HS_HEADER_COLOR = (224, 224, 239)  # 0xffe0e0ef(:2486)
_HS_ROW_COLOR = (255, 192, 192)  # 0xffffc0c0(:2508)
_HS_HEADER = "No  Name       Score(Stage)   Date   Slow"  # :2487
_HS_NO_SLOW = "--"  # lagPercentage 无存档字段(偏离注明)

# 符卡文本(OnDraw :2552-2684)
_SC_HEADER_OFF = (320, -16)  # textVms[10](:2564-2566)
_SC_ROW_Y0 = 16
_SC_ROW_H = 33  # :2674
_SC_NO_X = 0  # "No.%02d"(:2612)
_SC_NAME_X = 78  # :2614
_SC_STATS_X = 446  # :2616
_SC_MAXBONUS_X = 424  # :2653
_SC_MAXBONUS_DY = -13  # :2654
_SC_DIVIDER_OFF = (320, 16)  # :2583-2588
_SC_DIVIDER_SX = 2.375  # :2587
_SC_HIDDEN_COLOR = (192, 192, 255)  # 0xc0c0c0ff(:2600)
_SC_FAILED_COLOR = (192, 160, 160)  # 0xffc0a0a0(:2605)
_SC_CAPTURED_RGB = (240, 240, 255)  # 0xfff0f0ff 起始, 每行 G/B 渐减 8(:2609)
_SC_MAXBONUS_COLOR = (160, 128, 144)  # 0xffa08090(:2656)
_SC_HIDDEN_ALPHA = 192

# 统计(HandleOtherStatsScreen :1997-2092 + 淡入 :2094-2101)
_STATS_X = 56
_STATS_Y0 = 64
_STATS_ROW_H = 17
_STATS_FADE_FRAMES = 40

_FONT_SIZE = 15  # textVms fontWidth/fontHeight=15(:2921-2922)
_MAXBONUS_FONT_SIZE = 12  # SetScale(0.8)(:2657) ≈ 15*0.8
_SHADOW = (0, 0, 48)
_WHITE = (255, 255, 255)


def _blit_text(
    surf: pygame.Surface,
    font: pygame.font.Font | None,
    text: str,
    x: int,
    y: int,
    color: tuple[int, int, int],
    alpha: int = 255,
) -> None:
    """左对齐绘字(4 向描边; 原作烘贴图/AsciiManager, 这里直接绘字)。"""
    if font is None or not text:
        return
    img = font.render(text, True, color)
    sh = font.render(text, True, _SHADOW)
    if alpha < 255:
        img.set_alpha(alpha)
        sh.set_alpha(alpha)
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        surf.blit(sh, (x + dx, y + dy))
    surf.blit(img, (x, y))


class ResultBrowseView:
    """Result 浏览面渲染器: render(flow, frame) 画一帧到 640x480 surface。

    构造失败(无数据/资源损坏)抛异常, 由后端回退文字版(口径同
    TitleView/MusicRoomView)。vm 的 interrupt 驱动是边沿触发: 只在
    (状态, 光标, 页, 机体) 变化时发, 与各 Handle* 的写法一致。
    """

    def __init__(self, data_path) -> None:
        self._bank = SpriteBank(data_path, game="th08")
        self._tcache = TransformCache()
        # result00.anm 与 title01.anm 同病: 扁平存储 id 全文件连续编号
        # (entry0=[0..24], entry1=[25..32], …) —— 复用装载序重建
        # (title_view.py:57)
        self._sb = _TitleScriptBank(self._bank, _RESULT_ANM)
        if not self._sb.ok:
            raise FileNotFoundError(_RESULT_ANM)
        raw = try_decrypt_from_table(self._bank._archive().load(_RESULT_BG))
        self._bg = pygame.image.load(io.BytesIO(raw))
        try:
            self._bg = self._bg.convert()  # 快速 blit 路径(需 display 初始化)
        except pygame.error:
            pass
        self._vms: list[Vm2d] = []
        for gid in range(_LISTING + 1):
            v = Vm2d(self._sb, self._tcache, vm_cls=AnmVmTh08)
            if not v.start(gid):
                raise ValueError(f"{_RESULT_ANM} 缺脚本 {gid}")
            self._vms.append(v)
        self._divider = self._sb.sprite_surf(_DIVIDER_SPRITE)
        self._frame = pygame.Surface((_W, _H))
        self._font = None
        self._mb_font = None
        # 上帧的 (state, cursor, page, shot_type) —— interrupt 边沿检测
        self._last: tuple | None = None

    # ---- vm 驱动(interrupt 边沿; 各分支出处见注) ----
    def _fire(self, idx: int, interrupt: int) -> None:
        """置 pendingInterrupt 并立即 ExecuteScript(对照 C++ 设中断后当场
        ExecuteScript 的写法, :564 等; 不立即跑的话同帧后设的中断会顶掉先设的)。"""
        v = self._vms[idx]
        v.vm.pending_interrupt = interrupt
        v.execute()

    def _appear_select_group(self, group: range, cursor: int) -> None:
        """选择屏进场: 组内全部滑入(22) + 选中/未选中(20/21)
        (各 *Select case 0 段, :554-573/:704-720/:871-887/:1011-1027/:1102-1118)。"""
        for i in group:
            self._fire(i, _INT_APPEAR)
            self._fire(
                i, _INT_SELECTED if i - group.start == cursor else _INT_NOT_SELECTED
            )

    def _exit_select_group(self, group: range, chosen: int) -> None:
        """选择屏确认退出: 选中项滑定(23), 其余退隐(1)
        (:635-645/:760-770/:933-943/:1067-1077/:1162-1172)。"""
        for i in group:
            self._fire(i, _INT_CHOSEN if i - group.start == chosen else _INT_HIDE)

    def _hide_group(self, group: range) -> None:
        """整组退隐(1 → 脚本 -1 默认处理器滑出, :58-61 注释)。"""
        for i in group:
            self._fire(i, _INT_HIDE)

    def _on_state_change(
        self, old: int | None, flow: ResultFlowTh08, prev_cursor: int
    ) -> None:
        """状态转移的 vm 动作(对照各 Handle* 的 SELECTMENU/RETURNMENU 分支 +
        目标态 case 0 进场段)。"""
        new = flow.state
        # 离开侧
        if old == ResultBrowseState.CATEGORY:
            if new != ResultBrowseState.CATEGORY:
                self._exit_select_group(_GROUP_CATEGORY, prev_cursor)
        elif old == ResultBrowseState.HIGHSCORE_DIFFICULTY:
            if new == ResultBrowseState.HIGHSCORE_CHARACTER:
                self._exit_select_group(_GROUP_HS_DIFF, prev_cursor)
        elif old == ResultBrowseState.HIGHSCORE_CHARACTER:
            if new == ResultBrowseState.HIGHSCORE:
                self._exit_select_group(_GROUP_HS_CHAR, prev_cursor)
            else:  # 回难度选择(:919-922)
                self._hide_group(_GROUP_HS_CHAR)
        elif old == ResultBrowseState.SPELLCARD_DIFFICULTY:
            if new == ResultBrowseState.SPELLCARD_CHARACTER:
                self._exit_select_group(_GROUP_SC_DIFF, prev_cursor)
        elif old == ResultBrowseState.SPELLCARD_CHARACTER:
            if new == ResultBrowseState.SPELLCARD:
                self._exit_select_group(_GROUP_SC_CHAR, prev_cursor)
            else:  # 回难度选择(:1150-1153)
                self._hide_group(_GROUP_SC_CHAR)
        elif old in (ResultBrowseState.HIGHSCORE, ResultBrowseState.SPELLCARD):
            self._fire(_LISTING, _INT_HIDE)  # :992/:1283
        # 进入侧
        if new == ResultBrowseState.CATEGORY:
            # 回类别选择走 phase 0: 全部退隐后 0..3 滑入(:554-573)
            self._hide_group(range(0, _LISTING + 1))
            self._appear_select_group(_GROUP_CATEGORY, flow.cursor)
        elif new == ResultBrowseState.HIGHSCORE_DIFFICULTY:
            self._appear_select_group(_GROUP_HS_DIFF, flow.cursor)
        elif new == ResultBrowseState.HIGHSCORE_CHARACTER:
            self._appear_select_group(_GROUP_HS_CHAR, flow.cursor)
        elif new == ResultBrowseState.HIGHSCORE:
            self._fire(_LISTING, _INT_LISTING_APPEAR)  # :945(进场恒 3)
        elif new == ResultBrowseState.SPELLCARD_DIFFICULTY:
            self._appear_select_group(_GROUP_SC_DIFF, flow.cursor)
        elif new == ResultBrowseState.SPELLCARD_CHARACTER:
            self._appear_select_group(_GROUP_SC_CHAR, flow.cursor)
        elif new == ResultBrowseState.SPELLCARD:
            self._fire(_LISTING, _INT_LISTING_APPEAR)  # :1179

    def _on_same_state_change(
        self, flow: ResultFlowTh08, prev_cursor: int, prev_page: int, prev_shot: int
    ) -> None:
        """同状态内的光标/页/机体变化(各 Handle* 的移动分支)。"""
        s = flow.state
        if s == ResultBrowseState.CATEGORY:
            group = _GROUP_CATEGORY
        elif s == ResultBrowseState.HIGHSCORE_DIFFICULTY:
            group = _GROUP_HS_DIFF
        elif s == ResultBrowseState.HIGHSCORE_CHARACTER:
            group = _GROUP_HS_CHAR
        elif s in (
            ResultBrowseState.SPELLCARD_DIFFICULTY,
            ResultBrowseState.SPELLCARD_CHARACTER,
        ):
            group = (
                _GROUP_SC_DIFF
                if s == ResultBrowseState.SPELLCARD_DIFFICULTY
                else _GROUP_SC_CHAR
            )
        else:
            group = None
        if group is not None:
            if prev_cursor != flow.cursor:  # :584-597 等(移动才重发)
                for i in group:
                    self._fire(
                        i,
                        _INT_SELECTED
                        if i - group.start == flow.cursor
                        else _INT_NOT_SELECTED,
                    )
            return
        if s == ResultBrowseState.HIGHSCORE and prev_cursor != flow.cursor:
            # 左右切机体: 旧名牌淡出/新名牌淡入 + 底板换色(:980-985)
            self._fire(_GROUP_HS_CHAR.start + prev_cursor, _INT_CHAR_DISAPPEAR)
            self._fire(_GROUP_HS_CHAR.start + flow.cursor, _INT_CHAR_APPEAR)
            self._fire(_LISTING, _INT_LISTING_APPEAR + flow.selected_difficulty)
        elif s == ResultBrowseState.SPELLCARD:
            if prev_page != flow.page:
                self._fire(_LISTING, _INT_LISTING_MOVE_PAGE)  # :1252
            if prev_shot != flow.shot_type:
                # :1270-1274(连按叠图的 ZUN bug 不修, 引擎侧无此现象)
                self._fire(_GROUP_SC_CHAR.start + prev_shot, _INT_CHAR_DISAPPEAR)
                self._fire(_GROUP_SC_CHAR.start + flow.shot_type, _INT_CHAR_APPEAR)

    def _sync_interrupts(self, flow: ResultFlowTh08) -> None:
        cur = (flow.state, flow.cursor, flow.page, flow.shot_type)
        prev = self._last
        if prev == cur:
            return
        if prev is None or prev[0] != flow.state:
            self._on_state_change(
                None if prev is None else prev[0], flow, 0 if prev is None else prev[1]
            )
        else:
            self._on_same_state_change(flow, prev[1], prev[2], prev[3])
        self._last = cur

    # ---- 绘制 ----
    def _draw_vm(self, surf: pygame.Surface, v: Vm2d) -> None:
        """按 vm 状态 blit(锚点感知; 与 MusicRoomView._draw_vm 同逻辑)。"""
        vm = v.vm
        img = v.surf
        if not vm.visible or img is None:
            return
        r, g, b, a = vm.color2 if vm.flag17 else vm.color
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

    def _listing_pos(self) -> tuple[int, int]:
        """底板 vm 的当前 pos(文本落位基准, OnDraw :2475/:2562)。"""
        vm = self._vms[_LISTING].vm
        return int(vm.pos[0]), int(vm.pos[1])

    def _draw_highscores(self, surf: pygame.Surface, flow: ResultFlowTh08) -> None:
        """高分榜 10 行(:2475-2549)。"""
        if self._font is None:
            self._font = _load_font(_FONT_SIZE)
        lx, ly = self._listing_pos()
        if lx >= _W:
            return  # 底板未入场(:2477)
        _blit_text(
            surf,
            self._font,
            _HS_HEADER,
            lx + _HS_HEADER_OFF[0],
            ly + _HS_HEADER_OFF[1],
            _HS_HEADER_COLOR,
        )
        rows = highscore_rows(
            flow.store, flow.selected_difficulty, flow.selected_character
        )
        for i, row in enumerate(rows):
            y = ly + _HS_ROW_Y0 + i * _HS_ROW_H
            _blit_text(
                surf, self._font, f"{row.rank:>2}", lx + _HS_RANK_X, y, _HS_ROW_COLOR
            )
            _blit_text(
                surf,
                self._font,
                f"{row.name:8s} {row.score:9d}{row.retries}({row.stage_label})",
                lx + _HS_NAME_X,
                y,
                _HS_ROW_COLOR,
            )
            _blit_text(
                surf,
                self._font,
                f" {row.date:>5s}   {_HS_NO_SLOW}",
                lx + _HS_DATE_X,
                y,
                _HS_ROW_COLOR,
            )

    def _draw_spellcards(self, surf: pygame.Surface, flow: ResultFlowTh08) -> None:
        """符卡战绩页(:2552-2689): 表头收取数 + 每页 10 行 + MaxBonus + 分隔条。"""
        if self._font is None:
            self._font = _load_font(_FONT_SIZE)
        if self._mb_font is None:
            self._mb_font = _load_font(_MAXBONUS_FONT_SIZE)
        lx, ly = self._listing_pos()
        if lx >= _W:
            return
        _blit_text(
            surf,
            self._font,
            spellcard_header(
                flow.store, flow.selected_spellcard_difficulty, flow.shot_type
            ),
            lx + _SC_HEADER_OFF[0],
            ly + _SC_HEADER_OFF[1],
            _WHITE,
        )
        rows = spellcard_rows(
            flow.store, flow.selected_spellcard_difficulty, flow.page, flow.shot_type
        )
        for i, row in enumerate(rows):
            y = ly + _SC_ROW_Y0 + i * _SC_ROW_H
            if not row.attempted:
                color, alpha = _SC_HIDDEN_COLOR, _SC_HIDDEN_ALPHA
            elif not row.captured:
                color, alpha = _SC_FAILED_COLOR, 255
            else:
                color = (
                    _SC_CAPTURED_RGB[0],
                    _SC_CAPTURED_RGB[1] - 8 * i,
                    _SC_CAPTURED_RGB[2] - 8 * i,
                )
                alpha = 255
            _blit_text(
                surf, self._font, row.number_label, lx + _SC_NO_X, y, color, alpha
            )
            _blit_text(surf, self._font, row.name, lx + _SC_NAME_X, y, color, alpha)
            _blit_text(surf, self._font, row.stats, lx + _SC_STATS_X, y, color, alpha)
            if row.max_bonus:
                _blit_text(
                    surf,
                    self._mb_font,
                    f"MaxBonus {row.max_bonus:8d}",
                    lx + _SC_MAXBONUS_X,
                    y + _SC_MAXBONUS_DY,
                    _SC_MAXBONUS_COLOR,
                )
            if self._divider is not None:
                div = self._tcache.get(self._divider, _SC_DIVIDER_SX, 1.0, 0.0)
                surf.blit(div, (lx + _SC_DIVIDER_OFF[0], y + _SC_DIVIDER_OFF[1]))

    def _draw_stats(self, surf: pygame.Surface, flow: ResultFlowTh08) -> None:
        """统计 19 行(:1997-2092) + 40 帧淡入(:2100)。"""
        if self._font is None:
            self._font = _load_font(_FONT_SIZE)
        alpha = min(255, flow.frames * 255 // _STATS_FADE_FRAMES)
        for i, line in enumerate(stats_lines(flow.store)):
            _blit_text(
                surf,
                self._font,
                line,
                _STATS_X,
                _STATS_Y0 + i * _STATS_ROW_H,
                _WHITE,
                alpha,
            )

    def render(self, flow: ResultFlowTh08, frame: int = 0) -> pygame.Surface:
        self._sync_interrupts(flow)
        surf = self._frame
        surf.blit(self._bg, (0, 0))
        for v in self._vms:
            v.execute()
        for v in self._vms:
            self._draw_vm(surf, v)
        if flow.state == ResultBrowseState.HIGHSCORE:
            self._draw_highscores(surf, flow)
        elif flow.state == ResultBrowseState.SPELLCARD:
            self._draw_spellcards(surf, flow)
        elif flow.state == ResultBrowseState.STATS:
            self._draw_stats(surf, flow)
        return surf


__all__ = ["ResultBrowseView"]
