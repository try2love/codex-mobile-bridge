"""A running gateway must resolve updated binaries before starting new helpers."""
import io
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.clients.codex.catalog import Catalog
from bridge.features.sessions.create import CreationUnavailable
from bridge.app.desktop import Desktop
from bridge.features.sessions.goal import GoalRPC
from bridge.app.service import Bridge, LiveSession
from bridge.features.sessions.side_chat import SideChatError

ROOT = Path(__file__).resolve().parents[1]
(ROOT / '.tmp').mkdir(exist_ok=True)


class RuntimeConsumerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.old, self.new = self.root / 'old.exe', self.root / 'new.exe'
        self.old.touch()
        self.new.touch()
        self.find = patch.object(Catalog, 'find_runtime', return_value=self.old).start()
        self.addCleanup(patch.stopall)
        with patch('bridge.app.service.threading.Thread.start'):
            self.bridge = Bridge(self.root, self.root / 'data')
        self.addCleanup(self.bridge.close)

    def update_runtime(self):
        self.old.unlink()
        self.find.return_value = self.new

    def test_account_and_goal_consumers_share_the_updated_catalog(self):
        self.assertEqual(self.bridge.account.rpc().executable, self.old)
        self.assertEqual(self.bridge.accounts.runtime, self.old)
        self.assertEqual(self.bridge.goal.executable, self.old)
        self.update_runtime()
        self.assertEqual(self.bridge.account.rpc().executable, self.new)
        self.assertEqual(self.bridge.accounts.runtime, self.new)
        self.assertEqual(self.bridge.goal.executable, self.new)
        self.assertTrue(self.bridge._goal_runtime_available())
        self.assertEqual(self.find.call_count, 2)

    def test_explicit_missing_runtime_is_retained_by_consumers(self):
        self.bridge.catalog_reader.executable = self.old
        self.update_runtime()
        self.assertEqual(self.bridge.account.rpc().executable, self.old)
        self.assertEqual(self.bridge.accounts.runtime, self.old)
        self.assertEqual(self.bridge.goal.executable, self.old)
        self.assertFalse(self.bridge._goal_runtime_available())
        self.assertEqual(self.find.call_count, 1)

    def test_goal_reuses_active_process_and_refreshes_only_when_restarting(self):
        getter = Mock(side_effect=[self.old, self.new])
        goal = GoalRPC(self.root, None, executable_getter=getter)
        self.addCleanup(goal.close)
        processes = [Mock(stdin=io.StringIO(), stdout=io.StringIO()) for _ in range(2)]
        with patch('bridge.features.sessions.goal.subprocess.Popen', side_effect=processes) as spawn, \
             patch.object(goal, '_receive', return_value={}):
            goal.start()
            self.update_runtime()
            goal.start()
            self.assertIs(goal.process, processes[0])
            self.assertEqual(getter.call_count, 1)
            self.assertEqual(spawn.call_count, 1)
            goal.close()
            goal.start()
            self.assertIs(goal.process, processes[1])
            self.assertEqual([call.args[0][0] for call in spawn.call_args_list],
                             [str(self.old), str(self.new)])

    @staticmethod
    def side_chat(*_):
        chat = SimpleNamespace(id=str(uuid.uuid4()))
        chat.view = lambda: {'id': chat.id}
        chat.close = lambda: chat.file_directory.cleanup()
        return chat

    def create_side_chat(self):
        return self.bridge.side_chats.operate(str(uuid.uuid4()), 'create',
            {'creationId': str(uuid.uuid4())}, {'cwd': str(self.root)}, {})

    def test_new_side_chat_rechecks_updated_runtime_before_forking(self):
        factory = Mock(side_effect=self.side_chat)
        self.bridge.side_chats.factory = factory
        with patch('bridge.features.sessions.side_chat.check_runtime') as check:
            self.create_side_chat()
            self.create_side_chat()
            self.assertEqual(check.call_count, 1)
            self.update_runtime()
            self.create_side_chat()
            self.assertEqual([call.args[0] for call in check.call_args_list], [self.old, self.new])
            self.assertEqual([call.args[0] for call in factory.call_args_list],
                             [self.old, self.old, self.new])

    def test_unsupported_replacement_does_not_fork_or_cache_success(self):
        factory = Mock(side_effect=self.side_chat)
        self.bridge.side_chats.factory = factory
        with patch('bridge.features.sessions.side_chat.check_runtime') as check:
            self.create_side_chat()
            self.update_runtime()
            check.side_effect = SideChatError('unsupported')
            for _ in range(2):
                with self.assertRaises(SideChatError):
                    self.create_side_chat()
            self.assertEqual(factory.call_count, 1)
            self.assertEqual(check.call_count, 3)

    def test_fork_start_failure_allows_same_request_retry(self):
        source, child = str(uuid.uuid4()), str(uuid.uuid4())
        session = LiveSession(source)
        session.state = {'id': source, 'title': 'Original', 'cwd': str(self.root),
                         'modelProvider': 'custom', 'latestModel': 'fixture',
                         'threadRuntimeStatus': {'type': 'idle'}, 'requests': [],
                         'turns': [{'turnId': 'turn', 'status': 'completed',
                                    'items': [{'id': 'answer', 'type': 'agentMessage',
                                               'text': 'Answer', 'phase': 'final'}]}]}
        session.timeline.update(session.view())
        row = session.timeline.rows[0]
        body = {'id': str(uuid.uuid4()), 'action': 'fork',
                'key': session.timeline.cursor(row['key']), 'version': row['version']}
        with patch.object(self.bridge, '_target', return_value=session), \
             patch('bridge.app.service.fork_copy', side_effect=[CreationUnavailable('missing'), child]) as fork:
            with self.assertRaises(CreationUnavailable):
                self.bridge.message_action(source, body)
            self.assertNotIn(body['id'], self.bridge.message_actions)
            persisted = json.loads(self.bridge.actions_path.read_text(encoding='utf-8'))
            self.assertNotIn(body['id'], persisted)
            self.assertEqual(self.bridge.message_action(source, body)['id'], child)
            self.assertEqual(fork.call_count, 2)

    def test_windows_local_services_preserve_automatic_selection_and_fallback(self):
        for name, configured, automatic, expected in (
            ('automatic', '', self.new, None),
            ('explicit', str(self.old), self.new, str(self.old)),
            ('nonstandard', '', None, str(self.old)),
        ):
            with self.subTest(name=name):
                desktop = Desktop(self.root / name)
                preferences = {'codexHome': str(self.root), 'codexBin': configured}
                self.find.return_value = automatic
                with patch.object(desktop, 'preferences', return_value=preferences), \
                     patch('bridge.app.desktop.os', SimpleNamespace(name='nt')), \
                     patch('bridge.clients.discovery.discover_clients',
                           return_value={'codex': {'runtime': str(self.old)}}), \
                     patch('bridge.app.service.Bridge') as bridge, \
                     patch('bridge.clients.manager.DesktopSessions'):
                    desktop.local_services()
                    self.assertEqual(bridge.call_args.kwargs['codex_bin'], expected)
