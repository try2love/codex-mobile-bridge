import tempfile
import threading
import unittest
import uuid
import hashlib
import json
import sqlite3
from pathlib import Path
from unittest.mock import Mock

from bridge.integrations.manager import DesktopSessions

ROOT = Path(__file__).resolve().parents[1]


def legacy_send(manager, sid, body):
    """The SELECT/INSERT transaction from the shipped Desktop Lab manager.

    Verified against its packaged call() bytecode, not a second instance of the
    new INSERT OR IGNORE implementation. Keep this fixture on the old algorithm.
    """
    rid = str(uuid.UUID(body['id']))
    fingerprint = hashlib.sha256(json.dumps(['deepseek', 'send', sid, body], sort_keys=True).encode()).hexdigest()
    with manager.locks['deepseek']:
        with manager.db_lock:
            old = manager.db.execute('SELECT fingerprint,result FROM requests WHERE id=?', (rid,)).fetchone()
            if old:
                if old[0] != fingerprint:
                    raise ValueError('相同请求 ID 不能用于不同操作')
                return json.loads(old[1])
            result = {'status': 'unknown', 'id': rid, 'message': '结果待核对，请检查桌面原会话；相同请求不会重复执行'}
            manager.db.execute('INSERT INTO requests VALUES (?,?,?)', (rid, fingerprint, json.dumps(result)))
            manager.db.commit()
        result = manager.adapters['deepseek'].call('send', sid, body)
        with manager.db_lock:
            manager.db.execute('UPDATE requests SET result=? WHERE id=?', (json.dumps(result), rid))
            manager.db.commit()
        return result


class RecoveredReceipts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.old = DesktopSessions(root/'old')
        self.new = DesktopSessions(root/'new')
        self.addCleanup(self.old.close)
        self.addCleanup(self.new.close)
        self.adapter = Mock()
        self.adapter.receipts_path = self.old.directory/'requests.sqlite'
        self.new.adapters['deepseek'] = self.adapter
        self.body = {'id': str(uuid.uuid4()), 'text': 'one explicit message'}

    def test_recovery_keeps_old_unknown_receipt_without_resending(self):
        old = Mock()
        old.call.side_effect = TimeoutError('original result unknown')
        self.old.adapters['deepseek'] = old
        first = self.old.call('deepseek', 'send', 'native-session', self.body)
        self.assertEqual(first['status'], 'unknown')
        self.assertEqual(self.new.call('deepseek', 'send', 'native-session', self.body), first)
        self.adapter.call.assert_not_called()
        with self.assertRaisesRegex(ValueError, '相同请求 ID'):
            self.new.call('deepseek', 'send', 'another-session', self.body)

    def test_shared_claim_keeps_inflight_request_at_most_once(self):
        entered, release = threading.Event(), threading.Event()
        old = Mock()
        def dispatch(*_):
            entered.set()
            if not release.wait(3):
                raise TimeoutError()
            return {'status': 'accepted'}
        old.call.side_effect = dispatch
        self.old.adapters['deepseek'] = old
        thread = threading.Thread(target=self.old.call, args=('deepseek', 'send', 'native-session', self.body))
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            pending = self.new.call('deepseek', 'send', 'native-session', self.body)
            self.assertEqual(pending['status'], 'unknown')
            self.adapter.call.assert_not_called()
        finally:
            release.set()
            thread.join(3)
        self.assertEqual(self.new.call('deepseek', 'send', 'native-session', self.body), {'status': 'accepted'})
        self.assertEqual(old.call.call_count, 1)
        self.adapter.call.assert_not_called()

    def test_missing_old_receipts_never_creates_empty_history_and_sends(self):
        self.adapter.receipts_path = self.old.directory/'missing.sqlite'
        with self.assertRaisesRegex(ValueError, '操作记录不可用'):
            self.new.call('deepseek', 'send', 'native-session', self.body)
        self.adapter.call.assert_not_called()
        self.assertFalse(self.adapter.receipts_path.exists())

    def test_real_legacy_claim_first_does_not_repeat_an_inflight_send(self):
        entered, release = threading.Event(), threading.Event()
        old = Mock()
        def dispatch(*args):
            entered.set()
            self.assertTrue(release.wait(3))
            return {'status': 'accepted'}
        old.call.side_effect = dispatch
        self.old.adapters['deepseek'] = old
        thread = threading.Thread(target=legacy_send, args=(self.old, 'native-session', self.body))
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertEqual(self.new.call('deepseek', 'send', 'native-session', self.body)['status'], 'unknown')
            self.adapter.call.assert_not_called()
        finally:
            release.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.new.call('deepseek', 'send', 'native-session', self.body), {'status': 'accepted'})
        self.assertEqual(old.call.call_count, 1)

    def test_new_claim_before_legacy_insert_sends_once_and_explains_old_database_lock(self):
        selected, proceed = threading.Event(), threading.Event()
        real_db = self.old.db
        old = Mock()
        self.old.adapters['deepseek'] = old
        self.adapter.call.return_value = {'status': 'accepted'}
        errors = []
        class PausedLegacyDatabase:
            def execute(self, sql, args):
                cursor = real_db.execute(sql, args)
                if sql.startswith('SELECT'):
                    class Cursor:
                        def fetchone(self):
                            result = cursor.fetchone()
                            selected.set()
                            if not proceed.wait(3):
                                raise TimeoutError('test failed to release legacy read')
                            return result
                    return Cursor()
                return cursor
            def commit(self):
                real_db.commit()
        self.old.db = PausedLegacyDatabase()
        def run_old():
            try:
                legacy_send(self.old, 'native-session', self.body)
            except Exception as exc:
                errors.append(exc)
        thread = threading.Thread(target=run_old)
        thread.start()
        try:
            self.assertTrue(selected.wait(2))
            self.assertEqual(self.new.call('deepseek', 'send', 'native-session', self.body), {'status': 'accepted'})
            proceed.set()
            thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], sqlite3.IntegrityError)
            self.assertTrue(real_db.in_transaction)  # Limitation of the already-shipped old owner.
            shared = self.new._receipts(self.adapter)
            shared.execute('PRAGMA busy_timeout=50')
            with self.assertRaisesRegex(ValueError, '接入的操作记录正被另一网关占用，请停止旧网关后重试'):
                self.new.call('deepseek', 'send', 'native-session', {'id': str(uuid.uuid4()), 'text': 'another explicit message'})
            self.assertFalse(shared.in_transaction)
            self.assertEqual(self.adapter.call.call_count, 1)
            old.call.assert_not_called()
        finally:
            proceed.set()
            thread.join(3)
            real_db.rollback()
            self.old.db = real_db
