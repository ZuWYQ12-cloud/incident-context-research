"""Prepare four development conditions from a verified context build."""

import argparse
import hashlib
import json
from pathlib import Path


def digest(content):
    return hashlib.sha256(content).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    input_bytes = args.input.read_bytes()
    data = json.loads(input_bytes.decode("utf-8"))
    require(data["purpose"] == "development", "Expected development data")

    manifest_path = args.build_dir / "build-manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    build = json.loads(manifest_bytes.decode("utf-8"))
    require(
        digest(input_bytes) == build["input_sha256"],
        "Input does not match the context build",
    )

    questions = data["question_text"]
    require(isinstance(questions, str) and questions.strip(), "Empty questions")

    base = {}
    contexts = {}
    for variant in ("plain", "structured"):
        name = f"prompt-{variant}.txt"
        raw = (args.build_dir / name).read_bytes()
        require(
            digest(raw) == build["output_sha256"][name],
            f"Build output changed: {name}",
        )
        prompt = raw.decode("utf-8")
        require(prompt.endswith(questions), f"Question suffix differs: {name}")
        base[variant] = prompt
        contexts[variant] = prompt[:-len(questions)]

    addition = (
        "\nДополнительные требования к обоснованию:\n"
        "Отделяй установленные факты от гипотез и неизвестных обстоятельств.\n"
        "Для причинного вывода укажи наблюдения, которые его подтверждают.\n"
        "Если наблюдения допускают несколько причин, обозначь это явно.\n"
        "Не представляй возможную причину как установленную.\n"
        "Укажи, какие дополнительные проверки помогут различить гипотезы.\n"
    )
    enhanced = questions.rstrip() + "\n" + addition

    specification = {
        "P0": ("plain", "original", questions),
        "S0": ("structured", "original", questions),
        "P1": ("plain", "enhanced", enhanced),
        "S1": ("structured", "enhanced", enhanced),
    }

    outputs = {}
    records = []
    for condition, (variant, instruction, suffix) in specification.items():
        prompt = contexts[variant] + suffix
        name = f"prompt-{condition}.txt"
        outputs[name] = prompt.encode("utf-8")
        records.append({
            "condition": condition,
            "representation": variant,
            "instruction": instruction,
            "file": name,
            "sha256": digest(outputs[name]),
            "context_sha256": digest(contexts[variant].encode("utf-8")),
            "instruction_sha256": digest(suffix.encode("utf-8")),
        })

    require(outputs["prompt-P0.txt"] == base["plain"].encode("utf-8"),
            "P0 changed unexpectedly")
    require(outputs["prompt-S0.txt"] == base["structured"].encode("utf-8"),
            "S0 changed unexpectedly")
    require(records[0]["context_sha256"] == records[2]["context_sha256"],
            "P0/P1 contexts differ")
    require(records[1]["context_sha256"] == records[3]["context_sha256"],
            "S0/S1 contexts differ")
    require(records[0]["instruction_sha256"] == records[1]["instruction_sha256"],
            "P0/S0 instructions differ")
    require(records[2]["instruction_sha256"] == records[3]["instruction_sha256"],
            "P1/S1 instructions differ")

    result = {
        "purpose": "development_pipeline_check",
        "incident_id": data["incident_id"],
        "input_file": args.input.as_posix(),
        "input_sha256": digest(input_bytes),
        "build_manifest": manifest_path.as_posix(),
        "build_manifest_sha256": digest(manifest_bytes),
        "generator_sha256": digest(Path(__file__).read_bytes()),
        "conditions": records,
        "limitations": [
            "Known development incident; not an independent evaluation.",
            "Enhanced instructions change prompt length.",
            "Shared source and temporal explanations are present in all conditions.",
            "This comparison does not isolate the benefit of adding those metadata.",
        ],
    }

    args.output_dir.mkdir(parents=True, exist_ok=False)
    for name, raw in outputs.items():
        (args.output_dir / name).write_bytes(raw)
    (args.output_dir / "comparison-manifest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    for record in records:
        print(
            f"{record['condition']}: "
            f"{record['representation']}, {record['instruction']}"
        )
    print("Проверены исходные хеши, контексты и инструкции.")
    print("Сохранено:", args.output_dir)


if __name__ == "__main__":
    main()
