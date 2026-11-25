"""Evidence provenance and the player's differential diagnosis."""
import json
import math
import re


def fingerprint(finding):
    value = finding if isinstance(finding, str) else json.dumps(
        finding, ensure_ascii=False, sort_keys=True)
    return re.sub(r'\s+', '', value).casefold()


def new_findings(previous, proposed):
    seen = {fingerprint(item) for item in previous}
    findings = []
    for item in proposed:
        if not isinstance(item, (str, dict)) or not item:
            continue
        key = fingerprint(item)
        if key not in seen:
            seen.add(key)
            findings.append(item)
    return findings


def board(session):
    evidence = []
    seen = set()
    for number, event in enumerate(session['events'], 1):
        before, after = event['before'], event['after']
        for index, finding in enumerate(event.get('findings', after.get('observations', [])), 1):
            key = fingerprint(finding)
            if key in seen:
                continue
            seen.add(key)
            item = finding if isinstance(finding, dict) else {}
            sample = item.get('sampleMinute')
            if (type(sample) not in (int, float) or not math.isfinite(sample)
                    or not before['minute'] <= sample <= after['minute']):
                sample = None
            evidence.append({
                'id': f'E{number}.{index}', 'round': number, 'action': event['action'],
                'reportedMinute': after['minute'], 'sampleMinute': sample, 'finding': finding,
            })
    return {'evidence': evidence, 'hypotheses': session.get('hypotheses', []),
            'revision': len(session.get('hypothesisHistory', []))}


def hypothesis_update(session, data):
    current = board(session)
    if data.get('revision') != current['revision']:
        raise ValueError('诊断板已更新，请重新读取')
    items = data.get('hypotheses')
    if not isinstance(items, list) or len(items) > 6:
        raise ValueError('最多记录六项假设')
    known = {record['id'] for record in current['evidence']}
    result = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('假设格式无效')
        name, question = item.get('name'), item.get('question', '')
        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            raise ValueError('请填写假设名称，最多120字')
        if not isinstance(question, str) or len(question) > 400:
            raise ValueError('待解决问题最多400字')
        links = {}
        for key in ('supports', 'against'):
            refs = item.get(key, [])
            if not isinstance(refs, list) or any(not isinstance(ref, str) or ref not in known for ref in refs):
                raise ValueError('只能引用本局已获得的证据')
            links[key] = list(dict.fromkeys(refs))
        if set(links['supports']) & set(links['against']):
            raise ValueError('同一项证据请只选择一个方向')
        result.append({'name': name.strip(), 'question': question.strip(), **links})
    return result
