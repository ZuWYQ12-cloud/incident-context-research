"""One local model job at a time. Existing results are never overwritten."""
import argparse
import json
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from prepare_incident_model import prepare
from incident_model_view import load_model_review


def save_state(incident, status, error=None):
    path = incident / 'model-job.tmp'
    path.write_text(json.dumps({'status': status, 'updated_at': datetime.now(timezone.utc).isoformat(),
                                'error': error}, ensure_ascii=False), encoding='utf-8')
    path.replace(incident / 'model-job.json')


def job_state(incident):
    path = Path(incident) / 'model-job.json'
    if not path.exists():
        return {'status': 'not_started'}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'status': 'unknown', 'error': 'Не удалось прочитать состояние запуска'}


def execute(incident, model_python):
    try:
        save_state(incident, 'preparing')
        review = incident / 'model-review-001'
        prompt = prepare(incident, review)
        save_state(incident, 'generating')
        runner = Path(__file__).with_name('run_diagnosis.py')
        with (incident / 'model-job.log').open('wb') as log:
            result = subprocess.run([str(model_python), str(runner), str(prompt),
                '--output-dir', str(review / 'generation'), '--seed', '42', '--max-new-tokens', '1536'],
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, timeout=600)
        if result.returncode != 0:
            raise ValueError(f'Генерация завершилась с кодом {result.returncode}; см. model-job.log')
        if load_model_review(incident, incident.name)['status'] != 'available':
            raise ValueError('Сохранённый результат не прошёл проверку соответствия материалов')
        save_state(incident, 'completed')
    except Exception as exc:
        save_state(incident, 'failed', str(exc))


class Jobs:
    def __init__(self, root, model_python):
        self.root = Path(root).resolve()
        self.model_python = Path(model_python).absolute()
        self.lock = threading.Lock()
        self.process = None

    def start(self, identifier):
        with self.lock:
            if len(identifier) != 32 or any(c not in '0123456789abcdef' for c in identifier):
                raise ValueError('Некорректный идентификатор')
            incident = self.root / identifier
            if incident.is_symlink() or incident.resolve().parent != self.root:
                raise ValueError('Некорректная папка инцидента')
            if not (incident / 'manifest.json').is_file():
                raise ValueError('Нет HTTP-материалов для подготовки запроса')
            if (incident / 'model-review-001').exists() or (incident / 'model-job.json').exists():
                raise ValueError('Запуск уже существует. Повторная генерация запрещена.')
            if self.process and self.process.poll() is None:
                raise ValueError('Уже выполняется диагностика другого инцидента')
            for path in self.root.glob('*/model-job.json'):
                if job_state(path.parent)['status'] in ('queued', 'preparing', 'generating', 'unknown'):
                    raise ValueError('Есть незавершённый запуск. Проверьте его состояние перед новым запуском.')
            # Exclusive reservation also blocks a duplicate request from another server.
            with (incident / 'model-job.json').open('x', encoding='utf-8') as stream:
                json.dump({'status': 'queued'}, stream)
            try:
                self.process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                    '--incident', str(incident), '--model-python', str(self.model_python)],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError as exc:
                save_state(incident, 'failed', str(exc))
                raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--incident', type=Path, required=True)
    parser.add_argument('--model-python', type=Path, required=True)
    args = parser.parse_args()
    execute(args.incident, args.model_python)
