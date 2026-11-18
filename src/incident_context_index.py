"""Derived time/connection index; never edits or replaces source observations."""
import calendar
import json
import re
from datetime import datetime

STAMP = r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})'
TIME = re.compile(r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})$')
LINE = re.compile(r'^(?:[^|\r\n]+\|\s*)?(?P<stamp>' + STAMP + r')\s+(?P<message>.*)$')


def nanoseconds(value):
    if not isinstance(value, str):
        return None
    match = TIME.fullmatch(value)
    if not match:
        return None
    try:
        moment = datetime.fromisoformat(match[1] + match[3].replace('Z', '+00:00'))
        return calendar.timegm(moment.utctimetuple()) * 10**9 + int((match[2] or '').ljust(9, '0'))
    except (ValueError, OverflowError):
        return None


def phase(start, end, first_bad, confirmed):
    values = [nanoseconds(value) for value in (start, end, first_bad, confirmed)]
    if any(value is None for value in values):
        return 'unknown'
    a, b, first, confirmation = values
    if a > b or first > confirmation:
        return 'unknown'
    if b < first:
        return 'before_first_failed_check'
    if a >= confirmation:
        return 'at_or_after_confirmation'
    if a >= first and b < confirmation:
        return 'between_first_failure_and_confirmation'
    return 'crosses_boundary'


def build_index(trigger, observations):
    first, confirmed = trigger.get('first_bad_at'), trigger.get('confirmed_at')
    entries = []
    for obs in observations:
        identifier = obs['id']
        if identifier.startswith('HTTP-'):
            start, end = obs.get('started_at'), obs.get('collected_at')
            entries.append({'ref': identifier, 'time_basis': 'request_interval',
                'start': start, 'end': end, 'phase': phase(start, end, first, confirmed),
                'connection': 'monitor_to_api', 'http_status': obs.get('http_status'),
                'scope': 'Только запрос к указанному URL; ошибка соединения не описывает API → БД.'})
            continue
        for number, line in enumerate(obs.get('text', '').splitlines(), 1):
            match = LINE.match(line)
            stamp = match['stamp'] if match else None
            entry = {'ref': identifier, 'line': number, 'time_basis': 'docker_log_timestamp',
                'time': stamp, 'phase': phase(stamp, stamp, first, confirmed),
                'connection': 'unknown'}
            # A journal belongs to a service, not necessarily to a specific connection.
            # Only the explicit HTTP request record gives a direction here.
            if identifier == 'API' and match:
                try:
                    record = json.loads(match['message'])
                except (ValueError, TypeError):
                    record = None
                if (isinstance(record, dict) and record.get('event') == 'http_request'
                        and isinstance(record.get('method'), str)
                        and isinstance(record.get('path'), str)
                        and type(record.get('status_code')) is int
                        and 100 <= record['status_code'] <= 599):
                    entry.update(connection='client_to_api', method=record['method'],
                                 path=record['path'], http_status=record['status_code'])
            entries.append(entry)
    return {'version': 1, 'first_failed_check_at': first, 'confirmed_at': confirmed,
        'phase_labels': {
            'before_first_failed_check': 'До первого неуспешного замера монитора; это не доказательство исправности всех маршрутов.',
            'between_first_failure_and_confirmation': 'От первого неуспешного замера до подтверждения инцидента.',
            'at_or_after_confirmation': 'В момент подтверждения или позже; время восстановления здесь не задано.',
            'crosses_boundary': 'Интервал пересекает границу; его нельзя целиком отнести к одному периоду.',
            'unknown': 'Не хватает корректного времени с часовым поясом или порядок границ некорректен.'},
        'limitations': [
            'Это производный указатель, а не новые наблюдения. ref и line ссылаются на исходный текст.',
            'Время Docker — время строки журнала, не гарантированное время события приложения. Синхронизация часов не проверена.',
            'Первый неуспешный замер не равен точному моменту начала сбоя. Порядок событий не доказывает причинную связь.',
            'client_to_api не устанавливает личность клиента. unknown не означает отсутствие соединения.',
            'Соединение API → БД нельзя выводить из HTTP-ошибки или имени источника API/DB. Нужна явная запись о таком соединении.',
            'HTTP-код относится только к указанному запросу и времени. null означает, что HTTP-код не получен, а не код 200.'
        ], 'entries': entries}
