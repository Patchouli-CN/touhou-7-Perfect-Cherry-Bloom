"""Touhou: 标题/菜单纯逻辑测试。"""

from __future__ import annotations

import sys

sys.path.insert(0, r"D:\python_play\Touhou08")

from touhou.games.th07.view.screens import (  # noqa: E402
    CHARACTERS,
    DIFFICULTIES,
    MenuAction,
    MenuCursor,
    TitleFlow,
    build_character_cursor,
    build_difficulty_cursor,
    character_index,
    difficulty_index,
)


def test_menu_cursor_move_and_wrap() -> None:
    c = MenuCursor(["A", "B", "C"])
    assert c.current == "A"
    c.move(-1)
    assert c.current == "C"  # 回绕
    c.move(1)
    assert c.current == "A"


def test_flow_start_goes_to_difficulty() -> None:
    f = TitleFlow()
    r = f.handle(MenuAction.CONFIRM)  # "开始游戏"在 index 0
    assert r["action"] == "select_difficulty"


def test_flow_quit() -> None:
    f = TitleFlow()
    while f.cursor.current != "退出":
        f.handle(MenuAction.DOWN)
    r = f.handle(MenuAction.CONFIRM)
    assert r["action"] == "quit"


def test_flow_navigation_does_not_emit() -> None:
    f = TitleFlow()
    assert f.handle(MenuAction.DOWN) is None  # 只是移动, 不产生选择


def test_character_and_difficulty_lists() -> None:
    assert len(CHARACTERS) == 6
    assert len(DIFFICULTIES) == 6
    assert DIFFICULTIES[1] == "Normal"
    assert CHARACTERS[0] == "ReimuA"


def test_build_cursors() -> None:
    assert build_character_cursor().current == "ReimuA"
    assert build_difficulty_cursor().current == "Normal"  # 默认 Normal
    assert character_index("MarisaB") == 3
    assert difficulty_index("Hard") == 2


# ---------------------------------------------------------------------------
# Option 设置菜单(OptionFlow, MainMenu.cpp OnUpdateOptionsMenu)
# ---------------------------------------------------------------------------


def test_flow_option_emits_option_action() -> None:
    from touhou.games.th07.view.screens import MAIN_MENU_ITEMS

    f = TitleFlow()
    while f.cursor.current != "Option":
        f.handle(MenuAction.DOWN)
    assert MAIN_MENU_ITEMS[f.cursor.index] == "Option"
    r = f.handle(MenuAction.CONFIRM)
    assert r["action"] == "option"


def _option_flow():
    from touhou.games.th07.view.screens import OptionFlow

    return OptionFlow()


def test_option_navigation() -> None:
    f = _option_flow()
    assert f.cursor.current == "BGM 音量"
    assert f.handle(MenuAction.DOWN) is None
    assert f.cursor.current == "SE 音量"
    f.handle(MenuAction.UP)
    assert f.cursor.current == "BGM 音量"


def test_option_volume_adjust_and_clamp() -> None:
    f = _option_flow()
    r = f.handle(MenuAction.LEFT)  # 100 → 90
    assert r == {"action": "changed", "item": "BGM 音量", "value": 90}
    assert f.config.bgm_volume == 90
    for _ in range(20):
        r = f.handle(MenuAction.LEFT)
    assert f.config.bgm_volume == 0  # 截断不回绕
    assert r["value"] == 0
    f.handle(MenuAction.DOWN)  # → SE 音量
    r = f.handle(MenuAction.RIGHT)
    assert r == {"action": "changed", "item": "SE 音量", "value": 100}


def test_option_source_toggles() -> None:
    f = _option_flow()
    f.handle(MenuAction.DOWN)
    f.handle(MenuAction.DOWN)  # → 音源
    r = f.handle(MenuAction.RIGHT)
    assert r == {"action": "changed", "item": "音源", "value": "midi"}
    r = f.handle(MenuAction.LEFT)
    assert r["value"] == "wav"


def test_option_scale_and_lives_wrap() -> None:
    f = _option_flow()
    for _ in range(3):
        f.handle(MenuAction.DOWN)  # → 窗口缩放
    r = f.handle(MenuAction.RIGHT)  # 2 → 3
    assert r["value"] == 3
    r = f.handle(MenuAction.RIGHT)  # 3 → 回绕 1
    assert r["value"] == 1
    f.handle(MenuAction.DOWN)  # → 初始残机
    r = f.handle(MenuAction.LEFT)  # 3 → 2
    assert r["value"] == 2
    r = f.handle(MenuAction.LEFT)  # 2 → 回绕 5
    assert r["value"] == 5
    assert f.config.initial_lives == 5


def test_option_back_jumps_to_quit_then_quits() -> None:
    """BACK: 光标跳到"退出"(MainMenu.cpp:834-846); 在"退出"上 BACK/CONFIRM 退出。"""
    f = _option_flow()
    assert f.handle(MenuAction.BACK) is None
    assert f.cursor.current == "退出"
    assert f.handle(MenuAction.BACK) == {"action": "quit"}
    f = _option_flow()
    f.handle(MenuAction.BACK)  # 跳到退出
    assert f.handle(MenuAction.CONFIRM) == {"action": "quit"}


def test_option_adjust_on_quit_item_does_nothing() -> None:
    f = _option_flow()
    f.handle(MenuAction.BACK)  # 光标到"退出"
    assert f.handle(MenuAction.LEFT) is None
    assert f.handle(MenuAction.RIGHT) is None


# ---------------------------------------------------------------------------
# 结算入榜名字输入(ResultScreen.cpp HandleResultKeyboard :1141-1326)
# ---------------------------------------------------------------------------


def test_name_alphabet_matches_g_AlphabetList() -> None:
    """字表照抄 g_AlphabetList (:24): 96 字 = 6 行 x 16 列。"""
    from touhou.games.th07.view.screens import NAME_ALPHABET

    assert len(NAME_ALPHABET) == 96
    assert NAME_ALPHABET[:32] == "ABCDEFGHIJKLMNOPQRSTUVWXYZ.,:;_@"
    assert NAME_ALPHABET[32:64] == "abcdefghijklmnopqrstuvwxyz+-/*=%"
    assert NAME_ALPHABET[64:] == "0123456789#!?'\"$(){}[]<>&\\|~^ --"
    assert NAME_ALPHABET[93] == " "  # 唯一不可停格
    assert NAME_ALPHABET[94] == "-" and NAME_ALPHABET[95] == "-"


def _entry(**kw):
    from touhou.games.th07.view.screens import NameEntryFlow

    return NameEntryFlow(**kw)


def test_name_entry_initial_state() -> None:
    """初始: 名字槽带 LSNM(不足补空格); 有 LSNM 字表光标在 END(95),
    否则在 0('A') (:1200-1207)。"""
    e = _entry(initial="PLAYER", has_lsnm=False)
    assert e.name == "PLAYER  " and e.cursor == 0 and e.selected == 0
    e = _entry(initial="ZUN", has_lsnm=True)
    assert e.name == "ZUN     " and e.selected == 95
    e = _entry(initial="TOOLONGNAME99")  # 超 8 字符截断(name[9]=8+NUL)
    assert e.name == "TOOLONGN"


def test_name_entry_vertical_wrap_and_space_skip() -> None:
    """上下 ±16 回绕(:1215-1237); 落点是空格(93)时继续同向走。"""
    e = _entry()
    e.handle(MenuAction.UP)
    assert e.selected == 80  # 0-16 → +96 → 80 ('$')
    e.handle(MenuAction.DOWN)
    assert e.selected == 0  # 80+16=96 → -96 → 0
    e.handle(MenuAction.DOWN)
    assert e.selected == 16  # 'Q'
    # 空格跳过: 77(') DOWN → 93 是 ' ' → 109 → -96 → 13('N')
    e.selected = 77
    e.handle(MenuAction.DOWN)
    assert e.selected == 13
    e.selected = 13
    e.handle(MenuAction.UP)  # 13-16 → +96 → 93 ' ' → 77
    assert e.selected == 77


def test_name_entry_horizontal_row_wrap() -> None:
    """左右行内回绕(:1241-1269): 左出列 0 → 同行列 15; 右出列 15 → 列 0。"""
    e = _entry()
    e.handle(MenuAction.LEFT)  # 0 → 15 ('P')
    assert e.selected == 15
    e.handle(MenuAction.RIGHT)  # 15 → 16 行首回绕 → 0
    assert e.selected == 0
    e.selected = 16
    e.handle(MenuAction.LEFT)  # 16 → 15 → 行尾回绕 → 31
    assert e.selected == 31
    # 空格跳过: 94('-') LEFT → 93 ' ' → 92('^'); 92 RIGHT → 93 ' ' → 94
    e.selected = 94
    e.handle(MenuAction.LEFT)
    assert e.selected == 92
    e.handle(MenuAction.RIGHT)
    assert e.selected == 94


def test_name_entry_confirm_typing_and_end() -> None:
    """确认写槽并进下一槽(:1271-1299); 输满 8 槽光标=8 且跳 END(:1293)。"""
    e = _entry(initial="", has_lsnm=False)
    e.handle(MenuAction.CONFIRM)  # 'A' → 槽0, cursor=1
    assert e.name == "A       " and e.cursor == 1
    for _ in range(7):  # 再输 7 个 'A' → 满 8 槽
        e.handle(MenuAction.CONFIRM)
    assert e.name == "AAAAAAAA" and e.cursor == 8 and e.selected == 95
    r = e.handle(MenuAction.CONFIRM)  # END → 完成
    assert r == {"action": "finish", "name": "AAAAAAAA"}


def test_name_entry_confirm_on_end_finishes_early() -> None:
    """END(95) 上确认 = 完成(同 TH_BUTTON_MENU 出口, :1310-1323)。"""
    e = _entry(initial="ZUN", has_lsnm=True)  # selected=95
    r = e.handle(MenuAction.CONFIRM)
    assert r == {"action": "finish", "name": "ZUN     "}


def test_name_entry_space_cell_and_cursor8_overwrite() -> None:
    """94 = 写空格(:1279-1281); cursor==8 时确认普通字改写末槽(:1273)。"""
    e = _entry(initial="", has_lsnm=False)
    e.selected = 94
    e.handle(MenuAction.CONFIRM)  # 槽0 写 ' ', cursor=1
    assert e.name == "        " and e.cursor == 1
    e.cursor = 8
    e.selected = 25  # 'Z'
    e.handle(MenuAction.CONFIRM)  # cursor==8 → 改写槽7
    assert e.name == "       Z" and e.cursor == 8


def test_name_entry_delete_backs_up() -> None:
    """删除: 退一格并清当前槽与前一槽(:1300-1308); cursor=0 时不退。"""
    e = _entry(initial="AB", has_lsnm=False)
    e.cursor = 2
    e.handle(MenuAction.BACK)  # → cursor=1, 槽1/槽2 清
    assert e.cursor == 1 and e.name == "A       "
    e.handle(MenuAction.BACK)  # → cursor=0, 全清
    assert e.cursor == 0 and e.name == "        "
    e.handle(MenuAction.BACK)  # cursor=0: 不动
    assert e.cursor == 0
    # cursor==8 删除: cursor2=7 → 清槽7, cursor=7
    e2 = _entry(initial="ABCDEFGH")
    e2.cursor = 8
    e2.handle(MenuAction.BACK)
    assert e2.cursor == 7 and e2.name == "ABCDEFG "
