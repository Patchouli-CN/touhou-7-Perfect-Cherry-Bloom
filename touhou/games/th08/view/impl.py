"""th08 窗口版应用壳 —— 场景状态机 + 游戏流程, 与渲染后端解耦。

照 th07 (games/th07/view/impl.py) 改编的最小可用版: 标题主菜单 → 选难度 →
选机体 → 进入游戏(本篇 6 面 + Extra=9); 游戏内 Esc 暂停(Resume/Retry/
Quit to Title + 二次确认)、GameOver 续关菜单、通关/GameOver 结算。

标题主菜单已原作化(A 期): 9 项名单/title01.anm 成对 sprite/置灰锁定/
title00.png 背景/70 帧白淡入/底部帮助行, 见本包 title_flow.py(纯逻辑)与
title_view.py(渲染)。难度/机体/Extra 选择已原作化(B2 期): select00.png
背景 + title01.anm 难度项/头像/名牌 vm + 通关标记, 见 title_flow.py
(CharacterFlowTh08/completion_mark_sprite)与 select_view.py。
Option/KeyConfig 已原作化(C 期): OptionFlowTh08/KeyConfigFlowTh08(本包
title_flow.py) + option_view.py 贴图渲染(行标签/残机档/音量数字 vm +
键名绘字), Graphic/SlowMode 等无引擎对应物的行置灰锁定(偏离注明)。
Music Room 已原作化(C 期第 2 片): music_flow.MusicRoomFlowTh08(纯逻辑:
10 行滚动窗口/解锁隐藏/SKIP 淡出/RESET 重播) + music_view.MusicRoomView
(music.jpg 背景 + music00.anm 主装饰 vm + 曲名/简介直接绘字)。
Result 浏览面已原作化(C 期第 3 片): result_flow.ResultFlowTh08(纯逻辑:
类别/难度/机体三级选择 + 高分榜/符卡战绩/统计取数) +
result_view.ResultBrowseView(result.jpg 背景 + result00.anm 贴图 vm +
榜单/统计直接绘字); 入榜名字输入/replay 保存流(结算 GAME_RESULT 模式)
不在其内。一期遗留范围: Replay/Practice/Spell Practice
画面(菜单项给"未实装"提示)、对话立绘、符卡宣言、结局画面(world 出 ending
时直接 finish_ending 跳总结算)、录像录制/播放、入榜名字输入(结算直接存档回标题)。

渲染/输入采集委托 Renderer 后端(协议见 engine/render/__init__.py);
默认后端是本包 pygame_backend.PygameTh08Renderer(自持, 不进全局
register_renderer —— "pygame" 名被 th07 占用), 测试可注入实例。
主菜单 flow 是本包 title_flow.TitleFlowTh08(th08 的 9 项名单);
暂停/续关菜单复用 games/th07/view/screens.py 的作品无关零件
(MenuCursor/MenuAction/Screen); 名单/面数经 ``game_data`` 参数
(registry.GameData, TH08_DATA)。
"""

from __future__ import annotations

import time

from typing import TYPE_CHECKING

from ....logger import logger as log

from ....apis.basic import Game
from ....registry import GameData, get_game, register_app
from ....engine.config import DEFAULT_CONFIG_PATH, GameConfig
from ....engine.render import FrameInput, Renderer
from ....engine.view.sound_player import SoundPlayer
from ....engine.view.sprite_bank import SpriteBank
from ....paths import DEFAULT_SCORE_PATH, resolve_data_path
from ...th07.view.screens import (
    PAUSE_CONFIRM_ITEMS,
    PAUSE_ITEMS,
    MenuAction,
    MenuCursor,
    Screen,
)
from ..crypt import try_decrypt_from_table
from ..progress import (
    NUM_TEAMS,
    TITLE_BGM_INDEX,
    is_extra_unlocked_for_character,
    is_extra_unlocked_with_all_teams,
    load_score_store,
    unlock_bgm,
)
from ..sound import SE, SE_FILES, SE_VOLUMES
from .music_flow import MusicRoomFlowTh08, load_tracks
from .pygame_backend import PygameTh08Renderer
from .result_flow import ResultFlowTh08
from .title_flow import (
    CURSOR_FROM_GAME,
    CURSOR_FROM_MUSIC_ROOM,
    CURSOR_FROM_RESULT,
    ITEM_OPTION,
    OPTION_ROW_KEYCONFIG,
    CharacterFlowTh08,
    KeyConfigFlowTh08,
    OptionFlowTh08,
    TitleFlowTh08,
    completion_mark_sprite,
    unlock_flags,
)

if TYPE_CHECKING:
    from ....engine.score_store import ScoreStore

# 标题画面 BGM (TitleScreen.cpp:869/1036/3796: LoadMusic(8,"bgm/th08_01.mid"))
_TITLE_BGM = "th08_01.mid"
# 结算画面 BGM (Supervisor.cpp:737: ReadFileData(30,"bgm/init.mid"))
_RESULT_BGM = "init.mid"
# th08 Extra 面 stage_no (world: stage_no 1..9 = C currentStage+1, 9=EX)
_EXTRA_STAGE_NO = 9

_UNIMPLEMENTED_HINT_FRAMES = 90

# 进标题的 70 帧白淡入(TitleScreen.cpp:3800-3807 注册 SCREEN_EFFECT_FULL_FADE_IN)
_TITLE_FADE_FRAMES = 70

# 菜单空转预热清单(同 th07 _WARMUP_ANMS 的意图; 机体 anm 全预载:
# 开局选择未定; 菜单停留期间摊到每帧一项)
_WARMUP_ANMS = (
    "ascii.anm",
    "front.anm",
    "times.anm",
    "etama.anm",
    "enemy.anm",
    "stg1enm.anm",
    "stg1bg.anm",
    "player00.anm",
    "player01.anm",
    "player02.anm",
    "player03.anm",
)

# 主菜单未实装的子系统(选中给提示; 见模块 docstring 的一期遗留范围)
_UNSUPPORTED_ACTIONS = (
    "spell_practice",
    "practice",
    "replay",
)


@register_app("th08")
class GameApp:
    """th08 窗口应用: 标题菜单 + 游戏流程(渲染/输入由后端承担)。

    经 ``@register_app("th08")`` 登记到 registry; ``TouhouWorld.run()``
    (headless=False) 按作品名解析本类。构造契约(register_app):
    ``GameApp(make_game, *, data_path, bgm_path, game_data)`` —— 其余关键字
    参数均有默认值, 契约是关键字子集。
    """

    def __init__(
        self,
        make_game,
        *,
        scale: int | None = None,
        data_path=None,
        score_path=None,
        config_path=None,
        bgm_path=None,
        game_data: GameData | None = None,
        renderer: "Renderer | None" = None,
        spectate=None,
    ) -> None:
        data_path = resolve_data_path(data_path, game="th08")
        self._data_path = data_path
        self._make_game = make_game
        # 观战模式(registry.register_app 的可选契约): spectate 非 None 时是
        # "facade(Game) -> Input" 的逐帧策略 —— run() 跳过标题菜单直接开局,
        # 每帧输入由策略产出(键盘仅保留 Esc 中止观战/关窗退出);
        # 角色/难度/残机/种子以对局构造时注入的值为准。None = 正常键盘游玩
        self._spectate = spectate
        self._spectate_facade: Game | None = None  # 包局内 live 对局的门面
        # 渲染后端: None = 自持的 pygame 后端(不进 renderer 注册表);
        # 或直接传实现实例(测试插桩用)
        self._renderer: Renderer = (
            renderer if renderer is not None else PygameTh08Renderer(data_path)
        )
        # 设置(config.json): 缺失/损坏回退默认(engine/config.py 容错)
        if config_path is None:
            config_path = DEFAULT_CONFIG_PATH
        self._config_path = config_path
        self._config = GameConfig.load(config_path)
        self._scale = scale if scale is not None else self._config.window_scale
        # 启动直接显示标题主菜单(初始光标 0 = Start,
        # TitleScreen.cpp:3695-3696 wantedState2 默认分支)
        self._screen = Screen.MAIN_MENU
        self._flow = TitleFlowTh08()
        # Option/KeyConfig 画面 flow(C 期; 共享 config 实例, 改值即时生效)
        self._option_flow = OptionFlowTh08(config=self._config)
        self._keyconfig_flow = KeyConfigFlowTh08(config=self._config)
        # Music Room flow(C 期第 2 片; 进画面才建, 每进一次重读曲目表/解锁快照)
        self._music_flow: MusicRoomFlowTh08 | None = None
        # Result 浏览面 flow(C 期第 3 片; 进画面才建, 持存档快照)
        self._result_flow: ResultFlowTh08 | None = None
        self._title_fade = 0  # 进标题的白淡入帧计数(_TITLE_FADE_FRAMES 封顶)
        # 名单/面数: 作品数据表(game_data, 经 TouhouWorld 从 GameSpec.data 传入)
        # 优先; 缺省回落注册表 th08 表
        gd = game_data if game_data is not None else get_game("th08").data
        self._characters = list(gd.characters) if gd is not None else []
        self._difficulties = list(gd.difficulties) if gd is not None else []
        # 本篇 Start 难度(不含 Extra —— 那是额外关卡, 走 Extra Start 流程)
        main_n = gd.main_difficulty_count if gd is not None else 4
        self._main_difficulties = self._difficulties[:main_n]
        self._extra_stages = list(gd.extra_stages) if gd is not None else ["Extra"]
        self._diff = MenuCursor(self._main_difficulties, index=1)
        # 机体选择 flow(:1601-1854): menuLength 规则(12/4)由
        # _reload_title_unlocks 按存档重建 items; 初始先给全表
        self._char_flow = CharacterFlowTh08(
            cursor=MenuCursor(list(self._characters), index=0)
        )
        self._extra_mode = False  # Extra Start 流: 选 Extra → 选机体
        self._extra_stage = MenuCursor(self._extra_stages, index=0)
        # 难度/机体/Extra 屏的进屏帧计数(渲染用; frame==0 = 进场,
        # 对照原作 Init 的 SetInterruptArray 时机)
        self._menu_sub_frame = 0
        self._last_menu_screen = self._screen
        # score.json 落盘位置(与 world.py 同一来源, score_path 可覆盖, 测试用)
        if score_path is None:
            score_path = DEFAULT_SCORE_PATH
        self._score_path = score_path
        # 标题系画面的 store 快照(_reload_title_unlocks 填, 进标题才重读)
        self._title_store: ScoreStore | None = None
        # 进标题重读 score.json 的解锁态(ActualAddedCallback 每次进标题
        # 重开 score.dat, TitleScreen.cpp:3664-3675); 启动即标题, 构造时先读一次
        self._reload_title_unlocks()
        self._result_saved = False
        self._menu_frame = 0
        self._unimplemented_timer = 0
        self._game = None
        # SE/BGM(懒加载, 静音容错): th08 的 SE 表/game_id/edz 解密注入
        self._sound = SoundPlayer(
            data_path,
            bgm_path=bgm_path,
            se_files=SE_FILES,
            se_volumes=SE_VOLUMES,
            thbgm_game_id=0x800,  # th08-ref Supervisor.cpp:1426
            decrypt=try_decrypt_from_table,
        )
        self._sound.set_bgm_source(self._config.bgm_source)
        self._sound.set_bgm_volume(self._config.bgm_volume / 100)
        self._sound.set_se_volume(self._config.se_volume / 100)
        self._bgm_stage = 0  # 已播关卡曲的关卡号(换关切曲用)
        self._paused = False  # 游戏内暂停(Esc; 冻结 tick, WAV BGM 暂停)
        # 一期不接 Save Replay(录像二期), 暂停菜单 = Resume/Retry/Quit to Title
        self._pause_cursor = MenuCursor(PAUSE_ITEMS[:2] + PAUSE_ITEMS[3:], index=0)
        self._pause_confirm: str | None = None
        self._pause_confirm_cursor = MenuCursor(PAUSE_CONFIRM_ITEMS, index=1)
        # GameOver 续关菜单: 默认选 Yes
        self._continue_cursor = MenuCursor(["Yes", "No"], index=0)
        self._in_continue = False
        self._run_extra = False  # 本局是否 Extra(Retry 重开用)
        self._prev_bomb_pressed = False  # bomb 键沿检测(日志用)
        self._finished = False
        # 菜单空转预热(每帧一项, 同 th07 BUGS.md 增量#3)
        self._warmup: list[str] | None = None
        self._warmup_bank = None

    # ---- 键位映射(config.keymap → 后端输入映射) ----
    def _rebuild_keymap(self) -> None:
        """按当前 config.keymap 重建后端的动作/菜单键映射。"""
        self._renderer.set_keymap(self._config.keymap)

    # ---- 主循环(渲染/输入采集委托后端) ----
    def run(self) -> None:
        self._rebuild_keymap()
        self._renderer.open(scale=self._scale)
        log.info("th08 渲染后端就绪; config={}", self._config)
        self._sound.ensure_loaded()
        if self._spectate is not None:
            # 观战: 跳过标题状态机直接开局(角色/难度由 make_game 包装层定)
            log.debug("观战模式: 跳过标题直接开局")
            self._start_game()
        elif self._screen == Screen.MAIN_MENU:
            self._sound.play_music(_TITLE_BGM)
        running = True
        while running:
            inp = self._renderer.poll_input(
                capturing=self._screen == Screen.KEY_CONFIG
                and self._keyconfig_flow.capturing is not None
            )
            if inp.quit:
                running = False
            if inp.captured_key is not None and self._screen == Screen.KEY_CONFIG:
                # KeyConfig "按新键"捕获: 该键已被后端吃掉, 不进菜单动作
                self._keyconfig_capture(inp.captured_key)
            if self._screen == Screen.MAIN_MENU:
                self._run_title_menu(inp.menu_actions)
            elif self._screen in (
                Screen.DIFFICULTY,
                Screen.CHARACTER,
                Screen.EXTRA_LEVEL,
            ):
                self._run_menu(inp.menu_actions)
            elif self._screen == Screen.OPTION:
                self._run_option(inp)
            elif self._screen == Screen.KEY_CONFIG:
                self._run_keyconfig(inp)
            elif self._screen == Screen.MUSIC_ROOM:
                self._run_music_room(inp)
            elif self._screen == Screen.PLAYER_DATA:
                self._run_result_browse(inp)
            elif self._screen == Screen.RESULT:
                self._run_result(inp.menu_actions)
            else:  # playing
                self._run_game(inp)
                if self._finished:
                    running = False
            # WAV BGM 循环点回卷轮询: 与场景无关每帧跑
            self._sound.poll_loop()
            # 菜单空转预热: 每帧一项
            if self._screen != Screen.PLAYING:
                self._warmup_step()
            self._renderer.present()
        self._renderer.close()

    # ---- 菜单空转预热 ----
    def _warmup_step(self) -> None:
        """菜单场景每帧预载一项对局资源(进程级共享缓存, 开局命中即免费)。"""
        if self._warmup is None:
            self._warmup = list(_WARMUP_ANMS)
        if not self._warmup:
            return
        if self._warmup_bank is None:
            self._warmup_bank = SpriteBank(self._data_path, game="th08")
        self._warmup_bank.has(self._warmup.pop(0))

    # ---- 标题主菜单(th08 自持 9 项 flow; 未实装子系统给提示) ----
    def _run_title_menu(self, actions) -> None:
        self._menu_frame += 1
        if self._unimplemented_timer > 0:
            self._unimplemented_timer -= 1
        fade = self._title_fade if self._title_fade < _TITLE_FADE_FRAMES else None
        self._renderer.render_title(
            self._flow,
            self._menu_frame,
            show_unimplemented=self._unimplemented_timer > 0,
            fade_frame=fade,
        )
        self._title_fade = min(self._title_fade + 1, _TITLE_FADE_FRAMES)
        for act in actions:
            self._on_menu(act)

    # ---- 难度/机体/Extra 菜单 ----
    def _run_menu(self, actions) -> None:
        if self._screen != self._last_menu_screen:
            self._last_menu_screen = self._screen
            self._menu_sub_frame = 0
        frame = self._menu_sub_frame
        self._menu_sub_frame += 1
        if self._screen == Screen.DIFFICULTY:
            self._renderer.render_difficulty(
                self._diff.index, items=self._main_difficulties, frame=frame
            )
        elif self._screen == Screen.CHARACTER:
            # 通关标记只画主 CharacterSelect(OnDraw :3594-3596), Extra 变体不画
            mark = None
            if not self._extra_mode and self._title_store is not None:
                mark = completion_mark_sprite(
                    self._title_store,
                    self._char_flow.cursor.index,
                    self._char_flow.difficulty,
                )
            self._renderer.render_character(
                self._char_flow, completion=mark, frame=frame
            )
        else:  # Screen.EXTRA_LEVEL = 原作的 DifficultySelectExtra 单项画面
            self._renderer.render_extra(
                self._extra_stage.index, items=self._extra_stages, frame=frame
            )
        for act in actions:
            self._on_menu(act)

    def _enter_main_menu(self) -> None:
        """回标题主菜单: 切屏 + 标题曲。"""
        log.debug("切屏 → 标题主菜单")
        self._screen = Screen.MAIN_MENU
        self._sound.play_music(_TITLE_BGM)

    def _enter_title_scene(self, cursor: int) -> None:
        """跨场景进标题(游戏中退出/结算回来): 初始光标 + 重读解锁态 +
        70 帧白淡入。对照 ActualAddedCallback: 重开 score.dat
        (TitleScreen.cpp:3664-3675) + wantedState2 定初始光标(:3682-3698)
        + TitleSetupThread 注册白淡入(:3800-3807)。
        标题内子画面往返(难度/机体 BACK)不走这里 —— 光标保留不重置。
        """
        self._flow.cursor.index = cursor
        self._reload_title_unlocks()
        self._title_fade = 0
        self._enter_main_menu()

    def _reload_title_unlocks(self) -> None:
        """重读 score.json 更新标题系画面的存档快照: Extra/Spell Practice
        解锁态(置灰/跳过依据, 判定语义见 progress 的 5 个判定函数)、
        机体选择 menuLength(:1604/:1618, 全 4 组 Extra 解锁 → 12 项否则 4 项)
        与 Extra 流的逐机体解锁表(:1641-1648 跳过用); 标题曲播放即解锁
        Music Room 0 号曲(TitleScreen.cpp:293 PlayMusic(8, 0) →
        Supervisor.cpp:1579 置位), 有变化才落盘。
        对照 ActualAddedCallback: 只在进标题时重开 score.dat
        (TitleScreen.cpp:3664-3675), 子画面间不每帧读盘。"""
        store = load_score_store(self._score_path)
        self._title_store = store
        self._flow.extra_unlocked, self._flow.spell_practice_unlocked = unlock_flags(
            store
        )
        # menuLength = IsExtraUnlockedWithAllTeams ? 12 : 4(TitleScreen.cpp:1604)
        n = len(self._characters)
        if not is_extra_unlocked_with_all_teams(store):
            n = min(n, NUM_TEAMS)
        flow = self._char_flow
        flow.cursor.items = list(self._characters[:n])
        flow.extra_unlocked = [
            is_extra_unlocked_for_character(store, c) for c in range(n)
        ]
        flow.clamp_cursor()  # :1698-1701
        if not store.plst["bgmUnlocked"][TITLE_BGM_INDEX]:
            unlock_bgm(store, TITLE_BGM_INDEX)
            try:
                store.save(self._score_path)
            except OSError:
                pass  # 写盘失败不炸(容错同 score_store)

    def _enter_character(self, extra: bool) -> None:
        """进机体选择(OnUpdateCharacterSelect Init :1616-1648): 变体/难度
        (Extra 流 = 4, :1516)记入 flow, 光标钳制(:1698-1701), Extra 流
        顺向跳过锁定机体(:1641-1648)。初始光标 = 上次选择(原作 =
        g_GameManager.shotType 持久, :1616; 这里 flow.cursor 持续在屏间保留)。"""
        flow = self._char_flow
        flow.extra = extra
        flow.difficulty = 4 if extra else self._diff_index(self._diff.current)
        flow.clamp_cursor()
        if extra:
            flow.skip_locked_forward()
        self._screen = Screen.CHARACTER

    # ---- Option 设置页(OnUpdateOptions, TitleScreen.cpp:644-1153) ----
    def _enter_option(self) -> None:
        """进 Option: 光标归零 + 闲置/长按计数清零 + 喂 play_count 快照
        (残机高档位解锁判定 :699-707/:826-844, attemptsTotal = plst 的
        play_count; 只在进画面时读一次, 同 _reload_title_unlocks 口径)。"""
        flow = self._option_flow
        flow.cursor.index = 0
        flow.idle_frames = 0
        flow.hold_left = flow.hold_right = 0
        store = self._title_store
        flow.play_count = (store.plst["play_count"] or 0) if store is not None else 0
        self._screen = Screen.OPTION

    def _leave_option(self) -> None:
        """退回主菜单, 光标落 Option 项(:1113 cursor=TITLE_MENU_ITEM_OPTION)。"""
        self._screen = Screen.MAIN_MENU
        self._flow.cursor.index = ITEM_OPTION

    def _run_option(self, inp: FrameInput) -> None:
        """Option 一帧: 渲染 → 菜单键 → 长按连调(:947-988)→ 闲置超时
        (:1086-1092, 3600 帧无输入退回主菜单)。frame==0 = 进屏(进场动画)。"""
        flow = self._option_flow
        if self._last_menu_screen != Screen.OPTION:
            self._last_menu_screen = Screen.OPTION
            self._menu_sub_frame = 0
        frame = self._menu_sub_frame
        self._menu_sub_frame += 1
        self._renderer.render_option(flow, frame=frame)
        actions = inp.menu_actions
        for act in actions:
            self._on_menu(act)
            if self._screen != Screen.OPTION:
                return  # 已切屏(KeyConfig/主菜单), 本帧不再推进 Option 状态
        # 音量行选中时每 50 帧一声提示音(:1070-1076 stateTimer%50 → SE 29)
        if flow.is_volume_row and frame > 0 and frame % 50 == 0:
            self._play_se(SE.SOUND_TIMEOUT)
        held = inp.held
        r = flow.tick_held("left" in held, "right" in held)
        if r is not None:
            self._apply_option(r["item"], r["value"])
        if flow.tick_idle(bool(actions) or bool(held)):
            self._renderer.play_menu_se("cancel")
            self._leave_option()

    def _apply_option(self, item: str, value) -> None:
        """Option 调值即时生效 + 落盘(BGM 切源重播 :856-870; Mode 映射
        window_scale 即时 resize 是偏离 —— 原作 Mode 是全屏切换且退出时
        才检查重启 :1118-1122, 我们无全屏支持)。"""
        if item == "Vol":
            self._sound.set_bgm_volume(value / 100)
        elif item == "S.E.Vol":
            self._sound.set_se_volume(value / 100)
        elif item in ("BGM", "reset"):
            # 切源后需停再播才生效(sound_player docstring; :856-870
            # StopAudio → 改 musicMode → 重播当前曲)
            current = self._sound.current_bgm
            self._sound.set_bgm_source(self._config.bgm_source)
            if current:
                self._sound.stop_music()
                self._sound.play_music(current)
        elif item == "Mode":
            self._scale = value
            self._renderer.resize(self._screen, self._scale)
        # Player(初始残机): 开局时应用(_start_game), 无即时动作
        self._save_config()

    def _save_config(self) -> None:
        """即时写 config.json(容错: 写盘失败不炸, 同 th07 impl.py:244)。"""
        try:
            self._config.save(self._config_path)
        except OSError:
            pass

    # ---- KeyConfig 键位设置页(OnUpdateKeyConfig, TitleScreen.cpp:1156-1402) ----
    def _enter_keyconfig(self) -> None:
        """进 KeyConfig: 光标归零(:1106) + 捕获态/闲置计数清零。"""
        flow = self._keyconfig_flow
        flow.cursor.index = 0
        flow.capturing = None
        flow.idle_frames = 0
        self._screen = Screen.KEY_CONFIG

    def _leave_keyconfig(self) -> None:
        """退回 Option, 光标落 KeyConfig 行(:1369 cursor=8)。"""
        self._keyconfig_flow.capturing = None
        self._screen = Screen.OPTION
        self._option_flow.cursor.index = OPTION_ROW_KEYCONFIG

    def _run_keyconfig(self, inp: FrameInput) -> None:
        """KeyConfig 一帧: 渲染 → 菜单键(捕获态下按键走 captured_key,
        run() 已分流)→ 闲置超时(:1345-1347, 3600 帧退回 Option)。"""
        flow = self._keyconfig_flow
        if self._last_menu_screen != Screen.KEY_CONFIG:
            self._last_menu_screen = Screen.KEY_CONFIG
            self._menu_sub_frame = 0
        frame = self._menu_sub_frame
        self._menu_sub_frame += 1
        self._renderer.render_keyconfig(flow, frame=frame)
        if flow.capturing is not None:
            # 捕获中只跑闲置超时; 正常按键已由 captured_key 路径处理
            if flow.tick_idle(False):
                flow.capturing = None
                self._renderer.play_menu_se("cancel")
                self._leave_keyconfig()
            return
        actions = inp.menu_actions
        for act in actions:
            self._on_menu(act)
            if self._screen != Screen.KEY_CONFIG:
                return
        if flow.tick_idle(bool(actions)):
            self._renderer.play_menu_se("cancel")
            self._leave_keyconfig()

    def _keyconfig_capture(self, name: str) -> None:
        """捕获态收一个键名(th07 口径: 即时生效 + 落盘; Esc/X 取消的
        判定在 flow.capture)。"""
        flow = self._keyconfig_flow
        r = flow.capture(name)
        flow.idle_frames = 0
        log.debug("KeyConfig 捕获: {} 键={} → {}", r.get("item"), name, r["action"])
        se = r.get("se")
        if se is not None:
            self._renderer.play_menu_se(se)
        if r["action"] == "changed":
            self._rebuild_keymap()
            self._save_config()

    # ---- Music Room(MusicRoom.cpp RegisterChain :245 / OnUpdate :270) ----
    def _enter_music_room(self) -> None:
        """进 Music Room(AddedCallback :392): 解析 musiccmt.txt 曲目表 +
        从存档快照拷 bgmUnlocked(:528; 当次会话内不刷新, 曲名贴字原作就是
        进场烘焙)。标题曲不停(标题 → MusicRoom 分支无 StopAudio,
        TitleScreen.cpp:569-572), 选曲才切。"""
        tracks = load_tracks(self._data_path)
        unlocked: list[bool] = []
        store = self._title_store
        bgm = store.plst.get("bgmUnlocked") if store is not None else None
        if isinstance(bgm, list):
            unlocked = [
                bool(bgm[i]) if i < len(bgm) else False for i in range(len(tracks))
            ]
        self._music_flow = MusicRoomFlowTh08(tracks=list(tracks), unlocked=unlocked)
        self._screen = Screen.MUSIC_ROOM

    def _leave_music_room(self) -> None:
        """回标题主菜单: 初始光标 6 = Music Room 项(wantedState2 规则,
        TitleScreen.cpp:3692-3693) + 重读解锁态(本作播曲可能刚解锁新曲)。"""
        self._enter_title_scene(CURSOR_FROM_MUSIC_ROOM)

    def _play_music_room_track(self, index: int) -> None:
        """播 Music Room 曲目: 走 SoundPlayer.play_music(WAV 模式自动
        .mid→.wav, 对照 PlayAudio Supervisor.cpp:1601); 播曲即置位解锁
        (Supervisor.cpp:1617/:1632)并落盘(写盘失败不炸)。"""
        flow = self._music_flow
        if flow is None or not 0 <= index < len(flow.tracks):
            return
        self._sound.ensure_loaded()
        self._sound.play_music(flow.tracks[index].file_name)
        store = self._title_store
        bgm = store.plst.get("bgmUnlocked") if store is not None else None
        if isinstance(bgm, list) and 0 <= index < len(bgm) and not bgm[index]:
            unlock_bgm(store, index)
            try:
                store.save(self._score_path)
            except OSError:
                pass

    def _run_music_room(self, inp: FrameInput) -> None:
        """Music Room 一帧: 渲染 → 菜单键(进场 8 帧不受理, flow 内门控;
        移动/播放/淡出原作均无菜单 SE, ProcessInput 全文无 PlaySoundByIdx)。"""
        flow = self._music_flow
        if flow is None:  # 防御(正常 _enter_music_room 已建)
            self._enter_main_menu()
            return
        if self._last_menu_screen != Screen.MUSIC_ROOM:
            self._last_menu_screen = Screen.MUSIC_ROOM
            self._menu_sub_frame = 0
        frame = self._menu_sub_frame
        self._menu_sub_frame += 1
        self._renderer.render_music_room(flow, frame)
        for act in inp.menu_actions:
            r = flow.handle(act)
            if not r:
                continue
            action = r["action"]
            if action in ("play", "replay"):
                self._play_music_room_track(r["index"])
            elif action == "fadeout":
                self._sound.fadeout_music(8.0)  # FadeOutMusic(8.0), :229
            elif action == "quit":
                self._leave_music_room()
                return
        flow.tick_frame()

    # ---- Result 浏览面(ResultScreen BROWSE 模式, ResultScreen.cpp:544-2151) ----
    def _enter_result_browse(self) -> None:
        """进 Result 浏览面(RegisterChain(BROWSE), :2292; 标题菜单 Result 项)。
        持存档快照(当次会话内不刷新, 同 AddedCallback 一次开档口径);
        标题曲不停(标题 → ResultScreen 迁移分支无 StopAudio,
        Supervisor.cpp:180-186, 同 MusicRoom)。"""
        store = self._title_store
        if store is None:  # 防御(正常 _reload_title_unlocks 已读)
            store = load_score_store(self._score_path)
            self._title_store = store
        self._result_flow = ResultFlowTh08(store=store)
        self._screen = Screen.PLAYER_DATA

    def _leave_result_browse(self) -> None:
        """回标题主菜单: 初始光标 5 = Result 项(wantedState2 规则,
        TitleScreen.cpp:3689-3690) + 重读解锁态(同 _leave_music_room 口径)。
        原作退出有 20 帧退幕动画(EXITING 态, :2363-2383), 这里即时切换
        (与 A/B2/C 期各画面一致)。"""
        self._enter_title_scene(CURSOR_FROM_RESULT)

    def _run_result_browse(self, inp: FrameInput) -> None:
        """Result 浏览面一帧: 渲染 → 菜单键(各状态有进场输入门, flow 内门控;
        移动/确认/返回的菜单 SE 由 flow 结果的 "se" 键给出)。"""
        flow = self._result_flow
        if flow is None:  # 防御(正常 _enter_result_browse 已建)
            self._enter_main_menu()
            return
        if self._last_menu_screen != Screen.PLAYER_DATA:
            self._last_menu_screen = Screen.PLAYER_DATA
            self._menu_sub_frame = 0
        frame = self._menu_sub_frame
        self._menu_sub_frame += 1
        self._renderer.render_player_data(flow, self._title_store, frame)
        for act in inp.menu_actions:
            r = flow.handle(act)
            if not r:
                continue
            se = r.get("se")
            if se is not None:
                self._renderer.play_menu_se(se)
            if r["action"] == "quit":
                self._leave_result_browse()
                return
        flow.tick_frame()

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
            # 行移动/调值/子画面跳转的音效由 flow 结果的 "se" 键给出
            # (对照 :712+ 的 SOUND_MOVE_MENU/:1102/:1115/:1143 等)
            if action in (MenuAction.UP, MenuAction.DOWN):
                self._renderer.play_menu_se("select")
            r = self._option_flow.handle(action)
            if not r:
                return
            se = r.get("se")
            if se is not None:
                self._renderer.play_menu_se(se)
            act = r["action"]
            if act == "quit":
                self._leave_option()
            elif act == "changed":
                self._apply_option(r["item"], r["value"])
            elif act == "reset":
                self._apply_option("reset", None)
            elif act == "keyconfig":
                self._enter_keyconfig()
            # "back" = 光标跳 Quit 行(:1137-1141), 音效已播, 无其他动作
        elif self._screen == Screen.KEY_CONFIG:
            flow = self._keyconfig_flow
            if flow.capturing is None and action in (MenuAction.UP, MenuAction.DOWN):
                self._renderer.play_menu_se("select")
            r = flow.handle(action)
            if not r:
                return
            se = r.get("se")
            if se is not None:
                self._renderer.play_menu_se(se)
            if r["action"] == "quit":
                self._leave_keyconfig()
            elif r["action"] == "changed":  # Reset 恢复默认 keymap
                self._rebuild_keymap()
                self._save_config()
            # "capture" = 进入"按新键"捕获态; "back" = 光标跳 Quit 行(th07 口径)
        elif self._screen == Screen.DIFFICULTY:
            if action == MenuAction.UP:
                self._diff.move(-1)
                self._renderer.play_menu_se("select")
            elif action == MenuAction.DOWN:
                self._diff.move(1)
                self._renderer.play_menu_se("select")
            elif action == MenuAction.CONFIRM:
                self._renderer.play_menu_se("ok")
                self._enter_character(extra=False)
            elif action == MenuAction.BACK:
                self._renderer.play_menu_se("cancel")
                self._enter_main_menu()
        elif self._screen == Screen.CHARACTER:
            # 上下/左右都移光标(一期超集; 原作只认 LEFT/RIGHT,
            # MoveCursorHorizontal :3171-3205), Extra 流跳过锁定机体
            if action in (MenuAction.UP, MenuAction.LEFT):
                self._char_flow.move(-1)
                self._renderer.play_menu_se("select")
            elif action in (MenuAction.DOWN, MenuAction.RIGHT):
                self._char_flow.move(1)
                self._renderer.play_menu_se("select")
            elif action == MenuAction.CONFIRM:
                self._renderer.play_menu_se("ok")
                log.trace("选定角色: {}", self._char_flow.cursor.current)
                if self._extra_mode:
                    # Extra Start: 选完机体直接进 EX 面
                    self._start_game(extra=True)
                    self._extra_mode = False
                else:
                    self._start_game()
            elif action == MenuAction.BACK:
                self._renderer.play_menu_se("cancel")
                if self._extra_mode:
                    self._screen = Screen.EXTRA_LEVEL
                else:
                    self._screen = Screen.DIFFICULTY
        elif self._screen == Screen.EXTRA_LEVEL:
            # DifficultySelectExtra(:1433-1444): 单项画面, UP/DOWN 只播音效
            # (MoveCursorVertical(1) 回绕到 0, :1493-1494 不重发贴图)
            if action in (MenuAction.UP, MenuAction.DOWN):
                self._renderer.play_menu_se("select")
            elif action == MenuAction.CONFIRM:
                self._renderer.play_menu_se("ok")
                self._enter_character(extra=True)
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
            self._renderer.play_menu_se("ok")
            self._extra_mode = True
            self._screen = Screen.EXTRA_LEVEL
        elif act == "option":
            # → Option 设置页(:565-570 case TITLE_MENU_ITEM_OPTION)
            self._renderer.play_menu_se("ok")
            self._enter_option()
        elif act == "music_room":
            # → Music Room(:569-572 case TITLE_MENU_ITEM_START_MUSIC_ROOM)
            self._renderer.play_menu_se("ok")
            self._enter_music_room()
        elif act == "result":
            # → Result 浏览面(:573-575 case TITLE_MENU_ITEM_START_RESULT →
            # SupervisorState_ResultScreen, RegisterChain(BROWSE))
            self._renderer.play_menu_se("ok")
            self._enter_result_browse()
        elif act in _UNSUPPORTED_ACTIONS:
            # 一期未实装(见模块 docstring): 提示后留在主菜单
            self._renderer.play_menu_se("cancel")
            self._unimplemented_timer = _UNIMPLEMENTED_HINT_FRAMES

    def _diff_index(self, name: str | None, default: int = 1) -> int:
        """难度名 → 下标(按当前作品难度表; 未知名/None 按 default)。"""
        return self._difficulties.index(name) if name in self._difficulties else default

    def _char_index(self, name: str | None, default: int = 0) -> int:
        """机体名 → 下标(按当前作品机体表; 未知名/None 按 default)。"""
        return self._characters.index(name) if name in self._characters else default

    # ---- 开局 ----
    def _start_game(self, extra: bool = False, seed: int | None = None) -> None:
        t0 = time.time()
        ch = self._char_flow.cursor.current or (
            self._characters[0] if self._characters else ""
        )
        ch_idx = self._char_index(ch)
        if extra:
            dif_idx = 4  # Extra 固定 DIFF_EXTRA (ScoreDat.hpp:44-52)
        else:
            dif_idx = self._diff_index(self._diff.current)
        log.debug(
            "开局: character={}({}) difficulty={} extra={}", ch, ch_idx, dif_idx, extra
        )
        self._game = self._make_game(difficulty=dif_idx, character=ch_idx)
        # 回放确定性: 每局一个种子(原版 Rng 以时间播种); 观战以构造注入为准
        if self._spectate is not None and seed is None:
            self._run_seed = int(getattr(self._game, "seed", 0x5EED))
        else:
            self._run_seed = (
                (int(time.time() * 1000) & 0xFFFF) if seed is None else (seed & 0xFFFF)
            )
            if hasattr(self._game, "set_seed"):
                self._game.set_seed(self._run_seed)
        # Option 初始残机: make_game 签名固定 (difficulty, character) 无法透参,
        # 这里按 config 覆写(difficulty>=4 固定 2 不动, 同 world 构造)
        # 观战跳过此覆写: 残机以对局构造注入值(TouhouWorld.lives)为准
        g0 = getattr(self._game, "globals", None)
        if (
            self._spectate is None
            and g0 is not None
            and dif_idx < 4
            and hasattr(g0, "lives_remaining")
        ):
            g0.lives_remaining = float(self._config.initial_lives)
            if hasattr(self._game, "initial_lives"):
                self._game.initial_lives = int(self._config.initial_lives)
        self._sound.ensure_loaded()
        log.debug("音效资源加载完成 ({}s)", time.time() - t0)
        # Extra 直入 9 面: 进关后再建渲染资源(贴图按关取)
        self._run_extra = extra
        if extra and hasattr(self._game, "enter_stage"):
            self._game.enter_stage(_EXTRA_STAGE_NO)
        if self._spectate is not None:
            # 观战: 包局内 live 对局为 Game 门面(不重复构造对局)
            self._spectate_facade = Game._from_impl(
                self._game, get_game("th08"), "th08"
            )
        self._screen = Screen.PLAYING
        self._paused = False
        self._in_continue = False
        self._renderer.begin_game(self._game, character=ch_idx)
        self._bgm_stage = 0  # 关卡曲由 _run_game 的换关监听播(GameManager.cpp:1021)
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
        # 续关菜单中 Esc 无效
        if inp.esc and not self._paused and not getattr(self._game, "game_over", False):
            if self._spectate is not None:
                # 观战中止: Esc 直接退出(不弹暂停菜单, 观战无标题可回)
                log.debug("观战中止 (Esc, frame={})", getattr(self._game, "frame", "?"))
                self._finished = True
                return
            self._paused = True
            self._pause_cursor.index = 0
            self._pause_confirm = None
            # BGM 暂停 (SOUNDPLAYER_COMMAND_PAUSE)
            self._sound.pause_music()
            self._play_se(SE.SOUND_PAUSE)  # se_pause
            # 开暂停的这帧把 Esc 映射的 BACK 滤掉, 否则同帧又触发 Resume 闪退
            menu_actions = tuple(a for a in menu_actions if a != MenuAction.BACK)
        if self._paused:
            self._run_pause(menu_actions)
            return
        game = self._game
        msg_active = (
            getattr(game, "msg_vm", None) is not None
            and game.msg_vm.has_current_msg_idx()
        )
        # 对话中: 射击键推进对话, skip 键快进; 炸弹键在对话中被门控
        held = inp.held
        skip = msg_active and "skip" in held
        bomb_pressed = "bomb" in held
        if bomb_pressed and not self._prev_bomb_pressed:
            log.trace(
                "bomb 键按下 (frame={}, msg_active={}, bombs={}, bomb_in_use={})",
                game.frame,
                msg_active,
                game.globals.bombs_remaining,
                game.bomb.is_in_use,
            )
        self._prev_bomb_pressed = bomb_pressed
        shot_held = "shoot" in held
        keys6 = (
            "left" in held,
            "right" in held,
            "up" in held,
            "down" in held,
            "focus" in held,
            shot_held,
        )
        if self._spectate is not None:
            # 观战: 本帧输入来自策略(policy 的实参 = 包 live 对局的 Game 门面)
            pi = self._spectate(self._spectate_facade)
            keys6 = pi._keys()
            bomb_pressed = pi.bomb
            adv = pi.advance and msg_active
            game.tick(keys=keys6, bomb=bomb_pressed, advance=adv, skip=pi.skip)
        else:
            game.tick(
                keys=keys6,
                bomb=bomb_pressed,
                advance=inp.advance and msg_active,
                skip=skip,
            )
        # 关卡主题曲: 进关/换关播 stage.bgm_paths[0]
        # (GameManager.cpp:1019-1025 AddedCallback 段; 统一进关即播, 近似)
        stage_no = getattr(game, "stage_no", 1)
        if stage_no != self._bgm_stage:
            self._bgm_stage = stage_no
            paths = getattr(getattr(game, "stage", None), "bgm_paths", ())
            main_bgm = next((p for p in paths if p), "")
            if main_bgm:
                self._sound.play_music(main_bgm.split("/")[-1])
        # 本帧音效/BGM 事件(引擎帧末快照)
        self._sound.play_frame(
            getattr(game, "frame_sounds", []),
            getattr(game, "frame_bgm", []),
            getattr(getattr(game, "stage", None), "bgm_paths", ()),
        )
        # GameOver 续关菜单: 可续关时画面冻结(tick 在 game_over 早退), 等 Yes/No
        if (
            getattr(game, "game_over", False)
            and getattr(game, "result", None) is None
            and getattr(game, "continue_available", False)
        ):
            if self._spectate is not None:
                # 观战不可续关: 等价选 No → 结算 → 结束观战
                game.finalize_game_over()
                self._finished = True
                return
            self._run_continue_menu(menu_actions)
            return
        # 通关 → 结局: 一期无结局画面, 直接看完进总结算
        # (world 的 _enter_ending 已备好 EndingData; 二期再画)
        if getattr(game, "ending", None) is not None:
            log.debug("结局(一期跳过画面) → 总结算 (frame={})", game.frame)
            game.finish_ending()
            return
        # 通关(EX)/GameOver → 结算(world 填 result)
        if getattr(game, "result", None) is not None:
            if self._spectate is not None:
                # 观战不进结算画面(不写榜), 直接结束观战
                log.debug("观战到结算 (frame={}) → 结束观战", game.frame)
                self._finished = True
                return
            self._enter_result()
            return
        self._renderer.render_game(game)

    # ---- 游戏内暂停(冻结 tick; WAV BGM 暂停) ----
    def _resume_from_pause(self) -> None:
        """退出暂停回游戏: 清确认态 + BGM 恢复。"""
        self._paused = False
        self._pause_confirm = None
        self._sound.unpause_music()

    def _run_pause(self, actions) -> None:
        for act in actions:
            if self._pause_confirm is not None:
                # 二次确认态(Quit to Title): 只有 Yes/No, 默认停 No
                if act in (MenuAction.UP, MenuAction.DOWN):
                    self._pause_confirm_cursor.move(1 if act == MenuAction.DOWN else -1)
                    self._renderer.play_menu_se("select")
                elif act == MenuAction.BACK:
                    self._renderer.play_menu_se("cancel")
                    self._resume_from_pause()
                elif act == MenuAction.CONFIRM:
                    if self._pause_confirm_cursor.current == "Yes":
                        self._pause_confirm = None
                        self._renderer.play_menu_se("cancel")
                        self._quit_to_title()
                        return  # 已切屏, 不再画暂停面板
                    else:  # No → 回暂停主菜单
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
                    self._renderer.play_menu_se("ok")
                    self._retry_game()
                    return
                elif item == "Quit to Title":
                    self._renderer.play_menu_se("ok")
                    self._pause_confirm = "Quit to Title"
                    self._pause_confirm_cursor.index = 1  # 默认 No
        if self._screen != Screen.PLAYING:
            return  # 切屏防御(正常仍在 PLAYING)
        confirm = None
        if self._pause_confirm is not None:
            confirm = (self._pause_confirm, self._pause_confirm_cursor.index)
        self._renderer.render_pause(
            self._game, self._pause_cursor.index, confirm=confirm
        )

    def _run_continue_menu(self, actions) -> None:
        """GameOver 续关菜单: Yes → continue_play 当场复活接着玩;
        No → finalize_game_over 进结算。画面冻结(tick 在 game_over 早退)。"""
        game = self._game
        if not self._in_continue:
            self._in_continue = True
            self._continue_cursor.index = 0  # 默认 Yes
            self._sound.pause_music()
            log.debug(
                "续关菜单弹出 (frame={}, 剩余续关={})",
                getattr(game, "frame", "?"),
                getattr(game, "max_retries", 0) - game.globals.num_retries,
            )
        for act in actions:
            if act in (MenuAction.UP, MenuAction.DOWN):
                self._continue_cursor.move(1 if act == MenuAction.DOWN else -1)
                self._renderer.play_menu_se("select")
            elif act == MenuAction.CONFIRM:
                self._in_continue = False
                if self._continue_cursor.index == 0:
                    self._renderer.play_menu_se("ok")
                    self._sound.unpause_music()
                    game.continue_play()
                    log.debug(
                        "续关 (numRetries={}, frame={})",
                        game.globals.num_retries,
                        getattr(game, "frame", "?"),
                    )
                else:
                    self._renderer.play_menu_se("cancel")
                    game.finalize_game_over()  # → result → 下帧进结算
                return  # 状态已变, 下帧走正常路径
        if self._screen != Screen.PLAYING:
            return  # 防御(正常仍在 PLAYING)
        self._renderer.render_continue(
            game,
            self._continue_cursor.index,
            getattr(game, "max_retries", 0) - game.globals.num_retries,
        )

    def _retry_game(self) -> None:
        """暂停菜单 Retry: 重开本关(同难度同机体重建 game)。"""
        self._paused = False
        self._pause_confirm = None
        # 先解除暂停态, 否则同名关卡曲 play_music 早退会让 BGM 一直停在暂停态
        self._sound.unpause_music()
        self._start_game(extra=self._run_extra)

    def _quit_to_title(self) -> None:
        """暂停菜单 Quit to Title: 弃局回标题主菜单(初始光标 1, 见
        title_flow.CURSOR_FROM_GAME; TitleScreen.cpp:3684-3687)。"""
        self._paused = False
        self._pause_confirm = None
        self._sound.unpause_music()
        self._in_continue = False
        self._game = None
        # 游戏/标题窗口同为 640x480×scale, 无需 resize
        self._enter_title_scene(CURSOR_FROM_GAME)

    def _play_se(self, idx: int) -> None:
        """游戏内 SE(SoundPlayer 已加载的表); 未加载/无声卡静音跳过。"""
        snd = self._sound.sounds.get(int(idx))
        if snd is not None:
            try:
                snd.play()
            except Exception:
                pass

    # ---- 结算画面(一期: 文字版, 无入榜名字输入 —— 二期) ----
    def _enter_result(self) -> None:
        self._screen = Screen.RESULT
        self._result_saved = False
        self._menu_frame = 0
        # 结算曲: init.mid (Supervisor.cpp:737 槽30)
        self._sound.play_music(_RESULT_BGM)
        # 结算画面 640x480, 切回标题尺寸
        self._renderer.resize(self._screen, self._scale)

    def _save_result_and_exit(self, game) -> None:
        """结算收尾: 保存 score.json(只写一次) → 回标题主菜单。"""
        if not self._result_saved:
            store = getattr(game, "store", None)
            if store is not None:
                try:
                    store.save(self._score_path)
                except OSError:
                    pass  # 写盘失败不炸(容错同 score_store)
            self._result_saved = True
        self._renderer.play_menu_se("ok")
        self._game = None
        # 结算回来初始光标 5 = Result 项(TitleScreen.cpp:3689-3690)
        self._enter_title_scene(CURSOR_FROM_RESULT)

    def _run_result(self, actions) -> None:
        self._menu_frame += 1
        game = self._game
        if game is None or getattr(game, "result", None) is None:
            self._enter_main_menu()
            return
        self._renderer.render_result(
            game.result, self._menu_frame, store=getattr(game, "store", None)
        )
        for act in actions:
            if act in (MenuAction.CONFIRM, MenuAction.BACK):
                self._save_result_and_exit(game)
                break
