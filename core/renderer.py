"""轻量软件 3D 渲染器：把 Mesh 渲染为 PNG 预览图。

实现原理（零 GPU 依赖，仅 numpy + PIL）：
1. 透视投影：把模型变换到相机空间并投影到屏幕
2. 背面剔除：丢弃法线背对相机的三角形（约省一半）
3. z-buffer：逐三角形光栅化，逐像素深度测试消除遮挡
4. 光照：双光源 Lambert 漫反射 + 环境光，按高度渐变着色
5. 输出：PIL Image -> PNG bytes

性能：60k 三角形、512x512 输出约 1~3 秒，聊天场景完全够用。
"""

from __future__ import annotations

import io
from typing import Optional, Tuple

import numpy as np

from .mesh import Mesh

# --------------------------------------------------------------------------- #
# 相机与投影
# --------------------------------------------------------------------------- #


def _look_at(eye: np.ndarray, center: np.ndarray, up: np.ndarray = np.array([0.0, 1.0, 0.0])) -> np.ndarray:
    """构造视图矩阵（列主序 4x4，OpenGL 风格）。"""
    f = center - eye
    f = f / np.linalg.norm(f)
    u = up / np.linalg.norm(up)
    s = np.cross(f, u)
    s = s / np.linalg.norm(s)
    u = np.cross(s, f)
    m = np.eye(4)
    m[0, :3] = s
    m[1, :3] = u
    m[2, :3] = -f
    m[0, 3] = -np.dot(s, eye)
    m[1, 3] = -np.dot(u, eye)
    m[2, 3] = np.dot(f, eye)
    return m


def _perspective(fov_y: float, aspect: float, near: float, far: float) -> np.ndarray:
    """构造透视投影矩阵。"""
    f = 1.0 / np.tan(fov_y / 2.0)
    m = np.zeros((4, 4))
    m[0, 0] = f / aspect
    m[1, 1] = f
    m[2, 2] = (far + near) / (near - far)
    m[2, 3] = 2 * far * near / (near - far)
    m[3, 2] = -1.0
    return m


def _transform(vertices: np.ndarray, mat: np.ndarray) -> np.ndarray:
    """对顶点做 4x4 齐次变换，返回齐次坐标 (N, 4)。"""
    homo = np.hstack([vertices, np.ones((len(vertices), 1))])
    return homo @ mat.T


# --------------------------------------------------------------------------- #
# 主渲染函数
# --------------------------------------------------------------------------- #

def render_mesh(
    mesh: Mesh,
    width: int = 512,
    height: int = 512,
    fov_y: float = 40.0,
    azimuth: float = -35.0,   # 水平旋转（度）
    elevation: float = 25.0,  # 俯仰（度）
    distance: Optional[float] = None,
    color: Tuple[int, int, int] = (68, 170, 255),
    height_color: bool = True,
    background: Optional[Tuple[int, int, int]] = None,
) -> "np.ndarray":
    """渲染网格，返回 RGB 图像数组 (height, width, 3)。

    Args:
        mesh: 待渲染网格
        width/height: 输出分辨率
        fov_y: 垂直视场角（度）
        azimuth/elevation: 相机方位角/俯仰角（度）
        distance: 相机距离（None 时根据包围盒自动计算）
        color: 主材质颜色 (R, G, B)
        height_color: 是否按高度渐变着色（地形等效果好）
        background: 背景色（None 时用渐变天空）
    """
    if mesh.vertex_count == 0 or mesh.face_count == 0:
        raise ValueError("网格为空，无法渲染")

    lo, hi = mesh.bounds()
    center = (lo + hi) / 2.0
    radius = float(np.linalg.norm(hi - lo)) / 2.0
    radius = max(radius, 1e-6)
    if distance is None:
        distance = radius / np.tan(np.radians(fov_y) / 2) * 1.35

    # 相机位置
    az, el = np.radians(azimuth), np.radians(elevation)
    eye = center + distance * np.array([
        np.cos(el) * np.cos(az), np.sin(el), np.cos(el) * np.sin(az),
    ])

    view = _look_at(eye, center)
    aspect = width / height
    proj = _perspective(np.radians(fov_y), aspect, 0.1, distance * 10)

    # ---- 变换到裁剪空间 ----
    v = mesh.vertices.astype(np.float64)
    v_view_full = _transform(v - center, view)   # (N, 4) 视空间齐次
    v_view = v_view_full[:, :3]                   # 视空间 xyz（深度用）
    v_clip = v_view_full @ proj.T  # (N, 4) 裁剪空间齐次
    w = np.abs(v_clip[:, 3:4])
    w[w < 1e-9] = 1e-9
    v_ndc = v_clip[:, :3] / w  # NDC: x,y in [-1,1]

    # 屏幕坐标
    sx = (v_ndc[:, 0] * 0.5 + 0.5) * (width - 1)
    sy = (1.0 - (v_ndc[:, 1] * 0.5 + 0.5)) * (height - 1)
    # 视空间 z（用于深度比较，负值在相机前）
    v_eye = v_view

    f = mesh.faces
    tri_screen = np.stack([sx[f], sy[f]], axis=-1)  # (F, 3, 2)
    # 视空间 z：相机前为正（越大越近）
    tri_eye_z = v_eye[f, 2].mean(axis=-1)           # (F,)
    tri_normals_view = mesh.normals.astype(np.float64)
    # 把法线变换到视空间（用视图矩阵的旋转部分）
    tri_normals_view = tri_normals_view @ view[:3, :3].T
    # 世界 Y 高度（用于高度渐变着色）
    tri_height = v[f, 1].mean(axis=-1)
    h_min, h_max = float(tri_height.min()), float(tri_height.max())
    h_span = (h_max - h_min) or 1.0
    tri_height_n = (tri_height - h_min) / h_span  # 0~1

    # ---- 背面剔除：法线视空间 z<0 表示朝向相机（相机看 -z）----
    facing = tri_normals_view[:, 2] < 0
    # 画家算法：z_view 大的（远的）先画
    order = np.argsort(-tri_eye_z)
    order = order[facing[order]]

    # ---- z-buffer ----
    # z_view 越小越近，初始 +inf（远未被覆盖）
    zbuf = np.full((height, width), np.inf, dtype=np.float64)
    img = np.zeros((height, width, 3), dtype=np.float64)

    # 光照：两个方向光 + 环境光
    light1 = np.array([0.4, 0.8, 0.6]); light1 = light1 / np.linalg.norm(light1)
    light2 = np.array([-0.6, 0.3, -0.7]); light2 = light2 / np.linalg.norm(light2)

    base_color = np.array(color, dtype=np.float64)
    n_faces = len(order)
    if n_faces == 0:
        raise ValueError("所有三角形都被剔除，请调整相机角度")

    ys, xs = np.meshgrid(np.arange(height), np.arange(width), indexing="ij")

    for idx in order:
        tri = tri_screen[idx]  # (3, 2)
        x0, y0 = tri[:, 0].min(), tri[:, 1].min()
        x1, y1 = tri[:, 0].max(), tri[:, 1].max()
        if x1 < 0 or y1 < 0 or x0 >= width or y0 >= height:
            continue
        ix0, iy0 = max(0, int(np.floor(x0))), max(0, int(np.floor(y0)))
        ix1, iy1 = min(width - 1, int(np.ceil(x1))), min(height - 1, int(np.ceil(y1)))
        if ix1 <= ix0 or iy1 <= iy0:
            continue
        h_slice, w_slice = iy1 - iy0 + 1, ix1 - ix0 + 1
        # 重心坐标测试（覆盖整个 bbox）
        px = xs[iy0:iy1 + 1, ix0:ix1 + 1].astype(np.float64)
        py = ys[iy0:iy1 + 1, ix0:ix1 + 1].astype(np.float64)
        ax, ay = tri[0]; bx, by = tri[1]; cx, cy = tri[2]
        denom = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(denom) < 1e-12:
            continue
        w0 = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / denom
        w1 = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / denom
        w2 = 1.0 - w0 - w1
        inside_2d = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
        if not inside_2d.any():
            continue
        # 深度插值（视空间 z，越小越近）
        ztri = v_view[f[idx], 2]
        z0, z1, z2 = float(ztri[0]), float(ztri[1]), float(ztri[2])
        z_2d = w0 * z0 + w1 * z1 + w2 * z2
        sub_zbuf = zbuf[iy0:iy1 + 1, ix0:ix1 + 1]
        sel_2d = inside_2d & (z_2d < sub_zbuf)
        if not sel_2d.any():
            continue
        # 光照
        n = tri_normals_view[idx]
        n = n / (np.linalg.norm(n) + 1e-9)
        diff = max(0.0, np.dot(n, light1)) * 0.75 + max(0.0, np.dot(n, light2)) * 0.3 + 0.28
        if height_color:
            t = float(tri_height_n[idx])
            col = (np.array([18, 52, 110]) * (1 - t) + np.array([255, 228, 180]) * t)
            col = np.clip(col * diff, 0, 255)
        else:
            col = np.clip(base_color * diff, 0, 255)
        # 通过 view 写入（保证写回 img / zbuf）
        col_2d = np.broadcast_to(col, (h_slice, w_slice, 3)).copy()
        sub_img = img[iy0:iy1 + 1, ix0:ix1 + 1]
        sub_img[sel_2d] = col_2d[sel_2d]
        sub_zbuf[sel_2d] = z_2d[sel_2d]

    # ---- 背景 ----
    if background is not None:
        bg = np.array(background, dtype=np.float64)
        bg_full = np.broadcast_to(bg, (height, width, 3)).copy()
    else:
        # 渐变天空
        t = np.linspace(0, 1, height)[:, None]
        bg = np.array([14, 18, 34]) * (1 - t) + np.array([44, 68, 120]) * t
        bg_full = np.broadcast_to(bg[:, None, :], (height, width, 3)).copy()
    # 填充背景（zbuf 仍为初始 inf = 未被任何三角形覆盖）
    mask = zbuf == np.inf
    img[mask] = bg_full[mask]

    return np.clip(img, 0, 255).astype(np.uint8)


def render_to_png(
    mesh: Mesh,
    width: int = 512,
    height: int = 512,
    color: Tuple[int, int, int] = (68, 170, 255),
    height_color: bool = True,
    **kwargs,
) -> bytes:
    """渲染并返回 PNG 字节。"""
    from PIL import Image

    arr = render_mesh(mesh, width=width, height=height, color=color,
                      height_color=height_color, **kwargs)
    img = Image.fromarray(arr, "RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
