"""Save checks and provenance for two existing runs; never generate or edit answers."""
import argparse
import hashlib
import json
from pathlib import Path
from incident_model_view import load_review_directory


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_comparison(baseline, timeline, output):
    runs = []
    for root in (Path(baseline), Path(timeline)):
        prep = json.loads((root / 'preparation.json').read_text(encoding='utf-8'))
        view = load_review_directory(root, prep['incident_id'])
        if view['status'] != 'available':
            raise ValueError(f'Результат не прошёл проверку: {root} ({view["status"]})')
        meta = json.loads((root / 'generation/metadata.json').read_text(encoding='utf-8'))
        runs.append({'root': str(root), 'preparation': prep, 'metadata': meta,
                     'answer_sha256': digest(root / 'generation/answer.txt'),
                     'metadata_sha256': digest(root / 'generation/metadata.json'),
                     'automatic_checks': view['automatic_checks']})
    first, second = runs
    if first['preparation']['incident_id'] != second['preparation']['incident_id']:
        raise ValueError('Разные инциденты')
    if first['preparation']['source_sha256'] != second['preparation']['source_sha256']:
        raise ValueError('Исходные материалы различаются')
    if first['preparation'].get('context_mode', 'baseline') != 'baseline' or second['preparation'].get('context_mode') != 'timeline':
        raise ValueError('Ожидается пара baseline и timeline')
    for key in ('model', 'revision', 'seed', 'generation', 'dtype', 'enable_thinking', 'torch_version', 'transformers_version', 'gpu'):
        if key not in first['metadata'] or key not in second['metadata'] or first['metadata'][key] != second['metadata'][key]:
            raise ValueError(f'Не совпадает или отсутствует параметр: {key}')
    result = {'purpose': 'development_comparison', 'independent_evaluation': False,
              'checker_sha256': digest(Path(__file__).with_name('model_answer_checks.py')),
              'incident_id': first['preparation']['incident_id'], 'runs': runs,
              'limitation': 'Один случай и seed, использованные при разработке. Автопроверки не являются экспертной оценкой и не доказывают эффективность метода.'}
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    (output / 'comparison-review.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# Подготовительное сравнение контекстов', '', result['limitation'], '',
             'Исходные материалы и записанные параметры генерации совпадают.', '']
    for label, run in zip(('Прежний контекст', 'Указатель времени и соединений'), runs):
        meta, checks = run['metadata'], run['automatic_checks']
        lines += ['## ' + label, '', f"Входных токенов: {meta.get('input_tokens')}. Выходных: {meta.get('output_tokens')}. Лимит достигнут: {meta.get('token_limit_reached')}.", '',
                  f"Статус ограниченной проверки: {checks['status']}. Замечаний: {len(checks['findings'])}.", '']
        for finding in checks['findings']:
            lines += [f"Код: {finding['code']}", '', '> ' + finding['quote'].replace('\n', '\n> '), '', finding['reason'], '']
        lines += [checks['limitations'], '']
    (output / 'comparison-review.md').write_text('\n'.join(lines), encoding='utf-8')
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--timeline', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    print('Сравнение сохранено:', save_comparison(args.baseline, args.timeline, args.output_dir))
    print('Модель не запускалась. Исходные ответы не изменены.')
