"""Read-only native identity and loopback checks for Antigravity language servers."""
import configparser
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shlex
import sys
import subprocess
from functools import lru_cache

from bridge.platforms.electron import package_manifest


BUNDLE_IDS = {'com.google.antigravity': 'app', 'com.google.antigravity-ide': 'ide'}
CLI_RELEASES = ('https://antigravity-cli-auto-updater-974169037036.us-central1.run.app',
                'https://storage.googleapis.com/antigravity-public/antigravity-cli')


def login_processes():
    """Fresh process inventory covering frontends and servers sharing the login."""
    names = ('agy', 'agy.exe', 'antigravity', 'antigravity.exe', 'antigravity ide',
             'antigravity ide.exe', 'antigravity-ide')
    def relevant(image):
        path = Path(image)
        if path.name.casefold() in names:
            return True
        if sys.platform == 'darwin':
            bundle = next((parent for parent in path.parents if parent.suffix == '.app'), None)
            if bundle is not None:
                try:
                    return plistlib.loads((bundle/'Contents/Info.plist').read_bytes()).get('CFBundleIdentifier') in BUNDLE_IDS
                except FileNotFoundError:
                    return 'antigravity' in bundle.name.casefold()
        return any('antigravity' in part.casefold() for part in path.parts) and path.name.startswith('language_server')
    if sys.platform == 'darwin':
        from bridge.platforms.macos.desktop import _process_rows
        return [pid for pid, image in _process_rows() if relevant(image)]
    if sys.platform == 'linux':
        result = []
        for path in Path('/proc').iterdir():
            if not path.name.isdigit():
                continue
            try:
                if path.stat().st_uid != os.getuid():
                    continue
                # A running executable can be unlinked by an updater. Its
                # kernel symlink remains evidence of a live credential writer.
                raw_image = os.readlink(path/'exe')
                image = raw_image.removesuffix(' (deleted)')
                pid = int(path.name)
                if relevant(image) or _trusted_image(raw_image, pid=pid):
                    result.append(pid)
            except FileNotFoundError:
                try:
                    state = (path/'stat').read_text().rsplit(')', 1)[1].split()[0]
                except FileNotFoundError:
                    continue
                if state not in ('Z', 'X'):
                    raise ValueError('无法核验同用户进程，请稍后重试账号操作') from None
            except PermissionError:
                # Non-dumpable login helpers such as sd-pam are not clients.
                # Keep an unreadable native candidate blocked, not silently idle.
                try:
                    name = (path/'comm').read_text().strip().casefold()
                except FileNotFoundError:
                    continue
                if not name or name in names or name.startswith(('antigravity', 'language_server')):
                    raise ValueError('无法核验 Antigravity 进程，请先在原客户端处理') from None
        return result
    if sys.platform == 'win32':
        # Credential Manager is per user, including CLI processes in other sessions.
        script = ("$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value; "
                  "$ids=@(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {"
                  "$_.Name -match '^(agy|antigravity|antigravity ide|antigravity-ide)\\.exe$' -or "
                  "($_.Name -match '^language_server.*\\.exe$' -and (!$_.ExecutablePath -or $_.ExecutablePath -match 'antigravity'))"
                  "} | ForEach-Object {$owner=Invoke-CimMethod -InputObject $_ -MethodName GetOwnerSid -ErrorAction Stop; "
                  "if($owner.ReturnValue -ne 0){throw 'process owner unavailable'}; "
                  "if($owner.Sid -eq $sid){[int]$_.ProcessId}}); ConvertTo-Json -Compress -InputObject $ids")
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                                 "$ErrorActionPreference='Stop'; "+script],
                                capture_output=True, text=True, check=True, timeout=15,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        return json.loads(result.stdout or '[]')
    raise ValueError('当前系统无法核验 Antigravity 进程')


@lru_cache(maxsize=8)
def _cli_metadata(path, identity, metadata_identity):
    path = Path(path)
    row = json.loads((path.parent/'agy.version.json').read_text(encoding='utf-8'))
    if (row.get('schemaVersion') != 1 or row.get('releaseBaseUrl') not in CLI_RELEASES or
            row.get('size') != identity[2] or row.get('ino') not in (0, identity[1]) or
            abs(row.get('mtimeMs', 0) - identity[3] / 1_000_000) > 2000 or
            not isinstance(row.get('sha512'), str) or not re.fullmatch('[a-fA-F0-9]{128}', row['sha512'])):
        return None
    digest = hashlib.sha512()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    if (digest.hexdigest() != row['sha512'].lower() or _file_identity(path) != identity or
            _file_identity(path.parent/'agy.version.json') != metadata_identity):
        return None
    return {'application': str(path), 'executable': str(path), 'version': str(row.get('version') or ''),
            'surface': 'cli'}


def _file_identity(path):
    value = path.stat()
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def _desktop_entry_surface(raw, *, member=None):
    """统一校验 AppImage SquashFS 或已挂载 FUSE 根目录中的 .desktop 元数据。"""
    if not raw or raw.get('type') != 'Application':
        return None
    command = shlex.split(raw.get('exec', ''))
    if not command or command[0] not in ('AppRun', 'antigravity', 'antigravity-ide'):
        return None
    version = raw.get('x-appimage-version', '')
    if len(version) > 128 or any(ord(char) < 32 for char in version):
        return None
    name_cf = raw.get('name', '').casefold()
    wm_cf = raw.get('startupwmclass', '').casefold()
    specs = (
        ('antigravity.desktop', ('antigravity',), 'app'),
        ('antigravity-ide.desktop', ('antigravity ide', 'antigravity-ide'), 'ide'),
    )
    for desktop_name, expected_names, surface in specs:
        if member and member != desktop_name:
            continue
        if name_cf in expected_names and wm_cf in (*expected_names, 'antigravity'):
            return {'surface': surface, 'version': version}
    return None


def _read_mounted_desktop(mount_root, desktop_name):
    """从已挂载的只读目录读取 .desktop，无需依赖外部 unsquashfs 二进制。"""
    target = mount_root/desktop_name
    try:
        if not target.is_file() or target.is_symlink():
            return None
        config = configparser.ConfigParser(interpolation=None)
        config.read_string(target.read_text(encoding='utf-8'))
        entry = {k.lower(): v for k, v in config['Desktop Entry'].items()}
        return _desktop_entry_surface(entry, member=desktop_name)
    except (OSError, ValueError, KeyError, configparser.Error, UnicodeError):
        return None


def _unpacked_installation(path):
    """识别普通解包桌面目录（同时兼容 resources/app/product.json 与 Electron app.asar）。"""
    product_file = path.parent/'resources/app/product.json'
    if product_file.is_file():
        product = json.loads(product_file.read_text(encoding='utf-8'))
        name = product.get('applicationName')
        if name in ('antigravity', 'antigravity-ide') and path.stem.casefold() in ('antigravity', 'antigravity ide', 'antigravity-ide'):
            return {'application': str(path), 'executable': str(path),
                    'version': str(product.get('ideVersion') or ''),
                    'surface': 'ide' if name.endswith('-ide') else 'app'}
    archive = path.parent/'resources/app.asar'
    if archive.is_file() and archive.resolve() == archive and path.stem.casefold() in ('antigravity', 'antigravity ide', 'antigravity-ide'):
        archive_identity = _file_identity(archive)
        manifest = package_manifest(archive)
        if _file_identity(archive) != archive_identity:
            return None
        name = manifest.get('name')
        product_name = str(manifest.get('productName') or '')
        if name in ('antigravity', 'antigravity-ide') or product_name.casefold() in ('antigravity', 'antigravity ide'):
            surface = 'ide' if (name == 'antigravity-ide' or 'ide' in product_name.casefold()) else 'app'
            return {'application': str(path), 'executable': str(path),
                    'version': str(manifest.get('version') or ''),
                    'surface': surface}
    return None


def _fuse_installation(appimage_path, mount_root):
    """验证 FUSE 只读挂载目录中的 Antigravity 主程序或桌面元数据（免 unsquashfs）。"""
    for member, payload_name in (('antigravity.desktop', 'antigravity'), ('antigravity-ide.desktop', 'antigravity-ide')):
        exe = mount_root/payload_name
        if exe.is_file() and os.access(exe, os.X_OK):
            unpacked = _unpacked_installation(exe)
            if unpacked:
                return unpacked
        desktop_info = _read_mounted_desktop(mount_root, member)
        if desktop_info:
            return {'application': str(appimage_path), 'executable': str(appimage_path), **desktop_info}
    return None


def _appimage_metadata(path):
    """核验 Linux Type 2 AppImage 内置桌面元数据是否属于官方 Antigravity。"""
    from bridge.platforms.linux import appimage
    if not os.access(path, os.X_OK) or not appimage.is_type2(path):
        return None
    for member in ('antigravity.desktop', 'antigravity-ide.desktop'):
        raw = appimage.metadata(path, member)
        verified = _desktop_entry_surface(raw, member=member)
        if verified:
            return {'application': str(path), 'executable': str(path),
                    **verified, 'packageType': 'appimage',
                    'installationIdentity': list(appimage.file_identity(path))}
    return None


def _extract_run_installation(path, image_identity):
    """Discover an AppImage using its already-running, same-user extract-and-run payload."""
    from bridge.platforms.linux import appimage
    surfaces = {}
    try:
        roots = sorted((row for row in Path('/proc').iterdir() if row.name.isdecimal()),
                       key=lambda row: int(row.name))
    except OSError:
        return None
    for root in roots:
        try:
            before = (root/'stat').read_text().rsplit(')', 1)[1].split()
            if before[0] == 'Z' or root.stat().st_uid != os.getuid():
                continue
            raw_image = os.readlink(root/'exe')
            clean_path = _clean_proc_path(raw_image)
            if not raw_image.endswith(' (deleted)') or clean_path is None:
                continue
            if not _trusted_appimage_runtime(raw_image, int(root.name)):
                continue
            unpacked = _unpacked_surface(clean_path)
            after = (root/'stat').read_text().rsplit(')', 1)[1].split()
            if (before[19] != after[19] or root.stat().st_uid != os.getuid() or
                    not unpacked or appimage.file_identity(path) != image_identity):
                continue
            surfaces.setdefault((unpacked, before[19]), clean_path)
        except (OSError, ValueError, TypeError, IndexError):
            continue
    product_surfaces = {surface for surface, _ in surfaces}
    if len(product_surfaces) != 1:
        return None
    surface = next(iter(product_surfaces))
    return {'application': str(path), 'executable': str(path), 'version': '', 'surface': surface,
            'packageType': 'appimage', 'installationIdentity': list(image_identity)}


def installation(application, *, platform=None):
    """Inspect official bundles or the CLI installer's matching binary metadata."""
    platform, path = platform or sys.platform, Path(application).expanduser().resolve()
    try:
        if path.name.casefold() in ('agy', 'agy.exe') and path.is_file():
            return _cli_metadata(str(path), _file_identity(path), _file_identity(path.parent/'agy.version.json'))
        if platform == 'darwin':
            from bridge.platforms.macos.discovery import candidate
            for identifier, surface in BUNDLE_IDS.items():
                row = candidate(path, identifier)
                if row:
                    return {**row, 'surface': surface}
        elif path.is_file():
            if platform == 'linux':
                from bridge.platforms.linux import appimage
                if os.access(path, os.X_OK) and appimage.is_type2(path):
                    identity = appimage.file_identity(path)
                    # 若当前已有 FUSE 挂载运行，优先直读只读挂载目录元信息，避免无 unsquashfs 时冷启动检测失败
                    runtimes = []
                    try:
                        for payload_name in ('antigravity', 'antigravity-ide'):
                            runtimes.extend(appimage.runtime_candidates(path, payload_name))
                    except OSError:
                        # Installation identity is still available from the
                        # image; a missing/unreadable proc inventory must not
                        # prevent its metadata-based installation discovery.
                        runtimes = []
                    unique_runtimes = list({(row.get('pid'), row.get('executable')): row for row in runtimes}.values())
                    if len(unique_runtimes) == 1:
                        runtime_exe = Path(unique_runtimes[0]['executable'])
                        fuse_info = _fuse_installation(path, runtime_exe.parent)
                        if fuse_info and appimage.file_identity(path) == identity:
                            return {**fuse_info, 'application': str(path), 'executable': str(path),
                                    'packageType': 'appimage', 'installationIdentity': list(identity)}
                    appimage_row = _appimage_metadata(path)
                    if appimage_row:
                        return appimage_row
                    extract_row = _extract_run_installation(path, identity)
                    if extract_row:
                        return extract_row
                    return None
            return _unpacked_installation(path)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        return None
    return None


def profile_lease(path):
    """Keep a nonblocking cross-process lock until its returned stream closes."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    stream = path.open('a+b')
    try:
        if os.name == 'nt':
            import msvcrt
            if path.stat().st_size == 0:
                stream.write(b'0')
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        stream.close()
        raise ValueError('此 Antigravity 用户资料已由另一网关接入，请先停用原接入') from None
    return stream


def _proc_env(pid, keys=(b'APPIMAGE', b'APPDIR')):
    """Read only bounded launcher evidence from a stable same-user process."""
    result = {}
    root = Path('/proc')/str(pid)
    try:
        before_stat = root.stat()
        before = (root/'stat').read_text().rsplit(')', 1)[1].split()
        if before_stat.st_uid != os.getuid() or before[0] == 'Z':
            return result
        with (root/'environ').open('rb') as stream:
            raw = stream.read(65537)
        after = (root/'stat').read_text().rsplit(')', 1)[1].split()
        if (len(raw) > 65536 or before[19] != after[19] or
                root.stat().st_uid != os.getuid()):
            return result
    except (OSError, ValueError, IndexError):
        return result
    for entry in raw.split(b'\x00'):
        if b'=' not in entry:
            continue
        key, value = entry.split(b'=', 1)
        if key in keys:
            result[key.decode('ascii')] = value.decode('utf-8', 'surrogateescape')
    return result


def _clean_proc_path(raw):
    text = str(raw).removesuffix(' (deleted)')
    if not text or '..' in Path(text).parts:
        return None
    candidate = Path(os.path.normpath(text))
    return candidate if candidate.is_absolute() else None


def _parent_before(path, *markers):
    """按路径层级定位首个标记目录（如 resources/extensions）的上级根目录。"""
    for marker in markers:
        if marker in path.parts:
            idx = path.parts.index(marker)
            if idx > 0:
                yield path.parents[len(path.parts) - 1 - idx]


def _trusted_appimage_runtime(image, pid):
    """核验 Linux AppImage 解压或 FUSE 运行时派生出的 language_server 进程。"""
    if sys.platform != 'linux' or pid is None:
        return False
    clean_path = _clean_proc_path(image)
    if clean_path is None:
        return False
    if not (clean_path.name.startswith('language_server')
            and ('resources' in clean_path.parts or 'extensions' in clean_path.parts)):
        return False

    proc_root = Path('/proc')/str(pid)
    try:
        proc_stat = (proc_root/'stat').read_text().rsplit(')', 1)[1].split()
        if proc_stat[0] == 'Z' or proc_root.stat().st_uid != os.getuid() or os.readlink(proc_root/'exe') != str(image):
            return False
    except (OSError, ValueError, IndexError):
        return False
    def process_unchanged():
        try:
            after = (proc_root/'stat').read_text().rsplit(')', 1)[1].split()
            return (proc_stat[19] == after[19] and proc_root.stat().st_uid == os.getuid() and
                    os.readlink(proc_root/'exe') == str(image))
        except (OSError, ValueError, IndexError):
            return False
    env = _proc_env(pid)
    appimage_path = None
    image_identity = None
    if 'APPIMAGE' in env:
        try:
            appimage_path = Path(env['APPIMAGE']).resolve(strict=True)
            from bridge.platforms.linux import appimage
            if not appimage_path.is_file() or not os.access(appimage_path, os.X_OK) or not appimage.is_type2(appimage_path):
                return False
            image_identity = appimage.file_identity(appimage_path)
        except (OSError, RuntimeError):
            return False
    appdir_path = None
    if 'APPDIR' in env:
        appdir_path = _clean_proc_path(env['APPDIR'])
        if (appdir_path is None or appdir_path.is_symlink() or not appdir_path.is_dir()
                or not clean_path.is_relative_to(appdir_path)):
            return False

    # 确定解包根目录：language_server 位于 <root>/resources/... 或 APPDIR 内
    payload_root = appdir_path or next(_parent_before(clean_path, 'resources', 'extensions'), None)
    if payload_root is None:
        return False

    # 1. APPIMAGE_EXTRACT_AND_RUN=1 模式：沿同用户父进程链追溯，确保解包进程链直达已签章核验的 AppImage 宿主
    seen = {int(pid)}
    try:
        cursor_root = Path('/proc')/str(pid)
        if cursor_root.stat().st_uid != os.getuid():
            return False
        parent = int((cursor_root/'stat').read_text().rsplit(')', 1)[1].split()[1])
    except (OSError, ValueError, IndexError):
        parent = 0
    for _ in range(16):
        if parent <= 1 or parent in seen:
            break
        seen.add(parent)
        anc_root = Path('/proc')/str(parent)
        try:
            if anc_root.stat().st_uid != os.getuid():
                break
            before = (anc_root/'stat').read_text().rsplit(')', 1)[1].split()
            if before[0] == 'Z':
                break
            anc_raw = os.readlink(anc_root/'exe')
            anc_path = _clean_proc_path(anc_raw)
            if anc_path is None:
                break
            def ancestor_unchanged():
                after = (anc_root/'stat').read_text().rsplit(')', 1)[1].split()
                return (before[19] == after[19] and anc_root.stat().st_uid == os.getuid() and
                        os.readlink(anc_root/'exe') == anc_raw)
            if not anc_raw.endswith(' (deleted)'):
                resolved = anc_path.resolve(strict=True)
                if appimage_path is not None and resolved == appimage_path:
                    if _appimage_metadata(resolved):
                        return process_unchanged() and ancestor_unchanged()
                    # With APPIMAGE_EXTRACT_AND_RUN, the temporary tree is a
                    # sibling product image removed during startup. Trust it
                    # only when the executing Type 2 image is the exact
                    # APPIMAGE file and that process tree contains its APPDIR
                    # with a valid Antigravity app.asar manifest.
                    if (appdir_path is not None and image_identity is not None and
                            clean_path.is_relative_to(appdir_path) and
                            _unpacked_surface(clean_path) is not None and
                            appimage.file_identity(resolved) == image_identity):
                        return process_unchanged() and ancestor_unchanged()
            # 中间父进程必须留在同一解包根目录内，防止跨进程树伪造来源
            if not anc_path.is_relative_to(payload_root):
                break
            after = (anc_root/'stat').read_text().rsplit(')', 1)[1].split()
            if (before[19] != after[19] or anc_root.stat().st_uid != os.getuid() or
                    os.readlink(anc_root/'exe') != anc_raw):
                break
            parent = int(after[1])
        except (OSError, RuntimeError, ValueError, IndexError):
            break

    # 2. FUSE 挂载模式：核验 APPDIR 为同用户只读 FUSE 挂载，且存在绑定该 AppImage 与 pipe 的真实运行时
    if appimage_path is not None and appdir_path is not None:
        from bridge.platforms.linux import appimage
        try:
            if appdir_path in appimage._mounts():
                # 优先直接校验已挂载只读目录中的资源/桌面元信息，缺 unsquashfs 时同样可用
                info = _fuse_installation(appimage_path, appdir_path) or _appimage_metadata(appimage_path)
                if info:
                    payload_name = 'antigravity-ide' if info['surface'] == 'ide' else 'antigravity'
                    runtimes = appimage.runtime_candidates(appimage_path, payload_name)
                    if (any(Path(row['executable']).is_relative_to(appdir_path) for row in runtimes) and
                            appimage.file_identity(appimage_path) == image_identity and process_unchanged()):
                        return True
        except (OSError, ValueError):
            return False
    return False


def _unpacked_surface(image):
    """Return one app.asar-verified unpacked product surface for a runtime path."""
    surfaces = set()
    for root in _parent_before(image, 'resources', 'extensions'):
        for name in ('antigravity', 'Antigravity', 'antigravity-ide', 'Antigravity IDE'):
            candidate = root/name
            try:
                info = (_unpacked_installation(candidate) if candidate.is_file() and
                        not candidate.is_symlink() and os.access(candidate, os.X_OK) else None)
                if info and Path(info['executable']).resolve(strict=True).is_relative_to(root.resolve(strict=True)):
                    surfaces.add(info['surface'])
            except (OSError, ValueError, TypeError, KeyError):
                continue
    return next(iter(surfaces)) if len(surfaces) == 1 else None


def _unpacked_root_verified(image):
    """Verify an unpacked installation without requiring a conventional root directory name."""
    return _unpacked_surface(image) is not None


def _trusted_directory_image(image):
    raw = str(image)
    if raw.endswith(' (deleted)'):
        return False
    image = Path(raw).resolve()
    if image.name.casefold() in ('agy', 'agy.exe'):
        return installation(image) is not None
    if not image.name.startswith('language_server'):
        return False
    if sys.platform == 'darwin':
        bundle = next((p for p in image.parents if p.suffix == '.app'), None)
        if bundle is None:
            return False
        try:
            info = plistlib.loads((bundle/'Contents/Info.plist').read_bytes())
        except (OSError, ValueError):
            return False
        return info.get('CFBundleIdentifier') in BUNDLE_IDS
    # Packaged native servers only; never accept a process name or PATH alias.
    parts = {part.casefold() for part in image.parts}
    if ('antigravity' in parts or 'antigravity-ide' in parts) and ('resources' in parts or 'extensions' in parts):
        return True
    return _unpacked_root_verified(image)


def _trusted_image(image, *, pid=None):
    return _trusted_directory_image(image) or _trusted_appimage_runtime(image, pid)


def _identity(pid):
    if sys.platform == 'win32':
        from bridge.platforms.windows.zcode_pipe import identity
        return identity(pid)  # Same user, interactive session, birth and image.
    if sys.platform == 'darwin':
        from bridge.platforms.macos.force_exit import Native
    else:
        from bridge.platforms.linux.force_exit import Native
    native = Native()
    row = (native.identity(pid, allow_deleted=True) if sys.platform == 'linux'
           else native.identity(pid))
    if row['uid'] != os.getuid() or row['zombie']:
        raise PermissionError('Antigravity 进程用户不匹配')
    return row


def _candidates():
    if sys.platform == 'win32':
        from bridge.platforms.windows.desktop import _rows
        from bridge.platforms.windows.zcode_pipe import process_args
        for row in _rows():
            if _trusted_image(row['ExecutablePath']):
                pid = int(row['ProcessId'])
                yield pid, process_args(pid)
    elif sys.platform == 'darwin':
        from bridge.platforms.macos.desktop import _process_rows, commands
        for pid, image in _process_rows():
            if _trusted_image(image):
                # Flags consumed below contain no spaces. The executable comes
                # from the kernel image, never from this display command line.
                yield pid, shlex.split(commands([pid]).get(pid, ''))
    elif sys.platform == 'linux':
        from bridge.platforms.linux.desktop import _process_rows
        for path, image in _process_rows(preserve_deleted_marker=True):
            pid = int(path.name)
            if _trusted_image(image, pid=pid):
                yield pid, (path/'cmdline').read_bytes().decode('utf-8', 'replace').rstrip('\0').split('\0')


def _flag(args, name):
    values = []
    for index, value in enumerate(args):
        if value == name and index + 1 < len(args):
            values.append(args[index + 1])
        elif value.startswith(name + '='):
            values.append(value[len(name) + 1:])
    return values[0] if len(values) == 1 else None


def _listener(pid, port, image):
    if sys.platform in ('darwin', 'win32'):
        if sys.platform == 'darwin':
            from bridge.platforms.macos.loopback import listener_identity
        else:
            from bridge.platforms.windows.loopback import listener_identity
        return listener_identity(port, image)['pid'] == pid
    # Linux exposes socket ownership through /proc without running lsof.
    sockets = set()
    for fd in (Path('/proc')/str(pid)/'fd').iterdir():
        try:
            match = re.fullmatch(r'socket:\[(\d+)\]', os.readlink(fd))
            if match:
                sockets.add(match[1])
        except OSError:
            continue
    listeners = []
    for name in ('tcp', 'tcp6'):
        for line in (Path('/proc/net')/name).read_text().splitlines()[1:]:
            fields = line.split()
            address, number = fields[1].split(':')
            if int(number, 16) == port and fields[3] == '0A':
                listeners.append((address, fields[9]))
    return len(listeners) == 1 and listeners[0][0] == '0100007F' and listeners[0][1] in sockets


_PORT_RE = re.compile(r'Language server listening on \w+ port at (\d+) for (?:local )?HTTP\b')
_LOG_HEAD_BYTES = 64 * 1024
_LOG_TAIL_BYTES = 256 * 1024


def _log_ports(log):
    """Extract listening HTTP ports from both log head and tail for large logs."""
    ports = set()
    with log.open('rb') as stream:
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(0)
        if size <= _LOG_HEAD_BYTES + _LOG_TAIL_BYTES:
            chunks = [stream.read(size)]
        else:
            chunks = [stream.read(_LOG_HEAD_BYTES) + stream.readline(4096)]
            stream.seek(max(0, size - _LOG_TAIL_BYTES))
            chunks.append(stream.read(_LOG_TAIL_BYTES))
    for chunk in chunks:
        text = chunk.decode('utf-8', errors='replace')
        ports.update(int(p) for p in _PORT_RE.findall(text))
    return ports


def _csrf_token(pid, args):
    """Use legacy arguments or the same native process's 2.34 launch token."""
    token = _flag(args, '--csrf_token')
    if token:
        return token
    if sys.platform == 'linux':
        return _proc_env(pid, keys=(b'ANTIGRAVITY_SERVER_CSRF_TOKEN',)).get('ANTIGRAVITY_SERVER_CSRF_TOKEN')
    if sys.platform == 'darwin':
        from bridge.platforms.macos.codex_daemon_env import _environment
        try:
            return _environment(pid, ('ANTIGRAVITY_SERVER_CSRF_TOKEN',)).get('ANTIGRAVITY_SERVER_CSRF_TOKEN')
        except (OSError, ValueError):
            return None
    return None


def endpoint(surface, home):
    """Return one same-user, same-surface server, or fail without contacting it."""
    if Path(home).resolve() != Path.home().resolve():
        raise ValueError('Antigravity 本机控制只支持当前系统用户资料')
    data_name = {'app': 'antigravity', 'ide': 'antigravity-ide', 'cli': 'antigravity-cli'}.get(surface)
    if not data_name:
        raise ValueError('Antigravity 来源无效')
    matches = []
    for pid, args in _candidates():
        if _flag(args, '--app_data_dir') != data_name:
            continue
        try:
            before = _identity(pid)
        except (OSError, ValueError):
            continue
        if not _trusted_image(before['image'], pid=before['pid']):
            continue
        token = _csrf_token(before['pid'], args)
        if not token or not re.fullmatch(r'[A-Za-z0-9_-]{16,256}', token):
            continue
        # Read only the matching product's log. A log port alone is never
        # sufficient: the listener must belong to this exact process generation.
        if Path(before['image'].removesuffix(' (deleted)')).name.casefold() in ('agy', 'agy.exe'):
            # The official IDE launches agy --hub with app_data_dir=antigravity.
            # Its hub port serves the UI; use its separately logged native LS
            # HTTP listener and the existing LanguageServerService protocol.
            root = Path(home)/'.gemini'/data_name
            logs = [root/'cli.log', *sorted((root/'log').glob('cli-*.log'), reverse=True)[:3]]
        elif sys.platform == 'darwin':
            logs = [Path(home)/'Library/Logs'/('Antigravity' if surface == 'app' else 'Antigravity IDE')/'language_server.log']
        elif sys.platform == 'win32':
            logs = [Path(os.environ.get('APPDATA', ''))/('Antigravity' if surface == 'app' else 'Antigravity IDE')/'logs/language_server.log']
        else:
            logs = [Path(home)/'.config'/('Antigravity' if surface == 'app' else 'Antigravity IDE')/'logs/language_server.log']
        ports = set()
        for log in logs:
            if log.is_file():
                ports.update(_log_ports(log))
        for port in ports:
            try:
                if 1024 <= port <= 65535 and _listener(pid, port, before['image']) and _identity(pid) == before:
                    matches.append({'address': 'http://127.0.0.1:%d' % port, 'token': token,
                                    'identity': before, 'surface': surface})
            except (OSError, ValueError):
                continue  # An old log port is not a current native endpoint.
    if len(matches) != 1:
        raise ValueError('未找到唯一、同用户且属于此来源的 Antigravity 原生服务；请在电脑打开相应应用')
    return matches[0]


def verify_endpoint(value):
    before = value['identity']
    port = int(value['address'].rsplit(':', 1)[1])
    if (_identity(before['pid']) != before or not _trusted_image(before['image'], pid=before['pid']) or
            not _listener(before['pid'], port, before['image'])):
        raise ValueError('Antigravity 原生服务进程已变化，请重新连接')
