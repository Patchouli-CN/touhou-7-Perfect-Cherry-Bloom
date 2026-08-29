"""ECL 翻译器的真实 th07 回放测试: 一面琪露诺符卡 sub → trace → 妖归 JSON。

数据流: 真实 th07.dat 的 ecldata1.ecl → YoukaiDanmakuTranslator.record()
(EclMachineTh07 逐帧回放) → compile() → SpellDefinition dict。
"""

from __future__ import annotations

import json

import pytest

import touhou  # noqa: F401  # import 即完成 th07 注册
from touhou.engine.translate import TranslateMode, YoukaiDanmakuTranslator
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


def _walk_actions(actions: list) -> list:
    out = []
    for a in actions:
        out.append(a)
        for key in ("body", "if_true", "if_false"):
            sub = a.get(key)
            if isinstance(sub, list):
                out.extend(_walk_actions(sub))
    return out


def test_control_mode_real_spellcard_sub() -> None:
    """CONTROL(静态控制流, 不走 VM): 真实符卡 sub → 合法 SpellDefinition 结构。"""
    tr = YoukaiDanmakuTranslator("th07")
    out = tr.translate(_ecl_bytes(), _LINGERING_COLD_SUB, mode=TranslateMode.CONTROL)

    assert out["id"].startswith("youkaishomecoming:ecl_th07_card")
    assert out["entry_phase"] == f"{out['id']}/main"
    assert "寒符" in out["display"]["name"]
    phase = out["phases"][out["entry_phase"]]
    assert all("type" in a for a in phase["on_tick"])
    json.dumps(out, ensure_ascii=False)


def test_control_mode_loop_structure_preserved() -> None:
    """二面 天符「天仙鳴動」(ecldata2 sub 64): 波次循环保留为嵌套 repeat。"""
    ecl = GameArchive.open(DEFAULT_DATA).load("ecldata2.ecl")
    tr = YoukaiDanmakuTranslator("th07")
    out = tr.translate(ecl, 64, mode=TranslateMode.CONTROL)

    actions = out["phases"][out["entry_phase"]]["on_tick"]
    all_actions = _walk_actions(actions)
    repeats = [a for a in all_actions if a["type"] == "repeat"]
    assert repeats, "回边循环应重建为 repeat"
    # 有限计数循环(DEC_JUMP 计数器初值可静态确定)
    assert any(isinstance(r["count"], int) and r["count"] < 100000 for r in repeats)
    # 循环体的时间语义: delay 迭代表达式 + fire
    delays = [a for a in all_actions if a["type"] == "delay"]
    assert any("$i" in d["delay_ticks"] for d in delays)
    fires = [a for a in all_actions if a["type"] == "fire_danmaku"]
    assert fires and all(f["count"] >= 1 and f["speed"] > 0 for f in fires)
    json.dumps(out, ensure_ascii=False)


# ==================== AUTO 模式(静态骨架 + 动态补盲) ====================


def test_auto_mode_lingering_cold_filled_by_dynamic() -> None:
    """寒符 AUTO: 自动射击(SET_SHOOT_INTERVAL)是静态盲区, 动态段补回 → 非空。

    这是 AUTO 的核心验收: 同一张卡的 CONTROL 输出近乎全空(v1 不覆盖
    自动射击), AUTO 必须靠 provenance=None 的残余事件兜底出内容。
    """
    tr = YoukaiDanmakuTranslator("th07")
    control = tr.translate(_ecl_bytes(), _LINGERING_COLD_SUB, mode=TranslateMode.CONTROL)
    control_actions = control["phases"][control["entry_phase"]]["on_tick"]
    assert not _walk_actions(control_actions), "寒符 CONTROL 应为空(静态盲区)"

    out = tr.translate(
        _ecl_bytes(), _LINGERING_COLD_SUB, mode=TranslateMode.AUTO, max_frames=3600
    )
    assert "寒符" in out["display"]["name"]
    actions = out["phases"][out["entry_phase"]]["on_tick"]
    assert actions, "静态骨架为空时动态段兜底, phases 不得为空"
    all_actions = _walk_actions(actions)
    fires = [a for a in all_actions if a["type"] == "fire_danmaku"]
    assert fires
    gated = [
        a for a in actions if a.get("condition", {}).get("type") == "tick_interval"
    ]
    assert gated, "自动射击的周期 pattern 应折叠成 tick_interval 门控"
    json.dumps(out, ensure_ascii=False)


def test_auto_mode_tenken_structure_without_duplicates() -> None:
    """天符 AUTO: repeat 结构保留; 静态已覆盖的事件不重复进动态补充段。

    实测(provenance 溯源): 90 条弹幕事件里 41 条来自静态已翻译指令(去重
    丢弃), 50 条来自 4 条变量依赖、静态求值失败的 fire 指令 —— 动态补回
    折叠为 10 条 fire, 故 AUTO = 静态 4 + 补盲 10。
    """
    ecl = GameArchive.open(DEFAULT_DATA).load("ecldata2.ecl")
    tr = YoukaiDanmakuTranslator("th07")
    control = tr.translate(ecl, 64, mode=TranslateMode.CONTROL)
    auto = tr.translate(ecl, 64, mode=TranslateMode.AUTO, max_frames=3600)

    auto_all = _walk_actions(auto["phases"][auto["entry_phase"]]["on_tick"])
    assert any(a["type"] == "repeat" for a in auto_all), "静态骨架的 repeat 应保留"
    auto_fires = [a for a in auto_all if a["type"] == "fire_danmaku"]
    control_all = _walk_actions(control["phases"][control["entry_phase"]]["on_tick"])
    control_fires = [a for a in control_all if a["type"] == "fire_danmaku"]
    assert len(control_fires) == 4
    assert len(auto_fires) == 4 + 10  # 静态骨架 + 动态补盲(无重复)
    json.dumps(auto, ensure_ascii=False)


def test_auto_mode_hankaichou_display_name_is_lunatic_branch() -> None:
    """反魂蝶(sub 62)AUTO + difficulty=3: display 名取运行时宣言的八分咲。

    回归: 该 sub 四个难度变体四条 BEGIN_SPELLCARD, 静态 compile_ir 拿文本序
    第一条(一分咲/Easy); 运行时宣言事件的 origin 指向分支指令, 旧 AUTO
    过滤把它当"静态已覆盖"丢弃 → display 名错成一分咲。
    """
    ecl = GameArchive.open(DEFAULT_DATA).load("ecldata6.ecl")
    tr = YoukaiDanmakuTranslator("th07")
    out = tr.translate(
        ecl, 62, mode=TranslateMode.AUTO, context={"difficulty": 3}, max_frames=3600
    )
    assert out["display"]["name"] == "「反魂蝶 -八分咲-」"
    json.dumps(out, ensure_ascii=False)
