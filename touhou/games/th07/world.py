""" 主游戏逻辑 —— PerfectCherryBloom(TH07)。

把资源、关卡、玩家、弹幕、敌人串成一个可玩的竖版弹幕关。
本类是整合层: 各逻辑模块(同包 player/bomb/items/boss + engine/enemies)为纯逻辑,
计数与状态收口在同包 globals.ZunGlobals; 引擎透出的副作用事件
(PlayerEvent/BombStartResult/handle_timer_callback/end_spellcard 等)
由本类逐帧消费并接回 globals/items/bullets; 发声经 schema/sound.SoundQueue
(self.sounds) 收帧末快照 frame_sounds/frame_bgm, 由播放层(sound_player.py)消费。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from ...schema.archive import GameArchive
from ...registry import GameData, GameHooks, register_world_impl
from .data import CHARACTER_SHT, TH07_DATA
from ...engine.bullets import BulletWorld, SCREEN
from ...engine.ecl import EclFile, EclTimelineRunner, EclWorld
from .ecl_host import GameEclHost
from .ecl_vm import EclMachineTh07
from ...engine.ending import EndingData
from ...engine.enemies import (
    EclEnemy,
    EnemyHost,
    Targeting,
    aimed_ring_fire,
    aimed_spread_fire,
    settle_damage,
)
from .player import (
    GRAZE_SCORE_DISPLAY,
    GRAZE_STAGE_CAP,
    GRAZE_SUBRANK,
    GRAZE_TOTAL_CAP,
    DeathContext,
    DeathSettle,
    KillResult,
    Player,
    PlayerEventKind,
    PlayerState,
)
from ...schema.stage import Stage
from ...engine.rng import Rng
from ...schema.shot_data import parse_sht
from ...utils import Vec2
from ...engine.lasers import LaserWorld
from ...schema.msg import MsgFile, MsgVm
from .items import (
    FULL_POWER,
    OFFSCREEN_SUBRANK_PENALTY,
    POWER_LEVELS,
    GameContext,
    ItemType,
    ItemWorld,
    next_needed_point_items_for_extend,
)
from .bomb import (
    BORDER_BREAK_INVULN,
    CHAR_MARISA_B,
    CHAR_REIMU_A,
    EVENT_REMOVE_ALL_ITEMS,
    EVENT_STOP_BULLET_MOVEMENT,
    Bomb,
    BombContext,
    Border,
    BorderState,
    try_start_bomb,
)
from .boss import SPELLCARD_SCORE, Boss
from .globals import ZunGlobals
from .results import RunStats, ScoreRecord, TopList, clear_percent, rating
from ...engine.score_store import ScoreStore, make_highscore_record
from ...schema.sound import SE, SoundQueue
from ...paths import (DEFAULT_DATA, DEFAULT_SCORE_PATH,  # noqa: F401 (DEFAULT_DATA 为兼容再导出)
                      resolve_data_path)

# CHARACTER_SHT(角色 → .sht 文件名)已集中于同包 data.py(单一来源);
# 这里的导入作 data 缺省/残缺的回落默认。

# 咲夜(角色 4/5)的死亡樱点惩罚上限不同 (Player.cpp UpdateDeath)
_SAKUYA_CHARACTERS = (4, 5)

# 炸弹发声音 (BombData.cpp 各 *Calc 的 timer==0 分支): (character, focus) → SE
_BOMB_SOUNDS = {
    (0, False): SE.BOMB_REIMU_A,           # :180
    (0, True): SE.BOMB_REIMU_A,            # :374
    (1, False): SE.BOMB_REIMARI,           # :542
    (1, True): SE.BOMB_REIMARI,            # :653
    (2, False): SE.BOMB_REIMARI,           # :737
    (2, True): SE.BOMB_MARISA_A_FOCUS,     # :866
    (3, False): SE.BOMB_SAKUMARI,          # :990
    (3, True): SE.BOMB_SAKUMARI,           # :1117
    (4, False): SE.BOMB_SAKUYA_A,          # :1219
    (4, True): SE.BOMB_SAKUYA_A,           # :1352
    (5, False): SE.BOMB_SAKUMARI,          # :1516
    (5, True): SE.BOMB_SAKUMARI,           # :1658
}


def _power_level(power: float) -> int:
    """火力档位 (ItemManager.cpp 的 while (currentPower >= g_PowerLevels[j]) j++)。"""
    n = 0
    while n < len(POWER_LEVELS) and int(power) >= POWER_LEVELS[n]:
        n += 1
    return n

from ...logger import logger as log


@register_world_impl("th07")
class PerfectCherryBloom:
    """《东方妖妖梦》主逻辑类(注册为 th07 的对局实现, 见 registry)。"""

    def __init__(self, data_path: str | Path | None = None, character: int = 0,
                 difficulty: int = 1, *, score_store: ScoreStore | None = None,
                 score_path: str | Path | None = None,
                 initial_lives: int = 3, seed: int | None = None,
                 hooks: GameHooks | None = None,
                 data: GameData | None = None) -> None:
        # 关卡资源命名规则(th07 默认; TouhouWorld(game=...) 经注册表注入)
        self.hooks = hooks or GameHooks()
        # 数值表(registry.GameData; 缺省 = th07 表, data.TH07_DATA)
        self.data = data if data is not None else TH07_DATA
        # 小怪随机掉落表(data 注入; 空 = 引擎默认表, drop_random 的 table=None)
        self._drop_table: list[int] | None = \
            list(self.data.drop_table) if self.data.drop_table else None
        # data_path 解析顺序: 显式参数 > TOUHOU_DAT 环境变量 > 默认 (paths.py)
        self.archive = GameArchive.open(resolve_data_path(data_path))
        self.stage_no = 1
        self.stage = Stage.read(
            self.archive.load(self.hooks.stage_file.format(n=self.stage_no)),
            self.stage_no)
        self.character = character
        self.difficulty = difficulty

        # 加载真实射击数据(角色 n 非/focus; 文件名映射 = data.character_sht,
        # 空表回落 th07 默认 CHARACTER_SHT)
        sht_map = self.data.character_sht or CHARACTER_SHT
        n_unf, n_foc = sht_map.get(character, sht_map[0])
        self.shot_data = parse_sht(self.archive.load(n_unf))
        self.shot_data_focus = parse_sht(self.archive.load(n_foc))

        # 游戏实体
        self.player = Player(shot_data=self.shot_data, shot_data_focus=self.shot_data_focus,
                             rotating_options=(character == 5))  # 咲夜B 旋转子机
        self.player.is_marisa_b = (character == CHAR_MARISA_B)  # 炸弹中不发射
        self.bullets = BulletWorld()
        self.bullets.player_pos = self.player.pos
        self.lasers = LaserWorld()
        self.host = EnemyHost()
        self.items = ItemWorld()
        self.bomb = Bomb(character=character)
        self.border = Border()
        self.boss: Boss | None = None
        # 回放确定性: seed=None 保持旧固定种子(0x5EED/0); 显式 seed 时
        # 主 rng 用 seed, ECL rng 用其派生值(录制/回放见 engine/replay.py)
        self.seed = 0x5EED if seed is None else (seed & 0xFFFF)
        self._ecl_seed = 0 if seed is None else ((self.seed ^ 0x3C7) & 0xFFFF)
        self.rng = Rng(self.seed)
        self._inject_player_rng()
        self.targeting = Targeting()  # 每帧索敌状态 (Player::UpdateUI 重置, EnemyManager 扫描)

        # 结算统计
        self.stats = RunStats(difficulty=self.difficulty)
        self.toplist = TopList()
        # 成绩持久化(内存库; 落盘由 view 结算确认时做)。
        # score_store 直接注入 > score_path 指定文件 > 默认 score.json(仓库根)。
        if score_store is not None:
            self.store = score_store
        else:
            self.store = ScoreStore.load(score_path or DEFAULT_SCORE_PATH,
                                         spellcard_count=len(self.data.spellcard_scores))
        self.store.record_play(character, difficulty)  # PSCR/PLST 开局计数
        self.result: dict | None = None   # 结算数据(通关/GameOver 后填, view 消费)
        self.cleared = False              # 通关标志(终面击破+timeline 完)
        self.stage_results: dict | None = None  # STAGERESULTS 结算快照(view 消费)
        self.ending: EndingData | None = None   # 6 面通关后的结局(view 消费)
        self._pending_next_level = False        # NEXT_LEVEL 事件登记, 次帧帧首换关
        self._point_items_prev_stages = 0       # 已过关面的点道具累计(结算用)
        self._result_cache: dict | None = None
        self._catk_idx: int | None = None  # 当前 ECL 符卡的全局编号(0..140)

        # ---- 音效/BGM 事件(引擎纯逻辑透出, 播放层每帧消费; schema/sound.py) ----
        self.sounds = SoundQueue()          # 本帧累积的发声队列(节流语义同 C++)
        self.frame_sounds: list[int] = []   # 上一帧的音效快照(take 后的队列)
        self.bgm_events: list[tuple] = []   # 本帧累积的 BGM 事件
        # 震屏事件 (BombEffects::RegisterChain(1,...) 透出; bomb.py/ecl_host.py
        # 各注册点累积, 帧末拍成 frame_shakes 快照, view 层维护衰减与偏移)
        self.frame_shakes: list[tuple[int, int, int]] = []
        self.frame_bgm: list[tuple] = []    # 上一帧的 BGM 事件快照
        self._last_spellcard_secs = -1      # 符卡倒计时警告音的去抖 (Gui.cpp:1888)
        self.player.sound = self.sounds

        # 状态(计数/分数/樱点/动态难度收口在 globals, 见同包 globals.py)
        self.frame = 0
        self.globals = ZunGlobals()
        self.globals.initialize_rank(difficulty)
        # cherryMax/初始樱点按难度 (GameManager::AddedCallback 新开局分支 switch):
        # Easy/Normal +200000, Hard +250000, Lunatic +300000, Extra/Phantasm +400000
        # 且 Extra 预填 cherry=cherryStart+200000 / Phantasm +300000
        g0 = self.globals
        g0.cherry_max = g0.cherry_start + (
            200000 if difficulty <= 1 else 250000 if difficulty == 2
            else 300000 if difficulty == 3 else 400000)
        if difficulty == 4:
            g0.cherry = g0.cherry_start + 200000
        elif difficulty == 5:
            g0.cherry = g0.cherry_start + 300000
        if difficulty >= 4:
            # C: difficulty>=4 → defaultCfg->lifeCount=2; 点道具奖残门槛 200
            g0.lives_remaining = 2.0
            g0.next_needed_point_items_for_extend = 200
        else:
            # Option 初始残机(cfg.lifeCount+1, MainMenu.cpp:626): 默认 3,
            # view 层开局按 config 覆写(make_game 无法透参, 见 view._start_game)
            g0.lives_remaining = float(initial_lives)
        self.power_overflow = 0
        self.game_over = False           # 无残机死亡(C++ 进 retry 菜单)
        # 续关上限 (MainMenu.cpp:2576-2587): 累计游戏时长 <7h→3 / <14h→4 / 否则 5
        # (plst.gameHours 无对应字段, 用 plst.total_frames 折算)
        _play_hours = self.store.plst.get("total_frames", 0) / (60 * 3600)
        self.max_retries = 3 if _play_hours < 7 else 4 if _play_hours < 14 else 5
        # 续关回残基数 (retry 菜单 Yes: SetLivesRemaining(defaultCfg->lifeCount),
        # 与开局同值; view 按 config 覆写开局残机时同步改这里)
        self.initial_lives = initial_lives
        self._death_pos: Vec2 | None = None      # 死亡点(掉 P 位置)
        self._last_enemy_hit: Vec2 | None = None  # positionOfLastEnemyHit(追踪炸弹用)
        self._border_clear_boxes: list = []       # BreakBorder 的全屏清弹圆
        self._score_milestone = 0               # score 里程碑去抖(每 1000 万)

        # ---- ECL 关卡脚本(真实 .ecl 驱动波次/Boss; 加载失败回退合成波次) ----
        self.ecl_file: EclFile | None = None
        self.ecl_world: EclWorld | None = None
        self.ecl_host: GameEclHost | None = None
        self.ecl_timelines: list[EclTimelineRunner] = []
        self.msg_file: MsgFile | None = None   # msg{stage}.dat 对话脚本
        self.msg_vm: MsgVm | None = None       # 对话 VM(GuiMsgVm; 门控见 tick)
        self._boss_ecl_state = None      # 当前 Boss 绑定的 EclEnemyState
        self._boss_ecl_enemy: EclEnemy | None = None
        self._rand_spawn_idx = 0         # C randomItemSpawnIdx (itemDrop==-1 每 3 杀掉 1)
        self._rand_table_idx = 0         # C randomItemTableIdx
        self._load_ecl()

    # ---- 回放确定性 ----
    def set_seed(self, seed: int) -> None:
        """开局后、首帧前重设种子(回放播放/每局随机种子; 重建 rng 与 ECL rng)。

        EclWorld 每关在 _load_ecl 重建(用 self._ecl_seed), 所以这里只需
        同步当前关的 ecl_world; 换关自动带新种子。
        """
        self.seed = seed & 0xFFFF
        self._ecl_seed = (self.seed ^ 0x3C7) & 0xFFFF
        self.rng = Rng(self.seed)
        if self.ecl_world is not None:
            self.ecl_world.rng = Rng(self._ecl_seed)

    def _inject_player_rng(self) -> None:
        """把 Player.rand_float 接到 self.rng (确定性修复)。

        player.py 默认 rand_float = random.random()*r (非确定), 用于
        UPDATE_UPWARD_ACCEL (velocity.y 抖动) 与魔理沙A导弹爆炸角
        (player.py:760/:855); 注入后整局只依赖 self.rng/self._ecl_seed。
        """
        self.player.rand_float = lambda r: self.rng.unit() * r

    # ---- ECL 关卡装载 ----
    def _load_ecl(self) -> None:
        """加载本关 ecldata{stage}.ecl 并接好宿主/时间轴; 缺资源则保持合成波次。"""
        try:
            data = self.archive.load(self.hooks.ecl_file.format(n=self.stage_no))
        except KeyError:
            self.ecl_file = None
            self.ecl_timelines = []
            return
        self.ecl_file = EclFile.parse(data)
        self.ecl_world = EclWorld(rng=Rng(self._ecl_seed), difficulty=self.difficulty,
                                  rank=self.globals.rank,
                                  current_stage=self.stage_no,
                                  player_shottype=self.character)
        self.ecl_host = GameEclHost(
            self.ecl_file, self.ecl_world, enemies=self.host,
            bullets=self.bullets, lasers=self.lasers, items=self.items,
            ecl_machine_cls=EclMachineTh07)
        self.ecl_host.sound = self.sounds
        self.ecl_host.on_set_boss = self._ecl_on_set_boss
        self.ecl_host.on_begin_spellcard = self._ecl_on_begin_spellcard
        self.ecl_host.on_end_spellcard = self._ecl_on_end_spellcard
        self.ecl_host.on_spellcard_timeout = self._ecl_on_spellcard_timeout
        self.ecl_host.on_set_power = lambda v: setattr(
            self.globals, "current_power", float(v))
        self.ecl_host.on_add_cherry_plus = self._add_cherry_plus
        # 对话系统: msg{stage}.dat (Gui::LoadMsg); 缺资源则不留 VM(不停轴)
        self.msg_file = None
        self.msg_vm = None
        try:
            self.msg_file = MsgFile.parse(
                self.archive.load(self.hooks.msg_file.format(n=self.stage_no)))
            self.msg_vm = MsgVm(self.msg_file)
            self.ecl_host.msg_vm = self.msg_vm
            # C g_GameManager.character 是 0..2; 本实现 character 是 shotType(0..5)
            self.ecl_host.msg_character = self.character // 2
        except KeyError:
            self.ecl_host.msg_vm = None
        # C OnUpdate 每帧按序跑本关全部时间轴
        self.ecl_timelines = [
            EclTimelineRunner(self.ecl_file, i, self.ecl_world, self.ecl_host)
            for i in range(len(self.ecl_file.timelines))
        ]

    def _step_ecl(self) -> None:
        """每帧: 同步世界快照 → 推进全部时间轴(刷怪/msg/等 boss 停轴)。"""
        h = self.ecl_host
        assert h is not None  # 仅 _load_ecl 成功(ECL 驱动)时被调用
        h.frame_update(
            player_pos=self.player.pos, difficulty=self.difficulty,
            rank=self.globals.rank, power=self.power, shottype=self.character,
            spellcard_active=self._spellcard_active(),
            frozen=self.bomb.is_in_use or self.player.state != PlayerState.ALIVE,
            bomb_in_use=self.bomb.is_in_use)
        for tl in self.ecl_timelines:
            tl.step()

    # ---- ECL Boss/符卡桥接(boss.py 状态机只记账, 阶段/超时切换由 ECL 驱动) ----
    def _ecl_on_set_boss(self, idx: int, st) -> None:
        if st is None:
            if self._boss_ecl_state is not None and self._boss_ecl_state.boss_id == idx:
                if self.boss and self.boss.is_active:
                    self._apply_spellcard_end(self.boss.end_spellcard())
                self.boss = None
                self._boss_ecl_state = None
                self._boss_ecl_enemy = None
            return
        b = Boss(name=f"boss{idx}", spellcard_scores=self.data.spellcard_scores)
        b.boss_id = idx
        b.pos = Vec2(st.pos.x, st.pos.y)
        b.set_life(max(st.life, 1))
        log.debug("Boss 出场: boss_id={} stage={} pos=({:.1f},{:.1f}) (frame={})",
                  idx, self.stage_no, st.pos.x, st.pos.y, self.frame)
        self.boss = b
        self._boss_ecl_state = st
        assert self.ecl_host is not None  # ECL 回调路径, 宿主必已装载
        self._boss_ecl_enemy = self.ecl_host.enemy_by_state.get(id(st))

    def _ecl_on_begin_spellcard(self, st, gui_id: int, idx: int, name: str) -> None:
        if self._boss_ecl_state is not st:
            self._ecl_on_set_boss(st.boss_id if st.boss_id >= 0 else 0, st)
        boss = self.boss
        assert boss is not None  # begin 前 _ecl_on_set_boss 必已建 Boss
        boss.name = name
        boss.spellcard_face = gui_id  # 宣言立绘差分 (Gui.cpp:367-368, ShowSpellcard)
        boss.is_survival_spellcard = bool(st.is_survival_spellcard)
        # 超时阈值若已在 ECL 里设置则采用, 否则给 60 秒兜底(实际超时由
        # ECL 的 timer callback 驱动, 这里只影响捕获分衰减率)
        timeout = st.timer_callback_threshold if st.timer_callback_threshold > 0 else 3600
        boss.set_life(max(st.life, 1))
        boss.begin_spellcard(min(idx, len(SPELLCARD_SCORE) - 1), timeout,
                             timeout_sub=max(st.timer_callback_sub, 0))
        # catk: BeginSpellcard  attempts[shot]/[6] ++ (EclManager.cpp:709-744)
        self._catk_idx = idx
        self.store.record_spellcard_attempt(idx, name, self.character)
        self.sounds.play(SE.BOMB)  # 符卡宣告 (Gui.cpp:396, ShowSpellcard)
        log.debug("符卡宣言: #{} {} (stage={}, 基础分={}, 时限={}s) (frame={})",
                  idx, name, self.stage_no,
                  SPELLCARD_SCORE[min(idx, len(SPELLCARD_SCORE) - 1)] // 10,
                  timeout // 60, self.frame)

    def _ecl_on_end_spellcard(self, st) -> None:
        if self.boss is not None and self._boss_ecl_state is st:
            self._apply_spellcard_end(self.boss.end_spellcard())

    def _ecl_on_spellcard_timeout(self, st) -> None:
        """ECL timer callback 触发的符卡超时: 捕获失败 + 清弹 + 樱点惩罚。
        (ECL 侧的切 sub/清场已在 EclEnemy/GameEclHost 里做掉)"""
        if st.is_survival_spellcard:
            return
        boss = self.boss
        if boss is None or self._boss_ecl_state is not st:
            return
        boss.capture_score = 0
        boss.is_capturing = False
        if boss.is_active:
            boss.is_active += 1  # 2 = 超时失败
        self.bullets.clear()
        # RemoveAllBullets(10) 连带激光 (BulletManager.cpp:439-471):
        # param==10 时 flags&4 激光不豁免, 无道具
        self.lasers.remove_all(spawn_items=False, skip_flag4=False)
        g = self.globals
        penalty = int((g.cherry - g.cherry_start) * 0.25)
        penalty -= penalty % 10
        g.cherry = max(g.cherry_start, g.cherry - penalty)

    # ---- 兼容属性(实际存储在 self.globals) ----
    @property
    def lives(self) -> float:
        return self.globals.lives_remaining

    @lives.setter
    def lives(self, v: float) -> None:
        self.globals.lives_remaining = v

    @property
    def bombs(self) -> float:
        return self.globals.bombs_remaining

    @bombs.setter
    def bombs(self, v: float) -> None:
        self.globals.bombs_remaining = v

    @property
    def power(self) -> float:
        return self.globals.current_power

    @power.setter
    def power(self, v: float) -> None:
        self.globals.current_power = v

    @property
    def graze_total(self) -> int:
        return self.globals.graze_in_total

    @graze_total.setter
    def graze_total(self, v: int) -> None:
        self.globals.graze_in_total = v

    @property
    def cherry(self) -> int:
        return self.globals.cherry

    @cherry.setter
    def cherry(self, v: int) -> None:
        self.globals.cherry = v

    @property
    def cherry_start(self) -> int:
        return self.globals.cherry_start

    @cherry_start.setter
    def cherry_start(self, v: int) -> None:
        self.globals.cherry_start = v

    @property
    def bombs_used_count(self) -> float:
        return self.globals.bombs_used

    @bombs_used_count.setter
    def bombs_used_count(self, v: float) -> None:
        self.globals.bombs_used = v

    # ---- 道具上下文 / 收集结算 ----
    def _item_ctx(self) -> GameContext:
        """给 ItemWorld 的游戏状态快照(§E; pocY/吸附速度/半径取自 .sht)。"""
        g = self.globals
        return GameContext(
            power=g.current_power, lives=int(g.lives_remaining),
            bombs=int(g.bombs_remaining), graze_total=g.graze_in_total,
            player_pos=self.player.pos, player_alive=self.player.alive,
            player_state=int(self.player.state),  # 1=SPAWNING: 道具缓降不吸附
            border_active=self.border.active, difficulty=self.difficulty,
            bombing=self.bomb.is_in_use,
            power_overflow_counter=self.power_overflow,
            spell_cards_captured=g.spell_cards_captured,
            cherry_gap=g.cherry - g.cherry_start,
            cherry_maxed=g.cherry >= g.cherry_max,
            extends_from_point_items=g.extends_from_point_items,
            point_items_collected_for_extend=g.point_items_collected_for_extend,
            poc_y=self.shot_data.poc_y,
            item_collect_speed=self.shot_data.item_collect_speed,
            item_collect_radius=self.shot_data.item_collect_radius,
        )

    def _apply_collect(self, r) -> None:
        """把道具收集结果应用到游戏状态(CollectResult → globals)。"""
        g = self.globals
        power_before = self.power
        # r.score 是显示分, AddScore 入参为代码值(显示分*10)
        g.add_score(r.score * 10)
        self.power = min(FULL_POWER, self.power + r.delta_power)
        if r.delta_power > 0 and self.power >= FULL_POWER:
            self.power_overflow = 0
        if r.power_overflow_next is not None:
            self.power_overflow = r.power_overflow_next
        self.bombs = min(8, self.bombs + r.delta_bombs)
        self.lives += r.delta_lives
        if r.extends:
            # 点道具残机 (ItemManager.cpp:285-325)
            self.lives += r.extends
            g.extends_from_point_items += r.extends
            g.next_needed_point_items_for_extend = next_needed_point_items_for_extend(
                g.extends_from_point_items, self.difficulty)
        # add_cherry_plus 会同时累加 cherry, 两轨分开入账不双加
        g.add_cherry(r.delta_cherry)
        self._add_cherry_plus(r.delta_cherry_plus)
        if r.subrank > 0:
            g.increase_subrank(r.subrank)
        if r.point_items_collected:
            g.point_items_collected_this_stage += r.point_items_collected
            g.point_items_collected_for_extend += r.point_items_collected
        if r.clear_bullets:
            self.bullets.clear()
            # 满火力清弹 RemoveAllBullets(1) 连带激光 (ItemManager.cpp:229/347/
            # 383 → BulletManager.cpp:439-471): flags&4 豁免, 沿线出弹消点
            self.lasers.remove_all(spawn_items=True, skip_flag4=True,
                                   spawn_item=self._spawn_point_bullet)
        # ---- 收集音效 (ItemManager.cpp OnUpdate 收集段) ----
        if r.extends:
            log.debug("奖残(点道具) (frame={}, 残机={})", self.frame, self.lives)
        if r.delta_lives > 0:
            log.debug("奖残(LIFE 道具) (frame={}, 残机={})", self.frame, self.lives)
        if r.delta_lives > 0 or r.extends:
            # 奖残(满残转奖弹也响) (GameManager.cpp:104/113, ExtendFromPoints)
            self.sounds.play(SE.EXTEND)
        if r.delta_power > 0 and _power_level(power_before) != _power_level(self.power):
            # 火力升档/满火力道具 (ItemManager.cpp:243/361/385)
            self.sounds.play(SE.POWERUP)
        # 本帧有道具入袋, 每帧至多一次 (ItemManager.cpp:482, itemAcquired)
        self.sounds.play(SE.SOUND_21)

    def _add_cherry_plus(self, x: int) -> None:
        """cherryPlus 入账; 满樱信号(触达 cherryStart+50000) → 结界 READY。"""
        if x > 0 and self.globals.add_cherry_plus(x):
            self.border.ready_border()
            log.debug("结界 READY (frame={}, cherryPlus={})",
                      self.frame, self.globals.cherry_plus)

    # ---- 读档 / 关卡切换 ----
    def enter_stage(self, stage_no: int) -> None:
        self.stage_no = stage_no
        self.stage = Stage.read(
            self.archive.load(self.hooks.stage_file.format(n=stage_no)), stage_no)
        log.debug("进关: stage={} 「{}」 BGM={} (frame={})", stage_no,
                  self.stage.title.strip(),
                  next((n for n in self.stage.bgm_names if n), ""), self.frame)
        self._load_ecl()

    # ---- 波次编排 ----
    # ---- 对话(GuiImpl::RunMsg 的每帧语义 + 世界门控) ----
    def _msg_active(self) -> bool:
        return self.msg_vm is not None and self.msg_vm.has_current_msg_idx()

    def _step_msg(self, *, advance: bool, skip: bool) -> None:
        """Gui::OnUpdate → RunMsg: 推进对话 VM; 对话中每帧清道具、结界自然破
        (Gui.cpp RunMsg: playerState != DEAD 时 RemoveAllItems; hasBorder != NONE
        时 BreakBorderNaturally)。时间轴停轴由 ecl_host.msg_wait 完成。"""
        vm = self.msg_vm
        if vm is None:
            return
        vm.step(advance_pressed=advance, skip_held=skip)
        for ev in vm.take_events():
            if ev == "stage_results":
                self._on_stage_results()
            elif ev == "next_level":
                self._on_next_level()
            elif ev.startswith("music:"):
                # MSG_MUSIC (Gui.cpp:939-958): musicIdx 索引 stage.bgm_paths
                self.bgm_events.append(("music", int(ev[6:])))
            elif ev == "fadeout_music":
                # MSG_FADEOUT_MUSIC (Gui.cpp:997-998): FadeOutMusic(4.0)
                self.bgm_events.append(("fadeout", 4.0))
            else:
                log.debug("msg event: {}", ev)
        if not vm.has_current_msg_idx():
            return
        if self.player.state != PlayerState.DEAD:
            self.items.remove_all_items()
        if self.border.has_border != BorderState.NONE:
            self._apply_natural_border_break()

    def _apply_natural_border_break(self) -> None:
        """BreakBorderNaturally 入账(与 _tick_border 的 res 分支同账)。"""
        g = self.globals
        res = self.border.break_border_naturally(
            cherry=g.cherry, cherry_start=g.cherry_start, cherry_max=g.cherry_max)
        log.debug("结界自然破(对话中) (frame={}, 得分={})", self.frame, res.score)
        g.cherry = res.cherry
        g.cherry_max = res.cherry_max
        g.cherry_plus = res.cherry_plus
        g.add_score(res.score)
        self.player.state = PlayerState.INVULNERABLE
        self.player.invuln = max(self.player.invuln, res.invulnerability_timer)
        self.sounds.play(SE.BORDER_BREAK)  # BreakBorderNaturally (Player.cpp:2015)

    def _step_stage(self) -> None:
        """关卡波次/Boss 编排: 有 ECL 数据走真实时间轴, 否则回退合成波次+演示 Boss。"""
        if self.ecl_file is not None:
            self._step_ecl()
        else:
            self._wave()
            self._boss_spawn()

    def _wave(self) -> None:
        """按关卡时间线周期放出杂兵波次 + 偶发的激光精英。"""
        cycle = self.frame // 80
        if self.frame % 80 == 1:
            side = -1 if cycle % 2 else 1
            cx = SCREEN.x / 2 + side * 90
            last = SCREEN.y - 40
            # 每 3 轮一个激光精英, 其余放瞄准环/扇
            if cycle % 3 == 2:
                self.host.spawn(
                    path=[Vec2(cx, -20), Vec2(cx, 40)],
                    life=6 + cycle,
                    speed=1.6,
                    fire=self._laser_fire,
                    radius=16,
                )
            else:
                fire = aimed_ring_fire(10 + cycle % 3, 2.0 + cycle * 0.1) if cycle % 2 else aimed_spread_fire()
                self.host.spawn(
                    path=[Vec2(cx, -20), Vec2(cx, 60), Vec2(cx, last)],
                    life=3 + cycle // 2,
                    speed=2.0,
                    fire=fire,
                )

    def _laser_fire(self, enemy, world) -> None:
        """精英敌人: 周期性瞄准玩家放激光。"""
        if self.frame % 120 < 40 and len(self.lasers.lasers) < 64:
            self.lasers.spawn(
                enemy.pos, 0.0, aimed=True, player_pos=self.player.pos,
                width=10.0, duration=60, start_time=15, hitbox_start_time=10,
                end_time=25, hitbox_end_time=20,
            )

    # ---- 每帧 ----
    def tick(self, *, keys: tuple[bool, ...] | None = None, bomb: bool = False,
             advance: bool = False, skip: bool = False) -> None:
        """推进一帧。keys 为 (left,right,up,down,focus[,shoot]); bomb=True 表示按炸弹键;
        advance = 对话中 Z 新按下(提前结束 PAUSE), skip = 按住 Ctrl(快进对话)。"""
        if self.game_over:
            # 无残机死亡流程走完(C++ 进 retry 菜单): 可续关则画面冻结,
            # 等 view 选择(continue_play / finalize_game_over); 不可续关
            # (Extra/Phantasm/次数用尽, AsciiManager.cpp:839-846 直接跳过
            # retry 菜单) 同 C++ 进结算。practice/replay 由 view 短路。
            if self.result is None and not self.continue_available:
                log.debug("GameOver → 结算 (frame={}, score={})", self.frame,
                         self.globals.gui_score)
                self.result = self.final_result(cleared=False)
            self._drain_frame_events()
            return
        if self.cleared:
            self._drain_frame_events()
            return  # 已通关进结算, 游戏画面冻结
        if self.ending is not None:
            self._drain_frame_events()
            return  # 结局画面显示中(view 看完调 finish_ending)
        if self._pending_next_level:
            # NEXT_LEVEL → 换关(Gui.cpp transitionToScoreScreen → curState=3 →
            # GameManager 重建); 事件在 _step_msg 里登记, 次帧帧首切关,
            # 避免 tick 半途换世界
            self._pending_next_level = False
            self._advance_stage()
        self.frame += 1
        # 对话门控(Gui::HasCurrentMsgIdx): 玩家可移动, 不能射击/炸弹
        # (Player.cpp:1616 射击 / :1722 炸弹 都以 !HasCurrentMsgIdx 为前提)
        msg_active = self.msg_vm is not None and self.msg_vm.has_current_msg_idx()
        self.player.dialog_active = msg_active  # 持续弹压计时(Player.cpp:373/:417)
        if keys:
            self.player.push(
                (1 if keys[1] else 0) - (1 if keys[0] else 0),
                (1 if keys[3] else 0) - (1 if keys[2] else 0),
                focus=keys[4],
                # Z=射击(C++ HandlePlayerInputs); keys 无第 6 元时默认按住
                # (兼容旧 harness 的 5 元组, 语义=全程射击)
                firing=(keys[5] if len(keys) > 5 else True) and not msg_active,
            )
        else:
            self.player._firing = not msg_active
        self.player.power = self.power
        g = self.globals

        # ---- 炸弹/结界键 (Player.cpp UpdateBorderAndBombState 触发分支) ----
        if bomb and not msg_active and not self.bomb.is_in_use \
                and self.border.has_border != BorderState.NONE:
            # hasBorder != NONE 时按 bomb 键 = 主动破结界 (Player.cpp:1686-1692)
            self._break_border(by_bomb_key=True)
        elif bomb and not msg_active and not self.bomb.is_in_use:
            self._try_bomb()

        # ---- 炸弹每帧 (UpdateBombProjectiles → cherryDrain → bombCalc) ----
        # UpdateBombProjectiles 在 C++ 每帧无条件执行 (Player.cpp:2231),
        # 即使 bomb 已结束 (清弹盒可比 bomb 活得久, 如灵梦B 集中 lifetime=210 >
        # duration=190 多活 20 帧); 若只在 is_in_use 时 tick, 结束帧留下的
        # active 伤害/清弹盒会被冻结成永久残留 (渲染层会持续画出)。
        was_bomb_in_use = self.bomb.is_in_use
        self.bomb.tick(self._bomb_ctx())
        if was_bomb_in_use and not self.bomb.is_in_use:
            log.debug("bomb 结束 (frame={}, 持续={}帧)", self.frame, self.bomb.timer)
        if was_bomb_in_use:
            if self.bomb.drain_applied:
                g.subtract_cherry_drain(self.bomb.drain_applied)
            self._apply_bomb_boxes()
            for ev in self.bomb.events:
                if ev == EVENT_REMOVE_ALL_ITEMS:
                    self.items.remove_all_items()
                elif ev == EVENT_STOP_BULLET_MOVEMENT:
                    self.bullets.stop_bullet_movement()  # 咲夜B 停时
                # EVENT_END_PLAYER_SPELLCARD 是 GUI 横幅事件, 逻辑侧无影响
            self.bomb.events.clear()

        # ---- 结界 (READY 自动激活 / ACTIVE 每帧倒计时与自然破) ----
        self._tick_border()

        # ---- 玩家步进(移动/射击/死亡倒计时/重生全在内部状态机) ----
        death_ctx = DeathContext(lives=int(g.lives_remaining), cherry=g.cherry,
                                 cherry_start=g.cherry_start,
                                 is_sakuya=self.character in _SAKUYA_CHARACTERS)
        self.player.bomb_active = self.bomb.is_in_use  # 持续弹压计时/伤害 /3
        mult = self.bomb.move_speed_multiplier if self.bomb.is_in_use else 1.0
        if mult != 1.0:
            # C++ 是炸弹中对最终移速乘倍率; 线性缩放入输入向量与之等价
            orig_move = self.player._move
            self.player._move = orig_move * mult
            self.player.step(death_ctx)
            self.player._move = orig_move
        else:
            self.player.step(death_ctx)

        self._step_stage()
        self._step_msg(advance=advance, skip=skip)
        self.host.step(self.bullets, rng=self.rng)

        # 敌人体术判定 (EnemyManager.cpp:754-775, OnUpdate 伤害段最前, 在自机弹
        # 伤害之前): 敌人 hitbox/1.5 vs 玩家 killbox; 命中同子弹路径
        # (BORDER→破由 BREAK_BORDER 事件统一处理, ALIVE→die)。
        # 炸弹中跳过: C++ 此时 playerState=INVULNERABLE 且 CalcKillboxCollision
        # 开头的 CheckBombGraze 会把命中短路成返回 2 (Player.cpp:1009-1012)。
        if not self.bomb.is_in_use and self.host.contact_hits(self.player):
            self._death_pos = self.player.pos
            self.lasers.clear()

        # 自机弹打敌人(全管线: CalcDamageToEnemy → settle_damage → 入账/掉落)。
        # 索敌状态每帧重置(Player::UpdateUI), 敌人扫描中更新, Boss 扫描后写回 player。
        self.targeting.reset()
        results, kills = self.host.shoot_hits(
            self.player, self.targeting, is_focus=self.player.focus,
            is_sakuya=self.character in _SAKUYA_CHARACTERS,
            bomb_in_use=self.bomb.is_in_use, stage=self.stage_no,
            spellcard_active=self._spellcard_active(),
            used_bomb=bool(self.boss and self.boss.used_bomb),
            is_reimu_a=self.character == CHAR_REIMU_A,
            bomb_box_hit=self._bomb_box_hit)
        for _, r in results:
            if r.score_code:
                g.add_score(r.score_code)
            if r.cherry_gain:
                self._add_cherry_plus(r.cherry_gain)
            if r.damage:
                # 敌受击音 (EnemyManager.cpp:1052, playedDamageSound)
                self.sounds.play(SE.SOUND_20)
        counter = self.frame
        for e in kills:
            self._kill_reward(e, counter)
            self._last_enemy_hit = e.pos
            counter += 1

        self._tick_boss()
        self._tick_spellcard_timeout_warn()
        self.player.position_of_last_enemy_hit = self.targeting.position_of_last_enemy_hit
        self.player.sakuya_target_position = self.targeting.sakuya_target_position

        # ---- 敌弹推进 + 擦弹/命中判定 (§A.7 AABB) ----
        self.bullets.player_pos = self.player.pos
        self.bullets.step()
        bsize = (self.bullets.bullet_radius * 2.0, self.bullets.bullet_radius * 2.0)
        for b in self.bullets.alive():
            if b.spawn_state:
                continue  # 出生态弹无擦弹/命中 (OnUpdate SPAWNING_* 分支不到 do_collision)
            self.player.graze_bullet(b, bsize)
            if self.bomb.is_in_use:
                continue  # 炸弹中 playerState=INVULNERABLE, 不命中
            kr = self.player.check_killbox(b.pos, bsize)
            if kr == KillResult.DEATH:
                self._death_pos = self.player.pos
                self.lasers.clear()
                break
            # BORDER_BREAK 不死, 由 BREAK_BORDER 事件统一处理(见下)

        # 结界破裂的全屏清弹圆 → 弹转小樱点
        self._tick_border_clear_boxes(bsize)

        # ---- 道具推进 + 收集 ----
        ctx = self._item_ctx()
        for _ in self.items.step(ctx):
            g.decrease_subrank(OFFSCREEN_SUBRANK_PENALTY)  # 掉出道具 subrank -3
        for item in list(self.items.alive()):
            if self.items.collect_pickup(item, ctx):
                cr = self.items.collect(item, ctx)
                self._apply_collect(cr)
                self.items.remove(item)

        # ---- 激光推进 + 玩家碰撞 ----
        self.lasers.step()
        lhit, _ = self.lasers.check_player(self.player.pos, self.player.hitbox_radius)
        if lhit and not self.bomb.is_in_use:
            if self.player.state == PlayerState.BORDER:
                self._break_border()  # 结界挡刀
            elif self.player.state == PlayerState.ALIVE:
                self._death_pos = self.player.pos
                self.player.die()
            self.lasers.clear()

        # ---- 玩家事件消费 (死亡结算/重生/擦弹/结界破/重生清弹) ----
        self._consume_player_events()

        # guiScore 每帧向真实分追赶 (GameManager::OnUpdate)
        g.tick_gui_score()
        milestone = g.gui_score // 10_000_000
        if milestone > self._score_milestone:
            self._score_milestone = milestone
            log.debug("score 突破 {}000万 (frame={}, stage={}, score={})",
                      milestone, self.frame, self.stage_no, g.gui_score)

        # 通关判定(尾王击破 + timeline 完)。正常路径: msg 脚本 STAGERESULTS →
        # NEXT_LEVEL 事件先行(见 _step_msg, 事件必在时间轴全完之前发出);
        # 这里只兜无 msg 脚本/测试强跳时间轴的底
        if self.result is None and self.ending is None \
                and not self._pending_next_level and self._stage_cleared():
            log.debug("关卡 {} 通过 (frame={})", self.stage_no, self.frame)
            if self.stage_no < 6:
                self._pending_next_level = True   # 次帧帧首换关
            elif self.stage_no == 6:
                self._enter_ending()              # curState=9 → Ending
            else:
                # 7=Extra 8=Phantasm: 通关直接总结算(Gui.cpp NEXT_LEVEL →
                # finished=1 → curState=6 ResultScreen)
                self.cleared = True
                log.debug("通关(Extra/Phantasm) → 总结算 (frame={}, score={})",
                          self.frame, g.gui_score)
                self.result = self.final_result(cleared=True)

        # 帧末收口: 本帧音效/BGM 事件快照给播放层(C++ 主循环每帧 ProcessQueues)
        self._drain_frame_events()

    def _drain_frame_events(self) -> None:
        """把本帧累积的发声队列/BGM 事件/震屏事件拍成 frame_sounds/frame_bgm/frame_shakes 快照。"""
        if self.ecl_host is not None and self.ecl_host.bgm_events:
            self.bgm_events += self.ecl_host.bgm_events
            self.ecl_host.bgm_events.clear()
        self.frame_sounds = self.sounds.take()
        self.frame_bgm = self.bgm_events
        self.bgm_events = []
        shakes = list(self.bomb.shakes)
        self.bomb.shakes.clear()
        if self.ecl_host is not None and self.ecl_host.shake_events:
            shakes += self.ecl_host.shake_events
            self.ecl_host.shake_events.clear()
        self.frame_shakes = shakes

    def _tick_spellcard_timeout_warn(self) -> None:
        """符卡倒计时 <10 秒的逐秒警告音 (Gui.cpp:1888-1892, SOUND_29)。"""
        boss = self.boss
        if boss is None or not boss.is_active or boss.timer_callback_threshold <= 0:
            self._last_spellcard_secs = -1
            return
        secs = (boss.timer_callback_threshold - boss.timer) // 60
        if secs < 10 and secs != self._last_spellcard_secs:
            self.sounds.play(SE.SOUND_29)
        self._last_spellcard_secs = secs

    def _stage_cleared(self) -> bool:
        """通关判定: ECL 时间轴全部跑完且 Boss 已退场。

        实跑观察(ecldata1, ReimuA/Normal, Z 脉冲推对话): 中超 Boss 2668 出场,
        尾王前置对话(msg0) 5518-5835(APPEAR_ENEMY 窗口内 Boss 入场),
        符卡 7409 开始, 尾王击破后结算对话(msg1) 10232 起, 10441 帧全部
        timeline done。
        不区分击破/逃走: 原版两者都经同一条 boss 后 msg → NEXT_LEVEL 推进,
        结局选择只看 numRetries (Ending.cpp:499-505, numRetries!=0 → bad
        ending), 与尾王是击破还是超时逃走无关; 逃走的影响仅停留在当张
        符卡的捕获失败(超时路径, EnemyManager.cpp:470-486)。
        无 ECL 数据的合成波次回退路径不触发(永远 False)。

        判定为真后的去向(tick 尾部): 1-5 面 → 换关(_advance_stage);
        6 面 → 结局(_enter_ending); 7/8 面 → 总结算(final_result)。
        正常路径由 msg 的 NEXT_LEVEL 事件先行驱动(_on_next_level)。
        """
        return (self.ecl_file is not None and bool(self.ecl_timelines)
                and all(t.done for t in self.ecl_timelines)
                and self.boss is None)

    # ---- STAGERESULTS 过关结算 / NEXT_LEVEL 换关 (Gui.cpp RunMsg) ----
    def _on_stage_results(self) -> None:
        """msg STAGERESULTS 指令: 快照本关计数, 算过关奖励并入账, 留给
        view 画结算面板 (Gui.cpp:972-991 快照 + :1357-1417 奖励计算/入账)。

        奖励(代码值): Clear=stage*100000 + Graze*50 + Point*5000
        + Cherry(cherryMax-cherryStart); 6/7/8 面追加 Player=lives*2000000
        + Bomb=bombs*400000; 难度修正 Easy*0.5/Hard*1.2/Lunatic*1.5/Extra·
        Phantasm*2; 初始残机(lifeCount) 3→*0.5 4→*0.2。
        画面显示值是代码值的 10 倍("Total = %8d0" 尾 0 为字面拼接)。
        """
        g = self.globals
        snap = {
            "stage": self.stage_no,
            "clear_power": int(g.current_power),
            "point_items": g.point_items_collected_this_stage,
            "cherry_max": g.cherry_max - g.cherry_start,
            "graze": g.graze_in_stage,
            "lives": int(g.lives_remaining),
            "bombs": int(g.bombs_remaining),
        }
        # C: currentStage<6 → stageClearBg+转场截图(渲染层事); 否则奖残封口
        if self.stage_no >= 6:
            g.extends_from_point_items = -1
        survivor = self.stage_no >= 6  # 6/7/8 面: 残机/炸弹奖(无 practice/replay)
        bonus = (self.stage_no * 100000 + snap["graze"] * 50
                 + snap["point_items"] * 5000 + snap["cherry_max"])
        if survivor:
            bonus += snap["lives"] * 2000000 + snap["bombs"] * 400000
        d = self.difficulty
        rank_line = ("Easy Rank    *0.5", "Normal Rank  *1.0", "Hard Rank    *1.2",
                     "Lunatic Rank *1.5", "Extra Rank   *2.0", "Phantasm Rank*2.0")[d]
        if d == 0:
            bonus //= 2
        elif d == 2:
            bonus = bonus * 12 // 10
        elif d == 3:
            bonus = bonus * 15 // 10
        elif d >= 4:
            bonus <<= 1
        # lifeCount 惩罚 (Gui.cpp:1389-1399): 简化 —— 不与 Option 初始残机
        # 联动, 固定 3(Extra/Phantasm 为 2, 同 C++ difficulty>=4 的 lifeCount=2)
        penalty_line = None
        life_count = 2 if d >= 4 else 3
        if d < 4:
            if life_count == 3:
                bonus = bonus * 5 // 10
                penalty_line = "Player Penalty*0.5"
            elif life_count == 4:
                bonus = bonus * 2 // 10
                penalty_line = "Player Penalty*0.2"
        # Gui.cpp:1408-1417 ZUN bloat: AddScore ×10 (每次内部 //10, 合计 = bonus)
        for _ in range(10):
            g.add_score(bonus)
        # 面板行(显示值, 同 Gui::OnDraw finishedStage 段)
        lines = [
            ("Clear", self.stage_no * 1000000),
            ("Point", snap["point_items"] * 50000),
            ("Graze", snap["graze"] * 500),
            ("Cherry", snap["cherry_max"] * 10),
        ]
        if survivor:
            lines.append(("Player", snap["lives"] * 20000000))
            lines.append(("Bomb", snap["bombs"] * 4000000))
        self.stage_results = {
            "stage": self.stage_no,
            "all_clear": self.stage_no >= 6,  # C: currentStage<6 → "Stage Clear"
            "lines": lines,
            "rank_line": rank_line,
            "penalty_line": penalty_line,
            "total": bonus,  # 显示为 f"{bonus}0" (Gui.cpp "Total = %8d0")
            "snapshot": snap,
        }

    def _on_next_level(self) -> None:
        """msg NEXT_LEVEL 指令 (Gui.cpp:1004-1058): 按当前面分流转场。"""
        if self._pending_next_level or self.result is not None \
                or self.ending is not None:
            return
        if self.stage_no < 6:
            self._pending_next_level = True  # 转场结算 → 次帧帧首换关
        elif self.stage_no == 6:
            self._enter_ending()             # finished=1 → curState=9 Ending
        else:
            # Extra/Phantasm 通关 → finished=1 → curState=6 ResultScreen
            self.cleared = True
            log.debug("通关(Extra/Phantasm) → 总结算 (frame={}, score={})",
                      self.frame, self.globals.gui_score)
            self.result = self.final_result(cleared=True)

    def _advance_stage(self) -> None:
        """换关 (GameManager::AddedCallback curState==3 分支 + 公共路径)。

        带走(globals 不清): score(见下)/lives/bombs/currentPower/cherry 系
        (cherry/cherryPlus/cherryMax/cherryStart)/grazeInTotal/rank/
        pointItemsCollectedForExtend/extendsFromPointItems/spellCardsCaptured/
        deaths/bombsUsed/numRetries。
        重置(AddedCallback 公共路径): subrank=0/pointItemsCollectedThisStage=0/
        grazeInStage=0; score=0 但 guiScore 已对齐旧值, 次帧 tick_gui_score
        单调追赶恢复(C++ 同, GameManager.cpp:235-237)。
        重建: 子弹/激光(BulletManager::RegisterChain)、敌人(EnemyManager::
        RegisterChain, randomItemSpawnIdx 随之归零)、玩家/炸弹/结界
        (Player::RegisterChain → AddedCallback: SPAWNING+出生点)。
        道具(C++ ItemManager 不换关重建): 结算对话期间每帧 RemoveAllItems
        已清空, 无需处理。ECL/msg 由 enter_stage 换新关脚本(EclManager.Load)。
        """
        g = self.globals
        log.debug("换关: stage {} → {} (frame={}, score={})",
                 self.stage_no, self.stage_no + 1, self.frame, g.gui_score)
        # NEXT_LEVEL/AddedCallback: guiScore 对齐真实分(fallback 路径也走这)
        g.gui_score = g.score
        g.gui_score_difference = 0
        self.stage_results = None
        self._point_items_prev_stages += g.point_items_collected_this_stage
        g.subrank = 0
        g.point_items_collected_this_stage = 0
        g.graze_in_stage = 0
        g.score = 0
        # 各 Manager 重建(清场)
        self.bullets = BulletWorld()
        self.lasers = LaserWorld()
        self.host = EnemyHost()
        self.boss = None
        self._boss_ecl_state = None
        self._boss_ecl_enemy = None
        self._catk_idx = None
        self._rand_spawn_idx = 0
        self._rand_table_idx = 0
        self._border_clear_boxes = []
        self._death_pos = None
        self._last_enemy_hit = None
        # Player::RegisterChain(0): 玩家/炸弹/结界重建 → SPAWNING 出生点
        self.player = Player(shot_data=self.shot_data,
                             shot_data_focus=self.shot_data_focus,
                             rotating_options=(self.character == 5))
        self.player.is_marisa_b = (self.character == CHAR_MARISA_B)
        self.player.sound = self.sounds
        self._inject_player_rng()  # 换关重建后重新注入确定性 rand_float
        self.bullets.player_pos = self.player.pos
        self.bomb = Bomb(character=self.character)
        self.border = Border()
        # currentStage++ → Stage::RegisterChain + EclManager.Load + Gui::LoadMsg
        self.enter_stage(self.stage_no + 1)

    # ---- 结局(6 面通关) ----
    def _enter_ending(self) -> None:
        """6 面通关 → 结局 (Gui.cpp NEXT_LEVEL currentStage==6 → curState=9)。

        结局文件 (Ending.cpp:499-505): numRetries!=0 → bad ending,
        否则按机体正常结局。本期简化: 文本滚动(解析见 engine/ending.py);
        资源缺失时退化为通用通关画面。游戏画面冻结, view 看完调
        finish_ending() 进总结算。
        """
        g = self.globals
        g.snap_gui_score()  # NEXT_LEVEL: guiScore 对齐真实分
        self.stage_results = None
        log.debug("6 面通关 → 结局 (frame={}, bad={}, score={})",
                  self.frame, g.num_retries > 0, g.gui_score)
        try:
            self.ending = EndingData.load(self.archive, self.character,
                                          bad=g.num_retries > 0)
        except (KeyError, ValueError, OSError) as e:
            log.warning("结局资源缺失, 用通用通关画面: {}", e)
            self.ending = EndingData.generic(self.character)

    def finish_ending(self) -> None:
        """结局看完(view 确认) → 总结算 (Ending 结束 → ResultScreen)。"""
        if self.ending is None:
            return
        self.ending = None
        self.cleared = True
        log.debug("结局结束 → 总结算 (score={})", self.globals.gui_score)
        self.result = self.final_result(cleared=True)

    @property
    def continue_available(self) -> bool:
        """续关菜单是否可出现 (AsciiManager.cpp RetryMenu::OnUpdate 门控:
        :839-846 numRetries>=maxRetries 或 difficulty>=4(Extra/Phantasm)
        直接跳过菜单进结算; practice/replay 由 view 短路)。"""
        return (self.game_over and self.result is None
                and self.difficulty < 4
                and self.globals.num_retries < self.max_retries)

    def finalize_game_over(self) -> None:
        """续关菜单选 No (RetryMenu case 4 → curState=6 ResultScreen): 进结算。"""
        if self.game_over and self.result is None:
            log.debug("续关菜单选 No → 结算 (frame={}, score={})",
                     self.frame, self.globals.gui_score)
            self.result = self.final_result(cleared=False)

    def continue_play(self) -> None:
        """续关(retry 菜单 Yes, AsciiManager.cpp:955-976)。

        当场复活接着玩(玩家已重生, 不重来本关)。重置清单:
        numRetries++; score 清零(C: guiScore=numRetries→score=guiScore);
        残机回开局数; bomb 回满; currentPower=0; cherry=cherryStart;
        grazeInStage/本关点道具/奖残进度(grazeInStage,
        pointItemsCollectedThisStage/ForExtend, extendsFromPointItems,
        nextNeededPointItemsForExtend=50)清零。保留: grazeInTotal/rank/
        deaths/bombsUsed/spellCardsCaptured/已过面进度。
        """
        if not self.continue_available:
            return
        g = self.globals
        g.num_retries += 1
        self.stats.retries = g.num_retries
        g.gui_score = g.num_retries        # C: guiScore = numRetries (≈0)
        g.gui_score_difference = 0
        g.score = g.gui_score              # C: score = guiScore
        g.lives_remaining = float(self.initial_lives)
        g.bombs_remaining = self.shot_data.initial_bombs
        g.graze_in_stage = 0
        g.point_items_collected_this_stage = 0
        g.point_items_collected_for_extend = 0
        g.current_power = 0.0
        g.extends_from_point_items = 0
        g.next_needed_point_items_for_extend = 50
        g.cherry = g.cherry_start
        self._score_milestone = 0  # 续关分清零, 里程碑重新计
        self.game_over = False
        self.result = None
        self._result_cache = None

    # ---- 炸弹 ----
    def _bomb_ctx(self) -> BombContext:
        g = self.globals
        return BombContext(player_pos=self.player.pos, cherry=g.cherry,
                           cherry_start=g.cherry_start, difficulty=self.difficulty,
                           last_enemy_hit=self._last_enemy_hit,
                           rng_float=self.rng.unit)

    def _try_bomb(self) -> None:
        """炸弹触发 (Player.cpp:1719-1755); 成功后把透出事件接回 globals/boss。"""
        g = self.globals
        res = try_start_bomb(
            self.bomb, self._bomb_ctx(), focus=self.player.focus,
            bombs_remaining=g.bombs_remaining,
            respawn_timer=self.player.respawn_timer,
            initial_respawn_timer=self.shot_data.initial_respawn_timer,
            border_invulnerability_time=self.border.border_invulnerability_time,
            bomb_pressed=True,
            spellcard_active=bool(self.boss and self.boss.is_active))
        if not res.started:
            log.debug("bomb 触发被拒 (frame={}): bombs={} respawn_timer={} "
                      "border_invuln={} bomb_in_use={}", self.frame,
                      g.bombs_remaining, self.player.respawn_timer,
                      self.border.border_invulnerability_time,
                      self.bomb.is_in_use)
            return
        log.debug("bomb 触发 (frame={}, character={}, focus={}, 剩余炸弹={})",
                 self.frame, self.character, self.player.focus,
                 g.bombs_remaining - 1)
        # 炸弹发声音(BombData.cpp 各 *Calc timer==0) + 符卡横幅音
        # (Gui.cpp:356, ShowBombNamePortrait)
        self.sounds.play(_BOMB_SOUNDS.get((self.character, self.player.focus),
                                          SE.BOMB_REIMU_A))
        self.sounds.play(SE.BOMB)
        g.bombs_used += res.bombs_used_delta
        g.bombs_remaining += res.bombs_remaining_delta
        g.decrease_subrank(-res.subrank_delta)
        self.player.respawn_timer = res.respawn_timer
        if res.spellcard_capture_reset and self.boss:
            self.boss.mark_bombed()  # 用弹 → 本张符卡不算捕获
        # 炸弹首帧无敌由机体 calc 设定 (BombData *Calc timer==0 分支)
        self.player.invuln = max(self.player.invuln, self.bomb.invulnerability_timer)

    def _bomb_box_hit(self, pos: Vec2, full_size: tuple[float, float]) -> bool:
        """bomb 伤害盒与敌人盒(center, 全宽/全高)是否相交 (collisionOut 语义,
        Player.cpp:939-942); shoot_hits 的 graze 额外伤跳过判定用。"""
        return self.bomb.hits(pos, Vec2(full_size[0] / 2, full_size[1] / 2))

    def _spawn_point_bullet(self, pos: Vec2) -> None:
        """弹消点道具 (C RemoveAllBullets/DespawnBullets 的 this->itemType)。"""
        self.items.spawn(pos, ItemType.POINT_BULLET, power=self.power)

    def _apply_bomb_boxes(self) -> None:
        """炸弹盒生效: 清弹盒→弹转道具(CheckBombGraze), 伤害盒→敌人/Boss。

        【分路径结算, 与 C++ 有偏差】 C++ 在 CalcDamageToEnemy
        (Player.cpp:904-927) 把 bomb 盒 lifetime 与子弹伤害合成一笔再统一
        符卡缩放 (EnemyManager.cpp:849-868); 这里 bomb 盒单独 settle_damage
        (bomb_damage=True → 符卡中 /2.5 或 0), 子弹在 shoot_hits 里单独
        /7。同帧混合命中的总额与 C++ 差一个截断级甚至更高(除数不同);
        详见 enemies.EnemyHost.shoot_hits 的偏差注记。保持现状是为了不动
        既有测试钉住的数值语义与帧内作用顺序(这里在 ECL 步进前, C++ 在后)。"""
        g = self.globals
        bsize = Vec2(self.bullets.bullet_radius * 2.0, self.bullets.bullet_radius * 2.0)
        for b in self.bullets.alive():
            if b.spawn_state:
                continue  # 出生态弹不吃炸弹盒 (C++ CheckBombGraze 只在判定路径里触发)
            if self.bomb.check_bomb_graze(b.pos, bsize):
                self.items.spawn(b.pos, ItemType(self.bomb.item_type), power=self.power)
                b.dead = True
        for e in self.host.alive():
            dmg = self.bomb.damage_to(e.pos, Vec2(e.radius, e.radius))
            if dmg:
                r = settle_damage(int(dmg), is_boss=e.is_boss, is_focus=self.player.focus,
                                  bomb_in_use=True, bomb_damage=True, stage=self.stage_no,
                                  spellcard_active=self._spellcard_active(),
                                  used_bomb=bool(self.boss and self.boss.used_bomb),
                                  invincibility_timer=e.invincibility_timer,
                                  enemy_timer=e._tick, can_be_damaged=e.can_be_damaged)
                e.life -= r.damage
                g.add_score(r.score_code)
                self._last_enemy_hit = e.pos
                if r.damage:
                    self.sounds.play(SE.SOUND_20)  # 敌受击音 (EnemyManager.cpp:1052)
                if e.life <= 0 and e.kill():
                    self._kill_reward(e, self.frame)
        # ECL Boss 在敌人列表里已随上面循环结算; 仅演示 Boss 单独结算
        if self.boss and self.boss.is_active and self._boss_ecl_state is None:
            bd = self.bomb.damage_to(self.boss.pos, Vec2(30.0, 30.0))
            if bd:
                r = self.boss.damage(bd, from_bomb=True, is_focus=self.player.focus,
                                     stage=self.stage_no,
                                     is_reimu_a=self.character == CHAR_REIMU_A)
                g.add_score(r.score_code)
                if r.cherry_gain:
                    self._add_cherry_plus(r.cherry_gain)
                self._last_enemy_hit = self.boss.pos

    # ---- 结界 ----
    def _tick_border(self) -> None:
        """结界每帧 (UpdateBorderAndBombState + UpdateState 的 BORDER 分支)。"""
        g = self.globals
        if self.border.has_border == BorderState.READY and not self.bomb.is_in_use:
            # READY 时每帧尝试 ActivateBorder (Player.cpp:1694-1696), 按 playerState 择时
            if self.player.state == PlayerState.DEAD and self.player.respawn_timer != 0:
                self._break_border()  # 死亡中结界保命 (ActivateBorder 的 DEAD 分支)
            elif self.player.state == PlayerState.ALIVE:
                if self.border.activate_border():
                    self.player.state = PlayerState.BORDER
                    log.debug("结界激活 (frame={}, pos=({:.1f},{:.1f}))",
                              self.frame, self.player.pos.x, self.player.pos.y)
                    # 结界激活 (Player.cpp:2139-2140)
                    self.sounds.play(SE.BORDER_ACTIVATE)
                    self.sounds.play(SE.BORDER_ACTIVATE2)
        plus, res = self.border.tick(cherry=g.cherry, cherry_start=g.cherry_start,
                                     cherry_max=g.cherry_max)
        if res is not None:
            # 自然破 (BreakBorderNaturally): +10000 上限/樱点, 得分 (cherry-cherryStart)*10
            log.debug("结界自然破 (frame={}, 得分={})", self.frame, res.score)
            g.cherry = res.cherry
            g.cherry_max = res.cherry_max
            g.cherry_plus = res.cherry_plus
            g.add_score(res.score)
            self.player.state = PlayerState.INVULNERABLE
            self.player.invuln = max(self.player.invuln, res.invulnerability_timer)
            self.sounds.play(SE.BORDER_BREAK)  # Player.cpp:2015
        elif self.border.active:
            g.cherry_plus = plus  # 结界中 cherryPlus 随剩余时间衰减的显示值

    def _break_border(self, *, by_bomb_key: bool = False) -> None:
        """BreakBorder (Player.cpp:2148-2182): 主动破(bomb键)/中弹破/死亡破。"""
        reason = "主动破" if by_bomb_key else (
            "死亡破" if self.player.state == PlayerState.DEAD else "中弹破")
        log.debug("结界破[{}] (frame={}, pos=({:.1f},{:.1f}))", reason,
                  self.frame, self.player.pos.x, self.player.pos.y)
        box = self.border.break_border(self.player.pos)
        self._border_clear_boxes.append(box)
        self.globals.cherry_plus = self.globals.cherry_start
        self.player.state = PlayerState.INVULNERABLE
        self.player.invuln = max(self.player.invuln, BORDER_BREAK_INVULN)
        if self.boss:
            self.boss.mark_death()  # 结界破裂 → 捕获失败 (Player.cpp:2175-2176)
        if by_bomb_key:
            self.items.remove_all_items()  # Player.cpp:1691
        # 结界破 (Player.cpp:2191-2192, BreakBorder)
        self.sounds.play(SE.BOMB_MARISA_A_FOCUS)
        self.sounds.play(SE.BORDER_BREAK)

    def _tick_border_clear_boxes(self, bsize) -> None:
        """BreakBorder 的 SpawnBombEffect(32, +16, 50, CHERRY_SMALL): 弹转小樱点。"""
        size = Vec2(bsize[0], bsize[1])
        keep = []
        for box in self._border_clear_boxes:
            box.tick()
            if not box.active:
                continue
            for b in self.bullets.alive():
                if b.spawn_state:
                    continue  # 出生态弹不吃清弹圆 (同上, CheckBombGraze 路径不到)
                if box.hits(b.pos, size):
                    self.items.spawn(b.pos, ItemType(box.item_type), power=self.power)
                    b.dead = True
            keep.append(box)
        self._border_clear_boxes = keep

    # ---- Boss / 符卡 ----
    def _spellcard_active(self) -> bool:
        return bool(self.boss and self.boss.is_active and self.boss.spellcard_idx >= 0)

    # ---- GameEngine 协议(touhou/types.py)的公开访问器 ----
    # 门面(api.Game)只认这组公开名; 内部热路径继续用 _spellcard_active/_msg_active。
    def spellcard_active(self) -> bool:
        """符卡进行中(GameEngine 可选能力位)。"""
        return self._spellcard_active()

    def msg_active(self) -> bool:
        """对话/剧情进行中(GameEngine 可选能力位)。"""
        return self._msg_active()

    def _spawn_demo_boss(self) -> Boss:
        """手写演示 Boss(无 ECL 数据时的回退路径; 测试可直接调用)。"""
        b = Boss(name="小Boss", spellcard_scores=self.data.spellcard_scores)
        b.pos = Vec2(SCREEN.x / 2, 120)
        b.set_life(600)
        b.life_thresholds = [(400, 1), (200, 2)]
        b.begin_spellcard(0, 1800)  # is_active=1, 30 秒超时
        self.boss = b
        log.debug("Boss 出场(演示): {} stage={} (frame={})",
                  b.name, self.stage_no, self.frame)
        self.sounds.play(SE.BOMB)  # 符卡宣告 (Gui.cpp:396, ShowSpellcard)
        return b

    def _boss_spawn(self) -> None:
        if self.boss is None and self.frame == 600:
            self._spawn_demo_boss()

    def _tick_boss(self) -> None:
        """Boss 每帧: 自机弹伤害 → 生命阈值切阶段 → B.5 超时 → 捕获分衰减。"""
        if self.boss is None:
            return
        if self._boss_ecl_state is not None:
            self._tick_boss_ecl()
            return
        if not self.boss.is_active:
            return
        g = self.globals
        boss = self.boss
        # 自机弹伤害同样走 CalcDamageToEnemy (AABB 60×60, 命中弹进爆炸态);
        # 是弹伤非 bomb 伤 (from_bomb=False: 炸弹中弹伤仍按 collisionOut==0 缩放)
        boss_dmg = self.player.calc_damage_to_enemy(boss.pos, (60.0, 60.0))
        if boss_dmg:
            r = boss.damage(boss_dmg, from_bomb=False, is_focus=self.player.focus,
                            bomb_in_use=self.bomb.is_in_use, stage=self.stage_no,
                            is_reimu_a=self.character == CHAR_REIMU_A)
            g.add_score(r.score_code)
            if r.cherry_gain:
                self._add_cherry_plus(r.cherry_gain)
            self._last_enemy_hit = boss.pos
        self.targeting.update(boss.pos, self.player.pos, is_boss=True,
                              is_sakuya=self.character in _SAKUYA_CHARACTERS)
        if boss.life <= 0:
            self._apply_spellcard_end(boss.end_spellcard())  # 击破
            return
        # 阶段切换(生命阈值, HandleLifeCallback)
        cb = boss.check_life_threshold(self._clear_field)
        if cb:
            self._apply_spellcard_end(boss.end_spellcard())
            g.add_score(10000)  # 显示分 1000 → 代码值*10
            boss.begin_spellcard(min(cb, len(SPELLCARD_SCORE) - 1), 1800,
                                 timeout_sub=cb + 1)
        # B.5 超时状态机
        ev = boss.handle_timer_callback(cherry_above_start=g.cherry - g.cherry_start,
                                        clear_field_cb=self._clear_field)
        if ev["fired"]:
            if ev["cherry_penalty"]:
                g.cherry = max(g.cherry_start, g.cherry - ev["cherry_penalty"])
            if ev["remove_all_bullets"]:
                self.bullets.clear()
                # RemoveAllBullets(10) 连带激光: flags&4 不豁免, 无道具
                self.lasers.remove_all(spawn_items=False, skip_flag4=False)
            self._apply_spellcard_end(boss.end_spellcard())
            nxt = ev["callback"]
            if nxt and boss.life > 0:
                boss.begin_spellcard(min(nxt, len(SPELLCARD_SCORE) - 1), 1800,
                                     timeout_sub=nxt + 1)
        boss.tick()

    def _tick_boss_ecl(self) -> None:
        """ECL 驱动的 Boss: 只同步状态 + 捕获分衰减; 伤害走 shoot_hits
        (boss 敌人带 is_boss 在敌人列表里), 阶段/超时由 ECL 回调驱动。"""
        boss = self.boss
        st = self._boss_ecl_state
        assert boss is not None and st is not None  # ECL boss 存活期才进此函数
        boss.pos = Vec2(st.pos.x, st.pos.y)
        boss.life = max(st.life, 0)
        boss.max_life = max(st.max_life, 1)
        boss.invincibility_timer = st.invincibility_timer
        boss.is_survival_spellcard = bool(st.is_survival_spellcard)
        boss.tick()
        e = self._boss_ecl_enemy
        if e is None or not e.alive:
            # Boss 敌人已退场(击破/逃走)且 ECL 未自己 END_SPELLCARD 时兜底
            if boss.is_active:
                self._apply_spellcard_end(boss.end_spellcard())
            self.boss = None
            self._boss_ecl_state = None
            self._boss_ecl_enemy = None

    def _apply_spellcard_end(self, res: dict) -> None:
        """EndSpellcard 透出事件入账: 捕获得分(代码值)/计数/清弹转道具/清敌。"""
        if not res["ended"]:
            return
        # 符卡结束音 (EclManager.cpp:847, EclManager::EndSpellcard 收尾)
        self.sounds.play(SE.ENEMY_SPELLCARD_END)
        name = self.boss.name if self.boss is not None else "?"
        if res["captured"]:
            log.debug("符卡捕获: {} (frame={}, 分数={})",
                      name, self.frame, res["score"] // 10)
        elif res["timed_out"]:
            log.debug("符卡超时: {} (frame={})", name, self.frame)
        else:
            log.debug("符卡未捕获: {} (frame={})", name, self.frame)
        if res["captured"]:
            self.globals.add_score(res["score"])  # capture+grazeBonus 已是代码值
            self.globals.spell_cards_captured += res["spell_cards_captured"]
            self.stats.add_spellcard()
            # catk: 捕获成功 successes++/highscore 取 max (EclManager.cpp EndSpellcard)。
            # 只接 ECL 路径(_catk_idx 由 begin 登记); 演示 Boss 不统计。
            if self._catk_idx is not None:
                self.store.record_spellcard_success(
                    self._catk_idx, self.character, res["score"] // 10)
        self._catk_idx = None
        if res["despawn_bullets"]:
            for b in self.bullets.alive():
                self.items.spawn(b.pos, ItemType.POINT_BULLET, power=self.power)
                b.dead = True
            # DespawnBullets(8000,1) 连带激光 (BulletManager.cpp:524-550):
            # 无 flags&4 豁免, 原点+沿线出弹消点
            self.lasers.remove_all(spawn_items=True, skip_flag4=False,
                                   spawn_at_pos=True,
                                   spawn_item=self._spawn_point_bullet)
        if res["remove_all_enemies"]:
            if self.ecl_host is not None:
                # C RemoveAllEnemies 跳过 boss(ECL  Boss 机体还要跑后续 sub)
                self.globals.add_score(self.ecl_host.remove_all_enemies(8000, 0))
            else:
                self.host.clear()

    def _clear_field(self) -> None:
        if self.ecl_host is not None:
            self.ecl_host.remove_all_enemies(8000, 0)
        else:
            self.host.clear()
        self.bullets.clear()
        # 演示 Boss 路径的连带激光(同清弹一并消散; C++ HandleLifeCallback
        # 本身不清弹, 此处的清弹是演示路径的简化, 激光跟随之)
        self.lasers.remove_all(spawn_items=False, skip_flag4=True)

    def _kill_reward(self, e, counter: int) -> None:
        """击杀入账: 得分 + 掉落 + 击坠音。ECL 敌人按 C 死亡分支 (enemy->score/itemDrop);
        合成波次敌人维持旧的 500 显示分 + 随机掉落表。"""
        # 敌死亡音 (EnemyManager.cpp:1016, PlaySoundByIdx(i % 2 + 2) 两档音量交替)
        self.sounds.play(SE(counter % 2 + 2))
        g = self.globals
        if isinstance(e, EclEnemy):
            st = e.state
            if not e._kill_no_score:  # C 死亡分支: 仅 case 2(death_type==2) 无 AddScore
                g.add_score(st.score)  # AddScore(enemy->score)
            d = st.item_drop
            if 0 <= d:
                try:
                    t = ItemType(d)
                except ValueError:
                    t = ItemType.NO_ITEM
                if t != ItemType.NO_ITEM:
                    self.items.spawn(e.pos, t, power=self.power)
            elif d == -1:
                # C: randomItemSpawnIdx%3==0 才掉, 掉落表索引独立递增
                if self._rand_spawn_idx % 3 == 0:
                    self.items.drop_random(e.pos, table=self._drop_table,
                                           counter=self._rand_table_idx,
                                           power=self.power)
                    self._rand_table_idx = (self._rand_table_idx + 1) % 32
                self._rand_spawn_idx += 1
            if st.is_boss and not self._spellcard_active():
                # boss 击坠且非符卡中: 清弹转道具 + 清场 (C 死亡分支 case 2)
                for b in self.bullets.alive():
                    self.items.spawn(b.pos, ItemType.POINT_BULLET, power=self.power)
                    b.dead = True
                # DespawnBullets(8000,1) 连带激光 (BulletManager.cpp:524-550)
                self.lasers.remove_all(spawn_items=True, skip_flag4=False,
                                       spawn_at_pos=True,
                                       spawn_item=self._spawn_point_bullet)
                if self.ecl_host is not None:
                    g.add_score(self.ecl_host.remove_all_enemies(8000, 0))
            return
        g.add_score(5000)  # 显示分 500/杀 → 代码值*10
        self.items.drop_random(e.pos, table=self._drop_table,
                               counter=counter, power=self.power)

    # ---- 玩家事件消费 ----
    def _consume_player_events(self) -> None:
        g = self.globals
        for ev in self.player.take_events():
            k = ev.kind
            if k == PlayerEventKind.DEATH_SETTLE:
                # DEATH_SETTLE 事件必带 th07 的 DeathSettle(基类注解为通用基座,
                # 本作品运行时恒为子类 —— isinstance 收窄兼作不变量断言)
                assert isinstance(ev.data, DeathSettle)
                pos = self._death_pos or self.player.pos
                log.debug("玩家死亡 (frame={}, pos=({:.1f},{:.1f}), "
                          "power {}→{}, 残机={})", self.frame, pos.x, pos.y,
                          int(g.current_power), int(ev.data.new_power),
                          g.lives_remaining)
                self._apply_death_settle(ev.data)
            elif k == PlayerEventKind.RESPAWNED:
                # C++ 残机在重生时扣 (Player.cpp UpdateDeath else 分支 AddLivesRemaining(-1))
                if g.lives_remaining > 0:
                    g.lives_remaining -= 1
                    g.bombs_remaining = self.shot_data.initial_bombs
                    log.debug("重生 (frame={}, 剩余残机={})", self.frame,
                             g.lives_remaining)
                else:
                    log.debug("无残机 → GameOver (frame={})", self.frame)
                    self.game_over = True  # 无残机 → C++ 进 retry 菜单
            elif k == PlayerEventKind.GRAZE:
                g.graze_in_stage = min(GRAZE_STAGE_CAP, g.graze_in_stage + 1)
                g.graze_in_total = min(GRAZE_TOTAL_CAP, g.graze_in_total + 1)
                g.add_score(GRAZE_SCORE_DISPLAY * 10)  # 擦弹显示 200 → 代码值 2000
                g.increase_subrank(GRAZE_SUBRANK)
                self.stats.add_graze()
                if self.boss and self.boss.is_capturing:
                    self.boss.add_graze_bonus(g.cherry - g.cherry_start)
            elif k == PlayerEventKind.BREAK_BORDER:
                self._break_border()
            elif k == PlayerEventKind.REMOVE_ALL_BULLETS:
                self.bullets.clear()  # 重生后 60 帧清弹期(bulletGracePeriod)
                # RemoveAllBullets(0) 连带激光 (Player.cpp:1914 →
                # BulletManager.cpp:439-471): flags&4 豁免, 无道具
                self.lasers.remove_all(spawn_items=False, skip_flag4=True)

    def _apply_death_settle(self, s: DeathSettle) -> None:
        """死亡结算 (Player.cpp UpdateDeath respawnTimer==0 分支)。"""
        g = self.globals
        g.current_power = s.new_power
        g.deaths += 1
        self.stats.deaths += 1
        pos = self._death_pos or self.player.pos
        for _ in range(s.drop_power_big):
            self.items.spawn(pos, ItemType.POWER_BIG, power=s.new_power)
        for _ in range(s.drop_power_small):
            self.items.spawn(pos, ItemType.POWER_SMALL, power=s.new_power)
        for _ in range(s.drop_full_power):
            self.items.spawn(pos, ItemType.FULL_POWER, power=s.new_power)
        if s.cherry_penalty:
            g.cherry = max(g.cherry_start, g.cherry - s.cherry_penalty)
        if s.activate_all_items:
            self.items.activate_all_items()
        if s.subrank_delta:
            g.decrease_subrank(-s.subrank_delta)
        if self.boss:
            self.boss.mark_death()  # 死亡 → 捕获失败 (Player.cpp:1782-1783)

    # ---- 擦弹与结算 ----
    def tally_spellcard(self) -> None:
        """记录捕获一张符卡(旧手动接口; 正常流程由 end_spellcard 透出处理)。"""
        if self.boss and self.boss.is_capturing:
            self.boss.mark_death()
        self.globals.spell_cards_captured += 1
        self.stats.add_spellcard()

    def final_result(self, *, cleared: bool = False, slow_percent: float = 0.0,
                     name: str | None = None) -> dict:
        """结算: 汇总 globals → RunStats + 评级 + 入榜 + 写 store(内存)。

        slow_percent: 固定 60fps 下恒 0(无减速统计), 参数仅留接口。
        name: 入榜记录名; None = 带出 LSNM(store.last_name, 原版
        curScore.name = lsnmHeader.name), 名字输入态完成后由 view 经
        store.set_entry_name 改写并保存 LSNM。
        幂等: 一局只结算一次(重复调用返回缓存, 不重复入榜/计数);
        落盘由调用方(view 结算画面确认时)负责。
        """
        if self._result_cache is not None:
            return self._result_cache
        if name is None:
            name = self.store.last_name
        g = self.globals
        g.snap_gui_score()  # CutChain: 显示分对齐真实分
        st = self.stats
        st.score = g.score
        st.cleared = cleared
        st.clear_percent = 1.0 if cleared else clear_percent(self.frame / 60.0)
        st.retries = g.num_retries
        st.bombs_used = g.bombs_used
        st.graze_total = g.graze_in_total
        # 点道具: 已过关面的累计 + 本关(_advance_stage 入账前关, 终面用当前值)
        st.point_items_collected = (self._point_items_prev_stages
                                    + g.point_items_collected_this_stage)
        st.play_time_frames = self.frame
        rank_value = rating(st, slow_percent=slow_percent)
        rec = make_highscore_record(
            g.score, self.character, self.difficulty, self.stage_no,
            name=name, num_retries=g.num_retries)
        pos = self.store.insert_score(rec)
        self.toplist.insert(ScoreRecord(score=g.score, character=self.character,
                                        difficulty=st.difficulty, stage=self.stage_no))
        if cleared:
            # CLRD: currentStage-1 = 通过的面数(本期只有 1 面 → 1)
            self.store.record_clear(self.character, self.difficulty,
                                    self.stage_no, g.num_retries)
        self.store.record_run_end(self.character, self.difficulty, score=g.score,
                                  frames=self.frame, cleared=cleared,
                                  num_retries=g.num_retries)
        self._result_cache = {
            "score": g.score, "rating": round(rank_value, 1), "rank": pos,
            "cleared": cleared, "clear_percent": st.clear_percent * 100.0,
            "difficulty": self.difficulty, "character": self.character,
            "stage": self.stage_no, "name": name,
            "retries": g.num_retries, "deaths": int(st.deaths),
            "bombs": g.bombs_used, "spellcards": st.spellcards_captured,
            "graze": st.graze_total, "point_items": st.point_items_collected,
            "slow_percent": slow_percent,
            "high_score": self.store.high_score(self.difficulty, self.character),
        }
        return self._result_cache

    # ---- 渲染(离屏/无窗口用) ----
    def render_into(self, arr, *, width: int, height: int) -> None:
        arr[..., 0] = 8; arr[..., 1] = 12; arr[..., 2] = 30; arr[..., 3] = 255
        for b in self.bullets.alive():
            y0, x0 = int(b.pos.y) - 4, int(b.pos.x) - 4
            if 0 <= x0 < width and 0 <= y0 < height:
                arr[y0:y0 + 8, x0:x0 + 8, :3] = (235, 235, 90)
        for e in self.host.alive():
            y0, x0 = int(e.pos.y) - 12, int(e.pos.x) - 12
            if 0 <= x0 < width and 0 <= y0 < height:
                arr[y0:y0 + 24, x0:x0 + 24, :3] = (250, 40, 220)
        for s in self.player.shots:
            y0, x0 = int(s.pos.y) - 6, int(s.pos.x) - 2
            if 0 <= x0 < width and 0 <= y0 < height:
                arr[y0:y0 + 12, x0:x0 + 4, :3] = (120, 220, 255)
        px, py = int(self.player.pos.x), int(self.player.pos.y)
        for dy in range(-8, 9):
            for dx in range(-8, 9):
                if dx * dx + dy * dy <= 64 and 0 <= py + dy < height and 0 <= px + dx < width:
                    arr[py + dy, px + dx, :3] = (255, 255, 255)

    def render_frame(self) -> np.ndarray:
        w, h = int(SCREEN.x), int(SCREEN.y)
        out = np.zeros((h, w, 4), dtype=np.uint8)
        self.render_into(out, width=w, height=h)
        return out

    # ---- 批量跑(无窗口) ----
    def run(self, *, frames: int = 120) -> np.ndarray | None:
        """离屏跑 N 帧并渲染, 返回末帧 RGB 数组。窗口玩法请用 engine/view/sprite_view.py 的 GameView。"""
        print(f"《第{self.stage_no}面 {self.stage.title}》 BGM: {self.stage.bgm_names[0]}")
        last: np.ndarray | None = None
        for i in range(frames):
            self.tick()
            last = self.render_frame()
        print(f"跑完 {frames} 帧 | 分数={self.globals.gui_score} 残机={self.lives}")
        return last

    def dump_frames(self, frames: int = 120, outdir: str | Path = "out_frames") -> None:
        """离线导出若干帧 PNG(便于无窗口环境查看/验证)。"""
        outdir = Path(outdir)
        outdir.mkdir(exist_ok=True)
        for i in range(frames):
            self.tick()
            Image.fromarray(self.render_frame(), "RGBA").save(outdir / f"f{i:03d}.png")
        print(f"已导出 {frames} 帧到 {outdir}/ | 分数={self.globals.gui_score} 残机={self.lives}")
