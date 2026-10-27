"""Opt-in live catalog/hash matching and optional isolated Java installation."""
import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / '.build-env')]
from launcher.core import request_json, Task
from launcher.service import Application
from launcher.java_manager import download


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--download-java', action='store_true')
    args = parser.parse_args()
    java = request_json('https://api.adoptium.net/v3/assets/latest/21/hotspot?architecture=x64&image_type=jre&os=windows&vendor=eclipse')
    assert java and len(java[0]['binary']['package']['checksum']) == 64
    versions = request_json('https://api.modrinth.com/v2/project/fabric-api/version?game_versions=%5B%221.20.1%22%5D&loaders=%5B%22fabric%22%5D')
    assert versions
    chosen = versions[0]
    hashed = chosen['files'][0]['hashes']['sha512']
    matched = request_json('https://api.modrinth.com/v2/version_files', {'hashes': [hashed], 'algorithm': 'sha512'})
    assert matched[hashed]['id'] == chosen['id']
    packs = request_json('https://api.modrinth.com/v2/project/fabulously-optimized/version')
    assert len(packs) >= 2 and all(v['project_id'] == packs[0]['project_id'] for v in packs)
    result = {'temurin': java[0]['version']['semver'], 'hash_match': matched[hashed]['id'], 'pack_versions': len(packs)}
    if args.download_java:
        parent = ROOT / 'test-results/management-live'
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=str(parent)) as temporary:
            app = Application(Path(temporary) / 'data')
            installed = download(app, 21, 'x64', Task('Download Java', lambda t: None))
            inspected = app.game.inspect_java(installed['path'])
            assert inspected and inspected['major'] == 21
            result['installed_java_version'] = inspected['version']
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__': main()
