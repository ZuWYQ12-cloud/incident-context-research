import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import run_monitoring as app
from runtime_control import acquire_lock


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.args = argparse.Namespace(database=self.root / 'db.sqlite', evidence_dir=self.root / 'evidence',
            log_dir=self.root / 'runs', port=8081, url='http://127.0.0.1:8000/health/ready',
            service_url='http://127.0.0.1:8000', compose_file=None, docker_sudo=False,
            model_python=None, vk_peer_id=123)

    def test_token_is_only_passed_to_sender(self):
        original = {'VK_COMMUNITY_TOKEN': 'secret', 'PATH': 'path'}
        self.assertNotIn('VK_COMMUNITY_TOKEN', app.child_environment(original))
        self.assertEqual(app.child_environment(original, 'new')['VK_COMMUNITY_TOKEN'], 'new')
        self.assertEqual(original['VK_COMMUNITY_TOKEN'], 'secret')

    def test_port_check_allows_address_reuse_before_bind(self):
        with patch.object(app.socket, 'socket') as factory:
            probe = factory.return_value.__enter__.return_value
            app.check_port(8081)
            self.assertEqual(probe.method_calls[0][0], 'setsockopt')
            probe.setsockopt.assert_called_once_with(app.socket.SOL_SOCKET, app.socket.SO_REUSEADDR, 1)
            probe.bind.assert_called_once_with(('127.0.0.1', 8081))

    @unittest.skipUnless(os.name == 'posix', 'Проверка сокетов выполняется в Ubuntu/WSL')
    def test_live_listener_is_still_rejected(self):
        with app.socket.socket() as server:
            server.setsockopt(app.socket.SOL_SOCKET, app.socket.SO_REUSEADDR, 1)
            server.bind(('127.0.0.1', 0))
            server.listen()
            with self.assertRaises(OSError):
                app.check_port(server.getsockname()[1])

    def test_commands_do_not_start_docker_and_vk_is_optional(self):
        self.args.vk_peer_id = None
        self.args.compose_file = self.root / 'compose.yaml'
        self.args.docker_sudo = True
        built = app.commands(self.args)
        self.assertEqual([name for name, _ in built], ['monitor', 'dashboard'])
        self.assertIn('--compose-file', built[0][1])
        self.assertNotIn('up', built[0][1])

    def test_readiness_rejects_exited_process(self):
        process = Mock(pid=123, returncode=1)
        process.poll.return_value = 1
        path = self.root / 'ready.json'
        path.write_text(json.dumps({'pid': 123, 'ready': True}))
        with self.assertRaises(RuntimeError):
            app.wait_ready(process, path)

    def test_readiness_checks_pid_and_times_out(self):
        process = Mock(pid=123)
        process.poll.return_value = None
        path = self.root / 'ready.json'
        path.write_text(json.dumps({'pid': 999, 'ready': True}))
        with patch.object(app.time, 'monotonic', side_effect=[0, 0, 21]), patch.object(app.time, 'sleep'):
            with self.assertRaises(RuntimeError):
                app.wait_ready(process, path)

    def run_fake(self, readiness, polls):
        processes = [Mock(pid=n, returncode=None) for n in (101, 102, 103)]
        for proc, result in zip(processes, polls):
            proc.poll.return_value = result
        with patch.object(app.subprocess, 'Popen', side_effect=processes) as popen, \
             patch.object(app, 'wait_ready', side_effect=readiness), \
             patch.object(app, 'stop_children') as cleanup, \
             patch.object(app.time, 'sleep', side_effect=KeyboardInterrupt), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = app.supervise(self.args, 'test-secret')
        return result, popen, cleanup, json.loads(next(self.args.log_dir.glob('*/status.json')).read_text())

    def test_partial_start_failure_cleans_up(self):
        result, popen, cleanup, status = self.run_fake([None, RuntimeError('startup failed')], [None]*3)
        self.assertEqual(result, 1)
        self.assertEqual(popen.call_count, 2)
        self.assertEqual(len(cleanup.call_args.args[0]), 2)
        self.assertEqual(status['status'], 'failed')

    def test_ctrl_c_stops_all_and_does_not_log_token(self):
        result, popen, cleanup, status = self.run_fake([None]*3, [None]*3)
        self.assertEqual(result, 0)
        self.assertEqual(len(cleanup.call_args.args[0]), 3)
        self.assertEqual(status['status'], 'stopped')
        self.assertNotIn('test-secret', json.dumps(status))
        for call in popen.call_args_list[:2]:
            self.assertNotIn('VK_COMMUNITY_TOKEN', call.kwargs['env'])
        self.assertEqual(popen.call_args_list[2].kwargs['env']['VK_COMMUNITY_TOKEN'], 'test-secret')

    def test_unexpected_clean_exit_is_not_silently_ignored(self):
        result, _, cleanup, status = self.run_fake([None]*3, [None, None, 0])
        self.assertEqual(result, 1)
        self.assertEqual(status['status'], 'failed')
        self.assertEqual(len(cleanup.call_args.args[0]), 3)

    @unittest.skipUnless(os.name == 'posix', 'Проверка flock выполняется в Ubuntu/WSL')
    def test_lock_blocks_second_process_and_releases(self):
        lock_path = self.root / 'test.lock'
        script = ('import sys; sys.path.insert(0, sys.argv[1]); '
                  'from runtime_control import acquire_lock; acquire_lock(sys.argv[2]).close()')
        command = [sys.executable, '-c', script, str(app.ROOT / 'src'), str(lock_path)]
        with acquire_lock(lock_path):
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
        self.assertEqual(subprocess.run(command, capture_output=True).returncode, 0)


if __name__ == '__main__':
    unittest.main()
