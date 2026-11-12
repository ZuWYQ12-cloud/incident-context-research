"""Compose an evidence report without treating model prose as verified facts."""
import argparse
import hashlib
import json
from pathlib import Path


def literal(text):
    # A model-supplied heading or fence must remain inside the literal block.
    longest = max((len(part) for part in __import__('re').findall(r'`+', text)), default=0)
    fence = '`' * max(3, longest + 1)
    return f'{fence}text\n{text}\n{fence}'


def compose(facts, observations, answer):
    if facts.get('schema_version') != '0.1' or facts.get('purpose') != 'development_signature_extraction':
        raise ValueError('Unsupported diagnostic facts format')
    records = observations.get('observations')
    if not isinstance(records, list):
        raise ValueError('Missing observations')
    source = {}
    for item in records:
        identifier = item.get('id')
        text = item.get('text', item.get('raw_text'))
        if not isinstance(identifier, str) or not identifier or identifier in source:
            raise ValueError('Invalid or duplicate observation ID')
        if not isinstance(text, str):
            raise ValueError('Missing observation text')
        if 'text' in item and 'raw_text' in item and item['text'] != item['raw_text']:
            raise ValueError('Conflicting observation text')
        source[identifier] = text
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError('Empty model answer')
    entries = facts.get('facts')
    if not isinstance(entries, list):
        raise ValueError('Missing facts list')
    checked = 0
    for fact in entries:
        for key in ('signature', 'meaning', 'next_check'):
            if not isinstance(fact.get(key), str) or not fact[key].strip():
                raise ValueError(f'Invalid fact field: {key}')
        if not isinstance(fact.get('not_established'), list) or not all(
            isinstance(value, str) for value in fact['not_established']
        ):
            raise ValueError('Invalid limitations')
        if not isinstance(fact.get('evidence'), list) or not fact['evidence']:
            raise ValueError('Fact without evidence')
        for evidence in fact['evidence']:
            identifier = evidence.get('observation_id')
            quote = evidence.get('quote')
            start, end = evidence.get('start_char'), evidence.get('end_char')
            if identifier not in source or not isinstance(quote, str) or not quote:
                raise ValueError('Missing evidence or unknown observation')
            if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(source[identifier]):
                raise ValueError('Invalid evidence offsets')
            if source[identifier][start:end] != quote:
                raise ValueError(f'Evidence mismatch: {identifier}')
            checked += 1
    # Deep copy: later mutation of the input must not rewrite this report.
    return {
        'schema_version': '0.1',
        'purpose': 'development_separated_diagnostic_report',
        'observed_signatures': json.loads(json.dumps(entries)),
        'verification': {
            'evidence_quotes_checked': checked,
            'quotes_match_supplied_observations': True,
            'model_claims_checked': False,
            'source_authenticity_checked': False,
            'temporal_eligibility_checked': False,
            'rule_semantics_checked_by_report_builder': False,
        },
        'model_output': {'status': 'not_automatically_verified', 'text': answer},
    }


def render(report):
    lines = ['# Диагностический отчёт', '',
        '## Факты из предоставленных наблюдений', '',
        'Ниже — распознанные сообщения об ошибках и их трактовки по правилам. '
        'Цитаты сверены с входными наблюдениями. Это не подтверждение первопричины, '
        'подлинности журналов или временной допустимости данных.']
    if not report['observed_signatures']:
        lines += ['', 'Поддерживаемые сигнатуры не найдены. Это не означает отсутствия сбоя.']
    for fact in report['observed_signatures']:
        lines += ['', '### ' + fact['signature'], '', 'Трактовка правила:', '', literal(fact['meaning'])]
        for evidence in fact['evidence']:
            lines += ['', 'Источник:', '', literal(evidence['observation_id']), '', literal(evidence['quote'])]
        lines += ['', 'Границы вывода по правилу:', '', literal('\n'.join(fact['not_established'])),
                  '', 'Предлагаемая правилом проверка (не выполнена автоматически):', '', literal(fact['next_check'])]
    lines += ['', '## Ответ модели — не проверен автоматически', '',
              'Этот раздел может содержать ошибки и противоречия. Он не изменяет данные раздела выше.', '',
              literal(report['model_output']['text']), '',
              '## Происхождение отчёта', '', literal(json.dumps(report.get('provenance', {}), ensure_ascii=False, indent=2))]
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--facts', type=Path, required=True)
    parser.add_argument('--observations', type=Path, required=True)
    parser.add_argument('--answer', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    paths = {'facts': args.facts, 'observations': args.observations, 'answer': args.answer}
    raw = {name: path.read_bytes() for name, path in paths.items()}
    report = compose(json.loads(raw['facts'].decode('utf-8-sig')),
                     json.loads(raw['observations'].decode('utf-8-sig')),
                     raw['answer'].decode('utf-8'))
    report['provenance'] = {
        name: {'path': path.as_posix(), 'sha256': hashlib.sha256(raw[name]).hexdigest()}
        for name, path in paths.items()
    }
    report['provenance']['builder_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report['provenance']['pairing_note'] = 'Caller supplies the pairing; model prompt equivalence is not checked here.'
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (args.output_dir / 'report.md').write_text(render(report), encoding='utf-8')
    print(f"Проверено цитат: {report['verification']['evidence_quotes_checked']}. Отчёт: {args.output_dir}")


if __name__ == '__main__':
    main()
