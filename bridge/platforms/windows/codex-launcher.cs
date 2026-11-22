// A private, immutable GUI CLI override. Never set the gateway's dispatch
// environment globally: only this child receives the private entry command.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text;
using System.Web.Script.Serialization;

class CodexLauncher {
    [DllImport("ntdll.dll")] static extern int NtQueryInformationProcess(
        IntPtr process, int kind, IntPtr buffer, int size, out int returned);

    static string Quote(string value) {
        // CommandLineToArgvW/CRT quoting, including empty and trailing slashes.
        var result = new StringBuilder("\""); int slashes = 0;
        foreach (char c in value) {
            if (c == '\\') { slashes++; continue; }
            result.Append('\\', c == '"' ? slashes * 2 + 1 : slashes);
            result.Append(c); slashes = 0;
        }
        return result.Append('\\', slashes * 2).Append('"').ToString();
    }

    static bool Private(string path, bool directory) {
        if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0) return false;
        FileSystemSecurity acl = directory ? (FileSystemSecurity)Directory.GetAccessControl(path)
                                           : File.GetAccessControl(path);
        var own = WindowsIdentity.GetCurrent().User;
        if (!own.Equals(acl.GetOwner(typeof(SecurityIdentifier))) || !acl.AreAccessRulesProtected) return false;
        foreach (FileSystemAccessRule rule in acl.GetAccessRules(true, true, typeof(SecurityIdentifier)))
            if (rule.AccessControlType != AccessControlType.Allow || !own.Equals(rule.IdentityReference)) return false;
        return true;
    }

    static Process Parent() {
        IntPtr buffer = Marshal.AllocHGlobal(IntPtr.Size * 6);
        try {
            int count;
            if (NtQueryInformationProcess(Process.GetCurrentProcess().Handle, 0, buffer, IntPtr.Size * 6, out count) != 0)
                throw new IOException("Cannot identify launcher parent");
            return Process.GetProcessById(Marshal.ReadIntPtr(buffer, IntPtr.Size * 5).ToInt32());
        } finally { Marshal.FreeHGlobal(buffer); }
    }

    static int Run(string executable, IEnumerable<string> arguments) {
        var info = new ProcessStartInfo(executable, String.Join(" ", arguments.Select(Quote)));
        info.UseShellExecute = false; info.CreateNoWindow = true;
        info.EnvironmentVariables.Remove("CMB_CODEX_DESKTOP_CONFIGURATION");
        // .NET Framework only sets STARTF_USESTDHANDLES when a stream is
        // redirected. Without it, a no-window child can lose the App's pipes.
        // Keep stdin/stdout on their original byte handles; drain stderr as
        // bytes concurrently so neither the handshake nor diagnostics block.
        info.RedirectStandardError = true;
        using (var child = Process.Start(info)) {
            var errors = child.StandardError.BaseStream.CopyToAsync(Console.OpenStandardError());
            child.WaitForExit(); errors.GetAwaiter().GetResult(); return child.ExitCode;
        }
    }

    static Dictionary<string, object> Configuration(string root) {
        try {
            string path = Path.Combine(root, "ordinary-launch.json");
            if (Private(root, true) && File.Exists(path) && Private(path, false) && new FileInfo(path).Length <= 4096)
                return new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(path));
        } catch (IOException) { }
          catch (UnauthorizedAccessException) { }
          catch (ArgumentException) { }
        return null;
    }

    static int Main(string[] args) {
        if (args.Length == 1 && args[0] == "--self-check") {
            Console.WriteLine("Codex ordinary launch dispatcher ready"); return 0;
        }
        try {
            string own = Process.GetCurrentProcess().MainModule.FileName;
            string root = Directory.GetParent(Directory.GetParent(Directory.GetParent(own).FullName).FullName).FullName;
            var record = Configuration(root);
            if (record != null && Convert.ToInt32(record["version"]) == 1) {
                var command = ((System.Collections.IEnumerable)record["command"]).Cast<object>().Select(v => (string)v).ToArray();
                if (command.Length > 0 && File.Exists(command[0]))
                    return Run(command[0], command.Skip(1).Concat(args));
            }
            // A removed gateway or private config never prevents the App's own
            // CLI from starting. Resolve from the actual caller, not old MSIX paths.
            using (var parent = Parent()) {
                string folder = Path.GetDirectoryName(parent.MainModule.FileName);
                foreach (string relative in new[] { @"resources\codex.exe", @"resources\codex-cli\bin\codex.exe" }) {
                    string native = Path.Combine(folder, relative);
                    if (File.Exists(native) && !String.Equals(native, own, StringComparison.OrdinalIgnoreCase))
                        return Run(native, args);
                }
            }
            throw new IOException("Codex bundled executable is unavailable");
        } catch (Exception error) {
            Console.Error.WriteLine("Codex launcher: " + error.Message); return 127;
        }
    }
}
