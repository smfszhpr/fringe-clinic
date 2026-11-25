import copy
import unittest
import threading
from simulation import launch
from simulation import schedule, advance, discharge, runtime, public_state


class TimelineTests(unittest.TestCase):
    def setUp(self):
        self.session = {'state': {'minute': 0, 'body': {'hidden': 'disease'},
                                 'patient': {'speech': '疼痛'}, 'appearance': {},
                                 'observations': []}, 'events': []}
        self.case = {'hidden': {'diagnosis': 'disease'}, 'chart': {}, 'lifeContext': {'residence': '独居'}}
        self.calls = []

    def model(self, prompt, payload):
        self.calls.append(payload)
        if 'knownState' in payload:
            surgery = payload['action'] == 'surgery'
            return {'kind': 'intervention' if surgery else 'observation',
                    'bedside': [], 'label': payload['action'], 'preparation': 2, 'procedure': 8,
                    'reportDelay': 10, 'specialties': ['perioperative'] if surgery else []}, {}
        if 'assignedSpecialty' in payload:
            return {'process': '术后恢复', 'events': [
                {'id': 'risk', 'onsetMinute': 30, 'manifestMinute': 60,
                 'probability': 1, 'mechanism': '测试延迟事件'}]}, {}
        if 'requestedMinutes' in payload:
            return {'nodes': [{'minute': t, 'body': {'at': t},
                               'patient': {'speech': '当前感受'}, 'appearance': {}, 'uncertainties': []}
                              for t in payload['requestedMinutes']]}, {}
        if 'sampledState' in payload:
            return {'findings': [{'method': '检查', 'result': str(payload['sampledState']['body'])}],
                    'speech': ''}, {}
        if 'dischargeMinute' in payload:
            return {'title': '后来', 'narrative': '患者出院后在家休养。', 'assumptions': []}, {}
        raise AssertionError(payload)

    def test_sample_time_and_forecast_reuse(self):
        schedule(self.model, self.session, self.case, 'test')
        rt = runtime(self.session)
        self.assertEqual(self.session['state']['minute'], 0)
        self.assertEqual(rt['metrics']['trajectory'], 1)
        advance(self.model, self.session, self.case)  # sample at 10
        self.assertEqual(self.session['state']['minute'], 10)
        self.assertEqual(self.session['state']['observations'], [])
        advance(self.model, self.session, self.case)  # cached 15
        advance(self.model, self.session, self.case)  # report at 20
        self.assertEqual(self.session['state']['observations'][0]['sampleMinute'], 10)
        observation = next(p for p in self.calls if 'sampledState' in p)
        self.assertEqual(observation['sampledState']['body']['at'], 10)
        self.assertEqual(rt['metrics']['trajectory'], 1)
        self.assertNotIn('forecast', public_state(self.session))

    def test_intervention_repredicts_and_risk_is_persistent(self):
        schedule(self.model, self.session, self.case, 'test')
        schedule(self.model, self.session, self.case, 'surgery')
        rt = runtime(self.session)
        self.assertEqual(rt['metrics']['trajectory'], 2)
        risks = copy.deepcopy(rt['processes'])
        self.assertTrue(risks[0]['events'][0]['selected'])
        advance(self.model, self.session, self.case)
        self.assertEqual(rt['processes'], risks)

    def test_connected_monitor_record_and_disconnect(self):
        def devices(prompt, payload):
            if 'channels' in payload:
                return {'readings': [{'id': 'hr', 'reading': 70 + payload['state']['minute'],
                                      'unit': 'bpm'}]}, {}
            result, usage = self.model(prompt, payload)
            if 'knownState' in payload:
                op = payload['action']
                result['bedside'] = [{'op': op, 'id': 'hr', 'label': '心率'}]
            if payload.get('requestedRecords'):
                result['recordings'] = [{'id': 'hr', 'reading': 90, 'unit': 'bpm'}]
            return result, usage

        state = self.session['state']
        schedule(devices, self.session, self.case, 'connect')
        self.assertFalse(state.get('devices'))
        advance(devices, self.session, self.case, 10)
        self.assertEqual(state['devices'][0]['reading'], 80)
        schedule(devices, self.session, self.case, 'record')
        advance(devices, self.session, self.case, 20)
        self.assertFalse(state.get('bedsideRecords'))
        advance(devices, self.session, self.case, 30)
        self.assertEqual(state['bedsideRecords'][0]['sampleMinute'], 20)
        self.assertEqual(state['devices'][0]['reading'], 100)
        schedule(devices, self.session, self.case, 'disconnect')
        advance(devices, self.session, self.case, 40)
        self.assertFalse(state['devices'])
        self.assertEqual(public_state(self.session)['view']['bedsideRecords'][0]['reading'], 90)

    def test_restore_legacy_monitor_at_current_minute(self):
        from simulation import needs_bedside_restore, restore_bedside
        schedule(self.model, self.session, self.case, 'ecg')
        advance(self.model, self.session, self.case, 20)
        task = runtime(self.session)['queue'][0]
        del task['bedside']
        events = copy.deepcopy(self.session['events'])

        def recover(prompt, payload):
            if 'history' in payload:
                self.assertEqual(payload['history'][0]['findings'], events[0]['findings'])
                return {'tasks': [{'taskId': task['id'], 'bedside': [
                    {'op': 'connect', 'id': 'ecg', 'label': '心电监护'}]}]}, {}
            return {'readings': [{'id': 'ecg', 'reading': 86, 'unit': '次/分',
                                  'signal': {'rate': 86, 'shape': 'regular'}}]}, {}

        self.assertTrue(needs_bedside_restore(self.session))
        restore_bedside(recover, self.session)
        device = public_state(self.session)['view']['devices'][0]
        self.assertEqual((device['sampleMinute'], device['signal']['rate']), (20, 86))
        self.assertEqual(self.session['events'], events)
        self.assertFalse(needs_bedside_restore(self.session))

    def test_discharge_uses_life_and_internal_state(self):
        discharge(self.model, self.session, self.case)
        self.assertEqual(self.session['outcome']['status'], 'discharged')
        payload = self.calls[-1]
        self.assertEqual(payload['lifeContext']['residence'], '独居')
        self.assertEqual(payload['actualState']['body']['hidden'], 'disease')
        self.assertTrue(self.session['outcome']['narrative'])

    def test_failed_observation_does_not_advance_or_lose_sample(self):
        schedule(self.model, self.session, self.case, 'test')
        advance(self.model, self.session, self.case)
        advance(self.model, self.session, self.case)
        original = copy.deepcopy(self.session['state'])
        self.session['lock'] = threading.Lock()
        finished = threading.Event()
        def save(sid, snapshot):
            if (snapshot['simulation'].get('job') or {}).get('status') == 'error':
                finished.set()
        def fail(prompt, payload):
            raise RuntimeError('test transport failure')
        with self.session['lock']:
            launch('test', self.session, self.case, 'advance', {}, fail, save)
        self.assertTrue(finished.wait(5))
        self.assertEqual(self.session['state'], original)
        self.assertIn('sample', self.session['simulation']['queue'][0])

    def test_new_observation_does_not_reroll_existing_forecast(self):
        schedule(self.model, self.session, self.case, 'test')
        advance(self.model, self.session, self.case)  # minute 10
        schedule(self.model, self.session, self.case, 'test')  # report at 30, not a cached node
        advance(self.model, self.session, self.case, 30)
        self.assertEqual(runtime(self.session)['metrics']['trajectory'], 1)
        self.assertTrue(all(t['status'] == 'completed' for t in runtime(self.session)['queue']))


if __name__ == '__main__':
    unittest.main()
