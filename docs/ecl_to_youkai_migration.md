# ECL → Youkai-Homecoming 妖归符卡 JSON 迁移指南

把原版东方(th07《东方妖妖梦》)的 ECL 弹幕脚本逆向还原成妖归
(Youkai-Homecoming 0.23.x)的 SpellDefinition JSON。
核心原则: **跑引擎就是跑原版** —— 不手猜参数语义, 用本仓库的 ECL VM 逐帧
回放符卡 sub, 回放输出就是权威输入。

工具链(`touhou/engine/translate/`):

- `EclTranslatorBase`(base.py): `EclHost` 的**录制**实现。`record()` 用
  注册表里的作品 VM(`EclMachineTh07`)逐帧回放 sub, 把每次弹幕回调记成
  带帧戳的 `TraceEvent`; `translate()` 按 `TranslateMode` 双模式分发
  (DIRECT = record → `compile(trace)`; CONTROL = `parse_ir()` →
  `compile_ir(ir)`, 见下节)。
- `ir.py`: CONTROL 模式的控制流 IR(`IrSeq`/`IrLoop`/`IrIf`/`IrOp`)与
  静态重建算法(作品无关)。
- `YoukaiDanmakuTranslator`(youkai.py): `compile(trace)` 把逐帧命令式
  trace **折叠**成妖归的声明式动作 JSON; `compile_ir(ir)` 把控制流 IR
  **直接映射**成妖归动作(循环/条件结构保留)。

## 翻译模式(DIRECT vs CONTROL)

`translate(ecl_data, sub_id, *, mode=...)` 两模式是两种不同的翻译哲学:

| | DIRECT(默认) | CONTROL |
|---|---|---|
| 路线 | 回放作品 VM 录逐帧 trace → 折叠 | 静态控制流重建 IR → 直接映射(不走 VM) |
| 保真度 | **忠实的运行时快照**(变量演进、rank 插值、自动射击都算出来了) | **静态近似**(只认指令字面值 + 简单仿射变量) |
| 结构 | 循环/条件被展开压扁成 tick_interval 平铺 | **保留循环/条件**(repeat/conditional/delay) |
| 变量依赖 | 不存在(回放时已是具体值) | 映射不了的跳过(log.warning) |
| 适用 | 要"这张卡实际长什么样"的权威快照 | 要可读的波次结构/想手调循环参数/给 LuaSTG 类目标供结构化 IR |

CONTROL 的 IR 重建规则(ir.py, 作品无关):

- **回边 → `IrLoop`**: 无条件 JUMP 跳回前面的指令 = 无限循环
  (`condition=None`); `DEC_JUMP` 回边 = 计数循环(`counter_var`);
  `JUMP_IF_*` 回边 = 条件循环。节点保留时间语义: `loop_time`(回边重置的
  context time)与 `period`(一轮帧数), 迭代 k 里 time=T 的指令在绝对帧
  T + k×period 执行。
- **条件前跳 → `IrIf`**: `JUMP_IF_*` 向前跳 = if; if_true 末尾是无条件
  JUMP 且跳到更后面时识别出 else 双臂。
- **其余 → `IrOp`**: 原样携带 `EclInstr`。goto 蛛网不做完备结构化:
  回边目标不在节点边界、循环体内有逃逸跳转等不可归约情形保留为 IrOp
  平铺(log.debug 说明), 另有深度(32)/节点数(4096)兜底, 不会死循环/炸栈。

CONTROL 的妖归映射(youkai.py compile_ir):

- `IrLoop` → `repeat`: DEC_JUMP 计数器初值可静态确定 → 有限 `count`;
  无限/次数未知 → `count=100000` 近似上限(≈27.8 分钟, 覆盖任意真实符卡)。
  循环体发射包 `delay`(`delay_ticks = "$i * period + T"`, `$i` 是 repeat
  的 index_variable, 嵌套用 `$j`/`$k`)。
- `IrIf` → `conditional`(常量条件); 条件依赖变量且不可静态求值 →
  log.warning, if_true 内联近似(if_false 丢弃)。
- 弹幕/激光 IrOp → `fire_danmaku`/`fire_laser`(映射表与 DIRECT 共用)。
- **变量操作数**: 能识别成"SET 初值 + 每轮 ADD/SUB/INC/DEC 步进"仿射形式
  的, angle1 映射成 NumberExpr 简写(如 `$j * -22.5`, 度制); 其他操作数
  (count/speed/sprite 等)只接受常量, 带步进或不可求值 → log.warning 并
  跳过该指令。未写过的变量按 ECL 默认 0 处理 —— **难度分支变量也会取 0**,
  即 CONTROL 静态走的是 Easy 分支结构(首张 BEGIN_SPELLCARD 名也多为
  `-Easy-` 变体)。
- v1 不覆盖: `SET_SHOOT_INTERVAL` 自动射击(是 VM 帧更新行为, 不是指令)、
  激光的跨指令角度更新(ADD_LASER_ANGLE 等)、INIT_INTERP 插值 —— 这些卡的
  CONTROL 输出会偏少甚至为空(如实反映静态可见性)。

两模式实测对照(二面 天符「天仙鳴動」ecldata2 sub 64):

- CONTROL: `repeat(100000) → repeat(5) → delay("$i * 644 + $j * 20") →
  fire(line 10发, angle_offset="$j * -22.5")` —— 外层无限循环每 644 帧
  一轮, 内层 5 波每 20 帧一波, 每波旋转 -22.5°, 结构一目了然。
- DIRECT: `conditional(tick_interval interval=20 offset=240) →
  fire(line 11发, angle_offset="phase_tick * -1.125 + 270")` —— 同样的
  旋转(-1.125°/帧 × 20 帧 = -22.5°/波)被压扁成帧级表达式; count 差 1
  (DIRECT 是回放运行时值, 含 rank/ spellcard 状态影响)。
- 输出均过官方校验器 OK。

## 用法

### 代码

```python
import json
import touhou  # import 即完成 th07 注册
from touhou.schema.archive import GameArchive
from touhou.engine.translate import YoukaiDanmakuTranslator
from touhou.paths import DEFAULT_DATA

arc = GameArchive.open(DEFAULT_DATA)  # th07.dat(或传自己的路径)
ecl = arc.load("ecldata1.ecl")        # 一面 ECL

tr = YoukaiDanmakuTranslator("th07", speed_scale=0.5)
out = tr.translate(ecl, 42, max_frames=3600)  # sub 42 = 寒符「リンガリングコールド」
with open("lingering_cold.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
```

### 命令行

```bash
uv run python -c "
import json, touhou
from touhou.schema.archive import GameArchive
from touhou.engine.translate import YoukaiDanmakuTranslator
from touhou.paths import DEFAULT_DATA
ecl = GameArchive.open(DEFAULT_DATA).load('ecldata1.ecl')
out = YoukaiDanmakuTranslator('th07').translate(ecl, 42, max_frames=3600)
json.dump(out, open('lingering_cold.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
"
```

### 校验

产物过妖归官方校验器(jsonschema 依赖):

```bash
uv run python <youkai-danmaku-json>/scripts/validate_spell_json.py lingering_cold.json
# → lingering_cold.json: OK
```

### 怎么找 sub id

```python
from touhou.engine.ecl import EclFile
from touhou.engine.translate import decode_spellcard_name

ecl_file = EclFile.parse(ecl)
for sub_id, sub in enumerate(ecl_file.subs):
    for ins in sub:
        if ins.id == 90:  # BEGIN_SPELLCARD
            print(sub_id, decode_spellcard_name(ins.raw_arg_bytes()[4:52]))
```

注意一张卡常带多个难度变体(`skipOnDifficulty` 位掩码); 回放难度用
`record(..., context={"difficulty": 2})` 指定(0=E 1=N 2=H 3=L, 默认 1)。

## 工作流程

1. **定位符卡**: 按上面方法扫 BEGIN_SPELLCARD(名字 XOR 0xAA + Shift-JIS,
   VM 回放时已自动解码, host 收到的就是明文)。
2. **回放录制**: `record(ecl_bytes, sub_id)` 逐帧驱动 VM, 录制
   `spawn_bullet_pattern`/`spawn_laser_pattern` 的结构化参数快照。
   翻译场景没有真实对局: 玩家固定 (192, 400), `enemy.life` 默认 100000
   (SET_SHOOT_INTERVAL 自动射击只在 life>0 触发), 可经 `context` 覆盖
   (`{"difficulty": 2}` 或 `{"enemy.life": 5000}`)。
3. **编译**: `compile(trace)` 映射 + 折叠(下表)。
4. **校验 + 手调**: 过 `validate_spell_json.py`; 速度/密度按手感微调
   (`speed_scale` 是首要旋钮)。

## 映射表

| ECL 行为 | 妖归 JSON | 注意 |
|---|---|---|
| `spawn_bullet_pattern` | `fire_danmaku` | sprite→bullet、offset→颜色用实测默认表(th07 etama.anm 主色提取), 可经构造参数覆盖 |
| 环形(aim 2..5) | `pattern: "ring"` | 错半格(4/5)自动加 `angle_offset` 半格 |
| 扇形(aim 0/1) | `pattern: "line"` + `spread` | spread = angle2×(count1-1) 弧度→度 |
| 随机系(aim 6..8) | `pattern: "random"` + `spread` | 角度区间取中点做 `angle_offset` |
| 多层环(count2>1) | count=count1×count2, `speed: "rand(lo,hi)"` | 层间速度插值的声明式近似 |
| 速度(像素/帧) | `speed` | × `speed_scale`(默认 0.5; 参考 引擎0.8≈妖归0.4), **要手调** |
| TARGET_ANGLE(0x20) 纯加减速 | `formula` mover `x="(v0 + tick * a) * tick"` | 自身坐标系 x=forward |
| TARGET_ANGLE 纯旋转 | `polar` mover | radius=v/ω, angular_speed=deg(ω) |
| TARGET_ANGLE 加速+旋转 | `formula` x 加速 + `y="sin_rad(tick*w)*R"` | 三角函数必须 `sin_rad`/`cos_rad`, 禁裸 sin/cos |
| TARGET_VEL(0x10) | `acceleration` mover | 恰好同为世界坐标系 |
| 命令 duration 有限 | `composite` 前 mover 后匀速/零速 | 命令结束后弹继续直行 |
| 激光 start/duration/end | `setup_prepare`/`lifetime`/`setup_end` | 三段时序; 常驻激光 duration 收紧到 stop_frame/回放界 |
| 激光 add_angle 连发 | `rotate` mover(degrees_per_tick=均值) | |
| 激光旋转后停(laser_stop) | `composite` 前 `rotate` 后 `zero` | |
| 同构 pattern 固定间隔重复 | `conditional` + `tick_interval`(offset=首帧) | **折叠**, 见下节 |
| 单发事件 | `conditional` + `compare`(phase_tick == 帧) | 妖归无 one-shot 条件 |
| BEGIN_SPELLCARD | `display.name`/`custom_names` + id 建议(`..._card<gui_id>`) | |
| 道具/音效/清屏/召唤小怪 | 跳过 + log.debug | 不可翻译事件不无声丢弃 |

## 重复折叠算法

ECL 是逐帧命令式: 同一 pattern 按固定间隔一帧一帧发。直接平铺会得到
"一帧一条 action" 的巨型 JSON。`compile` 的折叠:

1. 按**签名**(sprite/offset/count/aim/速度/flags/命令, 不含帧/基准角/位置)
   分组;
2. 组内按帧排序, greedy 切出**间隔恒定**的链;
3. 链长 ≥ `min_fold`(默认 3) → 折成一条 `conditional`(`type` 必须写
   `"conditional"`, DFU codec 靠 type 分派) + `tick_interval(interval=间隔,
   offset=首帧)`;
4. 基准角逐波**等差**演进 → `angle_offset` 写成 `"phase_tick * k + b"`
   (NumberProvider 简写); 非等差(如随机角)取首波并记 log.debug;
5. 同帧同签名多次发射按"车道"各出一条 fire, 完全相同则合并 `count`;
6. 落单事件用 `compare`(phase_tick == 帧号)门控。

例: 寒符「リンガリングコールド」3600 帧回放 351 次发射 → 折叠后 108 条
action。

## 已知限制

命令式 → 声明式是**近似翻译**, 以下语义边界要清楚:

- **速度比例要手调**: `speed_scale` 默认 0.5 只是参考(引擎0.8≈妖归0.4),
  两引擎空间尺度不同(像素/帧 vs 格/tick), 逐卡微调。
- **变量/循环折叠的边界**: 折叠只认"参数逐波等差"的周期结构。ECL 变量
  驱动的复杂演进(条件分支改弹型/变速波/血量阶段插值)展开后是不同签名,
  折不动, 只能分段平铺或取首波近似 —— 这类卡在输出里 action 数会偏多,
  需要人工再提炼。
- **aimed 的语义漂移**: 妖归 `direction_to_target` 追踪玩家实时位置,
  ECL aimed 是发射瞬间瞄准 —— 玩家不动时等价, 移动时有差。
- **多层环速度**: count2>1 的层间确定性速度插值近似为 `rand(lo,hi)`。
- **随机角演进**: 随机系 pattern 的角度演进不折叠, 取区间中点。
- **mover 近似**: TARGET_ANGLE 旋转段轨迹用 polar/formula 近似(圆近似
  逐帧转向); `acceleration` 是世界坐标, 不适合环形弹自身加减速(故走
  formula); `deceleration` 是指数衰减, 线性减速一律走 formula。
- **激光长度**: ECL 激光延伸到屏幕边, 妖归要声明格数 `length`(默认 100,
  构造参数 `laser_length`); `thickness` 由像素宽 ×0.1 换算。
- **校验器 schema 怪癖**: `tick_interval.offset` 在校验器 schema 里是
  oneOf(integer, numberProvider), 裸 int 同时命中两边必报错 —— 翻译器
  写 NumberExprParser 简写字符串(如 `"300"`)规避, 运行时等价(引擎 DFU
  解析才是权威)。
- **不翻译的**: 道具/音效/清屏/小怪召唤/boss 移动(跳过并 log.debug);
  `spawn_shooter`/`set_spell_health` 等 boss 阶段结构需人工补。

## 实战: 寒符「リンガリングコールド」(一面琪露诺)

```bash
# 1. 翻译(上文命令行例)
# 2. 校验
uv run python scratch_dbg/youkai-danmaku-json/scripts/validate_spell_json.py lingering_cold.json
# → lingering_cold.json: OK
```

产物要点(实测输出):

```json
{
  "id": "youkaishomecoming:ecl_th07_card0",
  "display": { "name": "寒符「リンガリングコールド」" },
  "custom_names": { "phase:main": "寒符「リンガリングコールド」" },
  "phases": {
    "youkaishomecoming:ecl_th07_card0/main": {
      "on_tick": [
        {
          "type": "conditional",
          "condition": { "type": "tick_interval", "interval": 60, "offset": "300" },
          "if_true": [
            {
              "type": "fire_danmaku",
              "bullet": "ball",
              "color": "light_blue",
              "count": 1,
              "speed": 1.75,
              "lifetime": 200,
              "pattern": "line",
              "aim_mode": "direction_to_target"
            }
          ]
        }
      ]
    }
  }
}
```

激光卡对照(四面 大合葬「霊車コンチェルトグロッソ」, ecldata4 sub 135):
`fire_laser` 三段时序 `setup_prepare: 120 / setup_end: 16`, `rotate` mover
约 1.88°/tick, 同样过校验器 OK。

## 测试

- `tests/test_ecl_translate.py`: 通用层(stub VM + 手工构造 ECL 字节流),
  钉 record/translate 模板方法、激光假句柄追踪、错误路径、折叠与激光
  mover 结构; CONTROL 模式钉模式分发、不支持模式的报错、IR 重建
  (回边→loop、条件前跳→if/else、嵌套循环、不可归约兜底)与妖归
  compile_ir 的 repeat/delay/angle 表达式映射;
- `tests/game_test/th07/test_th07_ecl_translate.py`: 真实 th07.dat 回放
  寒符 sub(DIRECT), 断言 trace 非空与 SpellDefinition 结构; CONTROL 模式
  跑寒符 sub 与 天符「天仙鳴動」sub(ecldata2 sub 64), 断言 repeat/delay
  结构(无数据自动 skip)。
