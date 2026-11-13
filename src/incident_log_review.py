"""Read saved container logs; only verified, successful captures enter analysis."""
import hashlib
import json
from pathlib import Path
from extract_diagnostic_facts import extract


def review_logs(incident_directory, incident_id):
    root = Path(incident_directory) / 'container-logs'
    result = {'status': 'missing', 'sources': [], 'facts': [],
              'limitations': [
                  'Контрольная сумма подтверждает соответствие файлу манифеста, а не подлинность журнала.',
                  'Окно журналов включает время до и после обнаружения. Совпадение сигнатуры не доказывает связь с этим сбоем.',
                  'Полнота журналов неизвестна. Отсутствие сигнатур не означает отсутствие ошибки.',
                  'Разбор по четырём правилам; модель не запускалась.',
              ]}
    if not root.exists():
        return result
    try:
        manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
        if manifest.get('incident_id') != incident_id or manifest.get('schema') != 1:
            raise ValueError('Манифест не соответствует инциденту или схеме')
        sources = manifest['sources']
        if (not isinstance(sources, list) or len(sources) > 2
                or any(not isinstance(s, dict) or s.get('service') not in ('api', 'db') for s in sources)
                or len({s['service'] for s in sources}) != len(sources)):
            raise ValueError('Некорректный список источников')
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        result.update(status='unreadable', error=str(exc))
        return result
    result.update(status='available', collection_status=manifest.get('status'),
                  since=manifest.get('since'), until=manifest.get('until'))
    observations = []
    for source in sources:
        service = source['service']
        row = {'service': service, 'status': 'unverified', 'text': None,
               'collected_at': source.get('collected_at'), 'error': None}
        result['sources'].append(row)
        try:
            descriptor = source['stdout']
            filename = f'{service}.stdout.log'
            if descriptor['file'] != filename:
                raise ValueError('Неожиданное имя файла журнала')
            path = root / filename
            if path.is_symlink() or path.resolve().parent != root.resolve():
                raise ValueError('Файл журнала расположен вне папки материалов')
            with path.open('rb') as stream:
                raw = stream.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise ValueError('Превышен размер журнала')
            if hashlib.sha256(raw).hexdigest() != descriptor['sha256'] or len(raw) != descriptor['bytes']:
                raise ValueError('Контрольная сумма или размер не совпадают')
            text = raw.decode('utf-8')
            row['text'] = text
            row['hash_verified'] = True
            if source.get('exit_code') != 0 or source.get('timed_out') or source.get('status') != 'collected':
                row.update(status='collection_failed', error='Выгрузка завершилась неуспешно; текст исключён из разбора.')
            elif descriptor.get('truncated'):
                row.update(status='truncated', error='Файл обрезан; текст исключён из разбора.')
            elif not text.strip():
                row['status'] = 'empty'
            else:
                row['status'] = 'verified'
                observations.append({'id': service.upper(), 'text': text})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            row.update(status='invalid', error=str(exc), text=None)
    result['facts'] = extract({'observations': observations})['facts']
    result['analyzed_sources'] = [o['id'] for o in observations]
    return result
