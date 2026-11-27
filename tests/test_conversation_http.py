import unittest
from unittest.mock import Mock

import test_bridge


class ConversationHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.record = test_bridge.password_record('correct password test')

    setUp = test_bridge.HttpTests.setUp
    tearDown = test_bridge.HttpTests.tearDown
    request = test_bridge.HttpTests.request
    login = test_bridge.HttpTests.login

    def test_conversation_process_and_body_reads_use_authenticated_session_scope(self):
        target = '/api/sessions/' + test_bridge.THREAD
        reader = self.server.bridge.timeline_read = Mock(return_value={'rows': []})
        for path in ('/conversation', '/process?cursor=epoch.process', '/detail?key=epoch.item&projection=conversation'):
            self.assertEqual(self.request('GET', target + path)[0], 401)
        reader.assert_not_called()
        headers = self.login()
        self.assertEqual(self.request('GET', target + '/conversation?limit=20', headers=headers)[0], 200)
        reader.assert_called_with(test_bridge.THREAD, limit=20, before=None, projection='conversation')
        self.assertEqual(self.request('GET', target + '/process?cursor=epoch.process&after=item', headers=headers)[0], 200)
        reader.assert_called_with(test_bridge.THREAD, 'process', cursor='epoch.process', after='item', limit=20)
        self.assertEqual(self.request('GET', target + '/detail?key=epoch.item&projection=conversation&offset=16000&version=v', headers=headers)[0], 200)
        reader.assert_called_with(test_bridge.THREAD, 'detail', cursor='epoch.item', offset=16000, version='v', projection='conversation')
        self.assertEqual(self.request('GET', target + '/conversation?host=missing', headers=headers)[0], 404)

    def test_shared_client_projection_arguments_are_forwarded_only_after_authentication(self):
        manager = self.server.desktop_sessions = Mock()
        manager.call.return_value = {'rows': []}
        target = '/api/desktop-sessions/claude/detail?sessionId=chat&historyMode=conversation&historyProcess=cursor&historyAfter=last'
        self.assertEqual(self.request('GET', target)[0], 401)
        manager.call.assert_not_called()
        headers = self.login()
        status = self.request('GET', target, headers=headers)[0]
        self.assertEqual(status, 200)
        self.assertEqual(manager.call.call_args.args[:3], ('claude', 'detail', 'chat'))
        self.assertEqual(manager.call.call_args.args[3],
                         {'historyMode': 'conversation', 'historyProcess': 'cursor', 'historyAfter': 'last', 'historyLimit': 40})


if __name__ == '__main__':
    unittest.main()
