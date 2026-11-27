#!/usr/bin/env python3
"""Package the gateway's public chat shell for the two native offline hosts."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bridge.api.assets import FONT_ROUTE, PDF_CMAP_ROUTE, PDF_FONT_ROUTE, STATIC, served_asset_bytes


def build(destination):
    web = ROOT / 'web'
    paths = set(STATIC)
    for route, directory in ((FONT_ROUTE, 'vendor/katex/fonts'), (PDF_CMAP_ROUTE, 'vendor/pdfjs/cmaps'),
                             (PDF_FONT_ROUTE, 'vendor/pdfjs/standard_fonts')):
        for file in (web / directory).iterdir():
            name = '/' + file.relative_to(web).as_posix()
            if route.fullmatch(name):
                paths.add(name)
    destination.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for path in sorted(paths - {'/', '/index.html'}):
        target = destination / path[1:]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(served_asset_bytes(web, path))
        manifest[path] = path[1:]
    page = served_asset_bytes(web, '/')
    if page.count(b'<html ') != 1:
        raise ValueError('Expected one root element in the chat shell')
    (destination / 'index.html').write_bytes(page.replace(b'<html ', b'<html data-bridge-offline ', 1))
    for entry in ('/', '/index.html', '/offline-reader.html'):
        manifest[entry] = 'index.html'
    (destination / 'manifest.json').write_text(json.dumps(manifest, sort_keys=True) + '\n', encoding='utf-8')
    # Retain notices for the same vendored renderers shipped by the gateway.
    for notice in (web / 'vendor').rglob('*'):
        if notice.is_file() and 'LICENSE' in notice.name.upper():
            target = destination / 'licenses' / notice.relative_to(web / 'vendor')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(notice.read_bytes())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    build(parser.parse_args().destination)
