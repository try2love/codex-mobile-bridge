import json
import tempfile
import unittest
from pathlib import Path

from bridge.app.service import LiveSession
from bridge.features.sessions.model import normalize_state
from bridge.features.sessions.store import SessionStore
from bridge.features.workspace.files import artifact_paths, workspace_references
from bridge.features.workspace.workspace import Workspace

ROOT = Path(__file__).resolve().parents[1]


class ContextUsageTests(unittest.TestCase):
    def test_latest_request_not_cumulative_and_unknown_not_zero(self):
        state = {'latestTokenUsageInfo': {'last': {'totalTokens': 1500}, 'total': {'totalTokens': 900000}, 'modelContextWindow': 200000}}
        usage = normalize_state(state)['contextUsage']
        self.assertEqual(usage, {'usedTokens': 1500, 'contextWindow': 200000, 'cached': False})
        for last in (None, {}, {'totalTokens': -1}, {'totalTokens': True}, {'totalTokens': '10'}):
            state['latestTokenUsageInfo']['last'] = last
            self.assertIsNone(normalize_state(state)['contextUsage'])
        self.assertIsNone(normalize_state({})['contextUsage'])

    def test_saved_usage_fallback_and_live_compaction(self):
        session = LiveSession('test')
        with session.condition:
            session.set_history({'latestTokenUsageInfo': {'last': {'totalTokens': 150000}, 'modelContextWindow': 200000}, 'tokenUsageFromHistory': True})
        session.state = {'id': 'test'}; session.connected = True
        self.assertTrue(session.view()['contextUsage']['cached'])
        session.state['latestTokenUsageInfo'] = {'last': {'totalTokens': 1000}, 'modelContextWindow': 200000}
        self.assertEqual(session.view()['contextUsage']['usedTokens'], 1000)
        self.assertFalse(session.view()['contextUsage']['cached'])

    def test_rollout_token_count_uses_last_valid_usage(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp') as directory:
            home = Path(directory); (home / 'sessions').mkdir(); path = home / 'sessions/fixture.jsonl'
            records = [
                {'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {'last_token_usage': {'total_tokens': 400}, 'model_context_window': 2000}}},
                {'type': 'event_msg', 'payload': {'type': 'token_count', 'info': None}},
            ]
            path.write_text('\n'.join(json.dumps(row) for row in records))
            state = SessionStore(home)._history('test', {'rollout_path': str(path), 'cwd': str(home)})
            self.assertEqual(normalize_state(state)['contextUsage'], {'usedTokens': 400, 'contextWindow': 2000, 'cached': True})


class DirectoryReferenceTests(unittest.TestCase):
    def test_app_directory_is_locatable_but_not_an_ordinary_download(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp') as directory:
            root = Path(directory); target = root / 'Gateway Preview.app'; target.mkdir()
            text = '[macOS网关APP](<' + str(target) + '>)'
            files = artifact_paths({'cwd': str(root), 'turns': [{'items': [{'type': 'agentMessage', 'text': text}]}]}, None)
            self.assertEqual(len(files), 1)
            self.assertEqual(next(iter(files.values()))['kind'], 'directory')
            self.assertFalse(next(iter(files.values()))['image'])
            self.assertEqual(workspace_references([{'role': 'assistant', 'text': text}], str(root))[0]['path'], target.name)
            work = Workspace(root)
            self.assertEqual(work.info(target.name), {'name': target.name, 'path': target.name, 'kind': 'directory', 'size': None})
            with self.assertRaises((OSError, ValueError)):
                with work.download(target.name): pass
            link = root / 'outside'; link.symlink_to(root.parent, target_is_directory=True)
            with self.assertRaises((OSError, ValueError, PermissionError)): work.info('outside')
            self.assertFalse(workspace_references([{'role': 'assistant', 'text': '[bad](' + str(link) + ')'}], str(root)))


if __name__ == '__main__': unittest.main()
