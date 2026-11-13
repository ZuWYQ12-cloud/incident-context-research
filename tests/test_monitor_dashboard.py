import json
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from monitor_service import Monitor
from monitor_dashboard import snapshot


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.root = Path(self.temp.name)
        self.database = self.root / 'monitor.sqlite'
        self.monitor = Monitor(self.database, 'http://localhost:8000/health/ready')

    def tearDown(self):
        self.monitor.close()
        self.temp.cleanup()

    def sample(self, ok, stamp=None):
        return {'checked_at': stamp or datetime.now(timezone.utc).isoformat(),
                'ok': ok, 'http_status': 200 if ok else None,
                'error': None if ok else 'connection refused', 'duration_seconds': .01}

    def test_no_checks_is_stale(self):
        self.assertTrue(snapshot(self.database, self.root)['stale'])

    def test_old_success_does_not_look_current(self):
        for _ in range(2):
            self.monitor.observe(self.sample(True, '2020-01-01T00:00:00+00:00'))
        data = snapshot(self.database, self.root)
        self.assertEqual(data['state'], 'healthy')
        self.assertTrue(data['stale'])

    def test_incident_recovery_and_evidence(self):
        for _ in range(3):
            event = self.monitor.observe(self.sample(False))
        directory = self.root / event['incident_id']
        directory.mkdir()
        (directory / 'manifest.json').write_text(json.dumps({'status': 'completed', 'observations': []}), encoding='utf-8')
        for _ in range(2):
            self.monitor.observe(self.sample(True))
        data = snapshot(self.database, self.root)
        self.assertFalse(data['stale'])
        self.assertEqual(len(data['incidents']), 1)
        self.assertIsNotNone(data['incidents'][0]['recovered_at'])
        self.assertEqual(data['incidents'][0]['evidence']['status'], 'completed')
        self.assertEqual(len(data['checks']), 5)


if __name__ == '__main__':
    unittest.main()
