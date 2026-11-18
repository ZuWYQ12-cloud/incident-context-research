"""Narrow heuristic checks. Findings are review prompts, never a truth verdict."""
import json
import re


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
    limits = ('Проверяются только известные шаблоны ссылок и приписывания отказа БД. '
              'Возможны пропуски и ложные срабатывания. Отсутствие замечаний не подтверждает правильность ответа. '
              'Проверка не устанавливает первопричину и не заменяет оценку инженера.')
    try:
        observations = prompt_observations(prompt)
    except (ValueError, TypeError, AttributeError) as exc:
        return {'status': 'not_checked', 'version': 1, 'findings': [], 'limitations': limits, 'reason': str(exc)}
    identifiers = {row['id'] for row in observations}
    findings = []
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
    return {'status': 'warnings' if findings else 'no_matches', 'version': 1,
            'findings': findings, 'limitations': limits}
