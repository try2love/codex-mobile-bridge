"""Read existing App SSH metadata; turns still use the desktop's IPC owner."""
import base64
from contextlib import contextmanager
import json
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from bridge.clients.codex.catalog import CatalogError
from bridge.resources import source_text


class RemoteUnavailable(RuntimeError):
    pass


def ssh_command(alias):
    if not alias or alias.startswith('-') or any(c.isspace() for c in alias):
        raise RemoteUnavailable('SSH 别名无效')
    return ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ClearAllForwardings=yes',
               '-o', 'ConnectTimeout=8', '-o', 'StrictHostKeyChecking=yes',
               '-o', 'UpdateHostKeys=no', alias, 'python3 -']


def ssh_read(alias, source, timeout=18):
    command = ssh_command(alias)
    try:
        result = subprocess.run(command, input=source, text=True, encoding='utf-8', capture_output=True, timeout=timeout)
        if result.returncode:
            raise RemoteUnavailable('SSH 读取失败，请检查电脑上该主机的 SSH 连接')
        return json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        raise RemoteUnavailable('SSH 主机暂不可用，请检查电脑上的连接') from exc


@contextmanager
def ssh_download(alias, source):
    """Keep remote binary output on disk, including full-response fallbacks."""
    command = ssh_command(alias)
    with tempfile.TemporaryFile(prefix='bridge-ssh-download-') as stream:
        try:
            result = subprocess.run(command, input=source.encode('utf-8'), stdout=stream,
                                    stderr=subprocess.DEVNULL, timeout=60)
            if result.returncode:
                raise RemoteUnavailable('SSH 下载失败，请检查电脑上该主机的 SSH 连接')
            length = stream.tell()
            stream.seek(0)
            header = stream.readline(8193)
            if len(header) > 8192 or not header.endswith(b'\n'):
                raise ValueError('Invalid remote download header')
            metadata = json.loads(header)
            if 'error' in metadata:
                if metadata.get('permission'): raise PermissionError(metadata['error'])
                raise ValueError(metadata['error'])
            if (type(metadata.get('length')) is not int or not 0 <= metadata['length'] <= 2**53 - 1
                    or length - len(header) != metadata['length']):
                raise ValueError('SSH 下载中断，请重试')
        except PermissionError:
            raise
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RemoteUnavailable('SSH 主机暂不可用，请检查电脑上的连接') from exc
        yield metadata, stream


def payload(value):
    data = base64.b64encode(json.dumps(value).encode()).decode()
    return 'json.loads(__import__("base64").b64decode(' + repr(data) + '))'


class RemoteStore:
    def __init__(self, alias):
        self.alias = alias
        self.home = None  # Never map remote paths onto the local filesystem.
        self.cache = {}
        self.kind_caches = {}
        self.lock = threading.Lock()

    def call(self, method, args):
        source = source_text('features/sessions/store.py')
        return ssh_read(self.alias, source + '\nimport os\ns = SessionStore(Path(os.environ.get("CODEX_HOME", str(Path.home()/".codex"))))\n' +
                        'print(json.dumps(s.' + method + '(**' + payload(args) + '), ensure_ascii=False))\n', timeout=30)

    def list(self, **kwargs):
        key = json.dumps(kwargs, sort_keys=True)
        with self.lock:
            previous = self.cache.get(key)
            if previous and time.monotonic() - previous[0] < 15:
                return [dict(row) for row in previous[1]]
            rows = self.call('list', kwargs)
            self.cache[key] = (time.monotonic(), rows)
            return rows

    def notification_changes(self, since=0):
        return self.call('notification_changes', {'since': since})

    def lifecycle_threads(self):
        return self.call('lifecycle_threads', {})

    def get(self, thread_id):
        return self.call('get', {'thread_id': thread_id})

    def history(self, thread_id, turn_limit=None):
        return self.call('history', {'thread_id': thread_id, 'turn_limit': turn_limit})

    def subagents(self, thread_id):
        return self.call('subagents', {'thread_id': thread_id})

    def subagent_history(self, thread_id, agent_id):
        return self.call('subagent_history', {'thread_id': thread_id, 'agent_id': agent_id})

    def recencies(self, identifiers):
        return self.call('recencies', {'identifiers': identifiers})


class RemoteCatalog:
    def __init__(self, alias):
        self.alias = alias
        self.kind_caches = {}
        self.lock = threading.RLock()

    def invalidate_models(self):
        with self.lock:
            for kind in ('catalog', 'models'):
                self.kind_caches.pop(kind, None)

    def _source(self):
        source = source_text('features/auth/tls.py') + '\n'
        source += source_text('features/accounts/models.py').replace('from bridge.features.auth.tls import client_context', '') + '\n'
        source += source_text('clients/codex/catalog.py').replace('from bridge.features.accounts.models import model_ids', '')
        source += '\nimport shutil\nhome=Path(os.environ.get("CODEX_HOME", str(Path.home()/".codex")))\n'
        source += 'runtime=shutil.which("codex") or str(Path.home()/".local/bin/codex")\n'
        source += 'reader=Catalog(home, runtime, allow_background_refresh=False)\n'
        return source

    def get(self, cwd, refresh=False, provider=None):
        with self.lock:
            key = (cwd, provider)
            previous = self.kind_caches.setdefault('catalog', {}).get(key)
            if previous and not refresh and time.monotonic() - previous[0] < 300:
                return previous[1]
            source = self._source()
            source += 'print(json.dumps(reader.get(' + payload(cwd) + ', refresh=' + payload(refresh) + ', provider=' + payload(provider) + '), ensure_ascii=False))\n'
            try:
                result = ssh_read(self.alias, source, timeout=120)
            except RemoteUnavailable as exc:
                raise CatalogError(str(exc)) from exc
            self.kind_caches['catalog'][key] = (time.monotonic(), result)
            return result

    def get_kind(self, kind, cwd, refresh=False, provider=None, query='', offset=0, limit=200, ids=None):
        if kind not in ('models', 'skills'):
            raise CatalogError('未知目录类型')
        with self.lock:
            identifiers = tuple(ids or [])
            if kind == 'skills':
                key = (kind, cwd, query, offset, limit, identifiers)
            else:
                key = (kind, cwd, provider)
            cache = self.kind_caches.setdefault(kind, {})
            previous = cache.get(key)
            if previous and not refresh and time.monotonic() - previous[0] < 300:
                return previous[1]
            source = self._source()
            source += ('print(json.dumps(reader.get_kind(' + payload(kind) + ', ' + payload(cwd) +
                        ', refresh=' + payload(refresh) + ', provider=' + payload(provider) +
                        ', query=' + payload(query) + ', offset=' + payload(offset) +
                        ', limit=' + payload(limit) + ', ids=' + payload(list(identifiers)) +
                        '), ensure_ascii=False))\n')
            try:
                result = ssh_read(self.alias, source, timeout=120)
            except RemoteUnavailable as exc:
                raise CatalogError(str(exc)) from exc
            cache[key] = (time.monotonic(), result)
            if kind == 'skills' and not refresh and result.get('cache', {}).get('state') in ('stale', 'legacy'):
                return self.get_kind(kind, cwd, refresh=True, provider=provider, query=query,
                                      offset=offset, limit=limit, ids=list(identifiers))
            return result

    def validate_skills(self, cwd, ids, refresh=False):
        source = self._source()
        source += ('print(json.dumps(reader.validate_skills(' + payload(cwd) + ', ' + payload(list(ids)) +
                    ', refresh=' + payload(refresh) + '), ensure_ascii=False))\n')
        try:
            result = ssh_read(self.alias, source, timeout=120)
        except RemoteUnavailable as exc:
            raise CatalogError(str(exc)) from exc
        return result


class AppHosts:
    def __init__(self, home):
        self.home = Path(home)

    def state(self):
        try:
            return json.loads((self.home / '.codex-global-state.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}

    def hosts(self):
        state = self.state()
        relevant = {p['hostId'] for p in state.get('remote-projects', [])}
        relevant.update(state.get('thread-project-membership-host-ids', {}).values())
        return {h['hostId']: h for h in state.get('codex-managed-remote-connections', [])
                if h.get('hostId') in relevant and h.get('alias')}

    def projects(self):
        state = self.state()
        rows = []
        for project in state.get('local-projects', {}).values():
            roots = project.get('rootPaths') or []
            if roots:
                rows.append({'key': 'local|' + project['id'], 'host': 'local', 'hostLabel': '此电脑',
                             'name': project['name'], 'cwd': roots[0]})
        hosts = self.hosts()
        for project in state.get('remote-projects', []):
            host = project['hostId']
            if host in hosts and project.get('remotePath'):
                rows.append({'key': host + '|' + project['id'], 'host': host,
                             'hostLabel': hosts[host].get('displayName') or hosts[host]['alias'],
                             'name': project.get('label') or project['remotePath'].rsplit('/', 1)[-1],
                             'cwd': project['remotePath']})
        return rows

    def decorate(self, rows, host, label):
        state = self.state()
        if host == 'local':
            projects = [{'id': p['id'], 'name': p['name'], 'roots': p.get('rootPaths', [])}
                        for p in state.get('local-projects', {}).values()]
        else:
            projects = [{'id': p['id'], 'name': p.get('label') or p['remotePath'].rsplit('/', 1)[-1], 'roots': [p['remotePath']]}
                        for p in state.get('remote-projects', []) if p['hostId'] == host]
        assignments = state.get('thread-project-assignments', {})
        def normalized(path):
            if host == 'local' and os.name == 'nt':
                return path.replace('\\', '/').rstrip('/').casefold()
            return path.rstrip('/')
        for row in rows:
            raw_cwd = row.get('cwd', '')
            cwd = normalized(raw_cwd)
            assigned = assignments.get(row['id'], {})
            project = next((p for p in projects if p['id'] == assigned.get('projectId') and assigned.get('hostId', host) == host), None)
            if project is None:
                matches = [(len(normalized(root)), p) for p in projects for root in p['roots'] if cwd == normalized(root) or cwd.startswith(normalized(root) + '/')]
                project = max(matches, key=lambda v: v[0])[1] if matches else None
            row.update(host=host, hostLabel=label,
                       projectKey=host + '|' + (project['id'] if project else cwd or 'unassigned'),
                       projectName=project['name'] if project else (raw_cwd.replace('\\', '/') if host == 'local' and os.name == 'nt' else raw_cwd).rstrip('/').rsplit('/', 1)[-1] or '未归类',
                       recency=row.get('recency_at_ms') or (row.get('recency_at') or 0) * 1000 or row.get('updated_at_ms') or (row.get('updated_at') or 0) * 1000)
        return rows


def upload_file(alias, thread, identifier, name, data, digest):
    """Transfer only the selected file to a private directory on its execution host."""
    value = {'thread': thread, 'id': identifier, 'name': name, 'data': base64.b64encode(data).decode('ascii'), 'sha256': digest}
    source = 'import os,json,base64,hashlib\nfrom pathlib import Path\nv=' + payload(value) + '''
p=Path(os.environ.get('CODEX_HOME', str(Path.home()/'.codex')))/'mobile-bridge'/'uploads'/v['thread']/v['id']/v['name']
p.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
b=base64.b64decode(v['data'],validate=True)
assert hashlib.sha256(b).hexdigest()==v['sha256']
if p.is_symlink(): raise ValueError('Invalid attachment path')
if p.exists():
    assert hashlib.sha256(p.read_bytes()).hexdigest()==v['sha256']
else:
    tmp=p.parent/(v['id']+'.part')
    with tmp.open('wb') as f: f.write(b)
    tmp.chmod(0o600)
    tmp.replace(p)
print(json.dumps({'path':str(p.resolve())}))
'''
    return ssh_read(alias, source, timeout=90)['path']
