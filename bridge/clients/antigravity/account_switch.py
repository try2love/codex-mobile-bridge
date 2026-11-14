"""Explicit account switch: scoped normal quit, existing transaction, same GUI."""
from bridge.clients.errors import BridgeUnavailable
from bridge.platforms import antigravity_lifecycle as native
from bridge.platforms.antigravity import endpoint, verify_endpoint


def switch_account(store, adapter, descriptor, identifier):
    store.validate_saved(identifier)
    # Serialize native bridge writes with this switch. The manager also fences
    # new operations through its existing provider-changing gate.
    with adapter.cli._lock:
        plan = native.prepare(descriptor)
        if plan:
            surface = plan['installation']['surface']
            target = endpoint(surface, adapter.home)
            native.bind_server(plan, target)
            if adapter.hooks:
                adapter.hooks.assert_account_idle()
            adapter.cli.assert_idle(surface, target['identity'])
            verify_endpoint(target)
            native.verify(plan)
            native.stop(plan)
        # This retains the existing all-writers-stopped checks, conditional
        # rollback and read-back verification. No profile tree is copied.
        result = store.restore(identifier)
        if not plan:
            return result
        try:
            native.restart(plan)
        except Exception:
            # Once native startup may have happened, its credential refreshes
            # must not be overwritten by a speculative rollback or a retry.
            return {**result, 'message': '登录配置已应用，但未确认原客户端重新打开；请在电脑端检查。未重复启动，未回退或清除配置。'}
        # Starting a process is not proof of login. public() derives activeIds
        # exclusively from GetUserStatus; never assign a last-switched ID.
        try:
            current = store.public()
            if identifier in current.get('activeIds', []):
                return {**current, 'message': '已重新打开原客户端，并确认当前登录。'}
            result = current
        except (OSError, ValueError, BridgeUnavailable):
            pass
        return {**result, 'message': '登录配置已应用，已请求重新打开原客户端；登录尚未确认，请稍后刷新账号状态。'}
