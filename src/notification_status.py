"""Read delivery state without exposing destination credentials."""
from datetime import datetime, timezone


def timestamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat() if value else None


def notification_status(db, incident_ids):
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    result = {identifier: [] for identifier in incident_ids}
    if not incident_ids or 'notification_outbox' not in tables:
        return result
    delivery = 'vk_delivery' in tables
    fields = ('d.attempts,d.next_attempt,d.blocked,d.last_error,d.message_id,d.accepted_at'
              if delivery else '0 AS attempts,0 AS next_attempt,0 AS blocked,NULL AS last_error,NULL AS message_id,NULL AS accepted_at')
    join = 'LEFT JOIN vk_delivery d ON d.notification_id=o.id' if delivery else ''
    marks = ','.join('?' for _ in incident_ids)
    rows = db.execute(f'''SELECT o.id,o.incident_id,o.event_type,o.created_at,o.status,{fields}
        FROM notification_outbox o {join} WHERE o.incident_id IN ({marks}) ORDER BY o.id''', incident_ids)
    for row in rows:
        item = dict(row)
        state = ('sent' if item['status'] == 'sent' else
                 'blocked' if item['blocked'] else
                 'retry' if item['last_error'] else
                 'attempted' if item['attempts'] else 'pending')
        item['delivery_state'] = state
        item['attempts'] = item['attempts'] or 0
        item['accepted_at'] = timestamp(item['accepted_at'])
        item['next_attempt'] = timestamp(item['next_attempt']) if state == 'retry' else None
        result[item['incident_id']].append(item)
    return result
