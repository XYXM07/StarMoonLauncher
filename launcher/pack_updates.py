"""Stage pack releases, compare against the original pack, then merge reversibly."""
import shutil
import tempfile
from pathlib import Path

from .core import Cancelled, LauncherError, Store, atomic_json, read_json, safe_path
from .management import ChangeSet, digest, inventory

PROVIDERS = ('modrinth', 'curseforge', 'atlauncher', 'ftb')
PROTECTED = {'saves', 'screenshots', 'logs', 'crash-reports', 'backups'}


def pack_files(directory, task=None):
    return inventory(directory, task, exclude=PROTECTED | {'options.txt', 'servers.dat', 'servers.dat_old', 'optionsof.txt'})


def mod_ids(content, directory, files):
    result = {}
    for path in files:
        if path.startswith('mods/') and path.endswith(('.jar', '.jar.disabled', '.litemod', '.litemod.disabled')):
            identifier = content.previews.local(safe_path(directory, path)).get('mod_id')
            if identifier: result[path] = identifier
    return result


def remember(content, identifier, task, source=None):
    if source: content.store.update_instance(identifier, source)
    instance = content.store.instance(identifier)
    files = pack_files(content.store.game_dir(identifier), task)
    atomic_json(content.store.instance_dir(identifier) / 'pack-baseline.json', {
        'files': files,
        'mod_ids': mod_ids(content, content.store.game_dir(identifier), files),
        'provider': instance.get('pack_provider'), 'project': instance.get('pack_project'), 'version': instance.get('pack_version')})


def stage(content, parent, provider, project, version, task):
    from .content import ContentManager
    from .game import GameManager
    store = Store(parent)
    # Only the immutable vanilla/libraries cache is shared; all instance files stay private.
    store.minecraft_root = content.store.minecraft_root
    config = content.store.config()
    keys = ('source', 'concurrency', 'java_path', 'java_auto', 'curseforge_key', 'download_parallel', 'download_segments',
            'ai_pack_download_fallback', 'ai_base_url', 'ai_model', 'ai_api_key_protected')
    store.save({key: config[key] for key in keys if key in config})
    game = GameManager(store, content.net, content.game.accounts)
    game.ensure_java = content.game.ensure_java
    manager = ContentManager(store, content.net, game)
    # Recovery uses the user's already configured model and explicit consent UI.
    manager.ai_downloads = content.ai_downloads
    result = manager.install_project(project, version, '', 'modpack', provider, task)
    identifier = result['instance_id']
    return store, identifier


def plan_merge(old, current, new):
    plan = []
    for path in sorted(set(old) | set(new)):
        before, local, incoming = old.get(path), current.get(path), new.get(path)
        if local == incoming or before == incoming: continue
        action = 'remove' if incoming is None else ('add' if local is None else 'replace')
        conflict = local != before
        plan.append({'path': path, 'action': action, 'conflict': conflict, 'before': local, 'incoming': incoming})
    return plan


def upgrade(app, identifier, provider, project, version, current_version, task):
    if provider not in PROVIDERS or not project or not version: raise LauncherError('请选择整合包来源和目标版本')
    with app.management.guard(identifier):
        instance = app.store.instance(identifier)
        if instance.get('external_minecraft'):
            raise LauncherError('外部版本请先导入为独立整合包后升级')
        baseline = read_json(app.store.instance_dir(identifier) / 'pack-baseline.json', {})
        known = instance.get('pack_provider') and instance.get('pack_project')
        if known and (provider != instance['pack_provider'] or str(project) != str(instance['pack_project'])):
            raise LauncherError('升级来源必须与已安装整合包一致')
        old_version = instance.get('pack_version') or current_version
        if str(old_version) == str(version): raise LauncherError('已安装此整合包版本')
        parent = safe_path(app.store.root, 'cache/pack-upgrades')
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=str(parent)) as temporary:
            root = Path(temporary)
            old = baseline.get('files')
            old_ids = baseline.get('mod_ids', {})
            if old is None:
                if not old_version: raise LauncherError('旧整合包缺少来源记录，请填写当前整合包版本 ID')
                task.update('重建旧整合包文件清单', 1)
                old_store, old_id = stage(app.content, root / 'old', provider, project, str(old_version), task)
                old = pack_files(old_store.game_dir(old_id), task)
                old_ids = mod_ids(app.content, old_store.game_dir(old_id), old)
            task.update('准备整合包新版本', 5)
            incoming_store, incoming_id = stage(app.content, root / 'new', provider, project, str(version), task)
            incoming_dir = incoming_store.game_dir(incoming_id)
            incoming_instance = incoming_store.instance(incoming_id)
            new = pack_files(incoming_dir, task)
            game_dir = app.store.game_dir(identifier)
            current = pack_files(game_dir, task)
            current_ids = mod_ids(app.content, game_dir, current)
            new_ids = mod_ids(app.content, incoming_dir, new)
            # Disabled mods keep their state across filename-preserving updates.
            disabled = {}
            for name in old:
                if name not in current and name.startswith('mods/') and name + '.disabled' in current:
                    disabled[name] = name + '.disabled'
                    current[name] = current.pop(name + '.disabled')
            plan = plan_merge(old, current, new)
            # A renamed JAR is still the same mod. Preserve disabled state and
            # treat a customized old JAR + its replacement as one conflict.
            for new_path, mod_id in new_ids.items():
                previous = [p for p, value in old_ids.items() if value == mod_id and p != new_path]
                if len(previous) == 1:
                    old_path = previous[0]
                    if old_path in disabled: disabled[new_path] = new_path + '.disabled'
                    if current.get(old_path) != old.get(old_path):
                        for item in plan:
                            if item['path'] in (old_path, new_path): item['conflict'] = True
                custom = [p for p, value in current_ids.items() if value == mod_id and p not in old and (p[:-9] if p.endswith('.disabled') else p) not in old]
                for path in custom:
                    if path in (new_path, new_path + '.disabled'): continue
                    if not any(p['path'] == path for p in plan):
                        plan.append({'path': path, 'action': 'remove', 'conflict': True, 'before': current.get(path), 'incoming': None})
                    for item in plan:
                        if item['path'] == new_path: item['conflict'] = True
            lines = [{'path': p['path'], 'action': p['action'], 'conflict': p['conflict']} for p in plan]
            choice = task.request_choice('pack_upgrade', {
                'name': instance['name'], 'from': old_version or '', 'to': version,
                'game_version': incoming_instance['game_version'], 'loader': incoming_instance['loader'], 'loader_version': incoming_instance.get('loader_version', ''),
                'changes': lines, 'conflicts': sum(p['conflict'] for p in plan)},
                [{'id': 'keep', 'name': '保留本地修改并升级'}, {'id': 'replace', 'name': '采用整合包文件并升级'}])
            if choice not in ('keep', 'replace'): raise Cancelled('已取消整合包升级')
            selected = [p for p in plan if not p['conflict'] or choice == 'replace']
            for item in selected:
                target = safe_path(game_dir, disabled.get(item['path'], item['path']))
                if digest(target, task) != item['before']:
                    raise LauncherError('预览后文件发生变化，请重新检查：' + item['path'])
            change = ChangeSet(app.store, identifier, '整合包升级 · ' + str(version), task)
            change.capture(['game/' + disabled.get(p['path'], p['path']) for p in selected] +
                           ['meta/instance.json', 'meta/content.json', 'meta/pack-baseline.json'])
            change.begin()
            try:
                for index, item in enumerate(selected):
                    task.update('应用整合包文件 · ' + item['path'], 90 + 8 * index / max(1, len(selected)))
                    target = safe_path(game_dir, disabled.get(item['path'], item['path']))
                    if item['action'] == 'remove': target.unlink()
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(str(safe_path(incoming_dir, item['path'])), str(target))
                keys = ('game_version', 'version', 'loader', 'loader_version', 'loader_installed', 'required_java', 'detected_java')
                changes = {key: incoming_instance[key] for key in keys if key in incoming_instance}
                changes.update(pack_provider=provider, pack_project=str(project), pack_version=str(version), ready=True, pack_install_pending=False)
                app.store.update_instance(identifier, changes)
                incoming_records = read_json(incoming_store.instance_dir(incoming_id) / 'content.json', [])
                retained = read_json(app.store.instance_dir(identifier) / 'content.json', [])
                # Only attach a new registry entry when its actual file was applied.
                applied = {p['path'] for p in selected if p['action'] != 'remove'}
                for record in incoming_records:
                    folder = {'mod': 'mods', 'resourcepack': 'resourcepacks', 'shader': 'shaderpacks'}.get(record['kind'])
                    if folder and folder + '/' + record['filename'] in applied:
                        retained = [r for r in retained if (r.get('provider'), r.get('project_id')) != (record['provider'], record['project_id'])]
                        retained.append(record)
                atomic_json(app.store.instance_dir(identifier) / 'content.json', retained)
                atomic_json(app.store.instance_dir(identifier) / 'pack-baseline.json', {'files': new, 'mod_ids': new_ids, 'provider': provider, 'project': project, 'version': version})
                task.check()
                change.finish()
            except BaseException:
                change.restore()
                raise
            return {'instance_id': identifier, 'changed': len(selected), 'preserved': len(plan) - len(selected)}
