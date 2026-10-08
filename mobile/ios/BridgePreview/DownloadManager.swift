import Foundation

// A single foreground download survives navigation. All mutable transfer state
// and file writes belong to queue; UI callbacks and credential reads use main.
final class DownloadManager: NSObject, URLSessionDataDelegate, @unchecked Sendable {
    enum Phase: String { case idle, connecting, downloading, paused, failed, complete, saved, cancelled }
    struct Snapshot {
        var phase: Phase = .idle
        var origin = ""
        var name = "download"
        var received: Int64 = 0
        var total: Int64?
        var speed: Double = 0
        var message = ""
        var canResume = true
        var file: URL?
        var visible: Bool { phase != .idle && phase != .cancelled }
        var busy: Bool { ![.idle, .cancelled, .saved].contains(phase) }
    }
    struct TransferError: Error { let message: String; var restart = false }
    static let limit: Int64 = 50 * 1024 * 1024
    static let chunk: Int64 = 1024 * 1024
    var onChange: ((Snapshot) -> Void)?
    // Return only a current, matching authenticated cookie, never a cached login.
    var credentials: ((String, URL, @escaping (String?) -> Void) -> Void)?
    private let queue = DispatchQueue(label: "bridge.download")
    private let scratch: URL
    private lazy var delegateQueue: OperationQueue = {
        let value = OperationQueue(); value.maxConcurrentOperationCount = 1; value.underlyingQueue = queue; return value
    }()
    private lazy var session: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.httpCookieStorage = nil; config.httpShouldSetCookies = false; config.urlCredentialStorage = nil; config.urlCache = nil
        config.timeoutIntervalForRequest = 20; config.timeoutIntervalForResource = 120
        return URLSession(configuration: config, delegate: self, delegateQueue: delegateQueue)
    }()
    private var value = Snapshot()
    private var url: URL?
    private var folder: URL?
    private var handle: FileHandle?
    private var task: URLSessionDataTask?
    private var epoch = 0
    private var etag: String?
    private var requestedEnd: Int64 = 0
    private var responseBytes: Int64 = 0
    private var expectedBytes: Int64?
    private var wholeResponse = false
    private var accepted = false
    private var errorBody: Data?
    private var canContinue = true
    private var rateBytes: Int64 = 0
    private var rateTime = ProcessInfo.processInfo.systemUptime
    private var clock: DispatchSourceTimer?

    init(scratch: URL = FileManager.default.temporaryDirectory) { self.scratch = scratch; super.init() }

    static func allows(_ url: URL, origin: String) -> Bool {
        guard GatewayURL.same(url, origin), url.fragment == nil else { return false }
        return url.path.range(of: "^/api/sessions/[0-9a-f-]{36}/(?:files/[a-f0-9]{64}|workspace/download)$", options: .regularExpression) != nil ||
            url.path.range(of: "^/api/desktop-sessions/(?:claude|deepseek)/workspace/download$", options: .regularExpression) != nil
    }

    func start(_ url: URL, origin: String) {
        queue.async {
            guard !self.value.busy, Self.allows(url, origin: origin) else { return }
            self.discard(); self.epoch += 1; self.url = url; self.etag = nil; self.canContinue = true
            self.value = Snapshot(); self.value.origin = origin; self.value.phase = .connecting
            self.value.name = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name == "path" })?.value.map { Self.filename($0) } ?? "download"
            self.prepare(); self.fetch()
        }
    }
    func pause() {
        queue.async {
            guard [.connecting, .downloading].contains(self.value.phase) else { return }
            self.stopTask(); self.value.phase = .paused; self.value.speed = 0
            self.value.canResume = self.value.received == 0 || self.etag != nil && self.canContinue
            self.value.message = self.value.canResume ? "" : "当前服务器不支持续传，请重新下载"
            self.publish()
        }
    }
    func resume() {
        queue.async {
            guard [.paused, .failed].contains(self.value.phase), self.value.canResume else { return }
            self.value.phase = .connecting; self.value.message = ""; self.value.speed = 0
            self.rateTime = ProcessInfo.processInfo.systemUptime; self.rateBytes = self.value.received
            self.fetch()
        }
    }
    func restart() {
        queue.async {
            guard [.paused, .failed].contains(self.value.phase) else { return }
            self.stopTask(); self.discard(); self.value.received = 0; self.value.total = nil
            self.value.file = nil; self.value.speed = 0; self.value.message = ""; self.value.canResume = true
            self.value.phase = .connecting; self.etag = nil; self.canContinue = true
            self.prepare(); self.fetch()
        }
    }
    func cancel(origin: String? = nil) {
        queue.async {
            guard origin == nil || self.value.origin == origin else { return }
            self.stopTask(); self.discard(); self.value.phase = .cancelled; self.value.file = nil; self.value.speed = 0; self.publish()
        }
    }
    func markSaved() {
        queue.async {
            guard self.value.phase == .complete else { return }
            self.value.phase = .saved; self.discard(); self.value.file = nil; self.publish()
        }
    }
    private func prepare() {
        do {
            let folder = scratch.appendingPathComponent("bridge-download-" + UUID().uuidString, isDirectory: true)
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
            self.folder = folder
            let file = folder.appendingPathComponent("payload.part")
            guard FileManager.default.createFile(atPath: file.path, contents: nil, attributes: [.posixPermissions: 0o600]) else { throw TransferError(message: "无法保存下载文件，请检查剩余空间") }
            handle = try FileHandle(forWritingTo: file)
        } catch { fail("无法保存下载文件，请检查剩余空间", restart: true) }
    }
    private func discard() {
        try? handle?.close(); handle = nil
        if let folder { try? FileManager.default.removeItem(at: folder) }; folder = nil
    }
    private func stopTask() {
        epoch += 1; let previous = task; task = nil; previous?.cancel()
        clock?.cancel(); clock = nil
        try? handle?.synchronize()
    }
    private func publish() {
        let snapshot = value
        DispatchQueue.main.async { [weak self] in self?.onChange?(snapshot) }
    }
    private func fail(_ message: String, restart: Bool = false) {
        stopTask(); value.phase = .failed; value.message = message; value.speed = 0
        value.canResume = !restart && (value.received == 0 || etag != nil && canContinue)
        publish()
    }
    private func fetch() {
        guard value.phase == .connecting || value.phase == .downloading, let url, handle != nil else { return }
        let ticket = epoch, origin = value.origin
        publish()
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            self.credentials?(origin, url) { cookie in
                self.queue.async {
                    guard ticket == self.epoch, [.connecting, .downloading].contains(self.value.phase) else { return }
                    guard let cookie, !cookie.isEmpty else { self.fail("登录已失效，请重新连接电脑"); return }
                    self.request(url, cookie: cookie)
                }
            }
        }
    }
    private func request(_ url: URL, cookie: String) {
        var request = URLRequest(url: url); request.cachePolicy = .reloadIgnoringLocalCacheData
        request.setValue(cookie, forHTTPHeaderField: "Cookie"); request.setValue(value.origin, forHTTPHeaderField: "Origin")
        request.setValue("BridgeMobile/0.1-iOS", forHTTPHeaderField: "User-Agent")
        request.setValue("identity", forHTTPHeaderField: "Accept-Encoding")
        requestedEnd = min(Self.limit, value.received + Self.chunk) - 1
        request.setValue("bytes=\(value.received)-\(requestedEnd)", forHTTPHeaderField: "Range")
        if value.received > 0, let etag { request.setValue(etag, forHTTPHeaderField: "If-Range") }
        responseBytes = 0; expectedBytes = nil; accepted = false; wholeResponse = false; errorBody = nil
        task = session.dataTask(with: request); task?.resume()
        rateTime = ProcessInfo.processInfo.systemUptime; rateBytes = value.received
        if clock == nil {
            let timer = DispatchSource.makeTimerSource(queue: queue)
            timer.schedule(deadline: .now() + .milliseconds(500), repeating: .milliseconds(500))
            timer.setEventHandler { [weak self] in self?.rate() }; timer.resume(); clock = timer
        }
    }
    private func rate() {
        guard value.phase == .downloading else { return }
        let now = ProcessInfo.processInfo.systemUptime, elapsed = max(0.001, now - rateTime)
        let instant = Double(value.received - rateBytes) / elapsed
        value.speed = value.speed == 0 ? instant : 0.35 * instant + 0.65 * value.speed
        rateTime = now; rateBytes = value.received; publish()
    }
    static func filename(_ proposed: String) -> String {
        var name = ((proposed.replacingOccurrences(of: "\\", with: "/") as NSString).lastPathComponent)
            .components(separatedBy: .controlCharacters).joined().trimmingCharacters(in: .whitespacesAndNewlines)
        while name.utf8.count > 240 { name.removeLast() }
        return name.isEmpty || name == "." || name == ".." ? "download" : name
    }
    private func finish() {
        do {
            try handle?.synchronize(); try handle?.close(); handle = nil
            guard let folder else { throw TransferError(message: "无法保存下载文件，请检查剩余空间") }
            let file = folder.appendingPathComponent(value.name)
            let partial = folder.appendingPathComponent("payload.part")
            if file != partial { try FileManager.default.moveItem(at: partial, to: file) }
            stopTask(); value.phase = .complete; value.file = file; value.speed = 0; value.message = ""; publish()
        } catch { fail("无法保存下载文件，请检查剩余空间", restart: true) }
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        if task === self.task { fail("下载地址发生跳转，已取消。请重新打开电脑附件。", restart: true) }
        completionHandler(nil)
    }
    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive response: URLResponse, completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        guard dataTask === task, let response = response as? HTTPURLResponse, let responseURL = response.url,
              Self.allows(responseURL, origin: value.origin) else { completionHandler(.cancel); return }
        do {
            if [401, 403].contains(response.statusCode) { throw TransferError(message: "登录已失效，请重新连接电脑") }
            if response.statusCode == 409 {
                guard response.expectedContentLength <= 16384 else { throw TransferError(message: "下载失败，请检查网络后重试") }
                errorBody = Data(); completionHandler(.allow); return
            }
            let encoding = response.value(forHTTPHeaderField: "Content-Encoding")?.lowercased() ?? "identity"
            guard encoding == "identity" else { throw TransferError(message: "下载响应格式不正确，请重新下载", restart: true) }
            let tag = response.value(forHTTPHeaderField: "ETag").flatMap { $0.hasPrefix("\"") && $0.hasSuffix("\"") && $0.count > 2 ? $0 : nil }
            if value.received > 0, tag == nil || tag != etag { throw TransferError(message: "文件已变化，请重新下载", restart: true) }
            value.name = Self.filename(response.suggestedFilename ?? value.name)
            if response.statusCode == 416 {
                let expected = "bytes */\(value.received)"
                guard response.value(forHTTPHeaderField: "Content-Range") == expected,
                      value.total == nil || value.total == value.received else { throw TransferError(message: "文件已变化，请重新下载", restart: true) }
                value.total = value.received; completionHandler(.cancel); finish(); return
            }
            if response.statusCode == 206 {
                let field = response.value(forHTTPHeaderField: "Content-Range") ?? ""
                let regex = try NSRegularExpression(pattern: "^bytes ([0-9]+)-([0-9]+)/([0-9]+|\\*)$")
                let source = field as NSString
                guard let match = regex.firstMatch(in: field, range: NSRange(location: 0, length: source.length)),
                      let start = Int64(source.substring(with: match.range(at: 1))),
                      let end = Int64(source.substring(with: match.range(at: 2))),
                      start == value.received, end >= start, end <= requestedEnd else { throw TransferError(message: "下载响应格式不正确，请重新下载", restart: true) }
                let total = Int64(source.substring(with: match.range(at: 3)))
                if let total, total <= end || total > Self.limit { throw TransferError(message: total > Self.limit ? "附件超过 50 MB 下载限制" : "下载响应格式不正确，请重新下载", restart: true) }
                if let prior = value.total, total != prior { throw TransferError(message: "文件已变化，请重新下载", restart: true) }
                expectedBytes = end - start + 1; value.total = total
                guard response.expectedContentLength < 0 || response.expectedContentLength == expectedBytes else { throw TransferError(message: "下载响应格式不正确，请重新下载", restart: true) }
                guard tag != nil else { throw TransferError(message: "网关不支持安全续传，请更新网关后重试", restart: true) }
            } else if response.statusCode == 200 && value.received == 0 {
                wholeResponse = true; expectedBytes = response.expectedContentLength >= 0 ? response.expectedContentLength : nil
                value.total = expectedBytes; canContinue = tag != nil
                if let total = value.total, total > Self.limit { throw TransferError(message: "附件超过 50 MB 下载限制", restart: true) }
            } else { throw TransferError(message: value.received > 0 ? "文件已变化或服务器不支持续传，请重新下载" : "下载失败，请检查网络后重试", restart: value.received > 0) }
            etag = tag
            accepted = true; value.phase = .downloading; publish(); completionHandler(.allow)
        } catch let error as TransferError { completionHandler(.cancel); fail(error.message, restart: error.restart) }
        catch { completionHandler(.cancel); fail("下载响应格式不正确，请重新下载", restart: true) }
    }
    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        guard dataTask === task else { return }
        if var body = errorBody {
            guard body.count + data.count <= 16384 else { fail("下载失败，请检查网络后重试"); return }
            body.append(data); errorBody = body; return
        }
        guard accepted else { return }
        let count = Int64(data.count)
        guard value.received + count <= Self.limit, expectedBytes == nil || responseBytes + count <= expectedBytes! else {
            fail(value.received + count > Self.limit ? "附件超过 50 MB 下载限制" : "下载响应格式不正确，请重新下载", restart: true); return
        }
        do { try handle?.write(contentsOf: data); value.received += count; responseBytes += count }
        catch { fail("无法保存下载文件，请检查剩余空间", restart: true) }
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        guard task === self.task else { return }
        self.task = nil
        if let body = errorBody {
            let payload = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any]
            if payload?["code"] as? String == "download_changed" { fail("文件已变化，请重新下载", restart: true) }
            else { fail("电脑暂时不可用，可继续下载") }
            return
        }
        guard error == nil, accepted, expectedBytes == nil || responseBytes == expectedBytes else { fail("下载中断，可继续下载"); return }
        if wholeResponse || value.total == value.received { finish(); return }
        guard value.received < Self.limit else { fail("附件超过 50 MB 下载限制", restart: true); return }
        fetch()
    }
}
