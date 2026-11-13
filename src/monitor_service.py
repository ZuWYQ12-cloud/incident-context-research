"""Single-process HTTP monitor. Records events locally; sends no messages."""
import argparse
import json
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import build_opener, HTTPRedirectHandler


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def probe(url, timeout):
    started = time.monotonic()
    status, error = None, None
    try:
        with build_opener(NoRedirect).open(url, timeout=timeout) as response:
            status = response.status
    except HTTPError as exc:
        status = exc.code
        exc.close()
    except (URLError, OSError, TimeoutError) as exc:
        error = str(exc)
    return {'checked_at': utc_now(), 'ok': status == 200,
            'http_status': status, 'error': error,
            'duration_seconds': round(time.monotonic() - started, 6)}


class Monitor:
    def __init__(self, database, url, failures=3, successes=2):
        if failures < 1 or successes < 1:
            raise ValueError('Thresholds must be positive')
        self.db = sqlite3.connect(database)
        self.db.row_factory = sqlite3.Row
        self.failures, self.successes = failures, successes
        with self.db:
            self.db.execute('CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, value TEXT NOT NULL)')
            self.db.execute('''CREATE TABLE IF NOT EXISTS state (
                id INTEGER PRIMARY KEY, status TEXT NOT NULL, bad INTEGER NOT NULL,
                good INTEGER NOT NULL, first_bad TEXT, incident_id TEXT)''')
            self.db.execute('''CREATE TABLE IF NOT EXISTS checks (
                id INTEGER PRIMARY KEY, checked_at TEXT NOT NULL, ok INTEGER NOT NULL,
                http_status INTEGER, error TEXT, duration_seconds REAL NOT NULL)''')
            self.db.execute('''CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, event_type TEXT NOT NULL,
                incident_id TEXT NOT NULL, confirmed_at TEXT NOT NULL,
                first_bad_at TEXT, check_id INTEGER NOT NULL)''')
            config = json.dumps({'schema': 1, 'url': url, 'failures': failures, 'successes': successes}, sort_keys=True)
            existing = self.db.execute('SELECT value FROM settings WHERE id=1').fetchone()
            if existing and existing['value'] != config:
                raise ValueError('Настройки отличаются от сохранённых. Используйте другую базу мониторинга.')
            self.db.execute('INSERT OR IGNORE INTO settings VALUES (1,?)', (config,))
            self.db.execute("INSERT OR IGNORE INTO state VALUES (1,'unknown',0,0,NULL,NULL)")
            # Do not join partial confirmation streaks across monitoring downtime.
            self.db.execute('UPDATE state SET bad=0, good=0, first_bad=NULL WHERE id=1')

    def close(self):
        self.db.close()

    def observe(self, sample):
        event = None
        with self.db:
            check_id = self.db.execute('''INSERT INTO checks
                (checked_at,ok,http_status,error,duration_seconds) VALUES (?,?,?,?,?)''',
                (sample['checked_at'], int(sample['ok']), sample['http_status'],
                 sample['error'], sample['duration_seconds'])).lastrowid
            state = dict(self.db.execute('SELECT * FROM state WHERE id=1').fetchone())
            if sample['ok']:
                state['bad'], state['first_bad'] = 0, None
                state['good'] += 1
                if state['status'] == 'incident' and state['good'] >= self.successes:
                    event = {'event_type': 'recovered', 'incident_id': state['incident_id'],
                             'confirmed_at': sample['checked_at'], 'first_bad_at': None}
                    state['status'], state['incident_id'] = 'healthy', None
                elif state['status'] == 'unknown' and state['good'] >= self.successes:
                    state['status'] = 'healthy'
                state['good'] = min(state['good'], self.successes)
            else:
                state['good'] = 0
                if state['bad'] == 0:
                    state['first_bad'] = sample['checked_at']
                state['bad'] = min(state['bad'] + 1, self.failures)
                if state['status'] != 'incident' and state['bad'] >= self.failures:
                    state['status'], state['incident_id'] = 'incident', uuid.uuid4().hex
                    event = {'event_type': 'incident_opened', 'incident_id': state['incident_id'],
                             'confirmed_at': sample['checked_at'], 'first_bad_at': state['first_bad']}
            self.db.execute('''UPDATE state SET status=?,bad=?,good=?,first_bad=?,incident_id=? WHERE id=1''',
                            tuple(state[k] for k in ('status','bad','good','first_bad','incident_id')))
            if event:
                self.db.execute('''INSERT INTO events
                    (event_type,incident_id,confirmed_at,first_bad_at,check_id) VALUES (?,?,?,?,?)''',
                    (event['event_type'],event['incident_id'],event['confirmed_at'],event['first_bad_at'],check_id))
        return event


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8000/health/ready')
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--interval', type=float, default=5)
    parser.add_argument('--timeout', type=float, default=3)
    parser.add_argument('--max-checks', type=int, default=0, help='0: until Ctrl+C')
    parser.add_argument('--evidence-dir', type=Path)
    parser.add_argument('--service-url', help='HTTP service origin for evidence collection')
    parser.add_argument('--compose-file', type=Path, help='Enable optional api/db log collection')
    parser.add_argument('--docker-sudo', action='store_true', help='Use sudo -n for Docker only')
    args = parser.parse_args()
    if args.compose_file and (not args.evidence_dir or not args.compose_file.is_file()):
        parser.error('--compose-file требует существующий файл и --evidence-dir')
    if args.docker_sudo and not args.compose_file:
        parser.error('--docker-sudo требует --compose-file')
    if bool(args.evidence_dir) != bool(args.service_url):
        parser.error('--evidence-dir и --service-url задаются вместе')
    if args.interval <= 0 or args.timeout <= 0 or args.max_checks < 0:
        parser.error('Invalid interval, timeout or max-checks')
    if not args.url.startswith(('http://', 'https://')):
        parser.error('Expected HTTP(S) URL')
    args.database.parent.mkdir(parents=True, exist_ok=True)
    monitor = Monitor(args.database, args.url)
    print('Мониторинг:', args.url, '\nБаза событий:', args.database, flush=True)
    try:
        count = 0
        while True:
            sample = probe(args.url, args.timeout)
            event = monitor.observe(sample)
            print(json.dumps({'check': sample, 'event': event}, ensure_ascii=False), flush=True)
            if event and event['event_type'] == 'incident_opened' and args.evidence_dir:
                from collect_http_evidence import collect
                try:
                    directory = collect(args.evidence_dir, args.service_url, event, args.timeout)
                    print('HTTP-материалы сохранены:', directory, flush=True)
                except Exception as exc:
                    print('Ошибка сбора HTTP-материалов:', str(exc), flush=True)
                if args.compose_file:
                    from collect_container_logs import collect_logs
                    try:
                        path = collect_logs(args.evidence_dir / event['incident_id'],
                                            args.compose_file, event, args.docker_sudo)
                        print('Результат выгрузки журналов (проверьте manifest.json):', path, flush=True)
                    except Exception as exc:
                        print('Ошибка сбора журналов:', str(exc), flush=True)
            count += 1
            if args.max_checks and count >= args.max_checks:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print('\nМониторинг остановлен; данные сохранены.')
    finally:
        monitor.close()


if __name__ == '__main__':
    main()
