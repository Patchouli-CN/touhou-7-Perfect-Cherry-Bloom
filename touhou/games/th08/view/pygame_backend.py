"""th08 的 pygame 渲染后端 —— 自持实现, 不进 register_renderer。

("pygame" 全局唯一名被 th07 的 pygame_backend 占用; th08 后端由
games/th08/view/impl.py 直接实例化, 第三后端出现时再抽象。)

与 th07 后端 (games/th07/view/pygame_backend.py) 的差异:
- 标题主菜单 + 难度/机体/Extra 选择 + Option/KeyConfig 是原作版贴图渲染
  (本包 title_view.TitleView: title01.anm 菜单 vm + title00.png 背景 + 白淡入/
  帮助行; select_view.DifficultySelectView/CharacterSelectView:
  select00.png 背景 + 难度项/头像/名牌 vm + 通关标记;
  option_view.OptionView/KeyConfigView: title01.anm 行标签/残机档/音量数字
  vm + 键名绘字; Music Room 是 music_view.MusicRoomView: music.jpg 背景 +
  music00.anm 主装饰 vm + 曲名/简介直接绘字), 加载失败回退文字菜单;
- 结算/暂停/续关覆盖层同样是文字版; 对话立绘/结局/弹字二期;
- 菜单 SE 自带小三件套(se_ok00/se_cancel00/se_select00, th08-ref
  SoundPlayer.hpp SoundIdx: SOUND_SELECT=10/BACK=11/MOVE_MENU=12),
  从 th08.dat 懒加载(edz 内层解密, games/th08/crypt.py), 静音容错。

输入采集/键位映射照抄 th07 后端(方向/WASD/Enter/Esc 硬编码防锁死,
确认/返回另随 keymap 的 shoot/bomb)。
"""

from __future__ import annotations

import io
import time

import pygame

from ....logger import logger as log
from ....paths import resolve_data_path
from ....schema.archive import open_archive
from ....engine.render import ACTION_NAMES, FrameInput
from ....engine.render import overlay as overlay_mod
from ....engine.health import HealthCenter
from ....engine.view.shake_view import ScreenShake
from ...th07.view.screens import MenuAction, Screen
from ..crypt import try_decrypt_from_table
from .hud_view import HudView
from .music_flow import MusicRoomFlowTh08
from .music_view import MusicRoomView
from .option_view import KeyConfigView, OptionView
from .select_view import CharacterSelectView, DifficultySelectView
from .sprite_view import GAME_H, GAME_W, GAME_X, GAME_Y, WIN_H, WIN_W, GameView
from .title_flow import (
    KEYCONFIG_ITEMS,
    OPTION_ITEMS,
    CharacterFlowTh08,
    KeyConfigFlowTh08,
    OptionFlowTh08,
    TITLE_MENU_ITEMS,
    TitleFlowTh08,
)
from .title_view import TitleView

TITLE_W, TITLE_H = 640, 480

_DEFAULT_CAPTION = "東方永夜抄 ～ Imperishable Night. ver 1.00d"

# 菜单基础键(硬编码, 防锁死): 方向/WASD 导航 + Enter 确认 + Esc 返回。
# 确认/返回另外跟随 keymap 的 shoot/bomb, 见 set_keymap。
# R 键 = Music Room 的 RESET(重播当前曲; TH_BUTTON_RESET 固定 DIK_R,
# th08-ref Global.cpp:802); 其余画面不认这个动作, 各 flow 自然忽略。
_BASE_MENU_KEYS = {
    pygame.K_UP: MenuAction.UP,
    pygame.K_w: MenuAction.UP,
    pygame.K_DOWN: MenuAction.DOWN,
    pygame.K_s: MenuAction.DOWN,
    pygame.K_LEFT: MenuAction.LEFT,
    pygame.K_a: MenuAction.LEFT,
    pygame.K_RIGHT: MenuAction.RIGHT,
    pygame.K_d: MenuAction.RIGHT,
    pygame.K_RETURN: MenuAction.CONFIRM,  # Enter 硬编码保留(防锁死)
    pygame.K_ESCAPE: MenuAction.BACK,  # Esc 菜单语义不动
    pygame.K_r: MenuAction.RESET,
}

# 手写 config.json 的键名别名(pygame 规范名格式)
_KEY_ALIASES = {
    **{f"kp{i}": f"[{i}]" for i in range(10)},
    "lshift": "left shift",
    "rshift": "right shift",
    "lctrl": "left ctrl",
    "rctrl": "right ctrl",
    "lalt": "left alt",
    "ralt": "right alt",
    "enter": "return",
    "esc": "escape",
}

# 菜单 SE 三件套(th08.dat 内 wav, edz 加密)
_MENU_SE_FILES = {
    "ok": "se_ok00.wav",
    "cancel": "se_cancel00.wav",
    "select": "se_select00.wav",
}

_TEXT_COLOR = (230, 225, 240)
_CURSOR_COLOR = (255, 220, 130)
_DIM_COLOR = (140, 140, 160)


def _key_code(name: str) -> "int | None":
    """pygame 键名 → 键码; 未知名(坏 config/手误)返回 None 跳过, 不炸。"""
    try:
        return pygame.key.key_code(_KEY_ALIASES.get(name, name))
    except ValueError:
        return None


def _load_font(size: int):
    # 字体模块可能已被 pygame.quit() 关掉(如 close 后重建/测试串台),
    # 守卫口径同 hud_view.py:460
    if not pygame.font.get_init():
        pygame.font.init()
    for name in ("Microsoft YaHei", "SimHei", "SimSun", None):
        try:
            return pygame.font.SysFont(name, size)
        except Exception:
            continue
    return pygame.font.Font(None, size)


class PygameTh08Renderer:
    """th08 的 pygame 渲染后端(窗口 + Surface 合成 + 键鼠事件采集)。

    构造签名对齐 registry 渲染后端契约 ``cls(data_path=None)``(但本类
    不注册); 单个画面渲染失败不拖垮应用(降级为底色填充)。
    """

    def __init__(self, data_path=None, *, caption: str = _DEFAULT_CAPTION) -> None:
        if not pygame.get_init():
            pygame.init()  # key_code/key.name 需要; open() 再 init 幂等
        self._data_path = resolve_data_path(data_path, game="th08")
        self._caption = caption
        self._scr = None  # 窗口 surface(open/resize 时建)
        self._clock = None  # 帧调度(open 时建)
        self._scale = 1
        self._health = None
        # 对局场景渲染器(begin_game 按机体/关卡建)
        self._game_view: GameView | None = None
        self._hud_view: HudView | None = None
        # 标题画面贴图视图(懒加载; 无数据/损坏回退文字菜单)
        self._title_view: TitleView | None = None
        self._title_view_broken = False
        # 难度/机体选择贴图视图(懒加载; 无数据/损坏回退文字菜单)
        self._difficulty_view: DifficultySelectView | None = None
        self._character_view: CharacterSelectView | None = None
        self._select_view_broken = False
        # Option/KeyConfig 贴图视图(懒加载; 无数据/损坏回退文字菜单)
        self._option_view: OptionView | None = None
        self._keyconfig_view: KeyConfigView | None = None
        self._option_view_broken = False
        # Music Room 贴图视图(懒加载; 无数据/损坏回退文字菜单)
        self._music_view: MusicRoomView | None = None
        self._music_view_broken = False
        # 输入映射(set_keymap 重建)
        self._action_codes: dict[str, list[int]] = {}
        self._menu_keys: dict[int, MenuAction] = {}
        # 游戏帧合成面(复用) + 震屏(engine/view/shake_view.py)
        self._frame_surf = None
        self._game_surf = None
        self._shake = ScreenShake()
        self._shake_consumed = None  # 已消费的 frame_shakes 所属 (id(game), frame)
        # 菜单 SE(懒加载三件套, 静音容错)
        self._menu_se_loaded = False
        self._menu_sounds: dict[str, pygame.mixer.Sound] = {}
        self._fonts: dict[int, pygame.font.Font] = {}

    # ---- 文字工具(菜单占位渲染) ----
    def _font(self, size: int):
        font = self._fonts.get(size)
        if font is None:
            font = _load_font(size)
            self._fonts[size] = font
        return font

    def _draw_menu_list(
        self, surf, title: str, items, cursor: int, *, hint: str = ""
    ) -> None:
        """文字版菜单: 标题 + 纵列选项 + 光标高亮(贴图版二期)。"""
        surf.fill((12, 10, 28))
        font = self._font(32)
        surf.blit(font.render(title, True, _TEXT_COLOR), (48, 40))
        item_font = self._font(26)
        for i, it in enumerate(items):
            color = _CURSOR_COLOR if i == cursor else _TEXT_COLOR
            prefix = "→ " if i == cursor else "  "
            surf.blit(
                item_font.render(prefix + str(it), True, color), (72, 110 + i * 34)
            )
        if hint:
            surf.blit(self._font(20).render(hint, True, _DIM_COLOR), (48, 430))

    # ---- 窗口生命周期 / 帧调度 ----
    def open(self, *, scale: int) -> None:
        pygame.init()
        try:  # 没声卡也能跑, 静音即可
            pygame.mixer.init()
            log.info("mixer 初始化成功")
        except pygame.error as e:
            log.warning("mixer 初始化失败(静音运行): {}", e)
        self._scale = scale
        self._scr = pygame.display.set_mode((TITLE_W * scale, TITLE_H * scale))
        pygame.display.set_caption(self._caption)
        self._clock = pygame.time.Clock()
        # 渲染压力告警收进引擎层 engine.health
        self._health = HealthCenter("renderer")

    def close(self) -> None:
        pygame.quit()

    def resize(self, screen: Screen, scale: int) -> None:
        """按当前场景尺寸 × 缩放重设窗口(headless 无窗口时跳过)。

        th08 窗口一律 640x480(标题/对局同尺寸), 保留接口对齐协议。"""
        self._scale = scale
        if not (pygame.display.get_init() and pygame.display.get_surface() is not None):
            return
        self._scr = pygame.display.set_mode((TITLE_W * scale, TITLE_H * scale))

    def present(self) -> None:
        if pygame.display.get_init():
            pygame.display.flip()
        if self._clock is not None:
            elapsed = self._clock.tick(60)
            if self._health is not None:
                self._health.tick(elapsed)

    # ---- 输入采集 ----
    def set_keymap(self, keymap) -> None:
        """按当前 config.keymap 重建动作键码表与菜单键表(语义同 th07 后端)。"""
        codes: dict[str, list[int]] = {}
        for action, names in keymap.items():
            lst = []
            for n in names:
                c = _key_code(n)
                if c is not None and c not in lst:
                    lst.append(c)
            codes[action] = lst
        self._action_codes = codes
        m = dict(_BASE_MENU_KEYS)
        for c in codes.get("bomb", []):
            m.setdefault(c, MenuAction.BACK)
        for c in codes.get("shoot", []):
            m.setdefault(c, MenuAction.CONFIRM)
        for c in codes.get("skip", []):
            # skip 键(Ctrl) = Music Room 的 SKIP(TH_BUTTON_SKIP 淡出,
            # MusicRoom.cpp:228-230); 其余画面不认, 各 flow 自然忽略
            m.setdefault(c, MenuAction.SKIP)
        self._menu_keys = m

    def held_actions(self, pressed) -> frozenset[str]:
        """按键状态面(pygame.key.get_pressed() 同构) → 按住的动作名集合。"""
        return frozenset(
            a
            for a in ACTION_NAMES
            if any(pressed[c] for c in self._action_codes.get(a, ()))
        )

    def poll_input(self, *, capturing: bool = False) -> FrameInput:
        menu_actions: list[MenuAction] = []
        advance = esc = quit_req = False
        captured = None
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                quit_req = True
            elif ev.type == pygame.KEYDOWN:
                log.trace("KEYDOWN {}", pygame.key.name(ev.key))
                if capturing and captured is None:
                    captured = pygame.key.name(ev.key)
                elif ev.key in self._menu_keys:
                    menu_actions.append(self._menu_keys[ev.key])
                    if ev.key in self._action_codes.get("shoot", ()):
                        # 对话推进 = 射击键(对话中射击被门控)
                        advance = True
                if ev.key == pygame.K_ESCAPE:
                    esc = True  # 游戏内暂停开关(固定 Esc, 不动)
            elif ev.type == pygame.KEYUP:
                log.trace("KEYUP   {}", pygame.key.name(ev.key))
        return FrameInput(
            quit=quit_req,
            menu_actions=tuple(menu_actions),
            advance=advance,
            esc=esc,
            captured_key=captured,
            held=self.held_actions(pygame.key.get_pressed()),
        )

    # ---- 合成辅助 ----
    def _target(self):
        """窗口 surface; 未 open 时取现有 display 面(测试), 再无则离屏兜底。"""
        if self._scr is not None:
            return self._scr
        if pygame.display.get_init():
            scr = pygame.display.get_surface()
            if scr is not None:
                return scr
        return pygame.Surface((TITLE_W, TITLE_H))  # 无窗口兜底(离屏渲染)

    def _blit_scaled(self, surf) -> None:
        """逻辑尺寸面 → 窗口(整帧缩放)。"""
        scr = self._target()
        scr.blit(pygame.transform.scale(surf, scr.get_size()), (0, 0))

    # ---- 菜单系场景 ----
    def _ensure_title_view(self) -> "TitleView | None":
        """标题贴图视图(懒加载一次; 失败永久回退文字菜单, 不逐帧重试)。"""
        if self._title_view is None and not self._title_view_broken:
            try:
                self._title_view = TitleView(self._data_path)
            except Exception as e:
                log.warning("标题贴图视图加载失败, 回退文字菜单: {}", e)
                self._title_view_broken = True
        return self._title_view

    def render_title(
        self,
        flow: TitleFlowTh08,
        frame: int,
        *,
        show_unimplemented: bool = False,
        fade_frame: "int | None" = None,
    ) -> None:
        """标题主菜单: 原作版贴图渲染(title_view), 失败/无数据回退文字菜单。"""
        view = self._ensure_title_view()
        if view is not None:
            try:
                surf = view.render(
                    flow, show_unimplemented=show_unimplemented, fade_frame=fade_frame
                )
                self._blit_scaled(surf)
                return
            except Exception:
                log.exception("标题画面渲染异常(本帧降级为文字菜单)")
        surf = pygame.Surface((TITLE_W, TITLE_H))
        hint = "(未实装 — 二期)" if show_unimplemented else ""
        self._draw_menu_list(
            surf,
            "東方永夜抄 ～ Imperishable Night",
            TITLE_MENU_ITEMS,
            flow.cursor.index,
            hint=hint,
        )
        self._blit_scaled(surf)

    def render_difficulty(self, cursor: int, *, items=(), frame: int = 0) -> None:
        """难度选择: 原作版贴图渲染(select_view), 失败/无数据回退文字菜单。"""
        view = self._ensure_select_views()[0]
        if view is not None:
            try:
                self._blit_scaled(view.render(False, cursor, frame))
                return
            except Exception:
                log.exception("难度选择画面渲染异常(本帧降级为文字菜单)")
        surf = pygame.Surface((TITLE_W, TITLE_H))
        self._draw_menu_list(surf, "Select Difficulty", items, cursor)
        self._blit_scaled(surf)

    def render_character(
        self, flow: CharacterFlowTh08, *, completion: int | None = None, frame: int = 0
    ) -> None:
        """机体选择: 原作版贴图渲染(select_view), 失败/无数据回退文字菜单。"""
        view = self._ensure_select_views()[1]
        if view is not None:
            try:
                self._blit_scaled(view.render(flow, completion, frame))
                return
            except Exception:
                log.exception("机体选择画面渲染异常(本帧降级为文字菜单)")
        surf = pygame.Surface((TITLE_W, TITLE_H))
        self._draw_menu_list(
            surf, "Select Character", flow.cursor.items, flow.cursor.index
        )
        self._blit_scaled(surf)

    def render_extra(self, cursor: int, *, items=(), frame: int = 0) -> None:
        """Extra Start 流: 原作的 DifficultySelectExtra 单项画面(同一视图)。"""
        view = self._ensure_select_views()[0]
        if view is not None:
            try:
                self._blit_scaled(view.render(True, cursor, frame))
                return
            except Exception:
                log.exception("Extra 难度画面渲染异常(本帧降级为文字菜单)")
        surf = pygame.Surface((TITLE_W, TITLE_H))
        self._draw_menu_list(surf, "Extra Start", items, cursor)
        self._blit_scaled(surf)

    def _ensure_select_views(
        self,
    ) -> "tuple[DifficultySelectView | None, CharacterSelectView | None]":
        """难度/机体选择贴图视图(懒加载一次; 失败永久回退文字菜单, 不逐帧
        重试 —— 同 _ensure_title_view 口径)。两视图同源资源, 一起建。"""
        if self._difficulty_view is None and not self._select_view_broken:
            try:
                self._difficulty_view = DifficultySelectView(self._data_path)
                self._character_view = CharacterSelectView(self._data_path)
            except Exception as e:
                log.warning("难度/机体选择贴图视图加载失败, 回退文字菜单: {}", e)
                self._difficulty_view = None
                self._character_view = None
                self._select_view_broken = True
        return self._difficulty_view, self._character_view

    def _ensure_option_views(
        self,
    ) -> "tuple[OptionView | None, KeyConfigView | None]":
        """Option/KeyConfig 贴图视图(懒加载一次; 失败永久回退文字菜单,
        同 _ensure_title_view 口径)。两视图同源资源(title01.anm), 一起建。"""
        if self._option_view is None and not self._option_view_broken:
            try:
                self._option_view = OptionView(self._data_path)
                self._keyconfig_view = KeyConfigView(self._data_path)
            except Exception as e:
                log.warning("Option/KeyConfig 贴图视图加载失败, 回退文字菜单: {}", e)
                self._option_view = None
                self._keyconfig_view = None
                self._option_view_broken = True
        return self._option_view, self._keyconfig_view

    @staticmethod
    def _option_fallback_items(flow: OptionFlowTh08) -> "list[str]":
        """Option 文字回退的 "项: 值" 名单(锁定行标 [N/A])。"""
        cfg = flow.config
        values = {
            0: str(cfg.initial_lives),
            2: cfg.bgm_source.upper(),
            3: str(cfg.bgm_volume),
            4: str(cfg.se_volume),
            5: f"Window x{cfg.window_scale}",
        }
        items = []
        for i, label in enumerate(OPTION_ITEMS):
            if flow.locked(i):
                items.append(f"{label}: [N/A]")
            elif i in values:
                items.append(f"{label}: {values[i]}")
            else:
                items.append(label)
        return items

    def render_option(self, flow: OptionFlowTh08, *, frame: int = 0) -> None:
        """Option 画面: 原作版贴图渲染(option_view), 失败/无数据回退文字菜单。"""
        view = self._ensure_option_views()[0]
        if view is not None:
            try:
                self._blit_scaled(view.render(flow, frame))
                return
            except Exception:
                log.exception("Option 画面渲染异常(本帧降级为文字菜单)")
        surf = pygame.Surface((TITLE_W, TITLE_H))
        self._draw_menu_list(
            surf, "Option", self._option_fallback_items(flow), flow.cursor.index
        )
        self._blit_scaled(surf)

    def render_keyconfig(self, flow: KeyConfigFlowTh08, *, frame: int = 0) -> None:
        """KeyConfig 画面: 原作版贴图渲染(option_view), 失败/无数据回退文字菜单。"""
        view = self._ensure_option_views()[1]
        if view is not None:
            try:
                self._blit_scaled(view.render(flow, frame))
                return
            except Exception:
                log.exception("KeyConfig 画面渲染异常(本帧降级为文字菜单)")
        items = [
            item
            if item in ("reset", "quit")
            else f"{item}: {' / '.join(flow.config.keymap.get(item, ()))}"
            for item in KEYCONFIG_ITEMS
        ]
        hint = "<press a key>" if flow.capturing is not None else ""
        surf = pygame.Surface((TITLE_W, TITLE_H))
        self._draw_menu_list(surf, "KeyConfig", items, flow.cursor.index, hint=hint)
        self._blit_scaled(surf)

    # ---- Music Room(music_view 贴图渲染; 失败回退文字列表) ----
    def _ensure_music_view(self) -> "MusicRoomView | None":
        """Music Room 贴图视图(懒加载一次; 失败永久回退文字菜单,
        同 _ensure_title_view 口径)。"""
        if self._music_view is None and not self._music_view_broken:
            try:
                self._music_view = MusicRoomView(self._data_path)
            except Exception as e:
                log.warning("Music Room 贴图视图加载失败, 回退文字菜单: {}", e)
                self._music_view_broken = True
        return self._music_view

    def render_music_room(self, flow: MusicRoomFlowTh08, frame: int = 0) -> None:
        """Music Room: 原作版渲染(music_view), 失败/无数据回退文字列表。"""
        view = self._ensure_music_view()
        if view is not None:
            try:
                self._blit_scaled(view.render(flow, frame))
                return
            except Exception:
                log.exception("Music Room 渲染异常(本帧降级为文字列表)")
        surf = pygame.Surface((TITLE_W, TITLE_H))
        n = len(flow.tracks)
        items = [
            flow.display_title(i)
            for i in range(flow.listing_offset, min(flow.listing_offset + 10, n))
        ]
        self._draw_menu_list(
            surf, "Music Room", items, flow.cursor - flow.listing_offset
        )
        self._blit_scaled(surf)

    # ---- 对局场景 ----
    def begin_game(self, game, *, character: int) -> None:
        """开局/重开: 按机体与当前关建本局渲染资源(失败降级, 不拖垮游戏)。"""
        t0 = time.perf_counter()
        stage = getattr(game, "stage_no", 1)
        try:
            self._game_view = GameView(
                self._data_path, character=character, stage=stage
            )
        except Exception:
            log.exception("战斗贴图渲染器初始化失败(降级为简笔渲染)")
            self._game_view = None
        try:
            self._hud_view = HudView(self._data_path)
        except Exception:
            log.exception("HUD 渲染器初始化失败(降级为无 HUD)")
            self._hud_view = None
        ms = (time.perf_counter() - t0) * 1000
        if ms >= 30.0:
            log.debug("开局渲染资源装配耗时 {:.1f}ms (stage={})", ms, stage)

    def render_game(self, game) -> None:
        # 窗口布局 640x480: 游戏区 384x448 渲染后 blit 到 (32,16),
        # 右栏 HUD 面板; surface 复用避免每帧分配
        if self._frame_surf is None:
            self._frame_surf = pygame.Surface((WIN_W, WIN_H))
            self._game_surf = pygame.Surface((GAME_W, GAME_H))
        frame, surf = self._frame_surf, self._game_surf
        frame.fill((6, 8, 20))
        # 右栏 HUD 先画(窗口框/标签/数值; 不覆盖游戏区)
        if self._hud_view is not None:
            try:
                self._hud_view.render(frame, game)
            except Exception:
                pass  # 渲染异常不拖垮游戏循环
        if self._game_view is not None:
            try:
                self._game_view.render(surf, game)
            except Exception:
                surf.fill((8, 12, 30))  # 渲染异常不拖垮游戏循环
                log.exception("战斗画面渲染异常(本帧降级)")
        else:
            surf.fill((8, 12, 30))
        # 过关结算面板(文字版, 半透明叠加)
        sr = getattr(game, "stage_results", None)
        if sr is not None:
            self._render_stage_results(surf, sr)
        # 对话覆盖层(一期: 纯文字; 立绘二期)
        vm = getattr(game, "msg_vm", None)
        if vm is not None and getattr(vm, "active", False):
            self._render_dialog(surf, vm)
        # 震屏: 消费引擎帧末快照 frame_shakes, 整帧位移只动游戏区 ——
        # HUD 不晃。快照按 (game, frame) 去重: 暂停/续关菜单冻结 tick 时
        # 帧号不变, 不重复注册同一帧的事件。
        _shake_key = (id(game), getattr(game, "frame", -1))
        if _shake_key != self._shake_consumed:
            self._shake_consumed = _shake_key
            for _ev in getattr(game, "frame_shakes", ()):
                self._shake.register(*_ev)
        # Mod 覆盖层(engine/render/overlay 的立即模式命令, 同 th07 后端)
        _cmds = overlay_mod.SINK.drain()
        if _cmds:
            try:
                self._render_overlay(surf, _cmds)
            except Exception:
                pass  # 渲染异常不拖垮游戏循环
        dx, dy = self._shake.tick()
        frame.blit(surf, (GAME_X + dx, GAME_Y + dy))
        # 时刻表盘 + 妖率计(GUI 层, 原版画在 640x480 窗口 framebuffer)
        if self._hud_view is not None:
            try:
                self._hud_view.render_overlay(frame, game)
            except Exception:
                pass
        # FPS 显示(左下 HUD 区, 同 th07 惯例)
        if self._hud_view is not None and self._clock is not None:
            try:
                self._hud_view.render_fps(frame, self._clock.get_fps())
            except Exception:
                pass
        self._blit_scaled(frame)

    def _render_stage_results(self, surf, sr: dict) -> None:
        """过关结算面板(文字版; 贴图版二期)。sr = world._on_stage_results 快照。"""
        try:
            box = pygame.Surface((280, 240), pygame.SRCALPHA)
            box.fill((10, 10, 40, 220))
            pygame.draw.rect(box, (200, 200, 220, 255), box.get_rect(), 1)
            font = self._font(20)
            box.blit(
                font.render(
                    f"Stage {sr.get('stage', '?')} Clear!", True, _CURSOR_COLOR
                ),
                (16, 12),
            )
            y = 44
            for label, value in sr.get("lines", []):
                box.blit(
                    font.render(f"{label:<10}{value:>12}", True, _TEXT_COLOR), (16, y)
                )
                y += 24
            box.blit(
                font.render(
                    f"{'Total':<10}{sr.get('total', 0):>12}", True, _CURSOR_COLOR
                ),
                (16, y + 4),
            )
            surf.blit(box, ((GAME_W - 280) // 2, (GAME_H - 240) // 2))
        except Exception:
            pass  # 渲染失败不拖垮游戏循环

    def _render_dialog(self, surf, vm) -> None:
        """对话覆盖层(一期纯文字: 对话框 + 打字机文本; 立绘/名字二期)。"""
        try:
            lines = [
                ln.shown_text
                for ln in getattr(vm, "dialogue_lines", ())
                if ln.visible and ln.shown_text
            ]
            if not lines:
                return
            box = pygame.Surface((GAME_W - 16, 72), pygame.SRCALPHA)
            box.fill((8, 8, 40, 210))
            pygame.draw.rect(box, (180, 180, 210, 255), box.get_rect(), 1)
            font = self._font(18)
            for i, text in enumerate(lines[:2]):
                box.blit(font.render(str(text), True, _TEXT_COLOR), (10, 8 + i * 26))
            surf.blit(box, (8, GAME_H - 84))
        except Exception:
            pass

    def _render_overlay(self, surf, cmds) -> None:
        """把本帧 Mod 覆盖层命令画到游戏区 surface(语义同 th07 后端)。"""
        for cmd in cmds:
            if isinstance(cmd, overlay_mod.OverlayLine):
                pygame.draw.line(
                    surf, cmd.color, (cmd.x1, cmd.y1), (cmd.x2, cmd.y2), cmd.width
                )
            elif isinstance(cmd, overlay_mod.OverlayCircle):
                pygame.draw.circle(
                    surf, cmd.color, (cmd.x, cmd.y), cmd.radius, cmd.width
                )
            elif isinstance(cmd, overlay_mod.OverlayPolyline):
                if len(cmd.points) >= 2:
                    pygame.draw.lines(
                        surf, cmd.color, cmd.closed, cmd.points, cmd.width
                    )
            elif isinstance(cmd, overlay_mod.OverlayText):
                surf.blit(
                    self._font(cmd.size).render(cmd.content, True, cmd.color),
                    (cmd.x, cmd.y),
                )

    def render_pause(self, game, cursor: int, *, hint=None, confirm=None) -> None:
        """暂停: 冻结画面(render_game 同图) + 半透明暂停面板。"""
        self.render_game(game)
        overlay = pygame.Surface((WIN_W, WIN_H), pygame.SRCALPHA)
        try:
            items = ["Resume", "Retry", "Quit to Title"]
            box = pygame.Surface((260, 170), pygame.SRCALPHA)
            box.fill((16, 16, 48, 230))
            pygame.draw.rect(box, (200, 200, 220, 255), box.get_rect(), 1)
            font = self._font(24)
            if confirm is not None:
                # 二次确认态(Quit to Title): 只有 Yes/No, 默认停 No
                label, ci = confirm
                box.blit(font.render(f"{label}?", True, _TEXT_COLOR), (16, 12))
                for j, yn in enumerate(("Yes", "No")):
                    color = _CURSOR_COLOR if j == ci else _DIM_COLOR
                    box.blit(font.render(yn, True, color), (40 + j * 110, 72))
            else:
                box.blit(font.render("Paused", True, _TEXT_COLOR), (16, 12))
                for j, it in enumerate(items):
                    color = _CURSOR_COLOR if j == cursor else _TEXT_COLOR
                    box.blit(font.render(it, True, color), (32, 48 + j * 32))
            overlay.blit(box, ((WIN_W - 260) // 2, (WIN_H - 170) // 2))
        except Exception:
            pass  # 渲染失败不拖垮游戏循环
        self._blit_scaled(overlay)

    def render_continue(self, game, cursor: int, retries_left: int) -> None:
        """GameOver 续关菜单(冻结画面 + Continue? Yes/No 覆盖层)。"""
        self.render_game(game)
        overlay = pygame.Surface((WIN_W, WIN_H), pygame.SRCALPHA)
        try:
            box = pygame.Surface((300, 140), pygame.SRCALPHA)
            box.fill((16, 16, 48, 230))
            pygame.draw.rect(box, (200, 200, 220, 255), box.get_rect(), 1)
            font = self._font(24)
            box.blit(
                font.render(f"Continue? (残 {retries_left})", True, _TEXT_COLOR),
                (16, 12),
            )
            for j, yn in enumerate(("Yes", "No")):
                color = _CURSOR_COLOR if j == cursor else _DIM_COLOR
                box.blit(font.render(yn, True, color), (60 + j * 120, 72))
            overlay.blit(box, ((WIN_W - 300) // 2, (WIN_H - 140) // 2))
        except Exception:
            pass
        self._blit_scaled(overlay)

    # ---- 结算(文字版一期; 入榜名字输入二期) ----
    def render_result(self, result: dict, frame: int, *, store=None) -> None:
        surf = pygame.Surface((TITLE_W, TITLE_H))
        try:
            surf.fill((12, 10, 28))
            font = self._font(24)
            small = self._font(20)
            cleared = result.get("cleared", False)
            title = "All Clear!" if cleared else "Game Over"
            if result.get("bad_ending"):
                title = "Bad Ending"
            surf.blit(font.render(title, True, _CURSOR_COLOR), (48, 40))
            rows = (
                ("Score", result.get("score", 0)),
                ("Stage", result.get("stage", "?")),
                ("Deaths", result.get("deaths", 0)),
                ("Bombs", result.get("bombs", 0)),
                ("Spellcards", result.get("spellcards", 0)),
                ("Graze", result.get("graze", 0)),
                ("Time Orbs", result.get("time_orbs", 0)),
                ("Rating", result.get("rating", 0)),
            )
            y = 90
            for label, value in rows:
                surf.blit(
                    small.render(f"{label:<14}{value}", True, _TEXT_COLOR), (64, y)
                )
                y += 28
            surf.blit(small.render("Z: 保存并回标题", True, _DIM_COLOR), (48, 430))
        except Exception:
            surf.fill((10, 10, 30))  # 渲染失败不拖垮流程
        self._blit_scaled(surf)

    # ---- 菜单 SE(th08.dat 三件套, 懒加载 + 静音容错) ----
    def _ensure_menu_se(self) -> None:
        if self._menu_se_loaded:
            return
        self._menu_se_loaded = True
        if not pygame.mixer.get_init():
            return
        try:
            arc = open_archive(self._data_path)
            for key, name in _MENU_SE_FILES.items():
                data = try_decrypt_from_table(arc.load(name))
                self._menu_sounds[key] = pygame.mixer.Sound(file=io.BytesIO(data))
        except Exception as e:
            log.warning("菜单 SE 加载失败, 静音运行: {}", e)
            self._menu_sounds = {}

    def play_menu_se(self, key: str) -> None:
        """菜单音效("select"/"ok"/"cancel"); 未加载/无声卡静音跳过。"""
        self._ensure_menu_se()
        snd = self._menu_sounds.get(key)
        if snd is not None:
            try:
                snd.play()
            except pygame.error:
                pass
