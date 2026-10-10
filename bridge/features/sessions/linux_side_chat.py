"""Linux runtime compatibility for bridge-owned local side chats."""
import copy
import threading

from bridge.features.sessions.side_chat import SideChat, SideChatError
from bridge.features.sessions.linux_permissions import (
    read_permission_facts, desktop_permission_preferences, permission_options,
    permission_settings, require_permission, current_mode,
)

PERMISSION_CONFIRM_TIMEOUT = 5


class LinuxSideChat(SideChat):
    def __init__(self, executable, home, *args, **kwargs):
        self.home = home
        self.permission_revision = 0
        super().__init__(executable, home, *args, **kwargs)
        self.permission_condition = threading.Condition(self.lock)

    def view(self):
        with self.lock:
            return {**super().view(), 'linuxPermissionChecks': True, 'permissionMode': current_mode(self.state)}

    def _event(self, message):
        condition = getattr(self, 'permission_condition', None)
        if condition is None:
            return super()._event(message)
        method, params = message.get('method'), message.get('params') or {}
        with condition:
            if method == 'thread/settings/updated':
                if self.closed or not self.ready or params.get('threadId') != self.id:
                    return
                settings = params.get('threadSettings')
                if not isinstance(settings, dict):
                    return
                settings = copy.deepcopy(settings)
                profile = settings.get('activePermissionProfile') or {}
                settings['permissions'] = profile.get('id') or settings.get('permissions')
                self.state['latestThreadSettings'] = settings
                # A normalized sandbox policy must clear any previous profile.
                for key in ('permissions', 'approvalPolicy', 'approvalsReviewer'):
                    self.turn_permissions.pop(key, None)
                    if settings.get(key) is not None:
                        self.turn_permissions[key] = settings[key]
                self.permission_revision += 1
                condition.notify_all()
                return
            super()._event(message)
            if method in ('bridge/disconnected', 'thread/closed'):
                condition.notify_all()

    def _permission_options(self):
        view = self.view()
        if not view['connected']:
            raise SideChatError('侧边聊天已失效，请关闭后新建')
        facts = read_permission_facts(
            lambda method, params: self.runtime.request(method, params, timeout=5), view['cwd'])
        preferences = desktop_permission_preferences(self.home)
        return facts, permission_options(facts, preferences, view.get('permissionMode'), native=False)

    def permission_options(self):
        with self.actions:
            return self._permission_options()[1]

    def permissions(self, preset, confirmed=False):
        if preset not in ('ask', 'auto-review', 'full-access'):
            raise ValueError('权限设置无效')
        if preset == 'full-access' and confirmed is not True:
            raise ValueError('请确认完全访问权限')
        with self.actions:
            facts, options = self._permission_options()
            require_permission(options, preset)
            settings = permission_settings(preset, facts)
            with self.lock:
                revision = self.permission_revision
            self.runtime.request('thread/settings/update', {'threadId': self.id, **settings})
            # An empty RPC result is only an acknowledgement, not applied state.
            with self.permission_condition:
                self.permission_condition.wait_for(
                    lambda: self.error or self.closed or (
                        self.permission_revision > revision and self.view()['permissionMode'] == preset),
                    timeout=PERMISSION_CONFIRM_TIMEOUT)
                if (self.error or self.closed or self.permission_revision <= revision
                        or self.view()['permissionMode'] != preset):
                    raise SideChatError('权限设置尚未获运行时确认，请刷新状态后重试。')
                return self.view()
