"""Exercise Codex quit only against an isolated Electron app on an inactive desktop."""
import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from ctypes import wintypes as w
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class InactiveDesktop:
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

    def __init__(self):
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.user.CreateDesktopW.argtypes = [w.LPCWSTR, w.LPCWSTR, w.LPVOID, w.DWORD, w.DWORD, w.LPVOID]
        self.user.CreateDesktopW.restype = w.HANDLE
        self.user.CloseDesktop.argtypes = [w.HANDLE]
        self.user.GetForegroundWindow.restype = w.HWND
        self.kernel.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.POINTER(self.Security), w.DWORD, w.DWORD, w.HANDLE]
        self.kernel.CreateFileW.restype = w.HANDLE
        self.kernel.CreateProcessW.argtypes = [w.LPCWSTR, w.LPWSTR, w.LPVOID, w.LPVOID, w.BOOL, w.DWORD, w.LPVOID, w.LPCWSTR, ctypes.POINTER(self.Startup), ctypes.POINTER(self.ProcessInfo)]
        self.kernel.CreateProcessW.restype = w.BOOL
        self.kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        self.kernel.GetExitCodeProcess.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
        self.kernel.CloseHandle.argtypes = [w.HANDLE]
        self.name = 'CodexQuitFixture_' + uuid.uuid4().hex
        self.handle = self.user.CreateDesktopW(self.name, None, None, 0, 0x01ff, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.foreground = self.user.GetForegroundWindow()
        self.processes = []

    def start(self, command, output, cwd):
        security = self.Security(ctypes.sizeof(self.Security), None, True)
        log = self.kernel.CreateFileW(str(output), 0x40000000, 3, ctypes.byref(security), 2, 0x80, None)
        if log == w.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        startup = self.Startup()
        startup.cb, startup.desktop, startup.flags = ctypes.sizeof(startup), self.name, 0x100
        startup.stdout = startup.stderr = log
        process = self.ProcessInfo()
        environment = {key: value for key, value in os.environ.items() if key.upper() != 'ELECTRON_RUN_AS_NODE'}
        block = ctypes.create_unicode_buffer('\0'.join(key+'='+value for key, value in sorted(environment.items()))+'\0\0')
        try:
            if not self.kernel.CreateProcessW(str(command[0]), ctypes.create_unicode_buffer(subprocess.list2cmdline([str(part) for part in command])),
                    None, None, True, 0x400, block, str(cwd), ctypes.byref(startup), ctypes.byref(process)):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            self.kernel.CloseHandle(log)
        self.kernel.CloseHandle(process.thread)
        self.processes.append(process.process)
        return process.process, process.pid

    def wait(self, process, seconds):
        if self.kernel.WaitForSingleObject(process, int(seconds*1000)) != 0:
            return None
        result = w.DWORD()
        if not self.kernel.GetExitCodeProcess(process, ctypes.byref(result)):
            raise ctypes.WinError(ctypes.get_last_error())
        return result.value

    def close(self):
        for process in self.processes:
            self.kernel.CloseHandle(process)
        self.user.CloseDesktop(self.handle)


MAIN = r'''
const {app, BrowserWindow, ipcMain, dialog} = require('electron');
const fs = require('fs'), path = require('path');
const base = path.dirname(__dirname), state = JSON.parse(fs.readFileSync(path.join(base, 'scenario.json')));
app.setPath('userData', path.join(base, 'profile'));
app.commandLine.appendSwitch('force-renderer-accessibility');
app.commandLine.appendSwitch('disable-gpu');
let window, stopping = false, prompted = false;
if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => {
    fs.appendFileSync(path.join(base, 'reopens.log'), 'reopen\n');
    if (window) window.show();
  });
  app.on('before-quit', event => {
    if (state.mode === 'pending' && !stopping) {
      event.preventDefault();
      if (!prompted) {
        prompted = true;
        dialog.showMessageBox(window, {type: 'question', message: 'Fixture pending work', buttons: ['Cancel', 'Quit anyway'], cancelId: 0});
      }
    }
  });
  app.whenReady().then(async () => {
    window = new BrowserWindow({show: state.mode !== 'hidden', width: 600, height: 400,
      title: 'Codex menu fixture', webPreferences: {preload: path.join(__dirname, 'preload.js')}});
    window.removeMenu();
    window.on('close', event => {if (!stopping) {event.preventDefault(); window.hide();}});
    ipcMain.on('fixture-quit', event => {
      if (event.sender !== window.webContents) return;
      fs.appendFileSync(path.join(base, 'dispatches.log'), 'quit\n');
      stopping = state.mode !== 'pending';
      app.quit();
    });
    ipcMain.on('fixture-expanded', () => {
      fs.appendFileSync(path.join(base, 'expands.log'), 'expand\n');
      if (state.mode === 'late-cancel') fs.writeFileSync(path.join(base, 'cancel'), 'cancel');
    });
    await window.loadFile(path.join(__dirname, 'index.html'));
    fs.writeFileSync(path.join(base, 'ready.json'), JSON.stringify({pid: process.pid, handle: window.getNativeWindowHandle().readBigUInt64LE().toString()}));
    const deadline = Date.now() + 40000;
    setInterval(() => {if (fs.existsSync(path.join(base, 'stop')) || Date.now() > deadline) {stopping = true; app.quit();}}, 100).unref();
  });
}
'''
PRELOAD = "const {contextBridge,ipcRenderer}=require('electron');contextBridge.exposeInMainWorld('fixture',{quit:()=>ipcRenderer.send('fixture-quit'),expanded:()=>ipcRenderer.send('fixture-expanded')});"
HTML = '''<!doctype html><meta charset="utf-8"><title>Codex menu fixture</title>
<div role="menubar" aria-label="Application menu"><button role="menuitem" id="application-menu-trigger-file-menu" aria-haspopup="menu" aria-expanded="false">File</button></div>
<div id="application-menu-content" role="menu" hidden></div>
<script>
const file=document.querySelector('button'), content=document.querySelector('[role=menu]');
const mode=MODE, label=LABEL, accelerator=mode==='wrong-accelerator'?'Ctrl+W':'Ctrl+Q';
function open(){
  file.setAttribute('aria-expanded','true');content.hidden=false;
  window.fixture.expanded();
  if(mode==='late-cancel'){setTimeout(render,500);return;}
  render();
}
function render(){
  content.innerHTML='<div role="menuitem" tabindex="0">退出登录</div><div role="menuitem" tabindex="0" class="quit"><span>'+label+'</span> <span>'+accelerator+'</span></div>';
  if(mode==='duplicate')content.innerHTML+='<div role="menuitem" tabindex="0" class="quit">'+label+' '+accelerator+'</div>';
  for(const item of document.querySelectorAll('.quit'))item.addEventListener('click',()=>window.fixture.quit());
}
file.addEventListener('click',()=>file.getAttribute('aria-expanded')==='false'?open():close());
function close(){file.setAttribute('aria-expanded','false');content.hidden=true;content.replaceChildren();}
</script>'''


@unittest.skipUnless(sys.platform == 'win32', 'Windows native helper required')
class CodexNativeQuit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.framework = Path(os.environ.get('WINDIR', 'C:/Windows'))/'Microsoft.NET/Framework64/v4.0.30319'
        if not (cls.framework/'csc.exe').is_file():
            raise unittest.SkipTest('Windows .NET Framework compiler required')
        cls.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.folder = Path(cls.temp.name)
        cls.helper = cls.folder/'codex-quit-helper.exe'
        built = subprocess.run([str(cls.framework/'csc.exe'), '/nologo', '/target:exe', '/platform:x64', '/out:'+str(cls.helper),
            *['/reference:'+str(cls.framework/'WPF'/name) for name in ('UIAutomationClient.dll', 'UIAutomationTypes.dll', 'WindowsBase.dll')],
            str(ROOT/'bridge/integrations/native/codex-quit-helper.cs')], capture_output=True, text=True, timeout=30)
        if built.returncode:
            raise AssertionError(built.stdout+built.stderr)

    def test_exact_menu_selectors_and_cancellation(self):
        result = subprocess.run([str(self.helper), '--self-check'], capture_output=True, text=True, encoding='utf-8-sig', timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertIn('cancellation guards OK', result.stdout)

    def test_wrong_process_identity_has_no_dispatch(self):
        result = subprocess.run([str(self.helper), str(os.getpid()), '--quit', str(self.helper)], capture_output=True, text=True, encoding='utf-8-sig', timeout=10)
        self.assertEqual(result.returncode, 1)
        rows = result.stdout.strip().splitlines()
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0])['quitState'], 'failed')

    def test_electron_menu_on_inactive_desktop(self):
        runtime = ROOT/'node_modules/electron/dist'
        if not (runtime/'electron.exe').is_file():
            self.skipTest('npm Electron runtime required')
        target = self.folder/'runtime'
        def link(source, destination):
            try:
                os.link(source, destination)
            except OSError:
                shutil.copy2(source, destination)
            return destination
        shutil.copytree(runtime, target, copy_function=link)
        app = target/'resources/app'; app.mkdir()
        (app/'package.json').write_text(json.dumps({'name': 'codex-menu-fixture', 'version': '1.0.0', 'main': 'main.js'}), encoding='utf-8')
        (app/'main.js').write_text(MAIN, encoding='utf-8')
        (app/'preload.js').write_text(PRELOAD, encoding='utf-8')
        state = target/'resources'
        for mode in ('normal', 'hidden', 'pending', 'duplicate', 'wrong-accelerator', 'cancelled', 'late-cancel'):
            with self.subTest(mode=mode):
                for name in ('stop', 'ready.json', 'dispatches.log', 'reopens.log', 'expands.log'):
                    (state/name).unlink(missing_ok=True)
                (state/'scenario.json').write_text(json.dumps({'mode': mode}), encoding='utf-8')
                label = 'Quit Codex' if mode == 'normal' else '退出 ChatGPT'
                (app/'index.html').write_text(HTML.replace('MODE', json.dumps(mode)).replace('LABEL', json.dumps(label)), encoding='utf-8')
                desktop = InactiveDesktop()
                app_process = None
                try:
                    app_log = state/'electron.log'
                    app_process, _ = desktop.start([target/'electron.exe', '--no-sandbox'], app_log, target)
                    deadline = time.monotonic()+12
                    while not (state/'ready.json').exists() and time.monotonic()<deadline and desktop.wait(app_process, 0) is None:
                        time.sleep(.1)
                    self.assertTrue((state/'ready.json').exists(), app_log.read_text(encoding='utf-8', errors='replace'))
                    pid = json.loads((state/'ready.json').read_text())['pid']
                    log = state/'helper.log'
                    cancel = state/'cancel'
                    cancel.unlink(missing_ok=True)
                    if mode == 'cancelled':
                        cancel.write_text('cancel', encoding='utf-8')
                    helper, _ = desktop.start([self.helper, str(pid), '--quit', target/'electron.exe', cancel], log, target)
                    exit_code = desktop.wait(helper, 23)
                    self.assertIsNotNone(exit_code, 'helper exceeded its deadline')
                    output = log.read_text(encoding='utf-8-sig')
                    rows = [json.loads(line) for line in output.splitlines()]
                    reply = rows[-1]
                    sent = (state/'dispatches.log').read_text().splitlines() if (state/'dispatches.log').exists() else []
                    expected = mode in ('normal', 'hidden', 'pending')
                    self.assertEqual(len(sent), int(expected), output)
                    self.assertEqual(sum(row.get('quitPhase') == 'dispatching' for row in rows), int(expected), output)
                    self.assertEqual(reply['pid'], pid)
                    if mode in ('normal', 'hidden'):
                        self.assertEqual(reply['quitState'], 'exited', output)
                        self.assertEqual(desktop.wait(app_process, 3), 0)
                    elif mode == 'pending':
                        self.assertEqual(reply['quitState'], 'pending', output)
                        self.assertIsNone(desktop.wait(app_process, 0), 'native confirmation was bypassed')
                    else:
                        self.assertEqual(reply['quitState'], 'failed', output)
                        self.assertIsNone(desktop.wait(app_process, 0), 'unverified command closed the app')
                    reopens = (state/'reopens.log').read_text().splitlines() if (state/'reopens.log').exists() else []
                    self.assertEqual(len(reopens), int(mode == 'hidden'), output)
                    if mode == 'late-cancel':
                        self.assertEqual((state/'expands.log').read_text().splitlines(), ['expand'])
                    self.assertEqual(desktop.user.GetForegroundWindow(), desktop.foreground)
                finally:
                    (state/'stop').write_text('stop', encoding='utf-8')
                    if app_process is not None:
                        self.assertEqual(desktop.wait(app_process, 42), 0, 'fixture did not exit through its own shutdown handler')
                    desktop.close()


if __name__ == '__main__':
    unittest.main()
