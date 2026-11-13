import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from collect_container_logs import collect_logs


class LogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.root = Path(self.temp.name)
        self.compose = self.root / 'compose.yaml'
        self.compose.write_text('services: {}', encoding='utf-8')
        self.event = {'incident_id': 'a' * 32, 'first_bad_at': '2026-11-13T17:00:00+00:00'}

    def tearDown(self):
        self.temp.cleanup()

    def read(self, path):
        return json.loads((path / 'manifest.json').read_text(encoding='utf-8'))

    def test_success_hashes_and_no_overwrite(self):
        with patch('collect_container_logs.subprocess.run', return_value=subprocess.CompletedProcess([], 0, b'line\n', b'')) as run:
            path = collect_logs(self.root, self.compose, self.event, True)
        data = self.read(path)
        self.assertEqual(data['status'], 'completed')
        self.assertEqual(data['since'], '2026-11-13T16:59:00+00:00')
        self.assertEqual(run.call_args_list[0].args[0][:2], ['sudo', '-n'])
        self.assertEqual([r['service'] for r in data['sources']], ['api', 'db'])
        for row in data['sources']:
            self.assertEqual(row['stdout']['sha256'], hashlib.sha256((path / row['stdout']['file']).read_bytes()).hexdigest())
        with self.assertRaises(FileExistsError):
            collect_logs(self.root, self.compose, self.event)

    def test_timeout_preserves_partial_output_and_continues(self):
        with patch('collect_container_logs.subprocess.run', side_effect=[
            subprocess.TimeoutExpired('docker', 15, output=b'partial'),
            subprocess.CompletedProcess([], 0, b'', b'')]):
            path = collect_logs(self.root, self.compose, self.event)
        data = self.read(path)
        self.assertEqual(data['status'], 'partial_or_failed')
        self.assertTrue(data['sources'][0]['timed_out'])
        self.assertEqual((path / 'api.stdout.log').read_bytes(), b'partial')
        self.assertEqual(data['sources'][1]['status'], 'collected')

    def test_permission_error_is_not_success(self):
        with patch('collect_container_logs.subprocess.run', return_value=subprocess.CompletedProcess([], 1, b'', b'permission denied')):
            data = self.read(collect_logs(self.root, self.compose, self.event))
        self.assertEqual(data['status'], 'partial_or_failed')
        self.assertTrue(all(r['exit_code'] == 1 for r in data['sources']))


if __name__ == '__main__':
    unittest.main()
