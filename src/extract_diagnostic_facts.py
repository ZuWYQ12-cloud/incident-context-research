"""Extract diagnostic signatures; never infer a root cause from a signature.

Input: {"observations": [{"id": "E1", "text": "..."}, ...]}.
raw_text is accepted as an alternative to text. Output retains exact matches.
This module does not validate collection times or replace the context builder.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path


RULES = (
    {
        "id": "server_process_finished",
        "pattern": r'(?m)^[ \t]*(?:[^\r\n|]+\|[ \t]*)?(?:\d{4}-\d{2}-\d{2}T[0-9:.+\-]+Z?[ \t]+)?INFO:[ \t]+Finished server process[ \t]+\[\d+\][ \t]*\r?$',
        "meaning": "В журнале зарегистрировано завершение серверного процесса.",
        "not_established": [
            "Причина и инициатор завершения процесса.",
            "Остановка всего контейнера, отсутствие других процессов или последующего перезапуска.",
            "Причинная связь завершения процесса с наблюдаемым отказом HTTP требует сопоставления времени.",
        ],
        "next_check": "Сопоставить время завершения с отказами HTTP, проверить состояние контейнера и события его остановки или перезапуска.",
    },
    {
        "id": "password_authentication_failed",
        "pattern": r'(?im)^.*(?:FATAL:\s*password authentication failed for user\s+"[^"\r\n]+").*$',
        "meaning": "Зарегистрирован отказ парольной аутентификации.",
        "not_established": [
            "Где возникло несоответствие учётных данных.",
            "Ошибочность pg_hba.conf или изменение пароля конкретным лицом.",
        ],
        "next_check": "Сверить параметры подключения API с ожидаемыми учётными данными без вывода пароля в журнал.",
    },
    {
        "id": "host_name_resolution_failed",
        "pattern": r'(?im)^.*(?:failed to resolve host|could not translate host name).*$',
        "meaning": "Зарегистрирована ошибка разрешения имени узла.",
        "not_established": ["Остановка PostgreSQL или неисправность конкретного DNS-компонента."],
        "next_check": "Проверить разрешение указанного имени из среды API и её сетевую конфигурацию.",
    },
    {
        "id": "relation_not_found",
        "pattern": r'(?im)^.*(?:ERROR:\s*|UndefinedTable:\s*)relation\s+"[^"\r\n]+"\s+does not exist.*$',
        "meaning": "Запрос сообщил об отсутствии указанного отношения в контексте своего выполнения.",
        "not_established": ["Удаление таблицы: также не проверены база подключения, схема и search_path."],
        "next_check": "Проверить базу подключения, схему и search_path, затем наличие отношения.",
    },
    {
        "id": "table_permission_denied",
        "pattern": r'(?im)^.*(?:ERROR:\s*|InsufficientPrivilege:\s*)permission denied for (?:table|relation)\s+[^\r\n]+$',
        "meaning": "Зарегистрирован отказ доступа к таблице или отношению из-за прав.",
        "not_established": ["Кто изменил права и изменялись ли они вообще."],
        "next_check": "Проверить текущую роль соединения и её права на указанный объект.",
    },
)


def extract(document):
    observations = document.get("observations")
    if not isinstance(observations, list):
        raise ValueError("Ожидается список observations")
    seen = set()
    findings = {rule["id"]: [] for rule in RULES}
    unmatched = []
    for observation in observations:
        if not isinstance(observation, dict):
            raise ValueError("Наблюдение должно быть объектом")
        identifier = observation.get("id")
        if not isinstance(identifier, str) or not identifier.strip() or identifier in seen:
            raise ValueError("Пустой или повторяющийся id наблюдения")
        seen.add(identifier)
        text = observation.get("text", observation.get("raw_text"))
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"Нет текста наблюдения {identifier}")
        if "text" in observation and "raw_text" in observation and observation["text"] != observation["raw_text"]:
            raise ValueError(f"Конфликт text и raw_text: {identifier}")
        matched = False
        for rule in RULES:
            for match in re.finditer(rule["pattern"], text):
                matched = True
                findings[rule["id"]].append({
                    "observation_id": identifier,
                    "quote": match.group(),
                    "start_char": match.start(),
                    "end_char": match.end(),
                })
        if not matched:
            unmatched.append(identifier)

    facts = []
    for rule in RULES:
        evidence = findings[rule["id"]]
        if evidence:
            facts.append({
                "signature": rule["id"],
                "meaning": rule["meaning"],
                "evidence": evidence,
                "not_established": rule["not_established"],
                "next_check": rule["next_check"],
            })
    return {
        "schema_version": "0.1",
        "purpose": "development_signature_extraction",
        "facts": facts,
        "observations_without_supported_signature": unmatched,
        "limitations": [
            "Совпадение строки с сигнатурой не доказывает первопричину или подлинность журнала.",
            f"Поддерживаются {len(RULES)} явных сигнатур; отсутствие совпадения не означает отсутствия ошибки.",
            "Разные сигнатуры сохраняются вместе, а не сводятся к одной причине.",
            "Временная допустимость и связи разных попыток подключения здесь не проверяются.",
            "Пояснения и рекомендации являются внешними правилами, а не исходными наблюдениями.",
        ],
    }


def render(result):
    lines = [
        "Дополнительные сведения от детерминированного анализатора (не исходный журнал):",
        "Сигнатуры описывают зарегистрированные события и ошибки и не устанавливают первопричину.",
    ]
    for fact in result["facts"]:
        ids = list(dict.fromkeys(item["observation_id"] for item in fact["evidence"]))
        lines.extend([
            "", f"[{', '.join(ids)}] {fact['meaning']}",
            "Не установлено: " + " ".join(fact["not_established"]),
            "Проверка: " + fact["next_check"],
        ])
    if not result["facts"]:
        lines.append("Поддерживаемые сигнатуры не найдены. Автоматический диагноз не сформирован.")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input_file", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    raw = args.input_file.read_bytes()
    result = extract(json.loads(raw.decode("utf-8-sig")))
    result["input_sha256"] = hashlib.sha256(raw).hexdigest()
    result["extractor_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    result["input_file"] = args.input_file.as_posix()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / "diagnostic-facts.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output_dir / "diagnostic-context.txt").write_text(render(result), encoding="utf-8")
    print(f"Сигнатур: {len(result['facts'])}. Сохранено: {args.output_dir}")


if __name__ == "__main__":
    main()
