"""Read ChatGPT subscription dates for one saved OAuth identity.

Product backend endpoints, not billing guarantees. Never infer expiry from JWTs,
choose another workspace, follow redirects, or expose upstream error bodies.
"""
import base64
from datetime import datetime
import json
import math
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import HTTPSHandler, Request, build_opener

from bridge.features.accounts.models import NoRedirect
from bridge.features.notifications.channels import read_json
from bridge.features.auth.tls import client_context


def timestamp(value):
    if isinstance(value, bool):
        return None
    try:
        if isinstance(value, str) and not value.replace('.', '', 1).isdigit():
            date = datetime.fromisoformat(value.replace('Z', '+00:00'))
            return date.timestamp() if date.tzinfo else None
        value = float(value)
        if not math.isfinite(value) or value <= 0:
            return None
        return value / 1000 if value > 1_000_000_000_000 else value
    except (ValueError, TypeError, OverflowError):
        return None


def claims(token):
    try:
        encoded = token.split('.')[1]
        value = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
        return value if isinstance(value, dict) else {}
    except (ValueError, IndexError, TypeError):
        return {}


def request_json(path, token, query=None):
    url = 'https://chatgpt.com/backend-api/' + path
    if query:
        url += '?' + urlencode(query)
    request = Request(url, headers={'Authorization': 'Bearer '+token, 'Accept': 'application/json',
                                   'User-Agent': 'Codex-Mobile-Bridge', 'Referer': 'https://chatgpt.com/',
                                   'x-openai-target-path': '/backend-api/'+path,
                                   'x-openai-target-route': '/backend-api/'+path})
    opener = build_opener(NoRedirect(), HTTPSHandler(context=client_context()))
    with opener.open(request, timeout=8) as response:
        payload = response.read(1024*1024+1)
    if len(payload) > 1024*1024:
        raise ValueError('subscription response too large')
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError('invalid subscription response')
    return value


def subscription_period(home, email=None):
    observed = time.time()
    unknown = {'source': 'online', 'periodEndsAt': None, 'observedAt': observed}
    try:
        tokens = read_json(Path(home)/'auth.json', {}).get('tokens') or {}
        token = tokens.get('access_token')
        if not isinstance(token, str) or not token:
            return unknown
        identity = claims(token)
        login = claims(tokens.get('id_token', ''))
        profile = identity.get('https://api.openai.com/profile') or {}
        emails = [v for v in (identity.get('email'), profile.get('email'), login.get('email')) if v]
        if email and any(v.casefold() != email.casefold() for v in emails):
            return unknown
        auth = identity.get('https://api.openai.com/auth') or {}
        account_id = tokens.get('account_id') or auth.get('chatgpt_account_id')
        if not isinstance(account_id, str) or not account_id:
            return unknown
        claim_id = auth.get('chatgpt_account_id')
        if claim_id and claim_id != account_id:
            return unknown
        payload = request_json('accounts/check/v4-2023-04-27', token, {'timezone_offset_min': 0})
        records = payload.get('accounts')
        records = list(records.values()) if isinstance(records, dict) else records
        if not isinstance(records, list):
            return unknown
        selected = None
        for record in records:
            if not isinstance(record, dict):
                continue
            account = record.get('account', record)
            if not isinstance(account, dict):
                continue
            identifier = account.get('account_id') or account.get('id') or account.get('chatgpt_account_id') or account.get('workspace_id')
            if identifier == account_id:
                selected = record
                break
        if selected is None:
            return unknown
        entitlement = selected.get('entitlement') or {}
        account = selected.get('account', selected)
        end = timestamp(entitlement.get('expires_at') or account.get('expires_at'))
        if end is None or end <= observed:
            subscription = request_json('subscriptions', token, {'account_id': account_id})
            returned_id = subscription.get('account_id')
            if returned_id and returned_id != account_id:
                return unknown
            end = timestamp(subscription.get('active_until') or subscription.get('expires_at'))
        return {**unknown, 'periodEndsAt': end}
    except Exception:
        # Billing reads must not hide usable quota/cards or leak tokens/response bodies.
        return {**unknown, 'error': '订阅信息暂不可用，请稍后刷新'}
