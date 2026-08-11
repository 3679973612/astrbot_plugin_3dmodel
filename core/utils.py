"""工具函数：指令参数解析、文件名处理等。"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

#: 中文参数键 -> 标准参数名
PARAM_ALIASES = {
    "高度": "height", "高": "height",
    "半径": "radius",
    "边长": "size", "大小": "size", "尺寸": "size",
    "宽度": "width", "宽": "width",
    "厚度": "thickness", "厚": "thickness",
    "齿数": "teeth", "齿": "teeth",
    "圈数": "coils", "圈": "coils",
    "边数": "sides", "边": "sides",
    "分段": "segments", "精度": "segments", "细分": "detail",
    "文字": "text", "文本": "text",
    "样式": "style", "风格": "style",
    "种子": "seed", "随机": "seed",
    "管径": "tube", "管半径": "tube",
    "分辨率": "resolution",
    "迭代": "iterations",
}


def parse_params(args: str) -> Tuple[str, Dict[str, Any]]:
    """解析指令参数。

    支持的语法（混用均可）：
        /3d 花瓶 height=10 radius=3 style=tulip
        /3d 花瓶 高度=10 半径:3 样式=tulip
        /3d gear teeth 16 radius 2.2
        /3d gear --teeth 16 --radius 2.2
        /3d text text=你好 厚度0.5

    Returns:
        (model_name, kwargs)
    """
    args = args.strip()
    if not args:
        return "", {}

    # 提取模型名（第一个 token）
    tokens = args.split()
    model = tokens[0]
    rest = tokens[1:]

    kwargs: Dict[str, Any] = {}
    i = 0
    while i < len(rest):
        tok = rest[i]
        # --key value 或 -key value
        if tok.startswith("--") or (tok.startswith("-") and len(tok) > 1 and not tok[1:].isdigit()):
            key = tok.lstrip("-")
            val: Any = True
            if i + 1 < len(rest) and not rest[i + 1].startswith("-"):
                val = _to_number(rest[i + 1])
                i += 1
            kwargs[key] = val
            i += 1
            continue
        # key=value
        m = re.match(r"^([\w\u4e00-\u9fff-]+)=(.+)$", tok)
        if m:
            key, val = m.group(1), m.group(2)
            kwargs[_normalize_key(key)] = _to_number(val)
            i += 1
            continue
        # key:value
        m = re.match(r"^([\w\u4e00-\u9fff-]+):(.+)$", tok)
        if m:
            key, val = m.group(1), m.group(2)
            kwargs[_normalize_key(key)] = _to_number(val)
            i += 1
            continue
        # key value（成对出现）
        if i + 1 < len(rest):
            key = tok
            val = rest[i + 1]
            kwargs[_normalize_key(key)] = _to_number(val)
            i += 2
            continue
        # 裸词 -> 布尔 True
        kwargs[_normalize_key(tok)] = True
        i += 1

    return model, kwargs


def _normalize_key(key: str) -> str:
    k = key.strip().lower().replace("-", "_")
    return PARAM_ALIASES.get(k, k)


def _to_number(val: str) -> Any:
    """把字符串转为 int/float/bool，失败保留原字符串。"""
    v = val.strip()
    if v.lower() in ("true", "yes", "on", "是", "开"):
        return True
    if v.lower() in ("false", "no", "off", "否", "关"):
        return False
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    try:
        return float(v)
    except ValueError:
        return v


def safe_filename(name: str, max_len: int = 40) -> str:
    """清理文件名中的非法字符。"""
    cleaned = re.sub(r"[^\w\u4e00-\u9fff-]", "_", name)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned[:max_len] or "model"


def format_model_help() -> str:
    """生成模型清单帮助文本。"""
    from .generators import MODEL_REGISTRY

    lines = ["📦 支持的模型（/3d <模型名> [参数]）："]
    for name, (_, desc) in MODEL_REGISTRY.items():
        lines.append(f"  · {name} — {desc}")
    lines.append("")
    lines.append("参数写法：key=值 或 --key 值 或 中文（如 高度=10 半径:3）")
    lines.append("示例：/3d 花瓶 高度=10 样式=tulip")
    return "\n".join(lines)
