"""Fixed visualize shell with its own resource policy, separate from HTML previews."""
import json
from pathlib import Path

SHELL_PATH = '/visualize-shell'
SHELL_CSP = (
    "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline' "
    "https://cdnjs.cloudflare.com https://esm.sh https://cdn.jsdelivr.net https://unpkg.com "
    "https://persistent.oaistatic.com/plugins/visualize/; "
    "style-src 'unsafe-inline' https://cdnjs.cloudflare.com https://esm.sh https://cdn.jsdelivr.net "
    "https://unpkg.com https://persistent.oaistatic.com/plugins/visualize/ "
    "https://fonts.googleapis.com https://fonts.bunny.net; "
    "font-src data: https://fonts.gstatic.com https://fonts.bunny.net; img-src data: blob:; "
    "media-src data: blob:; connect-src 'none'; frame-src 'none'; object-src 'none'; "
    "worker-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
)


def shell_bytes(web_dir):
    root = Path(web_dir) / 'shared'
    source = (root / 'visualize-shell.html').read_text(encoding='utf-8')
    for key, filename in (('STYLE', 'visualize-runtime.css'), ('RUNTIME', 'visualize-runtime.js')):
        source = source.replace('__' + key + '__', json.dumps((root / filename).read_text(encoding='utf-8')).replace('<', '\\u003c'))
    return source.encode('utf-8')
