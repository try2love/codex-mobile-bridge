"""Creation-time model choices use the same account-bound catalogs as chat settings."""
import re


FIELDS = ('model', 'effort', 'modelCatalogIdentity', 'manualModel', 'manualEffort')


def requested(value):
    return {key: value[key] for key in FIELDS if key in value}


def intent(value):
    # Catalog/environment revisions are preconditions, not a new user request.
    # Refreshing them must not replay a creation with an uncertain receipt.
    return {key: item for key, item in value.items() if key not in ('modelCatalogIdentity', 'environment')}


def validate(value, catalog):
    selection = requested(value)
    if not selection:
        return {}  # Existing callers retain the original client's default.
    identity = (catalog.get('cache') or {}).get('identity')
    if not identity or selection.get('modelCatalogIdentity') != identity:
        raise ValueError('账号或运行环境已变化，请刷新新建聊天的模型列表')
    model = selection.get('model')
    if not isinstance(model, str) or not re.fullmatch(r'[^\s\x00-\x1f\x7f]{1,300}', model):
        raise ValueError('请先选择新会话的模型')
    capabilities = catalog.get('capabilities') or {}
    rows = catalog.get('models') or []
    selected = next((row for row in rows if row.get('id') == model), None)
    if selected is None and not (selection.get('manualModel') is True and capabilities.get('manualModels') is True):
        raise ValueError('所选模型已不可用，请刷新模型列表后重新选择')
    effort = selection.get('effort')
    if effort is not None:
        if selected and selected.get('supportsEffort') is False:
            raise ValueError('所选模型未提供思考强度设置')
        if not isinstance(effort, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', effort):
            raise ValueError('请选择有效的思考强度')
        allowed = (selected or {}).get('efforts') or []
        if effort not in allowed and not (selection.get('manualEffort') is True and capabilities.get('manualEffort') is True):
            raise ValueError('所选模型不支持此思考强度，请重新选择')
    elif selected and selected.get('efforts'):
        raise ValueError('请先选择新会话的思考强度')
    return {key: val for key, val in selection.items() if key != 'modelCatalogIdentity'}
