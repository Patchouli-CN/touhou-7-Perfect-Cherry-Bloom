"""渲染 / 播放层(views + 菜单逻辑 + pygame 渲染后端)。

收录全部依赖 pygame 的表现层模块:

- ``impl``: GameApp 应用壳(场景状态机/游戏流程; 本模块本身不 import pygame,
  渲染与输入采集委托 Renderer 后端 —— 协议见 engine/render/__init__.py)
- ``pygame_backend``: 默认渲染后端 PygameRenderer(import 本包即经
  ``@register_renderer("pygame")`` 登记到 registry)
- ``sprite_view`` / ``hud_view``: 游戏画面与 HUD 渲染
- ``shake_view``: 震屏整帧位移 (BombEffects type=1 的 view 侧衰减)
- ``title_view`` / ``select_view`` / ``result_view`` / ``stage_results_view``
  / ``ending_view`` / ``dialog_view`` / ``continue_view``: 标题、选人、结算、
  结局、对话、续关菜单画面
- ``sound_player``: BGM/SE 播放(pygame.mixer; 与渲染后端正交, GameApp 持有)
- ``screens``: 菜单状态纯逻辑(不含 pygame, 被渲染层使用, 一并归入)

边界约定: 本包是 engine 内唯一允许 import pygame 的位置(impl.py 例外 ——
应用壳只面向 Renderer 协议编程); engine 其余模块与 schema 包保持纯逻辑,
不碰 pygame/渲染。
"""

from .impl import GameApp
from .pygame_backend import PygameRenderer, _load_font

__all__ = ["GameApp", "PygameRenderer", "_load_font"]
