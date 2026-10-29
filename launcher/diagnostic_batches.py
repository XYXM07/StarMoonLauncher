"""Bounded AI inputs with cross-batch dependency context and no silent omissions."""
import json

from .core import LauncherError


PROMPT_LIMIT = 30000


def mod_batches(data):
    """Partition metadata and findings, retaining providers outside each batch."""
    mods = data.get('mods', [])
    provided = data.get('provided', {})
    common = {k: v for k, v in data.items() if k not in ('mods', 'provided', 'local_findings')}
    disabled, owners = {}, {}
    for mod in mods:
        names = [(mod.get('id'), mod.get('version', '?'))]
        for alias in mod.get('provides', []):
            if isinstance(alias, str): names.append((alias, mod.get('version', '?')))
            elif isinstance(alias, dict): names.append((alias.get('id'), alias.get('version', mod.get('version', '?'))))
        for name, version in names:
            if not name: continue
            if mod.get('enabled') is False:
                disabled.setdefault(name, set()).add(str(version))
            elif name == mod.get('id') and '!' not in mod.get('file', ''):
                owners.setdefault(name, set()).add(mod.get('file', ''))

    def payload(records, findings):
        names = {'minecraft', 'java', 'forge', 'neoforge', 'fabricloader', 'quilt_loader'}
        for record in records:
            names.add(record.get('id'))
            for field in ('depends', 'breaks'):
                names.update(record.get(field, {}))
            for alias in record.get('provides', []):
                names.add(alias if isinstance(alias, str) else alias.get('id') if isinstance(alias, dict) else None)
        return dict(common, mods=records, local_findings=findings,
                    provided={name: provided[name] for name in sorted(n for n in names if n) if name in provided},
                    disabled_providers={name: sorted(disabled[name]) for name in sorted(n for n in names if n) if name in disabled},
                    duplicate_ids={name: len(owners[name]) for name in sorted(n for n in names if n) if len(owners.get(name, ())) > 1})

    def fits(records, findings):
        # Reserve room for batch numbers, added after the partition is known.
        return len(json.dumps(payload(records, findings), ensure_ascii=False)) <= PROMPT_LIMIT - 256

    if not fits([], []):
        raise LauncherError('诊断上下文过大，请缩短额外启动参数或错误信息后重试')
    skipped = []

    def fragments(record):
        if fits([record], []):
            yield record
            return
        # An unusually large descriptor may still contain valid, small individual
        # requirements. Split collections rather than dropping the entire JAR.
        fields = [name for name in ('depends', 'breaks', 'provides')
                  if isinstance(record.get(name), (dict, list)) and len(record[name]) > 1]
        if not fields:
            skipped.append(str(record.get('id') or record.get('file') or '?')[:120])
            return
        field = max(fields, key=lambda name: len(json.dumps(record[name], ensure_ascii=False)))
        values = record[field]
        items = list(values.items()) if isinstance(values, dict) else values
        middle = len(items) // 2
        first, second = items[:middle], items[middle:]
        if isinstance(values, dict): first, second = dict(first), dict(second)
        left = dict(record, metadata_fragment=True)
        left[field] = first
        right = {k: v for k, v in record.items() if k not in ('depends', 'breaks', 'provides')}
        right.update(metadata_fragment=True)
        right[field] = second
        yield from fragments(left)
        yield from fragments(right)

    batches, records, findings = [], [], []
    for mod in mods:
        for record in fragments(mod):
            if records and not fits(records + [record], findings):
                batches.append(payload(records, findings))
                records, findings = [], []
            records.append(record)
    for finding in data.get('local_findings', []):
        if not fits([], [finding]):
            skipped.append('local finding')
            continue
        if not fits(records, findings + [finding]):
            batches.append(payload(records, findings))
            records, findings = [], []
        findings.append(finding)
    if records or findings or not batches: batches.append(payload(records, findings))
    for index, batch in enumerate(batches, 1):
        batch['batch'] = {'index': index, 'total': len(batches)}
    return batches, sorted(set(skipped))


def combine(reports, total, skipped, english=False):
    """Select a concise conclusion; retain every batch's advice for inspection."""
    rank = {'ok': 0, 'unknown': 1, 'risk': 2}
    primary = max(reports, key=lambda item: rank.get(item.get('status'), 1))
    result = {key: primary[key] for key in ('status', 'cause', 'suggestion', 'source')}
    completed = sum(not report.get('failed') for report in reports)
    if completed < total:
        result.update(status='unknown',
                      cause=('Only {}/{} batches completed; analysis is incomplete.' if english else '仅完成 {}/{} 批检查，结果不完整。').format(completed, total),
                      suggestion=('Review the completed batches and retry after resolving the API error.' if english else '查看已完成批次的结果，排除接口错误后重新检查。'))
    if skipped:
        result.update(status='unknown',
                      cause=('Some individual metadata fields are too large to analyze.' if english else '部分模组的单项元数据异常过大，未能完整检查。'),
                      suggestion=('Review the affected mods and completed batches.' if english else '检查以下模组的元数据，并查看已完成批次：') + '、'.join(skipped)[:80])
    result.update(batch_count=total, completed_batches=completed, batch_reports=reports, skipped_mods=skipped)
    result['cause'], result['suggestion'] = result['cause'][:80], result['suggestion'][:140]
    return result
