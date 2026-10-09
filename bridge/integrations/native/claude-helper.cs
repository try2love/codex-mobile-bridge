// Adapted from 2389859005/coding-mobile, MIT, commit 7a8f003. See ../LICENSE.coding-mobile.
// Product component. Only a verified Claude DevTools Console may receive input.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Windows.Automation;

class ClaudeKeyboard {
    [StructLayout(LayoutKind.Sequential)] struct INPUT { public uint type; public UNION data; }
    [StructLayout(LayoutKind.Explicit)] struct UNION {
        [FieldOffset(0)] public KEYBDINPUT key;
        [FieldOffset(0)] public MOUSEINPUT mouse;
    }
    [StructLayout(LayoutKind.Sequential)] struct KEYBDINPUT { public ushort vk,scan; public uint flags,time; public UIntPtr extra; }
    [StructLayout(LayoutKind.Sequential)] struct MOUSEINPUT { public int x,y; public uint data,flags,time; public UIntPtr extra; }
    [DllImport("user32.dll")] static extern uint SendInput(uint n, INPUT[] input, int size);
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr window, out uint pid);
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr window);
    [DllImport("user32.dll")] static extern bool BringWindowToTop(IntPtr window);
    [DllImport("user32.dll")] static extern bool AttachThreadInput(uint first,uint second,bool attach);
    [DllImport("kernel32.dll")] static extern uint GetCurrentThreadId();
    [DllImport("user32.dll")] static extern IntPtr OpenInputDesktop(uint flags,bool inherit,uint access);
    [DllImport("user32.dll")] static extern bool CloseDesktop(IntPtr desktop);
    [DllImport("user32.dll")] static extern IntPtr GetThreadDesktop(uint thread);
    [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern bool GetUserObjectInformation(IntPtr handle,int index,StringBuilder value,uint length,out uint needed);
    [DllImport("wtsapi32.dll",CharSet=CharSet.Unicode)] static extern bool WTSQuerySessionInformation(IntPtr server,int id,int info,out IntPtr buffer,out uint bytes);
    [DllImport("wtsapi32.dll")] static extern void WTSFreeMemory(IntPtr buffer);
    [DllImport("kernel32.dll",SetLastError=true)] static extern IntPtr OpenProcess(uint access,bool inherit,int pid);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode)] static extern int GetApplicationUserModelId(IntPtr process,ref uint length,StringBuilder value);
    [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr window,int mode);
    [DllImport("user32.dll")] static extern short GetAsyncKeyState(int key);
    delegate bool EnumWindow(IntPtr window,IntPtr data);
    [DllImport("user32.dll")] static extern bool EnumWindows(EnumWindow callback,IntPtr data);
    [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr window,StringBuilder text,int count);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr window);
    [DllImport("user32.dll")] static extern bool IsWindow(IntPtr window);
    [DllImport("user32.dll")] static extern IntPtr GetWindow(IntPtr window,uint command);
    [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr window,StringBuilder text,int count);
    [DllImport("user32.dll",EntryPoint="GetWindowLongPtrW")] static extern IntPtr GetWindowLongPtr(IntPtr window,int index);
    [DllImport("user32.dll",EntryPoint="GetWindowLongW")] static extern int GetWindowLong(IntPtr window,int index);
    [DllImport("dwmapi.dll")] static extern int DwmGetWindowAttribute(IntPtr window,uint attribute,out uint value,uint size);
    [DllImport("user32.dll")] static extern bool PostMessage(IntPtr window,uint message,IntPtr wparam,IntPtr lparam);
    static string executable;
    static string cancelFile;
    static IntPtr inputWindow;
    static bool quitAction;
    static int quitPid;
    static int session=Process.GetCurrentProcess().SessionId;
    static void Stage(string text) {Console.WriteLine(text);Console.Out.Flush();}
    static string DesktopName(IntPtr desktop) {
        var value=new StringBuilder(256);uint needed;
        return desktop!=IntPtr.Zero&&GetUserObjectInformation(desktop,2,value,512,out needed)?value.ToString():null;
    }
    static string DesktopUnavailable() {
        IntPtr buffer=IntPtr.Zero;
        try {
            uint bytes;
            // WTSSessionInfoEx's level-one union starts at byte eight. Query
            // this process's session, never another user's active console.
            if(!WTSQuerySessionInformation(IntPtr.Zero,session,25,out buffer,out bytes)||buffer==IntPtr.Zero||bytes<20||
               Marshal.ReadInt32(buffer,0)!=1||Marshal.ReadInt32(buffer,8)!=session)
                return "Windows 桌面不可用；无法确认当前会话，请恢复电脑桌面后重试";
            int connection=Marshal.ReadInt32(buffer,12),flags=Marshal.ReadInt32(buffer,16);
            var version=Environment.OSVersion.Version;
            if(version.Major==6&&version.Minor==1&&(flags==0||flags==1))flags=1-flags;
            if(flags==0)return "Windows 已锁定；请解锁电脑后重试";
            if(flags!=1||connection!=0||session==0)return "Windows 桌面不可用；请恢复电脑桌面会话后重试";
        } finally {if(buffer!=IntPtr.Zero)WTSFreeMemory(buffer);}
        var input=OpenInputDesktop(0,false,1); // Read only; never switch desktops.
        if(input==IntPtr.Zero)return "Windows 输入桌面不可用；请关闭系统安全提示或恢复桌面后重试";
        try {
            string current=DesktopName(GetThreadDesktop(GetCurrentThreadId())),active=DesktopName(input);
            if(current==null||active==null||!current.Equals(active,StringComparison.Ordinal))
                return "Windows 输入桌面不可用；请恢复电脑桌面后重试";
        } finally {CloseDesktop(input);}
        return null;
    }
    static void CheckInteractiveDesktop() {
        var reason=DesktopUnavailable();if(reason!=null)throw new Exception(reason);
    }
    static void Activate(IntPtr window) {
        CheckCancelled();
        CheckInteractiveDesktop();
        uint ignored,current=GetCurrentThreadId(),foreground=GetWindowThreadProcessId(GetForegroundWindow(),out ignored);
        bool attached=foreground!=0&&foreground!=current&&AttachThreadInput(current,foreground,true);
        try {ShowWindow(window,9);BringWindowToTop(window);SetForegroundWindow(window);}
        finally {if(attached)AttachThreadInput(current,foreground,false);}
        Thread.Sleep(300);
        CheckForeground();
    }
    static bool Owned(int pid) {
        try { using(var p=Process.GetProcessById(pid)) return p.SessionId==session && p.ProcessName.Equals("Claude",StringComparison.OrdinalIgnoreCase) && p.MainModule.FileName.Equals(executable,StringComparison.OrdinalIgnoreCase); }
        catch { return false; }
    }
    sealed class AppWindow {
        public IntPtr Handle,Owner;
        public int Pid;
        public bool Visible,ToolWindow,Cloaked;
        public string Title="",ClassName="";
    }
    sealed class WindowSelection {
        public IntPtr Handle;
        public int Count;
    }
    static bool Eligible(AppWindow window,int pid) {
        return window.Pid==pid&&window.Handle!=IntPtr.Zero&&window.Visible&&!window.ToolWindow&&!window.Cloaked
            &&!String.IsNullOrEmpty(window.Title)&&!DevToolsTitle(window.Title);
    }
    static WindowSelection SelectWindow(IEnumerable<AppWindow> windows,int pid,IntPtr foreground) {
        var selected=new WindowSelection();IntPtr only=IntPtr.Zero;
        foreach(var window in windows) {
            if(!Eligible(window,pid))continue;
            selected.Count++;only=window.Handle;
            if(window.Handle==foreground)selected.Handle=foreground;
        }
        if(selected.Count==1)selected.Handle=only;
        return selected;
    }
    static WindowSelection SelectDevTools(IEnumerable<AppWindow> windows,int pid) {
        var selected=new WindowSelection();
        foreach(var window in windows) {
            if(window.Pid!=pid||window.Handle==IntPtr.Zero||!window.Visible||window.Cloaked||!DevToolsTitle(window.Title))continue;
            selected.Count++;selected.Handle=window.Handle;
        }
        if(selected.Count!=1)selected.Handle=IntPtr.Zero;
        return selected;
    }
    static List<AppWindow> ReadWindows(int selectedPid) {
        var windows=new List<AppWindow>();
        EnumWindows(delegate(IntPtr handle,IntPtr data) {
            uint pid;GetWindowThreadProcessId(handle,out pid);
            if(pid!=selectedPid)return true;
            var title=new StringBuilder(1024);var name=new StringBuilder(256);
            GetWindowText(handle,title,title.Capacity);GetClassName(handle,name,name.Capacity);
            long style=IntPtr.Size==8?GetWindowLongPtr(handle,-20).ToInt64():GetWindowLong(handle,-20);
            uint cloaked;bool hidden=DwmGetWindowAttribute(handle,14,out cloaked,4)==0&&cloaked!=0;
            windows.Add(new AppWindow {Handle=handle,Owner=GetWindow(handle,4),Pid=(int)pid,
                Visible=IsWindowVisible(handle),ToolWindow=(style&0x80)!=0,Cloaked=hidden,
                Title=title.ToString(),ClassName=name.ToString()});
            return true;
        },IntPtr.Zero);
        return windows;
    }
    static IntPtr WaitWindow(Func<WindowSelection> find,Action restore,Action check,Action pause,int attempts) {
        bool restored=false;
        for(int attempt=0;attempt<attempts;attempt++) {
            check();var selected=find();
            if(selected.Handle!=IntPtr.Zero)return selected.Handle;
            if(selected.Count>1)throw new Exception("检测到多个 Claude 窗口，请选中要连接的窗口后重试");
            if(!restored&&restore!=null) {check();restore();restored=true;}
            if(attempt+1<attempts)pause();
        }
        throw new Exception("Claude 窗口尚未准备好，请从系统托盘打开 Claude 后重试");
    }
    static IntPtr WaitAppWindow(Process process,bool restore) {
        Action check=delegate {
            CheckCancelled();
            if(restore&&!quitAction)CheckInteractiveDesktop();
            if(process.HasExited||!Owned(process.Id))throw new Exception("Claude 主进程已改变，请重试连接");
        };
        Action reopen=restore?(Action)delegate {
            // Claude's native single-instance launch restores its tray window.
            // Never activate one of its many hidden utility windows directly.
            check();
            using(var launched=Process.Start(NativeLaunch(executable,ApplicationId(process.Id),
                Environment.GetFolderPath(Environment.SpecialFolder.Windows)))) {}
        }:null;
        return WaitWindow(()=>SelectWindow(ReadWindows(process.Id),process.Id,GetForegroundWindow()),
                          reopen,check,()=>Thread.Sleep(250),32);
    }
    static string ApplicationId(int pid) {
        var handle=OpenProcess(0x1000,false,pid); // Query limited process metadata only.
        if(handle==IntPtr.Zero)throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
        try {
            uint length=0;int status=GetApplicationUserModelId(handle,ref length,null);
            if(status==15700||status==15703)return null; // Unpackaged / no application identity.
            if(status!=122||length==0||length>512)throw new Exception("无法确认 Claude 的原生应用启动入口");
            var value=new StringBuilder((int)length);
            status=GetApplicationUserModelId(handle,ref length,value);
            if(status!=0)throw new System.ComponentModel.Win32Exception(status);
            return value.ToString();
        } finally {CloseHandle(handle);}
    }
    static ProcessStartInfo NativeLaunch(string path,string applicationId,string windowsDirectory) {
        if(applicationId==null)return new ProcessStartInfo(path) {UseShellExecute=false,WorkingDirectory=Path.GetDirectoryName(path)};
        // Registered Store binaries can reject both CreateProcess and direct
        // ShellExecute. Use the selected process's OS-reported app identity.
        if(!System.Text.RegularExpressions.Regex.IsMatch(applicationId,@"^[A-Za-z0-9_.-]+_[A-Za-z0-9]+![A-Za-z0-9_.-]+$"))
            throw new Exception("Claude 的原生应用启动入口格式无效");
        return new ProcessStartInfo(Path.Combine(windowsDirectory,"explorer.exe"),"shell:AppsFolder\\"+applicationId) {
            UseShellExecute=false,CreateNoWindow=true,WindowStyle=ProcessWindowStyle.Hidden};
    }
    static string JsonString(string value) {
        return "\""+value.Replace("\\","\\\\").Replace("\"","\\\"").Replace("\r","\\r").Replace("\n","\\n").Replace("\t","\\t")+"\"";
    }
    static void InspectWindow(Process process) {
        // Diagnostic only: no native launch, activation, key state or input.
        var windows=ReadWindows(process.Id);var selection=SelectWindow(windows,process.Id,GetForegroundWindow());
        var json=new StringBuilder();
        json.Append("{\"processId\":").Append(process.Id).Append(",\"sessionId\":").Append(process.SessionId)
            .Append(",\"helperSessionId\":").Append(session).Append(",\"mainWindowHandle\":").Append(process.MainWindowHandle.ToInt64())
            .Append(",\"selectedWindowHandle\":").Append(selection.Handle.ToInt64()).Append(",\"eligibleCount\":").Append(selection.Count)
            .Append(",\"appUserModelId\":");
        var applicationId=ApplicationId(process.Id);
        json.Append(applicationId==null?"null":JsonString(applicationId)).Append(",\"windows\":[");
        bool first=true;
        foreach(var window in windows) {
            if(!first)json.Append(',');first=false;
            json.Append("{\"handle\":").Append(window.Handle.ToInt64()).Append(",\"owner\":").Append(window.Owner.ToInt64())
                .Append(",\"visible\":").Append(window.Visible?"true":"false").Append(",\"toolWindow\":").Append(window.ToolWindow?"true":"false")
                .Append(",\"cloaked\":").Append(window.Cloaked?"true":"false").Append(",\"eligible\":").Append(Eligible(window,process.Id)?"true":"false")
                .Append(",\"devTools\":").Append(DevToolsTitle(window.Title)?"true":"false").Append(",\"className\":").Append(JsonString(window.ClassName)).Append('}');
        }
        Console.WriteLine(json.Append("]}").ToString());
    }
    static void CheckCancelled() {
        if ((cancelFile!=null && File.Exists(cancelFile)) || (!quitAction&&!backgroundAction&&(GetAsyncKeyState(27)&0x8001)!=0))
            throw new Exception(quitAction?"已取消 Claude 退出":"已取消键盘连接，点击 Claude 标题选择启动可重试");
    }
    static void CheckForeground() {
        CheckCancelled();
        CheckInteractiveDesktop();
        var window=GetForegroundWindow();uint pid; GetWindowThreadProcessId(window,out pid);
        if (!Owned((int)pid)) throw new Exception(quitAction?"Claude 退出窗口未保持在前台，退出请求尚未提交，请重试退出":"焦点已离开 Claude，自动连接已停止，请重试");
        if(inputWindow!=IntPtr.Zero&&window!=inputWindow)throw new Exception(quitAction?"Claude 退出窗口已改变，退出请求尚未提交，请重试退出":"Claude 窗口已改变，自动连接已停止，请重试");
    }
    static bool PromptName(string name,string id) {
        return id=="console-prompt" || name=="Console prompt" || name=="控制台提示" || name=="控制台提示符";
    }
    static bool IsPrompt(AutomationElement e) {
        return e!=null && Owned(e.Current.ProcessId) && PromptName(e.Current.Name,e.Current.AutomationId);
    }
    static void CheckPrompt(AutomationElement prompt) {
        CheckForeground();
        if(!FocusedWithin(prompt,AutomationElement.FocusedElement,TreeWalker.ControlViewWalker.GetParent,
                          (first,second)=>Automation.Compare(first,second)))
            throw new Exception("Console 输入焦点已改变，自动输入已取消");
    }
    static bool FocusedWithin<T>(T expected,T focused,Func<T,T> parent,Func<T,T,bool> equal) where T:class {
        for(int i=0;i<5&&focused!=null;i++,focused=parent(focused))if(equal(expected,focused))return true;
        return false;
    }
    static void FocusPrompt(AutomationElement prompt) {
        CheckForeground();
        prompt.SetFocus();
        for(int i=0;i<10;i++){Thread.Sleep(100);CheckForeground();try{CheckPrompt(prompt);return;}catch{}}
        CheckPrompt(prompt);
    }
    static INPUT Key(ushort vk,bool up) { var i=new INPUT(); i.type=1;i.data.key.vk=vk;i.data.key.flags=up?2u:0u;return i; }
    static void Send(INPUT[] keys) {
        CheckForeground();
        if(SendInput((uint)keys.Length,keys,Marshal.SizeOf(typeof(INPUT)))!=keys.Length) throw new Exception("Windows 拒绝键盘输入，请检查 Claude 与助手的权限是否一致");
    }
    static void Shortcut() { CheckForeground();Send(new[]{Key(17,false),Key(18,false),Key(73,false),Key(73,true),Key(18,true),Key(17,true)}); }
    static AutomationElement ConsolePrompt() {
        return ConsolePrompt(AutomationElement.FromHandle(GetForegroundWindow()));
    }
    static AutomationElement ConsolePrompt(AutomationElement root) {
        var elements=root.FindAll(TreeScope.Descendants,new OrCondition(
            new PropertyCondition(AutomationElement.AutomationIdProperty,"console-prompt"),
            new PropertyCondition(AutomationElement.NameProperty,"Console prompt"),
            new PropertyCondition(AutomationElement.NameProperty,"控制台提示"),
            new PropertyCondition(AutomationElement.NameProperty,"控制台提示符")));
        foreach(AutomationElement e in elements) if(IsPrompt(e)&&!e.Current.IsOffscreen) return e;
        return null;
    }
    static bool DevToolsTitle(string title) {
        return title=="Developer Tools"||title=="DevTools"||title=="开发者工具"||title.StartsWith("Developer Tools - ",StringComparison.Ordinal)||title.StartsWith("DevTools - ",StringComparison.Ordinal)||title.StartsWith("开发者工具 - ",StringComparison.Ordinal);
    }
    static void CloseDevTools() {
        // Called only after the parent verifies the signed bridge heartbeat.
        // Detached tools close by their exact Claude-owned window, without
        // activating another app or ever sending a close to Claude's main UI.
        var windows=new System.Collections.Generic.List<IntPtr>();
        EnumWindows(delegate(IntPtr window,IntPtr data){uint pid;GetWindowThreadProcessId(window,out pid);if(IsWindowVisible(window)&&Owned((int)pid))windows.Add(window);return true;},IntPtr.Zero);
        int closed=0;
        foreach(var window in windows) {
            CheckCancelled();
            if(!IsWindow(window))continue;
            var root=AutomationElement.FromHandle(window);
            if(ConsolePrompt(root)==null)continue;
            var title=new StringBuilder(512);GetWindowText(window,title,title.Capacity);
            if(DevToolsTitle(title.ToString())) {
                if(!PostMessage(window,0x0010,IntPtr.Zero,IntPtr.Zero))throw new Exception("Claude 已连接，但关闭开发者工具失败");
                for(int i=0;i<30&&IsWindow(window);i++)Thread.Sleep(100);
                if(IsWindow(window))throw new Exception("Claude 已连接，开发者工具尚未关闭");
                closed++;continue;
            }
            var close=root.FindFirst(TreeScope.Descendants,new AndCondition(new PropertyCondition(AutomationElement.ControlTypeProperty,ControlType.Button),new OrCondition(new PropertyCondition(AutomationElement.NameProperty,"Close DevTools"),new PropertyCondition(AutomationElement.NameProperty,"关闭开发者工具"))));
            object pattern;
            if(close!=null&&Owned(close.Current.ProcessId)&&!close.Current.IsOffscreen&&close.TryGetCurrentPattern(InvokePattern.Pattern,out pattern)) {((InvokePattern)pattern).Invoke();closed++;continue;}
            // A docked Console without its own accessible close button remains
            // open. Cleanup must never activate a window or send a shortcut.
            throw new Exception("Claude 已连接；开发者工具未自动关闭，请手动关闭");
        }
        Stage("DevTools closed: "+closed);
    }
    static void SelectConsoleTab() {
        var root=AutomationElement.FromHandle(GetForegroundWindow());
        var tabs=root.FindAll(TreeScope.Descendants,new PropertyCondition(AutomationElement.ControlTypeProperty,ControlType.TabItem));
        foreach(AutomationElement tab in tabs) {
            string name=tab.Current.Name;
            if((name=="Console"||name=="控制台")&&!tab.Current.IsOffscreen&&Owned(tab.Current.ProcessId)) {
                object pattern;
                CheckForeground();
                if(tab.TryGetCurrentPattern(SelectionItemPattern.Pattern,out pattern)) ((SelectionItemPattern)pattern).Select();
                return;
            }
        }
    }
    const string DeveloperMenuUnavailable="请在 Claude 的帮助菜单中启用开发者模式，然后重试连接";
    static T WaitMenuEntry<T>(Func<T> find,Action check,Action pause) where T:class {
        for(int attempt=0;attempt<20;attempt++) {
            check();var item=find();if(item!=null)return item;
            if(attempt<19)pause();
        }
        return null;
    }
    static void EnableDeveloperMenu<T>(Func<string,T> find,Action<T> invoke,Action<T> expand,Action check,Action pause) where T:class {
        check();var help=find("help");
        if(help==null) {
            var menu=find("menu");if(menu==null)throw new Exception(DeveloperMenuUnavailable);
            check();invoke(menu);
            help=WaitMenuEntry(()=>find("help"),check,pause);
        }
        if(help==null)throw new Exception(DeveloperMenuUnavailable);
        check();expand(help);
        T enable=null,troubleshooting=null;
        WaitMenuEntry(delegate {
            enable=find("enable");troubleshooting=find("troubleshooting");
            return enable??troubleshooting;
        },check,pause);
        if(enable==null&&troubleshooting!=null) {
            check();expand(troubleshooting);
            enable=WaitMenuEntry(()=>find("enable"),check,pause);
        }
        if(enable==null)throw new Exception(DeveloperMenuUnavailable);
        check();invoke(enable);
    }
    static bool AvailableMenuControl(AutomationElement item) {
        return item!=null&&Owned(item.Current.ProcessId)&&!item.Current.IsOffscreen&&item.Current.IsEnabled;
    }
    static AutomationElement FindMenuControl(AutomationElement root,ControlType type,params string[] names) {
        var conditions=new Condition[names.Length];
        for(int i=0;i<names.Length;i++)conditions[i]=new PropertyCondition(AutomationElement.NameProperty,names[i]);
        var elements=root.FindAll(TreeScope.Descendants,new AndCondition(
            new PropertyCondition(AutomationElement.ControlTypeProperty,type),new OrCondition(conditions)));
        foreach(AutomationElement item in elements)if(AvailableMenuControl(item))return item;
        return null;
    }
    static void InvokeMenuControl(AutomationElement item) {
        CheckForeground();object pattern;
        if(!AvailableMenuControl(item)||!item.TryGetCurrentPattern(InvokePattern.Pattern,out pattern))throw new Exception(DeveloperMenuUnavailable);
        ((InvokePattern)pattern).Invoke();
    }
    static void ExpandMenuControl(AutomationElement item) {
        CheckForeground();object pattern;
        if(!AvailableMenuControl(item)||!item.TryGetCurrentPattern(ExpandCollapsePattern.Pattern,out pattern))throw new Exception(DeveloperMenuUnavailable);
        ((ExpandCollapsePattern)pattern).Expand();
    }
    static void EnableDeveloperMode(Process process) {
        var window=WaitAppWindow(process,true);
        inputWindow=window;
        Activate(window);
        var root=AutomationElement.FromHandle(window);
        EnableDeveloperMenu(delegate(string role) {
            if(role=="menu")return FindMenuControl(root,ControlType.Button,"Menu","菜单");
            if(role=="help")return FindMenuControl(root,ControlType.MenuItem,"Help","帮助");
            if(role=="troubleshooting")return FindMenuControl(root,ControlType.MenuItem,"Troubleshooting","故障排除");
            if(role=="enable")return FindMenuControl(root,ControlType.MenuItem,"Enable Developer Mode…","Enable Developer Mode...","启用开发者模式…","启用开发者模式...");
            return null;
        },InvokeMenuControl,ExpandMenuControl,CheckForeground,()=>Thread.Sleep(100));
        // Preserve Claude's native Enable/Don't Enable confirmation.
        Stage("请确认 Claude 的开发者模式提示，完成后会继续连接");
    }
    const string QuitMenuUnavailable="找不到 Claude 的原生退出菜单，请在电脑端检查菜单后重试";
    static bool QuitMenuName(string role,ControlType type,string name) {
        if(role=="menu")return type==ControlType.Button&&(name=="Menu"||name=="菜单"||name=="選單");
        if(type!=ControlType.MenuItem)return false;
        if(role=="file")return name=="File"||name=="文件"||name=="檔案";
        return role=="exit"&&(name=="Exit"||name=="退出"||name=="結束");
    }
    static T UniqueQuitControl<T>(IEnumerable<T> items,Func<T,bool> matches) where T:class {
        T found=null;
        foreach(var item in items)if(matches(item)) {
            if(found!=null)throw new Exception("Claude 退出菜单不唯一，请在电脑端检查后重试");
            found=item;
        }
        return found;
    }
    static void QuitMenu<T>(Func<string,T> find,Action<T> invoke,Action<T> expand,Action check,Action pause,Action<T> submit=null) where T:class {
        check();var file=find("file");
        if(file==null) {
            var menu=find("menu");if(menu==null)throw new Exception(QuitMenuUnavailable);
            check();invoke(menu);
            file=WaitMenuEntry(()=>find("file"),check,pause);
        }
        if(file==null)throw new Exception(QuitMenuUnavailable);
        check();expand(file);
        var exit=WaitMenuEntry(()=>find("exit"),check,pause);
        if(exit==null)throw new Exception(QuitMenuUnavailable);
        check();(submit??invoke)(exit);
    }
    static AutomationElement FindQuitControl(AutomationElement root,string role,int pid) {
        var elements=root.FindAll(TreeScope.Descendants,new PropertyCondition(AutomationElement.ControlTypeProperty,
            role=="menu"?ControlType.Button:ControlType.MenuItem));
        var candidates=new List<AutomationElement>();
        foreach(AutomationElement item in elements)candidates.Add(item);
        return UniqueQuitControl(candidates,item=>item.Current.ProcessId==pid&&AvailableMenuControl(item)
            &&QuitMenuName(role,item.Current.ControlType,item.Current.Name));
    }
    static bool SameQuitTarget(bool exited,bool owned,long actualStart,long expectedStart,bool exists,int windowPid,int expectedPid) {
        return !exited&&owned&&actualStart==expectedStart&&exists&&windowPid==expectedPid;
    }
    static void CheckQuitNavigation(Process process,long started,IntPtr window) {
        CheckCancelled();
        uint pid;GetWindowThreadProcessId(window,out pid);
        if(!SameQuitTarget(process.HasExited,Owned(process.Id),process.StartTime.ToUniversalTime().Ticks,
            started,IsWindow(window),(int)pid,process.Id))
            throw new Exception("Claude 主进程或窗口已改变，退出请求已停止，请重试");
    }
    static void InvokeQuitControl(AutomationElement item,Action check,bool expand,Action submitting=null) {
        check();
        if(item==null||item.Current.ProcessId!=quitPid||!AvailableMenuControl(item))throw new Exception(QuitMenuUnavailable);
        object pattern;
        if(expand&&item.TryGetCurrentPattern(ExpandCollapsePattern.Pattern,out pattern)) {
            ((ExpandCollapsePattern)pattern).Expand();return;
        }
        if(!item.TryGetCurrentPattern(InvokePattern.Pattern,out pattern))throw new Exception(QuitMenuUnavailable);
        check();if(submitting!=null)submitting();
        ((InvokePattern)pattern).Invoke();
    }
    sealed class QuitSubmission {
        public volatile bool Started,Finished;
        public Exception Error;
        readonly object gate=new object();
        bool cancelled;
        public bool CancelBeforeSubmit() {
            lock(gate) {if(Started)return false;cancelled=true;return true;}
        }
        public QuitSubmission(Process process,long started,IntPtr window) {
            Action check=delegate {
                lock(gate) {if(cancelled)throw new OperationCanceledException();}
                CheckQuitNavigation(process,started,window);
            };
            Action submitting=delegate {
                lock(gate) {
                    if(cancelled)throw new OperationCanceledException();Started=true;
                    // A timeout or lost final reply cannot prove that Exit was
                    // not delivered. Flush this evidence before invoking it.
                    Stage("{\"quitPhase\":\"dispatching\",\"pid\":"+process.Id+"}");
                }
            };
            // Any menu provider call can block. Keep the entire navigation on
            // an MTA worker; only this worker can submit Exit, exactly once.
            var thread=new Thread(delegate() {
                try {
                    check();var root=AutomationElement.FromHandle(window);
                    QuitMenu(role=>FindQuitControl(root,role,process.Id),item=>InvokeQuitControl(item,check,false),
                        item=>InvokeQuitControl(item,check,true),check,()=>Thread.Sleep(100),
                        item=>InvokeQuitControl(item,check,false,submitting));
                }
                catch(Exception error) {Error=error;}
                finally {Finished=true;}
            });
            thread.IsBackground=true;thread.SetApartmentState(ApartmentState.MTA);thread.Start();
        }
    }
    static bool HasNativeDialog(Process process) {
        foreach(var window in ReadWindows(process.Id)) {
            if(!window.Visible||window.Cloaked||DevToolsTitle(window.Title))continue;
            try {
                var root=AutomationElement.FromHandle(window.Handle);object pattern;
                if(root.Current.ProcessId!=process.Id||!Owned(process.Id))continue;
                bool modal=root.TryGetCurrentPattern(WindowPattern.Pattern,out pattern)&&((WindowPattern)pattern).Current.IsModal;
                if(modal)return true;
                if(window.ClassName!="#32770")continue;
                var buttons=root.FindAll(TreeScope.Descendants,new PropertyCondition(AutomationElement.ControlTypeProperty,ControlType.Button));
                foreach(AutomationElement button in buttons) {
                    string name=button.Current.Name;
                    if(button.Current.ProcessId==process.Id&&!button.Current.IsOffscreen&&
                       (name=="Quit anyway"||name=="仍要退出"||name=="仍要結束"||name=="Wait for Claude"||name=="等待 Claude"))return true;
                }
            }catch(ElementNotAvailableException) {}
            catch(ArgumentException) {if(IsWindow(window.Handle))throw;}
        }
        return false;
    }
    static string ObserveQuit(Func<bool> exited,Func<bool> dialog,Action pause,int attempts) {
        for(int i=0;i<attempts;i++) {
            if(exited())return "exited";
            if(dialog())return "pending";
            if(i+1<attempts)pause();
        }
        return exited()?"exited":"submitted";
    }
    static void QuitResult(string state,string reason,int pid) {
        Console.WriteLine("{\"quitState\":"+JsonString(state)+",\"reason\":"+JsonString(reason)+",\"pid\":"+pid+"}");
    }
    static void QuitNative(Process process) {
        // This explicit exit path invokes native controls only. It must never
        // enter the DevTools, SendInput or Console focus paths below.
        long started=process.StartTime.ToUniversalTime().Ticks;
        if(HasNativeDialog(process)) {
            QuitResult("pending","Claude 有待处理的原生对话框，请在电脑端确认或取消",process.Id);return;
        }
        var window=WaitAppWindow(process,true);
        CheckCancelled();
        if(process.HasExited||!Owned(process.Id)||process.StartTime.ToUniversalTime().Ticks!=started)
            throw new Exception("Claude 主进程已改变，退出请求已停止");
        QuitSubmission submission=null;string state=null;
        for(int attempt=0;attempt<2;attempt++) {
            submission=new QuitSubmission(process,started,window);
            var timer=Stopwatch.StartNew();
            while(!submission.Started&&!submission.Finished&&timer.Elapsed.TotalSeconds<8) {
                CheckCancelled();
                if(process.HasExited) {state="exited";break;}
                if(HasNativeDialog(process)) {state="pending";break;}
                Thread.Sleep(100);
            }
            if(state!=null) {submission.CancelBeforeSubmit();break;}
            if(submission.Started)break;
            bool finished=submission.Finished;
            if(!submission.CancelBeforeSubmit())break;
            // Only a completed failure before Exit can receive one foreground
            // fallback. A stalled provider must never race a second attempt.
            string unavailable=DesktopUnavailable();
            if(attempt==0&&finished&&unavailable==null) {
                CheckQuitNavigation(process,started,window);
                inputWindow=window;Activate(window);continue;
            }
            if(unavailable!=null)throw new Exception(unavailable+"；Claude 未接收退出请求");
            throw submission.Error??new Exception("Claude 退出菜单操作超时，请重试");
        }
        // Normal teardown changes focus and can take time to flush sessions.
        // Never repeat Exit or accept a native busy-work confirmation.
        if(state==null)state=ObserveQuit(()=>process.HasExited,()=>HasNativeDialog(process),()=>Thread.Sleep(200),16);
        if(state=="submitted"&&!submission.Started)
            throw submission.Error??new Exception("Claude 尚未接收退出请求，请重试");
        // An Invoke exception after dispatch may only mean its acknowledgement
        // was lost. The parent must keep observing the real process shutdown.
        bool uncertain=submission.Started&&submission.Finished&&submission.Error!=null;
        QuitResult(state,state=="exited"?"Claude 已正常退出":state=="pending"?
            "Claude 正在等待电脑端退出确认，请自行确认或取消":uncertain?
            "Claude 退出命令回执中断，正在等待保存和退出完成":"已请求 Claude 正常退出，正在等待保存和退出完成",process.Id);
    }
    [StructLayout(LayoutKind.Sequential)] struct BackgroundRect {public int left,top,right,bottom;}
    [StructLayout(LayoutKind.Sequential)] struct BackgroundGui {public int size,flags;public IntPtr active,focus,capture,menuOwner,moveSize,caret;public BackgroundRect caretRect;}
    [DllImport("user32.dll")] static extern bool GetGUIThreadInfo(uint thread,ref BackgroundGui value);
    [DllImport("user32.dll")] static extern bool EnumChildWindows(IntPtr window,EnumWindow callback,IntPtr data);
    static bool backgroundAction;
    static BackgroundOperation backgroundWork;
    sealed class BackgroundOperation {
        public volatile bool Started,Finished;public Exception Error;
        readonly object gate=new object();bool cancelled;
        public void Check(){lock(gate){if(cancelled)throw new OperationCanceledException("已取消 Claude 后台连接");}}
        public bool Cancel(){lock(gate){cancelled=true;return Started;}}
        public void ClaimDispatch(Action submitting){lock(gate){if(cancelled||Started||(cancelFile!=null&&File.Exists(cancelFile)))throw new OperationCanceledException("已取消 Claude 后台连接");Started=true;submitting();}}
        public void Dispatch(int pid){ClaimDispatch(delegate{backgroundSubmission="uncertain";Stage("{\"connectPhase\":\"dispatching\",\"pid\":"+pid+"}");});}
        public void Start(Process process,string text){var thread=new Thread(delegate(){try{ConnectBackground(process,text);}catch(Exception e){Error=e;}finally{Finished=true;}});thread.IsBackground=true;thread.SetApartmentState(ApartmentState.MTA);thread.Start();}
    }
    static void RunBackgroundConnection(Process process,string text) {
        var operation=new BackgroundOperation();backgroundWork=operation;operation.Start(process,text);var timer=Stopwatch.StartNew();
        while(!operation.Finished&&timer.Elapsed.TotalSeconds<25){if(cancelFile!=null&&File.Exists(cancelFile)){operation.Cancel();break;}Thread.Sleep(50);}
        if(!operation.Finished){bool dispatched=operation.Cancel();backgroundSubmission=dispatched?"uncertain":"none";throw new Exception(dispatched?"Claude 连接提交结果待核对，正在等待连接回执":"Claude 后台初始化已取消或超时，连接脚本未提交");}
        if(operation.Error!=null)throw operation.Error;
        ConnectResult("submitted",backgroundSubmission,process.Id,"已向 Claude Console 提交连接脚本，正在等待连接回执");
    }
    static string VerifiedDevToolsTitle(string native,string accessible) {
        var first=DevToolsSource(native);var second=DevToolsSource(accessible);
        if(first!=null&&second!=null&&(AppDevToolsTitle(native)!=AppDevToolsTitle(accessible)||ShellDevToolsTitle(native)!=ShellDevToolsTitle(accessible)))
            throw new Exception("Claude 开发者工具来源不一致，后台连接已停止");
        return first!=null?native:second!=null&&DevToolsTitle(native)?accessible:native;
    }
    static List<AppWindow> ReadBackgroundWindows(int pid) {
        var windows=ReadWindows(pid);
        foreach(var window in windows)if(window.Visible&&!window.Cloaked&&DevToolsTitle(window.Title)) {
            var root=AutomationElement.FromHandle(window.Handle);if(root.Current.ProcessId!=pid)throw new Exception("Claude 开发者工具进程已改变");
            window.Title=VerifiedDevToolsTitle(window.Title,root.Current.Name);
        }
        return windows;
    }
    static string backgroundSubmission="none";
    static Uri DevToolsSource(string title) {
        foreach(string prefix in new[]{"DevTools - ","Developer Tools - ","开发者工具 - "})if(title.StartsWith(prefix,StringComparison.Ordinal)) {
            Uri source;return Uri.TryCreate(title.Substring(prefix.Length),UriKind.Absolute,out source)?source:null;
        }
        return null;
    }
    static bool AppDevToolsTitle(string title) {
        var source=DevToolsSource(title);
        return source!=null&&source.Scheme=="app"&&source.Host.Equals("localhost",StringComparison.OrdinalIgnoreCase)&&source.UserInfo.Length==0&&source.Port==-1;
    }
    static bool ShellDevToolsTitle(string title) {
        var source=DevToolsSource(title);
        return source!=null&&source.IsFile&&source.AbsolutePath.EndsWith("/main_window/index.html",StringComparison.OrdinalIgnoreCase);
    }
    static WindowSelection SelectBackgroundDevTools(IEnumerable<AppWindow> windows,int pid) {
        var selected=new WindowSelection();
        foreach(var window in windows)if(window.Pid==pid&&window.Handle!=IntPtr.Zero&&window.Visible&&!window.Cloaked&&AppDevToolsTitle(window.Title)) {
            selected.Count++;selected.Handle=window.Handle;
        }
        if(selected.Count!=1)selected.Handle=IntPtr.Zero;return selected;
    }
    static void CheckBackgroundProcess(Process process,long started) {
        if(backgroundWork!=null)backgroundWork.Check();
        if(cancelFile!=null&&File.Exists(cancelFile))throw new OperationCanceledException("已取消 Claude 后台连接");
        if(process.HasExited||!Owned(process.Id)||process.StartTime.ToUniversalTime().Ticks!=started)
            throw new Exception("Claude 主进程已改变，后台连接已停止");
    }
    static void CheckBackgroundWindow(Process process,long started,IntPtr window) {
        CheckBackgroundProcess(process,started);uint pid;GetWindowThreadProcessId(window,out pid);
        if(!IsWindow(window)||pid!=process.Id)throw new Exception("Claude 目标窗口已改变，后台连接已停止");
    }
    static AutomationElement BackgroundMenuControl(AutomationElement root,ControlType type,int pid,params string[] names) {
        var items=root.FindAll(TreeScope.Descendants,new PropertyCondition(AutomationElement.ControlTypeProperty,type));
        AutomationElement match=null;
        foreach(AutomationElement item in items) {
            bool name=false;foreach(string expected in names)if(item.Current.Name==expected){name=true;break;}
            if(!name||item.Current.ProcessId!=pid||!AvailableMenuControl(item))continue;
            if(match!=null)throw new Exception("Claude 开发者菜单不唯一，后台连接已停止");match=item;
        }
        return match;
    }
    static void BackgroundMenuAction(AutomationElement item,Action check,bool expand) {
        check();object pattern;
        if(item==null||!AvailableMenuControl(item))throw new Exception("请先在 Claude 中启用开发者模式，再重试初始化连接");
        if(expand&&item.TryGetCurrentPattern(ExpandCollapsePattern.Pattern,out pattern))((ExpandCollapsePattern)pattern).Expand();
        else if(item.TryGetCurrentPattern(InvokePattern.Pattern,out pattern))((InvokePattern)pattern).Invoke();
        else throw new Exception("Claude 开发者菜单不支持后台操作，请在电脑端打开开发者工具后重试");
    }
    static void OpenBackgroundDevTools(Process process,long started) {
        CheckBackgroundProcess(process,started);
        var before=ReadBackgroundWindows(process.Id);var prior=new HashSet<IntPtr>();foreach(var window in before)prior.Add(window.Handle);
        var selected=SelectWindow(before,process.Id,IntPtr.Zero);
        if(selected.Count!=1||selected.Handle==IntPtr.Zero)throw new Exception("找不到唯一 Claude 主窗口，请在电脑端打开 Claude 后重试");
        IntPtr windowHandle=selected.Handle,foreground=GetForegroundWindow();Action check=()=>CheckBackgroundWindow(process,started,windowHandle);
        var root=AutomationElement.FromHandle(windowHandle);
        try {
            var developer=BackgroundMenuControl(root,ControlType.MenuItem,process.Id,"Developer","开发者","開發者");
            if(developer==null) {
                var menu=BackgroundMenuControl(root,ControlType.Button,process.Id,"Menu","菜单","選單");BackgroundMenuAction(menu,check,false);
                developer=WaitMenuEntry(()=>BackgroundMenuControl(root,ControlType.MenuItem,process.Id,"Developer","开发者","開發者"),check,()=>Thread.Sleep(100));
            }
            BackgroundMenuAction(developer,check,true);
            var show=WaitMenuEntry(()=>BackgroundMenuControl(root,ControlType.MenuItem,process.Id,"Show All Dev Tools","显示所有开发者工具","顯示所有開發者工具"),check,()=>Thread.Sleep(100));
            BackgroundMenuAction(show,check,false);
            for(int i=0;i<40;i++) {check();if(SelectBackgroundDevTools(ReadBackgroundWindows(process.Id),process.Id).Count!=0)break;Thread.Sleep(100);}
            // Close only the shell tool created by this invocation. Never close
            // pre-existing user DevTools or a Claude conversation window.
            foreach(var created in ReadBackgroundWindows(process.Id))if(!prior.Contains(created.Handle)&&ShellDevToolsTitle(created.Title)) {
                CheckBackgroundWindow(process,started,created.Handle);object pattern;
                var tool=AutomationElement.FromHandle(created.Handle);
                if(!tool.TryGetCurrentPattern(WindowPattern.Pattern,out pattern))throw new Exception("Claude 新建外壳开发者工具无法关闭，请在电脑端处理后重试");
                ((WindowPattern)pattern).Close();
                for(int i=0;i<30&&IsWindow(created.Handle);i++){CheckBackgroundProcess(process,started);Thread.Sleep(100);}
                if(IsWindow(created.Handle))throw new Exception("Claude 新建外壳开发者工具尚未关闭，请稍后重试");
            }
        } finally {Stage("{\"connectPhase\":\"tools-navigation\",\"pid\":"+process.Id+",\"foregroundChanged\":"+(foreground!=GetForegroundWindow()?"true":"false")+"}");}
    }
    static IntPtr BackgroundRenderer(Process process,long started,IntPtr tools) {
        CheckBackgroundWindow(process,started,tools);var handles=new List<IntPtr>();
        EnumChildWindows(tools,delegate(IntPtr window,IntPtr data){uint pid;GetWindowThreadProcessId(window,out pid);var name=new StringBuilder(256);GetClassName(window,name,name.Capacity);if(pid==process.Id&&name.ToString()=="Chrome_RenderWidgetHostHWND")handles.Add(window);return true;},IntPtr.Zero);
        if(handles.Count!=1)throw new Exception("Claude 开发者工具渲染窗口不唯一，后台输入已停止");return handles[0];
    }
    static AutomationElement BackgroundPrompt(Process process,long started,IntPtr tools,IntPtr renderer,bool requireFocus) {
        CheckBackgroundWindow(process,started,tools);CheckBackgroundWindow(process,started,renderer);
        var selected=SelectBackgroundDevTools(ReadBackgroundWindows(process.Id),process.Id);
        if(selected.Count!=1||selected.Handle!=tools||BackgroundRenderer(process,started,tools)!=renderer)
            throw new Exception("Claude 开发者工具目标已改变，后台输入已停止");
        var root=AutomationElement.FromHandle(tools);var tabs=root.FindAll(TreeScope.Descendants,new PropertyCondition(AutomationElement.ControlTypeProperty,ControlType.TabItem));
        int consoles=0;bool consoleSelected=false;
        foreach(AutomationElement tab in tabs)if(tab.Current.ProcessId==process.Id&&!tab.Current.IsOffscreen&&(tab.Current.AutomationId=="tab-console"||tab.Current.Name=="Console"||tab.Current.Name=="控制台")) {
            consoles++;object pattern;consoleSelected=tab.TryGetCurrentPattern(SelectionItemPattern.Pattern,out pattern)&&((SelectionItemPattern)pattern).Current.IsSelected;
        }
        if(consoles!=1||!consoleSelected)throw new Exception("Claude 开发者工具未停留在 Console，请在电脑端选择 Console 后重试");
        var elements=root.FindAll(TreeScope.Descendants,new OrCondition(new PropertyCondition(AutomationElement.AutomationIdProperty,"console-prompt"),new PropertyCondition(AutomationElement.NameProperty,"Console prompt"),new PropertyCondition(AutomationElement.NameProperty,"控制台提示"),new PropertyCondition(AutomationElement.NameProperty,"控制台提示符")));
        AutomationElement prompt=null;int count=0;
        foreach(AutomationElement element in elements)if(IsPrompt(element)&&element.Current.ProcessId==process.Id&&!element.Current.IsOffscreen&&element.Current.IsEnabled){prompt=element;count++;}
        if(count!=1)throw new Exception("Claude Console 输入框不唯一，后台输入已停止");
        if(requireFocus) {
            uint pid;uint thread=GetWindowThreadProcessId(renderer,out pid);var gui=new BackgroundGui();gui.size=Marshal.SizeOf(gui);
            if(!prompt.Current.HasKeyboardFocus||!GetGUIThreadInfo(thread,ref gui)||gui.focus!=renderer)
                throw new Exception("Claude Console 内部焦点已改变，后台输入未提交");
        }
        return prompt;
    }
    static void ConnectResult(string state,string submission,int pid,string reason) {
        Stage("{\"setupState\":"+JsonString(state)+",\"submission\":"+JsonString(submission)+",\"pid\":"+pid+",\"reason\":"+JsonString(reason)+"}");
    }
    static void SelectNewBackgroundConsole(Process process,long started,IntPtr tools) {
        CheckBackgroundWindow(process,started,tools);var root=AutomationElement.FromHandle(tools);
        var tab=BackgroundMenuControl(root,ControlType.TabItem,process.Id,"Console","控制台");object pattern;
        if(tab==null||!tab.TryGetCurrentPattern(SelectionItemPattern.Pattern,out pattern))throw new Exception("Claude Console 标签不可用，请在电脑端选择后重试");
        CheckBackgroundWindow(process,started,tools);((SelectionItemPattern)pattern).Select();Thread.Sleep(150);
    }
    static void SubmitBackgroundVerifiedConsole(string text,Func<string> readDraft,Action<string> replace,Func<string> readDocument,
        Action check,Action pause,Func<bool> expired,Action reset,Func<bool> retryEmpty,Action submit) {
        for(int attempt=0;attempt<2;attempt++) {
            check();reset();check();if(!String.IsNullOrWhiteSpace(readDraft()))throw new Exception("Console 中已有未提交内容，已保留，请处理后重试连接");
            bool enteredSubmit=false;
            try {SubmitVerifiedConsole(text,readDraft,replace,readDocument,check,pause,expired,delegate{enteredSubmit=true;submit();});return;}
            catch {
                if(attempt!=0||enteredSubmit)throw;
                check();if(!String.IsNullOrWhiteSpace(readDraft())||!retryEmpty())throw;
                pause();check();if(!String.IsNullOrWhiteSpace(readDraft())||!retryEmpty())throw;
            }
        }
    }
    static void ConnectBackground(Process process,string text) {
        long started=process.StartTime.ToUniversalTime().Ticks;CheckBackgroundProcess(process,started);
        var tools=SelectBackgroundDevTools(ReadBackgroundWindows(process.Id),process.Id);
        bool opened=tools.Count==0;
        if(opened){OpenBackgroundDevTools(process,started);tools=SelectBackgroundDevTools(ReadBackgroundWindows(process.Id),process.Id);}
        if(tools.Count!=1||tools.Handle==IntPtr.Zero)throw new Exception("找不到唯一 app://localhost Claude 开发者工具，请在电脑端检查后重试");
        IntPtr window=tools.Handle;if(opened)SelectNewBackgroundConsole(process,started,window);IntPtr renderer=BackgroundRenderer(process,started,window);
        AutomationElement prompt=null;ValuePattern value=null;TextPattern document=null;var timer=new Stopwatch();
        Action acquire=delegate {
            prompt=BackgroundPrompt(process,started,window,renderer,false);object valueObject,textObject;
            if(!prompt.TryGetCurrentPattern(ValuePattern.Pattern,out valueObject)||((ValuePattern)valueObject).Current.IsReadOnly||!prompt.TryGetCurrentPattern(TextPattern.Pattern,out textObject))
                throw new Exception("Claude Console 不支持核验后台写入，请在电脑端手动初始化连接");
            value=(ValuePattern)valueObject;document=(TextPattern)textObject;timer.Restart();
        };
        Action check=()=>BackgroundPrompt(process,started,window,renderer,false);
        SubmitBackgroundVerifiedConsole(text,()=>Contents(prompt),textValue=>value.SetValue(textValue),()=>document.DocumentRange.GetText(-1).TrimEnd('\r','\n'),check,()=>Thread.Sleep(50),()=>timer.Elapsed.TotalSeconds>=6,acquire,
            delegate {if(backgroundWork!=null&&backgroundWork.Started)return false;var current=BackgroundPrompt(process,started,window,renderer,true);return Automation.Compare(prompt,current);},delegate {
            var current=BackgroundPrompt(process,started,window,renderer,true);
            if(!Automation.Compare(prompt,current)||Contents(current)!=text)throw new Exception("Claude Console 内容已改变，后台输入未提交");
            // This flushed marker precedes the only targeted input operation.
            // A lost helper response from this point is an uncertain submit.
            backgroundWork.Dispatch(process.Id);
            if(!PostMessage(renderer,0x0100,new IntPtr(13),new IntPtr(0x001c0001))){backgroundSubmission="none";throw new Exception("Windows 未接收 Claude Console 提交，请检查权限后重试");}
            if(!PostMessage(renderer,0x0101,new IntPtr(13),new IntPtr(unchecked((int)0xc01c0001))))throw new Exception("Claude Console 提交回执不完整，请等待连接状态核对");
            backgroundSubmission="submitted";
        });
    }

    static string ReadContents(string text,string value) {
        // Chromium contenteditable exposes an empty ValuePattern even when its
        // TextPattern contains the entire CodeMirror document.
        if(!String.IsNullOrEmpty(text)) return text;
        if(value!=null) return value;
        if(text!=null) return text;
        throw new Exception("无法核对 Console 内容，已停止输入");
    }
    static string Contents(AutomationElement prompt) {
        object pattern;string text=null,value=null;
        if(prompt.TryGetCurrentPattern(TextPattern.Pattern,out pattern)) text=((TextPattern)pattern).DocumentRange.GetText(-1);
        if(!String.IsNullOrEmpty(text))return text.TrimEnd('\r','\n');
        if(prompt.TryGetCurrentPattern(ValuePattern.Pattern,out pattern)) value=((ValuePattern)pattern).Current.Value;
        return ReadContents(text,value).TrimEnd('\r','\n');
    }
    static void SubmitVerifiedConsole(string text,Func<string> readDraft,Action<string> replace,
        Func<string> readDocument,Action check,Action pause,Func<bool> expired,Action submit) {
        check();
        string pending=readDraft().Trim('\r','\n',' ','\t','\u200b','\ufeff');
        if(!String.IsNullOrWhiteSpace(pending)&&!pending.StartsWith("/* codex bridge connector */"))
            throw new Exception("Console 中已有未提交内容，已保留，请处理后重试连接");
        check();replace(text);
        // Chromium applies accessibility edits asynchronously. ValuePattern may
        // echo the new value before CodeMirror's actual document has changed.
        while(true) {
            check();
            if(readDocument()==text)break;
            if(expired())throw new Exception("Console 脚本尚未完整写入，未执行；请通过手动初始化重试");
            pause();
        }
        check();submit();
    }
    static void WriteConsoleOnce(AutomationElement prompt,string text) {
        CheckPrompt(prompt);
        object valueObject,textObject;
        if(!prompt.TryGetCurrentPattern(ValuePattern.Pattern,out valueObject)||
            ((ValuePattern)valueObject).Current.IsReadOnly||
            !prompt.TryGetCurrentPattern(TextPattern.Pattern,out textObject))
            throw new Exception("当前 Claude Console 不支持一次写入，请在电脑端手动初始化连接");
        var value=(ValuePattern)valueObject;var document=(TextPattern)textObject;
        var timer=Stopwatch.StartNew();
        SubmitVerifiedConsole(text,()=>Contents(prompt),value.SetValue,
            ()=>document.DocumentRange.GetText(-1).TrimEnd('\r','\n'),()=>CheckPrompt(prompt),
            ()=>Thread.Sleep(50),()=>timer.Elapsed.TotalSeconds>=6,delegate {
                Stage("脚本已完整写入并核对，正在提交");
                Send(new[]{Key(13,false),Key(13,true)});
            });
    }
    static void CheckWindowSelection() {
        var owned=new AppWindow {Handle=new IntPtr(11),Owner=new IntPtr(99),Pid=7,Visible=true,Title="Claude"};
        var other=new AppWindow {Handle=new IntPtr(12),Pid=7,Visible=true,Title="Claude conversation"};
        var excluded=new [] {
            new AppWindow {Handle=new IntPtr(13),Pid=8,Visible=true,Title="Claude"},
            new AppWindow {Handle=new IntPtr(14),Pid=7,Visible=false,Title="Claude"},
            new AppWindow {Handle=new IntPtr(15),Pid=7,Visible=true,Title="DevTools - app://localhost"},
            new AppWindow {Handle=new IntPtr(16),Pid=7,Visible=true,ToolWindow=true,Title="Claude"},
            new AppWindow {Handle=new IntPtr(17),Pid=7,Visible=true,Cloaked=true,Title="Claude"}};
        if(SelectWindow(new[]{owned},7,IntPtr.Zero).Handle!=owned.Handle||SelectWindow(excluded,7,IntPtr.Zero).Count!=0)
            throw new Exception("Claude 窗口范围自检失败");
        var ambiguous=SelectWindow(new[]{owned,other},7,IntPtr.Zero);
        if(ambiguous.Count!=2||ambiguous.Handle!=IntPtr.Zero||SelectWindow(new[]{owned,other},7,other.Handle).Handle!=other.Handle)
            throw new Exception("Claude 多窗口选择自检失败");
        int scans=0,restores=0,pauses=0;
        var result=WaitWindow(()=>++scans<3?new WindowSelection():new WindowSelection {Handle=owned.Handle,Count=1},
            ()=>restores++,()=>{},()=>pauses++,4);
        if(result!=owned.Handle||restores!=1||pauses!=2)throw new Exception("Claude 托盘恢复自检失败");
        restores=0;int checks=0;bool cancelled=false;
        try {WaitWindow(()=>new WindowSelection(),()=>restores++,()=>{if(++checks==3)throw new OperationCanceledException();},()=>{},4);}
        catch(OperationCanceledException){cancelled=true;}
        if(!cancelled||restores!=1)throw new Exception("Claude 等待取消自检失败");
        restores=0;cancelled=false;
        try {WaitWindow(()=>new WindowSelection(),()=>restores++,()=>{throw new OperationCanceledException();},()=>{},4);}
        catch(OperationCanceledException){cancelled=true;}
        if(!cancelled||restores!=0)throw new Exception("Claude 启动前取消自检失败");
        restores=0;bool rejected=false;
        try {WaitWindow(()=>ambiguous,()=>restores++,()=>{},()=>{},4);}
        catch(Exception){rejected=true;}
        if(!rejected||restores!=0)throw new Exception("Claude 歧义窗口恢复自检失败");
    }
    static void CheckMenuNavigation() {
        var actions=new List<string>();int stage=0;
        Func<string,string> modern=role=>
            (role=="menu"&&stage==0||role=="help"&&stage==1||role=="troubleshooting"&&stage==2||role=="enable"&&stage==3)?role:null;
        Action<string> invoke=item=>{actions.Add("invoke:"+item);stage++;};
        Action<string> expand=item=>{actions.Add("expand:"+item);stage++;};
        EnableDeveloperMenu(modern,invoke,expand,()=>{},()=>{});
        if(String.Join(",",actions.ToArray())!="invoke:menu,expand:help,expand:troubleshooting,invoke:enable")
            throw new Exception("Claude 自定义菜单导航自检失败");
        actions.Clear();stage=0;
        EnableDeveloperMenu(role=>role=="help"||role=="enable"&&stage==1?role:null,invoke,expand,()=>{},()=>{});
        if(String.Join(",",actions.ToArray())!="expand:help,invoke:enable")throw new Exception("Claude 经典菜单导航自检失败");
        actions.Clear();stage=0;bool cancelled=false;
        try {EnableDeveloperMenu(modern,invoke,expand,()=>{if(stage==3)throw new OperationCanceledException();},()=>{});}
        catch(OperationCanceledException){cancelled=true;}
        if(!cancelled||actions.Contains("invoke:enable"))throw new Exception("Claude 菜单取消自检失败");
        actions.Clear();stage=0;bool unavailable=false;
        try {EnableDeveloperMenu(role=>role=="help"?role:null,invoke,expand,()=>{},()=>{});}
        catch(Exception){unavailable=true;}
        if(!unavailable||String.Join(",",actions.ToArray())!="expand:help")throw new Exception("Claude 菜单允许列表自检失败");
    }
    static void CheckNativeLaunch() {
        var standalone=NativeLaunch(@"C:\Programs\Claude\Claude.exe",null,@"C:\Windows");
        if(standalone.FileName!=@"C:\Programs\Claude\Claude.exe"||standalone.Arguments!=""||standalone.UseShellExecute)
            throw new Exception("Claude 独立应用启动自检失败");
        var store=NativeLaunch(@"C:\Program Files\WindowsApps\Claude\app\Claude.exe","Claude_fixturepublisher!Claude",@"C:\Windows");
        if(store.FileName!=@"C:\Windows\explorer.exe"||store.Arguments!=@"shell:AppsFolder\Claude_fixturepublisher!Claude"||
            store.UseShellExecute||!store.CreateNoWindow||store.WindowStyle!=ProcessWindowStyle.Hidden)
            throw new Exception("Claude 商店应用启动自检失败");
        bool rejected=false;
        try {NativeLaunch(@"C:\Claude.exe","Claude_fixturepublisher!Claude --other",@"C:\Windows");}
        catch(Exception){rejected=true;}
        if(!rejected)throw new Exception("Claude 启动入口允许列表自检失败");
    }
    static void CheckDirectSubmission() {
        const string text="/* codex bridge connector */void 0;";
        int writes=0,submits=0,pauses=0;
        SubmitVerifiedConsole(text,()=>"/* codex bridge connector */partial",
            value=>{if(value!=text)throw new Exception("wrong write");writes++;},
            ()=>pauses==0?"old document":text,()=>{},()=>pauses++,()=>pauses>=3,()=>submits++);
        if(writes!=1||submits!=1||pauses!=1)throw new Exception("Console 异步整段写入自检失败");
        writes=0;submits=0;pauses=0;bool refused=false;
        try {SubmitVerifiedConsole(text,()=>"",value=>writes++,()=>"partial",()=>{},()=>pauses++,()=>pauses>=2,()=>submits++);}
        catch(Exception){refused=true;}
        if(!refused||writes!=1||submits!=0)throw new Exception("Console 不完整脚本拒绝执行自检失败");
        writes=0;submits=0;refused=false;
        try {SubmitVerifiedConsole(text,()=>"userCommand()",value=>writes++,()=>text,()=>{},()=>{},()=>true,()=>submits++);}
        catch(Exception){refused=true;}
        if(!refused||writes!=0||submits!=0)throw new Exception("Console 用户草稿保护自检失败");
        writes=0;submits=0;int checks=0;refused=false;
        try {SubmitVerifiedConsole(text,()=>"",value=>writes++,()=>text,
            ()=>{if(++checks==4)throw new Exception("focus changed before Enter");},()=>{},()=>false,()=>submits++);}
        catch(Exception){refused=true;}
        if(!refused||writes!=1||submits!=0)throw new Exception("Console 提交前焦点保护自检失败");
        writes=0;submits=0;checks=0;refused=false;
        try {SubmitVerifiedConsole(text,()=>"",value=>writes++,()=>"partial",
            ()=>{if(++checks==4)throw new OperationCanceledException();},()=>{},()=>false,()=>submits++);}
        catch(OperationCanceledException){refused=true;}
        if(!refused||writes!=1||submits!=0)throw new Exception("Console 异步等待取消自检失败");
        writes=0;submits=0;refused=false;
        try {SubmitVerifiedConsole(text,()=>"",value=>writes++,()=>text,
            ()=>{throw new OperationCanceledException();},()=>{},()=>false,()=>submits++);}
        catch(OperationCanceledException){refused=true;}
        if(!refused||writes!=0||submits!=0)throw new Exception("Console 写入前取消自检失败");
    }
    static void CheckDetachedReuse() {
        var tools=new AppWindow {Handle=new IntPtr(21),Pid=7,Visible=true,Title="DevTools - app://localhost"};
        var main=new AppWindow {Handle=new IntPtr(22),Pid=7,Visible=true,Title="Claude"};
        var foreign=new AppWindow {Handle=new IntPtr(23),Pid=8,Visible=true,Title="DevTools - app://localhost"};
        var duplicate=new AppWindow {Handle=new IntPtr(24),Pid=7,Visible=true,Title="Developer Tools - app://localhost"};
        if(SelectDevTools(new[]{main,foreign,tools},7).Handle!=tools.Handle||SelectDevTools(new[]{main,foreign},7).Count!=0)
            throw new Exception("Claude 已有开发者工具归属自检失败");
        var ambiguous=SelectDevTools(new[]{tools,duplicate},7);
        if(ambiguous.Count!=2||ambiguous.Handle!=IntPtr.Zero)throw new Exception("Claude 开发者工具歧义自检失败");
        tools.Visible=false;
        if(SelectDevTools(new[]{tools},7).Count!=0)throw new Exception("Claude 隐藏开发者工具自检失败");
    }
    static void CheckNativeQuit() {
        if(!SameQuitTarget(false,true,11,11,true,7,7)||SameQuitTarget(true,true,11,11,true,7,7)||
           SameQuitTarget(false,false,11,11,true,7,7)||SameQuitTarget(false,true,12,11,true,7,7)||
           SameQuitTarget(false,true,11,11,false,7,7)||SameQuitTarget(false,true,11,11,true,8,7))
            throw new Exception("Claude 原生退出目标身份自检失败");
        var actions=new List<string>();int stage=0;
        Func<string,string> find=role=>(role=="menu"&&stage==0||role=="file"&&stage==1||role=="exit"&&stage==2)?role:null;
        Action<string> invoke=item=>{actions.Add("invoke:"+item);stage++;};
        Action<string> expand=item=>{actions.Add("expand:"+item);stage++;};
        QuitMenu(find,invoke,expand,()=>{},()=>{});
        if(String.Join(",",actions.ToArray())!="invoke:menu,expand:file,invoke:exit")throw new Exception("Claude 原生退出导航自检失败");
        actions.Clear();stage=0;bool cancelled=false;
        try {QuitMenu(find,invoke,expand,()=>{if(stage==2)throw new OperationCanceledException();},()=>{});}
        catch(OperationCanceledException){cancelled=true;}
        if(!cancelled||actions.Contains("invoke:exit"))throw new Exception("Claude 原生退出取消自检失败");
        actions.Clear();stage=0;bool missing=false;
        try {QuitMenu(role=>role=="file"?role:null,invoke,expand,()=>{},()=>{});}
        catch(Exception){missing=true;}
        if(!missing||actions.Contains("invoke:exit"))throw new Exception("Claude 缺失退出菜单自检失败");
        if(!QuitMenuName("exit",ControlType.MenuItem,"Exit")||!QuitMenuName("exit",ControlType.MenuItem,"退出")||
           !QuitMenuName("exit",ControlType.MenuItem,"結束")||QuitMenuName("exit",ControlType.Button,"Exit")||
           QuitMenuName("exit",ControlType.Button,"Quit anyway")||QuitMenuName("exit",ControlType.MenuItem,"Close Window"))
            throw new Exception("Claude 退出菜单允许列表自检失败");
        bool ambiguous=false;
        try {UniqueQuitControl(new[]{"exit","exit"},item=>item=="exit");}catch(Exception){ambiguous=true;}
        if(!ambiguous)throw new Exception("Claude 退出菜单歧义自检失败");
        int checks=0;
        if(ObserveQuit(()=>++checks==2,()=>false,()=>{},3)!="exited"||
           ObserveQuit(()=>false,()=>true,()=>{},3)!="pending"||
           ObserveQuit(()=>false,()=>false,()=>{},3)!="submitted")throw new Exception("Claude 退出结果自检失败");
    }
    static void CheckBackgroundConnection() {
        foreach(string title in new[]{"DevTools - app://localhost","Developer Tools - app://localhost/new?example=1","开发者工具 - app://localhost/"})
            if(!AppDevToolsTitle(title))throw new Exception("Claude 后台来源允许列表自检失败");
        foreach(string title in new[]{"DevTools - app://localhost.evil","DevTools - app://user@localhost","DevTools - app://localhost:80","DevTools - https://localhost","Developer Tools - file:///C:/Claude/.vite/renderer/main_window/index.html","Claude"})
            if(AppDevToolsTitle(title))throw new Exception("Claude 后台来源拒绝列表自检失败");
        var content=new AppWindow {Handle=new IntPtr(31),Pid=7,Visible=true,Title="Developer Tools - app://localhost/new"};
        var shell=new AppWindow {Handle=new IntPtr(32),Pid=7,Visible=true,Title="Developer Tools - file:///C:/Claude/.vite/renderer/main_window/index.html"};
        var foreign=new AppWindow {Handle=new IntPtr(33),Pid=8,Visible=true,Title=content.Title};
        if(SelectBackgroundDevTools(new[]{content,shell,foreign},7).Handle!=content.Handle||!ShellDevToolsTitle(shell.Title))throw new Exception("Claude 后台内容与外壳隔离自检失败");
        var duplicate=new AppWindow {Handle=new IntPtr(34),Pid=7,Visible=true,Title=content.Title};
        if(SelectBackgroundDevTools(new[]{content,duplicate},7).Handle!=IntPtr.Zero)throw new Exception("Claude 后台来源歧义自检失败");
        content.Visible=false;if(SelectBackgroundDevTools(new[]{content,shell},7).Count!=0)throw new Exception("Claude 后台隐藏窗口拒绝自检失败");
        bool rejected=false;try{VerifiedDevToolsTitle(content.Title,shell.Title);}catch(Exception){rejected=true;}
        if(!rejected||VerifiedDevToolsTitle("Developer Tools",content.Title)!=content.Title)throw new Exception("Claude 后台来源交叉核验自检失败");
        const string text="/* codex bridge connector */void 0;";
        foreach(string mode in new[]{"ready","blank-first","partial","draft","interrupted","submit-error","cancelled","always-blank"}) {
            int writes=0,submits=0,resets=0,retries=0;bool failed=false;
            Func<string> read=()=>mode=="draft"?"user draft":mode=="partial"&&writes>0?"partial":mode=="interrupted"&&retries>=2?"new user draft":"";
            try {SubmitBackgroundVerifiedConsole(text,read,value=>writes++,()=>mode=="ready"||mode=="submit-error"||mode=="blank-first"&&writes==2?text:mode=="partial"?"partial":"",()=>{},()=>{},()=>true,()=>resets++,
                delegate{retries++;if(mode=="cancelled")throw new OperationCanceledException();return true;},delegate{submits++;if(mode=="submit-error")throw new Exception("lost callback");});}
            catch(Exception){failed=true;}
            if(mode=="ready"&&(failed||writes!=1||submits!=1)||mode=="blank-first"&&(failed||writes!=2||submits!=1)||
               mode=="draft"&&(!failed||writes!=0||submits!=0)||mode=="submit-error"&&(!failed||writes!=1||submits!=1)||
               mode=="always-blank"&&(!failed||writes!=2||submits!=0)||
               (mode=="partial"||mode=="interrupted"||mode=="cancelled")&&(!failed||writes!=1||submits!=0))
                throw new Exception("Claude 后台整段提交与有界空白重试自检失败: "+mode);
        }
        int dispatches=0;var cancelled=new BackgroundOperation();cancelled.Cancel();rejected=false;
        try{cancelled.ClaimDispatch(()=>dispatches++);}catch(OperationCanceledException){rejected=true;}
        if(!rejected||dispatches!=0)throw new Exception("Claude 后台取消与提交互斥自检失败");
        var dispatched=new BackgroundOperation();dispatched.ClaimDispatch(()=>dispatches++);if(!dispatched.Cancel()||dispatches!=1)throw new Exception("Claude 后台提交不确定状态自检失败");
        rejected=false;try{dispatched.ClaimDispatch(()=>dispatches++);}catch(OperationCanceledException){rejected=true;}
        if(!rejected||dispatches!=1||String.IsNullOrWhiteSpace(FailureMessage(new COMException(""),true)))throw new Exception("Claude 后台重复提交与空错误保护自检失败");
    }
    static string FailureMessage(Exception error,bool quitting) {
        if(error is System.ComponentModel.Win32Exception)return "无法读取 Claude 窗口，请检查运行权限";
        return !String.IsNullOrWhiteSpace(error.Message)?error.Message:quitting?"Claude 原生退出菜单操作失败，请在电脑端检查后重试":"Claude 原生连接操作失败，请在电脑端检查后重试";
    }
    [STAThread] static int Main(string[] args) {
        Console.OutputEncoding=new UTF8Encoding(false);
        try {
            if(args.Length==1&&args[0]=="--self-check") {
                if(Marshal.SizeOf(typeof(INPUT))!=(IntPtr.Size==8?40:28)||!PromptName("Console prompt","")||PromptName("Message Claude","")) throw new Exception("键盘组件自检失败");
                if(ReadContents("script","")!="script"||ReadContents("","value")!="value"||ReadContents(null,"value")!="value"||ReadContents("",null)!="")throw new Exception("Console 文本读取自检失败");
                if(!DevToolsTitle("Developer Tools - app://localhost/new")||!DevToolsTitle("DevTools - app://localhost")||DevToolsTitle("Claude"))throw new Exception("开发者工具窗口匹配自检失败");
                Func<string,string> parent=value=>value=="editor-child"?"expected-console":value=="other-editor"?"other-console":null;
                if(!FocusedWithin("expected-console","editor-child",parent,(a,b)=>a==b)||FocusedWithin("expected-console","other-editor",parent,(a,b)=>a==b)||FocusedWithin("expected-console","other-console",parent,(a,b)=>a==b))throw new Exception("Console 精确焦点自检失败");
                CheckWindowSelection();
                CheckMenuNavigation();
                CheckNativeLaunch();
                CheckDirectSubmission();
                CheckDetachedReuse();
                CheckNativeQuit();
                CheckBackgroundConnection();
                Console.WriteLine("keyboard ABI, Console allowlist and contenteditable text fallback OK; window selection and cancellable restore OK; menu navigation OK; packaged launch OK; atomic console submission OK; detached console reuse OK; native menu quit guards OK; background console guards OK");return 0;
            }
            if(args.Length!=3 && args.Length!=4) throw new Exception("键盘连接参数无效");
            quitAction=args[1]=="--quit";
            backgroundAction=args[1].StartsWith("--connect-background=",StringComparison.Ordinal);
            cancelFile=args.Length==4?args[3]:null;
            executable=Path.GetFullPath(args[2]);
            var process=Process.GetProcessById(Int32.Parse(args[0]));
            quitPid=process.Id;
            if(!Owned(process.Id)) throw new Exception("目标不是 Claude");
            if(quitAction) {QuitNative(process);return 0;}
            if(args[1]=="--inspect-window") {InspectWindow(process);return 0;}
            if(args[1]=="--close-devtools") {CloseDevTools();return 0;}
            if(args[1]=="--enable-devtools") {GetAsyncKeyState(27);EnableDeveloperMode(process);return 0;}
            if(args[1]=="--inspect-error") {
                var texts=AutomationElement.FromHandle(WaitAppWindow(process,false)).FindAll(TreeScope.Descendants,new PropertyCondition(AutomationElement.ControlTypeProperty,ControlType.Text));
                foreach(AutomationElement item in texts) {
                    string value=item.Current.Name;
                    if((value.StartsWith("Error:")||value.StartsWith("Uncaught"))&&(value.Contains("尚未信任目录")||value.Contains("WorkspaceTrustError")||value.Contains("trust_required"))) {Stage("NEEDS_TRUST");return 0;}
                }
                Stage("NO_TRUST_ERROR");return 0;
            }
            string script=backgroundAction?args[1].Substring("--connect-background=".Length):args[1];
            string text=File.ReadAllText(script,Encoding.UTF8);
            if(text.Length>300000||text.Contains("\n")||text.Contains("\r")||!text.StartsWith("/* codex bridge connector */")) throw new Exception("连接脚本格式无效");
            if(backgroundAction){RunBackgroundConnection(process,text);return 0;}
            Stage("正在等待 Claude 窗口");
            GetAsyncKeyState(27); // Clear an old Escape press, subsequent presses cancel.
            var existingTools=SelectDevTools(ReadWindows(process.Id),process.Id);
            if(existingTools.Count>1)throw new Exception("检测到多个 Claude 开发者工具窗口，请关闭多余窗口后重试");
            bool reuseTools=existingTools.Handle!=IntPtr.Zero;
            IntPtr window=reuseTools?existingTools.Handle:WaitAppWindow(process,true);
            inputWindow=window;
            Activate(window);
            Stage("已定位 Claude 窗口");
            if(reuseTools)SelectConsoleTab();
            var prompt=ConsolePrompt();
            var previousWindows=new System.Collections.Generic.HashSet<IntPtr>();
            EnumWindows(delegate(IntPtr handle,IntPtr data){previousWindows.Add(handle);return true;},IntPtr.Zero);
            if(prompt==null&&!reuseTools) {Shortcut();inputWindow=IntPtr.Zero;}
            Stage("等待 Console 输入框");
            var deadline=DateTime.UtcNow.AddSeconds(8);
            while(prompt==null&&DateTime.UtcNow<deadline) {
                Thread.Sleep(250);CheckForeground();
                var current=GetForegroundWindow();
                if(current!=window) {
                    var title=new StringBuilder(512);GetWindowText(current,title,title.Capacity);
                    if(previousWindows.Contains(current)||!DevToolsTitle(title.ToString()))throw new Exception("Claude 窗口已改变，自动连接已停止，请重试");
                }
                SelectConsoleTab();prompt=ConsolePrompt();
            }
            if(prompt==null) throw new Exception("找不到 Claude Console 输入框，请确认已允许开发者工具后通过标题重试");
            inputWindow=GetForegroundWindow();
            Stage("已定位 Console 输入框");
            // This foreground bootstrap is used only for explicit manual
            // initialization. Reconnects never launch this keyboard helper.
            FocusPrompt(prompt);WriteConsoleOnce(prompt,text);Thread.Sleep(700);
            Console.WriteLine("脚本已输入，等待连接回执");return 0;
        } catch(Exception e) {
            string message=FailureMessage(e,quitAction);
            if(quitAction)QuitResult("failed",message,quitPid);else if(backgroundAction)ConnectResult("failed",backgroundSubmission,quitPid,message);else Console.WriteLine(message);
            return 1;
        }
    }
}
