import json
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from model_answer_checks import check_answer


def prompt(extra=None):
    observations = [{'id': 'HTTP-1', 'http_status': None, 'error': 'Connection refused'},
                    {'id': 'API', 'text': 'INFO: Finished server process [1]'}]
    observations.extend(extra or [])
    return 'Instructions\n' + json.dumps({'observations': observations}) + '\nQuestions'


class AnswerChecksTests(unittest.TestCase):
    def test_observed_wrong_db_claims_are_flagged_with_exact_offsets(self):
        for answer in (
            'Подтверждено, что API не может связаться с БД (HTTP-ошибки Connection refused).',
            'Поддерживается объяснение, что серверный процесс завершился из-за отсутствия доступа к БД.',
            'Поддерживается, что API завершил работу из-за отказа в соединении с БД.',
        ):
            result = check_answer(answer, prompt())
            self.assertEqual(result['status'], 'warnings')
            finding = result['findings'][0]
            self.assertEqual(finding['code'], 'possible_db_misattribution')
            self.assertEqual(answer[finding['start_char']:finding['end_char']], finding['quote'])

    def test_hypothesis_and_negative_claims_are_not_assertions(self):
        for answer in (
            'Не подтверждено, что API завершился из-за недоступности БД.',
            'Гипотеза: API завершился из-за отказа БД.',
            'Проверить, не может ли API подключиться к БД.',
            'Отказ HTTP не доказывает, что API завершился из-за БД.',
        ):
            self.assertEqual(check_answer(answer, prompt())['status'], 'no_matches')

    def test_unknown_citations_but_not_component_names(self):
        result = check_answer('API использует БД. [HTTP-9], (DB), HTTP-1, [API].', prompt())
        self.assertEqual([f['quote'] for f in result['findings']], ['HTTP-9', '(DB)'])

    def test_database_connection_evidence_is_not_ignored(self):
        result = check_answer('Подтверждено, что API не может подключиться к БД.', prompt([
            {'id': 'DB', 'text': 'FATAL: password authentication failed for user "test"'}]))
        self.assertEqual(result['status'], 'no_matches')
        self.assertIn('не подтверждает правильность', result['limitations'])

    def test_without_refused_http_this_rule_does_not_apply(self):
        value = 'Header\n' + json.dumps({'observations': [{'id': 'HTTP-1', 'http_status': 503}]})
        self.assertEqual(check_answer('Подтверждено, что API не может связаться с БД.', value)['status'], 'no_matches')

    def test_unknown_prompt_is_not_reported_as_verified(self):
        self.assertEqual(check_answer('answer', 'unknown format')['status'], 'not_checked')


if __name__ == '__main__':
    unittest.main()
