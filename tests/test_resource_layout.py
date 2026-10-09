"""Source-only layout contracts; never build helpers or contact a desktop/SSH host."""
import ast
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath

from bridge.api.assets import FONT_ROUTE, STATIC
from bridge.clients.codex.remote import RemoteCatalog
from bridge.features.auth import tls
from bridge.resources import project_root, source_text


ROOT = Path(__file__).resolve().parents[1]
# Public URLs from the pre-layout source snapshot (21ae09f). Physical directories
# may change. Only the two explicit host/layout entrypoints are added; physical
# directories and manifest entries must not become public URLs.
PUBLIC_URLS = set('''
/ /host.js /layout.js /permissions.js /downloads.js /downloads.css
/client-icons/codex.png /client-icons/claude.png /client-icons/deepseek.png
/client-navigation.js /client-navigation.css /desktop-sessions.js
/client-accounts.js /desktop-sessions.css /vendor/xterm/xterm.js
/vendor/xterm/addon-fit.js /vendor/xterm/xterm.css /command-terminal-panel.js
/list-sync.js /agents-panel.js /side-chat.js /terminal-panel.js /floating-panel.js
/git-panel.js /workbench.js /image-viewer.js /workbench.css /i18n.js /app.js
/modes.js /attachments.js /activity.js /fast-mode.js /accounts.js /account.js
/account.css /presentation.js /presentation.css /markdown.js /message-actions.js
/timeline.js /vendor/markdown-it.min.js /vendor/texmath.js
/vendor/katex/katex.min.js /vendor/katex/katex.min.css /style.css
/manifest.webmanifest /icon.png
'''.split())

SSH_SOURCES = {
    'bridge/features/sessions/store.py': ['SessionStore'],
    'bridge/clients/codex/catalog.py': ['Catalog'],
    'bridge/features/sessions/create.py': ['create_empty', 'fork_copy', 'rename_thread'],
    'bridge/features/accounts/models.py': ['model_ids'],
    'bridge/features/auth/tls.py': ['client_context'],
    'bridge/features/workspace/workspace.py': ['Workspace', 'operate'],
    'bridge/features/terminals/manager.py': ['CommandJob', 'serve_remote'],
    'bridge/platforms/posix/terminal.py': ['TerminalSession', 'remote_main'],
}
CONNECTORS = {'bridge/clients/claude/connector.js', 'bridge/clients/deepseek/host.mjs'}
NATIVE_SOURCES = {'bridge/platforms/macos/claude-helper.swift',
                  'bridge/platforms/windows/claude-helper.cs'}

ISOLATED_DEFINITIONS = r'''
import importlib.util, json, pathlib, shutil, sys
from unittest.mock import patch
assert importlib.util.find_spec('bridge') is None, 'Local bridge package leaked into isolated interpreter'
def guard(event, args):
    if event in ('subprocess.Popen', 'os.system', 'os.exec', 'os.posix_spawn', 'socket.connect', 'socket.bind'):
        raise AssertionError('Payload attempted an operation: ' + event)
    if event == 'import' and (args[0] == 'bridge' or args[0].startswith('bridge.')):
        raise AssertionError('Payload imported the local package: ' + args[0])
sys.addaudithook(guard)
results = []
with patch.object(pathlib.Path, 'home', return_value=pathlib.Path.cwd()), patch.object(shutil, 'which', return_value='/fixture/codex'):
    for case in json.load(sys.stdin):
        namespace = {'__name__': 'isolated_payload', '__file__': str(pathlib.Path.cwd()/'payload.py')}
        exec(case['source'], namespace)
        assert all(name in namespace for name in case['symbols']), case['name']
        assert not any(name == 'bridge' or name.startswith('bridge.') for name in sys.modules)
        results.append(case['name'])
print(json.dumps(results))
'''


def path_expression(node, names):
    """Evaluate only constant path concatenations, never calls or build code."""
    if isinstance(node, ast.Name):
        return names[node.id]
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return path_expression(node.left, names) / path_expression(node.right, names)
    raise AssertionError('Build resource path is not a static path expression')


class ResourceLayoutTests(unittest.TestCase):
    def test_static_urls_stay_fixed_and_resolve_inside_web(self):
        self.assertEqual(set(STATIC), PUBLIC_URLS)
        layout = json.loads((ROOT/'web/assets.json').read_text())
        web = (ROOT/'web').resolve()
        for url, (relative, mime) in STATIC.items():
            with self.subTest(url=url):
                configured = layout.get(url, relative)
                self.assertEqual(relative, tuple(configured) if isinstance(configured, list) else configured)
                for item in (relative,) if isinstance(relative, str) else relative:
                    target = (web/item).resolve()
                    target.relative_to(web)
                    self.assertTrue(target.is_file(), item)
                self.assertTrue(mime)
        # Directory relocation and manifest metadata do not become HTTP routes.
        for url in ('/index.html', '/assets.json', '/shell/app.js',
                    '/features/chat/permissions.js', '/../package.json',
                    '/vendor/katex/fonts/../../package.json', '/vendor/katex/fonts/KaTeX_Main.js'):
            self.assertNotIn(url, STATIC)
            self.assertIsNone(FONT_ROUTE.fullmatch(url))
        fonts = ROOT/'web/vendor/katex/fonts'
        for path in fonts.iterdir():
            if path.suffix in ('.woff', '.woff2', '.ttf'):
                self.assertIsNotNone(FONT_ROUTE.fullmatch('/vendor/katex/fonts/'+path.name))

    def test_gateway_manifest_contains_all_remote_sources_and_client_assets(self):
        manifest = json.loads((ROOT/'scripts/gateway-resources.json').read_text())
        self.assertEqual(len(manifest), len(set(manifest)))
        self.assertEqual({name for name in manifest if name.endswith('.py')}, set(SSH_SOURCES))
        self.assertTrue(CONNECTORS <= set(manifest))
        self.assertIn('bridge/clients/LICENSE.coding-mobile', manifest)
        for relative in manifest:
            with self.subTest(resource=relative):
                path = PurePosixPath(relative)
                self.assertFalse(path.is_absolute())
                self.assertNotIn('..', path.parts)
                self.assertTrue((ROOT/relative).is_file())
        self.assertEqual(project_root(), ROOT)

    def test_all_eight_ssh_sources_define_without_an_installed_bridge_package(self):
        # The real catalog builder combines TLS/model/catalog definitions and
        # removes package imports. Test its output, not a duplicate assembler.
        combined = {'bridge/features/auth/tls.py', 'bridge/features/accounts/models.py',
                    'bridge/clients/codex/catalog.py'}
        source = RemoteCatalog('fixture-host')._source()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0)
                self.assertFalse((node.module or '').startswith('bridge'))
            elif isinstance(node, ast.Import):
                self.assertFalse(any(name.name.startswith('bridge') for name in node.names))
        cases = [{'name': 'catalog-with-models-and-tls', 'source': source,
                  'symbols': [symbol for name in sorted(combined) for symbol in SSH_SOURCES[name]]}]
        for relative, symbols in SSH_SOURCES.items():
            if relative not in combined:
                cases.append({'name': relative, 'source': source_text(relative.removeprefix('bridge/')),
                              'symbols': symbols})
        (ROOT/'.tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
            result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', ISOLATED_DEFINITIONS],
                                    input=json.dumps(cases), cwd=folder, text=True,
                                    capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertEqual(json.loads(result.stdout), [case['name'] for case in cases])

    def test_client_templates_and_native_sources_are_at_platform_paths(self):
        from bridge.clients.claude import adapter as claude
        from bridge.clients.deepseek import adapter as deepseek
        from bridge.clients.deepseek import setup as deepseek_setup
        self.assertEqual(Path(claude.__file__).with_name('connector.js'), ROOT/'bridge/clients/claude/connector.js')
        self.assertEqual(Path(deepseek.__file__).with_name('host.mjs'), ROOT/'bridge/clients/deepseek/host.mjs')
        self.assertEqual(Path(deepseek_setup.__file__).with_name('host.mjs'), ROOT/'bridge/clients/deepseek/host.mjs')
        for relative in CONNECTORS | NATIVE_SOURCES:
            with self.subTest(resource=relative):
                self.assertTrue((ROOT/relative).is_file())
                self.assertGreater((ROOT/relative).stat().st_size, 0)

    def test_build_manifest_certificate_and_helper_paths_match_runtime_layout(self):
        build = ast.parse((ROOT/'scripts/build-desktop.py').read_text())
        constants = {node.value for node in ast.walk(build) if isinstance(node, ast.Constant) and isinstance(node.value, str)}
        self.assertIn('scripts/gateway-resources.json', constants)
        destinations = []
        for node in ast.walk(build):
            if (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add)
                    and isinstance(node.left, ast.Call) and ast.unparse(node.left.func) == 'certifi.where'):
                destinations.append(ast.literal_eval(node.right))
        self.assertEqual(destinations, [':'+tls.BUNDLED_CA.parent.relative_to(ROOT).as_posix()])
        helper = ast.parse((ROOT/'scripts/build-client-helpers.py').read_text())
        source_assignment = next(node for node in ast.walk(helper) if isinstance(node, ast.Assign)
                                 and any(isinstance(target, ast.Name) and target.id == 'source' for target in node.targets))
        names = {'root': ROOT}
        names['source'] = path_expression(source_assignment.value, names)
        sources = set()
        for node in ast.walk(helper):
            if (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)
                    and isinstance(node.right, ast.Constant) and isinstance(node.right.value, str)
                    and node.right.value.endswith(('.swift', '.cs'))):
                sources.add(path_expression(node, names).relative_to(ROOT).as_posix())
        self.assertEqual(sources, NATIVE_SOURCES)


if __name__ == '__main__':
    unittest.main()
