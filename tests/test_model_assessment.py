import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from model_assessment import save_assessment, load_assessment, BOUND_FILES


class AssessmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.root = Path(self.temp.name)
        for name in BOUND_FILES:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'original')

    def tearDown(self):
        self.temp.cleanup()

    def test_save_preserves_answer_and_rejects_overwrite(self):
        save_assessment(self.root, 'Researcher', 'issues_found', 'Unsupported DB hypothesis')
        self.assertEqual(load_assessment(self.root)['status'], 'current')
        self.assertEqual((self.root / 'generation/answer.txt').read_bytes(), b'original')
        with self.assertRaises(FileExistsError):
            save_assessment(self.root, 'Researcher', 'supported', 'Different assessment')

    def test_changed_answer_invalidates_assessment(self):
        save_assessment(self.root, 'Researcher', 'uncertain', 'Check evidence')
        (self.root / 'generation/answer.txt').write_bytes(b'changed')
        self.assertEqual(load_assessment(self.root)['status'], 'stale')

    def test_empty_note_rejected(self):
        with self.assertRaises(ValueError):
            save_assessment(self.root, 'Researcher', 'supported', ' ')
        self.assertEqual(load_assessment(self.root)['status'], 'missing')


if __name__ == '__main__':
    unittest.main()
