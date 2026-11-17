"""Single-recipient VK outbox sender; run in Ubuntu/WSL. Dry run by default."""
import argparse
import hashlib
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler


class DeliveryError(Exception):
    def __init__(self, code, retryable=False):
        super().__init__(code)
        self.retryable = retryable


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def vk_send(token, peer_id, message, random_id):
    data = urlencode(dict(access_token=token, v='5.199', peer_id=peer_id,
                          random_id=random_id, message=message)).encode()
    request = Request('https://api.vk.com/method/messages.send', data=data, method='POST')
    try:
        with build_opener(NoRedirect).open(request, timeout=15) as response:
            raw = response.read(65537)
        if len(raw) > 65536:
            raise DeliveryError('oversized_response', True)
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise DeliveryError('invalid_response', True)
        if 'error' in payload:
            code = payload['error'].get('error_code')
            # Do not log error_msg/request_params: responses may echo credentials.
            raise DeliveryError(f'vk_error_{code}', code in (1, 6, 10))
        result = payload.get('response')
        if type(result) is not int or result <= 0:
            raise DeliveryError('invalid_message_id', True)
        return result
    except HTTPError as exc:
        code = exc.code
        exc.close()
        raise DeliveryError(f'http_{code}', code == 429 or code >= 500) from None
    except (URLError, OSError, TimeoutError):
        raise DeliveryError('network_error', True) from None
    except (ValueError, UnicodeError, AttributeError):
        raise DeliveryError('invalid_response', True) from None


def setup(db, peer_id, token):
    binding = json.dumps([peer_id, hashlib.sha256(token.encode()).hexdigest()])
    with db:
        db.execute('CREATE TABLE IF NOT EXISTS vk_destination (id INTEGER PRIMARY KEY, binding TEXT NOT NULL)')
        old = db.execute('SELECT binding FROM vk_destination WHERE id=1').fetchone()
        if old and old[0] != binding:
            raise ValueError('Получатель или токен отличается от привязанного к этой очереди. Автоматическая смена запрещена.')
        db.execute('INSERT OR IGNORE INTO vk_destination VALUES (1,?)', (binding,))
        db.execute('''CREATE TABLE IF NOT EXISTS vk_delivery (
            notification_id INTEGER PRIMARY KEY, random_id INTEGER NOT NULL UNIQUE,
            attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0,
            blocked INTEGER NOT NULL DEFAULT 0, last_error TEXT,
            message_id INTEGER, accepted_at REAL)''')


def rotate_token(db, peer_id, token):
    """Explicit local credential rotation; caller holds the sender lock."""
    if not token.strip():
        raise ValueError('Новый токен пуст')
    with db:
        old = db.execute('SELECT binding FROM vk_destination WHERE id=1').fetchone()
        if not old or json.loads(old[0])[0] != peer_id:
            raise ValueError('Замена токена требует существующую привязку к тому же получателю')
        binding = json.dumps([peer_id, hashlib.sha256(token.encode()).hexdigest()])
        db.execute('UPDATE vk_destination SET binding=? WHERE id=1', (binding,))


def deliver_next(db, token, peer_id, send=vk_send, now=None):
    now = time.time() if now is None else now
    row = db.execute("SELECT * FROM notification_outbox WHERE status='pending' ORDER BY id LIMIT 1").fetchone()
    if row is None:
        return 'empty'
    with db:
        if not db.execute('SELECT 1 FROM vk_delivery WHERE notification_id=?', (row['id'],)).fetchone():
            while True:
                random_id = secrets.randbelow(2147483647) + 1
                if not db.execute('SELECT 1 FROM vk_delivery WHERE random_id=?', (random_id,)).fetchone():
                    break
            db.execute('INSERT INTO vk_delivery(notification_id,random_id) VALUES (?,?)', (row['id'], random_id))
    delivery = db.execute('SELECT * FROM vk_delivery WHERE notification_id=?', (row['id'],)).fetchone()
    if delivery['blocked']:
        return 'blocked'
    if delivery['next_attempt'] > now:
        return 'waiting'
    attempts = delivery['attempts'] + 1
    # Persist before HTTP: a crash keeps the same random_id and retry delay.
    with db:
        db.execute('UPDATE vk_delivery SET attempts=?,next_attempt=? WHERE notification_id=?',
                   (attempts, now + 30, row['id']))
    try:
        message_id = send(token, peer_id, row['message'], delivery['random_id'])
    except DeliveryError as exc:
        blocked = not exc.retryable or attempts >= 5
        with db:
            db.execute('UPDATE vk_delivery SET blocked=?,last_error=?,next_attempt=? WHERE notification_id=?',
                       (int(blocked), str(exc), now + min(30 * 2 ** min(attempts - 1, 5), 900), row['id']))
        return 'blocked' if blocked else 'retry'
    with db:
        db.execute('UPDATE vk_delivery SET message_id=?,accepted_at=?,last_error=NULL WHERE notification_id=?',
                   (message_id, time.time(), row['id']))
        db.execute("UPDATE notification_outbox SET status='sent' WHERE id=?", (row['id'],))
    return 'sent'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--peer-id', type=int, required=True, help='Числовой ID вашего профиля VK')
    parser.add_argument('--send', action='store_true', help='Отправить ожидающие сообщения в VK')
    parser.add_argument('--rotate-token', action='store_true', help='Заменить привязанный токен без отправки; новый ключ должен быть от того же сообщества')
    parser.add_argument('--watch', action='store_true', help='Продолжать обработку очереди')
    parser.add_argument('--retry-failed', action='store_true', help='После исправления причины повторить заблокированные сообщения')
    args = parser.parse_args()
    if args.rotate_token and (args.send or args.watch or args.retry_failed):
        parser.error('--rotate-token выполняется отдельно от отправки')
    if not 0 < args.peer_id < 2000000000:
        parser.error('На этом этапе поддерживается личный диалог с пользователем VK')
    path = args.database.resolve()
    if not path.is_file():
        parser.error('База не найдена')
    if not args.send and (args.watch or args.retry_failed):
        parser.error('--watch и --retry-failed требуют --send')
    db = sqlite3.connect(path.as_uri() + ('?mode=rw' if args.send or args.rotate_token else '?mode=ro'), uri=True)
    db.row_factory = sqlite3.Row
    try:
        if not args.send and not args.rotate_token:
            print(f'Предпросмотр. Получатель: {args.peer_id}. Отправки нет.')
            for row in db.execute("SELECT id,message FROM notification_outbox WHERE status='pending' ORDER BY id"):
                print(f"\n--- {row['id']} ---\n{row['message']}")
            return
        token = os.environ.get('VK_COMMUNITY_TOKEN', '').strip()
        if not token:
            parser.error('Задайте VK_COMMUNITY_TOKEN в окружении; не передавайте его аргументом')
        import fcntl
        with Path(str(path) + '.vk-sender.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('Отправитель для этой базы уже работает') from None
            if args.rotate_token:
                rotate_token(db, args.peer_id, token)
                print('Привязка токена обновлена. Очередь и история сохранены. Отправки не было.')
                return
            setup(db, args.peer_id, token)
            print('Отправитель VK запущен. Ожидающие сообщения будут отправлены.', flush=True)
            if args.retry_failed:
                with db:
                    db.execute('UPDATE vk_delivery SET blocked=0,attempts=0,next_attempt=0 WHERE blocked=1')
            while True:
                result = deliver_next(db, token, args.peer_id)
                if result not in ('empty', 'waiting'):
                    print('Доставка VK:', result, flush=True)
                if result == 'blocked':
                    print('Отправка остановлена. Код ошибки сохранён в vk_delivery; после исправления используйте --retry-failed.')
                    return
                if not args.watch and result != 'sent':
                    return
                time.sleep(1 if result == 'sent' else 5)
    finally:
        db.close()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nОтправитель остановлен. Очередь сохранена.')
    except (ValueError, sqlite3.Error) as exc:
        raise SystemExit(str(exc))
