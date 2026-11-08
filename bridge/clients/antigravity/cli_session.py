"""Cold-session resume and new-conversation launcher via the official `agy` CLI / ACP server."""
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import urllib.request

from bridge.clients.errors import BridgeUnavailable
from .setup import discover_agy_binary

APP_DATA_DIR_NAMES = {
    'app': 'antigravity',
    'ide': 'antigravity-ide',
    'cli': 'antigravity-cli',
}

CANONICAL_BASE_MODELS = (
    {
        'id': 'gemini-4-argon',
        'name': 'Gemini 4 Argon',
        'efforts': ['low', 'medium', 'high'],
        'defaultEffort': 'medium',
        'contextWindow': 1_048_576,
    },
    {
        'id': 'gemini-3.8-flash',
        'name': 'Gemini 3.8 Flash',
        'efforts': ['low', 'medium', 'high'],
        'defaultEffort': 'high',
        'contextWindow': 1_048_576,
    },
    {
        'id': 'claude-opus-5-5',
        'name': 'Claude Opus 5.5',
        'efforts': ['low', 'medium', 'high'],
        'defaultEffort': 'medium',
        'contextWindow': 200_000,
    },
    {
        'id': 'claude-sonnet-5-5',
        'name': 'Claude Sonnet 5.5',
        'efforts': ['low', 'medium', 'high'],
        'defaultEffort': 'medium',
        'contextWindow': 200_000,
    },
    {
        'id': 'gpt-oss-120b-medium',
        'name': 'GPT-OSS 120B (Medium)',
        'efforts': ['medium'],
        'defaultEffort': 'medium',
        'contextWindow': 131_072,
    },
)

MODEL_ALIAS_MAP = {
    ('gemini-4-argon', 'low'): 'gemini-4-argon-low',
    ('gemini-4-argon', 'medium'): 'gemini-4-argon-medium',
    ('gemini-4-argon', 'high'): 'gemini-4-argon-high',
    ('gemini-3.8-flash', 'low'): 'gemini-3.8-flash-low',
    ('gemini-3.8-flash', 'medium'): 'gemini-3.8-flash-medium',
    ('gemini-3.8-flash', 'high'): 'gemini-3.8-flash-high',
    ('claude-opus-5-5', 'low'): 'claude-opus-5-5-low',
    ('claude-opus-5-5', 'medium'): 'claude-opus-5-5-medium',
    ('claude-opus-5-5', 'high'): 'claude-opus-5-5-high',
    ('claude-sonnet-5-5', 'low'): 'claude-sonnet-5-5-low',
    ('claude-sonnet-5-5', 'medium'): 'claude-sonnet-5-5-medium',
    ('claude-sonnet-5-5', 'high'): 'claude-sonnet-5-5-high',
    ('gpt-oss-120b-medium', 'medium'): 'gpt-oss-120b-medium',
}


class _ActiveRun:
    def __init__(self, process, surface_key, conversation_id=None, cwd=''):
        self.process = process
        self.surface_key = surface_key
        self.conversation_id = conversation_id
        self.cwd = cwd
        self.init_event = threading.Event()
        self.error = None
        self.started_at = time.time()


class AntigravityCli:
    """Manage background `agy acp --app-data-dir` invocations for cold resume and session creation."""

    def __init__(self, home=None):
        self.home = Path(home).expanduser().resolve() if home else None
        self._lock = threading.Lock()
        self._runs = {}
        self._catalog_cache = None

    def binary(self):
        return discover_agy_binary(self.home)

    def available(self):
        return bool(self.binary())

    def _get_ls_info(self):
        addr = os.environ.get('ANTIGRAVITY_LS_ADDRESS')
        token = os.environ.get('ANTIGRAVITY_CSRF_TOKEN')
        if addr and token:
            return addr, token

        log_dir = self.home if self.home else Path.home()
        log_path = log_dir / '.config' / 'Antigravity' / 'logs' / 'language_server.log'
        port = None
        if log_path.is_file():
            try:
                with log_path.open('r', encoding='utf-8', errors='replace') as f:
                    for line in reversed(f.readlines()[-100:]):
                        m = re.search(r'Language server listening on random port at (\d+) for HTTP', line)
                        if m:
                            port = int(m.group(1))
                            break
            except Exception:
                pass

        if not token:
            try:
                out = subprocess.check_output(['pgrep', '-a', 'language_server'], text=True)
                for line in out.splitlines():
                    m = re.search(r'--csrf_token\s+([a-f0-9\-]+)', line)
                    if m:
                        token = m.group(1)
                        break
            except Exception:
                pass

        if port and token:
            return f'localhost:{port}', token
        return None, None

    def language_server_status(self):
        addr, token = self._get_ls_info()
        if not addr or not token:
            return {}
        try:
            req = urllib.request.Request(
                f'http://{addr}/exa.language_server_pb.LanguageServerService/GetUserStatus',
                data=b'{}',
                headers={'Content-Type': 'application/json', 'x-codeium-csrf-token': token},
            )
            with urllib.request.urlopen(req, timeout=2.5) as resp:
                return json.loads(resp.read().decode('utf-8', errors='replace'))
        except Exception:
            return {}

    def catalog_models(self, *, refresh=False):
        now = time.monotonic()
        with self._lock:
            if not refresh and self._catalog_cache and (now - self._catalog_cache[0]) < 45.0:
                return dict(self._catalog_cache[1])

        ls_status = self.language_server_status()
        user_status = ls_status.get('userStatus') or {}
        user_tier = user_status.get('userTier') or {}
        tier_name = str(user_tier.get('name') or 'Google AI Pro')
        plan_status = user_status.get('planStatus') or {}
        configs = user_status.get('cascadeModelConfigData', {}).get('clientModelConfigs', [])
        quotas = {}
        for c in configs:
            mid = c.get('modelId')
            q = c.get('quotaInfo')
            if mid and q:
                quotas[mid] = q

        notice_lines = [f'【官方帳號與額度 · {tier_name}】']
        catalog_models = []

        for base in CANONICAL_BASE_MODELS:
            bid = base['id']
            # Find representative quota
            sample_keys = [
                MODEL_ALIAS_MAP.get((bid, effort), f'{bid}-{effort}')
                for effort in base['efforts']
            ]
            q_info = next((quotas[k] for k in sample_keys if k in quotas), None)
            quota_tag = ''
            reset_note = ''
            if q_info:
                frac = q_info.get('remainingFraction')
                reset_time = str(q_info.get('resetTime') or '')
                if reset_time and 'T' in reset_time:
                    try:
                        # Format ISO date like 11/14 06:27
                        dt_part = reset_time.split('T')
                        md = '/'.join(dt_part[0].split('-')[1:])
                        hm = dt_part[1][:5]
                        reset_note = f'將於 {md} {hm} 重置'
                    except Exception:
                        reset_note = f'重置時間 {reset_time}'

                if frac is None or frac <= 0.005:
                    quota_tag = '剩餘 0.6%' if frac is not None else '額度已用盡'
                    quota_status = 'exhausted'
                elif frac >= 0.999:
                    quota_tag = '剩餘 100%'
                    quota_status = 'good'
                else:
                    quota_tag = f'剩餘 {frac * 100.0:.1f}%'
                    quota_status = 'warning' if frac < 0.2 else 'good'

                notice_line = f"• {base['name']}: {quota_tag}"
                if reset_note and (frac is None or frac < 0.999):
                    notice_line += f' ({reset_note})'
                notice_lines.append(notice_line)

            catalog_models.append({
                'id': bid,
                'name': base['name'],
                'baseName': base['name'],
                'efforts': list(base['efforts']),
                'supportsEffort': len(base['efforts']) > 1,
                'defaultEffort': base['defaultEffort'],
                'contextWindow': base['contextWindow'],
                'quotaTag': quota_tag,
                'quotaStatus': quota_status,
                'resetNote': reset_note,
            })

        p_credits = plan_status.get('availablePromptCredits')
        f_credits = plan_status.get('availableFlowCredits')
        credits_line = ''
        if p_credits is not None or f_credits is not None:
            credits_line = f'可用 Prompt 點數: {p_credits or 0} · Flow 點數: {f_credits or 0}'
            notice_lines.append(credits_line)

        usage_items = []
        for m in catalog_models:
            if m.get('quotaTag'):
                usage_items.append({
                    'name': m['baseName'],
                    'val': m['quotaTag'],
                    'note': m.get('resetNote') or '',
                    'status': m.get('quotaStatus') or 'good',
                })

        payload = {
            'models': catalog_models,
            'notice': '\n'.join(notice_lines),
            'usageSummary': {
                'tier': tier_name,
                'items': usage_items,
                'credits': credits_line,
            },
            'tier': tier_name,
            'userStatus': user_status,
        }
        with self._lock:
            self._catalog_cache = (now, payload)
        return dict(payload)

    def models(self, *, refresh=False):
        return self.catalog_models(refresh=refresh)['models']

    def is_running(self, conversation_id):
        if not conversation_id:
            return False
        with self._lock:
            run = self._runs.get(conversation_id)
            if run is None:
                return False
            if run.process.poll() is None:
                return True
            self._runs.pop(conversation_id, None)
            return False

    def start_turn(self, surface_key, conversation_id, prompt, *, cwd=None, model=None, effort=None, wait_init=8.0):
        """Launch `agy acp --app-data-dir <surface>` to resume a cold conversation or create a new one."""
        binary = self.binary()
        if not binary:
            raise BridgeUnavailable('未找到本機 agy CLI 執行檔，請確認已安裝 Antigravity CLI')

        text = str(prompt or '').strip()
        if not text:
            raise ValueError('訊息內容不能為空')

        if conversation_id and self.is_running(conversation_id):
            raise BridgeUnavailable('此會話已有 CLI 任務正在執行，請稍候或先停止')

        app_data = APP_DATA_DIR_NAMES.get(surface_key, 'antigravity')
        cmd = [binary, 'acp', '--app-data-dir', app_data]

        work_dir = str(Path(cwd).resolve()) if cwd and Path(cwd).is_dir() else str(self.home or Path.home())
        env = os.environ.copy()
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=work_dir,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace',
                bufsize=1,
            )
        except OSError as exc:
            raise BridgeUnavailable(f'無法啟動 agy CLI：{exc}') from exc

        run = _ActiveRun(proc, surface_key, conversation_id=conversation_id, cwd=work_dir)
        if conversation_id:
            with self._lock:
                self._runs[conversation_id] = run

        threading.Thread(
            target=self._drive_acp_turn,
            args=(run, text, model, effort),
            name=f'agy-acp-{conversation_id or "new"}',
            daemon=True,
        ).start()

        run.init_event.wait(timeout=wait_init)
        if run.error:
            raise BridgeUnavailable(run.error)
        return run.conversation_id or conversation_id

    def _drive_acp_turn(self, run, prompt_text, model, effort):
        req_seq = 0

        def send_rpc(method, params):
            nonlocal req_seq
            req_seq += 1
            rid = req_seq
            payload = json.dumps({'jsonrpc': '2.0', 'id': rid, 'method': method, 'params': params})
            if run.process.stdin is None:
                raise RuntimeError('agy ACP stdin closed')
            run.process.stdin.write(payload + '\n')
            run.process.stdin.flush()
            while True:
                line = run.process.stdout.readline() if run.process.stdout is not None else ''
                if not line:
                    stderr_text = ''
                    if run.process.stderr is not None:
                        try:
                            stderr_text = run.process.stderr.read().strip()
                        except OSError:
                            stderr_text = ''
                    raise RuntimeError(stderr_text or f'agy ACP 異常終止 ({method})')
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue
                if msg.get('id') == rid:
                    if 'error' in msg and msg['error']:
                        err = msg['error']
                        err_msg = err.get('message') if isinstance(err, dict) else str(err)
                        raise RuntimeError(err_msg or f'agy ACP 錯誤 ({method})')
                    return msg.get('result') or {}
                # Handle reverse permission requests if any arrive during prompt
                if msg.get('method') == 'session/request_permission' and 'id' in msg:
                    resp = {
                        'jsonrpc': '2.0',
                        'id': msg['id'],
                        'result': {'outcome': {'outcome': 'selected', 'optionId': 'allow'}},
                    }
                    try:
                        run.process.stdin.write(json.dumps(resp) + '\n')
                        run.process.stdin.flush()
                    except OSError:
                        pass

        try:
            send_rpc('initialize', {'protocolVersion': 1, 'clientCapabilities': {}})
            if run.conversation_id:
                send_rpc(
                    'session/resume',
                    {'sessionId': run.conversation_id, 'cwd': run.cwd, 'mcpServers': []},
                )
            else:
                res = send_rpc('session/new', {'cwd': run.cwd, 'mcpServers': []})
                sid = res.get('sessionId')
                if isinstance(sid, str) and sid.strip():
                    run.conversation_id = sid.strip()
                    with self._lock:
                        self._runs[run.conversation_id] = run

            if model and str(model).strip() and str(model).strip() != 'auto':
                m_str = str(model).strip()
                e_str = str(effort or '').strip().lower()
                effective_id = MODEL_ALIAS_MAP.get((m_str, e_str)) or m_str
                try:
                    send_rpc(
                        'session/set_model',
                        {'sessionId': run.conversation_id, 'modelId': effective_id},
                    )
                except Exception:
                    pass

            if effort and str(effort).strip() and str(effort).strip() not in ('auto', 'none'):
                try:
                    send_rpc(
                        'session/set_config_option',
                        {
                            'sessionId': run.conversation_id,
                            'configId': 'thought_level',
                            'value': str(effort).strip(),
                        },
                    )
                except Exception:
                    pass

            # Session is now initialized/resumed; release start_turn caller
            run.init_event.set()

            send_rpc(
                'session/prompt',
                {
                    'sessionId': run.conversation_id,
                    'prompt': [{'type': 'text', 'text': prompt_text}],
                },
            )
        except Exception as exc:
            run.error = str(exc)
            run.init_event.set()
        finally:
            run.init_event.set()
            try:
                if run.process.poll() is None:
                    run.process.terminate()
                    try:
                        run.process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        run.process.kill()
            except OSError:
                pass
            if run.conversation_id:
                with self._lock:
                    current = self._runs.get(run.conversation_id)
                    if current is run:
                        self._runs.pop(run.conversation_id, None)

    def stop(self, conversation_id):
        with self._lock:
            run = self._runs.pop(conversation_id, None)
        if run is None or run.process.poll() is not None:
            return False
        try:
            if run.process.stdin is not None:
                try:
                    cancel_msg = json.dumps({
                        'jsonrpc': '2.0',
                        'id': 9999,
                        'method': 'session/cancel',
                        'params': {'sessionId': conversation_id},
                    })
                    run.process.stdin.write(cancel_msg + '\n')
                    run.process.stdin.flush()
                except OSError:
                    pass
            run.process.terminate()
            try:
                run.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                run.process.kill()
                run.process.wait(timeout=2)
        except OSError:
            pass
        return True

    def close(self):
        with self._lock:
            cids = list(self._runs.keys())
        for cid in cids:
            self.stop(cid)
