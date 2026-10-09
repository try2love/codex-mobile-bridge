"""POSIX process primitives; callers own identity and idle-state checks."""
import os
import signal
import subprocess


def quit_process(pid):
    os.kill(pid, signal.SIGTERM)


def terminate(pid):
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass


def start(executable, home, environment):
    return subprocess.Popen([str(executable)], cwd=home,
                            env={**os.environ, **environment},
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True)
