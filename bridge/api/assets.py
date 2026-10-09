"""Fixed public web URLs, independent of physical feature directories."""
import json
import re
from functools import lru_cache
from pathlib import Path, PurePosixPath

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
          "/client-lifecycle.css": ("client-lifecycle.css", "text/css; charset=utf-8"),
          "/client-lifecycle.js": ("client-lifecycle.js", "text/javascript; charset=utf-8"),
          "/client-navigation.js": ("client-navigation.js", "text/javascript; charset=utf-8"),
          "/client-navigation.css": ("client-navigation.css", "text/css; charset=utf-8"),
          "/desktop-sessions.js": ("desktop-sessions.js", "text/javascript; charset=utf-8"),
          "/client-accounts.js": ("client-accounts.js", "text/javascript; charset=utf-8"),
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
          "/image-viewer.js": ("image-viewer.js", "text/javascript; charset=utf-8"),
          "/workbench.css": ("workbench.css", "text/css; charset=utf-8"),
          "/": ("index.html", "text/html; charset=utf-8"),
          "/i18n.js": ("i18n.js", "text/javascript; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
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
    signatures = tuple((stat.st_mtime_ns, stat.st_size) for stat in (Path(path).stat() for path in paths))
    return _assembled(paths, signatures)
