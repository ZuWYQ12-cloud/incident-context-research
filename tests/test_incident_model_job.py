from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from incident_model_job import Jobs, execute, job_state


class JobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.root = Path(self.temp.name)
        self.incident = self.root / ('a'*32)
        self.incident.mkdir()
        (self.incident / 'manifest.json').write_text('{}')

    def tearDown(self):
        self.temp.cleanup()

    def test_duplicate_blocked_and_one_process_started(self):
        jobs = Jobs(self.root, sys.executable)
        with patch('incident_model_job.subprocess.Popen', return_value=Mock()) as popen:
            jobs.start(self.incident.name)
            self.assertEqual(job_state(self.incident)['status'], 'queued')
            with self.assertRaises(ValueError):
                jobs.start(self.incident.name)
            popen.assert_called_once()

    def test_existing_answer_directory_preserved(self):
        (self.incident / 'model-review-001').mkdir()
        with patch('incident_model_job.subprocess.Popen') as popen:
            with self.assertRaises(ValueError):
                Jobs(self.root, sys.executable).start(self.incident.name)
            popen.assert_not_called()

    def test_failed_preparation_does_not_launch_model(self):
        with patch('incident_model_job.prepare', side_effect=ValueError('bad evidence')), patch('incident_model_job.subprocess.run') as run:
            execute(self.incident, Path(sys.executable))
        self.assertEqual(job_state(self.incident)['status'], 'failed')
        run.assert_not_called()

    def test_model_timeout_recorded(self):
        import subprocess
        with patch('incident_model_job.prepare', return_value=self.incident / 'prompt.txt'), patch('incident_model_job.subprocess.run', side_effect=subprocess.TimeoutExpired('model',600)):
            execute(self.incident, Path(sys.executable))
        self.assertEqual(job_state(self.incident)['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
