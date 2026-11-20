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

    async def test_desktop_last_prompt_is_not_current_context_when_summary_is_missing(self):
        event = {'type': 'assistant', 'message': {'id': 'step', 'model': 'desktop-model',
                 'usage': {'input_tokens': 10, 'cache_creation_input_tokens': 20,
                           'cache_read_input_tokens': 30}}}
        raw = {'sessionId': 'fixture', 'turnRunning': False}
        row = session('code', raw)
        self.adapter.index = {row['id']: row}
        self.adapter.desktop.capabilities = {'code': ['mobileDetail']}
        capacity = {'type': 'result', 'modelUsage': {'desktop-model': {'contextWindow': 200000}}}
        self.adapter.desktop.call.return_value = {'session': raw, 'transcript': [event, capacity],
                                                  'contextUsage': None, 'contextUsageStatus': 'unsupported'}
        with patch.object(self.adapter, 'prepare'), patch.object(self.adapter, 'connect'), \
             patch.object(self.adapter, 'reconnect'):
            result = await self.adapter.dispatch('detail', row['id'], {})
        self.assertIsNone(result['session']['contextUsage'])
        self.assertEqual(result['session']['lastPromptContextUsage']['usedTokens'], 60)
        self.assertEqual(result['session']['lastPromptContextUsage']['contextWindow'], 200000)
        self.assertEqual(result['session']['contextUsageStatus'], 'unsupported')
        self.assertFalse(result['capabilities']['contextUsage'])

    async def test_native_current_summary_wins_over_larger_last_request(self):
        raw = {'sessionId': 'fixture', 'turnRunning': False}
        row = session('code', raw)
        self.adapter.index = {row['id']: row}
        self.adapter.desktop.capabilities = {'code': ['mobileDetail', 'getContextUsageSummary']}
        self.adapter.desktop.call.return_value = {'session': raw,
            'transcript': [{'type': 'assistant', 'message': {'usage': {'input_tokens': 112973}}}],
            'contextUsage': {'usedTokens': 67500, 'contextWindow': 200000}, 'contextUsageStatus': 'available'}
        with patch.object(self.adapter, 'prepare'), patch.object(self.adapter, 'connect'), patch.object(self.adapter, 'reconnect'):
            result = await self.adapter.dispatch('detail', row['id'], {})
        self.assertEqual(result['session']['contextUsage']['usedTokens'], 67500)
        self.assertEqual(result['session']['lastPromptContextUsage']['usedTokens'], 112973)

    async def legacy_detail(self, transcript):
        # 无 mobileDetail 能力的旧版渲染器会分别调用 getSession 和 getTranscript；
        # 非分页返回值可能是列表容器字典，也可能是 null。
        raw = {'sessionId': 'fixture', 'turnRunning': False}
        row = session('code', raw)
        self.adapter.index = {row['id']: row}
        self.adapter.desktop.capabilities = {'code': []}
        self.adapter.desktop.call.side_effect = lambda kind, method, *args, **kwargs: (
            raw if method == 'getSession' else transcript)
        with patch.object(self.adapter, 'prepare'), patch.object(self.adapter, 'connect'), \
             patch.object(self.adapter, 'reconnect'):
            return await self.adapter.dispatch('detail', row['id'], {})

    async def test_container_transcript_keeps_last_prompt_reading(self):
        event = {'type': 'assistant', 'message': {'id': 'step', 'model': 'desktop-model',
                 'usage': {'input_tokens': 10, 'cache_read_input_tokens': 5},
                 'content': [{'type': 'text', 'text': 'ok'}]}}
        for key in ('messages', 'transcript'):
            with self.subTest(key=key):
                result = await self.legacy_detail({key: [event]})
                self.assertEqual(result['session']['lastPromptContextUsage']['usedTokens'], 15)
                self.assertEqual([m['role'] for m in result['messages']], ['assistant'])

    async def test_null_transcript_reports_unsupported_structure(self):
        # 应抛出与 model.transcript() 一致的 ValueError，而非未捕获的 TypeError（HTTP 500）。
        with self.assertRaisesRegex(ValueError, '不支持的数据结构'):
            await self.legacy_detail(None)


if __name__ == '__main__':
    unittest.main()
