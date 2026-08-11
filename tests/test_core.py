"""本地测试：验证核心生成引擎、导出器、渲染器。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.generators import generate, list_models, resolve_name
from core.mesh import Mesh
from core.parsers import parse_glb, parse_obj
from core.preview import generate_html_preview
from core.renderer import render_to_png
from core.utils import parse_params

OUT = "/tmp/3d_test"
os.makedirs(OUT, exist_ok=True)

results = []


def check(name, cond, extra=""):
    status = "✅" if cond else "❌"
    results.append((cond, name))
    print(f"{status} {name} {extra}")


# 1. 模型清单
models = list_models()
check("模型清单", len(models) >= 15, f"({len(models)} 种)")

# 2. 逐个生成并导出三种格式
for m in models:
    try:
        kwargs = {}
        if m == "text":
            kwargs = {"text": "Hi", "font_path": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"}
        t0 = time.time()
        mesh = generate(m, max_triangles=60000, **kwargs)
        dt = time.time() - t0
        assert mesh.vertex_count > 0 and mesh.face_count > 0
        # 导出
        for fmt in ("obj", "stl", "glb"):
            data, ext = mesh.export(fmt)
            assert len(data) > 0
        check(f"生成 {m}", True, f"({mesh.face_count} 面, {dt*1000:.0f}ms)")
    except Exception as e:
        check(f"生成 {m}", False, f"{type(e).__name__}: {e}")

# 3. 渲染测试（选 5 个代表）
for m in ["sphere", "vase", "gear", "terrain", "heart", "torus_knot"]:
    try:
        mesh = generate(m)
        png = render_to_png(mesh, width=320, height=320)
        p = os.path.join(OUT, f"{m}.png")
        with open(p, "wb") as f:
            f.write(png)
        check(f"渲染 {m}", len(png) > 1000)
    except Exception as e:
        check(f"渲染 {m}", False, f"{type(e).__name__}: {e}")

# 4. HTML 预览
try:
    mesh = generate("vase", style="tulip")
    html = generate_html_preview(mesh, title="vase")
    p = os.path.join(OUT, "vase.html")
    with open(p, "w", encoding="utf-8") as f:
        f.write(html)
    check("HTML 预览", "THREE" in html and "MODEL" in html)
except Exception as e:
    check("HTML 预览", False, f"{type(e).__name__}: {e}")

# 5. GLB 导出 -> 解析回 Mesh（往返一致性）
try:
    mesh = generate("torus")
    glb = mesh.to_glb()
    p = os.path.join(OUT, "torus.glb")
    with open(p, "wb") as f:
        f.write(glb)
    mesh2 = parse_glb(glb)
    check("GLB 往返", mesh2.face_count == mesh.face_count and mesh2.vertex_count == mesh.vertex_count,
          f"({mesh.vertex_count}->{mesh2.vertex_count} 顶点)")
except Exception as e:
    check("GLB 往返", False, f"{type(e).__name__}: {e}")

# 6. OBJ 导出 -> 解析回 Mesh
try:
    mesh = generate("cube")
    obj = mesh.to_obj()
    mesh2 = parse_obj(obj)
    check("OBJ 往返", mesh2.face_count == mesh.face_count)
except Exception as e:
    check("OBJ 往返", False, f"{type(e).__name__}: {e}")

# 7. STL 导出 -> 解析回 Mesh（用 ai_generator 里的解析器）
try:
    from core.ai_generator import _parse_binary_stl
    mesh = generate("sphere", segments=24)
    stl = mesh.to_stl()
    p = os.path.join(OUT, "sphere.stl")
    with open(p, "wb") as f:
        f.write(stl)
    mesh2 = _parse_binary_stl(stl)
    check("STL 往返", mesh2.face_count == mesh.face_count)
except Exception as e:
    check("STL 往返", False, f"{type(e).__name__}: {e}")

# 8. 参数解析
cases = [
    ("花瓶 高度=10 样式=tulip", ("花瓶", {"高度": 10, "样式": "tulip"})),
    ("gear teeth 16 radius 2.2", ("gear", {"teeth": 16, "radius": 2.2})),
    ("torus --tube 0.4 --radius 1.5", ("torus", {"tube": 0.4, "radius": 1.5})),
    ("花瓶 高度:8 半径:2", ("花瓶", {"高度": 8, "半径": 2})),
]
for arg, (exp_model, exp_kw) in cases:
    model, kw = parse_params(arg)
    check(f"解析 [{arg}]", model == exp_model, f"-> {model} {kw}")

# 9. 中文别名解析
for cn, std in [("甜甜圈", "torus"), ("金字塔", "pyramid"), ("爱心", "heart"), ("山脉", "terrain")]:
    r = resolve_name(cn)
    check(f"别名 {cn}->{std}", r == std, f"实际 {r}")

# 10. 合并
try:
    m1, m2 = generate("cube"), generate("sphere", segments=16)
    merged = Mesh.merge([m1, m2])
    check("合并网格", merged.face_count == m1.face_count + m2.face_count)
except Exception as e:
    check("合并网格", False, f"{type(e).__name__}: {e}")

# 汇总
fails = [n for ok, n in results if not ok]
print(f"\n{'='*50}\n总计 {len(results)} 项，通过 {len(results)-len(fails)} 项")
if fails:
    print("失败项：")
    for f in fails:
        print(f"  - {f}")
    sys.exit(1)
print("全部通过 ✅")
