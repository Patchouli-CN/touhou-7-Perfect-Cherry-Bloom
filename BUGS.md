
# BUG 报告【增量】

1. ex面变成先选人再选难度了，顺序换一下
【已修 35d1776 / touhou/games/th07/view/impl.py：Extra Start 流改为 EXTRA_LEVEL(选 Extra/Phantasm) → CHARACTER(选机体) → 进关，与本篇"先难度后选人"一致；BACK 逐级回退；tests/test_app.py 两条入口流测试同步改写】

2. HiScore显示末尾少了个0，和Score显示没对齐
【已修 35d1776 / touhou/games/th07/view/hud_view.py：HiScore 行补上 highScoreNumContinues 续关数后缀（Gui.cpp:1572-1576，同行 +112 与 Score 的 numRetries 后缀同位），8 位分 + 后缀与 Score 行对齐】

3. 屏幕下方应该要有帧率显示，比如 60.00 fps（原版这个再屏幕下方，但这个我建议放到左边HUD）
【已修 35d1776 / touhou/games/th07/view/hud_view.py + pygame_backend.py：HudView.render_fps 按原版 "%.02ffps" 格式（Supervisor.cpp:877）画在左下樱点槽右侧 (136,468)（原版在 (512,464)）；FPS 平滑值取 pygame Clock.get_fps，由渲染后端在 render_game 末尾喂入】

4. 开b的时候同时森罗结界就会发生 ./bug_樱花结界.jpg 的情况
【已修 35d1776 / touhou/games/th07/view/bomb_view.py：根因是无敌红环(_tick_ring)每帧读 player.invulnerability_timer 现值算缩放——逻辑层 ALIVE 时该计时不递减，下一发 bomb 的无敌时间短于残留值时 f=1-remaining/frames 变负，缩放被外推成满屏巨环。修法：环的寿命/缩放改为自驱动倒计时（出生帧定格 bomb.invulnerability_timer 帧，同 C++ SpawnBombInvulnEffect 的 scaleInterp 定格语义，BombData.cpp:71-76 + Player.cpp:1923-1929），不再读外部计时】

5. 道中打杂鱼的时候，灵梦追踪弹追超出游戏区的杂鱼了，修这个的时候同时得看看其他人物的技能有没有类似的bug
【已修 35d1776 / touhou/engine/enemies.py：shoot_hits 里 targeting.update 加 IsInBounds 门控（GameManager.cpp:42-65 口径，按敌人判定盒），只锁定版内敌人；版外敌人照常结算伤害。C++ 靠 isInBounds/OOB despawn（EnemyManager.cpp:701-731）让飞出版面的敌人退场，本移植未实现该 despawn，故在索敌入口筛选。灵梦B 追踪（UPDATE_HOMING）、咲夜A 索敌（sakuyaTargetPosition）、灵梦A 集/咲夜A 集炸弹的追踪目标全都来自同一个 Targeting，一并覆盖；tests/test_enemies.py 加两条回归（版底飞出/版顶未进场不锁定）】

6. 当Bomb结束后，有时bomb特效外环不会消失，看 ./bug_Bomb外环不消失.jpg 就懂了
【已修 35d1776 / touhou/games/th07/view/bomb_view.py：与 #4 同根因——环的存活条件是 player.invulnerability_timer>0，而逻辑层 ALIVE 时该计时不递减（只在 INVULNERABLE 态递减），从 ALIVE 开的 bomb 其环永不消失（"有时"= bomb 开在无敌期内时计时照常递减所以正常）。自驱动倒计时后固定活 bomb.invulnerability_timer 帧即消，与 C++ 无敌结束 inUseFlag=0（Player.cpp:1923-1929）一致；test_bomb_view.py 14 条残留回归全绿】

7. 没有决死B，资料在这：https://en.touhouwiki.net/wiki/Deathbombing，总之就是 在撞的一瞬间（听到biu的炸残声）的特定帧数内按B，可以用B换残机的机制
【已修 35d1776 / touhou/games/th07/world.py：_try_bomb 成功时若玩家在 DEAD 态（死亡窗口 = respawnTimer 倒数，灵梦15/魔理沙8/咲夜6 帧，实测自 .sht initialRespawnTimer；try_start_bomb 本就以 respawn_timer!=0 为门槛），把 player.state 翻为 INVULNERABLE —— 对应 C++ 各 *Calc 每帧 playerState=INVULNERABLE（BombData.cpp:182/378/601/…），UpdateDeath（Player.cpp:1764-1779）因此不再倒数结算，残机不扣、只消耗一枚 bomb；tests/test_integration.py::test_deathbomb_cancels_miss 钉住】
