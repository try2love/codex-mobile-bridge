"""Reconnect reads may observe an existing operation without replaying native work."""
import tempfile
import unittest
import uuid
from pathlib import Path
from bridge.clients.manager import DesktopSessions


class NativeRecovery:
    def __init__(self):
        self.ticket = object()
        self.observed = []
        self.complete = False

    def resume_step(self, sid, rid, continuation):
        self.observed.append(continuation)
        if continuation is self.ticket and self.complete:
            return {'status': 'ready', 'id': rid}, None
        return {'status': 'pending', 'id': rid, 'progress': 1, 'stage': 'restoring'}, self.ticket


class ProgressiveResumeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.manager = DesktopSessions(Path(temp.name))
        self.addCleanup(self.manager.close)
        self.native = NativeRecovery()
        self.manager.adapters['zcode'] = self.native
        self.manager.config.setdefault('enabled', {})['zcode'] = True
        self.request = {'id': str(uuid.uuid4()), 'progressive': True}

    def poll(self):
        return self.manager.call('zcode', 'resume', 'sess_a', self.request)

    def test_duplicate_after_lost_first_reply_observes_without_restarting(self):
        pending = self.poll()
        self.assertEqual(pending['status'], 'pending')
        self.assertEqual(self.poll(), pending)
        self.native.complete = True
        ready = self.poll()
        self.assertEqual(ready['status'], 'ready')
        self.assertEqual(self.poll(), ready)
        self.assertEqual(self.native.observed, [None, self.native.ticket, self.native.ticket])
        self.assertFalse(self.manager.pending_resumes)

    def test_old_receipt_cannot_activate_another_session_or_replacement_adapter(self):
        self.poll()
        with self.assertRaisesRegex(ValueError, '相同请求 ID'):
            self.manager.call('zcode', 'resume', 'sess_b', self.request)
        replacement = NativeRecovery()
        self.manager.adapters['zcode'] = replacement
        self.assertEqual(self.poll()['status'], 'blocked')
        self.assertEqual(replacement.observed, [])
        with self.assertRaises(ValueError):
            self.manager.call('zcode', 'send', 'sess_a', {**self.request, 'text': 'do not send'})
