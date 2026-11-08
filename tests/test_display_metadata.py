import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from bridge.app.lifecycle import private_json
from bridge.clients.manager import DesktopSessions
from bridge.features.sessions.display_metadata import DisplayMetadata, RenameCommands
from bridge.features.sessions.organization import OrganizationNotSent
import test_bridge


def change(kind, **values):
    return {'id': str(uuid.uuid4()), 'kind': kind, **values}


class DisplayMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.store = DisplayMetadata(self.temp.name, private_json)

    def test_persistent_project_section_and_alias_do_not_change_native_identity(self):
        project = self.store.change('one', 'chat', change('create-project', title='工程'))
        section = self.store.change('one', 'chat', change('create-section', title='待办'))
        self.store.change('one', 'chat', change('rename', title='显示名称'))
        original = {'id': 'chat', 'title': 'Native', 'cwd': '/original', 'owner': 'owner', 'provider': 'upstream'}
        row = DisplayMetadata(self.temp.name, private_json).decorate('one', original)
        self.assertEqual(row['title'], '显示名称')
        self.assertEqual(row['projectId'], project['projectId'])
        self.assertEqual(row['sectionId'], section['sectionId'])
        self.assertEqual(row['projectName'], '工程')
        for field in ('cwd', 'owner', 'provider'):
            self.assertEqual(row[field], original[field])
        self.assertEqual(self.store.decorate('two', original), original)
        self.assertEqual(original['title'], 'Native')

    def test_idempotent_create_conflict_and_stale_assignment(self):
        body = change('create-project', title='项目')
        first = self.store.change('one', 'chat', body)
        self.assertEqual(self.store.change('one', 'chat', body), first)
        self.assertEqual(len(self.store.snapshot('one', 'chat')['projects']), 1)
        with self.assertRaises(ValueError):
            self.store.change('one', 'other', body)
        with self.assertRaises(ValueError):
            self.store.change('one', 'chat', change('project', targetId=None, expectedId='stale'))
        self.store.change('one', 'chat', change('project', targetId=None, expectedId=first['projectId']))
        self.assertIsNone(self.store.snapshot('one', 'chat')['projectId'])

    def test_verified_native_title_is_not_masked_but_old_alias_is_retained(self):
        self.store.change('one', 'chat', change('rename', title='旧别名'))
        row = self.store.decorate('one', {'id': 'chat', 'title': '原生标题', 'capabilities': {'rename': True}})
        self.assertEqual(row['title'], '原生标题')
        self.assertNotIn('renameScope', row)
        self.assertEqual(self.store._read()['one']['sessions']['chat']['title'], '旧别名')

    def test_native_rename_unknown_blocks_new_request_after_restart(self):
        commands = RenameCommands(self.temp.name, private_json)
        read = Mock(return_value={'identity': {'id': 'chat', 'cwd': '/project'}, 'title': 'old'})
        dispatch = Mock(side_effect=TimeoutError)
        body = {'id': str(uuid.uuid4()), 'title': 'new'}
        with self.assertRaisesRegex(ValueError, '待核对'):
            commands.perform('chat', body, read, dispatch)
        with self.assertRaisesRegex(ValueError, '待核对'):
            RenameCommands(self.temp.name, private_json).perform('chat', {**body, 'id': str(uuid.uuid4())}, read, dispatch)
        dispatch.assert_called_once()
        read.return_value['title'] = 'new'
        result = RenameCommands(self.temp.name, private_json).perform('chat', body, read, dispatch)
        self.assertEqual(result['status'], 'confirmed')
        dispatch.assert_called_once()

    def test_explicit_not_sent_can_be_corrected(self):
        commands = RenameCommands(self.temp.name, private_json)
        read = Mock(return_value={'identity': {'id': 'chat'}, 'title': 'old'})
        body = {'id': str(uuid.uuid4()), 'title': 'new'}
        with self.assertRaises(OrganizationNotSent):
            commands.perform('chat', body, read, lambda title: {'notSent': True})
        def dispatch(title):
            read.return_value['title'] = title
        self.assertEqual(commands.perform('chat', body, read, dispatch)['status'], 'confirmed')

    def test_native_readback_converges_without_repeating_the_write(self):
        identity = {'id': 'chat', 'cwd': '/same'}
        read = Mock(side_effect=[{'identity': identity, 'title': 'old'}, None,
                                 {'identity': identity, 'title': 'old'}, {'identity': identity, 'title': 'new'}])
        dispatch = Mock(return_value={'status': 'confirmed'})
        with patch('bridge.features.sessions.display_metadata.time.sleep'):
            result = RenameCommands(self.temp.name, private_json).perform(
                'chat', {'id': str(uuid.uuid4()), 'title': 'new'}, read, dispatch)
        self.assertEqual(result['title'], 'new')
        dispatch.assert_called_once_with('new')

    def test_native_readback_identity_change_is_not_retried(self):
        read = Mock(side_effect=[{'identity': {'id': 'chat'}, 'title': 'old'},
                                 {'identity': {'id': 'another'}, 'title': 'new'}])
        with patch('bridge.features.sessions.display_metadata.time.sleep') as sleep, self.assertRaisesRegex(ValueError, '待核对'):
            RenameCommands(self.temp.name, private_json).perform('chat', {'id': str(uuid.uuid4()), 'title': 'new'}, read, Mock())
        sleep.assert_not_called()


class ManagerMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.manager = DesktopSessions(self.temp.name)
        self.addCleanup(self.manager.close)
        self.manager.config['enabled'] = {'claude': True, 'deepseek': True}
        self.row = {'id': 'chat', 'title': 'old', 'cwd': '/original', 'host': 'local', 'backend': 'cli'}
        self.calls = []
        self.project = None
        self.manager._adapter_call = self.adapter_call

    def adapter_call(self, provider, action, sid, body):
        self.calls.append((action, body))
        if action == 'list':
            return {'sessions': [dict(self.row)], 'connected': True}
        if action == 'detail':
            return {'session': dict(self.row)}
        if action == 'rename':
            self.row['title'] = body['title']
            return {'status': 'confirmed'}
        if action == 'organization':
            if body['operation'] == 'set-project':
                self.project = body['projectId']
            return {'scope': 'native', 'status': 'confirmed', 'projectId': self.project,
                    'projects': [{'id': 'p', 'title': 'Original'}], 'identity': {'id': 'chat', 'cwd': '/original'}}
        self.fail('Unexpected action: ' + action)

    def test_unavailable_native_rename_is_explicit_and_never_creates_an_alias(self):
        for provider in ('claude', 'deepseek'):
            with self.assertRaisesRegex(OrganizationNotSent, '原生改名'):
                self.manager.call(provider, 'rename', 'chat', {'id': str(uuid.uuid4()), 'title': 'Alias'})
        self.manager.call('claude', 'organization', 'chat', change('create-project', title='Project'))
        rows = self.manager.call('claude', 'list')['sessions']
        self.assertEqual(rows[0]['title'], 'old')
        self.assertEqual(rows[0]['projectName'], 'Project')
        self.assertEqual(self.row['title'], 'old')
        self.assertTrue(all(action == 'list' for action, body in self.calls))

    def test_native_rename_readback_and_bridge_alias_removal(self):
        scope = self.manager._display_scope('deepseek', self.row)
        self.manager.display_metadata.change(scope, 'chat', change('rename', title='Alias'))
        self.row['capabilities'] = {'rename': True}
        rid = str(uuid.uuid4())
        result = self.manager.call('deepseek', 'rename', 'chat', {'id': rid, 'title': 'Native'})
        self.assertEqual(result['title'], 'Native')
        self.assertEqual(self.manager.call('deepseek', 'list')['sessions'][0]['title'], 'Native')
        self.assertEqual(self.row['cwd'], '/original')
        self.assertNotIn('title', self.manager.display_metadata._read()[scope]['sessions']['chat'])
        self.assertIn(('rename', {'title': 'Native', 'id': rid}), self.calls)
        self.assertEqual(RenameCommands(self.manager.directory, private_json).perform(
            str(uuid.uuid5(uuid.NAMESPACE_URL, scope+'\nchat')), {'id': rid, 'title': 'Native'},
            lambda: {'title': 'Native', 'identity': {key: self.row.get(key)
                for key in ('id', 'cwd', 'host', 'backend', 'provider', 'modelProvider')}},
            lambda title: self.fail('confirmed receipt must not write again'))['scope'], 'native')

    def test_native_project_and_local_section_preserve_mixed_scope(self):
        self.row['capabilities'] = {'organization': True}
        result = self.manager.call('deepseek', 'organization', 'chat', change('project', targetId='p', expectedId=None))
        self.assertEqual(result['projectId'], 'p')
        result = self.manager.call('deepseek', 'organization', 'chat', change('create-section', title='Todo'))
        self.assertEqual(result['projectId'], 'p')
        self.assertEqual(result['sections'][0]['title'], 'Todo')
        self.assertEqual(result['sectionScope'], 'bridge')
        self.assertEqual(result['projectScope'], 'native')
        self.assertEqual(sum(body.get('operation') == 'set-project' for _, body in self.calls), 1)

    def test_lost_rename_receipt_is_confirmed_readonly_after_transport_disconnects(self):
        self.row['capabilities'] = {'rename': True}
        scope = self.manager._display_scope('deepseek', self.row)
        self.manager.display_metadata.change(scope, 'chat', change('rename', title='old alias'))
        normal = self.manager._adapter_call
        def lost(provider, action, sid, body):
            value = normal(provider, action, sid, body)
            if action == 'rename':
                raise TimeoutError('accepted, receipt lost')
            return value
        body = {'id': str(uuid.uuid4()), 'title': 'Native'}
        self.manager._adapter_call = lost
        with self.assertRaisesRegex(ValueError, '待核对'):
            self.manager.call('deepseek', 'rename', 'chat', body)
        self.row['capabilities'] = {'rename': False}
        retry = {**body, 'id': str(uuid.uuid4())}
        self.assertEqual(self.manager.call('deepseek', 'rename', 'chat', retry)['status'], 'confirmed')
        self.assertEqual(self.manager.call('deepseek', 'rename', 'chat', body)['status'], 'confirmed')
        self.assertEqual(sum(action == 'rename' for action, _ in self.calls), 1)
        self.assertNotIn('title', self.manager.display_metadata._read()[scope]['sessions']['chat'])


class MetadataHttpTests(unittest.TestCase):
    setUpClass = classmethod(test_bridge.HttpTests.setUpClass.__func__)
    setUp = test_bridge.HttpTests.setUp
    tearDown = test_bridge.HttpTests.tearDown
    request = test_bridge.HttpTests.request
    login = test_bridge.HttpTests.login

    def test_rename_and_organization_preserve_auth_csrf_and_opaque_session_id(self):
        manager = self.server.desktop_sessions = Mock()
        manager.call.return_value = {'status': 'confirmed'}
        for provider in ('claude', 'deepseek'):
            route = '/api/desktop-sessions/' + provider + '/organization'
            self.assertEqual(self.request('GET', route + '?sessionId=code%2Fnative')[0], 401)
            headers = self.login()
            self.assertEqual(self.request('GET', route + '?sessionId=code%2Fnative', headers=headers)[0], 200)
            manager.call.assert_called_with(provider, 'organization', 'code/native', {})
            for action, values in (('rename', {'title': '新名称'}), ('organization', {'kind': 'create-section', 'title': '待办'})):
                body = {'sessionId': 'code/native', 'id': str(uuid.uuid4()), **values}
                target = '/api/desktop-sessions/' + provider + '/' + action
                self.assertEqual(self.request('POST', target, body, {'Cookie': headers['Cookie']})[0], 403)
                self.assertEqual(self.request('POST', target, body, headers)[0], 200)
                manager.call.assert_called_with(provider, action, 'code/native', {key: value for key, value in body.items() if key != 'sessionId'})


if __name__ == '__main__':
    unittest.main()
