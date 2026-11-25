"""Replay complete games over HTTP and grade each turn against the dataset."""
import argparse
import json
import math
import tempfile
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import server

DATASET = server.ROOT / 'cases' / 'playthroughs.json'


def post(url, route, payload):
    request = urllib.request.Request(url + '/api/' + route,
                                     json.dumps(payload).encode(),
                                     {'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=480) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = json.load(error).get('error', error.reason)
        raise RuntimeError(f'{route}: HTTP {error.code}: {detail}') from error


def replay(url, case, judge=server.model):
    report = {'id': case['id'], 'turns': [], 'failures': []}
    failures = report['failures']
    try:
        started = post(url, 'start', {'patientId': case['patientId']})
        session = {'id': started['id']}
        minute = started['view']['minute']
        if 'initialHypothesis' in case:
            post(url, 'hypotheses', dict(session, revision=0, hypotheses=[case['initialHypothesis']]))
        for number, step in enumerate(case['turns'], 1):
            turn = {'number': number, 'action': step['action'], 'expected': step['expect']}
            report['turns'].append(turn)
            report['stage'] = f'round {number}: plan'
            planned = post(url, 'plan', dict(session, action=step['action']))
            if 'plan' not in planned:
                turn['clarification'] = planned.get('clarification')
                failures.append(f'Round {number}: planning requested clarification')
                break
            plan = planned['plan']
            turn['timing'] = plan
            report['stage'] = f'round {number}: action'
            answer = post(url, 'action', dict(session, planId=plan['id']))
            turn['result'] = answer
            if answer.get('committed') is not True:
                failures.append(f'Round {number}: action was not committed')
                break
            next_minute = answer['view']['minute']
            if plan['startMinute'] != minute or not math.isclose(next_minute, minute + plan['totalMinutes'], abs_tol=1e-8):
                failures.append(f'Round {number}: game clock disagrees with action plan')
            minute = next_minute
            if 'clues' in step or 'forbiddenClues' in step:
                actual = {item.get('clueId') for item in answer['findings'] if isinstance(item, dict)}
                for clue in set(step.get('clues', [])) - actual:
                    failures.append(f'Round {number}: missing clue {clue}')
                for clue in set(step.get('forbiddenClues', [])) & actual:
                    failures.append(f'Round {number}: premature clue {clue}')
            if 'revision' in step:
                revision = step['revision']
                board = post(url, 'diagnosis', session)
                refs = [record['id'] for record in board['evidence']
                        if record['round'] in revision['supportsRounds']
                        and isinstance(record['finding'], dict)
                        and record['finding'].get('clueId') in revision['supportsClues']]
                if not refs:
                    failures.append(f'Round {number}: no evidence to support hypothesis revision')
                previous = dict(board['hypotheses'][0], against=refs)
                revised = {'name': revision['name'], 'question': revision['question'], 'supports': refs}
                turn['diagnosis'] = post(url, 'hypotheses', dict(
                    session, revision=board['revision'], hypotheses=[previous, revised]))
            if 'outcome' in step:
                report['stage'] = f'round {number}: outcome'
                checkpoint = post(url, 'outcome', session)
                turn['outcome'] = checkpoint
                if checkpoint['status'] != step['outcome']:
                    failures.append(f'Round {number}: expected {step["outcome"]}, got {checkpoint["status"]}')
        report['stage'] = 'finish'
        final = post(url, 'finish', session)
        report['expectedFinal'] = case['expectedFinal']
        report['final'] = final
        if final['status'] != case['expectedFinal']:
            failures.append(f'Expected {case["expectedFinal"]}, got {final["status"]}')
        for key, expected in case.get('expectedChecks', {}).items():
            actual = final['checks'].get(key)
            if actual != expected:
                failures.append(f'Final check {key}: expected event {expected}, got {actual}')
        if judge and len(report['turns']) == len(case['turns']) and all(
            turn.get('result', {}).get('committed') for turn in report['turns']
        ):
            report['stage'] = 'semantic grading'
            evidence = []
            for turn in report['turns']:
                result = turn['result']
                evidence.append({
                    'number': turn['number'], 'action': turn['action'], 'expected': turn['expected'],
                    'result': {'minute': result['view']['minute'], 'patient': result['view']['patient'],
                               'findings': result['findings'], 'uncertainties': result['view']['uncertainties']},
                })
            grading, _ = judge(
                '你是独立提示词的游戏评审。逐轮对比 expected 和 result，'
                '以新检查所见 findings、患者视角和已执行行动为准，不把玩家请求当结果。'
                '每轮都返回一个编号，JSON {"turns":[{"number":1,"pass":true,"reason":"..."}]}。',
                {'turns': evidence},
            )
            report['semanticGrade'] = grading
            verdicts = grading.get('turns', [])
            if sorted(item['number'] for item in verdicts) != list(range(1, len(report['turns']) + 1)):
                raise ValueError('语义评审没有逐轮给出结果')
            failures.extend(f'Round {item["number"]}: {item.get("reason", "")}'
                            for item in verdicts if item.get('pass') is not True)
        report['status'] = 'fail' if failures else 'pass'
        report.pop('stage', None)
    except Exception as error:
        report.update(status='error', error=str(error))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', help='Existing demo URL. Default: start an isolated local server.')
    parser.add_argument('--case', action='append', help='Case ID, repeatable.')
    parser.add_argument('--jobs', type=int, default=1, help='Concurrent independent game sessions.')
    parser.add_argument('--output', type=Path, default=server.ROOT / 'results' / 'playthroughs.json')
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error('--jobs must be at least 1')
    cases = json.loads(DATASET.read_text(encoding='utf-8'))['cases']
    if args.case:
        if unknown := set(args.case) - {case['id'] for case in cases}:
            parser.error(f'Unknown case IDs: {sorted(unknown)}')
        cases = [case for case in cases if case['id'] in args.case]

    reports = []
    with tempfile.TemporaryDirectory(dir=server.ROOT) as temporary:
        httpd = None
        if not args.url:
            server.DB = Path(temporary) / 'eval.sqlite3'
            httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
        url = args.url.rstrip('/') if args.url else f'http://127.0.0.1:{httpd.server_port}'
        try:
            with ThreadPoolExecutor(max_workers=args.jobs) as pool:
                jobs = {pool.submit(replay, url, case): index for index, case in enumerate(cases)}
                completed = {}
                for job in as_completed(jobs):
                    report = job.result()
                    completed[jobs[job]] = report
                    reports = [completed[index] for index in sorted(completed)]
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    args.output.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding='utf-8')
                    print(report['id'], report['status'], flush=True)
        finally:
            if httpd:
                httpd.shutdown()
                httpd.server_close()
    print(' '.join(f'{status}={sum(r["status"] == status for r in reports)}'
                   for status in ('pass', 'fail', 'error')), flush=True)
    return int(any(report['status'] != 'pass' for report in reports))


if __name__ == '__main__':
    raise SystemExit(main())
