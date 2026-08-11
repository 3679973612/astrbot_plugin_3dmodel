---
name: 3d-model-creator
description: 创建 3D 模型。当用户要求「做一个/生成一个 3D 模型」「做个花瓶/齿轮/球体等模型」「3D 打印一个 XX」「用文字生成 3D 模型」「AI 建模」「混元3D」时使用。支持本地程序化生成 15+ 种参数化模型（球体/花瓶/齿轮/弹簧/地形/文字浮雕/心形/环形结等），以及 AI 文生 3D（Tripo/Meshy/腾讯混元 Hunyuan3D，含腾讯云 API 与 GitHub 开源模型本地部署），输出 STL/OBJ/GLB 模型文件、PNG 预览图和可旋转的 HTML 交互预览页。
---

# 创建 3D 模型（3D Model Creator）

本技能指导如何通过「3D 模型工厂」插件在聊天中创建 3D 模型。

## 能力总览

| 能力 | 说明 |
|------|------|
| 本地程序化生成 | 无需联网、无需 API Key，15+ 种参数化模型 |
| AI 文生 3D | 文字描述生成逼真模型，支持 Tripo / Meshy / 腾讯混元三种服务商 |
| 腾讯混元接入 | ①腾讯云 API（cloud 模式，TC3 签名，免费额度）②本地部署开源模型（local 模式，GitHub: Tencent-Hunyuan/Hunyuan3D-2，免费但需 GPU） |
| 输出格式 | STL（3D 打印）/ OBJ（通用）/ GLB（游戏/网页） |
| 结果展示 | 模型文件 + PNG 渲染预览图 + HTML 3D 交互预览页 |

## 触发方式

1. **指令触发**：用户发送 `/3d ...` 系列指令
2. **LLM 自动调用**：对话中用户表达建模意图时，调用 LLM 工具 `create_3d_model`，参数：
   - `prompt`：用户的完整描述
   - `model_type`：模型类型（见下表），缺省花瓶
   - 其余命名参数传给生成器（如 `height=10`）

## 支持的模型类型

| 模型名 | 中文别名 | 关键参数 | 示例 |
|--------|----------|----------|------|
| cube | 立方体/方块 | size | `/3d cube size=3` |
| sphere | 球体 | radius, segments | `/3d 球 radius=1.5` |
| icosphere | 细分球 | radius, detail(0-3) | `/3d icosphere detail=3` |
| cylinder | 圆柱 | radius, height | `/3d 圆柱 高度=5 半径=1` |
| cone | 圆锥 | radius, height | `/3d cone` |
| torus | 甜甜圈/环面 | radius, tube | `/3d 甜甜圈 tube=0.4` |
| prism | 棱柱 | sides, radius, height | `/3d prism sides=8` |
| pyramid | 金字塔/棱锥 | size, height, sides | `/3d 金字塔` |
| vase | 花瓶 | height, radius, style(classic/tulip/gourd/amphora) | `/3d 花瓶 高度=10 样式=tulip` |
| gear | 齿轮 | teeth, radius, height, hole_ratio | `/3d gear teeth 16` |
| spring | 弹簧 | radius, height, coils, tube | `/3d 弹簧 圈数=8` |
| terrain | 地形/山 | size, height, seed, resolution | `/3d 地形 seed=7` |
| text | 文字浮雕 | text, size, thickness | `/3d text text=你好 厚度=0.5` |
| moebius | 莫比乌斯带 | radius, width | `/3d moebius` |
| heart | 心形 | size, tube | `/3d 爱心` |
| torus_knot | 环形结 | p, q, radius, tube | `/3d torus_knot p=2 q=3` |
| sierpinski | 分形金字塔 | iterations(0-3) | `/3d sierpinski iterations=2` |

## 参数语法

指令参数支持三种写法（可混用）：
- 键值对：`height=10 radius=3`
- 长横线：`--height 10 --radius 3`
- 中文键：`高度=10 半径:3 样式=tulip`

常用中文键映射：高度→height、半径→radius、边长→size、厚度→thickness、齿数→teeth、圈数→coils、边数→sides、分段→segments、文字→text、样式→style、种子→seed、管径→tube。

## AI 文生 3D

用户要求「AI 建模 / 智能生成」时：

```
/3d ai 一只戴帽子的机械猫，赛博朋克风格
```

前置条件：插件配置中已填写所选服务商的密钥：
- `ai_provider=tripo` → 需 `tripo_api_key`
- `ai_provider=meshy` → 需 `meshy_api_key`
- `ai_provider=hunyuan` → 需 `hunyuan_secret_id` + `hunyuan_secret_key`（cloud 模式）
  或 `hunyuan_mode=local` + `hunyuan_local_url`（本地部署开源模型，见 README）

未配置时返回引导提示。AI 生成耗时 1~5 分钟，插件会先发送「正在建模」提示。

### 腾讯混元 Hunyuan3D 两种接入方式

1. **cloud（腾讯云 API，推荐）**：`hunyuan_mode=cloud`
   - 腾讯云控制台开通「混元生3D」（cloud.tencent.com/product/1804，有免费额度）
   - 配置 `hunyuan_secret_id` / `hunyuan_secret_key`（TC3-HMAC-SHA256 签名，插件已内置实现）
   - 默认调用极速版接口（SubmitHunyuanTo3DRapidJob），可开 `hunyuan_use_pro` 切专业版
   - 结果格式由 `hunyuan_result_format` 控制（GLB/OBJ/STL/USDZ/FBX/MP4）

2. **local（GitHub 开源模型本地部署）**：`hunyuan_mode=local`
   - 官方仓库：https://github.com/Tencent-Hunyuan/Hunyuan3D-2 （混元3D 2.0，几何+纹理双模型）
   - 部署：`git clone` → 安装 PyTorch + requirements.txt → 编译两个自定义算子 → `python api_server.py --host 0.0.0.0 --port 8080`
   - 插件配置 `hunyuan_local_url=http://127.0.0.1:8080`，直接 POST /generate 文生3D
   - 免费、数据不出内网，但需要 NVIDIA GPU（建议 ≥24GB 显存）

## 使用建议

- 本地生成耗时约 1~3 秒，AI 生成 1~5 分钟，务必先发「⏳ 正在生成」让用户感知进度
- 模型较大时（高 segments/resolution）注意控制参数，避免消息过大；插件内置 60000 三角面上限保护
- 3D 打印用途建议 STL 格式；网页/AR 用途建议 GLB；通用用途建议 OBJ
- 生成失败时给出原因与改进建议（换更简单的描述、检查 API Key、降低精度参数）
