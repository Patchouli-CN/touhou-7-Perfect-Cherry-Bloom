"""pygame 渲染后端 —— Renderer 协议(engine/render/__init__.py)的默认实现。

把原 GameApp 的绘制/窗口/输入采集代码原样收拢到这里: 各 view
(title/select/option/hud/sprite…)仍是"往 surface 上画"的函数集合,
本类把它们组织成协议方法; 场景流/状态机在 GameApp(view/impl.py, 应用壳)。

构造契约(registry.register_renderer): ``PygameRenderer(data_path=None)``。
单个画面渲染失败不拖垮应用(降级为底色填充, 沿用原 GameApp 容错)。
"""

from __future__ import annotations

import time

import pygame

from ....logger import logger as log
from ....paths import resolve_data_path
from ....registry import register_renderer
from ....engine.render import ACTION_NAMES, EndingFrame, FrameInput, Renderer
from ....engine.render import overlay as overlay_mod
from ....engine.health import HealthCenter
from .continue_view import ContinueView
from .dialog_view import DialogueView
from .ending_view import EndingView
from .hud_view import HudView
from .musicroom_view import MusicRoomView
from .option_view import OptionView
from .playerdata_view import PlayerDataView
from .popup_view import PopupView
from .replay_view import ReplayView
from .result_view import ResultScreen
from .screens import MenuAction, Screen
from .select_view import SelectView
from ....engine.view.shake_view import ScreenShake
from .sprite_view import GAME_H, GAME_W, GAME_X, GAME_Y, WIN_H, WIN_W, GameView
from .stage_results_view import StageResultsView
from .title_view import TITLE_H, TITLE_W, TitleScreen

_DEFAULT_CAPTION = "東方妖々夢 ～ Perfect Cherry Blossom. ver 1.00b"

# 菜单基础键(硬编码, 防锁死): 方向/WASD 导航 + Enter 确认 + Esc 返回。
# Enter/Esc 不随 keymap 改动 —— 用户把确认键改丢后菜单仍可用(任务约束)。
# 确认/返回另外跟随 keymap 的 shoot/bomb(原版 TH_BUTTON_SELECTMENU=
# ENTER|SHOOT, TH_BUTTON_RETURNMENU=MENU|BOMB, Controller.hpp),
# 见 set_keymap。
_BASE_MENU_KEYS = {
    pygame.K_UP: MenuAction.UP,
    pygame.K_w: MenuAction.UP,
    pygame.K_DOWN: MenuAction.DOWN,
    pygame.K_s: MenuAction.DOWN,
    pygame.K_LEFT: MenuAction.LEFT,  # Option 调值(游戏内移动走 keys 元组, 不冲突)
    pygame.K_a: MenuAction.LEFT,
    pygame.K_RIGHT: MenuAction.RIGHT,
    pygame.K_d: MenuAction.RIGHT,
    pygame.K_RETURN: MenuAction.CONFIRM,  # Enter 硬编码保留(防锁死)
    pygame.K_ESCAPE: MenuAction.BACK,  # Esc 菜单语义不动
}

# 手写 config.json 的键名别名(pygame 规范名: 小键盘 "[0]"、修饰键
# "left shift" 等 —— pygame.key.name 的输出格式)
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


def _key_code(name: str) -> "int | None":
    """pygame 键名 → 键码; 未知名(坏 config/手误)返回 None 跳过, 不炸。"""
    try:
        return pygame.key.key_code(_KEY_ALIASES.get(name, name))
    except ValueError:
        return None


def _load_font(size: int):
    for name in ("Microsoft YaHei", "SimHei", "SimSun", None):
        try:
            return pygame.font.SysFont(name, size)
        except Exception:
            continue
    return pygame.font.Font(None, size)


@register_renderer("pygame")
class PygameRenderer:
    """Renderer 协议的 pygame 实现(窗口 + Surface 合成 + 键鼠事件采集)。"""

    def __init__(self, data_path=None, *, caption: str = _DEFAULT_CAPTION) -> None:
        if not pygame.get_init():
            pygame.init()  # key_code/key.name 需要(未 init 会告警); open() 再 init 幂等
        self._data_path = resolve_data_path(data_path)
        self._caption = caption
        self._scr = None  # 窗口 surface(open/resize 时建; 测试可仅 set_mode)
        self._clock = None  # 帧调度(open 时建)
        self._scale = 1
        # 常驻菜单场景渲染器(资源懒加载, 各 view 自带容错)
        self._title = TitleScreen(self._data_path)
        self._select_view = SelectView(self._data_path)
        self._option_view = OptionView(self._data_path)
        self._mr_view = MusicRoomView(self._data_path)
        self._rp_view = ReplayView(self._data_path)
        self._result_view = ResultScreen(self._data_path)
        self._ending_view = EndingView(self._data_path)
        self._stage_results_view = StageResultsView()
        self._playerdata_view = PlayerDataView(self._data_path)
        self._continue_view = ContinueView(self._data_path)
        # 对局场景渲染器(begin_game 按机体/关卡建; 换关重建对话视图)
        self._dialog_view = None
        self._dialog_stage = 0
        self._game_view = None
        self._hud_view = None
        self._popup_view = None
        # 输入映射(set_keymap 重建)
        self._action_codes: dict[str, list[int]] = {}
        self._menu_keys: dict[int, MenuAction] = {}
        # 游戏帧合成面(复用) + 震屏(view 侧衰减, shake_view.py)
        self._frame_surf = None
        self._game_surf = None
        self._shake = ScreenShake()
        self._shake_consumed = None  # 已消费的 frame_shakes 所属 (id(game), frame)
        # Mod 覆盖层字号缓存(engine/render/overlay 命令的 text 字号 → Font)
        self._overlay_fonts: dict[int, pygame.font.Font] = {}

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
        # 渲染压力告警(Minecraft "Can't keep up!" 梗)收进引擎层 engine.health
        self._health = HealthCenter("renderer")

    def close(self) -> None:
        pygame.quit()

    def resize(self, screen: Screen, scale: int) -> None:
        """按当前场景尺寸 × 缩放重设窗口(测试 headless 无窗口时跳过)。"""
        self._scale = scale
        if not (pygame.display.get_init() and pygame.display.get_surface() is not None):
            return
        if screen == Screen.PLAYING:
            self._scr = pygame.display.set_mode((WIN_W * scale, WIN_H * scale))
        else:
            self._scr = pygame.display.set_mode((TITLE_W * scale, TITLE_H * scale))

    def present(self) -> None:
        if pygame.display.get_init():
            pygame.display.flip()
        if self._clock is not None:
            elapsed = self._clock.tick(60)
            self._health.tick(elapsed)

    # ---- 输入采集 ----
    def set_keymap(self, keymap) -> None:
        """按当前 config.keymap 重建动作键码表与菜单键表。

        菜单: 方向/WASD/Enter/Esc 硬编码(_BASE_MENU_KEYS, 防锁死 ——
        改丢确认键后 Enter 仍可用, Esc 暂停/返回不动); 确认/返回另随
        keymap 的 shoot/bomb(原版 SELECTMENU=ENTER|SHOOT /
        RETURNMENU=MENU|BOMB)。setdefault: 动作绑到导航键上时导航不丢。
        """
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
                    # KeyConfig "按新键"捕获: 吃掉该键, 不进菜单键表
                    captured = pygame.key.name(ev.key)
                elif ev.key in self._menu_keys:
                    menu_actions.append(self._menu_keys[ev.key])
                    if ev.key in self._action_codes.get("shoot", ()):
                        # 对话推进 = 射击键(对话中射击被门控; 小键盘0 为 IME 备用)
                        advance = True
                if ev.key == pygame.K_ESCAPE:
                    esc = True  # 游戏内暂停开关(固定 Esc, 不动)
            elif ev.type == pygame.KEYUP:
                log.trace("KEYUP   {}", pygame.key.name(ev.key))
            elif ev.type in (pygame.WINDOWFOCUSLOST, pygame.WINDOWFOCUSGAINED):
                log.debug("窗口焦点变化: {}", pygame.event.event_name(ev.type))
            elif ev.type == pygame.ACTIVEEVENT and ev.gain == 0:
                log.debug("窗口失去激活 (ACTIVEEVENT gain=0)")
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

    def _render_menu_surf(self, draw) -> None:
        """菜单场景通用骨架: 建 640x480 面 → draw(surf)(失败兜底填色) → 上屏。"""
        surf = pygame.Surface((TITLE_W, TITLE_H))
        try:
            draw(surf)
        except Exception:
            surf.fill((10, 10, 30))  # 渲染失败不拖垮菜单(原 GameApp 容错)
        self._blit_scaled(surf)

    # ---- 菜单系场景 ----
    def render_title(
        self, cursor: int, frame: int, *, show_unimplemented: bool = False
    ) -> None:
        surf = pygame.Surface((TITLE_W, TITLE_H))
        self._title.render(surf, cursor, frame, show_unimplemented=show_unimplemented)
        self._blit_scaled(surf)

    def render_difficulty(self, cursor: int) -> None:
        self._render_menu_surf(lambda s: self._select_view.render_difficulty(s, cursor))

    def render_character(self, cursor: int) -> None:
        self._render_menu_surf(lambda s: self._select_view.render_character(s, cursor))

    def render_practice_stage(
        self, cursor: int, max_stage: int, *, difficulty: str, character: str
    ) -> None:
        self._render_menu_surf(
            lambda s: self._select_view.render_practice_stage(
                s, cursor, max_stage, difficulty=difficulty, character=character
            )
        )

    def render_extra(self, cursor: int) -> None:
        self._render_menu_surf(lambda s: self._select_view.render_extra(s, cursor))

    def render_option(self, flow) -> None:
        self._render_menu_surf(lambda s: self._option_view.render(s, flow))

    def render_keyconfig(self, flow) -> None:
        self._render_menu_surf(lambda s: self._option_view.render_keyconfig(s, flow))

    def render_player_data(self, flow, store, frame: int) -> None:
        self._render_menu_surf(
            lambda s: self._playerdata_view.render(s, flow, store, frame)
        )

    def render_music_room(self, flow, frame: int) -> None:
        self._render_menu_surf(lambda s: self._mr_view.render(s, flow, frame))

    def render_replay_menu(self, flow, frame: int) -> None:
        self._render_menu_surf(lambda s: self._rp_view.render(s, flow, frame))

    def render_result(
        self, result: dict, frame: int, *, store, name_entry, replay_save=None
    ) -> None:
        surf = pygame.Surface((TITLE_W, TITLE_H))
        self._result_view.render(
            surf, result, frame, store=store, name_entry=name_entry
        )
        if replay_save is not None:
            # Save Replay 覆盖层(ResultScreen.cpp HandleReplaySaveKeyboard 简化)
            try:
                mode, cursor, msg = replay_save
                box = pygame.Surface((360, 120), pygame.SRCALPHA)
                box.fill((16, 16, 48, 230))
                pygame.draw.rect(box, (200, 200, 220, 255), box.get_rect(), 1)
                font = _load_font(24)
                if mode == "ask":
                    box.blit(
                        font.render("Save Replay?", True, (255, 255, 255)), (120, 16)
                    )
                    for j, yn in enumerate(("Yes", "No")):
                        color = (255, 255, 255) if j == cursor else (140, 140, 150)
                        box.blit(font.render(yn, True, color), (110 + j * 110, 64))
                else:  # "saved": 已存确认
                    box.blit(
                        font.render(f"Saved {msg}", True, (150, 255, 180)), (24, 32)
                    )
                    box.blit(font.render("Z: OK", True, (200, 200, 220)), (150, 72))
                surf.blit(box, ((TITLE_W - 360) // 2, (TITLE_H - 120) // 2))
            except Exception:
                pass  # 渲染失败不拖垮游戏循环
        self._blit_scaled(surf)

    def render_ending(self, ending, frame: int) -> EndingFrame:
        surf = pygame.Surface((TITLE_W, TITLE_H))
        self._ending_view.render(surf, ending, frame)
        self._blit_scaled(surf)
        # 脚本内音乐事件(@m/@M)随帧返回, 由应用壳消费给 SoundPlayer
        music = tuple(self._ending_view.pending_music)
        self._ending_view.pending_music = []
        return EndingFrame(finished=self._ending_view.finished, music=music)

    # ---- 对局场景 ----
    def begin_game(self, game, *, character: int) -> None:
        """开局/重开: 按机体与当前关建本局渲染资源(失败降级, 不拖垮游戏)。"""
        t0 = time.perf_counter()
        stage = getattr(game, "stage_no", 1)
        # 对话渲染器(角色立绘按 character//2, Boss 立绘按关卡)
        try:
            self._dialog_view = DialogueView(
                self._data_path, character=character // 2, stage=stage
            )
            self._dialog_stage = stage
        except Exception:
            log.exception("对话渲染器初始化失败(降级为无对话渲染)")
            self._dialog_view = None
        # 战斗贴图渲染器(与对话渲染器同风格: 失败不拖垮游戏)
        try:
            self._game_view = GameView(
                self._data_path, character=character, stage=stage
            )
        except Exception:
            log.exception("战斗贴图渲染器初始化失败(降级为简笔渲染)")
            self._game_view = None
        # 右栏 HUD(front.anm/ascii.anm 贴图, 同风格容错)
        try:
            self._hud_view = HudView(self._data_path)
        except Exception:
            log.exception("HUD 渲染器初始化失败(降级为无 HUD)")
            self._hud_view = None
        # 得分弹字/状态横幅(ascii.anm 贴字, 同风格容错)
        try:
            self._popup_view = PopupView(self._data_path)
        except Exception:
            log.exception("弹字渲染器初始化失败(降级为无弹字)")
            self._popup_view = None
        ms = (time.perf_counter() - t0) * 1000
        if ms >= 30.0:
            log.debug("开局渲染资源装配耗时 {:.1f}ms (stage={})", ms, stage)

    def render_game(self, game) -> None:
        # 窗口布局 640x480: 游戏区 384x448 渲染后 blit 到 (32,16),
        # 右栏 HUD 面板(hud_view, front.anm/ascii.anm 贴图); surface 复用避免每帧分配
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
        else:
            surf.fill((8, 12, 30))
        # 对话覆盖层(DrawDialogue: 立绘 → 对话框 → 文本); 换关时按新关重建
        if (
            self._dialog_view is not None
            and getattr(game, "stage_no", 0) != self._dialog_stage
        ):
            try:
                self._dialog_view = DialogueView(
                    self._data_path, character=game.character // 2, stage=game.stage_no
                )
                self._dialog_stage = game.stage_no
            except Exception:
                self._dialog_view = None  # 渲染失败不拖垮游戏
        vm = getattr(game, "msg_vm", None)
        if self._dialog_view is not None and vm is not None and vm.active:
            self._dialog_view.render(surf, vm)
        # 过关结算面板(STAGERESULTS, 半透明叠加)
        sr = getattr(game, "stage_results", None)
        if sr is not None:
            self._stage_results_view.render(surf, sr, game.frame)
        # 震屏: 消费引擎帧末快照 frame_shakes (ScreenEffect 的 SCREEN_EFFECT_SHAKE,
        # 上游 55ff90c 由 BombEffects 改名; view 侧衰减),
        # 整帧位移只动游戏区 —— HUD 不晃 (C++ Gui.cpp:159-160 绘制前清零 offset)。
        # 快照按 (game, frame) 去重: 暂停/续关菜单冻结 tick 时帧号不变,
        # 不重复注册同一帧的事件。
        _shake_key = (id(game), getattr(game, "frame", -1))
        if _shake_key != self._shake_consumed:
            self._shake_consumed = _shake_key
            for _ev in getattr(game, "frame_shakes", ()):
                self._shake.register(*_ev)
        # Mod 覆盖层(engine/render/overlay 的立即模式命令: ModApi.gui 每帧
        # 推入, 本端 drain 消费, 命令只活一帧; headless 无本端即静默丢弃)。
        # 画在游戏区 surf 上: 坐标系 = 游戏区像素(384x448, y 向下), 与场上
        # 实体同面, 震屏时随游戏区一起位移 —— 与子弹/自机保持对齐
        _cmds = overlay_mod.SINK.drain()
        if _cmds:
            try:
                self._render_overlay(surf, _cmds)
            except Exception:
                pass  # 渲染异常不拖垮游戏循环
        dx, dy = self._shake.tick()
        frame.blit(surf, (GAME_X + dx, GAME_Y + dy))
        # GUI 层(关卡标题/符卡宣言/bomb cutin): 原版画在 640x480 窗口
        # framebuffer (Gui::OnDraw), 不受游戏区右缘裁切
        if self._game_view is not None:
            try:
                self._game_view.render_gui(frame, game)
            except Exception:
                pass  # 渲染异常不拖垮游戏循环
        # 樱点槽(原版在弹点层, 盖在游戏场景上, AsciiManager.cpp:1052)
        if self._hud_view is not None:
            try:
                self._hud_view.render_overlay(frame, game)
            except Exception:
                pass
        # 得分弹字 + 状态横幅 (AsciiManager::DrawPopups / Gui::OnDraw statusPopup)
        if self._popup_view is not None:
            try:
                self._popup_view.render(frame, game)
            except Exception:
                pass
        # FPS 显示 (Supervisor.cpp:948-951; 左下 HUD, 见 hud_view.render_fps)
        if self._hud_view is not None and self._clock is not None:
            try:
                self._hud_view.render_fps(frame, self._clock.get_fps())
            except Exception:
                pass
        self._blit_scaled(frame)

    def _overlay_font(self, size: int):
        """覆盖层文字字号缓存(每字号一只 Font, 懒加载容错同 _load_font)。"""
        font = self._overlay_fonts.get(size)
        if font is None:
            font = _load_font(size)
            self._overlay_fonts[size] = font
        return font

    def _render_overlay(self, surf, cmds) -> None:
        """把本帧 Mod 覆盖层命令画到游戏区 surface(pygame.draw + font)。

        坐标系 = 游戏区像素(384x448, y 向下), 调用方(render_game)已兜
        异常; 单条命令的语义即 engine/render/overlay.py 的结构定义。
        """
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
                    self._overlay_font(cmd.size).render(cmd.content, True, cmd.color),
                    (cmd.x, cmd.y),
                )

    def render_pause(
        self,
        game,
        cursor: int,
        *,
        hint: "str | None" = None,
        confirm: "tuple[str, int] | None" = None,
    ) -> None:
        """暂停: 冻结画面(未 tick, 画面静止) + 半透明暂停面板 + 瞬态提示。"""
        self.render_game(game)
        overlay = pygame.Surface((WIN_W, WIN_H), pygame.SRCALPHA)
        try:
            self._option_view.render_pause(overlay, cursor, confirm=confirm)
        except Exception:
            pass  # 渲染失败不拖垮游戏循环
        if hint:
            # Save Replay 反馈提示(瞬态)
            try:
                font = _load_font(20)
                overlay.blit(
                    font.render(hint, True, (150, 255, 180)),
                    (WIN_W // 2 - 150, WIN_H // 2 + 120),
                )
            except Exception:
                pass  # 提示渲染失败不拖垮游戏循环
        self._blit_scaled(overlay)

    def render_continue(self, game, cursor: int, retries_left: int) -> None:
        """GameOver 续关菜单(冻结画面 + Continue? Yes/No 覆盖层)。"""
        self.render_game(game)
        overlay = pygame.Surface((WIN_W, WIN_H), pygame.SRCALPHA)
        try:
            self._continue_view.render(overlay, cursor, retries_left)
        except Exception:
            pass  # 渲染失败不拖垮游戏循环
        self._blit_scaled(overlay)

    # ---- 菜单 SE(走标题画面资源表, 与原 GameApp 同一音效路径) ----
    def play_menu_se(self, key: str) -> None:
        self._title.play_sound(key)
