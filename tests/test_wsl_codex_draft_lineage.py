"""Metadata-only Codex WSL draft handoff; no real CLI, daemon, or model access."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

from bridge.clients.codex.ipc import IPCError
from bridge.features.accounts import wsl_account_worker as worker
from bridge.features.accounts.draft_identity import codex_account_identity, draft_identity
from bridge.features.endpoints.wsl import WslEntry
from tests.test_wsl_accounts import install_target_toml

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'posix', 'Synthetic credential files use POSIX ownership')
class WslCodexDraftLineageTests(unittest.TestCase):
    def setUp(self):
        install_target_toml(self)
        (ROOT/'.tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()/'home'
        self.home.mkdir()
        (self.home/'auth.json').write_text('{"auth_mode":"apikey","OPENAI_API_KEY":"old-private-fixture"}')
        (self.home/'config.toml').write_text('model_provider="original_api"\n[model_providers.original_api]\nname="Original"\nbase_url="https://old.invalid"\nrequires_openai_auth=true\n')
        self.row = {'id': 'wsl:'+'a'*32, 'distribution': 'Fixture', 'user': 'fixture',
                    'home': str(self.home), 'label': 'Fixture'}
        with patch.object(threading.Thread, 'start'):
            self.entry = WslEntry(self.row, Path(self.temp.name)/'endpoint')
        self.entry.worker = Mock()  # No maintenance thread is started in this fixture.
        self.addCleanup(self.entry.close)
        self.rows = [{'id': str(uuid.uuid4()), 'cwd': '/project/'+str(i),
                      'model_provider': 'original_api', 'archived': i >= 401} for i in range(403)]
        self.rows.append({'id': str(uuid.uuid4()), 'cwd': '/other', 'model_provider': 'unrelated', 'archived': False})
        self.pages = []
        def listing(**options):
            self.pages.append(dict(options))
            rows = [row for row in self.rows if row['archived'] == options['archived']]
            return copy.deepcopy(rows[options['offset']:options['offset']+options['limit']])
        self.entry.store = SimpleNamespace(list=listing, get=lambda sid: copy.deepcopy(next(row for row in self.rows if row['id'] == sid)),
                                           history=Mock(side_effect=AssertionError('no history content')))
        self.entry.runtime = Mock(side_effect=AssertionError('no runtime activation'))
        self.sources = []
        def read(_alias, source):
            self.sources.append(source)
            output = io.StringIO()
            with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(output):
                exec(source, {'__file__': worker.__file__})
            return json.loads(output.getvalue())
        self.entry.read = read
        self.store = worker.AccountStore('codex', self.home, guard=lambda *_: [])

    def switch(self, number):
        self.store.change({'action': 'save-api', 'name': 'Account '+str(number),
            'baseUrl': 'https://new'+str(number)+'.invalid', 'model': 'selected-model', 'apiKey': 'new-private-'+str(number)})
        result = self.store.change({'action': 'switch', 'id': self.store.store['accounts'][-1]['id'],
                                   'expectedIdentity': self.store.public()['identity']})
        return result['identity']

    def test_first_api_without_old_login_or_history_still_configures_without_inventing_draft_owner(self):
        (self.home/'auth.json').unlink()
        (self.home/'config.toml').write_text('')
        self.rows = []
        snapshot = self.entry.capture_account_drafts('openai')
        self.assertEqual(snapshot['accountIdentity'], '')
        self.assertEqual(snapshot['sessions'], [])
        self.assertEqual(self.entry.complete_account_drafts(snapshot, self.switch(1)), [])
        self.assertFalse((self.entry.data_dir/'account-drafts.json').exists())

    def test_no_old_owner_with_history_does_not_grant_draft_migration_or_mutate_catalog(self):
        (self.home/'auth.json').write_text('{}')
        before = copy.deepcopy(self.rows)
        snapshot = self.entry.capture_account_drafts('original_api')
        self.assertEqual(snapshot['accountIdentity'], '')
        self.assertEqual(len(snapshot['sessions']), 403)
        self.assertEqual(self.entry.complete_account_drafts(snapshot, self.switch(1)), [])
        self.assertEqual(self.rows, before)
        self.assertFalse((self.entry.data_dir/'account-drafts.json').exists())
        self.entry.store.history.assert_not_called()
        self.entry.runtime.assert_not_called()

    def test_all_inactive_and_archived_history_is_captured_and_completed_without_activation(self):
        snapshot = self.entry.capture_account_drafts('original_api')
        self.assertEqual(len(snapshot['sessions']), 403)
        self.assertTrue(any(row['offset'] == 400 for row in self.pages))
        self.assertTrue(any(row['archived'] for row in self.pages))
        self.assertTrue(all(set(row) == {'id', 'cwd', 'provider'} for row in snapshot['sessions']))
        old = draft_identity('codex', self.entry.host, 'original_api', codex_account_identity(
            self.home, 'original_api', shared_auth=True, toml_loads=worker._toml_loads))
        self.assertEqual(snapshot['accountIdentity'], old)
        self.assertNotIn('old-private-fixture', json.dumps(snapshot))
        identity = self.switch(1)
        drafts = self.entry.complete_account_drafts(snapshot, identity)
        self.assertEqual(len(drafts), 403)
        self.assertEqual(drafts[0]['previousAccountIdentity'], old)
        self.assertNotEqual(drafts[0]['accountIdentity'], old)
        self.assertNotIn('new-private-1', json.dumps(drafts))
        self.entry.runtime.assert_not_called()
        self.entry.store.history.assert_not_called()
        self.assertEqual((self.entry.data_dir/'account-drafts.json').stat().st_mode & 0o777, 0o600)

    def test_view_uses_persistent_wrapped_lineage_and_external_changes_do_not_get_it(self):
        snapshot = self.entry.capture_account_drafts('original_api')
        self.entry.complete_account_drafts(snapshot, self.switch(1))
        sid = self.rows[-2]['id']  # Archived and never live/subscribed.
        view = Mock(side_effect=lambda **_kwargs: {'id': sid, 'turns': [], 'connected': False})
        session = SimpleNamespace(connected=False, view=view)
        self.entry.session = lambda *_args, **_kwargs: session
        self.entry._management_identity = lambda *_args, **_kwargs: ''
        self.entry._view_features = lambda _session, result: result
        result = self.entry.view(sid)
        view.assert_called_once_with(process_bodies=True)
        self.assertEqual(result['draftContinuation']['entry'], self.entry.host)
        self.assertEqual(result['draftContinuation']['previousAccountIdentities'], [snapshot['accountIdentity']])
        self.assertEqual(result['draftAccountIdentity'], result['draftContinuation']['accountIdentity'])
        self.rows[-2]['cwd'] = '/externally-changed'
        self.assertNotIn('draftContinuation', self.entry.view(sid))
        self.rows[-2]['cwd'] = '/project/402'
        with (self.home/'config.toml').open('a') as stream:
            stream.write('# external configuration edit\n')
        self.assertNotIn('draftContinuation', self.entry.view(sid))

    def test_changed_catalog_tuple_or_entry_rejects_before_persisting_any_lineage(self):
        snapshot = self.entry.capture_account_drafts('original_api')
        identity = self.switch(1)
        changed = copy.deepcopy(snapshot)
        changed['entry'] = 'wsl:'+'b'*32
        with self.assertRaises(IPCError):
            self.entry.complete_account_drafts(changed, identity)
        self.rows[-2]['cwd'] = '/changed'
        with self.assertRaises(IPCError):
            self.entry.complete_account_drafts(snapshot, identity)
        self.assertFalse((self.entry.data_dir/'account-drafts.json').exists())

    def test_multiple_explicit_switches_retain_offline_prior_account_lineage(self):
        before = self.entry.capture_account_drafts('original_api')
        first = self.entry.complete_account_drafts(before, self.switch(1))
        middle = self.entry.capture_account_drafts('original_api')
        self.assertEqual(middle['accountIdentity'], first[0]['accountIdentity'])
        applied = self.switch(2)
        self.entry.complete_account_drafts(middle, applied)
        persisted = (self.entry.data_dir/'account-drafts.json').read_bytes()
        # Recovery may repeat completion after persisting lineage but before
        # the lifecycle transaction's final receipt reached disk or the client.
        self.entry.complete_account_drafts(middle, applied)
        self.assertEqual((self.entry.data_dir/'account-drafts.json').read_bytes(), persisted)
        hint = self.entry._account_draft_continuation(self.rows[0]['id'])
        self.assertEqual(hint['previousAccountIdentities'], [before['accountIdentity'], middle['accountIdentity']])
        self.entry.runtime.assert_not_called()
