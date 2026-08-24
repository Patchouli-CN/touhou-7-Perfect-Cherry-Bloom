
# BUG 报告，必须修复

1. ~~难度选择出界~~ ✅ 已修复（待验证后可删）

- desc: 原版选择界面难度的时候有4个选项，但我们这里实际上6个，虽然界面上看起来四个，但四个选完了没回到 简单，而是出界
- 根因：AI你是不是把ex和ph也当难度了？不行，那是额外关卡，不是难度
- 修复：`GameApp._diff` 光标原本在全名单（6 项，含 Extra/Phantasm）上回绕，
  渲染只画 4 项 → 选中不可见的第 5/6 项出界。改为光标走 `MAIN_DIFFICULTIES`
  （本篇 4 项），数量取自新增 `GameData.main_difficulty_count`（默认 4）；
  Extra/Phantasm 仍走 Extra Start 流程不受影响。
  回归测试：`tests/test_app.py::test_main_difficulty_cursor_wraps_within_four`