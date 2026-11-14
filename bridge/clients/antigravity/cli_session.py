"""Use the verified original Antigravity server; never spawn a competing CLI."""
import json
from pathlib import Path
import threading
import subprocess
import urllib.error
import urllib.request

from bridge.clients.errors import BridgeUnavailable
from bridge.platforms.antigravity import endpoint, verify_endpoint


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise BridgeUnavailable('Antigravity 本机服务不允许重定向')


class AntigravityCli:
    def __init__(self, home=None):
        self.home = Path(home or Path.home()).resolve()
        self._lock = threading.RLock()
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def available(self, surface='app'):
        try:
            endpoint(surface, self.home)
            return True
        except (OSError, ValueError, subprocess.SubprocessError):
            return False

    def call_ls_rpc(self, method, payload=None, *, surface='app'):
        if method not in ('GetUserStatus', 'StartCascade', 'SendUserCascadeMessage', 'CancelCascadeInvocation'):
            raise ValueError('不支持的 Antigravity 原生操作')
        # This serializes bridge writes; ownership remains with the native LS.
        with self._lock:
            try:
                target = endpoint(surface, self.home)
                verify_endpoint(target)
                request = urllib.request.Request(
                    target['address'] + '/exa.language_server_pb.LanguageServerService/' + method,
                    data=json.dumps(payload or {}).encode('utf-8'),
                    headers={'Content-Type': 'application/json', 'x-codeium-csrf-token': target['token']})
                with self._opener.open(request, timeout=8) as response:
                    raw = response.read(8 * 1024 * 1024 + 1)
                if len(raw) > 8 * 1024 * 1024:
                    raise ValueError('Antigravity 原生回复过大')
                result = json.loads(raw) if raw else {}
                if not isinstance(result, dict):
                    raise ValueError('Antigravity 原生回复无效')
                verify_endpoint(target)
                return result
            except (OSError, ValueError, urllib.error.URLError) as error:
                # Do not expose native error bodies, tokens or retry a write.
                raise BridgeUnavailable('Antigravity 原生操作未确认，请检查电脑原会话后再操作') from None

    def user_status(self, surface='app'):
        return self.call_ls_rpc('GetUserStatus', surface=surface).get('userStatus') or {}

    def catalog_models(self, *, refresh=False, surface='app'):
        status = self.user_status(surface)
        configs = status.get('cascadeModelConfigData', {}).get('clientModelConfigs', [])
        models = []
        for row in configs:
            if not isinstance(row, dict) or not isinstance(row.get('modelId'), str):
                continue
            alias = row.get('modelOrAlias')
            if not isinstance(alias, dict) or not alias.get('model'):
                continue
            models.append({'id': row['modelId'], 'name': row.get('label') or row['modelId'],
                           'efforts': [], 'supportsEffort': False})
        return {'models': models, 'userStatus': status,
                'notice': '' if models else '原客户端暂未提供可用模型列表'}

    def _model(self, model, surface):
        if not model or model == 'auto':
            return None  # Preserve the original server's selected default.
        configs = self.user_status(surface).get('cascadeModelConfigData', {}).get('clientModelConfigs', [])
        found = [row.get('modelOrAlias', {}).get('model') for row in configs
                 if isinstance(row, dict) and row.get('modelId') == model]
        if len(found) != 1 or not found[0]:
            raise ValueError('所选模型不在当前原客户端的可用目录中')
        return found[0]

    def send_user_message(self, conversation_id, prompt, model=None, effort=None, *, surface='app'):
        body = {'cascade_id': conversation_id, 'items': [{'text': prompt}]}
        selected = self._model(model, surface)
        if selected:
            body['cascade_config'] = {'planner_config': {'requested_model': {'model': selected}}}
        return self.call_ls_rpc('SendUserCascadeMessage', body, surface=surface)

    def start_cascade(self, *, surface='app', model=None, effort=None, cwd=None):
        path = Path(cwd or '').resolve()
        if not cwd or not path.is_dir():
            raise ValueError('请选择有效的本机项目目录')
        uri = path.as_uri()
        body = {'source': 'CORTEX_TRAJECTORY_SOURCE_CASCADE_CLIENT', 'workspace_uris': [uri]}
        selected = self._model(model, surface)
        if selected:
            body['requested_model'] = selected
        result = self.call_ls_rpc('StartCascade', body, surface=surface)
        cid = result.get('cascadeId') or result.get('cascade_id')
        if not isinstance(cid, str) or not cid:
            raise BridgeUnavailable('Antigravity 原客户端未确认新会话标识')
        environment = result.get('projectEnvInfo') or result.get('project_env_info') or {}
        if environment.get('workspaceUris', environment.get('workspace_uris')) != [uri]:
            raise BridgeUnavailable('原客户端尚未确认新会话项目绑定，首条消息未发送；请在电脑检查已创建的会话')
        return cid

    def stop(self, conversation_id, *, surface='app'):
        return self.call_ls_rpc('CancelCascadeInvocation', {'cascade_id': conversation_id}, surface=surface)

    def close(self):
        pass
