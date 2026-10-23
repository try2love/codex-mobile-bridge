"""Recognize external Magpie configuration without copying its credentials."""
import re


def magpie_managed(config=None, text=None):
    if config is not None:
        profile = (config.get('profiles') or {}).get(config.get('profile')) or {}
        effective = {**config, **profile}
        if (effective.get('model_provider') == 'magpie' or
                'magpie' in (effective.get('model_providers') or {}) or
                str(effective.get('model_catalog_json', '')).replace('\\', '/').rsplit('/', 1)[-1] == 'magpie-models.json'):
            return True
        return bool(re.search(r'(?i)^https?://(?:localhost|127\.0\.0\.1|\[::1\]):3425(?:/v1)?/?$',
                              str(effective.get('openai_base_url') or '').strip()))
    # Conservative guard for source Python without TOML. False positives disable
    # writes; text in a comment can never grant permission to overwrite config.
    return bool(re.search(r'(?i)\bmagpie\b|https?://(?:localhost|127\.0\.0\.1|\[::1\]):3425\b', text or ''))
