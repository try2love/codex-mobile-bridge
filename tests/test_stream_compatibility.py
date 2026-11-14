import copy
import unittest

from bridge.clients.codex.ipc import StreamCompatibilityError, validate_stream_state


class StreamCompatibilityTests(unittest.TestCase):
    def state(self):
        return {'id': 'thread', 'modelProvider': 'original-provider', 'cwd': '/original/project',
                'threadRuntimeStatus': {'type': 'idle'}, 'requests': [],
                'turns': [{'status': 'completed', 'items': [{'type': 'agentMessage', 'text': 'history'}]}]}

    def test_future_versions_accept_additive_fields(self):
        for version in (12, 14, 99):
            value = self.state()
            value['futureField'] = {'anything': True}
            value['turns'][0]['extra'] = 1
            validate_stream_state(version, value)

    def test_legacy_partial_snapshots_remain_readable(self):
        validate_stream_state(11, {'id': 'thread'})

    def test_v13_requires_readable_control_state(self):
        for key, value in [('requests', None), ('threadRuntimeStatus', {'type': 'unknown'}),
                           ('turns', [{'status': 'new-status', 'items': []}])]:
            state = self.state()
            state[key] = value
            with self.subTest(key=key), self.assertRaises(StreamCompatibilityError):
                validate_stream_state(13, state)
        with self.assertRaises(StreamCompatibilityError):
            validate_stream_state(13, {'id': 'thread'})

    def test_future_versions_accept_canonical_history_and_pending_approvals(self):
        value = self.state()
        value['turnHistory'] = {'kind': 'canonical', 'history': {
            'entitiesByKey': {'key': value['turns'][0]}, 'islands': [{'entries': [{'value': 'key'}]}]}}
        value['requests'] = [{'id': 1, 'method': 'item/commandExecution/requestApproval', 'params': {}}]
        validate_stream_state(99, value)
        value['turnHistory']['history']['islands'][0]['entries'][0]['value'] = 'missing'
        with self.assertRaises(StreamCompatibilityError):
            validate_stream_state(99, value)

    def test_empty_canonical_items_are_valid_before_first_output(self):
        value = self.state()
        value['turns'] = [{'status': 'inProgress', 'items': {
            'entitiesByKey': {}, 'islands': [], 'isComplete': True}}]
        for version in (13, 99):
            with self.subTest(version=version):
                validate_stream_state(version, value)

    def test_explicit_null_history_is_not_a_legacy_turn_list(self):
        value = self.state()
        value['turnHistory'] = None
        for version in (13, 99):
            with self.subTest(version=version), self.assertRaises(StreamCompatibilityError):
                validate_stream_state(version, value)

    def test_future_versions_reject_unknown_control_shapes(self):
        changes = [('modelProvider', None), ('cwd', None), ('requests', None), ('requests', [{}]), ('turns', {}),
                   ('turns', [{'status': 'future-status', 'items': []}]),
                   ('turns', [{'status': 'completed', 'items': [None]}]),
                   ('turnHistory', {'kind': 'new-history'}),
                   ('threadRuntimeStatus', {'type': 'future-runtime'})]
        for key, value in changes:
            state = copy.deepcopy(self.state())
            state[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(StreamCompatibilityError):
                validate_stream_state(99, state)
        state = self.state()
        del state['requests']
        with self.assertRaises(StreamCompatibilityError):
            validate_stream_state(99, state)

    def test_invalid_or_older_protocol_versions_are_rejected(self):
        for version in (None, True, '14', 10, -1):
            with self.subTest(version=version), self.assertRaises(StreamCompatibilityError):
                validate_stream_state(version, self.state())
