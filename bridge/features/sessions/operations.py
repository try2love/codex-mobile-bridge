"""Public native desktop operations shared by the gateway and its transports."""

DESKTOP_PROVIDERS = frozenset({"claude", "deepseek", "zcode"})
READS = {'list', 'detail', 'catalog', 'projects', 'create-models', 'account', 'access', 'organization', 'tasks'}
WRITES = {'send', 'stop', 'settings', 'respond', 'create', 'access', 'resume', 'fork', 'pin', 'archive', 'rename', 'organization', 'goal', 'compact', 'stop-task', 'edit-message'}
