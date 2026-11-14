"""Use the verified original Antigravity server; never spawn a competing CLI."""
import json
import re
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
    def __init__(self, home=None, model_catalog=None):
        self.home = Path(home or Path.home()).resolve()
        self.model_catalog = model_catalog
        self._lock = threading.RLock()
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def available(self, surface='app'):
        try:
            endpoint(surface, self.home)
            return True
        except (OSError, ValueError, subprocess.SubprocessError):
            return False

    def call_ls_rpc(self, method, payload=None, *, surface='app', expected_identity=None):
        if method not in ('GetUserStatus', 'GetAllCascadeTrajectories', 'StartCascade', 'SendUserCascadeMessage', 'CancelCascadeInvocation'):
            raise ValueError('不支持的 Antigravity 原生操作')
        # This serializes bridge writes; ownership remains with the native LS.
        with self._lock:
            try:
                target = endpoint(surface, self.home)
                if expected_identity is not None and target['identity'] != expected_identity:
                    raise ValueError('原生服务进程已变化')
                verify_endpoint(target)
                request = urllib.request.Request(
                    target['address'] + '/exa.language_server_pb.LanguageServerService/' + method,
                    data=json.dumps(payload or {}).encode('utf-8'),
                    headers={'Content-Type': 'application/json', 'x-codeium-csrf-token': target['token']})
                with self._opener.open(request, timeout=8) as response:
                    raw = response.read(8 * 1024 * 1024 + 1)
                if len(raw) > 8 * 1024 * 1024:
                    raise ValueError('Antigravity 原生回复过大')
                if method == 'GetAllCascadeTrajectories' and not raw:
                    raise ValueError('Antigravity 任务状态回复为空')
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

    def assert_idle(self, surface, expected_identity):
        # Request every trajectory, including children. Saved history is neither
        # complete nor synchronous enough to authorize an account restart.
        result = self.call_ls_rpc('GetAllCascadeTrajectories', {'exclude_subtrajectories': False},
                                  surface=surface, expected_identity=expected_identity)
        # The installed protobuf service omits an empty map: a successful,
        # identity-verified response {} is its real zero-trajectory response.
        # Do not extend this to malformed fields, error objects or DB fallbacks.
        rows = {} if result == {} else result.get('trajectorySummaries')
        if set(result) - {'trajectorySummaries'}:
            raise ValueError('无法完整核验 Antigravity 任务，请在原客户端正常退出后切换账号')
        if not isinstance(rows, dict):
            raise ValueError('无法完整核验 Antigravity 任务，请在原客户端正常退出后切换账号')
        for row in rows.values():
            if not isinstance(row, dict) or row.get('status') != 'CASCADE_RUN_STATUS_IDLE':
                raise ValueError('Antigravity 有未结束或状态未知的任务，请处理后重试')
            if any(row.get(key) for key in ('waitingSteps', 'notFullyIdle', 'hasActiveChildren',
                                           'hasRunningSubagents', 'backgroundTaskCount')):
                raise ValueError('Antigravity 有运行任务、后台任务或待确认操作，请处理后重试')

    def catalog_models(self, *, refresh=False, surface='app'):
        status = self.user_status(surface)
        configs = status.get('cascadeModelConfigData', {}).get('clientModelConfigs', [])
        models = []
        for row in configs:
            if not isinstance(row, dict) or row.get('disabled') is True or not isinstance(row.get('modelId'), str):
                continue
            alias = row.get('modelOrAlias')
            if not isinstance(alias, dict) or not alias.get('model'):
                continue
            models.append({'id': row['modelId'], 'name': row.get('label') or row['modelId'],
                           'efforts': [], 'supportsEffort': False, 'nativeModel': alias['model']})
        return {'models': models, 'userStatus': status,
                'notice': '' if models else '原客户端暂未提供可用模型列表'}

    def _model(self, model, surface):
        if model is None:
            return None  # Preserve the original server's selected default.
        if isinstance(model, str) and re.fullmatch(r'MODEL_[A-Z0-9_]+', model):
            return model  # Explicit native enum, validated by the original service.
        catalog = self.model_catalog(surface) if self.model_catalog else self.catalog_models(surface=surface)
        found = [row.get('nativeModel') for row in catalog.get('models', [])
                 if isinstance(row, dict) and row.get('id') == model]
        if len(found) != 1 or not found[0]:
            raise ValueError('未找到此模型的原生标识，请刷新目录或手动填写完整 MODEL_ 标识')
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
