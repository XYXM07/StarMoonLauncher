# StarMoonLauncher

[简体中文](README.md)

A native Windows launcher for Minecraft: Java Edition, created by **星月晓梦**. Built with Python and PySide6, with a frameless liquid glass interface and a single-file EXE build.

Current version: **0.7.6**. [Download for Windows x64](https://github.com/XYXM07/StarMoonLauncher/releases/download/v0.7.6/StarMoonLauncher-0.7.6-windows-x64.exe) · [Releases](https://github.com/XYXM07/StarMoonLauncher/releases).

The launcher supports **Simplified Chinese and English**. Open **Settings → Appearance → Language** to switch immediately. The choice is saved for the next launch.

Launcher updates are checked against this project's stable GitHub Releases. Use **About → Check launcher updates**, or enable startup checks under **Settings → Advanced**. Downloads show progress and speed and require SHA-256 verification. Installation waits for games and other tasks to finish, replaces the EXE in place, retains a backup, and restarts with the same data folder. See [update details](docs/UPDATING.md).

Features include Microsoft sign-in through Windows Web Account Manager, offline accounts, game installation, version and mod management, modpack import and export, official and BMCLAPI download sources, parallel downloads, custom skins and wallpapers, multiple game sessions, and optional AI diagnostics.

The publisher has confirmed that the application's Minecraft Services access review has been approved. Players still need Minecraft: Java Edition ownership and an Xbox profile. Sign-in errors identify the failed authorization stage and error code. CurseForge requires your own API key.

The launcher creates a `.minecraft` folder only when requested in **Versions**. Existing game folders are referenced in place. **Scan .minecraft folders** searches local disks or a selected directory; choose the folders to add. Version names, player names, file paths and original logs retain their original text when switching languages.

For development on Windows x64 with Python 3.11:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe main.py
```

Build and test:

```powershell
.\.venv\Scripts\python.exe build.py --output-name StarMoonLauncher-0.7.6-windows-x64
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Third-party license notices are in [licenses/NOTICE.txt](licenses/NOTICE.txt). The project has not assigned a license to its own source code or artwork. StarMoonLauncher is not affiliated with Mojang or Microsoft.
