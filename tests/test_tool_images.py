import base64
import copy
import json
import hashlib
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from bridge.features.sessions.model import normalize_item, normalize_state
from bridge.features.sessions.timeline import Timeline, PAGE_BYTES, encoded
from bridge.app.service import Bridge, LiveSession
from bridge.features.sessions import tool_images

PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jR7kAAAAASUVORK5CYII='


def image(data=PNG, mime='image/png'):
    return {'type': 'image', 'data': data, 'mimeType': mime}


def result():
    return {'content': [{'type': 'text', 'text': json.dumps({'content': [
        {'type': 'text', 'text': 'Screenshot ready'}, image()]})}]}


class ToolImageTests(unittest.TestCase):
    def test_previewing_twenty_images_does_not_decode_the_whole_history_per_image(self):
        session = LiveSession('images', image_cache=tool_images.ToolImageCache())
        data = [base64.b64decode(PNG) + bytes([i]) for i in range(20)]
        session.state = {'turns': [{'turnId': 'turn', 'items': [
            {'type': 'mcpToolCall', 'id': 'shots', 'result': {'content': [
                image(base64.b64encode(raw).decode()) for raw in data]}}]}]}
        bridge = Bridge.__new__(Bridge)
        bridge.session = lambda *args, **kwargs: session
        with patch.object(base64, 'b64decode', wraps=base64.b64decode) as decode:
            session.view()
            with patch.object(session, 'view', wraps=session.view) as view:
                for raw in data:
                    preview, _ = bridge.desktop_image_preview('images', hashlib.sha256(raw).hexdigest())
                    self.assertEqual(preview['previewBytes'], raw)
                view.assert_not_called()
            for i in range(3):
                with session.condition:
                    session.state['turns'][0]['items'].append({'type': 'agentMessage', 'text': str(i)})
                    session.changed()
                session.view()
            self.assertEqual(decode.call_count, 20)

    def test_nested_computer_use_output_retains_text_and_extracts_image_without_mutation(self):
        item = {'type': 'mcpToolCall', 'id': 'shot', 'server': 'cua_repl', 'tool': 'js', 'result': result()}
        original = copy.deepcopy(item)
        row = normalize_item(item)
        self.assertEqual(item, original)
        self.assertNotIn(PNG, row['output'])
        self.assertIn('Screenshot ready', row['output'])
        self.assertEqual(row['attachments'][0]['type'], 'toolImage')
        self.assertEqual(row['attachments'][0]['data'], PNG)

    def test_saved_and_dynamic_results_have_same_images(self):
        live = normalize_item({'type': 'dynamicToolCall', 'contentItems': [image()]})
        state = normalize_state({'turns': [{'turnId': 'turn', 'items': [
            {'type': 'function_call', 'call_id': 'call', 'name': 'mcp__cua_repl__js'},
            {'type': 'function_call_output', 'call_id': 'call', 'output': json.dumps(result())}]}]})
        saved = state['turns'][0]['messages'][0]
        self.assertEqual(live['attachments'], saved['attachments'])
        self.assertNotIn(PNG, saved['output'])

    def test_codex_native_image_content_items_are_extracted(self):
        url = 'data:image/png;base64,' + PNG
        state = normalize_state({'turns': [{'turnId': 'turn', 'items': [
            {'type': 'function_call', 'call_id': 'call', 'name': 'view_image'},
            {'type': 'function_call_output', 'call_id': 'call', 'output': [
                {'type': 'input_text', 'text': 'Screenshot ready'},
                {'type': 'input_image', 'image_url': url, 'detail': 'auto'}]}]}]})
        saved = state['turns'][0]['messages'][0]
        live = normalize_item({'type': 'dynamicToolCall', 'tool': 'screenshot', 'contentItems': [
            {'type': 'inputText', 'text': 'Screenshot ready'}, {'type': 'inputImage', 'imageUrl': url}]})
        for row in (saved, live):
            with self.subTest(kind=row['kind']):
                self.assertEqual([a['type'] for a in row['attachments']], ['toolImage'])
                self.assertEqual(row['attachments'][0]['desktopId'], hashlib.sha256(base64.b64decode(PNG)).hexdigest())
                self.assertNotIn(PNG, row['output'])
                self.assertIn('Screenshot ready', row['output'])

    def test_timeline_accepts_null_attachments(self):
        timeline = Timeline()
        timeline.update({'sequence': 1, 'turns': [{'id': 'turn', 'messages': [
            {'id': 'reply', 'role': 'assistant', 'kind': 'agentMessage', 'text': 'done', 'attachments': None}]}]})
        self.assertNotIn('attachments', timeline.page()['rows'][0])

    def test_ordinary_tool_text_and_arguments_are_not_interpreted_as_images(self):
        row = normalize_item({'type': 'mcpToolCall', 'arguments': result(), 'result': 'ordinary output'})
        self.assertNotIn('attachments', row)
        self.assertIn(PNG, row['text'])
        self.assertEqual(json.loads(row['output']), 'ordinary output')

    def test_replaced_tool_output_does_not_keep_stale_images(self):
        state = normalize_state({'turns': [{'turnId': 'turn', 'items': [
            {'type': 'custom_tool_call', 'call_id': 'call', 'name': 'screenshot'},
            {'type': 'custom_tool_call_output', 'call_id': 'call', 'output': result()},
            {'type': 'custom_tool_call_output', 'call_id': 'call', 'output': 'replaced'}]}]})
        row = state['turns'][0]['messages'][0]
        self.assertEqual(row['output'], 'replaced')
        self.assertNotIn('attachments', row)

    def test_only_bounded_inline_raster_images_are_accepted(self):
        for value in [image('invalid!'), image(PNG, 'image/jpeg'), image(PNG, {}),
                      image(base64.b64encode(b'<svg/>').decode(), 'image/svg+xml'),
                      {'type': 'image', 'imageUrl': 'https://example.com/private.png'},
                      image('A' * (12 * 1024 * 1024))]:
            with self.subTest(value=str(value)[:100]):
                self.assertFalse(normalize_item({'type': 'mcpToolCall', 'result': {'content': [value]}}).get('attachments'))
        row = normalize_item({'type': 'dynamicToolCall', 'contentItems': [
            {'type': 'image', 'imageUrl': 'data:image/png;base64,' + PNG}]})
        self.assertEqual(row['attachments'][0]['data'], PNG)

    def test_duplicate_images_and_extraction_budgets(self):
        from bridge.features.sessions.tool_images import extract_tool_images
        _, images = extract_tool_images({'content': [image(), image()]})
        self.assertEqual(len(images), 1)
        second = image(base64.b64encode(base64.b64decode(PNG) + b'other').decode())
        with patch('bridge.features.sessions.tool_images.MAX_IMAGES', 1):
            clean, images = extract_tool_images({'content': [image(), second]})
            self.assertEqual(len(images), 1)
            self.assertNotIn(second['data'], json.dumps(clean))
        with patch('bridge.features.sessions.tool_images.MAX_TOTAL_BYTES', 1):
            self.assertEqual(extract_tool_images({'content': [image()]})[1], [])
        nested = {'content': [image()]}
        for _ in range(12):
            nested = {'content': [nested]}
        self.assertEqual(extract_tool_images(nested)[1], [])

    def test_unscanned_bulk_past_budgets_is_omitted_and_small_values_are_kept(self):
        from bridge.features.sessions.tool_images import extract_tool_images, MAX_UNSCANNED_CHARS
        bulk = 'A' * (MAX_UNSCANNED_CHARS + 1)
        def nested(value, levels=12):
            for _ in range(levels):
                value = {'content': [value]}
            return value
        omitted = {'type': 'omitted', 'reason': 'toolOutputBudget'}
        cases = {'depth': (nested(image(bulk)), {'MAX_DEPTH': tool_images.MAX_DEPTH}),
                 'nodes': ({'content': [{'type': 'text', 'text': 'x'}] * 5 + [image(bulk)]},
                           {'MAX_NODES': 4}),
                 'json': ({'content': [{'type': 'text', 'text': json.dumps({'content': [image(bulk)]})}]},
                          {'MAX_JSON_CHARS': 64})}
        for name, (output, limits) in cases.items():
            with self.subTest(name), patch.multiple(tool_images, **limits):
                original = copy.deepcopy(output)
                clean, images = extract_tool_images(output)
                self.assertEqual(output, original)
                self.assertEqual(images, [])
                encoded_clean = json.dumps(clean)
                self.assertNotIn(bulk, encoded_clean)
                self.assertIn(json.dumps(omitted)[1:-1], encoded_clean)
        small = nested({'type': 'text', 'text': 'deep but small'})
        self.assertEqual(extract_tool_images(small), (small, []))

    def test_unscanned_allowance_is_shared_by_all_small_values_in_one_output(self):
        from bridge.features.sessions.tool_images import extract_tool_images, MAX_NODES, MAX_UNSCANNED_CHARS
        # Production limits: one-node fillers exhaust the scan budget, then blocks
        # are each under the allowance but far above it together.
        block = base64.b64encode(b'\x89PNG' + bytes(7500)).decode()
        blocks = [image(block + str(i)) for i in range(512)]
        self.assertLess(len(json.dumps(blocks[0])), MAX_UNSCANNED_CHARS)
        output = {'content': [0] * MAX_NODES + blocks}
        clean, images = extract_tool_images(output)
        self.assertEqual(images, [])
        content = clean['content']
        kept = [part for part in content if isinstance(part, dict) and part.get('type') == 'image']
        self.assertTrue(kept)
        self.assertLessEqual(sum(len(json.dumps(part, ensure_ascii=False)) for part in kept), MAX_UNSCANNED_CHARS)
        self.assertEqual(content[-1]['type'], 'omitted')
        self.assertEqual(len(content) - 1 + content[-1]['items'], len(output['content']))
        self.assertLess(len(json.dumps(clean)), MAX_NODES * 3 + MAX_UNSCANNED_CHARS + 1024)
        # After the allowance is spent, remaining short items collapse to one marker.
        tiny = {'content': [0] * MAX_NODES + blocks[:3] + [{'type': 'text', 'text': 'y'}] * 10000}
        self.assertLess(len(json.dumps(extract_tool_images(tiny)[0])), len(json.dumps(tiny)) // 10)

    def test_public_view_omits_unscanned_bulk_but_keeps_session_record(self):
        bulk = 'A' * (tool_images.MAX_UNSCANNED_CHARS + 1)
        value = image(bulk)
        for _ in range(12):
            value = {'content': [value]}
        session = LiveSession('deep', image_cache=tool_images.ToolImageCache())
        session.state = {'turns': [{'turnId': 'turn', 'items': [
            {'type': 'mcpToolCall', 'id': 'deep', 'result': value}]}]}
        self.assertNotIn(bulk, json.dumps(session.view()))
        self.assertIn(bulk, json.dumps(session.state))

    def test_timeline_keeps_image_bytes_out_of_pages_and_changes_image_version(self):
        timeline = Timeline()
        def view(data, sequence):
            row = normalize_item({'type': 'mcpToolCall', 'id': 'shot', 'result': {'content': [image(data)]}})
            return {'sequence': sequence, 'turns': [{'id': 'turn', 'messages': [row]}]}
        timeline.update(view(PNG, 1))
        page = timeline.page()
        row = page['rows'][0]
        attachment = row['attachments'][0]
        self.assertEqual(attachment['type'], 'toolImage')
        self.assertEqual(len(attachment['desktopId']), 64)
        self.assertNotIn(PNG.encode(), encoded(page))
        self.assertLess(len(encoded(page)), PAGE_BYTES)
        data = base64.b64encode(base64.b64decode(PNG) + b'fixture').decode()
        timeline.update(view(data, 2))
        changed = timeline.changes(1, page['epoch'], page['before'])['rows']
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0]['key'], row['key'])
        self.assertNotEqual(changed[0]['version'], row['version'])


class ToolImageCacheTests(unittest.TestCase):
    def setUp(self):
        self.cache = tool_images.ToolImageCache()
        self.session = LiveSession('images', image_cache=self.cache)
        self.item = {'type': 'mcpToolCall', 'id': 'shot', 'result': result()}
        self.session.state = {'turns': [{'turnId': 'turn', 'items': [self.item]}]}
        self.bridge = Bridge.__new__(Bridge)
        self.sessions = {'images': self.session, 'other': LiveSession('other', image_cache=self.cache)}
        self.bridge.session = lambda thread, **kwargs: self.sessions[thread]
        self.raw = base64.b64decode(PNG)
        self.identifier = hashlib.sha256(self.raw).hexdigest()

    def preview(self, thread='images'):
        return self.bridge.desktop_image_preview(thread, self.identifier)[0]['previewBytes']

    def test_output_changes_in_place_and_removed_images_invalidate_membership(self):
        self.assertEqual(self.preview(), self.raw)
        with self.session.condition:
            self.item['result']['content'][:] = [image(base64.b64encode(self.raw + b'new').decode())]
            self.session.changed()
        with self.assertRaises(KeyError):
            self.preview()
        new_id = hashlib.sha256(self.raw + b'new').hexdigest()
        self.assertEqual(self.bridge.desktop_image_preview('images', new_id)[0]['previewBytes'], self.raw + b'new')
        with self.session.condition:
            self.item['result'] = 'removed'
            self.session.changed()
        with self.assertRaises(KeyError):
            self.bridge.desktop_image_preview('images', new_id)

    def test_shared_cache_does_not_grant_other_threads_access_or_accept_forged_descriptors(self):
        self.assertEqual(self.preview(), self.raw)
        with self.assertRaises(KeyError):
            self.preview('other')
        other = self.sessions['other']
        with other.condition:
            other.state = {'turns': [{'turnId': 'turn', 'items': [
                {'type': 'userMessage', 'content': [{'type': 'toolImage', 'desktopId': self.identifier,
                  'mime': 'image/png', 'data': 'invalid', 'name': 'forged.png'}]}]}]}
            other.changed()
        with self.assertRaises(KeyError):
            self.preview('other')

    def test_saved_history_prefix_is_indexed_with_live_tail(self):
        with self.session.condition:
            self.session.set_history(self.session.state)
            self.session.connected = True
            self.session.state = {'turns': [{'turnId': 'tail', 'items': []}],
                                  'turnsPagination': {'hasLoadedOldest': False}}
            self.session.saved_view['turns'].append({'id': 'tail', 'messages': []})
            self.session.changed()
        self.assertEqual(self.preview(), self.raw)

    def test_public_views_carry_image_ids_not_base64(self):
        with self.session.condition:
            # The saved prefix is shared by reference with the presented view.
            self.session.set_history(self.session.state)
            self.session.connected = True
            self.session.state = {'turns': [{'turnId': 'tail', 'items': []}],
                                  'turnsPagination': {'hasLoadedOldest': False}}
            self.session.saved_view['turns'].append({'id': 'tail', 'messages': []})
            self.session.changed()
        view = self.session.view()
        self.assertNotIn(PNG, json.dumps(view))
        attachment = view['turns'][0]['messages'][0]['attachments'][0]
        self.assertEqual((attachment['type'], attachment['desktopId']), ('toolImage', self.identifier))
        self.assertEqual(self.session.saved_view['turns'][0]['messages'][0]['attachments'][0]['data'], PNG)
        self.assertEqual(self.preview(), self.raw)

    def test_subagent_log_does_not_carry_image_base64(self):
        thread, agent = str(uuid.uuid4()), str(uuid.uuid4())
        self.bridge.store = type('Store', (), {'subagent_history': lambda _, *args: {
            'id': agent, 'turns': [{'turnId': 'turn', 'items': [self.item]}]}})()
        view = self.bridge.subagents(thread, agent)
        self.assertNotIn(PNG, json.dumps(view))
        self.assertEqual(view['turns'][0]['messages'][0]['attachments'][0]['desktopId'], self.identifier)

    def evict_preview_bytes(self):
        self.session.view()
        # Evict just decoded bytes; extraction metadata and thread membership remain.
        self.cache.image_limit = len(self.raw) + len(PNG) + 20
        self.cache.extract({'content': [image(base64.b64encode(self.raw + b'evict').decode())]})
        self.assertNotIn(self.identifier, self.cache.images)

    def test_concurrent_cold_previews_share_decode_without_holding_session_lock(self):
        self.evict_preview_bytes()
        started, release = threading.Event(), threading.Event()
        decode = tool_images.decode_tool_image

        def blocked_decode(data, mime):
            started.set()
            self.assertTrue(release.wait(5))
            return decode(data, mime)

        with patch.object(tool_images, 'decode_tool_image', side_effect=blocked_decode) as spy:
            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = [pool.submit(self.preview) for _ in range(8)]
                try:
                    self.assertTrue(started.wait(5))
                    acquired = self.session.condition.acquire(timeout=1)
                    self.assertTrue(acquired, 'preview decoding held the session lock')
                    if acquired:
                        self.session.condition.release()
                finally:
                    release.set()
                for future in futures:
                    self.assertEqual(future.result(timeout=5), self.raw)
            self.assertEqual(spy.call_count, 1)

    def test_image_removed_during_cold_decode_is_not_returned(self):
        self.evict_preview_bytes()
        started, release = threading.Event(), threading.Event()
        decode = tool_images.decode_tool_image

        def blocked_decode(data, mime):
            started.set()
            self.assertTrue(release.wait(5))
            return decode(data, mime)

        with patch.object(tool_images, 'decode_tool_image', side_effect=blocked_decode):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(self.preview)
                try:
                    self.assertTrue(started.wait(5))
                    with self.session.condition:
                        self.item['result'] = 'removed'
                        self.session.changed()
                finally:
                    release.set()
                with self.assertRaises(KeyError):
                    future.result(timeout=5)

    def test_cache_is_bounded_and_returned_output_is_independent(self):
        cache = tool_images.ToolImageCache(output_bytes=4000, image_bytes=200, max_entries=2)
        first = cache.extract(result())
        first[1][0]['name'] = 'changed'
        self.assertEqual(cache.extract(result())[1][0]['name'], 'Image 1.png')
        for i in range(10):
            cache.extract({'content': [image(base64.b64encode(self.raw + bytes([i])).decode())]})
        self.assertLessEqual(cache.output_bytes, 4000)
        self.assertLessEqual(cache.image_bytes, 200)
        self.assertLessEqual(len(cache.outputs), 2)
        self.assertLessEqual(len(cache.images), 2)
        self.assertEqual(cache.extract(result())[1][0]['data'], PNG)
        oversized = tool_images.ToolImageCache(output_bytes=1, image_bytes=1)
        self.assertEqual(oversized.extract(result())[1][0]['data'], PNG)
        self.assertFalse(oversized.outputs)
        self.assertFalse(oversized.images)


if __name__ == '__main__':
    unittest.main()
