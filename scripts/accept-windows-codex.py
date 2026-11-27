#!/usr/bin/env python3
"""Real official Codex lifecycle acceptance on a disposable hosted Windows runner."""
import hashlib
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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


def inspect_menu(helper, pid, *, advance_onboarding=False, skip_tutorial=False, confirm_skip=False):
    """Inspect this empty test App; optionally continue its known first-run page once."""
    return json.loads(powershell("$helper='"+str(helper).replace("'", "''")+"'; $target="+str(pid)+
        "; $advance="+('$true' if advance_onboarding else '$false')+
        "; $skip="+('$true' if skip_tutorial else '$false')+
        "; $confirmSkip="+('$true' if confirm_skip else '$false')+r'''
Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes
$type=[Reflection.Assembly]::LoadFrom($helper).GetType('CodexQuit')
$flags=[Reflection.BindingFlags]'NonPublic,Static'
$windows=$type.GetMethod('Windows',$flags).Invoke($null,@([int]$target))
$rows=@(foreach($window in $windows) {
    $row=@{class=$window.ClassName;visible=$window.Visible;tool=$window.Tool;cloaked=$window.Cloaked;
        owned=$window.Owner -ne [IntPtr]::Zero;hasTitle=-not [string]::IsNullOrWhiteSpace($window.Title);
        selected=$type.GetMethod('MainWindow',$flags).Invoke($null,@($window))}
    if($window.ClassName -eq 'Chrome_WidgetWin_1' -and $window.Visible) {
        $root=[Windows.Automation.AutomationElement]::FromHandle($window.Handle)
        $items=$root.FindAll([Windows.Automation.TreeScope]::Descendants,[Windows.Automation.Condition]::TrueCondition)
        $row.types=@($items | Group-Object {$_.Current.ControlType.ProgrammaticName} | ForEach-Object {@{type=$_.Name;count=$_.Count}})
        $row.signInVisible=@($items | Where-Object {$_.Current.Name -match '^(Sign in|Log in|Log in with ChatGPT|登录|使用 ChatGPT 登录)$'}).Count -gt 0
        $row.buttons=@($items | Where-Object {$_.Current.ControlType -eq [Windows.Automation.ControlType]::Button} | ForEach-Object {$_.Current.Name})
        $row.texts=@($items | Where-Object {$_.Current.ControlType -eq [Windows.Automation.ControlType]::Text} | ForEach-Object {$_.Current.Name})
        $row.buttonStates=@($items | Where-Object {$_.Current.ControlType -eq [Windows.Automation.ControlType]::Button} | ForEach-Object {
            @{name=$_.Current.Name;enabled=$_.Current.IsEnabled;offscreen=$_.Current.IsOffscreen;pidMatches=$_.Current.ProcessId -eq $target}
        })
        $row.menus=@($items | Where-Object {$_.Current.AutomationId -like 'application-menu-*'} | ForEach-Object {
            @{id=$_.Current.AutomationId;type=$_.Current.ControlType.ProgrammaticName;
              enabled=$_.Current.IsEnabled;offscreen=$_.Current.IsOffscreen;pidMatches=$_.Current.ProcessId -eq $target;
              patterns=@($_.GetSupportedPatterns() | ForEach-Object {$_.ProgrammaticName})}
        })
        if($advance -and $row.buttons -contains 'About personalized suggestions') {
            $roles=@($items | Where-Object {$_.Current.ProcessId -eq $target -and
                $_.Current.ControlType -eq [Windows.Automation.ControlType]::RadioButton -and
                $_.Current.IsEnabled -and -not $_.Current.IsOffscreen})
            $row.onboardingRoles=@($roles | ForEach-Object {$_.Current.Name})
            if($roles.Count -gt 0) {
                $selection=$null
                if($roles[0].TryGetCurrentPattern([Windows.Automation.SelectionItemPattern]::Pattern,[ref]$selection) -and
                    -not $selection.Current.IsSelected) {
                    # Choose a fictional role only in this guarded, account-free hosted profile.
                    $selection.Select()
                }
            }
            $next=@($items | Where-Object {$_.Current.ProcessId -eq $target -and
                $_.Current.ControlType -eq [Windows.Automation.ControlType]::Button -and
                $_.Current.Name -eq 'Continue' -and $_.Current.IsEnabled -and -not $_.Current.IsOffscreen})
            if($next.Count -eq 1) {
                $invoke=$null
                if($next[0].TryGetCurrentPattern([Windows.Automation.InvokePattern]::Pattern,[ref]$invoke)) {
                    $row.onboardingContinued=$true
                    $invoke.Invoke()
                }
            }
        }
        if($skip -and $row.buttons -contains 'Leave a note on my Desktop' -and
            $row.buttons -contains 'Turn this spreadsheet into a chart') {
            $skipButton=@($items | Where-Object {$_.Current.ProcessId -eq $target -and
                $_.Current.ControlType -eq [Windows.Automation.ControlType]::Button -and
                $_.Current.Name -eq 'Skip' -and $_.Current.IsEnabled -and -not $_.Current.IsOffscreen})
            if($skipButton.Count -eq 1) {
                $invoke=$null
                if($skipButton[0].TryGetCurrentPattern([Windows.Automation.InvokePattern]::Pattern,[ref]$invoke)) {
                    $row.tutorialSkipped=$true
                    $invoke.Invoke()
                }
            }
        }
        if($confirmSkip -and $row.texts -contains 'Skip setup?' -and
            $row.texts -contains ("You"+[char]0x2019+"ll go straight to ChatGPT") -and $row.buttons -contains 'Keep setting up') {
            $confirm=@($items | Where-Object {$_.Current.ProcessId -eq $target -and
                $_.Current.ControlType -eq [Windows.Automation.ControlType]::Button -and
                $_.Current.Name -eq 'Go to ChatGPT' -and $_.Current.IsEnabled -and -not $_.Current.IsOffscreen})
            if($confirm.Count -eq 1) {
                $invoke=$null
                if($confirm[0].TryGetCurrentPattern([Windows.Automation.InvokePattern]::Pattern,[ref]$invoke)) {
                    $row.tutorialSkipConfirmed=$true
                    $invoke.Invoke()
                }
            }
        }
    }
    $row
})
ConvertTo-Json -InputObject $rows -Depth 8 -Compress
'''))


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
    bridge = app = api = None
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
        class LocalApi(BaseHTTPRequestHandler):
            posts = 0
            def log_message(self, *args):
                pass
            def do_GET(self):
                self.send_response(404); self.end_headers()
            def do_POST(self):
                type(self).posts += 1
                self.send_response(503); self.end_headers()
        api = ThreadingHTTPServer(('127.0.0.1', 0), LocalApi)
        threading.Thread(target=api.serve_forever, daemon=True).start()
        fixtures = {
            'auth.json': json.dumps({'OPENAI_API_KEY': 'isolated-acceptance-not-a-real-key'}).encode(),
            'config.toml': ('model_provider = "isolated_acceptance"\ncli_auth_credentials_store = "file"\n'
                '[model_providers.isolated_acceptance]\nname = "Isolated acceptance"\n'
                f'base_url = "http://127.0.0.1:{api.server_port}/v1"\n'
                'wire_api = "responses"\nrequires_openai_auth = true\n').encode(),
        }
        for name, contents in fixtures.items():
            (home/name).write_bytes(contents)
        result['syntheticAuthOnly'] = True
        def unchanged_fixture():
            changes = [name for name, contents in fixtures.items() if (home/name).read_bytes() != contents]
            sessions = len(list(home.glob('sessions/**/*.jsonl')))
            if changes or sessions or LocalApi.posts:
                result['fixtureObservation'] = {'changedFiles': changes, 'sessionFiles': sessions,
                                                'syntheticApiPosts': LocalApi.posts}
                if 'config.toml' in changes:
                    def leaves(value, prefix=''):
                        flattened = {}
                        for key, item in value.items():
                            path = prefix+'.'+key if prefix else key
                            flattened.update(leaves(item, path) if isinstance(item, dict) else {path: item})
                        return flattened
                    old = leaves(tomllib.loads(fixtures['config.toml'].decode('utf-8')))
                    new = leaves(tomllib.loads((home/'config.toml').read_text(encoding='utf-8')))
                    result['fixtureObservation']['configChangedKeys'] = sorted(
                        key for key in old.keys() | new.keys() if old.get(key) != new.get(key))
                raise RuntimeError('Isolated account/configuration changed or a conversation request was made')
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
        # Let the real first-launch menu become accessible; no real sign-in is performed.
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
        # Establish the preservation baseline after the native App's first-run
        # normalization, while verifying that it still selects only our fake API.
        import tomllib
        actual_auth = json.loads((home/'auth.json').read_bytes())
        actual_config = tomllib.loads((home/'config.toml').read_text(encoding='utf-8'))
        result['firstRunNormalizedFiles'] = [name for name, contents in fixtures.items()
                                             if (home/name).read_bytes() != contents]
        if (actual_auth.get('OPENAI_API_KEY') != 'isolated-acceptance-not-a-real-key'
                or actual_auth.get('tokens')
                or actual_config.get('model_provider') != 'isolated_acceptance'
                or actual_config.get('model_providers', {}).get('isolated_acceptance', {}).get('base_url')
                    != f'http://127.0.0.1:{api.server_port}/v1'):
            raise RuntimeError('Native first-run profile no longer selects the isolated API')
        def ready_owner():
            previous = None
            deadline = time.monotonic()+30
            while time.monotonic() < deadline:
                observed = inspect_client(descriptor)
                result['lastOwnerObservation'] = observed
                if (not observed.get('unknown') and len(observed.get('mainPids', [])) == 1
                        and observed == previous):
                    return observed
                previous = observed
                time.sleep(1)
            # Record only process kinds from this empty test App, never argv or
            # another process's environment, to distinguish startup from failure.
            from bridge.platforms.windows.desktop import commands
            observed_commands = commands(result['lastOwnerObservation']['pids'])
            result['ownerProcessKinds'] = [
                {'pid': pid, 'commandAvailable': bool(command),
                 'electronChild': '--type' in (command or ''), 'utilityHost': '--expose-internals' in (command or '')}
                for pid, command in observed_commands.items()]
            raise RuntimeError('Real desktop owner did not become stable within 30 seconds')
        continued_onboarding = False
        skipped_tutorial = False
        confirmed_tutorial_skip = False
        for attempt in range(2):
            before = ready_owner()
            helpers = list(distribution.rglob('codex-quit-helper.exe'))
            if len(helpers) != 1:
                raise RuntimeError('Packaged native quit helper is not unique')
            deadline = time.monotonic()+60
            while time.monotonic() < deadline:
                result['menuObservation'] = inspect_menu(helpers[0], before['mainPids'][0],
                    advance_onboarding=attempt == 0 and not continued_onboarding,
                    skip_tutorial=attempt == 0 and not skipped_tutorial,
                    confirm_skip=attempt == 0 and skipped_tutorial and not confirmed_tutorial_skip)
                for window in result['menuObservation']:
                    if window.get('selected'):
                        page = {key: window.get(key, []) for key in ('buttons', 'texts', 'onboardingRoles')}
                        pages = result.setdefault('onboardingPages', [])
                        if page not in pages and len(pages) < 12:
                            pages.append(page)
                if any(window.get('onboardingContinued') for window in result['menuObservation']):
                    continued_onboarding = result['onboardingContinued'] = True
                if any(window.get('tutorialSkipped') for window in result['menuObservation']):
                    skipped_tutorial = result['tutorialSkipped'] = True
                if any(window.get('tutorialSkipConfirmed') for window in result['menuObservation']):
                    confirmed_tutorial_skip = result['tutorialSkipConfirmed'] = True
                if any(window.get('menus') for window in result['menuObservation']):
                    break
                time.sleep(3)
            else:
                raise RuntimeError('Official App workbench menu did not become available in the hosted profile')
            if attempt == 0:
                # Workbench visibility precedes asynchronous first-run settings.
                # Establish a stable baseline before the gateway lifecycle action.
                snapshot = {name: (home/name).read_bytes() for name in fixtures}
                stable_since = time.monotonic()
                deadline = stable_since+30
                while time.monotonic() < deadline:
                    time.sleep(1)
                    current = {name: (home/name).read_bytes() for name in fixtures}
                    if current != snapshot:
                        snapshot, stable_since = current, time.monotonic()
                    if time.monotonic()-stable_since >= 10:
                        break
                else:
                    raise RuntimeError('Native first-run configuration did not settle before initialization')
                fixtures = snapshot
                result['configBaselineStableSeconds'] = 10
            unchanged_fixture()
            previous = {pid: desktop_runtime.process_identity(pid) for pid in before['mainPids']}
            request = {'action': 'initializeDesktopRuntime', 'confirmed': True, 'tasksConfirmed': True,
                       'requestId': str(uuid.uuid4())}
            state = bridge.initialize(request)
            result.setdefault('initializationStates', []).append({
                key: state[key] for key in ('phase', 'error', 'taskStateUnavailable') if key in state})
            acknowledged = False
            if state.get('phase') == 'failed' and state.get('taskStateUnavailable'):
                # This newly created profile has no login or chats. Exercise the
                # existing explicit unavailable-state confirmation without faking an audit.
                unchanged_fixture()
                request.update(requestId=str(uuid.uuid4()), acknowledgeUnavailable=True)
                state = bridge.initialize(request)
                result['initializationStates'].append({
                    key: state[key] for key in ('phase', 'error', 'taskStateUnavailable') if key in state})
                acknowledged = True
            if state.get('phase') != 'complete':
                helpers = list(distribution.rglob('codex-quit-helper.exe'))
                if len(helpers) == 1:
                    try:
                        result['menuObservation'] = inspect_menu(helpers[0], before['mainPids'][0])
                    except Exception as error:
                        result['menuObservationError'] = type(error).__name__
                raise RuntimeError('Actual initialization failed: '+str(state.get('error')))
            after = ready_owner()
            current = {pid: desktop_runtime.process_identity(pid) for pid in after['mainPids']}
            if previous == current or set(before['pids']) & set(after['pids']):
                raise RuntimeError('Initialization did not replace the original native processes')
            client = desktop_runtime.connect(home, runtime)
            if client is None:
                raise RuntimeError('Restart did not establish a real native connection')
            client.close()
            unchanged_fixture()
            result['checks'].append({'normalQuit': True, 'newNativeOwner': True, 'nativeConnection': True,
                                     'unavailableStateExplicitlyConfirmed': acknowledged})
        result['ok'] = True
    except Exception as error:
        # No real user data is present; still remove runner-specific absolute paths.
        result['error'] = str(error).replace(str(ROOT), '<checkout>').replace(str(Path.home()), '<runner>')
        raise
    finally:
        if api:
            api.shutdown(); api.server_close()
            result['syntheticApiPosts'] = LocalApi.posts
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
