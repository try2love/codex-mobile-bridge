"""Visualization references, source boundaries, HTTP isolation and relay policy."""
import http.client
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_bridge as support
from bridge.app.service import Bridge, LiveSession
from bridge.clients.codex.remote import RemoteStore
from bridge.features.workspace.files import visualization_files, visualization_paths
from bridge.features.workspace.visualize import MARKER, END, identifier, read_source
from bridge.api.visualize import SHELL_CSP, shell_bytes
from relay.protocol import validate


def reference(path, **options):
    return MARKER + json.dumps({'path': str(path), **options}) + END


def state(text, cwd=None, kind='agentMessage'):
    return {'cwd': str(cwd) if cwd else None, 'turns': [{'items': [{'type': kind, 'text': text}]}]}


class VisualizeSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=support.ROOT / '.tmp')
        self.base = Path(self.temp.name).resolve()
        self.project = self.base / 'project'; self.project.mkdir()
        self.home = self.base / 'home'; (self.home / 'visualizations').mkdir(parents=True)
        self.file = self.home / 'visualizations' / 'slider.html'; self.file.write_text('<input type="range">')

    def tearDown(self): self.temp.cleanup()

    def test_only_complete_assistant_references_outside_code_grant_ids(self):
        ref = reference(self.file, mode='wide', title='滑块')
        paths = visualization_paths([ref])
        self.assertEqual(paths, {str(self.file)})
        rows = visualization_files(state(ref, self.project), [])
        self.assertEqual(rows[0]['id'], identifier(str(self.file)))
        self.assertTrue(rows[0]['visualization'])
        for source in ('```\n' + ref + '\n```', '`' + ref + '`', '\\' + ref, '    ' + ref,
                       ref[:-1], reference('relative.html'), reference('/etc/passwd'), reference(self.file, mode='invalid'),
                       reference(self.file, title='x' * 201)):
            self.assertEqual(visualization_paths([source]), set(), source)
        self.assertEqual(visualization_files(state(ref, self.project, 'userMessage'), []), [])

    def test_source_reads_authorized_roots_with_size_and_symlink_checks(self):
        result = read_source(str(self.file), str(self.project), str(self.home))
        self.assertEqual(result['text'], '<input type="range">')
        self.assertEqual(len(result['revision']), 64)
        project_file = self.project / 'test.html'; project_file.write_text('<b>project</b>')
        self.assertEqual(read_source(str(project_file), str(self.project), str(self.home))['text'], '<b>project</b>')
        outside = self.base / 'private.html'; outside.write_text('private')
        for target in (outside, self.project / '../private.html'):
            with self.assertRaises(PermissionError): read_source(str(target), str(self.project), str(self.home))
        link = self.project / 'link.html'; link.symlink_to(outside)
        with self.assertRaises(PermissionError): read_source(str(link), str(self.project), str(self.home))
        inside_link = self.project / 'inside.html'; inside_link.symlink_to(project_file)
        with self.assertRaises((PermissionError, OSError, ValueError)): read_source(str(inside_link), str(self.project), str(self.home))
        project_file.write_bytes(b'x' * (1024 * 1024 + 1))
        with self.assertRaises(ValueError): read_source(str(project_file), str(self.project), str(self.home))
        project_file.write_bytes(b'\xff')
        with self.assertRaises(UnicodeError): read_source(str(project_file), str(self.project), str(self.home))

    def test_service_rechecks_chat_reference_without_attaching_and_ssh_runs_standalone(self):
        source = state(reference(self.file), self.project)
        session = LiveSession(support.THREAD); session.state = source; session.connected = True
        attached = []
        bridge = SimpleNamespace(local=True, store=SimpleNamespace(home=self.home),
                                 session=lambda thread, attach: attached.append(attach) or session)
        self.assertEqual(Bridge.visualization(bridge, support.THREAD, identifier(str(self.file)))['text'], '<input type="range">')
        self.assertEqual(attached, [False])
        with self.assertRaises(PermissionError): Bridge.visualization(bridge, support.THREAD, 'a' * 64)
        project_file = self.project / "远端 '图表'.html"; project_file.write_text('remote fixture')
        with session.condition:
            session.state = state(reference(project_file), self.project)
            session.changed()
        bridge.local = False; bridge.host = 'remote:fixture'; bridge.store = RemoteStore('fixture')
        def ssh(alias, payload, **kwargs):
            self.assertEqual(alias, 'fixture')
            result = subprocess.run([sys.executable, '-I', '-S', '-B', '-X', 'utf8', '-'],
                                    input=payload, capture_output=True, encoding='utf-8', cwd=self.base)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        with patch('bridge.app.service.ssh_read', side_effect=ssh):
            self.assertEqual(Bridge.visualization(bridge, support.THREAD, identifier(str(project_file)))['text'], 'remote fixture')
            project_file.write_bytes(b'\xff')
            with self.assertRaises(ValueError):
                Bridge.visualization(bridge, support.THREAD, identifier(str(project_file)))

    def test_parent_components_cannot_change_the_authorized_symlink_target(self):
        outside = self.base / 'outside'; outside.mkdir()
        (self.project / 'sub').mkdir()
        (self.project / 'secret.html').write_text('authorized')
        (outside / 'secret.html').write_text('outside project')
        link = outside / 'link'; link.symlink_to(self.project / 'sub')
        path = str(link / '..' / 'secret.html')
        with self.assertRaises((PermissionError, ValueError)):
            read_source(path, str(self.project), str(self.home))

    def test_saved_prefix_is_readable_until_a_complete_snapshot_removes_it(self):
        saved = {'cwd': str(self.project), 'turns': [
            {'turnId': 'older', 'items': [{'id': 'viz', 'type': 'agentMessage', 'text': reference(self.file)}]},
            {'turnId': 'current', 'items': [{'id': 'now', 'type': 'agentMessage', 'text': 'current'}]},
        ]}
        session = LiveSession(support.THREAD)
        with session.condition:
            session.set_history(saved)
            session.state = {'cwd': str(self.project), 'turns': saved['turns'][1:], 'turnsPagination': {'hasLoadedOldest': False}}
            session.connected = True
            session.changed()
        bridge = SimpleNamespace(local=True, store=SimpleNamespace(home=self.home), session=lambda *args, **kwargs: session)
        self.assertIn('range', Bridge.visualization(bridge, support.THREAD, identifier(str(self.file)))['text'])
        with session.condition:
            session.state['turnsPagination']['hasLoadedOldest'] = True
            session.changed()
        with self.assertRaises(PermissionError): Bridge.visualization(bridge, support.THREAD, identifier(str(self.file)))

    def test_source_authorization_is_independent_of_the_presentation_window(self):
        older = {'turnId': 'older', 'items': [{'id': 'viz', 'type': 'agentMessage', 'text': reference(self.file)}]}
        current = {'turnId': 'current', 'items': [{'id': 'now', 'type': 'agentMessage', 'text': 'current'}]}
        session = LiveSession(support.THREAD)
        session.timeline_limit = 1
        session.state = {'cwd': str(self.project), 'turns': [older, current]}
        session.connected = True
        self.assertEqual(len(session.timeline_view()['turns']), 1)
        bridge = SimpleNamespace(local=True, store=SimpleNamespace(home=self.home), session=lambda *args, **kwargs: session)
        self.assertIn('range', Bridge.visualization(bridge, support.THREAD, identifier(str(self.file)))['text'])
        with session.condition:
            session.state['turns'] = [current]
            session.changed()
        with self.assertRaises(PermissionError):
            Bridge.visualization(bridge, support.THREAD, identifier(str(self.file)))


class VisualizeHttpTests(unittest.TestCase):
    setUpClass = classmethod(support.HttpTests.setUpClass.__func__)
    setUp = support.HttpTests.setUp
    tearDown = support.HttpTests.tearDown
    request = support.HttpTests.request
    login = support.HttpTests.login

    def raw(self, path, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        try:
            conn.request('GET', path, headers=headers or {})
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally: conn.close()

    def test_authenticated_fixed_shell_and_json_source(self):
        url = '/api/sessions/' + support.THREAD + '/visualizations/' + 'a' * 64
        self.assertEqual(self.raw('/visualize-shell')[0], 401)
        self.assertEqual(self.raw(url)[0], 401)
        login = self.login()
        status, headers, body = self.raw('/visualize-shell', login)
        self.assertEqual(status, 200)
        self.assertEqual(body, shell_bytes(support.ROOT / 'web'))
        self.assertEqual(headers['Content-Security-Policy'], SHELL_CSP)
        self.assertIn("connect-src 'none'", SHELL_CSP)
        self.assertNotIn('allow-same-origin', SHELL_CSP)
        self.assertEqual(self.raw('/visualize-shell?path=private', login)[0], 405)
        status, headers, _ = self.raw('/', login)
        self.assertIn(self.origin + '/visualize-shell', headers['Content-Security-Policy'])
        self.assertIn("script-src 'self';", headers['Content-Security-Policy'])
        calls = []
        self.server.bridge.visualization = lambda thread, key: calls.append((thread, key)) or {'text': '<script>1</script>', 'revision': 'b' * 64}
        status, headers, body = self.raw(url, login)
        self.assertEqual(status, 200)
        self.assertTrue(headers['Content-Type'].startswith('application/json'))
        self.assertEqual(json.loads(body)['text'], '<script>1</script>')
        self.assertEqual(calls, [(support.THREAD, 'a' * 64)])
        validate('GET', url)
        with self.assertRaises(PermissionError): validate('POST', url)


class VisualizeRelayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        try:
            from aiohttp.test_utils import TestClient, TestServer
            from relay.server import create_app, STATE
        except ModuleNotFoundError as error:
            raise unittest.SkipTest(str(error)) from error
        self.temp = tempfile.TemporaryDirectory(dir=support.ROOT / '.tmp')
        self.app = create_app(Path(self.temp.name) / 'relay', 'http://127.0.0.1:1', test_http=True)
        self.server = TestServer(self.app); await self.server.start_server()
        self.client = TestClient(self.server); await self.client.start_server()
        self.state = self.app[STATE]
        self.state.origin = str(self.server.make_url('')).rstrip('/')
        self.state.host = self.state.origin.removeprefix('http://')

    async def asyncTearDown(self):
        await self.client.close(); self.state.registry.close(); self.temp.cleanup()

    async def test_scoped_shell_requires_phone_and_retains_its_policy(self):
        self.assertEqual((await self.client.get('/visualize-shell')).status, 401)
        self.state.phone_auth = lambda _: {'id': 'fixture'}
        for prefix in ('', '/d/' + 'a' * 32):
            response = await self.client.get(prefix + '/visualize-shell')
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers['Content-Security-Policy'], SHELL_CSP)
            self.assertEqual(await response.read(), shell_bytes(support.ROOT / 'web'))
            page = await self.client.get(prefix + '/')
            self.assertIn(self.state.origin + prefix + '/visualize-shell', page.headers['Content-Security-Policy'])
            self.assertEqual((await self.client.get(prefix + '/visualize-shell?text=x')).status, 404)
