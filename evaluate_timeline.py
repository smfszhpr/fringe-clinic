"""Live-model checks for async time, cached observations and discharge stories."""
import json
import argparse
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import server
from evaluate_playthroughs import post


def run(url, label, action=None):
    record = {'scenario': label}
    try:
        session = {'id': post(url, 'start', {'patientId': 'leg-001'})['id']}
        def complete(route, payload=None):
            start = time.monotonic()
            result = post(url, route, dict(session, **(payload or {})))
            record.setdefault('acceptSeconds', []).append(round(time.monotonic() - start, 3))
            deadline = time.monotonic() + 900
            while (result.get('job') or {}).get('status') == 'running':
                if time.monotonic() > deadline:
                    raise TimeoutError('Background job exceeded 15 minutes')
                time.sleep(1)
                result = post(url, 'simulation', session)
            if (result.get('job') or {}).get('status') == 'error':
                raise RuntimeError(result['job']['message'])
            record.setdefault('steps', []).append(result)
            return result
        if action:
            result = complete('submit', {'action': action})
            if not result['queue']:
                raise AssertionError('Action was not scheduled')
            for _ in range(20):
                if all(t['status'] == 'completed' for t in result['queue']):
                    break
                result = complete('advance')
            if any(t['status'] != 'completed' for t in result['queue']):
                raise AssertionError('Procedure/report did not finish')
            if not result['view']['observations']:
                raise AssertionError('No report delivered')
        result = complete('discharge')
        if result['outcome']['status'] != 'discharged' or not result['outcome'].get('narrative'):
            raise AssertionError('Discharge story missing')
        repeated = post(url, 'discharge', session)
        if repeated['outcome'] != result['outcome']:
            raise AssertionError('Discharge story rerolled')
        record['metrics'] = server.sessions[session['id']]['simulation']['metrics']
        expected_role = {'surgery': 'perioperative', 'pharmacology': 'pharmacology'}.get(label)
        if expected_role and not record['metrics'].get(expected_role):
            raise AssertionError('Expected specialist was not invoked')
        record['status'] = 'passed'
    except Exception as error:
        record.update(status='error', error=str(error))
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--scenario', choices=['untreated', 'observation', 'surgery', 'pharmacology'])
    parser.add_argument('--output', default='timeline-live.json')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(dir=server.ROOT) as temp:
        server.DB = Path(temp) / 'test.sqlite3'
        httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        url = f'http://127.0.0.1:{httpd.server_port}'
        cases = [('untreated', None),
                 ('observation', '在床旁检查患者右腿外观、皮温及疼痛，记录结果。'),
                 ('surgery', '在游戏中由血管团队对已知右大腿血栓性血管病变实施血运重建手术，具体方案由团队决定，资源和同意齐备。'),
                 ('pharmacology', '游戏中由临床团队根据患者情况选择镇痛药、给药途径和剂量并实际给药，具体方案由团队确定，记录药物暴露与即时感受。')]
        if args.scenario:
            cases = [case for case in cases if case[0] == args.scenario]
        try:
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = [pool.submit(run, url, *case) for case in cases]
                records = []
                for future in futures:
                    record = future.result()
                    records.append(record)
                    output = server.ROOT / 'results' / args.output
                    output.parent.mkdir(exist_ok=True)
                    output.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
                    print(record['scenario'], record['status'], record.get('error', ''), flush=True)
        finally:
            httpd.shutdown()
            httpd.server_close()
    raise SystemExit(int(any(r['status'] != 'passed' for r in records)))
