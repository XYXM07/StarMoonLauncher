# 服务器管理

入口：**更多 → 服务器管理**。分为「服务器列表」和「本地开服」。

## 服务器列表

- 添加、编辑、搜索、移除服务器，保存名称、地址、备注及默认游戏版本。
- 支持域名、IPv4、IPv6 与自定义端口。未指定端口的域名支持 Minecraft SRV 查询。
- 手动「查询状态」显示在线人数、人数上限、状态请求延迟、版本和服务器简介。状态查询不可用时，不据此禁止加入。
- 选择游戏版本后「加入服务器」，使用启动器当前选择的账号。旧版本使用 `--server/--port`，支持 Quick Play 的版本使用 `--quickPlayMultiplayer`；连接地址只影响这一次启动。
- 启动前 AI 排查阻止启动时，「仍然启动」会保留服务器地址。

## 本地开服

- **导入服务端**：选择已有服务端文件夹及其中的 JAR。已安装好的 Forge/NeoForge 可选择 `libraries` 中的 `win_args.txt`，存在的 `user_jvm_args.txt` 也会被读取。不执行 BAT 或其他 shell 脚本。
- **下载原版服务端**：获取官方版本列表，选择版本与空文件夹；下载按官方 SHA-1 和大小校验，Java 主版本来自版本元数据。首次需主动阅读并勾选 EULA。
- **控制台与配置**：查看实时日志、发送单行命令，调整 Java 路径/版本、内存、额外 JVM 参数、端口、人数、简介、难度、游戏模式、视距、正版验证、白名单和 PVP。点击「应用配置」或「启动服务端」保存；运行中禁止改写配置。
- 「停止服务端」发送 `stop`，由服务端保存世界并退出；「强制结束」需再次确认，可能丢失未保存进度。
- 已运行的服务端在「正在运行」中显示，可重新打开控制台。关闭启动器窗口时进入后台，直到游戏、服务端和任务全部结束；运行中禁止安装启动器更新或清理 Java。
- 「移除记录」只移除启动器记录，保留服务端文件和存档。

服务器列表与本地服务端记录分别保存在启动器数据目录的 `servers.json` 和 `local-servers.json`。管理器只控制由当前启动器启动的服务端；不接管其他程序启动的 Java 进程，也不会自动开放防火墙端口或配置公网转发。

本次相关验证覆盖本机状态协议、SRV、地址校验、跨版本加入参数、配置保留、EULA、进程控制和管道日志，使用隔离数据。没有进行真实公网开服或修改已有世界。

技术依据：[Minecraft Quick Play 参数](https://www.minecraft.net/en-us/article/minecraft-snapshot-23w14a)、[官方服务端说明](https://www.minecraft.net/en-us/download/server)、[mcstatus 状态协议实现](https://github.com/py-mine/mcstatus/blob/master/mcstatus/_protocol/java_client.py)。DNS 查询使用兼容项目 Python 版本的 [dnspython 2.6.1](https://pypi.org/project/dnspython/2.6.1/)。
