"""Bounded, read-only pages of a Claude Code JSONL transcript."""

import base64
import json
import os
from pathlib import Path


MAX_PAGE_BYTES = 4 * 1024 * 1024


def merge_live_page(saved, live):
    """Append only the stream tail not already represented by this saved page."""
    def identity(event):
        message = event.get('message')
        identifier = event.get('uuid') or (message.get('id') if isinstance(message, dict) else None)
        return (event.get('type'), identifier) if isinstance(identifier, str) and identifier else None

    persisted = {key: index for index, event in enumerate(saved) if (key := identity(event)) is not None}
    for index in range(len(live) - 1, -1, -1):
        key = identity(live[index])
        if key in persisted:
            # Its live body may be newer. Replace in place so a following saved
            # tool result still sees exactly one preceding call.
            merged = list(saved)
            merged[persisted[key]] = live[index]
            return merged + live[index + 1:]
    return saved + live


def local_command_text(event, chain):
    """Format only the native CLI caveat -> command -> stdout record chain."""
    import re
    previous = dict(chain)
    chain.clear()
    message = event.get('message')
    if (event.get('type') != 'user' or event.get('entrypoint') != 'cli'
            or not isinstance(message, dict) or message.get('role') != 'user'
            or not isinstance(message.get('content'), str)
            or not all(isinstance(event.get(key), str) and event[key] for key in ('uuid', 'promptId', 'sessionId'))):
        return None
    text = message['content'].strip()
    phase, display = None, None
    if event.get('isMeta') is True and re.fullmatch(r'<local-command-caveat>.*</local-command-caveat>', text, re.S):
        phase = 'caveat'
    elif (event.get('parentUuid') == previous.get('uuid') and event['promptId'] == previous.get('promptId')
            and event['sessionId'] == previous.get('sessionId')):
        if previous.get('phase') == 'caveat':
            command = re.fullmatch(r'<command-name>([^<]+)</command-name>\s*<command-message>[^<]*</command-message>\s*<command-args>(.*?)</command-args>', text, re.S)
            if command and command[1].strip():
                phase = 'command'
                display = command[1].strip() + (' ' + command[2].strip() if command[2].strip() else '')
        elif previous.get('phase') == 'command':
            output = re.fullmatch(r'<local-command-stdout>(.*)</local-command-stdout>', text, re.S)
            if output:
                phase, display = 'stdout', output[1]
    if phase:
        chain.update({key: event[key] for key in ('uuid', 'promptId', 'sessionId')}, phase=phase)
    return display


def session_custom_title(event, native):
    """Read the CLI's own title event; never write or infer a title mutation."""
    title = event.get('customTitle')
    if (event.get('type') == 'custom-title' and event.get('sessionId') == native
            and isinstance(title, str) and title.strip()):
        return title.strip()
    return None


def image_event(path, source):
    """Read only an evidenced event; never resolve an image's arbitrary path."""
    if not isinstance(source, dict) or not isinstance(source.get('event'), str):
        raise ValueError('图片引用无效')
    with Path(path).open('rb') as stream:
        while True:
            offset = stream.tell()
            line = stream.readline(32 * 1024 * 1024 + 1)
            if not line:
                break
            if len(line) > 32 * 1024 * 1024:
                raise ValueError('原生图片记录超出预览大小限制')
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            message = event.get('message') if isinstance(event.get('message'), dict) else event
            identity = event.get('uuid') or message.get('id') or 'file:' + str(offset)
            if identity == source['event'] and event.get('type') in ('user', 'assistant'):
                return {**event, 'uuid': identity}
    raise ValueError('原生历史中未找到可预览图片')


def _stamp(stat, fd=None):
    changed = stat.st_ctime_ns
    if os.name == 'nt':
        from bridge.platforms.windows.file_revision import change_time
        changed = change_time(fd)
    return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, changed]


def file_stamp(path):
    if os.name == 'nt':
        with Path(path).open('rb') as stream:
            return _stamp(os.fstat(stream.fileno()), stream.fileno())
    return _stamp(Path(path).stat())


def _cursor(identity, stamp, offset):
    data = json.dumps([identity, stamp, offset], separators=(',', ':')).encode()
    return base64.urlsafe_b64encode(data).decode().rstrip('=')


def page(path, identity, before=None, limit=40):
    """Return chronological native events and a cursor to older complete records.

    A cursor is tied to a precise file version; append, replacement and rollback
    require a new recent page. No partial page is returned after the byte cap.
    """
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Claude 历史分页大小无效')
    path = Path(path)
    with path.open('rb') as stream:
        stamp = _stamp(os.fstat(stream.fileno()), stream.fileno())
        end = stamp[2]
        if before is not None:
            try:
                raw = base64.urlsafe_b64decode(before + '=' * (-len(before) % 4))
                source, version, end = json.loads(raw)
            except (ValueError, TypeError, UnicodeError) as exc:
                raise ValueError('Claude 历史游标无效') from exc
            if source != identity or version != stamp or type(end) is not int or not 0 <= end <= stamp[2]:
                raise ValueError('Claude 历史已变化，请重新加载最近记录')
            if end:
                stream.seek(end - 1)
                if stream.read(1) != b'\n':
                    raise ValueError('Claude 历史游标无效')
        pos, pending, consumed, events, boundary, visible = end, b'', 0, [], end, 0
        has_more = False
        while pos or pending:
            if pos:
                size = min(65536, pos, MAX_PAGE_BYTES - consumed)
                if size <= 0:
                    raise ValueError('Claude 历史单页扫描超过限制，请缩小分页范围')
                pos -= size
                stream.seek(pos)
                block = stream.read(size)
                if len(block) != size:
                    raise ValueError('Claude 历史读取期间发生变化')
                consumed += size
                pending = block + pending
            lines = pending.splitlines(keepends=True)
            keep = lines.pop(0) if pos else b''
            offset = pos + len(keep) + sum(map(len, lines))
            for line in reversed(lines):
                offset -= len(line)
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                kind = event.get('type')
                if kind not in ('user', 'assistant', 'result', 'system'):
                    continue
                if kind in ('user', 'assistant') and not event.get('isSynthetic') and not event.get('isMeta'):
                    visible += 1
                    # Native records without IDs need a stable ID across pages.
                    if not event.get('uuid') and not (isinstance(event.get('message'), dict)
                                                      and event['message'].get('id')):
                        event = {**event, 'uuid': 'file:' + str(offset)}
                events.append(event)
                boundary = offset
                if visible >= limit:
                    has_more = boundary > 0
                    break
            pending = keep
            if has_more:
                break
        if _stamp(os.fstat(stream.fileno()), stream.fileno()) != stamp:
            raise ValueError('Claude 历史读取期间发生变化')
        try:
            current = file_stamp(path)
        except OSError as exc:
            raise ValueError('Claude 历史读取期间发生变化') from exc
        if current != stamp:
            raise ValueError('Claude 历史读取期间发生变化')
        return list(reversed(events)), (_cursor(identity, stamp, boundary) if has_more else None), has_more
