"""th08 Option / KeyConfig 画面的原作版渲染 —— title00.png 背景 + title01.anm vm。

对照 th08-ref TitleScreen.cpp(行号相对其 src/); vm 槽位/贴图内容为本机真
th08.dat title01.anm 实测(脚本首帧 sprite = baseSpriteIndex 对应物,
AnmManager.cpp:956; base=彩色选中帧, base+1=灰未选中帧):

- Option(OnUpdateOptions :644-1153, 进场 SetInterruptArray(3) :653):
  vms[10..19] = 10 行标签 Player/Graphic/BGM/Vol/S.E.Vol/Mode/SlowMode/
  Reset/KeyConfig/Quit(行下标 = TITLE_MENU_ITEM_OPTION_* 枚举 :93-106),
  落位 (128, 110+32i);
  vms[20..26] = Player 残机档 1..7 数字(TITLE_SPRITE_OPTION_PLAYER_START/
  END :19-20, (256+32k, 110)); vms[27..28] = Graphic 32Bit/16Bit(:22-23);
  vms[29..31] = BGM Off/Wav/Midi(:25-26, MusicMode OFF=0/WAV=1/MIDI=2,
  Supervisor.hpp:21-26); vms[32..34]+[35] = Vol 三位数字+"%"(:28-30,
  百位 <100 隐、十位 <10 隐 :735-757); vms[36..38]+[39] = S.E.Vol 同构
  (:32-34, :768-798); vms[40..41] = Mode FullScreen/Window(:36-37);
  vms[42..43] = SlowMode Off/On(:39-40)。
- KeyConfig(OnUpdateKeyConfig :1156-1402, 进场 SetInterruptArray(4) :1168):
  vms[44] = "KeyConfig" 标题; vms[45..56] = 12 行标签 Shot/Bomb/Slow/Skip/
  Pause/Up/Down/Left/Right/ShotSlow/Reset/Quit(枚举 :108-122), 落位
  (204, 106+26i); vms[57..74] = 手柄按钮号数字对(SetKeyNumberSprite
  :1385-1402) —— 我们是键盘映射, 不建这组 vm, 键名直接绘字(pygame key
  name, 任务约定不烘贴图); vms[75..76] = ShotSlow Off/On(:42-43)。
- 选中行 = base 亮 / 其余 base+1 暗(:655-661/:675-681, :1170-1175/:1217-1222);
  当前值 = base, 其余值 base+1(:694-710 等)。
- 背景沿用标题的 title00.png(:304 只在回主菜单时换回, Option/KeyConfig 不重载);
  主菜单 vm 在 interrupt 3/4 下全部退隐(实测: logo 滑到 x=640, 菜单项
  滑出左屏), 所以这两屏只画自己的 vm。
- 画面切换即时(无退出动画), 与 A/B2 期一致; frame==0 = 进屏重放进场动画
  (对照 Init 的 SetInterruptArray 时机 :653/:1168)。

我们引擎无对应物而置灰锁定的项(任务约定的偏离点): Graphic(16bit 色 —
渲染位深不可调)、SlowMode(固定 60fps 无慢速模式)、BGM 的 Off、Mode 的
FullScreen(无全屏支持, Mode 映射为 window_scale 即时 resize, 缩放值另绘
文字)、KeyConfig 的 Pause(菜单键固定 Esc/Enter)与 ShotSlow 行。
"""

from __future__ import annotations

import io

import pygame

from ....engine.config import LIVES_MIN
from ....engine.view.anm_fx import TransformCache, Vm2d
from ....engine.view.sprite_bank import SpriteBank
from ..crypt import try_decrypt_from_table
from .anm_vm import AnmVmTh08
from .title_flow import (
    KEYCONFIG_ITEMS,
    KEYCONFIG_ROW_MAP,
    KeyConfigFlowTh08,
    OptionFlowTh08,
)
from .title_view import _TITLE_ANM, _TITLE_BG, _TitleScriptBank, _load_font

_W, _H = 640, 480

_GRAY = (0x40, 0x40, 0x40)  # 锁定项置灰 0xff404040(同主菜单 :356-365 口径)
_WHITE = (255, 255, 255)

# Option vm 槽位(常量定义 :17-40; script 号 = vm 下标)
_OPT_ROW_SCRIPTS = tuple(range(10, 20))  # 10 行标签
_OPT_PLAYER_SCRIPTS = tuple(range(20, 27))  # 残机档 1..7
_OPT_GRAPHIC_SCRIPTS = (27, 28)  # 32Bit / 16Bit
_OPT_BGM_SCRIPTS = (29, 30, 31)  # Off / Wav / Midi
_OPT_VOL_DIGITS = (32, 33, 34)  # Vol 百/十/个位
_OPT_VOL_PCT = 35  # "%"
_OPT_SE_DIGITS = (36, 37, 38)  # S.E.Vol 百/十/个位
_OPT_SE_PCT = 39
_OPT_MODE_SCRIPTS = (40, 41)  # FullScreen / Window
_OPT_SLOW_SCRIPTS = (42, 43)  # Off / On

# Mode 行的缩放值文字落位(Window 贴图右侧; 原作无此元素, 偏离注明)
_MODE_SCALE_TEXT_POS = (520, 278)

# KeyConfig vm 槽位(script 号 = vm 下标; :1170-1175 的 i+45)
_KC_TITLE_SCRIPT = 44  # "KeyConfig" 标题
_KC_ROW_SCRIPTS = tuple(range(45, 57))  # 12 行标签
_KC_SLOWSHOT_SCRIPTS = (75, 76)  # ShotSlow Off/On(:1250-1256)
# 原作 12 行的落位 y(实测; x=204 由脚本自带): 键名文字按行 y 对齐
_KC_ROW_Y = (106, 132, 158, 184, 210, 236, 262, 288, 314, 340, 366, 392)
_KC_KEYNAME_X = 380  # 键名文字左缘(原作按钮号数字对在 x=396/416, :1231-1248)
_KC_CAPTURE_TEXT = "<press a key>"
# 无对应物的置灰行(原作行号): 4=Pause(菜单键固定) / 9=ShotSlow
_KC_GRAY_ROWS = (4, 9)

_HELP_POS = (320, 448)  # 底部帮助行落位(同主菜单 text.anm script 9)
_HELP_FONT_SIZE = 15  # fontWidth/fontHeight=15(DrawTextCentered 缺省档)
_HELP_COLOR = (224, 240, 255)  # 0xfff0e0(COLORREF 0x00BBGGRR, :670/:1211)
_HELP_SHADOW = (0, 0, 48)  # 0x300000
_HELP_FADE_FRAMES = 20  # 换行重淡入(SetInterrupt(1), :686-687/:1226-1227)
_KEY_FONT_SIZE = 16


def _draw_text(
    surf: pygame.Surface,
    font: pygame.font.Font | None,
    text: str,
    center_x: int,
    y: int,
    alpha: int = 255,
) -> None:
    """居中绘字(4 向描边; 原作是 TextHelper 烘贴图, 这里直接绘字, §19.1 路线)。"""
    if font is None:
        return
    img = font.render(text, True, _HELP_COLOR)
    shadow = font.render(text, True, _HELP_SHADOW)
    if alpha < 255:
        img.set_alpha(alpha)
        shadow.set_alpha(alpha)
    rect = img.get_rect(center=(center_x, y))
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        surf.blit(shadow, rect.move(dx, dy))
    surf.blit(img, rect)


def _tint(v: Vm2d, gray: bool) -> None:
    """置灰(锁定项)/还原调制色(每帧重设, 同 TitleView._sync_menu 口径)。"""
    v.vm.color[:3] = _GRAY if gray else _WHITE


class _OptionViewBase:
    """两屏共用: 资源装载(title00.png + title01.anm 脚本表) + 锚点感知绘制
    + 底部帮助行。构造失败(无数据/资源损坏)抛异常, 由后端回退文字菜单
    (同 TitleView/_SelectViewBase 口径)。"""

    def __init__(self, data_path) -> None:
        self._bank = SpriteBank(data_path, game="th08")
        self._tcache = TransformCache()
        self._sb = _TitleScriptBank(self._bank, _TITLE_ANM)
        if not self._sb.ok:
            raise FileNotFoundError(_TITLE_ANM)
        # 背景(同标题画面; 封包内 edz 加密 PNG → 解密 → 解码)
        raw = try_decrypt_from_table(self._bank._archive().load(_TITLE_BG))
        self._bg = pygame.image.load(io.BytesIO(raw))
        try:
            self._bg = self._bg.convert()  # 快速 blit 路径(需 display 初始化)
        except pygame.error:
            pass
        self._frame = pygame.Surface((_W, _H))
        self._help_font = None
        self._key_font = None
        self._last_help = ""
        self._help_fade = _HELP_FADE_FRAMES

    def _start_vms(self, gids) -> tuple[list[Vm2d], list[int]]:
        """建 vm 并跑脚本(= ExecuteAnmIdxArray; 初始路径均停在隐藏态)。
        返回 (vm 列表, base sprite 表 = 各脚本首帧 sprite, AnmManager.cpp:956)。"""
        vms, bases = [], []
        for gid in gids:
            v = Vm2d(self._sb, self._tcache, vm_cls=AnmVmTh08)
            if not v.start(gid):
                raise ValueError(f"{_TITLE_ANM} 缺脚本 {gid}")
            vms.append(v)
            bases.append(v.vm.active_sprite_idx)
        return vms, bases

    def _draw_vm(self, surf: pygame.Surface, v: Vm2d) -> None:
        """按 vm 状态 blit; anchor &1=左锚 &2=顶锚(AnmManager.cpp:1303-1323),
        否则中心锚点(与 _SelectViewBase._draw_vm 同逻辑)。"""
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

    def _draw_help(self, surf: pygame.Surface, text: str) -> None:
        """底部帮助行(换行重淡入 20 帧, 对照 :686-687/:1226-1227 的
        SetInterrupt(1); 原作烘贴图, 这里直接绘字)。"""
        if text != self._last_help:
            self._last_help = text
            self._help_fade = 0
        alpha = min(255, self._help_fade * 255 // _HELP_FADE_FRAMES)
        if self._help_fade < _HELP_FADE_FRAMES:
            self._help_fade += 1
        if self._help_font is None:
            self._help_font = _load_font(_HELP_FONT_SIZE)
        _draw_text(surf, self._help_font, text, _HELP_POS[0], _HELP_POS[1], alpha)

    def _key_text(self, surf: pygame.Surface, text: str, x: int, y: int) -> None:
        """键名/缩放值文字(左对齐, 同款 4 向描边)。"""
        if self._key_font is None:
            self._key_font = _load_font(_KEY_FONT_SIZE)
        if self._key_font is None:
            return
        shadow = self._key_font.render(text, True, _HELP_SHADOW)
        img = self._key_font.render(text, True, _HELP_COLOR)
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            surf.blit(shadow, (x + dx, y + dy))
        surf.blit(img, (x, y))


class OptionView(_OptionViewBase):
    """Option 画面渲染(OnUpdateOptions 的显示侧对应物)。

    render(flow, frame): flow = OptionFlowTh08(光标/锁定/配置快照);
    frame==0 = 进屏(重放 interrupt 3 进场动画, :653)。
    """

    def __init__(self, data_path) -> None:
        super().__init__(data_path)
        self._rows, self._row_bases = self._start_vms(_OPT_ROW_SCRIPTS)
        self._players, self._player_bases = self._start_vms(_OPT_PLAYER_SCRIPTS)
        self._graphics, self._graphic_bases = self._start_vms(_OPT_GRAPHIC_SCRIPTS)
        self._bgm, self._bgm_bases = self._start_vms(_OPT_BGM_SCRIPTS)
        self._vol_digits, self._vol_digit_bases = self._start_vms(_OPT_VOL_DIGITS)
        vol_pct_vms, vol_pct_bases = self._start_vms((_OPT_VOL_PCT,))
        self._vol_pct, self._vol_pct_base = vol_pct_vms[0], vol_pct_bases[0]
        self._se_digits, self._se_digit_bases = self._start_vms(_OPT_SE_DIGITS)
        se_pct_vms, se_pct_bases = self._start_vms((_OPT_SE_PCT,))
        self._se_pct, self._se_pct_base = se_pct_vms[0], se_pct_bases[0]
        self._mode, self._mode_bases = self._start_vms(_OPT_MODE_SCRIPTS)
        self._slow, self._slow_bases = self._start_vms(_OPT_SLOW_SCRIPTS)
        self._all = (
            *self._rows,
            *self._players,
            *self._graphics,
            *self._bgm,
            *self._vol_digits,
            self._vol_pct,
            *self._se_digits,
            self._se_pct,
            *self._mode,
            *self._slow,
        )

    def _enter(self) -> None:
        """进场 SetInterruptArray(3)(:653): 重放各 vm 的入场动画。"""
        for v in self._all:
            v.vm.pending_interrupt = 3

    @staticmethod
    def _sync_volume(digits: list[Vm2d], bases: list[int], pct: Vm2d, pct_base: int, value: int) -> None:
        """三位数字 + "%"(:735-763/:768-798): 百位 <100 隐、十位 <10 隐,
        数字 sprite = base + 值*2(亮帧; :750-751 的公式, 此处无暗帧切换)。"""
        digits[0].set_sprite(bases[0])  # 百位恒 "1"(base)
        digits[0].vm.color[3] = 255 if value >= 100 else 0
        digits[1].set_sprite(bases[1] + (value // 10 % 10) * 2)
        digits[1].vm.color[3] = 255 if value >= 10 else 0
        digits[2].set_sprite(bases[2] + (value % 10) * 2)
        digits[2].vm.color[3] = 255
        pct.set_sprite(pct_base)
        pct.vm.color[3] = 255

    def _sync(self, flow: OptionFlowTh08) -> None:
        cfg = flow.config
        cursor = flow.cursor.index
        # 行标签: 选中亮/其余暗(:675-681); 锁定行(Graphic/SlowMode)置灰
        for i, v in enumerate(self._rows):
            v.set_sprite(self._row_bases[i] if i == cursor else self._row_bases[i] + 1)
            _tint(v, flow.locked(i))
        # Player 残机档 1..7(:694-710): 当前值亮, 其余暗; 低于引擎下限
        # (LIVES_MIN=2 → 1 架档, 原作可选 :836-839, 我们不接)与高于
        # play_count 解锁上限(:699-707 原作是 flag1 隐藏, 引擎 Vm2d 无
        # flag1 门控, 按任务约定置灰)的档位暗+置灰
        for k, v in enumerate(self._players):
            value = k + 1
            lit = value == cfg.initial_lives
            v.set_sprite(self._player_bases[k] if lit else self._player_bases[k] + 1)
            _tint(v, value < LIVES_MIN or value > flow.max_lives)
        # Graphic: 无对应物恒锁定 —— 32Bit 亮帧置灰(当前态), 16Bit 暗帧置灰
        self._graphics[0].set_sprite(self._graphic_bases[0])
        _tint(self._graphics[0], True)
        self._graphics[1].set_sprite(self._graphic_bases[1] + 1)
        _tint(self._graphics[1], True)
        # BGM(:722-730): Off 无对应物恒暗+置灰; Wav/Midi 当前亮
        self._bgm[0].set_sprite(self._bgm_bases[0] + 1)
        _tint(self._bgm[0], True)
        for k, src in ((1, "wav"), (2, "midi")):
            cur = cfg.bgm_source == src
            self._bgm[k].set_sprite(self._bgm_bases[k] if cur else self._bgm_bases[k] + 1)
            _tint(self._bgm[k], False)
        # Vol / S.E.Vol 数字(:732-798)
        self._sync_volume(
            self._vol_digits, self._vol_digit_bases, self._vol_pct, self._vol_pct_base,
            cfg.bgm_volume,
        )
        self._sync_volume(
            self._se_digits, self._se_digit_bases, self._se_pct, self._se_pct_base,
            cfg.se_volume,
        )
        # Mode(:796-805): 原作 FullScreen/Window 切换; 我们恒 Window(亮),
        # FullScreen 暗+置灰(无全屏支持), 缩放值另绘文字(偏离注明)
        self._mode[0].set_sprite(self._mode_bases[0] + 1)
        _tint(self._mode[0], True)
        self._mode[1].set_sprite(self._mode_bases[1])
        _tint(self._mode[1], False)
        # SlowMode: 无对应物恒锁定 —— Off 亮帧置灰, On 暗帧置灰
        self._slow[0].set_sprite(self._slow_bases[0])
        _tint(self._slow[0], True)
        self._slow[1].set_sprite(self._slow_bases[1] + 1)
        _tint(self._slow[1], True)

    def render(self, flow: OptionFlowTh08, frame: int) -> pygame.Surface:
        if frame == 0:
            self._enter()
        self._sync(flow)
        surf = self._new_frame()
        for v in self._all:
            v.execute()
        for v in self._all:
            self._draw_vm(surf, v)
        # Mode 行的缩放值文字(原作无此元素 —— Mode 映射为 window_scale)
        self._key_text(surf, f"x{flow.config.window_scale}", *_MODE_SCALE_TEXT_POS)
        self._draw_help(surf, flow.help_text)
        return surf


class KeyConfigView(_OptionViewBase):
    """KeyConfig 画面渲染(OnUpdateKeyConfig 的键盘化显示侧对应物)。

    render(flow, frame): flow = KeyConfigFlowTh08; frame==0 = 进屏
    (重放 interrupt 4 进场动画, :1168)。键名直接绘字(不建 vms[57..74]
    的按钮号数字对 vm)。
    """

    def __init__(self, data_path) -> None:
        super().__init__(data_path)
        title_vms, title_bases = self._start_vms((_KC_TITLE_SCRIPT,))
        self._title, self._title_base = title_vms[0], title_bases[0]
        self._rows, self._row_bases = self._start_vms(_KC_ROW_SCRIPTS)
        self._slowshot, self._slowshot_bases = self._start_vms(_KC_SLOWSHOT_SCRIPTS)
        self._all = (self._title, *self._rows, *self._slowshot)

    def _enter(self) -> None:
        """进场 SetInterruptArray(4)(:1168)。"""
        for v in self._all:
            v.vm.pending_interrupt = 4

    def _sync(self, flow: KeyConfigFlowTh08) -> None:
        # 当前行(我们的 10 项 → 原作 12 行号, KEYCONFIG_ROW_MAP):
        # 选中亮/其余暗(:1217-1222), Pause/ShotSlow 行恒暗+置灰
        cur_row = KEYCONFIG_ROW_MAP[flow.cursor.current]
        for i, v in enumerate(self._rows):
            v.set_sprite(
                self._row_bases[i] if i == cur_row else self._row_bases[i] + 1
            )
            _tint(v, i in _KC_GRAY_ROWS)
        # ShotSlow Off/On(:1250-1256): 无对应物 —— Off 亮帧置灰, On 暗帧置灰
        self._slowshot[0].set_sprite(self._slowshot_bases[0])
        _tint(self._slowshot[0], True)
        self._slowshot[1].set_sprite(self._slowshot_bases[1] + 1)
        _tint(self._slowshot[1], True)

    def render(self, flow: KeyConfigFlowTh08, frame: int) -> pygame.Surface:
        if frame == 0:
            self._enter()
        self._sync(flow)
        surf = self._new_frame()
        for v in self._all:
            v.execute()
        for v in self._all:
            self._draw_vm(surf, v)
        # 键名区(原作是手柄按钮号数字对 vms[57..74], :1231-1248): 直接绘字
        for item in KEYCONFIG_ITEMS:
            if item in ("reset", "quit"):
                continue
            row = KEYCONFIG_ROW_MAP[item]
            if flow.capturing == item:
                text = _KC_CAPTURE_TEXT
            else:
                text = " / ".join(flow.config.keymap.get(item, []))
            self._key_text(surf, text, _KC_KEYNAME_X, _KC_ROW_Y[row] + 8)
        self._draw_help(surf, flow.help_text)
        return surf


__all__ = ["KeyConfigView", "OptionView"]
