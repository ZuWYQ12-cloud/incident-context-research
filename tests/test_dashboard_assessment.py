import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from monitor_dashboard import handler
from monitor_service import Monitor


class AssessmentHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.root = Path(self.temp.name)
        database = self.root / 'monitor.sqlite'
        monitor = Monitor(database, 'http://localhost/health/ready')
        for _ in range(3):
            event = monitor.observe({'checked_at': '2026-11-14T00:00:00+00:00', 'ok': False,
                                     'http_status': None, 'error': 'refused', 'duration_seconds': .1})
        monitor.close()
        self.identifier = event['incident_id']
        with patch('monitor_dashboard.secrets.token_urlsafe', return_value='test-token'):
            self.cls = handler(database, self.root, 30)

    def tearDown(self):
        self.temp.cleanup()

    def post(self, token):
        raw = json.dumps({'incident_id': self.identifier, 'reviewer': 'Tester',
                         'verdict': 'issues_found', 'note': 'Test assessment'}).encode()
        request = self.cls.__new__(self.cls)
        request.path = '/api/assessment'
        request.headers = {'X-Review-Token': token, 'Content-Length': str(len(raw))}
        request.rfile = io.BytesIO(raw)
        request.wfile = io.BytesIO()
        request.connection = Mock()
        for name in ('send_response', 'send_header', 'end_headers', 'send_error'):
            setattr(request, name, Mock())
        request.do_POST()
        return request

    def test_missing_token_blocks_write(self):
        with patch('monitor_dashboard.save_assessment') as save:
            request = self.post('')
            request.send_error.assert_called_once_with(403)
            save.assert_not_called()

    def test_valid_request_and_duplicate(self):
        with patch('monitor_dashboard.load_model_review', return_value={'status': 'available'}), patch('monitor_dashboard.save_assessment') as save:
            request = self.post('test-token')
            request.send_response.assert_called_once_with(201)
            self.assertEqual(save.call_args.args[1:], ('Tester', 'issues_found', 'Test assessment'))
            save.side_effect = FileExistsError()
            request = self.post('test-token')
            request.send_response.assert_called_once_with(409)


if __name__ == '__main__':
    unittest.main()
