"""ECL 翻译器的真实 th07 回放测试: 一面琪露诺符卡 sub → trace → 妖归 JSON。

数据流: 真实 th07.dat 的 ecldata1.ecl → YoukaiDanmakuTranslator.record()
(EclMachineTh07 逐帧回放) → compile() → SpellDefinition dict。
"""

from __future__ import annotations

import json

import pytest

import touhou  # noqa: F401  # import 即完成 th07 注册
from touhou.engine.translate import YoukaiDanmakuTranslator
from touhou.paths import DEFAULT_DATA
from touhou.schema.archive import GameArchive

NEEDS_DAT = pytest.mark.skipif(not DEFAULT_DATA.exists(), reason="需要真实 th07.dat")

pytestmark = NEEDS_DAT

# ecldata1 sub 42 = 一面琪露诺 寒符「リンガリングコールド」(4 难度变体;
# 默认 difficulty=1 走 Normal 分支)
_STAGE1_ECL = "ecldata1.ecl"
_LINGERING_COLD_SUB = 42


def _ecl_bytes() -> bytes:
    return GameArchive.open(DEFAULT_DATA).load(_STAGE1_ECL)


def test_record_real_spellcard_sub() -> None:
    """真实符卡 sub 回放: trace 非空, 有符卡宣言与周期弹幕。"""
    tr = YoukaiDanmakuTranslator("th07")
    trace = tr.record(_ecl_bytes(), _LINGERING_COLD_SUB, max_frames=3600)
    assert tr.last_frame_count == 3600  # 符卡循环不会自然结束
    kinds = {ev.kind for ev in trace}
    assert "spellcard" in kinds and "bullets" in kinds
    card = next(ev for ev in trace if ev.kind == "spellcard")
    assert "リンガリングコールド" in card.data["name"]  # VM 已 XOR 0xAA 解码
    shots = [ev for ev in trace if ev.kind == "bullets"]
    assert len(shots) > 100  # 周期射击(SET_SHOOT_INTERVAL + life>0)
    # 快照是结构化参数(弹型/数量/速度都在)
    assert all(s.data["count1"] >= 1 for s in shots)


def test_compile_produces_valid_spell_structure() -> None:
    """compile 输出: 合法 SpellDefinition 结构 + 可 json.dumps + 折叠生效。"""
    tr = YoukaiDanmakuTranslator("th07")
    out = tr.translate(_ecl_bytes(), _LINGERING_COLD_SUB, max_frames=3600)

    # 顶层结构(对照妖归 assets/minimal_spell.json)
    assert out["id"].startswith("youkaishomecoming:ecl_th07_card")
    assert out["entry_phase"] == f"{out['id']}/main"
    phase = out["phases"][out["entry_phase"]]
    assert phase["id"] == out["entry_phase"]
    assert "寒符" in out["display"]["name"]
    assert out["custom_names"]["phase:main"] == out["display"]["name"]

    # 动作面: 每条 action 带 type 分派; 折叠出 tick_interval 门控
    actions = phase["on_tick"]
    assert actions and all("type" in a for a in actions)
    gated = [
        a for a in actions if a.get("condition", {}).get("type") == "tick_interval"
    ]
    assert gated, "周期同构 pattern 应折叠成 conditional+tick_interval"
    fire = gated[0]["if_true"][0]
    assert fire["type"] == "fire_danmaku"
    assert fire["count"] >= 1 and fire["speed"] > 0 and fire["lifetime"] >= 1

    # 可序列化(交付物就是 JSON)
    json.dumps(out, ensure_ascii=False)
