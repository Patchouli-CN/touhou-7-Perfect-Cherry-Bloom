"""主游戏逻辑 —— ImperishableNight(TH08 东方永夜抄)。

对局骨架照 th07(games/th07/world.py)改编: 帧流水线(输入 → bomb →
player.step → ECL/msg step → 敌人 → 体术 → 自机弹 → boss → 弹/擦弹 →
道具 → 激光 → 玩家事件 → 计分 → 通关判定 → 帧末事件快照)同构;
th07 专属段(樱点/结界)删除, 换成 th08 的时刻(Th08Clock/时刻符点)与
妖率计段。

th08 专属(出处 Reference/th08-ref/src/, 各方法注释标行号):
- 关卡资源: 文件名不规整(4A/4B), 按 data.STAGE_STD_FILES/STAGE_ECL_FILES/
  MSG_FILES 表取(下标 = C currentStage = stage_no-1);
- msg: 文本 XOR 0x77 + 立绘 4 槽(schema/msg.py 参数化), MsgRead 清场在
  宿主侧(ecl_host.msg_read, Gui.cpp:242-244);
- 时刻符点: ITEM_TIME 道具收集 → globals.add_time_orbs; 阈值表
  data.TIME_ORB_THRESHOLDS 进 globals.last_spell_time_orb_threshold
  (GameManager.cpp:881); 变量 10098 的发布在 _step_ecl;
- 妖率计: 收集/擦弹/死亡联动(ItemManager.cpp:634-636/Player.cpp:483-484/
  :534 SetYoukaiGauge(0)); 槽界按机体(Player.cpp:1607-1639);
- 决死: world._try_bomb 的 DEAD→INVULNERABLE 模式照 th07 world.py:1377-1385;
  决死窗公式(bombs×6+达标7/符卡×2/灵梦系×9/5, Player.cpp:535-557)与
  Last Spell/时刻结局/换关分支精修是后续阶段(单 B)的工作。

本类是 plain class, 满足 touhou/types.py 的 GameEngine 协议(无基类)。
"""

from __future__ import annotations

from pathlib import Path

from ...engine.bullets import BulletWorld
from ...engine.ecl import EclWorld
from ...engine.ending import EndingData
from ...engine.enemies import EclEnemy, EnemyHost, Targeting, settle_damage
from ...engine.events import EventBus
from ...engine.lasers import LaserWorld
from ...engine.rng import Rng
from ...engine.score_store import ScoreStore, make_highscore_record
from ...paths import DEFAULT_SCORE_PATH, resolve_data_path
from ...registry import GameData, GameHooks, register_world_impl
from ...schema.archive import open_archive
from ...schema.msg import MsgFile, MsgVm
from ...schema.shot_data import parse_sht_th08
from ...schema.sound import SoundQueue
from ...schema.stage import Stage
from ...utils import Vec2
from .bomb import (
    EVENT_END_PLAYER_SPELLCARD,  # noqa: F401 (事件名留档)
    EVENT_REMOVE_ALL_ITEMS,
    BOMB_SE,
    BombContext,
    Th08Bomb,
    try_start_bomb,
)
from .boss import Th08Boss
from .crypt import try_decrypt_from_table
from .data import (
    CHARACTER_SHT,
    MSG_FILES,
    STAGE_ECL_FILES,
    STAGE_STD_FILES,
    TH08_DATA,
)
from .ecl_host import Th08GameEclHost
from .ecl_file import EclFileTh08
from .ecl_timeline import Th08TimelineRunner
from .ecl_vm import EclMachineTh08
from .globals import (
    GRAZE_STAGE_CAP,
    GRAZE_TOTAL_CAP,
    Th08Globals,
    next_point_item_extend_threshold,
)
from .items import (
    FULL_POWER,
    OFFSCREEN_SUBRANK_PENALTY,
    STATE_ATTRACT,
    GameContext,
    ItemType,
    ItemWorld,
)
from .player import (
    GRAZE_GAUGE_YOUKAI,
    GRAZE_SCORE_MODERATE_YOUKAI,
    GRAZE_SCORE_NORMAL,
    GRAZE_SUBRANK,
    DeathContext,
    DeathSettle,
    KillResult,
    PlayerEventKind,
    PlayerState,
    Th08Player,
)

from ...logger import logger as log

# 音效索引 (th08 SoundPlayer.hpp SoundIdx)
SE_ENEMY_DEAD_A = 2  # SOUND_2(敌击坠两档交替, EnemyManager.cpp:298 段)
SE_DAMAGE = 20  # SOUND_DAMAGE(敌受击)
SE_ITEM = 21  # SOUND_ITEM(道具入袋, ItemManager.cpp:375)
SE_1UP = 28  # SOUND_1UP(奖残)
SE_GRAZE = 30  # SOUND_GRAZE(player._on_graze 内播)
SE_POWERUP = 31  # SOUND_POWERUP(火力升档/满火力)
SE_SPELL_DECLARE = 15  # SOUND_F(符卡宣告近似; cut-in 演出是 view 侧)
SE_SPELLCARD_END = 18  # SOUND_TOTAL_BOSS_DEATH(符卡收束近似)

# 换关分支 (GameManager::AdvanceToNextStage, GameManager.cpp:1472-1525):
# 3 面后按机体分 4A(=stage_no 4)/4B(=5); 5 面后按 finalStageRoute 分
# 6A(=7)/6B(=8); 6A→6B; EX(=9)无后继
_NEXT_STAGE_PLAIN = {1: 2, 2: 3, 4: 6, 5: 6, 7: 8}
# 3 面 → 4A/4B 按机体 (GameManager.cpp:1483-1505): 灵梦系/妖梦系 → 4B,
# 魔理沙系/咲夜系 → 4A
_STAGE4_BRANCH = {
    0: 5, 4: 5, 5: 5,  # ReimuYukari/Reimu/Yukari → STAGE4B
    1: 4, 6: 4, 7: 4,  # MarisaAlice/Marisa/Alice → STAGE4A
    2: 4, 8: 4, 9: 4,  # SakuyaRemilia/Sakuya/Remilia → STAGE4A
    3: 5, 10: 5, 11: 5,  # YoumuYuyuko/Youmu/Yuyuko → STAGE4B
}


def _power_level(power: float, levels: tuple[int, ...]) -> int:
    """火力档位 (g_PowerUpThresholds 的 while 循环)。"""
    n = 0
    while n < len(levels) and int(power) >= levels[n]:
        n += 1
    return n


@register_world_impl("th08")
class ImperishableNight:
    """《东方永夜抄》主逻辑类(注册为 th08 的对局实现, 见 registry)。"""

    def __init__(
        self,
        data_path: str | Path | None = None,
        character: int = 0,
        difficulty: int = 1,
        *,
        score_store: ScoreStore | None = None,
        score_path: str | Path | None = None,
        initial_lives: int = 3,
        seed: int | None = None,
        hooks: GameHooks | None = None,
        data: GameData | None = None,
    ) -> None:
        self.hooks = hooks or GameHooks(msg_file="msg{n}{team}.dat")
        self.data = data if data is not None else TH08_DATA
        self._drop_table: list[int] | None = (
            list(self.data.drop_table) if self.data.drop_table else None
        )
        self.archive = open_archive(
            resolve_data_path(data_path, game="th08"), game="th08"
        )
        # stage_no: 1..9 = C currentStage+1 (4=4A 5=4B 6=5面 7=6A 8=6B 9=EX)
        self.stage_no = 1
        self.character = character
        self.difficulty = difficulty
        self.stage = self._read_stage(self.stage_no)

        # 射击数据(双表: 非 focus / focus, Player.cpp:35-44 的双 sht;
        # 条目带 "edz" 内层加密, crypt.py 解)
        sht_map = self.data.character_sht or CHARACTER_SHT
        n_unf, n_foc = sht_map.get(character, sht_map[0])
        self.shot_data = parse_sht_th08(try_decrypt_from_table(self.archive.load(n_unf)))
        self.shot_data_focus = parse_sht_th08(
            try_decrypt_from_table(self.archive.load(n_foc))
        )

        # 游戏实体
        self.player = Th08Player(
            shot_data=self.shot_data,
            shot_data_focus=self.shot_data_focus,
            shot_type=character,
        )
        self.bullets = BulletWorld()
        self.bullets.player_pos = self.player.pos
        self.lasers = LaserWorld()
        self.host = EnemyHost()
        self.items = ItemWorld()
        self.bomb = Th08Bomb(shot_type=character)
        self.event_bus = EventBus()
        self.boss: Th08Boss | None = None
        # 回放确定性: seed=None 保持 th07 同款默认(0x5EED/0)
        self.seed = 0x5EED if seed is None else (seed & 0xFFFF)
        self._ecl_seed = 0 if seed is None else ((self.seed ^ 0x3C7) & 0xFFFF)
        self.rng = Rng(self.seed)
        self._inject_player_rng()
        self.items.rng_float = lambda r: self.rng.in_range(0.0, r)  # 时刻符点出生速度随机
        self.targeting = Targeting()

        # 成绩持久化(内存库; th08 catk 是另一格式, 本期按 0 张符卡建库)
        if score_store is not None:
            self.store = score_store
        else:
            self.store = ScoreStore.load(
                score_path or DEFAULT_SCORE_PATH, spellcard_count=0
            )
        self.store.record_play(character, difficulty)
        self.result: dict | None = None
        self.cleared = False
        self.stage_results: dict | None = None
        self.ending: EndingData | None = None
        self._pending_next_level = False
        self._result_cache: dict | None = None
        self._catk_idx: int | None = None

        # ---- 音效/BGM/震屏事件(帧末快照, 播放层消费) ----
        self.sounds = SoundQueue()
        self.frame_sounds: list[int] = []
        self.bgm_events: list[tuple] = []
        self.frame_shakes: list[tuple[int, int, int]] = []
        self.frame_bgm: list[tuple] = []
        self.player.sound = self.sounds

        # 状态
        self.frame = 0
        self.globals = Th08Globals()
        self.globals.initialize_rank(difficulty, self.data.rank_table)
        self.globals.high_score = self.store.high_score(difficulty, character)
        self.globals.high_score_num_continues = self.store.high_score_continues(
            difficulty, character
        )
        # 妖率槽界按机体 (Player.cpp:1607-1639)
        self._init_gauge_bounds()
        # 点道具初值按难度 (GameManagerSetup.cpp:149-161)
        if self.data.point_item_values:
            self.globals.point_item_value = self.data.point_item_values[
                min(difficulty, len(self.data.point_item_values) - 1)
            ]
        self.globals.next_point_item_extend_threshold = (
            next_point_item_extend_threshold(0, difficulty)
        )
        g0 = self.globals
        if difficulty >= 4:
            # C: Extra 默认残机 2 (difficulty>=4 → lifeCount=2)
            g0.lives_remaining = 2.0
        else:
            g0.lives_remaining = float(initial_lives)
        # 开局炸弹 = sht initialBombCount (Player.cpp:1592 段 AddedCallback)
        g0.bombs_remaining = self.shot_data.initial_bombs
        self.game_over = False
        self.max_retries = 3  # 续关上限(简化; th08 精确语义留后续)
        self.initial_lives = initial_lives
        self._death_pos: Vec2 | None = None
        self._score_milestone = 0

        # ---- ECL 关卡脚本 ----
        self.ecl_file: EclFileTh08 | None = None
        self.ecl_world: EclWorld | None = None
        self.ecl_host: Th08GameEclHost | None = None
        self.ecl_timelines: list[Th08TimelineRunner] = []
        self.msg_file: MsgFile | None = None
        self.msg_vm: MsgVm | None = None
        self._boss_ecl_state = None
        self._boss_ecl_enemy: EclEnemy | None = None
        self._rand_spawn_idx = 0  # C enemyDropCounter (itemDrop==-1 每 3 掉 1)
        self._rand_table_idx = 0  # C enemyDropScheduleIndex
        self._load_ecl()

    def _init_gauge_bounds(self) -> None:
        """妖率槽界按机体变体 (Player.cpp:1613-1639): 咏唱妖梦(3)/妖梦单人(10)
        半幅; 单人人类妖侧封顶 2000; 单人妖怪人侧封底 -2000。"""
        base = list(self.data.youkai_gauge_bounds or (-10000, 10000, -8000, 8000,
                                                      -2000, 2000))
        c = self.character
        if c == 3:
            base[0], base[2], base[4] = -5000, -3000, -2000
        elif c == 10:
            base = [-5000, 5000, -3000, 3000, -2000, 2000]
        elif c >= 4 and c % 2 == 0:  # IsSoloHuman (GameManager.hpp:189-192)
            base[1], base[3], base[5] = 2000, 8000, 2001
        elif c >= 4:  # IsSoloYoukai
            base[0], base[2], base[4] = -2000, -8000, -2001
        self.globals.gauge_bounds = base

    # ---- 回放确定性 ----
    def set_seed(self, seed: int) -> None:
        """开局后、首帧前重设种子(重建 rng 与 ECL rng; 换关自动带新种子)。"""
        self.seed = seed & 0xFFFF
        self._ecl_seed = (self.seed ^ 0x3C7) & 0xFFFF
        self.rng = Rng(self.seed)
        self.items.rng_float = lambda r: self.rng.in_range(0.0, r)
        if self.ecl_world is not None:
            self.ecl_world.rng = Rng(self._ecl_seed)

    def _inject_player_rng(self) -> None:
        """把 Player.rand_float 接到 self.rng(确定性)。"""
        self.player.rand_float = lambda r: self.rng.unit() * r

    # ---- ECL 关卡装载 ----
    def _load_ecl(self) -> None:
        """加载本关 ECL/msg 并接好宿主/时间轴; 缺资源则不留时间轴(空转)。"""
        try:
            data = self.archive.load(STAGE_ECL_FILES[self.stage_no - 1])
        except (KeyError, IndexError):
            self.ecl_file = None
            self.ecl_timelines = []
            return
        data = try_decrypt_from_table(data)
        self.ecl_file = EclFileTh08.parse(data)
        self.ecl_world = EclWorld(
            rng=Rng(self._ecl_seed),
            difficulty=self.difficulty,
            rank=self.globals.rank,
            current_stage=self.stage_no,
            player_shottype=self.character,
        )
        self.ecl_host = Th08GameEclHost(
            self.ecl_file,
            self.ecl_world,
            enemies=self.host,
            bullets=self.bullets,
            lasers=self.lasers,
            items=self.items,
            ecl_machine_cls=EclMachineTh08,
            extra=self.difficulty >= 4,
        )
        self.ecl_host.sound = self.sounds
        self.ecl_host.on_set_boss = self._ecl_on_set_boss
        self.ecl_host.on_begin_spellcard = self._ecl_on_begin_spellcard
        self.ecl_host.on_end_spellcard = self._ecl_on_end_spellcard
        self.ecl_host.on_spellcard_timeout = self._ecl_on_spellcard_timeout
        self.ecl_host.on_set_power = lambda v: setattr(
            self.globals, "current_power", float(v)
        )
        # 对话系统: msg 文件按 (关, 机体) 表取 (Gui.cpp:2098 LoadMsg);
        # 文本 XOR 0x77, 立绘 4 槽; 缺资源则不留 VM(不停轴)
        self.msg_file = None
        self.msg_vm = None
        try:
            raw = self.archive.load(MSG_FILES[self.stage_no - 1][self.character])
            self.msg_file = MsgFile.parse(try_decrypt_from_table(raw), text_xor=0x77)
            self.msg_vm = MsgVm(self.msg_file, num_portraits=4)
            self.msg_vm.pause_min_frames = 6  # th08 MsgRead: waitThreshold=6
            # (Gui.cpp:241)
            self.ecl_host.msg_vm = self.msg_vm
        except (KeyError, IndexError):
            self.ecl_host.msg_vm = None
        # 时刻符点阈值 (GameManager.cpp:881)
        if self.data.time_orb_thresholds:
            row = self.data.time_orb_thresholds[
                min(self.stage_no - 1, len(self.data.time_orb_thresholds) - 1)
            ]
            self.globals.last_spell_time_orb_threshold = row[
                min(self.difficulty, len(row) - 1)
            ]
        self.ecl_timelines = [
            Th08TimelineRunner(self.ecl_file, i, self.ecl_world, self.ecl_host)
            for i in range(len(self.ecl_file.timelines))
        ]

    def _step_ecl(self) -> None:
        """每帧: 同步世界快照 → 推进全部时间轴。"""
        h = self.ecl_host
        assert h is not None
        w = self.ecl_world
        assert w is not None
        # 变量 10098: 时刻符点 Last Spell 状态 (EclOperandsInt.cpp:139-144):
        # 当前+符卡待给+场上 ≥ 阈值 → 2(符卡待给 pendingTimeOrbs 是单 B 接线)
        g = self.globals
        on_field = sum(1 for it in self.items.alive() if it.type == ItemType.TIME)
        w.last_spell_orb_status = (
            2
            if g.current_time_orbs + on_field >= g.last_spell_time_orb_threshold
            else 0
        )
        if self.boss is not None and self.boss.is_active:
            w.spellcard_capture_status = int(self.boss.is_capturing)
            w.spellcard_timer_frames = self.boss.timer
        else:
            w.spellcard_capture_status = int(
                self.boss is not None and self.boss.spellcard_idx >= 0
            )
            w.spellcard_timer_frames = 0
        h.frame_update(
            player_pos=self.player.pos,
            difficulty=self.difficulty,
            rank=g.rank,
            power=self.power,
            shottype=self.character,
            spellcard_active=self._spellcard_active(),
            frozen=self.bomb.is_in_use or self.player.state != PlayerState.ALIVE,
            bomb_in_use=self.bomb.is_in_use,
            player_is_youkai=self.player.is_youkai,
        )
        for tl in self.ecl_timelines:
            tl.step()

    # ---- ECL Boss/符卡桥接(Th08Boss 只记账, 阶段/超时切换由 ECL 驱动) ----
    def _ecl_on_set_boss(self, idx: int, st) -> None:
        if st is None:
            if self._boss_ecl_state is not None and self._boss_ecl_state.boss_id == idx:
                if self.boss and self.boss.is_active:
                    self._apply_spellcard_end(self.boss.end_spellcard())
                self.boss = None
                self._boss_ecl_state = None
                self._boss_ecl_enemy = None
            return
        b = Th08Boss(name=f"boss{idx}")
        b.boss_id = idx
        b.pos = Vec2(st.pos.x, st.pos.y)
        b.set_life(max(st.life, 1))
        log.debug(
            "Boss 出场: boss_id={} stage={} pos=({:.1f},{:.1f}) (frame={})",
            idx, self.stage_no, st.pos.x, st.pos.y, self.frame,
        )
        self.boss = b
        self._boss_ecl_state = st
        assert self.ecl_host is not None
        self._boss_ecl_enemy = self.ecl_host.enemy_by_state.get(id(st))

    def _ecl_on_begin_spellcard(self, st, gui_id: int, idx: int, name: str) -> None:
        if self._boss_ecl_state is not st:
            self._ecl_on_set_boss(st.boss_id if st.boss_id >= 0 else 0, st)
        boss = self.boss
        assert boss is not None
        boss.name = name
        boss.spellcard_face = gui_id
        boss.is_survival_spellcard = bool(st.is_survival_spellcard)
        timeout = (
            st.timer_callback_threshold if st.timer_callback_threshold > 0 else 3600
        )
        boss.set_life(max(st.life, 1))
        assert self.ecl_host is not None
        boss.begin_spellcard(
            idx,
            timeout,
            timeout_sub=max(st.timer_callback_sub, 0),
            bonus=max(self.ecl_host.pending_spellcard_bonus, 0),
        )
        self._catk_idx = idx
        self.sounds.play(SE_SPELL_DECLARE)  # 符卡宣告(cut-in 演出是 view 侧)
        log.debug(
            "符卡宣言: #{} {} (stage={}, bonus={}, 时限={}s) (frame={})",
            idx, name, self.stage_no,
            self.ecl_host.pending_spellcard_bonus, timeout // 60, self.frame,
        )

    def _ecl_on_end_spellcard(self, st) -> None:
        if self.boss is not None and self._boss_ecl_state is st:
            self._apply_spellcard_end(self.boss.end_spellcard())

    def _ecl_on_spellcard_timeout(self, st) -> None:
        """ECL timer callback 触发的符卡超时: 捕获失败 + 清弹(无道具)。

        (PrepareSpellcardForTimerCallback 清 CAPTURE_VALID,
        EnemyManager.cpp:675-680; RemoveAllBullets(4) = despawn 无道具、
        激光不豁免, :626-627; boss 在场且自机 ALIVE → 自机无敌 70 帧,
        :630-633)"""
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
        self.lasers.remove_all(spawn_items=False, skip_flag4=False)
        if self.player.state == PlayerState.ALIVE:
            self.player.state = PlayerState.INVULNERABLE
            self.player.invuln = max(self.player.invuln, 70)

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
    def bombs_used_count(self) -> float:
        return self.globals.bombs_used

    @bombs_used_count.setter
    def bombs_used_count(self, v: float) -> None:
        self.globals.bombs_used = v

    # ---- 道具上下文 / 收集结算 ----
    def _item_ctx(self) -> GameContext:
        """给 ItemWorld 的游戏状态快照(pocY/吸附速度/半径取自 .sht)。"""
        g = self.globals
        return GameContext(
            power=g.current_power,
            lives=int(g.lives_remaining),
            bombs=int(g.bombs_remaining),
            focus=self.player.focus,
            shot_type=self.character,
            player_pos=self.player.pos,
            player_alive=self.player.alive,
            player_state=int(self.player.state),
            difficulty=self.difficulty,
            bombing=self.bomb.is_in_use,
            player_firing=self.player._firing,
            point_item_value=g.point_item_value,
            point_items_collected=g.point_items_collected,
            point_items_collected_this_stage=g.point_items_collected_this_stage,
            point_item_extends_so_far=g.point_item_extends_so_far,
            next_point_item_extend_threshold=g.next_point_item_extend_threshold,
            gauge_extremely_human=g.gauge_is_extremely_human(),
            time_orb_ready=(
                g.current_time_orbs >= g.last_spell_time_orb_threshold
            ),
            spellcard_active=self._spellcard_active(),
            poc_y=self.shot_data.poc_y,
            item_collect_speed=self.shot_data.item_collect_speed,
            item_collect_radius=self.shot_data.item_collect_radius,
        )

    def _apply_collect(self, r, pos: Vec2) -> None:
        """把道具收集结果应用到游戏状态(CollectResult → globals/弹字/音效)。"""
        g = self.globals
        power_before = self.power
        g.add_score(r.score * 10)
        self.power = min(FULL_POWER, self.power + r.delta_power)
        self.bombs = min(8, self.bombs + r.delta_bombs)
        self.lives += r.delta_lives
        if r.extends:
            self.lives += r.extends
            g.point_item_extends_so_far += r.extends
            g.next_point_item_extend_threshold = next_point_item_extend_threshold(
                g.point_item_extends_so_far, self.difficulty
            )
        if r.time_orbs:
            g.add_time_orbs(r.time_orbs)
        if r.gauge_delta:
            # AddToYoukaiGauge: 炸弹中不加 (GameManager.cpp:1312-1315 的
            # isInUse && !forceUpdate 门控)
            if not self.bomb.is_in_use:
                g.add_to_youkai_gauge(r.gauge_delta)
        if r.subrank > 0:
            g.increase_subrank(r.subrank)
        if r.point_items_collected:
            g.point_items_collected += r.point_items_collected
            g.point_items_collected_this_stage += r.point_items_collected
        if r.clear_bullets:
            # 满火力清弹 ClearBulletsForTransition = RemoveAllBullets(1)
            # (ItemManager.cpp:421/341 段 → Spellcard.cpp:883-886)
            assert self.ecl_host is not None
            self.ecl_host.clear_bullets_for_transition()
        if r.convert_power_items:
            # ConvertAllPowerItemsToTimeOrbs (ItemManager.cpp:429/576)
            self.items.convert_power_items_to_time_orbs()
        for value, color, kind in r.popups:
            g.add_popup(pos, value, color, kind)
        if r.delta_lives > 0 or r.extends:
            self.sounds.play(SE_1UP)
        if r.delta_power > 0 and _power_level(
            power_before, self.data.power_levels
        ) != _power_level(self.power, self.data.power_levels):
            self.sounds.play(SE_POWERUP)
        self.sounds.play(SE_ITEM)

    # ---- 读档 / 关卡切换 ----
    def _read_stage(self, stage_no: int) -> Stage:
        """读 stage*.std(条目带 "edz" 内层加密, crypt.py 解; 头布局与 th07
        同构 —— 名字 @0x10/曲名 @0x90/曲路径 @0x290, Background.hpp:17-31,
        头长同为 0x490=1168 字节, schema/stage.py 直接复用)。"""
        raw = try_decrypt_from_table(self.archive.load(STAGE_STD_FILES[stage_no - 1]))
        return Stage.read(raw, stage_no)

    def enter_stage(self, stage_no: int) -> None:
        self.stage_no = stage_no
        self.stage = self._read_stage(stage_no)
        log.debug(
            "进关: stage={} 「{}」 BGM={} (frame={})",
            stage_no, self.stage.title.strip(),
            next((n for n in self.stage.bgm_names if n), ""), self.frame,
        )
        self._load_ecl()

    # ---- 对话(GuiImpl::RunMsg 的每帧语义 + 世界门控) ----
    def _msg_active(self) -> bool:
        return self.msg_vm is not None and self.msg_vm.has_current_msg_idx()

    def _step_msg(self, *, advance: bool, skip: bool) -> None:
        """Gui::OnUpdate → RunMsg: 推进对话 VM; 对话中每帧道具转吸附
        (Gui.cpp:275-277, 自机非 DYING 时 AutoCollectAllItems)。
        时间轴停轴由 ecl_host.msg_wait 完成。"""
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
                # MSG_MUSIC: musicIdx 索引 stage.bgm_paths
                self.bgm_events.append(("music", int(ev[6:])))
            elif ev == "fadeout_music":
                self.bgm_events.append(("fadeout", 4.0))
            else:
                log.debug("msg event: {}", ev)
        if not vm.has_current_msg_idx():
            return
        if self.player.state != PlayerState.DEAD:
            self.items.remove_all_items()

    def _step_stage(self) -> None:
        """关卡波次编排: 有 ECL 数据走真实时间轴; 缺资源则空转(不生成)。"""
        if self.ecl_file is not None:
            self._step_ecl()

    # ---- 每帧 ----
    def tick(
        self,
        *,
        keys: tuple[bool, ...] | None = None,
        bomb: bool = False,
        advance: bool = False,
        skip: bool = False,
    ) -> None:
        """推进一帧(帧流水线骨架同 th07 world.py:748-1008, 删樱点/结界段,
        换时刻/妖率段)。keys = (left,right,up,down,focus[,shoot]);
        bomb=按炸弹键; advance=对话中 Z 新按下; skip=按住 Ctrl。"""
        if self.game_over:
            if self.result is None and not self.continue_available:
                log.debug(
                    "GameOver → 结算 (frame={}, score={})",
                    self.frame, self.globals.gui_score,
                )
                self.result = self.final_result(cleared=False)
            self._drain_frame_events()
            return
        if self.cleared or self.ending is not None:
            self._drain_frame_events()
            return
        if self._pending_next_level:
            self._pending_next_level = False
            self._advance_stage()
        self.frame += 1
        # 对话门控(Gui::IsDialoguePresent): 可移动, 不能射击/炸弹
        msg_active = self.msg_vm is not None and self.msg_vm.has_current_msg_idx()
        self.player.dialog_active = msg_active
        if keys:
            self.player.push(
                (1 if keys[1] else 0) - (1 if keys[0] else 0),
                (1 if keys[3] else 0) - (1 if keys[2] else 0),
                focus=keys[4],
                firing=(keys[5] if len(keys) > 5 else True) and not msg_active,
            )
        else:
            self.player._firing = not msg_active
        self.player.power = self.power
        g = self.globals

        # ---- 炸弹键 ----
        if bomb and not msg_active and not self.bomb.is_in_use:
            self._try_bomb()

        # ---- 炸弹每帧 ----
        was_bomb_in_use = self.bomb.is_in_use
        self.bomb.tick(self._bomb_ctx())
        if was_bomb_in_use and not self.bomb.is_in_use:
            log.debug("bomb 结束 (frame={}, 持续={}帧)", self.frame, self.bomb.timer)
        if was_bomb_in_use and self.bomb.invulnerable:
            self.player.state = PlayerState.INVULNERABLE
        if was_bomb_in_use:
            self._apply_bomb_boxes()
            for ev in self.bomb.events:
                if ev == EVENT_REMOVE_ALL_ITEMS:
                    self.items.remove_all_items()
            self.bomb.events.clear()

        # ---- 玩家步进 ----
        death_ctx = DeathContext(
            lives=int(g.lives_remaining),
            shot_type=self.character,
            bombs=int(g.bombs_remaining),
            time_orbs=g.current_time_orbs,
        )
        self.player.bomb_active = self.bomb.is_in_use
        self.player.step(death_ctx)
        # 决死窗倒数中每帧时刻符点 -15 (Player.cpp:1282-1284)
        if self.player.state == PlayerState.DEAD:
            g.add_time_orbs(-15)

        self._step_stage()
        self._step_msg(advance=advance, skip=skip)
        self.host.step(self.bullets, rng=self.rng)

        # 敌人体术判定(炸弹中跳过, 同 th07)
        if not self.bomb.is_in_use and self.host.contact_hits(self.player):
            self._death_pos = self.player.pos
            g.youkai_gauge = 0  # Die() → SetYoukaiGauge(0) (Player.cpp:534)
            self.lasers.clear()

        # 自机弹打敌人
        self.targeting.reset()
        results, kills = self.host.shoot_hits(
            self.player,
            self.targeting,
            is_focus=self.player.focus,
            is_sakuya=False,  # th07 索敌旋钮; th08 无咲夜A
            bomb_in_use=self.bomb.is_in_use,
            stage=self.stage_no,
            spellcard_active=self._spellcard_active(),
            used_bomb=bool(self.boss and self.boss.used_bomb),
            bomb_box_hit=self._bomb_box_hit,
        )
        for _, r in results:
            if r.score_code:
                g.add_score(r.score_code)
            if r.damage:
                self.sounds.play(SE_DAMAGE)
        counter = self.frame
        for e in kills:
            self._kill_reward(e, counter)
            counter += 1

        self._tick_boss()
        self.player.position_of_last_enemy_hit = (
            self.targeting.position_of_last_enemy_hit
        )

        # ---- 敌弹推进 + 擦弹/命中判定 ----
        self.bullets.player_pos = self.player.pos
        self.bullets.step()
        bsize = (self.bullets.bullet_radius * 2.0, self.bullets.bullet_radius * 2.0)
        h = self.ecl_host
        for b in self.bullets.alive():
            if b.spawn_state:
                continue
            # 铃仙冻结相位: 无判定 (collisionDisabled, EclExIns.cpp:622/687)
            if h is not None and h.bullet_collision_disabled(b):
                continue
            self.player.graze_bullet(b, bsize)
            if self.bomb.is_in_use:
                continue
            kr = self.player.check_killbox(b.pos, bsize)
            if kr == KillResult.DEATH:
                self._death_pos = self.player.pos
                g.youkai_gauge = 0  # Die() → SetYoukaiGauge(0)
                self.lasers.clear()
                break

        # ---- 道具推进 + 收集 ----
        ctx = self._item_ctx()
        for _ in self.items.step(ctx):
            g.decrease_subrank(OFFSCREEN_SUBRANK_PENALTY)  # 掉出道具 subrank -3
        for item in list(self.items.alive()):
            if self.items.collect_pickup(item, ctx):
                cr = self.items.collect(item, ctx)
                self._apply_collect(cr, item.pos)
                self.items.remove(item)
                # 同帧多个点道具过阈值的基线同步(同 th07 BUGS.md 增量#3)
                ctx.point_items_collected = g.point_items_collected
                ctx.point_item_extends_so_far = g.point_item_extends_so_far

        # ---- 激光推进 + 玩家碰撞 ----
        self.lasers.step()
        lhit, _ = self.lasers.check_player(self.player.pos, self.player.hitbox_radius)
        if lhit and not self.bomb.is_in_use:
            if self.player.state == PlayerState.ALIVE:
                self._death_pos = self.player.pos
                g.youkai_gauge = 0
                self.player.die()
            self.lasers.clear()

        # ---- 玩家事件消费 ----
        self._consume_player_events()

        # 得分弹字推进 + 显示分追赶 + 最高分跟随
        g.step_popups()
        g.tick_gui_score()
        g.tick_high_score()
        milestone = g.gui_score // 10_000_000
        if milestone > self._score_milestone:
            self._score_milestone = milestone
            log.debug(
                "score 突破 {}000万 (frame={}, stage={}, score={})",
                milestone, self.frame, self.stage_no, g.gui_score,
            )

        # 通关判定(尾王击破 + timeline 完; 正常路径由 msg NEXT_LEVEL 先行)
        if (
            self.result is None
            and self.ending is None
            and not self._pending_next_level
            and self._stage_cleared()
        ):
            log.debug("关卡 {} 通过 (frame={})", self.stage_no, self.frame)
            if self.stage_no < 8:
                self._pending_next_level = True
            else:
                # 8=6B(终面)/9=EX: 通关直接总结算(结局分支是单 B 的工作)
                self.cleared = True
                log.debug(
                    "通关 → 总结算 (frame={}, score={})",
                    self.frame, g.gui_score,
                )
                self.result = self.final_result(cleared=True)

        self._drain_frame_events()

    def _drain_frame_events(self) -> None:
        """把本帧累积的发声队列/BGM 事件/震屏事件拍成快照。"""
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

    def _stage_cleared(self) -> bool:
        """通关判定: ECL 时间轴全部跑完且 Boss 已退场(兜底路径;
        正常路径由 msg 的 NEXT_LEVEL 事件先行驱动)。"""
        return (
            self.ecl_file is not None
            and bool(self.ecl_timelines)
            and all(t.done for t in self.ecl_timelines)
            and self.boss is None
        )

    # ---- STAGERESULTS 过关结算 / NEXT_LEVEL 换关 (Gui.cpp RunMsg) ----
    def _on_stage_results(self) -> None:
        """msg STAGERESULTS 指令: 快照本关计数(精确的面板/奖励计算是
        单 B 的结算工作; 本期只留快照供 view/测试消费)。"""
        g = self.globals
        self.stage_results = {
            "stage": self.stage_no,
            "all_clear": self.stage_no >= 7,
            "snapshot": {
                "power": int(g.current_power),
                "point_items": g.point_items_collected_this_stage,
                "graze": g.graze_in_stage,
                "time_orbs": g.current_time_orbs,
                "lives": int(g.lives_remaining),
                "bombs": int(g.bombs_remaining),
            },
        }

    def _on_next_level(self) -> None:
        """msg NEXT_LEVEL 指令: 转场 → 次帧帧首换关/总结算。"""
        if (
            self._pending_next_level
            or self.result is not None
            or self.ending is not None
        ):
            return
        if self.stage_no < 8:
            self._pending_next_level = True
        else:
            self.cleared = True
            log.debug(
                "通关 → 总结算 (frame={}, score={})",
                self.frame, self.globals.gui_score,
            )
            self.result = self.final_result(cleared=True)

    def _next_stage_no(self) -> int:
        """AdvanceToNextStage (GameManager.cpp:1472-1525) 的映射。"""
        if self.stage_no == 3:
            return _STAGE4_BRANCH.get(self.character, 4)
        if self.stage_no == 6:
            # 5 面 → 6A/6B 按 finalStageRoute(msg 二选一写出, 默认 0=6A)
            route = self.msg_vm.final_stage_route if self.msg_vm else None
            return 8 if route else 7
        return _NEXT_STAGE_PLAIN.get(self.stage_no, self.stage_no + 1)

    def _advance_stage(self) -> None:
        """换关 (GameManager AddedCallback 公共路径; 重建清单照 th07)。

        时刻符点换关清零 (GameManager.cpp:878 currentTimeOrbs=0);
        过面时刻增量(达标?1:2, GetClockTimeIncrement :1379-1470)与
        ≥12 的 Bad Ending 判定 (:342-348)是单 B 的工作。
        """
        g = self.globals
        log.debug(
            "换关: stage {} → {} (frame={}, score={})",
            self.stage_no, self._next_stage_no(), self.frame, g.gui_score,
        )
        g.gui_score = g.score
        g.gui_score_difference = 0
        self.stage_results = None
        g.subrank = 0
        g.point_items_collected_this_stage = 0
        g.graze_in_stage = 0
        g.current_time_orbs = 0  # GameManager.cpp:878
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
        self._death_pos = None
        # 玩家/炸弹重建 → SPAWNING 出生点
        self.player = Th08Player(
            shot_data=self.shot_data,
            shot_data_focus=self.shot_data_focus,
            shot_type=self.character,
        )
        self.player.sound = self.sounds
        self._inject_player_rng()
        self.bullets.player_pos = self.player.pos
        self.bomb = Th08Bomb(shot_type=self.character)
        self.enter_stage(self._next_stage_no())

    # ---- 结局(终面通关; 时刻判定替换 th07 numRetries 是单 B 的工作) ----
    def _enter_ending(self) -> None:
        """(占位) th08 结局 = 时刻 ≥12 → Bad Ending (GameManager.cpp:342-348);
        资源/分支是单 B 工作, 本期直接总结算。"""
        self.globals.snap_gui_score()
        self.cleared = True
        self.result = self.final_result(cleared=True)

    def finish_ending(self) -> None:
        """结局看完 → 总结算(本期结局占位, 直接幂等收尾)。"""
        if self.ending is None:
            return
        self.ending = None
        self.cleared = True
        self.result = self.final_result(cleared=True)

    @property
    def continue_available(self) -> bool:
        """续关菜单是否可出现(简化: 无残机且非 Extra 且次数未尽)。"""
        return (
            self.game_over
            and self.result is None
            and self.difficulty < 4
            and self.globals.num_retries < self.max_retries
        )

    def finalize_game_over(self) -> None:
        """续关菜单选 No → 进结算。"""
        if self.game_over and self.result is None:
            self.result = self.final_result(cleared=False)

    def continue_play(self) -> None:
        """续关(retry 菜单 Yes): 当场复活接着玩; 重置清单照 th07 改编
        (樱点系换成时刻符点清零)。"""
        if not self.continue_available:
            return
        g = self.globals
        g.num_retries += 1
        g.gui_score = g.num_retries
        g.gui_score_difference = 0
        g.score = g.gui_score
        g.lives_remaining = float(self.initial_lives)
        g.bombs_remaining = self.shot_data.initial_bombs
        g.graze_in_stage = 0
        g.point_items_collected_this_stage = 0
        g.point_items_collected = 0
        g.current_power = 0.0
        g.point_item_extends_so_far = 0
        g.next_point_item_extend_threshold = next_point_item_extend_threshold(
            0, self.difficulty
        )
        g.current_time_orbs = 0
        g.youkai_gauge = 0
        self._score_milestone = 0
        self.game_over = False
        self.result = None
        self._result_cache = None

    # ---- 炸弹 ----
    def _bomb_ctx(self) -> BombContext:
        return BombContext(
            player_pos=self.player.pos,
            difficulty=self.difficulty,
            rng_float=self.rng.unit,
        )

    def _try_bomb(self) -> None:
        """炸弹触发; 成功后把透出事件接回 globals/boss。
        决死B (deathbomb): 中弹后的决死窗(respawnTimer 倒数, 初值 = sht
        deathbombWindowFrames) 内按 B → 消耗一枚 bomb 代替丢残机;
        DEAD→INVULNERABLE 翻转照 th07 world.py:1377-1385。"""
        g = self.globals
        deathbomb = self.player.state == PlayerState.DEAD
        res = try_start_bomb(
            self.bomb,
            self._bomb_ctx(),
            focus=self.player.focus,
            bombs_remaining=g.bombs_remaining,
            respawn_timer=self.player.respawn_timer,
            initial_respawn_timer=self.shot_data.initial_respawn_timer,
            border_invulnerability_time=0,  # th08 无结界
            bomb_pressed=True,
            spellcard_active=bool(self.boss and self.boss.is_active),
        )
        if not res.started:
            return
        if deathbomb:
            # 决死变体记账(callback_variant 2/3, bomb.start 已按普通跑;
            # 变体的行为差是逐机体移植(单 B+)的工作)
            self.bomb.callback_variant += 2
        log.debug(
            "bomb 触发{} (frame={}, character={}, focus={}, 剩余炸弹={})",
            "(决死)" if deathbomb else "",
            self.frame, self.character, self.player.focus, g.bombs_remaining - 1,
        )
        self.sounds.play(BOMB_SE)
        g.bombs_used += res.bombs_used_delta
        g.bombs_remaining += res.bombs_remaining_delta
        g.decrease_subrank(-res.subrank_delta)
        self.player.respawn_timer = res.respawn_timer
        if res.spellcard_capture_reset and self.boss:
            self.boss.mark_bombed()  # 用弹 → 本张符卡不算捕获
        self.player.invuln = max(self.player.invuln, self.bomb.invulnerability_timer)
        if deathbomb:
            # 决死B 成立: 本帧起死亡倒计时即停, 残机不扣
            self.player.state = PlayerState.INVULNERABLE
            log.debug("决死B 成立 (frame={}, character={})", self.frame, self.character)

    def _bomb_box_hit(self, pos: Vec2, full_size: tuple[float, float]) -> bool:
        return self.bomb.hits(pos, Vec2(full_size[0] / 2, full_size[1] / 2))

    def _spawn_point_star(self, pos: Vec2) -> None:
        """弹消星道具, 出生即吸附 (RemoveAllBullets 的 SpawnItem(…, 1))。"""
        self.items.spawn(
            pos, ItemType.POINT_STAR, power=self.power, state=STATE_ATTRACT
        )

    def _apply_bomb_boxes(self) -> None:
        """炸弹盒生效: 清弹盒→弹转弹消星(CheckBulletCancelCollision 等价),
        伤害盒→敌人/Boss(分路径结算, 偏差注记同 th07 world._apply_bomb_boxes)。"""
        g = self.globals
        bsize = Vec2(self.bullets.bullet_radius * 2.0, self.bullets.bullet_radius * 2.0)
        for b in self.bullets.alive():
            if b.spawn_state:
                continue
            if self.bomb.check_bomb_graze(b.pos, bsize):
                self.items.spawn(
                    b.pos,
                    ItemType(self.bomb.item_type),
                    power=self.power,
                    state=STATE_ATTRACT,
                )
                b.dead = True
        for e in self.host.alive():
            if not (e.can_die and e.is_hittable):
                continue
            dmg = self.bomb.damage_to(e.pos, Vec2(e.radius, e.radius))
            if dmg:
                r = settle_damage(
                    int(dmg),
                    is_boss=e.is_boss,
                    is_focus=self.player.focus,
                    bomb_in_use=True,
                    bomb_damage=True,
                    stage=self.stage_no,
                    spellcard_active=self._spellcard_active(),
                    used_bomb=bool(self.boss and self.boss.used_bomb),
                    invincibility_timer=e.invincibility_timer,
                    enemy_timer=e._tick,
                    can_be_damaged=e.can_be_damaged,
                )
                e.life -= r.damage
                g.add_score(r.score_code)
                if r.damage:
                    self.sounds.play(SE_DAMAGE)
                if e.life <= 0 and e.kill():
                    self._kill_reward(e, self.frame)

    # ---- Boss / 符卡 ----
    def _spellcard_active(self) -> bool:
        return bool(self.boss and self.boss.is_active and self.boss.spellcard_idx >= 0)

    # ---- GameEngine 协议的公开访问器 ----
    def spellcard_active(self) -> bool:
        return self._spellcard_active()

    def msg_active(self) -> bool:
        return self._msg_active()

    def _tick_boss(self) -> None:
        """ECL 驱动的 Boss: 只同步状态 + 捕获分衰减; 伤害走 shoot_hits。"""
        if self.boss is None:
            return
        if self._boss_ecl_state is None:
            return  # th08 无演示 Boss 路径(无 ECL 数据即空转)
        boss = self.boss
        st = self._boss_ecl_state
        boss.pos = Vec2(st.pos.x, st.pos.y)
        # 血条每帧取 bossId==0 敌人的 life/maxLife (同 th07 注记)
        bar_st = st
        if st.boss_id != 0 and self.ecl_world is not None:
            main = self.ecl_world.bosses[0]
            if main is not None and main.is_boss:
                bar_st = main
        boss.life = max(bar_st.life, 0)
        boss.max_life = max(bar_st.max_life, 1)
        boss.invincibility_timer = st.invincibility_timer
        boss.is_survival_spellcard = bool(st.is_survival_spellcard)
        boss.tick()
        e = self._boss_ecl_enemy
        if e is None or not e.alive:
            if boss.is_active:
                self._apply_spellcard_end(boss.end_spellcard())
            self.boss = None
            self._boss_ecl_state = None
            self._boss_ecl_enemy = None

    def _apply_spellcard_end(self, res: dict) -> None:
        """EndSpellcard 透出事件入账 (Spellcard::EndSpell, Spellcard.cpp:999-):
        非超时 → DespawnBullets(8000,1)+KillAllNonBossEnemies 清弹清敌累计分;
        捕获 → 得分/计数/横幅(捕获的 pendingTimeOrbs 时刻符点奖励是单 B)。"""
        if not res["ended"]:
            return
        self.sounds.play(SE_SPELLCARD_END)
        name = self.boss.name if self.boss is not None else "?"
        if res["captured"]:
            log.debug(
                "符卡捕获: {} (frame={}, 分数={})",
                name, self.frame, res["score"] // 10,
            )
        elif res["timed_out"]:
            log.debug("符卡超时: {} (frame={})", name, self.frame)
        else:
            log.debug("符卡未捕获: {} (frame={})", name, self.frame)
        if res["captured"]:
            self.globals.add_score(res["score"])
            self.globals.spell_cards_captured += res["spell_cards_captured"]
            if self.ecl_host is not None:
                self.ecl_host.spellcards_captured = (
                    self.globals.spell_cards_captured
                )  # ex24 发布值
            self.globals.show_spellcard_bonus(res["score"])
        self._catk_idx = None
        if res["despawn_bullets"]:
            removed = self._despawn_bullets_bonus()
            if res["remove_all_enemies"]:
                if self.ecl_host is not None:
                    removed = self.ecl_host.remove_all_enemies(8000, removed)
                else:
                    self.host.clear()
            if removed:
                self.globals.add_score(removed)
                self.globals.show_bonus_score(removed)
        elif res["remove_all_enemies"]:
            if self.ecl_host is not None:
                self.globals.add_score(self.ecl_host.remove_all_enemies(8000, 0))
            else:
                self.host.clear()

    def _despawn_bullets_bonus(self) -> int:
        """BulletManager::DespawnBullets(8000,1) (BulletManager.cpp:565-660):
        弹转弹消星(吸附) + 逐弹弹字(2000 起 +20, 8000 封顶, 黄=满分)
        + 累计清弹分(代码值, 激光不计分); 连带激光无豁免, 原点+沿线出星。
        末尾 spawnSuppressionFrames=10 (:656)。返回清弹分(无弹=0)。"""
        total = 0
        value = 2000
        for b in self.bullets.alive():
            self.items.spawn(
                b.pos, ItemType.POINT_STAR, power=self.power, state=STATE_ATTRACT
            )
            self.globals.add_popup(
                b.pos, value, 0xFFFFFF00 if value >= 8000 else 0xFFFFFFFF, kind=1
            )
            total += value
            value = min(value + 20, 8000)
            b.dead = True
        self.lasers.remove_all(
            spawn_items=True,
            skip_flag4=False,
            spawn_at_pos=True,
            spawn_item=self._spawn_point_star,
        )
        self.bullets.screen_clear_time = 10
        return total

    def _clear_field(self) -> None:
        if self.ecl_host is not None:
            self.ecl_host.remove_all_enemies(8000, 0)
        else:
            self.host.clear()
        self.bullets.clear()
        self.lasers.remove_all(spawn_items=False, skip_flag4=True)

    def _kill_reward(self, e, counter: int) -> None:
        """击杀入账: 得分 + 掉落 + 击坠音 (EnemyManager 死亡分支 +
        Enemy::DropItems(0), EnemyManager.cpp:743-800)。

        使魔链死亡的时刻符点掉星 (EnemyManager.cpp:270-350 段) 是
        后续阶段(单 B 妖率计/使魔全联动)的工作, 本期只掉 DropItems 部分。
        """
        self.sounds.play(SE_ENEMY_DEAD_A + counter % 2)  # 两档音量交替
        g = self.globals
        if isinstance(e, EclEnemy):
            st = e.state
            if not e._kill_no_score:  # 仅 death_type==2 无 AddScore (同 th07)
                g.add_score(st.score)
            d = st.item_drop
            if 0 <= d:
                self.items.spawn(e.pos, d, power=self.power)
            elif d == -1:
                # C: enemyDropCounter%3==0 才掉, 表索引独立递增 (:756-772)
                if self._rand_spawn_idx % 3 == 0:
                    self.items.drop_random(
                        e.pos,
                        table=self._drop_table,
                        counter=self._rand_table_idx,
                        power=self.power,
                    )
                    self._rand_table_idx = (self._rand_table_idx + 1) % 32
                self._rand_spawn_idx += 1
            # op144 的掉落数 (DropItems :773-800): 火力或点/纯点
            for _ in range(st.power_or_point_item_drop_count):
                pos = Vec2(
                    e.pos.x + self.rng.unit() * 128.0 - 64.0,
                    e.pos.y + self.rng.unit() * 128.0 - 64.0,
                )
                self.items.spawn(
                    pos,
                    ItemType.POWER_SMALL
                    if self.power < FULL_POWER
                    else ItemType.POINT,
                    power=self.power,
                )
            for _ in range(st.point_item_drop_count):
                pos = Vec2(
                    e.pos.x + self.rng.unit() * 128.0 - 64.0,
                    e.pos.y + self.rng.unit() * 128.0 - 64.0,
                )
                self.items.spawn(pos, ItemType.POINT, power=self.power)
            if st.is_boss and not self._spellcard_active():
                # boss 击坠且非符卡中: 清弹转道具 + 清场 + 累计分入账
                removed = self._despawn_bullets_bonus()
                if self.ecl_host is not None:
                    removed = self.ecl_host.remove_all_enemies(8000, removed)
                if removed:
                    g.add_score(removed)
                    g.show_bonus_score(removed)
            return
        g.add_score(5000)
        self.items.drop_random(
            e.pos, table=self._drop_table, counter=counter, power=self.power
        )

    # ---- 玩家事件消费 ----
    def _consume_player_events(self) -> None:
        g = self.globals
        for ev in self.player.take_events():
            k = ev.kind
            if k == PlayerEventKind.DEATH_SETTLE:
                assert isinstance(ev.data, DeathSettle)
                pos = self._death_pos or self.player.pos
                log.debug(
                    "玩家死亡 (frame={}, pos=({:.1f},{:.1f}), power {}→{}, 残机={})",
                    self.frame, pos.x, pos.y,
                    int(g.current_power), int(ev.data.new_power), g.lives_remaining,
                )
                self._apply_death_settle(ev.data)
            elif k == PlayerEventKind.RESPAWNED:
                # C++ 残机在重生时扣 (UpdateDeathAndRespawn else 分支 AddLives(-1))
                if g.lives_remaining > 0:
                    g.lives_remaining -= 1
                    g.bombs_remaining = self.shot_data.initial_bombs
                    log.debug(
                        "重生 (frame={}, 剩余残机={})", self.frame, g.lives_remaining
                    )
                else:
                    log.debug("无残机 → GameOver (frame={})", self.frame)
                    self.game_over = True
            elif k == PlayerEventKind.GRAZE:
                g.graze_in_stage = min(GRAZE_STAGE_CAP, g.graze_in_stage + 1)
                g.graze_in_total = min(GRAZE_TOTAL_CAP, g.graze_in_total + 1)
                # 擦弹分: 中度妖 4000 否则 2000 (Player.cpp:482-485 段)
                g.add_score(
                    (GRAZE_SCORE_MODERATE_YOUKAI
                     if g.gauge_is_moderately_youkai()
                     else GRAZE_SCORE_NORMAL) * 10
                )
                g.increase_subrank(GRAZE_SUBRANK)
                if self.player.is_youkai:
                    g.add_to_youkai_gauge(GRAZE_GAUGE_YOUKAI)  # Player.cpp:483-484
                # 极限妖且有 boss: 擦弹出时刻符点 (Player.cpp:486-495)
                if (
                    g.gauge_is_extremely_youkai()
                    and self.boss is not None
                    and not self.bomb.is_in_use
                ):
                    self.items.spawn(
                        self.player.pos, ItemType.TIME_APEX_REQUEST, power=self.power
                    )
                    if self._spellcard_active():
                        self.items.spawn(
                            self.player.pos,
                            ItemType.TIME_APEX_REQUEST,
                            power=self.power,
                        )
            elif k == PlayerEventKind.REMOVE_ALL_BULLETS:
                self.bullets.clear()  # 重生后 60 帧清弹期(bulletGracePeriod)
                self.lasers.remove_all(spawn_items=False, skip_flag4=True)

    def _apply_death_settle(self, s: DeathSettle) -> None:
        """死亡结算 (UpdateDeathAndRespawn 决死窗耗尽分支, Player.cpp:1286-1353)。"""
        g = self.globals
        g.current_power = s.new_power
        g.deaths += 1
        pos = self._death_pos or self.player.pos
        for _ in range(s.drop_power_big):
            self.items.spawn(pos, ItemType.POWER_BIG, power=s.new_power)
        for _ in range(s.drop_power_small):
            self.items.spawn(pos, ItemType.POWER_SMALL, power=s.new_power)
        for _ in range(s.drop_full_power):
            self.items.spawn(pos, ItemType.FULL_POWER, power=s.new_power)
        for _ in range(s.drop_bomb):
            self.items.spawn(pos, ItemType.BOMB, power=s.new_power)
        if s.time_orb_penalty:
            g.add_time_orbs(-s.time_orb_penalty)
        if s.activate_all_items:
            self.items.activate_all_items()
        if s.subrank_delta:
            g.decrease_subrank(-s.subrank_delta)
        if self.boss:
            self.boss.mark_death()  # 死亡 → 捕获失败 (Spellcard.InvalidateCapture)

    # ---- 擦弹与结算 ----
    def tally_spellcard(self) -> None:
        """记录捕获一张符卡(旧手动接口兼容)。"""
        if self.boss and self.boss.is_capturing:
            self.boss.mark_death()
        self.globals.spell_cards_captured += 1

    def final_result(
        self,
        *,
        cleared: bool = False,
        slow_percent: float = 0.0,
        name: str | None = None,
    ) -> dict:
        """结算: 汇总 globals → 入榜 + 写 store(内存)。

        幂等: 一局只结算一次。th08 的评级/结局差分是单 B 的工作。
        """
        if self._result_cache is not None:
            return self._result_cache
        if name is None:
            name = self.store.last_name
        g = self.globals
        g.snap_gui_score()
        rec = make_highscore_record(
            g.score,
            self.character,
            self.difficulty,
            self.stage_no,
            name=name,
            num_retries=g.num_retries,
        )
        pos = self.store.insert_score(rec)
        if cleared:
            self.store.record_clear(
                self.character, self.difficulty, self.stage_no, g.num_retries
            )
        self.store.record_run_end(
            self.character,
            self.difficulty,
            score=g.score,
            frames=self.frame,
            cleared=cleared,
            num_retries=g.num_retries,
        )
        self._result_cache = {
            "score": g.score,
            "rank": pos,
            "cleared": cleared,
            "difficulty": self.difficulty,
            "character": self.character,
            "stage": self.stage_no,
            "name": name,
            "retries": g.num_retries,
            "deaths": int(g.deaths),
            "bombs": g.bombs_used,
            "spellcards": g.spell_cards_captured,
            "graze": g.graze_in_total,
            "point_items": g.point_items_collected,
            "time_orbs": g.total_time_orbs,
            "clock": self.ecl_host.clock.units if self.ecl_host else 0,
            "high_score": self.store.high_score(self.difficulty, self.character),
        }
        return self._result_cache
