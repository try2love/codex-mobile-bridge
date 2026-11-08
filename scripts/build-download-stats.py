#!/usr/bin/env python3
"""Build public platform download badges from published GitHub release assets."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from urllib.request import Request, urlopen


PLATFORMS = {
    'android': ('Android', r'Android\.apk'),
    'ios': ('iOS', r'iOS(?:-source\.zip|(?:-unsigned)?\.ipa)'),
    'linux': ('Linux', r'Linux-[\w-]+\.(?:deb|AppImage)'),
    'macos': ('macOS', r'macOS-[\w-]+\.(?:dmg|zip)'),
    'windows': ('Windows', r'Windows-[\w-]+\.(?:exe|zip)'),
}


def platform_for(name):
    for key, (_, suffix) in PLATFORMS.items():
        if re.fullmatch(r'Codex-Mobile-Bridge-.+-' + suffix, name, re.IGNORECASE):
            return key
    return None


def totals(releases):
    counts = dict.fromkeys(PLATFORMS, 0)
    seen = set()
    for release in releases:
        if release.get('draft') or not release.get('published_at'):
            continue
        for asset in release['assets']:
            if asset['id'] in seen or asset['state'] != 'uploaded':
                continue
            seen.add(asset['id'])
            key = platform_for(asset['name'])
            if key:
                value = asset['download_count']
                if type(value) is not int or value < 0:
                    raise ValueError('Invalid asset download count')
                counts[key] += value
    return counts


def fetch_pages(path):
    headers = {'Accept': 'application/vnd.github+json',
               'User-Agent': 'codex-mobile-bridge-download-stats',
               'X-GitHub-Api-Version': '2022-11-28'}
    if os.environ.get('GH_TOKEN'):
        headers['Authorization'] = 'Bearer ' + os.environ['GH_TOKEN']
    result = []
    page = 1
    while True:
        request = Request(f'https://api.github.com/{path}?per_page=100&page={page}', headers=headers)
        with urlopen(request, timeout=30) as response:
            batch = json.load(response)
        if not isinstance(batch, list):
            raise ValueError('Expected a GitHub list response')
        result.extend(batch)
        if len(batch) < 100:
            return result
        page += 1


def fetch_releases(repository):
    releases = fetch_pages(f'repos/{repository}/releases')
    for release in releases:
        if not release.get('draft') and len(release['assets']) >= 100:
            release['assets'] = fetch_pages(f'repos/{repository}/releases/{int(release["id"])}/assets')
    return releases


def write_badges(releases, output):
    counts = totals(releases)
    output.mkdir(parents=True, exist_ok=True)
    for key, (label, _) in PLATFORMS.items():
        badge = {'schemaVersion': 1, 'label': label + ' downloads',
                 'message': str(counts[key]), 'color': 'blue'}
        (output / (key + '.json')).write_text(json.dumps(badge) + '\n', encoding='utf-8')
    summary = {'updatedAt': datetime.now(timezone.utc).isoformat(),
               'scope': 'Published release assets, including prereleases; download requests, not users.',
               'platforms': counts}
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', default='try2love/codex-mobile-bridge')
    parser.add_argument('--input', type=Path, help='Use a local public release list instead of the API')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'[\w.-]+/[\w.-]+', args.repository):
        parser.error('Expected owner/repository')
    releases = json.loads(args.input.read_text()) if args.input else fetch_releases(args.repository)
    print(json.dumps(write_badges(releases, args.output)))


if __name__ == '__main__':
    main()
