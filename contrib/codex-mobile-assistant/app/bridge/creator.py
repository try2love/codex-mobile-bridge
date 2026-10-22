"""Create an empty chat without executing a model turn."""
import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlencode

from .catalog import Catalog
from .runtime import MetadataRuntime, RuntimeErrorDetail


class CreationError(RuntimeErrorDetail):
    pass


class ThreadCreator:
    def __init__(self, codex_home, executable=None):
        self.home = Path(codex_home)
        self.executable = executable or Catalog.find_runtime()

    def create(self, cwd, title, roots, project_id=None, on_created=None, request_id=None, on_progress=None):
        if not Path(cwd).is_dir():
            raise CreationError('项目目录不存在，请在桌面检查项目位置。')
        progress = on_progress or (lambda phase: None)
        progress('starting')
        try:
            with MetadataRuntime(self.executable, self.home, cwd) as runtime:
                progress('creating')
                params = {'cwd': cwd, 'runtimeWorkspaceRoots': roots,
                          'threadSource': 'codex-mobile:' + str(uuid.UUID(request_id)) if request_id else 'user',
                          'ephemeral': False, 'historyMode': 'legacy'}
                if project_id:
                    params['projectId'] = project_id
                try:
                    response = runtime.request('thread/start', params)
                except RuntimeErrorDetail as exc:
                    # Desktop project IDs can be absent from the app-server registry.
                    # Only an explicit rejection is safe to retry; retain the exact
                    # workspace and recovery marker, never retry an uncertain write.
                    missing_project = 'project not found: ' + str(project_id)
                    if not project_id or exc.uncertain or str(exc).strip().casefold() != missing_project.casefold():
                        raise
                    params = {key: value for key, value in params.items() if key != 'projectId'}
                    response = runtime.request('thread/start', params)
                try:
                    thread_id = str(uuid.UUID(response['thread']['id']))
                except (KeyError, ValueError, TypeError) as exc:
                    raise CreationError('创建返回格式不兼容，可稍后找回对话。', True) from exc
                result = {'id': thread_id, 'title': title, 'cwd': cwd}
                if on_created:
                    on_created(result)
                progress('naming')
                try:
                    runtime.request('thread/name/set', {'threadId': thread_id, 'name': title})
                except RuntimeErrorDetail:
                    result['warning'] = '对话已创建，标题未同步，可在聊天菜单修改。'
                return result
        except RuntimeErrorDetail as exc:
            raise CreationError(str(exc), exc.uncertain) from exc


def open_desktop_chat(thread_id, host='local'):
    thread_id = str(uuid.UUID(thread_id))
    url = 'codex://threads/' + thread_id
    if host != 'local':
        url += '?' + urlencode({'hostId': host})
    if os.name == 'nt':
        os.startfile(url)
    else:
        subprocess.run(['open' if sys.platform == 'darwin' else 'xdg-open', url],
                       check=True, timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
