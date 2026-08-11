"""轻量 3D 模型文件解析器（GLB / OBJ）。

用于把 AI 服务商返回的模型文件解析回 :class:`core.mesh.Mesh`，
从而可以复用渲染器生成 PNG 预览、或转换为用户需要的其他格式。

- :func:`parse_glb`：解析 glTF 2.0 二进制（GLB），支持 POSITION/NORMAL/indices 访问器
- :func:`parse_obj`：解析 Wavefront OBJ 文本
"""

from __future__ import annotations

import json
import struct
from typing import List, Optional

import numpy as np

from .mesh import Mesh

_GL_COMPONENT = {
    5120: (np.int8, 1), 5121: (np.uint8, 1), 5122: (np.int16, 2),
    5123: (np.uint16, 2), 5125: (np.uint32, 4), 5126: (np.float32, 4),
}
_GL_TYPE_SIZE = {
    "SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4,
    "MAT2": 4, "MAT3": 9, "MAT4": 16,
}


def parse_glb(data: bytes) -> Mesh:
    """解析 GLB 二进制为 Mesh。

    Raises:
        ValueError: 格式非法或不支持的访问器类型
    """
    if len(data) < 20 or data[:4] != b"glTF":
        raise ValueError("不是有效的 GLB 文件（缺少 glTF 魔数）")
    version, length = struct.unpack_from("<II", data, 4)
    if version != 2:
        raise ValueError(f"不支持的 glTF 版本: {version}")

    # 遍历 chunks，找 JSON 和 BIN
    json_bytes: Optional[bytes] = None
    bin_bytes: bytes = b""
    off = 12
    while off + 8 <= len(data):
        c_len, c_type = struct.unpack_from("<I4s", data, off)
        c_data = data[off + 8:off + 8 + c_len]
        if c_type == b"JSON":
            json_bytes = c_data
        elif c_type == b"BIN\0":
            bin_bytes = c_data
        off += 8 + c_len

    if json_bytes is None:
        raise ValueError("GLB 缺少 JSON chunk")

    gltf = json.loads(json_bytes.decode("utf-8", errors="replace"))
    meshes = gltf.get("meshes", [])
    if not meshes:
        raise ValueError("GLB 中没有网格数据")
    prim = meshes[0]["primitives"][0]
    attrs = prim["attributes"]
    buffers = gltf.get("buffers", [])
    buffer_views = gltf.get("bufferViews", [])
    accessors = gltf.get("accessors", [])

    def read_accessor(acc_idx: int) -> np.ndarray:
        acc = accessors[acc_idx]
        comp_type = acc["componentType"]
        comp_dtype, _ = _GL_COMPONENT.get(comp_type, (None, None))
        if comp_dtype is None:
            raise ValueError(f"不支持的 componentType: {comp_type}")
        n_comp = _GL_TYPE_SIZE.get(acc["type"], 1)
        bv = buffer_views[acc["bufferView"]]
        buf = buffers[bv["buffer"]]
        # buffer 可能内嵌 base64（无 BIN chunk 的情况）
        uri = buf.get("uri", "")
        if uri.startswith("data:application/octet-stream;base64,"):
            import base64
            blob = base64.b64decode(uri.split(",", 1)[1])
        else:
            blob = bin_bytes
        byte_offset = bv.get("byteOffset", 0)
        byte_stride = bv.get("byteStride", comp_dtype().itemsize * n_comp)
        count = acc["count"]
        arr = np.frombuffer(blob, dtype=comp_dtype, count=count * n_comp,
                            offset=byte_offset)
        if byte_stride != comp_dtype().itemsize * n_comp:
            # 交错存储：按 stride 步进取数
            arr = np.frombuffer(blob, dtype=np.uint8, count=count * byte_stride,
                                offset=byte_offset)
            arr = arr.reshape(count, byte_stride)[:, :comp_dtype().itemsize * n_comp]
            arr = arr.reshape(count, n_comp).astype(np.float64)
            arr = np.frombuffer(arr.tobytes(), dtype=comp_dtype)
            arr = arr.astype(np.float64)
        arr = arr.astype(np.float64).reshape(count, n_comp)
        if "normalized" in acc and acc["normalized"]:
            arr = arr.astype(np.float32) / 127.5 - 1.0
        return arr

    positions = read_accessor(attrs["POSITION"])
    normals = None
    if "NORMAL" in attrs:
        normals = read_accessor(attrs["NORMAL"])
    if "indices" in prim:
        indices = read_accessor(prim["indices"]).astype(np.int32).reshape(-1)
    else:
        indices = np.arange(len(positions), dtype=np.int32)

    mesh = Mesh(positions, indices.reshape(-1, 3), name=gltf.get("asset", {}).get("generator", "glb_model"))
    if normals is not None and len(normals) == len(positions):
        mesh.normals = normals.astype(np.float32)
    return mesh


def parse_obj(text: str) -> Mesh:
    """解析 Wavefront OBJ 文本为 Mesh。"""
    verts: List[List[float]] = []
    faces: List[List[int]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if parts[0] == "v" and len(parts) >= 4:
            verts.append([float(parts[1]), float(parts[2]), float(parts[3])])
        elif parts[0] == "f":
            idxs = []
            for p in parts[1:]:
                if not p:
                    continue
                idx = int(p.split("/")[0])
                idxs.append(idx - 1 if idx > 0 else len(verts) + idx)
            if len(idxs) >= 3:
                # 多边形三角化（扇面）
                for i in range(1, len(idxs) - 1):
                    faces.append([idxs[0], idxs[i], idxs[i + 1]])
    if not verts or not faces:
        raise ValueError("OBJ 文件中没有有效的顶点/面数据")
    return Mesh(np.array(verts, dtype=np.float32),
                np.array(faces, dtype=np.int32), name="obj_model")
