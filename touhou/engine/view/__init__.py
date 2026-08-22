"""通用渲染机制层 —— 作品无关的 pygame 渲染基建。

只留数据驱动、无 th07 专属数值/布局的模块:

- ``anm_vm``: anm 脚本 VM(指令解释器, 对照 AnmManager.cpp::ExecuteScript)
- ``anm_fx``: 2D VM 宿主(Vm2d)/变换缓存(TransformCache)/特效层(EffectLayer;
  EFFECT_TABLE 是 th07 的 g_EffectMapping 子集数值, 作为数据表随层保留,
  新作品复用时需自带映射表)
- ``bg3d_view``: .std 数据驱动的 3D 背景软件渲染(StageScene)
- ``sprite_bank``: anm sprite 缓存(SpriteBank; 链式偏移表 + 旋转变换缓存)
- ``sound_player``: BGM/SE 播放(pygame.mixer; 与渲染后端正交, GameApp 持有)
- ``shake_view``: 震屏整帧位移(ScreenShake; 事件参数由游戏帧快照驱动)

th07 贴着画的内容(GameApp 应用壳、菜单场景、PygameRenderer 后端、GameView
战斗画面等)在 ``games/th07/view/``。

边界约定: 本包是 engine 内唯一允许 import pygame 的位置; engine 其余模块与
schema 包保持纯逻辑, 不碰 pygame/渲染。本包不 import 任何 games.* 内容。
"""
