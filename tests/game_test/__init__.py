"""作品专属测试子树 —— 按作品分包子目录(th07/th08/...), 互不污染。

tests/ 根下的 test_*.py 是通用层测试(只用 conftest 注册的假作品
"test00", 禁止 import games.*); 本子树是唯一的 games.* import 豁免区,
每部作品一个子包: game_test/th07/test_th07_*.py, 未来 th08 即
game_test/th08/test_th08_*.py。
"""
