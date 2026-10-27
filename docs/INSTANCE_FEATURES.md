# 下载、加载器与版本工具

## 高级启动设置

「版本设置 → 设置」下方新增渲染器和四个独立开关，按版本实时保存：

- OpenGL 默认、Vulkan（Mesa Zink）、DirectX 12（Mesa D3D12）和 Mesa LLVMpipe 软件渲染。Mesa 通过 OpenGL 转译工作，首次选择后启动时下载约 42–50 MB 运行库，要求 Windows 10 及以上；Vulkan / DirectX 12 还需要显卡驱动支持。运行库按所选 Java 的 x86 / x64 / ARM64 架构选择，固定版本及上游 SHA-1、大小校验，每次游戏在独立运行目录加载 DLL。
- 「不添加默认的 JVM 参数」关闭启动器生成的 `-Xms` / `-Xmx` 和编码参数；版本必需的类路径、模块及本地库参数、用户自定义 JVM 参数仍保留。需要手动内存限制时，写入自定义 `-Xmx`。
- 「不检查游戏完整性」仅跳过已安装游戏在启动前的原版、支持库和资源文件校验；首次安装与手动修复仍会检查，未完成的整合包不能借此标为可用。
- 「不检查 JVM 与游戏的兼容性」允许选择主版本不匹配的有效 Java；未指定时优先选择匹配运行时，否则可使用已找到的运行时。不会接受缺失或无法识别的 Java 程序。
- 「不尝试自动替换本地库」保留版本原有本地库架构选择规则及已解压的本地库文件；默认按所选 Java 的架构选择清单中适用的本地库。显式选择 Mesa 渲染器仍会加载其运行库。

未添加 Linux/FreeBSD 系统 GLFW、OpenAL 选项。渲染运行库使用 [Java Mesa Loader for Windows](https://github.com/HMCL-dev/mesa-loader-windows)，其上游基于 [mmozeiko/build-mesa](https://github.com/mmozeiko/build-mesa)；下载原始 Maven 发行包，不修改运行库或随 EXE 内嵌分发。渲染器选择不会安装 VulkanMod 或修改游戏模组列表。

## Faithful 资源包

「下载 → 资源包」的来源新增「Faithful」，无需 API Key。提供 Faithful 32x / 64x、Classic Faithful 32x / 64x 及 Jappa 变体，只读取 Java 版目录；可搜索系列、查看预览图、选择目标实例和兼容发布版本。带 `.x` 的官方版本范围匹配对应补丁版本，不自动扩大其他版本的兼容范围。

版本资料来自 [Faithful 官网下载目录](https://www.faithfulpack.net/archive/)，官网不可用时回退其官方 GitHub 仓库中的同一份公开数据。下载使用目录发布的文件直链，部分 64x 发布经 Modrinth 分发，版本页面会解析为该发布的实际文件并使用其哈希；只有 CurseForge 网页链接的条目不猜测下载地址，因此部分旧版本不会提供自动安装。

保留上游原始 ZIP，不改写纹理或内置许可。安装到目标实例的 `resourcepacks/`，校验 ZIP、资源包结构及 CRC；官网目录没有统一文件哈希时，计算的本地 SHA-256 用于文件识别，不充当上游签名。失败或取消保留原文件和记录，更新保留启用/禁用状态，旧文件移入版本回收目录。检索与版本窗口显示 Faithful Resource Pack 署名、官网和[许可链接](https://www.faithfulpack.net/license)。

## 数据包与地图

「下载」新增「数据包」「地图」。数据包支持 Modrinth 与 CurseForge；选择目标游戏版本后，再选择已有存档，安装到该存档的 `datapacks/`，不会安装到模组目录。没有存档时，先在游戏中创建世界。

地图通过 CurseForge 下载，需要在「设置 → 下载」配置有效的 CurseForge API Key。选择版本后导入 `saves/`；存在同名存档时创建新目录，保留原存档。损坏、越界或包含多个世界的压缩包会拒绝导入；临时文件由本次安装清理。

## 加载器

选定 Minecraft 原版版本后，安装页并列显示原版、Forge、NeoForge、Fabric、Quilt、OptiFine、Cleanroom、LiteLoader。选择一个安装方案；每项独立获取兼容版本，不支持的项不可选择，网络失败可单独重试。

「设置 → 下载 → 游戏下载源」选择「BMCLAPI 镜像」后，Fabric 版本列表、启动配置与 Maven 文件，以及 NeoForge 版本列表、安装器与匹配的 Maven 文件优先使用国内镜像，失败后回退到官方源。选择官方源时直接访问官方。NeoForge 官方 Java 安装器内部发起的依赖请求仍由安装器自行处理，可能继续访问上游；此处没有修改安装器本身。镜像版本同步可能晚于官方。

- OptiFine 使用 BMCLAPI 的真实版本目录，下载后使用原版客户端与 OptiFine 自带的补丁器生成文件，并读取安装器内置的启动库。
- Cleanroom 当前仅提供 Minecraft 1.12.2 的选项。使用官方安装器内的完整版本清单及内置库，按照清单匹配 Java；已校验的 0.7.12-alpha 要求 Java 25。旧安装器缺少标准清单时会提示选择较新版本。
- LiteLoader 使用真实版本清单和文件 MD5，适配其支持的旧版 Minecraft；模组管理支持导入、显示与禁用 `.litemod`。

上游参考：[BMCLAPI 文档](https://bmclapidoc.bangbang93.com/)、[Cleanroom 安装指南](https://cleanroommc.com/wiki/end-user-guide/installation/install-client)、[Quilt 元数据](https://meta.quiltmc.org/)。Cleanroom 的整合包兼容与推荐配套模组请按上游指南配置。

## 自定义窗口标题

「版本设置 → 设置」新增「游戏窗口标题」，输入后实时保存，下次启动游戏时应用。留空保持游戏默认标题。支持 `{name}`（版本名称）、`{version}`（Minecraft 版本）、`{player}`（玩家名）。只修改本次游戏进程拥有的 Minecraft / LWJGL / GLFW 窗口。

## 自定义整合包导出

「版本概览 → 导出整合包」打开选项窗口，可设置整合包名称、版本、作者、简介、格式、压缩级别及输出文件夹。可分别选择模组、配置、资源包、光影、脚本、存档、截图、游戏选项，并控制是否包含已禁用模组。默认不包含存档、截图和游戏选项。

支持星月 ZIP、Modrinth `.mrpack`、CurseForge ZIP，所选内容写入 `overrides/`，使用各自的标准清单。星月 ZIP 另保留自定义窗口标题。OptiFine / Cleanroom / LiteLoader 使用星月 ZIP；Quilt 使用星月 ZIP 或 Modrinth 格式。取消或失败时清理本次未完成的导出文件。

## 截图查看

版本侧栏新增「截图」，版本参数页也提供「查看截图」快捷入口。读取所选版本实际游戏目录的 `screenshots/`，支持 PNG、JPEG、WebP；提供异步缩略图、每页 20 张、刷新、原生大图预览、系统图片查看器与文件夹入口。

## 定向验证

普通修改仅运行本次相关检查，日志为 `test-results/instance-features-targeted.log`；不执行全量回归或发布验收。公开目录及独立的真实文件验证记录在 `test-results/extra-loaders-live.log`、`test-results/liteloader-live.log`，其中执行真实 OptiFine 补丁器、校验 Cleanroom 官方完整清单与内置库，以及 LiteLoader 的实际文件、MD5 和启动类。未在这些检查中启动完整 Minecraft 游戏。

可选网络检查命令：

```powershell
python tests/integration_extra_loaders.py --patch-optifine --inspect-cleanroom
```
