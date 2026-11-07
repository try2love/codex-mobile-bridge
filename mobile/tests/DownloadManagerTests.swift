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
        let capability = String(repeating: "a", count: 32)
        let cloudEntry = URL(string: origin + "/#cmb-cloud=" + capability)!
        let cloudGrant = DownloadManager.CloudAuthorization(entry: cloudEntry, origin: origin, generation: 7)!
        let cloudTarget = URL(string: origin + path + "?case=cloud")!
        precondition(cloudGrant.headers(for: cloudTarget, origin: origin, generation: 7) == ["X-CMB-Cloud-View": capability])
        precondition(cloudGrant.headers(for: cloudTarget, origin: origin, generation: 8) == nil)
        precondition(cloudGrant.headers(for: cloudTarget, origin: "https://other.invalid", generation: 7) == nil)
        precondition(cloudGrant.headers(for: URL(string: origin + "/api/sessions")!, origin: origin, generation: 7) == nil)
        precondition(cloudGrant.headers(for: URL(string: "http://127.0.0.1:1" + path)!, origin: origin, generation: 7) == nil)
        precondition(DownloadManager.CloudAuthorization(entry: URL(string: origin + "/#cmb-cloud=bad")!, origin: origin, generation: 7) == nil)
        precondition(DownloadManager.CloudAuthorization(entry: URL(string: "https://other.invalid/#cmb-cloud=" + capability)!, origin: "https://other.invalid", generation: 7) == nil)
        let prompt = CloudDownloadPromptFixture(origin, entry: cloudEntry)
        precondition(prompt.invoke(cloudTarget) == "started" && prompt.request?.url == cloudTarget)
        prompt.acceptsDownload = false; precondition(prompt.invoke(cloudTarget) == "rejected"); prompt.acceptsDownload = true
        precondition(prompt.invoke(cloudTarget, token: "old-token") == nil && prompt.request == nil)
        precondition(prompt.invoke(cloudTarget, source: URL(string: "https://other.invalid/")!) == "rejected" && prompt.request == nil)
        precondition(prompt.invoke(cloudTarget, main: false) == "rejected" && prompt.request == nil)
        precondition(prompt.invoke(cloudTarget, view: DownloadWebView(prompt.web?.url)) == nil && prompt.request == nil)
        prompt.generation += 1; precondition(prompt.invoke(cloudTarget) == "rejected" && prompt.request == nil); prompt.generation -= 1
        prompt.cloudView = false; precondition(prompt.invoke(cloudTarget) == "rejected" && prompt.request == nil); prompt.cloudView = true
        UIApplication.shared.applicationState = .inactive
        precondition(prompt.invoke(cloudTarget) == "rejected" && prompt.request == nil)
        UIApplication.shared.applicationState = .active
        prompt.web?.url = URL(string: "https://other.invalid/")!
        precondition(prompt.invoke(cloudTarget) == "rejected" && prompt.request == nil)
        prompt.web?.url = URL(string: origin + "/")!
        precondition(prompt.invoke(URL(string: origin + "/api/sessions")!) == "rejected" && prompt.request == nil)
        precondition(prompt.invoke(URL(string: "https://other.invalid" + path)!) == "rejected" && prompt.request == nil)
        print("cloud native download prompt boundaries passed")
        for scenario in ["cloud", "cloud-revoked", "cloud-redirect", "cloud-invalid-headers"] {
            let manager = DownloadManager(scratch: scratch)
            var state = DownloadManager.Snapshot(), generation = 7, reads = 0, paused = false
            manager.credentials = { _, _, _ in preconditionFailure("Cloud authorization fell back to gateway cookies") }
            manager.authorizationHeaders = { address, target, done in
                reads += 1
                if scenario == "cloud-invalid-headers" { done(["Authorization": "not-allowed"]); return }
                done(cloudGrant.headers(for: target, origin: address, generation: generation))
            }
            manager.onChange = { value in
                state = value
                if scenario == "cloud-revoked", !paused, value.phase == .downloading, value.received > 65536 {
                    paused = true; manager.pause()
                }
            }
            manager.start(URL(string: origin + path + "?case=" + scenario)!, origin: origin)
            if scenario == "cloud-revoked" {
                wait("cloud pause") { state.phase == .paused }
                generation += 1; manager.resume()
            }
            wait("cloud transfer did not settle") { [.complete, .failed].contains(state.phase) }
            if scenario == "cloud" {
                precondition(state.phase == .complete && reads >= 4)
                let data = try Data(contentsOf: state.file!)
                precondition(data.count == 3 * 1024 * 1024 + 301)
                precondition(data.enumerated().allSatisfy { $0.element == UInt8($0.offset % 251) })
            } else { precondition(state.phase == .failed) }
            manager.cancel(); wait("cloud cleanup") { state.phase == .cancelled }
            print(scenario + " passed")
        }
        let diagnostic = "/api/sessions/diagnostics", thread = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        for imagePath in ["/api/sessions/\(thread)/uploads/\(thread)/preview", "/api/sessions/\(thread)/desktop-images/" + String(repeating: "a", count: 64), "/api/desktop-sessions/zcode/uploads/\(thread)/preview", "/api/desktop-sessions/deepseek/images/" + String(repeating: "a", count: 64)] {
            precondition(DownloadManager.allows(URL(string: origin + imagePath + "?download=1&sessionId=fixture")!, origin: origin))
            for suffix in ["", "?download=0", "?download=1&download=0", "?download=1#fragment"] {
                precondition(!DownloadManager.allows(URL(string: origin + imagePath + suffix)!, origin: origin))
            }
            precondition(!DownloadManager.allows(URL(string: "https://other.invalid" + imagePath + "?download=1")!, origin: origin))
        }
        precondition(DownloadManager.allows(URL(string: origin + diagnostic)!, origin: origin))
        precondition(DownloadManager.allows(URL(string: origin + diagnostic + "?thread=" + thread)!, origin: origin))
        for suffix in ["?thread=bad", "?thread=" + thread + "&extra=1", "?other=1", "/extra", "#fragment"] {
            precondition(!DownloadManager.allows(URL(string: origin + diagnostic + suffix)!, origin: origin))
        }
        let scopeA = origin + "/d/" + String(repeating: "a", count: 32)
        let scopeB = origin + "/d/" + String(repeating: "b", count: 32)
        precondition(DownloadManager.allows(URL(string: scopeA + diagnostic)!, origin: scopeA))
        precondition(!DownloadManager.allows(URL(string: scopeA + diagnostic)!, origin: scopeB))
        let report = DownloadManager(scratch: scratch)
        var reportState = DownloadManager.Snapshot()
        report.credentials = { _, _, done in done("codex_mobile_session=fixture") }
        report.onChange = { reportState = $0 }
        report.start(URL(string: origin + diagnostic)!, origin: origin)
        wait("diagnostic download did not finish") { reportState.phase == .complete }
        precondition(reportState.name == "codex-session-diagnostics.json")
        let reportData = try Data(contentsOf: reportState.file!)
        precondition(reportData == Data("{\"hosts\":[],\"phase\":\"discovery\"}".utf8))
        report.cancel(); wait("diagnostic cleanup") { reportState.phase == .cancelled }
        let partialReport = DownloadManager(scratch: scratch)
        var partialState = DownloadManager.Snapshot(), pausedReport = false
        partialReport.credentials = { _, _, done in done("codex_mobile_session=fixture") }
        partialReport.onChange = { value in
            partialState = value
            if !pausedReport, value.phase == .downloading, value.received > 65536 {
                pausedReport = true; partialReport.pause()
            }
        }
        partialReport.start(URL(string: origin + diagnostic + "?thread=" + thread)!, origin: origin)
        wait("diagnostic partial did not pause") { partialState.phase == .paused }
        precondition(partialState.received > 0 && !partialState.canResume, "dynamic report partial was resumable")
        partialReport.cancel(); wait("diagnostic partial cleanup") { partialState.phase == .cancelled }
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
