"""Explicit repair of a missing connector; never adopt an unverified plugin."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import tempfile
from urllib.parse import urlsplit
from urllib.request import url2pathname

from bridge.clients.deepseek.adapter import PLUGIN_ID
from bridge.clients.deepseek.setup import MARKER, insertion, verified_directory


def _plain_path(path):
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('接入路径包含链接，请在电脑端检查，尚未修改配置')


def inspect(adapter, home):
    """A matching shape permits a repair preview, not a trusted connection."""
    if adapter.home != Path(home) or adapter.directory == adapter.home:
        raise ValueError('Harness 配置目录已变化，请重新检查')
    patch = adapter.home/'profiles/desktop/cordis.patch.yml'
    _plain_path(patch)
    _plain_path(adapter.directory)
    raw = patch.read_bytes()
    if len(raw) > 1024*1024:
        raise ValueError('Harness 配置过大，请在电脑端检查')
    text = raw.decode('utf-8')
    block = insertion(text)
    if not block or len(PLUGIN_ID.findall(text)) != 1:
        raise ValueError('无法唯一识别旧接入，尚未修改配置')
    entry = block[0]
    try:
        if set(entry) != {'insert'} or not isinstance(entry['insert'], list) or len(entry['insert']) != 1:
            raise ValueError()
        plugin = entry['insert'][0]
        if (set(plugin) != {'id', 'name', 'config'} or plugin['id'] != 'codex-mobile-desktop'
                or set(plugin['config']) != {'configPath'}):
            raise ValueError()
        uri = urlsplit(plugin['name'])
        if uri.scheme != 'file' or uri.netloc or uri.query or uri.fragment:
            raise ValueError()
        source = Path(url2pathname(uri.path))
        config = Path(plugin['config']['configPath'])
        old = config.parent
        if (not source.is_absolute() or not config.is_absolute() or '..' in config.parts or '..' in source.parts
                or config.name != 'connection.json' or source != old/'mobile-host.mjs'
                or old.name != 'deepseek' or old.parent.name != 'desktop-sessions'):
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise ValueError('旧接入形态无法安全恢复，尚未修改配置') from None
    _plain_path(old)
    try:
        old.lstat()
    except FileNotFoundError:
        pass
    else:
        raise ValueError('旧接入目录仍存在，请在电脑端检查，尚未修改配置')
    target_generation = None
    if adapter.directory.exists():
        # A previous confirmed attempt may have prepared all files but failed
        # before replacing the patch. Reuse only its complete native record.
        if not verified_directory(json.loads(adapter._line()[2:]), adapter.home):
            raise ValueError('恢复目标已有无法验证的文件，请在电脑端检查')
        target_generation = [hashlib.sha256((adapter.directory/name).read_bytes()).hexdigest()
                             for name in ('mobile-host.mjs', 'connection.json', 'installation.json')]
    return {'patch': patch, 'raw': raw, 'block': block, 'old': old,
            'generation': hashlib.sha256(raw).hexdigest(), 'directory': str(adapter.directory),
            'targetGeneration': target_generation}


def repair(adapter, expected, check_stopped, write_json):
    current = inspect(adapter, adapter.home)
    if any(current[key] != expected[key] for key in ('generation', 'directory', 'old', 'targetGeneration')):
        raise ValueError('Harness 接入配置已变化，请重新预览')
    check_stopped()
    adapter.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup = adapter.directory/('cordis.patch.before-repair-'+secrets.token_hex(8)+'.yml')
    with backup.open('xb') as stream:
        os.chmod(backup, 0o600)
        stream.write(current['raw'])
        stream.flush()
        os.fsync(stream.fileno())
    if not verified_directory(json.loads(adapter._line()[2:]), adapter.home):
        # Only a new, empty destination is admitted above. A partial failure is
        # left with its private backup and never silently adopted on retry.
        source = adapter.directory/'mobile-host.mjs'
        with source.open('xb') as stream:
            os.chmod(source, 0o600)
            stream.write(Path(__file__).with_name('host.mjs').read_bytes())
        write_json(adapter.directory/'connection.json', {
            'token': secrets.token_urlsafe(32), 'endpoint': str(adapter.directory/'endpoint.json')})
        write_json(adapter.directory/'installation.json', {'home': str(adapter.home), 'line': adapter._line()})
    check_stopped()
    _plain_path(current['patch'])
    if current['patch'].read_bytes() != current['raw']:
        raise ValueError('Harness 配置在恢复期间变化；原配置备份已保留，尚未替换')
    text = current['raw'].decode('utf-8')
    newline = '\r\n' if '\r\n' in text[current['block'][1]:current['block'][2]] else '\n'
    replacement = MARKER+newline+adapter._line()+newline
    result = (text[:current['block'][1]]+replacement+text[current['block'][2]:]).encode('utf-8')
    descriptor, temporary = tempfile.mkstemp(prefix='.mobile-repair-', dir=current['patch'].parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            os.chmod(temporary, stat.S_IMODE(current['patch'].stat().st_mode))
            stream.write(result)
            stream.flush()
            os.fsync(stream.fileno())
        check_stopped()
        latest = inspect(adapter, adapter.home)
        if latest['raw'] != current['raw']:
            raise ValueError('Harness 配置在恢复期间变化，尚未替换')
        os.replace(temporary, current['patch'])
    finally:
        Path(temporary).unlink(missing_ok=True)
    return backup
