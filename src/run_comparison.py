import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def require(condition, message):
    if not condition:
        raise SystemExit(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_result(job, plan):
    directory = Path(job["output_dir"])
    for name in ("prompt.txt", "formatted_prompt.txt", "answer.txt", "metadata.json"):
        require(
            (directory / name).is_file(),
            f"Незавершённый результат: {directory}; отсутствует {name}. "
            "Ничего не перезаписано.",
        )

    require(
        digest(directory / "prompt.txt") == job["prompt_sha256"],
        f"Не совпадает сохранённый запрос: {directory}",
    )
    require(
        bool((directory / "formatted_prompt.txt").read_text(encoding="utf-8")),
        f"Пустой оформленный запрос: {directory}",
    )
    metadata = json.loads(
        (directory / "metadata.json").read_text(encoding="utf-8")
    )
    expected = {
        "prompt_file": job["prompt_file"],
        "seed": job["seed"],
        "model": plan["expected_model"],
        "revision": plan["expected_revision"],
        "generation": plan["expected_generation"],
        "dtype": "bfloat16",
        "enable_thinking": False,
    }
    for key, value in expected.items():
        require(
            metadata.get(key) == value,
            f"Несовпадение {key}: {directory}",
        )
    require(
        isinstance(metadata.get("output_tokens"), int)
        and metadata["output_tokens"] > 0,
        f"Некорректное число выходных токенов: {directory}",
    )
    return metadata


parser = argparse.ArgumentParser()
parser.add_argument("plan_file", type=Path)
parser.add_argument("--dry-run", action="store_true")
args = parser.parse_args()

plan_bytes = args.plan_file.read_bytes()
plan = json.loads(plan_bytes.decode("utf-8"))
require(
    plan["purpose"] == "development_pipeline_check",
    "Ожидался план проверочной серии.",
)
require(
    digest(Path(plan["comparison_manifest"]))
    == plan["comparison_manifest_sha256"],
    "Манифест сравнения изменился.",
)
require(
    digest(Path(plan["runner_file"])) == plan["runner_sha256"],
    "Программа диагностики изменилась после составления плана.",
)

jobs = plan["jobs"]
pairs = {(job["condition"], job["seed"]) for job in jobs}
require(
    len(jobs) == 12
    and pairs == {
        (condition, seed)
        for condition in ("P0", "S0", "P1", "S1")
        for seed in (42, 43, 44)
    },
    "Ожидались ровно 12 уникальных сочетаний условия и seed.",
)
require(
    len({Path(job["output_dir"]).resolve() for job in jobs}) == len(jobs),
    "В плане повторяются папки результатов.",
)

for job in jobs:
    require(
        digest(Path(job["prompt_file"])) == job["prompt_sha256"],
        f"Изменился запрос: {job['prompt_file']}",
    )
    if Path(job["output_dir"]).exists():
        validate_result(job, plan)

print("План, хеши и существующие результаты проверены.", flush=True)
if args.dry_run:
    for number, job in enumerate(jobs, 1):
        state = "готов" if Path(job["output_dir"]).exists() else "ожидает"
        print(f"{number:02d}. {job['job_id']}: {state}")
    raise SystemExit(0)

require(
    sys.prefix != sys.base_prefix,
    "Сначала активируй окружение: source .venv/bin/activate",
)
import torch

require(torch.cuda.is_available(), "GPU недоступна для PyTorch.")

attempt = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
logs = Path("results/raw") / f"{plan['series_id']}-execution-{attempt}"
logs.mkdir(parents=True, exist_ok=False)
(logs / "run-plan.json").write_bytes(plan_bytes)
(logs / "execution.json").write_text(
    json.dumps({
        "python": sys.executable,
        "plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
        "batch_runner_sha256": digest(Path(__file__)),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
print("Журналы выполнения:", logs, flush=True)

for number, job in enumerate(jobs, 1):
    print(f"\n[{number}/12] {job['job_id']}", flush=True)
    if Path(job["output_dir"]).exists():
        metadata = validate_result(job, plan)
        print("Готовый результат проверен; повтор не выполняется.", flush=True)
    else:
        command = [
            sys.executable, "-u", plan["runner_file"],
            job["prompt_file"],
            "--seed", str(job["seed"]),
            "--output-dir", job["output_dir"],
            "--max-new-tokens",
            str(plan["expected_generation"]["max_new_tokens"]),
        ]
        log_file = logs / f"{job['job_id']}.log"
        print("Модель работает. Журнал:", log_file, flush=True)
        with log_file.open("w", encoding="utf-8") as stream:
            result = subprocess.run(
                command, stdout=stream, stderr=subprocess.STDOUT,
            )
        require(
            result.returncode == 0,
            f"Запуск завершился с кодом {result.returncode}. "
            f"Посмотри журнал: {log_file}. Результаты сохранены как есть.",
        )
        metadata = validate_result(job, plan)

    print(
        f"Токенов: {metadata['output_tokens']}; "
        f"генерация: {metadata['generation_seconds']} с; "
        f"лимит: {metadata['token_limit_reached']}",
        flush=True,
    )

print("\nВсе 12 результатов сохранены и проверены.", flush=True)
