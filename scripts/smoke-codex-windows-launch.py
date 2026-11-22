#!/usr/bin/env python3
"""Explicit interactive Windows smoke: isolated GUI, native stdio, restored user env.

Run in the user's interactive desktop, not SSH session 0. No real Codex tasks,
accounts or model requests are touched. Keep evidence under the supplied area.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CS = r'''
using System;using System.IO;using System.Diagnostics;using System.Threading;
using System.Text;using System.Web.Script.Serialization;
class Fixture {
 static JavaScriptSerializer json=new JavaScriptSerializer();
 static string Q(string value){return "\""+value.Replace("\"","\\\"")+"\"";}
 static int Main(string[] args){
#if NATIVE
  Console.InputEncoding=new UTF8Encoding(false);Console.OutputEncoding=new UTF8Encoding(false);
  string line;while((line=Console.ReadLine())!=null){
   File.WriteAllText(Path.Combine(Environment.GetEnvironmentVariable("CODEX_HOME"),"native-received.json"),line);
   dynamic p=json.DeserializeObject(line);string method=p.ContainsKey("method")?(string)p["method"]:"";
   if(!p.ContainsKey("id"))continue;
   object result=method=="initialize"?(object)new {codexHome=Environment.GetEnvironmentVariable("CODEX_HOME"),futureFeature=true}:new {data=new object[0],nextCursor=(string)null};
   Console.WriteLine(json.Serialize(new {id=p["id"],result=result}));Console.Out.Flush();
  }return 0;
#else
  string home=args[0];Directory.CreateDirectory(home);
  try {
   Environment.SetEnvironmentVariable("CODEX_HOME",home);
   if(args.Length>1&&args[1]=="--utility"){
    var info=new ProcessStartInfo(Process.GetCurrentProcess().MainModule.FileName,Q(home)+" --host");info.UseShellExecute=false;info.CreateNoWindow=true;
    using(var child=Process.Start(info)){child.WaitForExit();return child.ExitCode;}
   }
   string launcher=Environment.GetEnvironmentVariable("CODEX_CLI_PATH");
   var start=new ProcessStartInfo(launcher,"app-server --stdio --future-flag");start.UseShellExecute=false;start.CreateNoWindow=true;
   start.RedirectStandardInput=true;start.RedirectStandardOutput=true;start.RedirectStandardError=true;
   using(var child=Process.Start(start)){
    child.ErrorDataReceived+=(sender,e)=>{if(e.Data!=null)File.AppendAllText(Path.Combine(home,"adapter.stderr.log"),e.Data+Environment.NewLine);};child.BeginErrorReadLine();
    File.WriteAllText(Path.Combine(home,"gui-start.json"),json.Serialize(new {pid=Process.GetCurrentProcess().Id,childPid=child.Id,launcher=launcher}));
    child.StandardInput.WriteLine(json.Serialize(new {id=1,method="initialize",@params=new {clientInfo=new {name="Codex Desktop",version="future-additive"}}}));child.StandardInput.Flush();
    var read=child.StandardOutput.ReadLineAsync();if(!read.Wait(25000))throw new Exception("initialize timeout");
    string reply=read.Result;if(reply==null)throw new Exception("no native reply; see adapter.stderr.log");
    File.WriteAllText(Path.Combine(home,"gui.json"),json.Serialize(new {pid=Process.GetCurrentProcess().Id,launcher=launcher,initialize=json.DeserializeObject(reply)}));
    DateTime end=DateTime.UtcNow.AddSeconds(45);
    while(!File.Exists(Path.Combine(home,"stop"))&&DateTime.UtcNow<end)Thread.Sleep(100);
    child.StandardInput.Close();if(!child.WaitForExit(25000))throw new Exception("native normal exit timeout");
    File.WriteAllText(Path.Combine(home,"normal-exit"),child.ExitCode.ToString());return child.ExitCode;
   }
  }catch(Exception error){File.WriteAllText(Path.Combine(home,"gui-error.txt"),error.ToString());return 1;}
#endif
 }
}
'''


def close_probe(area, owned):
    from bridge.clients.codex import desktop_runtime as runtime
    row=owned['app']
    # Only the test's copied GUI in its empty home is disposable. This is not
    # a production quit fallback and can never target the installed App.
    if not Path(row['image']).is_relative_to(area/'Codex Probe'):
        raise ValueError('Refusing to close a non-probe App')
    if runtime.process_identity(row['pid'])!=row:
        raise ValueError('Probe generation changed')
    result=subprocess.run([str(area/'codex-quit-helper.exe'),str(row['pid']),'--quit',
        row['image'],str(row['created'])],capture_output=True,timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW)
    end=time.monotonic()+5
    while time.monotonic()<end:
        try:runtime.process_identity(row['pid'])
        except ProcessLookupError:
            runtime.wait_stopped(owned['home']);return 'native-quit'
        time.sleep(.2)
    if runtime.process_identity(row['pid'])!=row:
        raise ValueError('Probe generation changed before cleanup')
    os.kill(row['pid'],signal.SIGTERM)
    runtime.wait_stopped(owned['home'])
    return 'terminated-isolated-probe'


def verify_open_probe(area):
    from bridge.clients.codex import desktop_runtime as runtime
    from bridge.platforms.windows import codex_launch
    home=area/'real-profile'
    record=runtime._read(runtime.directory(home)/'active.json');runtime.verify_record(record)
    assert record['home']==str(home)
    assert Path(record['app']['image']).is_relative_to(area/'Codex Probe')
    client=runtime.connect(home,record['cli'])
    try:
        rows=client.request('thread/list',{'limit':1},timeout=8)['data']
        assert rows==[]  # This profile contains no original user chats or tasks.
    finally:client.close()
    result={'nativeReadSucceeded':True,'transport':record.get('transport'),
        'modelRequests':0,'installedCopy':True,'emptyIsolatedProfile':True}
    result['cleanup']=close_probe(area,record)
    (area/'real-gui-verification.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result),flush=True)


def real_gui(area, application, cli, helper, compiler):
    """Installed GUI copy, separate home/userData, zero model calls."""
    from unittest.mock import patch
    from bridge.clients.codex import desktop_runtime as runtime
    from bridge.platforms.windows import codex_launch, desktop as native, codex_quit
    original_app=Path(application).resolve();original_cli=Path(cli).resolve()
    # Store activation can return the already-running App despite independent
    # launch arguments. Copy installed, signed files; never close that App.
    import shutil
    copied=area/'Codex Probe'
    shutil.copytree(original_app.parent,copied)
    app=copied/original_app.name;cli=copied/original_cli.relative_to(original_app.parent)
    pids=native.processes(lambda image:os.path.normcase(image)==os.path.normcase(str(original_app)))
    identities={pid:runtime.process_identity(pid) for pid in pids}
    before={pid:row for pid,row in identities.items() if row['parent'] not in identities}
    home=area/'real-profile';home.mkdir(exist_ok=True)
    with patch.object(sys,'frozen',True,create=True):runtime.configure(home,app,cli)
    value=runtime.settings(home)
    command=[sys.executable,str(ROOT/'desktop.py'),'codex-desktop-launch','--configuration',
             str(runtime.directory(home)/'settings.json'),'--']
    dispatcher=codex_launch.install(runtime.directory(home),value,helper,command)
    quit_helper=area/'codex-quit-helper.exe'
    framework=compiler.parent
    subprocess.run([str(compiler),'/nologo','/target:exe','/platform:x64','/out:'+str(quit_helper),
        *['/reference:'+str(framework/'WPF'/name) for name in
          ('UIAutomationClient.dll','UIAutomationTypes.dll','WindowsBase.dll')],
        str(ROOT/'bridge/platforms/windows/codex-quit-helper.cs')],check=True,timeout=40)
    owned=None
    try:
        native.start(app,home,{'CODEX_HOME':str(home),'CODEX_SQLITE_HOME':str(home),
            'CODEX_ELECTRON_USER_DATA_PATH':str(area/'real-electron'),'CODEX_CLI_PATH':dispatcher,
            'CODEX_APP_SERVER_WS_URL':'','CODEX_APP_SERVER_FORCE_CLI':'0'},
            arguments=('--user-data-dir='+str(area/'real-electron'),),new_instance=True)
        end=time.monotonic()+40
        while time.monotonic()<end:
            try:
                candidate=runtime._read(runtime.directory(home)/'active.json')
                if candidate.get('ready'):
                    runtime.verify_record(candidate);owned=candidate;break
            except (OSError,ValueError,KeyError):pass
            time.sleep(.2)
        if owned is None:raise TimeoutError('real Codex GUI did not establish its native connection')
        assert owned['app']['pid'] not in before
        client=runtime.connect(home,str(cli))
        try:assert isinstance(client.request('thread/list',{'limit':1},timeout=8)['data'],list)
        finally:client.close()
        assert all(runtime.process_identity(pid)==row for pid,row in before.items())
        result={'nativeReadSucceeded':True,'transport':owned.get('transport','legacy-stdio'),
                'originalAppPids':list(before),'originalAppPreserved':True,'modelRequests':0,
                'installedCopy':True}
        (area/'real-gui-read.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        return result
    finally:
        active=runtime.directory(home)/'active.json'
        if owned is None and active.exists():
            candidate=runtime._read(active)
            if candidate.get('home')==str(home):owned=candidate
        if owned and owned['app']['pid'] not in before:
            close_probe(area,owned)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--area',required=True)
    parser.add_argument('--application')
    parser.add_argument('--cli')
    parser.add_argument('--verify-open',action='store_true')
    args=parser.parse_args()
    area=Path(args.area).resolve();area.mkdir(parents=True,exist_ok=True)
    if args.verify_open:
        verify_open_probe(area);return
    result={'complete':False,'modelRequests':0,'checks':[]}
    target=area/'result.json'
    from bridge.clients.codex import desktop_runtime as runtime
    from bridge.platforms.windows import codex_launch
    previous=codex_launch.get_environment()
    # Installing into an isolated home must not overwrite a real user's hook.
    if previous:
        raise ValueError('Existing CODEX_CLI_PATH preserved; use a separate test user')
    try:
        compiler=Path(os.environ['WINDIR'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        app=area/'App/ChatGPT.exe';app.parent.mkdir(exist_ok=True)
        native=app.parent/'resources/codex.exe';native.parent.mkdir(exist_ok=True)
        (native.parent/'app.asar').write_bytes(b'isolated fixture')
        source=area/'fixture.cs';source.write_text(CS,encoding='utf-8')
        helper=area/'codex-launcher.exe'
        for output,file,flags in ((app,source,[]),(native,source,['/define:NATIVE']),
                                  (helper,ROOT/'bridge/platforms/windows/codex-launcher.cs',[])):
            subprocess.run([str(compiler),'/nologo','/target:exe','/platform:x64',
                '/reference:System.Web.Extensions.dll','/reference:Microsoft.CSharp.dll',
                '/out:'+str(output),*flags,str(file)],check=True,timeout=40)
        subprocess.run([str(helper),'--self-check'],check=True,timeout=8)
        # The real App may select a downloaded CLI outside its install folder.
        # Exercise that layout as well as the bundled fallback used by the GUI.
        import shutil
        external=area/'downloaded-runtime/codex.exe';external.parent.mkdir(exist_ok=True)
        shutil.copyfile(native,external);native=external
        home=area/'profile';home.mkdir(exist_ok=True)
        # The fixture runs Python source as the gateway. Production requires a
        # frozen launcher; preserve that product gate outside this isolated test.
        from unittest.mock import patch
        with patch.object(sys,'frozen',True,create=True):
            runtime.configure(home,app,native)
        value=runtime.settings(home)
        command=[sys.executable,str(ROOT/'desktop.py'),'codex-desktop-launch','--configuration',
                 str(runtime.directory(home)/'settings.json'),'--']
        dispatcher=codex_launch.install(runtime.directory(home),value,helper,command)
        # No CLI override is supplied to the shell call; it must come from the
        # environment reload in Explorer, the same source as a normal icon.
        os.environ.pop('CODEX_CLI_PATH',None)
        for attempt,mode in enumerate(('', '--utility'),1):
            for file in ('stop','gui.json','gui-error.txt','normal-exit'):(home/file).unlink(missing_ok=True)
            link=area/'Codex fixture.lnk'
            ps=r'''$w=New-Object -ComObject WScript.Shell;$s=$w.CreateShortcut($env:CMB_LINK);$s.TargetPath=$env:CMB_APP;$s.Arguments='"'+$env:CMB_PROFILE+'" '+$env:CMB_MODE;$s.Save();$shell=New-Object -ComObject Shell.Application;$windows=$shell.Windows();$hwnd=0;$desktop=$windows.FindWindowSW(0,0,8,[ref]$hwnd,1);if($null -eq $desktop){throw 'Explorer desktop broker unavailable'};$desktop.Document.Application.ShellExecute($env:CMB_LINK,'','','open',0)'''
            subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',ps],
                env={**os.environ,'CMB_LINK':str(link),'CMB_APP':str(app),
                     'CMB_PROFILE':str(home),'CMB_MODE':mode},check=True,timeout=15)
            end=time.monotonic()+35
            while not (home/'gui.json').exists() and time.monotonic()<end:
                if (home/'gui-error.txt').exists():raise RuntimeError((home/'gui-error.txt').read_text(encoding='utf-8',errors='replace'))
                time.sleep(.2)
            gui=json.loads((home/'gui.json').read_text(encoding='utf-8'))
            assert gui['launcher'].casefold()==dispatcher.casefold()
            client=runtime.connect(home,str(native))
            try:
                assert isinstance(client.request('thread/list',{'limit':1},timeout=8)['data'],list)
            finally:client.close()
            record=runtime._read(runtime.directory(home)/'active.json');runtime.verify_record(record)
            assert record['dispatcher']['image'].casefold()==dispatcher.casefold()
            assert ('host' in record)==bool(mode)
            result['checks'].append({'attempt':attempt,'utilityHost':bool(mode),'shellInheritedOverride':True,
                                     'nativeReadSucceeded':True,'allProcessGenerationsVerified':True})
            (home/'stop').write_text('normal')
            end=time.monotonic()+30
            while not (home/'normal-exit').exists() and time.monotonic()<end:time.sleep(.2)
            assert (home/'normal-exit').read_text()=='0'
            runtime.wait_stopped(home)
        if args.application or args.cli:
            if not (args.application and args.cli):raise ValueError('Both application and CLI are required')
            codex_launch.set_environment(previous)
            result['realGui']=real_gui(area,args.application,args.cli,helper,compiler)
        result['complete']=True
    except BaseException as error:
        result['error']=type(error).__name__+': '+str(error)
        raise
    finally:
        if 'home' in locals():(home/'stop').write_text('normal cleanup')
        codex_launch.set_environment(previous)
        result['originalUserEnvironmentRestored']=codex_launch.get_environment()==previous
        target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
