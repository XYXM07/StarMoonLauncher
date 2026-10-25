# 启动器自动更新

从 0.7.6 起，默认在单文件 EXE 启动后检查更新，每 12 小时最多自动检查一次。「设置 → 高级」可以关闭启动时检查；「关于 → 检查启动器更新」随时手动检查。

客户端只读取 `XYXM07/StarMoonLauncher` 的正式 GitHub Release。仅更高版本可更新，不安装草稿或预发布版本。下载复用多连接下载器，显示真实进度和速度，可以取消。安装前会校验文件大小、SHA-256 和 Windows x64 PE 文件头。

点击「安装并重启」后，程序先保存设置。所有游戏、其他任务及正在进行的登录都结束后，才会启动内置更新辅助进程。辅助进程等待旧启动器退出，原位替换同名 EXE，并使用原数据目录重新启动。它不修改 `.Minecraft`、账号、皮肤、存档或其他用户文件，也不生成 BAT / PowerShell 脚本。

旧 EXE 备份位于程序旁，名称形如 `.StarMoonLauncher.exe.<随机标识>.backup`。替换失败或新进程无法创建时会恢复原程序；备份也可用于人工恢复。这个机制不保证检测到新程序启动后的每一种运行期错误。

缓存和安装结果位于数据目录下的 `updates/`。源码运行可以检查和下载，但自动替换只支持打包后的 Windows EXE。

## 后续发布需要

1. 在 `launcher/__init__.py` 提升 `__version__`，例如 `0.7.7`，构建对应单文件 EXE。
2. 创建正式 Release，标签例如 `v0.7.7`。
3. 附件使用固定名称 `StarMoonLauncher-0.7.7-windows-x64.exe`。
4. GitHub 的附件 `digest` 提供 SHA-256；若附件没有该字段，须同时上传 `SHA256SUMS.txt`，每行格式为 `64位SHA256  完整文件名`。

旧的公开版 0.7.5 尚不具备自动更新功能，需要先手动下载 0.7.6，完全退出旧版后在原便携目录运行，保留已有 `data/` 和游戏文件夹。0.7.6 会检查后续更高版本；当前已是最新版时不会重复更新。

## 隔离验收

```powershell
python tests/verify_updates.py dist/StarMoonLauncher-0.7.6-windows-x64.exe --candidate dist/StarMoonLauncher-0.7.7-windows-x64.exe
```

验收只复制并运行 `test-results/` 中自己的 EXE，模拟更新元数据，验证真实辅助进程等待、替换、备份、重新启动与数据保持。可用 `--candidate 新版.exe` 验证不同构建间的替换；候选程序版本应与当前源码一致。它不会替换开发者日常使用的 EXE，测试元数据不表示未来版本已经发布。
