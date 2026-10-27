# 测试与验收

## 验证范围

按项目约定，普通修改只验证本次改动及直接受影响的功能，不运行全量回归；本地构建也不自动触发全套发布验收。只有发布版本时才执行完整功能回归与发布验收。

0.7.8 发布前完整回归共 439 项，全部通过，日志为 `test-results/release-regression-0.7.8.log`。发布检查修正了批量更新窗口的旧测试，并补充实例在目录枚举后被失败清理删除的竞态用例；读取时跳过已移除的实例元数据，界面继续刷新并显示失败弹窗。

正式 EXE 的单文件资源、Windows 版本、WAM、默认值、透明四角、中英文与预设壁纸重启验收通过，日志为 `test-results/release-acceptance-0.7.8.log`。0.7.7 → 0.7.8 实际辅助进程完成替换、备份、重启，保留便携目录设置、账号文件与存档，记录为 `test-results/auto-update-acceptance-0.7.8.log` 及对应 JSON。所有验收仅操作独立测试目录。

依赖扫描加载器匹配修复定向验证共 18 项，日志为 `test-results/dependency-loader-targeted.log`（16 项）和 `test-results/dependency-loader-native.log`（2 项）。覆盖多加载器 JAR 的元数据选择、Forge 嵌套依赖、Quilt 的 Fabric 兼容、旧 NeoForge 描述文件、错误加载器模组及禁用项不提供前置、真实 Fabric 前置仍正常报缺失、修复候选加载器不符时回滚、缓存更新及界面状态。均使用临时测试模组，未运行全量回归或启动 Minecraft。

模组批量管理、整合包升级、依赖修复、Java 管理和问题报告定向验证共 46 项，通过日志为 `test-results/management-targeted.log`。覆盖启停/删除/锁定/忽略更新、文件哈希来源识别、禁用状态保持、写入前日志与中断恢复、失败和取消回滚、整合包三方比对与配置冲突、原实例和存档保留、手动导入旧前置替换、错误候选与新增依赖问题拒绝、托管 Java 清理范围、日志筛选和脱敏，以及相关模组预览、日志入口、多开、整合包清理、中英文 UI。所有游戏数据使用临时夹具，未运行全量回归。

`python tests/integration_management.py --download-java` 已验证真实 Modrinth SHA-512 查询、整合包版本目录和 Adoptium Java 目录，并在独立临时目录下载、校验、执行 Java 21（实测输出 `21.0.12.1`）；日志为 `test-results/management-live.log`。未启动完整 Minecraft 或验证所有整合包组合。紧凑窗口及英文界面截图位于 `test-results/management-preview/`。[使用说明](MANAGEMENT.md)

高级启动设置定向验证共 24 项，日志为 `test-results/launch-options-targeted.log`。覆盖四个开关的独立持久化与校验、默认/自定义/必需 JVM 参数、Java 兼容检查、原生库架构选择与保留原文件、启动文件检查、整合包未完成状态、多开、外部目录与英文界面。真实渲染验证命令为 `python tests/integration_renderers.py --probe-graphics`，日志 `test-results/renderers-live.log` 和 `test-results/renderer-integration/`：校验下载的 Mesa 运行库，在独立临时目录通过隐藏 GLFW 窗口实际创建 LLVMpipe、Zink Vulkan 1.4 和 D3D12 上下文，均返回 OpenGL 4.6；未启动 Minecraft，也不代表所有游戏/模组和显卡驱动的组合均已验证。

Faithful 来源定向验证共 19 项，日志为 `test-results/faithful-targeted.log`。覆盖 Java 系列与分类隔离、检索和版本范围、官方目录回退、Modrinth 精确文件及哈希、无效 ZIP 拒绝、下载和记录失败回滚、启用状态保持、同名玩家文件保留、原生安装流程、署名/官网/许可入口及中英文。真实目录与独立临时安装见 `test-results/faithful-live.log`，已实际安装并校验一个约 11 MB 的 Faithful 32x 包；`test-results/faithful-modrinth-live.log` 验证两种 64x 分发链接解析。命令为 `python tests/integration_faithful.py --download-one --resolve-modrinth`，不启动游戏，也不接触用户现有实例。

加载器重试与镜像接入定向验证共 16 项，日志为 `test-results/loader-retry-mirrors-targeted.log`。覆盖鼠标和键盘重试时卡片尺寸、滚动位置不变，等待期间手动滚动不被结果覆盖，其他加载器结果保持，Fabric / NeoForge 镜像路由、官方源选择、网络与 XML 解析失败回退、元数据大小限制及原文件哈希校验。可选真实网络检查为 `python tests/integration_loader_mirrors.py`，仅读取公开版本列表与 Fabric 启动配置，不安装或启动游戏；日志为 `test-results/loader-mirrors-live.log`。

自动安装布局定向验证共 6 项，日志为 `test-results/install-layout-targeted.log`。覆盖 900×600 最小窗口下版本列表与「继续」完整可见、顶部「重新选择原版」左邻「安装游戏」、加载器列表占满宽度且独立滚动、中英文按钮显示、窄窗口单列与宽窗口双列切换，以及加载器选择与安装参数保持。界面预览位于 `test-results/install-layout-preview/`，未执行全量回归或发布验收。

数据包/地图、额外加载器、窗口标题、自定义导出和截图功能定向验证共 28 项，通过记录为 `test-results/instance-features-targeted.log`。包括格式往返、选择内容与目录、取消导出清理、地图不覆盖旧存档、数据包路径与版本检查、LiteLoader 模组、真实 Win32 测试窗口标题、截图分页与异步预览、新界面中英文及直接受影响的版本管理用例。真实加载器验证见 `test-results/extra-loaders-live.log` 和 `test-results/liteloader-live.log`；操作仅限独立测试目录，未启动完整 Minecraft。[使用与来源](INSTANCE_FEATURES.md)

账号入口合并仅验证本次导航与直接受影响功能，共 11 项，日志为 `test-results/account-navigation-targeted.log`。覆盖「设置 → 账号管理」直接展示、启动页快捷入口、旧路由重定向、「更多」保留任务与关于、账号添加移除后的列表刷新、正版登录入口、中英文切换、离线皮肤头像、玩家皮肤下载与实时设置。

账户管理修复定向运行 `test_account_management_native.py`，并选择旧有离线皮肤、账号切换和正版头像用例，共 7 项，日志为 `test-results/account-management-targeted.log`。复现配置过自定义皮肤的离线账号与正版账号同时存在时，因缺少 `QImage` 导入导致账户页构建失败、刷新计时器停止的问题。验证头像实际颜色、当前账号切换与持续刷新；所有数据为临时夹具，正版皮肤请求使用模拟。

0.7.7 发布前完整回归共 339 项，全部通过，日志为 `test-results/release-regression-0.7.7.log`。正式 EXE 通过单文件资源、Windows 版本、WAM、默认值、透明四角、中英文和壁纸重启验收；另以 0.7.6 正式 EXE 为旧程序，实际验证更新至 0.7.7 后的替换、备份、重启和便携数据保持。记录为 `test-results/release-acceptance-0.7.7.log`、`test-results/auto-update-acceptance-0.7.7.log` 及对应 JSON；测试产物不上传源码仓库。

正版头像与模组图片可定向运行 `test_player_skins.py`、`test_mod_previews.py`、`test_visual_native.py`，并选择 `test_native.py` 中离线头像、旧皮肤外层与异步图片加载相关用例；本次相关验证共 25 项，日志为 `test-results/account-mod-art-targeted.log`。使用模拟官方皮肤响应和临时 JAR，不发起真实账号登录。

模组启用状态分类与占位图可定向运行 `test_mod_filters.py`，并选取直接相关的图片加载与模组管理用例。此次共 8 项相关检查，日志为 `test-results/mod-categories-targeted.log`，覆盖分类与搜索组合、启用后当前列表刷新、资源包页面隔离、非方形占位图铺满与圆角。界面截图位于 `test-results/mod-category-preview/`。

分类栏动画与比例修正定向运行 `test_mod_filters.py`、`test_mod_sites_native.py` 和顶部导航切换用例，共 11 项检查，日志为 `test-results/mod-switch-targeted.log`。覆盖滚动条变化、连续切换、动画期间缩放后继续播放及准确归位、中英文等宽紧凑布局、关闭动画设置；不运行全量回归。

筛选后宽度稳定修正仅运行 `test_mod_filters.py`，共 8 项检查，日志为 `test-results/mod-width-targeted.log`。新增用例检查长列表切换到已禁用、已启用、全部和空搜索结果时，视口、搜索框及分类按钮的宽度始终一致。

大量模组列表按需渲染的定向验证共 20 项，日志为 `test-results/mod-virtual-targeted.log`。覆盖 1000 模组的卡片数量上限、连续分类切换、末尾卡片操作目标、空搜索恢复、等待渲染时离开页面、更新结果切回分类，以及图片、网站、导入移除和资源包/光影列表。截图位于 `test-results/mod-virtual-preview/`。同一台机器的 1000 模组模拟样本中，点击分类的同步阻塞从约 2012–3481 毫秒降至约 3–10 毫秒，日志为 `test-results/mod-switch-performance-before.log` 和 `test-results/mod-switch-performance-after.log`；此耗时不代表所有硬件上的完整绘制时间。仅做本次相关验证，未运行全量回归或发布验收。

模组网站入口定向运行 `test_mod_sites.py`、`test_mod_sites_native.py` 与直接受影响的 `test_mod_previews.py`，共 15 项相关检查，日志为 `test-results/mod-sites-targeted.log`。覆盖元数据主页、来源平台、MC百科直链与名称搜索、禁用模组、链接去重、不安全协议拒绝和中英文；浏览器调用使用模拟，不自动打开网站。

## 发布前完整回归

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

回归覆盖账号加密、WAM 调用、下载、版本管理、文件夹扫描与批量添加、模组依赖扫描、诊断回答解析、多进程运行、离线皮肤、原生控件、壁纸及持久化。网络场景使用模拟响应或本机临时服务；不读取实际游戏目录、使用玩家密钥或下载 Minecraft。扫描测试仅遍历临时夹具，不扫描开发者的实际磁盘。

0.7.6 源码包含 194 项原生启动器相关测试，覆盖自动更新、中英文切换、扫描功能、二级窗口标题栏调整和登录错误分阶段诊断。

当前本地开发版另有 39 项内容来源回归，覆盖 ATLauncher / FTB 清单与安装、MC 百科故障回退、GitHub JAR 识别与事务安装，以及原生界面。套件共 233 项。GitHub 加入前的 215 项完整回归记录在 `test-results/content-sources-regression.log`；最新测试是否已完整运行，应以对应日志为准。

含 GitHub 的完整回归日志位于 `test-results/github-source-regression.log`；针对来源与界面的定向回归另存为 `test-results/github-source-targeted.log`。

AI 下载功能当前有 25 项后端回归、10 项 MC百科链接回归与 2 项原生界面回归，完整套件共 270 项。覆盖真实候选约束、名称检索开关、补下载默认开启与一次升级迁移、手动关闭记忆、独立开关即时保存与英文显示、兼容 Chat Completions 的本机 HTTP 夹具、请求脱敏与重定向拒绝、MC百科优先级与条目选择约束、出站链接解码、只读相关链接、分页精确名称排序、故障回退、原清单哈希校验、取消、原镜像优先、CurseForge 作者限制，以及 Modrinth / ATLauncher / FTB 整合包的补下载与完整提交。最新完整回归记录在 `test-results/mcmod-ai-regression.log`；是否通过应以日志结尾为准。普通测试使用模拟模型回答，不调用用户的商业模型 API。

补下载交互另有 7 项任务协议回归与 7 项原生界面回归，当前完整套件共 284 项，日志位于 `test-results/recovery-choice-regression.log`。覆盖确认前不调用模型、版本选择前不下载、不预选、只下载所选候选、失败后再次选择、确认 / 选择窗口关闭取消、任务取消、陈旧或伪造响应拒绝、并发文件失败结束等待、英文界面，以及完整整合包导入在用户选择并通过校验后才标记为可用。窗口预览位于 `test-results/recovery-preview/`。

拖拽安装与失败清理新增 12 项会话安全回归、7 项原生拖放与失败弹窗回归，以及 1 项 AI 校验失败后整包清理的完整交互回归。当前完整套件共 304 项，记录在 `test-results/pack-drag-cleanup-regression.log`。覆盖拖到输入框、多文件排队与去重、强制复制而非移动原始整合包、已有存档与选择恢复、下载 / 元数据写入 / 解压失败回滚、取消、损坏文件、短暂占用重试、持续占用警告、目录所有权变化、拒绝越界删除、Windows 目录联接目标保留，以及 AI 成功提示必须晚于哈希校验和文件提交。失败窗口预览位于 `test-results/pack-install-preview/`。

语言回归覆盖翻译模板、动态错误码、全部主页面和设置分类、二级窗口、语言即时切换、重启记忆，以及玩家名、版本名、路径和设置草稿保持原值。

更新回归覆盖正式版本筛选、防降级、固定来源、下载限制和校验、取消、进程等待、文件占用重试、失败恢复、数据保持与安装时机。真实单文件更新辅助进程验收见 [UPDATING.md](UPDATING.md)。

本机 0.7.6 完整回归日志为 `test-results/release-regression-0.7.6.log`，自动替换验收记录见 `test-results/auto-update-acceptance.json`；日志不随源码上传。Windows DPAPI 相关用例需要在正常 Windows 用户权限下运行。

## 原生界面预览

```powershell
.\.venv\Scripts\python.exe tests/preview.py
```

在临时数据目录生成界面；结果保存在 `test-results/preview/`，不依赖本机游戏安装。

## 最终 EXE 验收

构建后执行：

```powershell
.\.venv\Scripts\python.exe tests/verify_release.py dist/StarMoonLauncher-0.7.8-windows-x64.exe
```

检查 Windows 文件版本、资源、模块、图标、WAM DLL，并将 EXE 复制到独立临时目录启动，验证默认紧凑尺寸、不自动创建游戏目录、透明四角、预设壁纸切换，以及英文界面和语言选择在多次解包重启后保持。它会启动自己的测试窗口并自动退出，结果位于 `test-results/release-verification.json`。

## 可选真实网络测试

以下命令会访问外部网络，不包含在普通回归和 CI 中：

```powershell
# 只读取三个新来源的公开元数据，不安装游戏
.\.venv\Scripts\python.exe tests/integration_providers.py

# 同上，并从 ATLauncher / FTB 各校验一个不超过 256 KiB 的文件
.\.venv\Scripts\python.exe tests/integration_providers.py --download-files

# GitHub 公开仓库与 Release JAR 列表；默认使用 gnembon/fabric-carpet
.\.venv\Scripts\python.exe tests/integration_github_source.py

# 同上，并下载一个附件检查哈希与模组元数据，不安装游戏
.\.venv\Scripts\python.exe tests/integration_github_source.py --download-one

# 检索 MC百科“地毯”条目并读取 GitHub / Modrinth 相关链接；不调用模型或下载游戏
.\.venv\Scripts\python.exe tests/integration_mcmod_links.py

# 获取公开玩家皮肤
.\.venv\Scripts\python.exe tests/download_player_skin.py Notch

# 下载 Minecraft、Fabric 和测试模组，并验证整合包导出/导入
.\.venv\Scripts\python.exe tests/integration.py

# 同上，并实际启动、观察渲染就绪后结束测试游戏
.\.venv\Scripts\python.exe tests/integration.py --launch
```

真实游戏测试可能下载大量文件，使用 `test-results/integration/` 内的独立数据；可能需要安装对应版本 Java。游戏启动、真实微软登录及商业模型接口需要分别验证，普通回归通过不能替代这些验证。

