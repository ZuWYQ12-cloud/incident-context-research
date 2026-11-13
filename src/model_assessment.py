"""Human assessments are separate from model output and deterministic findings."""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

VERDICTS = {'supported': 'Замечаний по обоснованности не обнаружено',
            'issues_found': 'Обнаружены замечания', 'uncertain': 'Недостаточно данных для оценки'}
BOUND_FILES = ('preparation.json', 'prompt.txt', 'generation/prompt.txt',
               'generation/answer.txt', 'generation/metadata.json')


def hashes(root):
    result = {}
    for name in BOUND_FILES:
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('Некорректный путь к материалам оценки')
        result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def save_assessment(root, reviewer, verdict, note):
    root = Path(root)
    if verdict not in VERDICTS or not reviewer.strip() or not note.strip():
        raise ValueError('Укажите автора, оценку и комментарий')
    if len(note) > 10000 or len(reviewer) > 200:
        raise ValueError('Слишком длинный комментарий или имя автора')
    record = {'schema': 1, 'reviewer': reviewer.strip(), 'verdict': verdict,
              'note': note.strip(), 'reviewed_at': datetime.now(timezone.utc).isoformat(),
              'artifact_sha256': hashes(root),
              'scope': 'Ручная оценка одного ответа; не независимая экспертная проверка.'}
    # Exclusive creation protects an existing assessment from accidental replacement.
    with (root / 'human-assessment.json').open('x', encoding='utf-8') as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    return root / 'human-assessment.json'


def load_assessment(root):
    root = Path(root)
    path = root / 'human-assessment.json'
    if not path.exists():
        return {'status': 'missing'}
    try:
        if path.is_symlink() or path.stat().st_size > 100000:
            raise ValueError('Некорректный файл оценки')
        record = json.loads(path.read_text(encoding='utf-8'))
        if record.get('schema') != 1 or record.get('verdict') not in VERDICTS:
            raise ValueError('Неизвестный формат оценки')
        if record['artifact_sha256'] != hashes(root):
            return {'status': 'stale', 'message': 'Ответ или материалы изменились после оценки. Оценка не применяется.'}
        if not all(isinstance(record.get(k), str) and record[k].strip() for k in ('reviewer', 'note', 'reviewed_at', 'scope')):
            raise ValueError('Оценка заполнена не полностью')
        return {**record, 'status': 'current', 'label': VERDICTS[record['verdict']]}
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        return {'status': 'invalid', 'message': str(exc)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('incident', type=Path)
    parser.add_argument('--reviewer', required=True)
    parser.add_argument('--verdict', choices=VERDICTS, required=True)
    parser.add_argument('--note', required=True)
    args = parser.parse_args()
    from incident_model_view import load_model_review
    incident = args.incident.resolve()
    if load_model_review(incident, incident.name)['status'] != 'available':
        parser.error('Ответ модели или его материалы не прошли проверку')
    print('Сохранено:', save_assessment(incident / 'model-review-001', args.reviewer, args.verdict, args.note))
