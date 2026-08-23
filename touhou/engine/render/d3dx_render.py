"""D3DX 工具库 + D3D8 固定管线的 Python 复刻接口 —— 软件渲染层共用。

约定与原版 D3DX(D3D8 时代)一致: 行向量约定(``v·M``, 矩阵行优先填充)、
左手坐标系、裁剪空间 ``w = view_z``、NDC ``z∈[0,1]``。``D3DXLikeRender``
持有相机/雾/视口/帧缓冲状态, 给定相机参数 + 纹理 quad 列表(.std 场景的
objects/instances + AnmVm)产出像素; 调用方(``engine/view/bg3d_view.py``,
未来 ModernGL 后端)只负责场景脚本推进与资源装配。本模块只依赖 numpy,
不 import pygame(engine 非 view 层的 AST 守护; pygame surface 的放大
blit 由调用侧完成)。

对照的 th07 反编译出处:
- ``Stage.cpp`` UpdateCamera: ``look_at_lh``/``perspective_fov_lh``
  (D3DXMatrixLookAtLH + D3DXMatrixPerspectiveFovLH, 视口 640x480,
  近 30 远 1800; lookAt 是相对 pos 的方向); RenderObjects: 实例剔除
  (1300^2/dot<60)、quad VM 定位、autoRotate==2 面向相机 quad 特判。
- ``AnmManager.cpp`` Draw3: ``rotation_xyz``/``quad_corner_offsets``
  (世界变换 world = diag · Rx · Ry · Rz + pos, 旋序 Rx→Ry→Rz);
  DrawFacingCamera: 投影求屏幕尺寸的面向相机 quad(手动雾含 alpha
  衰减); DrawInner: uvStart..uvEnd + uvScroll, 颜色调制 tex*vm.color,
  blendMode 0=alpha 1=additive; Draw: 屏幕空间 2D quad(vm1/vm2)。
- 雾: D3D 线性雾(D3DFOG_LINEAR) f = (far - z)/(far - near) 按顶点
  view-z 求值夹取 [0,1], 光栅化时逐像素应用(逐顶点插值的近似)。

管线形态(RenderObjects/OnDrawHighPrio/OnDrawLowPrio 顺序语义):
- vm1/vm2(屏幕空间 2D)画在 3D 场景之前; 清屏色在其后(C: color!=0 →
  Clear TARGET); 然后两个 pass(z_level 0/1 → 2/3)各自做两阶段深度,
  pass 间不共享深度。整帧顺序由 ``render`` 驱动(固定管线的一部分)。
- 深度两阶段(原版 zbuffer 的近似): A. 不透明 quad 近→远, 逐像素
  filled 掩码保证近者胜 + tile 级遮挡剔除; B. 半透明/加算 quad 远→近
  (画家算法), 被更近不透明几何完全遮住的整只跳过。半透明 quad 被
  不透明几何部分遮挡时可能误盖(无逐像素深度, 实测各关不明显)。
- 逐 quad 两三角形光栅化: bbox 内向量化(barycentric + 透视矫正 uv,
  最近邻采样; 原版 D3D8 默认 POINT 过滤), 雾因子按顶点 view-z 线性插值
  (原版像素/顶点雾的近似)。轴对齐 quad(billboard/2D overlay)走无
  barycentric 的缩放 blit 快路径; 近满屏大 quad 隔像素采样 + 2x2 块写回。
- 帧缓冲以雾色打底: 原版 color==0 时每帧只清 zbuffer, 未覆盖像素保留
  上一帧(几乎总是同色天空); 黑底会漏接缝。内部缓冲按 render_scale
  (默认 0.35)降采样光栅化(原版 POINT 采样+重雾, 降采样后观感接近;
  性能取舍), 调用侧负责 smoothscale 放大回 384x448。

数值约束(重构红线): 标量/向量化入口的逐元素表达式与 bg3d_view 原裸写
版本同序同结合律, 保证渲染逐像素不变; "零角跳过旋转项"是精度无关的
性能优化(cos(0)=1/sin(0)=0 与跳过该项等价), 语义在此保留。
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, NamedTuple, Sequence

import numpy as np

if TYPE_CHECKING:
    # 仅为类型注解引用(运行时不产生依赖): Stage/.std 数据结构在
    # touhou.schema, AnmVm/SpriteTex 在 engine.view.anm_vm。
    from ...schema.stage import Stage, StdQuad
    from ..view.anm_vm import AnmVm, SpriteTex

    # prepare 产物: (sort_z, pts4x2, uv4x2, w4, fog4, sprite, color, blend,
    # zwrite_disable)
    _DrawItem = tuple[float, np.ndarray, np.ndarray, np.ndarray, np.ndarray,
                      SpriteTex, list[int], int, int]

__all__ = [
    "D3DXLikeRender",
    "Projection",
    "GAME_H",
    "GAME_W",
    "GAME_X",
    "GAME_Y",
    "WIN_H",
    "WIN_W",
    "linear_fog_factor",
    "linear_fog_factor_vec",
    "look_at_lh",
    "normalize",
    "perspective_fov_lh",
    "quad_corner_offsets",
    "rotation_xyz",
]

WIN_W, WIN_H = 640, 480          # 原版 3D 视口(UpdateCamera 用其纵横比)
GAME_X, GAME_Y = 32, 16          # 游戏区在窗口中的位置
GAME_W, GAME_H = 384, 448

_NEAR, _FAR = 30.0, 1800.0       # UpdateCamera 投影近远面
_CULL_DIST_SQ = 1690000.0        # RenderObjects: 1300^2
_CULL_NEAR_DOT = 60.0            # RenderObjects: dotProd < 60 剔除
_CULL_RADIUS_PAD = 880.0
_COV_TILE = 4                    # 遮挡剔除 tile 边长(内部缓冲像素)


# ---- D3DX 工具层(矩阵/向量/雾公式) ----

def normalize(v: np.ndarray) -> np.ndarray:
    """D3DXVec3Normalize: 单位化; 近零向量返回零向量(避免除零)。"""
    n = float(np.linalg.norm(v))
    if n < 1e-12:
        return v * 0.0
    return v / n


def look_at_lh(pos: np.ndarray, direction: np.ndarray,
               up: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """D3DXMatrixLookAtLH(方向版): 返回 (view 4x4, cam_right)。

    原版 UpdateCamera 的 lookAt 是相对 pos 的朝向向量, 故此处第二参数
    是方向而非目标点。cam_right = normalize(cross(z, up))(= view 的
    -x 轴), 供 DrawFacingCamera 沿屏幕右方向取边缘点求投影尺寸。
    """
    z = normalize(direction)
    x = normalize(np.cross(up, z))
    y = np.cross(z, x)
    v = np.identity(4)
    v[0, :3] = [x[0], y[0], z[0]]
    v[1, :3] = [x[1], y[1], z[1]]
    v[2, :3] = [x[2], y[2], z[2]]
    v[3, :3] = [-float(np.dot(x, pos)),
                -float(np.dot(y, pos)),
                -float(np.dot(z, pos))]
    cam_right = normalize(np.cross(z, up))
    return v, cam_right


def perspective_fov_lh(fov_y: float, aspect: float, near: float,
                       far: float) -> np.ndarray:
    """D3DXMatrixPerspectiveFovLH: 垂直视场角透视投影(左手系)。"""
    tan = math.tan(fov_y / 2.0)
    p = np.zeros((4, 4))
    p[0, 0] = 1.0 / (tan * aspect)
    p[1, 1] = 1.0 / tan
    p[2, 2] = far / (far - near)
    p[2, 3] = 1.0
    p[3, 2] = -near * far / (far - near)
    return p


def rotation_xyz(rot: Sequence[float]) -> tuple[float, ...]:
    """Draw3 旋序 Rx·Ry·Rz(行向量约定)的 3x3 元素, 行优先 9 元组。

    三角全零返回精确单位阵: cos(0)=1/sin(0)=0, 与跳过该旋转项等价
    (原版同理的零角优化, 精度路径一致)。
    """
    if rot[0] == 0.0 and rot[1] == 0.0 and rot[2] == 0.0:
        return (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
    cx_, sx_ = math.cos(rot[0]), math.sin(rot[0])
    cy_, sy_ = math.cos(rot[1]), math.sin(rot[1])
    cz_, sz_ = math.cos(rot[2]), math.sin(rot[2])
    return (cy_ * cz_, cy_ * sz_, -sy_,
            sx_ * sy_ * cz_ - cx_ * sz_, sx_ * sy_ * sz_ + cx_ * cz_,
            sx_ * cy_,
            cx_ * sy_ * cz_ + sx_ * sz_, cx_ * sy_ * sz_ - sx_ * cz_,
            cx_ * cy_)


def quad_corner_offsets(hw: float, hh: float,
                        rot: Sequence[float]) -> tuple[float, ...]:
    """Draw3: world = diag(hw,hh) · Rx·Ry·Rz + pos 的角点偏移(12 元组)。

    对角线缩放已并入角点(±hw,±hh), 平移 +pos 由调用方逐实例加。
    零旋转快路径返回精确角点(同 rotation_xyz 的零角语义)。
    """
    if rot[0] == 0.0 and rot[1] == 0.0 and rot[2] == 0.0:
        return (-hw, -hh, 0.0, hw, -hh, 0.0, -hw, hh, 0.0, hw, hh, 0.0)
    m00, m01, m02, m10, m11, m12, m20, m21, m22 = rotation_xyz(rot)
    coff: list[float] = []
    for bx, by in ((-hw, -hh), (hw, -hh), (-hw, hh), (hw, hh)):
        coff += [bx * m00 + by * m10,
                 bx * m01 + by * m11,
                 bx * m02 + by * m12]
    return tuple(coff)


def linear_fog_factor(view_z: float, near: float, far: float) -> float:
    """D3D 线性雾因子(标量): f = (far - z) * (1/(far - near)), 夹取 [0,1]。

    乘倒数而非直除, 与调用方原实现的浮点路径逐位一致。
    """
    inv = 1.0 / max(1e-6, far - near)
    f = (far - view_z) * inv
    return 0.0 if f < 0.0 else (1.0 if f > 1.0 else f)


def linear_fog_factor_vec(view_z: np.ndarray, near: float,
                          far: float) -> np.ndarray:
    """linear_fog_factor 的向量化版(逐元素同式, np.clip 与分支同值)。"""
    inv = 1.0 / max(1e-6, far - near)
    return np.clip((far - view_z) * inv, 0.0, 1.0)


class Projection(NamedTuple):
    """世界点的投影结果。

    x/y: 视口缓冲坐标(全窗视口映射后平移到游戏区原点, 未乘
    render_scale); clip_z/clip_w: 裁剪空间原始值(排序键/深度范围判
    定用, 原版 DrawFacingCamera 保留 c2 作 sort key)。
    """

    x: float
    y: float
    clip_z: float
    clip_w: float

    @property
    def ndc_z(self) -> float:
        """透视除法后的 NDC 深度(∈[0,1] 才在视锥内)。"""
        return self.clip_z / self.clip_w


class D3DXLikeRender:
    """D3DX 系列渲染器: 相机/雾/视口/帧缓冲状态 + 固定管线光栅化。

    用法: ``set_stage(stage, obj_vms)`` 绑定场景几何与 quad VM(VM 生命
    周期归调用方, 每帧先执行脚本); 每帧 .std 脚本指令直写 cam_*/
    sky_fog_*/world_origin/clear_color 字段, 然后 ``render(vm1, vm2)``
    产出内部缓冲 (int(GAME_H*render_scale), int(GAME_W*render_scale), 3)
    uint8 RGB。render_scale<1 是性能折中(原版 POINT 采样+雾本身较糊,
    半分辨率平滑放大后观感接近)。
    """

    def __init__(self, render_scale: float = 0.35) -> None:
        self.render_scale = render_scale
        self.buf_w = max(1, int(GAME_W * render_scale))
        self.buf_h = max(1, int(GAME_H * render_scale))
        # ---- 每帧由场景脚本写入的状态(UpdateCamera/SetFog 等指令) ----
        # 相机(lookAt 是相对 pos 的方向)
        self.cam_pos = np.array([0.0, 0.0, 1000.0])
        self.cam_lookat = np.array([0.0, 0.0, 0.0])
        self.cam_up = np.array([0.0, 1.0, 0.0])
        self.cam_fov = math.pi / 6.0
        self.world_origin = np.zeros(3)         # .std opcode 0 世界原点
        self.clear_color = 0                    # .std opcode 13; 0 = 不清屏
        self.sky_fog_color = np.array([0.0, 0.0, 0.0])
        self.sky_fog_rgba = 0xFF000000
        self.sky_fog_near = 200.0
        self.sky_fog_far = 500.0
        # ---- 场景绑定(set_stage) ----
        self._stage: Stage | None = None
        self._obj_vms: list[list[AnmVm]] = []
        self._obj_pos = np.zeros((0, 3))
        self._obj_size = np.zeros((0, 3))
        self._obj_zlevel = np.zeros(0)
        self._obj_radius = np.zeros(0)
        self._inst_obj = np.zeros(0)
        self._inst_pos = np.zeros((0, 3))
        # ---- 派生缓存(update_camera 重建; 初始单位阵相机随时可投影) ----
        self.view = np.identity(4)
        self.proj = np.identity(4)
        self.vp = np.identity(4)
        self.look_at_dir = np.array([0.0, 0.0, 1.0])
        self.cam_right = np.array([1.0, 0.0, 0.0])
        self.cam_pos_t = (0.0, 0.0, 1000.0)
        self.cam_right_t = (1.0, 0.0, 0.0)
        self._vps: tuple[tuple[float, ...], ...] = tuple(
            tuple(1.0 if r == c else 0.0 for c in range(4))
            for r in range(4))
        self._vzs = (0.0, 0.0, 1.0, 0.0)
        # prepare 缓存: 每帧(render 一次)每 vm 的世界角点偏移/uv,
        # 同对象多实例(雾块等)仅平移不同, 旋转/uv 不必逐实例重算
        self._prep_seq = 0
        self._vm_cache: dict[int, tuple[int, tuple[float, ...], float, float,
                                        np.ndarray]] = {}

    # ---- 动态分辨率(帧超预算时降档; 纯视觉取舍, 不影响逻辑) ----
    def set_render_scale(self, scale: float) -> None:
        """切换内部光栅分辨率。帧缓冲/遮挡网格每帧按 buf_w/buf_h 现建,
        无其他随分辨率持久的缓存, 改这三个字段即可。"""
        self.render_scale = scale
        self.buf_w = max(1, int(GAME_W * scale))
        self.buf_h = max(1, int(GAME_H * scale))

    def set_stage(self, stage: Stage, obj_vms: list[list[AnmVm]]) -> None:
        """绑定场景几何与 per-object quad VM, 并做剔除预计算
        (RenderObjects 实例剔除的向量化)。"""
        self._stage = stage
        self._obj_vms = obj_vms
        objs = stage.objects
        self._obj_pos = np.array([o.pos for o in objs], dtype=float
                                 ).reshape(-1, 3) if objs else np.zeros((0, 3))
        self._obj_size = np.array([o.size for o in objs], dtype=float
                                  ).reshape(-1, 3) if objs else np.zeros((0, 3))
        self._obj_zlevel = np.array([o.z_level for o in objs])
        self._obj_radius = np.linalg.norm(self._obj_size, axis=1) / 2.0 \
            + _CULL_RADIUS_PAD if objs else np.zeros(0)
        self._inst_obj = np.array([i.object_idx for i in stage.instances])
        self._inst_pos = np.array([i.pos for i in stage.instances], dtype=float
                                  ).reshape(-1, 3) if stage.instances \
            else np.zeros((0, 3))

    # ---- 相机矩阵(UpdateCamera: LookAtLH + PerspectiveFovLH, 行向量约定) ----
    def update_camera(self) -> None:
        """从 cam_* 字段重建 view/proj/vp 与标量行缓存(每帧渲染一次)。"""
        view, cam_right = look_at_lh(self.cam_pos, self.cam_lookat,
                                     self.cam_up)
        proj = perspective_fov_lh(self.cam_fov, WIN_W / WIN_H, _NEAR, _FAR)
        vp = view @ proj
        self.view = view
        self.proj = proj
        self.vp = vp
        self.look_at_dir = normalize(self.cam_lookat)
        self.cam_right = cam_right
        # 标量缓存(逐元素 float(); 投影热路径不再碰 numpy 小数组)
        self._vps = tuple(
            tuple(float(vp[r, c]) for c in range(4)) for r in range(4))
        self._vzs = (float(view[0, 2]), float(view[1, 2]),
                     float(view[2, 2]), float(view[3, 2]))
        self.cam_pos_t = (float(self.cam_pos[0]), float(self.cam_pos[1]),
                          float(self.cam_pos[2]))
        self.cam_right_t = (float(cam_right[0]), float(cam_right[1]),
                            float(cam_right[2]))

    # ---- 世界 → 裁剪/视口(行向量 v·M, 逐元素表达式标量/向量版同序) ----
    def _clip(self, x: float, y: float,
              z: float) -> tuple[float, float, float, float]:
        m = self._vps
        c3 = m[3][3] + x * m[0][3] + y * m[1][3] + z * m[2][3]
        c0 = m[3][0] + x * m[0][0] + y * m[1][0] + z * m[2][0]
        c1 = m[3][1] + x * m[0][1] + y * m[1][1] + z * m[2][1]
        c2 = m[3][2] + x * m[0][2] + y * m[1][2] + z * m[2][2]
        return c0, c1, c2, c3

    def project(self, x: float, y: float, z: float) -> Projection | None:
        """世界点 → Projection; 近面内侧(w<1)返回 None。

        w<1 即顶点穿入近面, D3D 裁剪语义下整只图元丢弃(调用方逐顶点
        检查, 任一顶点 None 即弃)。
        """
        c0, c1, c2, c3 = self._clip(x, y, z)
        if c3 < 1.0:
            return None
        return Projection((c0 / c3 + 1.0) * (WIN_W / 2.0) - GAME_X,
                          (1.0 - c1 / c3) * (WIN_H / 2.0) - GAME_Y,
                          c2, c3)

    def project_no_clip(self, x: float, y: float,
                        z: float) -> tuple[float, float]:
        """不做近裁剪的投影(DrawFacingCamera 边缘点: 原版不检查直接除)。"""
        c0, c1, _c2, c3 = self._clip(x, y, z)
        return ((c0 / c3 + 1.0) * (WIN_W / 2.0) - GAME_X,
                (1.0 - c1 / c3) * (WIN_H / 2.0) - GAME_Y)

    def project_points(self, x: np.ndarray, y: np.ndarray,
                       z: np.ndarray) -> tuple[np.ndarray, np.ndarray,
                                               np.ndarray]:
        """向量化投影: (N,...) 世界坐标 → (视口 x, 视口 y, clip_w)。

        不做近裁剪(调用方按 clip_w 自行掩码, 与标量版剔除同式)。
        """
        m = self._vps
        c3 = m[3][3] + x * m[0][3] + y * m[1][3] + z * m[2][3]
        c0 = m[3][0] + x * m[0][0] + y * m[1][0] + z * m[2][0]
        c1 = m[3][1] + x * m[0][1] + y * m[1][1] + z * m[2][1]
        return ((c0 / c3 + 1.0) * (WIN_W / 2.0) - GAME_X,
                (1.0 - c1 / c3) * (WIN_H / 2.0) - GAME_Y, c3)

    def view_z(self, x: float, y: float, z: float) -> float:
        """view 空间 z(= clip w; 雾因子/排序键用, view 矩阵第 3 列点乘)。"""
        vz = self._vzs
        return vz[0] * x + vz[1] * y + vz[2] * z + vz[3]

    def view_z_points(self, x: np.ndarray, y: np.ndarray,
                      z: np.ndarray) -> np.ndarray:
        """view_z 的向量化版(逐元素同序)。"""
        vz = self._vzs
        return vz[0] * x + vz[1] * y + vz[2] * z + vz[3]

    # ---- 整帧渲染(OnDrawHighPrio: vm1/vm2 → 清屏 → pass(0/1);
    #      OnDrawLowPrio: pass(2/3); 顺序语义是固定管线的一部分) ----
    def render(self, vm1: AnmVm, vm2: AnmVm) -> np.ndarray:
        """渲染一帧, 返回内部缓冲 (buf_h, buf_w, 3) uint8 RGB。"""
        # 雾色打底: 原版 color==0 时每帧只清 zbuffer, 未被几何覆盖的像素
        # 保留上一帧内容(几乎总是同色天空/雾); 黑底会漏出接缝
        fb = np.empty((self.buf_h, self.buf_w, 3), dtype=np.uint8)
        fb[:, :] = np.clip(self.sky_fog_color, 0, 255).astype(np.uint8)
        # vm1/vm2(屏幕空间 2D, 画在 3D 之前, OnDrawHighPrio 顺序)
        for vm in (vm1, vm2):
            if vm.active_sprite_idx > 0:
                self._draw_2d(fb, vm)
        # 清屏色(opcode 13; C: color!=0 → Clear TARGET)
        if self.clear_color:
            c = self.clear_color
            fb[:, :] = ((c >> 16) & 255, (c >> 8) & 255, c & 255)
        self.update_camera()
        self._prep_seq += 1                     # prepare 缓存帧标签
        for pass_levels in ((0, 1), (2, 3)):
            self._render_pass(fb, pass_levels)
        return fb

    def _draw_2d(self, fb: np.ndarray, vm: AnmVm) -> None:
        """vm1/vm2: 屏幕空间 quad(AnmManager::Draw, rotation.z + scale)。"""
        spr = vm.sprite
        if spr is None or not vm.visible or not vm.active or vm.color[3] == 0:
            return
        hw = spr.w * vm.scale[0] / 2.0
        hh = spr.h * vm.scale[1] / 2.0
        z = vm.rotation[2]
        c, s = math.cos(z), math.sin(z)
        rs = self.render_scale
        hw *= rs
        hh *= rs
        x0 = (vm.pos[0] - GAME_X) * rs
        y0 = (vm.pos[1] - GAME_Y) * rs
        pts = []
        for wx, wy in ((-hw, -hh), (hw, -hh), (-hw, hh), (hw, hh)):
            pts.append([wx * c - wy * s + x0, wx * s + wy * c + y0])
        if vm.anchor & 1:
            for p in pts:
                p[0] += hw
        if vm.anchor & 2:
            for p in pts:
                p[1] += hh
        uv = np.array([[spr.u0, spr.v0], [spr.u1, spr.v0],
                       [spr.u0, spr.v1], [spr.u1, spr.v1]])
        uv[:, 0] += vm.uv_scroll[0]
        uv[:, 1] += vm.uv_scroll[1]
        w = np.ones(4)
        fog = np.ones(4)
        self._raster_quad(fb, np.array(pts), uv, w, fog, spr, vm.color,
                          vm.blend_mode)

    def _render_pass(self, fb: np.ndarray, levels: tuple[int, int]) -> None:
        stage = self._stage
        if stage is None or len(stage.instances) == 0:
            return
        # 实例剔除(向量化; RenderObjects 同式)
        oi = self._inst_obj
        valid = oi < len(stage.objects)
        centers = (self._obj_pos[oi[valid]] + self._inst_pos[valid]
                   - self.world_origin + self._obj_size[oi[valid]] / 2.0)
        rel = centers - self.cam_pos
        dist2 = np.einsum("ij,ij->i", rel, rel)
        dot = rel @ self.look_at_dir
        keep = (dist2 <= _CULL_DIST_SQ) & (dot <= self._obj_radius[oi[valid]]) \
            & (dot >= _CULL_NEAR_DOT)
        zmask = np.isin(self._obj_zlevel[oi[valid]], list(levels))
        inst_ids = np.nonzero(valid)[0][keep & zmask]
        # quad 准备: 同 (object,quad) 的实例分组, ≥3 只走向量化 prepare
        # (与 _prepare_quad 数值一致, 组内共享 vm 旋转/uv 缓存);
        # slots 按原 (实例,quad) 顺序回填, 保证 draw_list 顺序不变
        pairs = []
        for ii in inst_ids:
            inst = stage.instances[ii]
            obj = stage.objects[inst.object_idx]
            for qi, quad in enumerate(obj.quads):
                if quad.type == 0:
                    pairs.append((ii, inst.object_idx, qi))
        groups: dict[tuple[int, int], list[int]] = {}
        for pi, (ii, oi_, qi) in enumerate(pairs):
            groups.setdefault((oi_, qi), []).append(pi)
        slots: list[_DrawItem | None] = [None] * len(pairs)
        for (obj_i, qi), pis in groups.items():
            obj = stage.objects[obj_i]
            quad = obj.quads[qi]
            vm = self._obj_vms[obj_i][qi]
            if len(pis) >= 3 and vm.auto_rotate != 2:
                res = self._prepare_quad_group(
                    vm, quad, [pairs[pi][0] for pi in pis])
                for pi in pis:
                    slots[pi] = res.get(pairs[pi][0])
            else:
                ip = [pairs[pi][0] for pi in pis]
                for pi, ii in zip(pis, ip):
                    slots[pi] = self._prepare_quad(
                        vm, quad, self._inst_pos[ii])
        draw_list = [it for it in slots if it is not None]
        # 两阶段(等价原版 zbuffer 的近似, 见模块 docstring):
        # A. 不透明 quad 近→远, 逐像素 filled 掩码保证近者胜,
        #    tile 级遮挡剔除跳过被完全遮住的 quad;
        # B. 半透明/加算 quad 远→近(画家算法)盖在不透明结果上。
        opaque = []
        trans = []
        for item in draw_list:
            if item[7] == 0 and item[6][3] == 255 and item[5].opaque \
                    and not item[8]:
                opaque.append(item)
            else:
                trans.append(item)
        opaque.sort(key=lambda t: t[0])
        trans.sort(key=lambda t: -t[0])
        tw = (self.buf_w + _COV_TILE - 1) // _COV_TILE
        th = (self.buf_h + _COV_TILE - 1) // _COV_TILE
        covered = np.zeros((th, tw), dtype=bool)
        tile_depth = np.full((th, tw), np.inf)   # 各 tile 最近不透明深度
        filled = np.zeros((self.buf_h, self.buf_w), dtype=bool)
        # 同纹理/同混合的连续普通 quad 走批量光栅化(保序; 快路径 quad
        # 不参与, 仍逐只走 _raster_quad)。批量只是合并采样/着色的 numpy
        # 调用, 剔除/filled/混合写回的顺序语义逐项保持不变。
        i = 0
        n = len(opaque)
        while i < n:
            item = opaque[i]
            key = self._gen_batch_key(item)
            if key is not None:
                j = i + 1
                while j < n and self._gen_batch_key(opaque[j]) == key:
                    j += 1
                if j - i >= 2:
                    self._raster_gen_batch(fb, opaque[i:j], covered,
                                           tile_depth, filled)
                    i = j
                    continue
            pts = item[1]
            if covered.any():
                tx0 = max(0, int(pts[:, 0].min()) // _COV_TILE)
                tx1 = min(tw, int(pts[:, 0].max()) // _COV_TILE + 1)
                ty0 = max(0, int(pts[:, 1].min()) // _COV_TILE)
                ty1 = min(th, int(pts[:, 1].max()) // _COV_TILE + 1)
                if tx1 > tx0 and ty1 > ty0 \
                        and covered[ty0:ty1, tx0:tx1].all():
                    i += 1
                    continue
            self._raster_quad(fb, item[1], item[2], item[3], item[4],
                              item[5], item[6], item[7], covered, filled)
            # 记录已覆盖 tile 的最近不透明深度(供半透明阶段遮挡剔除)
            tx0 = max(0, int(pts[:, 0].min()) // _COV_TILE)
            tx1 = min(tw, int(pts[:, 0].max()) // _COV_TILE + 1)
            ty0 = max(0, int(pts[:, 1].min()) // _COV_TILE)
            ty1 = min(th, int(pts[:, 1].max()) // _COV_TILE + 1)
            if tx1 > tx0 and ty1 > ty0:
                zmin = float(item[3].min())
                region = tile_depth[ty0:ty1, tx0:tx1]
                cov_region = covered[ty0:ty1, tx0:tx1]
                np.minimum(region, np.where(cov_region, zmin, np.inf),
                           out=region)
            i += 1
        i = 0
        n = len(trans)
        while i < n:
            item = trans[i]
            key = self._gen_batch_key(item)
            if key is not None:
                j = i + 1
                while j < n and self._gen_batch_key(trans[j]) == key:
                    j += 1
                if j - i >= 2:
                    self._raster_gen_batch(fb, trans[i:j], covered,
                                           tile_depth, None)
                    i = j
                    continue
            # 被更近不透明几何完全遮住的半透明 quad 整只跳过
            if covered.any():
                pts = item[1]
                tx0 = max(0, int(pts[:, 0].min()) // _COV_TILE)
                tx1 = min(tw, int(pts[:, 0].max()) // _COV_TILE + 1)
                ty0 = max(0, int(pts[:, 1].min()) // _COV_TILE)
                ty1 = min(th, int(pts[:, 1].max()) // _COV_TILE + 1)
                if tx1 > tx0 and ty1 > ty0:
                    zmin = float(item[3].min())
                    if (covered[ty0:ty1, tx0:tx1]
                            & (tile_depth[ty0:ty1, tx0:tx1] < zmin)).all():
                        i += 1
                        continue
            self._raster_quad(fb, item[1], item[2], item[3], item[4],
                              item[5], item[6], item[7], None, None)
            i += 1

    # ---- quad prepare(世界变换 + 投影 + 雾因子; Draw3/DrawFacingCamera) ----
    def _prepare_quad(self, vm: AnmVm, quad: StdQuad,
                      ipos: np.ndarray) -> _DrawItem | None:
        """返回 _DrawItem 或 None(剔除)。

        投影/雾经标量热路径(update_camera 已缓存矩阵行)。
        """
        spr = vm.sprite
        if spr is None or not vm.visible or not vm.active or vm.color[3] == 0:
            return None
        # 微透明 quad 跳过(原版也画但对画面贡献不可测; 性能取舍, 见 docstring)
        if vm.color[3] < 8:
            return None
        # RenderObjects case 0: vm.pos = offset + quad.pos + inst.pos - stage.pos
        px = float(vm.offset[0]) + quad.pos[0] + float(ipos[0]) \
            - self.world_origin[0]
        py = float(vm.offset[1]) + quad.pos[1] + float(ipos[1]) \
            - self.world_origin[1]
        pz = float(vm.offset[2]) + quad.pos[2] + float(ipos[2]) \
            - self.world_origin[2]
        sx, sy = vm.scale
        if quad.size[0] != 0.0:
            sx = quad.size[0] / spr.w
        if quad.size[1] != 0.0:
            sy = quad.size[1] / spr.h
        if vm.auto_rotate == 2:
            return self._prepare_billboard(vm, quad, (px, py, pz), spr, sx)
        coff, ax, ay, uv = self._quad_vm_cache(vm, quad, spr, sx, sy)
        tx = px + ax
        ty = py + ay
        rs = self.render_scale
        fog_near = self.sky_fog_near
        fog_far = self.sky_fog_far
        pts: list[tuple[float, float]] = []
        ws: list[float] = []
        fogs: list[float] = []
        zsum = 0.0
        for i in range(0, 12, 3):
            x = coff[i] + tx
            y = coff[i + 1] + ty
            z = coff[i + 2] + pz
            pr = self.project(x, y, z)
            if pr is None:                      # 顶点穿近面(w<1), 整只丢弃
                return None
            pts.append((pr.x * rs, pr.y * rs))
            ws.append(pr.clip_w)
            zv = self.view_z(x, y, z)
            zsum += zv
            fogs.append(linear_fog_factor(zv, fog_near, fog_far))
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        if max(xs) < 0 or min(xs) >= self.buf_w or max(ys) < 0 \
                or min(ys) >= self.buf_h:
            return None
        return (zsum * 0.25, np.array(pts), uv, np.array(ws),
                np.array(fogs), spr, vm.color, vm.blend_mode,
                vm.zwrite_disable)

    def _quad_vm_cache(self, vm: AnmVm, quad: StdQuad, spr: SpriteTex,
                       sx: float, sy: float) -> tuple[tuple[float, ...],
                                                      float, float,
                                                      np.ndarray]:
        """每帧每 vm 缓存: 旋转后世界角点偏移(12 float)/锚点位移/uv。

        同对象多实例(雾块等)共享 vm, 仅平移不同, 命中后省去旋转矩阵
        与 uv 构建。供 _prepare_quad/_prepare_quad_group 共用。
        """
        ck = self._vm_cache.get(id(vm))
        if ck is not None and ck[0] == self._prep_seq:
            return ck[1], ck[2], ck[3], ck[4]
        hw = spr.w * sx / 2.0
        hh = spr.h * sy / 2.0
        ax = abs(hw) if vm.anchor & 1 else 0.0
        ay = abs(hh) if vm.anchor & 2 else 0.0
        # Draw3: world = diag(hw/128 已并入角点) · Rx · Ry · Rz + pos
        # (旋转/零角优化在 quad_corner_offsets)
        coff = quad_corner_offsets(hw, hh, vm.rotation)
        su, sv = vm.uv_scroll[0], vm.uv_scroll[1]
        uv = np.array([[spr.u0 + su, spr.v0 + sv],
                       [spr.u1 + su, spr.v0 + sv],
                       [spr.u0 + su, spr.v1 + sv],
                       [spr.u1 + su, spr.v1 + sv]])
        self._vm_cache[id(vm)] = (self._prep_seq, coff, ax, ay, uv)
        return coff, ax, ay, uv

    def _prepare_quad_group(self, vm: AnmVm, quad: StdQuad,
                            iis: list[int]) -> dict[int, _DrawItem]:
        """同 (object,quad) 多实例的向量化 prepare, 返回 {instance_idx: item}。

        同组共享 vm(可见性检查结果一致, 检查一次); 投影/雾/bbox 剔除按
        实例向量化, 逐元素表达式与 _prepare_quad 标量版同序(zsum 用显
        式左连加保持结合序), 数值结果一致。剔除的实例不出现在字典里。
        """
        spr = vm.sprite
        if spr is None or not vm.visible or not vm.active or vm.color[3] == 0:
            return {}
        if vm.color[3] < 8:
            return {}
        coff, ax, ay, uv = self._quad_vm_cache(vm, quad, spr,
                                               *self._quad_scale(vm, quad,
                                                                 spr))
        IP = self._inst_pos[np.asarray(iis)]            # (N,3)
        # 与标量版相同的加括号顺序: ((off+qpos)+ipos)-spos (+anchor)
        b0 = float(vm.offset[0]) + quad.pos[0]
        b1 = float(vm.offset[1]) + quad.pos[1]
        b2 = float(vm.offset[2]) + quad.pos[2]
        tx = (b0 + IP[:, 0]) - self.world_origin[0] + ax
        ty = (b1 + IP[:, 1]) - self.world_origin[1] + ay
        pz = (b2 + IP[:, 2]) - self.world_origin[2]
        C = np.asarray(coff).reshape(4, 3)
        X = C[None, :, 0] + tx[:, None]               # (N,4) 世界角点
        Y = C[None, :, 1] + ty[:, None]
        Z = C[None, :, 2] + pz[:, None]
        sx_b, sy_b, c3 = self.project_points(X, Y, Z)
        rs = self.render_scale
        sx_px = sx_b * rs
        sy_px = sy_b * rs
        zv = self.view_z_points(X, Y, Z)
        zsum = zv[:, 0] + zv[:, 1] + zv[:, 2] + zv[:, 3]
        fg = linear_fog_factor_vec(zv, self.sky_fog_near, self.sky_fog_far)
        keep = (c3 >= 1.0).all(axis=1)
        keep &= ~((sx_px.max(axis=1) < 0)
                  | (sx_px.min(axis=1) >= self.buf_w)
                  | (sy_px.max(axis=1) < 0)
                  | (sy_px.min(axis=1) >= self.buf_h))
        out = {}
        for k, ii in enumerate(iis):
            if not keep[k]:
                continue
            out[ii] = (float(zsum[k]) * 0.25,
                       np.stack((sx_px[k], sy_px[k]), axis=1), uv,
                       c3[k].copy(), fg[k].copy(), spr, vm.color,
                       vm.blend_mode, vm.zwrite_disable)
        return out

    @staticmethod
    def _quad_scale(vm: AnmVm, quad: StdQuad,
                    spr: SpriteTex) -> tuple[float, float]:
        sx, sy = vm.scale
        if quad.size[0] != 0.0:
            sx = quad.size[0] / spr.w
        if quad.size[1] != 0.0:
            sy = quad.size[1] / spr.h
        return sx, sy

    def _prepare_billboard(self, vm: AnmVm, quad: StdQuad,
                           pos: tuple[float, float, float], spr: SpriteTex,
                           sx: float) -> _DrawItem | None:
        """autoRotate==2: 面向相机 quad(RenderObjects 特判 + DrawFacingCamera)。"""
        x, y, z = pos
        pr = self.project(x, y, z)
        if pr is None:                          # 中心点穿近面(w<1)
            return None
        ndc_z = pr.ndc_z
        if ndc_z < 0.0 or ndc_z > 1.0:
            return None
        var_98 = quad.size[0] if quad.size[0] != 0.0 else spr.w
        off = var_98 * sx
        cr = self.cam_right_t
        x2 = x + cr[0] * off
        y2 = y + cr[1] * off
        z2 = z + cr[2] * off
        ex_b, ey_b = self.project_no_clip(x2, y2, z2)   # 边缘点原版不裁剪
        rs = self.render_scale
        cx = pr.x * rs
        cy = pr.y * rs
        ex = ex_b * rs
        ey = ey_b * rs
        scale = math.hypot(ex - cx, ey - cy) / var_98
        hw = spr.w * scale / 2.0
        hh = spr.h * scale / 2.0
        # 手动雾(3D 距离; alpha 也衰减, 见 RenderObjects)
        color = vm.color
        cam = self.cam_pos_t
        dist = math.sqrt((x - cam[0]) ** 2 + (y - cam[1]) ** 2
                         + (z - cam[2]) ** 2)
        if self.sky_fog_near < dist:
            f = (self.sky_fog_near - dist) / (self.sky_fog_near
                                              - self.sky_fog_far)
            if f >= 1.0:
                return None
            fc = self.sky_fog_color
            color = [int(color[k] - (color[k] - float(fc[k])) * f)
                     for k in range(3)] + [int(color[3] * (1.0 - f))]
        if vm.anchor & 1:
            cx += hw
        if vm.anchor & 2:
            cy += hh
        pts = np.array([[cx - hw, cy - hh], [cx + hw, cy - hh],
                        [cx - hw, cy + hh], [cx + hw, cy + hh]])
        su, sv = vm.uv_scroll[0], vm.uv_scroll[1]
        uv = np.array([[spr.u0 + su, spr.v0 + sv], [spr.u1 + su, spr.v0 + sv],
                       [spr.u0 + su, spr.v1 + sv], [spr.u1 + su, spr.v1 + sv]])
        return (pr.clip_z, pts, uv, np.full(4, pr.clip_w), np.ones(4), spr,
                color, vm.blend_mode, vm.zwrite_disable)

    # ---- 光栅化(两三角形, bbox 向量化, 透视矫正 uv, 最近邻) ----
    def _gen_batch_key(self, item: _DrawItem) -> tuple[int, int] | None:
        """普通光栅化 quad 的批量分组键(同纹理+同混合), 否则 None。

        与 _raster_quad 的派发条件保持一致: 全雾/轴对齐匀 w 走快路径;
        近满屏大 quad(step==2 抽样)不批量——其 2x2 块写回与 filled 标
        记存在粒度差(原版逐只处理亦然), 单独走原路径。
        """
        fog = item[4]
        if fog[0] <= 0.004 and fog[1] <= 0.004 and fog[2] <= 0.004 \
                and fog[3] <= 0.004:
            return None
        p = item[1]
        x0, x1, x2, x3 = (float(p[0, 0]), float(p[1, 0]),
                          float(p[2, 0]), float(p[3, 0]))
        y0, y1, y2, y3 = (float(p[0, 1]), float(p[1, 1]),
                          float(p[2, 1]), float(p[3, 1]))
        if abs(x0 - x2) < 0.02 and abs(x1 - x3) < 0.02 \
                and abs(y0 - y1) < 0.02 and abs(y2 - y3) < 0.02:
            w = item[3]
            wmin = min(float(w[0]), float(w[1]), float(w[2]), float(w[3]))
            wmax = max(float(w[0]), float(w[1]), float(w[2]), float(w[3]))
            if wmin > 0 and (wmax - wmin) < 0.005 * wmin:
                return None
        H, W = self.buf_h, self.buf_w
        bx0 = max(0, int(math.floor(min(x0, x1, x2, x3))))
        bx1 = min(W, int(math.ceil(max(x0, x1, x2, x3))))
        by0 = max(0, int(math.floor(min(y0, y1, y2, y3))))
        by1 = min(H, int(math.ceil(max(y0, y1, y2, y3))))
        bw, bh = bx1 - bx0, by1 - by0
        if bw * bh > (H * W) // 5 and min(bw, bh) >= 8:
            return None                      # step==2 大 quad 不批量
        return (id(item[5].tex), item[7])

    def _raster_gen_batch(self, fb: np.ndarray, items: list[_DrawItem],
                          covered: np.ndarray, tile_depth: np.ndarray,
                          filled: np.ndarray | None) -> None:
        """同纹理/同混合的连续普通 quad 批量光栅化(保序)。

        逐 quad 保持 _render_pass + _raster_quad_pts 的顺序语义(遮挡剔
        除/tile 深度/filled 过滤逐项生效); 透视矫正采样/纹理取色/雾/着
        色合并成一次跨 quad 大数组运算; 写回逐 quad(不透明只写未填像
        素, 半透明按序读 dst 混合)。批量项 step 恒为 1(大 quad 已被
        _gen_batch_key 排除)。
        """
        H, W = fb.shape[:2]
        th, tw = covered.shape
        opaque = filled is not None
        recs = []                # (gi, gj) 帧缓冲像素下标
        counts = []
        Xs, Ys = [], []          # 像素屏幕坐标(float32)
        inAs = []                # (mA, yi, xi) 延迟 gather(仅雾变化时用)
        coef = []                # (au,bu,cu, av,bv,cv, ai,bi,ci)
        fogcf = []               # (aA,bA,cA, aB,bB,cB) 雾仿射系数
        tints = []
        any_fog = False
        vary_fog = False
        for item in items:
            _z, pts, uv, w, fog, spr, color, blend, _zw = item
            px = pts[:, 0].tolist()
            py = pts[:, 1].tolist()
            wl = w.tolist()
            xmin = min(px)
            xmax = max(px)
            ymin = min(py)
            ymax = max(py)
            tx0 = max(0, int(xmin) // _COV_TILE)
            tx1 = min(tw, int(xmax) // _COV_TILE + 1)
            ty0 = max(0, int(ymin) // _COV_TILE)
            ty1 = min(th, int(ymax) // _COV_TILE + 1)
            zmin = min(wl)

            def upd_tile() -> None:   # 与 _render_pass 相同的 tile 深度更新
                if opaque and tx1 > tx0 and ty1 > ty0:
                    cr = covered[ty0:ty1, tx0:tx1]
                    if cr.any():   # 无覆盖 tile 时与原版的 minimum 同效(空操作)
                        region = tile_depth[ty0:ty1, tx0:tx1]
                        np.minimum(region, np.where(cr, zmin, np.inf),
                                   out=region)

            # 遮挡剔除(与 _render_pass 同序同式)
            if covered.any() and tx1 > tx0 and ty1 > ty0:
                if opaque:
                    if covered[ty0:ty1, tx0:tx1].all():
                        continue
                elif (covered[ty0:ty1, tx0:tx1]
                        & (tile_depth[ty0:ty1, tx0:tx1] < zmin)).all():
                    continue
            # bbox(与 _raster_quad_pts 同式)
            x0 = max(0, int(math.floor(xmin)))
            x1 = min(W, int(math.ceil(xmax)))
            y0 = max(0, int(math.floor(ymin)))
            y1 = min(H, int(math.ceil(ymax)))
            if x1 <= x0 or y1 <= y0:
                upd_tile()
                continue
            xr = np.arange(x0, x1, dtype=np.float32) + 0.5
            yr = np.arange(y0, y1, dtype=np.float32) + 0.5
            # 三角形系数(标量; 与 _raster_quad_pts 同式, 跳过退化)
            cfs = []
            for tri in ((0, 1, 2), (1, 3, 2)):
                i0, i1, i2 = tri
                xa, ya = px[i0], py[i0]
                xb, yb = px[i1], py[i1]
                xc, yc = px[i2], py[i2]
                d = (yb - yc) * (xa - xc) + (xc - xb) * (ya - yc)
                if abs(d) < 1e-9:
                    continue
                cfs.append((tri, (yb - yc) / d, (xc - xb) / d,
                            (yc - ya) / d, (xa - xc) / d, xc, yc))
            if not cfs:
                upd_tile()
                continue
            # 两三角堆叠成 (2,bh,bw) 一次算 mask: 逐元素运算与逐三角
            # 版本同序(a0*(x-xc)+b0*(y-yc) → 比较), 结果逐位相同
            if len(cfs) == 2:
                C = np.array([c[1:] for c in cfs])
            else:
                C = np.array([cfs[0][1:], (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)])
            xc_ = C[:, 4][:, None, None]
            yc_ = C[:, 5][:, None, None]
            du = xr[None, None, :] - xc_                # (2,1,bw)
            dv = yr[None, :, None] - yc_                # (2,bh,1)
            l0 = C[:, 0][:, None, None] * du + C[:, 1][:, None, None] * dv
            l1 = C[:, 2][:, None, None] * du + C[:, 3][:, None, None] * dv
            m = (l0 >= -1e-4) & (l1 >= -1e-4)
            l0 += l1
            m &= l0 <= 1.0001
            if len(cfs) == 1:
                m[1] = False                            # 屏蔽缺失的第二三角
            tris = [(cfs[0][0], m[0]) + cfs[0][1:]]
            if len(cfs) == 2:
                tris.append((cfs[1][0], m[1]) + cfs[1][1:])
            mask = m[0] | m[1]
            yi, xi = np.nonzero(mask)
            if yi.size == 0:
                upd_tile()
                continue
            if opaque:
                assert filled is not None     # opaque == filled is not None
                keep = ~filled[yi + y0, xi + x0]
                if not keep.any():
                    self._cov_mark(covered, mask, x0, y0)
                    upd_tile()
                    continue
                yi, xi = yi[keep], xi[keep]
                # 提前回填(=原版写回集合, 供后续 quad 过滤; 顺序等价)
                filled[yi + y0, xi + x0] = True
                self._cov_mark(covered, mask, x0, y0)
            upd_tile()
            # 仿射拟合(与 _raster_quad_pts 同式)
            tri0, mA, a0, b0, a1, b1, p2x, p2y = tris[0]
            i0, i1, i2 = tri0

            def _fit(g0: float, g1: float,
                     g2: float) -> tuple[float, float, float]:
                A = a0 * (g0 - g2) + a1 * (g1 - g2)
                B = b0 * (g0 - g2) + b1 * (g1 - g2)
                return A, B, g2 - A * p2x - B * p2y

            w0, w1, w2 = wl[i0], wl[i1], wl[i2]
            coef.append(_fit(uv[i0, 0] / w0, uv[i1, 0] / w1,
                             uv[i2, 0] / w2)
                        + _fit(uv[i0, 1] / w0, uv[i1, 1] / w1,
                               uv[i2, 1] / w2)
                        + _fit(1.0 / w0, 1.0 / w1, 1.0 / w2))
            if fog[0] < 0.996 or fog[1] < 0.996 or fog[2] < 0.996 \
                    or fog[3] < 0.996:
                any_fog = True
                if fog[0] == fog[1] == fog[2] == fog[3]:
                    c = float(fog[0])
                    fogcf.append((0.0, 0.0, c, 0.0, 0.0, c))
                else:
                    vary_fog = True
                    fA = _fit(float(fog[i0]), float(fog[i1]),
                              float(fog[i2]))
                    if len(tris) == 2:
                        j0, j1, j2 = tris[1][0]
                        _mB, a0b, b0b, a1b, b1b, p2xb, p2yb = tris[1][1:]
                        fj2 = float(fog[j2])
                        A = a0b * (float(fog[j0]) - fj2) \
                            + a1b * (float(fog[j1]) - fj2)
                        B = b0b * (float(fog[j0]) - fj2) \
                            + b1b * (float(fog[j1]) - fj2)
                        fB = (A, B, fj2 - A * p2xb - B * p2yb)
                    else:
                        fB = fA
                    fogcf.append(fA + fB)
            else:
                fogcf.append((0.0, 0.0, 1.0, 0.0, 0.0, 1.0))
            recs.append((yi + y0, xi + x0))
            counts.append(yi.size)
            Xs.append(xr[xi])
            Ys.append(yr[yi])
            inAs.append((mA, yi, xi))
            tints.append(color)
        if not recs:
            return
        # ---- 跨 quad 合并采样/着色(数值与逐 quad 路径同式同 dtype) ----
        K = len(recs)
        gid = np.repeat(np.arange(K), counts)
        X = np.concatenate(Xs)
        Y = np.concatenate(Ys)
        cg = np.array(coef)[gid]
        iws = cg[:, 6] * X + cg[:, 7] * Y + cg[:, 8]
        iws = np.where(np.abs(iws) < 1e-12, 1e-12, iws)
        u = (cg[:, 0] * X + cg[:, 1] * Y + cg[:, 2]) / iws
        v = (cg[:, 3] * X + cg[:, 4] * Y + cg[:, 5]) / iws
        spr = items[0][5]
        tex = spr.tex
        th, tw = tex.shape[:2]
        # uv 恒非负(scroll 归一到 [0,1) 后加在正 uv 上), int 截断即 floor
        tu = (u * tw).astype(np.int32) % tw
        tv = (v * th).astype(np.int32) % th
        src = tex[tv, tu].astype(np.float32)
        tg = (np.array(tints, dtype=np.float32) * (1.0 / 255.0))[gid]
        rgb = src[:, :3] * tg[:, :3]
        # 雾(逐三角形仿射; 无雾 quad 系数取 fg=1, rgb*1+fc*0 数值不变)
        if any_fog:
            fgq = np.array(fogcf)
            fgA = fgq[:, 0][gid] * X + fgq[:, 1][gid] * Y + fgq[:, 2][gid]
            if vary_fog:
                inA = np.concatenate([mA[yi, xi] for mA, yi, xi in inAs])
                fgB = fgq[:, 3][gid] * X + fgq[:, 4][gid] * Y \
                    + fgq[:, 5][gid]
                fg = np.where(inA, fgA, fgB)
            else:
                fg = fgA
            fg = fg[:, None]
            fog_rgb = self.sky_fog_color.astype(np.float32)
            rgb = rgb * fg + fog_rgb * (1.0 - fg)
        # ---- 逐 quad 写回(保序) ----
        off = 0
        blend = items[0][7]
        if opaque:
            out_all = np.clip(rgb, 0, 255).astype(np.uint8)
            for gi, gj in recs:
                n_ = gi.shape[0]
                fb[gi, gj] = out_all[off:off + n_]
                off += n_
        else:
            a_all = src[:, 3:4] * tg[:, 3:4]
            for gi, gj in recs:
                n_ = gi.shape[0]
                rgb_s = rgb[off:off + n_]
                a = a_all[off:off + n_]
                dst = fb[gi, gj].astype(np.float32)
                if blend == 1:      # DESTBLEND ONE(加算)
                    out = dst + rgb_s * (a * (1.0 / 255.0))
                else:               # SRCALPHA / INVSRCALPHA
                    out = rgb_s * (a * (1.0 / 255.0)) \
                        + dst * (1.0 - a * (1.0 / 255.0))
                fb[gi, gj] = np.clip(out, 0, 255).astype(np.uint8)
                off += n_

    def _raster_quad(self, fb: np.ndarray, pts: np.ndarray, uv: np.ndarray,
                     w: np.ndarray, fog: np.ndarray, spr: SpriteTex,
                     color: Sequence[int], blend_mode: int,
                     cov: np.ndarray | None = None,
                     filled: np.ndarray | None = None) -> None:
        # 全雾快捷路径: 4 顶点雾因子≈0 → 输出即雾色(D3D 雾压过纹理),
        # 不透明普通混合直接填矩形, 其余(加算/半透明)影响可忽略, 跳过
        if fog[0] <= 0.004 and fog[1] <= 0.004 and fog[2] <= 0.004 \
                and fog[3] <= 0.004:
            if blend_mode == 0 and color[3] == 255:
                x0 = max(0, int(math.floor(float(pts[:, 0].min()))))
                x1 = min(self.buf_w, int(math.ceil(float(pts[:, 0].max()))))
                y0 = max(0, int(math.floor(float(pts[:, 1].min()))))
                y1 = min(self.buf_h, int(math.ceil(float(pts[:, 1].max()))))
                if x1 > x0 and y1 > y0:
                    fb[y0:y1, x0:x1] = np.clip(self.sky_fog_color, 0, 255
                                               ).astype(np.uint8)
                    if cov is not None:
                        self._cov_fill(cov, x0, y0, x1, y1)
                    if filled is not None:
                        filled[y0:y1, x0:x1] = True
            return
        # 轴对齐矩形快路径(billboard/2D overlay: 屏幕对齐 quad 的缩放 blit;
        # w 四顶点近似相等时仿射=透视, 误差不可测)
        p = pts
        if abs(p[0, 0] - p[2, 0]) < 0.02 and abs(p[1, 0] - p[3, 0]) < 0.02 \
                and abs(p[0, 1] - p[1, 1]) < 0.02 \
                and abs(p[2, 1] - p[3, 1]) < 0.02:
            wmin = float(w.min())
            if wmin > 0 and (float(w.max()) - wmin) < 0.005 * wmin:
                self._blit_rect(fb, p, uv, spr, color, blend_mode, cov,
                                filled)
                return
        self._raster_quad_pts(fb, pts, uv, w, fog, spr, color, blend_mode,
                              cov, filled)

    def _blit_rect(self, fb: np.ndarray, p: np.ndarray, uv: np.ndarray,
                   spr: SpriteTex, color: Sequence[int], blend_mode: int,
                   cov: np.ndarray | None, filled: np.ndarray | None) -> None:
        """屏幕轴对齐 quad 的最近邻缩放 blit(向量化切片, 无 barycentric)。"""
        H, W = fb.shape[:2]
        xa, ya = float(p[0, 0]), float(p[0, 1])
        xb, yb = float(p[3, 0]), float(p[3, 1])
        wdt = xb - xa
        hgt = yb - ya
        if wdt <= 0.0 or hgt <= 0.0:
            return
        dx0 = max(0, int(math.floor(xa)))
        dy0 = max(0, int(math.floor(ya)))
        dx1 = min(W, int(math.ceil(xb)))
        dy1 = min(H, int(math.ceil(yb)))
        if dx1 <= dx0 or dy1 <= dy0:
            return
        tex = spr.tex
        th, tw = tex.shape[:2]
        # 大 rect 隔像素采样 + 2x2 repeat(天空等大平面; 细条不抽取防虚线)
        bw = dx1 - dx0
        bh = dy1 - dy0
        step = 2 if bw * bh > (H * W) // 4 and min(bw, bh) >= 8 else 1
        us = uv[0, 0] + ((np.arange(dx0, dx1, dtype=np.float32)[::step] + 0.5 - xa)
                         / wdt) * (uv[1, 0] - uv[0, 0])
        vs = uv[0, 1] + ((np.arange(dy0, dy1, dtype=np.float32)[::step] + 0.5 - ya)
                         / hgt) * (uv[2, 1] - uv[0, 1])
        tu = (us * tw).astype(np.int32) % tw
        tv = (vs * th).astype(np.int32) % th
        src = tex[tv][:, tu].astype(np.float32)
        tint = np.array(color, dtype=np.float32) * (1.0 / 255.0)
        rgb = src[:, :, :3] * tint[:3]
        if filled is not None:
            # 不透明阶段: 只写未填充像素, 不读 dst 不混合
            if step > 1:
                rgb = np.repeat(np.repeat(rgb, 2, axis=0), 2, axis=1)
            h2 = min(rgb.shape[0], dy1 - dy0)
            w2 = min(rgb.shape[1], dx1 - dx0)
            rgb = np.clip(rgb[:h2, :w2], 0, 255).astype(np.uint8)
            keep = ~filled[dy0:dy0 + h2, dx0:dx0 + w2]
            region = fb[dy0:dy0 + h2, dx0:dx0 + w2]
            region[keep] = rgb[keep]
            filled[dy0:dy0 + h2, dx0:dx0 + w2] = True
            if cov is not None:
                self._cov_fill(cov, dx0, dy0, dx0 + w2, dy0 + h2)
            return
        a = src[:, :, 3:4] * tint[3]
        if step > 1:
            rgb = np.repeat(np.repeat(rgb, 2, axis=0), 2, axis=1)
            a = np.repeat(np.repeat(a, 2, axis=0), 2, axis=1)
            h2 = min(rgb.shape[0], dy1 - dy0)
            w2 = min(rgb.shape[1], dx1 - dx0)
            rgb = rgb[:h2, :w2]
            a = a[:h2, :w2]
        else:
            h2 = dy1 - dy0
            w2 = dx1 - dx0
        dst = fb[dy0:dy0 + h2, dx0:dx0 + w2].astype(np.float32)
        if blend_mode == 1:
            out = dst + rgb * (a * (1.0 / 255.0))
        else:
            out = rgb * (a * (1.0 / 255.0)) + dst * (1.0 - a * (1.0 / 255.0))
        fb[dy0:dy0 + h2, dx0:dx0 + w2] = np.clip(out, 0, 255).astype(np.uint8)

    @staticmethod
    def _cov_mark(cov: np.ndarray, mask: np.ndarray, x0: int, y0: int) -> None:
        """把光栅 mask 中完全被覆盖的 4x4 tile 标进遮挡网格。"""
        h, w = mask.shape
        h4, w4 = h // _COV_TILE, w // _COV_TILE
        if h4 == 0 or w4 == 0:
            return
        blocks = mask[:h4 * _COV_TILE, :w4 * _COV_TILE].reshape(
            h4, _COV_TILE, w4, _COV_TILE)
        full = blocks.all(axis=(1, 3))
        th, tw = cov.shape
        gy, gx = y0 // _COV_TILE, x0 // _COV_TILE
        cov[gy:gy + h4, gx:gx + w4] |= full[:th - gy, :tw - gx]

    @staticmethod
    def _cov_fill(cov: np.ndarray, x0: int, y0: int, x1: int, y1: int) -> None:
        th, tw = cov.shape
        gx0, gy0 = (x0 + _COV_TILE - 1) // _COV_TILE, (y0 + _COV_TILE - 1) // _COV_TILE
        gx1, gy1 = x1 // _COV_TILE, y1 // _COV_TILE
        if gx1 > gx0 and gy1 > gy0:
            cov[gy0:gy1, gx0:gx1] = True

    def _raster_quad_pts(self, fb: np.ndarray, pts: np.ndarray,
                         uv: np.ndarray, w: np.ndarray, fog: np.ndarray,
                         spr: SpriteTex, color: Sequence[int], blend_mode: int,
                         cov: np.ndarray | None = None,
                         filled: np.ndarray | None = None) -> None:
        """单 quad(两三角形共享 bbox 一次光栅化)。

        两三角形 mask → 透视矫正 uv → 最近邻采样 → 雾 → 混合。
        u/w, v/w, 1/w 对平面 quad 在屏幕空间是仿射函数, 用三顶点拟合
        一次仿射即与逐三角形 barycentric 同值(barycentric 插值就是过
        三顶点的仿射函数), 省掉逐像素的三角形挑选; 雾仍逐三角形仿射。
        filled 非空时为不透明阶段: 只写未填充像素并回填 filled(近者胜),
        且不读 dst 不混合(opaque ⇒ out=rgb)。
        """
        H, W = fb.shape[:2]
        x0 = max(0, int(math.floor(float(pts[:, 0].min()))))
        x1 = min(W, int(math.ceil(float(pts[:, 0].max()))))
        y0 = max(0, int(math.floor(float(pts[:, 1].min()))))
        y1 = min(H, int(math.ceil(float(pts[:, 1].max()))))
        if x1 <= x0 or y1 <= y0:
            return
        # 超大 quad(近满屏): 隔像素采样 + 2x2 块写回(内部低分辨率之上
        # 再抽取, 地面/天空等大平面视觉差可忽略; 细条 quad 不抽取防虚线)
        bw = x1 - x0
        bh = y1 - y0
        step = 2 if bw * bh > (H * W) // 5 and min(bw, bh) >= 8 else 1
        xr = np.arange(x0, x1, dtype=np.float32)[::step] + 0.5
        yr = np.arange(y0, y1, dtype=np.float32)[::step] + 0.5
        # 两三角系数(标量) + 堆叠成 (2,bh,bw) 一次算 mask:
        # 逐元素运算与逐三角版本同序(a0*(x-xc)+b0*(y-yc) → 比较), 结果相同
        px = pts[:, 0].tolist()
        py = pts[:, 1].tolist()
        cfs = []
        for tri in ((0, 1, 2), (1, 3, 2)):
            i0, i1, i2 = tri
            xa, ya = px[i0], py[i0]
            xb, yb = px[i1], py[i1]
            xc, yc = px[i2], py[i2]
            d = (yb - yc) * (xa - xc) + (xc - xb) * (ya - yc)
            if abs(d) < 1e-9:
                continue
            cfs.append((tri, (yb - yc) / d, (xc - xb) / d,
                        (yc - ya) / d, (xa - xc) / d, xc, yc))
        if not cfs:
            return
        if len(cfs) == 2:
            C = np.array([c[1:] for c in cfs])
        else:
            C = np.array([cfs[0][1:], (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)])
        xc_ = C[:, 4][:, None, None]
        yc_ = C[:, 5][:, None, None]
        du = xr[None, None, :] - xc_                    # (2,1,bw)
        dv = yr[None, :, None] - yc_                    # (2,bh,1)
        l0 = C[:, 0][:, None, None] * du + C[:, 1][:, None, None] * dv
        l1 = C[:, 2][:, None, None] * du + C[:, 3][:, None, None] * dv
        m = (l0 >= -1e-4) & (l1 >= -1e-4) & (l0 + l1 <= 1.0001)
        if len(cfs) == 1:
            m[1] = False                                # 屏蔽缺失的第二三角
        tris = [(cfs[0][0], m[0]) + cfs[0][1:]]
        if len(cfs) == 2:
            tris.append((cfs[1][0], m[1]) + cfs[1][1:])
        mask = m[0] | m[1]
        yi, xi = np.nonzero(mask)
        if yi.size == 0:
            return
        if filled is not None:
            keep = ~filled[yi * step + y0, xi * step + x0]
            if not keep.any():
                if cov is not None and step == 1:
                    self._cov_mark(cov, mask, x0, y0)
                return
            yi, xi = yi[keep], xi[keep]
        if cov is not None and step == 1:
            self._cov_mark(cov, mask, x0, y0)
        # 仿射拟合(顶点属性 g: g(x,y) = A·x + B·y + C, 过三顶点)
        tri0, _mA, a0, b0, a1, b1, p2x, p2y = tris[0]
        i0, i1, i2 = tri0

        def _fit(g0: float, g1: float,
                 g2: float) -> tuple[float, float, float]:
            A = a0 * (g0 - g2) + a1 * (g1 - g2)
            B = b0 * (g0 - g2) + b1 * (g1 - g2)
            return A, B, g2 - A * p2x - B * p2y

        w0, w1, w2 = float(w[i0]), float(w[i1]), float(w[i2])
        au, bu, cu = _fit(float(uv[i0, 0]) / w0, float(uv[i1, 0]) / w1,
                          float(uv[i2, 0]) / w2)
        av, bv, cv = _fit(float(uv[i0, 1]) / w0, float(uv[i1, 1]) / w1,
                          float(uv[i2, 1]) / w2)
        ai, bi, ci = _fit(1.0 / w0, 1.0 / w1, 1.0 / w2)
        X = xr[xi]
        Y = yr[yi]
        iws = ai * X + bi * Y + ci
        iws = np.where(np.abs(iws) < 1e-12, 1e-12, iws)
        u = (au * X + bu * Y + cu) / iws
        v = (av * X + bv * Y + cv) / iws
        tex = spr.tex
        th, tw = tex.shape[:2]
        # uv 恒非负(scroll 归一到 [0,1) 后加在正 uv 上), int 截断即 floor
        tu = (u * tw).astype(np.int32) % tw
        tv = (v * th).astype(np.int32) % th
        src = tex[tv, tu].astype(np.float32)
        tint = np.array(color, dtype=np.float32) * (1.0 / 255.0)
        rgb = src[:, :3] * tint[:3]
        # 雾(屏幕空间逐顶点线性插值, 近似 D3D 雾管线; 见模块 docstring):
        # 逐三角形仿射(=barycentric)拟合, 按像素所属三角形挑选
        if fog[0] < 0.996 or fog[1] < 0.996 or fog[2] < 0.996 \
                or fog[3] < 0.996:
            if fog[0] == fog[1] == fog[2] == fog[3]:
                fgc = float(fog[0])
                rgb = rgb * fgc \
                    + self.sky_fog_color.astype(np.float32) * (1.0 - fgc)
            else:
                af, bf, cf = _fit(float(fog[i0]), float(fog[i1]),
                                  float(fog[i2]))
                fgA = af * X + bf * Y + cf
                if len(tris) == 2:
                    j0, j1, j2 = tris[1][0]
                    _mB, a0b, b0b, a1b, b1b, p2xb, p2yb = tris[1][1:]
                    fj2 = float(fog[j2])
                    A = a0b * (float(fog[j0]) - fj2) \
                        + a1b * (float(fog[j1]) - fj2)
                    B = b0b * (float(fog[j0]) - fj2) \
                        + b1b * (float(fog[j1]) - fj2)
                    fgB = A * X + B * Y + (fj2 - A * p2xb - B * p2yb)
                    fg = np.where(tris[0][1][yi, xi], fgA, fgB)
                else:
                    fg = fgA
                fg = fg[:, None]
                fog_rgb = self.sky_fog_color.astype(np.float32)
                rgb = rgb * fg + fog_rgb * (1.0 - fg)
        if step == 1:
            gi = yi + y0
            gj = xi + x0
        else:
            gi = yi * step + y0
            gj = xi * step + x0
        if filled is not None:
            # 不透明阶段: out = rgb(不读 dst), 并回填 filled
            out = np.clip(rgb, 0, 255).astype(np.uint8)
            filled[gi, gj] = True
        else:
            a = src[:, 3:4] * tint[3]                 # 合成 alpha 0..255
            dst = fb[gi, gj].astype(np.float32)
            if blend_mode == 1:  # DESTBLEND ONE(加算)
                out = dst + rgb * (a * (1.0 / 255.0))
            else:                # SRCALPHA / INVSRCALPHA
                out = rgb * (a * (1.0 / 255.0)) \
                    + dst * (1.0 - a * (1.0 / 255.0))
            out = np.clip(out, 0, 255).astype(np.uint8)
        if step == 1:
            fb[gi, gj] = out
        else:
            fb[gi, gj] = out
            mx = gj + 1 < x1
            fb[gi[mx], gj[mx] + 1] = out[mx]
            my = gi + 1 < y1
            fb[gi[my] + 1, gj[my]] = out[my]
            mxy = mx & my
            fb[gi[mxy] + 1, gj[mxy] + 1] = out[mxy]
