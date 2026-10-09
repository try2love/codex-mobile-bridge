// Invoke Codex's normal application-menu Quit command, never close or kill a window.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Windows.Automation;

class CodexQuit {
    [DllImport("user32.dll")] static extern bool IsWindow(IntPtr window);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr window);
    [DllImport("user32.dll")] static extern bool IsWindowEnabled(IntPtr window);
    [DllImport("user32.dll")] static extern IntPtr GetWindow(IntPtr window,uint command);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr window,out uint pid);
    [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr window,StringBuilder value,int size);
    [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr window,StringBuilder value,int size);
    [DllImport("user32.dll",EntryPoint="GetWindowLongPtrW")] static extern IntPtr GetWindowLongPtr(IntPtr window,int index);
    [DllImport("user32.dll",EntryPoint="GetWindowLongW")] static extern int GetWindowLong(IntPtr window,int index);
    [DllImport("dwmapi.dll")] static extern int DwmGetWindowAttribute(IntPtr window,uint attribute,out uint value,uint size);
    delegate bool EnumWindow(IntPtr window,IntPtr data);
    [DllImport("user32.dll")] static extern bool EnumWindows(EnumWindow callback,IntPtr data);
    [DllImport("kernel32.dll",SetLastError=true)] static extern IntPtr OpenProcess(uint access,bool inherit,int pid);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode)] static extern int GetApplicationUserModelId(IntPtr process,ref uint length,StringBuilder value);

    static string executable,cancelFile;
    static readonly int session=Process.GetCurrentProcess().SessionId;
    static string Json(string text) {return "\""+text.Replace("\\","\\\\").Replace("\"","\\\"").Replace("\r","\\r").Replace("\n","\\n").Replace("\t","\\t")+"\"";}
    static void Stage(string text) {Console.WriteLine(text);Console.Out.Flush();}
    static void Reply(string state,int pid,string reason) {Stage("{\"quitState\":"+Json(state)+",\"pid\":"+pid+",\"reason\":"+Json(reason)+"}");}
    sealed class Window {
        public IntPtr Handle,Owner;public bool Visible,Tool,Cloaked;public string ClassName,Title;
    }
    static List<Window> Windows(int pid) {
        var result=new List<Window>();
        EnumWindows(delegate(IntPtr handle,IntPtr data) {
            uint actual;GetWindowThreadProcessId(handle,out actual);if(actual!=pid)return true;
            var title=new StringBuilder(512);var name=new StringBuilder(128);
            GetWindowText(handle,title,title.Capacity);GetClassName(handle,name,name.Capacity);
            long style=IntPtr.Size==8?GetWindowLongPtr(handle,-20).ToInt64():GetWindowLong(handle,-20);uint cloaked;
            result.Add(new Window {Handle=handle,Owner=GetWindow(handle,4),Visible=IsWindowVisible(handle),Tool=(style&0x80)!=0,
                Cloaked=DwmGetWindowAttribute(handle,14,out cloaked,4)==0&&cloaked!=0,ClassName=name.ToString(),Title=title.ToString()});return true;
        },IntPtr.Zero);return result;
    }
    static bool MainWindow(Window window) {
        return window.Visible&&!window.Tool&&!window.Cloaked&&window.Owner==IntPtr.Zero&&window.ClassName=="Chrome_WidgetWin_1"&&
            !String.IsNullOrWhiteSpace(window.Title)&&!window.Title.StartsWith("DevTools",StringComparison.Ordinal)&&
            !window.Title.StartsWith("Developer Tools",StringComparison.Ordinal)&&!window.Title.StartsWith("开发者工具",StringComparison.Ordinal);
    }
    static bool PendingDialog(IEnumerable<Window> windows) {
        foreach(var window in windows) {
            if(!window.Visible||window.Cloaked)continue;
            if(window.ClassName=="#32770"||window.Owner!=IntPtr.Zero&&!window.Tool||MainWindow(window)&&!IsWindowEnabled(window.Handle))return true;
        }
        return false;
    }
    static bool Owned(Process process,long started) {
        try {return !process.HasExited&&process.SessionId==session&&process.StartTime.ToUniversalTime().Ticks==started&&
            String.Equals(Path.GetFullPath(process.MainModule.FileName),executable,StringComparison.OrdinalIgnoreCase);}
        catch {return false;}
    }
    static string ApplicationId(int pid) {
        var handle=OpenProcess(0x1000,false,pid);
        if(handle==IntPtr.Zero)throw new Exception("无法核对 Codex 的原生启动入口");
        try {
            uint length=0;int status=GetApplicationUserModelId(handle,ref length,null);
            if(status==15700||status==15703)return null;
            if(status!=122||length==0||length>512)throw new Exception("无法核对 Codex 的原生启动入口");
            var value=new StringBuilder((int)length);status=GetApplicationUserModelId(handle,ref length,value);
            if(status!=0)throw new Exception("无法核对 Codex 的原生启动入口");return value.ToString();
        } finally {CloseHandle(handle);}
    }
    static ProcessStartInfo NativeLaunch(string path,string applicationId) {
        ProcessStartInfo launch;
        if(applicationId==null)launch=new ProcessStartInfo(path) {WorkingDirectory=Path.GetDirectoryName(path)};
        else {
            if(!System.Text.RegularExpressions.Regex.IsMatch(applicationId,@"^[A-Za-z0-9_.-]+_[A-Za-z0-9]+![A-Za-z0-9_.-]+$"))
                throw new Exception("Codex 原生启动入口格式无效");
            launch=new ProcessStartInfo(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows),"explorer.exe"),"shell:AppsFolder\\"+applicationId);
        }
        launch.UseShellExecute=false;launch.CreateNoWindow=true;launch.WindowStyle=ProcessWindowStyle.Hidden;
        launch.EnvironmentVariables.Remove("ELECTRON_RUN_AS_NODE");return launch;
    }
    sealed class Operation {
        readonly object gate=new object();bool cancelled;
        public volatile bool Submitted,Finished;public Exception Error;public string State,Reason;
        public void Cancel() {lock(gate){cancelled=true;}}
        public void Check() {lock(gate){if(cancelled||cancelFile!=null&&File.Exists(cancelFile))throw new OperationCanceledException("已取消 Codex 退出");}}
        public void Dispatch(int pid,Action invoke) {
            lock(gate) {
                if(cancelled||Submitted||cancelFile!=null&&File.Exists(cancelFile))throw new OperationCanceledException("已取消 Codex 退出");
                Submitted=true;Stage("{\"quitPhase\":\"dispatching\",\"pid\":"+pid+"}");
            }
            // UIA may block or throw after delivery. It must not hold the gate,
            // and a lost return value must never trigger a second invocation.
            invoke();
        }
        public void Start(Process process,long started) {
            var thread=new Thread(delegate {try {Quit(process,started,this);}catch(Exception error){Error=error;}finally{Finished=true;}});
            thread.IsBackground=true;thread.SetApartmentState(ApartmentState.MTA);thread.Start();
        }
    }
    static void Check(Process process,long started,Operation operation,IntPtr window) {
        operation.Check();
        if(!Owned(process,started))throw new Exception("Codex 主进程已变化，退出请求已停止");
        if(window!=IntPtr.Zero) {
            uint pid;GetWindowThreadProcessId(window,out pid);
            if(!IsWindow(window)||pid!=process.Id)throw new Exception("Codex 主窗口已变化，退出请求已停止");
        }
    }
    static bool Available(AutomationElement item,int pid) {return item!=null&&item.Current.ProcessId==pid&&item.Current.IsEnabled&&!item.Current.IsOffscreen;}
    static AutomationElement Unique(AutomationElement root,Condition condition,int pid) {
        var items=root.FindAll(TreeScope.Descendants,condition);AutomationElement selected=null;
        foreach(AutomationElement item in items)if(Available(item,pid)) {
            if(selected!=null)throw new Exception("Codex 退出菜单不唯一，尚未提交退出");selected=item;
        }
        return selected;
    }
    static AutomationElement Wait(Func<AutomationElement> find,Action check,int milliseconds) {
        var timer=Stopwatch.StartNew();
        do {
            check();AutomationElement found=null;
            try {found=find();}catch(ElementNotAvailableException) {}
            check();if(found!=null)return found;Thread.Sleep(100);
        } while(timer.ElapsedMilliseconds<milliseconds);
        throw new Exception("Codex 应用程序退出菜单尚未就绪，请在电脑端检查后重试");
    }
    static string Compact(string text) {
        var result=new StringBuilder();foreach(char value in text??"")if(!Char.IsWhiteSpace(value))result.Append(value);return result.ToString();
    }
    static readonly string[] QuitLabels={"退出 Codex","Quit Codex","退出 ChatGPT","Quit ChatGPT"};
    static bool QuitLabel(string text) {
        foreach(string label in QuitLabels)if(text==label)return true;return false;
    }
    static bool QuitName(string name,string accelerator) {
        foreach(string label in QuitLabels) {
            if(Compact(name)==Compact(label+"Ctrl+Q"))return true;
            if(Compact(name)==Compact(label)&&Compact(accelerator)=="Ctrl+Q")return true;
        }
        return false;
    }
    static bool QuitItem(AutomationElement item,int pid) {
        if(!Available(item,pid)||item.Current.ControlType!=ControlType.MenuItem)return false;
        if(QuitName(item.Current.Name,item.Current.AcceleratorKey))return true;
        if(!QuitLabel(item.Current.Name))return false;
        var texts=item.FindAll(TreeScope.Descendants,new PropertyCondition(AutomationElement.ControlTypeProperty,ControlType.Text));
        int accelerators=0;
        foreach(AutomationElement text in texts)if(text.Current.ProcessId==pid&&Compact(text.Current.Name)=="Ctrl+Q")accelerators++;
        return accelerators==1;
    }
    static AutomationElement FindQuit(AutomationElement content,int pid) {
        var items=content.FindAll(TreeScope.Descendants,new PropertyCondition(AutomationElement.ControlTypeProperty,ControlType.MenuItem));
        AutomationElement result=null;
        foreach(AutomationElement item in items)if(QuitItem(item,pid)) {
            if(result!=null)throw new Exception("Codex 退出命令不唯一，尚未提交退出");result=item;
        }
        return result;
    }
    static void Quit(Process process,long started,Operation operation) {
        Action check=()=>Check(process,started,operation,IntPtr.Zero);
        var timer=Stopwatch.StartNew();bool reopened=false;IntPtr main=IntPtr.Zero;
        while(main==IntPtr.Zero) {
            check();var windows=Windows(process.Id);
            if(PendingDialog(windows)){operation.State="pending";operation.Reason="Codex 有待处理的原生对话框，请在电脑端确认或取消";return;}
            foreach(var window in windows)if(MainWindow(window)) {
                if(main!=IntPtr.Zero)throw new Exception("Codex 主窗口不唯一，请在电脑端检查后重试");main=window.Handle;
            }
            if(main!=IntPtr.Zero)break;
            if(!reopened) {
                string id=ApplicationId(process.Id);check();reopened=true;
                using(var launched=Process.Start(NativeLaunch(executable,id))) {}
                check();
            }
            if(timer.Elapsed.TotalSeconds>=8)throw new Exception("Codex 隐藏主窗口尚未就绪，请在电脑端打开后重试");Thread.Sleep(100);
        }
        check=()=>Check(process,started,operation,main);
        check();var root=AutomationElement.FromHandle(main);
        var file=Wait(()=>Unique(root,new AndCondition(new PropertyCondition(AutomationElement.ControlTypeProperty,ControlType.MenuItem),
            new PropertyCondition(AutomationElement.AutomationIdProperty,"application-menu-trigger-file-menu")),process.Id),check,3000);
        object pattern;
        if(!file.TryGetCurrentPattern(ExpandCollapsePattern.Pattern,out pattern))throw new Exception("Codex 文件菜单不支持正常展开，尚未提交退出");
        check();if(PendingDialog(Windows(process.Id))){operation.State="pending";operation.Reason="Codex 有待处理的原生对话框";return;}
        ((ExpandCollapsePattern)pattern).Expand();
        var content=Wait(()=>Unique(root,new PropertyCondition(AutomationElement.AutomationIdProperty,"application-menu-content"),process.Id),check,3000);
        var exit=Wait(()=>FindQuit(content,process.Id),check,3000);
        if(!exit.TryGetCurrentPattern(InvokePattern.Pattern,out pattern))throw new Exception("Codex 退出菜单不支持正常调用，尚未提交退出");
        check();
        if(PendingDialog(Windows(process.Id))){operation.State="pending";operation.Reason="Codex 有待处理的原生对话框";return;}
        var current=FindQuit(content,process.Id);
        if(current==null||!Automation.Compare(current,exit)||!Available(file,process.Id)||
            ((ExpandCollapsePattern)file.GetCurrentPattern(ExpandCollapsePattern.Pattern)).Current.ExpandCollapseState!=ExpandCollapseState.Expanded)
            throw new Exception("Codex 退出菜单已变化，尚未提交退出");
        check();operation.Dispatch(process.Id,()=>((InvokePattern)pattern).Invoke());
        timer.Restart();
        while(timer.Elapsed.TotalSeconds<3) {
            operation.Check();
            if(process.HasExited){operation.State="exited";operation.Reason="Codex 已正常退出";return;}
            if(!Owned(process,started))throw new Exception("Codex 主进程已变化，正在等待退出结果");
            if(PendingDialog(Windows(process.Id))){operation.State="pending";operation.Reason="Codex 正在等待原生退出确认，请在电脑端确认或取消";return;}
            Thread.Sleep(100);
        }
        operation.State="submitted";operation.Reason="已请求 Codex 正常退出，正在等待保存和退出完成";
    }
    static void SelfCheck() {
        if(!QuitName("退出 Codex Ctrl+Q","")||!QuitName("退出 ChatGPT Ctrl+Q","")||!QuitName("Quit Codex","Ctrl+Q")||QuitName("Quit Codex","Alt+F4")||
            QuitName("Close Codex Ctrl+Q","")||QuitName("Quit Codex Ctrl+Q extra","")||QuitName("Quit Claude Ctrl+Q","")||QuitName("退出登录 Ctrl+Q",""))throw new Exception("quit selector failed");
        var standalone=NativeLaunch(@"C:\Fixture\Codex.exe",null);
        if(standalone.UseShellExecute||standalone.Arguments!=""||!standalone.CreateNoWindow)throw new Exception("native launch failed");
        bool rejected=false;try{NativeLaunch(@"C:\Fixture\Codex.exe","bad id --quit");}catch{rejected=true;}
        if(!rejected)throw new Exception("launch identity failed");
        var cancelled=new Operation();cancelled.Cancel();int invoked=0;
        try{cancelled.Dispatch(0,()=>invoked++);}catch(OperationCanceledException){}
        if(invoked!=0||cancelled.Submitted)throw new Exception("cancel guard failed");
        Stage("Codex menu selectors, native launch and cancellation guards OK");
    }
    static int Main(string[] args) {
        Console.OutputEncoding=new UTF8Encoding(false);int pid=0;Operation operation=null;
        try {
            if(args.Length==1&&args[0]=="--self-check"){SelfCheck();return 0;}
            if((args.Length!=3&&args.Length!=4)||args[1]!="--quit")throw new Exception("Codex 退出参数无效");
            pid=Int32.Parse(args[0]);executable=Path.GetFullPath(args[2]);cancelFile=args.Length==4?args[3]:null;
            using(var process=Process.GetProcessById(pid)) {
                long started=process.StartTime.ToUniversalTime().Ticks;operation=new Operation();
                Check(process,started,operation,IntPtr.Zero);operation.Start(process,started);var timer=Stopwatch.StartNew();
                while(!operation.Finished&&timer.Elapsed.TotalSeconds<20) {
                    if(cancelFile!=null&&File.Exists(cancelFile)){operation.Cancel();break;}Thread.Sleep(50);
                }
                if(!operation.Finished) {operation.Cancel();throw new Exception("Codex 原生退出菜单操作已取消或超时");}
                if(operation.Error!=null)throw operation.Error;
                Reply(operation.State,pid,operation.Reason);return 0;
            }
        } catch(Exception error) {
            bool submitted=operation!=null&&operation.Submitted;
            string reason=submitted?"Codex 退出命令回执中断，正在等待保存和退出完成":
                error is OperationCanceledException?"已取消 Codex 退出":String.IsNullOrWhiteSpace(error.Message)?"Codex 原生退出菜单不可用，请在电脑端检查后重试":error.Message;
            Reply(submitted?"submitted":"failed",pid,reason);return submitted?0:1;
        }
    }
}
