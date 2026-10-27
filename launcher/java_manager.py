"""Inventory, install and safely retire launcher-owned Temurin runtimes."""
import os
import platform
import shutil
import tempfile
import uuid
from pathlib import Path

from .core import LauncherError, extract_zip, safe_path
from .launch_options import java_architecture
from .management import inventory


def listing(app):
    instances = app.store.instances()
    candidates = app.game.java_candidates()
    known = {os.path.normcase(str(Path(j['path']).resolve())) for j in candidates}
    for instance in instances:
        path = instance.get('java_path') or instance.get('detected_java')
        if path and os.path.normcase(str(Path(path).resolve())) not in known:
            inspected = app.game.inspect_java(path)
            if inspected:
                candidates.append(inspected)
                known.add(os.path.normcase(str(Path(path).resolve())))
    result = []
    runtime = (app.store.root / 'runtime').resolve()
    for item in candidates:
        path = Path(item['path']).resolve()
        uses = [i['name'] for i in instances if any(i.get(k) and Path(i[k]).resolve() == path for k in ('java_path', 'detected_java'))]
        global_path = app.store.config().get('java_path')
        if global_path and Path(global_path).resolve() == path: uses.insert(0, '全局默认')
        managed = runtime in path.parents
        home = path.parent.parent
        size = 0
        if managed:
            for directory, folders, files in os.walk(str(home), followlinks=False):
                folders[:] = [n for n in folders if not (Path(directory) / n).is_symlink()]
                for name in files:
                    f = Path(directory) / name
                    if not f.is_symlink(): size += f.stat().st_size
        result.append(dict(item, architecture=java_architecture(str(path)), managed=managed, uses=uses, size=size))
    return result


def download(app, major, architecture, task):
    major = int(major)
    if major not in (8, 11, 17, 21, 25) or architecture not in ('x64', 'x86', 'aarch64'):
        raise LauncherError('不支持此 Java 版本或架构')
    if os.name != 'nt': raise LauncherError('此下载功能仅支持 Windows')
    url = 'https://api.adoptium.net/v3/assets/latest/{}/hotspot?architecture={}&image_type=jre&os=windows&vendor=eclipse'.format(major, architecture)
    assets = app.net.json(url)
    if not assets: raise LauncherError('没有可用的 Java 下载')
    package = assets[0]['binary']['package']
    parent = safe_path(app.store.root, 'runtime')
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='java-install-', dir=str(parent)) as temporary:
        root = Path(temporary)
        archive = root / 'runtime.zip'
        app.net.download(package['link'], archive, sha256=package['checksum'], size=package['size'], task=task)
        target = root / 'unpacked'
        extract_zip(archive, target, task=task)
        inventory(target, task)
        binaries = list(target.glob('*/bin/java.exe'))
        if len(binaries) != 1: raise LauncherError('Java 目录结构无效')
        inspected = app.game.inspect_java(str(binaries[0]))
        if not inspected or inspected['major'] != major: raise LauncherError('Java 版本校验失败')
        task.check()
        destination = parent / ('temurin-{}-{}-{}'.format(major, architecture, uuid.uuid4().hex[:8]))
        binaries[0].parent.parent.rename(destination)
        return {'path': str(destination / 'bin/java.exe')}


def remove(app, path, task):
    with app.management.runtime_lock:
        return _remove(app, path, task)


def _remove(app, path, task):
    # Conservative: do not retire any runtime while any owned game is active.
    if any(p['running'] for p in app.game.process_status()): raise LauncherError('请先关闭所有游戏再清理 Java')
    item = next((j for j in listing(app) if Path(j['path']).resolve() == Path(path).resolve()), None)
    if not item or not item['managed'] or item['uses']: raise LauncherError('仅可清理未被使用的启动器托管 Java')
    home = Path(path).resolve().parent.parent
    runtime = (app.store.root / 'runtime').resolve()
    if runtime not in home.parents or home == runtime or home.is_symlink(): raise LauncherError('拒绝清理此 Java 路径')
    inventory(home, task)
    task.check()
    trash = safe_path(app.store.root, 'trash/java-' + uuid.uuid4().hex)
    trash.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(home), str(trash))
    return {'path': str(trash)}
