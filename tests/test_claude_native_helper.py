"""Run native selectors against self-checks and disposable app fixtures only."""
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


@unittest.skipUnless(shutil.which('swift'), 'Swift interpreter required')
class DesktopSessionGuards(unittest.TestCase):
    def test_pure_state_guards_without_starting_native_helper_or_reading_desktop(self):
        source = (ROOT/'bridge/platforms/macos/claude-helper.swift').read_text()
        pure = source[source.index('func desktopIssue('):source.index('func currentDesktopIssue(')]
        fixture = '''import Foundation
struct SetupError: Error { let state: String; let reason: String }
''' + pure + '''
func state(_ has: Bool = true, _ console: Bool? = true, _ login: Bool? = true,
           _ locked: Bool? = false, _ front: String? = "com.anthropic.claudefordesktop", _ saver: Bool = false) -> String? {
    desktopIssue(hasSession: has, onConsole: console, loginDone: login, locked: locked, foreground: front, screenSaverWindow: saver)?.state
}
precondition(state() == nil)
precondition(state(true, true, true, nil) == nil)
precondition(state(true, true, true, false, "com.apple.ScreenSaver.Engine") == "needs-screen-saver")
precondition(state(true, true, true, nil, "com.anthropic.claudefordesktop", true) == "needs-screen-saver")
precondition(state(true, true, true, true, "com.apple.ScreenSaver.Engine", true) == "needs-unlock")
precondition(state(true, true, true, nil, "com.apple.loginwindow") == "needs-desktop")
precondition(state(false, nil, nil, nil, nil) == "needs-desktop")
precondition(state(true, false, true, false, "com.apple.ScreenSaver.Engine") == "needs-desktop")
precondition(state(true, true, false) == "needs-desktop")
precondition(state(true, nil, nil) == "needs-desktop")
precondition(state(true, true, true, nil, nil) == "needs-desktop")
precondition(isScreenSaverWindow(bundle: "com.apple.ScreenSaver.Engine", layer: 1000, onScreen: true, saverLevel: 1000))
precondition(!isScreenSaverWindow(bundle: "com.apple.ScreenSaver.Engine", layer: 0, onScreen: true, saverLevel: 1000))
precondition(!isScreenSaverWindow(bundle: "com.apple.ScreenSaver.Engine", layer: 1000, onScreen: false, saverLevel: 1000))
precondition(!isScreenSaverWindow(bundle: "com.apple.WallpaperAgent", layer: 1000, onScreen: true, saverLevel: 1000))
print("15 desktop-state fixtures passed without GUI APIs")
'''
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
            folder = Path(folder); script = folder/'guards.swift'; script.write_text(fixture)
            result = subprocess.run(['swift', '-module-cache-path', str(folder/'modules'), str(script)],
                                    capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('15 desktop-state fixtures passed', result.stdout)


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('swiftc'), 'macOS Swift compiler required')
class NativeConsoleSelectors(unittest.TestCase):
    def test_observed_console_selectors_submission_and_window_guards(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as folder:
            folder = Path(folder)
            helper = folder/'claude-helper'
            built = subprocess.run(['swiftc', '-module-cache-path', str(folder/'modules'),
                                    str(ROOT/'bridge/platforms/macos/claude-helper.swift'), '-o', str(helper)],
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
            str(ROOT/'bridge/platforms/windows/claude-helper.cs')], capture_output=True, text=True, timeout=30)
        if built.returncode:
            raise AssertionError(built.stdout + built.stderr)

    def test_real_helper_self_checks_window_selection_and_cancellable_restore(self):
        result = subprocess.run([str(self.helper), '--self-check'], capture_output=True,
                                text=True, encoding='utf-8-sig', timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('window selection and cancellable restore OK', result.stdout)
        self.assertIn('menu navigation OK', result.stdout)
        self.assertIn('packaged launch OK', result.stdout)
        self.assertIn('atomic console submission OK', result.stdout)
        self.assertIn('detached console reuse OK', result.stdout)
        self.assertIn('native menu quit guards OK', result.stdout)
        self.assertIn('background console guards OK', result.stdout)
        self.assertIn('background cleanup guards OK', result.stdout)
        self.assertIn('background startup readiness OK', result.stdout)
        self.assertIn('background native resume OK', result.stdout)
        self.assertIn('background window restore OK', result.stdout)
        self.assertIn('background Console readiness OK', result.stdout)

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

    def test_background_connection_rejects_other_process_without_dispatch(self):
        script = self.folder/'background-connector.js'
        script.write_text('/* codex bridge connector */void 0;', encoding='utf-8')
        result = subprocess.run([str(self.helper), str(os.getpid()),
            '--connect-background='+str(script), sys.executable],
            capture_output=True, text=True, encoding='utf-8-sig', timeout=10)
        self.assertEqual(result.returncode, 1)
        replies = result.stdout.splitlines()
        self.assertEqual(len(replies), 1, 'no target may report a dispatch')
        reply = json.loads(replies[0])
        self.assertEqual(reply['setupState'], 'failed')
        self.assertEqual(reply['submission'], 'none')
        self.assertEqual(reply['pid'], os.getpid())
        self.assertTrue(reply['reason'].strip())

    def test_native_quit_on_inactive_desktop_preserves_confirmation(self):
        import ctypes
        import uuid
        from ctypes import wintypes as w

        class Security(ctypes.Structure):
            _fields_ = [('length', w.DWORD), ('descriptor', w.LPVOID), ('inherit', w.BOOL)]

        class Startup(ctypes.Structure):
            _fields_ = [('cb', w.DWORD), ('reserved', w.LPWSTR), ('desktop', w.LPWSTR),
                ('title', w.LPWSTR), ('x', w.DWORD), ('y', w.DWORD), ('width', w.DWORD),
                ('height', w.DWORD), ('columns', w.DWORD), ('rows', w.DWORD),
                ('fill', w.DWORD), ('flags', w.DWORD), ('show', w.WORD),
                ('reservedSize', w.WORD), ('reservedBytes', ctypes.POINTER(ctypes.c_byte)),
                ('stdin', w.HANDLE), ('stdout', w.HANDLE), ('stderr', w.HANDLE)]

        class ProcessInfo(ctypes.Structure):
            _fields_ = [('process', w.HANDLE), ('thread', w.HANDLE), ('pid', w.DWORD), ('tid', w.DWORD)]

        user = ctypes.WinDLL('user32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        user.CreateDesktopW.argtypes = [w.LPCWSTR, w.LPCWSTR, w.LPVOID, w.DWORD, w.DWORD, w.LPVOID]
        user.CreateDesktopW.restype = w.HANDLE
        user.CloseDesktop.argtypes = [w.HANDLE]
        user.CloseDesktop.restype = w.BOOL
        user.GetForegroundWindow.restype = w.HWND
        kernel.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.POINTER(Security),
                                      w.DWORD, w.DWORD, w.HANDLE]
        kernel.CreateFileW.restype = w.HANDLE
        kernel.CreateProcessW.argtypes = [w.LPCWSTR, w.LPWSTR, w.LPVOID, w.LPVOID, w.BOOL,
            w.DWORD, w.LPVOID, w.LPCWSTR, ctypes.POINTER(Startup), ctypes.POINTER(ProcessInfo)]
        kernel.CreateProcessW.restype = w.BOOL
        kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        kernel.WaitForSingleObject.restype = w.DWORD
        kernel.GetExitCodeProcess.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
        kernel.GetExitCodeProcess.restype = w.BOOL
        kernel.CloseHandle.argtypes = [w.HANDLE]
        kernel.CloseHandle.restype = w.BOOL

        name = 'ClaudeBridgeTest-' + uuid.uuid4().hex
        desktop = user.CreateDesktopW(name, None, None, 0, 0x01ff, None)
        self.assertTrue(desktop, ctypes.WinError(ctypes.get_last_error()))
        self.addCleanup(user.CloseDesktop, desktop)
        foreground = user.GetForegroundWindow()

        def launch(args, output):
            security = Security(ctypes.sizeof(Security), None, True)
            handles = []
            try:
                for path, access, disposition in [('NUL', 0x80000000, 3), (str(output), 0x40000000, 2)]:
                    handle = kernel.CreateFileW(path, access, 3, ctypes.byref(security), disposition, 0x80, None)
                    if handle == ctypes.c_void_p(-1).value:
                        raise ctypes.WinError(ctypes.get_last_error())
                    handles.append(handle)
                startup = Startup()
                startup.cb = ctypes.sizeof(startup)
                startup.desktop = 'winsta0\\' + name
                startup.flags = 0x100
                startup.stdin, startup.stdout, startup.stderr = handles[0], handles[1], handles[1]
                info = ProcessInfo()
                command = ctypes.create_unicode_buffer(subprocess.list2cmdline([str(arg) for arg in args]))
                if not kernel.CreateProcessW(None, command, None, None, True, 0x08000000,
                        None, str(self.folder), ctypes.byref(startup), ctypes.byref(info)):
                    raise ctypes.WinError(ctypes.get_last_error())
                kernel.CloseHandle(info.thread)
                self.addCleanup(kernel.CloseHandle, info.process)
                return info
            finally:
                for handle in handles:
                    kernel.CloseHandle(handle)

        def exited(info):
            return kernel.WaitForSingleObject(info.process, 0) == 0

        def wait(info, seconds):
            self.assertEqual(kernel.WaitForSingleObject(info.process, int(seconds * 1000)), 0,
                             'synthetic process did not finish')
            code = w.DWORD()
            self.assertTrue(kernel.GetExitCodeProcess(info.process, ctypes.byref(code)))
            return code.value

        source, fixture = self.folder/'quit-fixture.cs', self.folder/'Claude.exe'
        source.write_text('''using System;
using System.Diagnostics;
using System.IO;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Automation;
using System.Windows.Automation.Peers;
using System.Windows.Automation.Provider;
using System.Windows.Threading;
class InlineItem : Button {
    public Action Open; public bool Expanded;
    public string ErrorMarker;
    public void Submit(){OnClick();}
    protected override AutomationPeer OnCreateAutomationPeer(){return new InlinePeer(this);}
}
class InlinePeer : ButtonAutomationPeer,IExpandCollapseProvider,IInvokeProvider {
    readonly InlineItem item;
    public InlinePeer(InlineItem owner):base(owner){item=owner;}
    protected override AutomationControlType GetAutomationControlTypeCore(){return AutomationControlType.MenuItem;}
    public override object GetPattern(PatternInterface pattern){return pattern==PatternInterface.ExpandCollapse||pattern==PatternInterface.Invoke?this:base.GetPattern(pattern);}
    public void Invoke(){
        if(item.ErrorMarker!=null) {
            item.Dispatcher.Invoke(new Action(item.Submit));
            File.AppendAllText(item.ErrorMarker,"provider-error\\n");
            throw new InvalidOperationException("Exit accepted, provider acknowledgement lost");
        }
        item.Dispatcher.BeginInvoke(new Action(item.Submit));
    }
    public void Expand(){item.Dispatcher.Invoke(new Action(()=>{item.Expanded=true;if(item.Open!=null)item.Open();}));}
    public void Collapse(){item.Dispatcher.Invoke(new Action(()=>item.Expanded=false));}
    public ExpandCollapseState ExpandCollapseState {get{return item.Expanded?ExpandCollapseState.Expanded:ExpandCollapseState.Collapsed;}}
}
class QuitFixture {
    [STAThread] static void Main(string[] args) {
        var app=new Application(); var window=new Window {Title="Claude",Width=360,Height=240};
        Action quit=null;
        if(args[3]=="native-popup") {
            var menu=new Menu();var file=new MenuItem {Header="File"};var exit=new MenuItem {Header="Exit"};
            file.Items.Add(exit);menu.Items.Add(file);window.Content=menu;
            exit.Click+=(sender,eventArgs)=>quit();
        } else {
            // Match the product's in-window accessible menu shape. Real UIA
            // provider methods expand/invoke directly, without key injection.
            var menu=new StackPanel();var file=new InlineItem {Content="File"};
            var exit=new InlineItem {Content="Exit",Visibility=Visibility.Collapsed};
            if(args[3]=="dispatch-error")exit.ErrorMarker=args[2];
            file.Open=()=>exit.Visibility=Visibility.Visible;
            menu.Children.Add(file);menu.Children.Add(exit);window.Content=menu;
            exit.Click+=(sender,eventArgs)=>quit();
        }
        var timer=new DispatcherTimer {Interval=TimeSpan.FromMilliseconds(100)};
        var deadline=DateTime.UtcNow.AddSeconds(45);
        timer.Tick+=(sender,eventArgs)=>{if(File.Exists(args[1])||DateTime.UtcNow>=deadline)app.Shutdown();};
        quit=()=>{
            File.AppendAllText(args[2],"exit\\n");
            if(args[3]=="dispatch-error") {
                // Keep the app alive beyond the helper's observation window,
                // like native asynchronous session saving after app.quit().
                var saving=new DispatcherTimer {Interval=TimeSpan.FromSeconds(6)};
                saving.Tick+=(s,e)=>{saving.Stop();app.Shutdown();};saving.Start();
            } else if(args[3]=="pending") {
                var confirm=new Window {Title="Save changes",Owner=window,Width=260,Height=120};
                var buttons=new StackPanel();var keep=new Button {Content="Wait for Claude"};
                keep.Click+=(s,e)=>{File.AppendAllText(args[2],"answered\\n");confirm.Close();};
                buttons.Children.Add(keep);confirm.Content=buttons;confirm.ShowDialog();
            } else app.Shutdown();
        };
        window.Loaded+=(sender,eventArgs)=>{
            if(args[3]=="initialize")window.Hide();
            if(args[3]=="background-cleanup-foreign") {
                var other=new Window {Title="Developer Tools - file:///C:/Claude/main_window/index.html",Owner=window,Width=260,Height=120};
                other.Closing+=(s,e)=>File.AppendAllText(args[2],"foreign-closed\\n");other.Show();
            }
            File.WriteAllText(args[0],Process.GetCurrentProcess().Id.ToString());
        };
        timer.Start();app.Run(window);
    }
}
''', encoding='utf-8')
        built = subprocess.run([str(self.framework/'csc.exe'), '/nologo', '/target:winexe', '/platform:x64',
            '/out:'+str(fixture), '/reference:System.Xaml.dll',
            *['/reference:'+str(self.framework/'WPF'/dll) for dll in
              ('PresentationFramework.dll', 'PresentationCore.dll', 'WindowsBase.dll',
               'UIAutomationTypes.dll', 'UIAutomationProvider.dll')], str(source)],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)

        # This desktop is never activated or switched to. All tested apps are
        # disposable fixtures; the real Claude and user's input remain untouched.
        for iteration, mode in enumerate(('normal',) * 10 + ('pending', 'dispatch-error', 'native-popup', 'initialize',
                     'background-connect', 'background-cancel', 'background-cleanup',
                     'background-cleanup-cancel', 'background-cleanup-foreign')):
            with self.subTest(mode=mode, iteration=iteration):
                prefix = str(iteration)+'-'+mode
                ready, stop, marker = (self.folder/(prefix+suffix) for suffix in ('.ready', '.stop', '.invoked'))
                app_output = self.folder/(prefix+'.app.log')
                app = launch([fixture, ready, stop, marker, mode], app_output)
                helper = None
                try:
                    deadline = time.monotonic()+10
                    while not ready.exists() and not exited(app) and time.monotonic() < deadline:
                        time.sleep(.05)
                    self.assertTrue(ready.exists(), 'inactive-desktop fixture did not initialize')
                    output = self.folder/(prefix+'.quit.log')
                    action = '--quit'
                    if mode == 'initialize' or mode.startswith('background-'):
                        script = self.folder/'connector.js'
                        script.write_text('/* codex bridge connector */void 0;', encoding='utf-8')
                        action = ('--connect-background=' if mode.startswith('background-') else '')+str(script)
                    if mode.startswith('background-cleanup'):
                        action = '--close-background-devtools'
                    arguments = [self.helper, app.pid, action, fixture]
                    if mode in ('background-cancel', 'background-cleanup-cancel'):
                        cancel = self.folder/'background.cancel'
                        cancel.write_text('cancel', encoding='utf-8')
                        arguments.append(cancel)
                    started = time.monotonic()
                    helper = launch(arguments, output)
                    code = wait(helper, 15)
                    if mode == 'initialize':
                        self.assertEqual(code, 1, output.read_text(encoding='utf-8-sig'))
                        self.assertIn('桌面不可用', output.read_text(encoding='utf-8-sig'))
                        self.assertLess(time.monotonic()-started, 3, 'initialization must fail before window restore')
                        self.assertFalse(marker.exists())
                        self.assertFalse(exited(app))
                        self.assertEqual(user.GetForegroundWindow(), foreground)
                        continue
                    replies = output.read_text(encoding='utf-8-sig').splitlines()
                    if mode.startswith('background-cleanup'):
                        self.assertEqual(code, 1 if mode.endswith('-cancel') else 0, replies)
                        self.assertTrue(replies)
                        if not mode.endswith('-cancel'):
                            self.assertEqual(replies, ['Background DevTools closed'])
                        self.assertLess(time.monotonic()-started, 8)
                        self.assertFalse(marker.exists(), 'main and foreign DevTools must remain open')
                        self.assertFalse(exited(app))
                        self.assertEqual(user.GetForegroundWindow(), foreground)
                        continue
                    result = json.loads(replies[-1])
                    if mode.startswith('background-'):
                        self.assertEqual(code, 1, result)
                        self.assertEqual(result['setupState'], 'failed')
                        self.assertEqual(result['submission'], 'none')
                        self.assertEqual(result['pid'], app.pid)
                        self.assertTrue(result['reason'].strip())
                        self.assertFalse(any(json.loads(line).get('connectPhase') == 'dispatching'
                                             for line in replies))
                        self.assertLess(time.monotonic()-started, 8)
                        self.assertFalse(marker.exists())
                        self.assertFalse(exited(app))
                        self.assertEqual(user.GetForegroundWindow(), foreground)
                        continue
                    if mode == 'native-popup':
                        # WPF's standard popup currently does not expose Exit
                        # here. Keep this real provider limitation covered:
                        # fail fast, no foreground fallback or fabricated exit.
                        self.assertEqual(code, 1, result)
                        self.assertEqual(result['quitState'], 'failed', result)
                        self.assertIn('桌面不可用', result['reason'])
                        self.assertLess(time.monotonic()-started, 12)
                        self.assertFalse(marker.exists())
                        self.assertEqual(len(replies), 1, 'unsubmitted failure must not report dispatch')
                        self.assertFalse(exited(app))
                        self.assertEqual(user.GetForegroundWindow(), foreground)
                        continue
                    diagnostic = {'replies': replies, 'appExited': exited(app),
                                  'invoked': marker.read_text(encoding='utf-8') if marker.exists() else None,
                                  'appLog': app_output.read_text(encoding='utf-8-sig')}
                    self.assertEqual(code, 0, diagnostic)
                    self.assertEqual([json.loads(line) for line in replies[:-1]],
                                     [{'quitPhase': 'dispatching', 'pid': app.pid}])
                    if mode == 'dispatch-error':
                        self.assertEqual(result['quitState'], 'submitted', result)
                        self.assertEqual(marker.read_text(encoding='utf-8'), 'exit\nprovider-error\n')
                        self.assertFalse(exited(app), 'must wait for native saving after an uncertain Invoke')
                        self.assertEqual(wait(app, 8), 0)
                        self.assertEqual(user.GetForegroundWindow(), foreground)
                        continue
                    self.assertEqual(marker.read_text(encoding='utf-8'), 'exit\n')
                    if mode == 'normal':
                        self.assertIn(result['quitState'], ('exited', 'submitted'))
                        self.assertEqual(wait(app, 5), 0)
                    else:
                        self.assertEqual(result['quitState'], 'pending', result)
                        self.assertFalse(exited(app), 'native confirmation must remain open')
                    self.assertEqual(user.GetForegroundWindow(), foreground)
                finally:
                    stop.write_text('stop', encoding='utf-8')
                    wait(app, 46)
                    if helper is not None:
                        wait(helper, 10)
