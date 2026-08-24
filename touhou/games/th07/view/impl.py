""" 窗口版应用壳 —— 场景状态机 + 游戏/录像/存档流程, 与渲染后端解耦。

GameApp: 标题主菜单(原版 8 项) → 选难度 → 选角色 → 进入游戏。
渲染与输入采集全部下沉到 Renderer 后端(协议见 engine/render/__init__.py;
默认 "pygame" 实现在 pygame_backend.py, 经 registry 按名解析,
``GameApp(..., renderer="pygame")`` 可换)。菜单逻辑在 screens.py(纯逻辑)。
本模块不 import pygame; 音频(SoundPlayer, pygame.mixer)与渲染后端正交,
由本壳持有(静音容错内建)。

作品耦合说明: 机体/难度名单与面数经 ``game_data`` 参数(registry.GameData,
缺省 = th07 表); 其余表现层(HUD 右栏坐标/选人贴图布局/标题 8 项菜单等)
是 th07 专属实现, 不强行泛化 —— 新作品复用窗口版需自带 view 层。
"""

from __future__ import annotations

import time

from ....logger import logger as log

from ....apis.basic import Game
from ....registry import GameData, get_game, get_renderer, register_app
from ....engine.config import DEFAULT_CONFIG_PATH, GameConfig
from ....schema.sound import SE
from ....engine import replay as replay_mod
from ....engine.render import FrameInput, Renderer
from ....engine.score_store import ScoreStore
from .screens import (
    CHARACTERS,
    DIFFICULTIES,
    EXTRA_STAGES,
    MAIN_DIFFICULTIES,
    PAUSE_CONFIRM_ITEMS,
    PAUSE_ITEMS,
    PRACTICE_DIFFICULTIES,
    PRACTICE_STAGE_ITEMS,
    RESULT_SAVE_ITEMS,
    KeyConfigFlow,
    MenuAction,
    MenuCursor,
    MusicRoomFlow,
    NameEntryFlow,
    OptionFlow,
    PlayerDataFlow,
    ReplayFlow,
    Screen,
    TitleFlow,
    load_tracks,
    practice_max_stage,
)
from ....engine.view.sound_player import SoundPlayer
from ....engine.view.sprite_bank import SpriteBank
from ....paths import DEFAULT_SCORE_PATH, resolve_data_path

# 标题画面 BGM (MainMenu.cpp:230/2685: LoadAudio(8,"bgm/th07_01.mid"))
_TITLE_BGM = "th07_01.mid"
# 结算(分数)画面 BGM (Supervisor.cpp:713 槽30="bgm/init.mid",
# GameManager::DeletedCallback PlayLoaded(30))
_RESULT_BGM = "init.mid"

_UNIMPLEMENTED_HINT_FRAMES = 90


def _default_spellcard_count() -> int:
    """未传 game_data 时的符卡总数兜底: 注册表里 th07 的数值表, 找不到为 0。

    延迟到运行时的注册表查询(registry 是叶子模块, 无循环 import);
    引擎层不 import 作品包。
    """
    try:
        data = get_game("th07").data
    except KeyError:  # NotRegisteredError(未注册 th07)
        return 0
    return len(data.spellcard_scores) if data is not None else 0


@register_app("th07")
class GameApp:
    """完整应用: 标题菜单 + 游戏流程(渲染/输入由 Renderer 后端承担)。

    经 ``@register_app("th07")`` 登记到 registry; ``TouhouWorld.run()``
    (headless=False) 按作品名解析本类。构造契约(register_app):
    ``GameApp(make_game, *, data_path, bgm_path, game_data)`` —— 其余关键字
    参数均有默认值, 契约是关键字子集。
    """

    def __init__(self, make_game, *, scale: int | None = None,
                 data_path=None, score_path=None,
                 config_path=None, replay_dir=None, bgm_path=None,
                 game_data: GameData | None = None,
                 renderer: "str | Renderer" = "pygame",
                 spectate=None) -> None:
        data_path = resolve_data_path(data_path)
        self._data_path = data_path
        self._make_game = make_game
        # 观战模式(registry.register_app 的可选契约): spectate 非 None 时是
        # "facade(Game) -> Input" 的逐帧策略 —— run() 跳过标题菜单直接开局,
        # 每帧输入由策略产出(键盘仅保留 Esc 中止观战/关窗退出);
        # 角色/难度/残机/种子以对局构造时注入的值为准(不再做 config 覆写
        # 与时间播种)。None = 正常键盘游玩, 行为不变。
        self._spectate = spectate
        self._spectate_facade: Game | None = None  # 包局内 live 对局的门面
        # 渲染后端: 名字经 registry 解析(默认 "pygame"), 或直接传实现实例
        # (测试插桩用; 构造契约 cls(data_path=None), 见 registry.register_renderer)
        if isinstance(renderer, str):
            renderer = get_renderer(renderer)(data_path)
        self._renderer: Renderer = renderer
        # 设置(config.json): 缺失/损坏回退默认(engine/config.py 容错)
        if config_path is None:
            config_path = DEFAULT_CONFIG_PATH
        self._config_path = config_path
        self._config = GameConfig.load(config_path)
        # 显式 scale 优先, 否则用 config 的窗口缩放
        self._scale = scale if scale is not None else self._config.window_scale
        # 原版启动直接显示标题主菜单(MainMenu 从 STATE_PRE_INPUT 开始)
        self._screen = Screen.MAIN_MENU
        self._flow = TitleFlow()
        # 名单/面数: 作品数据表(game_data, 经 TouhouWorld 从 GameSpec.data 传入)
        # 优先; 缺省/空字段回落 th07 表(screens 模块常量)
        gd = game_data
        self._characters = list(gd.characters) \
            if gd is not None and gd.characters else CHARACTERS
        self._difficulties = list(gd.difficulties) \
            if gd is not None and gd.difficulties else DIFFICULTIES
        # 本篇 Start 难度(不含 Extra/Phantasm, BUGS.md#1: 光标在全名单上
        # 回绕会选中不可见的 Extra/Phantasm → 出界; 渲染侧本就只画 4 项)
        self._main_difficulties = (
            self._difficulties[:gd.main_difficulty_count]
            if gd is not None and gd.difficulties else MAIN_DIFFICULTIES)
        self._extra_stages = list(gd.extra_stages) \
            if gd is not None and gd.extra_stages else EXTRA_STAGES
        self._practice_difficulties = (
            self._difficulties[:gd.practice_difficulty_count]
            if gd is not None and gd.difficulties else PRACTICE_DIFFICULTIES)
        self._practice_stage_items = (
            [f"Stage {i}" for i in range(1, gd.stage_count + 1)]
            if gd is not None else PRACTICE_STAGE_ITEMS)
        # 符卡总数(catk 长度): game_data 优先, 缺省走注册表兜底
        self._spellcard_count = (
            len(gd.spellcard_scores) if gd is not None and gd.spellcard_scores
            else _default_spellcard_count())
        self._diff = MenuCursor(self._main_difficulties, index=1)
        self._char = MenuCursor(self._characters, index=0)
        self._extra_mode = False  # Extra Start 流: 选 Extra/Phantasm → 选机体
                                  # (BUGS.md 增量#1: 与本篇"先难度后选人"一致)
        self._extra_stage = MenuCursor(self._extra_stages, index=0)
        # Practice Start 流(MainMenu.cpp practice 分支): 难度(4 项) → 机体 → 选关
        self._practice_mode = False
        self._practice_diff = MenuCursor(self._practice_difficulties, index=1)
        self._practice_stage_cursor = MenuCursor(self._practice_stage_items,
                                                 index=0)
        self._practice_max_stage = 1   # 可选到第几面(clrd 解锁)
        self._practice_stage = None    # 本局练习的面(打完检测用, None=非练习)
        # Player Data(Result 画面): 翻页状态 + 进入时加载的 score.json
        self._pd_flow = PlayerDataFlow()
        self._pd_store: ScoreStore | None = None
        # Music Room / Replay: 进入时建 flow(曲目/录像列表)
        self._mr_flow: MusicRoomFlow | None = None
        self._rp_flow: ReplayFlow | None = None
        # 回放录制/播放(engine/replay.py): 每局开录制器, 播放时逐帧喂输入
        if replay_dir is None:
            replay_dir = replay_mod.DEFAULT_REPLAY_DIR
        self._replay_dir = replay_dir
        self._recorder: replay_mod.ReplayRecorder | None = None
        self._playback: dict | None = None   # {"codes", "idx", "name"}
        self._pause_hint = ""                # 暂停面板瞬态提示(Save Replay 反馈)
        self._pause_hint_timer = 0
        self._option_flow = OptionFlow(config=self._config)  # 共享同一 config
        self._keyconfig_flow = KeyConfigFlow(config=self._config)  # 同上
        # config.keymap(键名) → 后端输入映射(改键后 _rebuild_keymap 重建)
        self._rebuild_keymap()
        # score.json 落盘位置: exe 同目录语义 → 仓库根(score_path 可覆盖, 测试用)
        # 默认值集中定义在 touhou/paths.py, 与 world.py 同一来源, 别各算各的
        if score_path is None:
            score_path = DEFAULT_SCORE_PATH
        self._score_path = score_path
        self._result_saved = False
        self._name_entry: NameEntryFlow | None = None  # 入榜名字输入态
        # 结算画面 Save Replay 流程(ResultScreen.cpp HandleReplaySaveKeyboard):
        # None=未到此步; "ask"=Save Replay? Yes/No 询问中(state 11);
        # "saved"=已存, 显示确认信息待按键退出(state 14 存完回 state 2 的简化)
        self._result_save: str | None = None
        self._result_save_cursor = MenuCursor(RESULT_SAVE_ITEMS, index=0)
        self._result_save_msg = ""   # "saved" 态显示的录像文件名
        self._menu_frame = 0
        self._unimplemented_timer = 0
        self._game = None
        self._sound = SoundPlayer(data_path, bgm_path=bgm_path)  # SE/BGM(懒加载, 静音容错)
        # 启动即应用 config: 音源偏好; 音量系数存进 SoundPlayer,
        # ensure_loaded/每次 play 时带上(此时 mixer 未初始化也安全)
        self._sound.set_bgm_source(self._config.bgm_source)
        self._sound.set_bgm_volume(self._config.bgm_volume / 100)
        self._sound.set_se_volume(self._config.se_volume / 100)
        self._bgm_stage = 0        # 已播关卡曲的关卡号(换关切曲用)
        self._paused = False       # 游戏内暂停(Esc; 冻结 tick, WAV BGM 暂停)
        self._pause_cursor = MenuCursor(PAUSE_ITEMS, index=0)
        # Retry/Quit 二次确认态(AsciiManager.cpp PauseMenu case 5-8):
        # None=主暂停菜单; "Retry"/"Quit to Title"=待确认的项。
        # 原版确认子菜单只有 Yes/No(不能 Save Replay), 默认停 No。
        self._pause_confirm: str | None = None
        self._pause_confirm_cursor = MenuCursor(PAUSE_CONFIRM_ITEMS, index=1)
        # GameOver 续关菜单(AsciiManager.cpp RetryMenu): 默认选 Yes(C curState=1)
        self._continue_cursor = MenuCursor(["Yes", "No"], index=0)
        self._in_continue = False  # 续关菜单显示中(进入沿检测, 重置光标用)
        self._run_extra_stage = None  # 本局 extra_stage(Retry 重开用)
        self._prev_msg_active = False  # 对话门控沿检测(日志用)
        self._prev_bomb_pressed = False  # bomb 键沿检测(日志用)
        self._prev_shot_held = False   # 射击键沿检测(日志用)
        self._finished = False
        # ---- 菜单空转预热(BUGS.md 增量#3, 见 _warmup_step) ----
        self._warmup: list[str] | None = None    # 待预载 anm 队列(None=未建)
        self._warmup_bank = None                 # 预热用 SpriteBank(懒建)

    # ---- 键位映射(config.keymap → 后端输入映射) ----
    def _rebuild_keymap(self) -> None:
        """按当前 config.keymap 重建后端的动作/菜单键映射(防锁死规则在后端:
        方向/WASD/Enter/Esc 硬编码, 确认/返回另随 keymap 的 shoot/bomb)。"""
        self._renderer.set_keymap(self._config.keymap)

    def _save_config(self) -> None:
        """即时写 config.json(容错: 写盘失败不炸, 同 score_store)。"""
        try:
            self._config.save(self._config_path)
        except OSError:
            pass

    # ---- 主循环(渲染/输入采集委托后端) ----
    def run(self) -> None:
        self._renderer.open(scale=self._scale)
        log.info("渲染后端就绪; config={}", self._config)
        # 标题画面 BGM (MainMenu.cpp:230/2685: LoadAudio(8,"bgm/th07_01.mid"))
        self._sound.ensure_loaded()
        if self._spectate is not None:
            # 观战: 跳过标题状态机直接开局(菜单没人会选;
            # 角色/难度由 make_game 包装层定, 见 TouhouWorld.run)
            log.debug("观战模式: 跳过标题直接开局")
            self._start_game()
        elif self._screen == Screen.MAIN_MENU:
            self._sound.play_music(_TITLE_BGM)
        running = True
        while running:
            inp = self._renderer.poll_input(
                capturing=self._screen == Screen.KEY_CONFIG
                and self._keyconfig_flow.capturing is not None)
            if inp.quit:
                running = False
            if inp.captured_key is not None:
                # KeyConfig "按新键"捕获: 该键已被后端吃掉, 不进菜单动作
                self._keyconfig_capture(inp.captured_key)
            if self._screen == Screen.MAIN_MENU:
                self._run_title_menu(inp.menu_actions)
            elif self._screen in (Screen.DIFFICULTY, Screen.CHARACTER,
                                  Screen.EXTRA_LEVEL, Screen.PRACTICE_STAGE):
                self._run_menu(inp.menu_actions)
            elif self._screen == Screen.OPTION:
                self._run_option(inp.menu_actions)
            elif self._screen == Screen.KEY_CONFIG:
                self._run_keyconfig(inp.menu_actions)
            elif self._screen == Screen.PLAYER_DATA:
                self._run_player_data(inp.menu_actions)
            elif self._screen == Screen.MUSIC_ROOM:
                self._run_music_room(inp.menu_actions)
            elif self._screen == Screen.REPLAY:
                self._run_replay_menu(inp.menu_actions)
            elif self._screen == Screen.RESULT:
                self._run_result(inp.menu_actions)
            elif self._screen == Screen.ENDING:
                self._run_ending(inp.menu_actions)
            else:  # playing
                self._run_game(inp)
                if self._finished:
                    running = False
            # WAV BGM 循环点回卷轮询: 与场景无关每帧跑(BUGS.md#4 —— 只在
            # 对局内轮询时, 标题/结算/音乐室的 WAV 曲播完一遍就停了)
            self._sound.poll_loop()
            # 菜单空转预热: 每帧一项 (BUGS.md 增量#3)
            if self._screen != Screen.PLAYING:
                self._warmup_step()
            self._renderer.present()
        self._renderer.close()

    # ---- 菜单空转预热 (BUGS.md 增量#3) ----
    # 开局集中解压/解码的资源清单: 通用战斗/HUD 贴图 + 1 面关卡资源 +
    # 三机体 player/face (开局选择未定, 全预载; 菜单停留期间摊到每帧一项)
    _WARMUP_ANMS = (
        "ascii.anm", "front.anm", "etama.anm",
        "std1txt.anm", "stg1enm.anm", "stg1bg.anm", "eff01.anm",
        "face_01_00.anm",
        "player00.anm", "face_rm00.anm",
        "player01.anm", "face_mr00.anm",
        "player02.anm", "face_sk00.anm",
    )

    def _warmup_step(self) -> None:
        """菜单场景每帧预载一项对局资源。

        解压(GameArchive._DECOMP_CACHE)与解码(schema.anm.parse_cached)
        都是进程级共享缓存, 预热后开局/对局内各视图命中即免费; 用户
        快速开局时未预载完的项退回原懒加载路径, 无回归。队列耗尽后
        每次调用仅一次空判断, 零开销。
        """
        if self._warmup is None:
            self._warmup = list(self._WARMUP_ANMS)
        if not self._warmup:
            return
        if self._warmup_bank is None:
            self._warmup_bank = SpriteBank(self._data_path)
        self._warmup_bank.has(self._warmup.pop(0))

    # ---- 标题主菜单(原版 8 项) ----
    def _run_title_menu(self, actions) -> None:
        self._menu_frame += 1
        if self._unimplemented_timer > 0:
            self._unimplemented_timer -= 1
        self._renderer.render_title(self._flow.cursor.index, self._menu_frame,
                                    show_unimplemented=self._unimplemented_timer > 0)
        for act in actions:
            self._on_menu(act)

    # ---- 难度/角色/Extra 菜单(贴图版, 布局见 select_view.py docstring) ----
    def _run_menu(self, actions) -> None:
        if self._screen == Screen.DIFFICULTY:
            self._renderer.render_difficulty(self._active_diff_cursor().index)
        elif self._screen == Screen.CHARACTER:
            self._renderer.render_character(self._char.index)
        elif self._screen == Screen.PRACTICE_STAGE:
            self._renderer.render_practice_stage(
                self._practice_stage_cursor.index, self._practice_max_stage,
                difficulty=self._practice_diff.current or "",
                character=self._char.current or "")
        else:  # Screen.EXTRA_LEVEL
            self._renderer.render_extra(self._extra_stage.index)
        for act in actions:
            self._on_menu(act)

    def _active_diff_cursor(self) -> MenuCursor:
        """难度页光标: Practice 流用 4 项 E/N/H/L(原版无 Extra/Phantasm)。"""
        return self._practice_diff if self._practice_mode else self._diff

    def _enter_main_menu(self) -> None:
        """回标题主菜单: 切屏 + 标题曲(返回时也播, MainMenu.cpp:2685)。"""
        log.debug("切屏 → 标题主菜单")
        self._screen = Screen.MAIN_MENU
        self._sound.play_music(_TITLE_BGM)

    # ---- Option 设置页(贴图+文字, 布局见 option_view.py docstring) ----
    def _run_option(self, actions) -> None:
        self._renderer.render_option(self._option_flow)
        for act in actions:
            self._on_menu(act)

    # ---- KeyConfig 键位设置页(MainMenu.cpp STATE_KEY_CONFIG) ----
    def _run_keyconfig(self, actions) -> None:
        self._renderer.render_keyconfig(self._keyconfig_flow)
        for act in actions:
            self._on_menu(act)

    def _keyconfig_capture(self, name: str) -> None:
        """KeyConfig 捕获状态: 收一个键名(后端 poll_input 已转为规范名)
        设为该动作主键。Esc/X = 取消(flow.capture 内判定); 改动后重建
        键表 + 即时落盘。
        """
        r = self._keyconfig_flow.capture(name)
        log.debug("KeyConfig 捕获: {} 键={} → {}", r.get("item"), name,
                  r["action"])
        if r["action"] == "changed":
            self._renderer.play_menu_se("ok")
            self._rebuild_keymap()
            self._save_config()
        else:
            self._renderer.play_menu_se("cancel")

    # ---- Player Data(Result 画面) ----
    def _run_player_data(self, actions) -> None:
        self._menu_frame += 1
        self._renderer.render_player_data(self._pd_flow, self._pd_store,
                                          self._menu_frame)
        for act in actions:
            self._on_menu(act)

    # ---- Music Room(选曲播放, 布局见 musicroom_view docstring) ----
    def _run_music_room(self, actions) -> None:
        self._menu_frame += 1
        if self._mr_flow is not None:
            self._renderer.render_music_room(self._mr_flow, self._menu_frame)
        for act in actions:
            self._on_menu(act)

    # ---- Replay 选择(录像列表, 布局见 replay_view docstring) ----
    def _run_replay_menu(self, actions) -> None:
        self._menu_frame += 1
        if self._rp_flow is not None:
            self._renderer.render_replay_menu(self._rp_flow, self._menu_frame)
        for act in actions:
            self._on_menu(act)

    def _apply_option(self, item: str, value) -> None:
        """Option 调值实时生效 + 即时写 config.json。"""
        if item == "BGM 音量":
            self._sound.set_bgm_volume(value / 100)
        elif item == "SE 音量":
            self._sound.set_se_volume(value / 100)
        elif item == "音源":
            # 切源后需停再播才生效(见 sound_player docstring);
            # 原版同: StopAudio → 改 musicMode → 重播标题曲(MainMenu.cpp:643-662)
            current = self._sound.current_bgm
            self._sound.set_bgm_source(value)
            if current:
                self._sound.stop_music()
                self._sound.play_music(current)
        elif item == "窗口缩放":
            self._scale = value
            self._renderer.resize(self._screen, self._scale)
        # 初始残机: 开局时应用(_start_game), 无需即时动作
        self._save_config()

    def _on_menu(self, action: MenuAction) -> None:
        if self._screen == Screen.MAIN_MENU:
            if action in (MenuAction.UP, MenuAction.DOWN):
                self._renderer.play_menu_se("select")
            elif action == MenuAction.BACK:
                self._renderer.play_menu_se("cancel")
            r = self._flow.handle(action)
            if r:
                self._handle_main_result(r)
        elif self._screen == Screen.OPTION:
            if action in (MenuAction.UP, MenuAction.DOWN,
                          MenuAction.LEFT, MenuAction.RIGHT):
                self._renderer.play_menu_se("select")
            r = self._option_flow.handle(action)
            if r:
                if r["action"] == "quit":
                    self._renderer.play_menu_se("cancel")
                    self._enter_main_menu()
                elif r["action"] == "changed":
                    self._apply_option(r["item"], r["value"])
                elif r["action"] == "keyconfig":
                    # → KeyConfig 页(MainMenu.cpp:803-808 case 7)
                    self._renderer.play_menu_se("ok")
                    self._keyconfig_flow.cursor.index = 0
                    self._keyconfig_flow.capturing = None
                    self._screen = Screen.KEY_CONFIG
        elif self._screen == Screen.KEY_CONFIG:
            if not self._keyconfig_flow.capturing \
                    and action in (MenuAction.UP, MenuAction.DOWN):
                self._renderer.play_menu_se("select")
            r = self._keyconfig_flow.handle(action)
            if r:
                if r["action"] == "quit":
                    self._renderer.play_menu_se("cancel")
                    self._screen = Screen.OPTION
                elif r["action"] == "capture":
                    self._renderer.play_menu_se("ok")  # 进入"按新键"捕获状态
                elif r["action"] == "changed":   # 恢复默认
                    self._renderer.play_menu_se("ok")
                    self._rebuild_keymap()
                    self._save_config()
        elif self._screen == Screen.DIFFICULTY:
            cur = self._active_diff_cursor()
            if action == MenuAction.UP:
                cur.move(-1)
                self._renderer.play_menu_se("select")
            elif action == MenuAction.DOWN:
                cur.move(1)
                self._renderer.play_menu_se("select")
            elif action == MenuAction.CONFIRM:
                self._renderer.play_menu_se("ok")
                self._screen = Screen.CHARACTER
            elif action == MenuAction.BACK:
                self._renderer.play_menu_se("cancel")
                self._practice_mode = False
                self._enter_main_menu()
        elif self._screen == Screen.CHARACTER:
            # 原版(MainMenu.cpp:1481): 左右选自机(±2 保持 A/B), 上下选机型(±1)
            if action == MenuAction.UP:
                self._char.move(-1)
                self._renderer.play_menu_se("select")
            elif action == MenuAction.DOWN:
                self._char.move(1)
                self._renderer.play_menu_se("select")
            elif action == MenuAction.LEFT:
                self._char.move(-2)
                self._renderer.play_menu_se("select")
            elif action == MenuAction.RIGHT:
                self._char.move(2)
                self._renderer.play_menu_se("select")
            elif action == MenuAction.CONFIRM:
                self._renderer.play_menu_se("ok")
                log.trace("选定角色: {}", self._char.current)
                if self._extra_mode:
                    # Extra Start: 选完机体直接进关(Extra → 7, Phantasm → 8)
                    self._start_game(extra_stage=7 + self._extra_stage.index)
                    self._extra_mode = False
                elif self._practice_mode:
                    self._enter_practice_stage_select()
                else:
                    self._start_game()
            elif action == MenuAction.BACK:
                self._renderer.play_menu_se("cancel")
                if self._extra_mode:
                    # Extra Start 流: 退回 Extra/Phantasm 选择页
                    self._screen = Screen.EXTRA_LEVEL
                else:  # 通常/practice 流: 退回难度页(MainMenu.cpp:1591-1598)
                    self._screen = Screen.DIFFICULTY
        elif self._screen == Screen.PRACTICE_STAGE:
            cur = self._practice_stage_cursor
            if action == MenuAction.UP:
                # 回绕范围 = 已解锁面数(MainMenu.cpp:1927 MoveCursorVertical)
                cur.index = (cur.index - 1) % self._practice_max_stage
                self._renderer.play_menu_se("select")
            elif action == MenuAction.DOWN:
                cur.index = (cur.index + 1) % self._practice_max_stage
                self._renderer.play_menu_se("select")
            elif action == MenuAction.CONFIRM:
                self._renderer.play_menu_se("ok")
                self._start_practice()
            elif action == MenuAction.BACK:
                self._renderer.play_menu_se("cancel")
                self._screen = Screen.CHARACTER
        elif self._screen == Screen.PLAYER_DATA:
            if action in (MenuAction.UP, MenuAction.DOWN,
                          MenuAction.LEFT, MenuAction.RIGHT):
                self._renderer.play_menu_se("select")
            r = self._pd_flow.handle(action)
            if r and r["action"] == "quit":
                self._renderer.play_menu_se("cancel")
                self._enter_main_menu()
        elif self._screen == Screen.MUSIC_ROOM:
            flow = self._mr_flow
            if flow is None:
                self._enter_main_menu()
                return
            prev_cursor = flow.cursor
            r = flow.handle(action)
            if action in (MenuAction.UP, MenuAction.DOWN) \
                    and flow.cursor != prev_cursor:
                self._renderer.play_menu_se("select")
            if r:
                if r["action"] == "quit":
                    # 退出 → 停当前曲回标题(标题曲由 _enter_main_menu 重播)
                    self._renderer.play_menu_se("cancel")
                    self._sound.stop_music()
                    flow.playing = None
                    self._enter_main_menu()
                elif r["action"] == "play":
                    # MusicRoom.cpp:113 PlayAudio(trackDescriptors[i].path)
                    self._renderer.play_menu_se("ok")
                    self._sound.ensure_loaded()
                    self._sound.play_music(flow.tracks[r["index"]].file_name)
                elif r["action"] == "stop":
                    # 简化: 停止 = 停当前曲并回标题曲(原版无停止, 只有换曲)
                    self._renderer.play_menu_se("cancel")
                    self._sound.stop_music()
                    self._sound.play_music(_TITLE_BGM)
        elif self._screen == Screen.REPLAY:
            flow = self._rp_flow
            if flow is None:
                self._enter_main_menu()
                return
            if action in (MenuAction.UP, MenuAction.DOWN) and flow.entries:
                self._renderer.play_menu_se("select")
            r = flow.handle(action)
            if r:
                if r["action"] == "quit":
                    self._renderer.play_menu_se("cancel")
                    self._enter_main_menu()
                elif r["action"] == "play":
                    self._renderer.play_menu_se("ok")
                    self._start_replay(flow.entries[r["index"]])
        elif self._screen == Screen.EXTRA_LEVEL:
            if action == MenuAction.UP:
                self._extra_stage.move(-1)
                self._renderer.play_menu_se("select")
            elif action == MenuAction.DOWN:
                self._extra_stage.move(1)
                self._renderer.play_menu_se("select")
            elif action == MenuAction.CONFIRM:
                self._renderer.play_menu_se("ok")
                # 选定 Extra/Phantasm 后再选机体(与本篇难度→机体同序)
                self._screen = Screen.CHARACTER
            elif action == MenuAction.BACK:
                self._renderer.play_menu_se("cancel")
                self._extra_mode = False
                self._screen = Screen.MAIN_MENU

    def _handle_main_result(self, r: dict) -> None:
        act = r["action"]
        log.trace("主菜单选择: {}", act)
        if act == "quit":
            self._renderer.play_menu_se("ok")
            self._finished = True
        elif act == "select_difficulty":
            self._renderer.play_menu_se("ok")
            self._screen = Screen.DIFFICULTY
        elif act == "extra_start":
            # Extra Start: 原版需通关解锁, 本期不设解锁条件, 直接可进;
            # 顺序与本篇一致 —— 先选 Extra/Phantasm(相当于难度)再选机体
            self._renderer.play_menu_se("ok")
            self._extra_mode = True
            self._screen = Screen.EXTRA_LEVEL
        elif act == "option":
            # Option → 设置页(MainMenu.cpp:441 STATE_OPTIONS)
            self._renderer.play_menu_se("ok")
            self._option_flow.cursor.index = 0
            self._screen = Screen.OPTION
        elif act == "player_data":
            # Player Data → Result 画面(MainMenu.cpp:430-433 curState=5)
            self._renderer.play_menu_se("ok")
            self._pd_flow = PlayerDataFlow()
            self._pd_store = ScoreStore.load(self._score_path,
                                             spellcard_count=self._spellcard_count)
            self._screen = Screen.PLAYER_DATA
        elif act == "music_room":
            # Music Room → 音乐室(MainMenu.cpp:434-437 → MusicRoom::RegisterChain)
            self._renderer.play_menu_se("ok")
            self._mr_flow = MusicRoomFlow(tracks=load_tracks(self._data_path))
            self._screen = Screen.MUSIC_ROOM
        elif act == "replay":
            # Replay → 录像选择(MainMenu.cpp:418-421 STATE_SELECT_REPLAY)
            self._renderer.play_menu_se("ok")
            self._rp_flow = ReplayFlow(
                entries=replay_mod.list_replays(self._replay_dir))
            self._screen = Screen.REPLAY
        elif act == "practice":
            # Practice Start → 难度(4 项) → 机体 → 选关(MainMenu.cpp:384-399)
            self._renderer.play_menu_se("ok")
            self._practice_mode = True
            self._screen = Screen.DIFFICULTY
        elif act == "unimplemented":
            # 兜底: 未知菜单项(现菜单项均已接线, 正常不会走到)
            self._renderer.play_menu_se("cancel")
            self._unimplemented_timer = _UNIMPLEMENTED_HINT_FRAMES

    def _diff_index(self, name: str | None, default: int = 1) -> int:
        """难度名 → 下标(按当前作品难度表; 未知名/None 按 default)。"""
        return self._difficulties.index(name) \
            if name in self._difficulties else default

    def _char_index(self, name: str | None, default: int = 0) -> int:
        """机体名 → 下标(按当前作品机体表; 未知名/None 按 default)。"""
        return self._characters.index(name) if name in self._characters \
            else default

    def _enter_practice_stage_select(self) -> None:
        """选完机体 → Practice 选关页: 按 clrd 算可解锁面数
        (MainMenu.cpp:1912-1926, without_retries[难度], 下限 1)。"""
        store = ScoreStore.load(self._score_path,
                                spellcard_count=self._spellcard_count)
        dif_idx = self._diff_index(self._practice_diff.current)
        self._practice_max_stage = practice_max_stage(
            store, self._char_index(self._char.current), dif_idx)
        self._practice_stage_cursor = MenuCursor(
            self._practice_stage_items[:self._practice_max_stage], index=0)
        self._screen = Screen.PRACTICE_STAGE

    def _start_practice(self) -> None:
        """选关确认 → 直接进该关(MainMenu.cpp:1928-1940:
        difficulty=所选难度, currentStage=cursor, curState=2)。"""
        stage = self._practice_stage_cursor.index + 1
        dif_idx = self._diff_index(self._practice_diff.current)
        log.debug("Practice 进关: stage={} difficulty={} character={}",
                 stage, dif_idx, self._char.current)
        self._practice_stage = stage
        self._practice_mode = False
        self._start_game(stage=stage, difficulty=dif_idx)

    def _finish_practice(self) -> None:
        """练习关打完(通关/GameOver) → 直接回标题, 不进结算/排行榜(简化:
        原版 practice 通关走 ResultScreen 但不入 Hscr 榜, GameManager.cpp
        :459-465 用 pscr 当最高分)。catk 符卡统计合并落盘, Top10/clrd 不写
        score.json。"""
        game, self._game = self._game, None
        store = getattr(game, "store", None)
        if store is not None:
            try:
                disk = ScoreStore.load(self._score_path,
                                       spellcard_count=self._spellcard_count)
                disk.catk = store.catk      # catk 记(符卡挑战/捕获)
                disk.save(self._score_path)
            except OSError:
                pass  # 写盘失败不炸(容错同 score_store)
        self._practice_stage = None
        self._practice_mode = False
        self._enter_main_menu()

    def _start_game(self, extra_stage: int | None = None,
                    stage: int | None = None,
                    difficulty: int | None = None,
                    seed: int | None = None,
                    record: bool = True) -> None:
        t0 = time.time()
        ch = self._char.current or (self._characters[0] if self._characters
                                    else "ReimuA")
        ch_idx = self._char_index(ch)
        if extra_stage is not None:
            # Extra/Phantasm 不选难度, 固定 DIFF_EXTRA/DIFF_PHANTASM(4/5)
            dif_idx = 4 if extra_stage == 7 else 5
        elif difficulty is not None:
            dif_idx = difficulty  # Practice: 用 practice 难度页选的难度
        else:
            dif_idx = self._diff_index(self._diff.current)
        log.debug("开局: character={}({}) difficulty={} extra_stage={} stage={}",
                 ch, ch_idx, dif_idx, extra_stage, stage)
        self._game = self._make_game(difficulty=dif_idx, character=ch_idx)
        # 回放确定性: 每局一个种子(原版 Rng 以时间播种; 回放播放传录像种子)
        if self._spectate is not None and seed is None:
            # 观战: 种子在对局构造时已注入(TouhouWorld.seed; None=impl 默认
            # 固定种子), 不再覆写 —— meta 记 impl 实际种子即可复现
            self._run_seed = int(getattr(self._game, "seed", 0x5EED))
        else:
            self._run_seed = (int(time.time() * 1000) & 0xFFFF) if seed is None \
                else (seed & 0xFFFF)
            if hasattr(self._game, "set_seed"):
                self._game.set_seed(self._run_seed)
        # Option 初始残机: make_game 签名固定 (difficulty, character) 无法透参,
        # 这里按 config 覆写初始残(difficulty>=4 固定 2 不动, 同 impl __init__)
        # 观战跳过此覆写: 残机以对局构造注入值(TouhouWorld.lives)为准
        g0 = getattr(self._game, "globals", None)
        if self._spectate is None and g0 is not None and dif_idx < 4 \
                and hasattr(g0, "lives_remaining"):
            g0.lives_remaining = float(self._config.initial_lives)
            # 续关回残基数同步(retry 菜单 Yes: SetLivesRemaining(defaultCfg->lifeCount))
            if hasattr(self._game, "initial_lives"):
                self._game.initial_lives = int(self._config.initial_lives)
        # 回放录制: 记开局参数, 之后每帧在 _run_game 记输入(播放模式不录)
        if record:
            self._recorder = replay_mod.ReplayRecorder(replay_mod.make_meta(
                difficulty=dif_idx, character=ch_idx,
                stage=extra_stage if extra_stage is not None else (stage or 1),
                seed=self._run_seed,
                initial_lives=int(getattr(g0, "lives_remaining",
                                          self._config.initial_lives)
                                  if g0 is not None else
                                  self._config.initial_lives)))
        else:
            self._recorder = None
        if self._recorder is not None:
            log.debug("录像录制开始: character={} difficulty={} stage={} seed={}",
                      ch_idx, dif_idx,
                      extra_stage if extra_stage is not None else (stage or 1),
                      self._run_seed)
        self._sound.ensure_loaded()
        log.debug("音效资源加载完成 ({}s)", time.time() - t0)
        # Retry 重开本关 / Extra 直入 7/8 面: 进关后再建渲染资源(贴图按关取)
        self._run_extra_stage = extra_stage
        target_stage = extra_stage if extra_stage is not None else stage
        if target_stage and target_stage != 1 \
                and hasattr(self._game, "enter_stage"):
            self._game.enter_stage(target_stage)
        if self._spectate is not None:
            # 观战: 包局内 live 对局为 Game 门面(不重复构造对局;
            # policy 拿到的观测面与自建 Game 一致)
            self._spectate_facade = Game._from_impl(
                self._game, get_game("th07"), "th07")
        self._screen = Screen.PLAYING
        self._paused = False
        self._in_continue = False
        self._renderer.begin_game(self._game, character=ch_idx)
        self._bgm_stage = 0  # 关卡曲由 _run_game 的换关监听播(GameManager.cpp:785)
        log.debug("开局完成, 总耗时 {}s", time.time() - t0)
        # 窗口 640x480: 游戏区 384x448 画到 (32,16), 右侧留 HUD 区
        self._renderer.resize(Screen.PLAYING, self._scale)

    # ---- 游戏 ----
    def _run_game(self, inp: FrameInput) -> None:
        if self._game is None:
            self._finished = True
            return
        menu_actions = inp.menu_actions
        # Esc → 暂停(冻结 tick; 本帧直接画冻结画面, Esc 的 BACK 不立即触发 Resume)
        # 回放播放中 Esc = 中止播放回标题(不进暂停菜单)
        # 续关菜单中 Esc 无效(C: isInRetryMenu!=0 时不开暂停菜单, GameManager.cpp:128)
        if inp.esc and not self._paused \
                and not getattr(self._game, "game_over", False):
            if self._playback is not None:
                log.debug("回放播放中止 (Esc, frame={})",
                          getattr(self._game, "frame", "?"))
                self._quit_playback()
                return
            if self._spectate is not None:
                # 观战中止: Esc 直接退出(不弹暂停菜单, 观战无标题可回)
                log.debug("观战中止 (Esc, frame={})",
                          getattr(self._game, "frame", "?"))
                self._finished = True
                return
            self._paused = True
            self._pause_cursor.index = 0
            self._pause_confirm = None
            log.trace("Esc 暂停键 (frame={})", getattr(self._game, "frame", "?"))
            # BGM 暂停 (GameManager.cpp:141 PushCommand(AUDIO_PAUSE);
            # 原版 6 面 BGM 延迟 300 帧前不推, 本作进关即播故无此门控)
            self._sound.pause_music()
            self._play_se(SE.SOUND_37)  # se_pause (SoundPlayer.cpp 暂停音)
            # 开暂停的这帧把 Esc 映射的 BACK 滤掉, 否则同帧又触发 Resume 闪退
            menu_actions = tuple(a for a in menu_actions
                                 if a != MenuAction.BACK)
        if self._paused:
            self._run_pause(menu_actions)
            return
        game = self._game
        msg_active = getattr(game, "msg_vm", None) is not None \
            and game.msg_vm.has_current_msg_idx()
        if msg_active != self._prev_msg_active:
            log.debug("对话门控变化: msg_active={} (frame={}, idx={}) — 射击/炸弹{}",
                     msg_active, game.frame,
                     game.msg_vm.current_msg_idx if game.msg_vm else None,
                     "禁用" if msg_active else "恢复")
            self._prev_msg_active = msg_active
        # 对话中: 射击键推进对话, skip 键(C 的 SKIP 键)快进; 炸弹键在对话中被门控
        # 按住状态来自后端采集的动作名集合(FrameInput.held)
        held = inp.held
        skip = msg_active and "skip" in held
        bomb_pressed = "bomb" in held  # 默认 X/小键盘1(IME 备用)/J
        if bomb_pressed and not self._prev_bomb_pressed:
            log.trace("bomb 键按下 (frame={}, msg_active={}, bombs={}, "
                      "bomb_in_use={}, border={})", game.frame, msg_active,
                      game.globals.bombs_remaining, game.bomb.is_in_use,
                      game.border.has_border)
        self._prev_bomb_pressed = bomb_pressed
        shot_held = "shoot" in held  # 默认 Z/小键盘0(IME 备用)
        if shot_held != self._prev_shot_held:
            log.trace("射击键{} (frame={}, msg_active={}, fire_time={})",
                      "按下" if shot_held else "松开", game.frame, msg_active,
                      game.player.fire_time)
            self._prev_shot_held = shot_held
        if inp.advance:
            log.trace("对话推进 advance (frame={}, msg idx={})", game.frame,
                      game.msg_vm.current_msg_idx if game.msg_vm else None)
        keys6 = (
            "left" in held,    # 默认 ←/A
            "right" in held,   # 默认 →/D
            "up" in held,      # 默认 ↑/W
            "down" in held,    # 默认 ↓/S
            "focus" in held,   # 默认 Shift(低速)
            shot_held,         # 射击(按住); 原版键位 Z=shot
        )
        if self._playback is not None:
            # 回放播放: 输入逐帧来自录像(真实键盘只认 Esc, 已在上面处理)
            pb = self._playback
            if pb["idx"] >= len(pb["codes"]):
                log.debug("回放播完 ({} 帧, {}) → 回标题", pb["idx"], pb["name"])
                self._quit_playback()
                return
            keys6, bomb_pressed, adv, skip = replay_mod.decode_input(
                pb["codes"][pb["idx"]])
            pb["idx"] += 1
            game.tick(keys=keys6, bomb=bomb_pressed,
                      advance=adv and msg_active, skip=skip)
        elif self._spectate is not None:
            # 观战: 本帧输入来自策略(policy 的实参 = 包 live 对局的 Game 门面,
            # 观测面与自建 Game 一致); 键盘仅保留 Esc(上面已处理)。
            # advance 与正常路径同按对话门控; 录制复用既有路径(记实际喂入)
            pi = self._spectate(self._spectate_facade)
            keys6 = pi._keys()
            bomb_pressed = pi.bomb
            adv = pi.advance and msg_active
            game.tick(keys=keys6, bomb=bomb_pressed, advance=adv, skip=pi.skip)
            if self._recorder is not None:
                self._recorder.record(keys6, bomb_pressed, adv, pi.skip)
        else:
            game.tick(keys=keys6, bomb=bomb_pressed,
                      advance=inp.advance and msg_active, skip=skip)
            # 回放录制: 记本帧实际喂给 tick 的输入(与播放路径同构)
            if self._recorder is not None:
                self._recorder.record(keys6, bomb_pressed,
                                      inp.advance and msg_active, skip)
        # 关卡主题曲: 进关/换关播 stage.bgm_paths[0]
        # (GameManager.cpp:785-794 AddedCallback LoadAudio(0)+PlayLoadedAudio(0);
        # 原版 6 面延迟 300 帧(Gui.cpp:140-142), 这里统一进关即播, 近似)
        stage_no = getattr(game, "stage_no", 1)
        if stage_no != self._bgm_stage:
            self._bgm_stage = stage_no
            paths = getattr(getattr(game, "stage", None), "bgm_paths", ())
            main_bgm = next((p for p in paths if p), "")
            if main_bgm:
                self._sound.play_music(main_bgm.split("/")[-1])
        # 本帧音效/BGM 事件(引擎帧末快照, ProcessQueues 对应)
        self._sound.play_frame(getattr(game, "frame_sounds", []),
                               getattr(game, "frame_bgm", []),
                               getattr(getattr(game, "stage", None), "bgm_paths", ()))
        # GameOver 续关菜单 (AsciiManager.cpp RetryMenu): 可续关时画面冻结
        # (impl.tick 在 game_over 早退), 等 Yes/No; 不可续关的局
        # (Extra/Phantasm/次数用尽) impl.tick 已直接进结算
        if getattr(game, "game_over", False) \
                and getattr(game, "result", None) is None \
                and getattr(game, "continue_available", False):
            if self._practice_stage is not None:
                # Practice 不可续关 (C++ practice 跳过 retry 菜单 → curState=6
                # 结算; 本作 practice 简化回标题, 结算照走保持 store 入账一致)
                log.debug("Practice GameOver (frame={}) → 回标题", game.frame)
                game.finalize_game_over()
                self._finish_practice()
                return
            if self._playback is not None:
                # 回放不可续关 (C++ replay 跳过 retry 菜单 → curState=7 回主菜单)
                log.debug("回放播到 GameOver (frame={}) → 回标题", game.frame)
                self._quit_playback()
                return
            if self._spectate is not None:
                # 观战不可续关: 等价选 No → 结算 → 结束观战
                log.debug("观战 GameOver (frame={}) → 结算退出", game.frame)
                game.finalize_game_over()
                self._finished = True
                return
            self._run_continue_menu(menu_actions)
            return
        # Practice: 练习关打完直接回标题(不进结算/排行榜)。
        # 通关判定: 1-5 面 clear → _advance_stage 换关(stage_no 前进),
        # 6 面 clear → ending; GameOver → result。(原版 practice 通关亦
        # 不进 Hscr 排行榜, GameManager.cpp:459-465 用 pscr 当最高分)
        if self._practice_stage is not None:
            if (stage_no != self._practice_stage
                    or getattr(game, "result", None) is not None
                    or getattr(game, "ending", None) is not None):
                log.debug("Practice 结束(进练习面 {}, 现 stage={}) → 回标题",
                         self._practice_stage, stage_no)
                self._finish_practice()
                return
        # 6 面通关 → 结局画面(impl.tick 填 ending, 看完 → 总结算)
        if getattr(game, "ending", None) is not None:
            if self._playback is not None:
                log.debug("回放播到结局 (frame={}) → 回标题", game.frame)
                self._quit_playback()
                return
            if self._spectate is not None:
                # 观战: 结局自动看完 → 总结算 → 结束观战
                log.debug("观战播到结局 (frame={}) → 结束观战", game.frame)
                game.finish_ending()
                self._finished = True
                return
            # 结局曲: .end 脚本 @m 指令(Ending.cpp:300 LoadAudio(0)+PlayLoadedAudio(0),
            # 如 end00.end → bgm/th07_14.mid); 没有 @m 则淡出
            ending_bgm = getattr(game.ending, "music", None)
            if ending_bgm:
                self._sound.play_music(ending_bgm)
            else:
                self._sound.fadeout_music(2.0)
            self._screen = Screen.ENDING
            self._menu_frame = 0
            # 结局画面 640x480, 切回标题尺寸
            self._renderer.resize(self._screen, self._scale)
            return
        # 通关(Extra/Phantasm/结局后)/GameOver → 结算(impl.tick 填 result)
        if getattr(game, "result", None) is not None:
            if self._playback is not None:
                # 播放模式不进结算(不写榜), 直接回标题
                log.debug("回放播到结算 (frame={}) → 回标题", game.frame)
                self._quit_playback()
                return
            if self._spectate is not None:
                # 观战不进结算画面(不写榜), 直接结束观战
                log.debug("观战到结算 (frame={}) → 结束观战", game.frame)
                self._finished = True
                return
            self._enter_result()
            return
        self._renderer.render_game(game)

    # ---- 游戏内暂停(冻结 tick 与渲染推进; WAV BGM 暂停, 原版 AUDIO_PAUSE) ----
    def _resume_from_pause(self) -> None:
        """退出暂停回游戏: 清确认态 + BGM 恢复
        (AsciiManager.cpp:666 PushCommand(AUDIO_UNPAUSE))。"""
        self._paused = False
        self._pause_confirm = None
        self._sound.unpause_music()

    def _run_pause(self, actions) -> None:
        for act in actions:
            if self._pause_confirm is not None:
                # 二次确认态(AsciiManager.cpp PauseMenu case 5-8):
                # 只有 Yes/No(原版此处不能 Save Replay), 默认停 No
                if act in (MenuAction.UP, MenuAction.DOWN):
                    self._pause_confirm_cursor.move(
                        1 if act == MenuAction.DOWN else -1)
                    self._renderer.play_menu_se("select")
                elif act == MenuAction.BACK:
                    # 原版 Esc 在任意暂停菜单态直接关菜单回游戏(:448-460)
                    self._renderer.play_menu_se("cancel")
                    self._resume_from_pause()
                elif act == MenuAction.CONFIRM:
                    if self._pause_confirm_cursor.current == "Yes":
                        pending = self._pause_confirm
                        self._pause_confirm = None
                        if pending == "Retry":
                            self._renderer.play_menu_se("ok")
                            self._retry_game()
                        else:  # Quit to Title
                            self._renderer.play_menu_se("cancel")
                            self._quit_to_title()
                            return  # 已切屏, 不再画暂停面板
                    else:  # No → 回暂停主菜单(原版回 case 2/3)
                        self._renderer.play_menu_se("cancel")
                        self._pause_confirm = None
                continue
            if act == MenuAction.UP:
                self._pause_cursor.move(-1)
                self._renderer.play_menu_se("select")
            elif act == MenuAction.DOWN:
                self._pause_cursor.move(1)
                self._renderer.play_menu_se("select")
            elif act == MenuAction.BACK:
                self._renderer.play_menu_se("cancel")
                self._resume_from_pause()
            elif act == MenuAction.CONFIRM:
                item = self._pause_cursor.current
                if item == "Resume":
                    self._renderer.play_menu_se("ok")
                    self._resume_from_pause()
                elif item == "Retry":
                    # 二次确认(AsciiManager.cpp case 3 → 7/8)
                    self._renderer.play_menu_se("ok")
                    self._pause_confirm = "Retry"
                    self._pause_confirm_cursor.index = 1  # 默认 No
                elif item == "Save Replay":
                    # 存本局到此刻的全部输入序列(简化: 随时可存, 存后继续玩)
                    self._renderer.play_menu_se("ok")
                    if self._recorder is not None:
                        path = self._recorder.save(
                            replay_mod.new_replay_name(self._replay_dir))
                        self._pause_hint = f"Saved {path.name} ({self._recorder.frames}f)"
                        self._pause_hint_timer = 150
                        log.debug("录像已保存: {} ({} 帧)", path,
                                 self._recorder.frames)
                    # 保持暂停, 让玩家看到 Saved 提示(再按 Resume/确认返回)
                elif item == "Quit to Title":
                    # 二次确认(AsciiManager.cpp case 2 → 5/6)
                    self._renderer.play_menu_se("ok")
                    self._pause_confirm = "Quit to Title"
                    self._pause_confirm_cursor.index = 1  # 默认 No
        if self._screen != Screen.PLAYING:
            return  # Retry 重建中防御(正常仍在 PLAYING)
        # Save Replay 反馈提示(瞬态); 冻结画面 + 半透明面板由后端合成
        hint = None
        if self._pause_hint_timer > 0:
            self._pause_hint_timer -= 1
            hint = self._pause_hint
        confirm = None
        if self._pause_confirm is not None:
            confirm = (self._pause_confirm, self._pause_confirm_cursor.index)
        self._renderer.render_pause(self._game, self._pause_cursor.index,
                                    hint=hint, confirm=confirm)

    def _run_continue_menu(self, actions) -> None:
        """GameOver 续关菜单 (RetryMenu::OnUpdate case 1/2): Yes/No 选择。

        画面冻结(impl.tick 在 game_over 早退), 中央叠 "Continue? Yes/No"
        (continue_view, ascii.anm pause.png 贴图); 原版无倒计时, 无限等待。
        Yes → continue_play 当场复活接着玩; No → finalize_game_over 进结算。
        """
        game = self._game
        if not self._in_continue:
            self._in_continue = True
            self._continue_cursor.index = 0  # 默认 Yes (C curState=1)
            # 续关菜单同样暂停 BGM (AsciiManager.cpp:852 RetryMenu AUDIO_PAUSE)
            self._sound.pause_music()
            log.debug("续关菜单弹出 (frame={}, 剩余续关={})",
                     getattr(game, "frame", "?"),
                     getattr(game, "max_retries", 0)
                     - game.globals.num_retries)
        for act in actions:
            if act in (MenuAction.UP, MenuAction.DOWN):
                self._continue_cursor.move(
                    1 if act == MenuAction.DOWN else -1)
                self._renderer.play_menu_se("select")  # SOUND_0 光标音
            elif act == MenuAction.CONFIRM:
                self._in_continue = False
                if self._continue_cursor.index == 0:
                    self._renderer.play_menu_se("ok")  # SOUND_SELECT 确定音
                    # 续关恢复 BGM (AsciiManager.cpp:999 AUDIO_UNPAUSE)
                    self._sound.unpause_music()
                    game.continue_play()
                    log.debug("续关 (numRetries={}, frame={})",
                             game.globals.num_retries,
                             getattr(game, "frame", "?"))
                else:
                    self._renderer.play_menu_se("cancel")
                    game.finalize_game_over()  # → result → 下帧进结算
                return  # 状态已变, 下帧走正常路径
        if self._screen != Screen.PLAYING:
            return  # 防御(正常仍在 PLAYING)
        self._renderer.render_continue(
            game, self._continue_cursor.index,
            getattr(game, "max_retries", 0) - game.globals.num_retries)

    def _retry_game(self) -> None:
        """暂停菜单 Retry: 重开本关(同难度同机体同 stage 重建 game)。"""
        self._paused = False
        self._pause_confirm = None
        # 原版不推 AUDIO_UNPAUSE(重开后关卡曲重播); 这里先解除暂停态,
        # 否则同名关卡曲 play_music 早退会让 BGM 一直停在暂停态
        self._sound.unpause_music()
        stage = getattr(self._game, "stage_no", 1)
        if self._practice_stage is not None:
            # Practice Retry: 重开同一练习面 + practice 难度页的难度
            dif = self._diff_index(self._practice_diff.current)
            self._start_game(stage=self._practice_stage, difficulty=dif)
            return
        self._start_game(extra_stage=self._run_extra_stage,
                         stage=None if self._run_extra_stage else stage)

    def _quit_to_title(self) -> None:
        """暂停菜单 Quit to Title: 弃局回标题主菜单(标题曲由 _enter_main_menu 播)。"""
        self._paused = False
        self._pause_confirm = None
        # 原版不推 AUDIO_UNPAUSE(标题曲 PlayAudio 直接重播); 同步解除暂停态
        self._sound.unpause_music()
        self._in_continue = False
        self._game = None
        self._recorder = None
        self._practice_stage = None
        self._practice_mode = False
        self._enter_main_menu()  # 游戏/标题窗口同为 640x480×scale, 无需 resize

    # ---- 回放播放(录像选择 → 重建 game → 逐帧喂录像输入) ----
    def _start_replay(self, entry: dict) -> None:
        """播放选中的录像: 按 meta 重建同难度/机体/种子的 game, 逐帧喂输入。

        确定性依赖: impl.tick 只由输入帧 + 种子驱动(engine/replay.py);
        残机按录像的 initial_lives 覆写(与录制时 _start_game 的覆写同位)。
        """
        try:
            r = replay_mod.load_replay(entry["path"])
        except ValueError as e:
            log.warning("录像加载失败: {}", e)
            return
        meta = r["meta"]
        dif = int(meta.get("difficulty", 1)) % len(self._difficulties)
        ch = int(meta.get("character", 0)) % len(self._characters)
        stage = int(meta.get("stage", 1))
        seed = int(meta.get("seed", 0x5EED))
        log.debug("播放录像 {}: character={} difficulty={} stage={} seed={} "
                  "frames={}", entry["path"].name, ch, dif, stage, seed,
                  len(r["codes"]))
        self._char.index = ch  # _start_game 用 self._char.current 定机体
        if stage >= 7:
            self._start_game(extra_stage=stage, seed=seed, record=False)
        else:
            self._start_game(stage=None if stage <= 1 else stage,
                             difficulty=dif, seed=seed, record=False)
        g0 = getattr(self._game, "globals", None)
        if g0 is not None and dif < 4 and hasattr(g0, "lives_remaining"):
            g0.lives_remaining = float(meta.get("initial_lives", 3))
        self._playback = {"codes": r["codes"], "idx": 0,
                          "name": entry["path"].name}

    def _quit_playback(self) -> None:
        """退出回放播放(Esc 中止/播完/出结算) → 回标题主菜单。"""
        self._playback = None
        self._quit_to_title()

    def _play_se(self, idx: int) -> None:
        """游戏内 SE(SoundPlayer 已加载的表); 未加载/无声卡静音跳过。"""
        snd = self._sound.sounds.get(int(idx))
        if snd is not None:
            try:
                snd.play()
            except Exception:
                pass

    # ---- 结局画面 ----
    def _run_ending(self, actions) -> None:
        self._menu_frame += 1
        game = self._game
        ef = self._renderer.render_ending(game.ending, self._menu_frame)
        # 脚本内音乐事件: @m 切曲 (Ending.cpp:298-301) / @M 淡出 (:302-306,
        # 参数是秒 —— C++ FadeOutMusic(f32) 直接吃该值, 如 @M5 → 5 秒)
        for ev in ef.music:
            if ev[0] == "play" and ev[1] != self._sound.current_bgm:
                self._sound.play_music(ev[1])
            elif ev[0] == "fadeout":
                self._sound.fadeout_music(float(ev[1]))
        # 播完 (@z; 结局 → @F staff00.end staff roll → @z) → 自动进总结算
        # (Ending 链移除 → DeletedCallback curState=6, Ending.cpp:520)
        if ef.finished:
            log.debug("结局播完 → 总结算 (character={} bad={})",
                      game.ending.character, game.ending.bad)
            game.finish_ending()
            self._enter_result()
            return
        for act in actions:
            if act == MenuAction.CONFIRM:
                # 确认 → 跳过整段直接总结算(简化: 原版为快进/跳行)
                self._renderer.play_menu_se("ok")
                game.finish_ending()
                self._enter_result()

    # ---- 结算画面 ----
    def _enter_result(self) -> None:
        self._screen = Screen.RESULT
        self._result_saved = False
        self._result_save = None
        self._result_save_cursor.index = 0   # 原版默认 Yes (state 11 cursor=0)
        self._result_save_msg = ""
        self._menu_frame = 0
        # 入榜 → 名字输入态(ResultScreen.cpp HandleResultKeyboard: LinkScoreEx
        # < 10 才进输入, 否则直接 state16); 初始名带 LSNM
        self._name_entry = None
        game = self._game
        res = getattr(game, "result", None)
        store = getattr(game, "store", None)
        if res is not None and store is not None and res.get("rank", -1) >= 0:
            self._name_entry = NameEntryFlow(
                initial=store.last_name, has_lsnm=store.lsnm is not None)
        # 结算曲: init.mid (Supervisor.cpp:713 槽30, GameManager::DeletedCallback
        # PlayLoaded(30)); staff roll 已在结局画面内由 @F staff00.end 续播
        self._sound.play_music(_RESULT_BGM)
        # 结算画面 640x480, 切回标题尺寸
        self._renderer.resize(self._screen, self._scale)

    def _can_save_result_replay(self, game) -> bool:
        """结算画面能否存录像(ResultScreen.cpp:1364-1376):
        续关过(numRetries != 0)不能存(interrupt 14 提示); 慢放/计时异常
        也不能存(interrupt 19, 本作无此判定, 略)。无录制器/0 帧自然不能存。
        """
        if self._recorder is None or self._recorder.frames == 0:
            return False
        res = getattr(game, "result", None) or {}
        return res.get("retries", 0) == 0

    def _result_next_step(self, game) -> None:
        """名字输入完/未入榜确认后的下一步: 可存录像 → Save Replay? 询问
        (ResultScreen state 16→17→11); 不可存 → 直接收尾回标题。"""
        if self._can_save_result_replay(game):
            self._result_save = "ask"
            self._result_save_cursor.index = 0
        else:
            self._save_result_and_exit(game)

    def _save_result_and_exit(self, game) -> None:
        """结算收尾: 保存 score.json(原子写, 只写一次) → 回标题主菜单。"""
        if not self._result_saved:
            try:
                game.store.save(self._score_path)
            except OSError:
                pass  # 写盘失败不炸(容错同 score_store)
            self._result_saved = True
        self._renderer.play_menu_se("ok")
        self._game = None
        self._recorder = None   # 录像不留到下一局(原版出 ResultScreen 即弃)
        self._name_entry = None
        self._result_save = None
        self._enter_main_menu()

    def _run_result(self, actions) -> None:
        self._menu_frame += 1
        game = self._game
        replay_save = None
        if self._result_save == "ask":
            replay_save = ("ask", self._result_save_cursor.index, "")
        elif self._result_save == "saved":
            replay_save = ("saved", -1, self._result_save_msg)
        self._renderer.render_result(game.result, self._menu_frame,
                                     store=getattr(game, "store", None),
                                     name_entry=self._name_entry,
                                     replay_save=replay_save)
        for act in actions:
            if self._result_save is not None:
                # Save Replay 流程(ResultScreen.cpp HandleReplaySaveKeyboard)
                if self._result_save == "ask":
                    # state 11: Yes/No 选择(原版左右切换, 上下也接受)
                    if act in (MenuAction.LEFT, MenuAction.RIGHT,
                               MenuAction.UP, MenuAction.DOWN):
                        self._result_save_cursor.move(1)
                        self._renderer.play_menu_se("select")
                    elif act == MenuAction.CONFIRM:
                        if self._result_save_cursor.current == "Yes":
                            # state 13/14 选槽+命名 → SaveReplay 的简化:
                            # 自动文件名直存, 存完显示确认信息
                            self._renderer.play_menu_se("ok")
                            path = self._recorder.save(
                                replay_mod.new_replay_name(self._replay_dir))
                            self._result_save_msg = (
                                f"{path.name} ({self._recorder.frames}f)")
                            log.debug("结算画面录像已保存: {} ({} 帧)", path,
                                      self._recorder.frames)
                            self._result_save = "saved"
                        else:  # No → 不存, 收尾回标题(state 11 BACK → state 2)
                            self._save_result_and_exit(game)
                            break
                    elif act == MenuAction.BACK:
                        # 原版 BACK/MENU = 不存直接退出(SOUND_BACK_AND_RETURN)
                        self._save_result_and_exit(game)
                        break
                else:  # "saved": 任意确认/返回 → 收尾回标题
                    if act in (MenuAction.CONFIRM, MenuAction.BACK):
                        self._save_result_and_exit(game)
                        break
                continue
            if self._name_entry is not None:
                # 名字输入态: 上下换字/左右移字表光标, Z 写槽, X 删除;
                # END(或输满 8 槽后在 END 上确认) → 定名 + 保存 + 回标题
                ev = self._name_entry.handle(act)
                if ev is None:
                    continue
                kind = ev["action"]
                if kind == "finish":
                    name = ev["name"]
                    res = game.result
                    game.store.set_entry_name(res["difficulty"],
                                              res["character"],
                                              res["rank"], name)
                    game.store.set_last_name(name)  # LSNM (:1321-1322)
                    res["name"] = name
                    self._name_entry = None   # 名字输入态结束(→ state 16)
                    self._result_next_step(game)  # → Save Replay? / 收尾
                    break  # 已定名, 余下动作不再喂旧 game
                elif kind == "move":
                    self._renderer.play_menu_se("select")  # SOUND_MOVE_MENU
                elif kind == "input":
                    self._renderer.play_menu_se("ok")      # SOUND_SELECT
                elif kind == "delete":
                    self._renderer.play_menu_se("cancel")  # SOUND_BACK
            elif act == MenuAction.CONFIRM:
                # 未入榜: 确认 → Save Replay? 询问(可存时) / 保存回标题
                self._result_next_step(game)
                break
