"""Prepare paired development contexts using the existing repository builder."""
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    raw_root = Path("results/raw/20261112T144344Z-table-permission")
    destination = Path("experiments/context-method-dev/incident-003-pair")
    if destination.exists():
        raise SystemExit(f"Папка уже существует, ничего не перезаписано: {destination}")
    template = json.loads(Path(
        "experiments/context-method-dev/incident-002-sources.json"
    ).read_text(encoding="utf-8"))
    cutoff = (raw_root / "operator/diagnosis_cutoff_utc.txt").read_text().strip()
    tracked = {}

    def read(relative):
        path = raw_root / relative
        raw = path.read_bytes()
        tracked[relative] = {"path": path.as_posix(), "sha256": digest(raw)}
        return raw.decode("utf-8").strip()

    records = []
    for identifier, phase in (("E1", "before"), ("E2", "during")):
        lines = [f"[{identifier}] Отдельные HTTP-проверки:"]
        collected = []
        event_times = []
        for name, endpoint in (("health_live", "/health/live"),
                               ("health_ready", "/health/ready"), ("tasks", "/tasks")):
            meta = read(f"observations/{phase}_{name}.meta")
            entries = meta.splitlines()
            values = dict(line.split("=", 1) for line in entries[1:] if "=" in line)
            if values.get("curl_exit_code") != "0":
                raise ValueError(f"Неуспешная HTTP-проверка: {phase}/{name}")
            timestamp = entries[0]
            received = read(f"observations/{phase}_{name}.collected_at.txt")
            lines.append(f"{timestamp}: GET {endpoint} — {values['http_code']}. Получено: {received}.")
            event_times.append(timestamp)
            collected.append(received)
        # Aggregate collection is the latest completion; no invented aggregate event time.
        records.append({"id": identifier, "text": "\n".join(lines),
                        "event_time": event_times[0] if len(set(event_times)) == 1 else None,
                        "collected_at": max(collected)})

    for identifier, service in (("E3", "db"), ("E4", "api")):
        exit_code = read(f"operator/{service}_logs_exit_code.txt")
        if exit_code != "0":
            raise ValueError(f"Выгрузка {service} завершилась с кодом {exit_code}")
        log = read(f"observations/{service}_before_recovery.log")
        collected = read(f"observations/{service}_before_recovery.collected_at.txt")
        records.append({"id": identifier,
                        "text": f"[{identifier}] Сохранённый журнал {service}; время получения: {collected}.\n{log}",
                        "event_time": None, "collected_at": collected})

    header = template["header_text"]
    header, count = re.subn(r"Момент диагностики:[^\n]*", f"Момент диагностики: {cutoff}.", header)
    if count != 1:
        raise ValueError("Не найдена единственная строка момента диагностики")
    header += (
        "\nОграничения данных:\n"
        "HTTP-проверки являются отдельными замерами. Журналы сохранены полностью в пределах предоставленных выгрузок; "
        "полнота исходных журналов не установлена. Время получения журналов записано с разрешением в секунду "
        "и совпадает с исключающей границей диагностики; строгая временная допустимость не подтверждена. "
        "Применяется режим аудита без исключения наблюдений. Синхронизация часов не проверена. "
        "У журнала несколько событий, поэтому ему не приписывается единое время события.\n\n"
    )
    excerpt_file = destination / "prepared-observations.txt"
    excerpt_bytes = ("\n\n".join(row["text"] for row in records) + "\n").encode("utf-8")
    for row in records:
        row["provenance"] = {"kind": "prepared_prompt_excerpt",
                             "source_file": excerpt_file.as_posix(),
                             "source_sha256": digest(excerpt_bytes)}
    data = {"schema_version": "0.1", "purpose": "development", "incident_id": "incident-003",
            "header_text": header, "question_text": template["question_text"],
            "diagnosis_cutoff": cutoff, "observations": records}
    destination.mkdir(parents=True, exist_ok=False)
    excerpt_file.write_bytes(excerpt_bytes)
    input_path = destination / "input.json"
    input_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for variant, flag in (("baseline", []), ("with-rules", ["--include-diagnostic-facts"])):
        subprocess.run([sys.executable, "src/build_context.py", str(input_path),
                        "--output-dir", str(destination / variant), *flag], check=True)
    plain = (destination / "baseline/prompt-plain.txt").read_bytes()
    enhanced = (destination / "with-rules/prompt-plain.txt").read_bytes()
    section = (destination / "with-rules/diagnostic-context.txt").read_bytes()
    if not section or enhanced.count(section) != 1 or enhanced.replace(section, b"", 1) != plain:
        raise ValueError("Различие запросов не ограничено диагностическим блоком")
    manifest = {
        "purpose": "development_paired_rule_check",
        "input_format": "schema 0.1; shared source/time limitations explicitly included in header",
        "selection": "HTTP metadata for before/during and entire saved API/DB logs; no error-based line selection",
        "source_files": tracked,
        "cutoff_sha256": digest((raw_root / "operator/diagnosis_cutoff_utc.txt").read_bytes()),
        "question_template_sha256": digest(template["question_text"].encode("utf-8")),
        "preparer_sha256": digest(Path(__file__).read_bytes()),
        "comparison": {"only_diagnostic_section_differs": True,
                       "baseline_sha256": digest(plain), "with_rules_sha256": digest(enhanced)},
        "planned_seeds": [42, 43, 44], "planned_max_new_tokens": 1536,
        "limitations": ["No main-study efficacy claim; same known stand and supported failure signature.",
                        "Full logs may add irrelevant events; both conditions receive the same events.",
                        "The four-condition 12-job runner is not compatible with this two-condition pair."],
    }
    (destination / "pair-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("ГОТОВО: два обычных текстовых запроса различаются только диагностическим блоком.")
    print("Папка:", destination)
    print("Модель не запускалась. Старые результаты не изменены.")


if __name__ == "__main__":
    main()
