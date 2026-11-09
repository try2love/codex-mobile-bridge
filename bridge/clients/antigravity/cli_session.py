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


class AntigravityCli:
    """Manage live Antigravity Language Server native RPC communication for zero-latency sync."""

    def __init__(self, home=None):
        self.home = Path(home).expanduser().resolve() if home else None
        self._lock = threading.Lock()
        self._runs = {}
        self._catalog_cache = None

    def binary(self):
        return discover_agy_binary(self.home)

    def available(self):
        addr, token = self._get_ls_info()
        return bool(addr and token) or bool(self.binary())

    def _is_desktop_process_alive(self):
        try:
            out = subprocess.check_output(['pgrep', '-a', 'language_server'], text=True)
            if out.strip():
                return True
        except Exception:
            pass
        try:
            out = subprocess.check_output(['pgrep', '-a', 'antigravity'], text=True)
            if out.strip():
                return True
        except Exception:
            pass
        return False

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
                    for line in f:
                        m = re.search(r'Language server listening on random port at (\d+) for HTTP', line)
                        if m:
                            port = int(m.group(1))
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
            quota_status = 'good'
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

    def call_ls_rpc(self, method, payload=None, timeout=6.0):
        """Invoke a gRPC-Web JSON-RPC endpoint on the active Antigravity Language Server."""
        addr, token = self._get_ls_info()
        if not addr or not token:
            if self._is_desktop_process_alive():
                raise BridgeUnavailable('檢測到 Antigravity 桌面端進程正在運行，但內部通訊介面暫未響應；為維護 Single Writer 原則並避免對話樹分叉，已阻止獨立 CLI 寫入。請在桌面端重啟或確認連線。')
            raise BridgeUnavailable('未檢測到正在運行的 Antigravity Desktop / Language Server 進程')
        req = urllib.request.Request(
            f'http://{addr}/exa.language_server_pb.LanguageServerService/{method}',
            data=json.dumps(payload or {}).encode('utf-8'),
            headers={
                'Content-Type': 'application/json',
                'x-codeium-csrf-token': token,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                return json.loads(raw.decode('utf-8', errors='replace')) if raw else {}
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode('utf-8', errors='replace')
            raise BridgeUnavailable(f'Antigravity Language Server 請求失敗 ({method}): {err_body}') from exc
        except Exception as exc:
            raise BridgeUnavailable(f'無法連接 Antigravity Language Server：{exc}') from exc

    def resolve_model_enum(self, model, effort=None):
        if not model:
            return None
        m_str = str(model).strip()
        e_str = str(effort or '').strip().lower()
        if not e_str:
            for suffix in ('-high', '-medium', '-low'):
                if m_str.endswith(suffix):
                    e_str = suffix[1:]
                    m_str = m_str[:-len(suffix)]
                    break
        if not e_str:
            e_str = 'high' if 'flash' in m_str else 'medium'

        target_model_id = MODEL_ALIAS_MAP.get((m_str, e_str), f'{m_str}-{e_str}')

        try:
            ls_status = self.language_server_status()
            configs = ls_status.get('userStatus', {}).get('cascadeModelConfigData', {}).get('clientModelConfigs', [])
            for c in configs:
                if c.get('modelId') == target_model_id:
                    m_alias = c.get('modelOrAlias')
                    if isinstance(m_alias, dict) and m_alias.get('model'):
                        return m_alias['model']
        except Exception:
            pass

        fallback_map = {
            'gemini-4-argon-high': 'MODEL_PLACEHOLDER_M417',
            'gemini-4-argon-medium': 'MODEL_PLACEHOLDER_M418',
            'gemini-4-argon-low': 'MODEL_PLACEHOLDER_M419',
            'gemini-3.8-flash-high': 'MODEL_PLACEHOLDER_M318',
            'gemini-3.8-flash-medium': 'MODEL_PLACEHOLDER_M319',
            'gemini-3.8-flash-low': 'MODEL_PLACEHOLDER_M320',
            'claude-opus-5-5-low': 'MODEL_PLACEHOLDER_M400',
            'claude-opus-5-5-medium': 'MODEL_PLACEHOLDER_M401',
            'claude-opus-5-5-high': 'MODEL_PLACEHOLDER_M402',
            'claude-sonnet-5-5-low': 'MODEL_PLACEHOLDER_M403',
            'claude-sonnet-5-5-medium': 'MODEL_PLACEHOLDER_M404',
            'claude-sonnet-5-5-high': 'MODEL_PLACEHOLDER_M405',
            'gpt-oss-120b-medium': 'MODEL_OPENAI_GPT_OSS_120B_MEDIUM',
        }
        return fallback_map.get(target_model_id)

    def send_user_message(self, conversation_id, prompt, model=None, effort=None):
        """Send message directly into the native Antigravity session (NO ACP, 100% in-sync)."""
        text = str(prompt or '').strip()
        if not text:
            raise ValueError('訊息內容不能為空')
        if not conversation_id:
            raise ValueError('缺少會話 ID')
        body = {
            'cascade_id': str(conversation_id),
            'items': [{'text': text}],
        }
        model_enum = self.resolve_model_enum(model, effort)
        if model_enum:
            body['cascade_config'] = {
                'planner_config': {
                    'requested_model': {
                        'model': model_enum,
                    }
                }
            }
        return self.call_ls_rpc('SendUserCascadeMessage', body)

    def start_cascade(self, source='CORTEX_TRAJECTORY_SOURCE_CLI', model=None, effort=None):
        """Create a new native conversation in the Antigravity Language Server."""
        body = {'source': source}
        model_enum = self.resolve_model_enum(model, effort)
        if model_enum:
            body['requested_model'] = model_enum
        res = self.call_ls_rpc('StartCascade', body)
        cid = res.get('cascadeId') or res.get('cascade_id')
        if not cid:
            raise BridgeUnavailable('Antigravity Language Server 未返回新會話 ID')
        return cid

    def cancel_cascade(self, conversation_id):
        """Cancel an ongoing invocation in the native Language Server."""
        if not conversation_id:
            return False
        try:
            self.call_ls_rpc('CancelCascadeInvocation', {'cascade_id': str(conversation_id)})
            return True
        except Exception:
            return False

    def is_running(self, conversation_id):
        return False

    def start_turn(self, surface_key, conversation_id, prompt, *, cwd=None, model=None, effort=None, wait_init=8.0):
        """Native turn starter: invokes Language Server directly without spawning `agy acp`."""
        text = str(prompt or '').strip()
        if not text:
            raise ValueError('訊息內容不能為空')
        cid = conversation_id or self.start_cascade(model=model, effort=effort)
        self.send_user_message(cid, text, model=model, effort=effort)
        return cid

    def stop(self, conversation_id):
        return self.cancel_cascade(conversation_id)

    def close(self):
        pass
