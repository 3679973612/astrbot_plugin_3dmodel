"""网格数据结构与多种 3D 格式导出器。

支持导出格式：
- OBJ：通用文本格式（Wavefront）
- STL：3D 打印标准（二进制）
- GLB：glTF 2.0 二进制格式（游戏/网页/AR 通用）

核心数据结构 :class:`Mesh` —— 顶点 + 三角形面 + 法线。
整个模块零第三方依赖（仅 numpy），保证在 AstrBot 任何环境下都能工作。
"""

from __future__ import annotations

import io
import json
import struct
import zlib
from typing import List, Optional, Tuple

import numpy as np

# 顶点类型：float32 节省内存
DTYPE = np.float32


class Mesh:
    """三角网格。

    Attributes:
        vertices: (N, 3) float32 顶点坐标
        faces:    (M, 3) int32 三角形面（顶点索引）
        normals:  (M, 3) float32 面法线（自动计算）
    """

    def __init__(
        self,
        vertices: np.ndarray,
        faces: np.ndarray,
        name: str = "model",
    ) -> None:
        self.vertices = np.asarray(vertices, dtype=DTYPE).reshape(-1, 3)
        self.faces = np.asarray(faces, dtype=np.int32).reshape(-1, 3)
        self.name = name
        self.compute_normals()

    # ------------------------------------------------------------------ #
    # 基础属性
    # ------------------------------------------------------------------ #
    @property
    def vertex_count(self) -> int:
        return int(len(self.vertices))

    @property
    def face_count(self) -> int:
        return int(len(self.faces))

    def bounds(self) -> Tuple[np.ndarray, np.ndarray]:
        """返回 (最小值, 最大值) 包围盒。"""
        return self.vertices.min(axis=0), self.vertices.max(axis=0)

    def compute_normals(self) -> None:
        """基于三角形叉积计算面法线，并做单位化（防御零向量）。"""
        v = self.vertices
        f = self.faces
        n = np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]])
        lens = np.linalg.norm(n, axis=1, keepdims=True)
        lens[lens == 0] = 1.0  # 退化三角形防护
        self.normals = (n / lens).astype(DTYPE)

    def recenter(self) -> "Mesh":
        """把模型平移到原点中心，方便渲染与导出。原地修改并返回 self。"""
        lo, hi = self.bounds()
        self.vertices -= (lo + hi) / 2.0
        self.vertices = self.vertices.astype(DTYPE)
        return self

    def normalize_scale(self, target: float = 2.0) -> "Mesh":
        """把模型最长边缩放到 target，方便统一展示。原地修改并返回 self。"""
        lo, hi = self.bounds()
        size = float((hi - lo).max())
        if size > 0:
            self.vertices = (self.vertices - (lo + hi) / 2.0) / size * target
            self.vertices = self.vertices.astype(DTYPE)
        return self

    def merge_vertices(self) -> "Mesh":
        """按坐标合并重复顶点（四舍五入到 1e-5），减小文件体积。"""
        key = np.round(self.vertices, 5)
        _, inv = np.unique(key, axis=0, return_inverse=True)
        # 重建顶点表与面索引
        order = np.unique(inv, return_index=True)[1]
        order = np.argsort(order)  # 保持原顺序
        new_vertices = np.zeros((int(inv.max()) + 1, 3), dtype=DTYPE)
        new_vertices[order] = self.vertices[order]
        # 若存在未被引用的顶点则裁剪
        used = np.unique(inv)
        remap = np.full(int(inv.max()) + 1, -1, dtype=np.int32)
        remap[used] = np.arange(len(used))
        new_vertices = new_vertices[used]
        self.vertices = new_vertices
        self.faces = remap[inv[self.faces]].astype(np.int32)
        self.compute_normals()
        return self

    # ------------------------------------------------------------------ #
    # 导出
    # ------------------------------------------------------------------ #
    def to_obj(self) -> str:
        """导出为 Wavefront OBJ 文本。"""
        lines = [f"# {self.name}", f"# vertices: {self.vertex_count}, faces: {self.face_count}"]
        for v in self.vertices:
            lines.append(f"v {v[0]:.6f} {v[1]:.6f} {v[2]:.6f}")
        # 面法线
        for n in self.normals:
            lines.append(f"vn {n[0]:.6f} {n[1]:.6f} {n[2]:.6f}")
        for f in self.faces:
            lines.append(f"f {f[0]+1}//{f[0]+1} {f[1]+1}//{f[1]+1} {f[2]+1}//{f[2]+1}")
        return "\n".join(lines) + "\n"

    def to_stl(self) -> bytes:
        """导出为二进制 STL（3D 打印标准格式）。"""
        buf = io.BytesIO()
        buf.write(b"\0" * 80)  # 文件头（80 字节，可写入名称）
        buf.write(struct.pack("<I", self.face_count))
        for i, f in enumerate(self.faces):
            n = self.normals[i]
            tri = self.vertices[f]
            buf.write(struct.pack("<3f", *n))
            for v in tri:
                buf.write(struct.pack("<3f", *v))
            buf.write(struct.pack("<H", 0))  # 属性字节
        return buf.getvalue()

    def to_glb(self) -> bytes:
        """导出为 glTF 2.0 二进制（GLB）格式。"""
        # ---- 打包二进制数据 ----
        positions = self.vertices.astype(np.float32).tobytes()
        normals = self.normals.astype(np.float32).tobytes()
        indices = self.faces.astype(np.uint32).tobytes()

        bin_parts: List[bytes] = []
        views: List[dict] = []

        def add_view(data: bytes) -> int:
            """追加一个 4 字节对齐的 bufferView。"""
            offset = sum(len(p) for p in bin_parts)
            padding = (4 - (offset % 4)) % 4
            if padding:
                bin_parts.append(b"\0" * padding)
                offset += padding
            bin_parts.append(data)
            view = {"buffer": 0, "byteOffset": offset, "byteLength": len(data)}
            views.append(view)
            return len(views) - 1, view

        # 访问器: POSITION / NORMAL / INDEX
        acc: List[dict] = []

        def add_accessor(view_idx: int, count: int, comp_type: int, vtype: str) -> int:
            acc.append({"bufferView": view_idx, "componentType": comp_type,
                        "count": count, "type": vtype, "min": None, "max": None})
            return len(acc) - 1

        pos_view, pv = add_view(positions)
        add_accessor(pos_view, self.vertex_count, 5126, "VEC3")
        nor_view, nv = add_view(normals)
        add_accessor(nor_view, self.vertex_count, 5126, "VEC3")
        idx_view, iv = add_view(indices)
        add_accessor(idx_view, self.face_count * 3, 5125, "SCALAR")

        # 补全 min/max（POSITION 需要，某些查看器要求）
        lo, hi = self.bounds()
        acc[0]["min"] = [float(x) for x in lo]
        acc[0]["max"] = [float(x) for x in hi]

        bin_blob = b"".join(bin_parts)
        gltf = {
            "asset": {"version": "2.0", "generator": "astrbot_plugin_3dmodel"},
            "scene": 0,
            "scenes": [{"nodes": [0]}],
            "nodes": [{"mesh": 0, "name": self.name}],
            "meshes": [{
                "name": self.name,
                "primitives": [{
                    "attributes": {"POSITION": 0, "NORMAL": 1},
                    "indices": 2,
                    "mode": 4,  # TRIANGLES
                }],
            }],
            "buffers": [{"byteLength": len(bin_blob)}],  # 不写 uri -> 指向 BIN chunk
            "bufferViews": views,
            "accessors": acc,
        }
        json_bytes = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
        json_bytes += b"\x20" * ((4 - len(json_bytes) % 4) % 4)  # 4 字节对齐

        # ---- 组装 GLB ----
        total = 12 + 8 + len(json_bytes) + 8 + len(bin_blob)
        out = io.BytesIO()
        out.write(b"glTF")
        out.write(struct.pack("<I", 2))
        out.write(struct.pack("<I", total))
        out.write(struct.pack("<I", len(json_bytes)))
        out.write(b"JSON")
        out.write(json_bytes)
        out.write(struct.pack("<I", len(bin_blob)))
        out.write(b"BIN\0")
        out.write(bin_blob)
        return out.getvalue()

    def export(self, fmt: str) -> Tuple[bytes, str]:
        """按格式导出，返回 (数据, 文件扩展名)。

        fmt: 'obj' | 'stl' | 'glb'
        """
        fmt = fmt.lower().strip(".")
        if fmt == "obj":
            return self.to_obj().encode("utf-8"), "obj"
        if fmt == "stl":
            return self.to_stl(), "stl"
        if fmt == "glb":
            return self.to_glb(), "glb"
        raise ValueError(f"不支持的导出格式: {fmt}（支持 obj/stl/glb）")

    # ------------------------------------------------------------------ #
    # 组合
    # ------------------------------------------------------------------ #
    @classmethod
    def merge(cls, meshes: List["Mesh"], name: str = "merged") -> "Mesh":
        """把多个 Mesh 合并为一个（顶点偏移拼接）。"""
        total_v = sum(m.vertex_count for m in meshes)
        total_f = sum(m.face_count for m in meshes)
        verts = np.zeros((total_v, 3), dtype=DTYPE)
        faces = np.zeros((total_f, 3), dtype=np.int32)
        v_off, f_off = 0, 0
        for m in meshes:
            verts[v_off:v_off + m.vertex_count] = m.vertices
            faces[f_off:f_off + m.face_count] = m.faces + v_off
            v_off += m.vertex_count
            f_off += m.face_count
        return cls(verts, faces, name)


def gzip_bytes(data: bytes) -> bytes:
    """gzip 压缩（用于减少聊天发送体积）。"""
    return zlib.compress(data, 6)
