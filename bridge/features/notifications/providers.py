"""Shared notification routing for native desktop providers."""
import re

PROVIDERS = ('codex', 'claude', 'deepseek', 'zcode', 'antigravity')


def desktop_client(host):
    prefix = 'desktop:'
    provider = host[len(prefix):] if isinstance(host, str) and host.startswith(prefix) else None
    return provider if provider in PROVIDERS[1:] or isinstance(provider, str) and re.fullmatch(r'(claude|deepseek):[0-9a-f]{32}', provider) else None


def desktop_provider(host):
    client = desktop_client(host)
    return client.split(':', 1)[0] if client else None
