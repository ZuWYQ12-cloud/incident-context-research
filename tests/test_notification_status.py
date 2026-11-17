import sqlite3
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from notification_status import notification_status
from notification_queue import initialize
from send_vk_notifications import setup


class NotificationStatusTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)

    def add_notice(self):
        initialize(self.db)
        self.db.execute("INSERT INTO notification_outbox(event_id,incident_id,event_type,created_at,message) VALUES (1,'abc','incident_opened','time','text')")

    def test_old_database_is_supported(self):
        self.assertEqual(notification_status(self.db, ['abc']), {'abc': []})

    def test_queue_without_sender(self):
        self.add_notice()
        result = notification_status(self.db, ['abc', 'other'])
        self.assertEqual(result['abc'][0]['delivery_state'], 'pending')
        self.assertEqual(result['other'], [])

    def test_delivery_states_and_no_credential_exposure(self):
        self.add_notice()
        setup(self.db, 123, 'secret-token')
        self.db.execute("INSERT INTO vk_delivery(notification_id,random_id,attempts,next_attempt,last_error) VALUES(1,1234,1,100,'network_error')")
        item = notification_status(self.db, ['abc'])['abc'][0]
        self.assertEqual(item['delivery_state'], 'retry')
        self.assertTrue(item['next_attempt'].endswith('+00:00'))
        self.assertNotIn('binding', item)
        self.assertNotIn('random_id', item)
        self.db.execute('UPDATE vk_delivery SET blocked=1')
        self.assertEqual(notification_status(self.db, ['abc'])['abc'][0]['delivery_state'], 'blocked')
        self.db.execute("UPDATE notification_outbox SET status='sent'")
        self.db.execute('UPDATE vk_delivery SET accepted_at=200,message_id=7,last_error=NULL')
        self.assertEqual(notification_status(self.db, ['abc'])['abc'][0]['delivery_state'], 'sent')


if __name__ == '__main__':
    unittest.main()
