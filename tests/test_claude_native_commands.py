"""Native command formatting requires an intact, evidenced CLI record chain."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from bridge.clients.claude import model
from bridge.clients.claude.cli_session import _saved
from bridge.clients.claude.history import page
from bridge.clients.claude.remote import worker_source


class NativeCommands(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp')
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        self.native = str(uuid.uuid4())
        self.path = self.home/(self.native+'.jsonl')
        base = {'type': 'user', 'entrypoint': 'cli', 'promptId': 'prompt', 'sessionId': self.native,
                'cwd': str(self.home), 'timestamp': '2026-10-25T11:33:00Z'}
        contents = ['<local-command-caveat>Native local command</local-command-caveat>',
                    '<command-name>/exit</command-name>\n<command-message>exit</command-message>\n<command-args></command-args>',
                    '<local-command-stdout>See ya!</local-command-stdout>']
        self.events = [{**base, 'uuid': str(i), 'parentUuid': str(i-1),
                        'message': {'role': 'user', 'content': content}} for i, content in enumerate(contents)]
        self.events[0]['isMeta'] = True

    def test_valid_native_chain_renders_command_and_output_with_original_identity(self):
        before = copy.deepcopy(self.events)
        messages = model.transcript(self.events)
        self.assertEqual([row['text'] for row in messages], ['/exit', 'See ya!'])
        self.assertEqual([row['eventId'] for row in messages], ['1', '2'])
        self.assertEqual(self.events, before)

    def test_ordinary_user_xml_and_unproven_chain_remain_verbatim(self):
        for change in ({'isMeta': False}, {'entrypoint': 'sdk'}, {'promptId': 'other'},
                       {'sessionId': 'other'}, {'uuid': 'other'}):
            with self.subTest(change=change):
                events = copy.deepcopy(self.events)
                events[0].update(change)
                self.assertEqual(model.transcript(events)[-2]['text'], events[1]['message']['content'])
                self.assertEqual(model.transcript(events)[-1]['text'], events[2]['message']['content'])
        self.assertEqual(model.transcript(self.events[1:])[0]['text'], self.events[1]['message']['content'])

    def test_other_prompt_output_and_intervening_message_are_not_rewritten(self):
        events = copy.deepcopy(self.events)
        events[2]['parentUuid'] = 'unrelated'
        self.assertEqual(model.transcript(events)[-1]['text'], events[2]['message']['content'])
        events = copy.deepcopy(self.events)
        events.insert(1, {'type': 'user', 'message': {'role': 'user', 'content': 'ordinary prompt'}})
        self.assertEqual(model.transcript(events)[-2]['text'], events[2]['message']['content'])

    def test_local_and_standalone_remote_titles_use_conversation_then_command_fallback(self):
        scope = {'__name__': 'native_command_worker'}
        exec(compile(worker_source(), 'native_command_worker', 'exec'), scope)
        found = {'origin': 'fixture', 'directory': str(self.home), 'binary': '/fixture/claude'}
        for extra, title in (([], '/exit'), ([{'type': 'user', 'message': {'role': 'user', 'content': 'Actual question'}}], 'Actual question')):
            with self.subTest(title=title):
                self.path.write_text('\n'.join(json.dumps(event) for event in self.events+extra)+'\n')
                before = self.path.read_bytes()
                self.assertEqual(_saved(self.path, 'fixture')['title'], title)
                with patch.dict(os.environ, {}, clear=True), patch.object(Path, 'home', return_value=self.home):
                    self.assertEqual(scope['saved'](self.path, found)['title'], title)
                self.assertEqual(self.path.read_bytes(), before)
        self.path.write_text('\n'.join(json.dumps(event) for event in self.events)+'\n')
        events, _, _ = page(self.path, self.native)
        self.assertEqual([row['text'] for row in model.transcript(events)], ['/exit', 'See ya!'])


if __name__ == '__main__':
    unittest.main()
