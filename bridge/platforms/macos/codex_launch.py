"""One user GUI-session variable; account and runtime policy stay with callers."""
import subprocess
import os
import plistlib
import stat
import tempfile
from pathlib import Path

KEY = 'CODEX_CLI_PATH'
LABEL = 'io.github.try2love.codexmobilebridge.codex-launch'


def get_environment():
    value = subprocess.run(['/bin/launchctl', 'getenv', KEY], capture_output=True,
                           text=True, timeout=5)
    if value.returncode not in (0, 3):
        raise ValueError('无法读取 Codex 的普通启动配置')
    return value.stdout.rstrip('\n') or None


def set_environment(launcher):
    subprocess.run(['/bin/launchctl', 'setenv', KEY, str(launcher)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
    if get_environment() != str(launcher):
        raise ValueError('Codex 普通启动配置尚未生效')


def install_login_environment(launcher):
    directory = Path.home()/'Library/LaunchAgents'
    directory.mkdir(parents=True, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise ValueError('无法核验 Codex 登录启动配置目录')
    target = directory/(LABEL+'.plist')
    value = {'Label': LABEL, 'ProgramArguments': [str(launcher), '--register-environment'],
             'RunAtLoad': True, 'LimitLoadToSessionType': 'Aqua', 'ProcessType': 'Background'}
    if target.exists() or target.is_symlink():
        previous = target.lstat()
        if not stat.S_ISREG(previous.st_mode) or previous.st_uid != os.getuid() or previous.st_mode & 0o077:
            raise ValueError('Codex 登录启动配置身份已变化')
        old = plistlib.loads(target.read_bytes())
        if not isinstance(old, dict):
            raise ValueError('已有其他 Codex 登录启动配置，保留原配置')
        arguments = old.get('ProgramArguments') or []
        if (old.get('Label') != LABEL or not isinstance(arguments, list) or len(arguments) != 2
                or not all(isinstance(argument, str) for argument in arguments)
                or arguments[1] != '--register-environment'
                or Path(arguments[0]).parent.parent != Path(launcher).parent.parent):
            raise ValueError('已有其他 Codex 登录启动配置，保留原配置')
        if old == value:
            return
    fd, temporary = tempfile.mkstemp(dir=directory, prefix=LABEL+'-')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(plistlib.dumps(value))
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
