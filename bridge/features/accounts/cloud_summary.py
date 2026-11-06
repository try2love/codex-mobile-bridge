"""Read-only projection of the current native Codex account for an approved cloud device."""
import math
import re
from urllib.parse import parse_qsl, urlsplit

SUMMARY_PATH = '/api/cloud/accounts'


def summary_query(method, target, length=0):
    url = urlsplit(target)
    if (method != 'GET' or length != 0 or len(target) > 512 or url.scheme or url.netloc or
            url.fragment or '#' in target or url.path != SUMMARY_PATH or any(ord(c) < 32 or ord(c) == 127 for c in target)):
        raise PermissionError('云账号摘要只支持受限的读取操作')
    try:
        pairs = parse_qsl(url.query, keep_blank_values=True, strict_parsing=True, max_num_fields=3, errors='strict') if url.query else []
    except ValueError:
        raise PermissionError('云账号摘要参数无效') from None
    query = dict(pairs)
    if len(query) != len(pairs) or set(query) - {'entry', 'section', 'refresh'}:
        raise PermissionError('云账号摘要参数无效')
    entry, section = query.get('entry', 'desktop'), query.get('section')
    if (entry != 'desktop' and not re.fullmatch(r'codex:[a-f0-9]{32}', entry) or
            section not in (None, 'usage', 'models') or
            'refresh' in query and (not section or query['refresh'] != '1')):
        raise PermissionError('此入口或账号摘要操作不受支持')
    return entry, section, 'refresh' in query


def _fields(value, strings=(), numbers=()):
    """Neither arbitrary nested objects nor unexpected upstream fields are public."""
    if not isinstance(value, dict):
        return {}
    result = {key: value[key] for key in strings if isinstance(value.get(key), str)}
    result.update({key: value[key] for key in numbers
                   if type(value.get(key)) in (float, int) and math.isfinite(value[key])})
    return result


def _detail(value, section):
    data = value.get(section)
    if not isinstance(data, dict):
        return None
    public = _fields(data, numbers=('updatedAt', 'checkedAt'))
    public['status'] = data.get('status') if data.get('status') in ('loading', 'ready', 'error') else 'ready'
    if public['status'] == 'error':
        public['error'] = '额度暂不可用' if section == 'usage' else '模型列表暂不可用'
    if section == 'models':
        public['models'] = []
        for model in data.get('models') or ():
            row = _fields(model, ('id', 'name'))
            if 'id' in row:
                efforts = model.get('efforts')
                row['efforts'] = [item for item in efforts if isinstance(item, str)] if isinstance(efforts, list) else []
                public['models'].append(row)
        public['cache'] = _fields(data.get('cache'), numbers=('updatedAt',))
        return public
    public.update(_fields(data, ('planType',)))
    if isinstance(data.get('limits'), list):
        public['limits'] = []
        for bucket in data['limits']:
            if not isinstance(bucket, dict):
                continue
            row = _fields(bucket, ('name',))
            row['windows'] = [_fields(window, numbers=('windowDurationMins','remainingPercent','resetsAt'))
                              for window in bucket.get('windows') or () if isinstance(window, dict)]
            credits = bucket.get('credits')
            if isinstance(credits, dict):
                balance = credits.get('balance')
                row['credits'] = _fields(credits, numbers=('balance',))
                if isinstance(balance, str) and len(balance) <= 80 and re.fullmatch(r'-?[0-9]+(?:\.[0-9]+)?', balance):
                    row['credits']['balance'] = balance
                row['credits']['unlimited'] = credits.get('unlimited') is True
            public['limits'].append(row)
    public['subscription'] = _fields(data.get('subscription'), ('source',), ('periodEndsAt',))
    public['resetCredits'] = _fields(data.get('resetCredits'), numbers=('availableCount',))
    return public


def current_summary(manager, section=None, refresh=False):
    """Do not traverse saved accounts, SSH hosts, updates or task blockers for this view."""
    with manager.lock:
        external = manager.management()['external']
        current = ({'status':'ready', 'kind':'external', 'name':'Magpie / 外部管理'} if external else manager.info.current())
        info = _fields(current, ('status', 'kind', 'name'))
        row = None
        if info.get('status') == 'ready' and info.get('kind') in ('api', 'chatgpt'):
            if info['kind'] == 'chatgpt':
                row = manager.info.current_login()
                if row and current.get('id') and row['id'] != current['id']:
                    raise ValueError('账号身份已变化，请刷新列表')
            if not row and current.get('id'):
                row = manager.row(current['id'])
        public = {'cloudReadOnly':True, 'current':info, 'scope':'profile' if manager.independent else 'desktop',
                  'canSwitch':False, 'canManageDesktop':False, 'accounts':[], 'loginAccount':None,
                  'management':{'external':external}, 'activeId':None, 'capabilities':{'models':False,'usage':False}}
        if not row:
            return public
        native_id = row['id']
        public['capabilities'] = {'usage':row['kind']=='chatgpt', 'models':True}
        if section:
            if not public['capabilities'][section]:
                raise PermissionError('当前接入不支持此账号详情')
            manager.info.request({'id':native_id, 'section':section, 'refresh':refresh}, snapshot=False)
        # current_login filters transient cached data by identity as well as route.
        if row.get('transient'):
            fresh = manager.info.current_login()
            if not fresh or fresh.get('accountKey') != row.get('accountKey'):
                raise ValueError('账号身份已变化，请刷新列表')
            details = fresh['details']
        else:
            details = manager.info.public_details(native_id)
        shown = _fields(row, ('name', 'kind', 'email', 'planType', 'model'))
        shown.update(id='cloud-current', transient=bool(row.get('transient')), details={})
        for key in ('usage','models'):
            detail = _detail(details, key)
            if detail is not None:
                shown['details'][key] = detail
        if current.get('id'):
            info['id'] = public['activeId'] = shown['id']
        if row['kind'] == 'chatgpt':
            public['loginAccount'] = shown
        else:
            public['accounts'] = [shown]
        return public
