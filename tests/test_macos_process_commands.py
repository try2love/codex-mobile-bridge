"""Read-only process command contract; never invokes ps or signals real apps."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bridge.platforms.macos.desktop import commands


class MacOSProcessCommandsTests(unittest.TestCase):
    def test_empty_selection_never_invokes_ps(self):
        # macOS ps -p '' can produce invalid UTF-8 diagnostic bytes.
        with patch('bridge.platforms.macos.desktop.subprocess.run') as run:
            self.assertEqual(commands([]), {})
            run.assert_not_called()

    def test_nonempty_selection_keeps_exact_pid_query_and_full_command(self):
        result = SimpleNamespace(returncode=0, stdout=' 701 /fixture/Desktop --profile /path with spaces\n')
        with patch('bridge.platforms.macos.desktop.subprocess.run', return_value=result) as run:
            self.assertEqual(commands([701]), {701: '/fixture/Desktop --profile /path with spaces'})
            self.assertEqual(run.call_args.args[0], ['ps', '-ww', '-p', '701', '-o', 'pid=,args='])

    def test_display_name_uses_same_user_kernel_image_without_signals(self):
        from bridge.platforms.macos.desktop import processes
        import os
        result=SimpleNamespace(stdout=' 701 ZCode\n 702 /Applications/Other.app/Contents/MacOS/Other\n 703 vanished\n')
        with patch('bridge.platforms.macos.desktop.subprocess.run',return_value=result), patch('bridge.platforms.macos.force_exit.Native') as native:
            native.return_value.identity.side_effect=[{'uid':os.getuid(),'zombie':False,'image':'/Applications/ZCode.app/Contents/MacOS/ZCode'}, ValueError('gone')]
            self.assertEqual(processes(lambda path:path=='/Applications/ZCode.app/Contents/MacOS/ZCode'),[701])
            self.assertEqual(native.return_value.identity.call_count,2)
            native.return_value.kill.assert_not_called()
