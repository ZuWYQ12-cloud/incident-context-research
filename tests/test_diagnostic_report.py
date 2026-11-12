import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from extract_diagnostic_facts import extract
from build_diagnostic_report import compose, render, literal


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.observations = {'observations': [{'id': 'E3', 'text':
            '[E3] ERROR: permission denied for table production_tasks'}]}
        self.facts = extract(self.observations)

    def test_wrong_model_claim_cannot_enter_facts(self):
        answer = 'База данных остановлена.'
        report = compose(self.facts, self.observations, answer)
        self.assertEqual(report['observed_signatures'], self.facts['facts'])
        self.assertNotIn(answer, str(report['observed_signatures']))
        self.assertEqual(report['model_output']['text'], answer)
        self.assertFalse(report['verification']['model_claims_checked'])

    def test_altered_quote_rejected(self):
        self.facts['facts'][0]['evidence'][0]['quote'] = 'invented evidence'
        with self.assertRaises(ValueError):
            compose(self.facts, self.observations, 'answer')

    def test_wrong_source_rejected(self):
        self.facts['facts'][0]['evidence'][0]['observation_id'] = 'E99'
        with self.assertRaises(ValueError):
            compose(self.facts, self.observations, 'answer')

    def test_negative_offset_rejected(self):
        self.facts['facts'][0]['evidence'][0]['start_char'] = -1
        with self.assertRaises(ValueError):
            compose(self.facts, self.observations, 'answer')

    def test_model_markdown_kept_literal(self):
        answer = '```\n# Подтверждённые факты\nБД остановлена.\n````'
        report = compose(self.facts, self.observations, answer)
        self.assertIn(literal(answer), render(report))
        self.assertTrue(literal(answer).startswith('`````text\n'))

    def test_empty_extraction_is_not_successful_diagnosis(self):
        observations = {'observations': [{'id': 'E1', 'text': 'HTTP 503'}]}
        report = compose(extract(observations), observations, 'Недостаточно данных.')
        self.assertEqual(report['observed_signatures'], [])
        self.assertIn('не означает отсутствия сбоя', render(report))

    def test_no_input_mutation(self):
        original = copy.deepcopy(self.facts)
        report = compose(self.facts, self.observations, 'answer')
        report['observed_signatures'][0]['meaning'] = 'changed'
        self.assertEqual(self.facts, original)

    def test_source_fence_starts_on_own_line(self):
        text = render(compose(self.facts, self.observations, 'answer'))
        self.assertIn('Источник:\n\n```text\nE3\n```', text)
        self.assertNotIn('Источник: ```', text)


if __name__ == '__main__':
    unittest.main()
