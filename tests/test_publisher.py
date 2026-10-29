import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from launcher import publisher
from launcher.core import LauncherError, Store, Task, atomic_json
from launcher.service import Application


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        publisher._runtime_settings.cache_clear()
        self.temp.cleanup()

    def test_private_build_input_and_environment_precedence(self):
        atomic_json(self.root / 'publisher.json', {'microsoft_client_id': 'd76a92c8-3ffa-467c-adef-85fe16cb2b55', 'curseforge_api_key': 'do-not-read-public-key'})
        atomic_json(self.root / 'publisher.local.json', {'curseforge_api_key': 'private-fixture', 'unrelated-secret': 'ignored'})
        settings = publisher.build_settings(self.root, {})
        self.assertEqual(settings, {'microsoft_client_id': 'd76a92c8-3ffa-467c-adef-85fe16cb2b55', 'curseforge_api_key': 'private-fixture'})
        self.assertEqual(publisher.build_settings(self.root, {'STARMOON_CURSEFORGE_API_KEY': 'env-fixture'})['curseforge_api_key'], 'env-fixture')
        with self.assertRaisesRegex(ValueError, 'format'):
            publisher.build_settings(self.root, {'STARMOON_CURSEFORGE_API_KEY': 'bad\r\nheader'})
        (self.root / 'publisher.local.json').unlink()
        self.assertNotIn('curseforge_api_key', publisher.build_settings(self.root, {}))

    def test_packaged_settings_use_embedded_resource_only(self):
        atomic_json(self.root / 'publisher.json', {'curseforge_api_key': 'embedded-fixture'})
        atomic_json(self.root / 'publisher.local.json', {'curseforge_api_key': 'ignored-local'})
        with patch.object(publisher.sys, 'frozen', True, create=True), patch.object(publisher.sys, '_MEIPASS', str(self.root), create=True):
            publisher._runtime_settings.cache_clear()
            self.assertEqual(publisher.curseforge_key(), 'embedded-fixture')

    def test_builtin_fallback_override_public_state_and_redaction(self):
        with patch('launcher.publisher.curseforge_key', return_value='embedded-fixture'):
            app = Application(self.root / 'data')
            with patch('launcher.content.request_json', return_value={'data': []}) as request:
                app.content.curse('/mods/search?gameId=432')
                self.assertEqual(request.call_args.kwargs['headers'], {'x-api-key': 'embedded-fixture'})
                state = app.state()['config']
                self.assertEqual(state['curseforge_key'], '')
                self.assertTrue(state['curseforge_configured'])
                self.assertTrue(state['curseforge_builtin'])
                self.assertFalse(state['curseforge_custom_configured'])
                self.assertNotIn('embedded-fixture', json.dumps(state))
                self.assertNotIn('embedded-fixture', app.diagnostics.redact('failure: embedded-fixture'))
                app.config_update({'memory': 2048})
                self.assertNotIn('embedded-fixture', app.store.path.read_text(encoding='utf-8'))
                app.config_update({'curseforge_key': 'custom-fixture'})
                app.content.curse('/mods/search?gameId=432')
                self.assertEqual(request.call_args.kwargs['headers'], {'x-api-key': 'custom-fixture'})
                self.assertTrue(app.store.public_config()['curseforge_custom_configured'])
                app.config_update({'curseforge_key': ''})
                self.assertEqual(Store(app.store.root).curseforge_key(), 'embedded-fixture')

    def test_missing_key_still_fails_and_pack_import_accepts_builtin(self):
        app = Application(self.root / 'data')
        with patch('launcher.publisher.curseforge_key', return_value=''):
            with self.assertRaisesRegex(LauncherError, 'API Key'): app.content.curse('/mods/search')
        app.store.create_minecraft()
        pack = self.root / 'pack.zip'
        with zipfile.ZipFile(pack, 'w') as archive:
            archive.writestr('manifest.json', json.dumps({'manifestType': 'minecraftModpack', 'manifestVersion': 1, 'name': 'Fixture', 'minecraft': {'version': '1.20.1'}, 'files': [{'projectID': 1, 'fileID': 2}]}))
        with patch('launcher.publisher.curseforge_key', return_value='embedded-fixture'), patch.object(app.game, 'install', side_effect=RuntimeError('fixture reached installation')):
            with self.assertRaisesRegex(RuntimeError, 'fixture reached installation'):
                app.content.import_pack(pack, Task('Import', None))


if __name__ == '__main__': unittest.main()
