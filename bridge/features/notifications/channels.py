"""Opt-in phone notifications. Watching a chat never changes its execution owner."""
from bridge.features.notifications.providers import PROVIDERS, desktop_provider, desktop_client

import hashlib
import base64
import hmac
from bridge.api.validation import FieldError, at_field
import json
import logging
import os
import re
import threading
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit, urlunsplit, parse_qsl, urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler, HTTPSHandler, ProxyHandler

from bridge.features.sessions.model import pending_requests, ordered_turns
from bridge.features.auth.tls import client_context
from bridge.features.auth.auth import session_file_lock

DEFAULTS = {'enabled': False, 'server': 'https://ntfy.sh', 'topic': '', 'token': '',
            'barkEnabled': False, 'barkServer': 'https://api.day.app', 'barkKey': '',
            'pushplusEnabled': False, 'pushplusToken': '',
            'webhookEnabled': False, 'webhookUrl': '', 'webhookHeaders': {},
            'webhookProvider': 'auto', 'webhookSignEnabled': False, 'webhookSignSecret': '',
            'webhookBodyTemplate': '{\n  "event": "{{event}}",\n  "title": "{{title}}",\n  "body": "{{body}}",\n  "session_name": "{{session_name}}",\n  "url": "{{url}}"\n}',
            'clickBase': '', 'includeTitle': True, 'addressEnabled': True, 'addressName': '', 'securityEnabled': True, 'mobileEnabled': True, 'mobileAppLinks': False}

WEBHOOK_FIELDS = frozenset(('event', 'event_id', 'title', 'body', 'session_name', 'url', 'client', 'host', 'count'))
WEBHOOK_TEMPLATES = {
    'generic': DEFAULTS['webhookBodyTemplate'],
    'feishu': json.dumps({'msg_type': 'text', 'content': {'text': '{{title}}\n{{body}}\n{{url}}'}}, ensure_ascii=False, indent=2),
    'dingtalk': json.dumps({'msgtype': 'text', 'text': {'content': '{{title}}\n{{body}}\n{{url}}'}}, ensure_ascii=False, indent=2),
    'discord': json.dumps({'content': '{{title}}\n{{body}}\n{{url}}', 'allowed_mentions': {'parse': []}}, ensure_ascii=False, indent=2),
    'telegram': json.dumps({'chat_id': '', 'text': '{{title}}\n{{body}}\n{{url}}'}, ensure_ascii=False, indent=2),
}
WEBHOOK_PLACEHOLDER = re.compile(r'{{\s*([^{}]+?)\s*}}')
UNSAFE_WEBHOOK_HEADERS = frozenset(('host', 'content-length', 'content-type', 'transfer-encoding', 'connection', 'proxy-authorization', 'proxy-authenticate', 'cookie', 'set-cookie', 'upgrade', 'te', 'trailer', 'keep-alive', 'expect'))
WEBHOOK_CONFIG_FIELDS = ('webhookEnabled', 'webhookUrl', 'webhookHeaders', 'webhookProvider',
                         'webhookSignEnabled', 'webhookSignSecret', 'webhookBodyTemplate')
WEBHOOK_CLEAR_FIELDS = ('clearWebhookUrl', 'clearWebhookHeaders', 'clearWebhookSignSecret')
SETTINGS_LOCK = threading.RLock()


class WebhookDeliveryError(RuntimeError):
    """A fixed, safe message that can be shown by a notification test endpoint."""


WEBHOOK_PROVIDER_NAMES = {'feishu': '飞书', 'dingtalk': '钉钉', 'discord': 'Discord', 'telegram': 'Telegram'}


def resolved_webhook_provider(config):
    provider = config.get('webhookProvider', 'auto')
    if provider != 'auto':
        return provider
    parsed = urlsplit(config.get('webhookUrl') or '')
    host, path = parsed.hostname, parsed.path
    if host == 'open.feishu.cn' and path.startswith('/open-apis/bot/v2/hook/'):
        return 'feishu'
    if host == 'oapi.dingtalk.com' and path == '/robot/send':
        return 'dingtalk'
    if host in ('discord.com', 'discordapp.com') and path.startswith('/api/webhooks/'):
        return 'discord'
    if host == 'api.telegram.org' and re.fullmatch(r'/bot[^/]+/sendMessage', path):
        return 'telegram'
    return 'generic'


def effective_webhook_template(config):
    template = config.get('webhookBodyTemplate', DEFAULTS['webhookBodyTemplate'])
    return WEBHOOK_TEMPLATES[resolved_webhook_provider(config)] if template == DEFAULTS['webhookBodyTemplate'] else template


def webhook_url(value):
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 33 or ord(c) == 127 for c in value):
        raise FieldError('Webhook 地址格式不正确', 'webhook-url')
    try:
        parsed = urlsplit(value)
    except ValueError:
        raise FieldError('Webhook 地址格式不正确', 'webhook-url') from None
    try:
        port = parsed.port
    except ValueError:
        raise FieldError('Webhook 地址格式不正确', 'webhook-url') from None
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.fragment or not parsed.netloc or port == 0):
        raise FieldError('Webhook 地址格式不正确', 'webhook-url')
    if parsed.scheme == 'http' and parsed.hostname not in ('localhost', '127.0.0.1', '::1'):
        raise FieldError('Webhook 请使用 HTTPS；HTTP 仅用于本机测试', 'webhook-url')
    return value


def webhook_headers(value):
    if not isinstance(value, dict) or len(value) > 32:
        raise FieldError('Webhook 请求头格式不正确', 'webhook-headers')
    result = {}
    for name, content in value.items():
        if (not isinstance(name, str) or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,80}", name)
                or name.lower() in UNSAFE_WEBHOOK_HEADERS or name.lower().startswith(('proxy-', 'sec-'))
                or not isinstance(content, str) or len(content) > 2000
                or any(ord(c) < 32 or ord(c) == 127 for c in content)):
            raise FieldError('Webhook 请求头格式不正确', 'webhook-headers')
        result[name] = content
    return result


def webhook_template(value):
    if not isinstance(value, str):
        raise FieldError('Webhook JSON 模板格式不正确', 'webhook-body-template')
    try:
        size = len(value.encode('utf-8'))
    except UnicodeEncodeError:
        raise FieldError('Webhook JSON 模板格式不正确', 'webhook-body-template') from None
    if size > 16384:
        raise FieldError('Webhook JSON 模板格式不正确', 'webhook-body-template')
    def reject_constant(_):
        raise ValueError('Non-finite JSON number')
    try:
        document = json.loads(value, parse_constant=reject_constant)
    except (ValueError, TypeError, RecursionError):
        raise FieldError('Webhook JSON 模板格式不正确', 'webhook-body-template') from None
    def check(item, depth=0):
        if depth > 32:
            raise FieldError('Webhook JSON 模板层级过深', 'webhook-body-template')
        if isinstance(item, dict):
            for key, child in item.items():
                try:
                    key.encode('utf-8')
                except UnicodeEncodeError:
                    raise FieldError('Webhook JSON 模板格式不正确', 'webhook-body-template') from None
                if '{{' in key or '}}' in key:
                    raise FieldError('Webhook 占位符只能用于字符串值', 'webhook-body-template')
                check(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                check(child, depth + 1)
        elif isinstance(item, str):
            try:
                item.encode('utf-8')
            except UnicodeEncodeError:
                raise FieldError('Webhook JSON 模板格式不正确', 'webhook-body-template') from None
            for match in WEBHOOK_PLACEHOLDER.finditer(item):
                if match.group(1) not in WEBHOOK_FIELDS:
                    raise FieldError('Webhook 模板包含未知占位符', 'webhook-body-template')
            if '{{' in WEBHOOK_PLACEHOLDER.sub('', item) or '}}' in WEBHOOK_PLACEHOLDER.sub('', item):
                raise FieldError('Webhook 模板占位符格式不正确', 'webhook-body-template')
    check(document)
    return value


def visible_webhook(config):
    url = config.get('webhookUrl', '')
    parsed = urlsplit(url) if url else None
    origin = (parsed.scheme + '://' + parsed.netloc) if parsed else ''
    visible = {'webhookEnabled': config['webhookEnabled'], 'webhookUrl': '', 'hasWebhookUrl': bool(url),
            'webhookUrlDisplay': origin, 'webhookHeaders': {}, 'hasWebhookHeaders': bool(config.get('webhookHeaders')),
            'webhookProvider': config.get('webhookProvider', 'auto'),
            'webhookResolvedProvider': resolved_webhook_provider(config),
            'webhookSignEnabled': config.get('webhookSignEnabled', False),
            'webhookSignSecret': '', 'hasWebhookSignSecret': bool(config.get('webhookSignSecret')),
            'webhookTemplates': WEBHOOK_TEMPLATES,
            'webhookBodyTemplate': effective_webhook_template(config)}
    if 'webhookEndpoints' in config:
        rows = []
        if config.get('webhookUrl') or config.get('webhookEnabled'):
            rows.append({'webhookId': 'legacy', 'webhookName': config.get('webhookName', '默认 Webhook'), **visible})
        rows.extend({'webhookId': row['webhookId'], 'webhookName': row['webhookName'], **visible_webhook(row)}
                    for row in config['webhookEndpoints'])
        visible['webhookEndpoints'] = rows
    return visible


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return default


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        temporary.chmod(0o600)
        deadline = time.monotonic() + 2 if os.name == 'nt' else 0
        while True:
            try:
                temporary.replace(path)
                break
            except PermissionError as exc:
                # Windows readers briefly block replacement; never fall back to truncation.
                if os.name != 'nt' or getattr(exc, 'winerror', None) not in (5, 32, 33):
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise
                time.sleep(min(.05, remaining))
    finally:
        temporary.unlink(missing_ok=True)


def valid_url(value, allow_path=False):
    parsed = urlsplit(value)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or (not allow_path and parsed.path not in ('', '/'))):
        raise ValueError('请输入完整的 HTTP/HTTPS 地址，不含账号、查询参数或片段')
    if parsed.scheme == 'http' and parsed.hostname not in ('localhost', '127.0.0.1', '::1'):
        raise ValueError('推送服务请使用 HTTPS；HTTP 仅用于本机测试')
    return value.rstrip('/')


def settings(data_dir):
    return {'webhookEndpoints': [], 'webhookName': '默认 Webhook', **DEFAULTS,
            **read_json(Path(data_dir)/'notifications.json', {})}


def merge_webhook_config(prior, value):
    result = {key: value.get(key, prior.get(key, DEFAULTS[key])) for key in WEBHOOK_CONFIG_FIELDS}
    if any(not isinstance(result[key], bool) for key in ('webhookEnabled', 'webhookSignEnabled')):
        raise ValueError('通知开关格式不正确')
    if (prior['webhookBodyTemplate'] == DEFAULTS['webhookBodyTemplate']
            and value.get('webhookBodyTemplate') == effective_webhook_template(prior)):
        result['webhookBodyTemplate'] = DEFAULTS['webhookBodyTemplate']
    for flag in ('clearWebhookUrl', 'clearWebhookHeaders', 'clearWebhookSignSecret'):
        if flag in value and not isinstance(value[flag], bool):
            raise ValueError('Webhook 配置格式不正确')
    if 'webhookUrl' in value and not isinstance(value['webhookUrl'], str):
        raise FieldError('Webhook 地址格式不正确', 'webhook-url')
    if 'webhookHeaders' in value and not isinstance(value['webhookHeaders'], dict):
        raise FieldError('Webhook 请求头格式不正确', 'webhook-headers')
    if value.get('clearWebhookUrl'):
        result['webhookUrl'] = ''
    elif value.get('webhookUrl'):
        result['webhookUrl'] = webhook_url(value['webhookUrl'])
    else:
        result['webhookUrl'] = prior['webhookUrl']
    url_changed = result['webhookUrl'] != prior['webhookUrl']
    if result['webhookProvider'] not in ('auto', *WEBHOOK_TEMPLATES):
        raise FieldError('Webhook 类型格式不正确', 'webhook-provider')
    provider_changed = result['webhookProvider'] != prior['webhookProvider']
    secret = result['webhookSignSecret']
    if not isinstance(secret, str) or len(secret) > 2000 or any(ord(c) < 33 or ord(c) == 127 for c in secret):
        raise FieldError('Webhook 签名密钥格式不正确', 'webhook-sign-secret')
    if not value.get('webhookSignSecret'):
        result['webhookSignSecret'] = '' if value.get('clearWebhookSignSecret') or url_changed or provider_changed else prior['webhookSignSecret']
    if result['webhookSignEnabled']:
        if resolved_webhook_provider(result) not in ('feishu', 'dingtalk'):
            raise FieldError('签名仅支持飞书和钉钉 Webhook', 'webhook-sign-enabled')
        if not result['webhookSignSecret']:
            raise FieldError('开启签名前请填写密钥；更换地址或类型后需重新填写', 'webhook-sign-secret')
    if value.get('clearWebhookHeaders'):
        result['webhookHeaders'] = {}
    elif value.get('webhookHeaders'):
        result['webhookHeaders'] = webhook_headers(value['webhookHeaders'])
    else:
        result['webhookHeaders'] = {} if url_changed else prior['webhookHeaders']
    result['webhookHeaders'] = webhook_headers(result['webhookHeaders'])
    result['webhookBodyTemplate'] = webhook_template(result['webhookBodyTemplate'])
    if result['webhookSignEnabled'] and resolved_webhook_provider(result) == 'feishu':
        signed_template = json.loads(effective_webhook_template(result))
        if not isinstance(signed_template, dict) or any(key in signed_template for key in ('timestamp', 'sign')):
            raise FieldError('签名消息不能自行设置 timestamp 或 sign', 'webhook-body-template')
    if result['webhookEnabled'] and not result['webhookUrl']:
        raise FieldError('开启 Webhook 前请填写地址', 'webhook-url')
    return result


def save_settings(data_dir, value):
    with SETTINGS_LOCK:
        path = Path(data_dir)/'notifications.json'
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with session_file_lock(path):
            return _save_settings_unlocked(data_dir, value)


def _save_settings_unlocked(data_dir, value):
    prior = settings(data_dir)
    result = {**prior, **{k: value[k] for k in DEFAULTS if k in value}}
    result['server'] = at_field('ntfy-server', valid_url, str(result['server']), allow_path=True)
    result['barkServer'] = at_field('bark-server', valid_url, str(result['barkServer']), allow_path=True)
    for key in ('enabled', 'barkEnabled', 'pushplusEnabled', 'webhookEnabled', 'webhookSignEnabled', 'includeTitle', 'addressEnabled', 'securityEnabled', 'mobileEnabled', 'mobileAppLinks'):
        if not isinstance(result[key], bool):
            raise ValueError('通知开关格式不正确')
    if not isinstance(result['addressName'], str) or len(result['addressName']) > 80 or any(not c.isprintable() for c in result['addressName']):
        raise FieldError('网关名称应为不超过 80 字的单行文本', 'address-name')
    if not isinstance(result['topic'], str) or (result['topic'] and not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', result['topic'])):
        raise FieldError('ntfy 主题只允许 1–64 位字母、数字、下划线和短横线', 'ntfy-topic')
    if result['enabled'] and not result['topic']:
        raise FieldError('开启通知前请填写 ntfy 主题', 'ntfy-topic')
    if result['clickBase']:
        # A link back to the existing LAN gateway may intentionally use HTTP.
        parsed = urlsplit(result['clickBase'])
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise FieldError('通知跳转地址格式不正确', 'click-base')
        result['clickBase'] = result['clickBase'].rstrip('/')
    if not isinstance(result['token'], str) or len(result['token']) > 2000 or '\n' in result['token'] or '\r' in result['token']:
        raise FieldError('ntfy Token 格式不正确', 'ntfy-token')
    # Blank fields from the UI preserve a token only for the same server.
    if not value.get('token'):
        result['token'] = '' if value.get('clearToken') or result['server'] != prior['server'] else prior['token']
    if not isinstance(result['barkKey'], str) or len(result['barkKey']) > 2000 or any(not c.isprintable() or c.isspace() or c in '/?#' for c in result['barkKey']):
        raise FieldError('Bark Device Key 格式不正确，请仅填写密钥，不要粘贴完整推送地址', 'bark-key')
    if not value.get('barkKey'):
        result['barkKey'] = '' if value.get('clearBarkKey') or result['barkServer'] != prior['barkServer'] else prior['barkKey']
    if result['barkEnabled'] and not result['barkKey']:
        raise FieldError('开启 Bark 前请填写 Device Key；更换服务地址后需重新填写', 'bark-key')
    if 'clearPushplusToken' in value and not isinstance(value['clearPushplusToken'], bool):
        raise ValueError('PushPlus 配置格式不正确')
    token = result['pushplusToken']
    if not isinstance(token, str) or len(token) > 2000 or any(not c.isprintable() or c.isspace() for c in token):
        raise FieldError('PushPlus Token 格式不正确', 'pushplus-token')
    if not value.get('pushplusToken'):
        result['pushplusToken'] = '' if value.get('clearPushplusToken') else prior['pushplusToken']
    if result['pushplusEnabled'] and not result['pushplusToken']:
        raise FieldError('开启 PushPlus 前请填写 Token', 'pushplus-token')
    result.update(merge_webhook_config(prior, value))
    write_json(Path(data_dir)/'notifications.json', result)
    return result


def webhook_config(config, webhook_id='legacy'):
    if webhook_id == 'legacy':
        return config
    if not isinstance(webhook_id, str):
        raise FieldError('Webhook 标识无效', 'webhook-id')
    for row in config.get('webhookEndpoints', []):
        if row.get('webhookId') == webhook_id:
            return row
    raise FieldError('Webhook 不存在，请刷新列表', 'webhook-id')


def webhook_channel_config(config, channel):
    return webhook_config(config, channel.split(':', 1)[1] if channel.startswith('webhook:') else 'legacy')


def save_webhook_endpoint(data_dir, value):
    if not isinstance(value, dict) or value.get('action') not in ('upsert', 'delete'):
        raise ValueError('Webhook 配置格式不正确')
    allowed = {'action', 'webhookId', 'webhookName', *WEBHOOK_CONFIG_FIELDS, *WEBHOOK_CLEAR_FIELDS}
    if set(value) - allowed:
        raise ValueError('Webhook 配置格式不正确')
    with SETTINGS_LOCK:
        path = Path(data_dir)/'notifications.json'
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with session_file_lock(path):
            return _save_webhook_endpoint_unlocked(data_dir, value)


def _save_webhook_endpoint_unlocked(data_dir, value):
    config = settings(data_dir)
    rows = list(config['webhookEndpoints'])
    webhook_id = value.get('webhookId')
    if value['action'] == 'delete':
        if set(value) != {'action', 'webhookId'}:
            raise ValueError('Webhook 删除请求格式不正确')
        webhook_config(config, webhook_id)
        if webhook_id == 'legacy':
            config.update({key: DEFAULTS[key] for key in WEBHOOK_CONFIG_FIELDS})
            config['webhookName'] = '默认 Webhook'
        else:
            config['webhookEndpoints'] = [row for row in rows if row['webhookId'] != webhook_id]
    else:
        creating = webhook_id is None
        if creating:
            if len(rows) >= 20:
                raise ValueError('最多配置 20 个额外 Webhook')
            webhook_id = uuid.uuid4().hex
            prior = {key: DEFAULTS[key] for key in WEBHOOK_CONFIG_FIELDS}
        else:
            prior = webhook_config(config, webhook_id)
        name = value.get('webhookName', prior.get('webhookName', '默认 Webhook' if webhook_id == 'legacy' else ''))
        if not isinstance(name, str) or not name.strip() or len(name) > 80 or any(not c.isprintable() for c in name):
            raise FieldError('Webhook 名称应为不超过 80 字的单行文本', 'webhook-name')
        updated = {**merge_webhook_config(prior, value), 'webhookId': webhook_id, 'webhookName': name.strip()}
        if webhook_id == 'legacy':
            config.update({key: updated[key] for key in WEBHOOK_CONFIG_FIELDS})
            config['webhookName'] = updated['webhookName']
        elif creating:
            config['webhookEndpoints'] = rows + [updated]
        else:
            config['webhookEndpoints'] = [updated if row['webhookId'] == webhook_id else row for row in rows]
    write_json(Path(data_dir)/'notifications.json', config)
    selected = webhook_id if value['action'] == 'upsert' else (
        'legacy' if config.get('webhookUrl') or config.get('webhookEnabled') else
        config['webhookEndpoints'][0]['webhookId'] if config['webhookEndpoints'] else '')
    return config, selected


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward notification credentials on redirects.


def publish(config, title, body, click=''):
    if not config.get('topic'):
        raise ValueError('请先配置 ntfy 服务和主题')
    server = valid_url(config['server'], allow_path=True)
    payload = {'topic': config['topic'], 'title': title, 'message': body, 'tags': ['bell'], 'priority': 3}
    if click:
        payload['click'] = click
    headers = {'Content-Type': 'application/json'}
    if config.get('token'):
        headers['Authorization'] = 'Bearer ' + config['token']
    request = Request(server + '/', data=json.dumps(payload, ensure_ascii=False).encode(), headers=headers)
    with build_opener(NoRedirect(), HTTPSHandler(context=client_context())).open(request, timeout=8) as response:
        if not 200 <= response.status < 300:
            raise RuntimeError('ntfy 未接受通知')
        response.read(65536)


def publish_bark(config, title, body, click=''):
    if not config.get('barkKey'):
        raise ValueError('请先配置 Bark 服务和 Device Key')
    server = valid_url(config['barkServer'], allow_path=True)
    payload = {'device_key': config['barkKey'], 'title': title, 'body': body, 'group': 'Codex Mobile Bridge'}
    if click:
        payload['url'] = click
    request = Request(server + '/push', data=json.dumps(payload, ensure_ascii=False).encode(),
                      headers={'Content-Type': 'application/json'})
    handlers = [NoRedirect(), HTTPSHandler(context=client_context())]
    # A local Bark gateway must not receive a private device key through a system proxy.
    if urlsplit(server).hostname in ('localhost', '127.0.0.1', '::1'):
        handlers.insert(0, ProxyHandler({}))
    try:
        with build_opener(*handlers).open(request, timeout=8) as response:
            result = json.loads(response.read(65536))
            if not 200 <= response.status < 300 or not isinstance(result, dict) or result.get('code') != 200:
                raise ValueError('Rejected')
    except Exception as error:
        # The server may echo the device key in its error response. Do not expose it.
        if isinstance(error, HTTPError):
            error.close()
        raise RuntimeError('Bark 未接受通知，请检查服务地址、Device Key 和网络') from None


def publish_pushplus(config, title, body, click=''):
    if not config.get('pushplusToken'):
        raise ValueError('请先配置 PushPlus Token')
    payload = {'token': config['pushplusToken'], 'title': title,
               'content': body + ('\n\n' + click if click else ''), 'template': 'txt'}
    request = Request('https://www.pushplus.plus/send',
                      data=json.dumps(payload, ensure_ascii=False).encode(),
                      headers={'Content-Type': 'application/json'})
    try:
        with build_opener(NoRedirect(), HTTPSHandler(context=client_context())).open(request, timeout=8) as response:
            result = json.loads(response.read(65536))
            if not 200 <= response.status < 300 or not isinstance(result, dict) or result.get('code') != 200:
                raise ValueError('Rejected')
    except Exception as error:
        if isinstance(error, HTTPError):
            error.close()
        raise RuntimeError('PushPlus 未接受通知，请检查 Token 和网络') from None


def publish_webhook(config, title, body, click='', *, event='test', event_id='', session_name='', client='', host='', count=1):
    url = webhook_url(config.get('webhookUrl', ''))
    provider = resolved_webhook_provider(config)
    values = {'event': event, 'event_id': event_id or uuid.uuid4().hex, 'title': title, 'body': body,
              'session_name': session_name, 'url': click, 'client': client, 'host': host, 'count': str(count)}
    template = json.loads(webhook_template(effective_webhook_template(config)))
    def render(item):
        if isinstance(item, dict):
            return {key: render(child) for key, child in item.items()}
        if isinstance(item, list):
            return [render(child) for child in item]
        if isinstance(item, str):
            return WEBHOOK_PLACEHOLDER.sub(lambda match: values[match.group(1)], item)
        return item
    document = render(template)
    if provider == 'telegram':
        if (not isinstance(document, dict) or type(document.get('chat_id')) not in (str, int)
                or not str(document['chat_id']).strip() or not isinstance(document.get('text'), str)):
            raise WebhookDeliveryError('Telegram 请在 JSON 模板中填写 chat_id 和 text')
        if len(document['text']) > 4096:
            raise WebhookDeliveryError('Telegram 消息超过 4096 字')
    if provider == 'discord':
        if not isinstance(document, dict) or ('content' in document and not isinstance(document['content'], str)):
            raise WebhookDeliveryError('Discord JSON 模板格式不正确')
        if len(document.get('content', '')) > 2000:
            raise WebhookDeliveryError('Discord 消息超过 2000 字')
    if config.get('webhookSignEnabled'):
        if provider not in ('feishu', 'dingtalk') or not config.get('webhookSignSecret'):
            raise WebhookDeliveryError('Webhook 签名配置不完整，请检查类型和密钥')
        if provider == 'feishu':
            if not isinstance(document, dict) or any(key in document for key in ('timestamp', 'sign')):
                raise WebhookDeliveryError('签名消息不能自行设置 timestamp 或 sign')
            timestamp = str(int(time.time()))
            key = (timestamp + '\n' + config['webhookSignSecret']).encode('utf-8')
            document['timestamp'] = timestamp
            document['sign'] = base64.b64encode(hmac.new(key, b'', hashlib.sha256).digest()).decode('ascii')
        else:
            timestamp = str(int(time.time() * 1000))
            message = (timestamp + '\n' + config['webhookSignSecret']).encode('utf-8')
            sign = base64.b64encode(hmac.new(config['webhookSignSecret'].encode('utf-8'), message, hashlib.sha256).digest()).decode('ascii')
            parsed = urlsplit(url)
            query = [(key, val) for key, val in parse_qsl(parsed.query, keep_blank_values=True) if key not in ('timestamp', 'sign')]
            url = urlunsplit(parsed._replace(query=urlencode(query + [('timestamp', timestamp), ('sign', sign)])))
    if provider == 'discord':
        parsed = urlsplit(url)
        query = [(key, val) for key, val in parse_qsl(parsed.query, keep_blank_values=True) if key != 'wait']
        url = urlunsplit(parsed._replace(query=urlencode(query + [('wait', 'true')])))
    payload = json.dumps(document, ensure_ascii=False).encode('utf-8')
    if len(payload) > (20 * 1024 if provider == 'feishu' else 65536):
        raise WebhookDeliveryError('飞书消息过长' if provider == 'feishu' else 'Webhook 消息过长')
    headers = {'Content-Type': 'application/json', **webhook_headers(config.get('webhookHeaders', {}))}
    request = Request(url, data=payload, headers=headers, method='POST')
    handlers = [NoRedirect(), HTTPSHandler(context=client_context())]
    if urlsplit(url).hostname in ('localhost', '127.0.0.1', '::1'):
        handlers.insert(0, ProxyHandler({}))
    try:
        with build_opener(*handlers).open(request, timeout=8) as response:
            if provider != 'generic':
                if (response.status != 200 if provider in ('feishu', 'dingtalk', 'telegram') else not 200 <= response.status < 300):
                    raise WebhookDeliveryError(f'{WEBHOOK_PROVIDER_NAMES[provider]}未接受通知（HTTP {response.status}）')
                try:
                    raw = response.read(65537)
                    if len(raw) > 65536:
                        raise WebhookDeliveryError(f'{WEBHOOK_PROVIDER_NAMES[provider]}响应格式不正确')
                    result = json.loads(raw)
                except (ValueError, UnicodeDecodeError, RecursionError):
                    raise WebhookDeliveryError(f'{WEBHOOK_PROVIDER_NAMES[provider]}响应格式不正确') from None
                if not isinstance(result, dict):
                    raise WebhookDeliveryError(f'{WEBHOOK_PROVIDER_NAMES[provider]}响应格式不正确')
                if provider == 'feishu':
                    code = result.get('code', result.get('StatusCode'))
                    if type(code) is not int:
                        raise WebhookDeliveryError('飞书响应格式不正确')
                    if code:
                        reasons = {9499: '消息格式错误', 19002: '消息格式错误', 19021: '签名或时间无效', 19022: 'IP 不在允许范围',
                                   19024: '未满足关键词要求', 11232: '发送过于频繁'}
                        raise WebhookDeliveryError(f'飞书未接受通知（{code}：{reasons.get(code, "请检查机器人配置")}）')
                elif provider == 'dingtalk':
                    code = result.get('errcode')
                    if type(code) is not int:
                        raise WebhookDeliveryError('钉钉响应格式不正确')
                    if code:
                        reasons = {310000: '请检查关键词、签名、时间或 IP 限制', 40035: '消息格式错误',
                                   400101: '访问令牌无效', 400102: '机器人已停用', 400105: '消息类型不支持',
                                   400106: '机器人不存在', 410100: '发送过于频繁', -1: '服务暂时不可用'}
                        raise WebhookDeliveryError(f'钉钉未接受通知（{code}：{reasons.get(code, "请检查机器人配置")}）')
                elif provider == 'telegram':
                    if result.get('ok') is not True:
                        raise WebhookDeliveryError('Telegram 未接受通知，请检查 chat_id 和机器人权限')
                elif provider == 'discord':
                    if not isinstance(result.get('id'), str) or not result['id']:
                        raise WebhookDeliveryError('Discord 响应缺少消息 ID')
            else:
                if not 200 <= response.status < 300:
                    raise ValueError('Rejected')
                response.read(65536)
    except WebhookDeliveryError:
        raise
    except Exception as error:
        if isinstance(error, HTTPError):
            error.close()
            if provider != 'generic':
                raise WebhookDeliveryError(f'{WEBHOOK_PROVIDER_NAMES[provider]}未接受通知（HTTP {error.code}）') from None
        raise WebhookDeliveryError(f'{WEBHOOK_PROVIDER_NAMES[provider]}未接受通知，请检查地址和网络' if provider != 'generic'
                                   else 'Webhook 未接受通知，请检查地址、认证和网络') from None


def destination(config, channel):
    if channel.startswith('webhook:'):
        webhook_id = channel.split(':', 1)[1]
        target = destination(webhook_config(config, webhook_id), 'webhook')
        return channel + ':' + hashlib.sha256((webhook_id + ':' + target).encode()).hexdigest()
    if channel == 'ntfy':
        values = [config['server'].rstrip('/'), config['topic']]
    elif channel == 'bark':
        values = [config['barkServer'].rstrip('/'), config['barkKey']]
    elif channel == 'pushplus':
        values = [config.get('pushplusToken', '')]
    elif channel == 'webhook':
        values = [config.get('webhookUrl', ''), sorted(config.get('webhookHeaders', {}).items()),
                  config.get('webhookProvider', 'auto'), resolved_webhook_provider(config),
                  config.get('webhookSignEnabled', False), config.get('webhookSignSecret', '')]
    else:
        raise ValueError('未知通知通道')
    return channel + ':' + hashlib.sha256(json.dumps(values).encode()).hexdigest()


def channels(config):
    result = {name: destination(config, name) for name, enabled in
            (('ntfy', config['enabled']), ('bark', config['barkEnabled']), ('pushplus', config.get('pushplusEnabled', False)),
             ('webhook', config.get('webhookEnabled', False))) if enabled}
    for row in config.get('webhookEndpoints', []):
        if row['webhookEnabled']:
            name = 'webhook:' + row['webhookId']
            result[name] = destination(config, name)
    return result


class Notifications:
    def __init__(self, bridge, data_dir, origins=lambda: [], public_url=lambda: '', *, desktop_sessions=None):
        self.bridge, self.data_dir, self.origins = bridge, Path(data_dir), origins
        self.public_url = public_url
        self.desktop_sessions = desktop_sessions
        self.desktop_poll_at = 0
        self.desktop_details = {}
        self.desktop_cursor = 0
        self.lock = threading.RLock()
        self.delivery_lock = threading.Lock()
        self.delivery_wakeup = threading.Event()
        self.delivery_queue = {}
        self.delivery_worker = None
        self.closed = threading.Event()
        self.ledger = read_json(self.data_dir/'notification-delivery.json', {})
        self.completions = read_json(self.data_dir/'notification-completions.json', {})
        self.attention = read_json(self.data_dir/'notification-attention.json', {})
        # Bind legacy ntfy-only records to their existing destination once. A new
        # recipient must not inherit another recipient's delivery or retry state.
        config = settings(self.data_dir)
        if config['topic']:
            target = destination(config, 'ntfy')
            migrated = {target + ':' + k if re.fullmatch(r'[a-f0-9]{64}', k) else k: v for k, v in self.ledger.items()}
            if migrated != self.ledger:
                self.ledger = migrated
                write_json(self.data_dir/'notification-delivery.json', migrated)
            migrated = {json.dumps(json.loads(k) + [target]) if len(json.loads(k)) == 2 else k: v for k, v in self.completions.items()}
            if migrated != self.completions:
                self.completions = migrated
                write_json(self.data_dir/'notification-completions.json', migrated)
        self.candidates = {}
        self.discovery_at = 0
        self.attached = {}
        self.dormant = set()
        self.target_signature = None
        self.wakeup = getattr(bridge, 'notification_event', threading.Event())
        self.worker = None
        from bridge.features.notifications.security import SecurityNotifications
        self.security = SecurityNotifications(self.data_dir)
        from bridge.features.notifications.events import MobileEvents
        self.mobile = MobileEvents(self.data_dir)
        from bridge.features.notifications.push import MobilePush
        self.mobile_push = MobilePush(self.data_dir, self.mobile)

    def _targets(self, config):
        result = {}
        if config.get('mobileEnabled'):
            result['mobile'] = 'mobile:' + self.mobile.value['streamId']
        result.update(channels(config))
        return result

    def mobile_snapshot(self, after=0, *, wait=0, stream=None, revision=None):
        started = time.monotonic()
        deadline = started + wait
        while True:
            version = self.mobile.version()
            snapshot = self._mobile_snapshot(after)
            # A content token also detects preferences saved by the desktop process.
            state = {key: value for key, value in snapshot.items() if key != 'events'}
            token = hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()[:32]
            remaining = deadline - time.monotonic()
            if (remaining <= 0 or self.closed.is_set() or not snapshot['enabled']
                    or stream != snapshot['streamId'] or after != snapshot['cursor'] or revision != token):
                elapsed = round((time.monotonic() - started) * 1000, 1)
                now = time.time()
                events = [{**event, 'ageMs': max(0, round((now - event['createdAt']) * 1000))}
                          for event in snapshot['events']]
                if events:
                    logging.debug('notification stage=read events=%d cursor=%d wait_ms=%.1f', len(events), snapshot['cursor'], elapsed)
                # Timing is response-local, outside the stable revision. Phone
                # and gateway wall clocks are never subtracted from one another.
                return {**snapshot, 'events': events, 'longPoll': 60, 'revision': token, 'timing': {'waitMs': elapsed}}
            # Desktop preferences live in a separate process; retain their existing refresh bound.
            self.mobile.wait(version, min(15, remaining))

    def _mobile_snapshot(self, after):
        """One passive snapshot for all phones; reads never attach a desktop chat."""
        enabled = settings(self.data_dir).get('mobileEnabled', False)
        policies, legacy = self.policies(), self.watches()
        cached = getattr(self.desktop_sessions, 'client_cache', None)
        states = {row['id']: row for row in cached[1].get('clients', [])} if isinstance(cached, tuple) and len(cached) == 2 else {}
        clients = [{'id': provider, 'connected': states.get(provider, {}).get('connected'), 'enabled': self.desktop_sessions.enabled(provider)
                    if self.desktop_sessions is not None else provider == 'codex'}
                   for provider in dict.fromkeys((*self.mobile.PROVIDERS, *[identifier for identifier in states if desktop_client('desktop:' + identifier)]))]
        active = {row['id'] for row in clients if row['enabled']}
        effective = {}
        def allowed(event):
            if not enabled or self.mobile.client(event) not in active:
                return False
            key = (event['host'], event['threadId'])
            if key not in effective:
                effective[key] = self._effective({'id': event['threadId'], 'host': event['host']}, policies, legacy)
            policy = effective[key]
            return policy['notifyOnCompletion' if event['kind'] == 'completion' else 'notifyOnRequest']
        snapshot = self.mobile.read(after, allowed)
        return {**snapshot, 'enabled': enabled, 'clients': [
            {**row, 'unread': snapshot['unread'][row['id']]} for row in clients]}

    def start(self):
        self.delivery_worker = threading.Thread(target=self._delivery_run, daemon=True)
        self.delivery_worker.start()
        self.worker = threading.Thread(target=self._run, daemon=True)
        self.worker.start()
        self.push_worker = threading.Thread(target=self._push_run, daemon=True)
        self.push_worker.start()

    def close(self):
        self.closed.set()
        self.mobile.close()
        self.wakeup.set()
        self.delivery_wakeup.set()
        if self.worker:
            self.worker.join(timeout=2)
        if self.delivery_worker:
            self.delivery_worker.join(timeout=2)
        for session in self.attached.values():
            session.watched = False

    def watches(self):
        return read_json(self.data_dir/'notification-watches.json', [])

    def policies(self):
        return read_json(self.data_dir/'notification-policies.json',
                         {'requests': True, 'completion': True, 'chats': {}})

    def defaults(self, value=None):
        with self.lock:
            policies = self.policies()
            if value is not None:
                if not isinstance(value, dict) or set(value) != {'requests', 'completion'} or any(type(v) is not bool for v in value.values()):
                    raise ValueError('通知开关格式不正确')
                self.candidates.update({key: {'host': key[0], 'id': key[1]} for key in self.dormant})
                self.dormant.clear()
                self.wakeup.set()
                policies.update(value)
                write_json(self.data_dir/'notification-policies.json', policies)
                self.mobile.changed()
            return {key: policies[key] for key in ('requests', 'completion')}

    def _effective(self, row, policies=None, legacy_rows=None):
        policies = policies or self.policies()
        key = row['host'] + '|' + row['id']
        overrides = policies.get('chats', {}).get(key, {})
        # Existing explicit watches keep their old preferences on upgrade.
        legacy = next((r for r in (self.watches() if legacy_rows is None else legacy_rows) if r['host'] == row['host'] and r['id'] == row['id']), None)
        choices = {key: overrides.get(key, ('on' if legacy.get('notifyOnCompletion') else 'off') if key == 'completion' and legacy else 'on' if legacy else 'inherit') for key in ('requests', 'completion')}
        enabled = {key: policies[key] if choice == 'inherit' else choice == 'on' for key, choice in choices.items()}
        return {**row, 'notifyOnRequest': enabled['requests'], 'notifyOnCompletion': enabled['completion'], 'choices': choices}

    @staticmethod
    def desktop_provider(host):
        return desktop_provider(host)

    def _client_enabled(self, host):
        if self.desktop_sessions is None:
            return not self.desktop_provider(host)
        client = desktop_client(host) or (host if host.startswith(('wsl:', 'codex:')) else 'codex')
        return self.desktop_sessions.enabled(client)

    def policy(self, thread_id, host, value=None):
        provider = self.desktop_provider(host)
        if provider:
            if not isinstance(thread_id, str) or not 1 <= len(thread_id) <= 512 or any(ord(c) < 32 or ord(c) == 127 for c in thread_id):
                raise ValueError('会话标识无效')
        else:
            uuid.UUID(thread_id)
        with self.lock:
            policies = self.policies()
            if value is not None:
                if not isinstance(value, dict) or set(value) != {'requests', 'completion'} or any(v not in ('inherit', 'on', 'off') for v in value.values()):
                    raise ValueError('聊天通知设置格式不正确')
                if provider:
                    if self.desktop_sessions is None:
                        raise ValueError('桌面会话接入不可用')
                    manager = self.desktop_sessions.resolve_instance(provider, desktop_client(host)) if ':' in desktop_client(host) else self.desktop_sessions
                    manager.require_enabled(provider)
                    manager.call(provider, 'detail', thread_id)
                elif self.bridge is not None:
                    self.bridge.for_host(host).store.get(thread_id)
                self.dormant.discard((host, thread_id))
                self.wakeup.set()
                policies.setdefault('chats', {})[host + '|' + thread_id] = value
                self._clear_completion(host, thread_id)
                write_json(self.data_dir/'notification-completions.json', self.completions)
                write_json(self.data_dir/'notification-policies.json', policies)
                self.mobile.changed()
            row = self._effective({'id': thread_id, 'host': host}, policies)
            return {'available': bool(self._targets(settings(self.data_dir))), 'watching': row['notifyOnRequest'] or row['notifyOnCompletion'],
                    'notifyOnRequest': row['notifyOnRequest'], 'notifyOnCompletion': row['notifyOnCompletion'],
                    'requests': row['choices']['requests'], 'completion': row['choices']['completion']}

    def _selected(self):
        policies = self.policies()
        legacy_rows = self.watches()
        rows = {(r['host'], r['id']): r for r in legacy_rows}
        if policies['requests'] or policies['completion']:
            rows.update(self.candidates)
        for key in policies.get('chats', {}):
            if self.desktop_provider(key.rsplit('|', 1)[0]):
                continue
            host, identifier = key.rsplit('|', 1)
            rows.setdefault((host, identifier), {'id': identifier, 'host': host})
        effective = [self._effective(row, policies, legacy_rows) for row in rows.values()]
        return [row for row in effective if self._client_enabled(row['host']) and
                (row['notifyOnRequest'] or row['notifyOnCompletion'])]

    def watch(self, thread_id, host, enabled=None, notify_on_completion=None, *, existing_only=False):
        uuid.UUID(thread_id)
        for value in (enabled, notify_on_completion):
            if value is not None and not isinstance(value, bool):
                raise ValueError('提醒开关格式不正确')
        with self.lock:
            config = settings(self.data_dir)
            targets = self._targets(config)
            rows = self.watches()
            prior = next((r for r in rows if r['id'] == thread_id and r['host'] == host), None)
            if existing_only and prior is None:
                raise ValueError('该会话的通知监控已移除，请刷新列表')
            selected = prior is not None if enabled is None else enabled
            completion = bool(prior and prior.get('notifyOnCompletion')) if notify_on_completion is None else notify_on_completion
            if enabled is False:
                completion = False
            if enabled is not None or notify_on_completion is not None:
                self.dormant.discard((host, thread_id))
                self.wakeup.set()
                if selected and not targets and not existing_only:
                    raise ValueError('请先配置并开启 PushPlus、Bark 或 ntfy 通知')
                if not selected and completion:
                    raise ValueError('请先开启此聊天提醒')
                rows = [r for r in rows if not (r['id'] == thread_id and r['host'] == host)]
                if selected:
                    if len(rows) >= 100:
                        raise ValueError('最多关注 100 个聊天')
                    session = self.attached.get((host, thread_id)) if existing_only else None
                    if not existing_only and self.bridge is not None:
                        session = self.bridge.for_host(host).session(thread_id, background=True)
                    row = {**(prior or {}), 'id': thread_id, 'host': host, 'notifyOnCompletion': completion}
                    if session is not None:
                        with session.condition:
                            state = session.state or {}
                            row.update({k: state[k] for k in ('title', 'cwd') if state.get(k)})
                    index = next((i for i, r in enumerate(self.watches()) if r['id'] == thread_id and r['host'] == host), len(rows))
                    rows.insert(index, row)
                if not selected or not completion:
                    self._clear_completion(host, thread_id)
                elif not prior or not prior.get('notifyOnCompletion'):
                    # Establish the boundary at opt-in, including an already running turn.
                    self._clear_completion(host, thread_id)
                    if session is not None:
                        with session.condition:
                            if session.connected:
                                for target in targets.values():
                                    key = self._completion_key(host, thread_id, target)
                                    self.completions[key] = self._completion_baseline(ordered_turns(session.state or {}))
                write_json(self.data_dir/'notification-completions.json', self.completions)
                write_json(self.data_dir/'notification-watches.json', rows)
                policies = self.policies()
                key = host + '|' + thread_id
                if not selected:
                    policies.setdefault('chats', {})[key] = {'requests': 'off', 'completion': 'off'}
                else:
                    policies.setdefault('chats', {}).pop(key, None)
                write_json(self.data_dir/'notification-policies.json', policies)
                self.mobile.changed()
            return {'available': bool(targets), 'watching': selected, 'notifyOnCompletion': selected and completion}

    def control(self, value):
        if not isinstance(value, dict) or not isinstance(value.get('id'), str) or not isinstance(value.get('host'), str):
            raise ValueError('通知监控操作格式不正确')
        action = value.get('action')
        if action == 'remove' and set(value) == {'action', 'id', 'host'}:
            # Removal is also available for a host/chat that is no longer reachable.
            return self.watch(value['id'], value['host'], False, existing_only=True)
        if action == 'update' and set(value) == {'action', 'id', 'host', 'notifyOnCompletion'} and isinstance(value['notifyOnCompletion'], bool):
            return self.watch(value['id'], value['host'], notify_on_completion=value['notifyOnCompletion'], existing_only=True)
        raise ValueError('通知监控操作格式不正确')

    def _clear_completion(self, host, thread_id):
        self.completions = {k: v for k, v in self.completions.items() if json.loads(k)[:2] != [host, thread_id]}
        self.delivery_queue = {key: job for key, job in self.delivery_queue.items()
                               if not (key[:2] == (host, thread_id) and key[-1] is True)}

    @staticmethod
    def _completion_key(host, thread_id, target):
        return json.dumps([host, thread_id, target])

    @staticmethod
    def _completion_baseline(turns):
        return {'anchor': next((t['turnId'] for t in reversed(turns) if t.get('turnId')), None),
                'running': [t['turnId'] for t in turns if t.get('turnId') and t.get('status') == 'inProgress'],
                'pending': []}

    def _completion_pending(self, row, turns, target):
        key = self._completion_key(row['host'], row['id'], target)
        with self.lock:
            if not self._effective(row)['notifyOnCompletion']:
                return []
            previous = self.completions.get(key)
            current = self._completion_baseline(turns)
            # DSH sequence numbers survive latest-page truncation. A missing UUID
            # anchor alone is not evidence that a historical turn is new.
            ordered = self.desktop_provider(row['host']) and all(type(t.get('sequence')) is int for t in turns)
            if ordered:
                current['sequence'] = max([t['sequence'] for t in turns] + [(previous or {}).get('sequence', -1)])
            current['title'] = row.get('title') or (previous or {}).get('title') or '聊天'
            if previous is not None:
                # Only turns after the last live boundary (or observed running) are new.
                # Older pages loaded into a snapshot must never become completion alerts.
                anchor = next((i for i, t in enumerate(turns) if t.get('turnId') == previous['anchor']), None)
                newer = turns if previous['anchor'] is None else turns[anchor + 1:] if anchor is not None else []
                if ordered and type(previous.get('sequence')) is int:
                    newer = [t for t in turns if t['sequence'] > previous['sequence']]
                eligible = set(previous['running']) | {t['turnId'] for t in newer if t.get('turnId')}
                pending = previous['pending'] + [t['turnId'] for t in turns if t.get('turnId') in eligible and t.get('status') == 'completed']
                current['pending'] = list(dict.fromkeys(pending))
                # A reconnect may temporarily supply only an older page or no turns.
                if previous['anchor'] is not None and anchor is None and not any(t.get('turnId') in previous['running'] or t.get('status') == 'inProgress' for t in turns):
                    current['anchor'] = previous['anchor']
                present = {t.get('turnId') for t in turns}
                current['running'] += [turn_id for turn_id in previous['running'] if turn_id not in present]
            current['pending'] = [turn_id for turn_id in current['pending'] if not self.ledger.get(self._delivery_key(row, turn_id, completion=True, target=target), {}).get('delivered')]
            if current != previous:
                self.completions[key] = current
                write_json(self.data_dir/'notification-completions.json', self.completions)
            return [self._delivery_key(row, turn_id, completion=True, target=target) for turn_id in current['pending']]

    @staticmethod
    def _delivery_key(row, event_id, completion=False, target=''):
        identity = [row['host'], row['id'], event_id]
        if completion:
            identity.append('attention' if completion == 'attention' else 'completion')
        return (target + ':' if target else '') + hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()

    def _attention_pending(self, row, turns, target):
        """Only explicit new terminal failures require intervention, never idle time.

        Keep an episode counter so a resumed native turn can fail again without
        replaying its first alert. First reads and older history are baselines.
        """
        key = self._completion_key(row['host'], row['id'], target)
        with self.lock:
            if not self._effective(row)['notifyOnRequest']:
                return []
            turns = [t for t in turns if t.get('turnId')]
            previous = self.attention.get(key)
            if not turns:
                if previous is None:
                    self.attention[key] = {'anchor': None, 'turns': {}, 'pending': ''}
                    write_json(self.data_dir/'notification-attention.json', self.attention)
                return []
            ordered = self.desktop_provider(row['host']) and all(type(t.get('sequence')) is int for t in turns)
            anchor = next((i for i, t in enumerate(turns) if t['turnId'] == (previous or {}).get('anchor')), None)
            newer = turns if previous and previous.get('anchor') is None else turns[anchor + 1:] if anchor is not None else []
            if ordered and previous and type(previous.get('sequence')) is int:
                newer = [t for t in turns if t['sequence'] > previous['sequence']]
            known = (previous or {}).get('turns', {})
            # A truncated/older page is not a recovery or a new failure.
            if previous and anchor is None and not newer and not any(known.get(t['turnId'], {}).get('status') == 'inProgress' for t in turns):
                return []
            eligible = {t['turnId'] for t in newer}
            observed = dict(known)
            pending = ''
            for turn in turns:
                identifier = turn['turnId']
                status = turn.get('status')
                if turn.get('willRetry') is True:
                    status = 'inProgress'
                failed = status in ('failed', 'blocked') and turn.get('endReason') not in ('aborted', 'interrupted', 'forked')
                old = known.get(identifier, {})
                episode = old.get('episode', 0)
                new_boundary = ordered and type(old.get('sequence')) is int and turn['sequence'] > old['sequence']
                if failed and (old.get('status') not in ('failed', 'blocked') or new_boundary):
                    if previous is not None and (identifier in eligible or old.get('status') == 'inProgress'):
                        episode += 1
                observed[identifier] = {'status': status, 'episode': episode}
                if ordered:
                    observed[identifier]['sequence'] = turn['sequence']
                if turn is turns[-1] and failed and episode:
                    pending = json.dumps([identifier, episode])
            current = {'anchor': turns[-1]['turnId'], 'turns': dict(list(observed.items())[-128:]),
                       'pending': pending, 'title': row.get('title') or '聊天'}
            if ordered:
                current['sequence'] = max([t['sequence'] for t in turns] + [(previous or {}).get('sequence', -1)])
            if current != previous:
                self.attention[key] = current
                write_json(self.data_dir/'notification-attention.json', self.attention)
            delivery = self._delivery_key(row, pending, completion='attention', target=target)
            return [delivery] if pending and not self.ledger.get(delivery, {}).get('delivered') else []

    def click_url(self, config, thread_id, host):
        thread_id = quote(thread_id, safe='').replace('~', '%7E')
        origins = sorted(self.origins())
        base = config.get('clickBase') or self.public_url() or next((o for o in origins if o.startswith('https://')), '')
        if not base:
            base = next((o for o in origins if urlsplit(o).hostname not in ('127.0.0.1', 'localhost')), '')
        if base and config.get('mobileAppLinks'):
            return 'codexbridge://open?origin=' + quote(base.rstrip('/'), safe='') + '&thread=' + thread_id + '&host=' + quote(host, safe='')
        return base.rstrip('/') + '/#' + thread_id + '~' + quote(host, safe='') if base else ''

    def _dispatch(self, config, row, title, channel, target, keys, completed):
        detected = time.monotonic()
        observed = self.attached.get((row['host'], row['id']))
        observation = None
        if not completed and getattr(observed, 'runtime', None) == 'cli-observer':
            with observed.condition:
                observation = (observed, observed.connection_epoch)
        if channel == 'mobile':
            return self._send_keys(config, row, title, channel, target, keys, completed,
                                   detected=detected, observation=observation)
        with self.lock:
            identity = (row['host'], row['id'], channel, completed)
            pending = [key for key in keys if not self.ledger.get(key, {}).get('delivered')
                       and time.time() >= self.ledger.get(key, {}).get('next', 0)]
            if not pending:
                self.delivery_queue.pop(identity, None)
                return False
            prior = self.delivery_queue.get(identity)
            if prior and prior[4] == target:
                detected = prior[-1]
            self.delivery_queue[identity] = (config, dict(row), title, channel, target, pending, completed, observation, detected)
            self.delivery_wakeup.set()
        return False

    def deliver_pending(self):
        """Drain one-shot scans or the background sender, never under the scan lock."""
        with self.delivery_lock:
            while not self.closed.is_set():
                with self.lock:
                    if not self.delivery_queue:
                        break
                    identity = next(iter(self.delivery_queue))
                    *args, observation, detected = self.delivery_queue.pop(identity)
                self._send_keys(*args, detected=detected, observation=observation)

    def _send_keys(self, config, row, title, channel, target, keys, completed, *, detected=None, observation=None):
        started = time.monotonic()
        now = time.time()
        with self.lock:
            pending = [k for k in keys if not self.ledger.get(k, {}).get('delivered') and now >= self.ledger.get(k, {}).get('next', 0)]
            if not pending or self.closed.is_set():
                return False
            config = settings(self.data_dir)
            if self._targets(config).get(channel) != target:
                return False
            provider = self.desktop_provider(row['host'])
            if not self._client_enabled(row['host']):
                return False
            current = self._effective(row)
            if not current['notifyOnCompletion' if completed is True else 'notifyOnRequest']:
                return False
            if observation is not None:
                observed, epoch = observation
                if self.attached.get((row['host'], row['id'])) is not observed:
                    return False
                with observed.condition:
                    if (observed.connection_epoch != epoch or not observed.connected
                            or observed.notification_connecting):
                        return False
                    active = {self._delivery_key(row, request['id'], target=target)
                              for request in observed.pending_requests()}
                pending = [key for key in pending if key in active]
                if not pending:
                    return False
            if completed == 'attention':
                record = self.attention.get(self._completion_key(row['host'], row['id'], target), {})
                if not record.get('pending') or self._delivery_key(row, record['pending'], completion='attention', target=target) not in pending:
                    return False
            client = {'claude': 'Claude', 'deepseek': 'DSH', 'zcode': 'ZCode', 'antigravity': 'Antigravity'}.get(provider, 'Codex')
            kind = 'attention' if completed == 'attention' else 'completion' if completed else 'request'
            heading = client + (' 需要你处理' if kind == 'attention' else ' 运行已完成' if completed else ' 需要你的确认')
            body = ('本轮运行遇到问题，请打开聊天查看并手动继续或调整模型。' if kind == 'attention' else
                    f'有 {len(pending)} 次运行已完成，请打开聊天查看。' if completed else f'有 {len(pending)} 项请求等待处理，请打开聊天查看。')
            if config['includeTitle']:
                body = title[:120] if channel == 'mobile' else title[:120] + '\n' + body
        success = False
        try:
            if channel == 'mobile':
                self.mobile.append(pending, row, heading, body, completed)
            else:
                click = self.click_url(config, row['id'], row['host'])
                if channel == 'webhook' or channel.startswith('webhook:'):
                    publish_webhook(webhook_channel_config(config, channel), heading, body, click,
                                    event='task.attention' if kind == 'attention' else 'task.completed' if completed else 'approval.required', event_id=pending[0],
                                    session_name=title[:120] if config['includeTitle'] else '', client=client,
                                    host=row['host'], count=len(pending))
                else:
                    sender = {'ntfy': publish, 'bark': publish_bark, 'pushplus': publish_pushplus}[channel]
                    sender(config, heading, body, click)
            success = True
        except Exception:
            pass  # Publish errors must not expose endpoint credentials or content.
        elapsed = (time.monotonic() - started) * 1000
        logging.log(logging.INFO if success else logging.WARNING, 'notification stage=%s provider=%s kind=%s channel=%s trace=%s count=%d queue_ms=%.1f work_ms=%.1f outcome=%s',
                     'inbox' if channel == 'mobile' else 'outbound', provider or 'codex',
                     kind, channel.split(':')[0], pending[0][-64:][:16],
                     len(pending), max(0, started - (detected if detected is not None else started)) * 1000,
                     elapsed, 'accepted' if success else 'failed')
        with self.lock:
            if self.closed.is_set():
                return False
            for key in pending:
                if success:
                    self.ledger[key] = {'delivered': True, 'time': now}
                else:
                    attempts = self.ledger.get(key, {}).get('attempts', 0) + 1
                    self.ledger[key] = {'delivered': False, 'attempts': attempts, 'next': now + min(300, 5 * 2 ** min(attempts, 6)), 'time': now}
            if success:
                self._status(channel, lastSent=now, error='')
            else:
                self._status(channel, error='发送失败，将在提醒仍开启时重试完成通知；待确认通知仅在请求仍待处理时重试。请检查服务地址、认证和网络。')
            if len(self.ledger) > 5000:
                self.ledger = dict(sorted(self.ledger.items(), key=lambda p: p[1].get('time', 0))[-4000:])
            write_json(self.data_dir/'notification-delivery.json', self.ledger)
        return True

    def scan(self, *, deliver=True):
        # A manual single pass retains synchronous delivery. The live scanner and
        # sender use separate workers, sharing the exact same detection path.
        started = time.monotonic()
        with self.lock:
            self.delivery_queue = {key: job for key, job in self.delivery_queue.items()
                                   if key[-1] or self.desktop_provider(key[0])}
        config = settings(self.data_dir)
        targets = self._targets(config)
        signature = tuple(sorted(targets.items()))
        if signature != self.target_signature:
            self.candidates.update({key: {'host': key[0], 'id': key[1]} for key in self.dormant})
            self.dormant.clear()
            self.target_signature = signature
        if targets and hasattr(self.bridge, 'notification_updates'):
            for row in self.bridge.notification_updates():
                key = (row['host'], row['id'])
                self.candidates[key] = row
                self.dormant.discard(key)
        if targets and time.monotonic() >= self.discovery_at and hasattr(self.bridge, 'notification_candidates'):
            self.discovery_at = time.monotonic() + 30
            # Explicit watches also need to discover a new run after going idle.
            discovery_started = time.monotonic()
            candidates = self.bridge.notification_candidates()
            logging.debug('notification stage=discovery work_ms=%.1f count=%d',
                         (time.monotonic() - discovery_started) * 1000, len(candidates))
            for row in candidates:
                key = (row['host'], row['id'])
                self.candidates[key] = row
                self.dormant.discard(key)
        with self.lock:
            watches = [r for r in self._selected() if (r['host'], r['id']) not in self.dormant] if targets else []
            changed = False
            policies, legacy_rows = self.policies(), self.watches()
            for key, record in list(self.attention.items()):
                host, identifier, target = json.loads(key)
                row = {'host': host, 'id': identifier}
                if target not in targets.values() or not self._effective(row, policies, legacy_rows)['notifyOnRequest']:
                    del self.attention[key]
                    write_json(self.data_dir/'notification-attention.json', self.attention)
                    continue
                if record.get('pending'):
                    channel = next(c for c, t in targets.items() if t == target)
                    keys = [self._delivery_key(row, record['pending'], completion='attention', target=target)]
                    # Unlike completion, do not replay mobile events before the latest
                    # native state has had a chance to cancel a recovered episode.
                    if channel != 'mobile':
                        changed = self._dispatch(config, row, record.get('title') or '聊天', channel, target, keys, 'attention') or changed
            for key, record in list(self.completions.items()):
                host, identifier, target = json.loads(key)
                row = {'host': host, 'id': identifier}
                provider = self.desktop_provider(host)
                if not self._client_enabled(host):
                    continue
                if target not in targets.values() or not self._effective(row, policies, legacy_rows)['notifyOnCompletion']:
                    del self.completions[key]
                    write_json(self.data_dir/'notification-completions.json', self.completions)
                    continue
                channel = next(c for c, t in targets.items() if t == target)
                keys = [self._delivery_key(row, turn, completion=True, target=target) for turn in record['pending']]
                changed = self._dispatch(config, row, record.get('title') or '聊天', channel, target, keys, True) or changed
        selected = {(r['host'], r['id']) for r in watches}
        for key in set(self.attached) - selected:
            session = self.attached[key]
            source = self.bridge.for_host(key[0])
            if hasattr(source, 'release_notification_session'):
                if not source.release_notification_session(session):
                    continue
            else:
                session.watched = False
            self.attached.pop(key, None)
        self._status(error='')
        for channel, target in targets.items():
            self._status(channel, target=target)
        for row in watches:
            if self.closed.is_set():
                break
            try:
                source = self.bridge.for_host(row['host'])
                session = source.notification_session(row['id']) if hasattr(source, 'notification_session') else source.session(row['id'], background=True)
                session.watched = True
                self.attached[(row['host'], row['id'])] = session
                with self.lock:
                    with session.condition:
                        metadata = {k: session.state[k] for k in ('title', 'cwd') if (session.state or {}).get(k)}
                        rows = self.watches()
                        legacy = next((r for r in rows if r['id'] == row['id'] and r['host'] == row['host']), None)
                        if legacy is not None and any(legacy.get(k) != v for k, v in metadata.items()):
                            legacy.update(metadata)
                            write_json(self.data_dir/'notification-watches.json', rows)
                        current = self._effective(row)
                        if not session.connected:
                            # Allow an asynchronous initial snapshot to arrive, then
                            # retire unanswered probes until a later discovery.
                            if hasattr(source, 'release_notification_session') and time.monotonic() >= session.notification_probe_until and source.release_notification_session(session):
                                key = (row['host'], row['id'])
                                self.dormant.add(key)
                                self.candidates.pop(key, None)
                                self.attached.pop(key, None)
                            continue  # Saved history is not a live pending approval.
                        requests = session.pending_requests()
                        live_running = session.state.get('threadRuntimeStatus', {}).get('type') == 'active' or any(t.get('status') == 'inProgress' for t in ordered_turns(session.state))
                        self.mobile_push.update_activity(row, 'waiting' if requests else 'running' if live_running else 'ready')
                        title = session.state.get('title') or '聊天'
                        turns = [{k: t[k] for k in ('turnId', 'status', 'willRetry') if k in t} for t in ordered_turns(session.state)]
                    completed_keys = {channel: self._completion_pending({**current, 'title': title}, turns, target) if current.get('notifyOnCompletion') else [] for channel, target in targets.items()}
                    attention_keys = {channel: self._attention_pending({**current, 'title': title}, turns, target) for channel, target in targets.items()}
                for channel, target in targets.items():
                    with self.lock:
                        for completed, keys in ((False, [self._delivery_key(row, r['id'], target=target) for r in requests]), (True, completed_keys[channel]), ('attention', attention_keys[channel])):
                            changed = self._dispatch(config, row, title, channel, target, keys, completed) or changed
                if hasattr(source, 'release_notification_session'):
                    # Keep only live tasks and approvals attached. Completion retry
                    # records above are independent of the thread subscription.
                    with session.condition:
                        state = session.state or {}
                        runtime = state.get('threadRuntimeStatus', {}).get('type')
                        active = bool(session.pending_requests()) or runtime == 'active' or any(t.get('status') == 'inProgress' for t in ordered_turns(state))
                        if not active and source.release_notification_session(session):
                            key = (row['host'], row['id'])
                            self.dormant.add(key)
                            self.candidates.pop(key, None)
                            self.attached.pop(key, None)
            except Exception:
                self._status(error='部分关注聊天暂时无法连接，请检查电脑 App 或 SSH 连接。')
        changed = self._scan_desktops(config, targets) or changed
        # Retry failed durable inbox writes after fresh native snapshots have
        # cancelled recovered episodes, including now-dormant subscriptions.
        if 'mobile' in targets:
            with self.lock:
                for key, record in self.attention.items():
                    host, identifier, target = json.loads(key)
                    if target == targets['mobile'] and record.get('pending'):
                        row = {'host': host, 'id': identifier}
                        keys = [self._delivery_key(row, record['pending'], completion='attention', target=target)]
                        changed = self._dispatch(config, row, record.get('title') or '聊天', 'mobile', target, keys, 'attention') or changed
        elapsed = (time.monotonic() - started) * 1000
        if elapsed >= 2000:
            logging.warning('notification stage=scan work_ms=%.1f', elapsed)
        if deliver:
            self.deliver_pending()

    def _scan_desktops(self, config, targets):
        manager = self.desktop_sessions
        if not targets or manager is None or time.monotonic() < self.desktop_poll_at:
            return False
        self.desktop_poll_at = time.monotonic() + 10
        with self.lock:
            self.delivery_queue = {key: job for key, job in self.delivery_queue.items()
                                   if key[-1] or not self.desktop_provider(key[0])}
        listed = time.monotonic()
        rows = manager.notification_rows()
        logging.debug('notification stage=desktop-list work_ms=%.1f count=%d', (time.monotonic() - listed) * 1000, len(rows))
        if not rows:
            return False
        # Rotate bounded history reads so one large client's history cannot block
        # permission notifications or continuously reread every idle transcript.
        offset = self.desktop_cursor % len(rows)
        rows = rows[offset:] + rows[:offset]
        self.desktop_cursor = (offset + 8) % len(rows)
        changed, reads = False, 0
        for row in rows:
            if self.closed.is_set():
                break
            client = desktop_client(row['host'])
            if not client or not manager.enabled(client):
                continue
            try:
                selected = manager.resolve_instance(row['provider'], client) if ':' in client else manager
            except (OSError, ValueError):
                continue
            current = self._effective(row)
            if not (current['notifyOnRequest'] or current['notifyOnCompletion']):
                continue
            title = row.get('title') or '聊天'
            requests = [r for r in row.get('requests', []) if isinstance(r, dict) and r.get('id')]
            if requests or row.get('runtimeKnown') is True:
                self.mobile_push.update_activity(row, 'waiting' if requests else 'running' if row.get('status') == 'active' else 'ready')
            for channel, target in targets.items():
                keys = [self._delivery_key(row, r['id'], target=target) for r in requests]
                changed = self._dispatch(config, row, title, channel, target, keys, False) or changed
            key = (row['host'], row['id'])
            signature = (row.get('updatedAt'), row.get('status'), row.get('error'))
            cached = self.desktop_details.get(key)
            if cached is None or cached[0] != signature or row.get('status') == 'active':
                if reads >= 8:
                    continue
                reads += 1
                try:
                    read_started = time.monotonic()
                    detail = selected.call(row['provider'], 'detail', row['id'])
                    logging.debug('notification stage=desktop-detail provider=%s work_ms=%.1f',
                                 row['provider'], (time.monotonic() - read_started) * 1000)
                    if detail.get('connected') is False or not isinstance(detail.get('turns'), list):
                        continue
                    cached = (signature, detail['turns'])
                    self.desktop_details[key] = cached
                except (OSError, ValueError):
                    continue
            for channel, target in targets.items():
                keys = self._completion_pending({**current, 'title': title}, cached[1], target)
                changed = self._dispatch(config, row, title, channel, target, keys, True) or changed
                keys = self._attention_pending({**current, 'title': title}, cached[1], target)
                changed = self._dispatch(config, row, title, channel, target, keys, 'attention') or changed
        return changed

    def _status(self, channel=None, **values):
        with self.lock:
            self._write_status(channel, **values)

    def _write_status(self, channel=None, **values):
        path = self.data_dir/'notification-status.json'
        prior = read_json(path, {})
        if channel:
            record = prior.get(channel, {})
            if 'target' in values and record.get('target') != values['target']:
                record = {}
            current = {**prior, channel: {**record, **values}}
        else:
            current = {**prior, **values}
        if current != prior:
            write_json(path, current)

    def _run(self):
        while not self.closed.is_set():
            try:
                self.scan(deliver=False)
            except Exception:
                self._status(error='通知配置读取失败，请在电脑启动器重新保存配置。')
            self.wakeup.wait(3)
            self.wakeup.clear()
            # Coalesce a burst of stream patches rather than scan per token.
            self.closed.wait(1)

    def _delivery_run(self):
        while not self.closed.is_set():
            self.delivery_wakeup.clear()
            try:
                self.deliver_pending()
                if not self.closed.is_set():
                    self.security.scan()
            except Exception:
                self._status(error='通知配置读取失败，请在电脑启动器重新保存配置。')
            self.delivery_wakeup.wait(3)

    def _push_allowed(self, event):
        provider = self.desktop_provider(event['host'])
        if provider and (self.desktop_sessions is None or not self.desktop_sessions.enabled(desktop_client(event['host']))):
            return False
        policy = self._effective({'id': event['threadId'], 'host': event['host']})
        if event['kind'] == 'state':
            return policy['notifyOnRequest'] or policy['notifyOnCompletion']
        return policy['notifyOnCompletion' if event['kind'] == 'completion' else 'notifyOnRequest']

    def _push_run(self):
        while not self.closed.is_set():
            try:
                self.mobile_push.drain(settings(self.data_dir).get('mobileEnabled', False), self._push_allowed)
            except Exception:
                self._status('nativePush', error='系统推送暂不可用，请检查平台配置')
            self.closed.wait(3)
