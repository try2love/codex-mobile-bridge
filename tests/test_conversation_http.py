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


if __name__ == '__main__':
    unittest.main()
