import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import server
from evaluate_playthroughs import DATASET, post, replay


def fixture_model(prompt, payload):
    if 'knownState' in payload:
        return {'label': '测试行动', 'preparationMinutes': 1,
                'procedureMinutes': 2, 'reportWaitMinutes': 1}, {}
    if 'previousState' in payload:
        action = payload['action']['description']
        if '手术血运重建' in action:
            finding = '记录处置已实施'
        elif '比较患肢灌注' in action:
            finding = '复评灌注改善，患者状态适合进入监护'
        else:
            finding = '影像证实存在血栓性动脉病变'
        return {
            **payload['previousState'], 'minute': payload['targetMinute'],
            'observations': [finding],
        }, {}
    if 'criteria' in payload:
        # Deliberately cite the treatment event as the reassessment when there
        # are only two events, to exercise the backend's ordering check.
        count = len(payload['events'])
        return {'diagnosis': 1, 'intervention': 2 if count >= 2 else None,
                'reassessment': count if count >= 2 else None}, {}
    raise AssertionError('Unexpected model call')


class PlaythroughAPI(unittest.TestCase):
    def test_treatment_requires_later_reassessment(self):
        cases = json.loads(DATASET.read_text(encoding='utf-8'))['cases']
        selected = {'direct-correct-care', 'treated-but-not-reassessed'}
        with tempfile.TemporaryDirectory(dir=server.ROOT) as directory:
            with patch.multiple(server, DB=Path(directory) / 'test.sqlite3',
                                sessions={}, model=fixture_model):
                with server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler) as httpd:
                    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
                    thread.start()
                    try:
                        url = f'http://127.0.0.1:{httpd.server_port}'
                        for case in cases:
                            if case['id'] in selected:
                                with self.subTest(case=case['id']):
                                    report = replay(url, case, judge=None)
                                    self.assertEqual(report['status'], 'pass', report)
                        session = {'id': post(url, 'start', {'patientId': 'leg-001'})['id']}
                        first = post(url, 'finish', session)
                        server.sessions.clear()
                        self.assertEqual(post(url, 'finish', session), first)
                        self.assertEqual(first['status'], 'ended_incomplete')
                        with self.assertRaisesRegex(RuntimeError, 'HTTP 409'):
                            post(url, 'plan', dict(session, action='观察'))
                    finally:
                        httpd.shutdown()
                        thread.join()


if __name__ == '__main__':
    unittest.main()
