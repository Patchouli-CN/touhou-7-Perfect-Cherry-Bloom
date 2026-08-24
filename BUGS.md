
# BUG 报告【增量】

1. fps显示和cherry点数叠加了，见 ./bug_fps显示.png 【已修 touhou/games/th07/view/hud_view.py：FPS 从 (136,468) 挪到 (190,466)——樱点下行两组数字最坏（各 7 位）画到 ~182px，取 190 避开；y=466 小字号不越 480 下沿】

2. bomb释放完了后，灵梦一直处在虚影状态，直到下次撞弹（疑似bug，得看是刻意的游戏设计还是bug）【已修 touhou/games/th07/world.py：确认是 bug 非设计——C++ 机体 bombCalc 每帧无条件置 playerState=INVULNERABLE（BombData.cpp:182/378/601/…），UpdateState 据此逐帧递减 invulnerabilityTimer、归零回 ALIVE（Player.cpp:1916-1933）；逻辑层漏了这步导致首帧设的无敌计时在 ALIVE 态冻结。补上后 bomb 结束剩 ~60 帧（无敌=duration+60）倒数归零，闪烁按原版时序自然结束；决死B/结界破/撞弹复活无敌路径均已回归验证】

3. 每次进入开局+进入下一面+符卡+bomb什么的都会卡一小下，体验不好 【已修 多处，定位与修法：打点实测（scratch_dbg/profile_stutter.py）主因是 LZSS 解压纯 Python 逐位实现仅 ~2MB/s（face_rm00 首发 bomb 时解压 946ms、ascii.anm 被 4 个 SpriteBank 实例各解压一次 ~230ms/次、换关帧集中解压 3.1s）——① schema/archive.py：LZSS 重写（去环形字典改输出流自复制+字面量 8 连批取，全 197 条目逐字节校验一致，~1.9x）+ GameArchive 解压结果进程级共享缓存；② schema/anm.py：fmt 2/3/5 纹理解码 numpy 向量化（逐位等价校验，~150ms→~7ms）+ parse_cached 进程级共享；③ 预载：关卡加载随载 bomb cutin/符卡立绘/eff（games/th07/view/sprite_view.py _ensure_stage）、结算面板静态期每帧一项预载下一关含 3D 场景预建（_preload_next_stage）、标题菜单空转每帧一项预热（games/th07/view/impl.py _warmup_step）；④ 慢加载 DEBUG 计时日志常驻（sprite_bank/bg3d_view/pygame_backend）。效果：首发 bomb 1228ms→30ms、首次符卡 ~400ms→15ms、换关 3121ms→结算面板摊帧吸收（无结算阶段的强制跳关兜底 1190ms）、开局 _start_game+首帧 4.4s→1.7s（菜单停留后趋近 0，SE 加载 ~1s 在启动时一次性）。残留：平稳期偶发 20-50ms 帧（bg3d 软光栅/出怪 burst/GC），与本次三处事件性卡顿无关，未动】