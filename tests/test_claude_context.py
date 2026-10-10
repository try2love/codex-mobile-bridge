"""Context counters only come from the existing desktop read API."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bridge.clients.claude.adapter import Claude
from bridge.clients.claude.model import session


class ClaudeContext(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.adapter = Claude(Path(self.temp.name)/'claude', auto_connect=False)
        self.adapter.desktop = SimpleNamespace(connected=True, capabilities={}, call=AsyncMock())

    async def detail(self, kind, methods, **context):
        raw = {'sessionId': 'fixture', 'turnRunning': False}
        row = session(kind, raw)
        self.adapter.index = {row['id']: row}
        self.adapter.desktop.capabilities = {kind: ['mobileDetail', *methods]}
        self.adapter.desktop.call.reset_mock()
        self.adapter.desktop.call.return_value = {'session': raw, 'transcript': [], **context}
        with patch.object(self.adapter, 'prepare') as prepare, \
             patch.object(self.adapter, 'connect') as connect, \
             patch.object(self.adapter, 'reconnect') as reconnect:
            result = await self.adapter.dispatch('detail', row['id'], {})
        self.adapter.desktop.call.assert_awaited_once_with(kind, 'mobileDetail', 'fixture')
        prepare.assert_not_called(); connect.assert_not_called(); reconnect.assert_not_called()
        return result

    async def test_native_context_and_status_survive_detail_translation(self):
        usage = {'usedTokens': 76543, 'contextWindow': 200000}
        result = await self.detail('code', ['getContextUsage'], contextUsage=usage, contextUsageStatus='available')
        self.assertEqual(result['session']['contextUsage'], usage)
        self.assertEqual(result['session']['contextUsageStatus'], 'available')
        self.assertTrue(result['capabilities']['contextUsage'])

    async def test_explicit_unavailable_and_unsupported_are_preserved(self):
        for kind, methods, status in [('code', ['getContextUsageSummary'], 'unavailable'),
                                      ('cowork', [], 'unsupported')]:
            with self.subTest(kind=kind):
                result = await self.detail(kind, methods, contextUsage=None, contextUsageStatus=status)
                self.assertIsNone(result['session']['contextUsage'])
                self.assertEqual(result['session']['contextUsageStatus'], status)
                self.assertEqual(result['capabilities']['contextUsage'], bool(methods))

    async def test_legacy_renderer_without_status_uses_existing_capabilities(self):
        for methods in (['getContextUsageSummary'], ['getContextUsage']):
            with self.subTest(methods=methods):
                result = await self.detail('code', methods, contextUsage=None)
                self.assertEqual(result['session']['contextUsageStatus'], 'unavailable')
                self.assertTrue(result['capabilities']['contextUsage'])
        result = await self.detail('cowork', [], contextUsage=None)
        self.assertEqual(result['session']['contextUsageStatus'], 'unsupported')

    async def test_legacy_renderer_counters_remain_available(self):
        usage = {'usedTokens': 0, 'contextWindow': 200000}
        result = await self.detail('code', ['getContextUsageSummary'], contextUsage=usage)
        self.assertEqual(result['session']['contextUsage'], usage)
        self.assertEqual(result['session']['contextUsageStatus'], 'available')

    async def test_unrecognized_status_is_not_published(self):
        result = await self.detail('code', ['getContextUsageSummary'], contextUsage=None, contextUsageStatus='private fixture')
        self.assertEqual(result['session']['contextUsageStatus'], 'unavailable')


if __name__ == '__main__':
    unittest.main()
