import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from incident_context_index import build_index, nanoseconds, phase


class ContextIndexTests(unittest.TestCase):
    def setUp(self):
        self.trigger = {'first_bad_at': '2026-11-17T22:18:36Z',
                        'confirmed_at': '2026-11-17T22:18:46Z'}

    def test_timezones_and_nanoseconds(self):
        self.assertEqual(nanoseconds('2026-11-18T01:18:33.293780196+03:00'),
                         nanoseconds('2026-11-17T22:18:33.293780196Z'))
        self.assertEqual(nanoseconds('2026-11-17T22:18:33.000000002Z') -
                         nanoseconds('2026-11-17T22:18:33.000000001Z'), 1)

    def test_missing_or_naive_time_stays_unknown(self):
        for value in (None, '2026-11-17T22:18:33', 'not a time', '2026-02-31T22:18:33Z'):
            self.assertIsNone(nanoseconds(value))
            self.assertEqual(phase(value, value, **dict(first_bad=self.trigger['first_bad_at'], confirmed=self.trigger['confirmed_at'])), 'unknown')

    def test_interval_crossing_and_exact_boundaries(self):
        first, confirmed = self.trigger.values()
        self.assertEqual(phase('2026-11-17T22:18:35Z', first, first, confirmed), 'crosses_boundary')
        self.assertEqual(phase(first, first, first, confirmed), 'between_first_failure_and_confirmation')
        self.assertEqual(phase(confirmed, confirmed, first, confirmed), 'at_or_after_confirmation')
        self.assertEqual(phase(confirmed, first, first, confirmed), 'unknown')

    def test_success_before_failure_does_not_transfer_status_or_connection(self):
        record = json.dumps({'event': 'http_request', 'method': 'GET', 'path': '/health/ready', 'status_code': 200})
        observations = [
            {'id': 'HTTP-1', 'url': 'http://localhost/health/live', 'http_status': None,
             'error': '[Errno 111] Connection refused', 'started_at': '2026-11-17T22:18:46Z',
             'collected_at': '2026-11-17T22:18:47Z'},
            {'id': 'API', 'text': 'api-1 | 2026-11-17T22:18:31Z ' + record + '\n'
             'api-1 | 2026-11-17T22:18:33.293780196Z INFO: Finished server process [1]\n'
             'traceback line without timestamp'},
            {'id': 'DB', 'text': 'db-1 | 2026-11-17T22:18:33Z connection refused'}]
        original = copy.deepcopy(observations)
        entries = build_index(self.trigger, observations)['entries']
        self.assertEqual(entries[0]['connection'], 'monitor_to_api')
        self.assertIsNone(entries[0]['http_status'])
        self.assertEqual(entries[0]['phase'], 'at_or_after_confirmation')
        self.assertEqual(entries[1]['phase'], 'before_first_failed_check')
        self.assertEqual(entries[1]['path'], '/health/ready')
        self.assertEqual(entries[1]['connection'], 'client_to_api')
        self.assertEqual(entries[2]['connection'], 'unknown')
        self.assertEqual(entries[3]['phase'], 'unknown')
        self.assertEqual(entries[4]['connection'], 'unknown')
        self.assertEqual(observations, original)


if __name__ == '__main__':
    unittest.main()
