"""One real Explorer-broker regression; only disposable fixture processes are started."""
import ctypes as c
from ctypes import wintypes as w
import json,os,shutil,subprocess,sys,tempfile,time,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if ROOT.name=='.tmp':ROOT=ROOT.parent
SOURCE=Path(os.environ.get('CMB_PREVIEW_LAUNCHER_SOURCE',str(ROOT)))
CS=r'''
using System;
using System.IO;
using System.Collections;
using System.Diagnostics;
using System.Windows.Forms;
using System.Web.Script.Serialization;
class Fixture {
 [STAThread] static void Main(){
  string home=Environment.GetEnvironmentVariable("CMB_DATA_DIR");
  Directory.CreateDirectory(home);
  bool updates=true;foreach(DictionaryEntry row in Environment.GetEnvironmentVariables())
   if(row.Key.ToString().StartsWith("CMB_UPDATE_",StringComparison.OrdinalIgnoreCase))updates=false;
  File.WriteAllText(Path.Combine(home,"fixture.json"),new JavaScriptSerializer().Serialize(new {
   pid=Process.GetCurrentProcess().Id,dataDirectory=home,updatesCleared=updates,
   electronNodeCleared=Environment.GetEnvironmentVariable("ELECTRON_RUN_AS_NODE")==null}));
  var context=new ApplicationContext();var timer=new Timer();timer.Interval=100;
  var deadline=DateTime.UtcNow.AddSeconds(90);
  timer.Tick+=(sender,e)=>{
   File.WriteAllText(Path.Combine(home,"heartbeat"),DateTime.UtcNow.Ticks.ToString());
   if(File.Exists(Path.Combine(home,"stop"))||DateTime.UtcNow>deadline){
    timer.Stop();File.WriteAllText(Path.Combine(home,"normal-exit"),"normal");context.ExitThread();
   }
  };
  timer.Start();Application.Run(context);
 }
}
'''
class Limits(c.Structure):
 _fields_=[('process',c.c_int64),('job',c.c_int64),('flags',w.DWORD),('minimum',c.c_size_t),('maximum',c.c_size_t),('active',w.DWORD),('affinity',c.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]
class IO(c.Structure):
 _fields_=[(name,c.c_uint64) for name in ('reads','writes','other','readbytes','writebytes','otherbytes')]
class Extended(c.Structure):
 _fields_=[('limits',Limits),('io',IO),('processmemory',c.c_size_t),('jobmemory',c.c_size_t),('peakprocess',c.c_size_t),('peakjob',c.c_size_t)]
@unittest.skipUnless(sys.platform=='win32','Windows Explorer broker required')
class PreviewLauncherTest(unittest.TestCase):
 def test_gateway_root_survives_invoking_job(self):
  import _winapi
  framework=Path(os.environ['WINDIR'])/'Microsoft.NET/Framework64/v4.0.30319'
  if not (framework/'csc.exe').is_file():self.skipTest('Windows .NET compiler required')
  # Keep the result for diagnosing an independent broker; do not delete an app while it is alive.
  folder=Path(tempfile.mkdtemp(prefix='preview launch 中文 % ! ',dir=ROOT/'.tmp'))
  (folder/'scripts').mkdir()
  shutil.copy2(SOURCE/'start-preview3-windows.cmd',folder/'start-preview3-windows.cmd')
  shutil.copy2(SOURCE/'scripts/start-preview3-windows.ps1',folder/'scripts/start-preview3-windows.ps1')
  app=folder/'dist/desktop/win-unpacked/Codex Mobile Bridge.exe';app.parent.mkdir(parents=True)
  source=folder/'fixture.cs';source.write_text(CS,encoding='utf-8')
  built=subprocess.run([str(framework/'csc.exe'),'/nologo','/target:winexe','/reference:System.Windows.Forms.dll','/reference:System.Web.Extensions.dll','/out:'+str(app),str(source)],capture_output=True,text=True,timeout=30)
  self.assertEqual(built.returncode,0,built.stdout+built.stderr)
  k=c.WinDLL('kernel32',use_last_error=True)
  k.CreateJobObjectW.argtypes=[c.c_void_p,w.LPCWSTR];k.CreateJobObjectW.restype=w.HANDLE
  k.SetInformationJobObject.argtypes=[w.HANDLE,c.c_int,c.c_void_p,w.DWORD]
  k.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE]
  k.IsProcessInJob.argtypes=[w.HANDLE,w.HANDLE,c.POINTER(w.BOOL)]
  k.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];k.OpenProcess.restype=w.HANDLE
  k.WaitForSingleObject.argtypes=[w.HANDLE,w.DWORD]
  k.ResumeThread.argtypes=[w.HANDLE];k.CloseHandle.argtypes=[w.HANDLE]
  job=k.CreateJobObjectW(None,None);self.assertTrue(job)
  info=Extended();info.limits.flags=0x2000
  self.assertTrue(k.SetInformationJobObject(job,9,c.byref(info),c.sizeof(info)))
  child=parent=thread=None;home=folder/'.local/windows-preview3-user'
  result={'folder':str(folder),'jobKillOnClose':True,'jobAllowsBreakaway':False}
  try:
   startup=subprocess.STARTUPINFO();startup.dwFlags|=1;startup.wShowWindow=0
   shell=Path(os.environ['WINDIR'])/'System32/cmd.exe'
   command=[str(shell),'/d','/c',str(folder/'start-preview3-windows.cmd')]
   parent,thread,parent_pid,_=_winapi.CreateProcess(str(shell),subprocess.list2cmdline(command),None,None,False,0x08000004,None,str(folder),startup)
   self.assertTrue(k.AssignProcessToJobObject(job,parent));self.assertNotEqual(k.ResumeThread(thread),0xffffffff)
   receipt=folder/'.local/windows-preview3-launch/latest.json';deadline=time.monotonic()+30
   while not receipt.exists() and time.monotonic()<deadline:time.sleep(.1)
   self.assertTrue(receipt.exists(),'No verified startup receipt: '+str(folder))
   value=json.loads(receipt.read_text(encoding='utf-8'));leaf=json.loads((home/'fixture.json').read_text(encoding='utf-8-sig'))
   self.assertEqual(value['pid'],leaf['pid']);self.assertEqual(leaf['dataDirectory'],str(home))
   self.assertTrue(leaf['updatesCleared']);self.assertTrue(leaf['electronNodeCleared'])
   child=k.OpenProcess(0x1000|0x100000,False,value['pid']);self.assertTrue(child)
   inside=w.BOOL();self.assertTrue(k.IsProcessInJob(child,job,c.byref(inside)))
   self.assertFalse(inside.value)
   result.update(receipt=value,fixture=leaf,childInInvokingJob=bool(inside.value))
   k.CloseHandle(job);job=None
   self.assertEqual(k.WaitForSingleObject(parent,3000),0)
   self.assertEqual(k.WaitForSingleObject(child,300),258)
   before=(home/'heartbeat').read_text();time.sleep(.3);after=(home/'heartbeat').read_text()
   self.assertNotEqual(before,after)
   result.update(parentExited=True,childSurvived=True,heartbeatContinues=True,success=True)
  finally:
   home.mkdir(parents=True,exist_ok=True);(home/'stop').write_text('normal cleanup')
   if job:k.CloseHandle(job)
   if child:
    result['normalFixtureExit']=k.WaitForSingleObject(child,5000)==0 and (home/'normal-exit').exists();k.CloseHandle(child)
   for handle in (thread,parent):
    if handle:k.CloseHandle(handle)
   (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
   print('Fixture result: '+str(folder/'result.json'),flush=True)
if __name__=='__main__':unittest.main()
