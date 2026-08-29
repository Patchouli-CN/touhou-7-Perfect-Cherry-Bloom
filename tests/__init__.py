"""Touhou 测试包。

分层布局(铁律见 tests/game_test/__init__.py):
- 根下 test_*.py: 通用层测试, 只用 conftest 注册的假作品 "test00",
  禁止 import games.*(AST 守护见 test_api.py);
- game_test/thXX/: 作品专属测试(test_thXX_ 前缀)。

运行: SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy uv run python -m pytest <路径> -q
"""
