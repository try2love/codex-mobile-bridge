#!/usr/bin/env python3
"""Real official Codex lifecycle acceptance on a disposable hosted Windows runner."""
import hashlib
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
URL = 'https://persistent.oaistatic.com/codex-app-prod/ChatGPT-x64.msix'


def require_hosted_windows():
    if (sys.platform != 'win32' or os.environ.get('GITHUB_ACTIONS') != 'true'
            or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted'):
        raise RuntimeError('Only a disposable GitHub-hosted Windows runner is allowed')


def main():
    require_hosted_windows()
    work = ROOT/'.tmp/windows-official-codex'
    work.mkdir(parents=True, exist_ok=False)
    result = {'ok': False, 'source': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
              'officialDownload': URL, 'modelMessages': 0, 'accountImported': False, 'checks': []}
    bridge = app = None
    ordinary_environment = None
    environment_observed = False
    try:
        archive = work/'official.msix'
        request = urllib.request.Request(URL, headers={
            'User-Agent': 'Codex-Mobile-Bridge-Release-Acceptance/2.2.2'})
        with urllib.request.urlopen(request, timeout=90) as response, archive.open('wb') as output:
            if response.headers.get('x-ms-meta-package_identity') != 'OpenAI.Codex':
                raise RuntimeError('Unexpected official package identity')
            result['officialVersion'] = response.headers.get('x-ms-meta-package_version')
            while block := response.read(1024 * 1024):
                output.write(block)
        with archive.open('rb') as source:
            result['officialSha256'] = hashlib.file_digest(source, 'sha256').hexdigest()
        unpacked = work/'official'
        with zipfile.ZipFile(archive) as package:
            for item in package.infolist():
                target = (unpacked/item.filename).resolve()
                if not target.is_relative_to(unpacked.resolve()) or ':' in item.filename:
                    raise RuntimeError('Unsafe official archive member')
            package.extractall(unpacked)
        # Use signed original binaries, with no unpacked-asar edits or replacement UI.
        applications = [p for name in ('ChatGPT.exe', 'Codex.exe') for p in unpacked.rglob(name)
                        if (p.parent/'resources/app.asar').is_file()]
        if len(applications) != 1:
            raise RuntimeError('Official desktop executable is not unique')
        executable = applications[0]
        quoted = str(executable).replace("'", "''")
        command = ("$s=Get-AuthenticodeSignature -LiteralPath '"+quoted+"'; "
                   '@{status=[string]$s.Status;subject=$s.SignerCertificate.Subject} | ConvertTo-Json -Compress')
        signature = subprocess.check_output(['powershell', '-NoProfile', '-NonInteractive', '-EncodedCommand',
            base64.b64encode(command.encode('utf-16le')).decode()], text=True, encoding='utf-8-sig')
        signature = json.loads(signature)
        if signature['status'] != 'Valid' or 'OpenAI' not in signature['subject']:
            raise RuntimeError('Official executable signing identity could not be verified')
        result['signature'] = signature
        sys.path.insert(0, str(ROOT))
        from bridge.clients.desktop_app import DesktopApp
        from bridge.clients.lifecycle import inspect_client, stop_client
        from bridge.clients.codex import desktop_runtime
        from bridge.platforms.windows import codex_launch
        from bridge.app.service import Bridge
        home, data = work/'home', work/'gateway'
        home.mkdir(); data.mkdir()
        ordinary_environment = codex_launch.get_environment()
        environment_observed = True
        if ordinary_environment:
            raise RuntimeError('Hosted runner already has a Codex launch environment')
        runtime = codex_launch.bundled_cli(executable)
        descriptor = {'id': 'codex', 'installed': True, 'executable': str(executable), 'dataDirectory': str(home)}
        if inspect_client(descriptor)['running']:
            raise RuntimeError('Official executable already has an owner')
        app = DesktopApp(executable, home)
        app.validate(runtime)
        app.start()
        deadline = time.monotonic()+60
        while time.monotonic() < deadline:
            state = inspect_client(descriptor)
            if state.get('running') and len(state.get('mainPids', [])) == 1:
                break
            time.sleep(.5)
        else:
            raise RuntimeError('Fresh official App did not start')
        # Let the real first-launch menu become accessible; no sign-in is performed.
        time.sleep(8)
        bridge = Bridge(home, data, codex_bin=runtime, observation_enabled=lambda: False)
        manager = bridge.accounts
        manager.index['desktopExecutable'] = str(executable)
        for attempt in range(2):
            before = inspect_client(descriptor)
            if before.get('unknown') or len(before.get('mainPids', [])) != 1:
                raise RuntimeError('Cannot establish the single real desktop owner')
            previous = {pid: desktop_runtime.process_identity(pid) for pid in before['mainPids']}
            request = {'action': 'initializeDesktopRuntime', 'confirmed': True, 'tasksConfirmed': True,
                       'requestId': str(uuid.uuid4())}
            manager.initialize_desktop_runtime(request)
            manager.thread.join(100)
            if manager.thread.is_alive():
                raise RuntimeError('Initialization did not settle; no forced termination was sent')
            acknowledged = False
            if manager.state.get('phase') == 'failed' and manager.state.get('taskStateUnavailable'):
                # This newly created profile has no login or chats. Exercise the
                # existing explicit unavailable-state confirmation without faking an audit.
                if (home/'auth.json').exists() or list(home.glob('sessions/**/*.jsonl')):
                    raise RuntimeError('Fresh-profile unavailable-state confirmation is no longer safe')
                request.update(requestId=str(uuid.uuid4()), acknowledgeUnavailable=True)
                manager.initialize_desktop_runtime(request)
                manager.thread.join(100)
                acknowledged = True
            if manager.thread.is_alive() or manager.state.get('phase') != 'complete':
                raise RuntimeError('Actual initialization failed: '+str(manager.state.get('error')))
            after = inspect_client(descriptor)
            if after.get('unknown') or len(after.get('mainPids', [])) != 1:
                raise RuntimeError('Restarted native owner is unavailable')
            current = {pid: desktop_runtime.process_identity(pid) for pid in after['mainPids']}
            if previous == current or set(before['pids']) & set(after['pids']):
                raise RuntimeError('Initialization did not replace the original native processes')
            client = desktop_runtime.connect(home, runtime)
            if client is None:
                raise RuntimeError('Restart did not establish a real native connection')
            client.close()
            if (home/'auth.json').exists() or list(home.glob('sessions/**/*.jsonl')):
                raise RuntimeError('Acceptance unexpectedly created account or conversation data')
            result['checks'].append({'normalQuit': True, 'newNativeOwner': True, 'nativeConnection': True,
                                     'unavailableStateExplicitlyConfirmed': acknowledged})
        result['ok'] = True
    except Exception as error:
        # No real user data is present; still remove runner-specific absolute paths.
        result['error'] = str(error).replace(str(ROOT), '<checkout>').replace(str(Path.home()), '<runner>')
        raise
    finally:
        if bridge:
            bridge.close()
        if app:
            try:
                state = inspect_client(descriptor)
                if state.get('running'):
                    stop_client(descriptor, state=state)
                result['cleanupNormalQuit'] = True
            except Exception as error:
                result['cleanupNormalQuit'] = False
                result['cleanupError'] = str(error).replace(str(ROOT), '<checkout>')
        if environment_observed:
            codex_launch.set_environment(ordinary_environment)
        (work/'result.json').write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
        print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
