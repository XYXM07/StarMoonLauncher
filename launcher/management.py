"""Reversible instance operations shared by bulk content and pack updates."""
import hashlib
import os
import shutil
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .core import LauncherError, atomic_json, read_json, safe_path


def digest(path, task=None):
    if not path.is_file():
        return None
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            if task: task.check()
            value.update(chunk)
    return value.hexdigest()


def inventory(root, task=None, exclude=()):
    root = Path(root).resolve()
    result = {}
    if not root.exists(): return result
    for directory, folders, files in os.walk(str(root), followlinks=False):
        if Path(directory) == root:
            folders[:] = [name for name in folders if name.lower() not in exclude]
            files[:] = [name for name in files if name.lower() not in exclude]
        for name in folders + files:
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            safe_path(root, relative)  # Reject links/junctions escaping the root.
            if path.is_symlink() or path.resolve() != path.absolute():
                raise LauncherError('操作目录包含链接，请先移除链接：' + relative)
        for name in files:
            path = Path(directory) / name
            result[path.relative_to(root).as_posix()] = digest(path, task)
            if len(result) > 100000:
                raise LauncherError('文件数量超过操作上限')
    return result


class Management:
    def __init__(self, app):
        self.app, self.locks = app, {}
        self.lock = threading.RLock()
        self.runtime_lock = threading.RLock()

    @contextmanager
    def guard(self, identifier, launching=False):
        # Versions sharing an external game directory must share a lock too.
        key = os.path.normcase(str(self.app.store.game_dir(identifier).resolve()))
        with self.lock:
            lock = self.locks.setdefault(key, threading.RLock())
        if not lock.acquire(False):
            raise LauncherError('此版本正在修改或启动，请等待完成')
        try:
            if not launching:
                for process in self.app.game.process_status():
                    if (process['running'] or not process.get('finalized', True)) and os.path.normcase(str(self.app.store.game_dir(process['instance_id']).resolve())) == key:
                        raise LauncherError('请先关闭此版本的所有游戏进程')
            yield
        finally:
            lock.release()

    def run(self, identifier, function, *args):
        with self.guard(identifier):
            return function(*args)


class ChangeSet:
    """Write-ahead per-file backup; interrupted operations can be restored in UI."""
    def __init__(self, store, identifier, title, task):
        self.store, self.identifier, self.task = store, identifier, task
        self.game = store.game_dir(identifier).resolve()
        self.meta = store.instance_dir(identifier).resolve()
        self.directory = self.meta / 'changes' / uuid.uuid4().hex
        safe_path(self.meta, self.directory.relative_to(self.meta).as_posix())
        self.directory.mkdir(parents=True)
        self.data = {'id': self.directory.name, 'title': title, 'time': time.time(), 'status': 'preparing', 'files': {}}
        self.save()

    def save(self): atomic_json(self.directory / 'change.json', self.data)

    def target(self, key):
        kind, relative = key.split('/', 1)
        if kind not in ('game', 'meta'): raise LauncherError('无效的变更路径')
        if kind == 'meta' and relative not in ('content.json', 'instance.json', 'pack-baseline.json', 'content-policy.json'):
            raise LauncherError('无效的版本元数据路径')
        root = self.game if kind == 'game' else self.meta
        target = safe_path(root, relative)
        if (root / relative).absolute() != target: raise LauncherError('变更目标不能包含目录链接')
        return target

    def capture(self, keys):
        for key in sorted(set(keys)):
            self.task.check()
            if key in self.data['files']: continue
            target = self.target(key)
            if target.is_dir() or target.is_symlink(): raise LauncherError('目标不是普通文件：' + key)
            before = digest(target, self.task)
            if before is not None:
                backup = safe_path(self.directory / 'before', key)
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(target), str(backup))
                if digest(backup, self.task) != before: raise LauncherError('备份期间文件发生变化')
            self.data['files'][key] = {'before': before}
        self.save()

    def begin(self):
        self.data['status'] = 'applying'
        self.save()

    def finish(self):
        for key, item in self.data['files'].items():
            item['after'] = digest(self.target(key))
            if key == 'meta/instance.json' and item['before'] is not None:
                before = read_json(safe_path(self.directory / 'before', key))
                after = read_json(self.target(key))
                item['fields'] = {field: {'before': before.get(field), 'after': after.get(field), 'existed': field in before}
                                  for field in set(before) | set(after) if before.get(field) != after.get(field)}
        self.data['status'] = 'completed'
        self.save()

    def restore(self):
        # Restore is deliberately not cancellable once it starts.
        for key, item in self.data['files'].items():
            target = self.target(key)
            if key == 'meta/instance.json' and 'fields' in item:
                current = read_json(target)
                for field, change in item['fields'].items():
                    if change['existed']: current[field] = change['before']
                    else: current.pop(field, None)
                atomic_json(target, current)
                continue
            if item['before'] is None:
                if target.is_file(): target.unlink()
            else:
                backup = safe_path(self.directory / 'before', key)
                if digest(backup) != item['before']: raise LauncherError('变更备份校验失败')
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(target.name + '.restore-' + uuid.uuid4().hex)
                shutil.copy2(str(backup), str(temporary))
                os.replace(str(temporary), str(target))
        self.data['status'] = 'restored'
        self.save()


def history(store, identifier):
    result = []
    for path in (store.instance_dir(identifier) / 'changes').glob('*/change.json'):
        value = read_json(path, {})
        if value.get('status') in ('completed', 'applying'):
            result.append({key: value.get(key) for key in ('id', 'title', 'time', 'status')})
    return sorted(result, key=lambda item: item['time'], reverse=True)


def restore_change(app, identifier, change_id, task):
    from .core import valid_id
    with app.management.guard(identifier):
        change = ChangeSet.__new__(ChangeSet)
        change.store, change.identifier, change.task = app.store, identifier, task
        change.game, change.meta = app.store.game_dir(identifier).resolve(), app.store.instance_dir(identifier).resolve()
        change.directory = safe_path(change.meta / 'changes', valid_id(change_id))
        change.data = read_json(change.directory / 'change.json', {})
        if change.data.get('status') not in ('completed', 'applying'):
            raise LauncherError('此变更无法恢复')
        for key, item in change.data['files'].items():
            task.check()
            fields = item.get('fields')
            matches = all(read_json(change.target(key)).get(field) == value['after'] for field, value in fields.items()) if fields is not None else digest(change.target(key), task) == item.get('after')
            if 'after' in item and not matches:
                raise LauncherError('变更后文件又被修改，请先处理后续修改：' + key)
            if item['before'] is not None and digest(safe_path(change.directory / 'before', key), task) != item['before']:
                raise LauncherError('变更备份校验失败')
        change.restore()
        return {'instance_id': identifier}


@contextmanager
def mod_transaction(app, identifier, task, title, filenames=None):
    change = ChangeSet(app.store, identifier, title, task)
    root = app.store.game_dir(identifier) / 'mods'
    before = inventory(root, task) if filenames is None else dict.fromkeys(filenames)
    change.capture(['game/mods/' + p for p in before] + ['meta/content.json', 'meta/content-policy.json'])
    change.data['scopes'] = ['mods'] if filenames is None else []
    change.begin()
    previous = getattr(app.content._writes, 'journal', None)
    app.content._writes.journal = change
    try:
        yield change
        task.check()
        for name in inventory(root) if filenames is None else []:
            change.data['files'].setdefault('game/mods/' + name, {'before': None})
        change.finish()
    except BaseException:
        # New files are recorded as absent before the operation.
        for name in inventory(root) if filenames is None else []:
            key = 'game/mods/' + name
            change.data['files'].setdefault(key, {'before': None})
        change.save()
        change.restore()
        raise
    finally:
        app.content._writes.journal = previous


def bulk(app, identifier, action, filenames, task, expected=None):
    if action not in ('enable', 'disable', 'delete', 'update', 'lock', 'unlock', 'ignore'):
        raise LauncherError('未知批量操作')
    if not isinstance(filenames, list) or not filenames or len(filenames) > 2048:
        raise LauncherError('请选择模组')
    with app.management.guard(identifier):
        items = {i['filename']: i for i in app.content.list_local(identifier) if i['kind'] == 'mod'}
        names = list(dict.fromkeys(filenames))
        if any(name not in items for name in names): raise LauncherError('模组列表已变化，请刷新后重试')
        policy_path = app.store.instance_dir(identifier) / 'content-policy.json'
        policies = read_json(policy_path, {})
        updates = {i['filename']: i for i in app.content.check_updates(identifier)} if action in ('update', 'ignore') else {}
        if action in ('update', 'ignore') and expected is not None:
            for name in names:
                if name not in updates or str(updates[name]['new_version']) != str(expected.get(name)):
                    raise LauncherError('可用更新已变化，请重新检查更新')
        files = None if action == 'update' else [] if action in ('lock', 'unlock', 'ignore') else list(set(names + [n[:-9] if n.endswith('.disabled') else n + '.disabled' for n in names]))
        with mod_transaction(app, identifier, task, '批量模组操作 · ' + action, filenames=files):
            for index, name in enumerate(names):
                task.update(name, index * 100 / len(names))
                item = items[name]
                original = name[:-9] if name.endswith('.disabled') else name
                key = policy_key(item)
                if action in ('lock', 'unlock', 'ignore'):
                    policy = policies.setdefault(key, {})
                    if action == 'ignore':
                        if name in updates: policy['ignored'] = str(updates[name]['new_version'])
                    else: policy['locked'] = action == 'lock'
                elif action == 'update':
                    update = updates.get(name)
                    if not update: continue
                    app.content.install_project(update['project_id'], str(update['new_version']), identifier, 'mod', update['provider'], task)
                    # Keep the user's enabled/disabled choice even if the filename changes.
                    records = app.content.list_local(identifier)
                    installed = next((r for r in records if r.get('provider') == item.get('provider') and r.get('project_id') == item.get('project_id')), None)
                    if installed and installed['enabled'] != item['enabled']:
                        app.content.toggle(identifier, installed['filename'], 'mod')
                elif action == 'delete' or item['enabled'] != (action == 'enable'):
                    app.content.toggle(identifier, name, 'mod', action == 'delete')
            if action == 'update':
                original_states = {policy_key(item): item['enabled'] for item in items.values()}
                for item in app.content.list_local(identifier):
                    key = policy_key(item)
                    if item['kind'] == 'mod' and key in original_states and item['enabled'] != original_states[key]:
                        app.content.toggle(identifier, item['filename'], 'mod')
            atomic_json(policy_path, policies)
        return {'instance_id': identifier, 'count': len(names)}


def policy_key(item):
    if item.get('project_id'): return item.get('provider', '') + ':' + str(item['project_id'])
    name = item['filename']
    return 'file:' + (name[:-9] if name.endswith('.disabled') else name)


def identify_mods(app, identifier, task):
    """Match local files by published SHA-512, never by guessed filenames."""
    from .core import request_json
    with app.management.guard(identifier):
        records_path = app.store.instance_dir(identifier) / 'content.json'
        records = read_json(records_path, [])
        known = {r['filename'] for r in records if r.get('kind') == 'mod'}
        hashes = {}
        folder = app.store.game_dir(identifier) / 'mods'
        for item in app.content.list_local(identifier):
            if item['kind'] != 'mod': continue
            name = item['filename'][:-9] if item['filename'].endswith('.disabled') else item['filename']
            if name in known: continue
            value = hashlib.sha512()
            with safe_path(folder, item['filename']).open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    task.check()
                    value.update(chunk)
            hashes[value.hexdigest()] = (name, item['filename'], digest(safe_path(folder, item['filename']), task))
        found = []
        keys = list(hashes)
        for start in range(0, len(keys), 100):
            task.update('识别模组更新来源', start * 90 / max(1, len(keys)))
            response = request_json('https://api.modrinth.com/v2/version_files', {'hashes': keys[start:start + 100], 'algorithm': 'sha512'})
            for hashed, version in response.items():
                if hashed not in hashes: continue
                if not any(file.get('hashes', {}).get('sha512') == hashed for file in version.get('files', [])): continue
                instance = app.store.instance(identifier)
                loader = 'forge' if instance['loader'] == 'cleanroom' else instance['loader']
                if instance['game_version'] not in version.get('game_versions', []) or loader not in version.get('loaders', []): continue
                name, actual, before = hashes[hashed]
                if digest(safe_path(folder, actual), task) != before: raise LauncherError('识别期间模组发生变化，请重新扫描')
                found.append({'provider': 'modrinth', 'project_id': version['project_id'], 'version_id': version['id'],
                              'filename': name, 'kind': 'mod', 'name': version['name'], 'installed': time.time()})
        change = ChangeSet(app.store, identifier, '识别模组更新来源', task)
        change.capture(['meta/content.json', 'meta/content-policy.json'])
        change.begin()
        try:
            task.check()
            atomic_json(records_path, records + found)
            policy_path = app.store.instance_dir(identifier) / 'content-policy.json'
            policies = read_json(policy_path, {})
            for item in found:
                previous = policies.pop('file:' + item['filename'], None)
                if previous: policies[policy_key(item)] = dict(previous, **policies.get(policy_key(item), {}))
            atomic_json(policy_path, policies)
            change.finish()
        except BaseException:
            change.restore()
            raise
        return {'instance_id': identifier, 'identified': len(found)}
