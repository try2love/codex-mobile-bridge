"""Read Type 2 AppImage metadata and bind its FUSE runtime without executing it."""
import configparser
from functools import lru_cache
import os
from pathlib import Path
import re
import selectors
import stat
import struct
import subprocess
import time

PROC = Path('/proc')
METADATA_LIMIT = 64 * 1024


def is_type2(path):
    try:
        with Path(path).open('rb') as stream:head = stream.read(16)
        return len(head) == 16 and head[:4] == b'\x7fELF' and head[8:11] == b'AI\x02' and head[4] in (1, 2) and head[5] in (1, 2)
    except OSError:
        return False


def file_identity(path):
    info = Path(path).stat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('AppImage 必须是普通文件')
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def image_offset(path):
    """The ELF file-backed sections end immediately before the Type 2 filesystem."""
    path = Path(path)
    size = path.stat().st_size
    with path.open('rb') as stream:
        head = stream.read(64)
        if len(head) != 64 or head[:4] != b'\x7fELF' or head[8:11] != b'AI\x02':
            return None
        endian = {1: '<', 2: '>'}.get(head[5])
        if endian is None or head[4] not in (1, 2):
            return None
        wide = head[4] == 2
        offset = struct.unpack_from(endian + ('Q' if wide else 'I'), head, 40 if wide else 32)[0]
        entry_size, count = struct.unpack_from(endian + 'HH', head, 58 if wide else 46)
        expected_size = 64 if wide else 40
        if entry_size != expected_size or not 0 < count <= 4096 or offset < (64 if wide else 52) or offset + count * entry_size > size:
            return None
        stream.seek(offset)
        table = stream.read(count * entry_size)
        end = offset + len(table)
        if len(table) != count * entry_size:
            return None
        for index in range(count):
            row = struct.unpack_from(endian + ('IIQQQQIIQQ' if wide else 'IIIIIIIIII'), table, index * entry_size)
            if row[1] == 8:  # SHT_NOBITS does not occupy bytes in the ELF file.
                continue
            if row[4] + row[5] > size:
                return None
            end = max(end, row[4] + row[5])
        stream.seek(end)
        superblock = stream.read(96)
        if len(superblock) != 96 or superblock[:4] != b'hsqs':
            return None
        if struct.unpack_from('<HH', superblock, 28) != (4, 0):
            return None
        used = struct.unpack_from('<Q', superblock, 40)[0]
        return end if 96 <= used <= size - end else None


def _parser():
    # Do not execute a user PATH shadow while passively scanning installations.
    for name in ('/usr/bin/unsquashfs', '/bin/unsquashfs'):
        path = Path(name).resolve()
        try:
            info = path.stat()
            if (stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022
                    and os.access(path, os.X_OK)):
                return str(path)
        except OSError:
            pass
    return None


def _read_desktop(parser, path, offset, member, *, timeout=4):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*\.desktop', member):
        raise ValueError('AppImage 元数据路径无效')
    process = subprocess.Popen([parser, '-o', str(offset), '-cat', str(path), member],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'})
    data = bytearray()
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise ValueError('AppImage 元数据读取超时')
                chunk = os.read(process.stdout.fileno(), min(16384, METADATA_LIMIT + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > METADATA_LIMIT:
                    raise ValueError('AppImage 元数据过大')
        if process.wait(timeout=max(.01, deadline-time.monotonic())):
            raise ValueError('无法读取 AppImage 元数据')
        return bytes(data)
    finally:
        process.stdout.close()
        if process.poll() is None:
            process.terminate()  # Only this owned, read-only parser; never the App.
            try:process.wait(timeout=1)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=1)


@lru_cache(maxsize=8)
def _metadata(path, identity, parser, member):
    offset = image_offset(path)
    if offset is None:
        return None
    config = configparser.ConfigParser(interpolation=None)
    config.read_string(_read_desktop(parser, path, offset, member).decode('utf-8'))
    entry = config['Desktop Entry']
    if file_identity(path) != identity:
        raise ValueError('AppImage 在扫描期间已变化')
    # Exec is deliberately never used: the upstream entry can include flags
    # such as --no-sandbox. Launch only the explicitly selected original image.
    return dict(entry)


def metadata(application, member):
    path = Path(application)
    try:
        if not path.is_absolute() or not os.access(path, os.X_OK):
            return None
        path = path.resolve(strict=True)
        parser = _parser()
        return _metadata(path, file_identity(path), parser, member) if parser else None
    except (OSError, ValueError, KeyError, configparser.Error, UnicodeError, subprocess.SubprocessError):
        return None


def _process(path):
    if path.stat().st_uid != os.getuid():
        return None
    before = (path/'stat').read_text().rsplit(')', 1)[1].split()
    if before[0] == 'Z':
        return None
    # The kernel already resolves /proc/exe. Do not stat arbitrary FUSE/NFS
    # executables belonging to unrelated processes during this inventory.
    raw_image = os.readlink(path/'exe')
    image = Path(raw_image)
    if not image.is_absolute() or raw_image.endswith(' (deleted)'):
        return None
    after = (path/'stat').read_text().rsplit(')', 1)[1].split()
    if before[19] != after[19] or path.stat().st_uid != os.getuid():
        return None
    return {'pid': int(path.name), 'parent': int(after[1]), 'birth': after[19], 'image': image, 'root': path}


def _fds(process):
    return {target for entry in (process['root']/'fd').iterdir()
            if (target := _readlink(entry)) is not None}


def _readlink(path):
    try:return os.readlink(path)
    except OSError:return None


def _mounts():
    result = set()
    for line in (PROC/'self/mountinfo').read_text().splitlines():
        parts = line.split()
        try:
            separator = parts.index('-')
            if (not parts[separator+1].startswith('fuse.') or 'ro' not in parts[5].split(',')
                    or 'user_id='+str(os.getuid()) not in parts[separator+3].split(',')):
                continue
            mount = re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), parts[4])
            if Path(mount).is_absolute():result.add(Path(mount))
        except (ValueError, IndexError, OSError):
            continue
    return result


def runtime_candidates(application, payload_name):
    """Source image fd → FUSE keeper pipe → original payload; no name-only pairing."""
    if not isinstance(payload_name, str) or Path(payload_name).name != payload_name or payload_name in ('', '.', '..'):
        raise ValueError('AppImage 运行程序名称无效')
    path = Path(application).resolve(strict=True)
    if not is_type2(path):
        return []
    proof = file_identity(path)
    mounts = _mounts()
    holders, payloads = [], []
    for root in PROC.iterdir():
        if not root.name.isdigit():
            continue
        try:
            process = _process(root)
            if not process:
                continue
            image = process['image']
            if image == path:
                fds = _fds(process)
                if '/dev/fuse' in fds and str(path) in fds:
                    holders.append((process, {fd for fd in fds if re.fullmatch(r'pipe:\[\d+\]', fd)}))
            elif image.name.casefold() == payload_name.casefold() and image.parent in mounts:
                payloads.append(process)
        except (OSError, ValueError, IndexError):
            continue
    result = []
    for process in payloads:
        try:
            fds = _fds(process)
            keeper = next((holder for holder, pipes in holders if pipes & fds), None)
            if not keeper:
                continue
            if _process(process['root']) != process or _process(keeper['root']) != keeper:
                continue
            result.append({'pid': process['pid'], 'parent': process['parent'], 'birth': process['birth'], 'executable': str(process['image']),
                           'keeperPid': keeper['pid'], 'keeperBirth': keeper['birth']})
        except (OSError, ValueError, IndexError):
            continue
    roots = {(row['pid'], row['keeperPid']) for row in result}
    return [row for row in result if (row['parent'], row['keeperPid']) not in roots] if file_identity(path) == proof else []
