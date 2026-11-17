import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from notification_queue import initialize
from send_vk_notifications import setup, deliver_next, DeliveryError, rotate_token


class VKTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        initialize(self.db)
        for n, kind in ((1, 'incident_opened'), (2, 'recovered')):
            self.db.execute('INSERT INTO notification_outbox(event_id,incident_id,event_type,created_at,message) VALUES (?,?,?,?,?)',
                            (n, 'incident', kind, 'time', kind))
        self.db.commit()
        setup(self.db, 123, 'test-token')

    def test_success_is_not_resent(self):
        send = Mock(return_value=7)
        self.assertEqual(deliver_next(self.db, 'test-token', 123, send, 100), 'sent')
        self.assertEqual(deliver_next(self.db, 'test-token', 123, send, 101), 'sent')
        self.assertEqual(deliver_next(self.db, 'test-token', 123, send, 102), 'empty')
        self.assertEqual(send.call_count, 2)
        self.assertEqual([c.args[2] for c in send.call_args_list], ['incident_opened', 'recovered'])

    def test_timeout_preserves_random_id_and_order(self):
        send = Mock(side_effect=[DeliveryError('network_error', True), 7])
        self.assertEqual(deliver_next(self.db, 'test-token', 123, send, 100), 'retry')
        self.assertEqual(deliver_next(self.db, 'test-token', 123, send, 110), 'waiting')
        self.assertEqual(deliver_next(self.db, 'test-token', 123, send, 131), 'sent')
        self.assertEqual(send.call_args_list[0].args, send.call_args_list[1].args)
        self.assertEqual(self.db.execute("SELECT count(*) FROM notification_outbox WHERE status='pending'").fetchone()[0], 1)

    def test_access_error_blocks_later_messages(self):
        send = Mock(side_effect=DeliveryError('vk_error_901'))
        self.assertEqual(deliver_next(self.db, 'test-token', 123, send, 100), 'blocked')
        self.assertEqual(deliver_next(self.db, 'test-token', 123, send, 1000), 'blocked')
        self.assertEqual(send.call_count, 1)

    def test_destination_cannot_change(self):
        with self.assertRaises(ValueError):
            setup(self.db, 456, 'test-token')
        with self.assertRaises(ValueError):
            setup(self.db, 123, 'different-token')

    def test_rotation_preserves_history_and_retry_identifier(self):
        deliver_next(self.db, 'test-token', 123, Mock(return_value=7), 100)
        deliver_next(self.db, 'test-token', 123, Mock(side_effect=DeliveryError('network_error', True)), 101)
        before = [tuple(r) for r in self.db.execute('SELECT * FROM vk_delivery ORDER BY notification_id')]
        rotate_token(self.db, 123, 'replacement')
        setup(self.db, 123, 'replacement')
        self.assertEqual(before, [tuple(r) for r in self.db.execute('SELECT * FROM vk_delivery ORDER BY notification_id')])
        self.assertEqual(self.db.execute("SELECT count(*) FROM notification_outbox WHERE status='sent'").fetchone()[0], 1)
        with self.assertRaises(ValueError):
            rotate_token(self.db, 456, 'other')
        setup(self.db, 123, 'replacement')


if __name__ == '__main__':
    unittest.main()
