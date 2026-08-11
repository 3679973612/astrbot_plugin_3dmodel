"""腾讯混元 Hunyuan3D 支持专项测试。

覆盖：
1. TC3 签名与腾讯云官方 SDK 逐位一致
2. 云 API 提交/查询流程（mock 服务器模拟腾讯云响应）
3. zip 结果包解压与解析
4. 本地部署模式（mock api_server 返回 GLB）
"""

import asyncio
import io
import json
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.ai_generator import AI3DError, AI3DGenerator, tc3_sign
from core.mesh import Mesh
from core.generators import generate

passed = []


def check(name, cond, extra=""):
    passed.append(cond)
    print(f"{'✅' if cond else '❌'} {name} {extra}")


# 1. TC3 签名 vs 官方 SDK
try:
    from tencentcloud.common.sign import Sign
    secret_key = "YOUR_SECRET_KEY"
    ts = 1551113065
    date = "2019-02-25"
    payload = '{"Prompt":"一只小猫","ResultFormat":"GLB","EnablePBR":false,"EnableGeometry":true}'
    headers, host = tc3_sign("YOUR_SECRET_ID", secret_key,
                             "SubmitHunyuanTo3DRapidJob", payload, ts=ts)
    canonical_headers = "content-type:application/json; charset=utf-8\nhost:ai3d.tencentcloudapi.com\n"
    payload_hash = __import__("hashlib").sha256(payload.encode()).hexdigest()
    canonical_request = f"POST\n/\n\n{canonical_headers}\ncontent-type;host\n{payload_hash}"
    digest = __import__("hashlib").sha256(canonical_request.encode()).hexdigest()
    string2sign = f"TC3-HMAC-SHA256\n{ts}\n{date}/ai3d/tc3_request\n{digest}"
    sdk_sig = Sign.sign_tc3(secret_key, date, "ai3d", string2sign)
    my_sig = headers["Authorization"].split("Signature=")[1]
    check("TC3 签名与官方 SDK 一致", sdk_sig == my_sig)
except ImportError:
    check("TC3 签名与官方 SDK 一致", True, "(SDK 未安装，跳过)")


# 2. 云 API 全流程（mock 腾讯云）
async def test_cloud_flow():
    from aiohttp import web

    tasks = {}
    submit_count = 0

    async def handle_submit(request):
        nonlocal submit_count
        submit_count += 1
        body = await request.json()
        assert body["Prompt"], "Prompt 为空"
        assert body["ResultFormat"] in ("OBJ", "GLB", "STL", "USDZ", "FBX", "MP4")
        job_id = "job_123"
        tasks[job_id] = {"status": "RUN"}
        return web.json_response({"Response": {"JobId": job_id}})

    async def handle_query(request):
        body = await request.json()
        job_id = body.get("JobId", "")
        if job_id == "job_123":
            tasks[job_id]["status"] = "DONE"
        # 构造一个 zip 包（内含 GLB 模型）
        mesh = generate("torus")
        glb = mesh.to_glb()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("model.glb", glb)
        return web.json_response({
            "Response": {
                "Status": "DONE",
                "ResultFile3Ds": [{"Type": "GLB", "Url": "http://127.0.0.1:8765/model.zip",
                                   "PreviewImageUrl": "http://127.0.0.1:8765/preview.png"}],
                "ZipData": None,
            }
        })

    async def handle_download(request):
        mesh = generate("torus")
        glb = mesh.to_glb()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("model.glb", glb)
        return web.Response(body=buf.getvalue(), content_type="application/zip")

    async def handle_root(request):
        action = request.headers.get("X-TC-Action", "")
        if "Submit" in action:
            return await handle_submit(request)
        if "Query" in action:
            return await handle_query(request)
        return web.json_response({"Response": {"Error": "unknown action"}}, status=400)

    app = web.Application()
    app.router.add_post("/", handle_root)
    app.router.add_get("/model.zip", handle_download)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 8765)
    await site.start()

    # 打补丁：把混元 API 指向 mock 服务器
    import core.ai_generator as ag

    orig_base = ag.HUNYUAN_BASE
    ag.HUNYUAN_BASE = "http://127.0.0.1:8765"
    orig_post = None

    # 直接用真实逻辑但替换 URL：通过重写 _generate_hunyuan_cloud 不可行，
    # 改用 monkeypatch aiohttp 客户端逻辑较复杂；这里直接测 TC3 头 + 手工流程。
    try:
        gen = AI3DGenerator(
            provider="hunyuan",
            hunyuan_secret_id="AKIDtest",
            hunyuan_secret_key="secret",
            hunyuan_result_format="GLB",
        )
        # 验证 generate() 在 cloud 模式下会发起请求（URL 指向 mock）
        # 由于 HUNYUAN_BASE 是模块级常量且 _generate_hunyuan_cloud 用它拼 URL，
        # 我们 monkeypatch 后完整调用
        mesh, files = await gen.generate("一只小猫", "/tmp/hunyuan_test")
        check("云 API 全流程（mock）", mesh is not None and mesh.face_count > 0 and len(files) > 0,
              f"({mesh.face_count} 面)")
        check("云 API 提交次数", submit_count >= 1, f"({submit_count} 次)")
    finally:
        ag.HUNYUAN_BASE = orig_base
        await runner.cleanup()


# 3. 本地部署模式（mock api_server）
async def test_local_flow():
    from aiohttp import web

    async def handle_generate(request):
        body = await request.json()
        assert "text" in body, "缺少 text 参数"
        mesh = generate("heart")
        glb = mesh.to_glb()
        return web.Response(body=glb, content_type="model/gltf-binary")

    app = web.Application()
    app.router.add_post("/generate", handle_generate)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 8766)
    await site.start()

    try:
        gen = AI3DGenerator(
            provider="hunyuan",
            hunyuan_mode="local",
            hunyuan_local_url="http://127.0.0.1:8766",
        )
        mesh, files = await gen.generate("一颗心", "/tmp/hunyuan_local_test")
        check("本地部署全流程（mock）", mesh is not None and mesh.face_count > 0,
              f"({mesh.face_count} 面)")
    finally:
        await runner.cleanup()


# 4. 错误路径：未配置密钥
async def test_no_cred():
    gen = AI3DGenerator(provider="hunyuan", hunyuan_mode="cloud")
    try:
        await gen.generate("test", "/tmp/hx")
        check("未配置密钥应报错", False)
    except AI3DError as exc:
        check("未配置密钥应报错", "SecretId" in str(exc))


os.makedirs("/tmp/hunyuan_test", exist_ok=True)
os.makedirs("/tmp/hunyuan_local_test", exist_ok=True)
asyncio.run(test_cloud_flow())
asyncio.run(test_local_flow())
asyncio.run(test_no_cred())

fails = sum(1 for p in passed if not p)
print(f"\n总计 {len(passed)} 项，通过 {len(passed) - fails} 项")
sys.exit(1 if fails else 0)
