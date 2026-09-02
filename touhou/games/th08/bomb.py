"""炸弹(th08 东方永夜抄) —— 回调驱动骨架。

对照 th08 反编译源码(Reference/th08-ref/src/):
- 触发/生命周期框架在引擎基座 engine/bomb_base.py(BombBase/try_start_bomb);
- th08 无 th07 式首帧参数表: 炸弹 = g_PlayerBombCallbacksByShotType
  (Player.cpp:79-92) 按 shotType×2 给的 5 槽回调集(PRIMARY/SECONDARY/
  PRIMARY_DEATHBOMB/SECONDARY_DEATHBOMB/SPECIAL=DissolveSpell,
  Player.hpp:203-210), 首帧各 Update* 回调里调 BeginBombSpell
  (PlayerBomb.cpp:157-176: duration/自机 invulnerable timer/吸附全部道具);
- 决死(deathbomb): DEAD 态(决死窗倒数)内按弹键 → 走 DEATHBOMB 变体
  (UpdateBorderAndBombState 系; world 侧 DEAD→INVULNERABLE 翻转照
  th07 world.py:1377-1385 模式)。

本期(阶段 3 单 A)是**骨架**: 24 套机体回调的逐套移植是后续工作
(PlayerBomb.cpp 各 Update*/Draw*); 这里给一个数据驱动占位 calc:
首帧 BeginBombSpell 等价物(duration=200/无敌 260, 取自梦想妙珠
UpdateFantasyOrbBomb 的 BeginBombSpell(0, …, 200, 260, 0),
PlayerBomb.cpp:189-191) + 常驻清弹圆/伤害盒跟随自机, 到点结束。
"""

from __future__ import annotations

from ...engine.bomb_base import (  # noqa: F401 (框架类型为兼容再导出)
    BombBase,
    BombStartResult,
    try_start_bomb,
)
from ...engine.bomb_base import BombContext as _BombContextBase
from ...utils import Vec2

# 清弹盒掉落道具类型: ITEM_POINT_STAR=6 (BulletManager.cpp:49 cancelItemType)
ITEM_POINT_STAR = 6

# 透出事件(上层接线)
EVENT_REMOVE_ALL_ITEMS = "remove_all_items"  # BeginBombSpell 的 AutoCollectAllItems
EVENT_END_PLAYER_SPELLCARD = "end_player_spellcard"  # 符卡横幅收束(GUI 侧)

# 骨架占位参数 (UpdateFantasyOrbBomb 首帧 BeginBombSpell(0,…,200,260,0) +
# CreateCircleCancelRegion(pos, 96.0, 0.0, 200, 6) / CreateCircleDamageRegion
# (…, 64.0, 0.0, 5, 200), PlayerBomb.cpp:189-211)
_PLACEHOLDER_DURATION = 200
_PLACEHOLDER_INVULN = 260
_PLACEHOLDER_CLEAR_RADIUS = 96.0
_PLACEHOLDER_DAMAGE_HALF = 64.0  # 伤害盒半宽(圆→方近似)
_PLACEHOLDER_DAMAGE_PER_FRAME = 5

# 炸弹发声音 (UpdateFantasyOrbBomb 等首帧 PlaySoundByIdx(13))
BOMB_SE = 13


class BombContext(_BombContextBase):
    """bombCalc 的每帧外部输入(th08 骨架: 基类字段已够)。"""


class Th08Bomb(BombBase[BombContext]):
    """一次炸弹的生命周期(th08) —— 通用生命周期/盒判定在基类 BombBase。

    callback_variant: 回调槽位(Player.hpp PlayerBombCallbackVariant):
    0=非 focus 弹, 1=focus 弹, 2/3=决死变体(本期骨架行为相同, 仅记账)。
    """

    shot_type: int = 0
    callback_variant: int = 0

    def start(self, *, focus: bool, ctx: BombContext, deathbomb: bool = False) -> None:
        """触发炸弹; deathbomb=True 时按决死变体记账(callbackVariant 2/3)。"""
        self.callback_variant = (1 if focus else 0) + (2 if deathbomb else 0)
        super().start(focus=focus, ctx=ctx)

    def _calc(self, ctx: BombContext) -> None:
        """机体炸弹占位 calc(骨架; 逐机体移植见模块 docstring)。

        首帧: BeginBombSpell 等价物(duration/无敌/吸附道具) + 常驻清弹圆
        (跟随自机) + 伤害盒; 每帧: 盒跟随自机, 到 duration 结束。
        """
        if self.timer >= self.duration:
            self.is_in_use = False
            self.events.append(EVENT_END_PLAYER_SPELLCARD)
            return
        if self.has_ticked and self.timer == 0:
            self.duration = _PLACEHOLDER_DURATION
            self.invulnerability_timer = _PLACEHOLDER_INVULN
            self.events.append(EVENT_REMOVE_ALL_ITEMS)
            self._spawn_clear(
                ctx.player_pos,
                radius=_PLACEHOLDER_CLEAR_RADIUS,
                growth=0.0,
                lifetime=_PLACEHOLDER_DURATION,
                item_type=ITEM_POINT_STAR,
            )
        # 盒跟随自机(骨架近似: 各机体的 workItem 运动留后续)
        for box in self.clear_boxes:
            if box.active:
                box.pos = ctx.player_pos
                box.lifetime = max(box.lifetime, 1)  # 常驻到 bomb 结束
        self.damage_boxes[0].pos = ctx.player_pos
        self.damage_boxes[0].size = Vec2(
            _PLACEHOLDER_DAMAGE_HALF * 2, _PLACEHOLDER_DAMAGE_HALF * 2
        )
        self.damage_boxes[0].lifetime = _PLACEHOLDER_DAMAGE_PER_FRAME
