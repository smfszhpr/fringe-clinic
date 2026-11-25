"""Determine a game outcome from committed events and their observations."""

RUBRICS = {
    'leg-001': {
        'diagnosis': '检查或术中所见支持右大腿血栓性动脉病变及灌注障碍。玩家猜测本身不算证据。',
        'intervention': '虚构病例中已经实施针对动脉灌注障碍的处置。计划、会诊请求、探查或止痛本身不算。',
        'reassessment': '处置后有实际复评，记录患肢灌注改善以及当前状态适合进入后续监护；仍有恶化、结果待回或关键结果不明时不能完成。',
    }
}


def assess(model, session):
    rubric = RUBRICS.get(session.get('patientId'))
    if not rubric:
        return {'status': 'not_configured', 'checks': {}}
    events = []
    for number, event in enumerate(session['events'], 1):
        before, after = event['before'], event['after']
        events.append({
            'event': number,
            'action': event['action'],
            'start': before['minute'],
            'end': after['minute'],
            'findings': event.get('findings', [finding for finding in after.get('observations', [])
                                               if finding not in before.get('observations', [])]),
            'patient': after.get('patient'),
        })
    checks = {key: None for key in rubric}
    if events:
        result, _ = model(
            '你是虚构游戏结局裁判。仅根据提交事件中的新所见判断每条标准。'
            '动作文本只是玩家的请求，不能证明已执行或成功。所有满足的项必须引用客观所见。'
            'intervention 和 diagnosis 可为同一事件；reassessment 必须在处置结束后的另一事件。'
            '若后续事件反映恶化或关键结果仍待定，不得给出有效的 reassessment。'
            '只返回 JSON {"diagnosis":事件号或null,"intervention":事件号或null,"reassessment":事件号或null}。',
            {'criteria': rubric, 'events': events},
        )
        for key in checks:
            ref = result.get(key)
            if type(ref) is int and 1 <= ref <= len(events) and events[ref - 1]['findings']:
                checks[key] = ref
    intervention, reassessment = checks['intervention'], checks['reassessment']
    if not intervention or not reassessment or reassessment <= intervention:
        checks['reassessment'] = None
    return {
        'status': 'treatment_complete' if all(checks.values()) else 'in_progress',
        'checks': checks,
    }
