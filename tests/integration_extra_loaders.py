"""Optional public-catalog check and real OptiFine patcher run in an isolated directory."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / '.build-env')]
from launcher.core import Task
from launcher.service import Application


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--patch-optifine', action='store_true')
    parser.add_argument('--inspect-cleanroom', action='store_true')
    args = parser.parse_args()
    output = ROOT / 'test-results/new-sources'
    output.mkdir(parents=True, exist_ok=True)
    result = {}
    with tempfile.TemporaryDirectory(prefix='loader-check-', dir=str(output)) as directory:
        app = Application(Path(directory) / 'data')
        app.store.create_minecraft()
        for loader, minecraft in (('optifine','1.20.1'),('cleanroom','1.12.2'),('liteloader','1.12.2')):
            choices = app.game.loader_versions(minecraft, loader)
            assert choices
            result[loader] = {'versions':len(choices),'first_version':choices[0]['version']}
            print(loader, result[loader], flush=True)
        if args.inspect_cleanroom:
            choices=app.game.loader_versions('1.12.2','cleanroom')
            instance=app.game.create('Cleanroom verification','1.12.2','cleanroom',choices[0]['version'])
            version=app.game.install_loader(instance,Task('Inspect official Cleanroom installer',None))
            profile=app.game.profile(version)
            libraries=app.game.libraries(profile)
            assert libraries and all(item.get('path') for item in libraries)
            for item in libraries:
                if not item.get('url'):
                    assert (app.game.mc/'libraries'/item['path']).is_file()
            result['cleanroom'].update(java=profile['javaVersion']['majorVersion'],library_files=len(libraries),official_installer_parsed=True)
            print('CLEANROOM_OFFICIAL_PROFILE_AND_EMBEDDED_LIBRARIES_OK',flush=True)
        if args.patch_optifine:
            app.game.ensure_base('1.20.1',Task('Base metadata',None))
            choices=app.game.loader_versions('1.20.1','optifine')
            chosen=next((item for item in choices if item['stable']),choices[0])['version']
            instance=app.game.create('OptiFine patch verification','1.20.1','optifine',chosen)
            java=shutil.which('java')
            assert java
            with patch.object(app.game,'ensure_java',return_value=java):
                version=app.game.install_loader(instance,Task('Run real OptiFine patcher',None))
            profile=app.game.profile(version)
            file=next(item for item in app.game.libraries(profile) if 'optifine/OptiFine/' in item['path'])
            with zipfile.ZipFile(app.game.mc/'libraries'/file['path']) as archive:
                assert 'optifine/OptiFineTweaker.class' in archive.namelist()
            result['optifine'].update(patcher_executed=True,patched_jar_valid=True)
            print('OPTIFINE_REAL_PATCHER_AND_LAUNCH_PROFILE_OK',flush=True)
    (output/'loader-integration.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result),flush=True)


if __name__=='__main__':
    main()
