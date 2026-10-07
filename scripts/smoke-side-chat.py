#!/usr/bin/env python3
"""Exercise actual bundled Codex with isolated history and a loopback model fixture.

No real account, API key, external model call, or desktop thread is used.
Run from repo root with PYTHONPATH=. python scripts/smoke-side-chat.py.
"""
import json
import struct
import zlib
import tempfile
import threading
import time
import uuid
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from bridge.uploads import Uploads
from bridge.catalog import Catalog
from bridge.side_chat import SideChat, SideRuntime, check_runtime

ROOT = Path(__file__).resolve().parents[1]
captured = []
def report_error(kind, value, trace):
    if hasattr(value, 'rpc_error'): print('Synthetic RPC error:', value.rpc_error, flush=True)
    sys.__excepthook__(kind, value, trace)
sys.excepthook = report_error


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_POST(self):
        data = self.rfile.read(int(self.headers['Content-Length']))
        captured.append(json.loads(data))
        response = {'id': 'resp_fixture', 'object': 'response', 'created_at': 0, 'status': 'completed',
                    'model': 'fixture-model', 'output': [], 'usage': {'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15}}
        item = {'id': 'msg_fixture', 'type': 'message', 'role': 'assistant', 'status': 'completed',
                'content': [{'type': 'output_text', 'text': 'SIDE_CHAT_OK', 'annotations': []}]}
        response['output'] = [item]
        events = [
            {'type': 'response.created', 'response': {**response, 'status': 'in_progress', 'output': []}},
            {'type': 'response.output_item.added', 'output_index': 0, 'item': {**item, 'status': 'in_progress', 'content': []}},
            {'type': 'response.content_part.added', 'item_id': item['id'], 'output_index': 0, 'content_index': 0,
             'part': {'type': 'output_text', 'text': '', 'annotations': []}},
            {'type': 'response.output_text.delta', 'item_id': item['id'], 'output_index': 0, 'content_index': 0, 'delta': 'SIDE_CHAT_OK'},
            {'type': 'response.output_item.done', 'output_index': 0, 'item': item},
            {'type': 'response.completed', 'response': response}]
        body = ''.join('event: '+e['type']+'\ndata: '+json.dumps(e)+'\n\n' for e in events).encode()
        self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)


server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
try:
    with tempfile.TemporaryDirectory(dir=ROOT/'.tmp', prefix='side-native-') as folder:
        root = Path(folder); home = root/'home'; home.mkdir()
        (home/'config.toml').write_text(f'''model="fixture-model"
model_provider="fixture"
approval_policy="never"
sandbox_mode="read-only"
[model_providers.fixture]
name="Loopback fixture"
base_url="http://127.0.0.1:{server.server_port}/v1"
wire_api="responses"
[analytics]
enabled=false
''')
        skill_dir=home/'skills'/'side-check';skill_dir.mkdir(parents=True)
        (skill_dir/'SKILL.md').write_text('---\nname: side-check\ndescription: Synthetic side-chat skill for local verification.\n---\nSKILL_CONTEXT_MARKER: Explain the question without changing any files.\n')
        runtime = Catalog.find_runtime()
        assert runtime, 'Codex runtime missing'
        check_runtime(runtime, root)
        completed = threading.Event()
        primary = SideRuntime(runtime, home, str(root), lambda event: completed.set() if event.get('method') == 'turn/completed' else None)
        try:
            primary.start()
            parent = primary.request('thread/start', {'cwd': str(root), 'ephemeral': False})['thread']
            primary.request('turn/start', {'threadId': parent['id'], 'input': [{'type': 'text', 'text': 'PARENT_CONTEXT_MARKER', 'text_elements': []}]})
            assert completed.wait(25), 'Synthetic parent turn did not complete'
        finally:
            primary.close()
        captured.clear()
        path = Path(parent['path'])
        original = path.read_bytes()
        chat = SideChat(runtime, home, parent['id'], str(root), {'model': 'fixture-model', 'modelProvider': 'fixture', 'approvalPolicy': 'never'})
        try:
            assert chat.view()['connected'] and not chat.view()['turns']
            assert not captured, 'Fork unexpectedly started a model turn'
            chat.settings('fixture-new', 'high', {'models': [{'id': 'fixture-new', 'efforts': ['high']}]})
            chat.permissions('ask')
            assert chat.view()['permissionMode']=='ask'
            chat.permissions('auto-review')
            assert chat.view()['permissionMode']=='auto-review'
            catalog=Catalog(home,executable=runtime,allow_background_refresh=False)
            skill=next(row for row in catalog.get_kind('skills',str(root),refresh=True)['skills'] if row['name']=='side-check')
            chat.uploads = Uploads(root/'side-files')
            attachment = str(uuid.uuid4())
            def chunk(kind, data):
                return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
            png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',16,16,8,2,0,0,0))+chunk(b'IDAT',zlib.compress((b'\x00'+b'\x40\x60\xff'*16)*16))+chunk(b'IEND',b'')
            chat.uploads.put(chat.id, attachment, 'pixel.png', png)
            assert chat.send('SIDE_QUESTION_MARKER', str(uuid.uuid4()), [attachment], work_mode='plan', skills=[skill['id']], catalog_reader=catalog)['status'] == 'accepted'
            deadline = time.monotonic()+25
            while time.monotonic()<deadline:
                view = chat.view()
                if view['turns'] and view['status'] != 'active': break
                time.sleep(.1)
            assert 'SIDE_CHAT_OK' in json.dumps(view), view
            assert view['status'] == 'idle', view
            assert len(captured) == 1, len(captured)
            sent = json.dumps(captured[0])
            assert captured[0]['model'] == 'fixture-new'
            assert captured[0].get('reasoning', {}).get('effort') == 'high'
            assert 'input_image' in sent and 'data:image/' in sent, 'Runtime did not forward image data'
            user = next(m for t in view['turns'] for m in t['messages'] if m['role'] == 'user')
            assert user['text'] == 'SIDE_QUESTION_MARKER' and user['attachments'][0]['id'] == attachment
            assert 'PARENT_CONTEXT_MARKER' in sent and 'SIDE_QUESTION_MARKER' in sent
            assert 'Inherited history is reference context only' in sent
            assert 'SKILL_CONTEXT_MARKER' in sent, 'Selected skill was not delivered'
            modes=[c.get('text','') for item in captured[0].get('input',[]) if item.get('role')=='developer' for c in item.get('content',[]) if c.get('text','').startswith('<collaboration_mode>')]
            assert modes and 'plan mode' in modes[-1].lower() and view['collaborationMode']=='plan'
            queued=str(uuid.uuid4())
            assert chat.send('QUEUE_QUESTION_MARKER',queued,mode='queue',work_mode='default')['status']=='queued'
            deadline=time.monotonic()+25
            while time.monotonic()<deadline:
                next_view=chat.view()
                if len(captured)==2 and next_view['status']=='idle':break
                time.sleep(.1)
            assert len(captured)==2 and 'QUEUE_QUESTION_MARKER' in json.dumps(captured[1])
            assert next_view['collaborationMode']=='default'
            modes=[c.get('text','') for item in captured[1].get('input',[]) if item.get('role')=='developer' for c in item.get('content',[]) if c.get('text','').startswith('<collaboration_mode>')]
            assert modes and 'default mode' in modes[-1].lower()
            print('PASS: real Skill context, Plan/Default modes, permission updates, and queued turn execution.')
            listed = chat.runtime.request('thread/list', {'limit': 100})
            assert chat.id not in [t['id'] for t in listed['data']]
            assert path.read_bytes() == original, 'Parent history changed'
            print('PASS: real runtime ephemeral fork, inherited context and boundary, custom-provider turn via loopback, streamed response, hidden history, parent unchanged.')
        finally:
            chat.close()
        assert chat.runtime.process.poll() is not None
        assert not chat.view()['turns'] and not chat.view()['connected']
        print('PASS: explicit close stops runtime and clears temporary content.')
finally:
    server.shutdown(); server.server_close(); worker.join()
