"""Bounded forced exit of an explicit, freshly validated target set."""
import os
from pathlib import Path
import sys
import time

from bridge.platforms import desktop

CHANGED = '客户端进程已变化或无法核对，已停止强制结束；请重新扫描后重试'
PROTECTED = '所选应用与网关或其启动进程重叠，不能强制结束'


def force_stop(executable, commands):
    if sys.platform == 'darwin':
        from bridge.platforms.macos.force_exit import Native
    elif sys.platform == 'linux':
        from bridge.platforms.linux.force_exit import Native
    else:
        raise ValueError('此系统不支持客户端强制结束')
    executable = str(Path(executable).resolve())
    native, held = Native(), {}
    deadline = time.monotonic() + 20
    try:
        protected, images, pid = set(), {str(Path(sys.executable).resolve())}, os.getpid()
        while pid > 1 and pid not in protected:
            protected.add(pid)
            identity = native.identity(pid)
            images.add(identity['image'])
            pid = identity['parent']
        if executable in images or protected.intersection(commands):
            raise ValueError(PROTECTED)

        def verify(pid, expected=None):
            identity = native.identity(pid)
            if (identity['pid'] != pid or identity['uid'] != os.getuid() or identity['ruid'] != os.getuid()
                    or identity['image'] != executable):
                raise ValueError(CHANGED)
            if expected and (identity['birth'] != expected['birth'] or identity['image'] != expected['image']):
                raise ValueError(CHANGED)
            return identity

        def current():
            rows = desktop(sys.platform).processes(lambda image: str(Path(image).resolve()) == executable)
            if set(rows) - set(commands):
                raise ValueError(CHANGED)
            latest = desktop(sys.platform).commands(rows)
            active = []
            for target in set(held) - set(rows):
                try:
                    identity = native.identity(target)
                except ProcessLookupError:
                    continue
                if (not identity['zombie'] and (identity['birth'] == held[target][1]['birth'] or
                        identity['image'] == executable and identity['uid'] == os.getuid())):
                    raise ValueError(CHANGED)  # An omitted live process is not success.
            for target in rows:
                if latest.get(target) != commands[target]:
                    raise ValueError(CHANGED)
                try:
                    identity = verify(target, held[target][1] if target in held else None)
                except ProcessLookupError:
                    continue
                if not identity['zombie']:
                    active.append(target)
            return active

        # Bind every *known* target independently of a possibly incomplete
        # inventory. The following command read is bracketed by this binding
        # and verify(expected), so PID reuse cannot attach it to a new process.
        for pid in commands:
            try:
                identity = verify(pid)
                held[pid] = (native.open(identity), identity)
            except ProcessLookupError:
                pass  # An independently verified natural exit is harmless.
        for index, pid in enumerate(current()):
            if index and pid not in current():
                continue
            if pid not in held:
                raise ValueError(CHANGED)
            verify(pid, held[pid][1])
            try:
                native.terminate(held[pid][0])
            except ProcessLookupError:
                pass
        while current():
            if time.monotonic() >= deadline:
                raise ValueError('等待客户端强制结束超时，请重新扫描并检查电脑端状态')
            time.sleep(.1)
    except OSError:
        raise ValueError('未能结束全部客户端进程，请检查权限或在电脑端处理后重试') from None
    finally:
        for handle, _ in held.values():
            native.close(handle)
