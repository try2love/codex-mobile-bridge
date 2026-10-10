"""Read model/skill metadata using the desktop's bundled runtime.

This short-lived helper only reads models, skills, login and configuration capabilities.
All thread mutations and all turns remain on the existing desktop IPC owner.
"""
import hashlib
import json
import logging
import os
import queue
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from bridge.features.accounts.models import model_ids

logger = logging.getLogger(__name__)
SKILL_CACHE = 'skill-cache.json'
SKILL_DB = 'skills-v2.sqlite3'
SKILL_PAGE_LIMIT = 200
SKILL_MAX_PAGE_LIMIT = 500


class CatalogError(RuntimeError):
    pass


class SkillPathChanged(ValueError):
    pass


class SkillStore:
    """Small per-CODEX_HOME durable cache; no credentials or API metadata are stored."""

    def __init__(self, home):
        self.home = Path(home).resolve()
        self.root = self.home / '.codex-mobile-bridge'
        self.path = self.root / SKILL_DB
        self.legacy_path = self.root / SKILL_CACHE

    def bucket(self, cwd):
        value = (str(self.home) + '\0' + str(Path(cwd).resolve())).encode()
        return hashlib.sha256(value).hexdigest()

    def _connect(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        db = None
        try:
            db = sqlite3.connect(str(self.path), timeout=3)
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA busy_timeout=3000')
            db.execute('PRAGMA foreign_keys=ON')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS skill_records(
                  skill_id TEXT PRIMARY KEY,
                  path TEXT UNIQUE NOT NULL,
                  name TEXT NOT NULL,
                  display_name TEXT,
                  description TEXT,
                  scope TEXT,
                  search_text TEXT NOT NULL,
                  size INTEGER,
                  mtime_ns INTEGER,
                  content_hash TEXT,
                  updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS skill_buckets(
                  bucket_id TEXT PRIMARY KEY,
                  cwd TEXT NOT NULL,
                  refreshed_at INTEGER NOT NULL,
                  runtime_version TEXT,
                  errors_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS bucket_skills(
                  bucket_id TEXT NOT NULL REFERENCES skill_buckets(bucket_id) ON DELETE CASCADE,
                  skill_id TEXT NOT NULL REFERENCES skill_records(skill_id) ON DELETE CASCADE,
                  position INTEGER NOT NULL,
                  PRIMARY KEY(bucket_id, skill_id)
                );
                CREATE INDEX IF NOT EXISTS bucket_skills_position ON bucket_skills(bucket_id, position);
            ''')
            if 'resolved_path' not in {row[1] for row in db.execute('PRAGMA table_info(skill_records)')}:
                db.execute('ALTER TABLE skill_records ADD COLUMN resolved_path TEXT')
            self.path.chmod(0o600)
            return db
        except sqlite3.DatabaseError:
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass
            for suffix in ('', '-wal', '-shm'):
                Path(str(self.path) + suffix).unlink(missing_ok=True)
            db = sqlite3.connect(str(self.path), timeout=3)
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA busy_timeout=3000')
            db.execute('PRAGMA foreign_keys=ON')
            db.executescript('''CREATE TABLE skill_records(skill_id TEXT PRIMARY KEY,path TEXT UNIQUE NOT NULL,name TEXT NOT NULL,display_name TEXT,description TEXT,scope TEXT,search_text TEXT NOT NULL,size INTEGER,mtime_ns INTEGER,content_hash TEXT,updated_at INTEGER NOT NULL);CREATE TABLE skill_buckets(bucket_id TEXT PRIMARY KEY,cwd TEXT NOT NULL,refreshed_at INTEGER NOT NULL,runtime_version TEXT,errors_json TEXT NOT NULL);CREATE TABLE bucket_skills(bucket_id TEXT NOT NULL REFERENCES skill_buckets(bucket_id) ON DELETE CASCADE,skill_id TEXT NOT NULL REFERENCES skill_records(skill_id) ON DELETE CASCADE,position INTEGER NOT NULL,PRIMARY KEY(bucket_id,skill_id));CREATE INDEX bucket_skills_position ON bucket_skills(bucket_id,position);''')
            db.execute('ALTER TABLE skill_records ADD COLUMN resolved_path TEXT')
            self.path.chmod(0o600)
            return db

    @staticmethod
    def _public(row):
        source = dict(row) if not isinstance(row, dict) else row
        value = source.get('skill') if isinstance(source.get('skill'), dict) else source
        return {
            'id': value.get('skill_id') or value.get('id'),
            'path': value.get('path'),
            'name': value.get('name'),
            'description': value.get('description', ''),
            'scope': value.get('scope'),
            'displayName': value.get('display_name') or value.get('displayName') or value.get('name'),
        }

    def read(self, cwd, query='', offset=0, limit=SKILL_PAGE_LIMIT, ids=None):
        bucket = self.bucket(cwd)
        query = (query or '').strip().casefold()
        ids = list(dict.fromkeys(ids or []))[:8]
        db = self._connect()
        try:
            meta = db.execute('SELECT * FROM skill_buckets WHERE bucket_id=?', (bucket,)).fetchone()
            if meta is None:
                return None
            sql = '''SELECT r.* FROM bucket_skills b
                     JOIN skill_records r ON r.skill_id=b.skill_id
                     WHERE b.bucket_id=? AND instr(r.search_text, ?)>0
                     ORDER BY b.position,r.skill_id LIMIT ? OFFSET ?'''
            rows = db.execute(sql, (bucket, query, limit, offset)).fetchall()
            selected = []
            if ids:
                marks = ','.join('?' for _ in ids)
                selected = db.execute(f'''SELECT r.* FROM bucket_skills b
                    JOIN skill_records r ON r.skill_id=b.skill_id
                    WHERE b.bucket_id=? AND b.skill_id IN ({marks}) ORDER BY b.position''',
                    [bucket, *ids]).fetchall()
            total = db.execute('''SELECT count(*) FROM bucket_skills b
                JOIN skill_records r ON r.skill_id=b.skill_id
                WHERE b.bucket_id=? AND instr(r.search_text, ?)>0''', (bucket, query)).fetchone()[0]
            errors = json.loads(meta['errors_json'] or '[]')
            return {
                'rows': [dict(row) for row in rows],
                'selected': [dict(row) for row in selected],
                'total': total, 'offset': offset, 'limit': limit,
                'refreshed_at': meta['refreshed_at'], 'bucket_id': bucket,
                'runtime_version': meta['runtime_version'], 'errors': errors,
            }
        finally:
            db.close()

    def write(self, cwd, skills, errors=None, runtime_version=None):
        bucket = self.bucket(cwd)
        now = int(time.time())
        records = []
        seen = set()
        for skill in skills:
            path = Path(skill['path'])
            if not path.is_absolute():
                path = Path(cwd) / path
            path = Path(os.path.abspath(path))
            identifier = hashlib.sha256(str(path).encode()).hexdigest()
            if identifier in seen:
                continue
            seen.add(identifier)
            size = mtime = content_hash = resolved_path = None
            try:
                resolved = path.resolve(strict=True)
                resolved_path = str(resolved)
                stat = resolved.stat()
                size, mtime = stat.st_size, stat.st_mtime_ns
                content_hash = hashlib.sha256(resolved.read_bytes()).hexdigest()
            except (OSError, RuntimeError):
                pass
            text = ' '.join(str(skill.get(key) or '') for key in ('name', 'displayName', 'description')).casefold()
            records.append((identifier, str(path), skill['name'], skill.get('displayName') or skill['name'],
                            skill.get('description', ''), skill.get('scope'), text, size, mtime, content_hash, now, resolved_path))
        db = self._connect()
        try:
            with db:
                db.executemany('''INSERT INTO skill_records(skill_id,path,name,display_name,description,scope,search_text,size,mtime_ns,content_hash,updated_at,resolved_path)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(skill_id) DO UPDATE SET path=excluded.path,name=excluded.name,display_name=excluded.display_name,
                      description=excluded.description,scope=excluded.scope,search_text=excluded.search_text,size=excluded.size,
                      mtime_ns=excluded.mtime_ns,content_hash=excluded.content_hash,updated_at=excluded.updated_at,resolved_path=excluded.resolved_path''', records)
                db.execute('''INSERT INTO skill_buckets(bucket_id,cwd,refreshed_at,runtime_version,errors_json)
                    VALUES(?,?,?,?,?) ON CONFLICT(bucket_id) DO UPDATE SET cwd=excluded.cwd,refreshed_at=excluded.refreshed_at,
                      runtime_version=excluded.runtime_version,errors_json=excluded.errors_json''',
                    (bucket, str(Path(cwd).resolve()), now, runtime_version, json.dumps(errors or [], ensure_ascii=False)))
                db.execute('DELETE FROM bucket_skills WHERE bucket_id=?', (bucket,))
                db.executemany('INSERT INTO bucket_skills(bucket_id,skill_id,position) VALUES(?,?,?)',
                               [(bucket, row[0], position) for position, row in enumerate(records)])
                db.execute('''DELETE FROM skill_records WHERE NOT EXISTS
                    (SELECT 1 FROM bucket_skills WHERE bucket_skills.skill_id=skill_records.skill_id)''')
            return bucket
        finally:
            db.close()

    def legacy(self):
        try:
            value = json.loads(self.legacy_path.read_text(encoding='utf-8'))
            return value if isinstance(value, list) and all(isinstance(item, dict) for item in value) else []
        except (OSError, ValueError):
            return []

    def validate(self, cwd, ids):
        ids = list(dict.fromkeys(ids))[:8]
        if not ids:
            return []
        bucket = self.bucket(cwd)
        db = self._connect()
        try:
            marks = ','.join('?' for _ in ids)
            rows = db.execute(f'''SELECT r.* FROM bucket_skills b JOIN skill_records r ON r.skill_id=b.skill_id
                WHERE b.bucket_id=? AND r.skill_id IN ({marks})''', [bucket, *ids]).fetchall()
            by_id = {row['skill_id']: dict(row) for row in rows}
            result = []
            for identifier in ids:
                row = by_id.get(identifier)
                if not row:
                    raise ValueError('Skill 不可用，请刷新列表')
                try:
                    path = Path(row['path']).resolve(strict=True)
                except (OSError, RuntimeError):
                    raise ValueError('Skill 不可用，请刷新列表') from None
                # Trust the exact file discovered by native skills/list, including
                # installed links and plugins outside CODEX_HOME. Do not silently
                # authorize a changed target during send-time cache refresh.
                if row['resolved_path'] is None:
                    raise ValueError('Skill 不可用，请刷新列表')
                if str(path) != row['resolved_path']:
                    raise SkillPathChanged('Skill 链接目标已变化，请刷新列表后重新选择')
                try:
                    stat = path.stat()
                    data = path.read_bytes()
                except OSError:
                    raise ValueError('Skill 不可用，请刷新列表') from None
                if row['size'] != stat.st_size or row['mtime_ns'] != stat.st_mtime_ns or row['content_hash'] != hashlib.sha256(data).hexdigest():
                    raise ValueError('Skill 已修改，请刷新列表')
                result.append(self._public({**row, 'path': str(path)}))
            return result
        finally:
            db.close()


class Catalog:
    METHODS = {"initialize", "model/list", "skills/list", "config/read", "account/read", "configRequirements/read"}

    def __init__(self, codex_home, executable=None, allow_background_refresh=True):
        self.home = Path(codex_home)
        self._automatic_runtime = not executable
        self._executable = Path(executable) if executable else self.find_runtime()
        self.cache = {}
        self.lock = threading.Lock()
        self.kind_locks = {'models': threading.Lock(), 'skills': threading.Lock()}
        self.kind_caches = {'models': {}, 'skills': {}}
        self.skill_store = SkillStore(self.home)
        self.skill_refreshing = set()
        self.allow_background_refresh = allow_background_refresh

    @property
    def executable(self):
        # Desktop updates remove versioned Windows runtimes while the gateway
        # stays running. Refresh automatic selection before the next operation.
        if self._automatic_runtime and (not self._executable or not self._executable.is_file()):
            self._executable = self.find_runtime()
        return self._executable

    @executable.setter
    def executable(self, value):
        self._automatic_runtime = not value
        self._executable = Path(value) if value else None

    def invalidate_models(self):
        with self.lock:
            self.cache.clear()
        with self.kind_locks['models']:
            self.kind_caches['models'].clear()

    @staticmethod
    def find_runtime():
        if sys.platform == 'linux':
            return Catalog.find_linux_runtime()
        if os.name == 'nt':
            local = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local')))
            versions = []
            for path in (local / 'OpenAI/Codex/bin').glob('*/codex.exe'):
                try:
                    versions.append((path.stat().st_mtime, path))
                except OSError:
                    continue  # An update can remove a candidate during discovery.
            candidates = [path for _, path in sorted(versions, reverse=True)]
            candidates += [local / 'Programs/Codex/resources/codex.exe',
                           local / 'Programs/ChatGPT/resources/codex.exe']
            for candidate in candidates:
                if candidate.is_file():
                    return candidate
            try:
                result = subprocess.run(
                    ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                     '[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); '
                     'Get-AppxPackage OpenAI.Codex | Select-Object -ExpandProperty InstallLocation'],
                    capture_output=True, text=True, encoding='utf-8', timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW)
                for folder in result.stdout.splitlines():
                    candidate = Path(folder.strip()) / 'app/resources/codex.exe'
                    if candidate.is_file():
                        return candidate
            except (OSError, subprocess.TimeoutExpired):
                pass
            executable = shutil.which('codex.exe')
            return Path(executable) if executable else None
        for app in ("ChatGPT", "Codex"):
            candidate = Path('/Applications') / (app + '.app') / 'Contents/Resources/codex-cli/bin/codex'
            if candidate.is_file():
                return candidate
        return None

    @staticmethod
    def find_linux_runtime():
        # Prefer a desktop-bundled runtime; resolve symlinked launchers first.
        roots = []
        for name in ('chatgpt', 'codex'):
            launcher = shutil.which(name)
            if launcher:
                roots.append(Path(launcher).resolve().parent)
        roots.extend(Path(folder) for folder in
                     ('/opt/ChatGPT', '/opt/chatgpt', '/opt/Codex', '/opt/codex',
                      '/usr/lib/chatgpt', '/usr/lib/codex'))
        for root in roots:
            for relative in ('resources/codex-cli/bin/codex', 'resources/codex'):
                candidate = root / relative
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    return candidate
        # Nonstandard packages can still use the explicit codexBin setting.
        executable = shutil.which('codex')
        return Path(executable) if executable else None

    def linux_permission_capabilities(self, cwd, reader):
        if sys.platform != 'linux':
            raise ValueError('Linux permission discovery is unavailable on this host')
        return self._fetch(cwd, kind='linux-permissions', request_timeout=5, permission_reader=reader)

    def _fetch(self, cwd, provider=None, kind='catalog', request_timeout=90, permission_reader=None):
        executable = self.executable
        if not executable:
            raise CatalogError("找不到桌面 App 的 Codex 运行时")
        env = dict(os.environ)
        env['CODEX_HOME'] = str(self.home)
        options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        try:
            process = subprocess.Popen([str(executable), 'app-server', '--listen', 'stdio://'], cwd=cwd, env=env,
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       text=True, encoding='utf-8', **options)
        except OSError as exc:
            raise CatalogError('无法启动 Codex 运行时，请检查运行配置中的程序路径、项目目录和访问权限') from exc
        messages = queue.Queue()
        def read():
            try:
                for line in process.stdout:
                    try:
                        messages.put(json.loads(line))
                    except ValueError:
                        continue
            finally:
                messages.put(None)
        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        counter = 0
        def request(method, params, timeout=request_timeout):
            nonlocal counter
            linux_permission_read = (sys.platform == 'linux' and kind == 'linux-permissions' and
                                     method in ('permissionProfile/list', 'experimentalFeature/list'))
            if method not in self.METHODS and not linux_permission_read:
                raise ValueError("目录接口只允许读取模型、Skill 与能力配置")
            counter += 1
            process.stdin.write(json.dumps({'id': counter, 'method': method, 'params': params}) + '\n')
            process.stdin.flush()
            deadline = time.monotonic() + timeout
            while True:
                try:
                    value = messages.get(timeout=max(.01, deadline - time.monotonic()))
                except queue.Empty as exc:
                    raise CatalogError("桌面模型/Skill 目录读取超时") from exc
                if value is None:
                    raise CatalogError("桌面目录查询已断开")
                if value.get('id') != counter:
                    if time.monotonic() >= deadline:
                        raise CatalogError("桌面目录读取超时")
                    continue
                if 'error' in value:
                    raise CatalogError(value['error'].get('message', '目录读取失败'))
                return value['result']
        started = time.monotonic()
        logger.info('catalog fetch start kind=%s executable=%s cwd=%s provider=%s', kind, executable, cwd, provider)
        try:
            request('initialize', {'clientInfo': {'name': 'codex_mobile_catalog', 'title': 'Mobile catalog', 'version': '0.2'},
                                   'capabilities': {'experimentalApi': True}})
            process.stdin.write('{"method":"initialized"}\n')
            process.stdin.flush()
            if kind == 'linux-permissions':
                return permission_reader(request, cwd)
            if kind == 'models':
                phase = time.monotonic();catalog = self.read_models(request, cwd, provider)
                logger.info('catalog models elapsed=%.3fs source=%s count=%d error=%r',
                            time.monotonic()-phase, catalog.get('modelSource'), len(catalog.get('models', [])), catalog.get('modelError'))
                logger.info('catalog fetch complete elapsed=%.3fs', time.monotonic()-started)
                return {**catalog, 'skillEntries': [],
                        'fastMode': self.fast_mode(request, cwd) if catalog['modelSource'] == 'codex' else {'allowed': False}}
            if kind == 'skills':
                phase = time.monotonic()
                # A runtime busy with an active task can stall a Skill scan. Run
                # this request independently from models so the model picker is
                # never blocked by it.
                skills = request('skills/list', {'cwds': [cwd], 'forceReload': False}, timeout=request_timeout)
                logger.info('catalog skills elapsed=%.3fs count=%d', time.monotonic()-phase, len(skills.get('data', [])))
                logger.info('catalog fetch complete elapsed=%.3fs', time.monotonic()-started)
                return {'models': [], 'kind': 'skills', 'skillEntries': skills.get('data', []), 'fastMode': {'allowed': False},
                        'modelSource': 'codex'}
            phase = time.monotonic();catalog = self.read_models(request, cwd, provider)
            logger.info('catalog models elapsed=%.3fs source=%s count=%d error=%r',
                        time.monotonic()-phase, catalog.get('modelSource'), len(catalog.get('models', [])), catalog.get('modelError'))
            phase = time.monotonic();skills = request('skills/list', {'cwds': [cwd], 'forceReload': False}, timeout=request_timeout)
            logger.info('catalog skills elapsed=%.3fs count=%d', time.monotonic()-phase, len(skills.get('data', [])))
            phase = time.monotonic();fast = self.fast_mode(request, cwd) if catalog['modelSource'] == 'codex' else {'allowed': False}
            logger.info('catalog fast-mode elapsed=%.3fs allowed=%s', time.monotonic()-phase, fast.get('allowed'))
            logger.info('catalog fetch complete elapsed=%.3fs', time.monotonic()-started)
            return {**catalog, 'kind': kind, 'skillEntries': skills.get('data', []), 'fastMode': fast}
        except Exception:
            logger.exception('catalog fetch failed elapsed=%.3fs', time.monotonic()-started)
            raise
        finally:
            process.stdin.close()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            reader.join(timeout=1)
            process.stdout.close()

    def read_models(self, request, cwd, provider=None):
        config = request('config/read', {'includeLayers': False, 'cwd': cwd}).get('config', {})
        profile = (config.get('profiles') or {}).get(config.get('profile')) or {}
        effective = {**config, **profile}
        provider = provider or effective.get('model_provider') or 'openai'
        definition = (effective.get('model_providers') or {}).get(provider) or {}
        base = definition.get('base_url') or (effective.get('openai_base_url') or os.environ.get('OPENAI_BASE_URL') if provider == 'openai' else None)
        custom = provider != 'openai' or base or any(definition.get(k) for k in
            ('env_key', 'experimental_bearer_token', 'http_headers', 'env_http_headers', 'auth', 'gateway_oauth'))
        auth = {} if custom else request('account/read', {'refreshToken': False}).get('account') or {}
        if custom or auth.get('type') == 'apiKey':
            result = {'models': [], 'modelSource': 'api', 'currentEffort': effective.get('model_reasoning_effort')}
            try:
                if any(definition.get(k) for k in ('http_headers', 'env_http_headers', 'auth', 'gateway_oauth', 'query_params', 'aws')):
                    raise ValueError('此 API 接入使用额外认证配置，请手动填写模型 ID')
                if not base and provider != 'openai':
                    raise ValueError('未找到当前 API 地址，请检查接入配置')
                key = (os.environ.get(definition['env_key'], '') if definition.get('env_key')
                       else definition.get('experimental_bearer_token') or '')
                if definition.get('env_key') and not key:
                    raise ValueError('未找到当前 API Key，请检查接入配置')
                if not key and (provider == 'openai' or definition.get('requires_openai_auth')):
                    try:
                        saved = json.loads((self.home/'auth.json').read_text(encoding='utf-8'))
                    except (OSError, ValueError):
                        saved = {}
                    key = saved.get('OPENAI_API_KEY') or os.environ.get('OPENAI_API_KEY', '')
                    if not key:
                        raise ValueError('未找到当前 API Key，请检查接入配置')
                base = base or 'https://api.openai.com/v1'
                endpoint = urlsplit(base)
                if (endpoint.scheme not in ('https', 'http') or not endpoint.hostname or
                        endpoint.username or endpoint.password or endpoint.query or endpoint.fragment):
                    raise ValueError('未找到当前 API 地址，请检查接入配置')
                ids = model_ids(base, key)
                result['models'] = [{'model': identifier, 'displayName': identifier} for identifier in ids]
            except ValueError as exc:
                result['modelError'] = str(exc)
            return result
        models, cursor = [], None
        for _ in range(10):
            response = request('model/list', {'cursor': cursor, 'limit': 100, 'includeHidden': False})
            models.extend(response.get('data', []))
            cursor = response.get('nextCursor')
            if not cursor:
                break
        return {'models': models, 'modelSource': 'codex', 'currentEffort': effective.get('model_reasoning_effort')}

    @staticmethod
    def fast_mode(request, cwd):
        # Return capability metadata only; credentials/config contents stay in the runtime.
        unavailable = {'allowed': False}
        try:
            config = request('config/read', {'includeLayers': False, 'cwd': cwd}).get('config', {})
            profile = (config.get('profiles') or {}).get(config.get('profile'), {})
            effective = {**config, **profile}
            provider = effective.get('model_provider') or 'openai'
            definition = (effective.get('model_providers') or {}).get(provider) or {}
            if provider != 'openai' or effective.get('openai_base_url') or any(definition.get(key) for key in
                    ('base_url', 'env_key', 'experimental_bearer_token', 'http_headers', 'env_http_headers', 'auth', 'gateway_oauth')):
                return unavailable
            auth = request('account/read', {'refreshToken': False})
            if (auth.get('account') or {}).get('type') != 'chatgpt' or auth.get('requiresOpenaiAuth') is not True:
                return unavailable
            requirements = request('configRequirements/read', {}).get('requirements') or {}
            features = {**(config.get('features') or {}), **(profile.get('features') or {})}
            allowed = features.get('fast_mode') is not False and (requirements.get('featureRequirements') or {}).get('fast_mode') is not False
            return {'allowed': allowed, 'defaultServiceTier': effective.get('service_tier')}
        except CatalogError:
            # Older/offline runtimes still expose their existing model and Skill list.
            return unavailable

    @staticmethod
    def fast_tier(model):
        return next((tier['id'] for tier in model.get('serviceTiers') or []
                     if isinstance(tier.get('id'), str) and (tier['id'] in ('fast', 'priority') or
                         str(tier.get('name', '')).strip().lower() in ('fast', 'priority'))), None)

    def _runtime_version(self):
        try:
            stat = self.executable.stat()
            return f'{stat.st_size}:{stat.st_mtime_ns}'
        except OSError:
            return ''

    @staticmethod
    def _public_skill(row):
        return SkillStore._public(row)

    def _skill_output(self, rows, *, total, offset, limit, refreshed_at, bucket_id,
                      errors=None, state='fresh', selected=None, background=False, refresh_error=None):
        value = {
            'skills': [self._public_skill(row) for row in rows],
            'selectedSkills': [self._public_skill(row) for row in selected or []],
            'kind': 'skills', 'errors': list(errors or []), 'total': total,
            'offset': offset, 'limit': limit,
            'cache': {'state': state, 'updatedAt': refreshed_at, 'bucketId': bucket_id, 'backgroundRefresh': background},
        }
        if refresh_error:
            value['refreshError'] = refresh_error
        return value

    def _legacy_output(self, rows, bucket, ids=None):
        identifiers=set(ids or [])
        selected=[row for row in rows if row.get('id') in identifiers or row.get('skill_id') in identifiers]
        return self._skill_output(rows, total=len(rows), offset=0, limit=SKILL_PAGE_LIMIT,
                                  refreshed_at=0, bucket_id=bucket, state='legacy', selected=selected)

    def _start_skill_refresh(self, cwd, provider, bucket):
        if not self.allow_background_refresh or bucket in self.skill_refreshing:
            return bucket in self.skill_refreshing
        self.skill_refreshing.add(bucket)
        def run():
            try:
                raw = self._fetch(cwd, provider, 'skills', 90)
                skills, errors = self._skill_rows(raw, cwd)
                try:
                    self.skill_store.write(cwd, skills, errors, self._runtime_version())
                except (OSError, sqlite3.DatabaseError):
                    logger.warning('Could not persist background Skill bucket', exc_info=True)
                self.kind_caches['skills'][bucket] = (time.monotonic(), {'skills': skills, 'errors': errors})
            except Exception as exc:
                logger.warning('background Skill refresh failed: %s', exc)
            finally:
                self.skill_refreshing.discard(bucket)
        threading.Thread(target=run, daemon=True).start()
        return True

    @staticmethod
    def _skill_rows(raw, cwd):
        skills, errors = [], []
        for entry in raw.get('skillEntries', []):
            errors.extend(entry.get('errors', []))
            for skill in entry.get('skills', []):
                if not skill.get('enabled', True) or not skill.get('path'):
                    continue
                path = Path(skill['path'])
                if not path.is_absolute(): path = Path(cwd)/path
                path = Path(os.path.abspath(path))
                skills.append({'id': hashlib.sha256(str(path).encode()).hexdigest(), 'name': skill['name'],
                               'description': skill.get('description', ''), 'path': str(path),
                               'scope': skill.get('scope'), 'displayName': (skill.get('interface') or {}).get('displayName') or skill['name']})
        return skills, errors

    def get_kind(self, kind, cwd, refresh=False, provider=None, query='', offset=0,
                 limit=SKILL_PAGE_LIMIT, ids=None):
        if kind not in self.kind_locks:
            raise ValueError('未知目录类型')
        cwd = str(Path(cwd).resolve())
        offset = max(0, int(offset))
        limit = min(SKILL_MAX_PAGE_LIMIT, max(1, int(limit)))
        if kind == 'models':
            key = (cwd, provider)
            with self.kind_locks[kind]:
                cached = self.kind_caches[kind].get(key)
                if cached and not refresh and time.monotonic() - cached[0] < 300:
                    return cached[1]
                raw = self._fetch(cwd, provider, kind)
                models = [{'id': m.get('model', m.get('id')), 'name': m.get('displayName', m.get('model')),
                           'description': m.get('description', ''), 'efforts': [e['reasoningEffort'] for e in m.get('supportedReasoningEfforts', [])],
                           'defaultEffort': m.get('defaultReasoningEffort'), 'isDefault': m.get('isDefault', False), 'fastTier': self.fast_tier(m),
                           'defaultServiceTier': m.get('defaultServiceTier')} for m in raw['models'] if not m.get('hidden')]
                value = {'models': models, 'kind': 'models', 'modelSource': raw.get('modelSource', 'codex'),
                         'fastMode': raw.get('fastMode', {'allowed': False}),
                         'modelError': raw.get('modelError'), 'currentModel': raw.get('currentModel'),
                         'currentEffort': raw.get('currentEffort')}
                self.kind_caches[kind][key] = (time.monotonic(), value)
                return value

        bucket = self.skill_store.bucket(cwd)
        with self.kind_locks['skills']:
            cached = self.kind_caches['skills'].get(bucket)
            if cached and not refresh and time.monotonic() - cached[0] < 300:
                rows, errors = cached[1]['skills'], cached[1]['errors']
                text = (query or '').strip().casefold()
                filtered = [row for row in rows if text in ' '.join(str(row.get(key) or '') for key in ('name','displayName','description')).casefold()]
                page = filtered[offset:offset+limit]
                selected = [row for row in rows if row.get('id') in set(ids or [])]
                return self._skill_output(page, total=len(filtered), offset=offset, limit=limit,
                                          refreshed_at=int(time.time()), bucket_id=bucket, errors=errors,
                                          selected=selected)

            persisted = self.skill_store.read(cwd, query, offset, limit, ids)
            legacy = self.skill_store.legacy() if persisted is None else []
            if legacy and not refresh:
                self._start_skill_refresh(cwd, provider, bucket)
                return self._legacy_output(legacy, bucket, ids)
            if persisted is not None and not refresh:
                fresh = time.time() - persisted['refreshed_at'] < 300
                state = 'fresh' if fresh else 'stale'
                background = False if fresh else self._start_skill_refresh(cwd, provider, bucket)
                return self._skill_output(persisted['rows'], total=persisted['total'], offset=offset,
                                          limit=limit, refreshed_at=persisted['refreshed_at'], bucket_id=bucket,
                                          errors=persisted['errors'], state=state, selected=persisted['selected'],
                                          background=background)

            try:
                raw = self._fetch(cwd, provider, 'skills', 90)
            except CatalogError as exc:
                if refresh and persisted is not None:
                    return self._skill_output(persisted['rows'], total=persisted['total'], offset=offset,
                                              limit=limit, refreshed_at=persisted['refreshed_at'], bucket_id=bucket,
                                              errors=persisted['errors'], state='stale', selected=persisted['selected'],
                                              refresh_error=str(exc))
                if legacy:
                    return self._legacy_output(legacy, bucket, ids)
                raise
            skills, errors = self._skill_rows(raw, cwd)
            try:
                self.skill_store.write(cwd, skills, errors, self._runtime_version())
            except (OSError, sqlite3.DatabaseError):
                logger.warning('Could not persist Skill bucket', exc_info=True)
            self.kind_caches['skills'][bucket] = (time.monotonic(), {'skills': skills, 'errors': errors})
            text = (query or '').strip().casefold()
            filtered = [row for row in skills if text in ' '.join(str(row.get(key) or '') for key in ('name','displayName','description')).casefold()]
            page = filtered[offset:offset+limit]
            selected = [row for row in skills if row.get('id') in set(ids or [])]
            return self._skill_output(page, total=len(filtered), offset=offset, limit=limit,
                                      refreshed_at=int(time.time()), bucket_id=bucket, errors=errors, selected=selected)

    def validate_skills(self, cwd, ids, refresh=False):
        cwd = str(Path(cwd).resolve())
        ids = list(dict.fromkeys(ids))[:8]
        if not refresh:
            try:
                return self.skill_store.validate(cwd, ids)
            except SkillPathChanged:
                raise
            except ValueError:
                return self.validate_skills(cwd, ids, refresh=True)
        raw = self._fetch(cwd, None, 'skills', 90)
        skills, errors = self._skill_rows(raw, cwd)
        self.skill_store.write(cwd, skills, errors, self._runtime_version())
        self.kind_caches['skills'][self.skill_store.bucket(cwd)] = (time.monotonic(), {'skills': skills, 'errors': errors})
        return self.skill_store.validate(cwd, ids)

    def get(self, cwd, refresh=False, provider=None):
        cwd = str(Path(cwd).resolve())
        with self.lock:
            key = (cwd, provider)
            cached = self.cache.get(key)
            if cached and not refresh and time.monotonic() - cached[0] < 300:
                return cached[1]
            raw = self._fetch(cwd, provider)
            models = [{'id': m.get('model', m.get('id')), 'name': m.get('displayName', m.get('model')),
                       'description': m.get('description', ''), 'efforts': [e['reasoningEffort'] for e in m.get('supportedReasoningEfforts', [])],
                       'defaultEffort': m.get('defaultReasoningEffort'), 'isDefault': m.get('isDefault', False), 'fastTier': self.fast_tier(m),
                       'defaultServiceTier': m.get('defaultServiceTier')} for m in raw['models'] if not m.get('hidden')]
            skills, errors = [], []
            for entry in raw['skillEntries']:
                errors.extend(entry.get('errors', []))
                for skill in entry.get('skills', []):
                    if not skill.get('enabled', True) or not skill.get('path'):
                        continue
                    path = skill['path']
                    skills.append({'id': hashlib.sha256(path.encode()).hexdigest(), 'name': skill['name'],
                                   'description': skill.get('description', ''), 'path': path,
                                   'scope': skill.get('scope'), 'displayName': (skill.get('interface') or {}).get('displayName') or skill['name']})
            value = {'models': models, 'skills': skills, 'errors': errors, 'fastMode': raw.get('fastMode', {'allowed': False}),
                     'modelSource': raw.get('modelSource', 'codex'), 'modelError': raw.get('modelError'),
                     'currentModel': raw.get('currentModel'), 'currentEffort': raw.get('currentEffort')}
            self.cache[key] = (time.monotonic(), value)
            return value
