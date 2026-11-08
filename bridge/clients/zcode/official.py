"""Use the selected official relay with the shared native ZCode operations."""
import hashlib
import json
import time

from bridge.clients.errors import BridgeUnavailable
from .adapter import ZCodePrototype, _NoLocalTransport
from .transport import operation_timeout


class ZCodeOfficial(ZCodePrototype):
    def __init__(self, directory, config, client):
        self.client = client
        # A relay connection never inherits a local renderer's live identity.
        config = {key: value for key, value in config.items()
                  if key not in ('binding', 'process', 'sessionId', 'workspace', 'port', 'debugFallbackPort')}
        super().__init__(directory, {**config, 'officialRemote': True}, transport=self._native)

    def _window_app(self):
        return None

    def _live_process(self):
        owner = self.client.owner()
        if not isinstance(owner, dict) or any(not isinstance(owner.get(key), str) or not owner[key]
                                              for key in ('epoch', 'device')):
            raise _NoLocalTransport('ZCode 官方远控尚未连接，请在应用管理中重新连接。')
        return {'epoch': owner['epoch'], 'device': owner['device']}

    def connection_owner(self, executable=None):
        try:
            return self._live_process()
        except _NoLocalTransport:
            return None

    def compatibility_owner(self, executable):
        raise BridgeUnavailable('官方远控不使用本机兼容端口。')

    def start_private_application(self, owner):
        raise BridgeUnavailable('请通过官方远控连接 ZCode。')

    def _native(self, request):
        owner = self._live_process()
        if owner != request.get('expectedProcess', self.process):
            raise BridgeUnavailable('ZCode 官方连接或设备已变化，请重新核验并显式接入。')
        timeout = 120 if request['action'] == 'application' else operation_timeout(request['action'])
        args = {**request, 'expectedProcess': owner,
                'expiresAt': min(int((time.time() + timeout) * 1000), request.get('expiresAt', float('inf')))}
        # The IPC server rechecks this same epoch at dispatch. Do not retry here:
        # accepted writes with a lost receipt remain the shared receipt's concern.
        return self.client.call(args)

    def connect_application(self, pid=None, *, wait=False, deadline=None):
        if wait:
            # A new native process cannot inherit the old terminal's workspace
            # binding, even if its final offline heartbeat has not arrived yet.
            self.client.close()
        self.client.connect(**({'wait': True} if wait else {}))
        owner = self._live_process()
        request = {'action': 'application', 'expectedProcess': owner}
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BridgeUnavailable('ZCode 官方连接尚未就绪，请重试。')
            request['expiresAt'] = int((time.time() + remaining) * 1000)
        self._confirm_application(owner, self.transport(request))

    def disconnect(self):
        try:
            self.client.close()
        finally:
            self._adopt_process(None)
            self.release_window()

    def reconnect_existing(self):
        sid = self.sid
        try:
            self.connect_application()
        except (OSError, ValueError, EOFError):
            return False
        if not sid:
            return True
        # The explicit reconnect may renew its lease after a relay epoch change;
        # retain the target session, never reuse the previous runtime binding.
        import uuid
        try:
            self.call('resume', sid, {'id': str(uuid.uuid4())})
            return True
        except (OSError, ValueError, EOFError):
            return False

    def _application_status(self):
        state = self.client.status()
        owner = state.get('owner')
        ready = state.get('connected') is True and owner is not None and owner == self.application_owner
        return {'connected': ready, 'historyAvailable': (self.home/'tasks-index.sqlite').is_file(),
                'sessionAccess': ready, 'canCreate': ready and self.application_create,
                'retryable': state.get('configured') is True and not ready,
                'reason': '已连接官方远控' if ready else
                          state.get('reason') or 'ZCode 官方远控尚未连接，请在应用管理中重新连接。'}

    def status(self):
        base = self._application_status()
        if not base['sessionAccess'] or not self.sid:
            return base
        return {**base, **super().status()}

    def account_scope(self):
        # Model caches must not cross the selected device/relay account identity.
        value = [super().account_scope(), self.client.owner()]
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
