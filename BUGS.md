
# BUG 报告，必须修复

1. ~~难度选择出界~~ ✅ 已修复（待验证后可删）

- desc: 原版选择界面难度的时候有4个选项，但我们这里实际上6个，虽然界面上看起来四个，但四个选完了没回到 简单，而是出界
- 根因：AI你是不是把ex和ph也当难度了？不行，那是额外关卡，不是难度
- 修复：`GameApp._diff` 光标原本在全名单（6 项，含 Extra/Phantasm）上回绕，
  渲染只画 4 项 → 选中不可见的第 5/6 项出界。改为光标走 `MAIN_DIFFICULTIES`
  （本篇 4 项），数量取自新增 `GameData.main_difficulty_count`（默认 4）；
  Extra/Phantasm 仍走 Extra Start 流程不受影响。
  回归测试：`tests/test_app.py::test_main_difficulty_cursor_wraps_within_four`

2.暂停不会暂停BGM，而且没有还原 ✅ 已修（e38e5cd，代码核实）
- 根因：暂停菜单未接 BGM 暂停/恢复。
- 修复：sound_player 增 pause_music/unpause_music（仅 WAV 音源响应，同原版
  AUDIO_PAUSE 门控 SoundPlayer.cpp:846-868；MIDI 不暂停），impl 暂停/续关菜单
  进出各处接好（AsciiManager.cpp:666/852/999, GameManager.cpp:141）。
- 回归测试：`tests/test_sound.py::test_pause_music_wav_only` 等
3.按B的资源吸取没有了 ✅ 已修（b959843，代码核实）
- 根因：bomb 触发时未调 RemoveAllItems（BombData.cpp 12 处 / Player.cpp:1691）。
- 修复：bomb 事件 EVENT_REMOVE_ALL_ITEMS → items.remove_all_items（全部道具
  转吸附 + (0,-0.5)，ItemManager.cpp:488-504）；主动破结界同接（world.py）。
- 回归测试：`tests/test_items.py`/`tests/test_integration.py`（bomb 吸取段）
4.BGM的播放会突然停止，具体表现为，标题界面standby 2-3分钟，就停了，正常是循环播放 ✅ 已修（e38e5cd，代码核实）
- 根因：WAV BGM 循环靠 _poll_wav_loop 轮询回卷（mixer.music 不支持段内循环，
  到段尾 play(start=intro_seconds)，CWaveFile::ResetFile dsutil.cpp:1071-1112），
  但轮询原本只在 play_frame（对局内）调用——标题界面 WAV 曲播完一遍即停。
- 修复：poll_loop() 提到应用壳主循环每帧调用、与场景无关（impl.py:270）；
  暂停态跳过轮询（pygame 2.6 暂停时 get_busy()=False，防误判回卷）；
  MIDI 路径 play(-1) 无限循环。标题 BGM 装载路径（thbgm.fmt basename 匹配
  th07_01.wav → thbgm.dat PCM）核实无误。
- 回归测试：`tests/test_sound.py::test_poll_loop_public_guarded_by_enabled` 等
- 备注：代码层核实已修；真机待机复测建议做一次（SDL_mixer WAV seek 行为）。
5.道中/boss的出现时只会清空弹幕，资源吸取没有，进入符卡也是一样，只清弹，清弹变成的星点不自动吸取 ✅ 已修（b959843，代码核实）
- 根因：清弹转的弹消点/星点道具出生为下落态，且对话/boss 出现未 RemoveAllItems。
- 修复：spawn 增 state 参数（C++ SpawnItem 第三参，1=出生即吸附），
  清弹转道具全部 state=STATE_ATTRACT（BulletManager.cpp:427/510/581）；
  对话期间 RemoveAllItems（Gui.cpp:759/821）已接。
- 回归测试：`tests/test_items.py`（state=1 吸附段）、`tests/test_integration.py`
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
8.背景闪屏+模糊 ✅ 已修（17db2e7，代码核实+冒烟）
- 修复：d3dx_render/bg3d_view 修正（闪屏模糊）；细节见提交 17db2e7。
- 回归测试：`tests/test_stage_smoke.py`
9.进入符卡的时候背景没有变化 ✅ 已修（17db2e7，代码核实+冒烟）
- 修复：spellcard_view 增 SpellcardBgView（Stage.cpp spellCardState 背景变化），
  经 sprite_view 接入渲染。
- 回归测试：`tests/test_stage_smoke.py`、`tests/test_spellcard_view.py`
10.B在boss战的时候出现极其严重的异常（没伤害，锁定不是boss“你B锁弹幕干啥”等超过5+个bug） ⚠️ 部分修复
- 根因①（锁弹幕不锁 boss）：追踪类炸弹（灵梦A集中 梦想妙珠集 BombData.cpp:390、
  咲夜A集中 :1403）的目标 positionOfLastEnemyHit 被接成了一个击杀/bomb盒命中时
  才刷新的土变量 _last_enemy_hit，而 C++ 该值由 EnemyManager 伤害扫描每帧
  按 boss 优先/|dx| 更新（EnemyManager.cpp:894-938）。结果追踪珠追的是上一个
  被打死的杂鱼/被 bomb 盒蹭到的弹系敌人（弹幕），根本不到 boss → 也没伤害。
- 修复①：_bomb_ctx 改用索敌系统写回的 player.position_of_last_enemy_hit，
  删除 _last_enemy_hit 及全部写入点。
- 根因②（伤害门控缺失）：bomb 伤害盒对敌人没走 C++ CalcDamageToEnemy 的
  canDie && isHittable 门控（EnemyManager.cpp:776-779），不可击目标也掉血。
- 修复②：_apply_bomb_boxes 敌人循环补该门控。
- 回归测试：`tests/test_integration.py::test_bomb_homing_targets_boss_via_targeting`、
  `::test_bomb_damage_box_respects_hittable_gate`
- 未尽：用户描述"超过5+个bug"不可枚举，本次只修了代码比对能坐实的 2 处偏差；
  bomb 盒与子弹伤害分路径结算的已知偏差（world._apply_bomb_boxes docstring）
  保持现状。建议实机复测后补具体条目。
11.森罗结界和森罗结界奖励的提示没了 ✅ 已修复
- 根因：STATUS_BORDER("Supernatural Border!!")/STATUS_BORDER_BONUS("Border Bonus")
  两条横幅的渲染（popup_view）早就有，但逻辑层从未触发——结界激活
  （Player.cpp:2138）与自然破（Player.cpp:2013）两处 ShowStatusPopup 调用漏接。
- 修复：world._tick_border 激活时 show_status_popup(0, STATUS_BORDER)，
  自然破时 show_status_popup(score, STATUS_BORDER_BONUS)。
- 回归测试：`tests/test_integration.py::test_border_banners`
12.full power mode能消弹，但是不是并不是“仅限于消弹”，我多余P点变成的樱点和full power mode的提示呢 ✅ 已修（b959843，代码核实）
- 根因：满火力后 P 道具未转樱点、无 "Full Power Mode!" 横幅、符卡中误清弹。
- 修复：满火力 POWER_SMALL/BIG 生成即转 CHERRY（ItemManager::SpawnItem）；
  触达满火力 ShowStatusPopup(0,1)（items.reached_full_power → world → globals）；
  符卡中不清弹（ItemManager.cpp:227/345 !spellcardInfo.isActive）；
  满火力 P 按 FULL_POWER_SCORE_BONUS 表给分并弹字。
- 回归测试：`tests/test_items.py`、`tests/test_globals.py`
13.森罗结界的那个樱花圈材质没了 ✅ 已修（17db2e7，代码核实+冒烟）
- 修复：anm_fx/sprite_bank/bomb_view 补结界樱花圈贴图链；细节见提交 17db2e7。
- 回归测试：`tests/test_stage_smoke.py`
14.retry和quit to title的二次确认没了（而且原版这里并不能保存rep） ✅ 已修（e38e5cd，代码核实）
- 修复：暂停菜单 Retry/Quit to Title 进二次确认态（只有 Yes/No，默认 No，
  原版此处不能 Save Replay，AsciiManager.cpp PauseMenu case 5-8）；
  Esc 任意态直接关菜单回游戏（:448-460）。
- 回归测试：`tests/test_app.py`（暂停确认段）
15.score和hiscore的问题，具体表现为score破记录的时候，hiscore不同步 ✅ 已修复
- 根因：逻辑层 globals 没有 highScore 概念，HUD 每帧直查 score_store 的榜上旧纪录，
  本局破纪录时 HISCORE 数字不会跟着涨。
- 修复：原版 GameManager 开局从 score.dat 载入 highScore（GameManager.cpp:453-455），
  之后每帧让 highScore 跟随显示分 guiScore（:265-268）。ZunGlobals 增
  high_score/high_score_num_continues 字段与 tick_high_score()，world 开局从
  store 载入（ScoreStore 增 high_score_continues），每帧 tick_gui_score 后同步；
  hud_view 改读 globals.high_score（缺省回落 store 直查）。
- 回归测试：`tests/test_globals.py::test_high_score_follows_gui_score` 等 3 例
16.收点的得点量数字消失了（就是显示的那个一〇〇〇〇〇什么的） ✅ 已修（b959843，代码核实）
- 修复：collect 透出 r.popups（数值/ARGB/槽位，CreatePopup1/2），
  globals.add_popup 环形缓冲（720/3 槽），popup_view 用 ascii.anm 贴字渲染
  （含 PowerUp 特殊字形/距离透明度，AsciiManager.cpp:1052-1129）。
- 回归测试：`tests/test_items.py`（popups 段）、`tests/test_globals.py`
17.死了不能在score ranking界面保存rep ✅ 已修（e38e5cd，代码核实）
- 修复：结算画面名字输入/未入榜确认后 → Save Replay? 询问（ResultScreen
  state 16→17→11；续关过不能存，ResultScreen.cpp:1364-1376），
  render_result 增 replay_save 覆盖层协议。
- 回归测试：`tests/test_app.py`（结算存 rep 段）、`tests/test_replay_musicroom.py`
18. and more（正在收集）