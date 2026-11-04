"""Authenticated HTTP operations cannot fall back to a default native account."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import urlencode

import test_bridge
from bridge.clients.uploads import DesktopUploads
from bridge.features.notifications.events import MobileEvents


class InstanceHTTP(unittest.TestCase):
    setUpClass = classmethod(test_bridge.HttpTests.setUpClass.__func__)
    tearDown = test_bridge.HttpTests.tearDown
    request = test_bridge.HttpTests.request
    login = test_bridge.HttpTests.login

    def setUp(self):
        test_bridge.HttpTests.setUp(self)
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = self.server.desktop_sessions = Mock()
        self.entries = {'claude:' + c*32: Mock() for c in ('a', 'b')}
        for identifier, manager in self.entries.items():
            manager.uploads = DesktopUploads(Path(self.temp.name)/identifier.replace(':', '-'))
            manager.call.return_value = {'session': {'id': 'same-id', 'backend': 'cli'}, 'status': 'accepted'}
            manager.list_with_activity.return_value = {'sessions': [{'id': 'same-id', 'title': identifier}]}
        def resolve(family, entry, **kwargs):
            if family != 'claude' or entry not in self.entries:
                raise ValueError('Unknown instance')
            return self.entries[entry]
        self.root.resolve_instance.side_effect = resolve

    def test_same_session_uses_selected_account_for_reads_writes_and_catalog(self):
        headers = self.login()
        for entry, manager in self.entries.items():
            query = '?' + urlencode({'entry': entry, 'sessionId': 'same-id'})
            base = '/api/desktop-sessions/claude/'
            status, _, value = self.request('GET', base+'list'+query, headers=headers)
            self.assertEqual(status, 200, value)
            self.assertEqual(value['sessions'][0]['title'], entry)
            body = {'sessionId': 'same-id', 'id': 'receipt', 'text': 'marker'}
            self.assertEqual(self.request('POST', base+'send'+query, body, headers)[0], 200)
            manager.call.assert_called_with('claude', 'send', 'same-id', {'id': 'receipt', 'text': 'marker'})
            manager.call.reset_mock()
            self.assertEqual(self.request('GET', base+'create-models'+query, headers=headers)[0], 200)
            manager.call.assert_called_once_with('claude', 'create-models', 'same-id', {'refresh': False})
        self.root.call.assert_not_called()
        self.root.list_with_activity.assert_not_called()

    def test_explicit_invalid_entry_cannot_operate_default_or_bypass_csrf(self):
        headers = self.login()
        base = '/api/desktop-sessions/claude/send?'
        body = {'sessionId': 'same-id', 'id': 'receipt', 'text': 'marker'}
        for query in ('entry=', 'entry=claude%3A'+'f'*32, 'entry=claude&entry=deepseek', 'entry=zcode'):
            self.assertEqual(self.request('POST', base+query, body, headers)[0], 400)
        query = urlencode({'entry': next(iter(self.entries))})
        self.assertEqual(self.request('POST', base+query, body, {'Cookie': headers['Cookie']})[0], 403)
        self.root.call.assert_not_called()
        for manager in self.entries.values():
            manager.call.assert_not_called()

    def test_uploaded_file_cannot_be_sent_from_another_instance_with_same_session(self):
        headers = self.login()
        a, b = self.entries
        identifier = '00000000-0000-4000-8000-000000000010'
        uploads = self.entries[a].uploads
        uploads.store.put(uploads.scope('claude', 'same-id'), identifier, 'note.txt', b'instance A')
        body = {'sessionId': 'same-id', 'id': 'receipt', 'text': 'marker', 'attachments': [identifier]}
        path = '/api/desktop-sessions/claude/send?'
        status, _, value = self.request('POST', path+urlencode({'entry': b}), body, headers)
        self.assertEqual(status, 400, value)
        self.assertFalse(any(call.args[1] == 'send' for call in self.entries[b].call.call_args_list))
        status, _, value = self.request('POST', path+urlencode({'entry': a}), body, headers)
        self.assertEqual(status, 200, value)
        sent = self.entries[a].call.call_args.args[3]
        self.assertEqual(Path(sent['resolvedFiles'][0]['path']).read_bytes(), b'instance A')


class InstanceNotifications(unittest.TestCase):
    def test_reading_one_instance_does_not_mark_default_or_sibling_read(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp') as temp:
            feed = MobileEvents(temp)
            clients = ['claude', 'claude:'+'a'*32, 'claude:'+'b'*32]
            for client in clients:
                feed.append([client], {'id': 'same-id', 'host': 'desktop:'+client}, 'done', '', True)
            snapshot = feed.read()
            feed.acknowledge(clients[1], snapshot['streamId'], snapshot['cursor'])
            value = MobileEvents(temp).read()
            self.assertEqual(value['unread']['claude'], 2)
            self.assertEqual({row['clientId']: row['unread'] for row in value['events']},
                             dict(zip(clients, [True, False, True])))
            self.assertTrue(all(row['provider'] == 'claude' for row in value['events']))
            feed.acknowledge('all', snapshot['streamId'], snapshot['cursor'])
            self.assertEqual(feed.read()['unread']['claude'], 0)
