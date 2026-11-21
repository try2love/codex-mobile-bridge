// Ordinary GUI launches share the selected App's native executor. Other App
// installations/profiles and a removed gateway keep their own bundled CLI.
import Darwin
import Foundation

@_silgen_name("proc_pidpath")
func processPath(_ pid: Int32, _ buffer: UnsafeMutableRawPointer, _ size: UInt32) -> Int32

func canonical(_ path: String) -> String {
    URL(fileURLWithPath: path).standardizedFileURL.resolvingSymlinksInPath().path
}
func image(_ pid: Int32) -> String? {
    var buffer = [CChar](repeating: 0, count: 4096)
    guard processPath(pid, &buffer, UInt32(buffer.count)) > 0 else { return nil }
    return canonical(String(cString: buffer))
}
func bundle(_ path: String) -> String? {
    var result: String?
    var current = URL(fileURLWithPath: path)
    while current.path != "/" {
        if current.pathExtension == "app" { result = current.path }
        current.deleteLastPathComponent()
    }
    return result
}
func privateFile(_ path: String, directory: Bool = false) -> Bool {
    var info = stat()
    guard lstat(path, &info) == 0, info.st_uid == getuid(), info.st_mode & 0o077 == 0 else { return false }
    return info.st_mode & S_IFMT == (directory ? S_IFDIR : S_IFREG)
}
func execute(_ command: [String], _ arguments: [String]) -> Never {
    let values = (command + arguments).map { strdup($0) } + [nil]
    values.withUnsafeBufferPointer { _ = execv(command[0], $0.baseAddress!) }
    for value in values { free(value) }
    fputs("Codex launcher executable is unavailable\n", stderr)
    exit(127)
}

if CommandLine.arguments.dropFirst() == ["--self-check"] {
    print("Codex ordinary launch dispatcher ready")
    exit(0)
}
let own = URL(fileURLWithPath: canonical(CommandLine.arguments[0]))
let root = own.deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().path
let recordPath = root + "/ordinary-launch.json"
var configuration: [String: Any]?
if privateFile(root, directory: true), privateFile(recordPath),
   let bytes = try? Data(contentsOf: URL(fileURLWithPath: recordPath)), bytes.count <= 65536 {
    configuration = (try? JSONSerialization.jsonObject(with: bytes)) as? [String: Any]
}
if CommandLine.arguments.dropFirst() == ["--register-environment"] {
    // A login job sets only the GUI default and exits. Respect another tool's
    // explicit override and do nothing after the gateway has been removed.
    guard let record = configuration, record["version"] as? Int == 1,
          let command = record["command"] as? [String], !command.isEmpty,
          access(command[0], X_OK) == 0 else { exit(0) }
    let read = Process(), output = Pipe()
    read.executableURL = URL(fileURLWithPath: "/bin/launchctl")
    read.arguments = ["getenv", "CODEX_CLI_PATH"]; read.standardOutput = output
    do { try read.run() } catch { exit(1) }
    let data = output.fileHandleForReading.readDataToEndOfFile(); read.waitUntilExit()
    guard read.terminationStatus == 0 || read.terminationStatus == 3 else { exit(1) }
    let previous = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .newlines) ?? ""
    if !previous.isEmpty {
        let old = URL(fileURLWithPath: previous)
        guard old.lastPathComponent == "codex-launcher",
              old.deletingLastPathComponent().deletingLastPathComponent().path == root + "/gui" else { exit(0) }
    }
    let write = Process()
    write.executableURL = URL(fileURLWithPath: "/bin/launchctl")
    write.arguments = ["setenv", "CODEX_CLI_PATH", own.path]
    do { try write.run(); write.waitUntilExit() } catch { exit(1) }
    exit(write.terminationStatus)
}
let arguments = Array(CommandLine.arguments.dropFirst())
let parentPID = getppid()
guard let parent = image(parentPID), let parentBundle = bundle(parent) else {
    fputs("Codex launcher requires an App parent\n", stderr)
    exit(127)
}
let candidates = ["Contents/Resources/codex-cli/bin/codex", "Contents/Resources/codex"]
guard let original = candidates.map({ parentBundle + "/" + $0 }).first(where: {
    access($0, X_OK) == 0 && canonical($0) != canonical(CommandLine.arguments[0])
}) else {
    fputs("Codex bundled executable is unavailable\n", stderr)
    exit(127)
}
if let record = configuration,
   record["version"] as? Int == 1,
   let application = record["application"] as? String,
   let home = record["home"] as? String,
   let cli = record["cli"] as? String,
   let command = record["command"] as? [String], !command.isEmpty,
   let selectedBundle = bundle(application),
   selectedBundle == parentBundle, canonical(cli) == canonical(original),
   parent == canonical(application) || parent.hasPrefix(selectedBundle + "/Contents/Frameworks/"),
   access(command[0], X_OK) == 0 {
    let environment = ProcessInfo.processInfo.environment
    let userHome = environment["HOME"] ?? NSHomeDirectory()
    let currentHome = environment["CODEX_HOME"] ?? userHome + "/.codex"
    let sqliteHome = environment["CODEX_SQLITE_HOME"] ?? currentHome
    if canonical(currentHome) == canonical(home), canonical(sqliteHome) == canonical(home),
       image(parentPID) == parent, getppid() == parentPID {
        // A normal default-home App need not explicitly export CODEX_HOME.
        // Set it only after the selected App/profile is proven. Python then
        // verifies the complete App/host/process generation before sharing.
        if environment["CODEX_HOME"] == nil { setenv("CODEX_HOME", home, 1) }
        execute(command, arguments)
    }
}
execute([original], arguments)
