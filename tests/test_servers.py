import io
import json
import queue
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '.build-env'))
from launcher.core import LauncherError, Task
from launcher.service import Application
from launcher.local_servers import properties
from launcher.server_list import address, join_arguments, read_varint, srv_target, status, varint


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = Application(self.root/'data')

    def tearDown(self): self.temp.cleanup()

    def test_addresses_validate_and_preserve_srv_vs_explicit_ports(self):
        self.assertEqual(address('PLAY.Example.COM')['address'], 'play.example.com')
        self.assertFalse(address('play.example.com')['explicit_port'])
        self.assertTrue(address('play.example.com:25565')['explicit_port'])
        self.assertEqual(address('[::1]:25566')['host'], '::1')
        self.assertEqual(address('::1')['address'], '[::1]')
        self.assertIn('xn--', address('我的世界.example')['address'])
        for invalid in ('', 'https://example.com', 'example.com:0', 'example.com:65536', 'example.com:-1', 'x --foo', '-bad', 'host/user', 'host\nstop', '[bad]:123'):
            with self.subTest(invalid=invalid), self.assertRaises(LauncherError): address(invalid)

    def test_saved_servers_persist_edit_and_remove_without_changing_instances(self):
        value = self.app.servers.save({'name': 'Test', 'address': 'example.com', 'notes': 'Hello'})
        value = self.app.servers.save(dict(value, address='example.com:25566'))
        self.assertEqual(Application(self.app.store.root).servers.list(), [value])
        with self.assertRaises(LauncherError): self.app.servers.save({'address': 'example.com:25566'})
        self.app.servers.remove(value['id'])
        self.assertEqual(self.app.servers.list(), [])
        self.assertFalse(self.app.store.minecraft_root.exists())

    def test_srv_resolution_respects_explicit_port(self):
        records = [SimpleNamespace(priority=10, weight=1, target='other.example.', port=25567), SimpleNamespace(priority=0, weight=1, target='srv.example.', port=25566)]
        with patch('dns.resolver.resolve', return_value=records) as lookup:
            self.assertEqual(srv_target(address('play.example'), 1), ('srv.example', 25566))
            lookup.assert_called_once_with('_minecraft._tcp.play.example', 'SRV', lifetime=1)
            lookup.reset_mock()
            self.assertEqual(srv_target(address('play.example:25565'), 1), ('play.example', 25565))
            self.assertEqual(srv_target(address('127.0.0.1'), 1), ('127.0.0.1', 25565))
            lookup.assert_not_called()

    def test_loopback_status_protocol_and_bounded_invalid_response(self):
        seen, invalid = [], [False]
        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                with self.request.makefile('rb') as stream:
                    size = read_varint(stream.read); seen.append(stream.read(size))
                    size = read_varint(stream.read); seen.append(stream.read(size))
                    if invalid[0]: self.request.sendall(varint(2*1024*1024)); return
                    body = json.dumps({'version': {'name': 'Fixture 1.20.1'}, 'players': {'online': 2, 'max': 20},
                                       'description': {'text': '§aHello', 'extra': [{'text': ' world'}]}}).encode()
                    response = b'\0' + varint(len(body)) + body
                    for byte in varint(len(response)) + response: self.request.sendall(bytes([byte]))
        server = socketserver.TCPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        try:
            result = status('127.0.0.1:' + str(server.server_address[1]))
            self.assertTrue(result['online']); self.assertEqual(result['motd'], 'Hello world')
            self.assertEqual(result['players'], 2); self.assertEqual(seen[1], b'\0')
            self.assertIn(b'127.0.0.1', seen[0]); self.assertGreaterEqual(result['latency'], 0)
            invalid[0] = True
            self.assertFalse(status('127.0.0.1:' + str(server.server_address[1]))['online'])
        finally: server.shutdown(); server.server_close(); worker.join()

    def test_version_aware_join_and_no_persistent_launch_changes(self):
        self.app.store.create_minecraft()
        instance = self.app.game.create('Test', '1.20.1')
        account = self.app.accounts.for_launch(self.app.accounts.offline('Tester')['id'])
        profile = {'id': '1.20.1', 'mainClass': 'Main', 'libraries': [], 'arguments': {'game': ['--username', '${auth_player_name}']}}
        self.app.config_update({'game_args': '--quickPlaySingleplayer OldWorld --server old.example --port 1234'})
        with patch.object(self.app.game, 'profile', return_value=profile), patch.object(self.app.game, 'libraries', return_value=[]), patch('launcher.game.java_bits', return_value=64):
            command = self.app.game.command(dict(instance, _join_server='new.example:25566'), account, 'java.exe')
            self.assertEqual(command[-2:], ['--quickPlayMultiplayer', 'new.example:25566'])
            self.assertNotIn('OldWorld', command)
            self.assertNotIn('--server', command)
            regular = self.app.game.command(instance, account, 'java.exe')
            self.assertIn('OldWorld', regular)
        self.assertNotIn('_join_server', self.app.store.instance(instance['id']))
        self.assertEqual(join_arguments({'id': '1.19.4'}, '[::1]:25566'), ['--server', '::1', '--port', '25566'])
        self.assertEqual(join_arguments({'id': '23w14a', 'arguments': {'game': ['--quickPlayMultiplayer']}}, 'example.com'), ['--quickPlayMultiplayer', 'example.com'])

    def test_preflight_resume_keeps_requested_server(self):
        self.app.store.create_minecraft()
        instance = self.app.game.create('Test', '1.20.1')
        self.app.config_update({'ai_auto_preflight': True})
        with patch.object(self.app.diagnostics, 'analyze', return_value={'status': 'risk'}), patch.object(self.app.game, 'launch', return_value={}) as launch:
            blocked = self.app.start_game(instance['id'], 'account', Task('Join', None), server='example.com')
            self.assertEqual(blocked['resume_server'], 'example.com'); launch.assert_not_called()
            self.app.start_game(instance['id'], 'account', Task('Join', None), True, blocked['resume_server'])
            self.assertEqual(launch.call_args.kwargs, {'server': 'example.com'})


class FakeServer:
    def __init__(self, command, **kwargs):
        self.args, self.kwargs, self.pid, self.code = command, kwargs, 8123, None
        self.gate, self.output = threading.Event(), queue.Queue()
        self.output.put(b'Done (0.1s)!\n')
        self.stdout, self.stdin, self.writes = self, self, []
    def readline(self, size=8192): return self.output.get(timeout=5)
    def write(self, value):
        self.writes.append(value)
        if value == b'stop\n': self.finish(0)
        return len(value)
    def flush(self): pass
    def close(self): pass
    def poll(self): return self.code
    def wait(self): self.gate.wait(5); return self.code
    def terminate(self): self.finish(-15)
    def finish(self, code):
        if self.code is None: self.code = code; self.output.put(b''); self.gate.set()


class LocalServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.app = Application(self.root/'data')
        self.folder = self.root/'existing server'; self.folder.mkdir()
        (self.folder/'server.jar').write_bytes(b'fixture')
        self.item = self.app.local_servers.register({'name': 'Local Test', 'folder': str(self.folder), 'entry': 'server.jar'})
        self.children = []
    def tearDown(self):
        for child in self.children: child.finish(0)
        time.sleep(.03)
        self.temp.cleanup()
    def spawn(self, *args, **kwargs):
        child = FakeServer(*args, **kwargs); self.children.append(child); return child
    def wait(self, predicate):
        end = time.monotonic()+3
        while time.monotonic()<end:
            if predicate(): return
            time.sleep(.01)
        self.fail('Timed out')

    def test_import_and_configuration_preserve_unrelated_files_and_eula(self):
        self.assertFalse(self.app.local_servers.config(self.item['id'])['eula'])
        path = self.folder/'server.properties'
        path.write_text('# Keep me\nrcon.password=fixture-private\nserver-port=25565\n', encoding='utf-8')
        self.app.local_servers.save(self.item['id'], {'memory': 4096, 'properties': {'motd': '星月测试', 'server-port': '25566'}})
        saved = path.read_text(encoding='utf-8')
        self.assertIn('# Keep me', saved); self.assertIn('rcon.password=fixture-private', saved)
        self.assertEqual(properties(path)['motd'], '星月测试')
        self.assertNotIn('rcon.password', self.app.local_servers.config(self.item['id'])['properties'])
        self.assertFalse((self.folder/'eula.txt').exists())
        self.app.local_servers.remove(self.item['id'])
        self.assertTrue((self.folder/'server.jar').is_file()); self.assertTrue(path.is_file())

    def test_eula_lifecycle_console_normal_stop_restart_and_running_guards(self):
        with patch.object(self.app.game, 'ensure_java', return_value='java.exe'), patch('launcher.local_servers.subprocess.Popen', side_effect=self.spawn) as spawn:
            with self.assertRaisesRegex(LauncherError, 'EULA'): self.app.local_servers.start(self.item['id'], Task('Start', None))
            spawn.assert_not_called()
            self.app.local_servers.save(self.item['id'], {'accept_eula': True})
            result = self.app.local_servers.start(self.item['id'], Task('Start', None))
            self.assertTrue(result['running']); self.assertEqual(len(self.app.state()['server_processes']), 1)
            self.assertEqual(self.children[0].kwargs['cwd'], str(self.folder))
            self.assertEqual(self.children[0].args[-3:], ['-jar', str(self.folder/'server.jar'), 'nogui'])
            self.assertNotIn('shell', self.children[0].kwargs)
            for action in (lambda: self.app.local_servers.start(self.item['id'], Task('Start', None)), lambda: self.app.local_servers.remove(self.item['id']), lambda: self.app.local_servers.save(self.item['id'], {})):
                with self.assertRaises(LauncherError): action()
            self.wait(lambda: self.app.local_servers.console(self.item['id'])['cursor'] > 0)
            console = self.app.local_servers.console(self.item['id'])
            self.assertIn('Done', console['lines'][0])
            self.assertEqual(self.app.local_servers.console(self.item['id'], console['cursor'], console['run_id'])['lines'], [])
            with self.assertRaises(LauncherError): self.app.local_servers.command(self.item['id'], 'list\nstop')
            self.app.local_servers.command(self.item['id'], 'list')
            self.app.local_servers.stop(self.item['id'])
            self.assertEqual(self.children[0].writes, [b'list\n', b'stop\n'])
            self.assertEqual(self.app.state()['server_processes'], [])
            self.app.local_servers.start(self.item['id'], Task('Restart', None))
            self.wait(lambda: self.app.local_servers.console(self.item['id'])['cursor'] > 0)
            self.assertIn('Done', self.app.local_servers.console(self.item['id'], 500, console['run_id'])['lines'][0])
            self.app.local_servers.stop(self.item['id'], True)
            self.assertEqual(self.children[-1].code, -15)

    def test_forge_args_file_and_invalid_paths(self):
        (self.folder/'libraries').mkdir()
        (self.folder/'libraries/win_args.txt').write_text('-cp libraries/test.jar Main')
        self.app.local_servers.remove(self.item['id'])
        item = self.app.local_servers.register({'folder': str(self.folder), 'entry': 'libraries/win_args.txt'})
        with self.assertRaises(LauncherError): self.app.local_servers.register({'folder': str(self.folder), 'entry': '../outside.jar'})
        self.app.local_servers.save(item['id'], {'accept_eula': True})
        with patch.object(self.app.game, 'ensure_java', return_value='java.exe'), patch('launcher.local_servers.subprocess.Popen', side_effect=self.spawn):
            self.app.local_servers.start(item['id'], Task('Start', None))
            self.assertIn('@' + str(self.folder/'libraries/win_args.txt'), self.children[-1].args)
            self.assertNotIn('-jar', self.children[-1].args)

    def test_real_child_console_pipe_and_graceful_stop(self):
        script = self.root/'fake_server.py'
        script.write_text('import sys\nprint("READY", flush=True)\nfor line in sys.stdin:\n print("GOT:"+line.strip(), flush=True)\n if line.strip()=="stop": break\n', encoding='utf-8')
        self.app.local_servers.save(self.item['id'], {'accept_eula': True})
        real_popen = subprocess.Popen
        def spawn(_command, **kwargs): return real_popen([sys.executable, '-u', str(script)], **kwargs)
        with patch.object(self.app.game, 'ensure_java', return_value='java.exe'), patch('launcher.local_servers.subprocess.Popen', side_effect=spawn):
            self.app.local_servers.start(self.item['id'], Task('Start', None))
        process = self.app.local_servers.runs[self.item['id']]['process']
        try:
            self.wait(lambda: 'READY' in self.app.local_servers.console(self.item['id'])['lines'])
            self.app.local_servers.command(self.item['id'], 'list')
            self.wait(lambda: 'GOT:list' in self.app.local_servers.console(self.item['id'])['lines'])
            self.app.local_servers.stop(self.item['id'])
            self.wait(lambda: process.poll() is not None)
            self.wait(lambda: process.stdout.closed)
            self.assertEqual(process.returncode, 0)
            self.assertIn('GOT:stop', self.app.local_servers.console(self.item['id'])['lines'])
        finally:
            if process.poll() is None: process.terminate(); process.wait(timeout=5)

    def test_download_vanilla_uses_manifest_hash_and_preserves_nonempty_target(self):
        target = self.root/'downloaded'
        data = {'folder': str(target), 'version': '1.20.1', 'accept_eula': True}
        server = {'url': 'https://example.invalid/server.jar', 'sha1': 'a'*40, 'size': 7}
        def download(url, path, sha1, **kwargs):
            self.assertEqual(sha1, server['sha1']); self.assertEqual(kwargs['size'], 7)
            path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'fixture')
        with patch.object(self.app.game, 'manifest', return_value={'versions': [{'id': '1.20.1', 'url': 'https://example.invalid/meta'}]}), patch.object(self.app.net, 'json', return_value={'id': '1.20.1', 'downloads': {'server': server}, 'javaVersion': {'majorVersion': 17}}), patch.object(self.app.net, 'download', side_effect=download):
            with self.assertRaisesRegex(LauncherError, 'EULA'): self.app.local_servers.create(dict(data, accept_eula=False), Task('Create', None))
            with self.assertRaisesRegex(LauncherError, '内存'): self.app.local_servers.create(dict(data, memory=1), Task('Create', None))
            self.assertFalse(target.exists())
            result = self.app.local_servers.create(data, Task('Create', None))
            self.assertEqual(result['java_major'], 17)
            self.assertEqual((target/'server.jar').read_bytes(), b'fixture')
            self.assertEqual(properties(target/'eula.txt')['eula'], 'true')
            with self.assertRaises(LauncherError): self.app.local_servers.create(data, Task('Create', None))
            self.assertEqual((target/'server.jar').read_bytes(), b'fixture')


if __name__ == '__main__': unittest.main()
