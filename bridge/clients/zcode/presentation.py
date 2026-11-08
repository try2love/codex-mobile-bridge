"""Saved composer presentation, separate from live operation permissions."""
from urllib.parse import unquote


# These slots describe the native composer, never permission to dispatch work.
COMPOSER_CAPABILITIES = dict.fromkeys(('models', 'effort', 'skills', 'permissions',
                                      'attachments', 'plan', 'goal'), True)


def composer_presentation(model, effort=None):
    value = model.strip() if isinstance(model, str) else ''
    # Follow @zcode/shared's custom-model-value and model-selection display
    # boundaries. The original routing ID is kept on the session unchanged.
    if value.startswith('custom:'):
        parts = value[7:].split(':')
        value = unquote(':'.join(parts[2:] if len(parts) >= 3 and parts[0] == 'builtin' else parts[1:]))
    elif '/' in value:
        value = value.split('/', 1)[1]
    name, separator, level = value.partition('$')
    if not separator or not name or not level:
        name, level = value, ''
    result = {'modelDisplayName': name, 'composerCapabilities': dict(COMPOSER_CAPABILITIES)}
    if isinstance(effort, str) and effort:
        result['effort'] = effort
    elif level:
        result['effort'] = level
    return result
