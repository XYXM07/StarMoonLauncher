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
.\.venv\Scripts\python.exe build.py --output-name StarMoonLauncher-0.7.9-windows-x64
```

输出为 `dist/StarMoonLauncher-0.7.9-windows-x64.exe`，Windows 文件属性中的版本号与 `launcher/__init__.py` 保持一致。构建脚本一并内置图标、六张预设壁纸、WAM 运行库、离线皮肤代理和第三方许可。无需 Node.js、浏览器前端或额外运行库目录。

构建成功后更新 `dist/current-launcher.txt`，供验收脚本自动定位输出文件。同一个 EXE 正在运行时 Windows 无法覆盖它。

## 微软登录

正版登录使用 Windows Web Account Manager。`publisher.json` 仅包含发布者的公开应用 ID，不包含 Client Secret、账号或令牌。需要替换发布者配置时可编辑此文件，或在构建前设置 `BLOCKCRAFT_MS_CLIENT_ID`；构建脚本会校验 UUID 并内置配置。

发布者已确认本项目应用的 Minecraft 服务访问审核通过，仍应维护应用注册与服务访问配置。回归测试模拟认证响应，不代替真实账号登录验证。

登录失败时分别显示 Xbox Live、XSTS、Minecraft 登录、授权检查及玩家档案请求的 HTTP 失败阶段和错误码，并在运行数据目录写入 `auth-last-error.json`。文件仅记录时间、固定接口、状态码、已筛选的错误码与建议，不保存请求内容、令牌或服务端原始响应。

## CurseForge 发行版密钥

本地构建从 Git 忽略的 `publisher.local.json` 读取 `curseforge_api_key`，也支持构建环境变量 `STARMOON_CURSEFORGE_API_KEY`（优先级更高）。构建脚本将它合入 EXE 内部的发布者配置；公开的 `publisher.json` 继续只保存微软公开应用 ID，源码导出不包含私有配置。CI 构建可用同名 GitHub Actions Secret 注入环境变量。

运行时优先使用玩家在「设置 → 下载」填写的自定义密钥，否则使用发行版内置密钥。搜索、内容下载和整合包导入/升级共用这一规则；内置密钥不写入玩家的 `config.json`，界面仅接收配置状态。未提供发行版密钥的源码构建仍支持自行填写。

单文件 EXE 中的内置密钥可以被提取，不能作为对使用者保密的服务端凭据。公开源码和普通构建日志不得包含真实密钥。

## 目录职责

界面翻译使用 `launcher/i18n.py` 和 `launcher/translations.py`。新增界面文字调用 `tr()`，英文文案加入 `EN` 字典；不要翻译配置键、接口字段、玩家输入或文件路径。服务层保持中文诊断记录，在界面显示时翻译；模型诊断会根据 `language` 设置请求英文回答。

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

`.github/workflows/tests.yml` 在 Windows 上安装开发依赖并运行回归测试，无需账号或 API Key；触发方式包括 push、pull request 和手动运行。发布前应确认目标提交对应的 Windows tests 已通过。

Action 配置参考官方说明：[checkout](https://github.com/actions/checkout)、[setup-python](https://github.com/actions/setup-python)。
