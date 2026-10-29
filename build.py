"""Build one native Windows EXE, without browser assets or external runtime folder."""
import json
import argparse
import os
import sys
from pathlib import Path
root = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument("--output-name",default="星月启动器")
output_name = parser.parse_args().output_name
if not output_name or output_name in (".","..") or any(c in output_name for c in '/\\:*?"<>|\r\n\x00'):
    parser.error("output-name must be a file name")
if (root / ".build-env").is_dir():
    sys.path.insert(0, str(root / ".build-env"))
os.environ["PYINSTALLER_CONFIG_DIR"] = str(root / ".build-cache")
os.chdir(str(root))
import PyInstaller.__main__
from PyInstaller.utils.win32.versioninfo import VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct, VarFileInfo, VarStruct
from launcher import __version__
version_numbers = tuple(int(part) for part in __version__.split("."))
version_numbers = (version_numbers + (0, 0, 0, 0))[:4]
publisher = root / ".build-cache" / "publisher.json"
publisher.parent.mkdir(parents=True, exist_ok=True)
version_file = publisher.parent / "version-info.txt"
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=version_numbers, prodvers=version_numbers, mask=0x3f, flags=0,
                      OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
    kids=[StringFileInfo([StringTable("040904B0", [
        StringStruct("CompanyName", "星月晓梦"), StringStruct("FileDescription", "StarMoonLauncher - Minecraft Java Launcher"),
        StringStruct("FileVersion", ".".join(map(str, version_numbers))), StringStruct("InternalName", "StarMoonLauncher"),
        StringStruct("OriginalFilename", output_name + ".exe"), StringStruct("ProductName", "StarMoonLauncher"),
        StringStruct("ProductVersion", __version__)])]), VarFileInfo([VarStruct("Translation", [1033, 1200])])])
version_file.write_text(str(version_info), encoding="utf-8")
from launcher.publisher import build_settings
publisher.write_text(json.dumps(build_settings(root)), encoding="utf-8")
PyInstaller.__main__.run([
    "--noconfirm", "--clean", "--onefile", "--windowed", "--name", output_name,
    "--icon", str(root / "assets" / "starmoon.ico"),
    "--version-file", str(version_file),
    "--collect-all", "pymsalruntime",
    "--hidden-import", "msal.broker",
    "--paths", str(root / ".build-env"),
    "--add-data", str(publisher) + os.pathsep + ".",
    "--add-data", str(root / "licenses") + os.pathsep + "licenses",
    "--add-data", str(root / "assets" / "authlib-injector-1.2.8.jar") + os.pathsep + "assets",
    "--add-data", str(root / "assets" / "starmoon.png") + os.pathsep + "assets",
    "--add-data", str(root / "assets" / "default-background.jpg") + os.pathsep + "assets",
    "--add-data", str(root / "assets" / "backgrounds") + os.pathsep + "assets/backgrounds",
    "--exclude-module", "PySide6.QtWebEngineCore", "--exclude-module", "PySide6.QtWebEngineWidgets",
    "--exclude-module", "PySide6.QtQml", "--exclude-module", "PySide6.QtQuick",
    "--exclude-module", "tkinter",
    "--distpath", str(root / "dist"), "--workpath", str(root / "build"), str(root / "main.py")
])
(root/"dist/current-launcher.txt").write_text(output_name+".exe\n",encoding="utf-8")
print("Native single-file build complete: " + str(root / "dist" / (output_name+".exe")))
