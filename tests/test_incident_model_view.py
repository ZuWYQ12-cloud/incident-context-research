import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from incident_model_view import load_model_review


class ModelViewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.incident = Path(self.temp.name)
        self.root = self.incident / 'model-review-001'
        (self.root / 'generation').mkdir(parents=True)
        (self.root / 'evidence').mkdir()
        (self.root / 'evidence/manifest.json').write_bytes(b'{}')
        record = {'incident_id': 'a'*32, 'purpose': 'retrospective_model_review',
                  'prompt_sha256': hashlib.sha256(b'prompt').hexdigest(),
                  'source_sha256': {'manifest.json': hashlib.sha256(b'{}').hexdigest()}}
        (self.root / 'preparation.json').write_text(json.dumps(record), encoding='utf-8')
        (self.root / 'prompt.txt').write_bytes(b'prompt')
        (self.root / 'generation/prompt.txt').write_bytes(b'prompt')
        (self.root / 'generation/metadata.json').write_text('{}', encoding='utf-8')
        (self.root / 'generation/answer.txt').write_text('<script>untrusted</script>', encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def test_answer_stays_literal_and_unverified(self):
        result = load_model_review(self.incident, 'a'*32)
        self.assertEqual(result['status'], 'available')
        self.assertEqual(result['answer'], '<script>untrusted</script>')
        self.assertIn('не проверено', result['warning'])

    def test_mismatched_generation_prompt_hides_answer(self):
        (self.root / 'generation/prompt.txt').write_bytes(b'other')
        self.assertEqual(load_model_review(self.incident, 'a'*32)['status'], 'invalid')

    def test_changed_evidence_hides_answer(self):
        (self.root / 'evidence/manifest.json').write_bytes(b'changed')
        self.assertEqual(load_model_review(self.incident, 'a'*32)['status'], 'invalid')

    def test_foreign_incident_hides_answer(self):
        self.assertEqual(load_model_review(self.incident, 'b'*32)['status'], 'invalid')


if __name__ == '__main__':
    unittest.main()
