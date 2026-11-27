"""History transfer uses synthetic saved records and passive native readers."""
import base64
import io
import hashlib
import http.client
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import test_bridge
from bridge.features.sessions.history_transfer import CHUNK_BYTES, HistorySources, HistoryTransfer, _rollout_record_reader
from bridge.features.sessions.store import SessionStore
from bridge.features.workspace.uploads import Uploads
from relay.protocol import download_headers, validate

ROOT = Path(__file__).resolve().parents[1]
TEMP = ROOT / '.tmp/history-transfer-8788-20261123/gateway'
TEMP.mkdir(parents=True, exist_ok=True)
THREAD = test_bridge.THREAD
STAMP = '2026-11-22T18:00:00.000Z'


class RolloutRecordReaderTests(unittest.TestCase):
    def test_fast_reader_preserves_private_history_leaf_and_public_metadata_identity(self):
        record = {'type': 'response_item', 'payload': {'type': 'message', 'id': 'i' * 5000,
                  'content': [{'text': 'x' * 10000}]}}
        data = json.dumps(record).encode() + b'\n'
        value = _rollout_record_reader()(io.BytesIO(data), len(data))
        text = value['payload']['content'][0]['text']
        self.assertTrue(hasattr(text, 'chars'))
        self.assertEqual(len(str(text)), 1024)
        self.assertEqual(text.slice(0, text.chars), 'x' * 10000)
        self.assertEqual(value['payload']['id'], record['payload']['id'])

    def test_bounded_fast_records_and_streaming_large_records_keep_exact_values(self):
        first = {'type': 'response_item', 'payload': {'type': 'message', 'content': [{'text': 'small 😀'}]}}
        large = {'type': 'response_item', 'payload': {'type': 'message', 'content': [{'text': 'x' * 200000}]}}
        data = b'\n'.join(json.dumps(value).encode() for value in (first, large)) + b'\n'
        source = io.BytesIO(data)
        read = _rollout_record_reader()
        self.assertEqual(read(source, len(data)), first)
        second = read(source, len(data))
        text = second['payload']['content'][0]['text']
        self.assertEqual(text.slice(0, text.chars) if hasattr(text, 'chars') else text, large['payload']['content'][0]['text'])
        self.assertEqual(source.tell(), len(data))


class HistoryTransferTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=TEMP)
        self.addCleanup(temporary.cleanup)
        home = Path(temporary.name) / 'codex'
        home.mkdir()
        self.data = Path(temporary.name) / 'gateway'
        self.rollout = home / 'sessions/saved.jsonl'
        self.rollout.parent.mkdir()
        self.rollout.touch()
        with closing(sqlite3.connect(home / 'state_5.sqlite')) as db, db:
            db.execute('CREATE TABLE threads(id TEXT PRIMARY KEY, title TEXT, cwd TEXT, updated_at INT, '
                       'archived INT, originator TEXT, source TEXT, rollout_path TEXT)')
            db.execute('INSERT INTO threads VALUES(?,?,?,?,?,?,?,?)', (THREAD, 'Synthetic conversation', str(home),
                       1795370400, 0, 'Codex Desktop', 'vscode', str(self.rollout)))
        self.store = SessionStore(home)
        self.uploads = Uploads(self.data)
        self.host = Mock(store=self.store, uploads=self.uploads, entries=None)
        self.host.hosts.hosts.return_value = {}
        self.host.for_host.return_value = self.host
        self.source = HistorySources(self.host, None)
        self.transfer = HistoryTransfer(self.data, self.source)

    def add_turn(self, suffix, *, answer='Answer', attachments=(), complete=True):
        turn = 'turn-' + suffix
        rows = [
            ('event_msg', {'type': 'task_started', 'turn_id': turn}),
            ('turn_context', {'turn_id': turn, 'model': 'fixture-model', 'effort': 'high'}),
            ('response_item', {'type': 'message', 'id': 'u-' + suffix, 'role': 'user', 'content': [
                {'type': 'input_text', 'text': 'Prompt https://example.org/resource [ref](README.md)'}, *attachments]}),
            ('response_item', {'type': 'reasoning', 'id': 'r-' + suffix, 'summary': [
                {'type': 'summary_text', 'text': 'Saved reasoning summary'}]}),
            ('response_item', {'type': 'message', 'id': 'a-' + suffix, 'role': 'assistant', 'content': [
                {'type': 'output_text', 'text': answer}]}),
        ]
        if complete:
            rows.append(('event_msg', {'type': 'task_complete', 'turn_id': turn}))
        with self.rollout.open('a', encoding='utf-8') as f:
            for kind, payload in rows:
                f.write(json.dumps({'type': kind, 'timestamp': STAMP, 'payload': payload}, ensure_ascii=False) + '\n')

    def begin(self, owner='phone'):
        sessions = self.transfer.catalog(0)['sessions']
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]['sourceBytes'], self.rollout.stat().st_size)
        receipt = self.transfer.start(owner, {'ids': [sessions[0]['id']]})
        self.assertEqual(receipt['status'], 'preparing')
        return receipt['id']

    def ready(self, transfer, owner='phone', *, expected='ready'):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            value = self.transfer.manifest(owner, transfer, '')
            if value['status'] != 'preparing':
                self.assertEqual(value['status'], expected, value)
                return value
            time.sleep(.01)
        self.fail('History transfer preparation did not finish')

    def fetch(self, manifest, item):
        raw = bytearray()
        for chunk in item['chunks']:
            _, checksum, body = self.transfer.content('phone', manifest, item['id'], chunk['offset'], chunk['bytes'])
            self.assertEqual(checksum, hashlib.sha256(body).hexdigest())
            self.assertEqual(chunk['sha256'], checksum)
            raw.extend(body)
        self.assertEqual(item['bytes'], len(raw))
        self.assertEqual(item['version'], hashlib.sha256(raw).hexdigest())
        return bytes(raw)

    def test_fixed_manifest_and_exact_versions_resume_across_restart(self):
        self.add_turn('old', answer='完整正文🚀' * 80000)
        first = self.begin()
        self.add_turn('new')
        snapshot = self.ready(first)
        self.assertEqual({row['turnId'] for row in snapshot['items']}, {'turn-old'})
        self.assertTrue(all(row['questionTime'] == 1795370400000 for row in snapshot['items']))
        reply = next(row for row in snapshot['items'] if row['textLength'] > 400000)
        self.assertGreater(len(reply['chunks']), 1)
        value = json.loads(self.fetch(first, reply))['messages'][0]
        self.assertEqual(value['row']['model'], 'fixture-model')
        self.assertEqual(value['row']['effort'], 'high')
        self.assertEqual(''.join(part['text'] for part in value['bodyParts'].values()), '完整正文🚀' * 80000)
        reasoning = [json.loads(self.fetch(first, row))['messages'][0] for row in snapshot['items'] if row['kind'] == 'record']
        step = next(item for item in reasoning if item['row'].get('kind') == 'reasoning')
        summary = next(item for item in reasoning if item['row'].get('kind') == 'processPage')
        self.assertTrue(step['row']['hasDetail'])
        self.assertLess(summary['order'], step['order'])
        self.host.session.assert_not_called()
        self.host._attach.assert_not_called()

        self.transfer = HistoryTransfer(self.data, self.source)
        self.assertEqual(self.ready(first), snapshot)
        second = self.begin()
        updated = self.ready(second)
        versions = {row['key']: row['version'] for row in updated['items']}
        self.assertTrue(all(row['version'] == versions[row['key']] for row in snapshot['items']))
        self.assertEqual({row['turnId'] for row in updated['items']}, {'turn-old', 'turn-new'})
        with self.assertRaises(KeyError):
            self.transfer.manifest('different-phone', second, '')

    def test_original_file_ranges_and_plain_references(self):
        identifier = '410c54ba-c48a-474b-9175-30c34104720e'
        file = (b'original file bytes\0' * 30000) + b'end'
        self.uploads.put(THREAD, identifier, 'fixture.bin', file)
        path = self.uploads.get(THREAD, identifier)['localPath']
        image = b'\x89PNG\r\n\x1a\n' + bytes(range(256)) * 16
        self.add_turn('attachments', attachments=[
            {'type': 'file', 'path': path, 'name': 'fixture.bin'},
            {'type': 'input_image', 'image_url': 'data:image/png;base64,' + base64.b64encode(image).decode()},
            {'type': 'image', 'thumbnail': 'data:image/png;base64,' + base64.b64encode(image).decode()},
            {'type': 'file', 'path': str(self.rollout), 'name': 'reference only'},
            {'type': 'input_image', 'image_url': 'https://example.org/not-fetched.png'},
        ])
        first = self.begin()
        value = self.ready(first)
        attachments = [item for item in value['items'] if item['kind'] == 'attachment']
        binary = next(row for row in attachments if row['name'] == 'fixture.bin')
        self.assertEqual(binary['attachmentKey'], identifier)
        self.assertNotIn('path', binary)
        self.assertEqual(self.fetch(first, binary), file)
        self.assertEqual({row['variant'] for row in attachments if row['mime'] == 'image/png'}, {'original', 'thumb'})
        self.assertTrue(all(self.fetch(first, row) == image for row in attachments if row['mime'] == 'image/png'))
        self.assertFalse(any(row['name'] == 'reference only' for row in attachments))
        self.assertEqual(self.transfer.chunk('phone', first, binary['id'], 1), binary['chunks'][1])
        with self.assertRaises(ValueError):
            self.transfer.content('phone', first, binary['id'], 1, CHUNK_BYTES)
        with self.assertRaises(ValueError):
            self.transfer.content('phone', first, binary['id'], CHUNK_BYTES, CHUNK_BYTES + 1)
        with self.assertRaises(KeyError):
            self.transfer.content('different-phone', first, binary['id'], 0, CHUNK_BYTES)

    def test_catalog_keeps_saved_session_without_a_readable_rollout(self):
        self.rollout.unlink()
        page = self.transfer.catalog(0)
        self.assertEqual(page['sessionCount'], 1)
        self.assertIsNone(page['sessions'][0]['sourceBytes'])

    def test_inventory_includes_other_agents_and_reports_failed_sources(self):
        desktop = Mock()
        desktop.enabled.side_effect = lambda entry: entry in ('codex', 'claude', 'deepseek', 'zcode')
        desktop.navigation_clients.return_value = ['codex', 'claude', 'deepseek', 'zcode']
        desktop.adapters = {name: SimpleNamespace(directory=self.data / name) for name in ('claude', 'deepseek')}
        def listing(provider, action):
            self.assertEqual(action, 'list')
            if provider == 'zcode':
                raise RuntimeError('Unavailable synthetic source')
            return {'sessions': [{'id': provider + '-saved', 'title': provider + ' saved'}]}
        desktop.call.side_effect = listing
        self.source.desktop = desktop
        page = self.transfer.catalog(0)
        self.assertEqual({row['client'] for row in page['sessions']}, {'codex', 'claude', 'deepseek'})
        self.assertEqual(page['unavailable'], [{'client': 'zcode', 'entry': 'zcode'}])

    def test_current_read_only_owner_delta_joins_saved_prefix_once(self):
        self.add_turn('old')
        self.add_turn('active', answer='Saved preview', complete=False)
        current = {'turnId': 'turn-active', 'status': 'inProgress',
                   'itemsPagination': {'hasLoadedOldest': False}, 'items': [
                       {'id': 'r-active', 'type': 'reasoning', 'summary': ['Saved reasoning summary']},
                       {'id': 'a-active', 'type': 'agentMessage', 'text': 'Owner response before rollout flush'},
                   ]}
        owner = SimpleNamespace(connected=True, state={'id': THREAD, 'turns': [current]}, condition=threading.Condition())
        self.host.lock = threading.RLock()
        self.host.live = {THREAD: owner}
        transfer = self.begin()
        with owner.condition:
            current['items'][1]['text'] = 'Newer live response after preparation'
        items = self.ready(transfer)['items']
        active = [json.loads(self.fetch(transfer, item))['messages'][0]
                  for item in items if item['turnId'] == 'turn-active']
        self.assertEqual(len([item for item in active if item['role'] == 'user']), 1)
        self.assertEqual([item['text'] for item in active if item['role'] == 'assistant'],
                         ['Owner response before rollout flush'])
        self.host.session.assert_not_called()
        self.host._attach.assert_not_called()

    def test_unmatched_partial_owner_keeps_saved_history_with_visible_warning(self):
        self.add_turn('active', answer='Saved preview', complete=False)
        current = {'turnId': 'turn-active', 'status': 'inProgress',
                   'itemsPagination': {'hasLoadedOldest': False}, 'items': [
                       {'id': 'not-on-disk', 'type': 'agentMessage', 'text': 'Unflushed owner tail'}]}
        self.host.lock = threading.RLock()
        self.host.live = {THREAD: SimpleNamespace(connected=True, state={'id': THREAD, 'turns': [current]},
                                                 condition=threading.Condition())}
        manifest = self.begin()
        page = self.ready(manifest)
        self.assertEqual(page['failedSessions'], 0)
        self.assertEqual(len(page['warnings']), 1)
        self.assertIn('未同步内容', page['warnings'][0]['message'])
        bodies = [json.loads(self.fetch(manifest, item)) for item in page['items'] if item['kind'] == 'record']
        text = json.dumps(bodies)
        self.assertIn('Saved preview', text)
        self.assertNotIn('Unflushed owner tail', text)

    def test_opted_in_record_batches_are_bounded_and_preserve_each_message(self):
        self.add_turn('batch')
        def rows(*_args):
            for i in range(130):
                yield {'turnId': 'same-turn', 'key': f'key-{i}', 'version': 'v1', 'role': 'assistant',
                       'text': str(i), 'order': i}, 1000, iter(()), iter(())
        self.source.records = rows
        session = self.transfer.catalog(0)['sessions'][0]
        identifier = self.transfer.start('phone', {'ids': [session['id']], 'batchRecords': True})['id']
        page = self.ready(identifier)
        self.assertEqual([len(item['records']) for item in page['items']], [64, 64, 2])
        messages = []
        for item in page['items']:
            self.assertLess(item['bytes'], 1024 * 1024)
            payload = json.loads(self.fetch(identifier, item))
            self.assertEqual(payload['id'], 'same-turn')
            messages.extend(payload['messages'])
        self.assertEqual([message['text'] for message in messages], [str(i) for i in range(130)])
        self.assertEqual(len({message['key'] for message in messages}), 130)

    def test_batching_keeps_large_record_streaming_between_small_batches(self):
        self.add_turn('large-batch')
        text = 'x' * (CHUNK_BYTES * 2 + 7)
        def rows(*_args):
            for i, body in enumerate(('before', text, 'after')):
                yield {'turnId': 'same-turn', 'key': f'key-{i}', 'version': 'v1', 'role': 'assistant',
                       'text': body, 'order': i}, 1000, iter(()), iter(())
        self.source.records = rows
        session = self.transfer.catalog(0)['sessions'][0]
        identifier = self.transfer.start('phone', {'ids': [session['id']], 'batchRecords': True})['id']
        page = self.ready(identifier)
        self.assertEqual(len(page['items']), 3)
        self.assertNotIn('records', page['items'][1])
        self.assertGreater(page['items'][1]['bytes'], CHUNK_BYTES)
        messages = [json.loads(self.fetch(identifier, item))['messages'][0] for item in page['items']]
        self.assertEqual([message['text'] for message in messages], ['before', text, 'after'])

    def test_manifest_keyset_pages_continuously(self):
        for i in range(17):
            self.add_turn(f'p{i}')
        manifest = self.begin()
        page = self.ready(manifest)
        rows = []
        while True:
            rows.extend(page['items'])
            if not page['nextCursor']:
                break
            page = self.transfer.manifest('phone', manifest, page['nextCursor'])
        self.assertEqual(len(rows), 17 * 4)
        self.assertEqual(len({row['id'] for row in rows}), len(rows))
        self.assertEqual([row['order'] for row in rows], sorted(row['order'] for row in rows))

    def test_failed_native_history_does_not_report_a_complete_manifest(self):
        self.add_turn('unavailable')
        self.source.records = Mock(side_effect=ValueError('fixture does not provide complete history'))
        with self.assertLogs('bridge.features.sessions.history_transfer', level='WARNING'):
            identifier = self.begin()
            result = self.ready(identifier, expected='failed')
        self.assertEqual(result['failedSessions'], 1)
        self.assertEqual(result['error'], result['errors'][0]['message'])
        self.assertEqual(result['items'], [])


class HistoryTransferHTTP(unittest.TestCase):
    setUpClass = classmethod(test_bridge.HttpTests.setUpClass.__func__)
    setUp = test_bridge.HttpTests.setUp
    tearDown = test_bridge.HttpTests.tearDown
    request = test_bridge.HttpTests.request
    login = test_bridge.HttpTests.login

    def test_slow_transfer_preparation_does_not_queue_live_send_or_stop(self):
        temporary = tempfile.TemporaryDirectory(dir=TEMP)
        self.addCleanup(temporary.cleanup)
        entered, release = threading.Event(), threading.Event()
        source = Mock()
        session = {'id': 'a' * 64, 'sessionId': THREAD, 'cacheSessionId': THREAD,
                   'title': 'Synthetic transfer', 'client': 'codex', 'entry': 'local', 'host': 'local'}
        source.inventory.return_value = ([session], [])
        source.pin_local.return_value = 'pinned'
        def records(*_args):
            entered.set()
            release.wait(5)
            return iter(())
        source.records.side_effect = records
        service = self.server.history_transfer = HistoryTransfer(temporary.name, source)
        self.server.bridge.send = Mock(return_value={'status': 'accepted'})
        self.server.bridge.interrupt = Mock(return_value={'status': 'stopped'})
        headers = self.login()
        result = self.request('POST', '/api/history-transfer/manifest', {'ids': [session['id']]}, headers)
        self.assertEqual(result[0], 202)
        self.assertTrue(entered.wait(1))
        try:
            started = time.monotonic()
            self.assertEqual(self.request('POST', '/api/sessions/' + THREAD + '/send',
                                          {'id': THREAD, 'text': 'Synthetic live input'}, headers)[0], 200)
            self.assertEqual(self.request('POST', '/api/sessions/' + THREAD + '/stop', {}, headers)[0], 200)
            self.assertLess(time.monotonic() - started, 1)
            self.assertFalse(release.is_set())
        finally:
            release.set()
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                with service.connect() as db:
                    status = db.execute('SELECT status FROM manifests WHERE id=?', (result[2]['id'],)).fetchone()[0]
                if status != 'preparing':
                    break
                time.sleep(.01)

    def test_auth_csrf_and_chunk_headers(self):
        service = Mock()
        self.server.history_transfer = service
        route = '/api/history-transfer/'
        self.assertEqual(self.request('GET', route + 'catalog')[0], 401)
        service.catalog.assert_not_called()
        headers = self.login()
        self.assertEqual(self.request('POST', route + 'manifest', {'all': True}, {'Cookie': headers['Cookie']})[0], 403)
        service.start.assert_not_called()
        service.start.return_value = {'id': '1' * 32}
        self.assertEqual(self.request('POST', route + 'manifest', {'all': True}, headers)[0], 202)
        service.start.assert_called_once()
        service.content.return_value = ({'name': 'record.json', 'version': 'a' * 64, 'bytes': 2,
                                         'session': {'client': 'codex', 'entry': 'local', 'host': 'local', 'sessionId': THREAD}},
                                        hashlib.sha256(b'{}').hexdigest(), b'{}')
        path = route + 'content?id=' + '1' * 32 + '&item=' + '2' * 64 + '&offset=0&length=2'
        with closing(http.client.HTTPConnection('127.0.0.1', self.port, timeout=2)) as connection:
            connection.request('GET', path, headers=headers)
            response = connection.getresponse()
            self.assertEqual((response.status, response.read()), (206, b'{}'))
            self.assertEqual(response.getheader('Content-Range'), 'bytes 0-1/2')
            self.assertEqual(response.getheader('X-Chunk-SHA256'), hashlib.sha256(b'{}').hexdigest())
        self.assertEqual(self.request('GET', path + '&length=3', headers=headers)[0], 400)

    def test_relay_validates_transfer_routes_and_checksum(self):
        validate('GET', '/api/history-transfer/content?id=fixture')
        validate('POST', '/api/history-transfer/manifest', 2)
        with self.assertRaises(PermissionError):
            validate('POST', '/api/history-transfer/content', 2)
        with self.assertRaises(PermissionError):
            validate('GET', '/api/history-transfer/path')
        self.assertEqual(download_headers({'X-Chunk-SHA256': '1' * 64}, response=True),
                         {'X-Chunk-SHA256': '1' * 64})
        with self.assertRaises(ValueError):
            download_headers({'X-Chunk-SHA256': 'bad'}, response=True)
