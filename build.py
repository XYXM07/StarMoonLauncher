"""Build one native Windows EXE, without browser assets or external runtime folder."""
import json
import argparse
import os
import sys
import uuid
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
publisher = root / ".build-cache" / "publisher.json"
publisher.parent.mkdir(parents=True, exist_ok=True)
settings_path = root / "publisher.json"
settings = json.loads(settings_path.read_text(encoding="utf-8-sig")) if settings_path.is_file() else {}
client_id = (os.environ.get("BLOCKCRAFT_MS_CLIENT_ID") or settings.get("microsoft_client_id", "")).strip()
if client_id:
    client_id = str(uuid.UUID(client_id))
publisher.write_text(json.dumps({"microsoft_client_id": client_id}), encoding="utf-8")
PyInstaller.__main__.run([
    "--noconfirm", "--clean", "--onefile", "--windowed", "--name", output_name,
    "--icon", str(root / "assets" / "starmoon.ico"),
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
