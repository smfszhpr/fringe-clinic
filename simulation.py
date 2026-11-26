"""Event-time simulation with private forecast caches and specialist proposals."""
import copy
import hashlib
import math
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from clinic_setting import CLINIC_SETTING
from diagnosis import new_findings

POOL = ThreadPoolExecutor(max_workers=8)
SPECIALISTS = ThreadPoolExecutor(max_workers=4)
PUBLIC = ('minute', 'patient', 'appearance', 'observations', 'devices', 'bedsideRecords', 'auscultation', 'uncertainties')

ROUTER = '''你负责虚构诊疗游戏的行动理解与调度。根据行动确定 observation/intervention/wait。
普通问诊和无创检测为observation；会影响身体的检查、用药、操作为intervention。
明确等待为wait。只在关键意图不明时clarification提问。委托团队的细节可写入假设。
耗时区分准备、操作和报告等待，单位分钟。specialties只选必要的pharmacology/perioperative，
其他干预由身体预测器直接处理。只输出JSON
{"kind":"observation","label":"...","preparation":1,"procedure":5,"reportDelay":10,
"specialties":[],"assumptions":[],"clarification":null}。等待只用procedure表达指定时长。
估计属于游戏模拟，不声称真实医疗标准。'''

SPECIALIST = '''你负责虚构患者的专项事件推演。根据assignedSpecialty、患者真实内部状态、
既往暴露和该次干预，提出药理过程或围术期恢复过程。药理包括起效、持续、相互作用和副作用；
围术期包括恢复、潜在延迟并发症及其可观察时间。不要生成临床操作教程。
events每项给出稳定id、绝对onsetMinute、manifestMinute、概率probability(0到1)、机制mechanism。
概率是模拟假设。无依据时减少精确性并说明假设。不能为了戏剧效果强行发生并发症。
只输出JSON {"process":"...","assumptions":[],"events":[
{"id":"...","onsetMinute":90,"manifestMinute":150,"probability":0.1,"mechanism":"..."}]}。
事件不得早于action.effectMinute。你只提出候选，最终是否发生由程序采样。'''

FORECAST = '''你是虚构患者身体轨迹预测器。基于currentState、固定病例事实、已实际发生的历史、
已排定的interventions和specialistProcesses，生成requestedMinutes每个绝对时点相对前一节点的状态增量。
每个干预只能在effectMinute后起效，完整处置于operationEnd完成；直到开始前仅是计划。
药理与术后影响按时间连续发展，保留原有疾病、药物暴露及因果。selected=false的潜在事件不能发生。
selected=true事件考虑其onsetMinute和manifestMinute；后续实际干预可改变其进展，但不能重抽概率。
患者主观感受与appearance只含当前可感知的变化，未显现内部事件不可泄露。
不得提前返回检查结果。所有diagnosticClues是隐藏的观测来源。不要预写玩家下一步操作。
这些节点定义离散状态，两个节点之间保持前一节点状态；重要变化应在请求节点合理体现，
表达模拟近似，避免声称精确连续生理曲线。
只输出JSON {"nodes":[{"minute":数字,"bodyPatch":只含变化的身体字段对象,
"patient":{"speech":"本人可感知的感受","symptoms":[]},"appearance":{"injuries":[],"posture":"","breathing":""},
"uncertainties":[]}]}。每个requestedMinutes必须恰好一个节点，按时间排序。
bodyPatch只写变化，空对象表示沿用，列表若改变则给完整新列表。
patient/appearance/uncertainties未变化可省略。全答控制在约1500中文字，避免重复病史和病例。'''

OBSERVE = '''你负责虚构病例的观察与表达，读取采集时的状态，不能改变病情。
根据requestedAction返回实际能观察到的结果。患者只能说自身感受和记得的病史。
影像/化验等只能报告其可检测内容。不得直接泄露隐藏病因或未来事件。
保留病例diagnosticClues的信息边界。结果属于sampleMinute，报告于reportMinute回传。
只输出JSON {"findings":[{"method":"...","result":"...","sampleMinute":数字}],
"speech":"患者自然表达或空字符串"}。问诊结果也记录在findings。'''

ROUTER += CLINIC_SETTING + """
目标或术式清楚的手术按intervention排程，并调度perioperative；有风险、证据不足仍按玩家明确决策推演。
仅当目标歧义会改变实际处置时，用clarification问一个决策问题。例如“切掉大脑”没有明确治疗靶点，
可问想解除颅内压力还是清除已发现的病灶，不擅自替换术式、不索要操作步骤。
仅要求外援时请玩家指定本地行动；已明确本地术式则无需另请团队确认。"""
SPECIALIST += CLINIC_SETTING
FORECAST += CLINIC_SETTING
OBSERVE += CLINIC_SETTING + '报告只写实际所见、测量限制和未解问题，不开具会诊或转院建议。'
ROUTER += """
额外输出bedside数组，列出本次实际使用的床旁设备，每个测量通道一项
{op:connect|disconnect|record, id:稳定的英文通道标识, label:中文名称}。
持续监护、连接后留置使用connect，撤机用disconnect。一次性检测或记录用record。
结合原始请求判断持续与单次，复用knownState.devices已有的id。普通问诊等返回空数组。
"""
OBSERVE += """
如果requestedRecords非空，逐项返回recordings数组，每项为
{id,reading:仪器测得的数值或简短文字,unit,signal:null或{shape:"regular",rate:数值}}。
仅在心电节律可用规则示意波形表达时给signal。根据采样时状态生成读数，与findings保持一致。
sampledState.devices同一时点的实测读数是已确定事实，应沿用。
"""
MONITOR = """你负责虚构病人已连接床旁设备的读数投影。根据当前时点的身体状态与此前读数，
只输出指定channels可测到的读数，不能泄露隐藏诊断。返回JSON对象含readings数组，每个通道恰好一项
{id,reading:仪器测得的数值或简短文字,unit:单位字符串,signal:null或{shape:"regular",rate:数值}}。
只有心电通道且节律适合用规则心搏波形示意时给signal，rate与显示的心率一致。
信号不可取得时如实写明。不要将之前采样的数值冒充当前实测数值。"""


def bedside_values(items, channels, minute):
    if not isinstance(items, list) or len(items) != len(channels):
        raise ValueError('设备读数数量与通道不符')
    values = []
    for channel in channels:
        item = next((v for v in items if isinstance(v, dict) and v.get('id') == channel['id']), {})
        reading = item.get('reading')
        if type(reading) not in (str, float, int) or not str(reading).strip():
            raise ValueError('设备缺少有效读数')
        value = {**channel, 'reading': reading, 'unit': str(item.get('unit') or ''), 'sampleMinute': minute}
        signal = item.get('signal')
        if isinstance(signal, dict) and signal.get('shape') == 'regular' and number(signal.get('rate')) > 0:
            value['signal'] = {'rate': signal['rate'], 'shape': 'regular'}
        values.append(value)
    return values


def bedside_changes(changes):
    if not isinstance(changes, list) or any(not isinstance(c, dict) or
            c.get('op') not in ('record', 'connect', 'disconnect') or
            not all(isinstance(c.get(k), str) and c[k].strip() for k in ('id', 'label'))
            for c in changes):
        raise ValueError('设备操作格式无效')
    return [{k: c[k] for k in ('id', 'label', 'op')} for c in changes]


def restore_bedside(model, session):
    rt = runtime(session)
    tasks = sorted((t for t in rt['queue'] if t['status'] in ('completed', 'awaiting_report')),
                   key=lambda t: t['sampleMinute'])
    old = [t for t in tasks if 'bedside' not in t]
    reports = {(e.get('action'), e.get('timing', {}).get('sampleMinute')): e.get('findings', [])
               for e in session['events']}
    history = [{'taskId': t['id'], 'action': t['action'], 'minute': t['sampleMinute'],
                'findings': reports.get((t['action'], t['sampleMinute']), [])} for t in old]
    result = call(model, rt, 'deviceHistory', ROUTER + """
现在输入是旧存档中实际已执行的行动和已回报所见。只恢复设备操作，不重新诊疗或估计耗时。
输出 {"tasks":[{"taskId":"与输入一致","bedside":[{"op":"connect","id":"稳定通道名","label":"设备及测量项目中文名"}]}]}。
每个输入taskId恰好一项。持续心电监护必须是connect，单次记录是record；没有设备的项目用空数组。
同一设备的接通与撤除使用同一id。只识别已执行的动作，建议、计划和报告里提到的设备不算。""",
                  {'history': history})
    entries = result.get('tasks')
    if not isinstance(entries, list) or len(entries) != len(old):
        raise ValueError('旧记录设备操作数量不符')
    by_id = {item['taskId']: bedside_changes(item.get('bedside')) for item in entries}
    for t in old:
        t['bedside'] = by_id[t['id']]
    for t in tasks:
        apply_connections(session['state'], t['bedside'])
    measure_monitors(model, rt, session['state'])
    rt['notice'] = '已按完成的操作恢复床旁设备。'


def needs_bedside_restore(session):
    rt = runtime(session)
    job = rt.get('job') or {}
    return (not session.get('outcome') and job.get('status') != 'running'
            and not (job.get('operation') == 'restoreBedside' and job.get('status') == 'error')
            and any('bedside' not in t and t['status'] in ('completed', 'awaiting_report') for t in rt['queue']))


def apply_connections(state, changes):
    devices = list(state.get('devices') or [])
    for change in changes:
        if change['op'] == 'record':
            continue
        devices = [d for d in devices if d.get('id', d.get('kind')) != change['id']]
        if change['op'] == 'connect':
            devices.append({'id': change['id'], 'label': change['label']})
    state['devices'] = devices


def measure_monitors(model, rt, state):
    channels = [{'id': d.get('id', d.get('kind')), 'label': d.get('label') or d.get('kind')}
                for d in state.get('devices') or [] if d.get('id') or d.get('kind')]
    if channels:
        result = call(model, rt, 'monitor', MONITOR, {'state': state, 'channels': channels})
        state['devices'] = bedside_values(result.get('readings'), channels, state['minute'])


ENDING = '''你是虚构医学游戏的出院后日谈作者。玩家已经结束本次诊疗。
根据离院时的真实身体状态、已生效治疗、药物暴露、已采样潜在事件、未解决疾病和生活情况，
生成一段有时间顺序的后续人生叙事。可以公开内部病因和因果关系。
正文应明确解释内部病因及其如何导致结局，不要继续对读者隐瞒诊断。
不能编造本次医生说过的话、检查或处置。未记录的医嘱不应写成实际已开具。
考虑生活习惯、工作、独居或照护、服药依从性、行动能力与慢性后遗症。
只把真正执行的动作计为治疗，待开始的操作不会在出院后自动实施。
可能恢复、带病生活、残疾或死亡，必须由病程支持，不为了戏剧性固定悲剧或奇迹。
未来的生活细节可以合理创作，并在assumptions标明虚构补充。
后日谈不引入再次住院、急诊抢救、新手术或新医疗团队补救。只依据本次实际处置、已有药物方案、
居家照护与生活环境发展后续。不要凭空安排医院后来治好患者。
不生成下一轮行动指令。避免血腥猎奇描写。
只输出JSON {"title":"...","narrative":"300至700字的中文后日谈",
"assumptions":["..."]}。'''


def number(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError('模型返回无效时间或概率')
    return float(value)


def merge_fields(previous, changes):
    merged = copy.deepcopy(previous)
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_fields(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def runtime(session):
    return session.setdefault('simulation', {
        'seed': uuid.uuid4().hex, 'revision': 0, 'queue': [], 'forecast': [],
        'processes': [], 'metrics': {}, 'job': None,
    })


def public_state(session):
    rt = runtime(session)
    return {
        'view': {key: session['state'].get(key) for key in PUBLIC},
        'queue': [{key: task.get(key) for key in
                   ('id', 'label', 'kind', 'sampleMinute', 'readyMinute', 'status')}
                  for task in rt['queue']],
        'job': rt['job'], 'revision': rt['revision'],
        'outcome': session.get('outcome'),
    }


def call(model, rt, role, prompt, payload):
    rt['metrics'][role] = rt['metrics'].get(role, 0) + 1
    result, _ = model(prompt, payload)
    return result


def forecast(model, session, case, required=()):
    rt = runtime(session)
    now = session['state']['minute']
    times = {now + 15, now + 60, now + 180, now + 360}
    times.update(t for t in required if t > now)
    times.update(t[k] for t in rt['queue'] if t['status'] != 'completed'
                 for k in ('sampleMinute', 'readyMinute') if t[k] > now)
    # Split large sets so one answer remains bounded.
    requested = sorted(times)[:8]
    result = call(model, rt, 'trajectory', FORECAST, {
        'currentState': session['state'], 'fixedFacts': case['hidden'],
        'diagnosticClues': case.get('clues', []), 'requestedMinutes': requested,
        'interventions': [t for t in rt['queue'] if t['kind'] == 'intervention'],
        'specialistProcesses': rt['processes'],
    })
    nodes = result.get('nodes')
    if not isinstance(nodes, list) or len(nodes) != len(requested):
        raise ValueError('预测节点数量不正确')
    previous = copy.deepcopy(session['state'])
    expanded = []
    for node, minute in zip(nodes, requested):
        if node.get('minute') != minute or not isinstance(node.get('bodyPatch', node.get('body')), dict):
            raise ValueError('预测节点时间或身体状态不正确')
        previous['body'] = merge_fields(previous['body'], node.get('bodyPatch', node.get('body')))
        for key in ('patient', 'appearance', 'uncertainties'):
            if key in node:
                previous[key] = copy.deepcopy(node[key])
        previous['minute'] = minute
        if not isinstance(previous.get('patient'), dict) or not isinstance(previous.get('appearance'), dict):
            raise ValueError('预测节点缺少患者可见表现')
        expanded.append({k: copy.deepcopy(previous.get(k))
                         for k in ('minute', 'body', 'patient', 'appearance', 'uncertainties')})
    rt['forecast'] = expanded


def specialist_proposals(model, session, task):
    rt = runtime(session)
    roles = task['specialties']
    futures = [SPECIALISTS.submit(model, SPECIALIST, {
        'assignedSpecialty': role, 'state': session['state'], 'action': task,
        'existingProcesses': rt['processes'],
    }) for role in roles]
    for role, future in zip(roles, futures):
        result, _ = future.result()
        rt['metrics'][role] = rt['metrics'].get(role, 0) + 1
        for event in result.get('events', []):
            onset, manifest = number(event['onsetMinute']), number(event['manifestMinute'])
            probability = number(event['probability'])
            if onset < task['effectMinute'] or manifest < onset or probability > 1:
                raise ValueError('专项事件时序或概率不正确')
            key = f"{rt['seed']}:{task['id']}:{role}:{event['id']}"
            draw = int(hashlib.sha256(key.encode()).hexdigest()[:13], 16) / 16**13
            event['selected'] = draw < probability
        rt['processes'].append({'actionId': task['id'], 'specialty': role, **result})


def schedule(model, session, case, action):
    rt = runtime(session)
    result = call(model, rt, 'dispatch', ROUTER, {
        'action': action, 'knownState': {k: session['state'].get(k) for k in PUBLIC},
    })
    if result.get('clarification'):
        rt['notice'] = str(result['clarification'])
        return
    kind = result.get('kind')
    if kind not in ('observation', 'intervention', 'wait'):
        raise ValueError('行动类型无效')
    prep, procedure, delay = [number(result[k]) for k in ('preparation', 'procedure', 'reportDelay')]
    if not 0 < prep + procedure + delay <= 10080:
        raise ValueError('行动耗时超出范围')
    roles = result.get('specialties', [])
    if not isinstance(roles, list) or any(r not in ('pharmacology', 'perioperative') for r in roles):
        raise ValueError('专项调度无效')
    now = session['state']['minute']
    if kind == 'wait':
        advance(model, session, case, now + prep + procedure + delay)
        return
    task = {
        'id': uuid.uuid4().hex, 'label': result.get('label', action),
        'action': action, 'kind': kind, 'specialties': list(dict.fromkeys(roles)),
        'startMinute': now, 'effectMinute': now + prep,
        'operationEnd': now + prep + procedure, 'sampleMinute': now + prep + procedure,
        'readyMinute': now + prep + procedure + delay, 'status': 'scheduled',
    }
    changes = bedside_changes(result.get('bedside'))
    task['bedside'] = changes
    rt['queue'].append(task)
    if task['sampleMinute'] == now:
        apply_connections(session['state'], changes)
        if changes:
            measure_monitors(model, rt, session['state'])
        task['sample'] = copy.deepcopy(session['state'])
        task['status'] = 'awaiting_report'
    if kind == 'intervention':
        specialist_proposals(model, session, task)
        forecast(model, session, case)
    elif not rt['forecast']:
        forecast(model, session, case)
    rt['notice'] = '已安排 ' + task['label']


def advance(model, session, case, target=None):
    rt = runtime(session)
    now = session['state']['minute']
    available = [n['minute'] for n in rt['forecast'] if n['minute'] > now]
    available += [t[k] for t in rt['queue'] if t['status'] != 'completed'
                  for k in ('sampleMinute', 'readyMinute') if t[k] > now]
    if target is None:
        if not available:
            forecast(model, session, case)
            available = [n['minute'] for n in rt['forecast']]
        target = min(available)
    if target <= now or target - now > 10080:
        raise ValueError('推进时间无效')
    while session['state']['minute'] < target:
        current = session['state']['minute']
        boundaries = [target]
        boundaries += [t[k] for t in rt['queue'] if t['status'] != 'completed'
                       for k in ('sampleMinute', 'readyMinute') if current < t[k] < target]
        step = min(boundaries)
        cached = [n['minute'] for n in rt['forecast'] if current < n['minute'] < step]
        if cached:
            step = min(cached)
        matching = next((n for n in rt['forecast'] if n['minute'] == step), None)
        if matching is None and rt['forecast'] and step < rt['forecast'][-1]['minute']:
            # Read the existing piecewise-constant forecast without rerolling it.
            matching = {k: copy.deepcopy(session['state'].get(k))
                        for k in ('body', 'patient', 'appearance', 'uncertainties')}
            matching['minute'] = step
        if matching is None:
            forecast(model, session, case, [step])
            matching = next((n for n in rt['forecast'] if n['minute'] == step), None)
            if matching is None:
                step = rt['forecast'][0]['minute']
                matching = rt['forecast'][0]
        before = copy.deepcopy(session['state'])
        session['state'].update(copy.deepcopy(matching))
        rt['forecast'] = [n for n in rt['forecast'] if n['minute'] > step]
        sampling = [t for t in rt['queue'] if t['status'] == 'scheduled' and t['sampleMinute'] == step]
        for task in sampling:
            apply_connections(session['state'], task.get('bedside', []))
        if sampling or step == target:
            measure_monitors(model, rt, session['state'])
        for task in rt['queue']:
            if task['status'] == 'completed':
                continue
            if 'sample' not in task and step >= task['sampleMinute']:
                task['sample'] = copy.deepcopy(session['state'])
                task['status'] = 'awaiting_report'
            if step >= task['readyMinute']:
                channels = [{k: change[k] for k in ('id', 'label')}
                            for change in task.get('bedside', []) if change['op'] == 'record']
                report = call(model, rt, 'observation', OBSERVE, {
                    'requestedRecords': channels, 'requestedAction': task['action'], 'sampleMinute': task['sampleMinute'],
                    'reportMinute': step, 'sampledState': task['sample'],
                    'patientHistory': session.get('patientKnowledge', []),
                    'diagnosticClues': case.get('clues', []),
                })
                if channels:
                    values = bedside_values(report.get('recordings'), channels, task['sampleMinute'])
                    session['state'].setdefault('bedsideRecords', []).extend(values)
                proposed = report.get('findings')
                if not isinstance(proposed, list):
                    raise ValueError('观察反馈缺少所见列表')
                for finding in proposed:
                    if isinstance(finding, dict):
                        finding['sampleMinute'] = task['sampleMinute']
                findings = new_findings(session['state'].get('observations', []), proposed)
                session['state'].setdefault('observations', []).extend(findings)
                if report.get('speech'):
                    session['state']['patient']['speech'] = report['speech']
                # Preserve sample-time provenance even if another action completed in between.
                event_before = copy.deepcopy(before)
                event_before['minute'] = min(before['minute'], task['sampleMinute'])
                session['events'].append({
                    'action': task['action'], 'before': event_before,
                    'after': copy.deepcopy(session['state']), 'findings': findings,
                    'timing': {k: task[k] for k in ('startMinute', 'sampleMinute', 'readyMinute')},
                })
                task['status'] = 'completed'
                task.pop('sample', None)
    rt['notice'] = '病程推进至 ' + str(session['state']['minute']) + ' 分钟'


def discharge(model, session, case):
    rt = runtime(session)
    now = session['state']['minute']
    started = {t['id'] for t in rt['queue']
               if t['kind'] == 'intervention' and t['effectMinute'] <= now}
    ending = call(model, rt, 'ending', ENDING, {
        'dischargeMinute': now, 'actualState': session['state'],
        'fixedCaseFacts': case['hidden'], 'lifeContext': case.get('lifeContext', {}),
        'chart': case['chart'], 'events': session['events'],
        'specialistProcesses': [p for p in rt['processes'] if p['actionId'] in started],
        'pendingTasks': [{k: t[k] for k in ('action', 'kind', 'effectMinute', 'operationEnd', 'status')}
                         for t in rt['queue'] if t['status'] != 'completed'],
    })
    if not isinstance(ending.get('narrative'), str) or not ending['narrative'].strip():
        raise ValueError('后日谈尚未生成完整文本')
    session['outcome'] = {
        **ending, 'status': 'discharged', 'minute': now,
        'diagnosisReveal': case['hidden'].get('primaryCondition', ''),
    }
    rt['forecast'] = []
    for task in rt['queue']:
        if task['status'] != 'completed':
            task['status'] = 'closed_at_discharge'
    session.pop('plan', None)


def launch(sid, session, case, operation, data, model, persist):
    """Caller holds session lock. Compute on a copy, commit atomically."""
    rt = runtime(session)
    request_id = data.get('requestId')
    previous_job = rt.get('job') or {}
    if request_id and previous_job.get('requestId') == request_id and previous_job.get('status') in ('running', 'complete'):
        return public_state(session)
    if session.get('outcome'):
        raise ValueError('本局已结束')
    if rt['job'] and rt['job']['status'] == 'running':
        raise ValueError('后台正在处理本次任务，请稍候')
    job = {'id': uuid.uuid4().hex, 'status': 'running', 'operation': operation, 'requestId': request_id}
    rt['job'] = job
    persist(sid, session)
    snapshot = copy.deepcopy({k: v for k, v in session.items() if k != 'lock'})

    def work():
        try:
            if operation == 'restoreBedside':
                restore_bedside(model, snapshot)
            elif operation == 'submit':
                schedule(model, snapshot, case, str(data['action']))
            elif operation == 'advance':
                advance(model, snapshot, case)
            elif operation == 'discharge':
                discharge(model, snapshot, case)
            else:
                raise ValueError('未知操作')
            updated = runtime(snapshot)
            updated['revision'] += 1
            updated['job'] = {**job, 'status': 'complete', 'message': updated.get('notice', '')}
            with session['lock']:
                # Diagnostic board changes made while computing remain current.
                for key in ('hypotheses', 'hypothesisHistory'):
                    if key in session:
                        snapshot[key] = session[key]
                persist(sid, snapshot)
                session.update(snapshot)
        except Exception as error:
            with session['lock']:
                rt['job'] = {**job, 'status': 'error', 'message': str(error)[:300]}
                persist(sid, session)
    POOL.submit(work)
    return public_state(session)
