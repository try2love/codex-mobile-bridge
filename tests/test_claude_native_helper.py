"""Run the real macOS Console selector regression without controlling any app."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('swiftc'), 'macOS Swift compiler required')
class NativeConsoleSelectors(unittest.TestCase):
    def test_observed_console_selectors_submission_and_window_guards(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
            folder = Path(folder)
            helper = folder/'claude-helper'
            built = subprocess.run(['swiftc', '-module-cache-path', str(folder/'modules'),
                                    str(ROOT/'bridge/integrations/native/claude-helper.swift'), '-o', str(helper)],
                                   capture_output=True, text=True, timeout=90)
            self.assertEqual(built.returncode, 0, built.stderr)
            result = subprocess.run([str(helper), '--self-check'], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['setupState'], 'ready', result.stdout)


@unittest.skipUnless(sys.platform == 'win32', 'Windows native helper required')
class WindowsNativeWindowSelectors(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.framework = Path(os.environ.get('WINDIR', 'C:/Windows'))/'Microsoft.NET/Framework64/v4.0.30319'
        if not (cls.framework/'csc.exe').is_file():
            raise unittest.SkipTest('Windows .NET Framework compiler required')
        cls.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.folder = Path(cls.temp.name)
        cls.helper = cls.folder/'claude-bridge-helper.exe'
        built = subprocess.run([str(cls.framework/'csc.exe'), '/nologo', '/target:exe', '/platform:x64',
            '/out:'+str(cls.helper), *['/reference:'+str(cls.framework/'WPF'/name) for name in
            ('UIAutomationClient.dll', 'UIAutomationTypes.dll', 'WindowsBase.dll')],
            str(ROOT/'bridge/integrations/native/claude-helper.cs')], capture_output=True, text=True, timeout=30)
        if built.returncode:
            raise AssertionError(built.stdout + built.stderr)

    def test_real_helper_self_checks_window_selection_and_cancellable_restore(self):
        result = subprocess.run([str(self.helper), '--self-check'], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('window selection and cancellable restore OK', result.stdout)
        self.assertIn('menu navigation OK', result.stdout)
        self.assertIn('packaged launch OK', result.stdout)
        self.assertIn('atomic console submission OK', result.stdout)

    def test_readonly_inspection_finds_owned_window_instead_of_main_window_hint(self):
        source, fixture = self.folder/'fixture.cs', self.folder/'Claude.exe'
        source.write_text('''using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;
class Fixture {
    [STAThread] static void Main(string[] args) {
        using(var owner=new Form()) using(var window=new Form()) {
            var ownerHandle=owner.Handle;
            window.Text="Claude"; window.StartPosition=FormStartPosition.Manual;
            window.Left=-20000; window.Top=-20000; window.Width=240; window.Height=120;
            window.Show(owner);
            File.WriteAllText(args[0],Process.GetCurrentProcess().Id+"|"+window.Handle.ToInt64());
            var deadline=DateTime.UtcNow.AddSeconds(20);
            var timer=new Timer(); timer.Interval=100;
            timer.Tick+=(sender,eventArgs)=>{if(File.Exists(args[1])||DateTime.UtcNow>=deadline)Application.Exit();};
            timer.Start(); Application.Run(); timer.Dispose();
        }
    }
}
''', encoding='utf-8')
        built = subprocess.run([str(self.framework/'csc.exe'), '/nologo', '/target:exe', '/platform:x64',
            '/reference:System.Windows.Forms.dll', '/out:'+str(fixture), str(source)],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        ready, stop = self.folder/'window.ready', self.folder/'window.stop'
        process = subprocess.Popen([str(fixture), str(ready), str(stop)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic()+10
            while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertTrue(ready.exists(), 'synthetic window did not initialize')
            pid, window = ready.read_text(encoding='utf-8').split('|')
            result = subprocess.run([str(self.helper), pid, '--inspect-window', str(fixture)],
                                    capture_output=True, text=True, encoding='utf-8-sig', timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            diagnostic = json.loads(result.stdout)
            self.assertIsNone(diagnostic['appUserModelId'])
            # Windows input-method overlays can themselves become the .NET
            # "main" window; the owned application window is excluded either way.
            self.assertNotEqual(diagnostic['mainWindowHandle'], int(window))
            self.assertEqual(diagnostic['selectedWindowHandle'], int(window))
            selected = next(row for row in diagnostic['windows'] if row['handle'] == int(window))
            self.assertTrue(selected['visible'])
            self.assertNotEqual(selected['owner'], 0)
            self.assertTrue(selected['eligible'])
        finally:
            stop.write_text('stop', encoding='utf-8')
            process.communicate(timeout=22)
