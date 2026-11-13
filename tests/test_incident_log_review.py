import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from incident_log_review import review_logs


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.root = Path(self.temp.name)
        self.logs = self.root / 'container-logs'
        self.logs.mkdir()
        self.raw = b'FATAL: password authentication failed for user "eval_auth"\n'
        (self.logs / 'api.stdout.log').write_bytes(self.raw)
        self.manifest = {'schema': 1, 'incident_id': 'a' * 32, 'status': 'completed', 'sources': [
            {'service': 'api', 'status': 'collected', 'exit_code': 0, 'timed_out': False,
             'stdout': {'file': 'api.stdout.log', 'sha256': hashlib.sha256(self.raw).hexdigest(),
                        'bytes': len(self.raw), 'truncated': False}}]}

    def tearDown(self):
        self.temp.cleanup()

    def review(self):
        (self.logs / 'manifest.json').write_text(json.dumps(self.manifest), encoding='utf-8')
        return review_logs(self.root, 'a' * 32)

    def test_valid_quote_is_exact(self):
        result = self.review()
        self.assertEqual(result['analyzed_sources'], ['API'])
        fact = result['facts'][0]
        self.assertEqual(fact['signature'], 'password_authentication_failed')
        quote = fact['evidence'][0]
        self.assertEqual(self.raw.decode()[quote['start_char']:quote['end_char']], quote['quote'])

    def test_changed_file_cannot_generate_facts(self):
        (self.logs / 'api.stdout.log').write_bytes(b'changed')
        result = self.review()
        self.assertEqual(result['facts'], [])
        self.assertEqual(result['sources'][0]['status'], 'invalid')

    def test_failed_or_truncated_capture_excluded(self):
        self.manifest['sources'][0]['exit_code'] = 1
        self.assertEqual(self.review()['facts'], [])
        self.manifest['sources'][0]['exit_code'] = 0
        self.manifest['sources'][0]['stdout']['truncated'] = True
        self.assertEqual(self.review()['facts'], [])

    def test_empty_log_is_explicit(self):
        (self.logs / 'api.stdout.log').write_bytes(b'')
        self.manifest['sources'][0]['stdout'].update(sha256=hashlib.sha256(b'').hexdigest(), bytes=0)
        result = self.review()
        self.assertEqual(result['sources'][0]['status'], 'empty')
        self.assertEqual(result['facts'], [])

    def test_foreign_incident_and_path_rejected(self):
        self.manifest['incident_id'] = 'b' * 32
        self.assertEqual(self.review()['status'], 'unreadable')
        self.manifest['incident_id'] = 'a' * 32
        self.manifest['sources'][0]['stdout']['file'] = '../secret'
        self.assertEqual(self.review()['sources'][0]['status'], 'invalid')


if __name__ == '__main__':
    unittest.main()
