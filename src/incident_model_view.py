"""Display a saved model answer without treating it as verified evidence."""
import hashlib
import json
from pathlib import Path


def load_model_review(incident_directory, incident_id):
    root = Path(incident_directory) / 'model-review-001'
    if not root.exists():
        return {'status': 'missing'}
    def read(path, limit=4 * 1024 * 1024):
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('Файл вне папки разбора')
        with path.open('rb') as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise ValueError('Файл превышает ограничение размера')
        return raw
    try:
        record = json.loads(read(root / 'preparation.json'))
        if record['incident_id'] != incident_id or record.get('purpose') != 'retrospective_model_review':
            raise ValueError('Подготовленный запрос относится к другому инциденту или режиму')
        prompt = read(root / 'prompt.txt')
        if hashlib.sha256(prompt).hexdigest() != record['prompt_sha256']:
            raise ValueError('Изменён подготовленный запрос')
        allowed = {'manifest.json', 'health_live.body', 'health_ready.body', 'tasks.body',
                   'container-logs/manifest.json', 'container-logs/api.stdout.log',
                   'container-logs/db.stdout.log'}
        hashes = record['source_sha256']
        if not isinstance(hashes, dict) or 'manifest.json' not in hashes:
            raise ValueError('Нет перечня исходных материалов')
        for name, expected in hashes.items():
            if name not in allowed:
                raise ValueError('Неожиданное имя исходного файла')
            if hashlib.sha256(read(root / 'evidence' / name)).hexdigest() != expected:
                raise ValueError('Изменён снимок исходных материалов')
        generation = root / 'generation'
        if not (generation / 'metadata.json').is_file() or not (generation / 'answer.txt').is_file():
            return {'status': 'incomplete'}
        if read(generation / 'prompt.txt') != prompt:
            raise ValueError('Запрос генерации не совпадает с подготовленным')
        metadata = json.loads(read(generation / 'metadata.json'))
        answer = read(generation / 'answer.txt').decode('utf-8')
        if not answer.strip():
            raise ValueError('Ответ пуст')
        return {'status': 'available', 'answer': answer,
                'prepared_at': record.get('prepared_at'),
                'model': metadata.get('model'), 'seed': metadata.get('seed'),
                'output_tokens': metadata.get('output_tokens'),
                'token_limit_reached': metadata.get('token_limit_reached'),
                'warning': 'Ответ модели требует проверки. Версии причин и ссылки могут быть ошибочными. '
                           'Проверено соответствие запроса и снимка материалов; содержание ответа автоматически не проверено. '
                           'Ответ показан из сохранённого файла, его подлинность не удостоверена.'}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return {'status': 'invalid', 'error': str(exc)}
