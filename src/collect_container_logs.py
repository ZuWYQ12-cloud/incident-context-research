"""Optional, bounded-time collection of Docker Compose logs, without a shell."""
import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


def collect_logs(directory, compose_file, event, use_sudo=False, timeout=15):
    compose_file = Path(compose_file).resolve()
    if not compose_file.is_file():
        raise ValueError('Файл Compose не найден')
    first = datetime.fromisoformat(event['first_bad_at'])
    if first.tzinfo is None:
        raise ValueError('Время первого отказа должно содержать часовой пояс')
    since = (first.astimezone(timezone.utc) - timedelta(seconds=60)).isoformat()
    until = now()
    output = Path(directory) / 'container-logs'
    output.mkdir(parents=True, exist_ok=False)
    manifest = {'schema': 1, 'status': 'collecting', 'incident_id': event['incident_id'],
                'since': since, 'until': until, 'started_at': now(), 'sources': [],
                'limitation': 'До 200 последних строк на сервис в заданном окне. '
                              'Полнота неизвестна. Время получения позже обнаружения сбоя; '
                              'отсутствие строк не доказывает отсутствие события.'}

    def save():
        path = output / 'manifest.tmp'
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        path.replace(output / 'manifest.json')

    save()
    for service in ('api', 'db'):
        command = (['sudo', '-n'] if use_sudo else []) + [
            'docker', 'compose', '-f', str(compose_file), 'logs', '--no-color',
            '--timestamps', '--since', since, '--until', until, '--tail', '200', service]
        row = {'service': service, 'command': command, 'started_at': now(),
               'exit_code': None, 'timed_out': False, 'error': None, 'completeness': 'unknown'}
        stdout, stderr = b'', b''
        try:
            result = subprocess.run(command, stdin=subprocess.DEVNULL,
                                    capture_output=True, timeout=timeout, check=False)
            stdout, stderr = result.stdout, result.stderr
            row['exit_code'] = result.returncode
        except subprocess.TimeoutExpired as exc:
            stdout, stderr = exc.stdout or b'', exc.stderr or b''
            row.update(timed_out=True, error='Превышен лимит времени выгрузки')
        except OSError as exc:
            row['error'] = str(exc)
        row['collected_at'] = now()
        row['status'] = 'collected' if row['exit_code'] == 0 else 'failed'
        for label, raw in (('stdout', stdout), ('stderr', stderr)):
            # Bound persisted artifacts even for unusually long individual log lines.
            data = raw[:2 * 1024 * 1024]
            name = f'{service}.{label}.log'
            (output / name).write_bytes(data)
            row[label] = {'file': name, 'sha256': hashlib.sha256(data).hexdigest(),
                          'bytes': len(data), 'truncated': len(data) < len(raw)}
        manifest['sources'].append(row)
        save()
    manifest['status'] = ('completed' if all(s['status'] == 'collected' for s in manifest['sources'])
                          else 'partial_or_failed')
    manifest['finished_at'] = now()
    save()
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--compose-file', type=Path, required=True)
    parser.add_argument('--sudo', action='store_true')
    args = parser.parse_args()
    # A standalone check is explicitly labelled; it is not attached to an old incident.
    event = {'incident_id': 'collector-smoke-check', 'first_bad_at': now()}
    path = collect_logs(args.output_dir, args.compose_file, event, args.sudo)
    print('Проверка сборщика сохранена:', path)
    print((path / 'manifest.json').read_text(encoding='utf-8'))


if __name__ == '__main__':
    main()
