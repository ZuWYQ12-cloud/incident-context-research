"""Conservative, deterministic interpretation of recorded HTTP metadata."""


def summarize_http(evidence):
    result = {
        'title': 'Недостаточно HTTP-материалов для разбора',
        'method': 'Разбор по правилам · модель не запускалась',
        'facts': [],
        'unknowns': [
            'Первопричина и действие, вызвавшее сбой, не установлены.',
            'Проверки выполнялись последовательно после обнаружения; '
            'они не описывают точное состояние в момент открытия инцидента.',
            'Разбор использует метаданные сборщика. Подлинность источника '
            'и контрольные суммы файлов здесь не проверяются.',
        ],
        'checks': [],
    }
    if not evidence or evidence.get('status') == 'unreadable':
        result['checks'] = ['Проверить наличие и читаемость материалов инцидента.']
        return result
    observations = evidence.get('observations', [])
    if evidence.get('status') != 'completed':
        result['unknowns'].append('Сбор не завершён успешно; доступен только частичный набор материалов.')
    kinds = set()
    for number, observation in enumerate(observations, 1):
        code = observation.get('http_status')
        error = observation.get('error')
        reference = f'HTTP-{number}'
        if error:
            if code is None and ('connection refused' in str(error).lower()
                                 or '[winerror 10061]' in str(error).lower()):
                meaning = 'Подключение отклонено; HTTP-ответ не получен.'
                kinds.add('refused')
            else:
                meaning = ('Запрос или чтение ответа завершились ошибкой.' if code is None
                           else f'Получен HTTP {code}, но запрос или чтение ответа завершились ошибкой.')
                kinds.add('transport')
        elif type(code) is int and 100 <= code <= 599:
            meaning = f'Получен HTTP {code}.'
            kinds.add('ok' if code == 200 else 'http_error')
        else:
            meaning = 'В метаданных нет корректного HTTP-кода и описания ошибки.'
            kinds.add('unknown')
        result['facts'].append({
            'reference': reference, 'url': observation.get('url'),
            'collected_at': observation.get('collected_at'),
            'meaning': meaning, 'error': error,
        })
        if observation.get('body_truncated'):
            result['unknowns'].append(f'[{reference}] Тело ответа сохранено не полностью.')
    if not observations:
        result['checks'] = ['Проверить, успел ли сборщик сохранить HTTP-наблюдения.']
        return result
    if kinds == {'refused'}:
        result['title'] = 'Во всех сохранённых проверках подключение отклонено'
    elif kinds == {'ok'}:
        result['title'] = 'При сборе материалов получены успешные HTTP-ответы'
        result['unknowns'].append('Успешные ответы при сборе не отменяют ранее обнаруженный сбой.')
    else:
        result['title'] = 'Сохранены результаты HTTP-проверок'
    if 'refused' in kinds or 'transport' in kinds:
        result['checks'].extend([
            'Проверить состояние API и прослушивание нужного порта со стороны монитора.',
            'Сопоставить адрес подключения и журналы API с временем неуспешных проверок.',
        ])
        result['unknowns'].append('Остановка контейнера, сбой сети или ошибка адреса по этим данным не доказаны.')
    elif 'http_error' in kinds:
        result['checks'].extend([
            'Сопоставить неуспешные запросы с журналом API по времени и идентификатору запроса, если он доступен.',
            'Проверить состояние зависимостей API и их журналы; не считать код HTTP доказательством остановки БД.',
        ])
    else:
        result['checks'] = ['Сопоставить время сбора с историей проверок монитора и журналами API.']
    return result
