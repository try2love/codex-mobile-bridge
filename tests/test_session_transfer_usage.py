"""Account lifetime and metering of the actual gzip body at the HTTP boundary."""
import gzip
import hashlib
import http.client
import json
import os
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_bridge
from bridge.api.httpd import GatewayServer
from bridge.app.desktop import Desktop
from bridge.features.auth.auth import Auth
from bridge.features.auth.session_transfer import TransferUsage
from bridge.features.sessions import sync
from bridge.features.sessions.timeline import Timeline


class TransferAccounts(unittest.TestCase):
    def test_old_totals_gain_source_labels_without_losing_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'session-transfer.sqlite3'
            with closing(sqlite3.connect(path)) as db, db:
                db.execute('CREATE TABLE usage (login TEXT,provider TEXT,host TEXT,session TEXT,title TEXT,'
                           'bytes INTEGER NOT NULL,PRIMARY KEY(login,provider,host,session))')
                db.execute("INSERT INTO usage VALUES ('phone','codex','ssh-id','thread','Old name',123)")
            with closing(TransferUsage(tmp)) as usage:
                self.assertEqual(usage.summary('phone')['totalBytes'], 123)
                self.assertEqual(usage.summary('phone')['totalReusedBytes'], 0)
                usage.record('phone', 'codex', 'ssh-id', 'thread', 'New name', 16, method='cli', host_label='88server', reused_bytes=11)
                usage.record('phone', 'codex', 'ssh-id', 'thread', '', 4, reused_bytes=5)
                self.assertEqual(usage.summary('phone')['sessions'], [
                    {'provider': 'codex', 'host': 'ssh-id', 'sessionId': 'thread', 'title': 'New name',
                     'bytes': 143, 'method': 'cli', 'hostLabel': '88server', 'reusedBytes': 16}])
                self.assertEqual(usage.summary('phone')['totalReusedBytes'], 16)
                self.assertEqual(usage.summary('browser')['sessions'], [])

    def test_gateway_slot_owner_and_explicit_invalidation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            with closing(Auth({'mode': 'none', 'username': 'alpha'}, path)) as auth:
                token, session = auth.new_session()
                key = auth.key(token)
                caps = auth.session_sync
                with closing(Auth(auth.config, path)) as reopened:
                    self.assertEqual(reopened.session_sync, caps)
                self.assertTrue(all(len(caps[k]) == size for k, size in [('gatewayId', 32), ('identity', 64)]))
                with closing(Auth(auth.config)) as other_gateway:
                    self.assertNotEqual(caps['gatewayId'], other_gateway.session_sync['gatewayId'])
                auth.record_transfer(key, 'codex', 'local', 'thread', 'question', 123)
                with closing(Auth(auth.config, path)) as reopened:
                    self.assertEqual(reopened.manage({'action': 'usage', 'id': key})['totalBytes'], 123)
                other, _ = auth.new_session()
                self.assertEqual(auth.manage({'action': 'usage', 'id': auth.key(other)})['sessions'], [])
                auth.manage({'action': 'revoke', 'id': key})
                self.assertIsNone(auth.get(token))
                with closing(Auth(auth.config, path)) as reopened:
                    self.assertIsNone(reopened.get(token))
                auth.record_transfer(key, 'codex', 'local', 'thread', '', 9)
                self.assertEqual(auth.usage.summary(key)['totalBytes'], 0)
                token, session = auth.new_session()
                session['expires'] = time.time() - 10
                self.assertIsNone(auth.get(token))

    def test_upgrade_keeps_deployed_cache_gateway_and_owner(self):
        with tempfile.TemporaryDirectory() as tmp, patch('bridge.features.auth.session_transfer.hardware_id',
                                                        return_value='synthetic-machine-id'):
            path = Path(tmp)
            config = {'mode': 'none', 'username': 'fixture'}
            with closing(Auth(config, path)) as before:
                token, _ = before.new_session()
                slot = os.path.normcase(str(path.resolve()))
                gateway_id = hashlib.sha256(('gateway-v1\0synthetic-machine-id\0' + slot).encode()).hexdigest()[:32]
                identity = hashlib.sha256(('cache-owner-v1\0' + gateway_id + '\0fixture').encode()).hexdigest()
            with closing(Auth(config, path)) as upgraded:
                self.assertEqual(upgraded.session_sync, {'version': 1, 'gatewayId': gateway_id, 'identity': identity})
                self.assertIsNotNone(upgraded.get(token))


    def test_offline_desktop_releases_usage_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            config = {'mode': 'none', 'username': 'alpha'}
            with closing(Auth(config, path)) as auth:
                token, _ = auth.new_session()
                key = auth.key(token)
                auth.record_transfer(key, 'codex', 'local', 'thread', '', 15)

            desktop = Desktop(path)
            with patch.object(desktop, 'status', return_value={'running': False}), patch.object(
                    desktop, 'config', return_value={'auth': config}):
                usage = desktop.devices({'action': 'usage', 'id': key})
            self.assertEqual(usage['totalBytes'], 15)
            (path / 'session-transfer.sqlite3').unlink()

    def test_listeners_release_only_their_owned_usage(self):
        address = ('127.0.0.1', 0)
        config = {'auth': {'mode': 'none'}, 'origins': []}
        with GatewayServer(address, SimpleNamespace(), config, '.') as gateway:
            auth = gateway.auth
            token, _ = auth.new_session()
            entry = ('codex', 'local', 'thread', 'title', 7)
            auth.record_transfer(auth.key(token), *entry)
            db = auth.usage.db
            with GatewayServer(address, None, config, '.', shared=gateway) as listener:
                self.assertIs(listener.auth, auth)
            with GatewayServer(address, None, config, '.', transfer_usage=auth.usage) as listener:
                relay_auth = listener.auth
                relay_token, _ = relay_auth.new_session()
                relay_auth.record_transfer(relay_auth.key(relay_token), *entry)
                self.assertEqual(relay_auth.transfer_summary(relay_token)['totalBytes'], 7)
            relay_auth.record_transfer(relay_auth.key(relay_token), *entry)
            self.assertEqual(auth.usage.summary(relay_auth.key(relay_token))['totalBytes'], 7)
            self.assertIs(auth.usage.db, db)
            self.assertEqual(auth.transfer_summary(token)['totalBytes'], 7)
        gateway.server_close()
        auth.record_transfer(auth.key(token), *entry)
        auth.usage.record(auth.key(token), *entry)
        self.assertIsNone(auth.usage.db)
        with self.assertRaises(sqlite3.ProgrammingError):
            db.execute('SELECT 1')


class TransferHTTP(unittest.TestCase):
    setUpClass = classmethod(test_bridge.HttpTests.setUpClass.__func__)
    setUp = test_bridge.HttpTests.setUp
    tearDown = test_bridge.HttpTests.tearDown
    request = test_bridge.HttpTests.request
    login = test_bridge.HttpTests.login

    def test_conditional_sync_counts_only_omitted_utf8_text(self):
        headers = self.login()
        timeline = Timeline()
        prompt = '中文 🚀'
        tool = '步骤 🚀'
        reply = '多字节正文 🚀' * 12000
        timeline.update({'sequence': 0, 'turns': [{'id': 'turn', 'status': 'completed', 'messages': [
            {'id': 'prompt', 'role': 'user', 'kind': 'userMessage', 'text': prompt},
            {'id': 'step', 'role': 'activity', 'kind': 'commandExecution', 'text': tool},
            {'id': 'reply', 'role': 'assistant', 'kind': 'agentMessage', 'text': reply},
        ]}]}, conversation=True)
        base = '/api/sessions/' + test_bridge.THREAD + '/sync?'
        latest = sync.page(timeline, 'recent')
        original = next(row for row in latest['rows'] if row['role'] == 'user')
        answer = next(row for row in latest['rows'] if row['role'] == 'assistant')
        group, = timeline.process_groups
        process = sync.process(timeline, key=group)
        requests = [
            ('mode=recent', 0),
            (f"mode=recent&known={original['key']}:{original['version']}", len(prompt.encode())),
            (f"mode=recent&ifVersion={latest['version']}", sum(len(row['text'].encode()) for row in latest['rows'])),
            (f"mode=recent&indexOnly=1&ifVersion={latest['version']}", 0),
            (f"mode=process&key={group}&ifVersion={process['version']}", len(tool.encode())),
            (f"mode=body&key={answer['key']}&version={answer['version']}&ifVersion={answer['version']}&offset=123",
             len(timeline.detail(timeline.cursor(answer['key']), offset=123, version=answer['version'])['text'].encode())),
        ]
        wire_bytes = 0
        with patch.object(self.server.bridge, 'timeline_read', create=True, side_effect=lambda sid, mode, sync_mode, **opts:
                          sync.read(timeline, sync_mode, **opts)), closing(http.client.HTTPConnection('127.0.0.1', self.port)) as conn:
            for query, omitted in requests:
                conn.request('GET', base + query, headers={**headers, 'Accept-Encoding': 'gzip'})
                response = conn.getresponse()
                body = response.read()
                wire_bytes += len(body)
                self.assertEqual(response.status, 200)
                value = json.loads(gzip.decompress(body) if response.getheader('Content-Encoding') == 'gzip' else body)
                self.assertEqual(value.get('reusedBytes', 0), omitted)
        _, _, total = self.request('GET', '/api/session-transfers', headers=headers)
        self.assertEqual(total['totalBytes'], wire_bytes)
        self.assertEqual(total['totalReusedBytes'], sum(count for _, count in requests))
        self.assertEqual(total['sessions'][0]['reusedBytes'], total['totalReusedBytes'])

    def test_preview_bytes_are_counted_once_and_not_modified_sends_no_body(self):
        import hashlib
        from unittest.mock import patch
        sid = '00000000-0000-4000-8000-000000000077'
        data = b'fixture image body'
        image_id = hashlib.sha256(data).hexdigest()
        artifact = {'previewBytes': data, 'previewMime': 'image/png', 'previewSha256': image_id, 'name': 'step.png'}
        path = f'/api/sessions/{sid}/desktop-images/{image_id}'
        headers = self.login()
        with patch.object(self.server.bridge, 'desktop_image_preview', return_value=(artifact, 'tool'), create=True):
            connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
            try:
                connection.request('GET', path, headers=headers)
                response = connection.getresponse()
                self.assertEqual((response.status, response.read()), (200, data))
                connection.request('GET', path, headers={**headers, 'If-None-Match': response.getheader('ETag')})
                response = connection.getresponse()
                self.assertEqual((response.status, response.read()), (304, b''))
            finally:
                connection.close()
        self.assertEqual(self.request('GET', '/api/session-transfers', headers=headers)[2]['totalBytes'], len(data))

    def test_wire_bytes_are_per_login_and_usage_reads_are_free(self):
        headers = self.login()
        status, _, auth = self.request('GET', '/api/auth', headers=headers)
        self.assertEqual((status, auth['sessionSync']['version']), (200, 1))
        sid = '00000000-0000-4000-8000-000000000077'
        payload = {'rows': [{'text': 'a'*5000}], 'meta': {'title': 'Test session', 'access': {'source': 'desktop'},
                                                           'hostLabel': '此电脑'}}
        from unittest.mock import patch
        with patch.object(self.server.bridge, 'timeline_read', return_value=payload, create=True):
            connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
            try:
                connection.request('GET', '/api/sessions/'+sid+'/sync?mode=recent', headers={**headers,'Accept-Encoding':'gzip'})
                response = connection.getresponse()
                data = response.read()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.getheader('Content-Encoding'), 'gzip')
                self.assertEqual(json.loads(gzip.decompress(data)), payload)
            finally:
                connection.close()
        for _ in range(2):
            status, _, stats = self.request('GET', '/api/session-transfers', headers=headers)
            self.assertEqual(status, 200)
            self.assertEqual(stats['totalBytes'], len(data))
            self.assertEqual(stats['sessions'][0]['sessionId'], sid)
            self.assertEqual((stats['sessions'][0]['method'], stats['sessions'][0]['hostLabel']), ('app', '此电脑'))
        self.assertEqual(self.request('GET', '/api/session-transfers', headers=self.login())[2]['sessions'], [])
        self.assertEqual(self.request('GET', '/api/session-transfers')[0], 401)
