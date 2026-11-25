import unittest

from diagnosis import board, hypothesis_update, new_findings


class DiagnosisTests(unittest.TestCase):
    def test_repeated_result_cannot_become_new_evidence(self):
        old = {'method': '问诊', 'result': '走快时 发紧'}
        duplicate = {'result': '走快时发紧', 'method': '问诊'}
        self.assertEqual(new_findings([old], [duplicate]), [])

    def test_links_reference_observed_evidence(self):
        finding = {'result': '双侧有差异', 'sampleMinute': 40}
        session = {'events': [{'action': '双侧查体', 'before': {'minute': 2},
                               'after': {'minute': 5}, 'findings': [finding]}]}
        record = board(session)['evidence'][0]
        self.assertEqual(record['id'], 'E1.1')
        self.assertEqual(record['reportedMinute'], 5)
        self.assertIsNone(record['sampleMinute'])
        item = {'name': '新假设', 'supports': [record['id']]}
        self.assertEqual(hypothesis_update(session, {'revision': 0, 'hypotheses': [item]})[0]['supports'],
                         ['E1.1'])
        with self.assertRaises(ValueError):
            hypothesis_update(session, {'revision': 0, 'hypotheses': [dict(item, supports=['E9.1'])]})
