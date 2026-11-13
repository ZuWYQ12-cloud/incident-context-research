"""Collect a later HTTP snapshot, not evidence of the exact detection instant."""
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import build_opener, HTTPRedirectHandler


def now():
    return datetime.now(timezone.utc).isoformat()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch(url, timeout=3, max_bytes=65536):
    started = now()
    timer = time.monotonic()
    status, error, body, headers, truncated = None, None, b'', [], False
    response = None
    try:
        try:
            response = build_opener(NoRedirect).open(url, timeout=timeout)
        except HTTPError as exc:
            response = exc
        status = response.code
        headers = list(response.headers.items())
        body = response.read(max_bytes + 1)
        truncated = len(body) > max_bytes
        body = body[:max_bytes]
    except (URLError, OSError, TimeoutError) as exc:
        error = str(exc)
    finally:
        if response is not None:
            response.close()
    return body, {'url': url, 'started_at': started, 'collected_at': now(),
                  'http_status': status, 'error': error, 'headers': headers,
                  'body_truncated': truncated,
                  'duration_seconds': round(time.monotonic() - timer, 6)}


def collect(root, base_url, event, timeout=3):
    """One immutable directory per incident. Existing captures are never replaced."""
    parsed = urlsplit(base_url)
    if (parsed.scheme not in ('http', 'https') or not parsed.netloc
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ('', '/')):
        raise ValueError('Укажите адрес сервиса без пути, пароля и параметров')
    identifier = event['incident_id']
    if len(identifier) != 32 or any(c not in '0123456789abcdef' for c in identifier):
        raise ValueError('Invalid incident ID')
    if event['event_type'] != 'incident_opened':
        raise ValueError('Expected incident_opened')
    directory = Path(root) / identifier
    directory.mkdir(parents=True, exist_ok=False)
    manifest = {'schema': 1, 'status': 'collecting', 'trigger': event,
                'started_at': now(), 'observations': [],
                'limitation': 'Последовательные проверки после обнаружения. '
                              'Не описывают точный момент сбоя и не устанавливают причину.'}

    def save():
        temporary = directory / 'manifest.tmp'
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        temporary.replace(directory / 'manifest.json')

    save()
    try:
        for endpoint in ('health/live', 'health/ready', 'tasks'):
            body, metadata = fetch(base_url.rstrip('/') + '/' + endpoint, timeout)
            name = endpoint.replace('/', '_') + '.body'
            (directory / name).write_bytes(body)
            metadata.update({'body_file': name, 'body_sha256': hashlib.sha256(body).hexdigest()})
            manifest['observations'].append(metadata)
            save()
        manifest['status'] = 'completed'
        manifest['finished_at'] = now()
        save()
    except Exception as exc:
        manifest.update(status='failed', error=str(exc), finished_at=now())
        save()
        raise
    return directory
