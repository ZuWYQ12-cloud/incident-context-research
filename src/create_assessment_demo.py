"""Isolated synthetic fixture for manual browser testing, never a research run."""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from monitor_service import Monitor
from prepare_incident_model import prepare


def create_demo(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    (root / 'DEMO_ONLY.txt').write_text('Синтетические данные для проверки формы. Не включать в исследовательские результаты. Модель не запускалась.\n', encoding='utf-8')
    monitor = Monitor(root / 'monitor.sqlite', 'http://demo.invalid/health/ready')
    stamp = datetime.now(timezone.utc).isoformat()
    try:
        for _ in range(3):
            event = monitor.observe({'checked_at': stamp, 'ok': False, 'http_status': None,
                                     'error': 'DEMO: simulated connection refused', 'duration_seconds': 0})
        for _ in range(2):
            monitor.observe({'checked_at': stamp, 'ok': True, 'http_status': 200,
                             'error': None, 'duration_seconds': 0})
    finally:
        monitor.close()
    incident = root / 'incidents' / event['incident_id']
    incident.mkdir(parents=True)
    (incident / 'health_ready.body').write_bytes(b'')
    manifest = {'schema': 1, 'status': 'completed', 'trigger': event,
                'limitation': 'ДЕМОНСТРАЦИЯ: все наблюдения синтетические, HTTP-запросы не выполнялись.',
                'observations': [{'url': 'http://demo.invalid/health/ready', 'http_status': None,
                    'error': 'DEMO: simulated connection refused', 'started_at': stamp, 'collected_at': stamp,
                    'body_file': 'health_ready.body', 'body_sha256': hashlib.sha256(b'').hexdigest(),
                    'body_truncated': False}]}
    (incident / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
    review = incident / 'model-review-001'
    prompt = prepare(incident, review)
    generation = review / 'generation'
    generation.mkdir()
    (generation / 'prompt.txt').write_bytes(prompt.read_bytes())
    (generation / 'answer.txt').write_text('ДЕМОНСТРАЦИОННЫЙ ОТВЕТ — написан для проверки формы, модель не запускалась.\nСинтетическое наблюдение HTTP-1 сообщает об отказе подключения. Причина неизвестна.', encoding='utf-8')
    (generation / 'metadata.json').write_text(json.dumps({'model': 'DEMO — модель не запускалась',
        'seed': None, 'output_tokens': None, 'token_limit_reached': False, 'purpose': 'ui_fixture'}), encoding='utf-8')
    return incident


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    print('Демонстрационный инцидент:', create_demo(args.output_dir))
    print('Только проверка интерфейса. Модель и Docker не запускались.')
