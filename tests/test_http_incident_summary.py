import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from http_incident_summary import summarize_http


class SummaryTests(unittest.TestCase):
    def summary(self, rows, status='completed'):
        return summarize_http({'status': status, 'observations': rows})

    def test_refusal_is_not_container_root_cause(self):
        result = self.summary([{'url': 'http://localhost/tasks', 'http_status': None,
                                'error': '<urlopen error [Errno 111] Connection refused>'}])
        self.assertIn('подключение отклонено', result['title'])
        self.assertEqual(result['facts'][0]['reference'], 'HTTP-1')
        self.assertTrue(any('не доказаны' in text for text in result['unknowns']))

    def test_503_does_not_establish_database_shutdown(self):
        result = self.summary([{'http_status': 503, 'error': None}])
        self.assertEqual(result['facts'][0]['meaning'], 'Получен HTTP 503.')
        self.assertTrue(any('не считать' in text for text in result['checks']))

    def test_read_failure_after_headers_is_not_success(self):
        result = self.summary([{'http_status': 200, 'error': 'timed out'}])
        self.assertIn('ошибкой', result['facts'][0]['meaning'])
        self.assertNotIn('успешные', result['title'])

    def test_later_success_does_not_erase_incident(self):
        result = self.summary([{'http_status': 200, 'error': None}])
        self.assertTrue(any('не отменяют' in text for text in result['unknowns']))

    def test_empty_and_unreadable_are_not_success(self):
        for evidence in (None, {'status': 'unreadable'}, {'status': 'completed', 'observations': []}):
            self.assertIn('Недостаточно', summarize_http(evidence)['title'])

    def test_partial_and_truncated_are_explicit(self):
        result = self.summary([{'http_status': 500, 'body_truncated': True}], 'failed')
        self.assertTrue(any('частичный' in text for text in result['unknowns']))
        self.assertTrue(any('HTTP-1' in text for text in result['unknowns']))


if __name__ == '__main__':
    unittest.main()
