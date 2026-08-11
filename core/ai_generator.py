"""AI 文生 3D 服务适配器（Tripo AI / Meshy AI / 腾讯混元 Hunyuan3D）。

使用 aiohttp 异步调用（AstrBot 规范：禁止 requests），
流程：提交任务 -> 轮询状态 -> 下载模型 -> 解析回 Mesh。

服务商：
- Tripo AI:   https://platform.tripo3d.ai   （有免费额度，默认模型 tripo-2.5）
- Meshy AI:   https://www.meshy.ai          （默认模型 meshy-4）
- 腾讯混元:   https://ai3d.tencentcloudapi.com（腾讯云混元生3D API，TC3 签名）
  · cloud 模式：腾讯云官方 API（极速版/专业版），需 SecretId + SecretKey
  · local 模式：GitHub 开源模型本地部署（Tencent-Hunyuan/Hunyuan3D-2，
    `python api_server.py --port 8080`，插件直连本地 /generate 接口）

API Key 由用户在插件配置中填写，留空则 AI 生成不可用（提示配置）。
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import json
import os
import time
import zipfile
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import aiohttp
import numpy as np

from .mesh import Mesh
from .parsers import parse_glb, parse_obj

#: 提交与轮询的超时上限（秒）
TASK_TIMEOUT = 360

TRIpo_BASE = "https://api.tripo3d.ai/v2/openapi"
MESHY_BASE = "https://api.meshy.ai/v2"
HUNYUAN_BASE = "https://ai3d.tencentcloudapi.com"
HUNYUAN_VERSION = "2025-05-13"
HUNYUAN_REGION = "ap-guangzhou"


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def tc3_sign(secret_id: str, secret_key: str, action: str, payload: str,
             service: str = "ai3d", region: str = HUNYUAN_REGION,
             ts: Optional[int] = None, host: Optional[str] = None) -> Tuple[Dict[str, str], str]:
    """构造腾讯云 API 3.0 TC3-HMAC-SHA256 签名请求头。

    Args:
        secret_id: 腾讯云 SecretId
        secret_key: 腾讯云 SecretKey
        action: X-TC-Action（如 SubmitHunyuanTo3DRapidJob）
        payload: JSON 请求体字符串
        service: 服务名（ai3d）
        region: 地域
        ts: 请求时间戳（测试可指定）
        host: 请求域名（默认 {service}.tencentcloudapi.com）

    Returns:
        (headers, host): 请求头字典 + 请求域名
    """
    ts = ts or int(time.time())
    host = host or f"{service}.tencentcloudapi.com"
    content_type = "application/json; charset=utf-8"

    # 1. CanonicalRequest（POST 无查询串）
    canonical_headers = f"content-type:{content_type}\nhost:{host}\n"
    signed_headers = "content-type;host"
    canonical_request = (
        f"POST\n/\n\n{canonical_headers}\n{signed_headers}\n{_sha256_hex(payload.encode('utf-8'))}"
    )

    # 2. StringToSign
    date = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    credential_scope = f"{date}/{service}/tc3_request"
    string_to_sign = (
        f"TC3-HMAC-SHA256\n{ts}\n{credential_scope}\n{_sha256_hex(canonical_request.encode('utf-8'))}"
    )

    # 3. 派生密钥并签名
    k_date = _hmac_sha256(f"TC3{secret_key}".encode("utf-8"), date)
    k_service = _hmac_sha256(k_date, service)
    k_signing = _hmac_sha256(k_service, "tc3_request")
    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    # 4. 组装 Authorization
    authorization = (
        f"TC3-HMAC-SHA256 Credential={secret_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    headers = {
        "Content-Type": content_type,
        "Host": host,
        "X-TC-Action": action,
        "X-TC-Version": HUNYUAN_VERSION,
        "X-TC-Timestamp": str(ts),
        "X-TC-Region": region,
        "Authorization": authorization,
    }
    return headers, host


class AI3DError(Exception):
    """AI 生成过程中的错误（会带上给用户看的友好提示）。"""


class AI3DGenerator:
    """AI 文生 3D 生成器。"""

    def __init__(
        self,
        provider: str = "tripo",
        api_key: str = "",
        model: str = "tripo-2.5",
        timeout: int = TASK_TIMEOUT,
        # 腾讯混元 Hunyuan3D 专用配置
        hunyuan_secret_id: str = "",
        hunyuan_secret_key: str = "",
        hunyuan_mode: str = "cloud",          # cloud=腾讯云API / local=本地部署开源模型
        hunyuan_local_url: str = "http://127.0.0.1:8080",
        hunyuan_result_format: str = "GLB",   # OBJ/GLB/STL/USDZ/FBX/MP4
        hunyuan_enable_pbr: bool = False,
        hunyuan_use_pro: bool = False,        # 用专业版接口（更精细但更慢更贵）
    ) -> None:
        self.provider = provider.strip().lower()
        self.api_key = api_key.strip()
        self.model = model.strip()
        self.timeout = timeout
        # 混元
        self.hunyuan_secret_id = (hunyuan_secret_id or "").strip()
        self.hunyuan_secret_key = (hunyuan_secret_key or "").strip()
        self.hunyuan_mode = (hunyuan_mode or "cloud").strip().lower()
        self.hunyuan_local_url = (hunyuan_local_url or "http://127.0.0.1:8080").rstrip("/")
        self.hunyuan_result_format = (hunyuan_result_format or "GLB").upper()
        self.hunyuan_enable_pbr = bool(hunyuan_enable_pbr)
        self.hunyuan_use_pro = bool(hunyuan_use_pro)

    # ------------------------------------------------------------------ #
    # 公共入口
    # ------------------------------------------------------------------ #
    async def generate(
        self,
        prompt: str,
        output_dir: str,
        download_formats: Optional[List[str]] = None,
    ) -> Tuple[Mesh, Dict[str, str]]:
        """根据提示词生成 3D 模型。

        Args:
            prompt: 文本描述
            output_dir: 下载/保存目录
            download_formats: 需要下载的文件格式（服务商提供范围内）

        Returns:
            (mesh, files): 解析后的网格 + {格式: 文件绝对路径}

        Raises:
            AI3DError: 未配置 Key / 服务商错误 / 超时
        """
        os.makedirs(output_dir, exist_ok=True)

        # 混元走独立认证（不要求通用 api_key）
        if self.provider == "hunyuan":
            if self.hunyuan_mode == "local":
                return await self._generate_hunyuan_local(prompt, output_dir)
            return await self._generate_hunyuan_cloud(prompt, output_dir)

        if not self.api_key:
            raise AI3DError(
                "尚未配置 API Key：请在插件配置中填写 "
                f"「{self.provider}_api_key」（{self.provider} 官网注册）"
            )
        if self.provider == "tripo":
            return await self._generate_tripo(prompt, output_dir, download_formats)
        if self.provider == "meshy":
            return await self._generate_meshy(prompt, output_dir, download_formats)
        raise AI3DError(f"未知 AI 服务商: {self.provider}（支持 tripo / meshy / hunyuan）")

    # ------------------------------------------------------------------ #
    # Tripo AI
    # ------------------------------------------------------------------ #
    async def _generate_tripo(
        self, prompt: str, output_dir: str, download_formats: Optional[List[str]] = None
    ) -> Tuple[Mesh, Dict[str, str]]:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with aiohttp.ClientSession(headers=headers) as session:
            # 1. 提交任务
            form = aiohttp.FormData()
            form.add_field("type", "text_to_model")
            form.add_field("prompt", prompt)
            form.add_field("model", self.model)
            async with session.post(f"{TRIpo_BASE}/task", data=form) as resp:
                body = await resp.json()
            if body.get("code") != 0:
                raise AI3DError(f"Tripo 提交任务失败: {body.get('message', body)}")
            task_id = body["data"]["task_id"]

            # 2. 轮询
            async def poll():
                async with session.get(f"{TRIpo_BASE}/task/{task_id}") as resp:
                    return await resp.json()

            result = await self._poll(poll, "Tripo", task_id)

            # 3. 提取模型 URL
            model_urls: Dict[str, str] = {}
            model_info = (result.get("data") or {}).get("result", {}).get("model", {})
            for fmt in (download_formats or ["glb", "obj"]):
                url = model_info.get(fmt)
                if url:
                    model_urls[fmt] = url

            # 4. 下载并解析
            return await self._download_and_parse(session, model_urls, output_dir)

    # ------------------------------------------------------------------ #
    # Meshy AI
    # ------------------------------------------------------------------ #
    async def _generate_meshy(
        self, prompt: str, output_dir: str, download_formats: Optional[List[str]] = None
    ) -> Tuple[Mesh, Dict[str, str]]:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with aiohttp.ClientSession(headers=headers) as session:
            # 1. 提交任务
            payload = {"mode": "preview", "prompt": prompt}
            if self.model and self.model.lower() not in ("tripo-2.5",):
                payload["art_style"] = "realistic"
            async with session.post(f"{MESHY_BASE}/text-to-3d", json=payload) as resp:
                body = await resp.json()
            if "result" not in body:
                raise AI3DError(f"Meshy 提交任务失败: {body}")
            task_id = body["result"]

            # 2. 轮询
            async def poll():
                async with session.get(f"{MESHY_BASE}/text-to-3d/{task_id}") as resp:
                    return await resp.json()

            result = await self._poll(poll, "Meshy", task_id)

            # 3. 提取模型 URL
            model_urls: Dict[str, str] = {}
            urls = result.get("model_urls", {})
            for fmt in (download_formats or ["glb", "obj", "usdz"]):
                if urls.get(fmt):
                    model_urls[fmt] = urls[fmt]

            # 4. 下载并解析
            return await self._download_and_parse(session, model_urls, output_dir)

    # ------------------------------------------------------------------ #
    # 公共流程
    # ------------------------------------------------------------------ #
    # ------------------------------------------------------------------ #
    # 腾讯混元 Hunyuan3D —— 腾讯云 API（TC3 签名）
    # ------------------------------------------------------------------ #
    async def _generate_hunyuan_cloud(
        self, prompt: str, output_dir: str
    ) -> Tuple[Mesh, Dict[str, str]]:
        """腾讯云混元生3D API（极速版/专业版，TC3-HMAC-SHA256 签名）。"""
        if not self.hunyuan_secret_id or not self.hunyuan_secret_key:
            raise AI3DError(
                "使用腾讯混元（云 API）需要先在腾讯云开通「混元生3D」并配置：\n"
                "· hunyuan_secret_id（SecretId）\n"
                "· hunyuan_secret_key（SecretKey）\n"
                "开通地址：cloud.tencent.com/product/1804"
            )

        submit_action = "SubmitHunyuanTo3DJob" if self.hunyuan_use_pro else "SubmitHunyuanTo3DRapidJob"
        query_action = "QueryHunyuanTo3DJob" if self.hunyuan_use_pro else "QueryHunyuanTo3DRapidJob"

        # 结果格式映射：腾讯云可选 OBJ/GLB/STL/USDZ/FBX/MP4
        fmt = self.hunyuan_result_format
        if fmt not in ("OBJ", "GLB", "STL", "USDZ", "FBX", "MP4"):
            fmt = "GLB"

        payload = json.dumps({
            "Prompt": prompt[:200],          # 中文提示词，≤200 字符
            "ResultFormat": fmt,
            "EnablePBR": self.hunyuan_enable_pbr,
            "EnableGeometry": not self.hunyuan_enable_pbr,  # 带纹理时关闭白模
        }, separators=(",", ":"))
        # host 从 HUNYUAN_BASE 解析（默认 ai3d.tencentcloudapi.com，便于测试替换）
        host = HUNYUAN_BASE.split("//")[-1].split("/")[0]
        headers, _ = tc3_sign(self.hunyuan_secret_id, self.hunyuan_secret_key,
                              submit_action, payload, host=host)
        url = f"{HUNYUAN_BASE}/"

        async with aiohttp.ClientSession() as session:
            # 1. 提交任务
            async with session.post(url, headers=headers, data=payload.encode("utf-8")) as resp:
                body = await resp.json()
            resp_data = body.get("Response", body)
            if "JobId" not in resp_data:
                raise AI3DError(f"混元提交任务失败: {body.get('Response', body)}")
            job_id = resp_data["JobId"]

            # 2. 轮询状态（WAIT -> RUN -> DONE/FAIL）
            start = time.monotonic()
            while True:
                if time.monotonic() - start > self.timeout:
                    raise AI3DError(f"混元生成超时（>{self.timeout}s），请稍后重试")
                q_headers, _ = tc3_sign(self.hunyuan_secret_id, self.hunyuan_secret_key,
                                        query_action, json.dumps({"JobId": job_id}), host=host)
                async with session.post(url, headers=q_headers,
                                        data=json.dumps({"JobId": job_id}).encode("utf-8")) as resp:
                    q_body = await resp.json()
                q_data = q_body.get("Response", q_body)
                status = (q_data.get("Status") or "").upper()
                if status == "DONE":
                    break
                if status == "FAIL":
                    raise AI3DError(
                        f"混元生成失败: {q_data.get('ErrorMessage') or q_data.get('ErrorCode') or '未知错误'}"
                    )
                await asyncio.sleep(5)

            # 3. 下载结果文件（Url 通常是 zip 包）
            files_3d = q_data.get("ResultFile3Ds") or []
            if not files_3d:
                raise AI3DError("混元任务完成但没有返回模型文件")
            dl_url = files_3d[0].get("Url") or ""
            if not dl_url:
                raise AI3DError("混元返回结果缺少下载地址")
            async with session.get(dl_url, timeout=aiohttp.ClientTimeout(total=300)) as resp:
                if resp.status != 200:
                    raise AI3DError(f"混元模型下载失败（HTTP {resp.status}）")
                data = await resp.read()

        # 4. 解压/解析：zip 内含模型文件
        mesh, files = self._extract_and_parse(data, fmt, output_dir, "hunyuan")
        return mesh, files

    # ------------------------------------------------------------------ #
    # 腾讯混元 Hunyuan3D —— 本地部署开源模型
    # ------------------------------------------------------------------ #
    async def _generate_hunyuan_local(
        self, prompt: str, output_dir: str
    ) -> Tuple[Mesh, Dict[str, str]]:
        """调用本地部署的 Hunyuan3D-2 开源模型 api_server。

        部署方式（见 README「本地部署混元3D 开源模型」）：
            1. git clone https://github.com/Tencent-Hunyuan/Hunyuan3D-2
            2. 按官方 README 安装依赖（PyTorch + requirements.txt + 两个自定义算子）
            3. python api_server.py --host 0.0.0.0 --port 8080
            4. 插件配置 hunyuan_local_url = http://127.0.0.1:8080
        """
        url = f"{self.hunyuan_local_url}/generate"
        payload = json.dumps({"text": prompt[:200]}, separators=(",", ":"))
        timeout = aiohttp.ClientTimeout(total=900)  # 本地模型生成较慢，放宽到 15 分钟
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers={"Content-Type": "application/json"},
                                        data=payload.encode("utf-8"), timeout=timeout) as resp:
                    if resp.status != 200:
                        text = (await resp.text())[:200]
                        raise AI3DError(
                            f"本地混元服务返回 HTTP {resp.status}：{text}\n"
                            "请确认已启动：python api_server.py --host 0.0.0.0 --port 8080"
                        )
                    data = await resp.read()
        except aiohttp.ClientConnectorError as exc:
            raise AI3DError(
                f"无法连接本地混元服务（{self.hunyuan_local_url}）：{exc}\n"
                "请先按 README 部署 Hunyuan3D-2 并启动 api_server"
            ) from exc

        if not data:
            raise AI3DError("本地混元服务返回了空数据")
        # 本地 api_server 直接返回 GLB 二进制（README curl 示例）
        fmt = self.hunyuan_result_format
        mesh = self._parse_bytes(data, "glb", "hunyuan_local")
        return mesh, self._save_files({fmt.lower(): data}, output_dir, "hunyuan_local")

    # ------------------------------------------------------------------ #
    # 公共：zip 解压 / 字节解析 / 保存
    # ------------------------------------------------------------------ #
    def _extract_and_parse(self, data: bytes, fmt: str, output_dir: str,
                           tag: str) -> Tuple[Mesh, Dict[str, str]]:
        """处理混元返回的 zip 包或单文件，解析出 Mesh 并保存。"""
        files: Dict[str, str] = {}
        mesh: Optional[Mesh] = None

        def _parse_bytes_blob(blob: bytes, ext: str):
            nonlocal mesh
            if mesh is None:
                try:
                    mesh = self._parse_bytes(blob, ext, tag)
                except Exception as exc:  # noqa: BLE001
                    print(f"[3DModel] 混元模型解析 {ext} 失败: {exc}")

        # zip 包（腾讯云返回的是 zip）
        if data[:4] == b"PK\x03\x04":
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                saved = 0
                for name in zf.namelist():
                    ext = os.path.splitext(name)[1].lstrip(".").lower()
                    if ext in ("obj", "glb", "stl", "fbx", "usdz", "mtl", "png", "jpg"):
                        blob = zf.read(name)
                        path = os.path.join(output_dir, f"{tag}_{saved}_{os.path.basename(name)}")
                        with open(path, "wb") as f:
                            f.write(blob)
                        files[ext] = path
                        if ext in ("obj", "glb", "stl"):
                            _parse_bytes_blob(blob, ext)
                        saved += 1
        else:
            # 直接是模型文件
            ext = fmt.lower()
            path = os.path.join(output_dir, f"{tag}_model.{ext}")
            with open(path, "wb") as f:
                f.write(data)
            files[ext] = path
            _parse_bytes_blob(data, ext)

        if mesh is None:
            raise AI3DError("混元模型文件解析失败（可能返回了压缩包内无网格数据）")
        return mesh, files

    def _parse_bytes(self, blob: bytes, ext: str, tag: str) -> Mesh:
        """按扩展名解析模型字节为 Mesh。"""
        if ext == "glb":
            mesh = parse_glb(blob)
        elif ext == "obj":
            mesh = parse_obj(blob.decode("utf-8", errors="replace"))
        elif ext == "stl":
            mesh = _parse_binary_stl(blob)
        else:
            raise AI3DError(f"不支持的模型格式: {ext}")
        mesh.name = tag
        return mesh

    def _save_files(self, model_urls: Dict[str, bytes], output_dir: str,
                    tag: str) -> Dict[str, str]:
        """把字节内容保存为文件，返回 {格式: 路径}。"""
        files: Dict[str, str] = {}
        for fmt, data in model_urls.items():
            path = os.path.join(output_dir, f"{tag}_{int(time.time())}.{fmt}")
            with open(path, "wb") as f:
                f.write(data)
            files[fmt] = path
        return files

    async def _poll(self, poll_fn, provider: str, task_id: str) -> dict:
        """轮询任务直到完成。"""
        start = time.monotonic()
        while True:
            if time.monotonic() - start > self.timeout:
                raise AI3DError(f"{provider} 生成超时（>{self.timeout}s），请稍后重试或换更简单的描述")
            body = await poll_fn()
            status = body.get("data", {}).get("status") if provider == "Tripo" else body.get("status")
            if provider == "Tripo":
                status = status or (body.get("data") or {}).get("status", "")
            status = (status or "").upper()
            if status in ("SUCCESS", "SUCCEEDED", "COMPLETED"):
                return body
            if status in ("FAILED", "ERROR", "CANCELED", "CANCELLED", "EXPIRED"):
                msg = (body.get("data") or {}).get("error", "") or body.get("error", "") or str(body)[:200]
                raise AI3DError(f"{provider} 生成失败: {msg}")
            await asyncio.sleep(4)

    async def _download_and_parse(
        self, session: aiohttp.ClientSession, model_urls: Dict[str, str], output_dir: str
    ) -> Tuple[Mesh, Dict[str, str]]:
        """并行下载模型文件，并解析第一个可用文件为 Mesh。"""
        files: Dict[str, str] = {}
        mesh: Optional[Mesh] = None

        async def fetch(fmt: str, url: str):
            nonlocal mesh
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                    if resp.status != 200:
                        return
                    data = await resp.read()
                ext = fmt
                path = os.path.join(output_dir, f"ai_model_{int(time.time())}.{ext}")
                with open(path, "wb") as f:
                    f.write(data)
                files[fmt] = path
                if mesh is None and data:
                    try:
                        if ext == "glb":
                            mesh = parse_glb(data)
                        elif ext == "obj":
                            mesh = parse_obj(data.decode("utf-8", errors="replace"))
                        elif ext == "stl":
                            mesh = _parse_binary_stl(data)
                    except Exception as exc:  # noqa: BLE001
                        print(f"[3DModel] 解析 {ext} 失败: {exc}")
            except Exception as exc:  # noqa: BLE001
                print(f"[3DModel] 下载 {fmt} 失败: {exc}")

        tasks = [asyncio.create_task(fetch(fmt, url)) for fmt, url in model_urls.items()]
        if tasks:
            await asyncio.gather(*tasks)

        if mesh is None:
            raise AI3DError("模型文件下载/解析失败，请检查网络或稍后重试")
        return mesh, files


def _parse_binary_stl(data: bytes) -> Mesh:
    """解析二进制 STL 为 Mesh。"""
    import struct

    if len(data) < 84:
        raise ValueError("STL 文件过小")
    n_faces = struct.unpack_from("<I", data, 80)[0]
    verts = []
    faces = []
    off = 84
    for i in range(n_faces):
        if off + 50 > len(data):
            break
        # 法线(12B) + 三个顶点(36B) + 属性(2B)
        tri = struct.unpack_from("<9f", data, off + 12)
        verts.extend([tri[0:3], tri[3:6], tri[6:9]])
        faces.append([3 * i, 3 * i + 1, 3 * i + 2])
        off += 50
    if not faces:
        raise ValueError("STL 没有有效三角形")
    return Mesh(np.array(verts, dtype=np.float32), np.array(faces, dtype=np.int32), "stl_model")
