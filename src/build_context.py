"""Build two context representations from prepared observations."""

import argparse
import hashlib
import json
from pathlib import Path

from context_time import audit_observation
from context_sources import validate_sources


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input_file", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--include-diagnostic-facts", action="store_true")
    args = parser.parse_args()

    input_bytes = args.input_file.read_bytes()
    data = json.loads(input_bytes.decode("utf-8"))

    require(data["schema_version"] in ("0.1", "0.2"), "Unsupported schema version")
    require(data["purpose"] == "development", "Expected development data")

    header = data["header_text"]
    questions = data["question_text"]
    observations = data["observations"]

    require(isinstance(header, str), "header_text must be a string")
    require(isinstance(questions, str), "question_text must be a string")
    require(
        isinstance(observations, list) and observations,
        "observations must be a nonempty list",
    )

    source_audit = None
    source_context = []

    if data["schema_version"] == "0.2":
        source_audit = validate_sources(data)
        for source in data["sources"]:
            public = {
                key: source[key]
                for key in (
                    "id", "kind", "component", "availability",
                    "representation", "completeness",
                    "coverage_interval", "note",
                )
            }
            public["observation_ids"] = source_audit[
                source["id"]
            ]["observation_ids"]
            source_context.append(public)

    seen = set()
    checked_sources = {}
    records = []
    time_audit = {}

    for observation in observations:
        identifier = observation["id"]
        text = observation["text"]
        require(isinstance(identifier, str), "ID must be a string")
        require(identifier not in seen, f"Duplicate ID: {identifier}")
        require(isinstance(text, str) and text.strip(), "Empty observation")
        require(text.startswith(f"[{identifier}]"), f"ID mismatch: {identifier}")
        seen.add(identifier)

        provenance = observation["provenance"]
        require(
            provenance["kind"] == "prepared_prompt_excerpt",
            "Unsupported provenance kind",
        )
        source = Path(provenance["source_file"])

        if source not in checked_sources:
            source_bytes = source.read_bytes()
            checked_sources[source] = (
                sha256(source_bytes),
                source_bytes.decode("utf-8"),
            )

        digest, source_text = checked_sources[source]
        require(
            digest == provenance["source_sha256"],
            f"Source changed: {source}",
        )
        require(text in source_text, f"Excerpt not found: {identifier}")

        # Only observation content enters the model's context.
        # Development labels and source paths stay outside it.
        records.append({"id": identifier, "text": text})
        time_audit[identifier] = audit_observation(
            observation.get("event_time"),
            observation.get("collected_at"),
            data["diagnosis_cutoff"],
        )

    plain_body = "\n\n".join(record["text"] for record in records)
    structured_body = json.dumps(records, ensure_ascii=False, indent=2)

    decoded = json.loads(structured_body)
    require(decoded == records, "Structured representation lost data")
    require(
        "\n\n".join(record["text"] for record in decoded) == plain_body,
        "Observation texts differ",
    )

    source_section = ""
    if source_context:
        source_section = (
            "Источники и покрытие данных:\n\n"
            + json.dumps(source_context, ensure_ascii=False, indent=2)
            + "\n\n"
        )

    temporal_context = None
    time_section = ""

    if data["schema_version"] == "0.2":
        temporal_context = {
            "diagnosis_cutoff": data["diagnosis_cutoff"],
            "boundary_rule": "exclusive",
            "mode": "audit_without_filtering",
            "timestamp_model": "truncated_timestamp_intervals",
            "clock_synchronization_verified": False,
            "interpretation": [
                "before означает до границы в принятой модели меток.",
                "missing означает отсутствие временной метки.",
                "invalid означает некорректную временную метку.",
                "overlaps означает пересечение интервала метки с границей.",
                "at_or_after означает метку на границе или после неё.",
                "strict_eligible требует времени события и получения до границы.",
                "strict_eligible=false не обязательно означает позднее событие.",
                "Разрешение метки не гарантирует точность часов.",
                "Наблюдения в режиме аудита не исключаются.",
            ],
            "observations": [
                {
                    "id": observation["id"],
                    "event_time_original": observation.get(
                        "event_time_original"
                    ),
                    "event_time": observation.get("event_time"),
                    "collected_at": observation.get("collected_at"),
                    **time_audit[observation["id"]],
                }
                for observation in observations
            ],
        }
        # Keep the full audit in the manifest; compact only its presentation.
        temporal_display = {
            key: value
            for key, value in temporal_context.items()
            if key != "observations"
        }
        defaults = {
            "time_model": "truncated_timestamp_intervals",
            "clock_synchronization_verified": False,
        }
        temporal_display["observation_defaults"] = defaults
        temporal_display["observation_defaults_scope"] = (
            "Defaults apply to every observation; explicit fields override them."
        )
        temporal_display["observations"] = []

        for record in temporal_context["observations"]:
            compact = dict(record)
            for key, value in defaults.items():
                require(
                    compact[key] == value,
                    "Cannot compact differing temporal properties",
                )
                del compact[key]
            temporal_display["observations"].append(compact)

        reconstructed = [
            {**defaults, **record}
            for record in temporal_display["observations"]
        ]
        require(
            reconstructed == temporal_context["observations"],
            "Temporal compaction lost data",
        )

        time_section = (
            "Время и ограничения наблюдений:\n\n"
            + json.dumps(
                temporal_display, ensure_ascii=False, separators=(",", ":")
            )
            + "\n\n"
        )

    diagnostic_context = None
    diagnostic_section = ""
    if args.include_diagnostic_facts:
        from extract_diagnostic_facts import extract, render

        # Only validated observation texts enter extraction. Operator metadata
        # and the known incident scenario must not influence these rules.
        diagnostic_context = extract({"observations": records})
        diagnostic_section = render(diagnostic_context) + "\n"

    def make_prompt(body):
        return (
            header + source_section + time_section
            + "Наблюдения:\n\n" + body + "\n\n" + diagnostic_section + questions
        )

    outputs = {
        "prompt-plain.txt": make_prompt(plain_body),
        "prompt-structured.txt": make_prompt(structured_body),
    }

    manifest = {
        "schema_version": data["schema_version"],
        "incident_id": data["incident_id"],
        "purpose": data["purpose"],
        "input_file": args.input_file.as_posix(),
        "input_sha256": sha256(input_bytes),
        "builder_sha256": sha256(Path(__file__).read_bytes()),
        "module_sha256": {
            name: sha256(Path(__file__).with_name(name).read_bytes())
            for name in ("context_time.py", "context_sources.py")
        },
        "source_audit": source_audit,
        "source_context": source_context,
        "temporal_context": temporal_context,
        "temporal_presentation": "shared_defaults_compact_json",
        "time_section_equal_between_variants": True,
        "source_section_equal_between_variants": True,
        "observation_order": [record["id"] for record in records],
        "checks": {
            "source_hashes_match": True,
            "excerpts_found_in_sources": True,
            "observation_texts_equal": True,
        },
        "time_audit_mode": "report_only",
        "diagnosis_cutoff": data["diagnosis_cutoff"],
        "time_audit": time_audit,
        "all_observations_strict_eligible": all(
            item["strict_eligible"] for item in time_audit.values()
        ),
        "limitations": [
            "Timestamp annotations are supplied in input; audit does not filter observations.",
            "No claim of equal token counts between representations.",
            "Input consists of prepared excerpts, not raw telemetry.",
        ],
        "output_sha256": {
            name: sha256(text.encode("utf-8"))
            for name, text in outputs.items()
        },
    }

    manifest["diagnostic_facts_enabled"] = args.include_diagnostic_facts
    if diagnostic_context is not None:
        manifest["module_sha256"]["extract_diagnostic_facts.py"] = sha256(
            Path(__file__).with_name("extract_diagnostic_facts.py").read_bytes()
        )
        manifest["diagnostic_context"] = diagnostic_context
        manifest["diagnostic_section_equal_between_variants"] = True
        manifest["diagnostic_section_sha256"] = sha256(
            diagnostic_section.encode("utf-8")
        )
        manifest["limitations"].append(
            "Diagnostic guidance adds external rule-based knowledge, not raw evidence; "
            "it is not a formatting-only change or a root-cause verification."
        )
        outputs["diagnostic-facts.json"] = json.dumps(
            diagnostic_context, ensure_ascii=False, indent=2
        ) + "\n"
        outputs["diagnostic-context.txt"] = diagnostic_section
        manifest["output_sha256"] = {
            name: sha256(value.encode("utf-8"))
            for name, value in outputs.items()
        }

    # Refuse to overwrite an existing build.
    args.output_dir.mkdir(parents=True, exist_ok=False)

    for name, text in outputs.items():
        (args.output_dir / name).write_bytes(text.encode("utf-8"))

    (args.output_dir / "build-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print("Создано:", args.output_dir)
    print("Наблюдения:", ", ".join(manifest["observation_order"]))
    print("Источники и сохранность текста проверены.")
    if diagnostic_context is not None:
        print("Диагностических сигнатур:", len(diagnostic_context["facts"]))
        print("Диагностический блок одинаков в обоих запросах.")
    for identifier, result in time_audit.items():
        print(
            f"{identifier}: event={result['event_time_status']}, "
            f"collection={result['collection_time_status']}, "
            f"strict_eligible={result['strict_eligible']}"
        )
    print("Режим аудита: наблюдения не исключались.")
    if source_audit is not None:
        print("Проверено источников:", len(source_audit))
        print("Описание источников включено одинаково в оба запроса.")




if __name__ == "__main__":
    main()
