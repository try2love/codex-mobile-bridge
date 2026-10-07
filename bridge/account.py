"""Account-only RPCs using the desktop runtime and its configured credential store.

Only sanitized account metadata leaves the gateway; no thread or login RPC is permitted.
The desktop's reset permission is enforced here as well as in its own UI.
"""
import copy
import base64
from datetime import datetime
import hashlib
import json
import math
import os
import queue
import subprocess
import threading
import time
import uuid
from pathlib import Path

from .catalog import Catalog
from .notifications import read_json, write_json


class AccountError(RuntimeError):
    pass


class AccountRPC:
    METHODS = {'initialize', 'config/read', 'account/read', 'account/rateLimits/read',
               'account/rateLimitResetCredit/consume'}

    def __init__(self, home, executable):
        self.home, self.executable = Path(home), executable
        self.process = None
        self.counter = 0
        self.messages = queue.Queue()

    def __enter__(self):
        if not self.executable:
            raise AccountError('找不到桌面 App 的 Codex 运行时')
        options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        try:
            # Use the account's home, not a chat's project/configuration layer.
            self.process = subprocess.Popen(
                [str(self.executable), 'app-server', '--listen', 'stdio://'], cwd=self.home,
                env={**os.environ, 'CODEX_HOME': str(self.home)}, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding='utf-8', **options)
            def read():
                try:
                    for line in self.process.stdout:
                        try:
                            self.messages.put(json.loads(line))
                        except ValueError:
                            continue
                finally:
                    self.messages.put(None)
            self.reader = threading.Thread(target=read, daemon=True)
            self.reader.start()
            self.request('initialize', {'clientInfo': {'name': 'codex_mobile_account', 'version': '0.2'},
                                        'capabilities': {'experimentalApi': True}})
            self.process.stdin.write('{"method":"initialized"}\n')
            self.process.stdin.flush()
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise AccountError('无法连接 Codex 账号服务，请检查电脑端登录状态') from None

    def request(self, method, params=None):
        if method not in self.METHODS:
            raise ValueError('不支持的账号操作')
        self.counter += 1
        try:
            self.process.stdin.write(json.dumps({'id': self.counter, 'method': method, 'params': params}) + '\n')
            self.process.stdin.flush()
            deadline = time.monotonic() + 15
            while True:
                result = self.messages.get(timeout=max(.01, deadline - time.monotonic()))
                if result is None:
                    raise AccountError('Codex 账号服务已断开，请刷新后重试')
                if result.get('id') == self.counter:
                    if 'error' in result:
                        # RPC errors may include upstream responses; never forward them to the phone.
                        raise AccountError('Codex 账号操作未完成，请检查电脑端登录状态或更新桌面 App')
                    return result['result']
                if time.monotonic() >= deadline:
                    raise queue.Empty()
        except (queue.Empty, OSError, KeyError):
            raise AccountError('Codex 账号操作超时或连接中断，请刷新后重试') from None

    def __exit__(self, *_):
        process = self.process
        if not process:
            return
        try:
            process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        self.reader.join(timeout=1)
        process.stdout.close()


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def subscription_period(home, email=None):
    """Optional native-login claim, not a billing API or token-expiry estimate."""
    try:
        path = Path(home)/'auth.json'
        tokens = read_json(path, {}).get('tokens') or {}
        for name in ('id_token', 'access_token'):
            try:
                encoded = tokens.get(name, '').split('.')[1]
                claims = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
                if email and claims.get('email') and claims['email'] != email:
                    continue
                auth = claims.get('https://api.openai.com/auth') or {}
                value = auth.get('chatgpt_subscription_active_until')
                if isinstance(value, str):
                    if value.replace('.', '', 1).isdigit():
                        value = float(value)
                    else:
                        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
                        value = parsed.timestamp() if parsed.tzinfo else None
                if number(value) and value > 0:
                    return {'periodEndsAt':value, 'source':'login', 'observedAt':path.stat().st_mtime}
            except (ValueError, KeyError, IndexError, TypeError):
                continue
    except (OSError, ValueError, TypeError):
        pass
    return None


def normalize_limits(raw):
    buckets = raw.get('rateLimitsByLimitId')
    if not isinstance(buckets, dict):
        legacy = raw.get('rateLimits')
        buckets = {legacy.get('limitId') or 'codex': legacy} if isinstance(legacy, dict) else {}
    limits = []
    for key, bucket in buckets.items():
        if not isinstance(bucket, dict):
            continue
        windows = []
        for name in ('primary', 'secondary'):
            value = bucket.get(name)
            if not isinstance(value, dict):
                continue
            used = value.get('usedPercent')
            windows.append({'kind': name, 'remainingPercent': max(0, min(100, 100-used)) if number(used) else None,
                            **{field: value.get(field) if number(value.get(field)) else None
                               for field in ('windowDurationMins', 'resetsAt')}})
        credit = bucket.get('credits')
        credits = None
        if isinstance(credit, dict):
            balance = credit.get('balance')
            credits = {'hasCredits':credit.get('hasCredits') if isinstance(credit.get('hasCredits'), bool) else None,
                       'unlimited':credit.get('unlimited') is True,
                       'balance':balance if number(balance) or isinstance(balance, str) and len(balance) <= 80 else None}
        limits.append({'id': str(key), 'name': bucket.get('limitName') or str(key), 'windows': windows,
                       'credits':credits})
    resets = raw.get('rateLimitResetCredits')
    cards = None
    if isinstance(resets, dict):
        count = resets.get('availableCount')
        details = resets.get('credits')
        cards = {'availableCount': count if isinstance(count, int) and not isinstance(count, bool) and count >= 0 else None,
                 'credits': [{key: row.get(key) for key in ('id', 'resetType', 'status', 'expiresAt', 'title', 'description')}
                             for row in details if isinstance(row, dict) and isinstance(row.get('id'), str)]
                 if isinstance(details, list) else None}
    return {'limits': limits, 'resetCredits': cards}


class Account:
    def __init__(self, home, data_dir, executable=None):
        self.home = Path(home)
        self.executable = executable or Catalog.find_runtime()
        self.path = Path(data_dir) / 'account-resets.json'
        self.lock = threading.Lock()
        self.usage_cache = {}

    def rpc(self):
        return AccountRPC(self.home, self.executable)

    @staticmethod
    def context(rpc):
        config = rpc.request('config/read', {'includeLayers': False}).get('config', {})
        profile = (config.get('profiles') or {}).get(config.get('profile'), {})
        provider = profile.get('model_provider', config.get('model_provider')) or 'openai'
        definition = (config.get('model_providers') or {}).get(provider) or {}
        # A custom provider can use ChatGPT-shaped credentials. It is not native login.
        if provider != 'openai' or profile.get('openai_base_url', config.get('openai_base_url')) or any(definition.get(key) for key in
                ('base_url', 'env_key', 'experimental_bearer_token', 'http_headers', 'env_http_headers', 'auth', 'gateway_oauth')):
            return {'loginType': 'api'}
        auth = rpc.request('account/read', {'refreshToken': False})
        account = auth.get('account') or {}
        if not account:
            return {'loginType': 'signedOut'}
        if account.get('type') == 'apiKey':
            return {'loginType': 'api'}
        if account.get('type') != 'chatgpt' or auth.get('requiresOpenaiAuth') is not True:
            return {'loginType': 'unknown'}
        identity = json.dumps([account, auth.get('workspaceRouting')], sort_keys=True, separators=(',', ':'))
        return {'loginType': 'chatgpt', 'accountKey': hashlib.sha256(identity.encode()).hexdigest(),
                'email': account.get('email'), 'planType': account.get('planType'),
                'canReset': (config.get('desktop') or {}).get('agent-usage-reset-enabled') is True}

    def limits(self, rpc, context, refresh=False):
        # Called under the shared account lock by both account views.
        key = context['accountKey']
        cached = self.usage_cache.get(key)
        if not refresh and cached and time.time()-cached['checkedAt'] < 300:
            return copy.deepcopy(cached['value'])
        value = normalize_limits(rpc.request('account/rateLimits/read'))
        value['updatedAt'] = time.time()
        self.usage_cache[key] = {'checkedAt':value['updatedAt'], 'value':value}
        return copy.deepcopy(value)

    def _status(self, rpc, context, refresh=False):
        if not context.get('accountKey'):
            return {'visible': False, 'loginType': context['loginType']}
        result = {'visible': True, **context, 'limits': [], 'resetCredits': None, 'error': None}
        try:
            result.update(self.limits(rpc, context, refresh=refresh))
        except AccountError as exc:
            result.update(copy.deepcopy(self.usage_cache.get(context['accountKey'], {}).get('value', {})))
            result['error'] = str(exc)
        current = self.context(rpc)
        if current.get('accountKey') != context['accountKey']:
            return {'visible': False, 'loginType': current['loginType'] if not current.get('accountKey') else 'unknown'}
        result['canReset'] = current['canReset']
        ledger = read_json(self.path, {})
        result['pendingReset'] = next(({'requestId': key, 'accountKey': row['accountKey'], 'creditId': row['creditId']}
                                      for key, row in ledger.items()
                                      if row['accountKey'] == context['accountKey'] and not row.get('outcome')), None)
        result.setdefault('updatedAt', None)
        result['subscription'] = subscription_period(self.home, context.get('email'))
        return result

    def read(self, refresh=True):
        with self.lock, self.rpc() as rpc:
            context = self.context(rpc)
            return self._status(rpc, context, refresh=refresh)

    def consume(self, value):
        if value.get('confirmed') is not True:
            raise ValueError('请先确认使用重置卡')
        request_id, account_key, credit_id = (value.get(key) for key in ('requestId', 'accountKey', 'creditId'))
        if not isinstance(request_id, str) or str(uuid.UUID(request_id)) != request_id:
            raise ValueError('重置请求标识无效')
        if not isinstance(account_key, str) or len(account_key) != 64:
            raise ValueError('请刷新账号信息后重试')
        if credit_id is not None and (not isinstance(credit_id, str) or not 1 <= len(credit_id) <= 1024):
            raise ValueError('重置卡标识无效')
        with self.lock, self.rpc() as rpc:
            self.usage_cache.clear()
            context = self.context(rpc)
            if not context.get('accountKey'):
                raise PermissionError('仅官方 ChatGPT 账号可使用重置卡')
            if context['accountKey'] != account_key:
                raise PermissionError('电脑端账号已变化，请刷新后重新确认')
            if not context['canReset']:
                raise PermissionError('请先在 Codex 桌面设置中允许使用额度重置')
            ledger = read_json(self.path, {})
            previous = ledger.get(request_id)
            if previous and (previous['accountKey'] != account_key or previous['creditId'] != credit_id):
                raise ValueError('同一重置请求不能用于不同账号或卡片')
            if previous and previous.get('outcome'):
                return {'outcome': previous['outcome'], 'account': self._status(rpc, context)}
            if not previous:
                if any(row['accountKey'] == account_key and not row.get('outcome') for row in ledger.values()):
                    raise AccountError('上次重置结果尚未确认，请刷新并重试原请求')
                status = self._status(rpc, context)
                if not status.get('visible') or not status.get('canReset'):
                    raise PermissionError('电脑端账号或重置权限已变化，请刷新后重新确认')
                cards = status['resetCredits']
                if status['error'] or cards is None or not cards['availableCount']:
                    raise AccountError('没有可用重置卡，或暂时无法读取卡片信息')
                if credit_id is not None and not any(
                        row['id'] == credit_id and row['status'] == 'available' and
                        row['resetType'] == 'codexRateLimits' and
                        (row['expiresAt'] is None or number(row['expiresAt']) and row['expiresAt'] > time.time())
                        for row in cards['credits'] or []):
                    raise ValueError('此重置卡已不可用，请刷新后重新选择')
            current = self.context(rpc)
            if current.get('accountKey') != account_key or not current['canReset']:
                raise PermissionError('电脑端账号或重置权限已变化，请刷新后重新确认')
            if not previous:
                ledger[request_id] = {'accountKey': account_key, 'creditId': credit_id, 'at': time.time()}
                write_json(self.path, ledger)
            params = {'idempotencyKey': request_id}
            if credit_id is not None:
                params['creditId'] = credit_id
            # Never retry automatically. An uncertain attempt keeps its key across restart.
            result = rpc.request('account/rateLimitResetCredit/consume', params)
            outcome = result.get('outcome')
            if outcome not in ('reset', 'alreadyRedeemed', 'nothingToReset', 'noCredit'):
                raise AccountError('重置结果尚未确认，请刷新并重试原请求')
            ledger[request_id]['outcome'] = outcome
            write_json(self.path, ledger)
            self.usage_cache.pop(account_key, None)
            return {'outcome': outcome, 'account': self._status(rpc, context)}

    def control(self, value):
        action = value.get('action', 'read')
        if action == 'read':
            return self.read(refresh=value.get('refresh', True) is not False)
        if action == 'consume':
            return self.consume(value)
        raise ValueError('不支持的账号操作')
