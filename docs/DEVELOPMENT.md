# 开发与构建

开发平台为 Windows x64，CI 配置使用 Python 3.11。运行依赖位于 `requirements.txt`；测试与构建依赖位于 `requirements-dev.txt`。

## 准备环境

在源码根目录的 PowerShell 中执行：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe main.py
```

## 构建单文件 EXE

```powershell
.\.venv\Scripts\python.exe build.py --output-name 星月启动器-0.7.5
```

输出为 `dist/星月启动器-0.7.5.exe`。构建脚本一并内置图标、六张预设壁纸、WAM 运行库、离线皮肤代理和第三方许可。无需 Node.js、浏览器前端或额外运行库目录。

构建成功后更新 `dist/current-launcher.txt`，供验收脚本自动定位输出文件。同一个 EXE 正在运行时 Windows 无法覆盖它。

## 微软登录

正版登录使用 Windows Web Account Manager。`publisher.json` 仅包含发布者的公开应用 ID，不包含 Client Secret、账号或令牌。需要替换发布者配置时可编辑此文件，或在构建前设置 `BLOCKCRAFT_MS_CLIENT_ID`；构建脚本会校验 UUID 并内置配置。

发布者应自行维护应用注册及 Minecraft 服务访问配置。应用 ID 本身不能证明应用具备服务访问资格。回归测试模拟认证响应，不代替真实账号登录验证。

## 目录职责

| 路径 | 内容 |
| --- | --- |
| `main.py` | 原生入口、单实例锁与便携数据路径 |
| `launcher/desktop.py` | 页面与对话框 |
| `launcher/service.py` | 账号、版本、内容及诊断的业务入口 |
| `launcher/core.py` | 配置、网络、任务与路径校验 |
| `launcher/game.py`、`folders.py` | 安装、启动、多开及外部游戏目录 |
| `launcher/accounts.py`、`wam.py` | 账号与 WAM 登录 |
| `launcher/content.py`、`downloader.py` | 模组、整合包与分段下载 |
| `launcher/diagnostics.py` | 模组排查与模型诊断 |
| `launcher/skins.py`、`player_skins.py` | 离线皮肤及公开玩家皮肤 |
| 其余 `launcher/*_ui.py` 等 | 玻璃材质、动效及原生控件 |
| `assets/`、`licenses/` | 必需资源与第三方许可 |
| `tests/` | 当前回归测试与可选验收脚本 |
| `scripts/export_source.py` | 生成干净源码目录和 ZIP |

## GitHub Actions

`.github/workflows/tests.yml` 在 Windows 上安装开发依赖并运行回归测试，无需账号或 API Key；触发方式包括 push、pull request 和手动运行。本地整理阶段未触发远程 CI。

Action 配置参考官方说明：[checkout](https://github.com/actions/checkout)、[setup-python](https://github.com/actions/setup-python)。
