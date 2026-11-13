import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from prepare_incident_model import prepare


class PrepareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.root = Path(self.temp.name)
        self.incident = self.root / ('a' * 32)
        self.incident.mkdir()
        (self.incident / 'tasks.body').write_bytes(b'')
        manifest = {'status': 'completed', 'trigger': {'incident_id': self.incident.name},
                    'observations': [{'url': 'http://localhost/tasks', 'body_file': 'tasks.body',
                        'body_sha256': hashlib.sha256(b'').hexdigest(), 'http_status': None,
                        'error': 'connection refused'}]}
        (self.incident / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def test_snapshot_hash_and_no_overwrite(self):
        output = self.root / 'review'
        prompt = prepare(self.incident, output)
        record = json.loads((output / 'preparation.json').read_text(encoding='utf-8'))
        self.assertEqual(record['prompt_sha256'], hashlib.sha256(prompt.read_bytes()).hexdigest())
        self.assertIn('HTTP-1', prompt.read_text(encoding='utf-8'))
        self.assertFalse(record['model_output_semantically_verified'])
        with self.assertRaises(FileExistsError):
            prepare(self.incident, output)

    def test_modified_body_blocks_prompt(self):
        (self.incident / 'tasks.body').write_bytes(b'changed')
        with self.assertRaises(ValueError):
            prepare(self.incident, self.root / 'review')
        self.assertFalse((self.root / 'review' / 'prompt.txt').exists())

    def test_foreign_log_manifest_blocks_prompt(self):
        logs = self.incident / 'container-logs'
        logs.mkdir()
        (logs / 'manifest.json').write_text(json.dumps({'schema': 1, 'incident_id': 'foreign', 'sources': []}), encoding='utf-8')
        with self.assertRaises(ValueError):
            prepare(self.incident, self.root / 'review')
        self.assertFalse((self.root / 'review' / 'prompt.txt').exists())


if __name__ == '__main__':
    unittest.main()
