"""Narrow heuristic checks. Findings are review prompts, never a truth verdict."""
import json
import re
from urllib.parse import urlsplit


def check_http_claims(answer, observations):
    """Narrow same-clause checks against captured HTTP probes, not all log events."""
    routes = {}
    for row in observations:
        if row['id'].startswith('HTTP-') and isinstance(row.get('url'), str):
            path = urlsplit(row['url']).path
            if path:
                routes.setdefault(path, []).append(row)
    findings = []
    for match in re.finditer(r'[^\r\n;]+', answer):
        clause = match.group()
        if re.search(r'не подтвержден|не установлен|не доказан|не означает|не доказывает|'
                     r'гипотез|возможно|может быть|проверить|неизвестно|'
                     r'ранее|до сбоя|до отказа|после восстановления|не вернул', clause, re.I):
            continue
        codes = set(re.findall(r'\b[1-5]\d{2}\b', clause))
        for path, rows in routes.items():
            if not re.search(re.escape(path) + r'(?![\w/.-])', clause):
                continue
            statuses = {row.get('http_status') for row in rows}
            if len(statuses) != 1:
                continue  # Multiple observations need explicit temporal association.
            status = next(iter(statuses))
            code, reason = None, None
            if len(codes) == 1 and re.search(r'HTTP|код|вернул|возврат', clause, re.I):
                claimed = int(next(iter(codes)))
                if claimed != status:
                    code = 'possible_http_status_mismatch'
                    reason = f'Для {path} в сохранённых HTTP-наблюдениях: {status if status is not None else "ответ не получен"}; в тексте указан {claimed}. Проверьте маршрут и время утверждения.'
            if status == 200 and re.search(r'потер[яи]\s+доступа|недоступен|недоступна|не отвечает', clause, re.I):
                code = 'possible_http_availability_mismatch'
                reason = f'Проверка {path} получила HTTP 200. Утверждение о недоступности требует проверки времени и смысла; один ответ не доказывает постоянную доступность.'
            if code:
                findings.append({'code': code, 'quote': clause, 'start_char': match.start(),
                    'end_char': match.end(), 'evidence_refs': [row['id'] for row in rows], 'reason': reason})
    return findings


def prompt_observations(prompt):
    # The preparer places one original payload before any derived index.
    start = prompt.find('\n{')
    if start < 0:
        raise ValueError('Не найден исходный блок наблюдений')
    payload, _ = json.JSONDecoder().raw_decode(prompt[start + 1:])
    observations = payload.get('observations')
    if not isinstance(observations, list) or any(
        not isinstance(row, dict) or not isinstance(row.get('id'), str) for row in observations
    ):
        raise ValueError('Формат наблюдений не поддерживается')
    return observations


def check_answer(answer, prompt):
    limits = ('Проверяются известные шаблоны ссылок, приписывания отказа БД и утверждений о HTTP-кодах и доступности маршрутов. '
              'Возможны пропуски и ложные срабатывания. Отсутствие замечаний не подтверждает правильность ответа. '
              'Проверка не устанавливает первопричину и не заменяет оценку инженера.')
    try:
        observations = prompt_observations(prompt)
    except (ValueError, TypeError, AttributeError) as exc:
        return {'status': 'not_checked', 'version': 1, 'findings': [], 'limitations': limits, 'reason': str(exc)}
    identifiers = {row['id'] for row in observations}
    findings = check_http_claims(answer, observations)
    # Bare API/DB in prose are component names, not automatically citations.
    refs = re.compile(r'\b(?:HTTP-\d+|E\d+)\b|\[\s*(?:API|DB)\s*\]|\(\s*(?:API|DB)\s*\)|\bID:\s*(?:API|DB)\b')
    for match in refs.finditer(answer):
        reference = re.search(r'HTTP-\d+|E\d+|API|DB', match.group()).group()
        if reference not in identifiers:
            findings.append({'code': 'unknown_reference', 'quote': match.group(),
                'start_char': match.start(), 'end_char': match.end(),
                'reason': f'Идентификатор {reference} отсутствует среди наблюдений запроса.'})
    refused = [row['id'] for row in observations if row['id'].startswith('HTTP-')
               and row.get('http_status') is None
               and 'connection refused' in str(row.get('error', '')).lower()]
    # Recognise only explicit connection diagnostics, not the empty source's name.
    db_connection_evidence = any(
        (row['id'] == 'DB' or re.search(r'psycopg|postgres', row.get('text', ''), re.I))
        and re.search(r'password authentication failed|connection failed|could not connect|connection refused',
                      row.get('text', ''), re.I)
        for row in observations if isinstance(row.get('text'), str)
    )
    if refused and not db_connection_evidence:
        for match in re.finditer(r'[^\r\n]+', answer):
            line = match.group()
            db = re.search(r'\bБД\b|PostgreSQL|баз[а-яё]*\s+данных', line, re.I)
            failure = re.search(r'не может связаться|не\s+может\s+подключиться|отказ|отклон|недоступ|отсутстви[а-яё]*\s+доступ|connection refused', line, re.I)
            assertion = re.search(r'подтвержден[оа]?|поддерживается|из-за|вызван[а-яё]*|не может связаться|не может подключиться', line, re.I)
            qualification = re.search(r'не подтвержден|не установлен|не доказан|не означает|не доказывает|гипотез|возможно|может быть|проверить|неизвестно', line, re.I)
            if db and failure and assertion and not qualification:
                findings.append({'code': 'possible_db_misattribution', 'quote': line,
                    'start_char': match.start(), 'end_char': match.end(),
                    'evidence_refs': refused,
                    'reason': 'В HTTP-наблюдениях отказ относится к соединению монитор → API. '
                              'Известные шаблоны ошибок соединения с БД в переданных журналах не найдены. '
                              'Утверждение о БД требует ручной проверки; отсутствие совпадения не доказывает исправность БД.'})
    return {'status': 'warnings' if findings else 'no_matches', 'version': 2,
            'findings': findings, 'limitations': limits}
