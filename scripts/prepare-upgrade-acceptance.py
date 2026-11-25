#!/usr/bin/env python3
"""Bind hosted acceptance to one build and verified official baseline packages."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.request

BASE = 'd79a89c0b58f212323dac2a28b8d99baa32bd281'
VERSION = '2.2.2-preview.4'
BASELINES = (('2.2.1', 'old', False), ('2.2.2-preview.1', 'preview1', True),
             ('2.2.2-preview.2', 'preview2', True), ('2.2.2-preview.3', 'preview3', True),
             ('2.2.2-preview.4', 'preview4', True))
ACCEPTANCE_FILES = {'scripts/prepare-upgrade-acceptance.py', 'scripts/smoke-published-upgrade.cjs',
                    'scripts/smoke-macos.cjs', 'tests/macos-smoke-readiness.test.cjs',
                    '.github/workflows/upgrade-acceptance.yml', '.github/workflows/desktop.yml',
                    '.github/workflows/mobile.yml', '.github/workflows/tests.yml',
                    'tests/published-upgrade.test.cjs', 'tests/test_published_upgrade.py',
                    'tests/test_ci_test_scope.py', 'tests/test_bridge.py', 'tests/test_hosted_desktop.py',
                    'tests/test_antigravity_reading.py', 'tests/test_retained_index_history.py',
                    'tests/test_history_control_separation.py', 'tests/test_progressive_loading.py',
                    'tests/browser/session-transfer-http.spec.cjs',
                    'tests/desktop-appearance.test.cjs', 'tests/harmony-gateway-url.test.cjs',
                    'tests/test_claude_native_helper.py',
                    'tests/test_wsl_manual_profiles.py', 'tests/test_cli_network_recovery.py',
                    'scripts/reuse-desktop-artifacts.py', 'tests/test_desktop_reuse.py', 'tests/test_desktop_idle.py',
                    'tests/release-assets.test.cjs',
                    '.agents/notes/implemented/testing/2026-10-28-regression-and-dependency-gates.md',
                    '.agents/notes/implemented/testing/2026-11-18-published-package-upgrade.md'}


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
    if product['version'] != VERSION:
        raise SystemExit('Unexpected frozen product version')
    run = api('/actions/runs/' + str(int(os.environ['CANDIDATE_RUN_ID'])))
    build_source = run['head_sha']
    if not re.fullmatch(r'[0-9a-f]{40}', build_source):
        raise SystemExit('Candidate build source must be an exact commit SHA')
    build_changes = [] if build_source == source else subprocess.check_output(
        ['git', 'diff', '--name-only', source, build_source], text=True).splitlines()
    if set(build_changes) - ACCEPTANCE_FILES:
        raise SystemExit('Candidate build differs from product beyond acceptance-only files: ' + repr(build_changes))
    if (run['path'] != '.github/workflows/desktop.yml'
            or run['status'] != 'completed' or run['conclusion'] != 'success'):
        raise SystemExit('Candidate must be a successful Desktop builds run of the frozen product commit')
    changed = subprocess.check_output(['git', 'diff', '--name-only', source, 'HEAD'], text=True).splitlines()
    if set(changed) - ACCEPTANCE_FILES:
        raise SystemExit('Workflow checkout differs from product beyond acceptance-only files: ' + repr(changed))
    if os.environ.get('SOURCE_ONLY') == 'true':
        print('Verified exact candidate build and acceptance-only checkout before signing')
        return
    work = Path('.tmp/published-upgrade')
    suffix = os.environ['OLD_ASSET_SUFFIX']
    baselines = []
    for version, folder, prerelease in BASELINES:
        release = api('/releases/tags/v' + version)
        if release['draft'] or release['prerelease'] != prerelease:
            raise SystemExit('Expected the official baseline release v' + version)
        directory = work / folder
        directory.mkdir(parents=True, exist_ok=True)
        assets = {row['name']: row for row in release['assets']}
        names = {'Codex-Mobile-Bridge-' + version + '-' + suffix}
        # Windows uses NSIS for manual bootstrap, ZIP for the genuine Updater.
        if suffix == 'Windows-x64-Setup.exe' and version in ('2.2.1', VERSION):
            names.add('Codex-Mobile-Bridge-' + version + '-Windows-x64.zip')
        filenames = ['SHA256SUMS.txt', 'bridge-update.json', *sorted(names)]
        for filename in filenames:
            asset = assets[filename]
            expected_url = ('https://github.com/' + os.environ['GITHUB_REPOSITORY']
                            + '/releases/download/v' + version + '/' + filename)
            if asset['browser_download_url'] != expected_url:
                raise SystemExit('Unexpected official release asset URL')
            urllib.request.urlretrieve(expected_url, directory / filename)
        checksums = {}
        for line in (directory / 'SHA256SUMS.txt').read_text().splitlines():
            digest, filename = line.split(maxsplit=1)
            checksums[filename.lstrip('*')] = digest
        digests = {}
        for filename in ['bridge-update.json', *sorted(names)]:
            with (directory / filename).open('rb') as stream:
                actual = hashlib.file_digest(stream, 'sha256').hexdigest()
            if actual != checksums[filename]:
                raise SystemExit('Official baseline checksum mismatch: ' + filename)
            digests[filename] = actual
        (directory / 'release.json').write_text(json.dumps(release, indent=2) + '\n', encoding='utf-8')
        baselines.append({'version': version, 'release': release['html_url'], 'sha256': digests})
    (work / 'provenance.json').write_text(json.dumps({'source': source, 'candidateRun': run['html_url'],
        'buildSource': build_source, 'sourceEquivalentChanges': build_changes,
        'baselines': baselines, 'acceptanceOnlyChanges': changed,
        'mode': 'original-preview3-and-preview4-updaters-to-ci-preview4-on-macos-windows; older-baselines-manual; updater-return-stable',
        'candidateManifest': 'same-run CI signature using original shipped public key; no release publication',
        'catalogue': 'fixture release directory; original official baseline manifests/packages and signed CI candidate'}, indent=2) + '\n')
    print('Verified official stable and preview1 through original preview4 packages and exact candidate build provenance')


if __name__ == '__main__':
    main()
