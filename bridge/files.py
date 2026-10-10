"""Serve local artifacts explicitly referenced by this chat."""
import hashlib
import ntpath
import os
import re
from pathlib import Path, PureWindowsPath
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname

from .model import ordered_turns, items_array

MARKDOWN_PATH = re.compile(r'!?\[[^\]\n]*\]\((?:<([^>]+)>|([^\n)]+))\)')
IMAGE_MARKDOWN_PATH = re.compile(r'!\[[^\]\n]*\]\((?:<([^>]+)>|([^\n)]+))\)')
AGENT_KINDS = ('agentMessage', 'assistantMessage', 'planImplementation')
IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}


def path_identity(value):
    """Return the host-normalized identity used to link duplicate references."""
    path = value if isinstance(value, Path) else Path(value)
    try:
        return Path(os.path.normcase(str(path.resolve(strict=False))))
    except (OSError, RuntimeError):
        return Path(os.path.normcase(os.path.abspath(str(path))))


def reference_path(value):
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
        return path if path.is_absolute() else None
    except (ValueError, OSError):
        return None


def _is_plain_reference(value):
    """Treat Windows drive paths as plain text, not as a URL scheme."""
    return bool(ntpath.splitdrive(value)[0]) or not urlsplit(value).scheme


def _model_image_references(state):
    """Map model image references and return the trusted runtime-view identities.

    Desktop image previews use runtime-generated ImageView evidence separately
    from model-authored Markdown references.
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


def artifact_paths(state, codex_home):
    candidates = set()
    for turn in ordered_turns(state):
        for item in items_array(turn.get('items')):
            if item.get('type') in ('agentMessage', 'assistantMessage'):
                for match in MARKDOWN_PATH.finditer(item.get('text', '')):
                    candidates.add(match[1] or match[2])
            for attachment in item.get('content', []) if isinstance(item.get('content'), list) else []:
                if isinstance(attachment, dict) and attachment.get('type') in ('localImage', 'image', 'file'):
                    value = attachment.get('path', attachment.get('url'))
                    if isinstance(value, str):
                        candidates.add(value)
    result = {}
    model_images, trusted_views = _model_image_references(state)
    for raw in candidates:
        value = re.sub(r':\d+$', '', raw)
        path = reference_path(value)
        if not path:
            continue
        try:
            path = path.resolve(strict=True)
            if not path.is_file() or path.stat().st_size > 50 * 1024 * 1024:
                continue
        except OSError:
            continue
        key = hashlib.sha256(str(path_identity(path)).encode()).hexdigest()
        artifact = result.setdefault(key, {'path': path, 'reference': raw,
            'references': [], 'name': path.name, 'image': path.suffix.lower() in IMAGE_SUFFIXES})
        artifact['references'].append(raw)
    # ImageView and Markdown may use different references for the same file;
    # merge them so both resolve to the same authenticated artifact ID.
    for path, references in model_images.items():
        if path not in trusted_views:
            continue
        try:
            if not path.is_file() or path.stat().st_size > 50 * 1024 * 1024:
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
                       'image': path.suffix.lower() in IMAGE_SUFFIXES}
    return result
