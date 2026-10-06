"""Regression coverage for the local v1.4 integration."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from bridge.catalog import Catalog
from bridge.remote import RemoteCatalog

ROOT = Path(__file__).resolve().parents[1]


class CatalogIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.reader = Catalog(self.home, 'fixture-runtime')

    def test_complete_remote_helper_constructs_reader_without_background_threads(self):
        source = RemoteCatalog('fixture')._source()
        output = io.StringIO()
        with patch.dict(os.environ, {'CODEX_HOME': str(self.home)}), contextlib.redirect_stdout(output):
            namespace = {'__file__': '<remote-helper>'}
            exec(compile(source, '<remote-helper>', 'exec'), namespace)
        self.assertFalse(namespace['reader'].allow_background_refresh)

    def test_split_models_preserve_native_fast_metadata(self):
        self.reader._fetch = lambda *args: {
            'models': [{'model': 'native', 'serviceTiers': [{'id': 'priority'}]}],
            'modelSource': 'codex', 'fastMode': {'allowed': True, 'defaultServiceTier': 'priority'}}
        value = self.reader.get_kind('models', self.home)
        self.assertEqual(value['fastMode'], {'allowed': True, 'defaultServiceTier': 'priority'})
        self.assertEqual(value['models'][0]['fastTier'], 'priority')

    def test_split_runtime_request_reads_fast_capability_without_skills(self):
        auth = {'account': {'type': 'chatgpt'}, 'requiresOpenaiAuth': True}
        config = {'config': {'service_tier': 'priority'}}
        replies = [{}, config, auth, {'data': [{'model': 'native', 'serviceTiers': [{'id': 'priority'}]}]},
                   config, auth, {'requirements': {}}]
        process = Mock(stdin=io.StringIO(), stdout=io.StringIO(''.join(
            json.dumps({'id': i, 'result': value}) + '\n' for i, value in enumerate(replies, 1))))
        writes = []
        process.stdin.write = lambda value: writes.append(value)
        with patch('bridge.catalog.subprocess.Popen', return_value=process):
            value = self.reader.get_kind('models', self.home)
        self.assertTrue(value['fastMode']['allowed'])
        methods = [json.loads(line)['method'] for line in writes]
        self.assertIn('configRequirements/read', methods)
        self.assertNotIn('skills/list', methods)

    def test_cached_skill_replaced_by_outside_symlink_is_rejected(self):
        workspace = self.root / 'workspace'
        workspace.mkdir()
        skill = workspace / 'SKILL.md'
        skill.write_text('fixture skill')
        self.reader._fetch = lambda *args: {'skillEntries': [{'skills': [
            {'name': 'fixture', 'path': str(skill)}]}]}
        result = self.reader.get_kind('skills', workspace)
        identifier = result['skills'][0]['id']
        outside = self.root / 'outside.md'
        skill.rename(outside)
        try:
            skill.symlink_to(outside)
        except OSError as exc:
            self.skipTest('symlinks unavailable: ' + str(exc))
        with self.assertRaisesRegex(ValueError, 'Skill.*(授权目录|链接目标已变化)'):
            self.reader.validate_skills(workspace, [identifier])

    def test_runtime_discovered_external_skill_links_load_and_can_be_selected(self):
        installed = self.root / 'installed'
        installed.mkdir()
        target = installed / 'SKILL.md'
        target.write_text('installed skill')
        links = self.home / 'skills'
        links.mkdir()
        (links / 'fixture').symlink_to(installed, target_is_directory=True)
        # Native skills/list may return either the link or the resolved filename.
        for path in (links / 'fixture' / 'SKILL.md', target):
            self.reader._fetch = Mock(return_value={'skillEntries': [{'skills': [
                {'name': 'fixture', 'path': str(path)}]}]})
            value = self.reader.get_kind('skills', self.home, refresh=True)
            identifier = value['skills'][0]['id']
            self.assertEqual(self.reader.validate_skills(self.home, [identifier])[0]['path'], str(target))
            self.reader._fetch.assert_called_once()
            self.reader.get_kind('skills', self.home)
            self.reader._fetch.assert_called_once()

    def test_one_broken_link_does_not_block_other_installed_skills(self):
        links = self.home / 'skills'
        links.mkdir()
        (links / 'missing').symlink_to(self.root / 'missing')
        path = self.home / 'SKILL.md'
        path.write_text('working skill')
        self.reader._fetch = Mock(return_value={'skillEntries': [{'skills': [
            {'name': 'working', 'path': str(path)}]}]})
        self.assertEqual(self.reader.get_kind('skills', self.home)['skills'][0]['name'], 'working')
