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


def start(executable, home, environment, *, arguments=(), new_instance=False, environment_unset=()):
    inherited = {key: value for key, value in os.environ.items() if key not in environment_unset}
    return subprocess.Popen([str(executable), *arguments], cwd=home,
                            env={**inherited, **environment},
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True)
