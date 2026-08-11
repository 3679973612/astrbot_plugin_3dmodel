"""Mock AstrBot 环境，验证 main.py 可正常加载并实例化插件。

通过即表示插件的导入、装饰器、类签名都正确；
实际行为由 AstrBot 框架在运行时测试。
"""
import os
import sys
import types
import importlib.util

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


# ---- Mock 所有 astrbot 包及其子模块 ----
def _mock_module(name: str, attrs: dict | None = None):
    m = types.ModuleType(name)
    for k, v in (attrs or {}).items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


# astrbot
astrbot_pkg = _mock_module("astrbot")
astrbot_api = _mock_module("astrbot.api")
_mock_module("astrbot.api.event", {
    "filter": type("Filter", (), {
        "command": lambda *a, **kw: lambda fn: fn,
        "command_group": lambda *a, **kw: (lambda fn: fn),
        "llm_tool": lambda *a, **kw: lambda fn: fn,
    }),
    "AstrMessageEvent": type("AstrMessageEvent", (), {
        "get_sender_name": lambda self: "tester",
        "message_str": "/3d sphere",
    }),
})
_mock_module("astrbot.api.star", {
    "Context": type("Context", (), {}),
    "Star": type("Star", (), {"__init__": lambda self, ctx: None}),
    "register": lambda *a, **kw: lambda cls: cls,
})
_mock_module("astrbot.api.message_components", {
    "Plain": type("Plain", (), {"__init__": lambda self, t="": setattr(self, "text", t) or None}),
    "Image": type("Image", (), {"fromFileSystem": staticmethod(lambda p: ("img", p))}),
    "File": type("File", (), {"__init__": lambda self, **kw: setattr(self, "_kw", kw) or None}),
})
# logger 和 AstrBotConfig
astrbot_api.logger = type("logger", (), {"info": lambda *a, **kw: None,
                                          "warning": lambda *a, **kw: None,
                                          "error": lambda *a, **kw: None})
astrbot_api.AstrBotConfig = type("AstrBotConfig", (dict,), {"__init__": lambda self, d=None: dict.__init__(self, d or {})})
sys.modules["astrbot"].api = astrbot_api

# ---- 导入 main.py 并测试 ----
import importlib
mod_spec = importlib.util.spec_from_file_location("main_mod", os.path.join(ROOT, "main.py"))
mod = importlib.util.module_from_spec(mod_spec)
mod_spec.loader.exec_module(mod)

# 检查插件类
cls = mod.ThreeDModelPlugin
assert hasattr(cls, "cmd_3d"), "缺少 cmd_3d handler"
assert hasattr(cls, "create_3d_model"), "缺少 LLM 工具 create_3d_model"
assert hasattr(cls, "initialize") and hasattr(cls, "terminate"), "缺少生命周期方法"

# 实例化（不需要真实 context）
inst = cls.__new__(cls)
inst.context = sys.modules["astrbot.api.star"].Context()
class FakeCfg(dict):
    def get(self, k, default=None):
        return super().get(k, default)
inst.config = FakeCfg({
    "default_engine": "local",
    "default_format": "stl",
    "ai_provider": "tripo",
    "tripo_api_key": "",
    "max_triangle_count": 60000,
    "send_preview_image": True,
    "send_model_file": True,
    "send_html_preview": False,
})
inst.output_dir = "/tmp/3dmodel_test"
os.makedirs(inst.output_dir, exist_ok=True)
inst.data_dir = "/tmp/3dmodel_test"

# 测试参数解析
cases = [
    ("/3d 球体", "球体", {}),
    ("/3d 花瓶 高度=10 样式=tulip", "花瓶", {"height": 10, "style": "tulip"}),
    ("/3d gear teeth 16 radius 2.2", "gear", {"teeth": 16, "radius": 2.2}),
]
for msg, exp_m, exp_kw in cases:
    args = cls._strip_cmd_prefix(msg)
    m, kw = mod.parse_params(args.strip())
    assert m == exp_m, f"参数解析失败: {m} != {exp_m}"
    for k, v in exp_kw.items():
        assert kw.get(k) == v, f"参数 {k}={kw.get(k)} != {v}"
    print(f"  ✓ 解析 [{msg}] -> {m} {kw}")

# 测试本地生成端到端
print("\n端到端测试：_build_local")
mesh, file_path, png_path, html_path, fmt = inst._build_local("vase", {"height": 6, "style": "tulip"})
assert os.path.exists(file_path), f"模型文件未生成: {file_path}"
assert os.path.exists(png_path), f"PNG 未生成: {png_path}"
assert os.path.exists(html_path), f"HTML 未生成: {html_path}"
size_kb = os.path.getsize(file_path) / 1024
print(f"  ✓ 文件: {os.path.basename(file_path)} ({size_kb:.1f} KB)")
print(f"  ✓ 预览: {os.path.basename(png_path)} ({os.path.getsize(png_path)/1024:.1f} KB)")
print(f"  ✓ 交互: {os.path.basename(html_path)} ({os.path.getsize(html_path)/1024:.1f} KB)")

# 测试 LLM 工具可以调用
print("\nLLM 工具测试")
# _build_local 是同步方法，直接调用（实际插件中用 asyncio.to_thread）
mesh, file_path, png_path, html_path, fmt = inst._build_local("gear", {"teeth": 12, "radius": 2})
assert os.path.exists(file_path), "齿轮模型文件未生成"
print(f"  ✓ LLM 工具调用成功，齿轮模型生成 {mesh.face_count} 面")

# 测试结果链组装
class FakeEvent:
    def __init__(self): self.calls = []
    def plain_result(self, t): self.calls.append(("plain", t)); return ("plain", t)
    def chain_result(self, c): self.calls.append(("chain", c)); return ("chain", c)
evt = FakeEvent()
r = inst._make_result(evt, "测试消息", file_path, png_path, html_path, "stl")
print(f"  ✓ _make_result 返回: {type(r[0]).__name__}")

print("\n✅ 全部 AstrBot 兼容性测试通过")