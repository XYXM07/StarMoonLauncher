# 上传 GitHub

当前公开仓库：[XYXM07/StarMoonLauncher](https://github.com/XYXM07/StarMoonLauncher)。主分支为 `main`，对应星月启动器 0.7.7 源码。

## 源码包

```powershell
python scripts/export_source.py
```

当前版本的源码目录和 ZIP 位于 `release-source/`，包含 Python 源码、构建配置、必需资源、测试和文档。默认输出已存在时会停止，可通过 `--output 新目录` 重新导出。

网页上传时解压 ZIP，进入 `StarMoonLauncher-0.7.7/` 后上传其中内容。保留 `.github/`、`.gitignore` 等以点开头的文件与目录。

## Git 上传

也可以直接使用整理后的源码工作目录。首次提交前检查文件清单：

```powershell
git status --short --untracked-files=all
git add .
git diff --cached --stat
git commit -m "Initial source import"
```

在 GitHub 新建空仓库，然后按照仓库页面给出的实际地址执行：

```powershell
git remote add origin https://github.com/你的用户名/你的仓库名.git
git push -u origin main
```

从 ZIP 开始时，应先在解压后的源码目录执行 `git init -b main`。克隆已有仓库则直接使用原远程地址。

## 上传内容

- `assets/` 是构建必需的图片、图标与离线皮肤代理，第三方许可位于 `licenses/`，需保留。
- EXE 在 GitHub Releases 单独上传，源码仓库不提交 `dist/`。
- `.gitignore` 排除本机运行数据、账号和密钥文件；不要强制添加这些文件。
- `publisher.json` 中为公开应用 ID，不是登录凭据。

项目尚未指定自身源码及图片素材的开源许可证。第三方组件遵循各自许可；`licenses/` 的内容不代表本项目采用相同许可证。
