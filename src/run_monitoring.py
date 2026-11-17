"""Run monitor, dashboard and optional VK sender together on Ubuntu/WSL."""
import argparse
import getpass
import json
import os
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from runtime_control import acquire_lock

ROOT = Path(__file__).resolve().parents[1]


def child_environment(source, token=None):
    env = dict(source)
    env.pop('VK_COMMUNITY_TOKEN', None)
    env['PYTHONUNBUFFERED'] = '1'
    if token is not None:
        env['VK_COMMUNITY_TOKEN'] = token
    return env


def check_port(port):
    # Match HTTPServer.allow_reuse_address: TIME_WAIT after a clean stop must
    # not reject a restart. Do not use SO_REUSEPORT: a live listener must fail.
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(('127.0.0.1', port))


def commands(args):
    base = [sys.executable, '-u']
    monitor = base + [str(ROOT / 'src/monitor_service.py'), '--database', str(args.database),
        '--url', args.url, '--evidence-dir', str(args.evidence_dir),
        '--service-url', args.service_url, '--interval', '5']
    if args.compose_file:
        monitor += ['--compose-file', str(args.compose_file)]
    if args.docker_sudo:
        monitor += ['--docker-sudo']
    dashboard = base + [str(ROOT / 'src/monitor_dashboard.py'), '--database', str(args.database),
        '--evidence-dir', str(args.evidence_dir), '--port', str(args.port)]
    if args.model_python:
        dashboard += ['--model-python', str(args.model_python)]
    result = [('monitor', monitor), ('dashboard', dashboard)]
    if args.vk_peer_id:
        result.append(('vk', base + [str(ROOT / 'src/send_vk_notifications.py'),
            '--database', str(args.database), '--peer-id', str(args.vk_peer_id), '--send', '--watch']))
    return result


def wait_ready(process, path, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f'Процесс завершился при запуске, код {process.returncode}')
        if path.is_file():
            data = json.loads(path.read_text(encoding='utf-8'))
            if data.get('ready') is True and data.get('pid') == process.pid:
                return
        time.sleep(0.1)
    raise RuntimeError('Не получено подтверждение готовности за 20 секунд')


def signal_groups(children, sig):
    for _, process, _ in children:
        try:
            # Every child was started as a new session; include its descendants.
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass


def stop_children(children):
    if not children:
        return
    signal_groups(children, signal.SIGINT)
    deadline = time.monotonic() + 8
    for _, process, _ in children:
        try:
            process.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
    # Also terminate descendants if their parent has already exited.
    signal_groups(children, signal.SIGTERM)
    deadline = time.monotonic() + 3
    for _, process, _ in children:
        try:
            process.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
    signal_groups(children, signal.SIGKILL)
    for _, process, _ in children:
        process.wait()


def supervise(args, token):
    folder = args.log_dir / datetime.now(timezone.utc).strftime('run-%Y%m%dT%H%M%S%fZ')
    folder.mkdir(parents=True, exist_ok=False)
    print('Журналы запуска:', folder, flush=True)
    children, streams = [], []
    status, exit_code = 'starting', 0
    current = 'подготовка'
    try:
        for name, command in commands(args):
            current = name
            log_path = folder / (name + '.log')
            log = log_path.open('wb')
            streams.append(log)
            ready = folder / (name + '.ready.json')
            process = subprocess.Popen(command + ['--ready-file', str(ready)], cwd=ROOT,
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                env=child_environment(os.environ, token if name == 'vk' else None),
                start_new_session=True)
            children.append((name, process, log_path))
            wait_ready(process, ready)
            print(name + ': запущен', flush=True)
        status = 'running'
        print(f'Откройте http://localhost:{args.port}', flush=True)
        print('Ctrl+C — остановить компоненты. Docker-стенд продолжит работать.', flush=True)
        while True:
            for name, process, log in children:
                if process.poll() is not None:
                    current = name
                    raise RuntimeError(f'Компонент завершился, код {process.returncode}. Журнал: {log}')
            time.sleep(0.5)
    except KeyboardInterrupt:
        status = 'stopped'
        print('\nОстанавливаем компоненты…', flush=True)
    except (OSError, RuntimeError, ValueError) as exc:
        status, exit_code = 'failed', 1
        print(f'Ошибка {current}: {exc}\nПроверьте журналы: {folder}', file=sys.stderr, flush=True)
    finally:
        # A second Ctrl+C must not interrupt child cleanup.
        previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            stop_children(children)
        finally:
            signal.signal(signal.SIGINT, previous)
            for stream in streams:
                stream.close()
        report = {'status': status, 'finished_at': datetime.now(timezone.utc).isoformat(),
                  'components': {name: {'pid': p.pid, 'exit_code': p.returncode, 'log': log.name}
                                 for name, p, log in children}}
        (folder / 'status.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print('Компоненты остановлены. База, очередь и журналы сохранены.', flush=True)
    return exit_code


def main():
    parser = argparse.ArgumentParser(description='Единый запуск мониторинга в Ubuntu/WSL')
    parser.add_argument('--database', type=Path, default=ROOT / 'results/raw/monitor-dev-001.sqlite')
    parser.add_argument('--evidence-dir', type=Path, default=ROOT / 'results/raw/monitor-incidents')
    parser.add_argument('--log-dir', type=Path, default=ROOT / 'results/raw/monitoring-runs')
    parser.add_argument('--url', default='http://127.0.0.1:8000/health/ready')
    parser.add_argument('--service-url', default='http://127.0.0.1:8000')
    parser.add_argument('--port', type=int, default=8081)
    parser.add_argument('--vk-peer-id', type=int, help='Включить реальную отправку VK этому пользователю')
    parser.add_argument('--compose-file', type=Path)
    parser.add_argument('--docker-sudo', action='store_true')
    parser.add_argument('--model-python', type=Path)
    args = parser.parse_args()
    if os.name != 'posix':
        parser.error('Запускайте в Ubuntu/WSL, а не в PowerShell')
    def request_stop(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGHUP, request_stop)
    if not 1 <= args.port <= 65535:
        parser.error('Некорректный порт')
    if args.vk_peer_id is not None and not 0 < args.vk_peer_id < 2000000000:
        parser.error('Нужен числовой ID пользователя VK')
    if args.docker_sudo and not args.compose_file:
        parser.error('--docker-sudo требует --compose-file')
    for field in ('database', 'evidence_dir', 'log_dir', 'compose_file'):
        value = getattr(args, field)
        if value is not None:
            setattr(args, field, value.resolve())
    if args.compose_file and not args.compose_file.is_file():
        parser.error('Файл Compose не найден')
    if args.model_python:
        args.model_python = args.model_python.absolute()  # Preserve virtualenv symlink.
        if not args.model_python.is_file():
            parser.error('Python окружения модели не найден')
    args.database.parent.mkdir(parents=True, exist_ok=True)
    with acquire_lock(str(args.database) + '.launcher.lock'):
        # Detect an updated standalone monitor before starting any component.
        with acquire_lock(str(args.database) + '.monitor.lock'):
            pass
        check_port(args.port)
        token = None
        if args.vk_peer_id:
            print(f'VK: сообщения из очереди будут отправляться пользователю {args.vk_peer_id}.')
            token = os.environ.pop('VK_COMMUNITY_TOKEN', '').strip()
            if not token:
                token = getpass.getpass('Токен сообщества VK (ввод скрыт): ').strip()
            if not token:
                parser.error('Токен не введён')
        return supervise(args, token)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit('\nЗапуск отменён.')
    except (OSError, ValueError) as exc:
        raise SystemExit(str(exc))
