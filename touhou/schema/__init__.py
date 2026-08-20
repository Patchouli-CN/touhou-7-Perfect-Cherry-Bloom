"""纯数据结构 / 文件格式解析层。

收录 th07 各类数据文件的解析与纯数据定义，无游戏运行时行为、
不 import engine 的行为模块、不碰 pygame:

- ``archive``: th07.dat 包体解包(GameArchive)
- ``anm``: .anm 贴图/脚本条目解析(AnmFile)与全量脚本指令(parse_scripts)
- ``stage``: .std 关卡解析(Stage): 标题/BGM + 3D 场景(物件/quad/
  实例/场景脚本指令)
- ``shot_data``: .sht 自机弹数据(parse_sht / ShotData)
- ``sound``: SE 音效表(SE / SE_FILES / SE_VOLUMES)与发声队列(SoundQueue)
- ``msg``: msg*.dat 对话文件。解析(MsgFile)与指令 VM(MsgVm)耦合在同一
  模块难以拆分, 故整体移入; VM 仍为纯逻辑(输入由调用方注入), 不碰渲染。
"""
