
# 全项目解耦重构 TODO

## 目标

把 `engine/` 彻底变成**通用东方 STG 引擎框架**（声明"做什么"），
把 `games/th07/` 变成**第一个作品实现**（决定"怎么做"），
消除所有 engine → games 反向依赖，为 th08/th09 铺路。

**核心原则：引擎只定契约，作品来履约。引擎绝不 import 任何具体作品。**

---

## 当前耦合全景图

```
engine/enemies.py          ──→ games.th07.player.PlayerState  (唯一硬反向边!)
engine/view/impl.py        ──→ games.th07.world.DEFAULT_SCORE_PATH
engine/score_store.py      ── 硬编码 SPELLCARD_COUNT=141 (th07 专属)
types.py GameEngine        ── cherry 必选属性 (th07 专属概念)
api.py                     ── ShotType/Difficulty 硬编码 th07 枚举
```

---

## Phase 1：消除唯一硬反向依赖（engine → games）

### 1.1 `engine/enemies.py` 解耦

**现状**：`engine/enemies.py` 第 1 行：
```python
from ..games.th07.player import Player, PlayerState, PlayerEventKind
```

这是**整个项目唯一的 engine → games 硬依赖**。`EnemyHost.contact_hits()` 和 `shoot_hits()` 需要知道玩家状态（ALIVE/DEAD/INVULNERABLE）和事件（GRAZE）。

**解耦方案**：

- [x] **新建 `engine/player_base.py`**：
  - [x] 定义 `PlayerState(IntEnum)`：ALIVE=0, SPAWNING=1, INVULNERABLE=2, DEAD=3, BORDER=4
        （实做按现有代码真实值 ALIVE=0/SPAWNING=1/DEAD=2/INVULNERABLE=3/BORDER=4 保留，本行枚举值笔误以代码为准）
  - [x] 定义 `PlayerEventKind(IntEnum)`：GRAZE, DEATH, EXTEND 等通用事件
        （实做：现有五成员整体上移（值不变），BREAK_BORDER 标注为结界系统作品用；
        IntEnum 不可子类扩展，无法按原计划拆"通用子集 + th07 扩展"）
  - [x] 定义 `PlayerEvent(msgspec.Struct)`：通用事件结构
        （data 字段注解为基座版 DeathSettle；th07 子类化 DeathSettle 追加樱点/subrank，
        world.py 消费处以 isinstance 收窄）
  - [x] 定义 `PlayerFace(Protocol)` 或抽象基类：pos, state, focus, invulnerability_timer, events 列表
        （实做名 `PlayerCombatFace`：pos/state + check_graze/check_contact/calc_damage_to_enemy，
        即 enemies 实际消费面；types.py 已有只读快照面 PlayerFace，为避免同名撞车而改名）

- [x] **修改 `engine/enemies.py`**：
  - [x] 删除 `from ..games.th07.player import ...`
  - [x] 改为 `from .player_base import PlayerState, PlayerEventKind, PlayerFace`
        （实做：`from .player_base import PlayerCombatFace, PlayerState`；PlayerEventKind 未被使用不引入）
  - [x] `contact_hits()` 参数从 `Player` 改为 `PlayerFace`（协议/ABC）
  - [x] `shoot_hits()` 同理

- [x] **修改 `games/th07/player.py`**：
  - [x] `Player` 类显式/隐式满足 `PlayerFace` 协议（隐式鸭子满足 `PlayerCombatFace`，mypy 验证通过）
  - [x] 删除自身 `PlayerState` / `PlayerEventKind` 定义，从 `engine.player_base` import
  - [x] th07 专属事件（如 BORDER_START）在 th07 层扩展，不污染基类
        （实做：枚举整体上移，BREAK_BORDER 在基类标注为结界系统作品可选事件；
        th07 专属结算经 DeathSettle/DeathContext 子类承载）

- [x] **修改 `tests/test_enemies.py`**：
  - [x] `from touhou.games.th07.player import PlayerState` → `from touhou.engine.player_base import PlayerState`

---

## Phase 2：提取引擎层通用基类（大工程）

### 2.1 玩家系统 —— `engine/player_base.py`

**现状**：`games/th07/player.py` 57KB，包含：
- 通用机制：移动、射击、碰撞、死亡重生、无敌计时
- TH07 专属：6 机体射击模式、樱点擦弹、火力档位、POC 线

**解耦方案**：

- [x] **新建 `engine/player_base.py`**：
  - [x] `PlayerState` / `PlayerEventKind` / `PlayerEvent`（从 Phase 1 移入）
  - [x] `class PlayerBase(msgspec.Struct)`：
    - 通用字段：pos, state, focus, invulnerability_timer, respawn_timer, fire_time, speed, hitbox_radius
    - 通用方法：move(keys), tick(), is_alive 等框架
    - **Stub 方法**：`_calc_shot()`（子类实现机体射击）、`_on_graze()`（子类实现擦弹结算）
    （实做：`PlayerBase` 为 plain class（原 Player 即非 Struct），泛型
    `PlayerBase[DeathCtxT]`；通用字段 pos/bounds/focus/state/invulnerability_timer/
    respawn_timer/bullet_grace_period/events/frame/hitbox_radius/graze_radius/sound；
    通用方法 push/push_keys/step/alive/invuln/take_events/die/_update_death/respawn/
    check_graze/graze_bullet/check_killbox/check_contact/is_hit/grazes/_move_player；
    hook：`_current_speeds`（移速来源，替代 TODO 的 `_calc_shot` —— th07 射击是
    .sht 数据驱动而非查表分派，无 ShotPattern 可抽象）、`_tick_options`、
    `_tick_shots`、`_on_graze`、`_settle_death`、`_on_push`；死亡音效索引经
    类属性 `DEATH_SE` 注入（th07=4）。fire_time/speed 属射击系统留 th07）
  - [ ] `class ShotPatternBase(Protocol)`：射击模式接口（供 `_calc_shot` 分派）
    （不采纳 —— th07 射击由 .sht 数据驱动（fire/update/hit 回调索引来自资源），
    不存在可抽象的"射击模式类"分派面；强行造 Protocol 是为抽象而抽象）

- [x] **精简 `games/th07/player.py`**：
  - [x] `class Player(PlayerBase)`：继承基类，实现 `_calc_shot`（6 机体查表分派）
    （实做：实现 `_current_speeds`/`_tick_shots`/`_tick_options`/`_on_graze`/
    `_settle_death` hook；射击本体（弹池/回调分派）数据驱动，非查表分派）
  - [x] 保留 TH07 专属：樱点擦弹、火力档位、POC 线吸附、擦弹分计算
  - [x] `CHAR_REIMU_A` 等机体常量保留在 th07 层（在 bomb.py，未动）

### 2.2 炸弹系统 —— `engine/bomb_base.py`

**现状**：`games/th07/bomb.py` 包含通用炸弹框架 + 12 套机体炸弹 + 樱之结界。

**解耦方案**：

- [x] **新建 `engine/bomb_base.py`**：
  - [x] `class BombBase(msgspec.Struct)`：
    - 通用字段：is_in_use, duration, timer, invulnerability_timer, invulnerable, move_speed_multiplier
    - 通用结构：DamageBox, ClearBox, BombSubInfo
    - 通用方法：start(), tick(), check_bomb_graze(), damage_to(), hits()
    - **Stub 方法**：`_calc(ctx)`（子类实现机体炸弹逻辑）
    （实做：`BombBase[BombCtxT]` 泛型；另有 `_tick_resource_cost`/`_reset_run_state`
    两个 hook 承载 th07 的樱点 drain，零行为改动）
  - [x] `class BombContext(msgspec.Struct)`：通用输入（player_pos, difficulty, rng）
    （th07 在同包 bomb.py 子类化追加 cherry/cherry_start/last_enemy_hit）
  - [x] `try_start_bomb()` 通用触发逻辑

- [x] **精简 `games/th07/bomb.py`**：
  - [x] `class Bomb(BombBase)`：继承基类，实现 `_calc`（12 套机体查表 `_BOMB_CALCS`）
  - [x] 保留 TH07 专属：樱点消耗 `compute_bomb_cherry_drain()`、樱之结界 `Border`

### 2.3 全局计数 —— `engine/globals_base.py`

**现状**：`games/th07/globals.py` 的 `ZunGlobals` 包含通用计数 + 樱点/动态难度。

**解耦方案**：

- [x] **新建 `engine/globals_base.py`**：
  - [x] `class GlobalsBase(msgspec.Struct)`：
    - 通用字段：score, gui_score, lives_remaining, bombs_remaining, bombs_used, deaths, num_retries, current_power
    - 通用方法：add_score(), tick_gui_score(), snap_gui_score()
    - **可选扩展点**：子类可覆盖 `add_score` 等行为
    （另：SCORE_MAX/GUI_SCORE_INCREMENT_MAX 两常量随计分逻辑一并移到基座，
    games/th07/globals.py 再导出以保持兼容）
- [x] **精简 `games/th07/globals.py`**：
  - [x] `class ZunGlobals(GlobalsBase)`：继承基类
  - [x] 保留 TH07 专属：cherry, cherry_max, cherry_plus, cherry_start, rank, subrank, graze_in_stage/total, spell_cards_captured
  - [x] 保留 TH07 专属方法：add_cherry(), add_cherry_plus(), increase_cherry_max(), subtract_cherry_drain(), increase/decrease_subrank()

### 2.4 道具系统 —— `engine/item_base.py`

**现状**：`games/th07/items.py` 包含通用道具机制 + TH07 专属道具类型/分值。

**解耦方案**：

- [x] **新建 `engine/item_base.py`**：
  - [x] `class ItemTypeBase(IntEnum)`：通用道具类型子集
    - POWER_SMALL=0, POINT=1, POWER_BIG=2, BOMB=3, FULL_POWER=4, LIFE=5, NO_ITEM=255
    （偏差：Python 禁止继承已有成员的 Enum 再加成员，`ItemTypeBase` 实做为普通
    int 常量类，作品层各自定义自己的 IntEnum —— 见 item_base.py docstring）
  - [x] `class ItemBase(msgspec.Struct)`：通用道具（pos, state, type, auto_collect）
    （偏差：`type` 字段留在作品子类（基类不识别作品枚举值），基类管运动学
    pos/start/state/auto_collect/timer/target/start_pos + drop/spawn_to/step）
  - [x] `class ItemWorldBase(msgspec.Struct)`：
    - 通用机制：spawn(), step(), collect_pickup(), remove_all_items(), despawn_all_items(), activate_all_items()
    - **Stub 方法**：`_collect(item, ctx)`（子类实现收集结算）
    （实做：`ItemWorldBase[ItemT, CtxT]` 泛型；step/collect_pickup/remove_all_items/
    activate_all_items/remove/clear/alive 在基类，`_status_change` 为吸附触发 hook；
    spawn/despawn_all_items 绑死 th07 满火力转 CHERRY 语义留在作品层，
    collect 整体即作品结算未再拆 `_collect`）
  - [x] `class CollectResultBase(msgspec.Struct)`：通用结算结构（score, delta_power, delta_bombs, delta_lives）
  （另：`ItemContextBase` 承载判定最小环境快照 player_pos/player_alive/player_state/吸附速度半径）

- [x] **精简 `games/th07/items.py`**：
  - [x] `class ItemType(ItemTypeBase)`：扩展 TH07 专属（POINT_BULLET=6, CHERRY=7, CHERRY_SMALL=8, STAR=9）
    （ItemType 仍为独立 IntEnum —— 枚举不可继承扩展，见上）
  - [x] `class ItemWorld(ItemWorldBase)`：覆盖 `_collect()` 实现 TH07 分值表
    （实做：覆盖 `_status_change()` 吸附触发；`collect()` 整体保留）
  - [x] 保留 TH07 专属：`GameContext`（含 cherry/border/difficulty 等）、`_point_score()`、`_graze_score()`

### 2.5 Boss 系统 —— `engine/boss_base.py`

**现状**：`games/th07/boss.py` 的 `Boss` 类比较通用，但 `SPELLCARD_SCORE` 是 TH07 的 141 张表。

**解耦方案**：

- [x] **新建 `engine/boss_base.py`**：
  - [x] `class BossBase(msgspec.Struct)`：
    - 通用字段：name, pos, life, max_life, is_active, phase, invincibility_timer
    - 通用机制：set_life(), check_life_threshold(), begin_spellcard(), tick(), end_spellcard()
    - **Stub/注入点**：`spellcard_scores: tuple[int, ...]`（外部注入，空=未配置）
    （另：`_score_table()` 为可覆盖 hook，th07 子类覆盖以回落内置默认表）
  - [ ] `class SpellcardResult(msgspec.Struct)`：符卡结算
    （不采纳 —— `end_spellcard()` 的 dict 返回面已被 world.py/测试大量消费，
    换 Struct 是行为面改动，收益不抵成本；维持 dict）

- [x] **精简 `games/th07/boss.py`**：
  - [x] `class Boss(BossBase)`：继承基类，默认注入 `SPELLCARD_SCORE`
  - [x] 保留 TH07 专属：`handle_timer_callback()` 的 cherry_penalty 逻辑
    （另：add_graze_bonus 的樱点擦弹加成公式同为 th07 专属，保留在本层）

---

## Phase 3：视图层解耦

### 3.1 `engine/view/impl.py` 去硬编码

**现状**：
```python
from ...games.th07.world import DEFAULT_SCORE_PATH
```

**解耦方案**：

- [x] `GameApp.__init__` 增加 `score_path: str | None = None` 参数（已有，但默认值从 th07 导入）
- [x] 删除 `from ...games.th07.world import DEFAULT_SCORE_PATH`
- [ ] 默认值改为 `score_path = score_path or "score.json"`（或从 `game_data` 参数推导）
      （未采用 —— 选了下行方案）
- [x] 或者：`DEFAULT_SCORE_PATH` 移到 `engine/paths.py` 作为引擎级默认值
      （实做：移到既有的 `touhou/paths.py`（路径模块本就是干这个的），world.py 与 view/impl.py 均从 paths 导入）

---

## Phase 4：ScoreStore 解耦

### 4.1 消除 `SPELLCARD_COUNT = 141` 硬编码

**现状**：`engine/score_store.py` 在引擎层硬编码了 141 张符卡（TH07 专属）。

**解耦方案**：

- [x] `ScoreStore.__init__` 增加 `spellcard_count: int = 0` 参数
- [x] `catk` 列表按 `spellcard_count` 长度初始化，不再硬编码 141
      （越界保护改用 `len(self.catk)`；from_dict/load 同名参数透传，
      读档时按 max(传入值, 存档实际长度) 扩容，读档不丢卡）
- [x] `ScoreStore` 的 `SPELLCARD_COUNT` 常量删除或改为文档注释（已删除常量）
- [x] `games/th07/world.py` 构造 `ScoreStore` 时传入 `spellcard_count=len(SPELLCARD_SCORE)`
      （实做传 `len(self.data.spellcard_scores)`，等值且随注入表走；
      view/impl.py 三处 load 也注入 —— game_data 优先，缺省经注册表查 th07 表兜底）
- [x] 或者：`GameData` 增加 `spellcard_count` 字段，注册时注入
      （不需要 —— `GameData.spellcard_scores` 元组已在，`len()` 即总数）

---

## Phase 5：协议层精化

### 5.1 `types.py` GameEngine 协议

**现状**：`cherry` 是 `GameEngine` 的必选属性。非 TH07 作品可能没有樱点。

**解耦方案**：

- [x] `GameEngine` 协议中 `cherry` 从 `@property` 必选降为**可选能力位**
- [x] `api.py` 中 `Game.cherry` 属性改为：
  ```python
  @property
  def cherry(self) -> int:
      return getattr(self._impl, "cherry", 0)
  ```
- [x] 同理检查 `border` 属性：已有 `BorderFace` 协议，无结界系统的作品可返回 `active=False` 的 stub
      （现状即如此 —— `BorderFace` docstring 已写明"无结界系统的作品给一个 active 恒 False 的对象即可"，无需改动）

### 5.2 `api.py` 枚举

**现状**：`ShotType` / `Difficulty` 硬编码 TH07 的 6 机体 / 6 难度。

**决策**：保留在 `api.py` 作为**"已知枚举"**（类似 `EclOpcode` 留在 engine），但文档注明这是 th07 的枚举。新作品可以：
- 复用同一枚举（如果机体/难度语义相同）
- 在 `games/th08/` 定义新枚举
- `api.py` 的 `Game` 门面接受 `int` 作为 character/difficulty，不限定必须用 `ShotType`/`Difficulty` 枚举

---

## Phase 6：注册表扩展（为 th08 准备）

### 6.1 新增注册维度（可选）

当前注册表已有：ecl / anm / game_hooks / world_impl / game_data / renderer

- [ ] 考虑新增 `register_player(name)`：注册玩家实现类（不采纳，见下）
- [ ] 考虑新增 `register_bomb(name)`：注册炸弹实现类（不采纳，见下）
- [x] 或者保持现状：这些通过 `world_impl` 的构造参数注入，不走独立注册维度
      （已采纳 —— 引擎经 `PlayerCombatFace`/`GameEngine` 协议与作品交互，
      无需新增注册维度）

**建议**：保持现状。`world_impl` 构造时自己决定用什么 `Player`/`Bomb`/`Globals` 类，引擎层只通过协议（`PlayerFace`/`GameEngine`）与它们交互。

---

## Phase 7：测试迁移

### 7.1 测试结构重组

- [x] `tests/test_ecl.py` → 已迁移，确认通过（本次重构未触及；全量套件验证通过）
- [x] `tests/test_enemies.py`：
  - [x] `from touhou.games.th07.player import PlayerState` → `from touhou.engine.player_base import PlayerState`
  - [x] `make_player()` 工厂函数确认构造的是 `Player`（th07 实现）
- [x] 新增 `tests/test_player_base.py`：用 mock/stub 测试 `PlayerBase` 框架（移动、状态机、无敌计时）
      （StubPlayer：固定移速 + 最小死亡结算；另覆盖判定/擦弹 AABB、清弹期事件、兼容属性）
- [x] 新增 `tests/test_bomb_base.py`：测试 `BombBase` 通用机制（DamageBox/ClearBox/触发）
      （StubBomb/BareBomb；触发门槛、盒推进、资源消耗 hook、判定几何）
- [x] 新增 `tests/test_globals_base.py`：测试 `GlobalsBase` 分数追赶逻辑

---

## 文件迁移映射（总表）

| 原位置 | 新位置 | 说明 |
|---|---|---|
| `games/th07/player.py` 中的 `PlayerState/PlayerEventKind` | `engine/player_base.py` | 通用玩家状态/事件 |
| `games/th07/player.py` 中的通用移动/碰撞框架 | `engine/player_base.py: PlayerBase` | 抽象基类 |
| `games/th07/player.py` 中的 6 机体射击 | `games/th07/player.py: Player` | 继承基类，保留专属 |
| `games/th07/bomb.py` 中的通用 Bomb 框架 | `engine/bomb_base.py: BombBase` | 抽象基类 |
| `games/th07/bomb.py` 中的 12 套机体炸弹 | `games/th07/bomb.py: Bomb` | 继承基类，保留专属 |
| `games/th07/bomb.py` 中的樱之结界 `Border` | `games/th07/bomb.py` | TH07 专属，不动 |
| `games/th07/globals.py` 中的通用计分 | `engine/globals_base.py: GlobalsBase` | 抽象基类 |
| `games/th07/globals.py` 中的樱点/动态难度 | `games/th07/globals.py: ZunGlobals` | 继承基类，保留专属 |
| `games/th07/items.py` 中的通用道具机制 | `engine/item_base.py: ItemWorldBase` | 抽象基类 |
| `games/th07/items.py` 中的 TH07 道具类型/分值 | `games/th07/items.py: ItemWorld` | 继承基类，保留专属 |
| `games/th07/boss.py` 中的通用 Boss 框架 | `engine/boss_base.py: BossBase` | 抽象基类 |
| `games/th07/boss.py` 中的符卡分值表 | `games/th07/boss.py: Boss` | 注入点保留在作品层 |
| `engine/score_store.py` 的 `SPELLCARD_COUNT=141` | 参数化 | 构造时注入 |
| `engine/view/impl.py` 的 `DEFAULT_SCORE_PATH` | 删除硬编码 | 通过参数/注册表注入 |

---

## 关键注意事项

1. **绝不反向依赖**：`engine/` 下任何文件**禁止** `import touhou.games.th07`。
2. **前向引用**：基类使用 `from __future__ import annotations`，避免循环导入。
3. **msgspec.Struct 继承**：子类继承基类 Struct 时，字段追加要符合 msgspec 规则（`msgspec.field(default_factory=...)`）。
4. **协议 vs ABC**：优先用 `Protocol`（`types.py` 已有成熟模式），但基类需要共享实现时用 `msgspec.Struct` + stub 方法。
5. **测试兼容性**：每 Phase 完成后跑 `pytest tests/`，确保行为零变化。
6. **渐进式迁移**：每个基类提取后，先保留原文件作为 fallback，测试通过后再删除旧逻辑。

---

## 未来展望（th08 落地时）

等本 TODO 全部完成后，th08 的接入成本：

```python
# games/th08/__init__.py
from .data import TH08_DATA
from .player import Player          # 继承 engine/player_base.PlayerBase
from .bomb import Bomb              # 继承 engine/bomb_base.BombBase
from .globals import Globals        # 继承 engine/globals_base.GlobalsBase
from .items import ItemWorld        # 继承 engine/item_base.ItemWorldBase
from .boss import Boss              # 继承 engine/boss_base.BossBase
from .ecl_vm import EclMachineTh08  # 继承 engine/ecl_base.EclMachineBase
from .world import ImperishableNight # 继承/实现 GameEngine 协议

@register_ecl("th08", file_format=EclFileTh08)
class EclMachineTh08(EclMachineTh07):
    # 覆盖差异 opcode，新增 th08 专属
    pass

@register_world_impl("th08")
class ImperishableNight:
    # 构造时注入 TH08_DATA
    # 使用 th08 专属的 Player/Bomb/Globals/ItemWorld/Boss
    pass
```

引擎层**完全不需要改动**。这才是"engine 声明做什么，games 决定怎么做"的终极形态。🫡
