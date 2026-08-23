"""对外公共 API —— 给外部程序(脚本/AI/自定义渲染)用的 Pythonic 门面。

本模块只面向 GameEngine 协议(touhou/types.py)编程: ``Game._impl`` 锚定为
协议类型, 对局实现经 registry 按作品名解析(默认 th07); 作品的专属探测
(符卡/对话进行中)走协议的可选能力位(getattr 回落 False), 不认具体类。
作品实现(games/thXX/ 的对局主逻辑及各 engine 模块)保持 C 移植风格并经
全量测试验证, 本模块只做封装, 不改引擎行为:

- 枚举: ShotType / Difficulty / GamePhase —— 替代内部 shotType/difficulty 整数
- Input: 命名布尔字段的一帧输入, 替代 tick 的 keys 元组
- Game: 开局/逐帧 step/只读状态属性/按需实体快照
- GameEvent: 每帧事件流(符卡/死亡/Bomb/奖残/过关…), 由帧前后状态差映射

架构铁律: 本层(apis)与 engine 一样不 import games.* —— 窗口 App 等作品
组件一律经 registry 按作品名解析(register_app; AST 守护测试钉死)。
"""
from __future__ import annotations

import msgspec
from enum import Enum, IntEnum
from pathlib import Path
from typing import Callable, Generic, Iterator, Literal, TypeVar, overload

from ..engine.lasers import LaserState
from ..engine.replay import ReplayRecorder, make_meta, new_replay_name
from ..registry import GameSpec, get_game
from ..types import GameEngine, InputSource, KeysTuple, PathLike


# ---- 枚举 ----
# ShotType/Difficulty 是"已知枚举"= th07 的 6 机体/6 难度(语义与原作一致的作品
# 可直接复用)。Game/TouhouWorld 的 character/difficulty 参数接受裸 int, 不限定
# 必须用这两个枚举; 新作品可在自己的 games/thXX/ 定义新枚举, 经 game= 参数
# 传入使用, 不需要改动本模块。
class ShotType(IntEnum):
    """机体(= 内部 shotType 0..5; 名单为 th07 的 6 机体)。"""
    REIMU_A = 0
    REIMU_B = 1
    MARISA_A = 2
    MARISA_B = 3
    SAKUYA_A = 4
    SAKUYA_B = 5


#: ``Character`` 是 ``ShotType`` 的别名(用户入口习惯叫法)。
Character = ShotType


class Difficulty(IntEnum):
    """难度(= 内部 difficulty 0..5; Extra/Phantasm 对应 7/8 面; th07 的 6 难度)。"""
    EASY = 0
    NORMAL = 1
    HARD = 2
    LUNATIC = 3
    EXTRA = 4
    PHANTASM = 5


class GamePhase(Enum):
    """对局所处阶段(由内部状态推导)。"""
    RUNNING = "running"            # 正常游玩中
    DIALOG = "dialog"              # 对话中(可移动, 不能射击/Bomb)
    STAGE_CLEAR = "stage_clear"    # 过关结算面板显示中
    ENDING = "ending"              # 结局画面显示中
    GAME_OVER = "game_over"        # 无残机(续关菜单/冻结)
    RESULT = "result"              # 总结算已出(result 可读)


class GameEventKind(Enum):
    """逐帧事件类别。"""
    SPELLCARD_BEGIN = "spellcard_begin"          # name=符卡名
    SPELLCARD_CAPTURED = "spellcard_captured"    # name=符卡名
    SPELLCARD_END = "spellcard_end"              # 未捕获结束(超时/击破失败); name=符卡名
    PLAYER_DEATH = "player_death"
    BOMB_START = "bomb_start"
    EXTEND = "extend"                            # 奖残(残机增加)
    BORDER_START = "border_start"                # 森罗结界激活
    BORDER_BREAK = "border_break"                # 结界破裂
    STAGE_CLEAR = "stage_clear"                  # stage=刚通过的关号
    GAME_OVER = "game_over"
    GAME_CLEAR = "game_clear"                    # 通关(总结算)
    ENDING_START = "ending_start"                # 6 面通关进结局


class GameEvent(msgspec.Struct, frozen=True):
    """一帧内发生的事件。name/stage 仅在相关类别时有值。"""
    kind: GameEventKind
    frame: int
    name: str | None = None
    stage: int | None = None


# ---- 输入 ----
class Input(msgspec.Struct, frozen=True):
    """一帧的按键输入(全部为"按住"语义; advance 建议点按脉冲)。

    对话推进用 advance(= 对话中 Z 新按下), 快进用 skip(= 按住 Ctrl)。
    """
    left: bool = False
    right: bool = False
    up: bool = False
    down: bool = False
    focus: bool = False
    shoot: bool = False
    bomb: bool = False
    advance: bool = False
    skip: bool = False

    @classmethod
    def none(cls) -> Input:
        """全空输入(不射击/不移动)。"""
        return cls()

    def _keys(self) -> KeysTuple:
        """转成内部 tick 的 keys 元组 (left, right, up, down, focus, shoot)。"""
        return (self.left, self.right, self.up, self.down, self.focus, self.shoot)


# ---- 实体快照(不可变; 供外部渲染/AI 观测用) ----
class PlayerSnapshot(msgspec.Struct, frozen=True):
    x: float
    y: float
    state: str            # alive/spawning/dead/invulnerable/border
    focus: bool
    invulnerable: bool


class BulletSnapshot(msgspec.Struct, frozen=True):
    x: float
    y: float
    angle: float
    speed: float
    sprite: int           # 弹型模板号(0..10)


class EnemySnapshot(msgspec.Struct, frozen=True):
    x: float
    y: float
    life: int
    radius: float
    is_boss: bool


class ItemSnapshot(msgspec.Struct, frozen=True):
    x: float
    y: float
    type: str             # ItemType 名, 如 "POWER_SMALL"/"POINT"/"LIFE"


class LaserSnapshot(msgspec.Struct, frozen=True):
    x: float
    y: float
    angle: float
    width: float
    active: bool          # 全宽命中态(SPAWNING/DESPAWNING 为 False)


class BossSnapshot(msgspec.Struct, frozen=True):
    name: str
    x: float
    y: float
    life: float
    max_life: float
    spellcard_active: bool


class Snapshot(msgspec.Struct, frozen=True):
    """某一帧的实体全景(由 Game.snapshot() 按需构造)。"""
    frame: int
    phase: GamePhase
    player: PlayerSnapshot
    boss: BossSnapshot | None
    bullets: tuple[BulletSnapshot, ...] = ()
    enemies: tuple[EnemySnapshot, ...] = ()
    items: tuple[ItemSnapshot, ...] = ()
    lasers: tuple[LaserSnapshot, ...] = ()


# ---- 对局门面 ----
def _require_world(game: str) -> GameSpec:
    """经注册表解析作品, 并要求其对局实现已注册(供 Game/TouhouWorld 构造)。"""
    spec = get_game(game)
    if spec.world is None:
        raise ValueError(
            f"作品 {game!r} 已注册, 但缺对局实现"
            f"(需要 @register_world_impl({game!r}) 装饰主逻辑类)")
    return spec


class Game:
    """一局作品对局(默认 th07, ``game=`` 指定其他已注册作品)。典型用法::

        game = Game(character=ShotType.REIMU_A, difficulty=Difficulty.NORMAL)
        while game.phase == GamePhase.RUNNING:
            events = game.step(Input(shoot=True))
            if game.frame % 60 == 0:
                snap = game.snapshot()   # 按需, 每帧构造有开销
    """

    def __init__(self, character: ShotType = ShotType.REIMU_A,
                 difficulty: Difficulty = Difficulty.NORMAL,
                 stage: int = 1, *,
                 game: str = "th07",
                 data_path: PathLike | None = None,
                 seed: int | None = None,
                 lives: int | None = None,
                 score_path: PathLike | None = None) -> None:
        # 经注册表解析作品(未注册名报带已注册列表的 KeyError);
        # 对局实现(register_world_impl 登记)必须有, 否则无法构造对局
        self.spec = _require_world(game)
        self.game_name = game
        # difficulty>=4(Extra/Phantasm) 固定 2 残, lives 仅 <4 生效
        kwargs: dict = {}
        if lives is not None:
            kwargs["initial_lives"] = lives
        if self.spec.data is not None:
            kwargs["data"] = self.spec.data  # 数值表(register_game_data 登记)
        world = self.spec.world
        assert world is not None  # _require_world 已校验
        # 注册表取回的构造器返回 Any; 门面只面向 GameEngine 协议编程
        # (touhou/types.py), th07 的 PerfectCherryBloom 鸭子满足该协议
        self._impl: GameEngine = world(
            data_path=data_path, character=int(character),
            difficulty=int(difficulty), seed=seed, score_path=score_path,
            hooks=self.spec.hooks, **kwargs)
        if stage != 1:
            self._impl.enter_stage(stage)
        self._prev = self._probe()  # 事件差的基准帧状态(每帧 step 后更新)

    @classmethod
    def _from_impl(cls, impl: GameEngine, spec: GameSpec, game_name: str
                   ) -> "Game":
        """包一个现存对局 impl 为 Game 门面(不重新构造对局)。

        观战模式用: 窗口 App 把局内 live 对局包出门面喂 policy(输入策略拿到的
        与 ``Game(...)`` 自建门面是同一观测面)。基准帧状态 ``_prev`` 按现况
        初始化(事件差从下一次 step 起算), 与 ``__init__`` 的基准帧逻辑一致。
        """
        obj = cls.__new__(cls)
        obj.spec = spec
        obj.game_name = game_name
        obj._impl = impl
        obj._prev = obj._probe()
        return obj

    # ---- 逐帧推进 ----
    def step(self, input: Input = Input.none()) -> list[GameEvent]:
        """推进一帧, 返回自上次 step 以来发生的事件列表。"""
        prev = self._prev
        self._impl.tick(keys=input._keys(), bomb=input.bomb,
                        advance=input.advance, skip=input.skip)
        now = self._probe()
        self._prev = now
        return self._diff_events(prev, now)

    # ---- 可选能力位(GameEngine 协议外的作品专属探测, getattr 回落) ----
    def _spellcard_active(self) -> bool:
        """符卡进行中(引擎实现 spellcard_active() 才有, 否则恒 False)。"""
        probe = getattr(self._impl, "spellcard_active", None)
        return bool(probe()) if callable(probe) else False

    def _msg_active(self) -> bool:
        """对话/剧情进行中(引擎实现 msg_active() 才有, 否则恒 False)。"""
        probe = getattr(self._impl, "msg_active", None)
        return bool(probe()) if callable(probe) else False

    def _probe(self) -> dict:
        g = self._impl
        boss = g.boss
        spell_key = None
        # spellcard_active() 蕴含 boss 非空(boss.is_active 且 spellcard_idx>=0)
        if boss is not None and self._spellcard_active():
            spell_key = (boss.spellcard_idx, boss.name)
            
        border = getattr(g, "border", None)
        
        return {
            "lives": g.lives,
            "deaths": g.globals.deaths,
            "bombs_used": g.globals.bombs_used,
            "captured": g.globals.spell_cards_captured,
            "spell_key": spell_key,
            "game_over": g.game_over,
            "cleared": g.cleared,
            "ending": g.ending is not None,
            "stage": g.stage_no,
            "border_active": bool(getattr(border, "active", False)),
        }

    def _diff_events(self, prev: dict, now: dict) -> list[GameEvent]:
        g = self._impl
        out: list[GameEvent] = []

        def emit(kind: GameEventKind, **kw) -> None:
            out.append(GameEvent(kind, frame=g.frame, **kw))

        if now["spell_key"] != prev["spell_key"]:
            if prev["spell_key"] is not None:
                if now["captured"] > prev["captured"]:
                    emit(GameEventKind.SPELLCARD_CAPTURED, name=prev["spell_key"][1])
                else:
                    emit(GameEventKind.SPELLCARD_END, name=prev["spell_key"][1])
            if now["spell_key"] is not None:
                emit(GameEventKind.SPELLCARD_BEGIN, name=now["spell_key"][1])
        if now["deaths"] > prev["deaths"]:
            emit(GameEventKind.PLAYER_DEATH)
        if now["bombs_used"] > prev["bombs_used"]:
            emit(GameEventKind.BOMB_START)
        if now["lives"] > prev["lives"]:
            emit(GameEventKind.EXTEND)
        if now["border_active"] and not prev["border_active"]:
            emit(GameEventKind.BORDER_START)
        elif prev["border_active"] and not now["border_active"]:
            emit(GameEventKind.BORDER_BREAK)
        if now["stage"] != prev["stage"]:
            emit(GameEventKind.STAGE_CLEAR, stage=prev["stage"])
        if now["ending"] and not prev["ending"]:
            emit(GameEventKind.ENDING_START)
        if now["game_over"] and not prev["game_over"]:
            emit(GameEventKind.GAME_OVER)
        if now["cleared"] and not prev["cleared"]:
            emit(GameEventKind.GAME_CLEAR)
        return out

    # ---- 只读状态 ----
    @property
    def frame(self) -> int:
        return self._impl.frame

    @property
    def score(self) -> int:
        return self._impl.globals.score

    @property
    def lives(self) -> int:
        return int(self._impl.lives)

    @property
    def bombs(self) -> int:
        return int(self._impl.bombs)

    @property
    def power(self) -> int:
        return int(self._impl.power)

    @property
    def cherry(self) -> int:
        # 樱点是 th07 专属能力位(非 GameEngine 必选协议): 无樱点系统的作品得 0
        return getattr(self._impl, "cherry", 0)

    @property
    def cherry_max(self) -> int:
        # 与 cherry 同理: th07 专属能力位, 无樱点系统的作品得 0
        return int(getattr(self._impl.globals, "cherry_max", 0))

    @property
    def graze(self) -> int:
        return self._impl.globals.graze_in_total

    @property
    def stage(self) -> int:
        return self._impl.stage_no

    @property
    def phase(self) -> GamePhase:
        g = self._impl
        if g.result is not None:
            return GamePhase.RESULT
        if g.game_over:
            return GamePhase.GAME_OVER
        if g.ending is not None:
            return GamePhase.ENDING
        if g.stage_results is not None:
            return GamePhase.STAGE_CLEAR
        if self._msg_active():
            return GamePhase.DIALOG
        return GamePhase.RUNNING

    @property
    def result(self) -> dict | None:
        """总结算数据(结算后非 None; 字段由作品实现定义)。"""
        return self._impl.result

    def finalize_game_over(self) -> None:
        """GameOver 后不续关直接进结算(= 续关菜单选 No)。"""
        self._impl.finalize_game_over()

    def finish_ending(self) -> None:
        """结局看完 → 总结算(窗口版由确认键触发, headless 由 stream 自动调)。"""
        self._impl.finish_ending()

    # ---- 实体快照 ----
    def snapshot(self) -> Snapshot:
        """当前帧实体快照(不可变 msgspec.Struct)。

        每帧都调用有构造开销(全场实体逐个装箱), 建议按需调用
        (如每 N 帧一次), 热循环里直接读 score/lives 等标量属性。
        """
        g = self._impl
        p = g.player
        player = PlayerSnapshot(
            x=p.pos.x, y=p.pos.y, state=p.state.name.lower(),
            focus=p.focus, invulnerable=p.invulnerability_timer > 0)
        boss = None
        if g.boss is not None:
            b = g.boss
            boss = BossSnapshot(
                name=b.name, x=b.pos.x, y=b.pos.y, life=b.life,
                max_life=b.max_life, spellcard_active=self._spellcard_active())
        return Snapshot(
            frame=g.frame, phase=self.phase, player=player, boss=boss,
            bullets=tuple(BulletSnapshot(
                x=b.pos.x, y=b.pos.y, angle=b.angle, speed=b.speed,
                sprite=b.sprite) for b in g.bullets.alive()),
            enemies=tuple(EnemySnapshot(
                x=e.pos.x, y=e.pos.y, life=int(e.life), radius=e.radius,
                is_boss=bool(e.is_boss)) for e in g.host.alive()),
            items=tuple(ItemSnapshot(
                x=i.pos.x, y=i.pos.y, type=i.type.name)
                for i in g.items.alive()),
            lasers=tuple(LaserSnapshot(
                x=l.pos.x, y=l.pos.y, angle=l.angle, width=l.width,
                active=l.state == LaserState.ACTIVE)
                for l in g.lasers.lasers if l.in_use),
        )


# ---- 资源包 + 世界入口(用户级 API) ----
class WorldData(msgspec.Struct, frozen=True):
    """游戏资源包路径。

    res_dat: 主资源包(th07 的例子: th07.dat; None = 按 paths.resolve_data_path
            规则解析: 环境变量 TOUHOU_DAT > 内置默认路径)
    bgm_dat: WAV 高音质 BGM 包(th07 的例子: thbgm.dat; None = 与 res_dat
            同目录推导, 缺失时自动回退 MIDI 音源)
    """
    res_dat: PathLike | None = None
    bgm_dat: PathLike | None = None

    def resolve_res(self) -> Path | None:
        """解析后的主资源包路径(None = 交给内置默认解析)。"""
        return Path(self.res_dat) if self.res_dat is not None else None

    def resolve_bgm(self) -> Path | None:
        """解析后的 BGM 包路径(None = res_dat 同目录推导/自动回退)。"""
        return Path(self.bgm_dat) if self.bgm_dat is not None else None


_H = TypeVar("_H", bound=bool)  # TouhouWorld 的 headless 字面量参数(类型级)


class TouhouWorldEventStream:
    """headless 世界的事件流——``TouhouWorld.run()``(headless=True) 的返回值。

    迭代即驱动: 每取一个事件, 世界按 ``policy``(缺省为世界的
    ``auto_input``; ``auto_input`` 是 callable 时直接作流的默认 policy)
    推进到下一个事件。终局自动收尾(GAME_OVER 等价续关菜单选 No, ENDING
    自动看完), 流到总结算(RESULT)时迭代结束。

    流是 headless 对局的唯一输入源, 故顺带零成本录像: 喂过的每帧输入都进
    内置 ``ReplayRecorder``(engine/replay.py 格式), ``save_replay()`` 落盘后
    可在窗口版 Replay 菜单播放或脚本重建复现。

    用法::

        stream = tw.run()
        for event in stream:
            ...
        print(stream.result)      # 迭代结束后可读总结算 dict
        stream.save_replay()      # 存录像(默认 replays/ 下时间戳命名)
        stream.policy = lambda game: Input(...)   # 中途接管输入(如 AI)
    """

    def __init__(self, world: "TouhouWorld",
                 policy: Callable[[Game], Input] | None = None) -> None:
        self._world = world
        self.policy = policy
        self._done = False
        # 录像器懒建(首次迭代/显式 save_replay 时): 造流不强制建对局
        self._recorder: ReplayRecorder | None = None

    @property
    def game(self) -> Game:
        """流正在驱动的对局门面。"""
        return self._world.game

    @property
    def result(self) -> dict | None:
        """总结算数据(流结束后非 None)。"""
        return self._world.game.result

    def _new_recorder(self) -> ReplayRecorder:
        """按世界参数建录像器; meta 记实际生效值(可据此逐帧复现整局)。"""
        w = self._world
        g = w.game
        # 种子取 impl 实际用的(能力位: GameEngine 协议外, getattr 回落):
        # seed=None 时 th07 用固定默认种子, 记实际值回放才能复现
        seed = getattr(g._impl, "seed", None)
        if not isinstance(seed, int):
            seed = 0x5EED if w.seed is None else (w.seed & 0xFFFF)
        return ReplayRecorder(make_meta(
            difficulty=int(w.difficulty), character=int(w.character),
            stage=w.stage, seed=seed, initial_lives=w.lives))

    def save_replay(self, path: PathLike | None = None) -> Path:
        """把本流已驱动的全部输入帧存成录像文件, 返回实际路径。

        ``path=None`` 时按 ``new_replay_name()`` 在默认录像目录(replays/)
        生成时间戳名; 尚未迭代过则存 0 帧录像(meta 仍完整)。
        """
        if self._recorder is None:
            self._recorder = self._new_recorder()
        if path is None:
            path = new_replay_name()
        return self._recorder.save(path)

    def __iter__(self) -> Iterator[GameEvent]:
        g = self.game
        if self._recorder is None:
            self._recorder = self._new_recorder()
        while not self._done:
            if self.policy is not None:
                inp = self.policy(g)
            else:
                default = self._world.auto_input
                inp = default(g) if callable(default) else default
            # 录像: 记本帧实际喂给对局的输入(与 tick 收到的同构)
            self._recorder.record(inp._keys(), bomb=inp.bomb,
                                  advance=inp.advance, skip=inp.skip)
            for ev in g.step(inp):
                yield ev
            ph = g.phase
            if ph == GamePhase.RESULT:
                self._done = True
            elif ph == GamePhase.GAME_OVER:
                g.finalize_game_over()   # headless 无续关 UI, 等价选 No
            elif ph == GamePhase.ENDING:
                g.finish_ending()


class TouhouWorld(Generic[_H]):
    """一部作品对局世界的统一入口(默认 th07)。典型用法::

        from touhou import TouhouWorld, WorldData, Character, Difficulty

        wd = WorldData(res_dat=".../th07.dat", bgm_dat=".../thbgm.dat")
        tw = TouhouWorld(wd=wd, character=Character.REIMU_A,
                         difficulty=Difficulty.NORMAL, lives=3, headless=True)
        stream = tw.run()                # headless: 返回 TouhouWorldEventStream
        for event in stream:
            ...

        tw2 = TouhouWorld(wd=wd, headless=False)
        tw2.run()                        # 非 headless: 弹出游戏窗口, 阻塞至关窗

    ``game`` 参数(默认 "th07")经 registry 全局注册表解析作品的
    ECL VM/ANM 格式/回调包/对局实现/窗口 App; 未注册名报清晰 KeyError。

    需要 AI 介入时给 ``stream.policy`` 赋一个 ``game -> Input`` 的函数,
    或直接用 ``tw.game.step(your_input)`` 自己逐帧驱动。

    ``auto_input`` 接受固定 ``Input`` 或 ``game -> Input`` 策略: headless 下
    callable 直接作事件流的默认 policy; 非 headless 下 callable 进入
    **观战模式** —— 窗口照开但跳过标题菜单直接进游戏, 每帧输入来自策略
    (角色/难度/残机/种子以 TouhouWorld 自身属性为准), Esc 随时中止观战::

        tw = TouhouWorld(character=Character.MARISA_A, headless=False,
                         auto_input=my_policy)
        tw.run()   # 看 AI 打游戏
    """

    # headless 字面量进泛型参数: run() 返回类型随之为 Stream / None(mypy 精确收窄)
    @overload
    def __init__(self: "TouhouWorld[Literal[True]]",
                 wd: WorldData | None = None,
                 character: ShotType = ShotType.REIMU_A,
                 difficulty: Difficulty = Difficulty.NORMAL,
                 lives: int = 3, *, headless: Literal[True],
                 stage: int = 1,
                 game: str = "th07",
                 seed: int | None = None,
                 auto_input: InputSource | None = None) -> None: ...
    @overload
    def __init__(self: "TouhouWorld[Literal[False]]",
                 wd: WorldData | None = None,
                 character: ShotType = ShotType.REIMU_A,
                 difficulty: Difficulty = Difficulty.NORMAL,
                 lives: int = 3, headless: Literal[False] = False,
                 stage: int = 1, *,
                 game: str = "th07",
                 seed: int | None = None,
                 auto_input: InputSource | None = None) -> None: ...
    @overload
    def __init__(self, wd: WorldData | None = None,
                 character: ShotType = ShotType.REIMU_A,
                 difficulty: Difficulty = Difficulty.NORMAL,
                 lives: int = 3, headless: bool = False,
                 stage: int = 1, *,
                 game: str = "th07",
                 seed: int | None = None,
                 auto_input: InputSource | None = None) -> None: ...
    def __init__(self, wd: WorldData | None = None,
                 character: ShotType = ShotType.REIMU_A,
                 difficulty: Difficulty = Difficulty.NORMAL,
                 lives: int = 3, headless: bool = False,
                 stage: int = 1, *,
                 game: str = "th07",
                 seed: int | None = None,
                 auto_input: InputSource | None = None) -> None:
        # 经注册表解析作品(未注册名在此即报 KeyError, 与 headless 无关)
        self.spec = _require_world(game)
        self.game_name = game
        self.wd = wd or WorldData()
        self.character = character
        self.difficulty = difficulty
        self.lives = lives
        self.headless = headless
        self.stage = stage
        self.seed = seed
        self.auto_input = auto_input if auto_input is not None else Input(
            shoot=True, advance=True)
        self._game: Game | None = None
        if headless:
            self._game = self._make_game()

    def _make_game(self) -> Game:
        return Game(character=self.character, difficulty=self.difficulty,
                    stage=self.stage, game=self.game_name,
                    data_path=self.wd.resolve_res(),
                    seed=self.seed, lives=self.lives)

    @property
    def game(self) -> Game:
        """headless 对局门面(非 headless 模式先 run() 弹窗, 不用此属性)。"""
        if self._game is None:
            self._game = self._make_game()
        return self._game

    @property
    def events(self) -> TouhouWorldEventStream:
        """headless 事件流(等价于 headless 模式调 run())。"""
        # 直接造流: run() 在非 headless 下会弹窗, 而流是 headless 专用语义
        return TouhouWorldEventStream(self)

    def stream(self, policy: Callable[[Game], Input] | None = None
               ) -> TouhouWorldEventStream:
        """带输入策略的事件流(等价 run() 后设置 stream.policy)。"""
        return TouhouWorldEventStream(self, policy)

    @overload
    def run(self: "TouhouWorld[Literal[True]]") -> TouhouWorldEventStream: ...
    @overload
    def run(self: "TouhouWorld[Literal[False]]") -> None: ...
    @overload
    def run(self) -> TouhouWorldEventStream | None: ...
    def run(self) -> TouhouWorldEventStream | None:
        """headless=True: 返回 ``TouhouWorldEventStream``(迭代即驱动世界);
        headless=False: 弹出作品的窗口 App(th07 = pygame 窗口), 阻塞直到
        关窗, 返回 None。"""
        if self.headless:
            return TouhouWorldEventStream(self)

        # auto_input 是 callable(逐帧策略)时进入观战模式: 窗口照开,
        # 输入由策略驱动而非键盘; 普通 Input / None 则行为不变(键盘游玩)
        spectate = self.auto_input if callable(self.auto_input) else None

        world = self.spec.world
        assert world is not None  # __init__ 里 _require_world 已校验

        # 窗口 App 经注册表按作品名解析(apis 不 import games.*, 架构铁律);
        # 未登记窗口 App 的作品只能 headless 运行
        app_cls = self.spec.app
        if app_cls is None:
            raise ValueError(
                f"作品 {self.game_name!r} 已注册, 但缺窗口 App"
                f"(需要 @register_app({self.game_name!r}) 装饰窗口应用类)")

        def make_game(*, difficulty: int, character: int) -> GameEngine:
            if spectate is not None:
                # 观战: 标题菜单被跳过(没人会选), 角色/难度以 TouhouWorld
                # 自身的属性为准, 忽略 App 传入的实参
                difficulty = int(self.difficulty)
                character = int(self.character)
            kwargs: dict = {}
            if self.spec.data is not None:
                kwargs["data"] = self.spec.data  # 数值表(注册表注入)
            impl: GameEngine = world(
                data_path=self.wd.resolve_res(), character=character,
                difficulty=difficulty, seed=self.seed,
                initial_lives=self.lives, hooks=self.spec.hooks, **kwargs)
            return impl

        # 构造契约见 registry.register_app:
        # app_cls(make_game, *, data_path, bgm_path, game_data, [spectate])
        # spectate 是可选 kwarg, 仅在观战时传(不声明支持的作品 App 不受影响)
        app_kwargs: dict = {}
        if spectate is not None:
            app_kwargs["spectate"] = spectate
        app = app_cls(make_game, data_path=self.wd.resolve_res(),
                      bgm_path=self.wd.resolve_bgm(), game_data=self.spec.data,
                      **app_kwargs)
        app.run()
        return None
