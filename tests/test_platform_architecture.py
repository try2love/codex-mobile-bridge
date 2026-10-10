"""Guard native dependency direction and process-control boundaries."""
import ast
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

from bridge.clients.desktop_app import DesktopApp
from bridge.clients.codex import transport


ROOT = Path(__file__).resolve().parents[1]


class PlatformArchitectureTests(unittest.TestCase):
    def test_native_modules_do_not_import_client_or_feature_policies(self):
        for path in (ROOT/'bridge/platforms').rglob('*.py'):
            package = '.'.join(path.relative_to(ROOT).parts[:-1])
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                modules = []
                if isinstance(node, ast.Import):
                    modules = [entry.name for entry in node.names]
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ''
                    if node.level:
                        module = importlib.util.resolve_name('.'*node.level+module, package)
                    modules = [module]
                    modules += [module+'.'+entry.name for entry in node.names if entry.name != '*']
                for module in modules:
                    with self.subTest(path=path.relative_to(ROOT), line=node.lineno, dependency=module):
                        self.assertFalse(module == 'bridge.clients' or module.startswith('bridge.clients.'))
                        self.assertFalse(module == 'bridge.features' or module.startswith('bridge.features.'))

    def test_selector_loads_only_requested_os_and_shared_posix_primitives(self):
        # Fresh interpreters keep prior test imports from hiding eager dependencies.
        script = ('import json,sys; from bridge.platforms import desktop,discovery; '
                  'desktop(sys.argv[1]); discovery(sys.argv[1]); '
                  'print(json.dumps([name for name in sys.modules if name.startswith("bridge.platforms.")]))')
        for platform, native in (('win32', 'windows'), ('darwin', 'macos'), ('linux', 'linux')):
            with self.subTest(platform=platform):
                result = subprocess.run([sys.executable, '-B', '-c', script, platform], cwd=ROOT,
                                        capture_output=True, text=True, check=True, timeout=10)
                loaded = json.loads(result.stdout)
                self.assertIn('bridge.platforms.'+native+'.desktop', loaded)
                self.assertIn('bridge.platforms.'+native+'.discovery', loaded)
                for other in {'windows', 'macos', 'linux'}-{native}:
                    self.assertFalse(any(name.startswith('bridge.platforms.'+other+'.') for name in loaded))

    def test_ipc_uses_only_selected_native_transport(self):
        for platform in ('nt', 'posix'):
            with self.subTest(platform=platform), patch.object(transport.os, 'name', platform), \
                    patch.object(transport.WindowsPipe, 'connect') as pipe, \
                    patch.object(transport, 'connect_unix_stream') as unix:
                result = transport.connect_stream('fixture-endpoint', timeout=3)
                selected, other = (pipe, unix) if platform == 'nt' else (unix, pipe)
                selected.assert_called_once_with('fixture-endpoint', 3)
                other.assert_not_called()
                self.assertIs(result, selected.return_value)

    def test_windows_and_linux_revalidate_each_pid_before_signalling(self):
        # GUI 11 disappears after the idle snapshot. Runtime 99 was never owned.
        # Only the still-present, explicitly verified runtime 12 can be stopped.
        app = DesktopApp(ROOT/'fixture-gui', ROOT)
        for platform in ('win32', 'linux'):
            native = Mock()
            with self.subTest(platform=platform), patch('bridge.clients.desktop_app.sys.platform', platform), \
                    patch('bridge.clients.desktop_app._native', return_value=native), \
                    patch.object(app, 'processes', side_effect=[[11, 12], [12], [12], []]) as read:
                app.stop(gui_pids=[11], runtime_pids=[99, 12])
                native.quit_process.assert_not_called()
                native.terminate.assert_called_once_with(12)
                self.assertEqual(read.call_count, 4)


if __name__ == '__main__':
    unittest.main()
