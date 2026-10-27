"""Loader-aware dependency inspection; all archives are local fixtures."""
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '.build-env'))
from launcher.core import LauncherError, Task
from launcher.dependencies import graph, repair
from launcher.service import Application


class DependencyLoaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = Application(Path(self.temp.name) / 'data')
        self.app.store.create_minecraft()
        self.identifier = self.app.game.create('Test', '1.20.1', 'forge', '47.4.0')['id']
        self.mods = self.app.store.game_dir(self.identifier) / 'mods'
        self.mods.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def loader(self, name, minecraft='1.21'):
        self.app.store.update_instance(self.identifier, {'loader': name, 'game_version': minecraft})

    def jar(self, name, entries):
        with zipfile.ZipFile(self.mods / name, 'w') as archive:
            for path, content in entries.items():
                archive.writestr(path, json.dumps(content) if isinstance(content, dict) else content)

    def toml(self, identifier, dependency=None):
        text = '[[mods]]\nmodId="{}"\nversion="1.0"\n'.format(identifier)
        if dependency:
            text += '[[dependencies.{}]]\nmodId="{}"\nmandatory=true\nversionRange="[1,)"\nside="BOTH"\n'.format(identifier, dependency)
        return text

    def test_multi_loader_jar_uses_only_current_loader_metadata(self):
        self.jar('universal.jar', {
            'fabric.mod.json': {'id': 'fabric_mod', 'version': '1', 'depends': {'fabric-api': '*'}},
            'quilt.mod.json': {'quilt_loader': {'id': 'quilt_mod', 'version': '1', 'depends': [{'id': 'qsl'}]}},
            'META-INF/mods.toml': self.toml('forge_mod', 'forge_library'),
            'META-INF/neoforge.mods.toml': self.toml('neo_mod', 'neo_library'),
        })
        fingerprints = set()
        for loader, source, dependency in [('forge', 'forge_mod', 'forge_library'), ('neoforge', 'neo_mod', 'neo_library'),
                                            ('fabric', 'fabric_mod', 'fabric-api'), ('quilt', 'quilt_mod', 'qsl'),
                                            ('cleanroom', 'forge_mod', 'forge_library')]:
            with self.subTest(loader=loader):
                self.loader(loader)
                data = graph(self.app, self.identifier)
                self.assertEqual([(e['source'], e['target'], e['status']) for e in data['edges']], [(source, dependency, 'missing')])
                self.assertEqual([n['id'] for n in data['nodes']], [source])
                fingerprints.add(data['fingerprint'])
        self.assertEqual(len(fingerprints), 5)
        self.loader('vanilla')
        self.assertEqual(graph(self.app, self.identifier)['edges'], [])

    def test_wrong_loader_cannot_supply_dependencies_or_disabled_repairs(self):
        self.jar('forge.jar', {'META-INF/mods.toml': self.toml('a', 'needed')})
        self.jar('wrong.jar', {'fabric.mod.json': {'id': 'needed', 'version': '2', 'depends': {'fabric-api': '*'}}})
        self.jar('wrong-disabled.jar.disabled', {'fabric.mod.json': {'id': 'needed', 'version': '2'}})
        data = graph(self.app, self.identifier)
        self.assertEqual([(e['target'], e['status']) for e in data['edges']], [('needed', 'missing')])
        self.assertIsNone(data['edges'][0]['disabled_file'])
        self.assertNotIn('fabric-api', json.dumps(data))
        self.assertTrue(any('wrong.jar' in issue and 'forge' in issue for issue in data['issues']))
        self.assertFalse(any('wrong-disabled' in issue for issue in data['issues']))
        snapshot, _ = self.app.diagnostics.snapshot(self.identifier)
        self.assertNotIn('needed', snapshot['provided'])

    def test_quilt_still_checks_fabric_dependencies_and_nested_aliases(self):
        child = io.BytesIO()
        with zipfile.ZipFile(child, 'w') as archive:
            archive.writestr('fabric.mod.json', json.dumps({'id': 'nested', 'version': '2', 'provides': ['alias']}))
        self.jar('fabric.jar', {
            'fabric.mod.json': {'id': 'a', 'version': '1', 'depends': {'fabric-api': '*', 'alias': '>=1'}, 'jars': [{'file': 'nested.jar'}]},
            'nested.jar': child.getvalue(),
        })
        for loader in ('fabric', 'quilt'):
            with self.subTest(loader=loader):
                self.loader(loader)
                self.assertEqual({e['target']: e['status'] for e in graph(self.app, self.identifier)['edges']}, {'fabric-api': 'missing', 'alias': 'ok'})

    def test_forge_nested_jars_follow_forge_metadata(self):
        child = io.BytesIO()
        with zipfile.ZipFile(child, 'w') as archive:
            archive.writestr('fabric.mod.json', json.dumps({'id': 'fabric_nested', 'version': '1', 'depends': {'fabric-api': '*'}}))
            archive.writestr('META-INF/mods.toml', self.toml('needed'))
        self.jar('a.jar', {
            'fabric.mod.json': {'id': 'fabric_parent', 'version': '1', 'depends': {'fabric-api': '*'}, 'jars': [{'file': 'invalid.jar'}]},
            'META-INF/mods.toml': self.toml('a', 'needed'),
            'META-INF/jarjar/metadata.json': {'jars': [{'path': 'nested.jar'}]},
            'nested.jar': child.getvalue(),
            'invalid.jar': b'not even an archive',
        })
        data = graph(self.app, self.identifier)
        self.assertEqual([(e['target'], e['status']) for e in data['edges']], [('needed', 'ok')])
        self.assertEqual({n['id'] for n in data['nodes']}, {'a', 'needed'})
        self.assertEqual(data['issues'], [])

    def test_neoforge_legacy_descriptor_is_version_scoped(self):
        self.jar('legacy.jar', {'META-INF/mods.toml': self.toml('a', 'library')})
        for version in ('1.20.1', '1.20.2', '1.20.4'):
            with self.subTest(version=version):
                self.loader('neoforge', version)
                self.assertEqual([e['target'] for e in graph(self.app, self.identifier)['edges']], ['library'])
        self.loader('neoforge', '1.21')
        self.assertEqual(graph(self.app, self.identifier)['edges'], [])

    def test_forge_dependencies_are_not_filtered_by_name(self):
        # A real Forge port/bridge may legitimately declare a Fabric-named ID.
        self.jar('bridge.jar', {'META-INF/mods.toml': self.toml('bridge', 'fabric-api')})
        self.assertEqual([e['target'] for e in graph(self.app, self.identifier)['edges']], ['fabric-api'])

    def test_unused_fabric_metadata_is_not_parsed_on_forge(self):
        self.jar('a.jar', {'fabric.mod.json': '{broken JSON', 'META-INF/mods.toml': self.toml('a')})
        data = graph(self.app, self.identifier)
        self.assertEqual([n['id'] for n in data['nodes']], ['a'])
        self.assertEqual(data['issues'], [])

    def test_wrong_loader_repair_candidate_is_rolled_back(self):
        self.jar('a.jar', {'META-INF/mods.toml': self.toml('a', 'needed')})
        def install(*args):
            self.jar('candidate.jar', {'fabric.mod.json': {'id': 'needed', 'version': '2'}})
        with patch.object(self.app.content, 'install_project', side_effect=install):
            with self.assertRaisesRegex(LauncherError, 'ID'):
                repair(self.app, self.identifier, 'needed', 'project', 'version', 'modrinth', '', Task('Test', lambda t: None))
        self.assertFalse((self.mods / 'candidate.jar').exists())
        self.assertTrue((self.mods / 'a.jar').exists())


if __name__ == '__main__':
    unittest.main()
