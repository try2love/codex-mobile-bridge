import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge.auth import Auth, password_record


class RememberLoginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = {'mode': 'password', 'username': 'admin', **password_record('test-only-password')}

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.file = Path(folder.name) / 'sessions.json'
        self.auth = Auth(self.config, self.file)

    def login(self, remember=True):
        return self.auth.login('admin', 'test-only-password', '127.0.0.1', remember)

    def test_remembered_login_survives_restart_without_storing_token_or_password(self):
        before = time.time()
        token, session = self.login()
        restored = Auth(self.config, self.file).get(token)
        self.assertEqual(restored['csrf'], session['csrf'])
        self.assertGreaterEqual(session['expires'], before + 30 * 24 * 3600)
        saved = self.file.read_text()
        self.assertNotIn(token, saved)
        self.assertNotIn('test-only-password', saved)
        self.assertIn(Auth.token_key(token), json.loads(saved)['sessions'])
        self.assertIsNone(Auth(self.config, self.file).get(Auth.token_key(token)))

    def test_normal_session_is_not_persisted(self):
        token, session = self.login(False)
        self.assertFalse(session['remember'])
        self.assertLessEqual(session['expires'], time.time() + Auth.SESSION_SECONDS)
        self.assertIsNotNone(self.auth.get(token))
        self.assertIsNone(Auth(self.config, self.file).get(token))

    def test_logout_revokes_after_restart(self):
        token, _ = self.login()
        self.auth.logout(token)
        self.assertIsNone(self.auth.get(token))
        self.assertIsNone(Auth(self.config, self.file).get(token))

    def test_expiry_revokes_after_restart(self):
        token, session = self.login()
        with patch('bridge.auth.time.time', return_value=session['expires'] + 1):
            self.assertIsNone(self.auth.get(token))
            self.assertIsNone(Auth(self.config, self.file).get(token))

    def test_password_or_account_change_invalidates_saved_logins(self):
        token, _ = self.login()
        for field, value in [('hash', '0' * 64), ('username', 'other'), ('mode', 'none')]:
            with self.subTest(field=field):
                self.assertIsNone(Auth({**self.config, field: value}, self.file).get(token))

    def test_wrong_password_and_bad_option_cannot_create_remembered_session(self):
        with self.assertRaises(PermissionError):
            self.auth.login('admin', 'incorrect', '127.0.0.1', True)
        with self.assertRaises(ValueError):
            self.login('true')
        self.assertFalse(self.file.exists())

    def test_corrupt_session_file_fails_closed(self):
        self.file.write_text('not json')
        self.assertEqual(Auth(self.config, self.file).sessions, {})

    def test_write_failure_does_not_leave_new_session(self):
        with patch.object(self.auth, '_save_sessions', side_effect=OSError('disk unavailable')):
            with self.assertRaises(ValueError):
                self.login()
        self.assertFalse(self.auth.sessions)


if __name__ == '__main__':
    unittest.main()
