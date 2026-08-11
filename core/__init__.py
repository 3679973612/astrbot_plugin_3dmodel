"""astrbot_plugin_3dmodel 核心包。

- mesh:        网格数据与 OBJ/STL/GLB 导出
- generators:  本地程序化生成引擎（15+ 种模型）
- renderer:    软件渲染 PNG 预览
- preview:     HTML 3D 交互预览
- ai_generator: AI 文生 3D（Tripo / Meshy）
- parsers:     GLB / OBJ / STL 解析
- utils:       参数解析与工具
"""

__version__ = "1.0.0"

from .mesh import Mesh  # noqa: F401
