"""TH08《东方永夜抄》的 ANM v3 —— 版本校验 + 外链纹理 loader 接线。

对照 th08-ref(Reference/th08-ref, 以下行号相对其 src/):
- version==3 强制校验(AnmManager.cpp:2457,2552) → ``_EXPECTED_VERSION``;
  entry 头/sprite/脚本布局与 th07(v2)逐字节同构, 解析机制全在 schema/anm.py。
- 纹理三来源(LoadTextureData, AnmManager.cpp:2558-2586): hasData=1 内嵌
  THTX / hasData=0+``@`` 开头名建空纹理 / 否则按名从封包外链加载。外链
  纹理由本模块的 ``make_texture_loader`` 接线: FileSystem::OpenFile 读出
  的条目经 TryDecryptFromTable 内层解密(Global.cpp:901-927,
  games/th08/crypt.py), 再 D3DXCreateTextureFromFileInMemoryEx 解码 +
  colorKey 抠色(CreateTextureFromFile, AnmManager.cpp:2225-2235;
  colorKey=0 表示不抠色)。

实测备注(2026-09-03, 本机 th08.dat 317 条目): 全部 113 个 .anm 共 310
entry, hasData=0 的只有 4 条且全是 ``@`` 空纹理(capture/music00/
resulttext/text.anm) —— 外链纹理路径在实装数据里无条目, loader 用
封包内 title00.png/select00.png(edz 加密的真 PNG)做端到端验证。
"""

from __future__ import annotations

import io

import numpy as np
from PIL import Image

from ...registry import register_anm
from ...schema.anm import AnmFile, AnmInstr, TextureLoader, parse_scripts
from ...schema.archive import ArchiveBase
from .crypt import try_decrypt_from_table


@register_anm("th08", version=3)
class Th08AnmFile(AnmFile):
    """anm v3 解析类; 与 v2 的差异只有版本号(机制继承 schema.anm.AnmFile)。"""

    _EXPECTED_VERSION = 3
    ANM_FLAT_LAYOUT = True

    @staticmethod
    def parse_scripts(data: bytes) -> list[dict[int, list[AnmInstr]]]:
        """v3 脚本表: 键 = entry 内装载序号(0 起)。

        C++ 把脚本装进扁平数组按装载序寻址, 文件里存的 id 被忽略
        (AnmManager.cpp:2389 + 2620-2624; 实装数据里存的 id 是工具链
        残留 —— front.anm/etama.anm 等存在负 id)。
        """
        return [dict(enumerate(scripts.values())) for scripts in parse_scripts(data)]

    @classmethod
    def decrypt_entry(cls, data: bytes) -> bytes:
        """封包条目的 edz 内层解密(FileSystem::OpenFile 的
        TryDecryptFromTable, Global.cpp:901-927); 明文条目透传。
        引擎消费点(SpriteBank/StageScene)按鸭子接口调用本钩子。"""
        return try_decrypt_from_table(data)

    @classmethod
    def make_texture_loader(cls, archive: ArchiveBase) -> TextureLoader:
        """CreateTextureFromFile 链路的 Python 版: 封包取文件 → edz 内层解密
        → 图像解码 → colorKey 抠色, 返回 (w, h, RGBA 字节)。"""

        def loader(name: str, color_key: int, _fmt: int) -> tuple[int, int, bytes]:
            raw = None
            for key in (name, f"data/{name}"):
                try:
                    raw = archive.load(key)
                    break
                except KeyError:
                    continue
            if raw is None:
                raise ValueError(f"th08 外链纹理不在封包内: {name}")
            data = try_decrypt_from_table(raw)  # TryDecryptFromTable
            # D3DXCreateTextureFromFileInMemoryEx → Pillow 解码(按内容认
            # PNG/JPG/BMP; Pillow 是项目硬依赖, games 非 view 层禁 pygame)
            img = Image.open(io.BytesIO(data)).convert("RGBA")
            w, h = img.size
            px = np.asarray(img, dtype=np.uint8).reshape(h * w, 4).copy()
            ck = color_key & 0xFFFFFFFF
            if ck != 0:  # D3DX colorKey=0 表示禁用抠色
                kr, kg, kb = (ck >> 16) & 255, (ck >> 8) & 255, ck & 255
                hit = (px[:, 0] == kr) & (px[:, 1] == kg) & (px[:, 2] == kb)
                px[hit] = 0  # 命中色 → 透明黑
            return w, h, px.tobytes()

        return loader
