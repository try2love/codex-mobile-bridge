"""A native instance owns notification history even when session IDs repeat."""
import unittest
from unittest.mock import Mock

import test_desktop_notifications as notification_fixture


class NativeInstanceNotifications(unittest.TestCase):
    scan = notification_fixture.DesktopNotificationTests.scan

    def setUp(self):
        notification_fixture.DesktopNotificationTests.setUp(self)
        self.entries = {'claude:' + letter*32: Mock() for letter in ('a', 'b')}
        self.rows[:] = [{**self.rows[0], 'host': 'desktop:' + entry,
                        'title': entry, 'provider': 'claude'} for entry in self.entries]
        self.turns = {entry: [{'turnId': 'baseline-' + entry, 'status': 'completed'}]
                      for entry in self.entries}
        for entry, manager in self.entries.items():
            manager.enabled.return_value = True
            manager.call.side_effect = lambda provider, action, sid, entry=entry: {
                'connected': True, 'turns': list(self.turns[entry])}
        self.desktop.call.side_effect = AssertionError('An instance notification read the default account')
        self.desktop.resolve_instance.side_effect = lambda family, entry, **kwargs: self.entries[entry]

    def test_default_disabled_does_not_block_enabled_instance_notifications(self):
        self.desktop.enabled.side_effect = lambda entry: entry in self.entries
        self.assertEqual(self.scan(), [])
        first = next(iter(self.entries))
        self.rows[0]['updatedAt'] = 2
        self.turns[first].append({'turnId': 'completed-only-in-A', 'status': 'completed'})
        events = self.scan()
        self.assertEqual([(e['host'], e['kind']) for e in events],
                         [('desktop:' + first, 'completion')])
        self.desktop.call.assert_not_called()

    def test_same_session_reads_each_instance_and_never_default_history(self):
        self.desktop.enabled.return_value = True
        self.assertEqual(self.scan(), [])
        for row in self.rows:
            turns = self.manager.desktop_details[(row['host'], row['id'])][1]
            self.assertEqual(turns, self.turns[row['host'][8:]])
        second = list(self.entries)[1]
        self.rows[1]['updatedAt'] = 2
        self.turns[second].append({'turnId': 'completed-only-in-B', 'status': 'completed'})
        events = self.scan()
        self.assertEqual([e['host'] for e in events], ['desktop:' + second])
        self.desktop.call.assert_not_called()
        for manager in self.entries.values():
            self.assertTrue(manager.call.called)
