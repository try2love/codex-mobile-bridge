#!/usr/bin/env python3
"""Re-upload successful platform packages without rebuilding equivalent product bytes."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import zipfile

spec = importlib.util.spec_from_file_location('prepare', Path(__file__).with_name('prepare-upgrade-acceptance.py'))
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)
PLATFORMS = {
    'desktop-Windows-x64': ('package (windows-latest, x64)', ('Windows-x64-Setup.exe', 'Windows-x64.zip')),
    'desktop-macOS-arm64': ('package (macos-15, arm64, mac-arm64)', ('macOS-arm64.dmg', 'macOS-arm64.zip')),
    'desktop-macOS-x64': ('package (macos-15-intel, x64, mac)', ('macOS-x64.dmg', 'macOS-x64.zip')),
    'desktop-Linux-x64': ('linux (ubuntu-22.04, x64, amd64, x86_64)', ('Linux-amd64.deb', 'Linux-x86_64.AppImage')),
    'desktop-Linux-arm64': ('linux (ubuntu-22.04-arm, arm64, arm64, arm64)', ('Linux-arm64.deb', 'Linux-arm64.AppImage')),
}


def validate(run, source, jobs, artifacts, name, changed):
    if not re.fullmatch('[0-9a-f]{40}', source):
        raise SystemExit('Reuse source must be an exact commit SHA')
    if run['head_sha'] != source or run['path'] != '.github/workflows/desktop.yml' or run['status'] != 'completed':
        raise SystemExit('Unexpected upstream Desktop builds run')
    if set(changed) - prepare.ACCEPTANCE_FILES:
        raise SystemExit('Reuse would include product changes: ' + repr(changed))
    job_name, _ = PLATFORMS[name]
    matching = [job for job in jobs if job['name'] == job_name]
    job = max(matching, key=lambda row: row['id']) if matching else {}
    if job.get('status') != 'completed' or job.get('conclusion') != 'success' or job.get('head_sha') != source:
        raise SystemExit('Upstream platform job did not succeed: ' + job_name)
    matching = [row for row in artifacts if row['name'] == name]
    if len(matching) != 1:
        raise SystemExit('Expected exactly one upstream artifact: ' + name)
    artifact = matching[0]
    if (artifact['expired'] or artifact['size_in_bytes'] <= 0 or
            artifact.get('workflow_run', {}).get('head_sha') != source or
            artifact.get('workflow_run', {}).get('id') != run['id'] or
            not re.fullmatch('sha256:[0-9a-f]{64}', artifact.get('digest', ''))):
        raise SystemExit('Invalid or expired upstream artifact')
    return artifact, job


def api(route):
    return prepare.api(route)


def pages(route, field):
    rows = []
    for page in range(1, 101):
        batch = api(route + ('&' if '?' in route else '?') + f'per_page=100&page={page}')[field]
        rows.extend(batch)
        if len(batch) < 100:
            return rows
    raise SystemExit('Unexpected API pagination overflow')


def sha256(filename):
    digest = hashlib.sha256()
    with filename.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def extract(archive, artifact, name, directory):
    digest = sha256(archive)
    if 'sha256:' + digest != artifact['digest']:
        raise SystemExit('Upstream artifact archive digest mismatch')
    expected = {'Codex-Mobile-Bridge-' + prepare.VERSION + '-' + suffix for suffix in PLATFORMS[name][1]}
    with zipfile.ZipFile(archive) as package:
        if set(package.namelist()) != expected or len(package.namelist()) != len(expected) or package.testzip():
            raise SystemExit('Incomplete upstream platform artifact')
        directory.mkdir(parents=True, exist_ok=False)
        package.extractall(directory)
    return {filename: sha256(directory/filename) for filename in sorted(expected)}


def reuse_source(name, run_id, source, mac_run='', mac_source=''):
    if bool(mac_run) != bool(mac_source):
        raise SystemExit('Mac ARM override requires both run and source')
    if mac_run and not re.fullmatch('[0-9a-f]{40}', mac_source):
        raise SystemExit('Mac ARM override source must be an exact commit SHA')
    return (mac_run, mac_source) if name == 'desktop-macOS-arm64' and mac_run else (run_id, source)


def main():
    name = os.environ['REUSE_ARTIFACT']
    selected_run, selected_source = reuse_source(name, os.environ['REUSE_RUN_ID'], os.environ['REUSE_SOURCE_REF'],
        os.environ.get('REUSE_MAC_ARM_RUN_ID', ''), os.environ.get('REUSE_MAC_ARM_SOURCE_REF', ''))
    source, checkout = selected_source, os.environ['SOURCE_REF']
    if not all(re.fullmatch('[0-9a-f]{40}', value) for value in (checkout, source)):
        raise SystemExit('Build and reuse sources must be exact commit SHAs')
    if subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip() != checkout:
        raise SystemExit('Build checkout differs from requested source')
    run_id = int(selected_run)
    changed = subprocess.check_output(['git', 'diff', '--name-only', source, checkout], text=True).splitlines()
    run = api(f'/actions/runs/{run_id}')
    artifact, job = validate(run, source, pages(f'/actions/runs/{run_id}/jobs?filter=all', 'jobs'),
                             pages(f'/actions/runs/{run_id}/artifacts', 'artifacts'), name, changed)
    root = Path('.tmp/desktop-reuse')/name
    root.mkdir(parents=True, exist_ok=False)
    archive = root/'artifact.zip'
    with archive.open('wb') as output:
        subprocess.run(['gh', 'api', f'repos/{os.environ["GITHUB_REPOSITORY"]}/actions/artifacts/{artifact["id"]}/zip'], stdout=output, check=True)
    hashes = extract(archive, artifact, name, root/'packages')
    (root/'provenance.json').write_text(json.dumps({'productSource': source, 'buildSource': checkout,
        'upstreamRun': run['html_url'], 'upstreamJob': job['id'], 'artifact': artifact,
        'sourceEquivalentChanges': changed, 'packages': hashes}, indent=2)+'\n')


if __name__ == '__main__':
    main()
