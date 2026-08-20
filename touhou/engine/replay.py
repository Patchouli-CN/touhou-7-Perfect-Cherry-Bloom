""" 回放(replay)录制/播放 —— 自定义 JSON 格式, 不兼容原版 .rpy。

原理: 游戏逻辑是逐帧确定的(impl.tick 只依赖输入帧与初始种子,
engine/rng.py 确定性 LCG, seed 注入见 core/impl.py), 所以回放只需记录
每帧输入 + 开局参数, 播放时重建 game 逐帧喂回输入即可复现整局。

文件格式 (单个 JSON):
    {
      "version": 1,
      "meta": {
        "difficulty": 1,          # 0..5 (DIFFICULTIES 下标)
        "character": 0,           # 0..5 (CHARACTERS 下标, shotType)
        "stage": 1,               # 起始关 (Extra=7 Phantasm=8)
        "seed": 3855,             # impl seed (主 rng; ECL rng 由其派生)
        "initial_lives": 3,       # 开局残机 (Option 覆写后的值)
        "frames": 12345,          # 输入帧数
        "created": "2026-08-18T10:00:00+00:00"
      },
      "inputs": [[code, count], ...]   # RLE: code 连续 count 帧
    }

每帧输入压成一个 int code (9 bit):
    bit0..5 = keys 6 元组 (left,right,up,down,focus,shoot)
    bit6 = bomb  bit7 = advance (对话 Z)  bit8 = skip (对话 Ctrl)
长段静止/连射会被 RLE 压得很小。

【原版 replayEventFlags 不移植】 原版 .rpy 每帧除输入外还存
replayEventFlags (ReplayManager.cpp:63 写入 inputKey 字段; 置位点:
体术命中 |=2 (Player.cpp:1026/1159)、bomb |=1 (Player.cpp:1728)、
结界 |=4/8 (Player.cpp:1780/2141)、决死 |=0x10 (Player.cpp:2193)、
敌人 deathType3 |=0x20 (EnemyManager.cpp:547/959/1012)、
道具相关 |=0x40 (ItemManager.cpp:198)、暂停 |=256 (ReplayManager.cpp:26))。
但播放侧只读 frameNum(输入), inputKey 从不被消费
(ReplayManager.cpp:118) —— 它只是给外部工具/调试用的事后元数据。
本格式回放同样只喂输入帧即可确定性复现, 无消费方, 故不记录这些标志。
"""

from __future__ import annotations

import msgspec
from datetime import datetime, timezone
from pathlib import Path

FORMAT_VERSION = 1
# 录像目录: 仓库根 replays/ (原版是 exe 旁 ./replay/th7_udXXXX.rpy)
DEFAULT_REPLAY_DIR = Path(__file__).resolve().parent.parent.parent / "replays"

_KEYS_LEN = 6


def encode_input(keys, bomb: bool, advance: bool, skip: bool) -> int:
    """一帧输入 → int code (见模块 docstring 位布局)。"""
    code = 0
    for i in range(_KEYS_LEN):
        if i < len(keys) and keys[i]:
            code |= 1 << i
    if bomb:
        code |= 1 << 6
    if advance:
        code |= 1 << 7
    if skip:
        code |= 1 << 8
    return code


def decode_input(code: int) -> tuple[tuple[bool, ...], bool, bool, bool]:
    """int code → (keys 6 元组, bomb, advance, skip)。"""
    keys = tuple(bool(code >> i & 1) for i in range(_KEYS_LEN))
    return keys, bool(code & 1 << 6), bool(code & 1 << 7), bool(code & 1 << 8)


def _rle(codes: list[int]) -> list[list[int]]:
    out: list[list[int]] = []
    for c in codes:
        if out and out[-1][0] == c:
            out[-1][1] += 1
        else:
            out.append([c, 1])
    return out


def _unrle(pairs: list[list[int]]) -> list[int]:
    codes: list[int] = []
    for c, n in pairs:
        codes.extend([c] * n)
    return codes


def make_meta(*, difficulty: int, character: int, stage: int, seed: int,
              initial_lives: int) -> dict:
    return {
        "difficulty": int(difficulty),
        "character": int(character),
        "stage": int(stage),
        "seed": int(seed),
        "initial_lives": int(initial_lives),
        "frames": 0,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


class ReplayRecorder:
    """录制一局: 开局给 meta, 每帧 record() 一次, 结束 save()。"""

    def __init__(self, meta: dict) -> None:
        self.meta = dict(meta)
        self._codes: list[int] = []

    def record(self, keys, bomb: bool, advance: bool, skip: bool) -> None:
        self._codes.append(encode_input(keys, bomb, advance, skip))

    @property
    def frames(self) -> int:
        return len(self._codes)

    def to_dict(self) -> dict:
        meta = dict(self.meta)
        meta["frames"] = len(self._codes)
        return {"version": FORMAT_VERSION, "meta": meta,
                "inputs": _rle(self._codes)}

    def save(self, path: str | Path) -> Path:
        """原子写盘(同 score_store 容错风格); 返回实际路径。"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(msgspec.json.encode(self.to_dict()))
        tmp.replace(path)
        return path


def new_replay_name(replay_dir: str | Path = DEFAULT_REPLAY_DIR) -> Path:
    """按时间戳生成不重名的录像文件名 th7_udNNNN.json (原版命名风格)。"""
    replay_dir = Path(replay_dir)
    stamp = datetime.now().strftime("%y%m%d%H%M%S")
    for i in range(100):
        name = f"th7_ud{stamp}{i:02d}.json"
        if not (replay_dir / name).exists():
            return replay_dir / name
    return replay_dir / f"th7_ud{stamp}x.json"


def load_replay(path: str | Path) -> dict:
    """读录像文件 → {"version", "meta", "codes": [int, ...]}; 坏文件抛 ValueError。"""
    try:
        data = msgspec.json.decode(Path(path).read_bytes())
    except (OSError, msgspec.DecodeError) as e:
        raise ValueError(f"录像文件无法读取: {path}: {e}") from e
    if not isinstance(data, dict) or data.get("version") != FORMAT_VERSION:
        raise ValueError(f"录像版本不符: {path}")
    meta = data.get("meta")
    pairs = data.get("inputs")
    if not isinstance(meta, dict) or not isinstance(pairs, list):
        raise ValueError(f"录像内容缺损: {path}")
    codes = _unrle(pairs)
    if meta.get("frames") not in (None, len(codes)):
        raise ValueError(f"录像帧数不符: {path}")
    return {"version": data["version"], "meta": meta, "codes": codes}


def list_replays(replay_dir: str | Path = DEFAULT_REPLAY_DIR) -> list[dict]:
    """扫录像目录, 返回 [{path, meta}], 按文件名序; 坏文件跳过。"""
    replay_dir = Path(replay_dir)
    out: list[dict] = []
    if not replay_dir.is_dir():
        return out
    for p in sorted(replay_dir.glob("*.json")):
        try:
            r = load_replay(p)
        except ValueError:
            continue
        out.append({"path": p, "meta": r["meta"]})
    return out
