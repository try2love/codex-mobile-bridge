#!/usr/bin/env python3
"""Isolated Mac App fixtures; no real Codex, accounts, chats or model calls.

Requires the already-built codex-launcher helper. Restores the one GUI-session
variable after proving a plain Launch Services open inherits the dispatcher.
"""
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bridge.clients.codex import desktop_runtime as runtime
from bridge.platforms.macos import codex_launch


def main():
    if sys.platform != 'darwin':
        raise SystemExit('Run on macOS with the existing command line developer tools')
    helper = ROOT/'dist/client-helpers/codex-launcher'
    if not helper.is_file():
        raise SystemExit('Build the Mac client helpers first')
    area = ROOT/'.tmp/codex-ordinary-launch'
    area.mkdir(parents=True, exist_ok=True)
    audit = area/'launch-audit.txt'
    audit.unlink(missing_ok=True)
    checks = []
    with tempfile.TemporaryDirectory(dir=area) as folder:
        work = Path(folder)
        home = work/'profile'
        home.mkdir(mode=0o700)
        root = runtime.private_directory(runtime.directory(home))
        source = work/'app.c'
        source.write_text('''#include <stdlib.h>
#include <stdio.h>
#include <unistd.h>
#include <sys/wait.h>
int main(int argc, char **argv) {
  FILE *audit=fopen(AUDIT_PATH,"a");
  if(audit) {fprintf(audit,"pid=%d cli=%s home=%s receipt=%s\\n",getpid(),getenv("CODEX_CLI_PATH"),getenv("CODEX_HOME"),getenv("CMB_LAUNCH_FIXTURE"));fclose(audit);}
  const char *launcher=getenv("CODEX_CLI_PATH"); if(!launcher)return 2;
  pid_t child=fork(); if(child<0)return 3;
  if(child==0) {argv[0]=(char *)launcher; execv(launcher,argv); _exit(127);}
  int status; if(waitpid(child,&status,0)<0)return 4;
  audit=fopen(AUDIT_PATH,"a");if(audit){fprintf(audit,"pid=%d child_status=%d\\n",getpid(),status);fclose(audit);}
  return WIFEXITED(status)?WEXITSTATUS(status):5;
}
''')
        receipt = work/'result.json'

        def executable(path, role):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('#!'+sys.executable+'\nimport json,os,sys\n'
                'data=sys.stdin.buffer.read()\n'
                'open(os.environ["CMB_LAUNCH_FIXTURE"],"w").write(json.dumps({"role":'+repr(role)+',"argv":sys.argv[1:],"home":os.environ.get("CODEX_HOME"),"input":data.decode()}))\n'
                'sys.stdout.buffer.write(data)\n')
            path.chmod(0o700)

        def application(name):
            app = work/(name+'.app')
            binary = app/'Contents/MacOS/App'
            binary.parent.mkdir(parents=True)
            subprocess.run(['clang', '-Os', '-DAUDIT_PATH='+json.dumps(str(audit)), str(source), '-o', str(binary)], check=True)
            (app/'Contents/Info.plist').write_bytes(plistlib.dumps({
                'CFBundleIdentifier': 'io.github.try2love.codexmobilebridge.fixture.'+name.lower(),
                'CFBundleExecutable': 'App', 'CFBundlePackageType': 'APPL',
                'CFBundleName': name, 'CFBundleVersion': '1', 'LSUIElement': True,
                'LSEnvironment': {'CODEX_HOME': str(home), 'CMB_LAUNCH_FIXTURE': str(receipt)}}))
            native = app/'Contents/Resources/codex'
            executable(native, 'native-'+name)
            subprocess.run(['codesign', '--force', '--sign', '-', str(app)], check=True, capture_output=True)
            return app, binary, native

        app, binary, native = application('Selected')
        foreign, foreign_binary, _ = application('Foreign')
        adapter = work/'adapter'
        executable(adapter, 'adapter')
        runtime._write(root/'settings.json', json.dumps({'version': 1, 'home': str(home),
            'application': str(binary), 'cli': str(native)}))
        with patch.object(runtime, 'sys', SimpleNamespace(platform='darwin', frozen=True, executable=str(adapter))), \
             patch.object(runtime, 'ordinary_launch_helper', return_value=helper), \
             patch.object(codex_launch, 'get_environment', return_value=None), \
             patch.object(codex_launch, 'install_login_environment'), \
             patch.object(codex_launch, 'set_environment') as install:
            runtime.enable_ordinary_launch(home, binary)
        launcher = str(install.call_args.args[0])
        environment = {**os.environ, 'CODEX_HOME': str(home), 'CODEX_CLI_PATH': launcher,
                       'CMB_LAUNCH_FIXTURE': str(receipt)}
        arguments = ['app-server', '--fixture', '附加 参数', 'literal $() `text`']
        payload = b'{"method":"initialize","id":1}\n'

        def check(label, executable_path=binary, values=None, expected='adapter'):
            result = subprocess.run([executable_path, *arguments], env=values or environment,
                                    input=payload, capture_output=True, timeout=10, check=True)
            value = json.loads(receipt.read_text())
            assert value['role'] == expected, (label, value)
            assert value['argv'][-len(arguments):] == arguments, (label, value)
            assert value['input'].encode() == result.stdout == payload, label
            checks.append(label)

        check('selected App/profile uses adapter and preserves arguments/stdin/stdout')
        check('another profile uses original runtime', values={**environment, 'CODEX_HOME': str(work/'other')}, expected='native-Selected')
        check('another App uses its own bundled runtime', executable_path=foreign_binary, expected='native-Foreign')
        check('different SQLite home stays on original runtime', values={**environment, 'CODEX_SQLITE_HOME': str(work/'other')}, expected='native-Selected')
        record = runtime._read(root/'ordinary-launch.json')
        default = {key: value for key, value in environment.items() if key != 'CODEX_HOME'}
        runtime._write(root/'ordinary-launch.json', json.dumps({**record, 'home': str(Path.home()/'.codex')}))
        check('ordinary default home works without CODEX_HOME', values=default)
        runtime._write(root/'ordinary-launch.json', json.dumps(record))
        adapter.rename(work/'removed-adapter')
        check('removed gateway leaves ordinary App usable', expected='native-Selected')
        (work/'removed-adapter').rename(adapter)
        (root/'ordinary-launch.json').chmod(0o644)
        check('untrusted configuration is not executed', expected='native-Selected')
        (root/'ordinary-launch.json').chmod(0o600)

        # This is the actual GUI startup regression: no per-launch --env or
        # gateway start command. Only our fixture application is opened.
        previous = codex_launch.get_environment()
        try:
            subprocess.run(['/bin/launchctl', 'unsetenv', codex_launch.KEY], check=True)
            subprocess.run([launcher, '--register-environment'], check=True, timeout=10)
            assert codex_launch.get_environment() == launcher
            checks.append('login registration restores the ordinary launch default')
            subprocess.run(['/bin/launchctl', 'setenv', codex_launch.KEY, '/external/custom-codex'], check=True)
            subprocess.run([launcher, '--register-environment'], check=True, timeout=10)
            assert codex_launch.get_environment() == '/external/custom-codex'
            checks.append('login registration preserves another tool override')
            codex_launch.set_environment(launcher)
            receipt.unlink()
            # This test itself runs inside Codex, whose explicit launch values
            # otherwise override launchd's current values. A normal Dock opener
            # has no caller-supplied Codex profile/CLI override.
            opener = {key: value for key, value in os.environ.items()
                      if key not in ('CODEX_CLI_PATH', 'CODEX_HOME', 'CODEX_SQLITE_HOME')}
            subprocess.run(['open', '-g', '-n', str(app)], env=opener, check=True, timeout=20)
            deadline = time.monotonic() + 10
            while not receipt.exists() and time.monotonic() < deadline:
                time.sleep(.05)
            assert receipt.exists(), audit.read_text()
            value = json.loads(receipt.read_text())
            assert value['role'] == 'adapter', value
            checks.append('plain GUI launch inherits dispatcher without manual restart sequence')
        finally:
            restore = (['setenv', codex_launch.KEY, previous] if previous is not None else ['unsetenv', codex_launch.KEY])
            subprocess.run(['/bin/launchctl', *restore], check=True)
            assert codex_launch.get_environment() == previous
    result = {'passed': len(checks), 'checks': checks, 'guiEnvironmentRestored': True,
              'realCodexRestarted': False}
    (area/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
