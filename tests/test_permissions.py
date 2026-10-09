import copy
from unittest.mock import patch
import test_bridge as support
from bridge.features.sessions.model import permission_mode
from bridge.clients.codex.ipc import IPCError


class PermissionsTests(support.IntegrationTests):
    # Reuse setup only, not the inherited integration tests.
    def test_permission_presets_use_original_owner(self):
        session = self.bridge.session(support.THREAD)
        original = copy.deepcopy(session.state)
        def call(target, method, body):
            self.assertIs(target, session)
            self.assertEqual(method, 'thread-follower-update-thread-settings')
            self.assertEqual(set(body), {'threadSettings'})
            with session.condition:
                session.state['latestThreadSettings'] = body['threadSettings']
                session.changed()
            return {'applied': True}
        with patch.object(self.bridge, '_call', side_effect=call):
            for preset in ('ask','auto-review','full-access'):
                result=self.bridge.permissions(support.THREAD,preset,confirmed=True)
                self.assertTrue(result['confirmed'])
                self.assertEqual(session.view()['permissionMode'],preset)
                self.assertEqual(session.state['latestModel'],original['latestModel'])
                self.assertEqual(session.state['modelProvider'],original['modelProvider'])
        self.assertEqual(session.owner,'owner')

    def test_side_fork_inherits_latest_permission_selection(self):
        from bridge.app.service import Bridge
        state = {'modelProvider':'custom', 'latestModel':'fixture',
                 'currentPermissions':{'approvalPolicy':'never', 'approvalsReviewer':'user'},
                 'latestThreadSettings':{'approvalPolicy':'on-request','approvalsReviewer':'auto_review',
                                         'activePermissionProfile':{'id':':workspace'}}}
        fork = Bridge._fork_settings(state)
        self.assertEqual(fork['approvalPolicy'],'on-request')
        self.assertEqual(fork['approvalsReviewer'],'auto_review')
        self.assertEqual(fork['permissions'],':workspace')

    def test_rejection_and_explicit_full_access(self):
        with patch.object(self.bridge, '_call', return_value={'applied':False}) as call:
            with self.assertRaises(ValueError):self.bridge.permissions(support.THREAD,'full-access')
            with self.assertRaises(ValueError):self.bridge.permissions(support.THREAD,'bad')
            call.assert_not_called()
            with self.assertRaises(IPCError):self.bridge.permissions(support.THREAD,'auto-review')
        self.assertEqual(permission_mode({'currentPermissions': {'approvalPolicy':'on-request','sandboxPolicy':{'type':'readOnly'}}}),'custom')
        self.assertEqual(permission_mode({'currentPermissions': {'approvalPolicy':'on-request','sandboxPolicy':{'type':'workspaceWrite'},'approvalsReviewer':'auto_review'}}),'auto-review')

# Avoid running the base tests a second time in discovery.
for name in dir(support.IntegrationTests):
    if name.startswith('test_') and name not in PermissionsTests.__dict__:
        setattr(PermissionsTests, name, None)
