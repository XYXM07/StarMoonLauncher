import json
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '.build-env'))
from launcher.core import Cancelled, LauncherError, Store, Task, atomic_json, read_json
from launcher.service import Application
from launcher.management import bulk, history, restore_change, digest
from launcher.dependencies import graph, impact, repair
from launcher.pack_updates import upgrade, plan_merge, remember, stage
from launcher import java_manager, problem_reports


class ManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.app = Application(self.root / 'data')
        self.app.store.create_minecraft()
        self.instance = self.app.game.create('Test', '1.20.1', 'fabric', '0.16.0')
        self.id = self.instance['id']
        self.game = self.app.store.game_dir(self.id)
        self.meta = self.app.store.instance_dir(self.id)
        (self.game / 'mods').mkdir()

    def tearDown(self): self.temp.cleanup()
    def task(self): return Task('Test', lambda t: None)

    def mod(self, filename, identifier, version='1.0.0', depends=None, provides=None):
        path = self.game / 'mods' / filename
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('fabric.mod.json', json.dumps({'id': identifier, 'version': version, 'depends': depends or {}, 'provides': provides or []}))
        return path

    def record(self, filename, project='a', version='v1'):
        record = {'filename': filename, 'provider': 'modrinth', 'project_id': project, 'version_id': version, 'kind': 'mod'}
        atomic_json(self.meta / 'content.json', [record])
        return record

    def test_bulk_enable_disable_delete_and_restore(self):
        original = self.mod('a.jar', 'a').read_bytes()
        self.mod('b.jar.disabled', 'b')
        bulk(self.app, self.id, 'disable', ['a.jar', 'b.jar.disabled'], self.task())
        self.assertFalse((self.game / 'mods/a.jar').exists())
        self.assertEqual(len(list((self.game / 'mods').glob('*.disabled'))), 2)
        change = history(self.app.store, self.id)[0]
        restore_change(self.app, self.id, change['id'], self.task())
        self.assertEqual((self.game / 'mods/a.jar').read_bytes(), original)
        bulk(self.app, self.id, 'delete', ['a.jar'], self.task())
        self.assertFalse((self.game / 'mods/a.jar').exists())
        restore_change(self.app, self.id, history(self.app.store, self.id)[0]['id'], self.task())
        self.assertTrue((self.game / 'mods/a.jar').is_file())

    def test_restore_refuses_later_user_changes(self):
        self.mod('a.jar', 'a')
        bulk(self.app, self.id, 'disable', ['a.jar'], self.task())
        (self.game / 'mods/a.jar.disabled').write_bytes(b'changed')
        with self.assertRaises(LauncherError): restore_change(self.app, self.id, history(self.app.store, self.id)[0]['id'], self.task())
        self.assertEqual((self.game / 'mods/a.jar.disabled').read_bytes(), b'changed')

    def test_bulk_update_failure_restores_files_and_registry(self):
        original = self.mod('a.jar', 'a').read_bytes()
        record = self.record('a.jar')
        update = dict(record, new_version='v2', new_name='2.0')
        def fail(*args):
            (self.game / 'mods/a.jar').unlink()
            (self.game / 'mods/new.jar').write_bytes(b'partial')
            atomic_json(self.meta / 'content.json', [])
            raise LauncherError('download failed')
        with patch.object(self.app.content, 'check_updates', return_value=[update]), patch.object(self.app.content, 'install_project', side_effect=fail):
            with self.assertRaises(LauncherError): bulk(self.app, self.id, 'update', ['a.jar'], self.task())
        self.assertEqual((self.game / 'mods/a.jar').read_bytes(), original)
        self.assertFalse((self.game / 'mods/new.jar').exists())
        self.assertEqual(read_json(self.meta / 'content.json'), [record])

    def test_lock_ignore_disabled_updates_and_stale_preview(self):
        self.mod('a.jar.disabled', 'a')
        self.record('a.jar')
        with patch.object(self.app.content, 'versions', return_value=[{'id': 'v2', 'name': 'new'}]):
            self.assertEqual(self.app.content.check_updates(self.id)[0]['filename'], 'a.jar.disabled')
            bulk(self.app, self.id, 'lock', ['a.jar.disabled'], self.task())
            self.assertEqual(self.app.content.check_updates(self.id), [])
            with self.assertRaises(LauncherError): self.app.content._check_locked(self.id, 'modrinth', 'a', 'v2')
            bulk(self.app, self.id, 'unlock', ['a.jar.disabled'], self.task())
            with self.assertRaises(LauncherError): bulk(self.app, self.id, 'update', ['a.jar.disabled'], self.task(), {'a.jar.disabled': 'stale'})
            bulk(self.app, self.id, 'ignore', ['a.jar.disabled'], self.task())
            self.assertEqual(self.app.content.check_updates(self.id), [])

    def test_successful_update_keeps_disabled_state(self):
        self.mod('a.jar.disabled', 'a')
        record = self.record('a.jar')
        def install(*args):
            (self.game / 'mods/a.jar.disabled').unlink()
            self.mod('a-new.jar', 'a', '2.0.0')
            self.record('a-new.jar', version='v2')
        with patch.object(self.app.content, 'check_updates', return_value=[dict(record, filename='a.jar.disabled', new_version='v2')]), patch.object(self.app.content, 'install_project', side_effect=install):
            bulk(self.app, self.id, 'update', ['a.jar.disabled'], self.task())
        self.assertTrue((self.game / 'mods/a-new.jar.disabled').is_file())
        self.assertFalse((self.game / 'mods/a-new.jar').exists())

    def test_dependency_graph_disabled_alias_mismatch_and_impact(self):
        self.mod('a.jar', 'a', depends={'alias': '>=2.0', 'absent': '*', 'minecraft': '1.19'})
        self.mod('b.jar.disabled', 'b', '2.0.0', provides=['alias'])
        states = {e['target']: e['status'] for e in graph(self.app, self.id)['edges']}
        self.assertEqual(states, {'alias': 'disabled', 'absent': 'missing', 'minecraft': 'mismatch'})
        repair(self.app, self.id, 'alias', None, None, None, 'b.jar.disabled', self.task())
        self.assertTrue((self.game / 'mods/b.jar').is_file())
        self.assertEqual(impact(self.app, self.id, ['b.jar'])[0]['source'], 'a')

    def test_dependency_wrong_candidate_rolls_back(self):
        self.mod('a.jar', 'a', depends={'needed': '>=2'})
        with patch.object(self.app.content, 'install_project', side_effect=lambda *args: self.mod('wrong.jar', 'wrong')):
            with self.assertRaisesRegex(LauncherError, 'ID'): repair(self.app, self.id, 'needed', 'project', 'v1', 'modrinth', '', self.task())
        self.assertFalse((self.game / 'mods/wrong.jar').exists())

    def test_dependency_replaces_unregistered_old_version_and_checks_new_dependencies(self):
        self.mod('a.jar', 'a', depends={'needed': '>=2'})
        self.mod('old.jar', 'needed', '1.0')
        with patch.object(self.app.content, 'install_project', side_effect=lambda *args: self.mod('new.jar', 'needed', '2.0')):
            repair(self.app, self.id, 'needed', 'project', 'v2', 'modrinth', '', self.task())
        self.assertFalse((self.game / 'mods/old.jar').exists())
        self.assertTrue((self.game / 'mods/new.jar').exists())
        restore_change(self.app, self.id, history(self.app.store, self.id)[0]['id'], self.task())
        with patch.object(self.app.content, 'install_project', side_effect=lambda *args: self.mod('new.jar', 'needed', '2.0', depends={'another': '*'})):
            with self.assertRaisesRegex(LauncherError, '新的依赖'):
                repair(self.app, self.id, 'needed', 'project', 'v2', 'modrinth', '', self.task())
        self.assertTrue((self.game / 'mods/old.jar').exists())
        self.assertFalse((self.game / 'mods/new.jar').exists())

    def test_metadata_actions_do_not_copy_mod_files(self):
        self.mod('a.jar', 'a')
        with patch('launcher.management.inventory', side_effect=AssertionError('must not scan/copy full mod tree')):
            bulk(self.app, self.id, 'lock', ['a.jar'], self.task())
        directory = self.meta / 'changes' / history(self.app.store, self.id)[0]['id']
        self.assertFalse((directory / 'before/game').exists())

    def test_download_target_is_journaled_before_mod_bytes_are_written(self):
        from launcher.management import mod_transaction
        version = {'id': 'v1', 'project_id': 'mod', 'name': 'Mod', 'game_versions': ['1.20.1'], 'loaders': ['fabric'],
                   'files': [{'filename': 'new.jar', 'url': 'https://example.invalid/mod', 'hashes': {'sha512': 'hash'}}]}
        def download(url, target, **kwargs):
            journal = self.app.content._writes.journal
            self.assertIsNone(read_json(journal.directory / 'change.json')['files']['game/mods/new.jar']['before'])
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            Path(target).write_bytes(b'downloaded')
        with patch.object(self.app.net, 'download', side_effect=download):
            with mod_transaction(self.app, self.id, self.task(), 'download'):
                self.app.content._mr_install(version, self.id, 'mod', self.task(), set())
        restore_change(self.app, self.id, history(self.app.store, self.id)[0]['id'], self.task())
        self.assertFalse((self.game / 'mods/new.jar').exists())

    def test_interrupted_mod_operation_can_restore_created_files(self):
        from launcher.management import ChangeSet
        original = self.mod('old.jar', 'old').read_bytes()
        change = ChangeSet(self.app.store, self.id, 'Interrupted', self.task())
        change.capture(['game/mods/old.jar', 'meta/content.json'])
        change.data['scopes'] = ['mods']
        change.begin()
        (self.game / 'mods/old.jar').unlink()
        change.capture(['game/mods/new.jar'])
        self.mod('new.jar', 'new')
        self.mod('user-added-later.jar', 'later')
        restore_change(self.app, self.id, change.directory.name, self.task())
        self.assertEqual((self.game / 'mods/old.jar').read_bytes(), original)
        self.assertFalse((self.game / 'mods/new.jar').exists())
        self.assertTrue((self.game / 'mods/user-added-later.jar').exists())

    def test_file_hash_identification_uses_published_hash_and_preserves_disabled(self):
        import hashlib
        from launcher.management import identify_mods
        path = self.mod('renamed.jar.disabled', 'a')
        bulk(self.app, self.id, 'lock', ['renamed.jar.disabled'], self.task())
        hashed = hashlib.sha512(path.read_bytes()).hexdigest()
        version = {'id': 'v1', 'project_id': 'a', 'name': 'A', 'game_versions': ['1.20.1'], 'loaders': ['fabric'], 'files': [{'hashes': {'sha512': hashed}}]}
        with patch('launcher.core.request_json', return_value={hashed: version}) as request:
            result = identify_mods(self.app, self.id, self.task())
            self.assertEqual(request.call_args[0][1], {'algorithm': 'sha512', 'hashes': [hashed]})
        self.assertEqual(result['identified'], 1)
        self.assertEqual(read_json(self.meta / 'content.json')[0]['filename'], 'renamed.jar')
        self.assertTrue(read_json(self.meta / 'content-policy.json')['modrinth:a']['locked'])
        self.assertTrue(path.is_file())

    def test_mutation_guard_blocks_other_workers_and_launch(self):
        errors = []
        def worker():
            try:
                with self.app.management.guard(self.id, launching=True): pass
            except LauncherError as error: errors.append(str(error))
        with self.app.management.guard(self.id):
            thread = threading.Thread(target=worker)
            thread.start()
            thread.join()
        self.assertEqual(len(errors), 1)

    def fake_stage(self, content, parent, provider, project, version, task):
        store = Store(parent)
        directory = store.instance_dir('incoming')
        (directory / 'game/config').mkdir(parents=True)
        (directory / 'game/config/a.txt').write_text('upstream-' + version)
        (directory / 'game/new.txt').write_text('new')
        atomic_json(directory / 'instance.json', dict(self.instance, id='incoming', loader_version='0.17', version='new-profile'))
        return store, 'incoming'

    def test_pack_upgrade_preserves_local_files_world_and_identity(self):
        (self.game / 'config').mkdir()
        (self.game / 'config/a.txt').write_text('upstream-v1')
        (self.game / 'obsolete.txt').write_text('old')
        remember(self.app.content, self.id, self.task(), {'pack_provider': 'modrinth', 'pack_project': 'pack', 'pack_version': 'v1'})
        (self.game / 'config/a.txt').write_text('my config')
        (self.game / 'custom.jar').write_bytes(b'custom')
        (self.game / 'saves').mkdir()
        (self.game / 'saves/world').write_text('world')
        (self.game / 'options.txt').write_text('options')
        task = self.task()
        task.request_choice = Mock(return_value='keep')
        with patch('launcher.pack_updates.stage', side_effect=self.fake_stage):
            upgrade(self.app, self.id, 'modrinth', 'pack', 'v2', '', task)
        self.assertEqual((self.game / 'config/a.txt').read_text(), 'my config')
        self.assertFalse((self.game / 'obsolete.txt').exists())
        self.assertEqual((self.game / 'saves/world').read_text(), 'world')
        self.assertEqual((self.game / 'options.txt').read_text(), 'options')
        self.assertEqual((self.game / 'custom.jar').read_bytes(), b'custom')
        self.assertEqual(self.app.store.instance(self.id)['id'], self.id)
        self.assertEqual(self.app.store.instance(self.id)['pack_version'], 'v2')
        self.app.store.update_instance(self.id, {'last_played': 12345, 'name': 'User rename'})
        restore_change(self.app, self.id, history(self.app.store, self.id)[0]['id'], self.task())
        self.assertEqual(self.app.store.instance(self.id)['pack_version'], 'v1')
        self.assertEqual(self.app.store.instance(self.id)['last_played'], 12345)
        self.assertEqual(self.app.store.instance(self.id)['name'], 'User rename')
        self.assertFalse((self.game / 'new.txt').exists())

    def test_pack_renamed_mod_stays_disabled_and_custom_mod_conflict_is_preserved(self):
        self.mod('old.jar', 'a')
        remember(self.app.content, self.id, self.task())
        (self.game / 'mods/old.jar').rename(self.game / 'mods/old.jar.disabled')
        def staged(content, parent, provider, project, version, task):
            store, identifier = self.fake_stage(content, parent, provider, project, version, task)
            mods = store.game_dir(identifier) / 'mods'
            mods.mkdir()
            with zipfile.ZipFile(mods / 'new.jar', 'w') as archive: archive.writestr('fabric.mod.json', json.dumps({'id': 'a', 'version': '2'}))
            return store, identifier
        task = self.task()
        task.request_choice = Mock(return_value='keep')
        with patch('launcher.pack_updates.stage', side_effect=staged): upgrade(self.app, self.id, 'modrinth', 'pack', 'v2', 'v1', task)
        self.assertFalse((self.game / 'mods/old.jar.disabled').exists())
        self.assertTrue((self.game / 'mods/new.jar.disabled').exists())
        self.assertFalse((self.game / 'mods/new.jar').exists())

    def test_stage_uses_real_import_pipeline_without_changing_original_selection(self):
        from launcher.game import GameManager
        selected = self.app.store.config()['selected_instance']
        archive = self.root / 'fixture.mrpack'
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('modrinth.index.json', json.dumps({'formatVersion': 1, 'game': 'minecraft', 'name': 'Pack', 'dependencies': {'minecraft': '1.20.1'}, 'files': []}))
            z.writestr('overrides/config/test.txt', 'setting')
        version = {'project_id': 'pack', 'files': [{'filename': 'pack.mrpack', 'url': 'https://example.invalid/pack'}]}
        def download(url, target, **kwargs):
            import shutil
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(str(archive), str(target))
        with patch.object(self.app.net, 'json', side_effect=[version, {'project_type': 'modpack'}]), patch.object(self.app.net, 'download', side_effect=download), patch.object(GameManager, 'install', return_value={}):
            store, identifier = stage(self.app.content, self.root / 'stage', 'modrinth', 'pack', 'v2', self.task())
        self.assertEqual((store.game_dir(identifier) / 'config/test.txt').read_text(), 'setting')
        self.assertEqual(store.instance(identifier)['pack_version'], 'v2')
        self.assertEqual(self.app.store.config()['selected_instance'], selected)
        self.assertEqual(len(self.app.store.instances()), 1)

    def test_pack_cancel_and_stale_preview_do_not_modify(self):
        (self.game / 'config').mkdir()
        (self.game / 'config/a.txt').write_text('original')
        remember(self.app.content, self.id, self.task())
        task = self.task()
        task.request_choice = Mock(return_value='cancel')
        with patch('launcher.pack_updates.stage', side_effect=self.fake_stage):
            with self.assertRaises(Cancelled): upgrade(self.app, self.id, 'modrinth', 'pack', 'v2', 'v1', task)
            def edit(*args):
                (self.game / 'config/a.txt').write_text('changed meanwhile')
                return 'replace'
            task.request_choice = edit
            with self.assertRaisesRegex(LauncherError, '预览后'): upgrade(self.app, self.id, 'modrinth', 'pack', 'v2', 'v1', task)
        self.assertEqual((self.game / 'config/a.txt').read_text(), 'changed meanwhile')

    def test_pack_failure_during_apply_restores_all(self):
        (self.game / 'config').mkdir()
        (self.game / 'config/a.txt').write_text('old')
        remember(self.app.content, self.id, self.task())
        task = self.task()
        task.request_choice = Mock(return_value='replace')
        original = task.update
        def cancel(message, progress=None):
            if message.endswith('new.txt'):
                task.cancel.set()
            original(message, progress)
        task.update = cancel
        with patch('launcher.pack_updates.stage', side_effect=self.fake_stage):
            with self.assertRaises(Cancelled): upgrade(self.app, self.id, 'modrinth', 'pack', 'v2', 'v1', task)
        self.assertEqual((self.game / 'config/a.txt').read_text(), 'old')
        self.assertFalse((self.game / 'new.txt').exists())

    def test_java_cleanup_refuses_external_or_referenced(self):
        java = self.app.store.root / 'runtime/21/jre/bin/java.exe'
        java.parent.mkdir(parents=True)
        java.write_bytes(b'fake')
        item = {'path': str(java), 'version': '21', 'major': 21}
        with patch.object(self.app.game, 'java_candidates', return_value=[item]):
            self.app.store.update_instance(self.id, {'java_path': str(java)})
            with self.assertRaises(LauncherError): java_manager.remove(self.app, str(java), self.task())
            self.app.store.update_instance(self.id, {'java_path': ''})
            self.assertTrue(java_manager.listing(self.app)[0]['managed'])
            result = java_manager.remove(self.app, str(java), self.task())
            self.assertFalse(java.exists())
            self.assertTrue(Path(result['path']).is_dir())

    def test_java_download_pins_checksum_and_checks_version(self):
        def downloaded(url, target, **kwargs):
            self.assertEqual(kwargs['sha256'], 'published-sha256')
            with zipfile.ZipFile(target, 'w') as archive: archive.writestr('jre/bin/java.exe', b'fake')
        package = {'link': 'https://example.com/java.zip', 'checksum': 'published-sha256', 'size': 100}
        with patch.object(self.app.net, 'json', return_value=[{'binary': {'package': package}}]), patch.object(self.app.net, 'download', side_effect=downloaded), patch.object(self.app.game, 'inspect_java', return_value={'major': 21}):
            result = java_manager.download(self.app, 21, 'x64', self.task())
        self.assertTrue(Path(result['path']).is_file())

    def test_reports_filter_redact_and_never_export_world_or_config(self):
        (self.game / 'logs').mkdir()
        (self.game / 'logs/latest.log').write_text('INFO hello\nERROR failed\n  at trace\nINFO password=secret123\n')
        (self.game / 'saves').mkdir()
        (self.game / 'saves/private').write_text('private-world')
        entry = problem_reports.logs(self.app, self.id)[0]
        text = problem_reports.read_log(self.app, self.id, entry['id'], level='error')['text']
        self.assertIn('at trace', text)
        self.assertNotIn('hello', text)
        destination = self.root / 'report.zip'
        problem_reports.export(self.app, self.id, destination, self.task())
        with zipfile.ZipFile(destination) as archive:
            data = '\n'.join(archive.read(name).decode() for name in archive.namelist())
            self.assertNotIn('secret123', data)
            self.assertNotIn('private-world', data)
            self.assertNotIn('config.json', archive.namelist())
        with self.assertRaises(LauncherError): problem_reports.export(self.app, self.id, destination, self.task())
        with self.assertRaises(LauncherError): problem_reports.read_log(self.app, self.id, 'meta/../../config.json')


if __name__ == '__main__': unittest.main()
