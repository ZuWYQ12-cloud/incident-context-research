"""Local transactional outbox. No network delivery is performed here."""
import argparse
import json
import sqlite3
from pathlib import Path


def initialize(connection):
    connection.execute('''CREATE TABLE IF NOT EXISTS notification_outbox (
        id INTEGER PRIMARY KEY,
        event_id INTEGER NOT NULL UNIQUE,
        incident_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        created_at TEXT NOT NULL,
        message TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','sent')),
        UNIQUE(incident_id, event_type)
    )''')


def message_text(url, event, sample):
    if event['event_type'] == 'incident_opened':
        lines = ['Обнаружен сбой сервиса', f'Адрес проверки: {url}',
                 f"Первый неуспешный замер: {event['first_bad_at']}",
                 f"Сбой подтверждён: {event['confirmed_at']}"]
        if sample['http_status'] is not None:
            lines.append(f"Последняя проверка: HTTP {sample['http_status']}")
        else:
            lines.append('Последняя проверка: HTTP-ответ не получен')
        lines.append('Причина пока не установлена. Материалы диагностики могут ещё собираться.')
    elif event['event_type'] == 'recovered':
        lines = ['Доступность сервиса восстановлена', f'Адрес проверки: {url}',
                 f"Восстановление подтверждено: {event['confirmed_at']}",
                 'Монитор подтвердил восстановление серией успешных проверок.',
                 'Это не устанавливает причину сбоя или способ его устранения.']
    else:
        raise ValueError('Неизвестный тип события уведомления')
    lines.append(f"Инцидент: {event['incident_id']}")
    return '\n'.join(lines)


def enqueue(connection, event_id, url, event, sample):
    # The caller owns the transaction: never commit separately from the event.
    connection.execute('''INSERT INTO notification_outbox
        (event_id,incident_id,event_type,created_at,message) VALUES (?,?,?,?,?)''',
        (event_id, event['incident_id'], event['event_type'],
         event['confirmed_at'], message_text(url, event, sample)))


def main():
    parser = argparse.ArgumentParser(description='Просмотр очереди без отправки сообщений')
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--limit', type=int, default=20)
    args = parser.parse_args()
    if not 1 <= args.limit <= 200:
        parser.error('--limit должен быть от 1 до 200')
    path = args.database.resolve()
    if not path.is_file():
        parser.error('База мониторинга не найдена')
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='notification_outbox'").fetchone()
        if not exists:
            print('Очередь ещё не создана. Запустите обновлённый монитор.')
            return
        counts = dict(db.execute('SELECT status,count(*) FROM notification_outbox GROUP BY status').fetchall())
        print('Локальная очередь:', json.dumps(counts, ensure_ascii=False))
        for row in db.execute('SELECT * FROM notification_outbox ORDER BY id DESC LIMIT ?', (args.limit,)):
            print(f"\n--- Уведомление {row['id']} · {row['status']} ---\n{row['message']}")
        print('\nПросмотр не отправляет сообщения и не меняет их статус.')
    finally:
        db.close()


if __name__ == '__main__':
    main()
