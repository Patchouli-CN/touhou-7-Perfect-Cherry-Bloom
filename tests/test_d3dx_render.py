"""D3DX 复刻接口(engine/render/d3dx_render)测试: 矩阵对照 D3DX 公式手算值。"""

from __future__ import annotations

import math
import sys

sys.path.insert(0, r"D:\python_play\Touhou08")

import numpy as np  # noqa: E402

from touhou.engine.render.d3dx_render import (  # noqa: E402
    D3DXLikeRender,
    linear_fog_factor,
    linear_fog_factor_vec,
    look_at_lh,
    normalize,
    perspective_fov_lh,
    quad_corner_offsets,
    rotation_xyz,
)


def test_normalize() -> None:
    v = normalize(np.array([3.0, 0.0, 4.0]))
    assert np.allclose(v, [0.6, 0.0, 0.8])
    # 近零向量 → 零向量(不除零)
    assert np.array_equal(normalize(np.zeros(3)), np.zeros(3))


def test_look_at_lh_axis_aligned() -> None:
    """pos=(1,2,3), 朝 +z, up=+y: view 旋转部单位阵, 平移 -pos。

    手算: z=(0,0,1); x=normalize(up×z)=(1,0,0); y=z×x=(0,1,0);
    cam_right=normalize(z×up)=(-1,0,0)(= view 的 -x 轴)。
    """
    view, cam_right = look_at_lh(
        np.array([1.0, 2.0, 3.0]), np.array([0.0, 0.0, 1.0]), np.array([0.0, 1.0, 0.0])
    )
    assert np.allclose(
        view, [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [-1, -2, -3, 1]]
    )
    assert np.allclose(cam_right, [-1.0, 0.0, 0.0])


def test_look_at_lh_normalizes_direction() -> None:
    """lookAt 是方向向量(UpdateCamera 语义), 非单位也按单位化处理。"""
    view, _ = look_at_lh(
        np.zeros(3), np.array([0.0, 0.0, 2.0]), np.array([0.0, 1.0, 0.0])
    )
    assert np.allclose(view, np.identity(4))


def test_perspective_fov_lh_hand_value() -> None:
    """fov=pi/2, aspect=4/3, 近 30 远 1800 的 D3DXMatrixPerspectiveFovLH。

    tan(pi/4)=1 → p00=1/(tan*aspect)=0.75, p11=1, p22=1800/1770,
    p23=1, p32=-30*1800/1770, 其余 0。
    """
    p = perspective_fov_lh(math.pi / 2.0, 640.0 / 480.0, 30.0, 1800.0)
    expect = np.zeros((4, 4))
    expect[0, 0] = 0.75
    expect[1, 1] = 1.0
    expect[2, 2] = 1800.0 / 1770.0
    expect[2, 3] = 1.0
    expect[3, 2] = -30.0 * 1800.0 / 1770.0
    assert np.allclose(p, expect)


def test_rotation_xyz_zero_is_exact_identity() -> None:
    """零角跳过优化: 返回精确单位阵(cos=1/sin=0 与跳过等价)。"""
    assert rotation_xyz(np.zeros(3)) == (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)


def test_rotation_xyz_matches_d3dx_matrix_product() -> None:
    """rotation_xyz 的 9 元组 = D3DX 行向量 Rx·Ry·Rz 的 3x3 乘积(行优先)。"""
    ax, ay, az = 0.3, -0.5, 0.7
    cx, sx = math.cos(ax), math.sin(ax)
    cy, sy = math.cos(ay), math.sin(ay)
    cz, sz = math.cos(az), math.sin(az)
    rx = np.array([[1, 0, 0], [0, cx, sx], [0, -sx, cx]])
    ry = np.array([[cy, 0, -sy], [0, 1, 0], [sy, 0, cy]])
    rz = np.array([[cz, sz, 0], [-sz, cz, 0], [0, 0, 1]])
    expect = (rx @ ry @ rz).reshape(-1)
    assert np.allclose(rotation_xyz(np.array([ax, ay, az])), expect)


def test_quad_corner_offsets_zero_rotation() -> None:
    assert quad_corner_offsets(64.0, 32.0, np.zeros(3)) == (
        -64.0,
        -32.0,
        0.0,
        64.0,
        -32.0,
        0.0,
        -64.0,
        32.0,
        0.0,
        64.0,
        32.0,
        0.0,
    )


def test_quad_corner_offsets_rotated() -> None:
    """旋转角点 = 基角点(±hw,±hh,0) 行向量乘 rotation_xyz 的 3x3。"""
    rot = np.array([0.2, 0.4, -0.1])
    coff = quad_corner_offsets(64.0, 32.0, rot)
    r = np.array(rotation_xyz(rot)).reshape(3, 3)
    base = np.array(
        [[-64.0, -32.0, 0.0], [64.0, -32.0, 0.0], [-64.0, 32.0, 0.0], [64.0, 32.0, 0.0]]
    )
    assert np.allclose(np.asarray(coff).reshape(4, 3), base @ r)


def test_linear_fog_factor() -> None:
    """D3DFOG_LINEAR: f=(far-z)/(far-near), [0,1] 夹取。"""
    assert linear_fog_factor(200.0, 200.0, 500.0) == 1.0
    assert linear_fog_factor(500.0, 200.0, 500.0) == 0.0
    assert linear_fog_factor(350.0, 200.0, 500.0) == 0.5
    assert linear_fog_factor(9000.0, 200.0, 500.0) == 0.0
    assert linear_fog_factor(0.0, 200.0, 500.0) == 1.0
    # far==near 退化: max(1e-6, ·) 防除零, 不炸
    assert linear_fog_factor(1.0, 5.0, 5.0) in (0.0, 1.0)


def test_linear_fog_factor_vec_matches_scalar() -> None:
    z = np.linspace(0.0, 1000.0, 64)
    vec = linear_fog_factor_vec(z, 200.0, 500.0)
    sca = np.array([linear_fog_factor(float(v), 200.0, 500.0) for v in z])
    assert np.array_equal(vec, sca)


def _straight_camera() -> D3DXLikeRender:
    d3 = D3DXLikeRender()
    d3.cam_pos = np.array([0.0, 0.0, 0.0])
    d3.cam_lookat = np.array([0.0, 0.0, 1.0])
    d3.cam_up = np.array([0.0, 1.0, 0.0])
    d3.cam_fov = math.pi / 2.0
    d3.update_camera()
    return d3


def test_project_straight_ahead() -> None:
    """相机原点朝 +z, fov=pi/2: (0,0,100) 投影到视口中心 (288,224)。

    手算: view 单位阵 → clip=(0, ?, 100*(1800/1770)-54000/1770, 100);
    ndc_x=0 → sx=(0+1)*320-32=288; ndc_y=0 → sy=(1-0)*240-16=224。
    """
    d3 = _straight_camera()
    pr = d3.project(0.0, 0.0, 100.0)
    assert pr is not None
    assert pr.x == 288.0 and pr.y == 224.0
    assert pr.clip_w == 100.0
    assert math.isclose(pr.clip_z, 100.0 * 1800.0 / 1770.0 - 30.0 * 1800.0 / 1770.0)
    assert 0.0 < pr.ndc_z < 1.0


def test_project_near_plane_clip() -> None:
    """w<1(近面内侧)返回 None(D3D 裁剪语义)。"""
    d3 = _straight_camera()
    assert d3.project(0.0, 0.0, 0.5) is None
    assert d3.project(0.0, 0.0, -10.0) is None
    assert d3.project(0.0, 0.0, 1.0) is not None


def test_view_z() -> None:
    """view 空间 z = 沿视线深度: pos=(10,20,30) 朝 +z 时点 (10,20,80) → 50。"""
    d3 = D3DXLikeRender()
    d3.cam_pos = np.array([10.0, 20.0, 30.0])
    d3.cam_lookat = np.array([0.0, 0.0, 1.0])
    d3.cam_up = np.array([0.0, 1.0, 0.0])
    d3.cam_fov = math.pi / 2.0
    d3.update_camera()
    assert d3.view_z(10.0, 20.0, 80.0) == 50.0


def test_project_points_vec_matches_scalar() -> None:
    """向量化投影/view_z 与标量版逐位一致(同序表达式)。"""
    d3 = D3DXLikeRender()
    d3.cam_pos = np.array([3.0, -7.0, 11.0])
    d3.cam_lookat = np.array([0.1, 0.2, 1.0])
    d3.cam_up = np.array([0.0, 1.0, 0.1])
    d3.cam_fov = 0.9
    d3.update_camera()
    rng = np.random.default_rng(7)
    pts = rng.uniform(-500.0, 500.0, size=(17, 3))
    pts[:, 2] += 900.0  # 保证在相机前方(w≥1)
    sx, sy, w = d3.project_points(pts[:, 0], pts[:, 1], pts[:, 2])
    zv = d3.view_z_points(pts[:, 0], pts[:, 1], pts[:, 2])
    for i in range(pts.shape[0]):
        pr = d3.project(float(pts[i, 0]), float(pts[i, 1]), float(pts[i, 2]))
        assert pr is not None
        assert pr.x == sx[i] and pr.y == sy[i] and pr.clip_w == w[i]
        assert d3.view_z(float(pts[i, 0]), float(pts[i, 1]), float(pts[i, 2])) == zv[i]
