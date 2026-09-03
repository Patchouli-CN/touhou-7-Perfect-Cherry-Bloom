"""th08 表现层 —— 贴着 th08 画的 view 模块(应用壳/贴图渲染/pygame 后端)。

最小可用窗口版(一期): 标题→难度→机体→打一面, GameView(敌机/弹幕/自机/
背景) + HUD(时刻表盘/妖率计/右栏基础行) + BGM/SE。标题主菜单已原作化
(A 期, title_flow/title_view); 难度/机体/Extra 选择已原作化(B2 期,
title_flow.CharacterFlowTh08 + select_view); Music Room 已原作化(C 期第 2 片,
music_flow + music_view); 对话立绘/符卡宣言/结局/Replay/录像 留二期
(见 impl.py 模块 docstring)。

- ``anm_vm``: AnmVmTh08(th08 指令集差集, 对照 th08-ref AnmManager.cpp
  ExecuteScript; 基类是 engine/view/anm_vm.py 的 AnmVm)
- ``title_flow``: 标题系菜单纯逻辑(主菜单 9 项名单/锁定跳过/初始光标;
  机体选择 flow/menuLength 规则/通关标记映射; 无 pygame)
- ``title_view``: 标题画面贴图渲染(title01.anm 菜单 vm + title00.png 背景)
- ``select_view``: 难度/机体选择贴图渲染(select00.png 背景 + title01.anm
  vms[111..135] + 通关标记 145-148)
- ``impl``: GameApp 应用壳(@register_app("th08"); 不 import pygame)
- ``pygame_backend``: PygameTh08Renderer —— 自持, 不进 register_renderer
  ("pygame" 全局名被 th07 占用)
- ``sprite_view`` / ``hud_view``: 战斗画面(GameView)与右栏 HUD

依赖方向: 本包 → games/th08(逻辑层)/games/th07 view 的纯逻辑 screens →
engine(view/render/config/…), 反向禁止。
"""

from .anm_vm import AnmVmTh08
from .impl import GameApp
from .pygame_backend import PygameTh08Renderer

__all__ = ["AnmVmTh08", "GameApp", "PygameTh08Renderer"]
