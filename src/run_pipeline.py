"""Run the existing context builder, optional local model, and evidence report.

Run from repository root. Outputs are never overwritten. Failed run artifacts
are retained with an explicit status; automatic resumption is not implemented.
"""
import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from build_diagnostic_report import compose, render


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('input_file', type=Path)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--mode', choices=('rules-only', 'with-model'), default='rules-only')
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--max-new-tokens', type=int, default=1536)
    args = parser.parse_args()
    if args.max_new_tokens <= 0:
        parser.error('--max-new-tokens must be positive')
    source = args.input_file.resolve()
    data = json.loads(source.read_text(encoding='utf-8'))
    scripts = Path(__file__).resolve().parent
    names = ['run_pipeline.py', 'build_context.py', 'context_time.py', 'context_sources.py',
             'extract_diagnostic_facts.py', 'build_diagnostic_report.py']
    if args.mode == 'with-model':
        names.append('run_diagnosis.py')
    hashes = {name: digest(scripts / name) for name in names}
    input_hash = digest(source)
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    (out / 'input-snapshot.json').write_bytes(source.read_bytes())
    state = {'schema_version': '0.1', 'status': 'running', 'stage': 'context',
             'mode': args.mode, 'started_at': datetime.now(timezone.utc).isoformat(),
             'input_file': str(source), 'input_sha256': input_hash,
             'module_sha256': hashes, 'python': sys.executable,
             'seed': args.seed if args.mode == 'with-model' else None,
             'max_new_tokens': args.max_new_tokens if args.mode == 'with-model' else None}
    status_file = out / 'pipeline-status.json'

    def unchanged():
        if digest(source) != input_hash or any(digest(scripts / name) != value for name, value in hashes.items()):
            raise ValueError('Input or source code changed during execution')

    def run(stage, command):
        unchanged()
        state['stage'] = stage
        save(status_file, state)
        print(f'Этап: {stage}. Журнал: {out / (stage + ".log")}', flush=True)
        with (out / (stage + '.log')).open('x', encoding='utf-8') as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)

    try:
        save(status_file, state)
        context = out / 'context'
        run('context', [sys.executable, str(scripts / 'build_context.py'), str(source),
                        '--output-dir', str(context), '--include-diagnostic-facts'])
        prompt = context / 'prompt-plain.txt'
        facts_path = context / 'diagnostic-facts.json'
        manifest = json.loads((context / 'build-manifest.json').read_text(encoding='utf-8'))
        for name, expected in manifest['output_sha256'].items():
            if digest(context / name) != expected:
                raise ValueError(f'Context output hash mismatch: {name}')
        answer = None
        if args.mode == 'with-model':
            model_out = out / 'model'
            run('model', [sys.executable, str(scripts / 'run_diagnosis.py'), str(prompt),
                          '--output-dir', str(model_out), '--seed', str(args.seed),
                          '--max-new-tokens', str(args.max_new_tokens)])
            metadata = json.loads((model_out / 'metadata.json').read_text(encoding='utf-8'))
            if digest(model_out / 'prompt.txt') != digest(prompt):
                raise ValueError('Model used a different prompt')
            if metadata['seed'] != args.seed or metadata['generation']['max_new_tokens'] != args.max_new_tokens:
                raise ValueError('Model parameters mismatch')
            answer = (model_out / 'answer.txt').read_text(encoding='utf-8')
            state['model_metadata'] = metadata
        unchanged()
        state['stage'] = 'report'
        save(status_file, state)
        facts = json.loads(facts_path.read_text(encoding='utf-8'))
        report = compose(facts, data, answer)
        report['provenance'] = {
            'input_sha256': input_hash, 'facts_sha256': digest(facts_path),
            'prompt_sha256': digest(prompt), 'module_sha256': hashes, 'mode': args.mode,
        }
        if answer is not None:
            report['provenance']['answer_sha256'] = digest(out / 'model/answer.txt')
            report['provenance']['model_metadata_sha256'] = digest(out / 'model/metadata.json')
        save(out / 'report.json', report)
        (out / 'report.md').write_text(render(report), encoding='utf-8')
        state['output_sha256'] = {name: digest(out / name) for name in ('report.json', 'report.md')}
        state['status'] = 'completed'
        state['stage'] = 'done'
        state['finished_at'] = datetime.now(timezone.utc).isoformat()
        save(status_file, state)
        print('Готово:', out / 'report.md')
        print('Модель:', 'не запускалась' if answer is None else 'ответ сохранён, семантически не проверен')
    except BaseException as exc:
        state['status'] = 'interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed'
        state['error'] = f'{type(exc).__name__}: {exc}'
        save(status_file, state)
        print('Выполнение не завершено. Состояние сохранено:', status_file, file=sys.stderr)
        raise


if __name__ == '__main__':
    main()
