# 🧊 3D 模型工厂（astrbot_plugin_3dmodel）

在 AstrBot 聊天中直接创建 3D 模型：**本地程序化生成**（15+ 种参数化模型，无需联网）+
**AI 文生 3D**（Tripo / Meshy / 腾讯混元 Hunyuan3D）。输出 **STL / OBJ / GLB 模型文件 + PNG 预览图 + 可旋转的 HTML 交互预览页**。

> 3D 打印、游戏建模、课堂演示、文创设计，一句话搞定。

---

## ✨ 功能特性

| 特性 | 说明 |
|------|------|
| 🏭 本地程序化生成 | 球体/花瓶/齿轮/弹簧/地形/文字浮雕/心形/环形结等 15+ 种，零成本零依赖 |
| 🤖 AI 文生 3D | 文字描述生成逼真模型，支持 Tripo AI、Meshy AI、腾讯混元 Hunyuan3D 三大服务商 |
| 🌀 腾讯混元双通道 | ①云 API：腾讯云「混元生3D」官方接口（TC3 签名，极速版/专业版）②本地部署：GitHub 开源模型 Hunyuan3D-2，免费且数据不出内网 |
| 📦 多格式导出 | STL（3D 打印）/ OBJ（通用）/ GLB（游戏/网页/AR） |
| 🖼️ PNG 预览图 | 纯软件渲染（z-buffer + Lambert 光照），无需 GPU，聊天内直接看图 |
| 🖥️ HTML 交互预览 | Three.js 自包含页面，浏览器打开即可拖拽旋转、缩放查看 |
| 🧠 LLM 工具 | 注册 `create_3d_model` 工具，AI 对话中自动识别建模意图并调用 |
| 🌏 中文友好 | 指令、参数、输出提示全中文，支持中文参数键（高度/半径/齿数...） |
| ⚡ 异步实现 | CPU 密集任务走线程池，不阻塞 AstrBot 事件循环 |

## 📥 安装

### 方式一：插件市场（推荐）

在 AstrBot WebUI → 插件管理 → 插件市场 中搜索「3D 模型工厂」一键安装。

### 方式二：手动安装

```bash
# 进入 AstrBot 插件目录
cd <AstrBot>/data/plugins
# 克隆或解压本项目
git clone https://github.com/<你的仓库>/astrbot_plugin_3dmodel
# 或解压 zip
unzip astrbot_plugin_3dmodel.zip -d astrbot_plugin_3dmodel
```

然后在 AstrBot WebUI → 插件管理 中启用插件。插件依赖（numpy/pillow/aiohttp）会自动安装，
或在插件目录执行 `pip install -r requirements.txt`。

## ⚙️ 配置

在 WebUI → 插件管理 → 3D 模型工厂 → 配置 中设置：

| 配置项 | 默认 | 说明 |
|--------|------|------|
| `default_engine` | local | 默认生成引擎：local=本地生成，ai=AI 生成 |
| `default_format` | stl | 默认导出格式：stl / obj / glb |
| `ai_provider` | tripo | AI 服务商：tripo / meshy / hunyuan（腾讯混元） |
| `tripo_api_key` | 空 | Tripo API Key（[platform.tripo3d.ai](https://platform.tripo3d.ai) 注册，有免费额度） |
| `meshy_api_key` | 空 | Meshy API Key（[meshy.ai](https://www.meshy.ai) 注册） |
| `ai_model` | tripo-2.5 | AI 生成模型名 |
| `hunyuan_mode` | cloud | 混元接入：cloud=腾讯云 API，local=本地部署开源模型 |
| `hunyuan_secret_id` | 空 | 腾讯云 SecretId（cloud 模式必填） |
| `hunyuan_secret_key` | 空 | 腾讯云 SecretKey（cloud 模式必填） |
| `hunyuan_local_url` | http://127.0.0.1:8080 | 本地 Hunyuan3D api_server 地址（local 模式） |
| `hunyuan_result_format` | GLB | 混元结果格式：GLB/OBJ/STL/USDZ/FBX |
| `hunyuan_enable_pbr` | false | 混元是否开启 PBR 材质 |
| `hunyuan_use_pro` | false | 混元使用专业版接口（质量更高更慢） |
| `send_preview_image` | true | 发送 PNG 渲染预览图 |
| `send_model_file` | true | 发送模型文件 |
| `send_html_preview` | false | 发送 HTML 交互预览页 |
| `max_triangle_count` | 60000 | 本地生成三角面上限 |

## 🌀 腾讯混元 Hunyuan3D 接入

插件内置两种混元 3D 接入方式（`ai_provider=hunyuan` 时生效）：

### 方式一：腾讯云 API（推荐，有免费额度）

1. 腾讯云控制台开通「混元生3D」：https://cloud.tencent.com/product/1804
2. 在 [API 密钥管理](https://console.cloud.tencent.com/cam/capi) 获取 SecretId / SecretKey
3. 插件配置：`hunyuan_mode=cloud` + 填入密钥
4. 使用：`/3d ai 一只戴帽子的机械猫`

实现细节：插件内置 **TC3-HMAC-SHA256 签名**（已与腾讯云官方 Python SDK 逐位验证一致），
默认调用极速版接口 `SubmitHunyuanTo3DRapidJob`，可开启 `hunyuan_use_pro` 切专业版。

### 方式二：GitHub 开源模型本地部署（免费、数据不出内网）

混元3D 2.0 已在 GitHub 开源：https://github.com/Tencent-Hunyuan/Hunyuan3D-2

```bash
# 1. 克隆仓库
git clone https://github.com/Tencent-Hunyuan/Hunyuan3D-2.git
cd Hunyuan3D-2

# 2. 安装依赖（先装官方 PyTorch：pytorch.org）
pip install -r requirements.txt
# 编译两个自定义算子（用于纹理生成）
cd hy3dgen/texgen/custom_rasterizer && python setup.py install && cd ../../..
cd hy3dgen/texgen/differentiable_renderer && python setup.py install && cd ../../..

# 3. 启动 API 服务器（文生3D / 图生3D）
python api_server.py --host 0.0.0.0 --port 8080
```

> 硬件要求：NVIDIA GPU，建议 ≥24GB 显存（2.6B 几何模型 + 1.3B 纹理模型）。
> 无 GPU 也可体验在线版：https://3d.hunyuan.tencent.com 或 HuggingFace Space。

启动后插件配置：`hunyuan_mode=local`，`hunyuan_local_url=http://127.0.0.1:8080`，
即可通过 `/3d ai <描述>` 直接调用本地混元模型（本地服务直接返回 GLB）。

## 🚀 使用

### 本地生成

```
/3d 花瓶 高度=10 样式=tulip
/3d gear teeth 16 radius 2.2
/3d 甜甜圈 tube=0.4
/3d 地形 seed=7 分辨率=120
/3d text text=你好 厚度=0.5
/3d 爱心
/3d torus_knot p=2 q=3
```

### AI 文生 3D（需 API Key）

```
/3d ai 一只戴帽子的机械猫，赛博朋克风格      # Tripo / Meshy
/3d ai 一把中世纪的橡木椅子                   # 腾讯混元（云 API 或本地部署均可）
```

### 辅助指令

```
/3d list    # 查看全部支持的模型与参数
/3d help    # 查看帮助
```

### LLM 自动调用

对话中直接说「给我做个花瓶」「造一个齿轮模型」，AI 会自动调用 `create_3d_model` 工具完成建模。

## 📁 输出说明

- 文件保存在 AstrBot 数据目录的 `3dmodel/` 子目录（重装/更新插件不丢失）
- 每次生成输出三件套：
  - `*.stl/obj/glb` — 模型文件（可直接导入切片软件 / Blender / Unity）
  - `*.png` — 渲染预览图（聊天内直接查看）
  - `*.html` — 交互预览页（浏览器打开，鼠标拖拽旋转）

## ❓ 常见问题

**Q：AI 生成提示需要配置 Key？**
按服务商配置：Tripo 填 `tripo_api_key`（注册有免费额度）；腾讯混元填 `hunyuan_secret_id` + `hunyuan_secret_key`（腾讯云开通有免费额度），或 `hunyuan_mode=local` 本地部署开源模型（免费）。不配置也能用本地程序化生成。

**Q：生成的文件在哪？**
AstrBot 数据目录（`data/store/.../3dmodel/` 或 `data/plugins/astrbot_plugin_3dmodel/data/3dmodel/`）。

**Q：混元 cloud 模式报签名错误？**
检查 SecretId/SecretKey 是否成对正确（CAM 密钥管理页复制），地域默认 ap-guangzhou。插件内置 TC3 签名与官方 SDK 逐位一致，无需额外依赖。

**Q：混元 local 模式连不上？**
确认已启动 `python api_server.py --host 0.0.0.0 --port 8080`，且 `hunyuan_local_url` 端口一致；首次加载模型需要较长时间，请耐心等待日志出现监听信息。

**Q：模型太精细导致消息卡顿？**
降低参数（segments、resolution、detail），或调小 `max_triangle_count`。

**Q：中文字体浮雕不显示？**
需要系统安装中文字体（Noto Sans CJK / 文泉驿）。无中文字体时自动回退到内置英文字体。

## 🧩 插件内嵌 Skill

插件自带 `skills/3d-model-creator/SKILL.md`，AstrBot 加载插件后会自动注册该 Skill，
供 LLM 理解 3D 建模能力并正确调用工具。

## 📜 技术栈

- 纯 Python + numpy + pillow（渲染零 GPU 依赖）
- aiohttp 异步请求 AI 服务（Tripo / Meshy / 腾讯云混元生3D）
- 自研 TC3-HMAC-SHA256 签名（与腾讯云官方 SDK 逐位一致，零额外依赖）
- 自研轻量软件渲染器（透视投影 + z-buffer + Lambert 光照）
- 自研 GLB 导出/解析（glTF 2.0 二进制）
- 本地部署适配：Hunyuan3D-2 开源模型 api_server（GitHub: Tencent-Hunyuan/Hunyuan3D-2）

## 🛡️ 许可

MIT License
