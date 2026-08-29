"""th07 专属 mod 能力实效测试 —— 樱点系(player)/结界系(border)命名空间。

ModApi 分层机制本身的通用契约(收割/归属/重名 fail fast/清单)用假作品在
tests/test_modding.py 验证; 这里验证 th07 注册的真实能力写入引擎生效
(需要真实 th07.dat)。
"""

from __future__ import annotations

import pytest

from touhou.apis.basic import Game
from touhou.apis.modding import ModApi
from touhou.games.th07.bomb import BorderState
from touhou.paths import DEFAULT_DATA

pytestmark = pytest.mark.skipif(not DEFAULT_DATA.exists(), reason="需要真实 th07.dat")


def _mods(seed: int = 1) -> tuple[Game, ModApi]:
    game = Game(character="ReimuA", difficulty="Normal", seed=seed)
    return game, ModApi(game)


def test_th07_capability_namespaces_and_readback() -> None:
    """注册链验证: set_cherry/set_cherry_max → api.player; border_break →
    api.border(th07 注册的整棵新命名空间)。"""
    game, mods = _mods()
    assert mods.is_capabilities_exist("player.set_cherry")
    assert mods.is_capabilities_exist("player.set_cherry_max")
    assert mods.is_capabilities_exist("border.border_break")
    assert callable(mods.player.set_cherry)
    assert callable(mods.border.border_break)
    mods.player.set_cherry(50000)
    assert game._impl.globals.cherry == 50000  # 写入落到引擎(门面无 cherry 属性)


def test_th07_available_layered_listing() -> None:
    """available() 分层清单: th07 能力按归属并入(樱点系进 player, 结界系自成 border)。"""
    _, mods = _mods()
    avail = mods.available()
    assert avail["player"]["set_cherry"].startswith("樱点直改")
    assert "set_cherry_max" in avail["player"]
    assert avail["border"]["border_break"].startswith("强制破裂")


def test_set_cherry_range_check() -> None:
    """set_cherry 域校验: 上限读引擎实况 cherryMax, 不写死魔法数。"""
    game, mods = _mods()
    cherry_max = game._impl.globals.cherry_max
    mods.player.set_cherry(cherry_max)
    assert game._impl.globals.cherry == cherry_max
    with pytest.raises(ValueError, match="超出"):
        mods.player.set_cherry(cherry_max + 1)
    with pytest.raises(ValueError, match="超出"):
        mods.player.set_cherry(-1)


def test_set_cherry_max() -> None:
    game, mods = _mods()
    g = game._impl.globals
    mods.player.set_cherry_max(g.cherry_start + 123456)
    assert g.cherry_max == g.cherry_start + 123456
    with pytest.raises(ValueError, match="超出"):
        mods.player.set_cherry_max(g.cherry_start - 1)


def test_border_break() -> None:
    """border_break: 有结界强制破裂(has_border→NONE), 无结界中文报错。"""
    game, mods = _mods()
    with pytest.raises(ValueError, match="没有结界可破"):
        mods.border.border_break()
    game._impl.border.ready_border()  # 满樱信号 → READY
    assert game._impl.border.has_border == BorderState.READY
    mods.border.border_break()
    assert game._impl.border.has_border == BorderState.NONE
