"""本地程序化 3D 模型生成引擎。

无需网络、无需 API Key，通过参数化算法在本地生成三角网格。
内置 15+ 种模型：基础几何体、参数化艺术品（花瓶/齿轮/弹簧/地形/文字浮雕）等。

每个生成函数接收 `**kwargs`（可选参数），返回 :class:`core.mesh.Mesh`。
统一入口 :func:`generate` 根据模型名分发，并自动做「居中 + 归一化 + 三角形上限保护」。
"""

from __future__ import annotations

import math
import random
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from .mesh import Mesh

# --------------------------------------------------------------------------- #
# 数学工具
# --------------------------------------------------------------------------- #


def _lathe(profile: np.ndarray, segments: int = 48) -> Mesh:
    """车削旋转体：把二维轮廓曲线 (z, r) 绕 Z 轴旋转成网格。

    profile: (N, 2) 数组，每行为 (z, r)。z 为高度，r 为该高度处的半径。
    返回顶部开口的旋转体（如需封底/封顶由调用方决定）。
    """
    zs = profile[:, 0]
    rs = profile[:, 1]
    n = len(profile)
    theta = np.linspace(0, 2 * np.pi, segments, endpoint=False)
    verts = np.zeros((n * segments, 3), dtype=np.float32)
    for i in range(n):
        x = rs[i] * np.cos(theta)
        y = rs[i] * np.sin(theta)
        verts[i * segments:(i + 1) * segments, 0] = x
        verts[i * segments:(i + 1) * segments, 1] = y
        verts[i * segments:(i + 1) * segments, 2] = zs[i]
    faces = []
    for i in range(n - 1):
        base = i * segments
        for j in range(segments):
            j2 = (j + 1) % segments
            faces.append((base + j, base + j2, base + segments + j2))
            faces.append((base + j, base + segments + j2, base + segments + j))
    return Mesh(verts, np.array(faces, dtype=np.int32))


def _cap_rings(mesh: Mesh, segments: int, closed: bool) -> Mesh:
    """为旋转体封顶/封底（若轮廓两端半径 > 0 则封盖）。"""
    faces = mesh.faces.tolist()
    verts = mesh.vertices.tolist()
    n = len(verts)
    # 底部封盖（z 最小处）
    v = np.asarray(verts, dtype=np.float32)
    zmin = v[:, 2].min()
    bottom_pts = np.where(np.abs(v[:, 2] - zmin) < 1e-6)[0]
    top_pts = np.where(np.abs(v[:, 2] - v[:, 2].max()) < 1e-6)[0]
    if len(bottom_pts) == segments and closed:
        center = np.array([0.0, 0.0, zmin], dtype=np.float32)
        verts.append(center.tolist())
        ci = len(verts) - 1
        for j in range(segments):
            j2 = (j + 1) % segments
            faces.append((ci, bottom_pts[j2], bottom_pts[j]))
    if len(top_pts) == segments and closed:
        center = np.array([0.0, 0.0, v[:, 2].max()], dtype=np.float32)
        verts.append(center.tolist())
        ci = len(verts) - 1
        for j in range(segments):
            j2 = (j + 1) % segments
            faces.append((ci, top_pts[j], top_pts[j2]))
    return Mesh(np.array(verts, dtype=np.float32), np.array(faces, dtype=np.int32))


def _value_noise2d(shape: Tuple[int, int], seed: int, scale: float = 8.0) -> np.ndarray:
    """简易 Value Noise（2D），用于地形等。"""
    rng = random.Random(seed)
    h, w = shape
    gx, gy = max(2, int(w / scale)), max(2, int(h / scale))
    grid = np.array([[rng.uniform(0, 1) for _ in range(gx + 1)] for _ in range(gy + 1)],
                    dtype=np.float64)

    xs = np.linspace(0, gx, w, endpoint=False)
    ys = np.linspace(0, gy, h, endpoint=False)
    X, Y = np.meshgrid(xs, ys)
    x0, y0 = X.astype(int), Y.astype(int)
    fx, fy = X - x0, Y - y0
    sx = fx * fx * (3 - 2 * fx)  # smoothstep
    sy = fy * fy * (3 - 2 * fy)

    def g(ix, iy):
        return grid[iy % (gy + 1), ix % (gx + 1)]

    n00 = g(x0, y0); n10 = g(x0 + 1, y0)
    n01 = g(x0, y0 + 1); n11 = g(x0 + 1, y0 + 1)
    nx0 = n00 * (1 - sx) + n10 * sx
    nx1 = n01 * (1 - sx) + n11 * sx
    return nx0 * (1 - sy) + nx1 * sy


def _fbm(shape: Tuple[int, int], seed: int, octaves: int = 4) -> np.ndarray:
    """分形布朗运动：多层噪声叠加，得到更自然的地形。"""
    out = np.zeros(shape, dtype=np.float64)
    amp, freq, total = 1.0, 1.0, 0.0
    for _ in range(octaves):
        out += amp * _value_noise2d(shape, seed + 100 * octaves, scale=freq * 8)
        total += amp
        amp *= 0.5
        freq *= 2.0
    return out / total


# --------------------------------------------------------------------------- #
# 基础几何体
# --------------------------------------------------------------------------- #

def generate_cube(size: float = 2.0, **_) -> Mesh:
    """立方体。size: 边长。"""
    s = size / 2
    v = np.array([
        [-s, -s, -s], [s, -s, -s], [s, s, -s], [-s, s, -s],
        [-s, -s, s], [s, -s, s], [s, s, s], [-s, s, s],
    ], dtype=np.float32)
    f = np.array([
        [0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7],
        [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5],
        [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7],
    ], dtype=np.int32)
    return Mesh(v, f, "cube")


def generate_sphere(radius: float = 1.0, segments: int = 32, rings: Optional[int] = None, **_) -> Mesh:
    """球体（经纬网格）。"""
    rings = rings or max(8, segments // 2)
    theta = np.linspace(0, np.pi, rings, endpoint=False)
    phi = np.linspace(0, 2 * np.pi, segments, endpoint=False)
    T, P = np.meshgrid(theta, phi, indexing="ij")
    x = radius * np.sin(T) * np.cos(P)
    y = radius * np.sin(T) * np.sin(P)
    z = radius * np.cos(T)
    verts = np.stack([x.ravel(), y.ravel(), z.ravel()], axis=1).astype(np.float32)
    faces = []
    for i in range(rings):
        for j in range(segments):
            j2 = (j + 1) % segments
            a = i * segments + j
            b = i * segments + j2
            c = (i + 1) * segments + j2 if i + 1 < rings else -1
            d = (i + 1) * segments + j if i + 1 < rings else -1
            if i == 0:  # 北极三角形
                faces.append((a, b, d))
            elif i == rings - 1:  # 南极三角形
                faces.append((a, b, c))
            else:
                faces.append((a, b, c))
                faces.append((a, c, d))
    return Mesh(verts, np.array(faces, dtype=np.int32), "sphere")


def generate_cylinder(radius: float = 1.0, height: float = 2.0, segments: int = 32, **_) -> Mesh:
    """圆柱体（带顶底盖）。"""
    z = np.array([-height / 2, height / 2])
    theta = np.linspace(0, 2 * np.pi, segments, endpoint=False)
    verts = []
    for zi in z:
        for t in theta:
            verts.append((radius * np.cos(t), radius * np.sin(t), zi))
    # 顶底中心
    verts.append((0, 0, -height / 2))
    verts.append((0, 0, height / 2))
    n = len(verts)
    ci_b, ci_t = n - 2, n - 1
    faces = []
    for j in range(segments):
        j2 = (j + 1) % segments
        # 侧面
        faces.append((j, j2, segments + j2))
        faces.append((j, segments + j2, segments + j))
        # 底盖（朝下）
        faces.append((ci_b, j2, j))
        # 顶盖（朝上）
        faces.append((ci_t, segments + j, segments + j2))
    return Mesh(np.array(verts, dtype=np.float32), np.array(faces, dtype=np.int32), "cylinder")


def generate_cone(radius: float = 1.0, height: float = 2.0, segments: int = 32, **_) -> Mesh:
    """圆锥体（含底面）。"""
    theta = np.linspace(0, 2 * np.pi, segments, endpoint=False)
    verts = [(radius * np.cos(t), radius * np.sin(t), -height / 2) for t in theta]
    verts.append((0, 0, height / 2))  # 顶点
    verts.append((0, 0, -height / 2))  # 底面中心
    n = len(verts)
    apex, cbase = n - 2, n - 1
    faces = []
    for j in range(segments):
        j2 = (j + 1) % segments
        faces.append((j, j2, apex))
        faces.append((cbase, j2, j))
    return Mesh(np.array(verts, dtype=np.float32), np.array(faces, dtype=np.int32), "cone")


def generate_torus(radius: float = 1.2, tube: float = 0.45, segments: int = 36, tubular_segments: Optional[int] = None, **_) -> Mesh:
    """环面（甜甜圈）。"""
    tubular_segments = tubular_segments or max(12, segments // 2)
    u = np.linspace(0, 2 * np.pi, segments, endpoint=False)
    v = np.linspace(0, 2 * np.pi, tubular_segments, endpoint=False)
    U, V = np.meshgrid(u, v, indexing="ij")
    x = (radius + tube * np.cos(V)) * np.cos(U)
    y = (radius + tube * np.cos(V)) * np.sin(U)
    z = tube * np.sin(V)
    verts = np.stack([x.ravel(), y.ravel(), z.ravel()], axis=1).astype(np.float32)
    faces = []
    for i in range(segments):
        for j in range(tubular_segments):
            j2 = (j + 1) % tubular_segments
            a = i * tubular_segments + j
            b = i * tubular_segments + j2
            c = ((i + 1) % segments) * tubular_segments + j2
            d = ((i + 1) % segments) * tubular_segments + j
            faces.append((a, b, c))
            faces.append((a, c, d))
    return Mesh(verts, np.array(faces, dtype=np.int32), "torus")


def generate_prism(sides: int = 6, radius: float = 1.0, height: float = 2.0, **_) -> Mesh:
    """正棱柱（六边形默认）。"""
    sides = max(3, int(sides))
    theta = np.linspace(0, 2 * np.pi, sides, endpoint=False)
    verts = []
    for zi in (-height / 2, height / 2):
        for t in theta:
            verts.append((radius * np.cos(t), radius * np.sin(t), zi))
    verts.append((0, 0, -height / 2))
    verts.append((0, 0, height / 2))
    n = len(verts)
    ci_b, ci_t = n - 2, n - 1
    faces = []
    for j in range(sides):
        j2 = (j + 1) % sides
        faces.append((j, j2, sides + j2))
        faces.append((j, sides + j2, sides + j))
        faces.append((ci_b, j2, j))
        faces.append((ci_t, sides + j, sides + j2))
    return Mesh(np.array(verts, dtype=np.float32), np.array(faces, dtype=np.int32), f"prism{sides}")


def generate_pyramid(size: float = 2.0, height: float = 2.0, sides: int = 4, **_) -> Mesh:
    """棱锥（默认四棱锥）。"""
    sides = max(3, int(sides))
    theta = np.linspace(0, 2 * np.pi, sides, endpoint=False)
    verts = [(size / 2 * np.cos(t), size / 2 * np.sin(t), -height / 2) for t in theta]
    verts.append((0, 0, height / 2))
    verts.append((0, 0, -height / 2))
    apex, cbase = sides, sides + 1
    faces = []
    for j in range(sides):
        j2 = (j + 1) % sides
        faces.append((j, j2, apex))
        faces.append((cbase, j2, j))
    return Mesh(np.array(verts, dtype=np.float32), np.array(faces, dtype=np.int32), "pyramid")


def generate_icosphere(radius: float = 1.0, detail: int = 2, **_) -> Mesh:
    """细分二十面体球（顶点分布均匀，质量比经纬球好）。detail: 0-3。"""
    detail = max(0, min(4, int(detail)))
    t = (1.0 + math.sqrt(5.0)) / 2.0
    verts = [
        (-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0),
        (0, -1, t), (0, 1, t), (0, -1, -t), (0, 1, -t),
        (t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1),
    ]
    faces = [
        (0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11),
        (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6), (7, 1, 8),
        (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9),
        (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1),
    ]
    # 中点细分
    cache: Dict[Tuple[int, int], int] = {}

    def mid(a: int, b: int) -> int:
        key = (min(a, b), max(a, b))
        if key not in cache:
            va, vb = np.array(verts[a]), np.array(verts[b])
            vm = (va + vb) / 2
            cache[key] = len(verts)
            verts.append(vm)
        return cache[key]

    for _ in range(detail):
        new_faces = []
        for a, b, c in faces:
            ab, bc, ca = mid(a, b), mid(b, c), mid(c, a)
            new_faces += [(a, ab, ca), (ab, b, bc), (ca, bc, c), (ab, bc, ca)]
        faces = new_faces

    v = np.array(verts, dtype=np.float32)
    v = v / np.linalg.norm(v, axis=1, keepdims=True) * radius
    return Mesh(v, np.array(faces, dtype=np.int32), "icosphere")


# --------------------------------------------------------------------------- #
# 参数化艺术模型
# --------------------------------------------------------------------------- #

def generate_vase(height: float = 6.0, radius: float = 1.6, style: str = "classic",
                  segments: int = 48, **_) -> Mesh:
    """花瓶（车削曲面）。

    style: classic=经典双曲线, tulip=郁金香, gourd=葫芦, amphora=双耳瓶
    """
    n = 32
    z = np.linspace(-height / 2, height / 2, n)
    t = (z - z.min()) / (z.max() - z.min())  # 0~1
    if style == "tulip":
        r = radius * (0.35 + 0.75 * np.sin(t * np.pi) ** 1.8 + 0.35 * np.exp(-((t - 0.85) ** 2) / 0.01))
    elif style == "gourd":
        r = radius * (0.4 + 1.15 * np.sin(t * np.pi) ** 2 * np.exp(-((t - 0.55) ** 2) / 0.06))
    elif style == "amphora":
        r = radius * (0.55 + 0.7 * np.abs(np.sin(t * np.pi * 2.2)) ** 2.5 + 0.2)
    else:  # classic
        r = radius * (0.45 + 0.95 * np.sin(t * np.pi) ** 1.5)
    r[-1] = max(r[-1], 0.15)  # 顶部开口保持一定口径
    profile = np.stack([z, r], axis=1)
    mesh = _lathe(profile, segments=segments)
    mesh = _cap_rings(mesh, segments, closed=True)
    mesh.name = f"vase_{style}"
    return mesh


def generate_gear(teeth: int = 12, radius: float = 2.2, height: float = 0.8,
                  hole_ratio: float = 0.3, segments: int = 4, **_) -> Mesh:
    """齿轮（梯形齿，含中心孔）。"""
    teeth = max(4, int(teeth))
    seg = max(2, int(segments))
    r_outer = radius
    r_inner = radius * 0.75
    r_hole = radius * hole_ratio
    n_pts = teeth * seg * 2
    theta = np.linspace(0, 2 * np.pi, n_pts, endpoint=False)
    # 齿形：每个齿 2 个 seg 段，一半在 r_outer 一半在 r_inner
    radii = []
    for i in range(teeth):
        for j in range(seg):
            radii.append(r_outer)
        for j in range(seg):
            radii.append(r_inner)
    radii = np.array(radii)
    # 平滑过渡
    radii = np.convolve(radii, [0.15, 0.7, 0.15], mode="same")
    x = radii * np.cos(theta)
    y = radii * np.sin(theta)
    verts = []
    for zi in (-height / 2, height / 2):
        for i in range(n_pts):
            verts.append((x[i], y[i], zi))
    # 中心孔
    hole_pts = []
    for i in range(n_pts):
        hole_pts.append((r_hole * np.cos(theta[i]), r_hole * np.sin(theta[i]), 0))
    n = len(verts)
    faces = []
    for i in range(n_pts):
        i2 = (i + 1) % n_pts
        faces.append((i, i2, n_pts + i2))
        faces.append((i, n_pts + i2, n_pts + i))
    # 顶盖（环形，带孔）：顶面在 z=+h/2，外圈 n_pts..2n_pts-1，孔用反向三角形
    # 用三角扇连接外圈与内圈（孔在中心，通过把孔圈也加入顶点实现）
    for i in range(n_pts):
        i2 = (i + 1) % n_pts
        # 底面（从下往上看为逆时针）
        faces.append((i, i2, n_pts + i2))
        faces.append((i, n_pts + i2, n_pts + i))
    verts_ext = list(verts)
    for p in hole_pts:
        verts_ext.append((p[0], p[1], height / 2))
    for p in hole_pts:
        verts_ext.append((p[0], p[1], -height / 2))
    base_top = 2 * n_pts
    base_bot = 3 * n_pts
    for i in range(n_pts):
        i2 = (i + 1) % n_pts
        # 顶面环形带：外圈顶 -> 外圈顶2 -> 内圈顶2 -> 内圈顶（四边形拆两个三角形）
        a = n_pts + i
        b = n_pts + i2
        c = base_top + i2
        d = base_top + i
        faces.append((a, b, c))
        faces.append((a, c, d))
        # 底面环形带（法线朝下）
        a2 = i
        b2 = i2
        c2 = base_bot + i2
        d2 = base_bot + i
        faces.append((a2, c2, b2))
        faces.append((a2, d2, c2))
        # 孔壁
        faces.append((base_top + i, base_top + i2, base_bot + i2))
        faces.append((base_top + i, base_bot + i2, base_bot + i))
    return Mesh(np.array(verts_ext, dtype=np.float32), np.array(faces, dtype=np.int32), "gear")


def generate_spring(radius: float = 1.2, height: float = 4.0, coils: int = 6,
                    tube: float = 0.25, segments: int = 24, tubular_segments: Optional[int] = None, **_) -> Mesh:
    """弹簧 / 螺旋线圈。"""
    tubular_segments = tubular_segments or max(8, segments // 2)
    u = np.linspace(0, coils * 2 * np.pi, segments, endpoint=False)
    v = np.linspace(0, 2 * np.pi, tubular_segments, endpoint=False)
    U, V = np.meshgrid(u, v, indexing="ij")
    x = (radius + tube * np.cos(V)) * np.cos(U)
    y = (radius + tube * np.cos(V)) * np.sin(U)
    z = height * U / (coils * 2 * np.pi) - height / 2 + tube * np.sin(V)
    verts = np.stack([x.ravel(), y.ravel(), z.ravel()], axis=1).astype(np.float32)
    faces = []
    for i in range(segments):
        for j in range(tubular_segments):
            j2 = (j + 1) % tubular_segments
            a = i * tubular_segments + j
            b = i * tubular_segments + j2
            c = ((i + 1) % segments) * tubular_segments + j2
            d = ((i + 1) % segments) * tubular_segments + j
            faces.append((a, b, c))
            faces.append((a, c, d))
    return Mesh(verts, np.array(faces, dtype=np.int32), "spring")


def generate_terrain(size: float = 6.0, height: float = 1.5, seed: int = 42,
                     resolution: int = 96, octaves: int = 4, **_) -> Mesh:
    """噪声地形（fBm）。"""
    res = min(160, max(16, int(resolution)))
    h = _fbm((res, res), seed, octaves=octaves)
    h = (h - h.min()) / (h.max() - h.min() + 1e-9) * height
    x = np.linspace(-size / 2, size / 2, res)
    y = np.linspace(-size / 2, size / 2, res)
    X, Y = np.meshgrid(x, y)
    verts = np.stack([X.ravel(), Y.ravel(), h.ravel()], axis=1).astype(np.float32)
    faces = []
    for i in range(res - 1):
        for j in range(res - 1):
            a = i * res + j
            b = i * res + j + 1
            c = (i + 1) * res + j + 1
            d = (i + 1) * res + j
            faces.append((a, b, c))
            faces.append((a, c, d))
    return Mesh(verts, np.array(faces, dtype=np.int32), "terrain")


def generate_text(text: str = "3D", size: float = 2.0, thickness: float = 0.4,
                  resolution: int = 128, font_path: Optional[str] = None,
                  max_triangles: int = 60000, **_) -> Mesh:
    """文字浮雕（高度场挤出）。

    使用 PIL 渲染文字为灰度图，再转为高度场生成顶面 + 侧壁 + 底面。
    font_path 缺省时自动探测系统字体。
    max_triangles: 三角面预算，用于自适应降低分辨率。
    """
    from PIL import Image, ImageDraw, ImageFont

    text = (text or "3D")[:12]  # 限制长度
    candidates = font_path and [font_path] or [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "C:/Windows/Fonts/arialbd.ttf",
    ]
    font = None
    for p in candidates:
        if p and __import__("os").path.exists(p):
            try:
                font = ImageFont.truetype(p, 64)
                break
            except Exception:
                continue
    if font is None:
        font = ImageFont.load_default()

    # 三角面预算：顶面 + 底面 + 侧壁 ≈ 4 * (res-1)^2
    res = min(256, max(32, int(resolution)))
    while res > 32 and (res - 1) ** 2 * 4 > max_triangles * 0.9:
        res //= 2
    img = Image.new("L", (res, res), 0)
    draw = ImageDraw.Draw(img)
    # 自动测宽居中
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    if tw <= 0 or th <= 0:
        tw, th = res // 2, res // 2
    scale = min((res * 0.8) / tw, (res * 0.8) / th)
    draw.text((res / 2 - tw * scale / 2 - bbox[0] * scale,
               res / 2 - th * scale / 2 - bbox[1] * scale),
              text, fill=255, font=font)
    # 平滑
    img = img.resize((res, res), Image.LANCZOS)
    gray = np.asarray(img, dtype=np.float64) / 255.0
    gray = (gray - gray.min()) / (gray.max() - gray.min() + 1e-9)

    # 高度场：底面 + 顶面
    h_map = gray * thickness
    x = np.linspace(-size / 2, size / 2, res)
    y = np.linspace(-size / 2, size / 2, res)
    X, Y = np.meshgrid(x, y)
    Z_top = h_map
    Z_bot = np.zeros_like(h_map)

    def _hfield(zz: np.ndarray, name: str) -> Mesh:
        verts = np.stack([X.ravel(), Y.ravel(), zz.ravel()], axis=1).astype(np.float32)
        faces = []
        for i in range(res - 1):
            for j in range(res - 1):
                a = i * res + j
                b = i * res + j + 1
                c = (i + 1) * res + j + 1
                d = (i + 1) * res + j
                faces.append((a, b, c))
                faces.append((a, c, d))
        return Mesh(verts, np.array(faces, dtype=np.int32), name)

    top = _hfield(Z_top, "text_top")
    # 底面反向
    bottom = _hfield(Z_bot, "text_bottom")
    bottom.faces = bottom.faces[:, ::-1].copy()
    bottom.compute_normals()
    # 侧壁：遍历顶面外轮廓（灰度>0 且邻居=0）
    side_verts = list(top.vertices.tolist())
    base_idx = len(side_verts)
    side_verts.extend(bottom.vertices.tolist())
    side_faces = []
    mask = gray > 0.05
    for i in range(res - 1):
        for j in range(res - 1):
            cells = [
                (i, j), (i, j + 1), (i + 1, j + 1), (i + 1, j),
            ]
            for k in range(4):
                a, b = cells[k], cells[(k + 1) % 4]
                if mask[a] and not mask[b]:
                    ia, ib = a[0] * res + a[1], b[0] * res + b[1]
                    # 顶面边 -> 底面边（两个三角形）
                    side_faces.append((ia, base_idx + ia, base_idx + ib))
                    side_faces.append((ia, base_idx + ib, ib))
    mesh = Mesh.merge([top, bottom, Mesh(np.array(side_verts, dtype=np.float32),
                                          np.array(side_faces, dtype=np.int32), "text_side")], "text")
    return mesh


def generate_moebius(radius: float = 1.6, width: float = 0.7, segments: int = 72,
                     tubular_segments: Optional[int] = None, **_) -> Mesh:
    """莫比乌斯带（单面曲面）。"""
    tubular_segments = tubular_segments or 12
    u = np.linspace(0, 2 * np.pi, segments, endpoint=False)
    v = np.linspace(-width / 2, width / 2, tubular_segments)
    U, V = np.meshgrid(u, v, indexing="ij")
    half = U / 2
    x = (radius + V * np.cos(half)) * np.cos(U)
    y = (radius + V * np.cos(half)) * np.sin(U)
    z = V * np.sin(half)
    verts = np.stack([x.ravel(), y.ravel(), z.ravel()], axis=1).astype(np.float32)
    faces = []
    for i in range(segments):
        for j in range(tubular_segments - 1):
            a = i * tubular_segments + j
            b = i * tubular_segments + j + 1
            c = ((i + 1) % segments) * tubular_segments + j + 1
            d = ((i + 1) % segments) * tubular_segments + j
            faces.append((a, b, c))
            faces.append((a, c, d))
    return Mesh(verts, np.array(faces, dtype=np.int32), "moebius")


def generate_heart(size: float = 1.8, tube: float = 0.28, segments: int = 40,
                   tubular_segments: Optional[int] = None, **_) -> Mesh:
    """心形曲面（参数方程环管）。"""
    tubular_segments = tubular_segments or 16
    u = np.linspace(0, 2 * np.pi, segments, endpoint=False)
    v = np.linspace(0, 2 * np.pi, tubular_segments, endpoint=False)
    U, V = np.meshgrid(u, v, indexing="ij")
    # 心形轮廓（平面参数方程）
    cx = 16 * np.sin(U) ** 3
    cy = 13 * np.cos(U) - 5 * np.cos(2 * U) - 2 * np.cos(3 * U) - np.cos(4 * U)
    # 缩放
    s = size / 17.0
    cx, cy = cx * s, cy * s
    # 法向（对 u 求导再旋转 90 度）
    dux = 48 * np.sin(U) ** 2 * np.cos(U)
    duy = -13 * np.sin(U) + 10 * np.sin(2 * U) + 6 * np.sin(3 * U) + 4 * np.sin(4 * U)
    nx, ny = duy, -dux
    nl = np.sqrt(nx ** 2 + ny ** 2) + 1e-9
    nx, ny = nx / nl, ny / nl
    x = cx + tube * nx * np.cos(V)
    y = cy + tube * ny * np.cos(V)
    z = tube * np.sin(V)
    verts = np.stack([x.ravel(), y.ravel(), z.ravel()], axis=1).astype(np.float32)
    faces = []
    for i in range(segments):
        for j in range(tubular_segments):
            j2 = (j + 1) % tubular_segments
            a = i * tubular_segments + j
            b = i * tubular_segments + j2
            c = ((i + 1) % segments) * tubular_segments + j2
            d = ((i + 1) % segments) * tubular_segments + j
            faces.append((a, b, c))
            faces.append((a, c, d))
    return Mesh(verts, np.array(faces, dtype=np.int32), "heart")


def generate_torus_knot(p: int = 2, q: int = 3, radius: float = 1.3, tube: float = 0.32,
                        segments: int = 200, tubular_segments: Optional[int] = None, **_) -> Mesh:
    """环形结（Torus Knot，数学上优雅的缠绕曲面）。"""
    tubular_segments = tubular_segments or 16
    p, q = max(1, int(p)), max(1, int(q))
    u = np.linspace(0, 2 * np.pi * q, segments, endpoint=False)
    v = np.linspace(0, 2 * np.pi, tubular_segments, endpoint=False)
    U, V = np.meshgrid(u, v, indexing="ij")
    # 中心线
    r = np.cos(q * U) + 2
    cx = r * np.cos(p * U)
    cy = r * np.sin(p * U)
    cz = -np.sin(q * U)
    # 沿 U 方向（axis=0）数值求导，构造 Frenet 标架
    dr = np.gradient(r, axis=0)
    dcx = np.gradient(cx, axis=0)
    dcy = np.gradient(cy, axis=0)
    dcz = np.gradient(cz, axis=0)
    # 切向量
    tx, ty, tz = dcx, dcy, dcz
    tl = np.sqrt(tx ** 2 + ty ** 2 + tz ** 2) + 1e-9
    tx, ty, tz = tx / tl, ty / tl, tz / tl
    # 参考向量 -> 法向量/副法向量
    rx = np.full_like(tx, 0.0); ry = np.full_like(ty, 0.0); rz = np.ones_like(tz)
    nx = ty * rz - tz * ry
    ny = tz * rx - tx * rz
    nz = tx * ry - ty * rx
    nl = np.sqrt(nx ** 2 + ny ** 2 + nz ** 2) + 1e-9
    nx, ny, nz = nx / nl, ny / nl, nz / nl
    bx, by, bz = (ty * nz - tz * ny), (tz * nx - tx * nz), (tx * ny - ty * nx)
    x = cx + tube * (nx * np.cos(V) + bx * np.sin(V))
    y = cy + tube * (ny * np.cos(V) + by * np.sin(V))
    z = cz + tube * (nz * np.cos(V) + bz * np.sin(V))
    verts = np.stack([x.ravel(), y.ravel(), z.ravel()], axis=1).astype(np.float32)
    faces = []
    for i in range(segments):
        for j in range(tubular_segments):
            j2 = (j + 1) % tubular_segments
            a = i * tubular_segments + j
            b = i * tubular_segments + j2
            c = ((i + 1) % segments) * tubular_segments + j2
            d = ((i + 1) % segments) * tubular_segments + j
            faces.append((a, b, c))
            faces.append((a, c, d))
    return Mesh(verts, np.array(faces, dtype=np.int32), f"torus_knot_{p}_{q}")


def generate_pyramid_fractal(iterations: int = 2, base: float = 2.4, **_) -> Mesh:
    """谢尔宾斯基三角金字塔（分形）。"""
    iterations = max(0, min(3, int(iterations)))
    meshes: List[Mesh] = []

    def _build(center, size, depth):
        if depth <= 0:
            meshes.append(generate_pyramid(size=size * 1.05, height=size * 1.1, sides=4))
            meshes[-1].vertices = meshes[-1].vertices + np.array(center, dtype=np.float32)
            return
        s = size / 2
        offsets = [(-s, -s, -s / 2), (s, -s, -s / 2), (-s, s, -s / 2), (s, s, -s / 2)]
        for dx, dy, dz in offsets:
            _build((center[0] + dx, center[1] + dy, center[2] + dz), s, depth - 1)

    _build((0, 0, 0), base, iterations)
    return Mesh.merge(meshes, "sierpinski_pyramid")


# --------------------------------------------------------------------------- #
# 注册表与统一入口
# --------------------------------------------------------------------------- #

#: 模型名 -> (生成函数, 参数说明)
MODEL_REGISTRY: Dict[str, Tuple[Callable, str]] = {
    "cube": (generate_cube, "立方体 · 参数: size=边长"),
    "sphere": (generate_sphere, "球体 · 参数: radius=半径, segments=分段"),
    "icosphere": (generate_icosphere, "细分球(均匀顶点) · 参数: radius, detail=细分0-3"),
    "cylinder": (generate_cylinder, "圆柱 · 参数: radius, height, segments"),
    "cone": (generate_cone, "圆锥 · 参数: radius, height, segments"),
    "torus": (generate_torus, "环面/甜甜圈 · 参数: radius, tube, segments"),
    "prism": (generate_prism, "棱柱 · 参数: sides=边数, radius, height"),
    "pyramid": (generate_pyramid, "棱锥/金字塔 · 参数: size, height, sides"),
    "vase": (generate_vase, "花瓶 · 参数: height, radius, style=classic/tulip/gourd/amphora"),
    "gear": (generate_gear, "齿轮 · 参数: teeth=齿数, radius, height, hole_ratio=孔占比"),
    "spring": (generate_spring, "弹簧/螺旋 · 参数: radius, height, coils=圈数, tube"),
    "terrain": (generate_terrain, "地形 · 参数: size, height, seed=随机种子, resolution"),
    "text": (generate_text, "文字浮雕 · 参数: text=文字, size, thickness=厚度"),
    "moebius": (generate_moebius, "莫比乌斯带 · 参数: radius, width, segments"),
    "heart": (generate_heart, "心形 · 参数: size, tube, segments"),
    "torus_knot": (generate_torus_knot, "环形结 · 参数: p, q(整数), radius, tube"),
    "sierpinski": (generate_pyramid_fractal, "谢尔宾斯基分形金字塔 · 参数: iterations=0-3"),
}

#: 中文别名 -> 标准名
ALIASES: Dict[str, str] = {
    "立方体": "cube", "方块": "cube", "正方体": "cube",
    "球": "sphere", "球体": "sphere", "圆球": "sphere",
    "细分球": "icosphere",
    "圆柱": "cylinder", "圆柱体": "cylinder",
    "圆锥": "cone", "圆锥体": "cone", "锥体": "cone",
    "环": "torus", "环面": "torus", "甜甜圈": "torus", "圆环": "torus", "面包圈": "torus",
    "棱柱": "prism", "六棱柱": "prism",
    "金字塔": "pyramid", "棱锥": "pyramid", "四棱锥": "pyramid",
    "花瓶": "vase", "花樽": "vase",
    "齿轮": "gear", "齿轮模型": "gear",
    "弹簧": "spring", "螺旋": "spring", "弹簧圈": "spring",
    "地形": "terrain", "山": "terrain", "山脉": "terrain", "丘陵": "terrain", "山地": "terrain",
    "文字": "text", "文字浮雕": "text", "字": "text", "浮雕": "text",
    "莫比乌斯": "moebius", "莫比乌斯带": "moebius",
    "心": "heart", "爱心": "heart", "心形": "heart",
    "环形结": "torus_knot", "绳结": "torus_knot", "结": "torus_knot",
    "分形": "sierpinski", "谢尔宾斯基": "sierpinski", "分形金字塔": "sierpinski",
}


def list_models() -> List[str]:
    """返回支持的模型名列表。"""
    return list(MODEL_REGISTRY.keys())


def resolve_name(name: str) -> Optional[str]:
    """解析模型名（支持中文别名），找不到返回 None。"""
    n = str(name).strip().lower().replace("-", "_").replace(" ", "_")
    if n in MODEL_REGISTRY:
        return n
    if name in ALIASES:
        return ALIASES[name]
    # 模糊匹配：包含关系
    for k in MODEL_REGISTRY:
        if k in n or n in k:
            return k
    return None


def generate(name: str, max_triangles: int = 60000, **kwargs) -> Mesh:
    """统一生成入口。

    Args:
        name: 模型名（支持中文别名）
        max_triangles: 三角形数量上限保护
        **kwargs: 传给具体生成函数的参数

    Returns:
        Mesh: 已居中、缩放到合适尺寸的网格

    Raises:
        ValueError: 模型名不存在
    """
    key = resolve_name(name)
    if key is None:
        raise ValueError(f"未知模型类型: {name}。可用: {', '.join(list_models())}")
    fn, _ = MODEL_REGISTRY[key]
    if key == "text":
        kwargs.setdefault("max_triangles", max_triangles)
    mesh = fn(**kwargs)
    mesh.name = key
    mesh.recenter().normalize_scale(target=2.0)
    # 三角形上限保护
    if mesh.face_count > max_triangles:
        # 最简单的降采样：直接截断三角面（对高精度模型可接受）
        mesh.faces = mesh.faces[:max_triangles]
        mesh.compute_normals()
    return mesh
