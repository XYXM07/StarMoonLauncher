# 测试与验收

## 自动回归

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

回归覆盖账号加密、WAM 调用、下载、版本管理、模组依赖扫描、诊断回答解析、多进程运行、离线皮肤、原生控件、壁纸及持久化。网络场景使用模拟响应或本机临时服务；不读取实际游戏目录、使用玩家密钥或下载 Minecraft。

当前版本包含 137 项原生启动器相关测试。

2026-10-23 整理后完整回归：137 项全部通过，耗时 77.150 秒。本机日志为 `test-results/source-cleanup-tests.log`；日志不随源码上传。

## 原生界面预览

```powershell
.\.venv\Scripts\python.exe tests/preview.py
```

在临时数据目录生成界面；结果保存在 `test-results/preview/`，不依赖本机游戏安装。

## 最终 EXE 验收

构建后执行：

```powershell
.\.venv\Scripts\python.exe tests/verify_release.py dist/星月启动器-0.7.5.exe
```

检查资源、模块、图标、WAM DLL，并将 EXE 复制到独立临时目录启动，验证默认紧凑尺寸、不自动创建游戏目录、透明四角、预设壁纸切换和多次重开保持选择。它会启动自己的测试窗口并自动退出，结果位于 `test-results/release-verification.json`。

## 可选真实网络测试

以下命令会访问外部网络，不包含在普通回归和 CI 中：

```powershell
# 获取公开玩家皮肤
.\.venv\Scripts\python.exe tests/download_player_skin.py Notch

# 下载 Minecraft、Fabric 和测试模组，并验证整合包导出/导入
.\.venv\Scripts\python.exe tests/integration.py

# 同上，并实际启动、观察渲染就绪后结束测试游戏
.\.venv\Scripts\python.exe tests/integration.py --launch
```

真实游戏测试可能下载大量文件，使用 `test-results/integration/` 内的独立数据；可能需要安装对应版本 Java。游戏启动、真实微软登录及商业模型接口需要分别验证，普通回归通过不能替代这些验证。

