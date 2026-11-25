import json
import unittest

from bridge.features.sessions.model import normalize_state
from bridge.features.sessions.timeline import Timeline, PAGE_BYTES, DETAIL_CHARS
from bridge.app.service import LiveSession


def view(count=320, sequence=1):
    return {'sequence': sequence, 'turns': [{'id': 'turn', 'messages': [
        {'id': str(i), 'kind': 'agentMessage', 'role': 'assistant', 'text': 'message ' + str(i)}
        for i in range(count)]}], 'requests': [{'id': 'approval'}]}


class TimelineTests(unittest.TestCase):
    def test_revert_anchor_survives_page_and_changes_with_the_native_message(self):
        timeline = Timeline()
        message = {'id': 'input', 'role': 'user', 'text': 'request',
                   'revertStepIndex': 4, 'revertMessageId': 'a' * 64}
        timeline.update({'sequence': 1, 'turns': [{'id': 'turn', 'messages': [message]}]})
        first = timeline.page()['rows'][0]
        self.assertEqual((first['revertStepIndex'], first['revertMessageId']), (4, 'a' * 64))
        message = {**message, 'text': 'replacement', 'revertMessageId': 'b' * 64}
        timeline.update({'sequence': 2, 'turns': [{'id': 'turn', 'messages': [message]}]})
        second = timeline.page()['rows'][0]
        self.assertEqual(second['revertMessageId'], 'b' * 64)
        self.assertNotEqual(first['version'], second['version'])

    def test_file_activity_preserves_complete_native_paths_and_counts_without_copying_patches(self):
        from bridge.features.sessions.model import normalize_item
        paths = ['/repo/' + 'long-directory/' * 20 + 'file-' + str(i) + '.py' for i in range(65)]
        changes = {path: {'type': 'update', 'unified_diff': '@@ -1 +1,2 @@\n-old\n+new\n+extra'} for path in paths}
        changes[paths[-1]] = {'type': 'add', 'content': '++ heading\n-- text\n'}
        for kind in ('fileChange', 'FileChange'):
            with self.subTest(kind=kind):
                message = normalize_item({'id': 'patch', 'type': kind, 'status': 'completed', 'changes': changes})
                expected = [{'path': path, 'added': 2, 'removed': 0 if path == paths[-1] else 1} for path in paths]
                self.assertEqual(message['activityFiles'], expected)
                self.assertIn('@@ -1 +1,2 @@\n-old\n+new\n+extra', message['text'])
                timeline = Timeline()
                timeline.update({'sequence': 1, 'turns': [{'id': 'turn', 'messages': [message]}]})
                row = timeline.page()['rows'][0]
                self.assertEqual(row['role'], 'activity')
                self.assertEqual(row['activityFiles'], expected)
                self.assertNotIn('diff', json.dumps(row['activityFiles']))
                self.assertNotIn('fileSummary', row)
                self.assertEqual(len(row['text']), 180)
                self.assertTrue(row['truncated'])

    def test_file_activity_does_not_claim_unconfirmed_or_failed_changes(self):
        from bridge.features.sessions.model import normalize_item
        for status in ('inProgress', 'failed', 'declined'):
            with self.subTest(status=status):
                message = normalize_item({'type': 'fileChange', 'status': status,
                                          'changes': [{'path': 'a.py', 'diff': '+proposed'}]})
                self.assertEqual(message['activityFiles'], [])
                self.assertEqual(message['text'], '')

    def test_file_activity_metadata_updates_version_and_incremental_rows(self):
        message = {'id': 'patch', 'kind': 'fileChange', 'role': 'activity', 'text': 'unchanged preview',
                   'activityFiles': [{'path': 'a.py', 'added': 1, 'removed': 0}]}
        source = {'sequence': 1, 'turns': [{'id': 'turn', 'messages': [message]}]}
        timeline = Timeline()
        timeline.update(source)
        before = timeline.page()
        original = before['rows'][0]
        message['activityFiles'] = [{'path': 'renamed.py', 'added': 2, 'removed': 1}]
        source['sequence'] = 2
        timeline.update(source)
        updated = timeline.changes(1, before['epoch'], before['before'])['rows'][0]
        self.assertEqual(updated['key'], original['key'])
        self.assertNotEqual(updated['version'], original['version'])
        self.assertEqual(updated['activityFiles'], message['activityFiles'])
        self.assertEqual(updated['text'], original['text'])

    def test_tool_identity_is_short_metadata_without_repeating_body(self):
        state = {'turns': [{'turnId': 'turn', 'items': [
            {'id': 'computer', 'type': 'mcpToolCall', 'server': 'cua_repl', 'tool': 'js', 'arguments': {'code': 'native body'}},
            {'id': 'long', 'type': 'dynamicToolCall', 'toolName': 'x' * 200}]}]}
        value = normalize_state(state)
        value['sequence'] = 1
        timeline = Timeline()
        timeline.update(value)
        rows = timeline.page()['rows']
        self.assertEqual(rows[0]['toolName'], 'cua_repl.js')
        self.assertEqual(len(rows[1]['toolName']), 160)
        self.assertNotIn('native body', rows[0]['toolName'])

    def test_recent_page_keeps_native_start_clock_when_input_is_not_loaded(self):
        value = view(30)
        value['turns'][0]['startedAt'] = 1700000000000
        timeline = Timeline()
        timeline.update(value)
        page = timeline.page(1)
        self.assertEqual(page['rows'][0].get('turnStartedAt'), 1700000000000)
        del value['turns'][0]['startedAt']
        value['sequence'] += 1
        timeline.update(value)
        self.assertNotIn('turnStartedAt', timeline.page(1)['rows'][0])

    def test_generic_session_fallback_is_not_native_completion_evidence(self):
        value = view(3)
        value['turns'][0].update(status='completed', completionKnown=False)
        timeline = Timeline()
        timeline.update(value)
        self.assertTrue(all(row.get('turnCompletionKnown') is False for row in timeline.page()['rows']))

    def test_native_answers_are_presentational_interactions_not_steered_tasks(self):
        from bridge.features.sessions.model import normalize_item
        reply = '<send_user_message_question_reply>' + json.dumps([{
            'questionItemId': '["request_user_input_async","question",0]',
            'question': '是否继续？', 'answer': '继续'}]) + '</send_user_message_question_reply>'
        for kind, status, expected in [('userMessage', None, True),
                                       ('steeringUserMessage', 'accepted', True),
                                       ('steeringUserMessage', 'pending', False),
                                       ('steeringUserMessage', 'rejected', False)]:
            row = normalize_item({'id': 'answer', 'type': kind, 'status': status,
                                  'input': [{'type': 'text', 'text': reply}],
                                  'content': [{'type': 'text', 'text': reply}]})
            self.assertEqual(row['role'], 'user')
            self.assertEqual(row['kind'], kind)
            self.assertEqual(row.get('interaction') == 'response', expected)
            timeline = Timeline()
            timeline.update({'sequence': 1, 'turns': [{'id': 'turn', 'messages': [row]}]})
            self.assertEqual(timeline.page()['rows'][0].get('interaction') == 'response', expected)
        for text in ['继续，同时增加一个检查', '<send_user_message_question_reply>[{"answer":"继续"}]</send_user_message_question_reply>']:
            row = normalize_item({'id': 'supplement', 'type': 'steeringUserMessage', 'status': 'accepted',
                                  'input': [{'type': 'text', 'text': text}]})
            self.assertNotIn('interaction', row)

    def test_native_completed_turn_file_summary_survives_history_and_updates(self):
        state = {'turns': [{'turnId': 'patch-turn', 'status': 'inProgress', 'items': [
            {'type': 'fileChange', 'id': 'patch', 'status': 'completed', 'changes': {
                '/repo/a.py': {'type': 'update', 'unified_diff': '@@ -1 +1,2 @@\n-old\n+new\n+extra'},
                '/repo/b.py': {'type': 'add', 'content': 'created\n'}}},
            {'type': 'agentMessage', 'id': 'reply', 'text': 'Done'}]}]}
        timeline = Timeline()
        timeline.update({**normalize_state(state), 'sequence': 1})
        before = timeline.page()
        self.assertFalse(any(row['kind'] == 'fileChanges' for row in before['rows']))
        state['turns'][0]['status'] = 'completed'
        timeline.update({**normalize_state(state), 'sequence': 2})
        footer = timeline.changes(1, before['epoch'], before['before'])['rows'][-1]
        self.assertEqual(footer['role'], 'fileChanges')
        self.assertEqual(footer['fileSummary']['count'], 2)
        self.assertEqual(footer['fileSummary']['added'], 3)
        self.assertEqual(footer['fileSummary']['removed'], 1)
        self.assertEqual(footer['text'], '')
        self.assertEqual(timeline.detail(timeline.cursor(footer['key']))['text'], '')
        self.assertFalse(footer['truncated'])
        timeline.update({**normalize_state(state), 'sequence': 2},limit=1)
        self.assertNotEqual(timeline.source_anchor[2] if timeline.source_anchor else None,'fileChanges')
        self.assertEqual(timeline.rows[-1]['fileSummary']['basis'],'recorded')

    def test_native_turn_diff_is_authoritative_over_individual_patch_totals(self):
        state = {'turns': [{'turnId': 'patch-turn', 'status': 'completed', 'diff':
            'diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-before\n+after\n',
            'items': [{'type': 'fileChange', 'changes': [{'path': 'a.py', 'diff': '+intermediate\n+temporary'}]}]}]}
        changes = normalize_state(state)['turns'][0]['fileChanges']
        self.assertEqual([(c['path'], c['added'], c['removed']) for c in changes], [('a.py', 1, 1)])
        self.assertEqual(normalize_state(state)['turns'][0]['fileChangesBasis'],'net')

    def test_native_file_changes_deduplicate_ids_and_parse_paths(self):
        from bridge.features.sessions.model import native_file_changes, native_turn_file_changes
        item = {'id': 'same-event', 'type': 'FileChange', 'changes': {'/repo/a': {'type': 'add', 'content': 'one\n'}}}
        self.assertEqual(native_file_changes([item, item])[0]['added'], 1)
        self.assertEqual(native_file_changes([{'type':'FileChange','changes':{'a':{'type':'add','content':'++ heading\n-- text\n'}}}])[0]['added'],2)
        diff = ('diff --git "a/space name.py" "b/space name.py"\n'
                '--- "a/space name.py"\n+++ "b/space name.py"\n@@ -1 +1 @@\n-old\n+new\n'
                'diff --git a/deleted.py b/deleted.py\n--- a/deleted.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-gone\n'
                'diff --git a/old.py b/new name.py\nsimilarity index 100%\nrename from old.py\nrename to new name.py\n')
        changes = native_turn_file_changes({'diff': diff})
        self.assertEqual([(c['path'],c['added'],c['removed']) for c in changes],
                         [('space name.py',1,1),('deleted.py',0,1),('new name.py',0,0)])

    def test_native_file_footer_ignores_large_patch_bodies_and_versions_only_summary(self):
        class PatchMustNotBeRead(dict):
            def __getitem__(self, key):
                if key == 'diff':
                    raise AssertionError('File card read a patch body')
                return super().__getitem__(key)
        change=PatchMustNotBeRead(path='a.py',added=100000,removed=1,diff='PRIVATE_PATCH\n'*100000)
        source={'sequence':1,'turns':[{'id':'patch-turn','status':'completed','messages':[],
                                    'fileChanges':[change],'fileChangesBasis':'net'}]}
        timeline=Timeline();timeline.update(source)
        footer=timeline.rows[-1]
        self.assertEqual(footer['text'],'')
        self.assertFalse(footer['truncated'])
        self.assertLess(len(json.dumps(timeline.page())),2000)
        self.assertNotIn('PRIVATE_PATCH',json.dumps(timeline.page()))
        detail=timeline.detail(timeline.cursor(footer['key']))
        self.assertEqual(detail['text'],'')
        self.assertIsNone(detail['next'])
        version=footer['version']
        change['diff']='DIFFERENT_PRIVATE_PATCH'
        source['sequence']=2;timeline.update(source)
        self.assertEqual(timeline.rows[-1]['version'],version)
        change['added']=100001;source['sequence']=3;timeline.update(source)
        self.assertNotEqual(timeline.rows[-1]['version'],version)

    def test_file_summary_preserves_all_paths_beyond_fifty_and_generic_detail(self):
        files=[{'path':'/repo/file-'+str(i)+'.py','added':i,'removed':1,'diff':'PRIVATE_PATCH'} for i in range(125)]
        source={'sequence':1,'turns':[{'id':'many','status':'completed','fileChanges':files,'messages':[
            {'id':'activity','kind':'commandExecution','role':'activity','text':'ordinary tool output'*1000},
            {'id':'reply','kind':'agentMessage','role':'assistant','text':'ordinary reply'*1000}]}]}
        timeline=Timeline();timeline.update(source)
        page=timeline.page();footer=page['rows'][-1]
        self.assertEqual(footer['fileSummary']['count'],125)
        self.assertEqual([file['path'] for file in footer['fileSummary']['files']],[file['path'] for file in files])
        self.assertLess(len(json.dumps(page).encode()),PAGE_BYTES)
        self.assertNotIn('PRIVATE_PATCH',json.dumps(page))
        for row in page['rows'][:-1]:
            self.assertTrue(timeline.detail(timeline.cursor(row['key']))['text'].startswith('ordinary'))
        self.assertEqual(timeline.detail(timeline.cursor(footer['key']))['text'],'')

    def test_native_patch_counts_distinguish_headers_from_hunk_content(self):
        from bridge.features.sessions.model import native_patch_counts
        hunk = '@@ -1,2 +1,2 @@\n---legitimate removed text\n-ordinary\n+++legitimate added text\n+ordinary\n'
        self.assertEqual(native_patch_counts(hunk), (2,2))
        self.assertEqual(native_patch_counts('diff --git a/a b/a\n--- a/a\n+++ b/a\n'+hunk), (2,2))
        self.assertEqual(native_patch_counts('+++raw added content\n---raw deleted content\n'), (1,1))

    def test_partial_turn_does_not_scan_omitted_native_patch_bodies(self):
        class UnreadablePatch(str):
            def splitlines(self, *args, **kwargs):
                raise AssertionError('Omitted native patch was scanned')
        state = {'turns':[{'turnId':'large-turn','status':'completed',
            'diff':UnreadablePatch('diff --git a/huge b/huge\n'+'+hidden\n'*100000),
            'items':[{'id':'old-patch','type':'fileChange','changes':[{'path':'huge','diff':UnreadablePatch('+hidden\n'*100000)}]},
                     {'id':'latest','type':'agentMessage','text':'Recent reply'}]}]}
        from unittest.mock import patch
        with patch('bridge.features.sessions.model.native_turn_file_changes', side_effect=AssertionError('Partial turn summary rescanned')):
            recent=normalize_state(state,item_limit=1)
        self.assertEqual(recent['turns'][0]['fileChanges'],[])
        self.assertEqual(recent['turns'][0]['messages'][0]['text'],'Recent reply')
        self.assertTrue(recent['historyWindowMore'])

    def test_native_items_pagination_does_not_publish_partial_fallback_totals(self):
        turn={'turnId':'partial','status':'completed','itemsPagination':{'hasLoadedOldest':False},
              'items':[{'type':'fileChange','changes':[{'path':'tail.py','diff':'+tail'}]}]}
        self.assertEqual(normalize_state({'turns':[turn]})['turns'][0]['fileChanges'],[])
        turn['diff']='diff --git a/net.py b/net.py\n--- a/net.py\n+++ b/net.py\n@@ -1 +1 @@\n-old\n+new'
        self.assertEqual(normalize_state({'turns':[turn]})['turns'][0]['fileChanges'][0]['path'],'net.py')
        turn.pop('diff');turn['_nativeFileChanges']=[{'path':'full.py','added':2,'removed':1,'diff':'full recorded patch'}]
        self.assertEqual(normalize_state({'turns':[turn]})['turns'][0]['fileChanges'][0]['path'],'full.py')

    def test_recent_window_skips_old_bodies_and_expands_without_cursor_reset(self):
        from unittest.mock import patch
        import bridge.features.sessions.timeline as module
        timeline = Timeline()
        source = view(10000)
        source['turns'][0]['messages'][0]['text'] = 'old body' * 100000
        encoded_sizes = []
        original = module.encoded
        def encode(value):
            result = original(value)
            encoded_sizes.append(len(result))
            return result
        with patch.object(module, 'encoded', encode):
            timeline.update(source, limit=40)
        first = timeline.page()
        self.assertEqual(len(timeline.rows), 40)
        self.assertLess(max(encoded_sizes), 1000)
        self.assertTrue(first['hasMore'])
        timeline.update(source, limit=140)
        older = timeline.page(100, first['before'])
        self.assertNotIn('reset', older)
        self.assertEqual(len(older['rows']), 100)
        self.assertEqual(older['rows'][-1]['text'], 'message 9979')
        # New content must not slide the reader's loaded prefix out of memory.
        source['turns'][0]['messages'].append({'id': 'new', 'kind': 'agentMessage', 'role': 'assistant', 'text': 'new'})
        source['sequence'] += 1
        timeline.update(source, limit=140)
        self.assertIsNotNone(timeline.position(older['before']))
        self.assertEqual(timeline.changes(1, first['epoch'], older['before'])['rows'][-1]['text'], 'new')

    def test_recent_window_keeps_duplicate_item_keys_stable(self):
        timeline = Timeline()
        source = view(100)
        for message in source['turns'][0]['messages']:
            message['id'] = None
        timeline.update(source, limit=20)
        first = timeline.page()
        timeline.update(source, limit=60)
        self.assertEqual(timeline.page()['before'], first['before'])
        self.assertIsNotNone(timeline.position(first['before']))

    def test_desktop_hydration_cannot_relabel_completed_history_with_current_model(self):
        session=LiveSession('chat')
        with session.condition:
            session.set_history({'id':'chat','turns':[
                {'turnId':'old','status':'completed','params':{'model':'gpt-6-astra','effort':'max'},'items':[]},
                {'turnId':'unknown','status':'completed','items':[]}]})
            session.state={'id':'chat','turns':[
                {'turnId':key,'status':'completed','params':{'model':'gemini-pro','effort':'high'},'items':[]}
                for key in ('old','unknown','new')]}
            session.connected=True
        turns=session.view()['turns']
        self.assertEqual([(t['model'],t['effort']) for t in turns],
                         [('gpt-6-astra','max'),(None,None),('gemini-pro','high')])
        self.assertEqual(session.state['turns'][0]['params']['model'],'gemini-pro')

    def test_assistant_labels_follow_each_turn_not_latest_chat_settings(self):
        from bridge.features.sessions.model import normalize_state
        state={'latestModel':'newest-model','latestReasoningEffort':'ultra','turns':[
            {'turnId':'old','params':{'model':'gpt-6-astra','effort':'max'},'items':[{'id':'a','type':'agentMessage','text':'old reply'}]},
            {'turnId':'new','params':{'model':'gemini-fixture','effort':'high'},'items':[{'id':'b','type':'agentMessage','text':'new reply'}]},
            {'turnId':'unknown','items':[{'id':'c','type':'agentMessage','text':'legacy reply'}]}]}
        timeline=Timeline();timeline.update({**normalize_state(state),'sequence':1})
        rows=timeline.rows
        self.assertEqual((rows[0]['model'],rows[0]['effort']),('gpt-6-astra','max'))
        self.assertEqual((rows[1]['model'],rows[1]['effort']),('gemini-fixture','high'))
        self.assertNotIn('model',rows[2]);self.assertNotIn('effort',rows[2])

    def test_process_clocks_survive_but_only_model_replies_get_recorded_model(self):
        state = {'latestModel': 'current-setting', 'turns': [{
            'turnId': 'saved', 'status': 'completed',
            'params': {'model': 'recorded-model', 'effort': 'high'},
            'items': [
                {'id': 'u', 'type': 'userMessage', 'content': [{'type': 'text', 'text': 'Question'}], 'timestamp': 1000},
                {'id': 'a', 'type': 'agentMessage', 'text': 'Interim', 'phase': 'commentary', 'timestamp': 2000},
                {'id': 's', 'type': 'steeringUserMessage', 'input': [{'type': 'text', 'text': 'Supplement'}], 'timestamp': 3000},
                {'id': 'r', 'type': 'reasoning', 'summary': [{'text': 'Reasoning'}], 'timestamp': 4000},
                {'id': 'c', 'type': 'commandExecution', 'command': 'fixture', 'timestamp': 5000},
            ]}]}
        timeline = Timeline(); timeline.update({**normalize_state(state), 'sequence': 1})
        rows = timeline.rows[1:]
        self.assertEqual(len(rows), 4)
        self.assertEqual([row.get('timestamp') for row in rows], [2000, 3000, 4000, 5000])
        self.assertEqual((rows[0].get('model'), rows[0].get('effort')), ('recorded-model', 'high'))
        self.assertTrue(all('model' not in row and 'effort' not in row for row in rows if row['role'] == 'user' or row.get('kind') == 'commandExecution'))
        self.assertNotIn('model', timeline.rows[0], 'the opening user prompt keeps its existing presentation')
        for sync in (False, True):
            exposed = [row for key in timeline.process_groups
                       for row in timeline.process(timeline.cursor(key), sync=sync)['rows']]
            self.assertEqual([row.get('timestamp') for row in exposed], [2000, 4000, 5000])
            self.assertEqual((exposed[0].get('model'), exposed[0].get('effort')), ('recorded-model', 'high'))
            self.assertTrue(all('model' not in row and 'effort' not in row for row in exposed if row['role'] == 'user' or row.get('kind') == 'commandExecution'))

    def test_saved_native_file_footer_survives_partial_live_turn_outside_saved_text_window(self):
        # Production API shape: the completed turn's tail is native-loaded while
        # its complete rollout patch evidence predates the saved text window.
        from bridge.features.sessions.store import native_turn_file_changes
        patch_turn={'turnId':'edited-nine','status':'completed','items':[
            {'type':'fileChange','id':'earlier','changes':[{'path':'earlier.py','diff':'+first'}]},
            *[{'type':'commandExecution','id':'cmd-'+str(i),'command':'fixture'} for i in range(100)],
            {'type':'fileChange','id':'tail','changes':[{'path':'tail.py','diff':'+last'}]},
            {'type':'agentMessage','id':'final','text':'Done'}]}
        patch_turn['_nativeFileChanges']=native_turn_file_changes(patch_turn)
        current={'turnId':'current','status':'inProgress','items':[
            {'type':'agentMessage','id':'current-'+str(i),'text':'Current'} for i in range(40)]}
        session=LiveSession('chat')
        with session.condition:
            session.set_history({'id':'chat','turns':[patch_turn,current]})
            self.assertNotIn('edited-nine',[turn['id'] for turn in session.saved_view['turns']])
            native_turn={'turnId':'edited-nine','status':'completed','itemsPagination':{'hasLoadedOldest':False},
                         'items':patch_turn['items'][-2:]}
            session.state={'id':'chat','turns':[native_turn,current],'turnsPagination':{'hasLoadedOldest':False}}
            session.connected=True
            view=session.view(item_limit=60)
        edited=next(turn for turn in view['turns'] if turn['id']=='edited-nine')
        self.assertEqual([file['path'] for file in edited['fileChanges']],['earlier.py','tail.py'])
        timeline=Timeline();timeline.update(view,limit=60)
        footer=next(row for row in timeline.rows if row['kind']=='fileChanges')
        self.assertEqual(footer['fileSummary']['count'],2)
        self.assertEqual([file['path'] for file in footer['fileSummary']['files']],['earlier.py','tail.py'])
        self.assertEqual(timeline.detail(timeline.cursor(footer['key']))['text'],'')
        self.assertNotIn('fileChanges',session.state['turns'][0])
        # An authoritative native net diff retains precedence over saved edits.
        native_turn['diff']='diff --git a/net.py b/net.py\n--- a/net.py\n+++ b/net.py\n@@ -1 +1 @@\n-old\n+new'
        with session.condition:
            updated=session.view(item_limit=60)
        self.assertEqual(updated['turns'][0]['fileChangesBasis'],'net')
        self.assertEqual(updated['turns'][0]['fileChanges'][0]['path'],'net.py')

    def test_partial_desktop_snapshot_keeps_saved_prefix_without_changing_patch_state(self):
        session = LiveSession('chat')
        saved = {'id': 'chat', 'turns': [
            {'turnId': str(i), 'items': [{'type': 'agentMessage', 'id': str(i), 'text': 'saved '+str(i)}]}
            for i in range(3)]}
        with session.condition:
            session.set_history(saved)
            session.state = {'id': 'chat', 'turns': [
                {'turnId': '2', 'items': [{'type': 'agentMessage', 'id': '2', 'text': 'live'}]}],
                'turnsPagination': {'hasLoadedOldest': False}, 'requests': []}
            session.connected = True
        result = session.view()
        self.assertEqual([t['id'] for t in result['turns']], ['0', '1', '2'])
        self.assertEqual(result['turns'][-1]['messages'][0]['text'], 'live')
        self.assertEqual(len(session.state['turns']), 1)
        self.assertTrue(result['historyComplete'])
        # A subsequent authoritative complete snapshot (including rollback) wins.
        session.state['turnsPagination']['hasLoadedOldest'] = True
        self.assertEqual([t['id'] for t in session.view()['turns']], ['2'])

    def test_empty_incomplete_snapshot_does_not_clear_saved_history(self):
        session = LiveSession('chat')
        with session.condition:
            session.set_history({'id': 'chat', 'turns': [{'turnId': 'old', 'items': []}]})
            session.state = {'id': 'chat', 'turns': [], 'turnsPagination': {'hasLoadedOldest': False}}
        self.assertEqual([t['id'] for t in session.view()['turns']], ['old'])

    def test_initial_backfill_and_older_are_contiguous_despite_new_arrivals(self):
        timeline = Timeline()
        timeline.update(view())
        first = timeline.page()
        self.assertEqual([r['text'] for r in first['rows']], ['message '+str(i) for i in range(300, 320)])
        self.assertEqual(first['meta']['requests'], [{'id': 'approval'}])
        timeline.update(view(323, 2))
        backfill = timeline.page(80, first['before'])
        older = timeline.page(100, backfill['before'])
        self.assertEqual(len(first['rows']+backfill['rows']), 100)
        self.assertEqual([r['text'] for r in older['rows']], ['message '+str(i) for i in range(120, 220)])
        changes = timeline.changes(1, first['epoch'], backfill['before'])
        self.assertEqual([r['text'] for r in changes['rows']], ['message 320', 'message 321', 'message 322'])
        self.assertNotIn('turns', changes['meta'])
        self.assertNotIn('reset', changes)

    def test_streaming_updates_same_key_and_only_changed_rows(self):
        timeline = Timeline()
        state = view()
        timeline.update(state)
        first = timeline.page()
        state['sequence'] = 2
        state['turns'][0]['messages'][-1]['text'] += ' streaming'
        timeline.update(state)
        changes = timeline.changes(1, first['epoch'], first['before'])
        self.assertEqual(len(changes['rows']), 1)
        self.assertEqual(changes['rows'][0]['key'], first['rows'][-1]['key'])
        self.assertEqual(timeline.changes(2, first['epoch'], first['before'])['rows'], [])

    def test_activity_is_bounded_and_detail_chunks_are_lossless(self):
        timeline = Timeline()
        state = view(20)
        text = ('长日志 🚀 \n' * 10000)
        for item in state['turns'][0]['messages']:
            item.update(role='activity', text=text)
        timeline.update(state)
        page = timeline.page()
        self.assertLess(len(json.dumps(page, ensure_ascii=False).encode()), 20000)
        row = page['rows'][0]
        cursor = timeline.cursor(row['key'])
        offset, result = 0, ''
        while offset is not None:
            detail = timeline.detail(cursor, offset, row['version'])
            self.assertLessEqual(len(detail['text']), DETAIL_CHARS)
            result += detail['text']
            offset = detail['next']
        self.assertEqual(result, text)
        state['sequence'] = 2
        state['turns'][0]['messages'][0]['text'] = 'updated'
        timeline.update(state)
        self.assertEqual(timeline.detail(cursor, 16000, row['version'])['offset'], 0)

    def test_large_message_page_budget_and_eof(self):
        timeline = Timeline()
        state = view(100)
        for item in state['turns'][0]['messages']:
            item['text'] = '内容' * 10000
        timeline.update(state)
        count, cursor = 0, None
        while True:
            page = timeline.page(100, cursor)
            self.assertLessEqual(len(json.dumps(page['rows'], ensure_ascii=False, separators=(',', ':')).encode()), PAGE_BYTES + 101)
            count += len(page['rows'])
            cursor = page['before']
            if not page['hasMore']:
                break
        self.assertEqual(count, 100)
        self.assertEqual(timeline.page(100, cursor)['rows'], [])

    def test_source_replacement_deletion_and_foreign_cursors_reset(self):
        first, other = Timeline(), Timeline()
        first.update(view())
        other.update(view())
        page = first.page()
        self.assertTrue(other.page(before=page['before'])['reset'])
        self.assertTrue(other.changes(1, page['epoch'], page['before'])['reset'])
        with self.assertRaises(ValueError):
            other.detail(page['before'])
        first.update(view(310, 2))
        self.assertTrue(first.changes(1, page['epoch'], page['before'])['reset'])
        self.assertTrue(first.page(before=page['before'])['reset'])

    def test_old_revision_resets_without_unbounded_delta(self):
        timeline = Timeline()
        timeline.update(view())
        first = timeline.page()
        for sequence in range(2, 25):
            timeline.update(view(320, sequence))
        self.assertTrue(timeline.changes(1, first['epoch'], first['before'])['reset'])
        self.assertEqual(len(timeline.versions), 16)

    def test_paged_changes_keep_the_history_window_when_delta_or_revision_expires(self):
        timeline = Timeline()
        timeline.update(view(320, 1))
        first = timeline.page()
        for sequence in range(2, 25):
            timeline.update(view(620, sequence))
        cursor = None
        received = []
        while True:
            result = timeline.changes(1, first['epoch'], first['before'], paged=True, through=cursor)
            self.assertNotIn('reset', result)
            self.assertLessEqual(len(result['rows']), 100)
            self.assertLessEqual(len(json.dumps(result['rows']).encode()), PAGE_BYTES)
            received.extend(row['key'] for row in result['rows'])
            cursor = result.get('next')
            if not cursor:
                break
        self.assertEqual(received, [row['key'] for row in timeline.rows[300:]])
        self.assertEqual(len(received), len(set(received)))
        timeline.update(view(5, 26))
        self.assertTrue(timeline.changes(1, first['epoch'], first['before'], paged=True)['reset'])

    def test_saved_call_and_result_are_one_record(self):
        state = {'turns': [{'turnId': 'turn', 'items': [
            {'type': 'function_call', 'call_id': 'call', 'name': 'test', 'arguments': '{}'},
            {'type': 'function_call_output', 'call_id': 'call', 'output': 'done'}]}]}
        messages = normalize_state(state)['turns'][0]['messages']
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]['output'], 'done')


if __name__ == '__main__':
    unittest.main()
