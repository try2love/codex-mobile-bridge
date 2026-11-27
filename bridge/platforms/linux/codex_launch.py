"""User desktop entry override for a proven unpacked Electron installation."""
import configparser
import os
from pathlib import Path
import shlex
import shutil
import tempfile

from .discovery import candidate


def supported(application):
    path = Path(application).resolve()
    return path.is_file() and (path.parent/'resources/app.asar').is_file()


def quoted(value):
    # Desktop Entry Exec has its own escaping, not shell quoting. Percent is
    # a field code even inside quotes and must be doubled for literal paths.
    value = str(value).replace('%', '%%')
    return '"'+''.join('\\'+char if char in '\\"`$' else char for char in value)+'"'


def install(application, home, launcher):
    application, home, launcher = Path(application).resolve(), Path(home).resolve(), Path(launcher).resolve()
    if not supported(application):
        raise ValueError('此 Linux 安装布局尚未核验普通启动，请通过网关初始化连接')
    data = Path(os.environ.get('XDG_DATA_HOME') or Path.home()/'.local/share')
    roots = [data, *[Path(value) for value in os.environ.get('XDG_DATA_DIRS', '/usr/local/share:/usr/share').split(':') if value]]
    matches, seen = {}, set()
    for root in roots:
        for path in sorted((root/'applications').glob('*.desktop')):
            if path.name in seen:
                continue
            seen.add(path.name)
            config = configparser.ConfigParser(interpolation=None, strict=True)
            config.optionxform = str
            try:
                config.read(path, encoding='utf-8')
                entry = config['Desktop Entry']
                original = entry.get('X-CMB-Original-Exec') or entry.get('Exec', '')
                arguments = shlex.split(original)
                if not arguments:
                    continue
                executable = arguments[0] if Path(arguments[0]).is_absolute() else shutil.which(arguments[0])
                installation = candidate(Path(executable), 'codex') if executable else None
                if not installation or Path(installation['executable']).resolve() != application:
                    continue
                matches[path.name] = (config, original)
            except (OSError, ValueError, KeyError, configparser.Error):
                continue
    if len(matches) != 1:
        raise ValueError('无法确认唯一的 Codex 桌面启动入口；可继续使用网关初始化连接')
    name, (config, original) = next(iter(matches.items()))
    entry = config['Desktop Entry']
    entry['X-CMB-Original-Exec'] = original
    entry['Exec'] = '/usr/bin/env '+quoted('CODEX_HOME='+str(home))+' '+quoted('CODEX_CLI_PATH='+str(launcher))+' '+original
    # A DBus activation would bypass Exec and silently skip the prepared path.
    if 'DBusActivatable' in entry:
        entry['DBusActivatable'] = 'false'
    directory = data/'applications';directory.mkdir(parents=True, exist_ok=True)
    target = directory/name
    if target.is_symlink():
        raise ValueError('Codex 用户启动入口身份已变化')
    import io
    content = io.StringIO();config.write(content, space_around_delimiters=False)
    content = content.getvalue()
    if target.exists() and target.read_text() == content:
        return
    fd, temporary = tempfile.mkstemp(dir=directory, prefix='cmb-codex-')
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(content);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
