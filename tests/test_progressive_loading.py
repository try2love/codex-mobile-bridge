"""Recent history reuse must stay independent of passive owner attachment."""
import unittest
from unittest.mock import patch

import test_bridge
from test_bridge import THREAD


class ProgressiveLoadingTests(unittest.TestCase):
    setUp = test_bridge.IntegrationTests.setUp
    tearDown = test_bridge.IntegrationTests.tearDown

    def test_unchanged_pages_reuse_projection_and_patch_invalidates_it(self):
        self.fixture.state['turns'] = [{'turnId': 'turn', 'status': 'completed', 'items': [
            {'id': str(i), 'type': 'agentMessage', 'text': 'row ' + str(i), 'timestamp': 1700000000 + i} for i in range(10000)]}]
        session = self.bridge.session(THREAD)
        first = self.bridge.timeline_read(THREAD)
        self.assertEqual(len(first['rows']), 20)
        self.assertEqual(len(session.timeline.rows), 40)
        with patch.object(session, 'view', wraps=session.view) as normalize:
            again = self.bridge.timeline_read(THREAD)
            normalize.assert_not_called()
            older = self.bridge.timeline_read(THREAD, before=first['before'], limit=100)
            self.assertEqual(again['rows'], first['rows'])
            self.assertEqual(len(older['rows']), 100)
            # A newly requested window is normalized lazily; old bodies were
            # deliberately not converted during the initial read.
            normalize.assert_called_once_with(item_limit=140)
        self.fixture.publish({'type': 'patches', 'baseRevision': 1, 'revision': 2,
                              'patches': [{'op': 'replace', 'path': ['turns', 0, 'items', 9999, 'text'], 'value': 'updated'}]})
        with session.condition:
            self.assertTrue(session.condition.wait_for(lambda: session.revision == 2, timeout=3))
        changed = self.bridge.timeline_read(THREAD, 'changes', after=first['sequence'],
                                           epoch=first['epoch'], start=older['before'])
        self.assertEqual([row['text'] for row in changed['rows']], ['updated'])
        self.assertEqual(len(session.state['turns'][0]['items']), 10000)
        self.assertEqual([r['method'] for r in self.fixture.requests], ['initialize'])


if __name__ == '__main__':
    unittest.main()
