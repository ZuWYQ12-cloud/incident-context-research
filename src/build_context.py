"""Build two context representations from prepared observations."""

import argparse
import hashlib
import json
from pathlib import Path


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input_file", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    input_bytes = args.input_file.read_bytes()
    data = json.loads(input_bytes.decode("utf-8"))

    require(data["schema_version"] == "0.1", "Unsupported schema version")
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

    seen = set()
    checked_sources = {}
    records = []

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

    plain_body = "\n\n".join(record["text"] for record in records)
    structured_body = json.dumps(records, ensure_ascii=False, indent=2)

    decoded = json.loads(structured_body)
    require(decoded == records, "Structured representation lost data")
    require(
        "\n\n".join(record["text"] for record in decoded) == plain_body,
        "Observation texts differ",
    )

    def make_prompt(body):
        return header + "Наблюдения:\n\n" + body + "\n\n" + questions

    outputs = {
        "prompt-plain.txt": make_prompt(plain_body),
        "prompt-structured.txt": make_prompt(structured_body),
    }

    manifest = {
        "schema_version": "0.1",
        "incident_id": data["incident_id"],
        "purpose": data["purpose"],
        "input_file": args.input_file.as_posix(),
        "input_sha256": sha256(input_bytes),
        "builder_sha256": sha256(Path(__file__).read_bytes()),
        "observation_order": [record["id"] for record in records],
        "checks": {
            "source_hashes_match": True,
            "excerpts_found_in_sources": True,
            "observation_texts_equal": True,
        },
        "limitations": [
            "No automatic timestamp extraction or cutoff validation.",
            "No claim of equal token counts between representations.",
            "Input consists of prepared excerpts, not raw telemetry.",
        ],
        "output_sha256": {
            name: sha256(text.encode("utf-8"))
            for name, text in outputs.items()
        },
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
    print("Временная граница автоматически пока не проверяется.")


if __name__ == "__main__":
    main()
