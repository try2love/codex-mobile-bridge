"""Read existing App SSH metadata; turns still use the desktop's IPC owner."""
import base64
import copy
from contextlib import contextmanager
import json
import os
import re
import shlex
import subprocess
import tempfile
import threading
import time
from pathlib import Path, PurePath

from bridge.clients.codex.catalog import CatalogError, CatalogIdentityChanged
from bridge.features.network.retry import (
    ConnectionUnavailable as RemoteUnavailable, TransportUnavailable as SshTransportUnavailable, ssh_connection)
from bridge.features.network.stdio import run_with_input
from bridge.resources import source_text


def ssh_command(alias):
    if not isinstance(alias, str) or not alias or alias.startswith('-') or any(c.isspace() for c in alias):
        raise RemoteUnavailable('SSH 别名无效')
    return ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ClearAllForwardings=yes',
               '-o', 'ConnectTimeout=8', '-o', 'StrictHostKeyChecking=yes',
               '-o', 'UpdateHostKeys=no', alias,
               'exec "$SHELL" -lc "exec python3 -"']


def ssh_read(alias, source, timeout=18):
    with ssh_connection(alias).attempt():
        return _ssh_read(alias, source, timeout)


def _ssh_read(alias, source, timeout=18):
    command = ssh_command(alias)
    try:
        result = run_with_input(command, source, text=True, encoding='utf-8', capture_output=True, timeout=timeout)
        if result.returncode == 255:
            raise SshTransportUnavailable('SSH 主机暂不可用，请检查电脑上的连接')
        if result.returncode:
            raise RemoteUnavailable('SSH 读取失败，请检查电脑上该主机的 SSH 连接')
        return json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SshTransportUnavailable('SSH 主机暂不可用，请检查电脑上的连接') from exc
    except ValueError as exc:
        raise RemoteUnavailable('SSH 主机暂不可用，请检查电脑上的连接') from exc


def retry_ssh(alias, *, manual=True):
    connection = ssh_connection(alias)
    status = connection.status()
    if not manual and (not status['failures'] or status['retryAfterSeconds']):
        return
    # Reserve the one probe before returning to HTTP; repeated clicks coalesce.
    attempt = connection.attempt(manual=manual)
    try:
        attempt.__enter__()
    except RemoteUnavailable:
        return
    # The context is already entered above; exit it exactly once in the worker.
    def run():
        try:
            _ssh_read(alias, 'import json\nprint(json.dumps({"available": True}))\n')
        except BaseException as exc:
            attempt.__exit__(type(exc), exc, exc.__traceback__)
            if not isinstance(exc, RemoteUnavailable):
                raise
        else:
            attempt.__exit__(None, None, None)
    threading.Thread(target=run, daemon=True, name='ssh-manual-retry').start()


@contextmanager
def ssh_download(alias, source):
    """Keep remote binary output on disk, including full-response fallbacks."""
    command = ssh_command(alias)
    with ssh_connection(alias).attempt(), tempfile.TemporaryFile(prefix='bridge-ssh-download-') as stream:
        try:
            result = run_with_input(command, source, stdout=stream,
                                    stderr=subprocess.DEVNULL, timeout=60)
            if result.returncode == 255:
                raise SshTransportUnavailable('SSH 主机暂不可用，请检查电脑上的连接')
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
            raise SshTransportUnavailable('SSH 主机暂不可用，请检查电脑上的连接') from exc
        yield metadata, stream


def payload(value):
    data = base64.b64encode(json.dumps(value).encode()).decode()
    return 'json.loads(__import__("base64").b64decode(' + repr(data) + '))'


@contextmanager
def ssh_cli_probe(alias, value):
    """Read the inspected native endpoint, never start the target daemon."""
    from bridge.clients.codex.app_server import CodexAppServer
    generation = value['generation']
    inner = ('export CODEX_HOME=' + shlex.quote(generation['home']) + '; exec ' +
             shlex.quote(generation['executable']) + ' app-server proxy --sock ' + shlex.quote(generation['socket']))
    command = ssh_command(alias)
    command[-1] = 'exec "$SHELL" -lc ' + shlex.quote(inner)
    client = CodexAppServer(generation['socket'], command=command, check_socket=False,
                            client_name='codex-mobile-bridge-target-check')
    try:
        yield client
    finally:
        client.close()


class RemoteStore:
    def __init__(self, alias):
        self.alias = alias
        self.home = None  # Never map remote paths onto the local filesystem.
        self.cache = {}
        self.sources_cache = None
        self.kind_caches = {}
        self.lock = threading.Lock()
        self.poll_lock = threading.Lock()
        self.poll_results = {}
        self.poll_pending = set()

    def poll(self, method, args, ttl=15):
        """Passive discovery returns promptly even during the first SSH timeout."""
        key = json.dumps(args, sort_keys=True)
        with self.poll_lock:
            previous = self.poll_results.get(method)
            matches = previous is not None and previous[0] == key
            if matches and time.monotonic() - previous[1] < ttl:
                if previous[3]:
                    raise RemoteUnavailable(previous[3])
                return copy.deepcopy(previous[2])
            if method not in self.poll_pending:
                self.poll_pending.add(method)
                def update():
                    result, error = None, None
                    try:
                        result = self.call(method, args)
                    except (RemoteUnavailable, OSError, ValueError) as exc:
                        error = str(exc)
                    finally:
                        with self.poll_lock:
                            self.poll_results[method] = key, time.monotonic(), result, error
                            self.poll_pending.discard(method)
                threading.Thread(target=update, daemon=True, name='ssh-passive-read').start()
            if matches and not previous[3]:
                return copy.deepcopy(previous[2])
        raise RemoteUnavailable('正在后台读取 SSH 主机；本机会话可正常使用。')

    def call(self, method, args, *, home=None):
        source = source_text('features/sessions/store.py')
        suffix = ('\nimport os\nhome_source = "environment" if os.environ.get("CODEX_HOME") else "default"\n'
                  's = SessionStore(' + ('Path(' + repr(str(home)) + ')' if home is not None else
                    'Path(os.environ.get("CODEX_HOME", str(Path.home()/".codex")))') + ')\n'
                  'result = s.' + method + '(**' + payload(args) + ')\n')
        if method == 'discovery_diagnostics':
            suffix += 'result["homeSource"] = home_source\n'
        return ssh_read(self.alias, source + suffix + 'print(json.dumps(result, ensure_ascii=False))\n', timeout=30)

    def list(self, **kwargs):
        key = json.dumps(kwargs, sort_keys=True)
        with self.lock:
            previous = self.cache.get(key)
            if previous and time.monotonic() - previous[0] < 15:
                return [dict(row) for row in previous[1]]
            rows = self.call('list', kwargs)
            self.cache[key] = (time.monotonic(), rows)
            return [dict(row) for row in rows]

    def available_sources(self):
        with self.lock:
            if self.sources_cache and time.monotonic() - self.sources_cache[0] < 15:
                return list(self.sources_cache[1])
            sources = self.call('available_sources', {})
            self.sources_cache = (time.monotonic(), sources)
            return list(sources)

    def notification_changes(self, since=0):
        return self.call('notification_changes', {'since': since})

    def lifecycle_threads(self, root_id=None):
        return self.call('lifecycle_threads', {} if root_id is None else {'root_id': root_id})

    def archive_flags(self, thread_id):
        return self.call('archive_flags', {'thread_id': thread_id})

    def get(self, thread_id):
        return self.call('get', {'thread_id': thread_id})

    def history(self, thread_id, turn_limit=None, item_limit=None):
        return self.call('history', {'thread_id': thread_id, 'turn_limit': turn_limit, 'item_limit': item_limit})

    def subagents(self, thread_id):
        return self.call('subagents', {'thread_id': thread_id})

    def subagent_history(self, thread_id, agent_id):
        return self.call('subagent_history', {'thread_id': thread_id, 'agent_id': agent_id})

    def recencies(self, identifiers):
        return self.call('recencies', {'identifiers': identifiers})

    def discovery_diagnostics(self, thread_id=None):
        return self.call('discovery_diagnostics', {'thread_id': thread_id})


class RemoteCatalog:
    def __init__(self, alias):
        self.alias = alias
        self.kind_caches = {}
        self.lock = threading.RLock()
        self.kind_locks = {kind: threading.RLock() for kind in ('catalog', 'models', 'skills')}
        self.skill_refreshing = set()
        self.model_refreshing = set()
        self.model_timers = {}
        self.model_retry_at = {}
        self.model_closed = False

    def invalidate_models(self):
        with self.kind_locks['catalog'], self.kind_locks['models'], self.lock:
            for kind in ('catalog', 'models'):
                self.kind_caches.pop(kind, None)
            for timer in self.model_timers.values():
                timer.cancel()
            self.model_timers.clear()

    def stop_model_refresh(self):
        with self.lock:
            self.model_closed = True
            for timer in self.model_timers.values():
                timer.cancel()
            self.model_timers.clear()

    def _source(self):
        source = source_text('features/auth/tls.py') + '\n'
        source += source_text('features/accounts/models.py').replace('from bridge.features.auth.tls import client_context', '') + '\n'
        source += source_text('clients/codex/configuration.py') + '\n'
        source += source_text('clients/codex/catalog.py').replace(
            'from bridge.features.accounts.models import model_catalog', '').replace(
            'from bridge.clients.codex.configuration import magpie_managed', '')
        source += '\nimport shutil\nhome=Path(os.environ.get("CODEX_HOME", str(Path.home()/".codex")))\n'
        source += 'runtime=Catalog.find_ssh_runtime(home) or str(Path.home()/".local/bin/codex")\n'
        source += 'reader=Catalog(home, runtime, allow_background_refresh=False, auth_status_only=True)\n'
        return source

    def _start_model_refresh(self, key, cwd, provider):
        if self.model_closed or key in self.model_refreshing or time.time() < self.model_retry_at.get(key, 0):
            return
        self.model_refreshing.add(key)
        def update():
            try:
                self.get_kind('models', cwd, refresh=True, provider=provider)
            except (OSError, ValueError, CatalogError):
                pass
            finally:
                with self.lock:
                    self.model_retry_at[key] = time.time() + 60
                    self.model_refreshing.discard(key)
                    result = self.kind_caches.get('models', {}).get(key)
                    updated_at = (result[1].get('cache') or {}).get('updatedAt') if result else None
                    self._schedule_model_refresh(key, cwd, provider, updated_at or time.time(), retry=True)
        threading.Thread(target=update, daemon=True, name='ssh-model-refresh').start()

    def _schedule_model_refresh(self, key, cwd, provider, updated_at, retry=False, replace=False):
        if self.model_closed:
            return
        if replace and key in self.model_timers:
            self.model_timers.pop(key).cancel()
        if key in self.model_timers:
            return
        delay = max(60 if retry else 1, updated_at + 12 * 60 * 60 - time.time())
        def due():
            with self.lock:
                self.model_timers.pop(key, None)
                self._start_model_refresh(key, cwd, provider)
        timer = threading.Timer(delay, due)
        timer.daemon = True
        self.model_timers[key] = timer
        timer.start()

    def get(self, cwd, refresh=False, provider=None):
        with self.kind_locks['catalog']:
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
        identifiers = tuple(ids or [])
        key = ((kind, cwd, query, offset, limit, identifiers) if kind == 'skills'
               else (kind, cwd, provider))
        # Cached Skills stay readable while a refresh owns the slow request lock.
        with self.lock:
            cache = self.kind_caches.setdefault(kind, {})
            previous = cache.get(key)
            if kind == 'skills' and previous and not refresh and time.monotonic() - previous[0] < 300:
                return previous[1]
        with self.kind_locks[kind]:
            with self.lock:
                cache = self.kind_caches.setdefault(kind, {})
                previous = cache.get(key)
                if kind == 'skills' and previous and not refresh and time.monotonic() - previous[0] < 300:
                    return previous[1]
            source = self._source()
            source += ('try:\n print(json.dumps(reader.get_kind(' + payload(kind) + ', ' + payload(cwd) +
                        ', refresh=' + payload(refresh) + ', provider=' + payload(provider) +
                        ', query=' + payload(query) + ', offset=' + payload(offset) +
                        ', limit=' + payload(limit) + ', ids=' + payload(list(identifiers)) +
                        '), ensure_ascii=False))\n'
                        'except CatalogIdentityChanged as exc:\n print(json.dumps({\"error\":str(exc),\"code\":exc.code}))\n')
            try:
                result = ssh_read(self.alias, source, timeout=120)
            except RemoteUnavailable as exc:
                raise CatalogError(str(exc)) from exc
            if result.get('code') == 'model_identity_changed':
                raise CatalogIdentityChanged(result.get('error') or '模型接入已变化，请重试')
            with self.lock:
                cache[key] = (time.monotonic(), result)
                if kind == 'models':
                    metadata = result.get('cache') or {}
                    if metadata.get('state') == 'stale' and not refresh:
                        self._start_model_refresh(key, cwd, provider)
                    elif metadata.get('state') == 'fresh' and metadata.get('updatedAt'):
                        self._schedule_model_refresh(key, cwd, provider, metadata['updatedAt'], replace=refresh)
                elif not refresh and result.get('cache', {}).get('state') in ('stale', 'legacy'):
                    self._start_skill_refresh(key, cwd, provider, query, offset, limit, identifiers)
            return result

    def _start_skill_refresh(self, key, cwd, provider, query, offset, limit, ids):
        if self.model_closed or key in self.skill_refreshing:
            return
        self.skill_refreshing.add(key)
        def update():
            try:
                self.get_kind('skills', cwd, refresh=True, provider=provider, query=query,
                              offset=offset, limit=limit, ids=ids)
            except (OSError, ValueError, CatalogError):
                pass  # Keep the last successful cached list if the remote scan fails.
            finally:
                with self.lock:
                    self.skill_refreshing.discard(key)
        threading.Thread(target=update, daemon=True, name='ssh-skill-refresh').start()

    def validate_skills(self, cwd, ids, refresh=False):
        source = self._source()
        source += ('print(json.dumps(reader.validate_skills(' + payload(cwd) + ', ' + payload(list(ids)) +
                    ', refresh=' + payload(refresh) + '), ensure_ascii=False))\n')
        try:
            result = ssh_read(self.alias, source, timeout=120)
        except RemoteUnavailable as exc:
            raise CatalogError(str(exc)) from exc
        return result


class RemoteCliModels:
    """Read a joined CLI's API catalog on its SSH host without returning secrets."""

    def __init__(self, alias):
        self.alias = alias

    @staticmethod
    def _source():
        source = source_text('features/auth/tls.py') + '\n'
        source += source_text('features/accounts/models.py').replace(
            'from bridge.features.auth.tls import client_context', '') + '\n'
        source += source_text('clients/codex/configuration.py') + '\n'
        source += source_text('clients/codex/catalog.py').replace(
            'from bridge.features.accounts.models import model_catalog', '').replace(
            'from bridge.clients.codex.configuration import magpie_managed', '') + '\n'
        source += source_text('platforms/linux/codex_daemon_env.py') + '\n'
        source += '_linux_read_process_environment = read_process_environment\n'
        source += source_text('platforms/macos/force_exit.py') + '\n'
        source += source_text('platforms/macos/codex_daemon_env.py').replace(
            'from bridge.platforms.macos.force_exit import Native', '') + '\n'
        source += '_macos_read_process_environment = read_process_environment\n'
        dispatcher = source_text('platforms/codex_daemon_env.py')
        dispatcher = dispatcher.replace(
            'from bridge.platforms.macos.codex_daemon_env import read_process_environment',
            'read_process_environment = _macos_read_process_environment').replace(
            'from bridge.platforms.linux.codex_daemon_env import read_process_environment',
            'read_process_environment = _linux_read_process_environment').replace(
            'from bridge.platforms.windows.codex_daemon_env import read_process_environment',
            "raise DaemonEnvironmentUnavailable('SSH Windows model catalog is unsupported')")
        source += dispatcher + '\n'
        source += source_text('clients/codex/remote_models.py') + '\n'
        return source

    def _call(self, operation, home, cwd, provider, config, account=None, native_models=None):
        home = str(home) if isinstance(home, PurePath) else home
        cwd = str(cwd) if isinstance(cwd, PurePath) else cwd
        value = {'operation': operation, 'home': home, 'cwd': cwd, 'provider': provider,
                 'config': config, 'account': account or {}, 'nativeModels': native_models or []}
        source = self._source() + '\nprint(json.dumps(remote_model_worker(' + payload(value) + '), ensure_ascii=False))\n'
        try:
            result = ssh_read(self.alias, source, timeout=120 if operation == 'models' else 20)
        except RemoteUnavailable as exc:
            raise CatalogError(str(exc)) from exc
        if not isinstance(result, dict):
            raise CatalogError('SSH 模型目录格式无效')
        if result.get('error'):
            raise CatalogError(result['error'])
        identity = result.get('identity')
        if not isinstance(identity, str) or len(identity) != 64 or any(char not in '0123456789abcdef' for char in identity):
            raise CatalogError('SSH 模型目录身份无效')
        return result

    def identity(self, home, cwd, provider, config, account=None):
        return self._call('identity', home, cwd, provider, config, account)['identity']

    def read_models(self, home, cwd, provider, config, account=None, native_models=None):
        return self._call('models', home, cwd, provider, config, account, native_models)


def remote_model_identity(alias, home, cwd, provider, config, account=None):
    return RemoteCliModels(alias).identity(home, cwd, provider, config, account)


def remote_model_catalog(alias, home, cwd, provider, config, account=None, native_models=None):
    return RemoteCliModels(alias).read_models(home, cwd, provider, config, account, native_models)


def _windows_path(path):
    return bool(re.match(r'^[a-z]:[\\/]|^\\\\|^//[?.]/', path, re.I))


def project_path_name(path):
    """Use the path's own separators, including for Windows SSH targets."""
    value = path.replace('\\', '/') if _windows_path(path) else path
    return value.rstrip('/').rsplit('/', 1)[-1]


class AppHosts:
    def __init__(self, home):
        self.home = Path(home)
        self._hosts_lock = threading.Lock()
        self._hosts_cache = None

    def state(self):
        try:
            return json.loads((self.home / '.codex-global-state.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}

    def hosts(self, *, cached=False):
        if cached:
            return self._observed_hosts()
        state = self.state()
        return {h['hostId']: h for h in state.get('codex-managed-remote-connections', [])
                if isinstance(h, dict) and isinstance(h.get('hostId'), str) and h['hostId']
                and isinstance(h.get('alias'), str) and h['alias']
                and not h['alias'].startswith('-') and not any(c.isspace() for c in h['alias'])}

    def _signature(self):
        try:
            stat = (self.home / '.codex-global-state.json').stat()
            return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns
        except OSError:
            return None

    def _observed_hosts(self):
        with self._hosts_lock:
            signature = self._signature()
            if signature is None:
                self._hosts_cache = None
                return {}
            if self._hosts_cache is None or signature != self._hosts_cache[0]:
                hosts = self.hosts()
                # An atomic replacement during parsing belongs to the next discovery.
                if signature != self._signature():
                    self._hosts_cache = None
                    return {}
                self._hosts_cache = signature, hosts
            return copy.deepcopy(self._hosts_cache[1])

    def lifecycle_hosts(self):
        """Keep shutdown/account checks scoped to App project and membership hosts."""
        state = self.state()
        relevant = {p.get('hostId') for p in state.get('remote-projects', []) if isinstance(p, dict)}
        relevant.update(state.get('thread-project-membership-host-ids', {}).values())
        return {host_id: host for host_id, host in self.hosts().items() if host_id in relevant}

    def project_import(self, project):
        """Prepare the native App import, never edit its live global state.

        Applying this file also applies connection settings. Refuse an existing
        multi-host/pending import rather than connecting unrelated hosts.
        """
        host = self.hosts().get(project['host'])
        if not host or host['alias'] != project.get('hostIdentity'):
            raise ValueError('原 SSH 主机配置已变化，请重新选择项目')
        path = self.home / 'codex-app' / 'config.json'
        if path.is_symlink() or path.parent.is_symlink():
            raise ValueError('Codex App 项目配置路径不可用，请在电脑 App 添加项目')
        original = path.read_bytes() if path.exists() else None
        try:
            config = json.loads(original) if original is not None else {'version': 1}
            if not isinstance(config, dict) or config.get('version', 1) != 1 or set(config) - {
                    'version', 'remoteConnections', 'remoteConnectionMaxRetryAttempts', 'sshConnectTimeoutSeconds'}:
                raise ValueError()
            rows = config.setdefault('remoteConnections', [])
            if not isinstance(rows, list) or len(rows) > 1:
                raise ValueError()
            if rows and (not isinstance(rows[0], dict) or set(rows[0]) - {'sshAlias', 'projects'} or rows[0].get('sshAlias') != host['alias']):
                raise ValueError()
            saved = {(row['host'], row['cwd']): row for row in self.projects()}
            for row in rows:
                if not isinstance(row.get('projects', []), list):
                    raise ValueError()
                for item in row.get('projects', []):
                    if not isinstance(item, dict):
                        raise ValueError()
                    native = saved.get((project['host'], item.get('remotePath')))
                    if (set(item) - {'remotePath', 'label'} or not native
                            or item.get('label', native['name']) != native['name']):
                        raise ValueError()
            # The App keeps this timeout only in memory; do not apply an
            # unverified global timeout while registering a single project.
            if 'sshConnectTimeoutSeconds' in config:
                raise ValueError()
            state = self.state()
            retry = state.get('remote-connection-max-retry-attempts')
            if 'remoteConnectionMaxRetryAttempts' in config and config['remoteConnectionMaxRetryAttempts'] != retry:
                raise ValueError()
            if retry is not None:
                config['remoteConnectionMaxRetryAttempts'] = retry
            if not rows:
                rows.append({'sshAlias': host['alias'], 'projects': []})
            projects = rows[0].setdefault('projects', [])
            if not any(item['remotePath'] == project['cwd'] for item in projects):
                projects.append({'remotePath': project['cwd'], 'label': project['name']})
        except (TypeError, KeyError, ValueError) as exc:
            raise ValueError('Codex App 存在其他待应用的远程配置，请先在电脑 App 添加此 SSH 项目，避免影响其他连接') from exc
        return path, original, config

    def write_project_import(self, prepared, write):
        path, original, config = prepared
        if path.is_symlink() or path.parent.is_symlink() or (path.read_bytes() if path.exists() else None) != original:
            raise ValueError('Codex App 配置已变化，请重新选择项目')
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        write(path, config)

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
                             'name': project.get('label') or project_path_name(project['remotePath']),
                             'cwd': project['remotePath']})
        return rows

    def decorate(self, rows, host, label, native_memberships=()):
        state = self.state()
        if host == 'local':
            projects = [{'id': p['id'], 'name': p['name']}
                        for p in state.get('local-projects', {}).values()]
        else:
            projects = [{'id': p['id'], 'name': p.get('label') or project_path_name(p['remotePath']),
                         'cwd': p['remotePath']}
                        for p in state.get('remote-projects', []) if p['hostId'] == host]
        assignments = state.get('thread-project-assignments', {})
        projectless = set(state.get('projectless-thread-ids', []))
        project_ids = {project['id'] for project in projects}
        legacy_ids = {server_id: legacy_id
                      for mapping in state.get('app-server-project-id-by-legacy-project-id-by-host', {}).values()
                      for legacy_id, server_id in mapping.items() if legacy_id in project_ids}
        for row in rows:
            assigned = assignments.get(row['id'], {})
            project = row.pop('_nativeProject', None)
            explicitly_projectless = row['id'] in projectless
            # Empty native metadata also occurs while Codex migrates memberships.
            if project is None and not explicitly_projectless and row['id'] not in native_memberships:
                project = next((p for p in projects if p['id'] == assigned.get('projectId') and assigned.get('hostId', host) == host), None)
                # The App also groups legacy SSH chats by their registered host/root,
                # without persisting a thread projectId. Keep explicit removals authoritative.
                if project is None and host != 'local' and not assigned and row.get('workspaceKind') != 'projectless':
                    project = next((p for p in projects if p['cwd'] and p['cwd'] == row.get('cwd')), None)
            row.update(host=host, hostLabel=label,
                       projectKey=host + '|' + (legacy_ids.get(project['id'], project['id']) if project else 'unassigned'),
                       projectName=project['name'] if project else '未归类',
                       recency=row.get('recency_at_ms') or (row.get('recency_at') or 0) * 1000 or row.get('updated_at_ms') or (row.get('updated_at') or 0) * 1000)
        return rows


def upload_file(alias, thread, identifier, name, data, digest, *, reader=None):
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
    return (reader or ssh_read)(alias, source, timeout=90)['path']
