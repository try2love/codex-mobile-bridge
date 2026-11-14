"""Selected-host Claude Code CLI sessions over SSH stdio."""

import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import shlex
import subprocess
import threading
import time
import uuid
from pathlib import Path

from bridge.clients.claude import model
from bridge.clients.claude.context import context_fields
from bridge.clients.claude.history import merge_live_page
from bridge.clients.claude.cli_session import (_Owned, _message_content, _skill_catalog, _cli_catalog,
                                                _set_cli_settings, _supports_compact, _compact_cli, _supports_rename, _rename_cli, MODES,
                                                _verify_creation_settings, PERMISSION_OPTIONS, ResumePreflightBlocked)
from bridge.clients.codex.remote import ssh_command
from bridge.clients.errors import BridgeUnavailable
from bridge.features.network.retry import ConnectionUnavailable, TransportUnavailable, ssh_connection
from bridge.features.network.stdio import run_with_input
from bridge.clients.uploads import DesktopUploads
from bridge.resources import source_text
import ast


def worker_source():
    source = source_text('clients/claude/remote_worker.py')
    source = source.replace('from bridge.clients.claude.creation import creation_launch_options',
                            source_text('clients/claude/creation.py'))
    marker = 'from bridge.clients.claude.file_archive import archive_paths, state as archive_file_state, change as archive_file_change, supported as archive_supported, assert_no_cli as archive_assert_no_cli'
    if source.count(marker) != 1:
        raise ValueError('Claude Code 远端归档模块无法加载')
    helper = source_text('clients/claude/file_archive.py')
    source = source.replace(marker, helper + '\narchive_file_state = state\narchive_file_change = change\narchive_supported = supported\narchive_assert_no_cli = assert_no_cli\n')
    history = source_text('clients/claude/history.py')
    readers = [node for node in ast.parse(history).body if isinstance(node, ast.FunctionDef) and
               node.name in ('image_event', 'session_custom_title', 'local_command_text')]
    if len(readers) != 3:
        raise ValueError('Claude Code 远端历史读取模块无法加载')
    return source.replace('from bridge.clients.claude.history import image_event, session_custom_title, local_command_text',
                          '\n\n'.join(ast.get_source_segment(history, node) for node in readers))


def _command(alias):
    source = worker_source()
    inner = 'exec python3 -u -c ' + shlex.quote(source)
    return ssh_command(alias)[:-1] + ['exec "$SHELL" -lc ' + shlex.quote(inner)]


def _remote(alias, request, timeout=30):
    try:
        with ssh_connection(alias).attempt():
            try:
                result = run_with_input(_command(alias), json.dumps(request, ensure_ascii=False)+'\n',
                                        capture_output=True, text=True, encoding='utf-8', timeout=timeout)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise TransportUnavailable('SSH Claude Code 主机不可用，请检查连接') from exc
            if result.returncode == 255:
                raise TransportUnavailable('SSH Claude Code 主机不可用，请检查连接')
        if result.returncode != 0:
            raise BridgeUnavailable('SSH Claude Code 主机不可用，请检查连接')
        value = json.loads(result.stdout.strip())
        if value.get('ok') is not True:
            raise BridgeUnavailable(value.get('message') or 'SSH Claude Code 读取失败')
        return value['value']
    except (ConnectionUnavailable, OSError, subprocess.TimeoutExpired, ValueError, KeyError) as exc:
        raise BridgeUnavailable('SSH Claude Code 主机不可用，请检查连接') from exc


class RemoteClaudeCli:
    transport_kind = 'ssh'

    def _command(self, alias):
        return _command(alias)

    def _process_options(self):
        return {}

    def _remote(self, alias, request, **options):
        return _remote(alias, request, **options)

    def __init__(self, hosts=None):
        self.hosts = hosts
        self.lock = threading.RLock()
        self.index = {}
        self.history = {}
        self.listed = False
        self.refreshing = False
        self.last_refresh = 0
        self.host_snapshot = {}
        self.complete = False
        self.owned = {}
        self.closed = False

    def cli_connections(self):
        from bridge.clients.claude.cli_session import owned_connections
        return owned_connections(self, remote=True)

    def available(self):
        return not self.closed and self.hosts is not None and bool(self.hosts.hosts())

    def busy(self):
        with self.lock:
            for owned in self.owned.values():
                if owned.process.poll() is None:
                    with owned.condition:
                        if owned.active or owned.pending:
                            return True
        return False

    def _row(self, host_id, host, raw):
        native = raw['nativeId']
        origin = raw['origin']
        sid = model.gateway_id('cli:'+self.transport_kind+':'+host_id+':'+origin, native)
        return {**raw, 'id': sid, 'backend': 'cli', 'mode': 'cli', 'host': host_id,
                'hostLabel': host.get('displayName') or host['alias'], 'alias': host['alias'],
                'archived': raw.get('archived') is True}

    def _fetch(self, item):
        host_id, host = item
        try:
            result = self._remote(host['alias'], {'operation': 'list'}, timeout=20)
            if not isinstance(result, dict):
                raise ValueError('SSH Claude Code 列表格式无效')
            return host_id, host, result
        except (BridgeUnavailable, ValueError):
            return host_id, host, None

    def _refresh(self, hosts):
        rows = {}
        complete = True
        with ThreadPoolExecutor(max_workers=min(4, max(1, len(hosts)))) as pool:
            futures = [pool.submit(self._fetch, item) for item in hosts.items()]
            for future in as_completed(futures):
                host_id, host, result = future.result()
                with self.lock:
                    previous = dict(self.index)
                if result is None:
                    complete = False
                    rows.update({sid: row for sid, row in previous.items()
                                 if row['host'] == host_id and row['alias'] == host['alias']})
                    continue
                if result.get('complete') is False:
                    complete = False
                for item in result.get('sessions') or []:
                    if isinstance(item, dict) and item.get('nativeId') and item.get('origin'):
                        row = self._row(host_id, host, item)
                        rows[row['id']] = row
                origin = (result.get('profile') or {}).get('origin')
                with self.lock:
                    rows.update({sid: row for sid, row in previous.items()
                                 if row['host'] == host_id and row['alias'] == host['alias']
                                 and row['origin'] == origin and sid in self.owned
                                 and self.owned[sid].process.poll() is None})
        with self.lock:
            trusted = self.hosts.hosts() if self.hosts else {}
            self.index = {sid: row for sid, row in rows.items()
                          if row['host'] in trusted and trusted[row['host']]['alias'] == row['alias']}
            self.history = {key: page for key, page in self.history.items() if key[0] in self.index}
            self.host_snapshot = hosts
            self.last_refresh = time.monotonic()
            self.complete = complete
            self.listed = True
            self.refreshing = False

    def listing(self):
        if self.closed or self.hosts is None:
            return {'connected': False, 'sessions': [], 'complete': True}
        hosts = self.hosts.hosts()
        with self.lock:
            if not self.refreshing and (hosts != self.host_snapshot or time.monotonic()-self.last_refresh >= 10):
                self.refreshing = True
                threading.Thread(target=self._refresh, args=(hosts,), daemon=True,
                                 name='claude-ssh-list').start()
            rows = {sid: row for sid, row in self.index.items()
                    if row['host'] in hosts and hosts[row['host']]['alias'] == row['alias']}
            return {'connected': bool(hosts), 'sessions': [self._public(row) for row in rows.values()],
                    'complete': self.complete and not self.refreshing and hosts == self.host_snapshot}

    def contains(self, sid):
        with self.lock:
            if sid in self.index:
                return True
            listed = self.listed
        if not listed:
            self.listing()
        with self.lock:
            return sid in self.index

    def projects(self, host_id=None):
        if self.closed or self.hosts is None:
            return []
        configured = self.hosts.hosts()
        if host_id is not None:
            configured = {host_id: configured[host_id]} if host_id in configured else {}
        known = self.hosts.projects()
        rows = []
        def discover(host_id, host):
            cwds = {item['cwd'] for item in known if item.get('host') == host_id}
            try:
                result = self._remote(host['alias'], {'operation': 'projects'}, timeout=12)
                found = result.get('profile')
                if not found or not result.get('authReady'):
                    return []
            except BridgeUnavailable:
                return []
            cwds.update(item['cwd'] for item in result.get('sessions') or []
                    if isinstance(item, dict) and isinstance(item.get('cwd'), str))
            discovered = []
            for cwd in sorted(cwds):
                if not cwd.startswith('/'):
                    continue
                discovered.append({'key': model.gateway_id('cli:project:'+self.transport_kind+':'+host_id+':'+found['origin'], cwd),
                                   'name': Path(cwd).name+' · '+(host.get('displayName') or host['alias']),
                                   'cwd': cwd, 'surface': 'cli', 'host': host_id,
                                   'alias': host['alias'], 'origin': found['origin'],
                                   'createRequiresMessage': True})
            return discovered
        with ThreadPoolExecutor(max_workers=min(4, max(1, len(configured)))) as pool:
            futures = [pool.submit(discover, host_id, host) for host_id, host in configured.items()]
            for future in as_completed(futures):
                rows.extend(future.result())
        return rows

    def project_environments(self, host_id=None):
        """Discover configured CLI hosts even when they have no saved project."""
        if self.closed or self.hosts is None:
            return []
        rows = []
        selected = self.hosts.hosts()
        if host_id is not None:
            selected = {host_id: selected[host_id]} if host_id in selected else {}
        for host_id, host in selected.items():
            try:
                result = self._remote(host['alias'], {'operation': 'projects'}, timeout=12)
            except BridgeUnavailable:
                continue
            found = result.get('profile')
            if found and result.get('authReady'):
                rows.append({'surface': 'cli', 'host': host_id, 'alias': host['alias'],
                    'hostLabel': host.get('displayName') or host['alias'],
                    'name': 'Claude Code · '+(host.get('displayName') or host['alias']),
                    'origin': found['origin'], 'scope': host['alias']+'|'+found['origin'],
                    'createRequiresMessage': True})
        return rows

    def _get(self, sid):
        with self.lock:
            row = self.index.get(sid)
        if row is None:
            self.listing()
            with self.lock:
                row = self.index.get(sid)
        if row is None or self.hosts is None:
            raise ValueError('找不到 SSH Claude Code 会话')
        host = self.hosts.hosts().get(row['host'])
        if not host or host['alias'] != row['alias']:
            raise ValueError('原 SSH 主机配置已变化')
        fresh = self._remote(row['alias'], {'operation': 'detail', 'native': row['nativeId'],
                                       'origin': row['origin']})
        if self._row(row['host'], host, fresh)['id'] != sid:
            raise ValueError('原 SSH Claude Code 会话身份已变化')
        updated = self._row(row['host'], host, fresh)
        with self.lock:
            self.index[sid] = updated
        return updated

    def _public(self, row):
        value = {key: item for key, item in row.items() if key not in
                 ('events', 'contextEvents', 'stamp', 'origin', 'alias', 'permissionKnown',
                  'projectConfig', 'draftAccountIdentity')}
        value.update(context_fields(row.get('contextEvents') or []))
        owned = self.owned.get(row['id'])
        live = bool(owned and owned.origin == row['origin'] and owned.process.poll() is None and not owned.error)
        if live:
            with owned.condition:
                pending, active = dict(owned.pending), owned.active
        else:
            pending, active = {}, False
        value.update(status='active' if active else 'idle', runtimeKnown=True,
                     connectionState='awaiting' if pending else 'active' if active else 'ready' if live else 'closed',
                     requests=[{'id': rid, 'tool': event['request'].get('tool_name', '工具'),
                                'input': event['request'].get('input', {}),
                                'needsInput': event['request'].get('tool_name') == 'AskUserQuestion'}
                               for rid, event in pending.items()])
        value['capabilities'] = {**value.get('capabilities', {}), 'rename': bool(live and not row.get('archived') and
                                 not row.get('archiveIncomplete') and _supports_rename(owned.catalog))}
        if live:
            value['model'] = owned.model or value['model']
            value['permissionMode'] = owned.permission or value['permissionMode']
        if row.get('archiveIncomplete'):
            value.update(runtimeKnown=False, status='unknown', connectionState='closed', requests=[],
                         capabilities={'archive': False, 'unarchive': False})
        return value

    def _upload_files(self, row, files):
        result = []
        scope = DesktopUploads.scope('claude', row['id'])
        for item in files:
            identifier = str(uuid.UUID(item['id']))
            path = Path(item['path'])
            data = path.read_bytes()
            if not 0 < len(data) <= 20*1024*1024:
                raise ValueError('文件附件大小无效')
            digest = hashlib.sha256(data).hexdigest()
            remote = self._remote(row['alias'], {'operation': 'upload', 'origin': row['origin'],
                'scope': scope, 'id': identifier, 'name': item['name'], 'sha256': digest,
                'data': base64.b64encode(data).decode('ascii')}, timeout=90)
            result.append({'name': item['name'], 'path': remote['path']})
        return result

    def _saved_user(self, alias, owned, message_id, scope):
        """Confirm one original user UUID from durable, identity-checked history."""
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                saved = self._remote(alias, {'operation': 'detail', 'origin': owned.origin,
                                            'native': owned.native}, timeout=5)
            except BridgeUnavailable:
                saved = None
            if saved:
                if any(saved.get(key) != value for key, value in
                       (('origin', owned.origin), ('nativeId', owned.native), ('cwd', owned.cwd))):
                    raise BridgeUnavailable('原 Claude Code 会话身份或工作目录已变化，消息结果不明')
                before = None
                while time.monotonic() < deadline:
                    try:
                        page = self._remote(alias, {'operation': 'history', 'origin': owned.origin,
                            'native': owned.native, 'scope': scope, 'historyBefore': before,
                            'historyLimit': 100}, timeout=5)
                    except BridgeUnavailable:
                        break
                    # Streaming appends change size/times, not file identity.
                    if page['stamp'][:2] != saved['stamp'][:2]:
                        break
                    if any(event.get('type') == 'user' and event.get('uuid') == message_id
                           and event.get('cwd') == owned.cwd and not event.get('isSidechain')
                           for event in page.get('events', [])):
                        return saved
                    history = page.get('history') or {}
                    if not history.get('hasMore') or not history.get('before') or history['before'] == before:
                        break
                    before = history['before']
            with owned.condition:
                if owned.error or owned.finished:
                    break
            time.sleep(.2)
        return None

    def create(self, host_id, alias, origin, cwd, message, directory_identity=None, selection=None):
        host = self.hosts.hosts().get(host_id) if self.hosts else None
        if not host or host['alias'] != alias:
            raise ValueError('原 SSH 主机配置已变化')
        preflight = self._remote(alias, {'operation': 'projects'}, timeout=12)
        fresh = preflight.get('profile')
        if not fresh or fresh['origin'] != origin or not preflight.get('authReady'):
            raise BridgeUnavailable('原 SSH Claude Code 账号配置已变化或不可用')
        native = str(uuid.uuid4())
        sid = model.gateway_id('cli:'+self.transport_kind+':'+host_id+':'+origin, native)
        process = subprocess.Popen(self._command(alias), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, text=True, encoding='utf-8', bufsize=1, **self._process_options())
        try:
            process.stdin.write(json.dumps({'operation': 'create', 'origin': origin,
                'native': native, 'cwd': cwd, 'directoryIdentity': directory_identity,
                **({'selection': selection} if selection else {}),
                'scope': DesktopUploads.scope('claude', sid)})+'\n')
            process.stdin.flush()
        except OSError:
            process.terminate()
            raise BridgeUnavailable('SSH Claude Code 新建结果不明，请检查会话列表') from None
        owned = _Owned(process, origin, native, cwd)
        with owned.condition:
            owned.condition.wait_for(lambda: owned.preflight is not None or owned.finished, timeout=20)
            blocked = owned.preflight and owned.preflight.get('status') == 'blocked'
        if blocked:
            error = owned.error
            owned.close()
            raise BridgeUnavailable(error)
        if owned.preflight is None:
            owned.close()
            raise BridgeUnavailable('SSH Claude Code 新建结果不明，请检查会话列表')
        try:
            owned.catalog = owned.control({'subtype': 'initialize', 'hooks': None}, timeout=30)
            if owned.error:
                raise BridgeUnavailable(owned.error)
            owned.permission = owned.catalog.get('current_permission_mode')
            if selection:
                _verify_creation_settings(owned, selection)
            with self.lock:
                self.owned[sid] = owned
            message_id = str(uuid.uuid4())
            with owned.condition:
                owned.active = True
                owned.write({'type': 'user', 'uuid': message_id,
                             'message': {'role': 'user', 'content': message}})
            saved = self._saved_user(alias, owned, message_id, sid)
            if saved is None:
                raise BridgeUnavailable('SSH Claude Code 新建消息结果不明，请检查会话列表')
            row = self._row(host_id, host, saved)
            with self.lock:
                self.index[sid] = row
            return {'status': 'ready', 'result': {'id': sid}}
        except Exception:
            with self.lock:
                retained = sid in self.owned
            if not retained:
                owned.close()
            raise

    def _archive(self, action, sid, body):
        with self.lock:
            row = self.index.get(sid)
            if row is None or self.hosts is None:
                raise ValueError('找不到原 Claude Code 会话')
            host = self.hosts.hosts().get(row['host'])
            if not host or host['alias'] != row['alias']:
                raise ValueError('原 SSH 主机配置已变化')
            owned = self.owned.get(sid)
            live = owned is not None and owned.process.poll() is None
            if live and (owned.origin != row['origin'] or owned.native != row['nativeId'] or owned.error):
                raise BridgeUnavailable('原 Claude Code 执行连接身份无法核验')
            request = {'operation': action, 'native': row['nativeId'], 'origin': row['origin']}
            if action == 'archive':
                if (set(body) not in ({'archived'}, {'archived', 'disconnect'}) or type(body['archived']) is not bool or
                        type(body.get('disconnect', False)) is not bool or (body.get('disconnect') and body['archived'] is not True)):
                    raise ValueError('Claude 归档设置无效')
                if live:
                    if not body.get('disconnect'):
                        raise BridgeUnavailable('原 Claude Code 会话仍有网关执行连接，请先在原客户端结束')
                    owned.release_for_archive()
                    live = False
                request['archived'] = body['archived']
            fresh = self._remote(row['alias'], request)
            updated = self._row(row['host'], host, fresh)
            if updated['id'] != sid:
                raise ValueError('原 SSH Claude Code 会话身份已变化')
            current_host = self.hosts.hosts().get(row['host'])
            if not current_host or current_host['alias'] != row['alias']:
                raise ValueError('原 SSH 主机配置已变化')
            self.index[sid] = updated
            self.history = {key: value for key, value in self.history.items() if key[0] != sid}
            return {**self._public(updated), 'archiveSafe': not live and fresh.get('archiveSafe') is True}

    def call(self, action, sid, body):
        if action in ('archive-state', 'archive'):
            return self._archive(action, sid, body)
        try:
            with self.lock:
                cached = self.index.get(sid)
                owned_cached = self.owned.get(sid)
            host = self.hosts.hosts().get(cached['host']) if cached and self.hosts else None
            live_cached = bool(cached and host and host['alias'] == cached['alias'] and
                               owned_cached and owned_cached.origin == cached['origin'] and
                               owned_cached.process.poll() is None and not owned_cached.error)
            if action == 'detail':
                before = body.get('historyBefore')
                limit = body.get('historyLimit', 40)
                if before is not None and (not isinstance(before, str) or len(before) > 1024):
                    raise ValueError('Claude 历史游标无效')
                if type(limit) is not int or not 1 <= limit <= 100:
                    raise ValueError('Claude 历史分页大小无效')
                row = cached if live_cached or cached is not None else self._get(sid)
                if row is None:
                    row = self._get(sid)
                current_host = self.hosts.hosts().get(row['host']) if self.hosts else None
                if not current_host or current_host['alias'] != row['alias']:
                    raise ValueError('原 SSH 主机配置已变化')
                page_key = (sid, before, limit)
                try:
                    page = self._remote(row['alias'], {'operation': 'history', 'native': row['nativeId'],
                            'origin': row['origin'], 'scope': sid,
                            'historyBefore': before, 'historyLimit': limit})
                    if not isinstance(page, dict) or not isinstance(page.get('events'), list) or not isinstance(page.get('history'), dict):
                        raise BridgeUnavailable('SSH Claude Code 历史格式无效')
                    if page.get('stamp') != row.get('stamp'):
                        row = self._get(sid)
                        if page.get('stamp') != row.get('stamp'):
                            raise BridgeUnavailable('SSH Claude Code 历史读取期间发生变化，请刷新')
                    with self.lock:
                        self.history = {key: value for key, value in self.history.items() if key[0] != sid}
                        self.history[page_key] = page
                except BridgeUnavailable as exc:
                    if not live_cached or before is not None or '主机不可用' not in str(exc):
                        raise
                    with self.lock:
                        page = self.history.get(page_key)
                    if page is None or page.get('stamp') != row.get('stamp'):
                        raise
            elif action == 'image':
                row = self._get(sid)
                event = self._remote(row['alias'], {'operation':'image','native':row['nativeId'],
                    'origin':row['origin'],'source':body.get('source')})
                return model.history_image([event], body.get('source'))
            elif live_cached and action != 'resume':
                row = cached
            else:
                row = self._get(sid)
        except (ValueError, BridgeUnavailable) as exc:
            if action == 'resume':
                raise ResumePreflightBlocked(str(exc)) from exc
            raise
        with self.lock:
            owned = self.owned.get(sid)
            if owned and (owned.origin != row['origin'] or owned.process.poll() is not None):
                owned = None
            public = self._public(row)
            live = owned is not None and not owned.error
            if action == 'detail':
                public['draftAccountIdentity'] = row.get('draftAccountIdentity', '')
                history = page['events']
                if live:
                    with owned.condition:
                        live_events = list(owned.events) if before is None else []
                    history = merge_live_page(history, live_events)
                else:
                    live_events = []
                public.update(context_fields((row.get('contextEvents') or []) + live_events))
                messages = model.transcript(history)
                ready = False
                notice = '无法确认原会话权限模式，请在远端终端继续' if not live and not row['permissionKnown'] else ''
                if not live and not row.get('archived') and row['permissionKnown']:
                    try:
                        preflight = self._remote(row['alias'], {'operation': 'resume-preflight',
                            'origin': row['origin'], 'native': row['nativeId'], 'cwd': row['cwd'],
                            'model': row['model'], 'permissionMode': row['permissionMode']}, timeout=12)
                        ready = preflight['ready'] is True
                        notice = preflight.get('notice', '')
                    except BridgeUnavailable as exc:
                        notice = str(exc)
                return {'connected': True, 'session': public,
                        'messages': list({item['id']: item for item in messages}.values()),
                        'turns': model.turns(history),
                        'history': page['history'],
                        'notice': notice,
                        'capabilities': {**row.get('capabilities', {}), 'resume': not live and not row.get('archived') and ready, 'send': live and public['connectionState'] == 'ready',
                                         'models': live, 'permissions': live, 'stop': live,
                                         'compact': live and not owned.active and not owned.pending and _supports_compact(owned.catalog),
                                         'attachments': True, 'queue': False, 'steer': False, 'skills': live,
                                         'rename': public['capabilities']['rename']}}
            if row.get('archived'):
                raise BridgeUnavailable('请先恢复归档的 Claude Code 会话')
            if action == 'catalog':
                if not live:
                    return {'models': [], 'capabilities': {'models': False}, 'notice': '请先继续这条 Claude Code 会话'}
                return _cli_catalog(owned, public, body.get('refresh') is True)
            if action == 'access' and 'mode' not in body:
                return {'status': 'accepted', 'mode': public['permissionMode'],
                        'options': [{'value': mode, 'label': label, 'description': description}
                                    for mode, label, description in PERMISSION_OPTIONS] if live else []}
            if action == 'resume':
                if live:
                    return {'status': 'ready'}
                if not row['permissionKnown'] or row['permissionMode'] not in MODES:
                    raise ResumePreflightBlocked('无法确认原会话权限模式')
                process = subprocess.Popen(self._command(row['alias']), stdin=subprocess.PIPE,
                                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                           text=True, encoding='utf-8', bufsize=1, **self._process_options())
                try:
                    process.stdin.write(json.dumps({'operation': 'resume', 'origin': row['origin'],
                        'native': row['nativeId'], 'cwd': row['cwd'], 'model': row['model'],
                        'permissionMode': row['permissionMode'],
                        'scope': DesktopUploads.scope('claude', sid)})+'\n')
                    process.stdin.flush()
                except OSError:
                    process.terminate()
                    raise BridgeUnavailable('SSH Claude Code 恢复结果不明，请检查原会话') from None
                owned = _Owned(process, row['origin'], row['nativeId'], row['cwd'])
                with owned.condition:
                    owned.condition.wait_for(lambda: owned.preflight is not None or owned.finished, timeout=20)
                    blocked = owned.preflight and owned.preflight.get('status') == 'blocked'
                if blocked:
                    message = owned.error
                    owned.close()
                    raise ResumePreflightBlocked(message)
                if owned.preflight is None:
                    owned.close()
                    raise BridgeUnavailable('SSH Claude Code 恢复结果不明，请检查原会话')
                try:
                    owned.catalog = owned.control({'subtype': 'initialize', 'hooks': None}, timeout=30)
                    if owned.error:
                        raise BridgeUnavailable(owned.error)
                    owned.permission = owned.catalog.get('current_permission_mode') or row['permissionMode']
                    if owned.permission != row['permissionMode']:
                        raise BridgeUnavailable('Claude Code 未保留原会话权限模式')
                except Exception:
                    owned.close()
                    raise
                self.owned[sid] = owned
                return {'status': 'ready'}
            if not live:
                raise BridgeUnavailable('请先继续这条 SSH Claude Code 会话')
            if action == 'rename':
                return _rename_cli(owned, row, body.get('title'), lambda: self._get(sid))
            if action == 'send':
                if body.get('mode') in ('queue', 'steer'):
                    raise ValueError('Claude Code CLI 暂不支持此发送选项')
                selected = body.get('plugins') or []
                if not isinstance(selected, list) or len(selected) > 1:
                    raise ValueError('Claude Code CLI 每条消息只能调用一个 Skill')
                if selected and selected[0] not in {skill['id'] for skill in _skill_catalog(owned.catalog)
                                                    if skill['selectable']}:
                    raise ValueError('技能已不可用，请刷新后选择')
                with owned.condition:
                    if owned.active or owned.pending:
                        raise BridgeUnavailable('请等待当前 Claude Code 任务结束后再发送')
                files = self._upload_files(row, body.get('resolvedFiles') or [])
                message = ('/'+selected[0]+(' '+body['text'] if body['text'].strip() else '')
                           if selected else body['text'])
                content = _message_content({**body, 'text': message, 'resolvedFiles': files})
                with owned.condition:
                    if owned.active or owned.pending:
                        raise BridgeUnavailable('请等待当前 Claude Code 任务结束后再发送')
                    owned.active = True
                    message_id = str(uuid.uuid4())
                    owned.write({'type': 'user', 'uuid': message_id,
                                 'message': {'role': 'user', 'content': content}})
                if self._saved_user(row['alias'], owned, message_id, sid) is None:
                    raise BridgeUnavailable('SSH Claude Code 发送结果不明，请检查原会话')
            elif action == 'compact':
                _compact_cli(owned, body['id'])
            elif action == 'stop':
                owned.control({'subtype': 'interrupt'})
            elif action == 'settings':
                _set_cli_settings(owned, body)
            elif action == 'access':
                mode = body.get('mode')
                if mode not in MODES:
                    raise ValueError('请选择 Claude Code 提供的权限模式')
                if mode == 'bypassPermissions' and body.get('confirmed') is not True:
                    raise ValueError('完全访问需要明确确认')
                result = owned.control({'subtype': 'set_permission_mode', 'mode': mode})
                if result.get('mode') != mode:
                    raise BridgeUnavailable('Claude Code 未确认权限模式')
                with owned.condition:
                    deadline = time.monotonic() + 5
                    while owned.permission != mode and owned.process.poll() is None and time.monotonic() < deadline:
                        owned.condition.wait(max(0, deadline-time.monotonic()))
                    if owned.permission != mode:
                        raise BridgeUnavailable('Claude Code 尚未报告生效的权限模式，请刷新检查')
            elif action == 'respond':
                rid = body.get('requestId')
                with owned.condition:
                    pending = owned.pending.get(rid)
                if not pending:
                    raise ValueError('请求已处理或过期')
                decision = {'accept': 'allow', 'decline': 'deny'}.get(body.get('decision'))
                if not decision:
                    raise ValueError('请选择本次允许或拒绝')
                response = {'behavior': decision}
                if decision == 'allow':
                    updated = dict(pending['request'].get('input', {}))
                    if pending['request'].get('tool_name') == 'AskUserQuestion':
                        answers = {}
                        for index, question in enumerate(updated.get('questions') or []):
                            key = str(question.get('id') or question.get('question') or index)
                            values = ((body.get('answers') or {}).get(key) or {}).get('answers') or []
                            if not values or not all(isinstance(value, str) for value in values):
                                raise ValueError('请回答每一项问题')
                            answers[question['question']] = ', '.join(values)
                        updated['answers'] = answers
                    response['updatedInput'] = updated
                else:
                    response['message'] = '用户拒绝了本次操作'
                with owned.condition:
                    if owned.pending.pop(rid, None) is not pending:
                        raise ValueError('请求已处理或过期')
                owned.write({'type': 'control_response', 'response': {'subtype': 'success',
                             'request_id': rid, 'response': response}})
            else:
                raise ValueError('不支持的 Claude Code 操作')
            return {'status': 'accepted'}

    def close(self):
        with self.lock:
            self.closed = True
            for owned in self.owned.values():
                owned.close()
            self.owned.clear()
