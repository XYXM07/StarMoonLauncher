# StarMoonLauncher

[简体中文](README.md)

A native Windows launcher for Minecraft: Java Edition, created by **星月晓梦**. Built with Python and PySide6, with a frameless liquid glass interface and a single-file EXE build.

Current version: **0.7.7**. [Download for Windows x64](https://github.com/XYXM07/StarMoonLauncher/releases/download/v0.7.7/StarMoonLauncher-0.7.7-windows-x64.exe) · [Releases](https://github.com/XYXM07/StarMoonLauncher/releases).

The launcher supports **Simplified Chinese and English**. Open **Settings → Appearance → Language** to switch immediately. The choice is saved for the next launch.

Launcher updates are checked against this project's stable GitHub Releases. Use **About → Check launcher updates**, or enable startup checks under **Settings → Advanced**. Downloads show progress and speed and require SHA-256 verification. Installation waits for games and other tasks to finish, replaces the EXE in place, retains a backup, and restarts with the same data folder. See [update details](docs/UPDATING.md).

Features include Microsoft sign-in through Windows Web Account Manager, offline accounts, game installation, version and mod management, modpack import and export, official and BMCLAPI download sources, parallel downloads, custom skins and wallpapers, multiple game sessions, and optional AI diagnostics.

Version 0.7.7 adds ATLauncher and FTB modpacks, MCMOD search and original-site links, and mod installation from public GitHub Release JARs. MCMOD keeps a browser fallback when its community search service is unavailable. For GitHub, enter a repository URL or owner/repo; metadata and dependencies are checked before installation. Two independent switches in **Settings → AI diagnostics** enable AI discovery by mod name (off by default) and recovery of failed modpack mod downloads (on by default). Both use the configured model API and first consult MCMOD articles and their related project links. Existing configurations enable recovery once on upgrade; later opt-outs persist. Recovered files must match the original pack hashes and size. See [content sources](docs/CONTENT_SOURCES.md).

Modpack recovery asks for confirmation before calling the model, then shows candidate versions, filenames and sources. Select a candidate explicitly before downloading; cancelling either dialog stops the installation. Failed candidates require another selection, and original pack hashes remain mandatory.

Microsoft accounts show cached skin-face avatars, including the head overlay. Installed mods show artwork embedded in their JARs, descriptions, and platform covers when embedded artwork is unavailable. Images load asynchronously and do not block sign-in or mod management.

Mod management filters all, enabled or disabled mods together with text search. Large lists create native cards only near the visible scroll position, keeping category changes responsive.

Drop local `.mrpack` or `.zip` modpacks onto the main window to queue installations, including multiple files. A failed installation shows an error dialog and removes only its newly created instance and temporary files. Original archives, existing games and worlds are preserved. AI recovery reports success only after the selected file passes integrity checks and is committed.

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
.\.venv\Scripts\python.exe build.py --output-name StarMoonLauncher-0.7.7-windows-x64
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Third-party license notices are in [licenses/NOTICE.txt](licenses/NOTICE.txt). The project has not assigned a license to its own source code or artwork. StarMoonLauncher is not affiliated with Mojang or Microsoft.
