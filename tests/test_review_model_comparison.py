import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from prepare_incident_model import prepare
from review_model_comparison import save_comparison


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        incident = self.root / ('a' * 32)
        incident.mkdir()
        (incident / 'tasks.body').write_bytes(b'')
        manifest = {'status': 'completed', 'trigger': {'incident_id': incident.name}, 'observations': [
            {'url': 'http://localhost/tasks', 'http_status': None, 'error': 'Connection refused',
             'body_file': 'tasks.body', 'body_sha256': hashlib.sha256(b'').hexdigest()}]}
        (incident / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        self.reviews = [self.root / mode for mode in ('baseline', 'timeline')]
        for mode, review in zip(('baseline', 'timeline'), self.reviews):
            prompt = prepare(incident, review, mode)
            generation = review / 'generation'
            generation.mkdir()
            (generation / 'prompt.txt').write_bytes(prompt.read_bytes())
            (generation / 'answer.txt').write_text('Подтверждено, что API не может связаться с БД.', encoding='utf-8')
            metadata = {'model': 'test', 'revision': 'fixed', 'seed': 42, 'generation': {'max_new_tokens': 1536},
                        'dtype': 'test', 'enable_thinking': False, 'torch_version': 'test',
                        'transformers_version': 'test', 'gpu': 'test'}
            (generation / 'metadata.json').write_text(json.dumps(metadata), encoding='utf-8')

    def test_save_checks_existing_runs_without_mutation(self):
        before = [p.joinpath('generation/answer.txt').read_bytes() for p in self.reviews]
        output = save_comparison(*self.reviews, self.root / 'review')
        record = json.loads((output / 'comparison-review.json').read_text(encoding='utf-8'))
        self.assertEqual([run['automatic_checks']['status'] for run in record['runs']], ['warnings', 'warnings'])
        self.assertFalse(record['independent_evaluation'])
        self.assertEqual(before, [p.joinpath('generation/answer.txt').read_bytes() for p in self.reviews])
        with self.assertRaises(FileExistsError):
            save_comparison(*self.reviews, output)

    def test_different_seed_stops_comparison(self):
        path = self.reviews[1] / 'generation/metadata.json'
        meta = json.loads(path.read_text())
        meta['seed'] = 43
        path.write_text(json.dumps(meta))
        with self.assertRaises(ValueError):
            save_comparison(*self.reviews, self.root / 'review')
        self.assertFalse((self.root / 'review').exists())


if __name__ == '__main__':
    unittest.main()
