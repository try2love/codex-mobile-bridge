#!/usr/bin/env python3
"""Real official Codex lifecycle acceptance on a disposable hosted Windows runner."""
import hashlib
import base64
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
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


def powershell(command):
    command = "$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new(); "+command
    return subprocess.check_output(['powershell', '-NoProfile', '-NonInteractive', '-EncodedCommand',
        base64.b64encode(command.encode('utf-16le')).decode()], text=True, encoding='utf-8-sig')


class Gateway:
    def __init__(self, executable, data, log):
        self.log = log.open('w', encoding='utf-8')
        self.process = subprocess.Popen([str(executable), 'management-stream', '--data-dir', str(data)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log, text=True, encoding='utf-8')
        self.responses = queue.Queue()
        def read():
            for line in self.process.stdout:
                self.responses.put(line)
        threading.Thread(target=read, daemon=True).start()

    def accounts(self, payload):
        self.process.stdin.write(json.dumps({'action': 'accounts', 'payload': payload})+'\n')
        self.process.stdin.flush()
        result = json.loads(self.responses.get(timeout=100))
        if not result.get('ok'):
            raise RuntimeError(result.get('error') or 'Packaged management request failed')
        return result['result']

    def initialize(self, request):
        value = self.accounts(request)
        deadline = time.monotonic()+100
        while time.monotonic() < deadline:
            state = value.get('switch', {})
            if state.get('phase') in ('complete', 'failed'):
                return state
            time.sleep(1)
            value = self.accounts({'action': 'list'})
        raise RuntimeError('Packaged initialization did not settle; no forced termination was sent')

    def close(self):
        self.process.stdin.close()
        self.process.wait(timeout=40)
        self.log.close()


def main():
    require_hosted_windows()
    work = ROOT/'.tmp/windows-official-codex'
    work.mkdir(parents=True, exist_ok=False)
    result = {'ok': False, 'source': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
              'officialDownload': URL, 'modelMessages': 0, 'accountImported': False, 'checks': []}
    bridge = app = None
    ordinary_environment = None
    environment_observed = False
    installed_package = None
    # The Actions pwsh -> Python -> Windows PowerShell chain otherwise inherits
    # incompatible PowerShell 7 modules. This changes only this test process.
    module_path = os.environ.pop('PSModulePath', None)
    try:
        powershell('Get-Command Get-AuthenticodeSignature,Add-AppxPackage,Get-AppxPackage,Remove-AppxPackage | Out-Null')
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
        sys.path.insert(0, str(ROOT))
        from bridge.platforms.windows.discovery_ext import _package_entries
        applications = list(_package_entries(unpacked, ('ChatGPT.exe', 'Codex.exe')))
        if len(applications) != 1:
            raise RuntimeError('Official desktop executable is not unique')
        executable, application_id = applications[0]
        relative_executable = executable.relative_to(unpacked)
        executable_digest = hashlib.sha256(executable.read_bytes()).hexdigest()
        quoted = str(executable).replace("'", "''")
        command = ("$s=Get-AuthenticodeSignature -LiteralPath '"+quoted+"'; "
                   '@{status=[string]$s.Status;subject=$s.SignerCertificate.Subject} | ConvertTo-Json -Compress')
        signature = json.loads(powershell(command))
        if signature['status'] != 'Valid' or 'OpenAI' not in signature['subject']:
            raise RuntimeError('Official executable signing identity could not be verified')
        result['signature'] = signature
        # Exercise the real registered MSIX entry and execution alias, not an
        # extracted binary that would bypass the production package checks.
        existing = powershell("@(Get-AppxPackage -Name OpenAI.Codex).Count")
        if existing.strip() != '0':
            raise RuntimeError('Hosted runner already has an official Codex package')
        archive_path = str(archive).replace("'", "''")
        powershell("Add-AppxPackage -Path '"+archive_path+"'")
        installed = json.loads(powershell("Get-AppxPackage -Name OpenAI.Codex | "
            'Select-Object PackageFullName,InstallLocation | ConvertTo-Json -Compress'))
        installed_package = installed['PackageFullName']
        executable = Path(installed['InstallLocation'])/relative_executable
        if hashlib.sha256(executable.read_bytes()).hexdigest() != executable_digest:
            raise RuntimeError('Installed GUI differs from the verified official executable')
        result['officialApplicationId'] = application_id
        result['registeredPackageInstalled'] = True
        from bridge.clients.desktop_app import DesktopApp
        from bridge.clients.lifecycle import inspect_client, stop_client
        from bridge.clients.codex import desktop_runtime
        from bridge.platforms.windows import codex_launch
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
        packages = list((ROOT/'.tmp/windows-official-artifact').rglob('Codex-Mobile-Bridge-2.2.2-Windows-x64.zip'))
        if len(packages) != 1:
            raise RuntimeError('Exact CI Windows gateway package is missing')
        distribution = work/'gateway-package'
        with zipfile.ZipFile(packages[0]) as package:
            for item in package.infolist():
                if not (distribution/item.filename).resolve().is_relative_to(distribution.resolve()):
                    raise RuntimeError('Unsafe gateway archive member')
            package.extractall(distribution)
        workers = list(distribution.rglob('codex-mobile-gateway.exe'))
        if len(workers) != 1:
            raise RuntimeError('Packaged gateway runtime is not unique')
        (data/'desktop.json').write_text(json.dumps({'codexHome': str(home), 'codexBin': str(runtime),
            'autoStart': False, 'lan': False, 'tunnel': False, 'connections': []}), encoding='utf-8')
        bridge = Gateway(workers[0], data, work/'gateway-stderr.log')
        bridge.accounts({'action': 'configure', 'desktopExecutable': str(executable)})
        result['packagedGateway'] = True
        result['gatewayPackageSha256'] = hashlib.sha256(packages[0].read_bytes()).hexdigest()
        for attempt in range(2):
            before = inspect_client(descriptor)
            if before.get('unknown') or len(before.get('mainPids', [])) != 1:
                raise RuntimeError('Cannot establish the single real desktop owner')
            previous = {pid: desktop_runtime.process_identity(pid) for pid in before['mainPids']}
            request = {'action': 'initializeDesktopRuntime', 'confirmed': True, 'tasksConfirmed': True,
                       'requestId': str(uuid.uuid4())}
            state = bridge.initialize(request)
            acknowledged = False
            if state.get('phase') == 'failed' and state.get('taskStateUnavailable'):
                # This newly created profile has no login or chats. Exercise the
                # existing explicit unavailable-state confirmation without faking an audit.
                if (home/'auth.json').exists() or list(home.glob('sessions/**/*.jsonl')):
                    raise RuntimeError('Fresh-profile unavailable-state confirmation is no longer safe')
                request.update(requestId=str(uuid.uuid4()), acknowledgeUnavailable=True)
                state = bridge.initialize(request)
                acknowledged = True
            if state.get('phase') != 'complete':
                raise RuntimeError('Actual initialization failed: '+str(state.get('error')))
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
        if installed_package and (app is None or result.get('cleanupNormalQuit')):
            name = installed_package.replace("'", "''")
            powershell("Remove-AppxPackage -Package '"+name+"'")
            result['testPackageRemoved'] = True
        if module_path is not None:
            os.environ['PSModulePath'] = module_path
        (work/'result.json').write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
        print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
