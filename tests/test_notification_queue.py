import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from monitor_service import Monitor


def sample(ok):
    return dict(checked_at='2026-11-17T12:00:00+00:00', ok=ok,
                http_status=200 if ok else None, error=None if ok else 'refused',
                duration_seconds=0.01)


class NotificationTests(unittest.TestCase):
    def test_short_failure_does_not_queue(self):
        monitor = Monitor(':memory:', 'http://test/ready')
        self.addCleanup(monitor.close)
        monitor.observe(sample(False))
        monitor.observe(sample(False))
        monitor.observe(sample(True))
        self.assertEqual(monitor.db.execute('SELECT count(*) FROM notification_outbox').fetchone()[0], 0)

    def test_restart_preserves_messages_without_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'monitor.sqlite'
            monitor = Monitor(path, 'http://test/ready')
            for _ in range(5):
                monitor.observe(sample(False))
            monitor.close()
            monitor = Monitor(path, 'http://test/ready')
            try:
                monitor.observe(sample(False))
                for _ in range(4):
                    monitor.observe(sample(True))
                rows = monitor.db.execute('SELECT * FROM notification_outbox ORDER BY id').fetchall()
                self.assertEqual([r['event_type'] for r in rows], ['incident_opened', 'recovered'])
                self.assertEqual(rows[0]['incident_id'], rows[1]['incident_id'])
                self.assertEqual([r['status'] for r in rows], ['pending', 'pending'])
                self.assertIn('HTTP-ответ не получен', rows[0]['message'])
            finally:
                monitor.close()

    def test_queue_failure_rolls_back_event_and_confirmation(self):
        monitor = Monitor(':memory:', 'http://test/ready')
        self.addCleanup(monitor.close)
        for _ in range(2):
            monitor.observe(sample(False))
        with patch('monitor_service.enqueue', side_effect=RuntimeError('queue unavailable')):
            with self.assertRaises(RuntimeError):
                monitor.observe(sample(False))
        self.assertEqual(monitor.db.execute('SELECT count(*) FROM events').fetchone()[0], 0)
        self.assertEqual(monitor.db.execute('SELECT count(*) FROM checks').fetchone()[0], 2)
        self.assertIsNotNone(monitor.observe(sample(False)))
        self.assertEqual(monitor.db.execute('SELECT count(*) FROM notification_outbox').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
