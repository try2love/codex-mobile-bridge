"""Shared, explicit CLI handoff. Never discover or start an execution owner."""
import json
import hashlib
import threading
import uuid
from pathlib import Path



class ReleasedSessions:
    def __init__(self, directory, save):
        self.save = save
        self.path = Path(directory) / 'released-cli-sessions.json'
        self.lock = threading.RLock()
        self.ids = set(json.loads(self.path.read_text()) if self.path.exists() else [])

    def contains(self, identifier):
        with self.lock:
            return identifier in self.ids

    def set(self, identifier, released):
        with self.lock:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if released:
                self.ids.add(identifier)
            else:
                self.ids.discard(identifier)
            self.save(self.path, sorted(self.ids))


class CliConnections:
    def __init__(self, directory, sources, save):
        self.save = save
        self.sources = sources
        self.path = Path(directory) / 'cli-release-operations.json'
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.callbacks = {}
        self.jobs = json.loads(self.path.read_text()) if self.path.exists() else {}
        for job in self.jobs.values():
            for row in job['results']:
                if row['status'] in ('waiting', 'releasing'):
                    row.update(status='unknown', error='网关已重启，请核对原会话；不会自动重放释放操作')

    def _connections(self):
        rows = []
        for source in self.sources():
            rows.extend(source.cli_connections())
        return rows

    def view(self):
        rows = self._connections()
        scopes = {}
        for row in rows:
            if row.get('scopeKey'):
                scope = scopes.setdefault(row['scopeKey'], {name: row[name] for name in (
                    'scopeKey', 'scopeLabel', 'sharedHome')})
                scope.setdefault('connections', []).append(row['key'])
        with self.lock:
            return {'connections': [{k: v for k, v in row.items() if not k.startswith('_')} for row in rows],
                    'scopes': list(scopes.values()),
                    'operations': json.loads(json.dumps(list(self.jobs.values())))}

    def _scope_preview(self, body):
        scope = body.get('scopeKey')
        if not isinstance(scope, str) or not scope:
            raise ValueError('请选择当前主机与配置的 CLI 范围')
        all_selected = body.get('all') is True
        fields = {'mode', 'scopeKey', 'all' if all_selected else 'connections'}
        if set(body) != fields:
            raise ValueError('CLI 范围预览无效')
        available = {row['key']: row for row in self._connections()}
        selected = (sorted(key for key, row in available.items() if row.get('scopeKey') == scope)
                    if all_selected else body.get('connections'))
        if (not isinstance(selected, list) or not 1 <= len(selected) <= 256
                or any(not isinstance(key, str) for key in selected) or len(set(selected)) != len(selected)):
            raise ValueError('请选择当前范围中的 CLI 连接')
        selected = sorted(selected)
        with self.lock:
            for key in selected:
                if key not in available:
                    row = self._force_available(key, self.callbacks)
                    if row:
                        available[key] = row
        if any(key not in available or available[key].get('scopeKey') != scope
               or '_scope_preview' not in available[key] for key in selected):
            raise ValueError('连接已变化或不属于当前范围，请重新选择')
        row = available[selected[0]]
        preview = row['_scope_preview']()
        if not preview.get('generation') or preview.get('scopeKey') != scope:
            return {**preview, 'canRelease': False, 'canForce': False}, available
        backend = preview['generation']
        generation = hashlib.sha256(json.dumps([scope, selected, backend]).encode()).hexdigest()
        return {**preview, 'connections': selected, 'generation': generation, 'scopeGeneration': backend}, available

    def _submit_scope(self, body):
        mode = body['mode']
        if mode == 'scopePreview':
            return self._scope_preview(body)[0]
        if mode == 'scopeCancel':
            if set(body) != {'mode', 'id'} or not isinstance(body['id'], str):
                raise ValueError('CLI 范围取消请求无效')
            with self.lock:
                job = self.jobs.get(body['id'])
                if not job or job['mode'] != 'scopeWait' or not any(row['status'] in ('waiting', 'releasing') for row in job['results']):
                    raise ValueError('此等待已开始退出或结束，不能取消')
                callback = self.callbacks[job['connections'][0]]['_scope_cancel']
            result = callback(job['id'])
            with self.lock:
                for row in job['results']:
                    row.update(result)
                self.save(self.path, self.jobs)
                return json.loads(json.dumps(job))
        if (set(body) != {'mode', 'id', 'scopeKey', 'connections', 'generation'}
                or mode not in ('scopeWait', 'scopeForce') or not isinstance(body['generation'], str)):
            raise ValueError('请预览并确认 CLI 释放范围')
        uuid.UUID(body['id'])
        with self.lock:
            old = self.jobs.get(body['id'])
            if old:
                if any(old.get(name) != value for name, value in body.items()):
                    raise ValueError('释放请求 ID 已用于其他操作')
                return json.loads(json.dumps(old))
        preview, available = self._scope_preview({name: body[name] for name in ('scopeKey', 'connections')}
                                               | {'mode': 'scopePreview'})
        if preview.get('generation') != body['generation'] or not preview.get('canForce' if mode == 'scopeForce' else 'canRelease'):
            raise ValueError('CLI 范围、进程或配置已变化，请重新预览')
        with self.lock:
            old = self.jobs.get(body['id'])
            if old:
                if any(old.get(name) != value for name, value in body.items()):
                    raise ValueError('释放请求 ID 已用于其他操作')
                return json.loads(json.dumps(old))
            for job in self.jobs.values():
                if job.get('scopeKey') != body['scopeKey'] or not any(
                        row['status'] in ('waiting', 'releasing', 'unknown') and not row.get('supersededBy')
                        for row in job['results']):
                    continue
                if mode != 'scopeForce' or job['mode'] == 'scopeForce' and any(
                        row['status'] in ('waiting', 'releasing') for row in job['results']):
                    raise ValueError('此范围已有释放请求，请先核对或明确强制释放')
            job = {**body, 'scopeGeneration': preview['scopeGeneration'], 'impact': preview['impact'],
                   'results': [{name: value for name, value in available[key].items() if not name.startswith('_')}
                               | {'status': 'waiting'} for key in body['connections']]}
            self.jobs[job['id']] = job
            self.callbacks.update({key: available[key] for key in body['connections']})
            self.save(self.path, self.jobs)
            threading.Thread(target=self._run_scope, args=(job, available), daemon=True, name='cli-release').start()
            return json.loads(json.dumps(job))

    def _run_scope(self, job, available):
        callback = available[sorted(job['connections'])[0]]
        while not self.closed.is_set():
            with self.lock:
                if not any(row['status'] == 'waiting' for row in job['results']):
                    return
                newer_force = job['mode'] != 'scopeForce' and any(
                    other.get('scopeKey') == job['scopeKey'] and other['mode'] in ('scopeForce', 'force') and any(
                        row['status'] in ('waiting', 'releasing') for row in other['results']) for other in self.jobs.values())
                if not newer_force:
                    for row in job['results']:
                        row['status'] = 'releasing'
                    self.save(self.path, self.jobs)
            if newer_force:
                self.closed.wait(5)
                continue
            try:
                call = callback['_scope_force' if job['mode'] == 'scopeForce' else '_scope_release']
                result = call(job['id'], job['scopeGeneration'])
            except Exception as error:
                result = getattr(error, 'release_result', {'status': 'unknown', 'error': str(error)})
            with self.lock:
                for row in job['results']:
                    if row['status'] == 'releasing':
                        row.update(result)
                if result.get('forceDispatched'):
                    for old in self.jobs.values():
                        if old is job or old.get('scopeKey') != job['scopeKey']:
                            continue
                        for prior in old['results']:
                            if prior['status'] in ('waiting', 'unknown'):
                                prior['supersededBy'] = job['id']
                                if prior['status'] == 'waiting':
                                    prior.update(status='superseded', notice='已改为明确强制释放此范围')
                self.save(self.path, self.jobs)
            if result.get('status') != 'waiting':
                return
            self.closed.wait(5)

    def _force_available(self, key, available):
        row = available.get(key)
        if row and '_force' in row:
            return row
        for job in reversed(list(self.jobs.values())):
            saved = next((item for item in job['results'] if item['key'] == key
                          and item['status'] in ('waiting', 'unknown', 'detached')), None)
            if saved:
                for source in self.sources():
                    callbacks = getattr(source, 'cli_force_callbacks', lambda _row: {})(saved)
                    if callbacks:
                        return {**saved, **callbacks}
        return None

    def submit(self, body):
        if isinstance(body, dict) and body.get('mode') in ('scopePreview', 'scopeWait', 'scopeForce', 'scopeCancel'):
            return self._submit_scope(body)
        if isinstance(body, dict) and body.get('mode') == 'forcePreview':
            keys = body.get('connections')
            if set(body) != {'connections', 'mode'} or not isinstance(keys, list) or len(keys) != 1 or not isinstance(keys[0], str):
                raise ValueError('请选择一个需要强制释放的 CLI 连接')
            with self.lock:
                row = self._force_available(keys[0], {**self.callbacks, **{item['key']: item for item in self._connections()}})
            if not row:
                return {'canForce': False, 'message': '当前无法核验此 CLI 连接及原生进程。'}
            return row['_force_preview']()
        force = isinstance(body, dict) and body.get('mode') == 'force'
        fields = {'id', 'connections', 'mode', 'generation'} if force else {'id', 'connections', 'mode'}
        if (not isinstance(body, dict) or set(body) != fields
                or body['mode'] not in ('wait', 'stop', 'check', 'unload', 'force')
                or force and not isinstance(body['generation'], str)):
            raise ValueError('CLI 释放请求无效')
        uuid.UUID(body['id'])
        selected = body['connections']
        if (not isinstance(selected, list) or not 1 <= len(selected) <= 256
                or any(not isinstance(key, str) for key in selected) or len(set(selected)) != len(selected)):
            raise ValueError('请选择需要释放的 CLI 连接')
        if force and len(selected) != 1:
            raise ValueError('请选择一个需要强制释放的 CLI 连接')
        with self.lock:
            previous = self.jobs.get(body['id'])
            if previous:
                if (previous['connections'] != selected or previous['mode'] != body['mode']
                        or force and previous.get('generation') != body['generation']):
                    raise ValueError('释放请求 ID 已用于其他操作')
                return json.loads(json.dumps(previous))
            busy = [row for job in self.jobs.values() for row in job['results']
                    if row['key'] in selected and row['status'] in ('waiting', 'releasing', 'unknown')
                    and not row.get('supersededBy')]
            if body['mode'] == 'check' and any(row['status'] != 'unknown' for row in busy):
                raise ValueError('释放操作仍在进行，请等待此次检查完成')
            if (force and any(row['status'] == 'releasing' for row in busy) or not force and body['mode'] != 'check'
                    and any(body['mode'] != 'stop' or row['status'] != 'waiting' for row in busy)):
                raise ValueError('所选连接已有释放请求，请先核对该请求的结果')
            if force and any(job['mode'] == 'force' and any(row['key'] == selected[0]
                    and row['status'] in ('waiting', 'releasing') for row in job['results']) for job in self.jobs.values()):
                raise ValueError('此连接已有强制释放请求')
            available = {row['key']: row for row in self._connections()}
            if body['mode'] in ('check', 'unload'):
                available = {**self.callbacks, **available}
            if body['mode'] == 'check':
                for key in selected:
                    if key not in available:
                        row = self._force_available(key, available)
                        if row and '_check' in row:
                            available[key] = row
            if force:
                row = self._force_available(selected[0], {**self.callbacks, **available})
                if not row:
                    raise ValueError('当前无法核验此 CLI 连接及原生进程')
                available = {selected[0]: row}
            if any(key not in available for key in selected):
                raise ValueError('连接已变化，请刷新后重新选择')
            if body['mode'] == 'check' and any('_check' not in available[key] for key in selected):
                raise ValueError('此连接无法自动核验，请在原客户端核对；未重放操作')
            if body['mode'] == 'unload' and any('_unload' not in available[key] or not any(
                    row['key'] == key and row['status'] == 'detached' for old in self.jobs.values() for row in old['results'])
                    for key in selected):
                raise ValueError('请先核对已退订的原连接，再明确释放原生占用')
            for row in busy:
                if body['mode'] == 'stop':
                    row.update(status='superseded', notice='已改为停止任务并释放；请查看新的释放请求')
            job = {**body, 'results': [{k: v for k, v in available[key].items() if not k.startswith('_')}
                                     | {'status': 'waiting'} for key in selected]}
            if force and available[selected[0]].get('scopeKey'):
                job['scopeKey'] = available[selected[0]]['scopeKey']
            self.callbacks.update({key: available[key] for key in selected})
            self.jobs[body['id']] = job
            # Bound completed receipts; pending/unknown operations remain attributable.
            if len(self.jobs) > 128:
                for key, old in list(self.jobs.items()):
                    if len(self.jobs) <= 128:
                        break
                    if key != body['id'] and all(r['status'] not in ('waiting', 'releasing', 'unknown') for r in old['results']):
                        del self.jobs[key]
            self.save(self.path, self.jobs)
            threading.Thread(target=self._run, args=(job, available), daemon=True,
                             name='cli-release').start()
            return json.loads(json.dumps(job))

    def _run(self, job, available):
        pending = list(job['results'])
        while pending and not self.closed.is_set():
            for row in list(pending):
                with self.lock:
                    if row['status'] != 'waiting':
                        pending.remove(row)
                        continue
                    if job['mode'] != 'force' and any(other['mode'] == 'force' and any(
                            target['key'] == row['key'] and target['status'] in ('waiting', 'releasing')
                            for target in other['results']) for other in self.jobs.values()):
                        continue
                    row['status'] = 'releasing'
                    self.save(self.path, self.jobs)
                try:
                    # Adapter rechecks the SAME ownership generation before any mutation.
                    callback = available[row['key']]
                    result = (callback['_check']() if job['mode'] == 'check' else callback['_unload'](job['id'])
                              if job['mode'] == 'unload' else callback['_force'](job['id'], job['generation'])
                              if job['mode'] == 'force' else callback['_release'](job['mode'] == 'stop'))
                except Exception as error:
                    result = getattr(error, 'release_result', {'status': 'unknown', 'error': str(error)})
                with self.lock:
                    if result.get('status') in ('released', 'detached'):
                        row.pop('error', None)
                    row.update(result)
                    if job['mode'] == 'force' and result.get('forceDispatched'):
                        for old in self.jobs.values():
                            if old is job:
                                continue
                            for prior in old['results']:
                                if (prior['key'] == row['key'] or job.get('scopeKey')
                                        and prior.get('scopeKey') == job['scopeKey']) and prior['status'] in ('waiting', 'unknown'):
                                    prior['supersededBy'] = job['id']
                                    if prior['status'] == 'waiting':
                                        prior.update(status='superseded', notice='已改为明确强制释放；请查看新的释放请求')
                    if result.get('status') in ('released', 'detached'):
                        for old in self.jobs.values():
                            for prior in old['results']:
                                if (prior['key'] == row['key'] and prior['status'] in ('unknown', 'detached')
                                        and not prior.get('supersededBy') and job['mode'] != 'force'):
                                    prior.pop('error', None)
                                    prior.update(result)
                    self.save(self.path, self.jobs)
                if row['status'] != 'waiting':
                    pending.remove(row)
            # Only user-requested completion handoffs poll; ordinary listings never query native runtimes.
            if pending:
                self.closed.wait(5)

    def close(self):
        self.closed.set()
        with self.lock:
            jobs = [job for job in self.jobs.values() if job['mode'] == 'scopeWait'
                    and any(row['status'] in ('waiting', 'releasing') for row in job['results'])]
        for job in jobs:
            callback = self.callbacks.get(job['connections'][0], {}).get('_scope_cancel')
            if callback is None:
                continue
            try:
                result = callback(job['id'])
            except ValueError:
                continue
            with self.lock:
                for row in job['results']:
                    if row['status'] in ('waiting', 'releasing'):
                        row.update(result)
                self.save(self.path, self.jobs)
