# 星月启动器

由 **星月晓梦** 制作的 Windows Minecraft Java 版第三方启动器。使用 Python / PySide6 构建，提供原生无边框液态玻璃界面，可打包为单个 EXE。当前版本 **0.7.5**。

[下载 Windows 64 位单文件版](https://github.com/XYXM07/StarMoonLauncher/releases/download/v0.7.5/StarMoonLauncher-0.7.5-windows-x64.exe) · [GitHub Releases](https://github.com/XYXM07/StarMoonLauncher/releases)

下载 EXE 后直接双击运行，无需安装 Python 或自行编译。

![启动界面](docs/images/home.png)

## 功能

- 离线账号、Windows Web Account Manager 微软登录。
- 游戏安装与启动、Forge / NeoForge / Fabric / Quilt、同版本和不同版本多开。
- PCL 式版本管理，直接添加已有 `.minecraft`，管理模组、存档与版本参数。
- 官方源 / BMCLAPI 镜像、分段下载、任务进度和速度显示。
- Modrinth / CurseForge 内容检索、模组与整合包下载；CurseForge 需要自行配置 API Key。
- 七种界面配色、动效、可调玻璃效果、自定义壁纸与六张预设壁纸。
- 离线皮肤自定义、公开正版玩家皮肤预览与 PNG 下载。
- 自动内存分配、可选模型崩溃分析和启动前模组排查。

![预设壁纸](docs/images/wallpaper-presets.png)

## 使用

运行打包后的 `星月启动器-0.7.5.exe`。首次打开不会自动创建游戏目录；在「启动 → 版本选择」中选择「创建 .minecraft 文件夹」或「添加 .minecraft 文件夹」。下载游戏后选择版本与账号，再启动。

账号、设置、缓存和运行时保存在 EXE 同目录的 `data/`，兼容旧 `BlockCraft/data/`。移动便携目录时一起保留数据。已有启动器使用同一数据目录时，新进程会唤起旧窗口；更新前先完全退出旧版。

模型分析在「设置 → 智能排查」配置兼容接口、模型与 API Key。分析会向所配置的服务发送相关模组信息或错误日志，密钥使用 Windows DPAPI 加密保存；结果只提供原因和建议。模组静态排查无法发现全部运行期冲突。

## 从源码运行

Windows x64，开发与 CI 配置使用 Python 3.11：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe main.py
```

构建单文件 EXE：

```powershell
.\.venv\Scripts\python.exe build.py --output-name 星月启动器-0.7.5
```

运行回归测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

更多说明：[开发与构建](docs/DEVELOPMENT.md) · [测试与验收](docs/TESTING.md) · [上传 GitHub](docs/GITHUB.md)。

## 源码目录

```text
launcher/          启动器业务、原生界面与控件
assets/            图标、壁纸与离线皮肤代理
licenses/          第三方组件许可
tests/             回归测试和可选验收脚本
scripts/           源码包导出工具
docs/              文档与界面截图
.github/           Windows CI 和反馈模板
main.py            程序入口
build.py           单文件构建
requirements*.txt  运行和开发依赖
publisher.json     公开的微软应用 ID
```

源码导出：`python scripts/export_source.py`。`release-source/` 中只生成当前版本的源码目录和 ZIP，包含源码、构建必需资源、测试与文档。

## 作者与许可

作者：**星月晓梦**。本项目尚未指定自身源码与图片素材的开源许可证；第三方组件许可见 [licenses/NOTICE.txt](licenses/NOTICE.txt)。本项目与 Mojang、Microsoft 没有官方关联。
