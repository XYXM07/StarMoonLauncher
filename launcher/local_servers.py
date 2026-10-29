"""Local Java server lifecycle; no shell scripts and no automatic EULA acceptance."""
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from collections import deque
from pathlib import Path

from .core import LauncherError, atomic_json, read_json, safe_path, version_id
from .game import split_args


PROPERTIES = {'motd': 'A Minecraft Server', 'server-port': '25565', 'max-players': '20',
              'difficulty': 'easy', 'gamemode': 'survival', 'view-distance': '10',
              'online-mode': 'true', 'white-list': 'false', 'pvp': 'true'}


def property_lines(path):
    if not path.is_file(): return [], 'utf-8'
    if path.stat().st_size > 1024 * 1024: raise LauncherError('服务端配置文件过大')
    raw = path.read_bytes()
    try: return raw.decode('utf-8').splitlines(), 'utf-8'
    except UnicodeDecodeError: return raw.decode('latin-1').splitlines(), 'latin-1'


def properties(path):
    values = {}
    for line in property_lines(path)[0]:
        match = re.match(r'^\s*([^#!\s:=]+)\s*[=:]\s*(.*)$', line)
        if match:
            value = re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m[1], 16)), match[2])
            value = value.encode('utf-16', errors='surrogatepass').decode('utf-16', errors='replace')
            values[match[1]] = re.sub(r'\\([\\:=#! ])', r'\1', value)
    return values


def write_properties(path, changes):
    lines, encoding = property_lines(path)
    # Preserve unrelated options, comments and credentials in the original file.
    lines = [line for line in lines if not (re.match(r'^\s*([^#!\s:=]+)\s*[=:]', line) and
                                          re.match(r'^\s*([^#!\s:=]+)', line)[1] in changes)]
    for key, value in changes.items():
        def escape(character):
            if ord(character) > 127:
                encoded = character.encode('utf-16-be')
                return ''.join('\\u' + encoded[index:index + 2].hex() for index in range(0, len(encoded), 2))
            return '\\\\' if character == '\\' else character
        escaped = ''.join(escape(c) for c in str(value))
        lines.append(key + '=' + escaped)
    temporary = path.with_name(path.name + '.tmp-' + uuid.uuid4().hex)
    try:
        temporary.write_text('\n'.join(lines) + '\n', encoding=encoding)
        os.replace(str(temporary), str(path))
    finally:
        if temporary.exists(): temporary.unlink()


class LocalServers:
    def __init__(self, app):
        self.app = app
        self.path = app.store.root / 'local-servers.json'
        self.lock = threading.RLock()
        self.runs = {}

    def entries(self):
        with self.lock:
            data = read_json(self.path, [])
            if not isinstance(data, list): raise LauncherError('本地服务端列表格式错误')
            return data

    def get(self, identifier):
        item = next((v for v in self.entries() if v['id'] == identifier), None)
        if not item: raise LauncherError('本地服务端不存在')
        return item

    def state(self, identifier):
        with self.lock:
            run = self.runs.get(identifier)
            if not run: return {'running': False, 'state': 'stopped', 'exit_code': None}
            process = run.get('process')
            running = run.get('starting') or (process is not None and process.poll() is None)
            return {'running': bool(running), 'state': 'starting' if run.get('starting') else 'stopping' if running and run.get('stopping') else 'running' if running else 'stopped',
                    'exit_code': process.poll() if process else None, 'pid': process.pid if process else None,
                    'started': run['started'], 'run_id': run['id'], 'java': run.get('java', '')}

    def list(self): return [dict(item, **self.state(item['id'])) for item in self.entries()]

    def active(self):
        with self.lock:
            return [dict(id=key, name=self.runs[key].get('name', ''), **self.state(key)) for key in self.runs if self.state(key)['running']]

    def stopped(self, identifier):
        if self.state(identifier)['running']: raise LauncherError('请先停止此服务端')

    def register(self, data):
        folder = Path(str(data.get('folder', ''))).expanduser().resolve()
        if not str(data.get('folder', '')).strip() or not folder.is_dir(): raise LauncherError('请选择已有的服务端文件夹')
        entry = safe_path(folder, str(data.get('entry', 'server.jar')))
        if not entry.is_file() or entry.suffix.lower() not in ('.jar', '.txt'):
            raise LauncherError('请选择服务端 JAR 或 Forge/NeoForge 的 win_args.txt')
        item = {'id': uuid.uuid4().hex, 'name': str(data.get('name', '')).strip()[:80] or folder.name,
                'folder': str(folder), 'entry': entry.relative_to(folder).as_posix(), 'version': str(data.get('version', ''))[:80],
                'java_path': str(data.get('java_path', '')).strip(), 'java_major': int(data.get('java_major', 21)),
                'memory': int(data.get('memory', 2048)), 'jvm_args': str(data.get('jvm_args', '')).strip()}
        self.validate(item)
        with self.lock:
            items = self.entries()
            if any(Path(v['folder']).resolve() == folder for v in items): raise LauncherError('此服务端文件夹已添加')
            atomic_json(self.path, items + [item])
        return item

    def validate(self, item):
        if not 512 <= item['memory'] <= 65536: raise LauncherError('服务端内存需为 512–65536 MB')
        if not 8 <= item['java_major'] <= 99: raise LauncherError('无效的 Java 版本')
        if len(item.get('jvm_args', '')) > 4000 or re.search(r'[\r\n\x00]', item.get('jvm_args', '')): raise LauncherError('JVM 参数格式错误')

    def create(self, data, task):
        if data.get('accept_eula') is not True: raise LauncherError('请先阅读并同意 Minecraft EULA')
        self.validate({'memory': int(data.get('memory', 2048)), 'java_major': 21, 'jvm_args': str(data.get('jvm_args', ''))})
        version = version_id(str(data.get('version', '')))
        record = next((v for v in self.app.game.manifest()['versions'] if v['id'] == version), None)
        if not record: raise LauncherError('未找到此原版服务端版本')
        metadata = self.app.net.json(record['url'])
        server = metadata.get('downloads', {}).get('server')
        if not server or metadata.get('id') != version: raise LauncherError('此版本未提供原版服务端下载')
        if not re.fullmatch(r'[a-fA-F0-9]{40}', server.get('sha1', '')): raise LauncherError('服务端下载缺少完整性校验')
        folder = Path(str(data.get('folder', ''))).expanduser().resolve()
        if not str(data.get('folder', '')).strip(): raise LauncherError('请选择空的服务端文件夹')
        if folder.exists() and (not folder.is_dir() or any(folder.iterdir())): raise LauncherError('下载原版服务端需要空文件夹')
        if any(Path(item['folder']).resolve() == folder for item in self.entries()): raise LauncherError('此服务端文件夹已添加')
        cache = safe_path(self.app.store.root, 'cache/servers/' + server['sha1'] + '.jar')
        task.update('下载原版服务端', 5)
        self.app.net.download(server['url'], cache, server['sha1'], task=task, size=server.get('size'))
        task.check()
        with self.lock:
            if folder.exists() and any(folder.iterdir()): raise LauncherError('目标文件夹已发生变化，请重新选择空文件夹')
            folder.mkdir(parents=True, exist_ok=True)
            target = safe_path(folder, 'server.jar')
            with target.open('xb') as output, cache.open('rb') as source: shutil.copyfileobj(source, output)
            write_properties(safe_path(folder, 'eula.txt'), {'eula': 'true'})
            item = self.register(dict(data, entry='server.jar', java_major=metadata.get('javaVersion', {}).get('majorVersion', 8)))
        return item

    def config(self, identifier):
        item = self.get(identifier)
        folder = Path(item['folder'])
        current = properties(safe_path(folder, 'server.properties'))
        return dict(item, properties={key: current.get(key, value) for key, value in PROPERTIES.items()},
                    eula=properties(safe_path(folder, 'eula.txt')).get('eula', '').lower() == 'true')

    def save(self, identifier, changes):
        with self.lock:
            self.stopped(identifier)
            item = self.get(identifier)
            for key in ('name', 'java_path', 'jvm_args'):
                if key in changes: item[key] = str(changes[key]).strip()[:4000]
            for key in ('java_major', 'memory'):
                if key in changes: item[key] = int(changes[key])
            self.validate(item)
            props = {k: str(v) for k, v in changes.get('properties', {}).items() if k in PROPERTIES}
            for key, value in props.items():
                if re.search(r'[\r\n\x00]', value) or len(value) > 300: raise LauncherError('服务端配置值格式错误')
                if key in ('server-port', 'max-players', 'view-distance'):
                    low, high = {'server-port': (1, 65535), 'max-players': (1, 10000), 'view-distance': (2, 32)}[key]
                    if not value.isdigit() or not low <= int(value) <= high: raise LauncherError('服务端数值配置超出范围')
                if key in ('online-mode', 'white-list', 'pvp') and value not in ('true', 'false'): raise LauncherError('服务端开关值无效')
                if key == 'difficulty' and value not in ('peaceful', 'easy', 'normal', 'hard'): raise LauncherError('难度无效')
                if key == 'gamemode' and value not in ('survival', 'creative', 'adventure', 'spectator'): raise LauncherError('游戏模式无效')
            folder = Path(item['folder'])
            if not folder.is_dir(): raise LauncherError('服务端文件夹不存在')
            if props: write_properties(safe_path(folder, 'server.properties'), props)
            if changes.get('accept_eula') is True: write_properties(safe_path(folder, 'eula.txt'), {'eula': 'true'})
            atomic_json(self.path, [item if v['id'] == identifier else v for v in self.entries()])
        return self.config(identifier)

    def remove(self, identifier):
        with self.lock:
            self.stopped(identifier)
            atomic_json(self.path, [item for item in self.entries() if item['id'] != identifier])
        return {}

    def start(self, identifier, task):
        with self.app.management.runtime_lock:
            with self.lock:
                self.stopped(identifier)
                item = self.config(identifier)
                if not item['eula']: raise LauncherError('请先阅读并同意 Minecraft EULA')
                folder = Path(item['folder'])
                entry = safe_path(folder, item['entry'])
                if not entry.is_file(): raise LauncherError('服务端启动文件不存在')
                run = {'id': uuid.uuid4().hex, 'name': item['name'], 'starting': True, 'started': time.time(), 'lines': deque(maxlen=1500), 'sequence': 0}
                self.runs[identifier] = run
            try:
                task.update('准备服务端 Java 环境', 5)
                java = self.app.game.ensure_java(item['java_major'], task, item)
                # javaw has no useful console stream; prefer its adjacent java.exe.
                if Path(java).name.lower() == 'javaw.exe' and Path(java).with_name('java.exe').is_file(): java = str(Path(java).with_name('java.exe'))
                command = [java, '-Xms512M', '-Xmx{}M'.format(item['memory']), '-Dfile.encoding=UTF-8'] + split_args(item['jvm_args'])
                if entry.suffix.lower() == '.txt':
                    user_args = safe_path(folder, 'user_jvm_args.txt')
                    if user_args.is_file(): command.append('@' + str(user_args))
                    command.append('@' + str(entry))
                else: command += ['-jar', str(entry)]
                command.append('nogui')
                task.check()
                process = subprocess.Popen(command, cwd=str(folder), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                           stderr=subprocess.STDOUT, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                with self.lock: run.update(process=process, starting=False, java=java)
            except Exception:
                with self.lock: run['starting'] = False
                raise
            def monitor():
                try:
                    # Bounded reads prevent one extremely long console line consuming memory.
                    for raw in iter(lambda: process.stdout.readline(8192), b''):
                        with self.lock:
                            run['sequence'] += 1
                            run['lines'].append((run['sequence'], raw.decode('utf-8', errors='replace').rstrip()))
                    code = process.wait()
                    with self.lock:
                        run['sequence'] += 1
                        run['lines'].append((run['sequence'], 'Process exited: ' + str(code)))
                finally:
                    process.stdout.close()
                    process.stdin.close()
            threading.Thread(target=monitor, daemon=True).start()
        return self.state(identifier)

    def command(self, identifier, command):
        command = str(command).strip()
        if not command or len(command) > 2000 or re.search(r'[\r\n\x00]', command): raise LauncherError('请输入单行服务端命令')
        with self.lock:
            run = self.runs.get(identifier, {})
            process = run.get('process')
            if not process or process.poll() is not None: raise LauncherError('服务端尚未运行')
            try:
                process.stdin.write((command.lstrip('/') + '\n').encode('utf-8'))
                process.stdin.flush()
            except (OSError, ValueError): raise LauncherError('无法向服务端发送命令') from None
            if command.lstrip('/') == 'stop': run['stopping'] = True
        return {}

    def stop(self, identifier, force=False):
        if not force: return self.command(identifier, 'stop')
        with self.lock:
            run = self.runs.get(identifier, {})
            process = run.get('process')
            if process and process.poll() is None: process.terminate()
        return {}

    def console(self, identifier, after=0, run_id=''):
        self.get(identifier)
        with self.lock:
            run = self.runs.get(identifier, {})
            if run_id != run.get('id', ''): after = 0
            return {'run_id': run.get('id', ''), 'lines': [line for seq, line in run.get('lines', []) if seq > after],
                    'cursor': run.get('sequence', 0), 'status': self.state(identifier)}
