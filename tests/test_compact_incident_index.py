import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from incident_context_index import build_index, compact_index, expand_index
from prepare_incident_model import prepare


class CompactIndexTests(unittest.TestCase):
    def test_round_trip_preserves_mixed_entries_and_boundaries(self):
        trigger = {'first_bad_at': '2026-11-18T12:17:07Z',
                   'confirmed_at': '2026-11-18T12:17:17Z'}
        observations = [{'id': 'HTTP-1', 'http_status': 503},
            {'id': 'API', 'text': '\n'.join([
                '2026-11-18T12:17:06.123456789Z before',
                '2026-11-18T12:17:07Z first',
                '2026-11-18T12:17:17Z confirmed',
                '2026-11-18T12:17:18Z {"event":"http_request","method":"GET","path":"/tasks","status_code":500}',
                'no timestamp'])}, {'id': 'DB', 'text': 'unknown'}]
        index = build_index(trigger, observations)
        encoded = json.loads(json.dumps(compact_index(index)))
        self.assertEqual(expand_index(encoded), index)
        self.assertEqual(expand_index(compact_index(build_index({}, []))), build_index({}, []))

    def test_large_index_fits_without_changing_log_or_full_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            incident = Path(tmp) / ('a' * 32)
            incident.mkdir()
            manifest = {'status': 'completed', 'trigger': {'incident_id': incident.name},
                        'observations': []}
            (incident / 'manifest.json').write_text(json.dumps(manifest))
            log = '\n'.join('2026-11-18T12:17:07.123456789Z ' + ('x' * 110)
                            for _ in range(107))
            logs_dir = incident / 'container-logs'
            logs_dir.mkdir()
            (logs_dir / 'manifest.json').write_text('{}')
            (logs_dir / 'api.stdout.log').write_text(log)
            reviewed = {'status': 'completed', 'sources': [
                {'service': 'api', 'status': 'verified', 'text': log, 'collected_at': None}],
                'facts': []}
            output = Path(tmp) / 'review'
            with patch('prepare_incident_model.review_logs', return_value=reviewed):
                prompt_path = prepare(incident, output)
            prompt = prompt_path.read_text(encoding='utf-8')
            payload, _ = json.JSONDecoder().raw_decode(prompt[prompt.index('\n{') + 1:])
            self.assertEqual(payload['observations'][0]['text'], log)
            record = json.loads((output / 'preparation.json').read_text(encoding='utf-8'))
            self.assertEqual(record['context_index_encoding'], 'shared_layouts_v2')
            self.assertLessEqual(len(prompt), 30000)
            index = json.loads((output / 'context-index.json').read_text(encoding='utf-8'))
            self.assertEqual(len(index['entries']), 107)
            self.assertEqual(record['prompt_sha256'], hashlib.sha256(prompt_path.read_bytes()).hexdigest())
            self.assertEqual((output / 'evidence/container-logs/api.stdout.log').read_bytes(),
                             (logs_dir / 'api.stdout.log').read_bytes())

    def test_excessive_source_still_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            incident = Path(tmp) / ('b' * 32)
            incident.mkdir()
            (incident / 'manifest.json').write_text(json.dumps({'status': 'completed',
                'trigger': {'incident_id': incident.name}, 'observations': []}))
            reviewed = {'status': 'completed', 'sources': [
                {'service': 'api', 'status': 'verified', 'text': 'x' * 31000}], 'facts': []}
            reviewed['sources'][0]['collected_at'] = None
            with patch('prepare_incident_model.review_logs', return_value=reviewed):
                with self.assertRaisesRegex(ValueError, 'Контекст слишком большой'):
                    prepare(incident, Path(tmp) / 'review')
            self.assertFalse((Path(tmp) / 'review/prompt.txt').exists())


if __name__ == '__main__':
    unittest.main()
