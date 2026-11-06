import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.features.notifications import channels

ROOT = Path(__file__).resolve().parents[1]


def sharing_error(code):
    error = PermissionError('fixture sharing denial')
    error.winerror = code
    return error


class NotificationStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'status.json'
        self.original = {'state': 'connecting'}
        self.path.write_text(json.dumps(self.original), encoding='utf-8')

    def assert_no_temporary(self):
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_atomic_roundtrip_keeps_only_complete_destination(self):
        expected = {'state': 'connected', 'message': '\u4e2d\u6587'}
        channels.write_json(self.path, expected)
        self.assertEqual(channels.read_json(self.path, {}), expected)
        self.assertTrue(self.path.read_bytes().endswith(b'\n'))
        self.assert_no_temporary()

    def test_windows_transient_sharing_errors_retry_the_same_complete_source(self):
        original_replace = Path.replace
        attempts = []
        expected = {'state': 'connected'}

        def replace(source, destination):
            attempts.append(source)
            self.assertEqual(channels.read_json(destination, {}), self.original)
            self.assertEqual(json.loads(source.read_text(encoding='utf-8')), expected)
            if len(attempts) <= 3:
                raise sharing_error((5, 32, 33)[len(attempts) - 1])
            return original_replace(source, destination)

        clock = SimpleNamespace(monotonic=Mock(side_effect=[0, .01, .02, .03]), sleep=Mock())
        with patch.object(channels, 'os', SimpleNamespace(name='nt')), \
                patch.object(channels, 'time', clock), patch.object(Path, 'replace', replace):
            channels.write_json(self.path, expected)
        self.assertEqual(len(attempts), 4)
        self.assertEqual(len(set(attempts)), 1)
        self.assertEqual(clock.sleep.call_count, 3)
        self.assertEqual(channels.read_json(self.path, {}), expected)
        self.assert_no_temporary()

    def test_persistent_windows_denial_is_bounded_and_preserves_original(self):
        error = sharing_error(5)
        replace = Mock(side_effect=error)
        clock = SimpleNamespace(monotonic=Mock(side_effect=[0, 1, 2]), sleep=Mock())
        with patch.object(channels, 'os', SimpleNamespace(name='nt')), \
                patch.object(channels, 'time', clock), patch.object(Path, 'replace', replace):
            with self.assertRaises(PermissionError) as result:
                channels.write_json(self.path, {'state': 'uncommitted'})
        self.assertIs(result.exception, error)
        self.assertEqual(replace.call_count, 2)
        clock.sleep.assert_called_once_with(.05)
        self.assertEqual(channels.read_json(self.path, {}), self.original)
        self.assert_no_temporary()

    def test_other_permission_errors_do_not_retry_or_leave_temporary(self):
        for error in (sharing_error(13), PermissionError('fixture access denial')):
            with self.subTest(error=str(error), code=getattr(error, 'winerror', None)):
                replace = Mock(side_effect=error)
                clock = SimpleNamespace(monotonic=Mock(return_value=0), sleep=Mock())
                with patch.object(channels, 'os', SimpleNamespace(name='nt')), \
                        patch.object(channels, 'time', clock), patch.object(Path, 'replace', replace):
                    with self.assertRaises(PermissionError) as result:
                        channels.write_json(self.path, {'state': 'uncommitted'})
                self.assertIs(result.exception, error)
                replace.assert_called_once()
                clock.sleep.assert_not_called()
                self.assertEqual(channels.read_json(self.path, {}), self.original)
                self.assert_no_temporary()

    def test_nonwindows_error_is_not_retried_even_with_a_sharing_code(self):
        replace = Mock(side_effect=sharing_error(5))
        clock = SimpleNamespace(monotonic=Mock(), sleep=Mock())
        with patch.object(channels, 'os', SimpleNamespace(name='posix')), \
                patch.object(channels, 'time', clock), patch.object(Path, 'replace', replace):
            with self.assertRaises(PermissionError):
                channels.write_json(self.path, {'state': 'uncommitted'})
        replace.assert_called_once()
        clock.monotonic.assert_not_called()
        clock.sleep.assert_not_called()
        self.assertEqual(channels.read_json(self.path, {}), self.original)
        self.assert_no_temporary()

    def test_other_io_failure_preserves_original_and_cleans_temporary(self):
        error = OSError('fixture I/O failure')
        replace = Mock(side_effect=error)
        with patch.object(Path, 'replace', replace):
            with self.assertRaises(OSError) as result:
                channels.write_json(self.path, {'state': 'uncommitted'})
        self.assertIs(result.exception, error)
        replace.assert_called_once()
        self.assertEqual(channels.read_json(self.path, {}), self.original)
        self.assert_no_temporary()

    def test_serialization_failure_leaves_original_untouched(self):
        with patch.object(Path, 'replace') as replace:
            with self.assertRaises(TypeError):
                channels.write_json(self.path, {'invalid': object()})
        replace.assert_not_called()
        self.assertEqual(channels.read_json(self.path, {}), self.original)
        self.assert_no_temporary()

    def test_permission_setup_failure_cleans_the_completed_temporary(self):
        with patch.object(Path, 'chmod', side_effect=PermissionError('fixture chmod denial')), \
                patch.object(Path, 'replace') as replace:
            with self.assertRaises(PermissionError):
                channels.write_json(self.path, {'state': 'uncommitted'})
        replace.assert_not_called()
        self.assertEqual(channels.read_json(self.path, {}), self.original)
        self.assert_no_temporary()


if __name__ == '__main__':
    unittest.main()
