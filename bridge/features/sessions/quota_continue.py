"""Opt-in continuation after a verified, personal Codex quota recovery."""
import hashlib
import json
import math
import re
import time
import uuid

from bridge.features.notifications.channels import read_json, write_json
from bridge.features.sessions.goal import goal_identity, normalize_goal
from bridge.features.sessions.model import ordered_turns, pending_requests, items_array


class QuotaDeferred(ValueError):
    """The native write has NOT started; retrying the preflight is safe."""
    def __init__(self, message, *, cancel=False):
        super().__init__(message)
        self.cancel = cancel


def quota_error(value):
    encoded = json.dumps(value, ensure_ascii=False).lower().replace('’', "'")
    compact = re.sub(r'[^a-z]', '', encoded)
    return (any(code in compact for code in ('usagelimitexceeded', 'usagelimitreached', 'insufficientquota'))
            or 'usage limit' in encoded and any(word in encoded for word in ('hit', 'reached', 'exceeded'))
            or any(word in encoded for word in ('额度已用尽', '使用额度已耗尽', '已达到使用限额')))


CONTINUE_TEXT = '额度已恢复。请从中断处继续完成之前的任务，先检查已有进度，避免重复已完成的操作。'


def candidate(session, *, state=None, owner=None):
    """Only authoritative, idle Desktop state can authorize a continuation."""
    state = (session.state if state is None else state) or {}
    owner = session.owner if owner is None else owner
    if (not session.connected or session.runtime != 'desktop' or session.origin != 'desktop'
            or not owner or state.get('modelProvider') != 'openai'
            or state.get('threadRuntimeStatus', {}).get('type') not in ('idle', 'systemError')
            or pending_requests(state)):
        return None
    turns = ordered_turns(state)
    if not turns or any(t.get('status') == 'inProgress' for t in turns):
        return None
    turn = turns[-1]
    # A manual interruption wins even if a lagging goal still says usageLimited.
    if turn.get('status') == 'interrupted':
        return None
    goal = normalize_goal(state.get('threadGoal'))
    if goal:
        if goal['status'] != 'usageLimited':
            return None
    else:
        errors = [turn.get('error')] + [item for item in items_array(turn.get('items', []))
                                       if item.get('type') == 'error']
        if turn.get('status') != 'failed' or turn.get('willRetry') or not any(quota_error(e) for e in errors if e):
            return None
    if not turn.get('turnId'):
        return None
    return {'owner': owner, 'cwd': state.get('cwd'), 'turnId': turn['turnId'],
            'goal': {**goal_identity(goal), 'status': goal['status']} if goal else None}


def quota_windows(value):
    """Conservative: every reported window must be known and usable."""
    if (value.get('error') or not value.get('visible') or not value.get('accountKey')
            or not value.get('updatedAt') or not 0 <= time.time() - value['updatedAt'] <= 60):
        return None
    windows = {}
    buckets = value.get('limits', [])
    # Code review and model-specific buckets must not block the general Codex bucket.
    codex = [b for b in buckets if b.get('id') == 'codex']
    for bucket in codex or buckets:
        for window in bucket.get('windows', []):
            remaining = window.get('remainingPercent')
            if (type(remaining) not in (int, float) or not math.isfinite(remaining)
                    or not 0 <= remaining <= 100):
                return None
            windows[bucket['id'] + ':' + window['kind']] = remaining
    return windows or None


def scope_stamp(manager):
    # Token refreshes do not change ownership. accountKey is verified separately.
    stamp = manager.info.source_stamp()
    return (stamp[0], *stamp[2:])


class QuotaContinuation:
    def __init__(self, monitor):
        self.monitor = monitor
        self.path = monitor.manager.root / 'quota-continuations.json'
        records = read_json(self.path, {})
        self.records = records if isinstance(records, dict) else {}
        self.stops_path = monitor.manager.root / 'quota-manual-stops.json'
        stops = read_json(self.stops_path, {})
        self.manual_stops = stops if isinstance(stops, dict) else {}
        self.exclusions_path = monitor.manager.root / 'quota-continuation-exclusions.json'
        exclusions = read_json(self.exclusions_path, [])
        self.excluded = set(exclusions) if isinstance(exclusions, list) else set()
        self.next_scan = self.next_discovery = 0
        self.eligible = set()
        self.generation = 0
        self.error = ''
        self.last_checked = 0
        self.monitored = self.connected = 0
        self.incompatible = 0

    def is_manually_stopped(self, thread_id, turn_id):
        return self.manual_stops.get(thread_id, {}).get('turnId') == turn_id

    def configure(self, value):
        if getattr(self.monitor.manager.bridge, 'host', 'local') != 'local':
            raise ValueError('请在默认网关主页设置额度自动继续')
        if set(value) == {'enabled'} and type(value['enabled']) is bool:
            self.monitor.configure({'autoContinue': value['enabled']})
        elif (set(value) == {'threadId', 'excluded'} and type(value['excluded']) is bool
              and isinstance(value['threadId'], str)):
            uuid.UUID(value['threadId'])
            with self.monitor.manager.lock:
                updated = set(self.excluded)
                if value['excluded']:
                    updated.add(value['threadId'])
                else:
                    updated.discard(value['threadId'])
                write_json(self.exclusions_path, sorted(updated))
                self.excluded = updated
                self.generation += 1
                self.next_scan = 0
        else:
            raise ValueError('额度自动继续设置无效')
        return self.public()

    def note_manual_stop(self, thread_id, turn_id):
        if not turn_id:
            return
        with self.monitor.manager.lock:
            self.manual_stops[thread_id] = {'turnId': turn_id, 'at': time.time()}
            write_json(self.stops_path, self.manual_stops)
            for record in self.records.values():
                if record.get('threadId') == thread_id and record.get('state') == 'waiting':
                    record.update(state='cancelled', reason='manual_stop')
            self.save()

    def public(self):
        with self.monitor.manager.lock:
            rows = list(self.records.values())
            pending = sum(r.get('state') == 'waiting' and r.get('threadId') not in self.excluded for r in rows)
            unknown = sum(r.get('state') in ('dispatching', 'unknown') for r in rows)
            resumed = sum(r.get('state') == 'accepted' for r in rows)
            tasks = {r['threadId']: {'threadId': r['threadId'], 'title': r.get('title') or r['threadId'],
                      'state': r['state'], 'excluded': r['threadId'] in self.excluded}
                     for r in rows if r.get('state') in ('waiting', 'dispatching', 'unknown')}
            for thread_id in self.excluded:
                tasks.setdefault(thread_id, {'threadId': thread_id, 'title': thread_id,
                                            'state': 'excluded', 'excluded': True})
        enabled = self.monitor.preferences.get('autoContinue', False)
        error = self.error or ('有聊天的原生同步格式无法识别，无法检查该聊天的额度中断，请更新 Bridge。' if self.incompatible else '')
        state = ('disabled' if not enabled else 'waitingGateway' if getattr(self.monitor, 'worker', None) is None
                 else 'error' if error else 'unknown' if unknown else 'waitingQuota' if pending
                 else 'waitingConnection' if self.monitored and not self.connected else 'monitoring')
        return {'enabled': enabled, 'state': state, 'pending': pending, 'unknown': unknown,
                'resumed': resumed, 'monitored': self.monitored, 'connected': self.connected,
                'checkedAt': self.last_checked, 'incompatible': self.incompatible, 'error': error,
                'tasks': list(tasks.values())}

    def scan(self):
        manager = self.monitor.manager
        bridge = manager.bridge
        if (bridge.host != 'local' or not self.monitor.preferences.get('autoContinue')
                or self.monitor.closed.is_set() or not bridge.observation_enabled()):
            return
        if time.monotonic() < self.next_scan:
            return
        self.next_scan = time.monotonic() + 60
        self.last_checked = time.time()
        self.error = ''
        if time.monotonic() >= self.next_discovery:
            offset, eligible = 0, set()
            while True:
                if self.monitor.closed.is_set() or not self.monitor.preferences.get('autoContinue'):
                    return
                rows = bridge.list(limit=500, offset=offset, source='desktop', local_only=True)
                bridge.activity([row['id'] for row in rows])
                eligible.update(row['id'] for row in rows)
                if len(rows) < 500:
                    break
                offset += len(rows)
            self.eligible = eligible
            self.next_discovery = time.monotonic() + 240
        self.scan_bridge(bridge, manager, self.eligible)

    def scan_bridge(self, bridge, manager, eligible=None):
        with bridge.lock:
            sessions = list(bridge.live.values())
        self.monitored = len(eligible) if eligible is not None else len(sessions)
        self.connected = sum(bool(s.connected) for s in sessions)
        self.incompatible = sum(bool(getattr(s, 'desktop_stream_error', None)) for s in sessions)
        stamp = scope_stamp(manager)
        generation = self.generation
        candidates = []
        for session in sessions:
            with session.condition:
                expected = candidate(session) if eligible is None or session.id in eligible else None
                if expected and self.is_manually_stopped(session.id, expected['turnId']):
                    expected = None
                connected = session.connected
            key = hashlib.sha256(json.dumps([bridge.host, session.id, expected, stamp], sort_keys=True).encode()).hexdigest()
            with manager.lock:
                if generation != self.generation or not self.monitor.preferences.get('autoContinue'):
                    return
                # A confirmed manual change invalidates old waiting intents; a disconnect does not.
                for old_key, record in self.records.items():
                    if record.get('threadId') == session.id and record.get('state') == 'waiting' and connected and old_key != key:
                        record.update(state='cancelled', reason='task_changed')
                if expected:
                    changed_scope = any(r.get('threadId') == session.id and r.get('expected') == expected
                                        and r.get('scope') not in (None, list(stamp)) for r in self.records.values())
                    record = self.records.setdefault(key, {'version': 2, 'threadId': session.id, 'expected': expected,
                        'title': str(session.state.get('title') or session.id)[:200],
                        'scope': list(stamp), 'submissionId': str(uuid.uuid4()),
                        'state': 'cancelled' if changed_scope else 'waiting', 'at': time.time(), 'accountKey': None})
                    if record.get('state') == 'waiting' and session.id not in self.excluded:
                        candidates.append((session, expected, key))
                self.save()
        if not candidates:
            return
        # Register quota failures before querying. The native error is depletion evidence;
        # polling the exact zero-percent moment is neither necessary nor reliable.
        try:
            value = bridge.account.read(refresh=True)
        except Exception:
            self.error = '个人额度查询暂不可用，任务已登记，稍后自动重试。'
            return
        windows = quota_windows(value)
        if windows is None:
            self.error = '未取得可核验的个人额度，任务已登记，稍后自动重试。'
            return
        if scope_stamp(manager) != stamp:
            return
        for session, expected, key in candidates:
            with manager.lock:
                if (self.monitor.closed.is_set() or not self.monitor.preferences.get('autoContinue')
                        or self.generation != generation):
                    return
                record = self.records[key]
                legacy_key = hashlib.sha256(json.dumps([bridge.host, session.id, expected, value['accountKey'], stamp],
                                                     sort_keys=True).encode()).hexdigest()
                if self.records.get(legacy_key, {}).get('attempted'):
                    record.update(state='unknown', reason='legacy_attempt')
                    self.save()
                    continue
                if record.get('accountKey') not in (None, value['accountKey']):
                    record.update(state='cancelled', reason='account_changed')
                    self.save()
                    continue
                record['accountKey'] = value['accountKey']
                self.save()
                if any(v <= 0 for v in windows.values()):
                    continue
            def before_write():
                with manager.lock:
                    if (generation != self.generation or not self.monitor.preferences.get('autoContinue')
                            or self.monitor.closed.is_set() or self.records.get(key, {}).get('state') != 'waiting'
                            or session.id in self.excluded
                            or self.is_manually_stopped(session.id, expected['turnId'])):
                        raise QuotaDeferred('自动继续已关闭或等待记录已变化', cancel=True)
                    record.update(state='dispatching', attempted=True, updatedAt=time.time())
                    self.save()  # A crash after this point must never replay the write.
            try:
                result = bridge.continue_after_quota(session.id, expected, record['submissionId'], stamp,
                    value['accountKey'], generation, before_write=before_write)
                activation = (result or {}).get('activation') or {}
                uncertain = (not isinstance(result, dict) or result.get('status') not in ('accepted', 'active', 'queued')
                             or activation.get('status') == 'unknown')
                with manager.lock:
                    record.update(state='unknown' if uncertain else 'accepted', updatedAt=time.time())
                    self.save()
            except Exception as exc:
                with manager.lock:
                    if record.get('state') == 'dispatching':
                        record.update(state='unknown', reason='delivery_unknown')
                        self.error = '自动继续结果待确认，请查看原聊天；不会重复发送。'
                    elif isinstance(exc, QuotaDeferred) and exc.cancel:
                        record.update(state='cancelled', reason='task_changed')
                    else:
                        record.update(state='waiting', reason='preflight_unavailable')
                        self.error = '发送前核验暂未通过，尚未发送，将自动重试。'
                    self.save()

    def save(self):
        write_json(self.path, self.records)
