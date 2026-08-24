
# 新bug

1. 4面boss血条显示问题，具体表现为，血量程序里是再掉，但血条显示一直满血
【已修 touhou/games/th07/world.py，C++ 血条每帧只取 bossId==0 敌人的 life/maxLife(EnemyManager.cpp:1066-1068)；4面三姐妹卫星机占槽1..6(life=999999，承伤转嫁槽0主体)，移植把 HUD 绑到最后一个 SET_BOSS 的卫星机 → 恒满。_tick_boss_ecl 显示血量改跟 ecl_world.bosses[0] 主体，槽0缺位回退旧绑定；回归测试 tests/test_stage_ecl.py::test_stage4_healthbar_tracks_boss_slot0】

2. 每次对话空隙可能闪出之前关卡的立绘，比如 爱丽丝关卡的时候，和爱丽丝对话，轮到主角说话的时候，爱丽丝测立绘变成第一关的boss
【已修 touhou/games/th07/view/dialog_view.py，非说话方压暗立绘的模块级 _dim_cache 键 (side,face_idx,w,h) 不含关卡/角色维度，而全部脸图都是 126×510 → 跨关必撞，自机说话(对侧压暗)时闪出上一关暗化立绘。压暗缓存收进 _FaceBook 按书隔离(get_dim)；与 parse_cached 无关(其键为文件字节，安全)。回归测试 tests/test_dialog_view.py】

3. th07的point点数偶发出现上限200，正常是125
【已修 touhou/games/th07/world.py，分母=下次点道具奖残所需累计数(50/125/200/300…，ItemManager.cpp:289-315)；收集循环的 ctx 是同帧快照，同帧收≥2个点道具过阈值时按旧基线重复奖残，extends 1→2 分母 125 跳 200(还多送1残)。每收完一个道具把 point_items_collected_for_extend/extends_from_point_items 刷回 ctx；回归测试 tests/test_integration.py::test_same_tick_point_items_extend_once】