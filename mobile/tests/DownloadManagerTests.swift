import Foundation

@main enum DownloadManagerTests {
    static func wait(_ message: String, seconds: Double = 12, until condition: () -> Bool) {
        let limit = Date().addingTimeInterval(seconds)
        while !condition(), Date() < limit { RunLoop.current.run(until: Date().addingTimeInterval(0.01)) }
        precondition(condition(), message)
    }
    static func settle(_ seconds: Double = 0.15) { RunLoop.current.run(until: Date().addingTimeInterval(seconds)) }
    static func main() throws {
        let origin = CommandLine.arguments[1], scratch = URL(fileURLWithPath: CommandLine.arguments[2])
        let path = "/api/desktop-sessions/claude/workspace/download"
        precondition(DownloadManager.allows(URL(string: origin + path + "?path=a")!, origin: origin))
        precondition(!DownloadManager.allows(URL(string: "https://other.invalid" + path)!, origin: origin))
        precondition(!DownloadManager.allows(URL(string: origin + "/api/desktop-sessions/claude/send")!, origin: origin))
        precondition(DownloadManager.filename("../folder/hello.txt") == "hello.txt")
        precondition(DownloadManager.filename("..") == "download")
        for scenario in ["pause", "changed", "changed-409", "offline-409", "etag", "ignored-range", "revoked", "cancel", "wrong-range", "redirect", "unauthorized", "oversize", "no-etag", "unknown", "unknown-range", "empty", "large"] {
            let manager = DownloadManager(scratch: scratch)
            var snapshot = DownloadManager.Snapshot(), requestedPause = false, cookie: String? = "codex_mobile_session=fixture"
            var credentialReads = 0, sawUnknownTotal = false
            let pauses = ["pause", "changed", "changed-409", "offline-409", "etag", "ignored-range", "revoked", "cancel"].contains(scenario)
            manager.credentials = { _, _, done in credentialReads += 1; done(cookie) }
            manager.onChange = { value in
                snapshot = value
                if value.phase == .downloading, value.total == nil { sawUnknownTotal = true }
                if pauses, !requestedPause, value.phase == .downloading, value.received > 65536 {
                    requestedPause = true; manager.pause()
                }
            }
            manager.start(URL(string: origin + path + "?case=" + scenario)!, origin: origin)
            if pauses {
                wait("did not pause " + scenario) { snapshot.phase == .paused }
                let offset = snapshot.received
                precondition(offset > 0 && offset < 3 * 1024 * 1024, "Pause must preserve a real partial file")
                settle(); precondition(snapshot.received == offset, "Paused download still grew")
                manager.markSaved(); settle(); precondition(snapshot.phase == .paused, "An incomplete transfer became saved")
                if scenario == "cancel" {
                    manager.cancel(origin: "https://another.invalid"); settle(); precondition(snapshot.phase == .paused)
                    manager.cancel(origin: origin); wait("source removal did not cancel") { snapshot.phase == .cancelled }
                    print(scenario + " passed"); continue
                }
                if scenario == "revoked" { cookie = nil } else { cookie = "codex_mobile_session=renewed" }
                manager.resume()
                wait("resume did not complete " + scenario) { [.complete, .failed].contains(snapshot.phase) }
                precondition(credentialReads >= 2, "Resume reused cached credentials")
                if scenario != "pause" {
                    precondition(snapshot.phase == .failed && snapshot.received == offset, "Bad resume appended incompatible bytes: " + scenario)
                    if ["revoked", "offline-409"].contains(scenario) { precondition(snapshot.canResume, "Recoverable failure lost the resumable partial file") }
                    else { precondition(!snapshot.canResume, "Changed file offered an unsafe continue") }
                    manager.cancel(); wait("cleanup") { snapshot.phase == .cancelled }; print(scenario + " passed"); continue
                }
            } else { wait("download did not settle " + scenario) { [.complete, .failed].contains(snapshot.phase) } }
            if ["wrong-range", "redirect", "unauthorized", "oversize", "no-etag"].contains(scenario) {
                precondition(snapshot.phase == .failed && snapshot.received == 0, "Rejected response wrote data: " + scenario)
            } else {
                precondition(snapshot.phase == .complete && snapshot.file != nil, "Expected a completed file: " + scenario)
                let data = try Data(contentsOf: snapshot.file!)
                let expected = scenario == "empty" ? 0 : scenario == "unknown" ? 30000 : scenario == "large" ? 55 * 1024 * 1024 : 3 * 1024 * 1024 + 301
                precondition(data.count == expected)
                precondition(data.enumerated().allSatisfy { $0.element == UInt8($0.offset % 251) }, "Resumed file bytes differ from source")
                if scenario == "unknown" { precondition(sawUnknownTotal && snapshot.total == nil, "Unknown total was fabricated") }
                precondition(snapshot.phase != .saved, "Network completion must not claim export")
                manager.markSaved(); wait("export result was not recorded") { snapshot.phase == .saved }
            }
            manager.cancel(); wait("cleanup") { snapshot.phase == .cancelled }
            print(scenario + " passed")
        }
        let reused = DownloadManager(scratch: scratch)
        var reusedState = DownloadManager.Snapshot(), pauseNext = false
        reused.credentials = { _, _, done in done("codex_mobile_session=fixture") }
        reused.onChange = { value in
            reusedState = value
            if pauseNext, value.received > 65536, value.phase == .downloading { pauseNext = false; reused.pause() }
        }
        reused.start(URL(string: origin + path + "?case=unknown")!, origin: origin)
        wait("first reused task") { reusedState.phase == .complete }
        reused.markSaved(); wait("saved before reuse") { reusedState.phase == .saved }
        pauseNext = true; reused.start(URL(string: origin + path + "?case=reuse")!, origin: origin)
        wait("second reused task") { reusedState.phase == .paused }
        precondition(reusedState.canResume, "Previous file's unsupported Range leaked into a new download")
        reused.cancel(); wait("reused cleanup") { reusedState.phase == .cancelled }
        let cancelled = DownloadManager(scratch: scratch)
        var cancelledState = DownloadManager.Snapshot()
        cancelled.onChange = { cancelledState = $0 }
        cancelled.credentials = { _, _, done in DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) { done("codex_mobile_session=fixture") } }
        cancelled.start(URL(string: origin + path + "?case=cancel-before-auth")!, origin: origin)
        wait("cancel credential setup") { cancelledState.phase == .connecting }
        cancelled.cancel(origin: origin); wait("cancel credential callback") { cancelledState.phase == .cancelled }; settle(0.3)
        precondition(cancelledState.phase == .cancelled, "Delayed credentials resurrected a cancelled task")
        let remaining = try FileManager.default.contentsOfDirectory(atPath: scratch.path)
        precondition(remaining.isEmpty, "Cancelled/saved download files leaked")
        print("iOS download streaming, range resume, lifecycle and rejection checks passed")
    }
}
