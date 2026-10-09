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

    def test_saved_call_and_result_are_one_record(self):
        state = {'turns': [{'turnId': 'turn', 'items': [
            {'type': 'function_call', 'call_id': 'call', 'name': 'test', 'arguments': '{}'},
            {'type': 'function_call_output', 'call_id': 'call', 'output': 'done'}]}]}
        messages = normalize_state(state)['turns'][0]['messages']
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]['output'], 'done')


if __name__ == '__main__':
    unittest.main()
