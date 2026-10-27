"""Whitelisted conversation metadata actions; never manipulate rollout files."""
import uuid
from pathlib import Path

from .catalog import Catalog
from .runtime import MetadataRuntime, RuntimeErrorDetail


class ThreadActions:
    def __init__(self, codex_home, executable=None):
        self.home = Path(codex_home)
        self.executable = executable or Catalog.find_runtime()

    def apply(self, thread_id, action, title=None):
        thread_id = str(uuid.UUID(thread_id))
        methods = {'rename': 'thread/name/set', 'archive': 'thread/archive',
                   'unarchive': 'thread/unarchive', 'delete': 'thread/delete'}
        if action not in methods:
            raise ValueError('不支持的聊天操作')
        params = {'threadId': thread_id}
        if action == 'rename':
            if not isinstance(title, str) or not title.strip() or len(title) > 120:
                raise ValueError('标题为 1–120 字')
            params['name'] = title.strip()
        with MetadataRuntime(self.executable, self.home, str(self.home)) as runtime:
            runtime.request(methods[action], params)
        return {'ok': True, 'id': thread_id, 'action': action}
