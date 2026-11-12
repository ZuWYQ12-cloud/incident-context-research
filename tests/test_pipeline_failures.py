"""Integration tests: real pipeline, builder, audits and report, no model/GPU."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class PipelineFailureTests(unittest.TestCase):
    def setUp(self):
        self.repository = Path(__file__).resolve().parents[1]
        # Use a workspace-local directory rather than platform-specific temp roots.
        self.temporary = tempfile.TemporaryDirectory(prefix='.pipeline-test-', dir=self.repository)
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)
        self.src = self.work / 'src'
        self.src.mkdir()
        for name in ('run_pipeline.py', 'build_context.py', 'context_time.py',
                     'context_sources.py', 'extract_diagnostic_facts.py',
                     'build_diagnostic_report.py'):
            shutil.copyfile(self.repository / 'src' / name, self.src / name)
        self.source = self.work / 'observations.txt'
        text = '[E1] ERROR: permission denied for table production_tasks'
        self.source.write_text(text, encoding='utf-8')
        self.input = self.work / 'input.json'
        data = {
            'schema_version': '0.1', 'purpose': 'development', 'incident_id': 'test-only',
            'header_text': 'Тестовая система.\n\n', 'question_text': 'Какая ошибка?\n',
            'diagnosis_cutoff': '2026-11-12T14:55:00Z',
            'observations': [{
                'id': 'E1', 'text': text, 'event_time': '2026-11-12T14:54:00Z',
                'collected_at': '2026-11-12T14:54:01Z',
                'provenance': {'kind': 'prepared_prompt_excerpt',
                               'source_file': str(self.source),
                               'source_sha256': hashlib.sha256(self.source.read_bytes()).hexdigest()},
            }],
        }
        self.input.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        self.output = self.work / 'output'

    def run_pipeline(self):
        return subprocess.run(
            [sys.executable, str(self.src / 'run_pipeline.py'), str(self.input),
             '--output-dir', str(self.output), '--mode', 'rules-only'],
            cwd=self.work, capture_output=True, text=True, encoding='utf-8',
            env={**os.environ, 'PYTHONIOENCODING': 'utf-8'}, timeout=30,
        )

    def state(self):
        return json.loads((self.output / 'pipeline-status.json').read_text(encoding='utf-8'))

    def test_success_without_model(self):
        result = self.run_pipeline()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.state()['status'], 'completed')
        report = json.loads((self.output / 'report.json').read_text(encoding='utf-8'))
        self.assertEqual(report['model_output']['status'], 'not_run')
        self.assertEqual(report['verification']['evidence_quotes_checked'], 1)
        self.assertFalse((self.output / 'model').exists())

    def test_changed_source_blocks_build_and_records_failure(self):
        self.source.write_text('changed source', encoding='utf-8')
        result = self.run_pipeline()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.state()['status'], 'failed')
        self.assertEqual(self.state()['stage'], 'context')
        self.assertIn('Source changed', (self.output / 'context.log').read_text(encoding='utf-8'))
        self.assertFalse((self.output / 'report.md').exists())

    def test_repeated_run_preserves_all_artifacts(self):
        first = self.run_pipeline()
        self.assertEqual(first.returncode, 0, first.stderr)
        before = {p.relative_to(self.output): p.read_bytes()
                  for p in self.output.rglob('*') if p.is_file()}
        second = self.run_pipeline()
        self.assertNotEqual(second.returncode, 0)
        after = {p.relative_to(self.output): p.read_bytes()
                 for p in self.output.rglob('*') if p.is_file()}
        self.assertEqual(after, before)
        self.assertEqual(self.state()['status'], 'completed')

    def test_bad_quote_stops_report_even_with_valid_output_hashes(self):
        # Deliberate defect in an isolated copy, before the run hashes are captured.
        # The real builder hashes its outputs normally, but report must reject
        # a quotation that does not match the provided observation.
        module = self.src / 'extract_diagnostic_facts.py'
        with module.open('a', encoding='utf-8') as stream:
            stream.write('''\n_original_extract = extract
def extract(document):
    value = _original_extract(document)
    value['facts'][0]['evidence'][0]['quote'] = 'fabricated quotation'
    return value
''')
        result = self.run_pipeline()
        self.assertNotEqual(result.returncode, 0)
        state = self.state()
        self.assertEqual(state['status'], 'failed')
        self.assertEqual(state['stage'], 'report')
        self.assertIn('Evidence mismatch', state['error'])
        self.assertTrue((self.output / 'context/build-manifest.json').exists())
        self.assertFalse((self.output / 'report.json').exists())
        self.assertFalse((self.output / 'report.md').exists())


if __name__ == '__main__':
    unittest.main()
