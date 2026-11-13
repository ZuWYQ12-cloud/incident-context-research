import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from monitor_service import Monitor


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.monitor = Monitor(':memory:', 'http://test/ready')
        self.addCleanup(self.monitor.close)
        self.counter = 0

    def feed(self, ok):
        self.counter += 1
        return self.monitor.observe({'checked_at': f't{self.counter}', 'ok': ok,
            'http_status': 200 if ok else 503, 'error': None, 'duration_seconds': .01})

    def test_short_failure_does_not_open_incident(self):
        self.assertIsNone(self.feed(False))
        self.assertIsNone(self.feed(False))
        self.assertIsNone(self.feed(True))
        self.assertIsNone(self.feed(False))

    def test_persistent_failure_opens_once(self):
        events = [self.feed(False) for _ in range(8)]
        self.assertEqual(sum(e is not None for e in events), 1)
        self.assertEqual(events[2]['event_type'], 'incident_opened')
        self.assertEqual(events[2]['first_bad_at'], 't1')

    def test_recovery_requires_two_successes(self):
        events = [self.feed(False) for _ in range(3)]
        self.assertIsNone(self.feed(True))
        self.assertIsNone(self.feed(False))
        self.assertIsNone(self.feed(True))
        recovered = self.feed(True)
        self.assertEqual(recovered['incident_id'], events[-1]['incident_id'])
        self.assertEqual(recovered['event_type'], 'recovered')
        self.assertIsNone(self.feed(True))

    def test_initial_success_is_not_recovery(self):
        self.assertIsNone(self.feed(True))
        self.assertIsNone(self.feed(True))

    def test_second_outage_has_new_id(self):
        first = [self.feed(False) for _ in range(3)][-1]
        self.feed(True)
        self.feed(True)
        second = [self.feed(False) for _ in range(3)][-1]
        self.assertNotEqual(first['incident_id'], second['incident_id'])

    def test_restart_keeps_open_incident(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as directory:
            path = Path(directory) / 'monitor.sqlite'
            first = Monitor(path, 'http://test')
            sample = {'checked_at': 't1', 'ok': False, 'http_status': None,
                      'error': 'timeout', 'duration_seconds': 3}
            for _ in range(3):
                event = first.observe(sample)
            first.close()
            second = Monitor(path, 'http://test')
            try:
                self.assertIsNone(second.observe(sample))
                row = second.db.execute('SELECT incident_id FROM state').fetchone()
                self.assertEqual(row['incident_id'], event['incident_id'])
                self.assertEqual(second.db.execute('SELECT count(*) FROM events').fetchone()[0], 1)
            finally:
                second.close()


if __name__ == '__main__':
    unittest.main()
