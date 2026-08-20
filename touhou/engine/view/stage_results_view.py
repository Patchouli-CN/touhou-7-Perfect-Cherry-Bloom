"""过关结算面板渲染(pygame) —— Gui.cpp STAGERESULTS 段的简化移植。

原版: stageClearBg 贴图 + 转场截图 + AsciiManager 逐行文字;
本期简化: 半透明面板 + 逐项数字(显示值, 同 Gui::OnDraw finishedStage 段)
+ 按键继续(对话 PAUSE 门控, Z 提前结束, 见 engine/msg.py)。
数据由 core.impl._on_stage_results 生成(game.stage_results)。
"""

from __future__ import annotations

import pygame

from ..bullets import SCREEN


def _load_font(size: int):
    pygame.font.init()  # 幂等; headless 测试也要能用字体
    for name in ("Microsoft YaHei", "SimHei", "SimSun", None):
        try:
            return pygame.font.SysFont(name, size)
        except Exception:
            continue
    return pygame.font.Font(None, size)


class StageResultsView:
    """过关结算面板: render(surf, results, frame) 画一帧(叠加在游戏面上)。"""

    def __init__(self) -> None:
        self._font = _load_font(16)
        self._font_big = _load_font(22)

    def _text(self, surf, font, s: str, x: int, y: int,
              color=(240, 240, 240)) -> None:
        surf.blit(font.render(s, True, color), (x, y))

    def render(self, surf: pygame.Surface, results: dict, frame: int) -> None:
        w, h = int(SCREEN.x), int(SCREEN.y)
        panel = pygame.Surface((320, 300), pygame.SRCALPHA)
        panel.fill((0, 0, 24, 170))  # 半透明底衬
        px, py = (w - 320) // 2, 60
        surf.blit(panel, (px, py))

        # C: currentStage<6 → "Stage Clear", 否则 "All Clear!"
        title = "All Clear!" if results.get("all_clear") else "Stage Clear"
        self._text(surf, self._font_big, title, px + 24, py + 14,
                   (255, 230, 130))
        y = py + 56
        for label, value in results.get("lines", []):
            self._text(surf, self._font, f"{label:8s}= {value:>9,}", px + 24, y)
            y += 24
        y += 12
        if results.get("rank_line"):
            self._text(surf, self._font, results["rank_line"], px + 24, y,
                       (255, 130, 130))
            y += 24
        if results.get("penalty_line"):
            self._text(surf, self._font, results["penalty_line"], px + 24, y,
                       (255, 130, 130))
            y += 24
        # Gui.cpp "Total = %8d0": 尾 0 是字面拼接(显示值=代码值×10)
        self._text(surf, self._font_big, f"Total = {results.get('total', 0):,}0",
                   px + 24, y + 4, (255, 255, 255))
        if frame % 60 < 40:  # 闪烁提示
            self._text(surf, self._font, "Z: 继续", px + 24, py + 276,
                       (200, 200, 220))
