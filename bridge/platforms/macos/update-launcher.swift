// Keep the updater's child PID while Launch Services gives the GUI its own
// privacy identity. SIGTERM requests normal GUI termination before rollback.
import AppKit
import Foundation

if CommandLine.arguments == [CommandLine.arguments[0], "--self-check"] {
    print("update launch supervisor ready")
    exit(0)
}
let arguments = CommandLine.arguments
let environment = ProcessInfo.processInfo.environment
guard arguments.count == 3, let token = environment["CMB_UPDATE_TOKEN"], !token.isEmpty else { exit(1) }
let target = URL(fileURLWithPath: arguments[1]).standardizedFileURL
let transaction = URL(fileURLWithPath: arguments[2]).standardizedFileURL
let launcherPID = ProcessInfo.processInfo.processIdentifier
var launched: NSRunningApplication?
var stopping = false
let configuration = NSWorkspace.OpenConfiguration()
configuration.activates = false
configuration.createsNewApplicationInstance = true
var childEnvironment = environment
childEnvironment["CMB_UPDATE_LAUNCHER_PID"] = String(launcherPID)
configuration.environment = childEnvironment
signal(SIGTERM, SIG_IGN)
let termination = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
termination.setEventHandler {
    stopping = true
    if let application = launched, !application.isTerminated { application.terminate() }
}
termination.resume()
NSWorkspace.shared.openApplication(at: target, configuration: configuration) { application, error in
    guard let application = application, error == nil,
          application.bundleURL?.standardizedFileURL == target,
          application.processIdentifier != launcherPID else {
        fputs("Update launch failed: \(String(describing: error))\n", stderr)
        exit(1)
    }
    launched = application
    do {
        let receipt: [String:Any] = ["pid":application.processIdentifier, "launcherPid":launcherPID, "token":token]
        try JSONSerialization.data(withJSONObject:receipt).write(to:transaction.appendingPathComponent("launch-services.json"),options:.atomic)
    } catch {
        fputs("Update launch receipt failed: \(error)\n", stderr)
        stopping = true
    }
    if stopping { application.terminate() }
}
let timer = DispatchSource.makeTimerSource(queue:.main)
timer.schedule(deadline:.now(),repeating:.milliseconds(100))
timer.setEventHandler {
    if let application = launched, application.isTerminated { exit(0) }
    // Once the update is committed, the GUI no longer needs a supervisor.
    if !stopping, let bytes = try? Data(contentsOf:transaction.appendingPathComponent("result.json")),
       let result = try? JSONSerialization.jsonObject(with:bytes) as? [String:Any], result["state"] as? String == "updated" { exit(0) }
}
timer.resume()
// AppKit refreshes NSRunningApplication's state only as the main run loop runs.
RunLoop.main.run()
