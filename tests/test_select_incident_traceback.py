import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from select_incident_traceback import select_traceback


class SelectionTests(unittest.TestCase):
    def test_complete_stack_keeps_terminal_and_events_with_original_lines(self):
        prefix = 'api-1  | 2026-11-23T19:06:31.123456789Z '
        messages = ['INFO: GET /tasks 500', 'Traceback (most recent call last):',
                    '  File "/app/main.py", line 77, in tasks', '    conn.execute()',
                    'psycopg.errors.InsufficientPrivilege: permission denied for table tasks',
                    'INFO: GET /health/live 200']
        text = ''.join(prefix + m + '\n' for m in messages)
        selected, kept, omitted = select_traceback(text)
        self.assertEqual(omitted, [3, 4])
        self.assertEqual(kept, [1, 2, 5, 6])
        self.assertEqual(selected, ''.join(text.splitlines(keepends=True)[n-1] for n in kept))

    def test_incomplete_traceback_is_not_removed(self):
        text = 'Traceback (most recent call last):\n  File "x"\n    code\n'
        self.assertEqual(select_traceback(text), (text, [1, 2, 3], []))

    def test_indented_text_outside_traceback_is_preserved(self):
        text = '  ERROR: permission denied\n    detail\n'
        self.assertEqual(select_traceback(text), (text, [1, 2], []))

    def test_unknown_event_interrupts_selection(self):
        text = 'Traceback (most recent call last):\n  frame\nunknown event\nValueError: issue\n'
        self.assertEqual(select_traceback(text)[0], text)


if __name__ == '__main__':
    unittest.main()
