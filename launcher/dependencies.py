"""Local dependency graph and transactional, verified candidate installation."""
from .core import LauncherError
from .diagnostics import satisfies
from .management import mod_transaction, digest


def graph(app, identifier, task=None):
    data, fingerprint = app.diagnostics.snapshot(identifier, task, include_disabled=True)
    nodes, edges = data['mods'], []
    disabled = {}
    for record in nodes:
        if record.get('enabled') is not False: continue
        identifiers = [record.get('id')] + [a if isinstance(a, str) else a.get('id') for a in record.get('provides', []) if isinstance(a, (str, dict))]
        for name in identifiers:
            if name: disabled[name] = record['file'].split('!')[0]
    for record in nodes:
        if record.get('enabled') is False: continue
        for kind in ('depends', 'breaks'):
            for target, required in record.get(kind, {}).items():
                version = data['provided'].get(target)
                match = satisfies(version, required) if version is not None else None
                if kind == 'breaks': status = 'conflict' if match is True else ('unknown' if version and match is None else 'ok')
                else: status = ('disabled' if target in disabled else 'missing') if version is None else ('mismatch' if match is False else ('ok' if match is True else 'unknown'))
                edges.append({'source': record.get('id') or record['file'], 'file': record['file'].split('!')[0], 'target': target,
                              'required': required, 'version': version, 'kind': kind, 'status': status, 'disabled_file': disabled.get(target)})
    return {'nodes': nodes, 'edges': edges, 'issues': data['local_findings'], 'fingerprint': fingerprint}


def impact(app, identifier, filenames):
    selected = set(filenames)
    data = graph(app, identifier)
    removed = set()
    for node in data['nodes']:
        if node['file'].split('!')[0] in selected:
            removed.add(node.get('id'))
            for alias in node.get('provides', []):
                removed.add(alias if isinstance(alias, str) else alias.get('id') if isinstance(alias, dict) else None)
    return [edge for edge in data['edges'] if edge['kind'] == 'depends' and edge['target'] in removed and edge['file'] not in selected]


def candidates(app, identifier, dependency, provider='modrinth'):
    if provider not in ('modrinth', 'curseforge'): raise LauncherError('请选择 Modrinth 或 CurseForge')
    instance = app.store.instance(identifier)
    result = app.content.search(dependency, 'mod', instance['game_version'], instance['loader'], provider=provider)
    return result.get('hits', [])


def repair(app, identifier, dependency, project, version, provider, disabled_file, task):
    with app.management.guard(identifier):
        current = graph(app, identifier, task)
        affected = [e for e in current['edges'] if e['target'] == dependency and e['kind'] == 'depends']
        if not affected: raise LauncherError('此依赖问题已变化，请重新扫描')
        def owns(node):
            return node.get('id') == dependency or dependency in [a if isinstance(a, str) else a.get('id') for a in node.get('provides', []) if isinstance(a, (str, dict))]
        originals = {n['file']: digest(app.store.game_dir(identifier) / 'mods' / n['file']) for n in current['nodes'] if owns(n) and '!' not in n['file']}
        with mod_transaction(app, identifier, task, '修复依赖 · ' + dependency):
            if disabled_file:
                if not any(e['disabled_file'] == disabled_file for e in affected): raise LauncherError('禁用模组已变化')
                app.content.toggle(identifier, disabled_file, 'mod')
            else:
                if provider not in ('modrinth', 'curseforge'): raise LauncherError('不支持此修复来源')
                app.content.install_project(project, version, identifier, 'mod', provider, task)
            after = graph(app, identifier, task)
            replacements = [n for n in after['nodes'] if owns(n) and n.get('enabled') is not False and '!' not in n['file'] and
                            (n['file'] not in originals or digest(app.store.game_dir(identifier) / 'mods' / n['file']) != originals[n['file']]) and
                            all(satisfies(n.get('version', '?'), e['required']) is True for e in affected)]
            if replacements and not disabled_file:
                for filename, before in originals.items():
                    nodes = [n for n in current['nodes'] if n['file'] == filename]
                    if filename not in {n['file'] for n in replacements} and len(nodes) == 1 and owns(nodes[0]):
                        path = app.store.game_dir(identifier) / 'mods' / filename
                        if path.is_file() and digest(path) == before:
                            app.content.toggle(identifier, filename, 'mod', remove=True)
                after = graph(app, identifier, task)
            resolved = [e for e in after['edges'] if e['target'] == dependency and e['kind'] == 'depends']
            if not resolved or any(e['status'] != 'ok' for e in resolved):
                raise LauncherError('下载的模组未满足所需 ID 或版本范围，已恢复原文件')
            def failures(data):
                return {(e['source'], e['target'], e['kind'], str(e['required'])) for e in data['edges'] if e['status'] in ('missing', 'disabled', 'mismatch', 'conflict')}
            duplicates = {i for i in after['issues'] if i.startswith('重复模组 ID：')} - set(current['issues'])
            if failures(after) - failures(current) or duplicates:
                raise LauncherError('修复引入了新的依赖问题，已恢复原文件；请检查依赖关系')
        return {'instance_id': identifier}
