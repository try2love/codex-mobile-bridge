"""Isolated synthetic session sources served by the production HTTP gateway."""
import argparse
import base64
import json
import os
import sqlite3
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, os.environ.get('CMB_FIXTURE_GATEWAY_SOURCE', str(ROOT)))
from bridge.api.httpd import GatewayServer
from bridge.app.service import Bridge
from bridge.features.auth.auth import password_record

SESSION = '12345678-1234-4234-9234-123456789012'
SECOND_SESSION = '22345678-1234-4234-9234-123456789012'


def reply_label(index):
    if index < 87:
        return {}
    return {'model': f'历史模型 {index}', **({'effort': {88: 'off', 89: 'high'}[index]} if index != 87 else {})}


def answer_text(index, home):
    if index == 88:
        return f'Answer {index}\n\n[report.md]({home.as_posix()}/report.md)'
    if index == 87:
        return f'Answer {index}\n\n' + 'x' * 17000 + f'\n\n[late.md]({home.as_posix()}/late.md)'
    return f'Answer {index}'


def make_history(home, steps=24):
    records = []

    def event(kind, **fields):
        records.append({'type': 'event_msg', 'payload': {'type': kind, **fields}})

    def message(role, text):
        records.append({'type': 'response_item', 'payload': {'type': 'message',
                        'role': role, 'content': [{'type': 'input_text' if role == 'user' else 'output_text', 'text': text}]}})

    for index in range(90):
        turn = 'turn-' + str(index)
        event('task_started', turn_id=turn)
        label = reply_label(index)
        if label:
            records.append({'type': 'turn_context', 'payload': {'turn_id': turn, **label}})
        message('user', f'Question {index}')
        if index == 89:
            for item in range(steps):
                output = 'Earlier tool output' if item != steps - 1 else 'Long detail marker\n' + 'a' * 16000 + 'SECOND CHUNK\n' + 'b' * 2000
                records.extend([
                    {'type': 'response_item', 'payload': {'type': 'function_call', 'name': 'shell_command',
                        'call_id': f'command-{item}', 'arguments': json.dumps({'command': f'fixture-command-{item}'})}},
                    {'type': 'response_item', 'payload': {'type': 'function_call_output',
                        'call_id': f'command-{item}', 'output': output}},
                ])
        message('assistant', answer_text(index, home))
        event('task_complete', turn_id=turn)

    rollout = home / 'sessions' / 'rollout.jsonl'
    rollout.parent.mkdir(parents=True)
    rollout.write_text(''.join(json.dumps(record) + '\n' for record in records), encoding='utf-8')
    with sqlite3.connect(home / 'state_5.sqlite') as db:
        db.execute('CREATE TABLE threads(id TEXT, title TEXT, cwd TEXT, updated_at INT, archived INT, '
                   'originator TEXT, source TEXT, rollout_path TEXT, model_provider TEXT)')
        db.execute('INSERT INTO threads VALUES(?,?,?,?,?,?,?,?,?)',
                   (SESSION, 'Transfer HTTP fixture', str(home), 1790000000, 0,
                    'codex_cli_rs', 'cli', str(rollout), 'openai'))


def follow_fixture_rollouts(bridge, home, stop):
    # Substitute local file observation for an installed native IPC owner.
    # The observer only writes the owner's fixture snapshot to LiveSession.
    monitor = {}
    for sid, filename in ((SESSION, 'rollout.jsonl'), (SECOND_SESSION, 'second.jsonl')):
        rollout = home / 'sessions' / filename
        session = bridge.session(sid, attach=False)
        monitor[sid] = rollout, session, None

    def poll():
        while not stop.is_set():
            for sid, (rollout, session, previous) in list(monitor.items()):
                stat = rollout.stat()
                revision = stat.st_mtime_ns, stat.st_size
                if revision == previous:
                    continue
                state = bridge.store.history(sid)
                active = bool(state['turns'] and state['turns'][-1]['status'] == 'inProgress')
                state['threadRuntimeStatus'] = {'type': 'active' if active else 'idle'}
                with session.condition:
                    session.state, session.connected, session.runtime = state, True, 'desktop'
                    session.changed()
                monitor[sid] = rollout, session, revision
            stop.wait(.02)

    thread = threading.Thread(target=poll, daemon=True)
    thread.start()
    return thread


def add_watch_source(home):
    rollout = home / 'sessions' / 'second.jsonl'
    rollout.write_text(''.join(json.dumps(record) + '\n' for record in (
        {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': [{'text': 'Other session question'}]}},
        {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'content': [{'text': 'Other session answer'}]}},
    )), encoding='utf-8')
    with sqlite3.connect(home / 'state_5.sqlite') as db:
        db.execute('INSERT INTO threads VALUES(?,?,?,?,?,?,?,?,?)',
                   (SECOND_SESSION, 'Other transfer fixture', str(home), 1790000000, 0,
                    'codex_cli_rs', 'cli', str(rollout), 'openai'))
    image = home / 'watch.png'
    image.write_bytes(base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/pGsAAAAASUVORK5CYII='))
    return {'rollout': str(home / 'sessions' / 'rollout.jsonl'), 'other': str(rollout), 'image': str(image)}


def source_for(provider, home, steps=24):
    session = {'id': SESSION, 'title': 'Transfer HTTP fixture', 'cwd': str(home), 'host': 'local',
               'backend': 'code' if provider == 'claude' else 'app', 'status': 'idle',
               'connectionState': 'ready', 'readOnly': True, 'requests': [], 'capabilities': {}}
    records = []
    for index in range(90):
        shared = {'turnId': f'turn-{index}', 'turnStatus': 'completed'}
        records.append(dict(shared, id=f'in-{index}', role='user', text=f'Question {index}'))
        if index == 89:
            for item in range(steps):
                output = 'Earlier tool output' if item != steps - 1 else 'Long detail marker\n' + 'a' * 16000 + 'SECOND CHUNK\n' + 'b' * 2000
                records.append(dict(shared, id=f'command-{item}', role='activity', kind='commandExecution',
                                    title=f'fixture-command-{item}', output=output))
        records.append(dict(shared, id=f'out-{index}', role='assistant', text=answer_text(index, home),
                            phase='final_answer', **reply_label(index)))

    def read(_provider, action, sid=None, options=None):
        if action == 'list':
            return {'sessions': [session], 'connected': True}
        if action == 'detail' and sid == SESSION:
            options = options or {}
            stop = int(options.get('historyBefore') or len(records))
            start = max(0, stop - options.get('historyLimit', 40))
            return {'connected': True, 'session': session, 'messages': records[start:stop],
                    'history': {'hasMore': start > 0, 'before': str(start), 'complete': start == 0}}
        return {'connected': False, 'capabilities': {}}
    return read


def serve(provider, legacy, steps, live_watch):
    temp_root = ROOT / '.tmp'
    temp_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='session-transfer-http-', dir=temp_root) as folder:
        root = Path(folder)
        home, data = root / 'codex', root / 'gateway'
        data.mkdir()
        make_history(home, steps)
        (home / 'report.md').write_text('Fixture file preview', encoding='utf-8')
        (home / 'late.md').write_text('Long body file preview', encoding='utf-8')
        watch_sources = add_watch_source(home) if live_watch else None
        bridge = Bridge(home, data, codex_bin=home / 'missing-test-runtime',
                        ipc_path=str(home / 'missing-test-socket'), observation_enabled=lambda: False)
        try:
            config = {'origins': [], 'auth': {'mode': 'password', 'username': 'fixture',
                       **password_record('session-transfer-test-only')}}
            web = Path(os.environ.get('CMB_FIXTURE_WEB_ROOT', str(ROOT / 'web')))
            with GatewayServer(('127.0.0.1', 0), bridge, config, web, data) as gateway:
                if legacy:
                    if isinstance(getattr(type(gateway.auth), 'session_sync', None), property):
                        type(gateway.auth).session_sync = None
                    else:
                        gateway.auth.session_sync = None
                    if provider == 'codex':
                        # Exercise the old view with saved history already loaded,
                        # without starting an owner/CLI probe in the host machine.
                        saved = bridge.store.history(SESSION)
                        session = bridge.session(SESSION, attach=False)
                        with session.condition:
                            session.set_history(saved)
                manager = gateway.desktop_sessions
                manager.config['enabled'] = {client: True for client in ('codex', provider)}
                manager.navigation_clients = lambda: list(manager.config['enabled'])
                manager.clients_status = lambda **_: {'clients': [
                    {'id': client, 'name': client.capitalize(), 'enabled': True, 'installed': True,
                     'available': True, 'connected': True, 'selectable': True, 'running': False, 'configured': True}
                    for client in manager.config['enabled']]}
                bridge.observation_enabled = lambda: False
                if provider != 'codex':
                    manager.call = source_for(provider, home, steps)
                    manager.list_with_activity = lambda name: manager.call(name, 'list')
                    adapter = manager.adapters.get(provider)
                    if adapter is not None:
                        def unavailable_saved_record(identifier):
                            # This fake renderer owns its records in memory. A
                            # fixture request must never inspect real host files.
                            raise KeyError('fixture has no saved transcript')
                        adapter.record_path = unavailable_saved_record
                origin = 'http://127.0.0.1:' + str(gateway.server_address[1])
                gateway.origins.add(origin)
                gateway.hosts.add(origin.removeprefix('http://'))
                stop = threading.Event()
                monitor = follow_fixture_rollouts(bridge, home, stop) if live_watch else None
                print(origin, flush=True)
                if watch_sources:
                    print(json.dumps(watch_sources), flush=True)
                try:
                    gateway.serve_forever()
                except KeyboardInterrupt:
                    pass
                finally:
                    stop.set()
                    if monitor:
                        monitor.join()
                    manager.close()
                    gateway.harness.close()
        finally:
            bridge.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider', choices=('codex', 'claude', 'deepseek', 'zcode', 'antigravity'), default='codex')
    parser.add_argument('--legacy', action='store_true')
    parser.add_argument('--steps', type=int, choices=range(1, 33), default=24)
    parser.add_argument('--live-watch', action='store_true')
    args = parser.parse_args()
    serve(args.provider, args.legacy, args.steps, args.live_watch)
