"""渲染后端协议 —— 换渲染后端(如 ModernGL) = 注册一个新实现, 不动游戏代码。

分层约定:

- 本模块是纯协议模块: 只定义 ``Renderer`` 协议与后端无关的数据形态
  (``FrameInput``/``EndingFrame``), **不 import pygame**, 不 import 任何后端
  实现; 后端类经 ``touhou.registry`` 的 ``@register_renderer(name)`` 登记,
  ``GameApp(..., renderer="pygame")`` 按名解析(默认 "pygame", 实现见
  ``games/th07/view/pygame_backend.py``)。
- ``Renderer`` 覆盖窗口版应用(GameApp)的全部渲染职责: 窗口生命周期
  (open/resize/present/close)、帧输入采集(poll_input)、各场景渲染
  (标题/选人/设置/游戏帧/暂停与续关覆盖层/结算/结局)、菜单 SE。
- 音频(BGM/SE 播放)不进本协议: SoundPlayer 是独立的 pygame.mixer 子系统,
  由 GameApp 持有, 与渲染后端正交(静音容错已内建)。

关键决策(写死, 新后端照此实现):

- **render_game 拿 game 对象**(满足 ``touhou.types.GameEngine`` 协议的对局
  实例), 不传快照。现有渲染路径本就直读 game 字段(msg_vm/frame_shakes/
  globals…), 且每帧全场实体快照装箱开销无谓; 后端按 GameEngine 面 +
  getattr 可选位读取, 与 api.Game 门面同一套约定。
- **输入采集随渲染后端走**(pygame 事件/键码本是后端细节): poll_input 返回
  后端无关的 ``FrameInput`` —— 菜单动作序列(MenuAction, 来自
  games/th07/view/screens.py 纯逻辑)、对话推进/Esc 沿、按住的动作名集合
  (left/right/up/down/focus/shoot/bomb/skip)、KeyConfig 捕获的键名。
  键名 → 键码 → 动作的映射(set_keymap)是后端内部实现细节。
- 菜单 flow/cursor 等状态对象(OptionFlow/MusicRoomFlow…, 均 pygame-free)
  由 GameApp 持有并传入渲染方法; 后端只读它们取光标/文本, 不推进状态。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Mapping, Protocol, Sequence

import msgspec

if TYPE_CHECKING:
    # 菜单流类型(OptionFlow 等)定义在 games/th07/view/screens.py —— 纯逻辑
    # (pygame-free), 此处仅为类型注解引用: TYPE_CHECKING 下 mypy 可见,
    # 运行时不产生 engine → games 依赖(单向依赖: 引擎 ←—— 作品 保持不破)。
    from ...games.th07.view.screens import (
        KeyConfigFlow,
        MenuAction,
        MusicRoomFlow,
        NameEntryFlow,
        OptionFlow,
        PlayerDataFlow,
        ReplayFlow,
        Screen,
    )
    from ...types import GameEngine
    from ..ending import EndingData
    from ..score_store import ScoreStore

__all__ = ["EndingFrame", "FrameInput", "Renderer"]

#: 游戏内动作名集合(config.keymap 的键; poll_input 的 held 即这些名的子集)。
ACTION_NAMES = ("left", "right", "up", "down", "focus", "shoot", "bomb", "skip")


class FrameInput(msgspec.Struct, frozen=True):
    """渲染后端采集到的一帧输入(后端无关形态; 全字段默认值 = 空帧)。

    menu_actions: 本帧新按下的菜单动作(``MenuAction``, 按事件顺序);
    advance: 对话推进(shoot 键新按下); esc: Esc 新按下(游戏内暂停开关);
    captured_key: KeyConfig "按新键"捕获到的键名(此时菜单动作被吃掉);
    held: 当前按住的动作名(见 ACTION_NAMES)。
    """

    quit: bool = False
    menu_actions: tuple["MenuAction", ...] = ()
    advance: bool = False
    esc: bool = False
    captured_key: str | None = None
    held: frozenset[str] = frozenset()


class EndingFrame(msgspec.Struct, frozen=True):
    """render_ending 的返回: 结局播放状态 + 本帧脚本内音乐事件。

    music: ("play", 曲名)/("fadeout", 秒) 序列(.end 脚本 @m/@M 指令),
    由 GameApp 消费给 SoundPlayer; finished: 脚本播完(@z) → 进总结算。
    """

    finished: bool = False
    music: tuple[tuple, ...] = ()


class Renderer(Protocol):
    """渲染后端协议 —— GameApp(应用壳)只面向本协议编程, 不认具体后端。

    实现约定(现有 PygameRenderer 语义, 新后端须保持一致):
    构造器签名 ``(data_path=None, **kw)``(资源包路径; 懒加载容错 ——
    单个画面渲染失败不拖垮应用, 降级为底色填充); open 幂等;
    resize/present 在无窗口环境(headless 测试)安全空调用。
    """

    # ---- 窗口生命周期 / 帧调度 ----
    def open(self, *, scale: int) -> None:
        """平台初始化 + 建窗口(标题场景尺寸 × scale); 幂等。"""
        ...

    def close(self) -> None:
        """收窗口/平台资源(主循环退出时调用)。"""
        ...

    def resize(self, screen: "Screen", scale: int) -> None:
        """按场景尺寸 × 缩放重设窗口(无窗口时跳过)。"""
        ...

    def present(self) -> None:
        """上屏 + 帧调度(60fps)。"""
        ...

    # ---- 输入采集 ----
    def set_keymap(self, keymap: Mapping[str, Sequence[str]]) -> None:
        """按 config.keymap(动作名 → 键名列表)重建输入映射。"""
        ...

    def poll_input(self, *, capturing: bool = False) -> FrameInput:
        """采集一帧输入。capturing=True(KeyConfig 捕获中)时键进
        captured_key, 不进菜单动作。"""
        ...

    # ---- 菜单系场景 ----
    def render_title(self, cursor: int, frame: int, *,
                     show_unimplemented: bool = False) -> None:
        """标题主菜单(frame 驱动花瓣动画)。"""
        ...

    def render_difficulty(self, cursor: int) -> None: ...

    def render_character(self, cursor: int) -> None: ...

    def render_practice_stage(self, cursor: int, max_stage: int, *,
                              difficulty: str, character: str) -> None: ...

    def render_extra(self, cursor: int) -> None: ...

    def render_option(self, flow: "OptionFlow") -> None: ...

    def render_keyconfig(self, flow: "KeyConfigFlow") -> None: ...

    def render_player_data(self, flow: "PlayerDataFlow",
                           store: "ScoreStore | None", frame: int) -> None: ...

    def render_music_room(self, flow: "MusicRoomFlow", frame: int) -> None: ...

    def render_replay_menu(self, flow: "ReplayFlow", frame: int) -> None: ...

    # ---- 对局场景(game 对象 = GameEngine 协议, 见模块 docstring 决策) ----
    def begin_game(self, game: "GameEngine", *, character: int) -> None:
        """开局/重开: 按机体与当前关建本局渲染资源(贴图视图等, 容错降级)。"""
        ...

    def render_game(self, game: "GameEngine") -> None:
        """渲染一帧对局(游戏区 + HUD + 对话/过关面板/震屏合成)。"""
        ...

    def render_pause(self, game: "GameEngine", cursor: int, *,
                     hint: str | None = None) -> None:
        """暂停: 冻结画面(render_game 同图) + 半透明暂停面板 + 瞬态提示。"""
        ...

    def render_continue(self, game: "GameEngine", cursor: int,
                        retries_left: int) -> None:
        """GameOver 续关菜单(冻结画面 + Continue? Yes/No 覆盖层)。"""
        ...

    # ---- 结算 / 结局 ----
    def render_result(self, result: dict, frame: int, *,
                      store: "ScoreStore | None",
                      name_entry: "NameEntryFlow | None") -> None: ...

    def render_ending(self, ending: "EndingData", frame: int) -> EndingFrame:
        """渲染一帧结局画面, 返回播放状态与脚本内音乐事件。"""
        ...

    # ---- 菜单 SE(现状走标题画面资源表, 后端自带静音容错) ----
    def play_menu_se(self, key: str) -> None:
        """菜单音效("select"/"ok"/"cancel"); 未加载/无声卡静音跳过。"""
        ...

# 默认后端 PygameRenderer 满足本协议的静态断言在 touhou/api.py
# (协议在 engine, 实现在 games/th07/view —— engine 不反向 import games,
# 而 games.th07.view.* 整体在 mypy 豁免区, 断言须落在被检查的门面模块)。

