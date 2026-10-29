"""Validate the release archive, Windows icon resources, and clean single-EXE startup."""
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / ".build-env"))
sys.path.insert(0, str(root))
from launcher.appearance import BACKGROUND_PRESETS
from launcher import __version__
import pefile
from PIL import Image
from PyInstaller.archive.readers import CArchiveReader

output = root/"test-results"
output.mkdir(parents=True,exist_ok=True)
pointer = root/"dist/current-launcher.txt"
filename = pointer.read_text(encoding="utf-8").strip() if pointer.is_file() else "星月启动器.exe"
executable = Path(sys.argv[1]).resolve() if len(sys.argv)>1 else root/"dist"/filename
archive = CArchiveReader(str(executable))
names = {name.replace("\\", "/"): name for name in archive.toc}
modules = archive.open_embedded_archive("PYZ.pyz").toc
assert "launcher.diagnostics" in modules and "tomli" in modules and "launcher.live_settings" in modules
assert "launcher.appearance" in modules and "launcher.player_skins" in modules
assert "launcher.wallpaper_ui" in modules
assert "launcher.providers" in modules and "launcher.pack_install" in modules
assert "launcher.github_source" in modules
assert "launcher.ai_downloads" in modules
assert "launcher.mcmod_links" in modules
assert "launcher.recovery_ui" in modules
assert "launcher.pack_session" in modules and "launcher.pack_ui" in modules
assert "launcher.mod_previews" in modules
assert all(name in modules for name in ('launcher.server_list', 'launcher.local_servers', 'launcher.server_ui',
                                       'launcher.diagnostic_batches', 'launcher.publisher', 'dns.resolver', 'dns.rdtypes.IN.SRV'))
assert "launcher.mod_sites" in modules
assert "launcher.local_list" in modules
assert all(name in modules for name in ("launcher.management", "launcher.management_ui", "launcher.pack_updates", "launcher.dependencies", "launcher.java_manager", "launcher.problem_reports"))
assert all(name in modules for name in ("launcher.extra_loaders", "launcher.game_title", "launcher.world_content", "launcher.screenshots", "launcher.instance_ui", "launcher.pack_export", "launcher.faithful", "launcher.launch_options"))
assert "launcher.i18n" in modules and "launcher.translations" in modules
assert all(name in modules for name in ("launcher.updater", "launcher.update_install", "launcher.update_ui"))
assert archive.extract(names["assets/default-background.jpg"]) == (root / "assets/default-background.jpg").read_bytes()
for preset,_,filename in BACKGROUND_PRESETS:
    asset = "assets/"+filename
    original = root/asset
    assert archive.extract(names[asset]) == original.read_bytes(),asset
assert "licenses/tomli.txt" in names
assert 'licenses/dnspython.txt' in names
assert archive.extract(names["assets/starmoon.png"]) == (root / "assets/starmoon.png").read_bytes()
publisher = json.loads(archive.extract(names["publisher.json"]))
expected_publisher = os.environ.get("BLOCKCRAFT_MS_CLIENT_ID") or json.loads((root/"publisher.json").read_text(encoding="utf-8"))["microsoft_client_id"]
assert publisher["microsoft_client_id"] == expected_publisher.strip()
from launcher.publisher import build_settings
expected_key = build_settings(root).get('curseforge_api_key', '')
assert expected_key and publisher.get('curseforge_api_key') == expected_key, 'Release requires the configured publisher key'
assert hashlib.sha256(archive.extract(names["assets/authlib-injector-1.2.8.jar"])).hexdigest() == "9c7f4343e6c82034958ffb48c14a2cb0c85928be7283103ce17da00c6d5a7b10"

icon = (root / "assets/starmoon.ico").read_bytes()
count = struct.unpack_from("<H", icon, 4)[0]
expected = set()
for index in range(count):
    size, offset = struct.unpack_from("<II", icon, 6 + index * 16 + 8)
    expected.add(hashlib.sha256(icon[offset:offset + size]).hexdigest())
pe = pefile.PE(str(executable))
fixed = pe.VS_FIXEDFILEINFO[0]
expected_version = tuple(int(part) for part in __version__.split(".")) + (0,)
assert (fixed.FileVersionMS >> 16, fixed.FileVersionMS & 65535, fixed.FileVersionLS >> 16, fixed.FileVersionLS & 65535) == expected_version
strings = {key.decode("utf-8"): value.decode("utf-8") for entries in pe.FileInfo for entry in entries
           if hasattr(entry, "StringTable") for table in entry.StringTable for key, value in table.entries.items()}
assert strings["ProductVersion"] == __version__
actual = set()
for entry in pe.DIRECTORY_ENTRY_RESOURCE.entries:
    if entry.id == 3:
        for child in entry.directory.entries:
            for language in child.directory.entries:
                record = language.data.struct
                actual.add(hashlib.sha256(pe.get_data(record.OffsetToData, record.Size)).hexdigest())
assert expected <= actual, "The EXE resources do not match the new multi-size icon"
pe.close()

isolated = Path(tempfile.mkdtemp(prefix="isolated-release-", dir=str(output)))
copy = isolated / executable.name
shutil.copy2(str(executable), str(copy))
environment = dict(os.environ)
for name in ("PYTHONHOME", "PYTHONPATH", "BLOCKCRAFT_MS_CLIENT_ID", "STARMOON_CURSEFORGE_API_KEY"):
    environment.pop(name, None)
environment["PATH"] = str(Path(environment.get("SystemRoot", "C:/Windows")) / "System32")
image = isolated / "exe.png"
result = subprocess.run([str(copy), "--smoke-test", "--screenshot", str(image)], env=environment, timeout=60)
assert result.returncode == 0
smoke = json.loads((isolated / "data/native-smoke-result.json").read_text(encoding="utf-8"))
assert smoke["success"] and smoke["version"] == __version__ and smoke["wam_runtime"]
assert not (isolated / ".Minecraft").exists()
config = json.loads((isolated / "data/config.json").read_text(encoding="utf-8"))
assert config["memory_auto"] and config["download_parallel"]
assert config["auto_check_updates"]
assert config["ai_github_search"] is False and config["ai_pack_download_fallback"] is True
assert config["ai_pack_download_default_on"] is True
assert config['ai_dependency_check'] is True
assert not config['curseforge_key'] and expected_key not in json.dumps(config)
assert config["background_mode"] == "default" and not config["background_path"]
assert config["background_preset"] == "default"
assert (config["launcher_width"],config["launcher_height"]) == (900,600)
pixels = Image.open(image).convert("RGBA")
assert all(pixels.getpixel(point)[3] == 0 for point in ((0, 0), (pixels.width - 1, 0), (0, pixels.height - 1), (pixels.width - 1, pixels.height - 1)))
config.update(background_mode="preset",background_preset="milky-way",background_path="")
(isolated/"data/config.json").write_text(json.dumps(config),encoding="utf-8")
preset_image = isolated/"preset-first.png"
again_image = isolated/"preset-restart.png"
for target in (preset_image,again_image):
    result = subprocess.run([str(copy),"--smoke-test","--screenshot",str(target)],env=environment,timeout=60)
    assert result.returncode == 0
    restored = json.loads((isolated/"data/config.json").read_text(encoding="utf-8"))
    assert restored["background_mode"] == "preset" and restored["background_preset"] == "milky-way"
    assert restored["background_path"] == ""
first = Image.open(preset_image).convert("RGBA")
second = Image.open(again_image).convert("RGBA")
# The home background must change from the default and remain stable across
# two independent one-file extractions; sample only unobstructed background.
sample = (first.width-55,first.height-70,first.width-25,first.height-40)
assert first.crop(sample).tobytes() != pixels.crop(sample).tobytes()
assert first.crop(sample).tobytes() == second.crop(sample).tobytes()
config = json.loads((isolated / "data/config.json").read_text(encoding="utf-8"))
config["language"] = "en_US"
(isolated / "data/config.json").write_text(json.dumps(config), encoding="utf-8")
for name in ("english-first.png", "english-restart.png"):
    result = subprocess.run([str(copy), "--smoke-test", "--screenshot", str(isolated / name)], env=environment, timeout=60)
    assert result.returncode == 0
    english = json.loads((isolated / "data/native-smoke-result.json").read_text(encoding="utf-8"))
    assert english["success"] and english["language"] == "en_US"
    assert english["display_name"] == "StarMoonLauncher"
    assert json.loads((isolated / "data/config.json").read_text(encoding="utf-8"))["language"] == "en_US"
record = {"version": __version__, "size": executable.stat().st_size,
          "executable":str(executable),
          "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
          "isolated": str(isolated), "new_icon_resources": len(expected),
          "single_exe_smoke": True, "transparent_corners": True,
          "bundled_wallpapers":len(BACKGROUND_PRESETS), "preset_survives_reextraction":True,
          "english_survives_reextraction": True, "languages": ["zh_CN", "en_US"], "auto_update_modules": True,
          "bundled_curseforge_key": True, "server_management_modules": True, "ai_dependency_batches": True,
          "windows_file_version": strings["FileVersion"]}
(output/"release-verification.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
print("ISOLATED_EXE_ENGLISH_WALLPAPERS_PRESET_RESTART_WAM_AND_CORNERS_OK", flush=True)
