#!/usr/bin/env python3
"""Download verified real v2.2.1 packages for a hosted-runner acceptance job."""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import subprocess
import urllib.request

SOURCE = '448859cd6f9d216a37f2ffaa4724312c2787471f'


def api(route):
    request = urllib.request.Request('https://api.github.com/repos/' + os.environ['GITHUB_REPOSITORY'] + route,
                                     headers={'Authorization': 'Bearer ' + os.environ['GH_TOKEN'],
                                              'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def main():
    if os.environ['SOURCE_REF'] != SOURCE:
        raise SystemExit('Product source must stay frozen at ' + SOURCE)
    run = api('/actions/runs/' + str(int(os.environ['CANDIDATE_RUN_ID'])))
    if (run['head_sha'] != SOURCE or run['path'] != '.github/workflows/desktop.yml'
            or run['status'] != 'completed' or run['conclusion'] != 'success'):
        raise SystemExit('Candidate must be a successful Desktop builds run of the frozen product commit')
    changed = subprocess.check_output(['git', 'diff', '--name-only', SOURCE, 'HEAD'], text=True).splitlines()
    allowed = {'scripts/prepare-upgrade-acceptance.py', 'scripts/smoke-published-upgrade.cjs',
               '.github/workflows/upgrade-acceptance.yml', '.github/workflows/tests.yml',
               'tests/published-upgrade.test.cjs',
               'mobile/ios/BridgePreview/Info.plist',
               '.agents/notes/implemented/testing/2026-11-18-published-package-upgrade.md'}
    if set(changed) - allowed:
        raise SystemExit('Workflow checkout differs from product beyond acceptance-only files: ' + repr(changed))
    # The iOS-only release identity correction is excluded from desktop builds.
    ios = 'mobile/ios/BridgePreview/Info.plist'
    previous = plistlib.loads(subprocess.check_output(['git', 'show', SOURCE + ':' + ios]))
    current = plistlib.loads(Path(ios).read_bytes())
    previous['BridgeReleaseVersion'] = '2.2.2-preview.1'
    if current != previous:
        raise SystemExit('Unexpected additional iOS metadata changes')
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
    (work / 'provenance.json').write_text(json.dumps({'source': SOURCE, 'candidateRun': run['html_url'],
        'oldRelease': release['html_url'], 'oldAsset': name, 'oldSha256': actual,
        'acceptanceOnlyChanges': changed, 'mode': 'manual-install-upgrade-and-rollback'}, indent=2) + '\n')
    print('Verified real v2.2.1 asset and frozen candidate run provenance')


if __name__ == '__main__':
    main()
