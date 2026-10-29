"""Exercise a real packaged helper using only test-owned portable EXE copies."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / ".build-env")]
from launcher.core import atomic_json
from launcher import __version__
from launcher.update_install import prepare_install, sha256_file, wait_for_parent


def wait_until(predicate, seconds=60):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.1)
    raise AssertionError("Packaged update acceptance timed out")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    parser.add_argument("--candidate", type=Path, help="Optional current-version candidate to prove bytes change")
    args = parser.parse_args()
    executable = args.executable.resolve()
    candidate = (args.candidate or args.executable).resolve()
    output = ROOT / "test-results"
    output.mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="isolated-auto-update-", dir=str(output)))
    target = work / "StarMoonLauncher.exe"
    shutil.copy2(str(executable), str(target))
    data = work / "portable-data"
    data.mkdir()
    config = {"language": "en_US", "memory": 8192, "auto_check_updates": False,
              "offline_name": "KeepPlayer", "custom_marker": "preserve-this-value",
              "accounts": [{"id": "a" * 32, "name": "KeepPlayer", "type": "offline"}], "selected_account": "a" * 32}
    atomic_json(data / "config.json", config)
    credentials = data / "credentials.json"
    credentials.write_bytes(b'{"test-only":"opaque synthetic credential sentinel"}')
    credentials_hash = sha256_file(credentials)
    # Newly introduced server records must survive upgrades just like accounts.
    server_files = {
        'servers.json': [{'id': 'fixture-server', 'name': 'Keep server', 'address': 'localhost:25565', 'instance': '', 'notes': 'Keep notes'}],
        'local-servers.json': [{'id': 'fixture-local', 'name': 'Keep local', 'folder': str(work / 'local-server'), 'entry': 'server.jar',
                                'java_path': '', 'java_major': 21, 'memory': 2048, 'jvm_args': '', 'version': '1.21.1'}]}
    for name, content in server_files.items(): atomic_json(data / name, content)
    server_hashes = {name: sha256_file(data / name) for name in server_files}
    world = work / ".Minecraft/saves/KeepWorld/level.dat"
    world.parent.mkdir(parents=True)
    world.write_bytes(b"existing world data must survive the update")
    cached = data / "updates/packages" / __version__ / "candidate.exe"
    cached.parent.mkdir(parents=True)
    shutil.copy2(str(candidate), str(cached))
    old_hash, new_hash = sha256_file(target), sha256_file(cached)
    ready = {"path": str(cached), "sha256": new_hash, "size": cached.stat().st_size, "version": __version__}
    # Test-owned metadata exercises installation without changing public release state.
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    old = subprocess.Popen([str(target), "--data-dir", str(data), "--smoke-test", "--screenshot", str(work / "before.png")],
                           cwd=str(work), creationflags=creationflags)
    plan, helper = prepare_install(data / "updates", target, ready, data, parent_pid=old.pid)
    updated_image = work / "after.png"
    updater = subprocess.Popen([str(helper), "--apply-update", str(plan), "--smoke-test", "--screenshot", str(updated_image)],
                               cwd=str(work), creationflags=creationflags)
    assert old.wait(timeout=60) == 0
    assert updater.wait(timeout=90) == 0
    receipt = json.loads((data / "updates/last-install.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "updated"
    wait_until(lambda: updated_image.is_file())
    metadata = data / "native-smoke-result.json"
    wait_until(lambda: metadata.is_file() and metadata.stat().st_mtime >= updated_image.stat().st_mtime)
    started = json.loads(metadata.read_text(encoding="utf-8"))
    assert started["success"] and started["language"] == "en_US"
    assert started["version"] == __version__
    if receipt.get("launched_pid"):
        wait_for_parent(receipt["launched_pid"], timeout=30)
    assert sha256_file(target) == new_hash
    assert sha256_file(Path(receipt["backup"])) == old_hash
    restored = json.loads((data / "config.json").read_text(encoding="utf-8"))
    assert all(restored[key] == value for key, value in config.items())
    assert sha256_file(credentials) == credentials_hash
    assert all(sha256_file(data / name) == digest for name, digest in server_hashes.items())
    assert world.read_bytes() == b"existing world data must survive the update"
    result = {"isolated": str(work), "packaged_helper": True, "waited_for_old_launcher": True,
              "replacement_hash": new_hash, "backup_hash": old_hash, "restarted": True,
              "language_preserved": True, "settings_preserved": True, "accounts_preserved": True,
              "credentials_preserved": True, "world_preserved": True,
              "server_records_preserved": True,
              "candidate_version": __version__, "restarted_version": started["version"], "different_payload_bytes": old_hash != new_hash}
    (output / "auto-update-acceptance.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("PACKAGED_HELPER_REPLACE_RESTART_BACKUP_AND_PORTABLE_DATA_OK", flush=True)


if __name__ == "__main__":
    main()
