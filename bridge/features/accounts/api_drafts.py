"""Explicit model-list discovery from an unsaved API draft; never runs inference."""
import re
from urllib.parse import urlsplit

from bridge.features.accounts.accounts import Accounts
from bridge.features.accounts.models import model_ids


FORMATS = {'anthropic-messages', 'openai-chat-completions', 'openai-responses'}


def model_draft(value, *, protocol='openai-responses', fixed_url=None, saved=None):
    if not isinstance(value, dict) or set(value) - {'provider', 'action', 'operation', 'id', 'baseUrl', 'apiKey', 'authScheme', 'apiFormat'}:
        raise ValueError('API 模型查询参数无效')
    if protocol not in FORMATS or value.get('apiFormat', protocol) != protocol:
        raise ValueError('请选择受支持的 API 协议')
    url = Accounts.api_url(value.get('baseUrl', fixed_url or ''))
    if fixed_url and url != fixed_url:
        raise ValueError('此客户端仅支持官方 API 地址')
    auth = value.get('authScheme', 'x-api-key' if protocol == 'anthropic-messages' else 'bearer')
    if auth not in ('bearer', 'x-api-key') or protocol != 'anthropic-messages' and auth != 'bearer':
        raise ValueError('API 认证方式无效')
    key = value.get('apiKey', '')
    if key == '' and value.get('id') and saved:
        previous = saved(value['id'])
        previous_auth = previous.get('authScheme', 'x-api-key' if previous['apiFormat'] == 'anthropic-messages' else 'bearer')
        if (Accounts.api_url(previous['baseUrl']) != url or previous['apiFormat'] != protocol or previous_auth != auth):
            raise ValueError('修改 API 地址、协议或认证方式后，请重新填写 API Key')
        key = previous['apiKey']
    if not isinstance(key, str) or not re.fullmatch(r'[\x21-\x7e]{1,8192}', key):
        raise ValueError('请输入有效的 API Key')
    headers = None
    if protocol == 'anthropic-messages':
        if not urlsplit(url).path.rstrip('/').endswith('/v1'):
            url += '/v1'
        headers = {**({'Authorization': 'Bearer '+key} if auth == 'bearer' else {'x-api-key': key}),
                   'anthropic-version': '2023-06-01'}
    return {'models': model_ids(url, key, **({'headers': headers} if headers else {}))}
