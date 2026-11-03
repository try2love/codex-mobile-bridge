"""Read the Start Plan balance receipt written by the original local ZCode host."""
import json
import os
import re
import stat
from datetime import datetime, timedelta
from urllib.parse import urlsplit


# Match main's local-host log envelope. A session or remote-host log can also
# contain the words "billing/balance" and is not an account usage receipt.
RECEIPT = re.compile(
    rb'^\[([0-9-]{10} [0-9:.]{12})\] \[info\] \[pid:\d+\] \[main\] \[host-log\] '
    rb'\(local-\d+\) \[host\] \[[0-9-]{10} [0-9:.]{12}\] \[pid:\d+\] '
    rb'\[usage-stats\] billing/balance [^{}\r\n]{1,60} (\{.*\})$')
TAIL_LIMIT = 2 * 1024 * 1024


def regular(path, directory=False):
    info = path.lstat()
    test = stat.S_ISDIR if directory else stat.S_ISREG
    if not test(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise ValueError('ZCode 额度数据路径不可用')
    return info


def login_generation(home):
    """Pin reads to one original official login without decrypting its material."""
    path = home / 'credentials.json'
    try:
        regular(home, directory=True)
        before = regular(path)
        with path.open('rb') as stream:
            if not os.path.samestat(before, os.fstat(stream.fileno())):
                return None
            fields = json.load(stream)
        after = regular(path)
        if (not os.path.samestat(before, after) or before.st_mtime_ns != after.st_mtime_ns
                or before.st_ctime_ns != after.st_ctime_ns or not isinstance(fields, dict)):
            return None
        active = fields.get('oauth:active_provider')
        if not isinstance(active, str) or not active.startswith('enc:v1:'):
            return None
        families = {k.split(':')[1] for k in fields if re.fullmatch(r'oauth:[^:]+:(access_token|user_info)', k)}
        if len(families) == 1:
            family = families.pop()
            login = [fields.get('oauth:'+family+':'+key) for key in ('access_token', 'user_info')]
            if family in ('bigmodel', 'zai') and all(isinstance(v, str) and v.startswith('enc:v1:') for v in login):
                return (family, after.st_dev, after.st_ino, after.st_mtime_ns, after.st_ctime_ns)
    except (OSError, ValueError):
        pass
    return None


def integer(value):
    if isinstance(value, str) and re.fullmatch('[0-9]{1,16}', value):
        value = int(value)
    return value if type(value) is int and 0 <= value <= 2**53-1 else None


def daily_limits(data, checked, now):
    plans = {p.get('user_plan_id'): p for p in data.get('plans') or []
             if isinstance(p, dict) and isinstance(p.get('user_plan_id'), str) and p.get('status') == 'active'}
    limits = []
    for bucket in data.get('balances') or []:
        if not isinstance(bucket, dict) or not isinstance(bucket.get('user_plan_id'), str):
            continue
        plan = plans.get(bucket['user_plan_id'])
        if not plan or bucket.get('plan_id') != plan.get('plan_id') or bucket.get('unit_type') != 'token':
            continue
        expires = integer(plan.get('ends_at'))
        if expires is not None and expires <= now:
            continue
        entitlements = plan.get('entitlements') or []
        if not any(isinstance(e, dict) and e.get('entitlement_id') == bucket.get('entitlement_id')
                   and e.get('period') == 'daily' for e in entitlements):
            continue
        start, end, remaining, total = (integer(bucket.get(k)) for k in
                                        ('period_start', 'period_end', 'remaining_units', 'total_units'))
        if None in (start, end, remaining, total) or not start <= checked <= now <= end or remaining > total:
            continue
        models = [c[6:] for c in bucket.get('capabilities') or []
                  if isinstance(c, str) and re.fullmatch(r'model:[a-zA-Z0-9_.-]{1,80}', c)]
        if not models:
            continue
        label = bucket.get('show_name')
        name = (label if len(models) == 1 and isinstance(label, str) and label.lower() == models[0].lower()
                else ', '.join(models))
        limits.append({'name': name, 'windows': [{'remaining': remaining, 'total': total, 'unit': 'tokens',
                       'remainingPercent': 100*remaining/total if total else None, 'validUntil': min(end, expires-1) if expires is not None else end}]})
    return limits


def read_receipt(path, family, changed, now):
    info = regular(path)
    with path.open('rb') as stream:
        if not os.path.samestat(info, os.fstat(stream.fileno())):
            return None
        stream.seek(max(0, info.st_size-TAIL_LIMIT))
        lines = stream.read(TAIL_LIMIT).splitlines()
    for line in reversed(lines):
        if b'[usage-stats] billing/balance ' not in line:
            continue
        match = RECEIPT.fullmatch(line)
        if not match:
            continue
        try:
            logged = datetime.fromisoformat(match[1].decode()).timestamp()
            event = json.loads(match[2])
            if event.get('providerId') != 'account:'+family+'-start-plan' or event.get('success') is not True:
                continue
            url = urlsplit(event.get('url', ''))
            if (url.scheme, url.netloc, url.path) != ('https', 'zcode.z.ai', '/api/v1/zcode-plan/billing/balance'):
                continue
            payload = event['payload']
            if type(payload.get('code')) is not int or payload['code'] != 0:
                continue
            data = payload['data']
            checked = integer(data.get('server_time'))
            # The native read has a 15s timeout + 1s request cache. A reply from
            # a request begun before a credential rotation cannot identify this login.
            if not checked or min(checked, logged) < changed+16 or checked > now or logged > now+2:
                continue
            limits = daily_limits(data, checked, now)
            return {'status': 'ok' if limits else 'unsupported', 'source': 'nativeSnapshot',
                    'checkedAt': checked, 'limits': limits}
        except (AttributeError, IndexError, KeyError, OSError, TypeError, ValueError, OverflowError):
            continue
    return None


def latest(home, generation, now):
    folder = home / 'logs'
    changed = max(generation[-2:])/1e9
    date = datetime.fromtimestamp(now).date()
    for day in (date, date-timedelta(days=1)):
        try:
            regular(folder, directory=True)
            result = read_receipt(folder / (day.isoformat()+'.log'), generation[0], changed, now)
        except (OSError, ValueError):
            continue
        if result:
            return result
    return {'status': 'unsupported', 'source': 'nativeSnapshot', 'limits': []}
