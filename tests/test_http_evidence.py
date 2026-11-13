import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import collect_http_evidence as evidence


class EvidenceTests(unittest.TestCase):
    def test_fetch_preserves_http_error_body_and_limits_size(self):
        error = HTTPError('http://localhost/tasks', 500, 'error', {}, io.BytesIO(b'abcdef'))
        with patch.object(evidence, 'build_opener') as opener:
            opener.return_value.open.side_effect = error
            body, metadata = evidence.fetch('http://localhost/tasks', max_bytes=4)
        self.assertEqual(body, b'abcd')
        self.assertEqual(metadata['http_status'], 500)
        self.assertTrue(metadata['body_truncated'])

    def test_connection_failure_is_recorded_without_http_code(self):
        with patch.object(evidence, 'build_opener') as opener:
            opener.return_value.open.side_effect = URLError('connection refused')
            body, metadata = evidence.fetch('http://localhost/tasks')
        self.assertEqual(body, b'')
        self.assertIsNone(metadata['http_status'])
        self.assertIn('connection refused', metadata['error'])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.root = Path(self.temp.name)
        self.event = {'incident_id': 'a' * 32, 'event_type': 'incident_opened',
                      'confirmed_at': '2026-11-13T17:00:00+00:00'}

    def tearDown(self):
        self.temp.cleanup()

    def test_http_failures_are_evidence_and_existing_capture_is_preserved(self):
        with patch.object(evidence, 'fetch', side_effect=[
            (b'alive', {'http_status': 200, 'error': None}),
            (b'not ready', {'http_status': 503, 'error': None}),
            (b'error', {'http_status': 500, 'error': None}),
        ]) as fetch:
            directory = evidence.collect(self.root, 'http://localhost:8000', self.event)
        manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['status'], 'completed')
        self.assertEqual(fetch.call_count, 3)
        self.assertEqual([x['http_status'] for x in manifest['observations']], [200, 503, 500])
        for row in manifest['observations']:
            self.assertEqual(row['body_sha256'], hashlib.sha256((directory / row['body_file']).read_bytes()).hexdigest())
        before = (directory / 'manifest.json').read_bytes()
        with self.assertRaises(FileExistsError):
            evidence.collect(self.root, 'http://localhost:8000', self.event)
        self.assertEqual(before, (directory / 'manifest.json').read_bytes())

    def test_collection_failure_is_recorded(self):
        with patch.object(evidence, 'fetch', side_effect=RuntimeError('test failure')):
            with self.assertRaises(RuntimeError):
                evidence.collect(self.root, 'http://localhost:8000', self.event)
        manifest = json.loads((self.root / ('a' * 32) / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['status'], 'failed')

    def test_invalid_id_cannot_escape_directory(self):
        self.event['incident_id'] = '../outside'
        with self.assertRaises(ValueError):
            evidence.collect(self.root, 'http://localhost:8000', self.event)
        self.assertEqual(list(self.root.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
