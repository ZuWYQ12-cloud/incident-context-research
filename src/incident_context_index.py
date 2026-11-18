"""Derived time/connection index; never edits or replaces source observations."""
import calendar
import json
import re
from datetime import datetime


def compact_index(index):
    """Shared layouts with ordered rows; retain every entry without rounding."""
    result = {key: value for key, value in index.items() if key != 'entries'}
    result['encoding'] = 'shared_layouts_v2'
    result['reading'] = ('Первое число каждой строки rows — индекс схемы в layouts (с нуля). '
                         'Остальные значения соответствуют columns этой схемы по порядку. '
                         'Поля common схемы относятся к этой строке. '
                         'Все записи сохранены в исходном порядке; строки не исключались.')
    layouts, rows = [], []
    for entry in index['entries']:
        common = {key: entry[key] for key in ('ref', 'time_basis', 'phase', 'connection')
                  if key in entry}
        columns = [key for key in entry if key not in common]
        layout = {'common': common, 'columns': columns}
        if layout not in layouts:
            layouts.append(layout)
        rows.append([layouts.index(layout)] + [entry[key] for key in columns])
    result['layouts'], result['rows'] = layouts, rows
    return result


def expand_index(compact):
    result = {key: value for key, value in compact.items()
              if key not in ('encoding', 'reading', 'groups', 'layouts', 'rows')}
    if compact['encoding'] == 'shared_layouts_v2':
        result['entries'] = []
        for row in compact['rows']:
            layout = compact['layouts'][row[0]]
            result['entries'].append(dict(layout['common'], **dict(zip(layout['columns'], row[1:]))))
    else:
        result['entries'] = [dict(group['common'], **dict(zip(group['columns'], row)))
                             for group in compact['groups'] for row in group['rows']]
    return result

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
