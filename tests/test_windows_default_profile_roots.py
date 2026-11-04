"""Default Windows account roots cannot be registered as independent profiles."""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bridge.clients.instances import ClientInstances


ROOT = Path(__file__).resolve().parents[1]


class WindowsDefaultProfileRoots(unittest.TestCase):
    def test_classic_and_store_official_profiles_also_reserve_both_global_api_roots(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as directory:
            root = Path(directory).resolve()
            roaming, local = root/'Roaming', root/'Local'
            native_api, legacy_api = local/'Claude-3p', roaming/'Claude-3p'
            for path in (native_api, legacy_api):
                path.mkdir(parents=True)
            selected = {'classic': roaming/'Claude',
                        'store': local/'Packages/Claude_fixture/LocalCache/Roaming/Claude'}
            with patch.dict(os.environ, {'APPDATA': str(roaming), 'LOCALAPPDATA': str(local),
                                         'CLAUDE_CONFIG_DIR': str(root/'default-code')}), \
                    patch('bridge.clients.instances.sys', SimpleNamespace(platform='win32')), \
                    patch('bridge.clients.profiles.sys', SimpleNamespace(platform='win32')):
                for kind, active in selected.items():
                    with self.subTest(kind=kind):
                        active.mkdir(parents=True)
                        manager = SimpleNamespace(directory=root/kind/'gateway/desktop-sessions', bridge=None,
                            config={'discovered': {'claude': {'dataDirectory': str(active)}},
                                    'deepseekHome': str(root/'default-dsh')})
                        instances = ClientInstances(manager)
                        declaration = next(p for p in instances.defaults() if p.family=='claude' and p.kind=='default')
                        self.assertTrue({active, native_api, legacy_api}.issubset(set(declaration.roots.values())))
                        for occupied in (active, native_api, legacy_api):
                            with self.subTest(occupied=occupied), self.assertRaisesRegex(ValueError, '默认'):
                                instances.registry.register({'family': 'claude', 'kind': 'desktop-profile',
                                    'home': str(occupied), 'label': 'Must not claim a default account'})
                        self.assertEqual(instances.registry.list(), [])

    def test_sibling_directory_in_appdata_remains_available_for_a_real_independent_profile(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as directory:
            root = Path(directory).resolve()
            roaming, local = root/'Roaming', root/'Local'
            selected = roaming/'Claude'
            home = local/'Independent Claude'
            selected.mkdir(parents=True);home.mkdir(parents=True)
            manager = SimpleNamespace(directory=root/'gateway/desktop-sessions', bridge=None,
                config={'discovered': {'claude': {'dataDirectory': str(selected)}},
                        'deepseekHome': str(root/'default-dsh')})
            with patch.dict(os.environ, {'APPDATA': str(roaming), 'LOCALAPPDATA': str(local),
                                         'CLAUDE_CONFIG_DIR': str(root/'default-code')}), \
                    patch('bridge.clients.instances.sys', SimpleNamespace(platform='win32')), \
                    patch('bridge.clients.profiles.sys', SimpleNamespace(platform='win32')):
                instances = ClientInstances(manager)
                row = instances.registry.register({'family': 'claude', 'kind': 'desktop-profile',
                    'home': str(home), 'label': 'Independent Claude'})
                self.assertEqual(row['home'], str(home))
                self.assertFalse(row['enabled'])


if __name__ == '__main__':
    unittest.main()
