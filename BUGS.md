
# BUG 报告，必须修复

1. ~~难度选择出界~~ ✅ 已修复（待验证后可删）

- desc: 原版选择界面难度的时候有4个选项，但我们这里实际上6个，虽然界面上看起来四个，但四个选完了没回到 简单，而是出界
- 根因：AI你是不是把ex和ph也当难度了？不行，那是额外关卡，不是难度
- 修复：`GameApp._diff` 光标原本在全名单（6 项，含 Extra/Phantasm）上回绕，
  渲染只画 4 项 → 选中不可见的第 5/6 项出界。改为光标走 `MAIN_DIFFICULTIES`
  （本篇 4 项），数量取自新增 `GameData.main_difficulty_count`（默认 4）；
  Extra/Phantasm 仍走 Extra Start 流程不受影响。
  回归测试：`tests/test_app.py::test_main_difficulty_cursor_wraps_within_four`

2.暂停不会暂停BGM，而且没有还原
3.按B的资源吸取没有了
4.BGM的播放会突然停止，具体表现为，标题界面standby 2-3分钟，就停了，正常是循环播放
5.道中/boss的出现时只会清空弹幕，资源吸取没有，进入符卡也是一样，只清弹，清弹变成的星点不自动吸取
6.我符卡奖励和普通bonus奖励哪去了？ ✅ 已修复
- 根因：捕获分/过关 bonus 的"分数"其实在入账，但 ① 符卡结束/boss 击坠的清弹累计分
  （BulletManager::DespawnBullets 的 2000 起 +20/弹、8000 封顶，接 RemoveAllEnemies
  2000 起 +30/敌）完全没算没入账；② "Spell Card Bonus!" 与 "BONUS %8d" 两条横幅
  （Gui.cpp ShowSpellcardBonus:98 / ShowBonusScore:78）从未实现。
- 修复：globals 增 bonus_score/spellcard_bonus 横幅状态（250/280 帧生命周期）；
  world._despawn_bullets_bonus 按 BulletManager.cpp:486-553 逐弹弹字+累计分；
  _apply_spellcard_end 与 _kill_reward（boss 击坠非符卡中，EnemyManager.cpp:1004-1011）
  把清弹+清敌累计分 AddScore 并弹 BONUS 横幅；捕获时弹 Spell Card Bonus 横幅；
  ecl_host.remove_all_enemies 补逐敌 CreatePopup1 弹字；popup_view 画两条横幅。
- 回归测试：`tests/test_result_flow.py::test_spellcard_capture_bonus_score_and_banners`、
  `tests/test_globals.py::test_bonus_banners_expire`
7.~~boss和道中的弹幕明显不是原版弹幕~~ ✅ 引擎层已查证修复（实测复测后可删）
- 查证：对照 th07/src/th07 全链路比对，发现 4 处真实偏差并修复：
  ① screenClearTime 机制缺失（清屏后 10 帧内新弹/激光应压制，BulletManager.cpp:480/553）
  ② TARGET_VEL 激活时未烘 framerateMultiplier（减速场中加速度为原版 4 倍，:347-349）
  ③ 出界宽限残余期提前杀弹（:957-975）④ 命令队列空槽应截断而非跳过（:318-320）
- 难度/rank 修正、弹型表、aim 分布、RNG 消耗序均核对无误。
- 代理判断：引擎层偏差多为边角，用户感知的"明显不对"可能还来自判定手感/伴随行为，
  建议结合 #3#5#10 等修复后实测复测。回归测试：test_bullets/test_exins 共 +7 例
8.背景闪屏+模糊
9.进入符卡的时候背景没有变化
10.B在boss战的时候出现极其严重的异常（没伤害，锁定不是boss“你B锁弹幕干啥”等超过5+个bug）
11.森罗结界和森罗结界奖励的提示没了
12.full power mode能消弹，但是不是并不是“仅限于消弹”，我多余P点变成的樱点和full power mode的提示呢
13.森罗结界的那个樱花圈材质没了
14.retry和quit to title的二次确认没了（而且原版这里并不能保存rep）
15.score和hiscore的问题，具体表现为score破记录的时候，hiscore不同步 ✅ 已修复
- 根因：逻辑层 globals 没有 highScore 概念，HUD 每帧直查 score_store 的榜上旧纪录，
  本局破纪录时 HISCORE 数字不会跟着涨。
- 修复：原版 GameManager 开局从 score.dat 载入 highScore（GameManager.cpp:453-455），
  之后每帧让 highScore 跟随显示分 guiScore（:265-268）。ZunGlobals 增
  high_score/high_score_num_continues 字段与 tick_high_score()，world 开局从
  store 载入（ScoreStore 增 high_score_continues），每帧 tick_gui_score 后同步；
  hud_view 改读 globals.high_score（缺省回落 store 直查）。
- 回归测试：`tests/test_globals.py::test_high_score_follows_gui_score` 等 3 例
16.收点的得点量数字消失了（就是显示的那个一〇〇〇〇〇什么的）
17.死了不能在score ranking界面保存rep
18. and more（正在收集）