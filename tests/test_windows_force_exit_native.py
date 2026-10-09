"""Opt-in Windows integration test using only self-created, invisible fixtures.

PowerShell, from the repository root:
    $env:CMB_RUN_NATIVE_FORCE_EXIT_TEST='1'
    python -m unittest discover -s tests -p test_windows_force_exit_native.py -v

Requires Windows PowerShell CIM access and the .NET Framework C# compiler.
No installed Codex/Claude/DSH/gateway process is used or terminated.
"""
import http.server
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request


FIXTURE_SOURCE = r'''
using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

class ForceExitFixture {
    static string Arg(string[] args, string prefix, string fallback = "") {
        foreach (var arg in args) if (arg.StartsWith(prefix)) return arg.Substring(prefix.Length);
        return fallback;
    }
    [STAThread]
    static void Main(string[] args) {
        string ready = Arg(args, "--ready-dir="), stop = Arg(args, "--stop-file=");
        string type = Arg(args, "--type=", "main");
        if (ready == "" || stop == "") return;
        int children = Int32.Parse(Arg(args, "--child-count=", "0"));
        if (type == "main") {
            for (int index = 0; index < children; index++) {
                var child = new ProcessStartInfo(Application.ExecutablePath,
                    "--type=renderer \"--ready-dir=" + ready + "\" \"--stop-file=" + stop + "\"");
                child.UseShellExecute = false;
                child.CreateNoWindow = true;
                Process.Start(child);
            }
        }
        File.WriteAllText(Path.Combine(ready, Process.GetCurrentProcess().Id + ".ready"), type);
        var started = DateTime.UtcNow;
        var timer = new Timer();
        timer.Interval = 100;
        timer.Tick += delegate {
            if (File.Exists(stop) || (DateTime.UtcNow - started).TotalSeconds > 60)
                Application.ExitThread();
        };
        timer.Start();
        // Real WinForms message loop with no windows, focus, tray or user UI.
        Application.Run(new ApplicationContext());
    }
}
'''


def _http_observer(folder):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(str(os.getpid()).encode())

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
    server.timeout = .1
    (folder/'observer.json').write_text(json.dumps({'pid': os.getpid(), 'port': server.server_port}))
    deadline = time.monotonic() + 60
    try:
        while not (folder/'observer.stop').exists() and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()


def _wait_until(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError('Self-created fixture did not become ready or exit in time')
        time.sleep(.05)


@unittest.skipUnless(sys.platform == 'win32' and os.environ.get('CMB_RUN_NATIVE_FORCE_EXIT_TEST') == '1',
                     'Opt-in Windows native test: set CMB_RUN_NATIVE_FORCE_EXIT_TEST=1')
class WindowsForceExitNativeTests(unittest.TestCase):
    def test_only_selected_self_created_installation_exits(self):
        from bridge.platforms.windows import force_exit as force

        framework = Path(os.environ.get('WINDIR', r'C:\Windows'))/'Microsoft.NET'
        compiler = next((framework/name/'v4.0.30319/csc.exe' for name in ('Framework64', 'Framework')
                         if (framework/name/'v4.0.30319/csc.exe').is_file()), None)
        if compiler is None:
            self.skipTest('.NET Framework C# compiler is unavailable')
        with tempfile.TemporaryDirectory(prefix='cmb-force-exit-native-') as temporary:
            folder = Path(temporary).resolve()
            selected, neighbor = folder/'selected/Fixture.exe', folder/'neighbor/Fixture.exe'
            ready, neighbor_ready = folder/'ready', folder/'neighbor-ready'
            for directory in (selected.parent, neighbor.parent, ready, neighbor_ready):
                directory.mkdir()
            source = folder/'Fixture.cs'
            source.write_text(FIXTURE_SOURCE, encoding='utf-8')
            compile_result = subprocess.run([str(compiler), '/nologo', '/target:winexe',
                '/reference:System.Windows.Forms.dll', '/out:'+str(selected), str(source)],
                capture_output=True, text=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(compile_result.returncode, 0, compile_result.stdout + compile_result.stderr)
            shutil.copy2(selected, neighbor)
            native, processes, fixture_handles = force._Native(), [], []
            flags = subprocess.CREATE_NO_WINDOW
            base_python = getattr(sys, '_base_executable', sys.executable)
            try:
                observer = subprocess.Popen([base_python, str(Path(__file__).resolve()),
                    '--fixture-http-observer', str(folder)], creationflags=flags)
                processes.append(observer)
                neighboring = subprocess.Popen([str(neighbor), '--ready-dir='+str(neighbor_ready),
                    '--stop-file='+str(folder/'neighbor.stop')], creationflags=flags)
                processes.append(neighboring)
                main = subprocess.Popen([str(selected), '--ready-dir='+str(ready),
                    '--stop-file='+str(folder/'selected.stop'), '--child-count=10'], creationflags=flags)
                processes.append(main)
                _wait_until(lambda: (folder/'observer.json').is_file() and len(list(ready.glob('*.ready'))) == 11
                            and len(list(neighbor_ready.glob('*.ready'))) == 1)
                pids = [int(file.stem) for file in ready.glob('*.ready')]
                self.assertEqual(len(pids), 11)
                self.assertIn(main.pid, pids)
                # Test-owned handles independently prove exit and support cleanup
                # if an assertion fails. Only this newly compiled image qualifies.
                for pid in pids:
                    handle = native.open(pid, terminate=True)
                    try:
                        identity = native.identity(handle)
                    except Exception:
                        native.close(handle)
                        raise
                    if identity['image'] != force._path(selected) or identity['session'] != native.session:
                        native.close(handle)
                        self.fail('Fixture PID does not belong to the self-created executable')
                    fixture_handles.append(handle)
                observer_info = json.loads((folder/'observer.json').read_text())
                url = 'http://127.0.0.1:'+str(observer_info['port'])
                with urllib.request.urlopen(url, timeout=2) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.read().decode(), str(observer.pid))
                started = time.monotonic()
                force.force_stop_client({'id': 'codex', 'installed': True, 'executable': str(selected)},
                    state={'pids': pids, 'mainPids': [main.pid], 'unknown': False})
                duration = time.monotonic() - started
                self.assertLess(duration, force.BUDGET)
                self.assertTrue(all(not native.alive(handle) for handle in fixture_handles))
                main.wait(timeout=2)
                remaining = force.process_inventory([selected, neighbor], timeout=5)
                self.assertEqual(remaining[selected]['pids'], [])
                self.assertEqual(remaining[neighbor]['pids'], [neighboring.pid])
                self.assertIsNone(neighboring.poll())
                self.assertIsNone(observer.poll())
                with urllib.request.urlopen(url, timeout=2) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.read().decode(), str(observer.pid))
            finally:
                for name in ('selected', 'neighbor', 'observer'):
                    (folder/(name+'.stop')).touch()
                # Give these fixtures' own timers time to finish normally first.
                for process in reversed(processes):
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.terminate()  # Popen's original self-owned handle.
                        process.wait(timeout=5)
                try:
                    _wait_until(lambda: all(not native.alive(handle) for handle in fixture_handles), timeout=2)
                except AssertionError:
                    for handle in fixture_handles:
                        if native.alive(handle):
                            native.terminate(handle)  # Verified, self-owned original handle only.
                    _wait_until(lambda: all(not native.alive(handle) for handle in fixture_handles), timeout=2)
                finally:
                    for handle in fixture_handles:
                        native.close(handle)


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--fixture-http-observer':
        _http_observer(Path(sys.argv[2]))
    else:
        unittest.main()
