"""Fixed public web URLs, independent of physical feature directories."""
import json
import re
import hashlib
from functools import lru_cache
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, urlencode, urlsplit

from bridge.resources import project_root

STATIC = {"/file-actions.js": ("file-actions.js", "text/javascript; charset=utf-8"),
"/host.js": ("hosts/environment.js", "text/javascript; charset=utf-8"),
          "/layout.js": ("layouts/viewport.js", "text/javascript; charset=utf-8"),
          "/permissions.js": ("permissions.js", "text/javascript; charset=utf-8"),
          "/downloads.js": ("downloads.js", "text/javascript; charset=utf-8"),
          "/downloads.css": ("downloads.css", "text/css; charset=utf-8"),
          "/client-icons/codex.png": ("client-icons/codex.png", "image/png"),
          "/client-icons/claude.png": ("client-icons/claude.png", "image/png"),
          "/client-icons/deepseek.png": ("client-icons/deepseek.png", "image/png"),
          "/client-icons/zcode.png": ("client-icons/zcode.png", "image/png"),
          "/client-icons/antigravity.png": ("client-icons/antigravity.png", "image/png"),
          "/client-lifecycle.css": ("client-lifecycle.css", "text/css; charset=utf-8"),
          "/client-lifecycle.js": ("client-lifecycle.js", "text/javascript; charset=utf-8"),
          "/client-navigation.js": ("client-navigation.js", "text/javascript; charset=utf-8"),
          "/client-navigation.css": ("client-navigation.css", "text/css; charset=utf-8"),
          "/project-picker.js": ("shared/project-picker.js", "text/javascript; charset=utf-8"),
          "/desktop-sessions.js": ("desktop-sessions.js", "text/javascript; charset=utf-8"),
          "/client-accounts.js": ("client-accounts.js", "text/javascript; charset=utf-8"),
          "/cli-access.js": ("cli-access.js", "text/javascript; charset=utf-8"),
          "/wsl-accounts.js": ("wsl-accounts.js", "text/javascript; charset=utf-8"),
          "/desktop-sessions.css": ("desktop-sessions.css", "text/css; charset=utf-8"),
          "/vendor/xterm/xterm.js": ("vendor/xterm/xterm.js", "text/javascript; charset=utf-8"),
          "/vendor/xterm/addon-fit.js": ("vendor/xterm/addon-fit.js", "text/javascript; charset=utf-8"),
          "/vendor/xterm/xterm.css": ("vendor/xterm/xterm.css", "text/css; charset=utf-8"),
          "/command-terminal-panel.js": ("command-terminal-panel.js", "text/javascript; charset=utf-8"),
          "/list-sync.js": ("list-sync.js", "text/javascript; charset=utf-8"),
          "/agents-panel.js": ("agents-panel.js", "text/javascript; charset=utf-8"),
          "/side-chat.js": ("side-chat.js", "text/javascript; charset=utf-8"),
          "/terminal-panel.js": ("terminal-panel.js", "text/javascript; charset=utf-8"),
          "/floating-panel.js": ("floating-panel.js", "text/javascript; charset=utf-8"),
          "/git-panel.js": ("git-panel.js", "text/javascript; charset=utf-8"),
          "/workbench.js": ("workbench.js", "text/javascript; charset=utf-8"),
          "/source-view.js": ("source-view.js", "text/javascript; charset=utf-8"),
          "/code-syntax.js": ("code-syntax.js", "text/javascript; charset=utf-8"),
          "/pdf-preview.js": ("pdf-preview.js", "text/javascript; charset=utf-8"),
          "/html-preview.js": ("html-preview.js", "text/javascript; charset=utf-8"),
          "/vendor/pdfjs/pdf.min.mjs": ("vendor/pdfjs/pdf.min.mjs", "text/javascript; charset=utf-8"),
          "/vendor/pdfjs/pdf.worker.min.mjs": ("vendor/pdfjs/pdf.worker.min.mjs", "text/javascript; charset=utf-8"),
          "/vendor/prism/source-view.min.js": ("vendor/prism/source-view.min.js", "text/javascript; charset=utf-8"),
          "/image-viewer.js": ("image-viewer.js", "text/javascript; charset=utf-8"),
          "/mermaid-renderer.js": ("mermaid-renderer.js", "text/javascript; charset=utf-8"),
          "/mermaid-render-frame.js": ("mermaid-render-frame.js", "text/javascript; charset=utf-8"),
          "/vendor/mermaid/mermaid.min.js": ("vendor/mermaid/mermaid.min.js", "text/javascript; charset=utf-8"),
          "/mermaid-viewer.js": ("mermaid-viewer.js", "text/javascript; charset=utf-8"),
          "/mermaid-viewer.css": ("mermaid-viewer.css", "text/css; charset=utf-8"),
          "/mermaid-cards.js": ("mermaid-cards.js", "text/javascript; charset=utf-8"),
          "/mermaid-cards.css": ("mermaid-cards.css", "text/css; charset=utf-8"),
          "/workbench.css": ("workbench.css", "text/css; charset=utf-8"),
          "/": ("index.html", "text/html; charset=utf-8"),
          "/icon-ui.png": ("icon-ui.png", "image/png"),
          "/icon-favicon.png": ("icon-favicon.png", "image/png"),
          "/icon-touch.png": ("icon-touch.png", "image/png"),
          "/icon-192.png": ("icon-192.png", "image/png"),
          "/i18n.js": ("i18n.js", "text/javascript; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/bootstrap.js": ("bootstrap.js", "text/javascript; charset=utf-8"),
          "/modes.js": ("modes.js", "text/javascript; charset=utf-8"),
          "/attachments.js": ("attachments.js", "text/javascript; charset=utf-8"),
          "/activity.js": ("activity.js", "text/javascript; charset=utf-8"),
          "/fast-mode.js": ("fast-mode.js", "text/javascript; charset=utf-8"),
          "/accounts.js": ("accounts.js", "text/javascript; charset=utf-8"),
          "/account.js": ("account.js", "text/javascript; charset=utf-8"),
          "/account.css": ("account.css", "text/css; charset=utf-8"),
          "/presentation.js": ("presentation.js", "text/javascript; charset=utf-8"),
          "/presentation.css": ("presentation.css", "text/css; charset=utf-8"),
          "/markdown.js": ("markdown.js", "text/javascript; charset=utf-8"),
          "/message-actions.js": ("message-actions.js", "text/javascript; charset=utf-8"),
          "/timeline.js": ("timeline.js", "text/javascript; charset=utf-8"),
          "/vendor/markdown-it.min.js": ("vendor/markdown-it.min.js", "text/javascript; charset=utf-8"),
          "/vendor/texmath.js": ("vendor/texmath.js", "text/javascript; charset=utf-8"),
          "/vendor/katex/katex.min.js": ("vendor/katex/katex.min.js", "text/javascript; charset=utf-8"),
          "/vendor/katex/katex.min.css": ("vendor/katex/katex.min.css", "text/css; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8"),
          "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
          "/icon.png": ("icon.png", "image/png")}
FONT_ROUTE = re.compile(r"^/vendor/katex/fonts/(KaTeX_[A-Za-z0-9_-]+\.(woff2|woff|ttf))$")
PDF_CMAP_ROUTE = re.compile(r"^/vendor/pdfjs/cmaps/([A-Za-z0-9_-]+\.bcmap)$")
PDF_FONT_ROUTE = re.compile(r"^/vendor/pdfjs/standard_fonts/([A-Za-z0-9_-]+\.(?:pfb|ttf))$")

# The manifest moves files, never expands the public URL allowlist.
_layout = json.loads((project_root() / 'web/assets.json').read_text(encoding='utf-8'))
for _url, (_name, _mime) in list(STATIC.items()):
    _source = _layout.get(_url, _name)
    _sources = tuple(_source) if isinstance(_source, list) else (_source,)
    if not _sources:
        raise ValueError('Empty web asset layout: ' + _url)
    for _relative in _sources:
        _path = PurePosixPath(_relative)
        if _path.is_absolute() or '..' in _path.parts or '\\' in _relative:
            raise ValueError('Invalid web asset layout: ' + _url)
    STATIC[_url] = (_sources if isinstance(_source, list) else _source, _mime)


@lru_cache(maxsize=16)
def _assembled(paths, signatures):
    # Preserve declared CSS cascade order. Cache changes with source metadata,
    # so serving a split stylesheet adds no repeated content reads or HTTP calls.
    return b''.join(Path(path).read_bytes() for path in paths)


def asset_bytes(web_dir, public_path):
    sources, _ = STATIC[public_path]
    if isinstance(sources, str):
        return (Path(web_dir) / sources).read_bytes()
    paths = tuple(str((Path(web_dir) / relative).resolve()) for relative in sources)
    signatures = tuple((stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size) for stat in (Path(path).stat() for path in paths))
    return _assembled(paths, signatures)


VERSION_KEY = 'v'
_HTML_URL = re.compile(rb'(?P<prefix>\b(?:src|href|data-bridge-src|data-bridge-href)\s*=\s*["\'])(?P<url>/[^"\']+)(?P<suffix>["\'])')
_MANIFEST_URL = re.compile(rb'(?P<prefix>"(?:src|href)"\s*:\s*")(?P<url>/[^" ]+)(?P<suffix>")')
_JS_URL = re.compile(rb'(?P<prefix>["\'])(?P<url>/[^"\'\s]+\.(?:js|mjs|css|png|webmanifest)(?:\?[^"\']*)?)(?P<suffix>["\'])')
_CSS_URL = re.compile(rb'url\((?P<quote>["\']?)(?P<url>[^)"\']+)(?P=quote)\)')


def _public_file(web_dir, public_path):
    if public_path in STATIC and public_path != '/':
        return asset_bytes(web_dir, public_path)
    for route, directory in ((FONT_ROUTE, 'vendor/katex/fonts'),
                             (PDF_CMAP_ROUTE, 'vendor/pdfjs/cmaps'),
                             (PDF_FONT_ROUTE, 'vendor/pdfjs/standard_fonts')):
        match = route.fullmatch(public_path)
        if match:
            file = Path(web_dir) / directory / match[1]
            return file.read_bytes() if file.is_file() else None
    return None


def _versioned_url(web_dir, url, parent, seen):
    try:
        value = url.decode('ascii')
    except UnicodeDecodeError:
        return url
    parsed = urlsplit(value)
    path = parsed.path
    if not path.startswith('/'):
        if parent != '/vendor/katex/katex.min.css' or not path.startswith('fonts/'):
            return url
        path = '/vendor/katex/' + path
    if path in seen or path == '/':
        return url
    if _public_file(web_dir, path) is None:
        return url
    digest = hashlib.sha256(served_asset_bytes(web_dir, path, seen)).hexdigest()
    query = [(key, val) for key, values in parse_qs(parsed.query, keep_blank_values=True).items()
             for val in values if key != VERSION_KEY]
    query.append((VERSION_KEY, digest))
    # Keep relative font URLs relative to the stylesheet URL.
    location = parsed.path if not parsed.path.startswith('/') else path
    return (location + '?' + urlencode(query) + (('#' + parsed.fragment) if parsed.fragment else '')).encode('ascii')


def served_asset_bytes(web_dir, public_path, seen=frozenset()):
    """Return the actual public representation, with only known asset references versioned."""
    source = asset_bytes(web_dir, public_path) if public_path in STATIC else _public_file(web_dir, public_path)
    if source is None:
        raise KeyError(public_path)
    if public_path in seen:
        return source
    seen = seen | {public_path}
    if public_path == '/':
        pattern = _HTML_URL
    elif public_path == '/manifest.webmanifest':
        pattern = _MANIFEST_URL
    elif public_path.endswith('.css'):
        pattern = _CSS_URL
    elif public_path.endswith(('.js', '.mjs')) and not public_path.startswith('/vendor/'):
        pattern = _JS_URL
    else:
        return source
    def replace(match):
        updated = _versioned_url(web_dir, match['url'], public_path, seen)
        if pattern is _CSS_URL:
            return b'url(' + match['quote'] + updated + match['quote'] + b')'
        return match['prefix'] + updated + match['suffix']
    return pattern.sub(replace, source)


def current_version(web_dir, public_path):
    return hashlib.sha256(served_asset_bytes(web_dir, public_path)).hexdigest()
