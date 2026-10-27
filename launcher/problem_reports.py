"""Bounded log views and explicit, local-only redacted problem reports."""
import json
import platform
import re
import time
import uuid
import zipfile
from pathlib import Path

from . import __version__
from .core import LauncherError, safe_path


def logs(app, identifier):
    meta, game = app.store.instance_dir(identifier), app.store.game_dir(identifier)
    paths = [('meta', p) for p in meta.glob('runs/*/launcher-game.log')]
    paths += [('meta', meta / 'installer.log'), ('meta', meta / 'launcher-game.log')]
    paths += [('game', p) for p in (game / 'logs').glob('*.log')]
    paths += [('game', p) for p in (game / 'crash-reports').glob('*.txt')]
    result = []
    for kind, path in paths:
        root = meta if kind == 'meta' else game
        if path.is_file() and not path.is_symlink() and path.resolve() == path.absolute():
            relative = path.relative_to(root).as_posix()
            result.append({'id': kind + '/' + relative, 'name': relative, 'time': path.stat().st_mtime, 'size': path.stat().st_size})
    return sorted(result, key=lambda r: r['time'], reverse=True)[:100]


def read_log(app, identifier, log_id, query='', level='all'):
    if log_id not in {item['id'] for item in logs(app, identifier)}: raise LauncherError('日志不存在或已变化')
    kind, relative = log_id.split('/', 1)
    root = app.store.instance_dir(identifier) if kind == 'meta' else app.store.game_dir(identifier)
    path = safe_path(root, relative)
    with path.open('rb') as stream:
        stream.seek(max(0, path.stat().st_size - 1024 * 1024))
        text = stream.read(1024 * 1024).decode('utf-8', errors='replace')
    if query or level != 'all':
        lines, include = [], False
        for line in text.splitlines():
            match = re.search(r'\b(TRACE|DEBUG|INFO|WARN|ERROR|FATAL)\b', line)
            if match: include = level == 'all' or match.group(1) in ({'ERROR', 'FATAL'} if level == 'error' else {'WARN', 'ERROR', 'FATAL'})
            elif level == 'all': include = True
            if include and query.casefold() in line.casefold(): lines.append(line)
        text = '\n'.join(lines)
    return {'text': text, 'truncated': path.stat().st_size > 1024 * 1024}


def export(app, identifier, destination, task):
    target = Path(destination).absolute()
    if target.suffix.lower() != '.zip' or target.exists(): raise LauncherError('请选择尚不存在的 ZIP 文件')
    if target.parent.resolve() != target.parent: raise LauncherError('导出目录不能是链接')
    instance = app.store.instance(identifier)
    metadata = {key: instance.get(key) for key in ('name', 'game_version', 'loader', 'loader_version', 'required_java', 'renderer')}
    metadata.update(launcher=__version__, os=platform.system(), os_release=platform.release(), architecture=platform.machine())
    data, _ = app.diagnostics.snapshot(identifier, task)
    temporary = target.with_name(target.name + '.part-' + uuid.uuid4().hex)
    try: key = app.diagnostics.key()
    except LauncherError: key = ''
    def clean(value): return app.diagnostics.redact(value, key)
    created = False
    try:
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('environment.json', clean(json.dumps(metadata, ensure_ascii=False, indent=2)))
            archive.writestr('mods.json', clean(json.dumps(data, ensure_ascii=False, indent=2)))
            for index, entry in enumerate(logs(app, identifier)[:8]):
                task.check()
                archive.writestr('logs/{}.txt'.format(index + 1), clean(entry['name'] + '\n' + read_log(app, identifier, entry['id'])['text']))
            archive.writestr('README.txt', 'Local diagnostic export. No account configuration, API keys, worlds or screenshots are included. Review logs before sharing.\n')
        task.check()
        # Exclusive destination creation avoids overwriting a report selected meanwhile.
        with target.open('xb') as output, temporary.open('rb') as source:
            created = True
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                task.check()
                output.write(chunk)
        return {'path': str(target)}
    except BaseException:
        if created and target.is_file(): target.unlink()
        raise
    finally:
        if temporary.exists(): temporary.unlink()
