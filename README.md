# touhou

通用东方弹幕游戏框架 —— TH07《东方妖妖梦》(Perfect Cherry Blossom) 为
首个接入作品/参考实现: 引擎逻辑对照原版反编译逐帧移植, 对外提供干净的
Pythonic API(`touhou.apis.basic`), 窗口版游戏用 pygame 渲染。
发行包名与入口命令沿用 `touhou07`。

## 架构

三层结构, 作品经注册表接入框架(各维度见下文"接入新作品"):

- **框架层**(`touhou/` 顶层): 作品无关的对外 API(`apis/`)、注册表
  (`registry.py` decorator)、类型门面(`types.py`)、工具与基础设施
  (`utils/`、`exceptions.py`、`paths.py`、`env.py`、`logger.py`)。
- **引擎层**(`touhou/engine/`): 跨作品可复用机制 —— ECL 虚拟机基类、
  Player/Bomb/Globals/Item/Boss 基座、弹幕/激光/敌人宿主,
  以及 `render/`(Renderer 协议 + D3DX-like 管线接口)与 `view/`
  (作品无关渲染基建)。
- **作品层**(`touhou/games/th07/`): TH07 的全部具体实现(对局主逻辑/
  自机/符卡/炸弹/道具/ECL VM/数值表/表现层), 在 `import touhou` 时
  经 decorator 完成全维度注册 —— 也是接新作品时照抄的骨架。

## 安装

```bash
pip install -e .        # 或: uv pip install -e .
```

依赖: Python ≥ 3.12, numpy, pillow, pygame, loguru, msgspec。

## 运行游戏

```bash
touhou07                # 安装后的入口脚本
python -m touhou        # 或直接用模块入口
```

## 游戏资源

各作品使用对应的原版游戏数据(th07 为 `th07.dat`, BGM 用同目录的
`thbgm.dat`, 自动推导), 运行时解包, 仓库不分发任何二进制资源。
路径解析顺序:

1. 显式参数(`Game(data_path=...)` / `GameApp(data_path=...)`)
2. 环境变量 `TOUHOU_DAT`
3. 内置默认路径(见 `touhou/paths.py`)

```bash
set TOUHOU_DAT=D:\games\th07\th07.dat   # Windows
export TOUHOU_DAT=/games/th07/th07.dat  # Linux
```

## API 用法

统一入口 `TouhouWorld`(headless 事件流 / 窗口版游戏):

```python
from touhou import TouhouWorld, WorldData, Character, Difficulty

wd = WorldData(res_dat=".../th07.dat", bgm_dat=".../thbgm.dat")  # 均可省略走默认解析

tw = TouhouWorld(wd=wd, character=Character.REIMU_A,
                 difficulty=Difficulty.NORMAL, lives=3, headless=True)
stream = tw.run()            # headless: 返回 TouhouWorldEventStream
for event in stream:         # 迭代即驱动世界, 终局自动收尾
    print(event.kind.value, event.name or "")
print(stream.result)         # 迭代结束后: 总结算 dict

tw2 = TouhouWorld(wd=wd, headless=False)
tw2.run()                      # 非 headless: 弹出游戏窗口(阻塞)
```

细粒度控制用 `Game` 门面:

```python
from touhou import Game, Input, ShotType, Difficulty, GamePhase

game = Game(character=ShotType.REIMU_A, difficulty=Difficulty.NORMAL, seed=42)
while game.phase in (GamePhase.RUNNING, GamePhase.DIALOG):
    events = game.step(Input(shoot=True, advance=True))
    for ev in events:                       # SPELLCARD_BEGIN/PLAYER_DEATH/...
        print(ev.kind.value, ev.name or "")
print(game.score, game.lives, game.result)  # 结算后 result 非 None
```

- `stream.policy = lambda game: Input(...)` 可随时接管输入(自定义策略/AI)。
- `game.snapshot()` 返回当前帧不可变实体快照(bullets/enemies/items/lasers/
  player/boss), 供外部渲染或 AI 观测; 每帧构造有开销, 按需调用。
- 属性 `score/lives/bombs/power/cherry/graze/frame/phase/stage/result` 均只读。
- `touhou.types` 是类型门面: 集中公共类型别名(`PathLike`/`KeysTuple`/事件钩子
  签名)与结构式 Protocol(`PosLike`/`Positioned`), 供 IDE 类型提示; 公共数据类型
  在类型检查期也可 `from touhou.types import Input, GameEvent, ...` 解析
  (运行时请从 `touhou`/`touhou.apis.basic` 导入)。

## 接入新作品

接新作品(th08…)的姿势: 照 `games/th07/` 抄一套骨架, 组件在定义处用
decorator 注册即可, 框架代码零改动。`touhou.registry` 是框架与作品的
接缝: 全局注册表 + decorator, th07 是第一个注册作品(各组件在定义处
"引用注册", 不移动文件)。注册维度:

- `@register_ecl(name, file_format=...)` — ECL 虚拟机(指令集解释器类 + ecldata 解析类)
- `@register_anm(name, version=...)` — ANM 格式变体(解析类 + 版本号)
- `@register_game_hooks(name, stage_file=..., ecl_file=..., msg_file=...)` —
  游戏回调包(EclHost 宿主实现类 + 关卡资源命名规则)
- `@register_world_impl(name)` — 对局主逻辑类, `TouhouWorld(game=name)` 经此构造
- `register_game_data(name, GameData)` — 数值表/名单(符卡分值/炸弹参数/
  掉落表/火力档/机体 sht 映射/角色与难度名单), 构造对局时经 `data=` 注入

同一维度同名重复注册报 `ValueError`(防静默覆盖); 查找未注册名报带已注册
列表的 `KeyError`。th07 的注册点: `games/th07/ecl_vm.py:EclMachineTh07`、
`schema/anm.py:AnmFile`、`games/th07/ecl_host.py:GameEclHost`、
`games/th07/world.py:PerfectCherryBloom`、`games/th07/data.py:TH07_DATA`。

门面(`touhou.apis.basic.Game`)只面向 `touhou.types.GameEngine` 协议编程
(tick/frame/stage_no/globals/boss/border/player + bullets/host/items/lasers
容器), 对局实现鸭子满足该协议即可, 无需 adapter; 符卡/对话等作品专属探测
走可选方法 `spellcard_active()`/`msg_active()`(不实现则门面按 False 回落)。
th07 的数值表集中在 `touhou/games/th07/data.py`(单一来源), 作品包模块
(boss/bomb/items)的模块级同名常量即该表 —— 不经注册表单独用这些模块也是
th07 默认行为。窗口版 GameApp 的名单/难度/面数经 `game_data` 参数化
(HUD/选人贴图布局为 th07 专属, 见各 view 模块 docstring)。

## 包结构

- `touhou/apis/basic.py` / `types.py` / `registry.py` — 对外门面、类型门面、
  作品注册表(渲染后端维度: `register_renderer`, 默认 "pygame")
- `touhou/games/th07/` — th07 的游戏逻辑包: `world.py`(对局主逻辑
  PerfectCherryBloom)、`player.py`(自机)、`boss.py`(符卡)、`bomb.py`
  (炸弹+结界)、`items.py`(道具经济)、`globals.py`(樱点/计数)、
  `results.py`(评级)、`ecl_host.py`(ECL 宿主钩子)、`ecl_vm.py`
  (TH07 ECL 虚拟机: EclMachineTh07 + EclVarId + opcode handler)、
  `playerdata.py`(Player Data 装配)、`data.py`(数值表, 单一来源)、
  `view/`(th07 表现层: GameApp 应用壳、菜单场景、HUD/战斗画面、
  PygameRenderer 后端)
- `touhou/engine/` — 跨作品可复用机制: `ecl.py`(ECL 文件解析+状态结构)、
  `ecl_base.py`(ECL VM 框架基类 EclMachineBase)、`bullets.py` /
  `bullet_commands.py` / `lasers.py`(弹幕/激光原语)、`enemies.py`
  (EclEnemy 宿主+伤害管线)、`player_base.py` / `bomb_base.py` /
  `boss_base.py` / `globals_base.py` / `item_base.py`(各系统的作品无关
  基座)、`ending.py`(结局脚本)、`rng.py`(确定性随机)、
  `replay.py` / `config.py` / `score_store.py`(机制类)、
  `render/`(渲染后端协议 Renderer + FrameInput)、`view/`(作品无关渲染
  基建: anm 脚本 VM/特效层/.std 3D 背景/SpriteBank/SoundPlayer/震屏)
- `touhou/schema/` — 资源格式解析(dat/anm/std/ecl/sht/msg/thbgm 等)

新作品(以假想的 th99 为例)的骨架:

```python
from touhou.registry import (
    GameData, register_anm, register_ecl, register_game_data,
    register_game_hooks, register_world_impl,
)
from touhou.engine.ecl import EclFile, EclHost
from touhou.engine.ecl_base import EclMachineBase

@register_ecl("th99", file_format=EclFile)     # 指令集不同就换自己的 Machine/File
class Th99EclMachine(EclMachineBase): ...      # 基类只定契约, opcode 用 @register 登记

@register_anm("th99", version=2)
class Th99AnmFile(...): ...

@register_game_hooks("th99", stage_file="st{n}.std")   # 自己的资源命名规则
class Th99EclHost(EclHost): ...

register_game_data("th99", GameData(           # 自己的数值表(可复用 th07 子集)
    characters=(...), difficulties=(...), character_sht={0: ("p0.sht", "p0s.sht")},
    spellcard_scores=(...), drop_table=(...)))

@register_world_impl("th99")   # 构造契约见 registry 模块 docstring
class Th99Game: ...            # 鸭子满足 GameEngine 协议; 也可直接复用 th07 引擎

tw = TouhouWorld(game="th99", headless=True)   # 从注册表解析
```

最小路径(只换数据/资源, 不改引擎): 复用 `PerfectCherryBloom` 作对局实现,
只注册自己的 `GameData` —— 示例见 `tests/test_registry.py` 的
`test_stub_game_with_custom_data_reuses_th07_engine`。ECL 指令集拆分为独立
子包仍是新作品落地时的工作。

## 测试

```bash
SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy uv run python -m pytest -q
```

测试用真实 th07.dat 数据(默认路径或 `TOUHOU_DAT` 指向)。
