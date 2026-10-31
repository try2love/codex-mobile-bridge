"""Shared notification routing for native desktop providers."""
PROVIDERS = ('codex', 'claude', 'deepseek', 'zcode')


def desktop_provider(host):
    prefix = 'desktop:'
    provider = host[len(prefix):] if isinstance(host, str) and host.startswith(prefix) else None
    return provider if provider in PROVIDERS[1:] else None
