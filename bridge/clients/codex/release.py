"""Release only the calling connection's native thread subscription."""
from bridge.clients.codex.app_server import AppServerUnsupported


GOAL_STATUSES = ('active', 'paused', 'blocked', 'usageLimited', 'budgetLimited', 'complete')


class ReleaseNotSent(ValueError):
    """Only reads ran; a fresh explicit release may safely retry."""
    def __init__(self, error):
        super().__init__(str(error))
        self.release_result = {'status': 'failed', 'notSent': True, 'error': str(error)}


def check_terminal_system_error(request, thread):
    """An Error precedes TurnComplete; systemError alone is not quiescence."""
    identifier = thread.get('id')
    def settled(snapshot):
        status = snapshot.get('status') or {}
        return (snapshot.get('id') == identifier and status.get('type') == 'systemError'
                and ('activeFlags' not in status or status['activeFlags'] == [])
                and ('requests' not in snapshot or snapshot['requests'] == [])
                and not any(turn.get('status') == 'inProgress' for turn in snapshot.get('turns') or []))
    if not settled(thread):
        raise ValueError('原 CLI 错误状态仍有待处理任务或状态未知，请先在原客户端处理')
    page = request('thread/turns/list', {'threadId': identifier, 'limit': 1, 'sortDirection': 'desc'})
    rows = page.get('data') if isinstance(page, dict) else None
    if (not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict)
            or not isinstance(rows[0].get('id'), str) or not rows[0]['id']
            or rows[0].get('status') not in ('completed', 'interrupted', 'failed')
            or type(rows[0].get('completedAt')) is not int):
        raise ValueError('原 CLI 错误尚未确认结束，请先在原客户端处理')
    # Thread has no requests field in 0.164. The terminal timestamp plus native
    # status (which prioritizes approval/input) is evidence, not a missing list.
    current = request('thread/read', {'threadId': identifier, 'includeTurns': False})['thread']
    if not settled(current):
        raise ValueError('原 CLI 会话状态已变化，请重新检查')


def check_release(request, identifier, unsubscribed=False):
    """Verify unload without resuming or repeating an unsubscribe."""
    thread = request('thread/read', {'threadId': identifier, 'includeTurns': False})['thread']
    if thread.get('id') != identifier:
        raise ValueError('原 CLI 会话身份已变化，未核对新的连接')
    status = (thread.get('status') or {}).get('type')
    if status == 'notLoaded':
        return {'status': 'released', 'notice': '原生会话已卸载，可在电脑端继续'}
    if status not in ('idle', 'active', 'systemError'):
        raise ValueError('无法确认原 CLI 会话状态')
    return {'status': 'detached' if unsubscribed else 'unknown',
            'notice': '网关已取消订阅；原生会话仍在保留，尚未确认其他执行器可以接管' if unsubscribed
                      else '原生会话仍已加载，无法据此确认此前释放结果；未重放操作'}


def unsubscribe_thread(request, identifier):
    result = request('thread/unsubscribe', {'threadId': identifier})
    if result.get('status') not in ('unsubscribed', 'notSubscribed', 'notLoaded'):
        raise ValueError('原 CLI 未确认取消订阅，请核对会话；不会自动重放')
    try:
        verified = check_release(request, identifier, unsubscribed=True)
    except Exception:
        verified = {'status': 'detached', 'notice': '网关已确认取消订阅；原生卸载状态暂时无法读取，请核对结果'}
    return {**verified, 'unsubscribed': True}


def release_thread(request, identifier, stop=False, *, allow_terminal_error=False):
    original_request, mutated = request, False
    def request(method, params):
        nonlocal mutated
        if method in ('thread/goal/set', 'turn/interrupt', 'thread/backgroundTerminals/clean', 'thread/unsubscribe'):
            mutated = True
        try:
            return original_request(method, params)
        except Exception as error:
            if not mutated:
                error.release_result = {'status': 'failed', 'notSent': True, 'error': str(error)}
            raise

    def state():
        thread = request('thread/read', {'threadId': identifier, 'includeTurns': False})['thread']
        status = (thread.get('status') or {}).get('type')
        if thread.get('id') != identifier or status not in ('idle', 'active', 'notLoaded', 'systemError'):
            raise ReleaseNotSent('无法确认原 CLI 会话状态，未释放连接')
        if status == 'systemError':
            if not allow_terminal_error:
                raise ReleaseNotSent('当前 CLI 版本的错误终态尚未核验，未释放连接')
            try:
                check_terminal_system_error(request, thread)
            except ValueError as error:
                raise ReleaseNotSent(error) from error
        return thread, status

    thread, status = state()
    if status != 'notLoaded':
        tasks = request('thread/backgroundTerminals/list', {'threadId': identifier, 'limit': 1})
        if not isinstance(tasks.get('data'), list):
            raise ReleaseNotSent('无法确认原 CLI 后台任务，未释放连接')
        try:
            result = request('thread/goal/get', {'threadId': identifier})
            goal = result.get('goal')
            if status == 'systemError' and ('goal' not in result or goal is not None and (
                    not isinstance(goal, dict) or goal.get('status') not in GOAL_STATUSES)):
                raise ReleaseNotSent('无法确认原 CLI 目标状态，未释放连接')
        except Exception as error:
            if not isinstance(error, AppServerUnsupported) and getattr(error, 'rpc_error', {}).get('code') != -32601:
                raise
            goal = None
        if goal and goal.get('status') == 'active':
            if not stop:
                return {'status': 'waiting'}
            paused = request('thread/goal/set', {'threadId': identifier, 'status': 'paused'}).get('goal')
            if not paused or paused.get('status') != 'paused':
                raise ValueError('原生目标暂停未确认，尚未释放连接')
        busy = status == 'active' or bool(thread.get('requests') or tasks['data'] or tasks.get('nextCursor'))
        if busy and not stop:
            return {'status': 'waiting'}
        if busy:
            from bridge.features.sessions.create import stop_archive_runtime
            try:
                stop_archive_runtime(request, [identifier])
            except Exception as error:
                if not mutated:
                    error.release_result = {'status': 'failed', 'notSent': True, 'error': str(error)}
                raise
    return unsubscribe_thread(request, identifier)
