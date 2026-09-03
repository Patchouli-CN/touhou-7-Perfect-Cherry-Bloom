"""config.json 设置持久化 —— 对照 MainMenu.cpp OnUpdateOptionsMenu / Supervisor.cfg。

原版 Option 9 项 (MainMenu.cpp:503-848, 说明文字 g_OptionsStrings:132):
  Player(初始残机 lifeCount 0-4, 默认 2 = 3 架) / Graphic(16/32Bit) /
  BGM 再生方法(WAV/MIDI/OFF) / Sound(SE 开关) / Mode(窗口/全屏) /
  SlowMode / Reset / KeyConfig / Quit。
本期接其中 5 项: BGM 音量(0-100) / SE 音量(0-100) / 音源(WAV/MIDI) /
窗口缩放(1-3) / 初始残机数(2-7, 上限随 th08 Option 档位解锁扩过,
th07 Option UI 仍按 2-5); KeyConfig 以 keymap 段落地(编辑界面在
games/th07/view, 对照 MainMenu.cpp OnUpdateKeyConfig)。

读写容错同 score_store: 文件缺失/损坏/字段非法一律回退默认值, 不抛异常
(对照 OpenScore 的 RECREATE_SCORE 分支)。落盘原子写(tmp + replace)。
"""

from __future__ import annotations

import json
import msgspec
from pathlib import Path

CONFIG_JSON_VERSION = 1
BGM_SOURCES = ("wav", "midi")
VOLUME_MIN, VOLUME_MAX = 0, 100
SCALE_MIN, SCALE_MAX = 1, 3
# 初始残机 2-7: 上限 7 是 th08 Option 的档位(th08-ref TitleScreen.cpp:826-844
# lifeCount 0..6, 高档位按总游戏次数解锁, 见 games/th08 Option 画面);
# th07 的 Option UI 仍按本篇口径 2-5 调值(games/th07/view/screens.py 本地上限)。
LIVES_MIN, LIVES_MAX = 2, 7

# ---- 键位映射(KeyConfig) ----
# 每个动作一组 pygame 键名(pygame.key.name 的格式), 多键 = 任一生效。
# 小键盘键("[0]"/"[1]")是中文 IME 吞字母键时的备用(实锤过), 默认保留。
# 默认值 = 改造前的硬编码键位(原 engine/view/view.py, 现 games/th07/view/impl.py)。
KEYMAP_ACTIONS = ("shoot", "bomb", "focus", "skip", "up", "down", "left", "right")
DEFAULT_KEYMAP: dict[str, list[str]] = {
    "shoot": ["z", "[0]"],  # Z / 小键盘0(IME 备用)
    "bomb": ["x", "[1]", "j"],  # X / 小键盘1 / J
    "focus": ["left shift", "right shift"],
    "skip": ["left ctrl", "right ctrl"],  # 对话快进
    "up": ["up", "w"],
    "down": ["down", "s"],
    "left": ["left", "a"],
    "right": ["right", "d"],
}
# Esc 不许当动作键(防锁死: Esc 固定承担暂停/菜单返回)
_FORBIDDEN_KEYS = ("escape",)


def _default_keymap() -> dict[str, list[str]]:
    return {a: list(keys) for a, keys in DEFAULT_KEYMAP.items()}


# exe 同目录语义 → 仓库根(config.py 在 touhou/engine/ 下, 上两级 = 仓库根)
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config.json"


def _clamp_int(v, lo: int, hi: int, default: int) -> int:
    """int 且非 bool → 截到 [lo, hi]; 否则回退 default。"""
    if isinstance(v, int) and not isinstance(v, bool):
        return max(lo, min(hi, v))
    return default


def _parse_keymap(data, fallback: dict) -> dict:
    """keymap 段容错解析: 逐动作校验, 坏的动作回退该动作默认值。

    合法值 = 非空字符串列表; 滤掉空串/非字符串/Esc(防锁死);
    过滤后为空 → 该动作回退默认。未知动作忽略, 缺失动作用默认。
    """
    out = (
        _default_keymap()
        if not isinstance(fallback, dict)
        else {a: list(fallback.get(a, DEFAULT_KEYMAP[a])) for a in KEYMAP_ACTIONS}
    )
    if not isinstance(data, dict):
        return out
    for action in KEYMAP_ACTIONS:
        v = data.get(action)
        if not isinstance(v, list):
            continue
        keys = []
        for k in v:
            if isinstance(k, str) and k and k not in _FORBIDDEN_KEYS and k not in keys:
                keys.append(k)
        if keys:
            out[action] = keys
    return out


class GameConfig(msgspec.Struct):
    """游戏设置。纯逻辑, 不依赖 pygame。"""

    bgm_volume: int = 100  # 0-100
    se_volume: int = 100  # 0-100
    bgm_source: str = "wav"  # "wav"(thbgm.dat 优先) / "midi"(强制 MIDI)
    window_scale: int = 2  # 窗口缩放倍率 1-3
    initial_lives: int = 3  # 初始残机数 2-7 (原版默认 3; 上限见 LIVES_MAX 注)
    # 键位映射: 动作 → pygame 键名列表(多键任一生效), 见模块顶部 KEYMAP_ACTIONS
    keymap: dict = msgspec.field(default_factory=_default_keymap)

    # ---- 键位操作 ----
    def reset_keymap(self) -> None:
        """恢复默认键位(KeyConfig 页的"恢复默认")。"""
        self.keymap = _default_keymap()

    def set_keymap_primary(self, action: str, key_name: str) -> bool:
        """把 key_name 设为 action 的主键(保留其余备用键, 去重)。

        Esc 不允许(防锁死); 未知动作/非法键名忽略。返回是否改动。
        """
        if (
            action not in KEYMAP_ACTIONS
            or not isinstance(key_name, str)
            or not key_name
            or key_name in _FORBIDDEN_KEYS
        ):
            return False
        old = self.keymap.get(action, [])
        self.keymap[action] = [key_name] + [k for k in old if k != key_name]
        return True

    # ---- JSON 读写(容错) ----
    def to_dict(self) -> dict:
        return {
            "version": CONFIG_JSON_VERSION,
            "bgm_volume": self.bgm_volume,
            "se_volume": self.se_volume,
            "bgm_source": self.bgm_source,
            "window_scale": self.window_scale,
            "initial_lives": self.initial_lives,
            "keymap": {
                a: list(self.keymap.get(a, DEFAULT_KEYMAP[a])) for a in KEYMAP_ACTIONS
            },
        }

    def save(self, path: str | Path) -> None:
        path = Path(path)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8"
        )
        tmp.replace(path)  # 原子替换, 避免半截文件

    @classmethod
    def from_dict(cls, data) -> "GameConfig":
        """从 JSON 对象恢复; 任何字段不对就回退该字段默认值。"""
        cfg = cls()
        if not isinstance(data, dict):
            return cfg
        cfg.bgm_volume = _clamp_int(
            data.get("bgm_volume"), VOLUME_MIN, VOLUME_MAX, cfg.bgm_volume
        )
        cfg.se_volume = _clamp_int(
            data.get("se_volume"), VOLUME_MIN, VOLUME_MAX, cfg.se_volume
        )
        src = data.get("bgm_source")
        if src in BGM_SOURCES:
            cfg.bgm_source = src
        cfg.window_scale = _clamp_int(
            data.get("window_scale"), SCALE_MIN, SCALE_MAX, cfg.window_scale
        )
        cfg.initial_lives = _clamp_int(
            data.get("initial_lives"), LIVES_MIN, LIVES_MAX, cfg.initial_lives
        )
        cfg.keymap = _parse_keymap(data.get("keymap"), cfg.keymap)
        return cfg

    @classmethod
    def load(cls, path: str | Path) -> "GameConfig":
        """读文件; 缺失/损坏/JSON 不合法 → 全新默认值 (不抛异常)。"""
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        return cls.from_dict(data)
