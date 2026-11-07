"""Invoke the bundled local ZCode window connector only on explicit actions."""
import json
import subprocess
import sys

from bridge.resources import project_root


def supports_window_send():
    # ZCode 3.17.2 reuses the *same* AX composer and send button across native
    # session IDs. Only advertise writes once a session-bound route is proven.
    return False


def operation(action, executable, sid, title, text='', *, expected_pid=None):
    if action != 'resume':
        raise ValueError('不支持此 ZCode 窗口操作')
    root = project_root()
    helper = (root if getattr(sys, 'frozen', False) else root/'dist')/'client-helpers/zcode-bridge-helper'
    try:
        result = subprocess.run([str(helper)], input=json.dumps(
            dict(action=action, executable=str(executable), sessionId=sid, title=title,
                 text=text, expectedPid=expected_pid), ensure_ascii=False),
            capture_output=True, text=True, timeout=14)
        reply = json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        raise ValueError('ZCode 原窗口未返回确认，请先查看该会话再决定是否重试') from None
    if reply.get('status') not in ('ready', 'submitted'):
        raise ValueError(reply.get('reason') or '无法操作 ZCode 原窗口')
    return reply
