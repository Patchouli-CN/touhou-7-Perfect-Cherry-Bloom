"""th07 表现层 —— 贴着 th07 画的 view 模块(应用壳/场景/贴图渲染/pygame 后端)。

从 engine/view 迁入(纯移动, 零行为改动): 通用渲染机制(anm 脚本 VM、特效层、
.std 3D 背景、SoundPlayer、SpriteBank、震屏)留在 engine/view/; 本包收
th07 专属部分 —— 菜单流(原版 8 项)、窗口布局常量(640x480 + 384x448 游戏区)、
ANM_OFFSET/特效 gid 等 th07 数值, 以及默认渲染后端 PygameRenderer。

- ``impl``: GameApp 应用壳(场景状态机/游戏流程; 不 import pygame,
  渲染与输入采集委托 Renderer 后端 —— 协议见 engine/render/__init__.py)
- ``pygame_backend``: 默认渲染后端 PygameRenderer(import 本包即经
  ``@register_renderer("pygame")`` 登记到 registry)
- ``sprite_view`` / ``hud_view``: 战斗画面(GameView)与右栏 HUD
- ``popup_view``: 得分弹字与状态横幅(AsciiManager::DrawPopups / Gui 的
  statusPopup 段; 逻辑侧在 games/th07/globals.py 的 popups/status_popup)
- ``title_view`` / ``select_view`` / ``option_view`` / ``result_view`` /
  ``stage_results_view`` / ``stage_title_view`` / ``ending_view`` /
  ``dialog_view`` / ``continue_view`` / ``playerdata_view`` /
  ``musicroom_view`` / ``replay_view``: 标题、选人、设置、结算、过关面板、
  关卡标题、结局、对话、续关、Player Data、Music Room、录像选择画面
- ``bomb_view`` / ``spellcard_view``: 12 套 bomb 视觉、boss 符卡宣言
- ``screens``: 菜单状态纯逻辑(不含 pygame)

依赖方向: 本包 → engine(view/render/config/…), 反向禁止。
GameApp 经 ``@register_app("th07")`` 登记到 registry(TouhouWorld.run()
非 headless 分支按作品名解析); PygameRenderer 满足 Renderer 协议的
符合性由运行时测试兜底(tests/test_registry.py —— 本包整体在 mypy
豁免区, engine/apis 均不 import 本包, 静态断言无处安放)。
"""

from .impl import GameApp
from .pygame_backend import PygameRenderer, _load_font

__all__ = ["GameApp", "PygameRenderer", "_load_font"]
