import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from bridge.clients.zcode.installation import installation, renderer_url
from bridge.platforms.windows.loopback import listener_identity


class ZCodeWindowsTests(unittest.TestCase):
    def test_windows_installation_and_renderer_require_exact_executable_and_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory)/'ZCode.exe'
            app.touch()
            with patch('bridge.clients.zcode.installation.sys.platform', 'win32'), patch(
                    'bridge.platforms.windows.executable.product_version', return_value='3.17.1.790'):
                self.assertIsNone(installation(app))
                (app.parent/'resources').mkdir()
                (app.parent/'resources/app.asar').touch()
                self.assertEqual(installation(app), {'executable': str(app.resolve()), 'version': '3.17.1'})
                self.assertEqual(renderer_url(app), (app.parent/'resources/app.asar/out/renderer/index.html').as_uri())
                self.assertIsNone(installation(app.with_name('other.exe')))

    def test_loopback_refuses_other_user_session_executable_and_listener_races(self):
        exe = Path('ZCode.exe').resolve()
        valid = {'pid': 12, 'created': 34, 'session': 1, 'image': os.path.normcase(str(exe))}
        for override, same_user, rows in (({}, True, [(12, '127.0.0.1')]),
                ({'session': 0}, True, [(12, '127.0.0.1')]),
                ({'session': 2}, True, [(12, '127.0.0.1')]),
                ({'image': 'other'}, True, [(12, '127.0.0.1')]),
                ({}, False, [(12, '127.0.0.1')]),
                ({}, True, [(13, '127.0.0.1')])):
            native = Mock(session=1)
            native.identity.return_value = {**valid, **override}
            native.alive.return_value = True
            with self.subTest(override=override, same_user=same_user, rows=rows), patch(
                    'bridge.platforms.windows.loopback._Native', return_value=native), patch(
                    'bridge.platforms.windows.loopback._same_user', return_value=same_user), patch(
                    'bridge.platforms.windows.loopback._listeners', side_effect=[[(12, '127.0.0.1')], rows]):
                if not override and same_user and rows == [(12, '127.0.0.1')]:
                    self.assertEqual(listener_identity(17742, exe), {'pid': 12, 'created': 34, 'session': 1})
                else:
                    with self.assertRaises(ValueError): listener_identity(17742, exe)
                self.assertTrue(all(call.kwargs == {'terminate': False} for call in native.open.call_args_list))
                self.assertEqual(native.close.call_count, 2)

    def test_port_preflight_distinguishes_empty_tables_from_unreadable_or_foreign(self):
        with patch('bridge.platforms.windows.loopback._listeners') as table:
            table.return_value=[]
            self.assertIsNone(listener_identity(17742, Path('ZCode.exe'), required=False))
            table.return_value=[(5, '::1')]
            with self.assertRaises(ValueError): listener_identity(17742, Path('ZCode.exe'), required=False)
            table.side_effect=OSError('native table unavailable')
            with self.assertRaises(OSError): listener_identity(17742, Path('ZCode.exe'), required=False)

    def test_external_and_duplicate_listeners_are_rejected_before_opening_process(self):
        for rows in ([(12, '0.0.0.0')], [(12, '::1')], [(12, '127.0.0.1'), (13, '::')], []):
            with patch('bridge.platforms.windows.loopback._listeners', return_value=rows), patch(
                    'bridge.platforms.windows.loopback._Native') as native:
                with self.assertRaises(ValueError): listener_identity(17742, Path('ZCode.exe'))
                native.assert_not_called()
