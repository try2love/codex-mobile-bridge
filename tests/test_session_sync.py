import json
import hashlib
import copy
import threading
import time
from collections import OrderedDict
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import test_bridge
from bridge.clients.manager import DesktopSessions
from bridge.features.sessions import sync
from bridge.features.sessions.timeline import Timeline
from relay.protocol import validate


def example_turns():
    return [{
        'id': f'turn-{i}', 'status': 'completed', 'messages': [
            {'id': f'question-{i}', 'role': 'user', 'kind': 'userMessage', 'text': f'question-{i}'},
            {'id': f'tool-{i}', 'role': 'activity', 'kind': 'commandExecution', 'text': f'PRIVATE_OUTPUT-{i}'},
            {'id': f'answer-{i}', 'role': 'assistant', 'kind': 'agentMessage', 'text': f'answer-{i}'},
        ],
    } for i in range(65)]


def projection(turns, sequence=1, **meta):
    timeline = Timeline()
    timeline.update({'sequence': sequence, 'turns': turns, **meta}, conversation=True)
    return timeline


class TimelineSyncTests(unittest.TestCase):
    def test_changed_process_cursors_are_recoverable_without_replaying_a_write(self):
        timeline = projection(example_turns()[:1])
        key = next(iter(timeline.process_groups))
        for options in ({'key': 'process-missing'}, {'key': key, 'after': 'removed'},
                        {'key': key, 'before': 'removed'}):
            with self.assertRaises(sync.SyncCursorChanged) as error:
                sync.process(timeline, **options)
            self.assertEqual(error.exception.code, 'sync_cursor_changed')
        self.assertTrue(sync.process(timeline, key=key)['rows'])

    def test_exact_range_does_not_delete_cached_children_hidden_by_process_groups(self):
        timeline = projection(example_turns()[:2], syncSourceComplete=True)
        recent = sync.read(timeline, 'recent')
        group = next(iter(timeline.process_groups))
        process = sync.process(timeline, key=group)
        child = process['index'][0]
        ranged = sync.read(timeline, 'range', start=recent['range']['start'], end=recent['range']['end'],
                           known={child['key']: child['version']})
        self.assertTrue(ranged['range']['complete'])
        self.assertNotIn(child['key'], {row['key'] for row in ranged['index']})
        self.assertEqual(ranged['deleted'], [])

    def test_failed_turn_exposes_its_recorded_reason_on_cached_reopen(self):
        turn = {'id': 'failed', 'status': 'failed', 'error': {'message': 'Tool permission denied'},
                'messages': [{'id': 'tool', 'role': 'activity', 'kind': 'commandExecution', 'text': 'private output'}]}
        timeline = projection([turn])
        first = sync.read(timeline, 'recent')
        warm = sync.read(timeline, 'recent', ifVersion=first['version'])
        self.assertFalse(warm['rows'])
        self.assertEqual((warm['turns'][0]['status'], warm['turns'][0]['error']), ('failed', 'Tool permission denied'))

    def test_reply_metadata_refreshes_page_and_watch_without_resending_body(self):
        messages = [
            {'id': 'first', 'turnId': 'one', 'role': 'assistant', 'text': 'first body'},
            {'id': 'note', 'turnId': 'one', 'role': 'assistant', 'phase': 'commentary', 'text': 'note body'},
            {'id': 'next', 'turnId': 'two', 'role': 'assistant', 'text': 'another body'},
        ]
        session = {'status': 'idle', 'model': 'current-setting', 'effort': 'high'}
        old = projection(DesktopSessions._history_turns(messages, session))
        old_page = sync.page(old, 'recent')
        old_group, = old.process_groups
        old_process = sync.process(old, key=old_group)
        messages[0].update(model='recorded-first', effort='max')
        messages[1].update(model='recorded-note', effort='low', timestamp=1700000000000)
        messages[2].update(model='recorded-next', effort='off')
        current = projection(DesktopSessions._history_turns(messages, session))
        known = {row['key']: row['version'] for row in old_page['index']}
        page = sync.page(current, 'recent', known=known, ifVersion=old_page['version'])
        self.assertTrue(all(row.get('kind') == 'processPage' for row in page['rows']))
        self.assertFalse(page['notModified'])
        self.assertEqual([(row['model'], row['effort']) for row in page['index'] if row['role'] == 'assistant'],
                         [('recorded-first', 'max'), ('recorded-next', None)])
        known_process = {row['key']: row['version'] for row in old_process['index']}
        process = sync.process(current, key=old_group, known=known_process)
        self.assertEqual(process['rows'], [])
        self.assertEqual([(row['model'], row['effort']) for row in process['index']], [('recorded-note', 'low')])
        self.assertEqual(process['index'][0]['timestamp'], 1700000000000)
        self.assertNotEqual(current.conversation_rows[1]['version'], old.conversation_rows[1]['version'])
        self.assertNotEqual(sync.watch(current)['version'], sync.watch(old)['version'])
        self.assertEqual(sync.body(current, key=page['index'][0]['key'], ifVersion=page['index'][0]['version'])['notModified'], True)
        messages[1]['timestamp'] += 1000
        clock_update = projection(DesktopSessions._history_turns(messages, session))
        self.assertNotEqual(sync.watch(clock_update)['version'], sync.watch(current)['version'])
        self.assertNotEqual(clock_update.conversation_rows[1]['version'], current.conversation_rows[1]['version'])
        updated_process = sync.process(clock_update, key=old_group, known={row['key']: row['version'] for row in process['index']})
        self.assertEqual(updated_process['rows'], [])
        self.assertEqual(updated_process['index'][0]['timestamp'], 1700000001000)

    def test_recent_counts_primary_and_adds_initial_input_through_long_process(self):
        turn = {'id': 'long', 'status': 'inProgress', 'messages': [
            {'id': 'initial', 'kind': 'userMessage', 'role': 'user', 'text': 'initial'},
            *({'id': f'tool-{i}', 'kind': 'commandExecution', 'role': 'activity', 'text': 'private'} for i in range(1005)),
            *({'id': f'supplement-{i}', 'kind': 'steeringUserMessage', 'role': 'user', 'text': f'supplement-{i}'} for i in range(23)),
            {'id': 'final', 'kind': 'agentMessage', 'role': 'assistant', 'text': 'final'},
        ]}
        timeline = projection([turn])
        response = sync.read(timeline, 'recent')
        texts = [row['text'] for row in response['rows'] if sync.primary(row)]
        self.assertEqual(texts, ['initial', *(f'supplement-{i}' for i in range(4, 23)), 'final'])
        self.assertEqual(len(response['turns'][0]['processes']), 1)
        group = response['turns'][0]['processes'][0]['key']
        self.assertNotIn('private', json.dumps(response))
        latest = sync.read(timeline, 'process', key=group)
        self.assertTrue(latest['hasEarlier'])
        self.assertFalse(latest['hasMore'])
        index_only = sync.read(timeline, 'process', key=group, indexOnly=True)
        self.assertEqual(index_only['index'], latest['index'])
        self.assertEqual(index_only['previous'], latest['previous'])
        self.assertEqual(index_only['rows'], [])
        self.assertEqual(latest['rows'][0]['key'], timeline.process_groups[group][-20]['key'])
        previous = sync.read(timeline, 'process', key=group, before=latest['previous'])
        self.assertTrue(previous['hasMore'])
        self.assertTrue(set(row['key'] for row in latest['rows']).isdisjoint(row['key'] for row in previous['rows']))
        turn['status'] = 'completed'
        restarted = projection([turn], sequence=57)
        first = sync.read(restarted, 'process', key=group)
        self.assertFalse(first['hasEarlier'])
        self.assertTrue(first['hasMore'])
        self.assertEqual(first['rows'][0]['key'], timeline.process_groups[group][0]['key'])
        forward = sync.read(restarted, 'process', key=group, after=first['next'])
        self.assertEqual(forward['rows'][0]['key'], timeline.process_groups[group][20]['key'])

    def test_exact_range_deletion_and_omission_never_overread(self):
        turns = example_turns()[:4]
        timeline = projection(turns, syncSourceComplete=True)
        recent = sync.read(timeline, 'recent')
        options = {'start': recent['range']['start'], 'end': recent['range']['end'],
                   'known': {item['key']: item['version'] for item in recent['index']}}
        edited = example_turns()[:4]
        edited[1]['messages'][0]['text'] = 'changed input'
        removed_key = next(row['key'] for row in recent['rows'] if row['text'] == 'answer-2')
        edited[2]['messages'].pop()
        refreshed = projection(edited, syncSourceComplete=True)
        ranged = sync.read(refreshed, 'range', **options)
        self.assertTrue(ranged['range']['complete'])
        self.assertEqual(ranged['deleted'], [removed_key])
        self.assertEqual([row['text'] for row in ranged['rows']], ['changed input'])
        self.assertEqual(sync.read(refreshed, 'recent', known=options['known'])['deleted'], [])
        omitted = sync.read(refreshed, 'recent', omitTurn={'turn-1'})
        self.assertTrue(all(row['turnId'] != 'turn-1' for row in omitted['rows']))
        self.assertTrue(all(row['id'] != 'turn-1' for row in omitted['turns']))
        directory = sync.read(refreshed, 'recent', indexOnly=True)
        self.assertEqual(directory['rows'], [])
        self.assertTrue(directory['indexOnly'])
        self.assertIn('turn-1', {row['turnId'] for row in directory['index']})
        missing = projection(edited[1:], syncSourceComplete=False)
        self.assertTrue(sync.read(missing, 'range', **options)['reset'])
        self.assertEqual(sync.read(missing, 'range', **options)['deleted'], [])
        complete = projection(edited[1:], syncSourceComplete=True)
        self.assertIn(options['start'], sync.read(complete, 'range', **options)['deleted'])
        self.assertEqual(sync.read(refreshed, 'watch', ifVersion=omitted['sourceVersion'])['notModified'], True)

    def test_authorized_page_can_reuse_identical_legacy_text_without_reusing_metadata(self):
        timeline = projection(example_turns()[:1])
        fingerprint = hashlib.sha256(b'question-0').hexdigest()[:24]
        _, options = sync.parse_query({'heldText': [fingerprint]})
        page = sync.read(timeline, 'recent', **options)
        row = next(item for item in page['rows'] if item['role'] == 'user')
        self.assertEqual(row['textRef'], fingerprint)
        self.assertNotIn('text', row)
        self.assertEqual(next(item for item in page['rows'] if item['role'] == 'assistant')['text'], 'answer-0')
        with self.assertRaises(ValueError):
            sync.parse_query({'mode': ['range'], 'heldText': [fingerprint]})

    def test_process_read_includes_plain_text_and_a_bounded_log_preview(self):
        timeline = projection([{'id': 'turn', 'status': 'completed', 'messages': [
            {'id': 'plain', 'kind': 'step', 'role': 'activity', 'text': 'short step result'},
            {'id': 'log', 'kind': 'commandExecution', 'role': 'activity', 'text': 'output ' * 1000},
        ]}])
        group = sync.read(timeline, 'recent')['turns'][0]['processes'][0]['key']
        page = sync.read(timeline, 'process', key=group)
        self.assertEqual([(row['order'], row['truncated']) for row in page['rows']], [(0, False), (1, True)])
        self.assertEqual(page['rows'][0]['text'], 'short step result')
        self.assertLessEqual(len(page['rows'][1]['text'].encode()), 180)
        self.assertEqual(sync.read(timeline, 'body', key=page['rows'][1]['key'])['text'], 'output ' * 1000)

    def test_lazy_body_version_survives_restart_and_revalidates_chunks(self):
        messages = [{'id': 'tool', 'kind': 'commandExecution', 'role': 'activity', 'text': '',
                     '_detailSource': [{'type': 'commandExecution', 'id': 'tool', 'command': 'echo',
                                        'aggregatedOutput': 'q' * 50500}]}]
        turns = [{'id': 'run', 'status': 'completed', 'messages': messages}]
        left, right = projection(turns, 1), projection(turns, 87)
        key = left.rows[0]['key']
        first = sync.read(left, 'body', key=key)
        self.assertEqual(first['offset'], 0)
        self.assertEqual(first['next'], 16000)
        self.assertEqual(sync.read(right, 'body', key=key, ifVersion=first['version']),
                         {'key': key, 'version': first['version'], 'notModified': True, 'files': [],
                          'reusedBytes': len(first['text'].encode('utf-8'))})
        second = sync.read(right, 'body', key=key, offset=first['next'], version=first['version'])
        self.assertEqual(second['offset'], 16000)
        messages[0]['_detailSource'][0]['aggregatedOutput'] += 'new'
        edited = sync.read(projection(turns), 'body', key=key, offset=16000, version=first['version'])
        self.assertEqual(edited['offset'], 0)
        self.assertNotEqual(edited['version'], first['version'])


class DesktopProviderSyncTests(unittest.TestCase):
    def manager(self, provider, messages):
        def detail(family, action, sid, options):
            self.assertEqual((family, action, sid), (provider, 'detail', 'chat'))
            limit = options['historyLimit']
            self.assertLessEqual(limit, 100)
            end = int(options.get('historyBefore', len(messages)))
            start = max(0, end - limit)
            return {'connected': True, 'session': {'id': 'chat', 'status': 'idle'}, 'messages': copy.deepcopy(messages[start:end]),
                    'history': {'before': str(start) if start else None, 'hasMore': start > 0}}
        return SimpleNamespace(read_lock=threading.Lock(), binding_epochs={provider: 1}, history_scans=OrderedDict(),
                               call=Mock(side_effect=detail), require_enabled=Mock())

    def resume(self, manager, provider, mode, **options):
        result = DesktopSessions.history_sync(manager, provider, 'chat', mode, options)
        while result.get('scan'):
            before = manager.call.call_count
            result = DesktopSessions.history_sync(manager, provider, 'chat', mode, {**options, 'scan': result['scan']})
            self.assertLessEqual(manager.call.call_count - before, 6)
        return result

    def test_recent_and_older_conditional_transfer_across_provider_instances(self):
        turns = example_turns()
        messages = [dict(row, turnId=turn['id'], turnStatus=turn['status'])
                    for turn in turns for row in turn['messages']]
        for provider in ('claude', 'deepseek', 'zcode', 'antigravity'):
            with self.subTest(provider=provider):
                manager = self.manager(provider, messages)
                read = lambda mode, **options: DesktopSessions.history_sync(manager, provider, 'chat', mode, options)
                recent = read('recent')
                self.assertEqual(sum(sync.primary(row) for row in recent['rows']), 20)
                self.assertTrue(recent['connected'])
                self.assertNotIn('PRIVATE_OUTPUT', json.dumps(recent))
                older = read('older', before=recent['before'], limit=100)
                self.assertEqual(sum(sync.primary(row) for row in older['rows']), 100)
                self.assertLess(max(row['order'] for row in older['rows']), min(row['order'] for row in recent['rows']))
                self.assertIsNotNone(older['before'])
                self.assertTrue(set(row['key'] for row in older['rows']).isdisjoint(row['key'] for row in recent['rows']))
                manager = self.manager(provider, messages)
                repeated = read('older', before=recent['before'], limit=100, ifVersion=older['version'])
                self.assertTrue(repeated['notModified'])
                self.assertEqual(repeated['rows'], [])
                known = {item['key']: item['version'] for item in recent['index']}
                self.assertEqual(read('recent', known=known)['rows'], [])
                self.assertTrue(read('watch', ifVersion=recent['sourceVersion'])['notModified'])
                key = recent['turns'][-1]['processes'][0]['key']
                process = read('process', key=key)
                self.assertEqual(len(process['rows']), 1)
                self.assertTrue(read('body', key=process['rows'][0]['detailCursor'])['text'].startswith('PRIVATE_OUTPUT-'))
                manager.require_enabled.assert_called()

    def test_initial_input_is_recovered_from_paged_native_long_turn(self):
        records = [{'id': 'initial', 'role': 'user', 'text': 'first'},
                   *({'id': f'activity-{i}', 'role': 'activity', 'text': 'operation'} for i in range(1050)),
                   *({'id': f'supplement-{i}', 'role': 'user', 'text': str(i)} for i in range(21)),
                   {'id': 'answer', 'role': 'assistant', 'text': 'last'}]
        messages = [dict(row, turnId='long', turnStatus='completed') for row in records]
        manager = self.manager('deepseek', messages)
        initial = DesktopSessions.history_sync(manager, 'deepseek', 'chat', 'recent', {})
        self.assertTrue(initial['pending'])
        self.assertNotIn('deleted', initial)
        restarted = self.manager('deepseek', messages)
        self.assertEqual(DesktopSessions.history_sync(restarted, 'deepseek', 'chat', 'recent', {'scan': initial['scan']}),
                         {'pending': True, 'scanReset': True})
        result = self.resume(restarted, 'deepseek', 'recent')
        recovered = self.resume(manager, 'deepseek', 'recent', scan=initial['scan'])
        self.assertEqual(recovered['index'], result['index'])
        self.assertEqual(recovered['sourceVersion'], result['sourceVersion'])
        self.assertEqual([row['text'] for row in result['rows'] if sync.primary(row)],
                         ['first', *(str(i) for i in range(2, 21)), 'last'])
        self.assertGreater(manager.call.call_count, 1)
        manager.call.reset_mock()
        watch = DesktopSessions.history_sync(manager, 'deepseek', 'chat', 'watch', {'ifVersion': result['sourceVersion']})
        self.assertTrue(watch['notModified'])
        manager.call.assert_called_once()
        self.assertNotIn('operation', json.dumps(result))

    def test_body_locator_beyond_previous_source_ceiling_is_bounded_and_survives_restart(self):
        messages = [{'id': str(i), 'turnId': f'native-{i}', 'turnStatus': 'completed',
                     'role': 'user' if i % 2 == 0 else 'assistant', 'text': f'entry-{i}'} for i in range(3350)]
        source = projection(DesktopSessions._history_turns(messages, {'status': 'idle'}))
        key, version = source.rows[0]['key'], source.rows[0]['version']
        manager = self.manager('claude', messages)
        result = self.resume(manager, 'claude', 'body', key=key)
        self.assertGreater(manager.call.call_count, 32)
        self.assertEqual((result['key'], result['version'], result['text']), (key, version, 'entry-0'))
        restarted = self.manager('claude', messages)
        repeated = self.resume(restarted, 'claude', 'body', key=key, ifVersion=version)
        self.assertTrue(repeated['notModified'])
        self.assertNotIn('text', repeated)

    def test_scan_token_scope_and_source_drift_never_produce_deletion(self):
        messages = [{'id': str(i), 'turnId': f'turn-{i}', 'role': 'user', 'text': str(i)} for i in range(650)]
        manager = self.manager('claude', messages)
        options = {'key': projection(DesktopSessions._history_turns(messages, {})).rows[0]['key']}
        initial = DesktopSessions.history_sync(manager, 'claude', 'chat', 'body', options)
        self.assertTrue(initial['pending'])
        wrong = DesktopSessions.history_sync(manager, 'claude', 'other', 'body', {**options, 'scan': initial['scan']})
        self.assertEqual(wrong, {'pending': True, 'scanReset': True})
        messages[-1]['text'] = 'native change during scan'
        changed = DesktopSessions.history_sync(manager, 'claude', 'chat', 'body', {**options, 'scan': initial['scan']})
        self.assertEqual(changed, {'pending': True, 'scanReset': True})
        self.assertFalse(manager.history_scans)

    def test_no_older_claim_when_native_window_cannot_supply_one(self):
        turns = example_turns()[:3]
        messages = [dict(row, turnId=turn['id'], turnStatus=turn['status'])
                    for turn in turns for row in turn['messages']]
        manager = self.manager('zcode', messages)
        manager.call.side_effect = None
        manager.call.return_value = {'messages': messages, 'history': {'hasMore': False, 'complete': False}}
        response = DesktopSessions.history_sync(manager, 'zcode', 'chat', 'recent', {})
        self.assertFalse(response['hasMore'])
        self.assertIsNone(response['before'])
        self.assertEqual(response['deleted'], [])


def raw_example():
    return [{'turnId': turn['id'], 'status': 'completed', 'items': [
        {'id': user['id'], 'type': 'userMessage', 'content': [{'type': 'text', 'text': user['text']}]},
        {'id': tool['id'], 'type': 'commandExecution', 'command': 'echo', 'aggregatedOutput': tool['text']},
        {'id': assistant['id'], 'type': 'agentMessage', 'text': assistant['text']},
    ]} for turn in example_turns() for user, tool, assistant in [turn['messages']]]


class WslProviderSyncTests(unittest.TestCase):
    def test_wsl_passive_follow_and_remote_revision_do_not_activate_a_writer(self):
        from bridge.features.endpoints.wsl import Session, WslEntry
        session = Session(test_bridge.THREAD)
        read_store = Mock()
        read_store.history_revision.return_value = [7, 8, 9]
        read_store.history.return_value = {'id': test_bridge.THREAD, 'turns': raw_example()}
        endpoint = SimpleNamespace(store=read_store, session=Mock(return_value=session),
                                   view=lambda sid, **opts: session.view(process_bodies=opts['process_bodies']),
                                   _refresh=Mock())
        endpoint._sync_saved_history = lambda current, **opts: WslEntry._sync_saved_history(endpoint, current, **opts)
        read = lambda mode, **opts: WslEntry.timeline_read(endpoint, test_bridge.THREAD, 'sync', sync_mode=mode, **opts)
        recent = read('recent')
        older = read('older', before=recent['before'], limit=100)
        self.assertEqual(sum(sync.primary(row) for row in recent['rows']), 20)
        self.assertEqual(sum(sync.primary(row) for row in older['rows']), 100)
        self.assertTrue(read('older', before=recent['before'], limit=100, ifVersion=older['version'])['notModified'])
        self.assertTrue(read('watch', ifVersion=recent['sourceVersion'])['notModified'])
        self.assertEqual(read_store.history.call_count, 1)
        endpoint._refresh.assert_not_called()
        endpoint.session.assert_called_with(test_bridge.THREAD, attach=False)

    def test_cached_wsl_detail_after_gateway_restart_reaches_older_than_eight_windows(self):
        from bridge.features.endpoints.wsl import Session, WslEntry
        session = Session(test_bridge.THREAD)
        turns = [{'turnId': f't-{i}', 'status': 'completed', 'items': [
            {'id': f'a-{i}', 'type': 'agentMessage', 'text': f'answer {i}'}]} for i in range(901)]
        store = Mock()
        store.history_revision.return_value = [1, 2, 3]
        store.history.side_effect = lambda sid, turn_limit: {
            'id': sid, 'turns': turns[-turn_limit:],
            'turnsPagination': {'hasLoadedOldest': turn_limit >= len(turns)}}
        endpoint = SimpleNamespace(store=store, session=Mock(return_value=session),
                                   view=lambda sid, **opts: session.view(process_bodies=opts['process_bodies']),
                                   _refresh=Mock())
        endpoint._sync_saved_history = lambda current, **opts: WslEntry._sync_saved_history(endpoint, current, **opts)
        key = projection([{'id': 't-0', 'messages': [
            {'id': 'a-0', 'role': 'assistant', 'kind': 'agentMessage', 'text': 'answer 0'}]}]).rows[0]['key']
        result = WslEntry.timeline_read(endpoint, test_bridge.THREAD, 'sync', sync_mode='body', key=key)
        self.assertEqual(result['text'], 'answer 0')
        self.assertLess(store.history.call_count, 9)
        endpoint._refresh.assert_not_called()


class CodexProviderSyncTests(unittest.TestCase):
    setUp = test_bridge.IntegrationTests.setUp
    tearDown = test_bridge.IntegrationTests.tearDown

    def test_connected_recent_reads_latest_recorded_usage_without_loading_history(self):
        from bridge.app.service import LiveSession

        thread = test_bridge.THREAD
        session = LiveSession(thread)
        session.state = {'id': thread, 'turns': []}
        session.connected = True
        rollout = self.root / 'sessions/fixture.jsonl'

        def record(amount):
            with rollout.open('a') as stream:
                stream.write(json.dumps({'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {
                    'last_token_usage': {'total_tokens': amount}, 'model_context_window': 100}}}) + '\n')

        def read(mode, **options):
            return self.bridge.timeline_read(thread, 'sync', sync_mode=mode, **options)

        with patch.object(self.bridge, 'session', return_value=session), patch.object(self.bridge, '_refresh_async'), \
                patch.object(self.bridge.store, 'history', side_effect=AssertionError('complete live history')):
            record(20)
            recent = read('recent')
            self.assertEqual(recent['meta']['contextUsage'], {'usedTokens': 20, 'contextWindow': 100, 'cached': True})
            self.assertTrue(read('watch', ifVersion=recent['sourceVersion'])['notModified'])
            with patch('bridge.features.sessions.store._rollout_metadata_tail', side_effect=AssertionError('unchanged rollout')):
                self.assertEqual(read('recent')['meta']['contextUsage']['usedTokens'], 20)
            with session.condition:
                session.state.update(latestTokenUsageInfo={'last': {'totalTokens': 20}, 'modelContextWindow': 100},
                                     tokenUsageFromHistory=True)
                session.changed()
            record(35)
            self.assertFalse(read('watch', ifVersion=recent['sourceVersion'])['notModified'])
            self.assertEqual(read('recent')['meta']['contextUsage']['usedTokens'], 35)
            with session.condition:
                session.state['latestTokenUsageInfo'] = {'last': {'totalTokens': 12}, 'modelContextWindow': 100}
                session.state.pop('tokenUsageFromHistory')
                session.changed()
            record(41)
            self.assertEqual(read('recent')['meta']['contextUsage'], {'usedTokens': 12, 'contextWindow': 100, 'cached': False})

    def test_saved_codex_sync_has_large_pages_and_existing_owner_is_passive(self):
        read = lambda mode, **options: self.bridge.timeline_read(test_bridge.THREAD, 'sync', sync_mode=mode, **options)
        with patch.object(self.bridge, '_attach') as attach, patch.object(self.bridge.store, 'history',
                return_value={'id': test_bridge.THREAD, 'cwd': '/workspace', 'turns': raw_example()}):
            recent = read('recent')
            earlier = read('older', before=recent['before'], limit=100)
            self.assertEqual(sum(sync.primary(row) for row in recent['rows']), 20)
            self.assertEqual(sum(sync.primary(row) for row in earlier['rows']), 100)
            self.assertFalse(recent['meta']['connected'])
            self.assertEqual(read('older', before=recent['before'], limit=100, ifVersion=earlier['version'])['rows'], [])
            attach.assert_not_called()

    def test_native_index_reaches_question_beyond_twelve_windows_and_reuses_completed_pages(self):
        import sqlite3
        from contextlib import closing

        thread = test_bridge.THREAD
        total = 1601
        rollout = self.root / 'sessions/fixture.jsonl'
        rollout.write_text(json.dumps({'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {
            'last_token_usage': {'total_tokens': 425}, 'model_context_window': 2000}}}) + '\n')
        with closing(sqlite3.connect(self.root / 'thread_history_1.sqlite')) as db, db:
            db.executescript("""
                CREATE TABLE thread_items(thread_id,turn_id,rollout_ordinal,item_json);
                CREATE TABLE thread_turns(thread_id,turn_id,status,error_json,started_at,first_user_item_id);
                CREATE TABLE thread_history_projection_state(thread_id,next_rollout_byte_offset);
            """)
            db.execute('INSERT INTO thread_history_projection_state VALUES(?,?)', (thread, rollout.stat().st_size))
            db.execute('INSERT INTO thread_turns VALUES(?,?,?,?,?,?)', (thread, 't', 'completed', None, 1, 'q'))
            items = [{'id': 'q', 'type': 'userMessage', 'content': [{'type': 'text', 'text': 'original'}]},
                     *({'id': str(i), 'type': 'commandExecution', 'command': str(i)} for i in range(total)),
                     {'id': 'a', 'type': 'agentMessage', 'text': 'answer'}]
            db.executemany('INSERT INTO thread_items VALUES(?,?,?,?)',
                           ((thread, 't', i, json.dumps(item)) for i, item in enumerate(items)))
        read = lambda mode, **kw: self.bridge.timeline_read(thread, 'sync', sync_mode=mode, **kw)
        recent = read('recent')
        self.assertEqual([r['text'] for r in recent['rows'] if sync.primary(r)], ['original', 'answer'])
        self.assertEqual(recent['meta']['contextUsage'], {'usedTokens': 425, 'contextWindow': 2000, 'cached': True})
        group = recent['turns'][0]['processes'][0]['key']
        orders = []
        page = {}
        with patch.object(self.bridge.store, 'history', side_effect=AssertionError('unnecessary source reread')):
            while not orders or page.get('next'):
                page = read('process', key=group, limit=100, **({'after': page['next']} if orders else {}))
                orders.extend(row['order'] for row in page['rows'])
        self.assertEqual(orders, list(range(total)))

    def test_partial_owner_turn_joins_saved_prefix_without_restoring_an_obsolete_answer(self):
        from bridge.app.service import LiveSession

        thread = test_bridge.THREAD
        session = LiveSession(thread)
        tool = {'id': 'tool', 'type': 'commandExecution', 'command': 'visible boundary'}
        saved = {'id': thread, 'turns': [{'turnId': 't', 'status': 'completed', 'items': [
            {'type': 'userMessage', 'id': 'q', 'content': [{'type': 'text', 'text': 'original'}]},
            tool, {'type': 'agentMessage', 'id': 'old', 'text': 'superseded'}]}]}
        native = {'turnId': 't', 'status': 'inProgress', 'itemsPagination': {'hasLoadedOldest': False},
                  'items': [tool, {'type': 'agentMessage', 'id': 'live', 'text': 'current'}]}
        with session.condition:
            session.state = {'id': thread, 'turns': [native]}
            session.connected = True
            session.changed()
        with patch.object(self.bridge, 'session', return_value=session), \
                patch.object(self.bridge.store, 'history', return_value=saved):
            data = self.bridge.timeline_read(thread, 'sync', sync_mode='recent')
        self.assertEqual([r['text'] for r in data['rows'] if sync.primary(r)], ['original', 'current'])
        self.assertEqual([r['id'] for r in native['items']], ['tool', 'live'])
        self.assertEqual(data['turns'][0]['status'], 'inProgress')

    def test_watch_updates_from_saved_revision_without_resending_upstream_bodies(self):
        read = lambda mode, **options: self.bridge.timeline_read(test_bridge.THREAD, 'sync', sync_mode=mode, **options)
        saved = {'id': test_bridge.THREAD, 'turns': raw_example()}
        with patch.object(self.bridge, '_refresh_async') as attach, patch.object(self.bridge.store, 'history', return_value=saved) as history, \
                patch.object(self.bridge.store, 'history_revision', return_value=[1, 2, 3]) as revision:
            recent = read('recent')
            history.assert_called_once()
            self.assertTrue(read('watch', ifVersion=recent['sourceVersion'])['notModified'])
            revision.return_value = [1, 2, 4]
            self.assertFalse(read('watch', ifVersion=recent['sourceVersion'])['notModified'])
            history.assert_called_once()
            read('recent')
            self.assertEqual(history.call_count, 2)
            self.assertEqual(attach.call_count, 2)


class HttpSyncAuthorizationTests(unittest.TestCase):
    setUpClass = classmethod(test_bridge.HttpTests.setUpClass.__func__)
    setUp = test_bridge.HttpTests.setUp
    tearDown = test_bridge.HttpTests.tearDown
    request = test_bridge.HttpTests.request
    login = test_bridge.HttpTests.login

    def test_sync_requires_login_before_disclosing_a_version(self):
        path = f'/api/sessions/{test_bridge.THREAD}/sync?mode=watch&ifVersion={"0" * 24}'
        target = self.server.bridge.timeline_read = Mock(return_value={'version': 'secret'})
        self.assertEqual(self.request('GET', path)[0], 401)
        target.assert_not_called()
        response = self.request('GET', path, headers=self.login())
        self.assertEqual(response[0], 200)
        self.assertEqual(response[2], {'version': 'secret', 'waitSupported': True})
        target.assert_called_once_with(test_bridge.THREAD, 'sync', sync_mode='watch', ifVersion='0' * 24)

    def test_watch_wakes_on_source_change_and_does_not_reuse_revoked_auth(self):
        seen = threading.Event()
        condition = threading.Condition()
        marker = [0]

        def wait(previous, delay):
            with condition:
                condition.wait_for(lambda: marker[0] != previous, delay)

        def version(*args, **kwargs):
            seen.set()
            return {'version': str(marker[0]) * 24, 'notModified': marker[0] == 0}

        self.server.bridge.timeline_read = Mock(side_effect=version)
        self.server.bridge.sync_watch_source = Mock(return_value=(lambda: marker[0], wait, 2))
        path = f'/api/sessions/{test_bridge.THREAD}/sync?mode=watch&ifVersion={"0" * 24}&waitMs=4000'
        for revoked in (False, True):
            with self.subTest(revoked=revoked):
                marker[0] = 0
                seen.clear()
                headers = self.login()
                response = []
                poller = threading.Thread(target=lambda: response.append(self.request('GET', path, headers=headers)))
                poller.start()
                self.assertTrue(seen.wait(2))
                if revoked:
                    self.server.auth.logout(headers['Cookie'].split('=', 1)[1])
                start = time.monotonic()
                with condition:
                    marker[0] = 1
                    condition.notify_all()
                poller.join(2)
                self.assertFalse(poller.is_alive())
                self.assertLess(time.monotonic() - start, 1)
                self.assertEqual(response[0][0], 401 if revoked else 200)
                if revoked:
                    self.assertNotIn('version', response[0][2])
                else:
                    self.assertEqual(response[0][2], {'version': '1' * 24, 'notModified': False, 'waitSupported': True})

    def test_desktop_enabled_guard_precedes_version_only_reads(self):
        path = '/api/desktop-sessions/claude/sync?sessionId=chat&mode=watch'
        manager = self.server.desktop_sessions = Mock()
        manager.require_enabled.side_effect = ValueError('disabled')
        self.assertEqual(self.request('GET', path)[0], 401)
        manager.require_enabled.assert_not_called()
        response = self.request('GET', path, headers=self.login())
        self.assertEqual(response[0], 400)
        manager.require_enabled.assert_called_once_with('claude')
        manager.history_sync.assert_not_called()


class SyncAuthorizationTests(unittest.TestCase):
    def test_relay_only_allows_read_operations_for_supported_routes(self):
        urls = [f'/api/sessions/{test_bridge.THREAD}/sync',
                *(f'/api/desktop-sessions/{provider}/sync?sessionId=chat' for provider in ('claude', 'deepseek', 'zcode', 'antigravity'))]
        for url in urls:
            validate('GET', url)
            with self.assertRaises(PermissionError):
                validate('POST', url)
        with self.assertRaises(PermissionError):
            validate('GET', '/api/desktop-sessions/unknown/sync')

    def test_unchanged_watch_holds_a_bound_without_rebuilding_projection(self):
        marker = [1]
        initial = Mock(return_value={'version': '0' * 24, 'notModified': True})
        start = time.monotonic()
        value = sync.wait_for_watch(initial, 80, (lambda: marker[0], lambda old, seconds: time.sleep(seconds), .01))
        self.assertGreaterEqual(time.monotonic() - start, .075)
        self.assertLess(time.monotonic() - start, 1)
        self.assertEqual(initial.call_count, 1)
        self.assertEqual(value, {'version': '0' * 24, 'notModified': True, 'waitSupported': True})

    def test_watch_wait_parameter_is_bounded_and_watch_only(self):
        self.assertEqual(sync.parse_query({'mode': ['watch'], 'waitMs': ['8000']}),
                         ('watch', {'waitMs': 8000}))
        for query in ({'mode': ['watch'], 'waitMs': ['8001']}, {'mode': ['recent'], 'waitMs': ['1']},
                      {'mode': ['watch'], 'waitMs': ['-1']}):
            with self.subTest(query=query), self.assertRaises(ValueError):
                sync.parse_query(query)

    def test_parse_page_limits_and_reject_excessive_conditional_indexes(self):
        key, version = '0' * 24 + '-0', '1' * 24
        self.assertEqual(sync.parse_query({'known': [key + ':' + version], 'omitTurn': ['turna']}),
                         ('recent', {'known': {key: version}, 'omitTurn': frozenset({'turna'})}))
        for query in ({'limit': ['101']}, {'known': [key + ':' + version] * 101}, {'mode': ['process']},
                      {'omitTurn': ['turn'] * 101}, {'mode': ['recent', 'watch']}):
            with self.subTest(query=query), self.assertRaises(ValueError):
                sync.parse_query(query)


if __name__ == '__main__':
    unittest.main()
