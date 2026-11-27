"""Recognize the active external Magpie configuration without copying credentials."""
import re


def magpie_managed(config):
    profile = (config.get('profiles') or {}).get(config.get('profile')) or {}
    effective = {**config, **profile}
    provider = effective.get('model_provider') or 'openai'
    if (provider == 'magpie' or
            str(effective.get('model_catalog_json', '')).replace('\\', '/').rsplit('/', 1)[-1] == 'magpie-models.json'):
        return True
    definition = (effective.get('model_providers') or {}).get(provider) or {}
    endpoint = definition.get('base_url') or (effective.get('openai_base_url') if provider == 'openai' else None)
    return bool(re.fullmatch(r'(?i)https?://(?:localhost|127\.0\.0\.1|\[::1\]):3425(?:/v1)?/?',
                             str(endpoint or '').strip()))
