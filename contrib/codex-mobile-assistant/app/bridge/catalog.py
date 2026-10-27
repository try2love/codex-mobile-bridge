"""Read model/skill metadata using the desktop's bundled runtime.

This short-lived helper only allows initialize, model/list and skills/list.
All thread mutations and all turns remain on the existing desktop IPC owner.
"""
import hashlib
import json
import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path


class CatalogError(RuntimeError):
    pass


class Catalog:
    METHODS = {"initialize", "model/list", "skills/list"}

    def __init__(self, codex_home, executable=None):
        self.home = Path(codex_home)
        self.executable = Path(executable) if executable else self.find_runtime()
        self.cache = {}
        self.lock = threading.Lock()

    @staticmethod
    def find_runtime():
        if os.name == 'nt':
            local = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local')))
            candidates = sorted((local / 'OpenAI/Codex/bin').glob('*/codex.exe'),
                                key=lambda path: path.stat().st_mtime, reverse=True)
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

    def _fetch(self, cwd):
        if not self.executable:
            raise CatalogError("找不到桌面 App 的 Codex 运行时")
        env = dict(os.environ)
        env['CODEX_HOME'] = str(self.home)
        process = subprocess.Popen([str(self.executable), 'app-server', '--listen', 'stdio://'], cwd=cwd, env=env,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   text=True, encoding='utf-8',
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
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
        def request(method, params):
            nonlocal counter
            if method not in self.METHODS:
                raise ValueError("目录接口只允许读取模型和 Skill")
            counter += 1
            process.stdin.write(json.dumps({'id': counter, 'method': method, 'params': params}) + '\n')
            process.stdin.flush()
            deadline = time.monotonic() + 25
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
        try:
            request('initialize', {'clientInfo': {'name': 'codex_mobile_catalog', 'title': 'Mobile catalog', 'version': '0.2'},
                                   'capabilities': {'experimentalApi': True}})
            process.stdin.write('{"method":"initialized"}\n')
            process.stdin.flush()
            models, cursor = [], None
            for _ in range(10):
                response = request('model/list', {'cursor': cursor, 'limit': 100, 'includeHidden': False})
                models.extend(response.get('data', []))
                cursor = response.get('nextCursor')
                if not cursor:
                    break
            skills = request('skills/list', {'cwds': [cwd], 'forceReload': True})
            return {'models': models, 'skillEntries': skills.get('data', [])}
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

    def get(self, cwd, refresh=False):
        cwd = str(Path(cwd).resolve())
        with self.lock:
            cached = self.cache.get(cwd)
            if cached and not refresh and time.monotonic() - cached[0] < 300:
                return cached[1]
            raw = self._fetch(cwd)
            models = [{'id': m.get('model', m.get('id')), 'name': m.get('displayName', m.get('model')),
                       'description': m.get('description', ''), 'efforts': [e['reasoningEffort'] for e in m.get('supportedReasoningEfforts', [])],
                       'defaultEffort': m.get('defaultReasoningEffort')} for m in raw['models'] if not m.get('hidden')]
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
            value = {'models': models, 'skills': skills, 'errors': errors}
            self.cache[cwd] = (time.monotonic(), value)
            return value
