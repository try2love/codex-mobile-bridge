"""Separate trusted image artifacts from explicit host-file actions in a chat."""
import hashlib
import ntpath
import os
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname

from bridge.features.sessions.model import ordered_turns, items_array, desktop_file_attachments
from bridge.features.workspace.visualize import reference_candidates, descriptors

MARKDOWN_PATH = re.compile(r'!?\[[^\]\n]*\]\((?:<([^>]+)>|([^\n)]+))\)')
IMAGE_MARKDOWN_PATH = re.compile(r'!\[[^\]\n]*\]\((?:<([^>]+)>|([^\n)]+))\)')
AGENT_KINDS = ('agentMessage', 'assistantMessage', 'planImplementation')
IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}
MEDIA_TYPES = {
    '.wav': 'audio/wav', '.mp3': 'audio/mpeg', '.m4a': 'audio/mp4',
    '.aac': 'audio/aac', '.ogg': 'audio/ogg', '.oga': 'audio/ogg',
    '.opus': 'audio/ogg', '.flac': 'audio/flac', '.aif': 'audio/aiff', '.aiff': 'audio/aiff',
    '.mp4': 'video/mp4', '.m4v': 'video/mp4', '.webm': 'video/webm',
    '.ogv': 'video/ogg', '.mov': 'video/quicktime',
}


def media_type(name):
    return MEDIA_TYPES.get(Path(name).suffix.lower())


def code_comment_references(text):
    """Extract directives outside code, preserving their quoted Markdown content."""
    yield from _directive_references(text, _code_comment_candidates(text))


def file_citation_references(text):
    """Extract complete file citations; grant resolution still enforces its roots."""
    yield from _directive_references(text, _code_comment_candidates(text, citation=True))


def visualization_paths(texts):
    """Extract references from assistant texts outside Markdown code."""
    return {path for text in texts
            for path in _directive_references(text, reference_candidates(text))}


def visualization_files(state, files):
    texts = (item.get('text', '') for turn in ordered_turns(state) for item in items_array(turn.get('items'))
             if item.get('type') in ('agentMessage', 'assistantMessage'))
    return [*files, *descriptors(visualization_paths(texts))]


def _directive_references(text, matches):
    candidates = {start: (end, path) for start, end, path in matches}
    fence, inline, consumed, base = None, None, 0, 0
    for line in text.splitlines(keepends=True):
        offset = max(0, consumed - base)
        if offset >= len(line):
            base += len(line)
            continue
        if offset == 0:
            content = re.sub(r'^ {0,3}(?:(?:> ?)|(?:[-+*] |\d+[.)] ))+', '', line)
            marker = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', content.rstrip('\r\n'))
            if marker:
                run, tail = marker.groups()
                if fence is None:
                    fence = (run[0], len(run))
                elif run[0] == fence[0] and len(run) >= fence[1] and not tail.strip():
                    fence = None
                base += len(line)
                continue
            if fence is not None or content.startswith(('    ', '\t')):
                base += len(line)
                continue
        while offset < len(line):
            candidate = candidates.get(base + offset) if inline is None else None
            if candidate:
                consumed, path = candidate
                yield path
                offset = consumed - base
            elif line[offset] == '`':
                end = offset + 1
                while end < len(line) and line[end] == '`':
                    end += 1
                width = end - offset
                if inline is None:
                    inline = width
                elif inline == width:
                    inline = None
                offset = end
            elif line[offset] == '\\' and inline is None and offset + 1 < len(line):
                offset += 2
            else:
                offset += 1
        base += len(line)


def _code_comment_candidates(value, citation=False):
    """Forward-only bounded parsing; Markdown context is checked by the caller."""
    attribute = re.compile(r'\s*([a-zA-Z_]+)=')
    marker = ':codex-file-citation{' if citation else '::code-comment{'
    allowed = ('path', 'artifact_kind', 'purpose') if citation else ('title', 'body', 'file', 'start', 'end', 'priority')
    offset = 0
    while True:
        offset = value.find(marker, offset)
        if offset < 0:
            return
        beginning = offset
        cursor, attrs, valid = offset + len(marker), {}, True
        limit = min(len(value), offset + 65536)
        while cursor < limit:
            if value[cursor] == '}':
                cursor += 1
                break
            field = attribute.match(value, cursor)
            if not field or field[1] in attrs or field[1] not in allowed:
                valid = False
                break
            key, cursor = field[1], field.end()
            if cursor < limit and value[cursor] == '"':
                cursor += 1
                chars = []
                while cursor < limit and value[cursor] not in ('"', '\x00'):
                    character = value[cursor]
                    if character == '\\' and cursor + 1 < limit and value[cursor + 1] in ('"', '\\'):
                        cursor += 1
                        character = value[cursor]
                    chars.append(character)
                    cursor += 1
                if cursor >= limit or value[cursor] != '"':
                    valid = False
                    break
                attrs[key] = ''.join(chars)
                cursor += 1
            else:
                number = re.match(r'\d{1,7}(?!\d)', value[cursor:cursor + 8])
                if not number:
                    valid = False
                    break
                attrs[key] = int(number[0])
                cursor += len(number[0])
            if cursor < limit and value[cursor] != '}' and not value[cursor].isspace():
                valid = False
                break
            while cursor < limit and value[cursor].isspace():
                cursor += 1
        else:
            valid = False
        offset = max(offset + len(marker), cursor)
        required = ('path',) if citation else ('title', 'body', 'file')
        if not valid or not all(isinstance(attrs.get(key), str) and attrs[key].strip() for key in required):
            continue
        if citation:
            if all(isinstance(value, str) for value in attrs.values()):
                yield beginning, cursor, attrs['path']
            continue
        if any(key in attrs and (not isinstance(attrs[key], int) or not 1 <= attrs[key] <= 1000000) for key in ('start', 'end')):
            continue
        if 'end' in attrs and ('start' not in attrs or attrs['end'] < attrs['start']):
            continue
        if 'priority' in attrs and (not isinstance(attrs['priority'], int) or not 0 <= attrs['priority'] <= 3):
            continue
        yield beginning, cursor, attrs['file']


def path_identity(value):
    """Return the host-normalized identity used to link duplicate references."""
    path = value if isinstance(value, Path) else Path(value)
    try:
        return Path(os.path.normcase(_without_windows_prefix(str(path.resolve(strict=False)))))
    except (OSError, RuntimeError):
        return Path(os.path.normcase(_without_windows_prefix(os.path.abspath(str(path)))))


def _without_windows_prefix(value):
    if os.name == 'nt':
        if value.startswith('\\\\?\\UNC\\'):
            return '\\\\' + value[8:]
        if value.startswith('\\\\?\\') and re.match(r'[A-Za-z]:\\', value[4:]):
            return value[4:]
    return value


def workspace_relative_path(path, cwd):
    """Locate an artifact inside the resolved workspace, including Windows aliases."""
    if not cwd:
        return None
    path = Path(_without_windows_prefix(str(Path(path).resolve())))
    root = Path(_without_windows_prefix(str(Path(cwd).resolve())))
    return path.relative_to(root).as_posix() if root in path.parents else None


def reference_path(value, allow_relative=False):
    """Resolve a local file reference without allowing remote URL access."""
    if not isinstance(value, str) or not value:
        return None
    # Chat links may prefix Windows drive paths with a URL-style slash.
    if os.name == 'nt' and re.fullmatch(r'/[A-Za-z]:[\\/].*', value):
        value = value[1:]
    try:
        parsed = urlsplit(value)
        if parsed.scheme == 'file':
            if parsed.netloc not in ('', 'localhost') or not parsed.path:
                return None
            file_path = unquote(parsed.path)
            # On Windows, file:///D:/... has a POSIX-style leading slash.
            # Strip it before translating so the result has the D: drive.
            if re.fullmatch(r'/[A-Za-z]:[\\/].+', file_path):
                file_path = file_path[1:]
            return Path(url2pathname(file_path))
        drive, tail = ntpath.splitdrive(value)
        if drive:
            # On Windows, keep the actual drive path. On POSIX, preserve a
            # PureWindowsPath-derived shape for synthetic cross-platform tests.
            if os.name == 'nt':
                return Path(value)
            candidate = PureWindowsPath(drive + tail)
            if not candidate.is_absolute():
                return None
            return Path(*candidate.parts)
        if parsed.scheme:
            return None
        path = Path(unquote(parsed.path or value))
        return path if path.is_absolute() or allow_relative and not parsed.netloc else None
    except (ValueError, OSError):
        return None


def _is_plain_reference(value):
    """Treat Windows drive paths as plain text, not as a URL scheme."""
    return bool(ntpath.splitdrive(value)[0]) or not urlsplit(value).scheme


def _model_image_references(state):
    """Map model image references and return the trusted runtime-view identities.

    ImageView rows are runtime-generated evidence and may point at desktop-side
    screenshots outside a workspace. Markdown image links are model-authored
    text and must not grant that broader filesystem trust.
    """
    result = {}
    trusted_views = set()
    def add(value, reference, runtime_view=False):
        # Remove an editor line suffix without changing the reference shown by
        # Markdown; only the actual filesystem lookup uses the trimmed path.
        path = reference_path(re.sub(r':\d+$', '', value))
        if not path or not (path.is_absolute() or bool(ntpath.splitdrive(str(path))[0])):
            return
        key = path_identity(path)
        row = result.setdefault(key, set())
        if isinstance(reference, str) and reference:
            row.add(reference)
        if runtime_view:
            trusted_views.add(key)

    for turn in ordered_turns(state):
        for item in items_array(turn.get('items')):
            kind = item.get('type')
            if kind in ('ImageView', 'imageView'):
                add(item.get('path'), item.get('path'), runtime_view=True)
            elif kind in AGENT_KINDS:
                text = item.get('text', '')
                if isinstance(text, str):
                    for match in IMAGE_MARKDOWN_PATH.finditer(text):
                        reference = match[1] or match[2]
                        add(reference, reference)
    return result, trusted_views


def referenced_model_images(state, image_views_only=False):
    """Map image paths shown by the runtime or embedded in model output."""
    result, trusted_views = _model_image_references(state)
    return trusted_views if image_views_only else result


def _references(state):
    candidates = set()
    user_candidates = set()
    comments = set()
    for turn in ordered_turns(state):
        for item in items_array(turn.get('items')):
            if item.get('type') == 'userMessage':
                text = item.get('text') if isinstance(item.get('text'), str) else ''
                for block in item.get('content', []) if isinstance(item.get('content'), list) else []:
                    if isinstance(block, dict) and block.get('type') in ('text', 'inputText'):
                        text += '\n' + str(block.get('text', ''))
                for match in MARKDOWN_PATH.finditer(text):
                    user_candidates.add(match[1] or match[2])
                user_candidates.update(file['path'] for file in desktop_file_attachments(text))
            if item.get('type') in ('agentMessage', 'assistantMessage'):
                for match in MARKDOWN_PATH.finditer(item.get('text', '')):
                    candidates.add(match[1] or match[2])
                comments.update(code_comment_references(item.get('text', '')))
                candidates.update(file_citation_references(item.get('text', '')))
            for attachment in item.get('content', []) if isinstance(item.get('content'), list) else []:
                if isinstance(attachment, dict) and attachment.get('type') in ('localImage', 'image', 'file'):
                    value = attachment.get('path', attachment.get('url'))
                    if isinstance(value, str):
                        (user_candidates if item.get('type') == 'userMessage' else candidates).add(value)
    return candidates, comments, user_candidates


def host_file_links(state, files=()):
    """Offer explicit host-file actions without reading a path while rendering.

    The workspace endpoint validates the selected target on the selected host
    when clicked. These links never acquire automatic image-preview trust.
    """
    known = {ref for file in files for ref in [file.get('reference'), *file.get('references', [])]}
    result = {}
    for raw in sorted(set().union(*_references(state)) - known):
        value = re.sub(r':\d+$', '', raw)
        if len(value) > 4096 or any(ord(c) < 32 for c in value):
            continue
        try:
            if value.startswith('file:'):
                url = urlsplit(value)
                if url.netloc not in ('', 'localhost') or url.query or url.fragment:
                    continue
                value = unquote(url.path)
            elif not re.match(r'^/?[A-Za-z]:[\\/]', value):
                url = urlsplit(value)
                if url.scheme or url.netloc:
                    continue
                value = unquote(url.path)
            if re.match(r'^/[A-Za-z]:[\\/]', value):
                value = value[1:]
            if value.startswith(('\\\\', '//')):
                continue
            cwd = state.get('cwd') or ''
            path_type = PureWindowsPath if re.match(r'^[A-Za-z]:[\\/]', value or cwd) or re.match(r'^[A-Za-z]:[\\/]', cwd) else PurePosixPath
            path = path_type(value)
            if not path.is_absolute():
                path = path_type(cwd) / path
            if not path.is_absolute() or '..' in path.parts or (path_type is PureWindowsPath and path.drive.startswith('\\\\')):
                continue
            absolute = path.as_posix()
            key = 'host-' + hashlib.sha256((absolute.casefold() if path_type is PureWindowsPath else absolute).encode()).hexdigest()
            row = result.setdefault(key, {'id': key, 'path': absolute, 'name': path.name or absolute,
                                         'reference': raw, 'references': [], 'image': False, 'scope': 'host'})
            row['references'].append(raw)
        except ValueError:
            continue
    return [*files, *result.values()]


def artifact_paths(state, codex_home):
    roots = [Path(codex_home) / 'visualizations'] if codex_home else []
    if state.get('cwd'):
        roots.append(Path(state['cwd']))
    roots = [root.resolve() for root in roots]
    candidates, comments, user_candidates = _references(state)
    result = {}
    model_images, trusted_views = _model_image_references(state)
    for raw in sorted(candidates | comments | user_candidates):
        # Model-authored Markdown and response attachments remain bound to the
        # workspace/visualization roots even when they name an image suffix.
        value = re.sub(r':\d+$', '', raw)
        path = reference_path(value, allow_relative=bool(state.get('cwd')))
        if not path:
            continue
        try:
            path = (Path(state['cwd']) / path) if not path.is_absolute() and state.get('cwd') else path
            if raw in comments and raw not in candidates:
                if not state.get('cwd'):
                    continue
                root = Path(state['cwd']).resolve()
                relative = workspace_relative_path(path, root)
                if relative is None:
                    continue
                unresolved = Path(os.path.abspath(path))
                # Ignore host aliases above the project (e.g. macOS /var), but
                # never grant a review reference through a project symlink.
                project = Path(os.path.abspath(state['cwd']))
                if project not in unresolved.parents or any(part.is_symlink() for part in (unresolved, *unresolved.parents) if part != project and project in part.parents):
                    continue
            path = path.resolve(strict=True)
            if raw in user_candidates and raw not in candidates | comments:
                if not state.get('cwd') or workspace_relative_path(path, Path(state['cwd']).resolve()) is None:
                    continue
            if not any(workspace_relative_path(path, root) is not None for root in roots) or not (path.is_file() or path.is_dir()):
                continue
        except (OSError, RuntimeError):
            continue
        key = hashlib.sha256(str(path_identity(path)).encode()).hexdigest()
        artifact = result.setdefault(key, {'path': path, 'reference': raw, 'references': [], 'name': path.name,
                       'kind': 'directory' if path.is_dir() else 'file',
                       'image': path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES})
        artifact['references'].append(raw)
    # A path can be shown once as ImageView and embedded later with another text
    # form (usually a plain absolute path versus file://). Only the runtime view
    # grants the outside-workspace exception; collect all references so Markdown
    # can still resolve to the same authenticated artifact ID.
    for path, references in model_images.items():
        if path not in trusted_views:
            continue
        try:
            if not path.is_file():
                continue
        except OSError:
            continue
        plain = sorted(
            (ref for ref in references if path_identity(reference_path(ref)) == path and _is_plain_reference(ref)),
            key=lambda value: (len(value), value),
        )
        file_refs = sorted(ref for ref in references if urlsplit(ref).scheme == 'file')
        reference = plain[0] if plain else (file_refs[0] if file_refs else next(iter(references)))
        key = hashlib.sha256(str(path).encode()).hexdigest()
        references.update(result.get(key, {}).get('references', []))
        result[key] = {'path': path, 'reference': reference, 'references': sorted(references), 'name': path.name,
                       'kind': 'directory' if path.is_dir() else 'file',
                       'image': path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES}
    return result


def workspace_references(messages, cwd, host_files=False):
    """Link displayed files, using host scope only for explicit read-only actions."""
    state = {'cwd': cwd, 'turns': [{'items': [{'type': 'userMessage' if row.get('role') == 'user' else 'agentMessage', 'text': row.get('text', ''), 'content': row.get('attachments', [])}
             for row in messages if row.get('role') in ('assistant', 'user')]} ]}
    if host_files:
        return host_file_links(state)
    if not cwd or not Path(cwd).is_absolute(): return []
    root = Path(cwd).resolve()
    return [{'id': key, 'name': value['name'], 'reference': value['reference'],
             'references': value['references'], 'path': workspace_relative_path(value['path'], root),
             'kind': value['kind'], 'image': False}
            for key, value in artifact_paths(state, None).items()]
