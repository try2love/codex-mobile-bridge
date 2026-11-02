"""Release only the calling connection's native thread subscription."""
from bridge.clients.codex.app_server import AppServerUnsupported


def release_thread(request, identifier, stop=False):
    def state():
        thread = request('thread/read', {'threadId': identifier, 'includeTurns': False})['thread']
        status = (thread.get('status') or {}).get('type')
        if thread.get('id') != identifier or status not in ('idle', 'active', 'notLoaded'):
            raise ValueError('无法确认原 CLI 会话状态，未释放连接')
        return thread, status

    thread, status = state()
    if status != 'notLoaded':
        tasks = request('thread/backgroundTerminals/list', {'threadId': identifier, 'limit': 1})
        if not isinstance(tasks.get('data'), list):
            raise ValueError('无法确认原 CLI 后台任务，未释放连接')
        try:
            goal = request('thread/goal/get', {'threadId': identifier}).get('goal')
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
            stop_archive_runtime(request, [identifier])
    result = request('thread/unsubscribe', {'threadId': identifier})
    if result.get('status') not in ('unsubscribed', 'notSubscribed', 'notLoaded'):
        raise ValueError('原 CLI 未确认取消订阅，请核对会话；不会自动重放')
    _, status = state()
    return {'status': 'released' if status == 'notLoaded' else 'detached',
            'notice': '网关已取消订阅并停止自动接入；Codex 原生运行时仍在保留会话，其他执行器可能需要等待原生卸载' if status != 'notLoaded'
                      else '连接已释放，可在电脑端继续'}
