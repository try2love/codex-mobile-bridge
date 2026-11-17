#!/usr/bin/env python3
"""Download verified real v2.2.1 packages for a hosted-runner acceptance job."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.request

BASE = 'bf69297fd21776991d1faed419f9ab1b89bce671'


def api(route):
    request = urllib.request.Request('https://api.github.com/repos/' + os.environ['GITHUB_REPOSITORY'] + route,
                                     headers={'Authorization': 'Bearer ' + os.environ['GH_TOKEN'],
                                              'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def main():
    source = os.environ['SOURCE_REF']
    if not re.fullmatch(r'[0-9a-f]{40}', source):
        raise SystemExit('Product source must be an exact 40-character commit SHA')
    subprocess.run(['git', 'merge-base', '--is-ancestor', BASE, source], check=True)
    product = json.loads(subprocess.check_output(['git', 'show', source + ':package.json']))
    if product['version'] != '2.2.2-preview.1':
        raise SystemExit('Unexpected frozen product version')
    run = api('/actions/runs/' + str(int(os.environ['CANDIDATE_RUN_ID'])))
    if (run['head_sha'] != source or run['path'] != '.github/workflows/desktop.yml'
            or run['status'] != 'completed' or run['conclusion'] != 'success'):
        raise SystemExit('Candidate must be a successful Desktop builds run of the frozen product commit')
    changed = subprocess.check_output(['git', 'diff', '--name-only', source, 'HEAD'], text=True).splitlines()
    allowed = {'scripts/prepare-upgrade-acceptance.py', 'scripts/smoke-published-upgrade.cjs',
               '.github/workflows/upgrade-acceptance.yml', '.github/workflows/desktop.yml',
               'tests/published-upgrade.test.cjs', 'tests/test_published_upgrade.py', 'tests/test_runtime_refresh.py',
               'tests/browser/chat-time.browser.js', 'tests/browser/send-feedback.spec.cjs',
               '.agents/notes/implemented/testing/2026-11-18-published-package-upgrade.md'}
    if set(changed) - allowed:
        raise SystemExit('Workflow checkout differs from product beyond acceptance-only files: ' + repr(changed))
    release = api('/releases/tags/v2.2.1')
    if release['draft'] or release['prerelease']:
        raise SystemExit('Expected the real stable v2.2.1 release')
    work = Path('.tmp/published-upgrade')
    old = work / 'old'
    old.mkdir(parents=True, exist_ok=True)
    suffix = os.environ['OLD_ASSET_SUFFIX']
    name = 'Codex-Mobile-Bridge-2.2.1-' + suffix
    assets = {row['name']: row for row in release['assets']}
    for filename in ['SHA256SUMS.txt', name]:
        urllib.request.urlretrieve(assets[filename]['browser_download_url'], old / filename)
    checksums = {}
    for line in (old / 'SHA256SUMS.txt').read_text().splitlines():
        digest, filename = line.split(maxsplit=1)
        checksums[filename.lstrip('*')] = digest
    with (old / name).open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    if actual != checksums[name]:
        raise SystemExit('Old release checksum mismatch')
    (work / 'provenance.json').write_text(json.dumps({'source': source, 'candidateRun': run['html_url'],
        'oldRelease': release['html_url'], 'oldAsset': name, 'oldSha256': actual,
        'acceptanceOnlyChanges': changed, 'mode': 'manual-install-upgrade-and-rollback'}, indent=2) + '\n')
    print('Verified real v2.2.1 asset and frozen candidate run provenance')


if __name__ == '__main__':
    main()
