# 🚀 GitHub 部署教程（本地执行）

> 由于 AI 沙箱网络隔离无法直连 GitHub，请在你的电脑上按本教程部署。
> 全程约 5 分钟，无需任何编程基础。

## 准备工作

- 一个 GitHub 账号（github.com）
- 电脑已安装 [Git](https://git-scm.com/downloads)（命令行工具）

## 方法 A：命令行一键推送（推荐）

打开终端（Windows 用 PowerShell / CMD，macOS/Linux 用 Terminal），逐条执行：

```bash
# 1. 解压发布包并进入目录
unzip astrbot_plugin_3dmodel.zip
cd astrbot_plugin_3dmodel

# 2. 初始化 git
git init -b main
git add .
git commit -m "feat: 3D 模型工厂 - 本地程序化生成 + Tripo/Meshy/腾讯混元 AI 文生3D"

# 3. 在 GitHub 网页新建仓库（一次）：
#    github.com → 右上角 + → New repository
#    名称填：astrbot_plugin_3dmodel，选 Public，不要勾选任何初始化选项
#    创建后复制页面显示的仓库地址，如 https://github.com/你的用户名/astrbot_plugin_3dmodel.git

# 4. 关联远程仓库（把下面的地址换成你自己的）
git remote add origin https://github.com/你的用户名/astrbot_plugin_3dmodel.git

# 5. 推送（首次会弹出 GitHub 登录窗口，网页授权即可）
git push -u origin main
```

## 方法 B：GitHub 网页上传（无需命令行）

1. 打开 https://github.com/new 创建仓库：名称 `astrbot_plugin_3dmodel`，选 Public
2. 创建后进入仓库页面，点 **uploading an existing file**（上传文件）
3. 把 `astrbot_plugin_3dmodel` 文件夹里的**所有文件**拖进去（保持目录结构，含 core/、skills/ 等子目录）
4. 点 **Commit changes**

> ⚠️ 网页上传注意：先点进 `core` 文件夹分别上传其中的文件；或用 zip 方式：把整个文件夹压缩上传后，在仓库里删掉 zip。

## 方法 C：使用 GitHub Desktop（图形界面）

1. 下载安装 [GitHub Desktop](https://desktop.github.com/)，登录账号
2. File → New repository → 名称 `astrbot_plugin_3dmodel`
3. 把发布包里的文件复制进自动创建的本地文件夹
4. 左侧看到所有文件 → 填 commit 信息 → **Commit to main**
5. 右上角 **Publish repository** → 完成！

## 发布到 AstrBot 插件市场（可选）

1. 注册登录 https://plugins.astrbot.app
2. 点发布插件，填入 GitHub 仓库地址与说明（README.md 已备好）
3. 提交后等待审核，插件 zip 需 < 16MB（本项目仅 268KB ✅）

## 后续更新代码

```bash
git add .
git commit -m "更新说明"
git push
```
