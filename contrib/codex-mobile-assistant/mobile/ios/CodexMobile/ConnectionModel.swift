import SwiftUI
import WebKit

@MainActor
final class ConnectionModel: NSObject, ObservableObject, WKNavigationDelegate, WKUIDelegate, WKHTTPCookieStoreObserver {
    enum Mode: String, CaseIterable {
        case temporary, fixed
        var title: String { self == .temporary ? "临时访问" : "固定域名" }
        var hint: String {
            self == .temporary
                ? "无需服务器和域名，粘贴电脑端生成的 HTTPS 临时链接；链接变化后在此更新。"
                : "使用自己部署的固定域名；电脑端需选择固定域名模式。"
        }
    }

    @Published var mode: Mode
    @Published var temporaryAddress: String
    @Published var fixedAddress: String
    @Published var autoConnect: Bool {
        didSet { defaults.set(autoConnect, forKey: "autoConnect") }
    }
    @Published var showSettings = true
    @Published var addressError: String?
    @Published var connectionError: String?
    @Published var currentURL: URL?
    @Published var pageTitle = "Codex 手机助手"
    @Published var chatPage = false
    @Published var desktopConnection = "idle"
    @Published var pendingHomeAction = ""
    @Published var fallbackHomeMenu = false
    @Published var systemNotificationsEnabled = false
    @Published var backgroundTaskNotifications = TaskMonitorController.shared.preferred {
        didSet {
            TaskMonitorController.shared.preferred = backgroundTaskNotifications
            syncTaskMonitor()
        }
    }
    @Published var backgroundTaskReminderStatus = "登录后开启后台提醒"
    @Published var connected = false
    @Published var loading = false
    @Published var clearing = false

    let webView: WKWebView
    private let defaults = UserDefaults.standard
    private var started = false
    private var openingTaskNotification = false
    private var monitorAuthenticated = false
    private var monitorSound = true
    private var monitorRevision = UUID()
    private var navigation: WKNavigation?
    private var titleObservation: NSKeyValueObservation?
    private var urlObservation: NSKeyValueObservation?
    private var lastPageURL: URL?
    private var needsWebContentRecovery = false
    private var lastAutomaticRecoveryAt: TimeInterval?


    override init() {
        let saved = UserDefaults.standard
        mode = Mode(rawValue: saved.string(forKey: "mode") ?? "") ?? .temporary
        temporaryAddress = saved.string(forKey: "temporaryAddress") ?? ""
        fixedAddress = saved.string(forKey: "fixedAddress") ?? ""
        autoConnect = saved.object(forKey: "autoConnect") as? Bool ?? true
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .default()
        configuration.ignoresViewportScaleLimits = false
        configuration.applicationNameForUserAgent = "CodexMobileNative/3"
        configuration.preferences.javaScriptCanOpenWindowsAutomatically = false
        webView = WKWebView(frame: .zero, configuration: configuration)
        super.init()
        TaskNotifications.shared.onChange = { [weak self] in
            self?.publishTaskNotifications()
            self?.syncTaskMonitor()
            self?.openPendingTaskNotification()
        }
        TaskMonitorController.shared.onStatus = { [weak self] in self?.backgroundTaskReminderStatus = $0 }
        webView.configuration.websiteDataStore.httpCookieStore.add(self)
        webView.configuration.userContentController.add(ChatChromeHandler(model: self), name: "assistantChrome")
        BackgroundDownloads.shared.onChange = { [weak self] in DispatchQueue.main.async { self?.publishDownloads() } }
        titleObservation = webView.observe(\.title, options: [.new]) { [weak self] _, _ in
            DispatchQueue.main.async {
                guard let self, let url = self.webView.url, self.sameOrigin(url) else { return }
                let title = self.webView.title ?? ""
                self.pageTitle = title.isEmpty ? "Codex 手机助手" : title
            }
        }
        urlObservation = webView.observe(\.url, options: [.new]) { [weak self] _, _ in
            DispatchQueue.main.async { self?.rememberCurrentPage() }
        }
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.allowsBackForwardNavigationGestures = false
        webView.isOpaque = false
        webView.backgroundColor = .white
        webView.scrollView.backgroundColor = .white
        // SwiftUI already lays the container out within the device safe area.
        webView.scrollView.contentInsetAdjustmentBehavior = .never
        lockPageZoom()
    }

    private func lockPageZoom() {
        webView.scrollView.pinchGestureRecognizer?.isEnabled = false
        webView.scrollView.minimumZoomScale = 1
        webView.scrollView.maximumZoomScale = 1
        webView.scrollView.setZoomScale(1, animated: false)
    }

    func receiveChatChrome(_ message: WKScriptMessage) {
        guard message.frameInfo.isMainFrame, let url = message.frameInfo.request.url,
              sameOrigin(url), let body = message.body as? [String: Any] else { return }
        if let chat = body["chat"] as? Bool { chatPage = chat }
        if let authenticated = body["authenticated"] as? Bool {
            monitorAuthenticated = authenticated
            monitorSound = body["sound"] as? Bool ?? true
            syncTaskMonitor()
        }
        if let status = body["connection"] as? String,
           ["idle", "connecting", "connected", "failed"].contains(status) {
            desktopConnection = status
        }
        if let action = body["action"] as? String {
            if action == "taskNotification" { receiveTaskNotification(body, source: url) }
            else if action == "notifications" { toggleTaskNotifications() }
            else if action == "address" { showSettings = true }
            else if ["reload", "clear"].contains(action) { pendingHomeAction = action }
        }
        if body["chat"] != nil { openPendingTaskNotification() }
    }


    func cookiesDidChange(in cookieStore: WKHTTPCookieStore) { syncTaskMonitor() }

    private func syncTaskMonitor() {
        let revision = UUID()
        monitorRevision = revision
        guard backgroundTaskNotifications, systemNotificationsEnabled, monitorAuthenticated,
              let origin = currentURL, !clearing else {
            TaskMonitorController.shared.stop(message: monitorAuthenticated ? "后台提醒已关闭" : "登录后开启后台提醒")
            return
        }
        webView.configuration.websiteDataStore.httpCookieStore.getAllCookies { [weak self] cookies in
            Task { @MainActor in
                guard let self, self.monitorRevision == revision else { return }
                TaskMonitorController.shared.configure(origin: origin, cookies: cookies, sound: self.monitorSound)
            }
        }
    }

    func refreshTaskNotifications() {
        Task { await TaskNotifications.shared.refresh() }
    }

    func appDidBecomeActive() {
        recoverWebContentIfNeeded()
        refreshTaskNotifications()
    }

    func appDidEnterBackground() {
        rememberCurrentPage()
        lastAutomaticRecoveryAt = nil
    }

    private func rememberCurrentPage() {
        guard connected, !loading, !clearing, !needsWebContentRecovery,
              let url = webView.url, sameOrigin(url) else { return }
        lastPageURL = url
    }

    func recoverWebContentIfNeeded() {
        guard needsWebContentRecovery, !showSettings, !clearing, currentURL != nil,
              UIApplication.shared.applicationState == .active else { return }
        let now = ProcessInfo.processInfo.systemUptime
        // Keep a repeatedly crashing page from entering an automatic reload loop.
        if let last = lastAutomaticRecoveryAt, now - last < 30 {
            desktopConnection = "failed"
            connectionError = "页面连续被系统关闭，自动恢复未成功，请稍后重试。"
            return
        }
        lastAutomaticRecoveryAt = now
        loadCurrentPage()
    }

    func toggleTaskNotifications() {
        Task {
            await TaskNotifications.shared.setEnabled(!systemNotificationsEnabled, openSettings: true)
        }
    }

    private func publishTaskNotifications() {
        systemNotificationsEnabled = TaskNotifications.shared.enabled
        guard let url = webView.url, sameOrigin(url) else { return }
        webView.callAsyncJavaScript("window.setAssistantTaskNotifications?.({enabled:enabled})",
            arguments: ["enabled": systemNotificationsEnabled], in: nil, in: .page, completionHandler: nil)
    }

    private func receiveTaskNotification(_ body: [String: Any], source: URL) {
        guard let requestID = body["requestId"] as? String, UUID(uuidString: requestID) != nil else { return }
        Task { [weak self] in
            var accepted = false
            if let self, self.sameOrigin(source), let origin = self.currentURL?.absoluteString,
               let id = body["id"] as? String, UUID(uuidString: id) != nil,
               let host = body["host"] as? String, !host.isEmpty, host.count <= 512,
               let title = body["title"] as? String,
               let message = body["message"] as? String, !message.isEmpty,
               let eventKey = body["eventKey"] as? String, !eventKey.isEmpty, eventKey.count <= 8192 {
                accepted = await TaskNotifications.shared.post(origin: origin, id: id, host: host,
                    title: title, message: message, eventKey: eventKey, sound: body["sound"] as? Bool ?? true)
            }
            guard let self, self.sameOrigin(source),
                  let page = self.webView.url, self.sameOrigin(page) else { return }
            self.webView.callAsyncJavaScript("window.assistantTaskNotificationResult?.(requestId,accepted)",
                arguments: ["requestId": requestID, "accepted": accepted], in: nil, in: .page, completionHandler: nil)
        }
    }

    private func openPendingTaskNotification() {
        guard !openingTaskNotification, !clearing,
              let target = TaskNotifications.shared.pendingTarget else { return }
        guard let origin = normalizedURL(target.origin), sameOrigin(origin) else {
            showSettings = true
            addressError = "此通知来自其他连接，请切换到对应的连接地址后查看。"
            return
        }
        guard connected, !showSettings, let page = webView.url, sameOrigin(page) else { return }
        openingTaskNotification = true
        webView.callAsyncJavaScript("return await window.openAssistantTaskNotification?.(target) === true",
            arguments: ["target": ["id": target.id, "host": target.host]], in: nil, in: .page) { [weak self] result in
            Task { @MainActor in
                guard let self else { return }
                self.openingTaskNotification = false
                if TaskNotifications.shared.pendingTarget == target {
                    if case .success(let value) = result, value as? Bool == true {
                        TaskNotifications.shared.pendingTarget = nil
                    }
                } else {
                    self.openPendingTaskNotification()
                }
            }
        }
    }

    func openHomeMenu() {
        guard !showSettings, connectionError == nil, let url = webView.url, sameOrigin(url) else {
            fallbackHomeMenu = true
            return
        }
        webView.evaluateJavaScript("window.openAssistantMenu?.({native:true,notifications:\(systemNotificationsEnabled ? "true" : "false"),notificationKind:'system'}) === true") { [weak self] value, error in
            Task { @MainActor in
                if error != nil || (value as? Bool) != true { self?.fallbackHomeMenu = true }
            }
        }
    }

    var address: String {
        get { mode == .temporary ? temporaryAddress : fixedAddress }
        set {
            if mode == .temporary { temporaryAddress = newValue }
            else { fixedAddress = newValue }
            addressError = nil
        }
    }

    func startIfNeeded() {
        guard !started else { return }
        started = true
        if autoConnect && !address.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            connect()
        }
    }

    private func normalizedURL(_ text: String) -> URL? {
        var value = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.contains(where: { $0.isWhitespace }) else { return nil }
        if !value.contains("://") { value = "https://" + value }
        guard var parts = URLComponents(string: value),
              parts.scheme?.lowercased() == "https",
              let host = parts.host, !host.isEmpty,
              parts.user == nil, parts.password == nil,
              parts.query == nil, parts.fragment == nil,
              parts.path.isEmpty || parts.path == "/",
              parts.port == nil || (1...65535).contains(parts.port!) else { return nil }
        parts.scheme = "https"
        parts.host = host.lowercased()
        parts.path = "/"
        return parts.url
    }

    func connect() {
        guard let destination = normalizedURL(address) else {
            addressError = "请填写 HTTPS 入口地址，例如 https://your-domain.example，不含账号、密码或其他路径。"
            return
        }
        address = destination.absoluteString
        defaults.set(mode.rawValue, forKey: "mode")
        defaults.set(address, forKey: mode == .temporary ? "temporaryAddress" : "fixedAddress")
        showSettings = false
        if currentURL == destination && connectionError == nil { return }
        monitorAuthenticated = false
        monitorRevision = UUID()
        TaskMonitorController.shared.stop(message: "正在切换连接")
        webView.stopLoading()
        pageTitle = "Codex 手机助手"
        connected = false
        lastPageURL = nil
        needsWebContentRecovery = false
        currentURL = destination
        reload()
    }

    func connectionAddress(fromQRCode value: String) -> String? {
        let text = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard text.count <= 2048, text.lowercased().hasPrefix("https://"),
              let destination = normalizedURL(text) else { return nil }
        return destination.absoluteString
    }

    func reload() {
        lastAutomaticRecoveryAt = nil
        loadCurrentPage()
    }

    private func loadCurrentPage() {
        guard let origin = currentURL, !clearing else { return }
        let url = lastPageURL.flatMap { sameOrigin($0) ? $0 : nil } ?? origin
        needsWebContentRecovery = false
        desktopConnection = "connecting"
        connectionError = nil
        connected = false
        loading = true
        navigation = webView.load(URLRequest(url: url, timeoutInterval: 30))
    }

    private func sameOrigin(_ url: URL) -> Bool {
        guard let current = currentURL,
              url.scheme?.lowercased() == "https", url.user == nil, url.password == nil else { return false }
        return current.host?.lowercased() == url.host?.lowercased()
            && (current.port ?? 443) == (url.port ?? 443)
    }

    func clearLogin() {
        guard !clearing else { return }
        clearing = true
        monitorAuthenticated = false
        monitorRevision = UUID()
        TaskMonitorController.shared.stop(message: "登录数据已清除")
        desktopConnection = "idle"
        loading = false
        webView.stopLoading()
        connected = false
        currentURL = nil
        lastPageURL = nil
        needsWebContentRecovery = false
        lastAutomaticRecoveryAt = nil
        pageTitle = "Codex 手机助手"
        navigation = webView.loadHTMLString("", baseURL: nil)
        let store = webView.configuration.websiteDataStore
        store.removeData(ofTypes: WKWebsiteDataStore.allWebsiteDataTypes(),
                         modifiedSince: .distantPast) { [weak self] in
            DispatchQueue.main.async {
                self?.clearing = false
                self?.connectionError = nil
                self?.showSettings = true
            }
        }
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { decisionHandler(.cancel); return }
        if url.absoluteString == "about:blank" && currentURL == nil {
            decisionHandler(.allow)
            return
        }
        guard action.targetFrame?.isMainFrame != false else {
            decisionHandler(.allow)
            return
        }
        if sameOrigin(url) {
            if url.path == "/__assistant_downloads" {
                let args = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? []
                let operation = args.first(where: { $0.name == "action" })?.value ?? "list"
                let id = args.first(where: { $0.name == "id" })?.value ?? ""
                if operation == "cancel" { BackgroundDownloads.shared.cancel(id, origin: url) }
                if operation == "pause" { BackgroundDownloads.shared.pause(id, origin: url) }
                if operation == "resume" || operation == "restart" {
                    webView.configuration.websiteDataStore.httpCookieStore.getAllCookies { cookies in
                        DispatchQueue.main.async {
                            BackgroundDownloads.shared.resume(id, origin: url, cookies: cookies, restart: operation == "restart")
                        }
                    }
                }
                if operation == "save" { exportDownload(id) }
                if operation == "delete" { BackgroundDownloads.shared.deleteRecord(id, origin: url) }
                publishDownloads(); decisionHandler(.cancel); return
            }
            if openConversationFile(url) { decisionHandler(.cancel); return }
            // Only the file list and preview download controls use this route.
            let fileRoute = url.path.range(of: #"^/api/sessions/[0-9a-f-]{36}/project-files/download$"#,
                                           options: .regularExpression) != nil
            if action.shouldPerformDownload || fileRoute { startBackgroundDownload(url); decisionHandler(.cancel); return }
            if action.targetFrame == nil {
                navigation = webView.load(action.request)
                decisionHandler(.cancel)
            } else { decisionHandler(.allow) }
            return
        }
        if action.navigationType == .linkActivated,
           ["https", "http", "mailto", "tel"].contains(url.scheme?.lowercased() ?? "") {
            UIApplication.shared.open(url)
        } else if currentURL != nil {
            connected = false
            desktopConnection = "failed"
            connectionError = "入口跳转到了其他地址，请在连接设置中核对最终的 HTTPS 地址。"
            loading = false
        }
        decisionHandler(.cancel)
    }

    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        if let url = response.response.url, openConversationFile(url) {
            decisionHandler(.cancel); return
        }
        if response.isForMainFrame, let http = response.response as? HTTPURLResponse,
           http.statusCode >= 400 {
            connected = false
            desktopConnection = "failed"
            connectionError = "入口返回 HTTP \(http.statusCode)，请确认助手和服务器连接正常后重试。"
            loading = false
            decisionHandler(.cancel)
        } else if let url = response.response.url, sameOrigin(url),
                  let http = response.response as? HTTPURLResponse,
                  http.value(forHTTPHeaderField: "Content-Disposition")?.lowercased().hasPrefix("attachment") == true {
            startBackgroundDownload(url)
            decisionHandler(.cancel)
        } else { decisionHandler(.allow) }
    }

    private func openConversationFile(_ url: URL) -> Bool {
        guard sameOrigin(url),
              url.path.range(of: #"^/api/sessions/[0-9a-f-]{36}/files/[a-f0-9]{64}$"#,
                             options: .regularExpression) != nil else { return false }
        guard let data = try? JSONSerialization.data(withJSONObject: [url.absoluteString]),
              let json = String(data: data, encoding: .utf8) else { return true }
        webView.evaluateJavaScript("typeof window.openConversationFile==='function' && window.openConversationFile((" + json + ")[0])") { [weak self] result, error in
            guard error != nil || (result as? Bool) != true else { return }
            let alert = UIAlertController(title: "无法打开文件列表", message: "请更新并重新启动电脑端助手，再重新加载页面。文件尚未下载。", preferredStyle: .alert)
            alert.addAction(UIAlertAction(title: "好", style: .default))
            self?.present(alert, fallback: {})
        }
        return true
    }

    private func startBackgroundDownload(_ url: URL) {
        guard sameOrigin(url) else { return }
        webView.configuration.websiteDataStore.httpCookieStore.getAllCookies { cookies in
            DispatchQueue.main.async {
                guard self.sameOrigin(url) else { return }
                if let ready = BackgroundDownloads.shared.start(url, cookies: cookies) { self.exportDownload(ready) }
            }
        }
    }
    private func publishDownloads() {
        guard let url = webView.url, sameOrigin(url),
              let data = try? JSONSerialization.data(withJSONObject: BackgroundDownloads.shared.rows(origin: url)),
              let json = String(data: data, encoding: .utf8) else { return }
        webView.evaluateJavaScript("window.assistantDownloads?.(" + json + ")", completionHandler: nil)
    }
    private func exportDownload(_ id: String) {
        guard let origin = currentURL, let file = BackgroundDownloads.shared.file(id, origin: origin),
              let scene = UIApplication.shared.connectedScenes.first(where: { $0.activationState == .foregroundActive }) as? UIWindowScene,
              var controller = scene.windows.first(where: { $0.isKeyWindow })?.rootViewController else { return }
        while let presented = controller.presentedViewController { controller = presented }
        guard !(controller is UIActivityViewController) else { return }
        let activity = UIActivityViewController(activityItems: [file], applicationActivities: nil)
        activity.completionWithItemsHandler = { [weak self] _, _, _, _ in
            DispatchQueue.main.async { self?.publishDownloads() }
        }
        if let popover = activity.popoverPresentationController {
            popover.sourceView = controller.view
            popover.sourceRect = CGRect(x: controller.view.bounds.midX, y: controller.view.bounds.midY, width: 1, height: 1)
            popover.permittedArrowDirections = []
        }
        controller.present(activity, animated: true)
    }

    func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
        self.navigation = navigation
        chatPage = false
        desktopConnection = currentURL == nil ? "idle" : "connecting"
        if currentURL != nil { loading = true }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        guard let navigation, navigation === self.navigation, !clearing else { return }
        lockPageZoom()
        connected = connectionError == nil && webView.url.map({ sameOrigin($0) }) == true
        loading = false
        if connected {
            rememberCurrentPage()
            publishTaskNotifications()
            Task { await TaskNotifications.shared.prepare() }
            openPendingTaskNotification()
            webView.evaluateJavaScript("if(typeof notifyNativeChatHeader==='function')notifyNativeChatHeader(!document.getElementById('app').hidden&&document.getElementById('app').classList.contains('chat-open'))", completionHandler: nil)
        }
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!,
                 withError error: Error) {
        showNetworkError(error, navigation: navigation)
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        showNetworkError(error, navigation: navigation)
    }

    private func showNetworkError(_ error: Error, navigation failed: WKNavigation?) {
        guard currentURL != nil, let failed, failed === navigation, !clearing else { return }
        let code = (error as NSError).code
        guard code != NSURLErrorCancelled else { return }
        loading = false
        desktopConnection = "failed"
        if [-1200, -1201, -1202, -1203, -1204].contains(code) {
            connected = false
            connectionError = "HTTPS 证书无法验证，请检查手机时间或服务器证书后重试。"
        } else {
            connected = false
            connectionError = "请检查手机网络，并确认电脑、Codex 和助手正在运行。临时链接失效时，请更新连接地址。"
        }
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        guard currentURL != nil, !clearing else { return }
        rememberCurrentPage()
        navigation = nil
        needsWebContentRecovery = true
        loading = false
        desktopConnection = "connecting"
        connected = false
        connectionError = nil
        recoverWebContentIfNeeded()
    }

    private func present(_ alert: UIAlertController, fallback: () -> Void) {
        guard let scene = UIApplication.shared.connectedScenes.first(where: {
            $0.activationState == .foregroundActive
        }) as? UIWindowScene,
        let root = scene.windows.first(where: { $0.isKeyWindow })?.rootViewController else {
            fallback()
            return
        }
        var presenter = root
        while let presented = presenter.presentedViewController { presenter = presented }
        guard !(presenter is UIAlertController) else { fallback(); return }
        presenter.present(alert, animated: true)
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let alert = UIAlertController(title: "网页提示", message: message, preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: "好", style: .default) { _ in completionHandler() })
        present(alert, fallback: completionHandler)
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let alert = UIAlertController(title: "网页确认", message: message, preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: "取消", style: .cancel) { _ in completionHandler(false) })
        alert.addAction(UIAlertAction(title: "继续", style: .default) { _ in completionHandler(true) })
        present(alert) { completionHandler(false) }
    }

    func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String,
                 defaultText: String?, initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping (String?) -> Void) {
        let alert = UIAlertController(title: "网页输入", message: prompt, preferredStyle: .alert)
        alert.addTextField { $0.text = defaultText }
        alert.addAction(UIAlertAction(title: "取消", style: .cancel) { _ in completionHandler(nil) })
        alert.addAction(UIAlertAction(title: "确定", style: .default) { _ in
            completionHandler(alert.textFields?.first?.text)
        })
        present(alert) { completionHandler(nil) }
    }
}


// Stream into App storage once; leaving the App pauses until an explicit resume.
final class BackgroundDownloads: NSObject, URLSessionDownloadDelegate, URLSessionDataDelegate {
    static let shared = BackgroundDownloads()
    static let identifier = "io.github.abyxs.codexmobile.downloads"
    var onChange: (() -> Void)?
    var completion: (() -> Void)?
    private var records: [[String: Any]] = UserDefaults.standard.array(forKey: "backgroundDownloads") as? [[String: Any]] ?? []
    private final class DirectDownload {
        let task: URLSessionDataTask
        var output: FileHandle?
        var folder: URL?
        var relativePath: String?
        var name: String
        var received: Int64 = 0
        var expected: Int64 = -1
        var requestedOffset: Int64 = 0
        init(task: URLSessionDataTask, name: String) { self.task = task; self.name = name }
    }
    private var directDownloads: [String: DirectDownload] = [:]
    private lazy var directSession: URLSession = {
        let config = URLSessionConfiguration.default
        config.httpCookieStorage = .shared
        config.urlCache = nil
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        config.timeoutIntervalForRequest = 60
        return URLSession(configuration: config, delegate: self, delegateQueue: .main)
    }()
    private lazy var session: URLSession = {
        let config = URLSessionConfiguration.background(withIdentifier: Self.identifier)
        config.isDiscretionary = false
        config.sessionSendsLaunchEvents = true
        config.httpCookieStorage = .shared
        return URLSession(configuration: config, delegate: self, delegateQueue: .main)
    }()
    func reconnect() {
        session.getAllTasks { tasks in
            DispatchQueue.main.async {
                for i in self.records.indices where self.records[i]["state"] as? String == "running" {
                    if let id = self.records[i]["id"] as? String, self.directDownloads[id] == nil {
                        self.records[i]["state"] = "paused"
                        self.records[i]["message"] = "下载已暂停，点击继续下载"
                    }
                }
                // Retire old system downloads without automatically starting a second transfer.
                tasks.forEach { $0.cancel() }
                self.persist()
            }
        }
    }
    private func origin(_ url: URL) -> String { "https://" + (url.host ?? "").lowercased() + ":" + String(url.port ?? 443) }
    private func persist() { UserDefaults.standard.set(records, forKey: "backgroundDownloads"); onChange?() }
    func rows(origin url: URL) -> [[String: Any]] {
        records.filter { $0["origin"] as? String == origin(url) }.map { row in
            let state = row["state"] as? String ?? "failed"
            let message: String
            if state == "ready" || state == "saved" {
                message = "已下载到 App，可选择其他应用或“存储到文件”。"
            } else {
                message = row["message"] as? String == "保存失败，请检查存储空间后重试" ? "上次保存失败，旧版未记录具体原因；请重新下载" : row["message"] as? String ?? ""
            }
            return ["id": row["id"] ?? "", "name": row["name"] ?? "文件", "sourceURL": row["sourceURL"] ?? "", "createdAt": row["createdAt"] ?? 0, "state": state, "message": message, "percent": row["percent"] ?? -1, "restartRequired": row["restartRequired"] ?? false]
        }
    }
    private func setCookies(_ cookies: [HTTPCookie], for url: URL) {
        guard let host = url.host else { return }
        for cookie in cookies {
            let domain = cookie.domain.trimmingCharacters(in: CharacterSet(charactersIn: "."))
            if host == domain || host.hasSuffix("." + domain) { HTTPCookieStorage.shared.setCookie(cookie) }
        }
    }
    private func sourceKey(_ url: URL) -> String {
        var parts = URLComponents(url: url, resolvingAgainstBaseURL: false)
        parts?.fragment = nil
        let items = parts?.queryItems?.filter { !($0.name == "host" && $0.value == "local") }.sorted { ($0.name, $0.value ?? "") < ($1.name, $1.value ?? "") }
        parts?.queryItems = items
        return parts?.string ?? url.absoluteString
    }
    func start(_ url: URL, cookies: [HTTPCookie]) -> String? {
        guard url.scheme == "https", url.host != nil, url.user == nil else { return nil }
        setCookies(cookies, for: url)
        if let row = records.reversed().first(where: {
            guard $0["origin"] as? String == origin(url), let source = $0["sourceURL"] as? String, let savedURL = URL(string: source) else { return false }
            return sourceKey(savedURL) == sourceKey(url)
        }), let id = row["id"] as? String {
            if ["ready", "saved"].contains(row["state"] as? String ?? ""), file(id, origin: url) != nil { persist(); return id }
            if row["state"] as? String != "running" { resume(id, origin: url, cookies: cookies) }
            else { persist() }
            return nil
        }
        let path = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name == "path" })?.value
        let name = path.map { ($0 as NSString).lastPathComponent } ?? "下载文件"
        let id = UUID().uuidString
        records.append(["id":id, "origin":origin(url), "name":name, "sourceURL":url.absoluteString, "createdAt":Date().timeIntervalSince1970 * 1000, "state":"paused", "message":"准备下载", "percent":-1])
        resume(id, origin: url, cookies: cookies)
        return nil
    }
    func pauseAll() {
        for row in records where row["state"] as? String == "running" {
            if let id = row["id"] as? String, let source = row["sourceURL"] as? String, let url = URL(string: source) { pause(id, origin: url) }
        }
    }
    func pause(_ id: String, origin url: URL) {
        guard let i = records.firstIndex(where: { $0["id"] as? String == id && $0["origin"] as? String == origin(url) }), records[i]["state"] as? String == "running" else { return }
        records[i]["state"] = "paused"; records[i]["message"] = "已暂停，点击继续下载"
        if let transfer = directDownloads[id] {
            try? transfer.output?.synchronize()
            transfer.task.cancel(); cleanDirectDownload(id, removeFile: false)
        } else { session.getAllTasks { $0.filter { $0.taskDescription == id }.forEach { $0.cancel() } } }
        persist()
    }
    func resume(_ id: String, origin url: URL, cookies: [HTTPCookie], restart: Bool = false) {
        guard let i = records.firstIndex(where: { $0["id"] as? String == id && $0["origin"] as? String == origin(url) }),
              records[i]["state"] as? String != "running", let source = records[i]["sourceURL"] as? String,
              let sourceURL = URL(string: source), origin(sourceURL) == origin(url) else { return }
        if ["ready", "saved"].contains(records[i]["state"] as? String ?? ""), file(id, origin: url) != nil { persist(); return }
        if records[i]["restartRequired"] as? Bool == true && !restart { persist(); return }
        if restart {
            if let path = file(id, origin: url) { try? FileManager.default.removeItem(at: path) }
            for key in ["file", "expected", "etag", "restartRequired"] { records[i].removeValue(forKey: key) }
            records[i]["percent"] = -1
        }
        setCookies(cookies, for: sourceURL)
        guard UIApplication.shared.applicationState != .background else {
            records[i]["state"] = "paused"; records[i]["message"] = "已暂停，回到 App 后点击继续下载"; persist(); return
        }
        records[i]["state"] = "running"
        startDirectDownload(id, url: sourceURL)
    }
    func cancel(_ id: String, origin url: URL) {
        guard let i = records.firstIndex(where: { $0["id"] as? String == id && $0["origin"] as? String == origin(url) }), records[i]["state"] as? String == "running" else { return }
        if let transfer = directDownloads[id] { transfer.task.cancel(); cleanDirectDownload(id, removeFile: true) }
        records[i]["state"] = "failed"; records[i]["message"] = "已取消下载"; persist()
        session.getAllTasks { tasks in tasks.filter { $0.taskDescription == id }.forEach { $0.cancel() } }
    }
    func deleteRecord(_ id: String, origin url: URL) {
        guard let i = records.firstIndex(where: { $0["id"] as? String == id && $0["origin"] as? String == origin(url) }), records[i]["state"] as? String != "running" else { return }
        if !["ready", "saved"].contains(records[i]["state"] as? String ?? ""), let path = file(id, origin: url) { try? FileManager.default.removeItem(at: path) }
        records.remove(at: i)
        persist()
    }
    func file(_ id: String, origin url: URL) -> URL? {
        guard let row = records.first(where: { $0["id"] as? String == id && $0["origin"] as? String == origin(url) }), let name = row["file"] as? String else { return nil }
        let base = row["storage"] as? String == "internal" ? directory() : FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0].appendingPathComponent("Downloads", isDirectory: true)
        let target = base.appendingPathComponent(name)
        return FileManager.default.fileExists(atPath: target.path) ? target : nil
    }
    private func directory() -> URL { FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("Downloads", isDirectory: true) }
    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didWriteData bytesWritten: Int64, totalBytesWritten: Int64, totalBytesExpectedToWrite: Int64) {
        guard let i = records.firstIndex(where: { $0["id"] as? String == downloadTask.taskDescription }), records[i]["state"] as? String == "running", records[i]["transport"] as? String != "direct" else { return }
        records[i]["percent"] = totalBytesExpectedToWrite > 0 ? Int(Double(totalBytesWritten) / Double(totalBytesExpectedToWrite) * 100) : -1
        records[i]["message"] = "正在下载 · " + ByteCountFormatter.string(fromByteCount: totalBytesWritten, countStyle: .file)
        onChange?()
    }
    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didFinishDownloadingTo location: URL) {
        guard let i = records.firstIndex(where: { $0["id"] as? String == downloadTask.taskDescription }), records[i]["state"] as? String == "running", records[i]["transport"] as? String != "direct" else { return }
        guard let response = downloadTask.response as? HTTPURLResponse, response.statusCode == 200,
              let finalURL = response.url, origin(finalURL) == (records[i]["origin"] as? String) else {
            records[i]["state"] = "failed"; records[i]["message"] = "下载失败，请检查登录状态或重新下载"; persist(); return
        }
        var stage = "创建下载目录"
        var createdFolder: URL?
        do {
            let manager = FileManager.default
            // Each transfer gets its own folder, avoiding collisions and oversized
            // filenames caused by prefixing a UUID to the server's filename.
            let folderName = UUID().uuidString
            let folder = directory().appendingPathComponent(folderName, isDirectory: true)
            createdFolder = folder
            try manager.createDirectory(at: folder, withIntermediateDirectories: true)
            let suggested = (response.suggestedFilename ?? (records[i]["name"] as? String) ?? "文件") as NSString
            let rawName = suggested.lastPathComponent
            let clean = rawName.isEmpty || rawName == "." || rawName == ".." ? "下载文件" : rawName
            let target = folder.appendingPathComponent(clean, isDirectory: false)
            stage = "创建 App 内部文件"
            // Read the temporary download during this callback; create a new local
            // file without moving or copying the system file's protected metadata.
            try Data().write(to: target)
            do {
                stage = "读取系统下载临时文件"
                let source = try FileHandle(forReadingFrom: location)
                defer { try? source.close() }
                stage = "打开 App 内部文件"
                let output = try FileHandle(forWritingTo: target)
                defer { try? output.close() }
                while true {
                    stage = "读取系统下载临时文件"
                    guard let chunk = try source.read(upToCount: 256 * 1024), !chunk.isEmpty else { break }
                    stage = "写入 App 内部文件"
                    try output.write(contentsOf: chunk)
                }
                stage = "完成 App 内部文件写入"
                try output.synchronize()
            } catch {
                try? manager.removeItem(at: target)
                throw error
            }
            records[i]["name"] = clean; records[i]["file"] = folderName + "/" + clean
            records[i]["storage"] = "internal"
            records[i]["state"] = "ready"; records[i]["percent"] = 100
            records[i]["message"] = "已下载到 App，可选择其他应用或“存储到文件”。"
        } catch {
            if let folder = createdFolder { try? FileManager.default.removeItem(at: folder) }
            if stage == "读取系统下载临时文件", isPermissionError(error) {
                records[i]["state"] = "paused"
                records[i]["message"] = "旧版系统下载无法读取，请点击继续下载改用 App 内存储"
                persist()
                return
            }
            records[i]["state"] = "failed"
            records[i]["message"] = saveFailure(error, stage: stage)
        }
        persist()
    }
    private func isPermissionError(_ error: Error) -> Bool {
        var current: NSError? = error as NSError
        for _ in 0..<5 {
            guard let value = current else { break }
            if (value.domain == NSCocoaErrorDomain && [257, 513].contains(value.code)) ||
               (value.domain == NSPOSIXErrorDomain && [1, 13].contains(value.code)) { return true }
            current = value.userInfo[NSUnderlyingErrorKey] as? NSError
        }
        return false
    }
    private func startDirectDownload(_ id: String, url: URL) {
        guard let i = records.firstIndex(where: { $0["id"] as? String == id }),
              records[i]["state"] as? String == "running", directDownloads[id] == nil, origin(url) == (records[i]["origin"] as? String) else { return }
        let savedFile = file(id, origin: url)
        let offset = savedFile.flatMap { try? $0.resourceValues(forKeys: [.fileSizeKey]).fileSize }.map { Int64($0) } ?? 0
        let etag = records[i]["etag"] as? String
        if offset > 0 && etag == nil {
            records[i]["state"] = "paused"; records[i]["restartRequired"] = true
            records[i]["message"] = "旧文件无法核对续传，请点击重新下载"; persist(); return
        }
        if offset > 0, let expected = records[i]["expected"] as? Int64, offset == expected {
            records[i]["state"] = "ready"; records[i]["percent"] = 100
            records[i]["message"] = "已下载到 App，可选择其他应用或“存储到文件”。"; persist(); return
        }
        var request = URLRequest(url: url)
        request.setValue("identity", forHTTPHeaderField: "Accept-Encoding")
        if offset > 0 {
            request.setValue("bytes=\(offset)-", forHTTPHeaderField: "Range")
            request.setValue(etag, forHTTPHeaderField: "If-Range")
        }
        let task = directSession.dataTask(with: request); task.taskDescription = id
        let transfer = DirectDownload(task: task, name: records[i]["name"] as? String ?? "下载文件")
        transfer.received = offset; transfer.requestedOffset = offset
        if offset > 0 { transfer.relativePath = records[i]["file"] as? String; transfer.folder = savedFile?.deletingLastPathComponent() }
        directDownloads[id] = transfer
        records[i]["transport"] = "direct"
        records[i]["message"] = offset > 0 ? "正在继续下载" : "正在下载到 App"
        persist(); task.resume()
    }
    private func cleanDirectDownload(_ id: String, removeFile: Bool) {
        guard let transfer = directDownloads.removeValue(forKey: id) else { return }
        try? transfer.output?.close()
        if removeFile, let folder = transfer.folder { try? FileManager.default.removeItem(at: folder) }
    }
    private func failDirectDownload(_ id: String, message: String) {
        guard let transfer = directDownloads[id] else { return }
        try? transfer.output?.synchronize()
        transfer.task.cancel(); cleanDirectDownload(id, removeFile: false)
        if let i = records.firstIndex(where: { $0["id"] as? String == id }), records[i]["state"] as? String == "running" {
            records[i]["state"] = "paused"; records[i]["message"] = message; persist()
        }
    }
    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive response: URLResponse,
                    completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        guard let id = dataTask.taskDescription, let transfer = directDownloads[id], transfer.task === dataTask,
              let i = records.firstIndex(where: { $0["id"] as? String == id }), records[i]["state"] as? String == "running" else {
            completionHandler(.cancel); return
        }
        guard let http = response as? HTTPURLResponse, [200, 206].contains(http.statusCode),
              let url = http.url, origin(url) == (records[i]["origin"] as? String) else {
            failDirectDownload(id, message: "下载失败，请检查登录状态和连接地址后重试")
            completionHandler(.cancel); return
        }
        var expected = response.expectedContentLength
        if transfer.requestedOffset > 0 || http.statusCode == 206 {
            let range = http.value(forHTTPHeaderField: "Content-Range") ?? ""
            let numbers = range.replacingOccurrences(of: "bytes ", with: "").split(whereSeparator: { $0 == "-" || $0 == "/" }).compactMap { Int64($0) }
            guard http.statusCode == 206, numbers.count == 3, numbers[0] == transfer.requestedOffset,
                  numbers[1] >= numbers[0], numbers[1] + 1 == numbers[2],
                  transfer.requestedOffset == 0 || http.value(forHTTPHeaderField: "ETag") == (records[i]["etag"] as? String) else {
                records[i]["restartRequired"] = true
                failDirectDownload(id, message: "文件已变化或入口不支持续传，请点击重新下载")
                completionHandler(.cancel); return
            }
            expected = numbers[2]
        }
        do {
            let rawName = ((response.suggestedFilename ?? transfer.name) as NSString).lastPathComponent
            let name = rawName.isEmpty || rawName == "." || rawName == ".." ? "下载文件" : rawName
            let target: URL
            if transfer.requestedOffset > 0, let relative = transfer.relativePath {
                target = directory().appendingPathComponent(relative)
            } else {
                let folderName = UUID().uuidString
                let folder = directory().appendingPathComponent(folderName, isDirectory: true)
                transfer.folder = folder
                try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true,
                    attributes: [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication])
                target = folder.appendingPathComponent(name, isDirectory: false)
                try Data().write(to: target, options: .completeFileProtectionUntilFirstUserAuthentication)
                transfer.relativePath = folderName + "/" + name
            }
            transfer.output = try FileHandle(forWritingTo: target)
            try transfer.output?.seek(toOffset: UInt64(transfer.received))
            transfer.name = name; transfer.expected = expected
            records[i]["name"] = name; records[i]["file"] = transfer.relativePath; records[i]["storage"] = "internal"
            records[i]["expected"] = expected
            records[i]["etag"] = http.value(forHTTPHeaderField: "ETag")
            records[i].removeValue(forKey: "restartRequired")
            persist()
            completionHandler(.allow)
        } catch {
            failDirectDownload(id, message: saveFailure(error, stage: "创建 App 内直接下载文件"))
            completionHandler(.cancel)
        }
    }
    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        guard let id = dataTask.taskDescription, let transfer = directDownloads[id], transfer.task === dataTask, let output = transfer.output,
              let i = records.firstIndex(where: { $0["id"] as? String == id }), records[i]["state"] as? String == "running" else { return }
        do {
            if transfer.expected >= 0 && transfer.received + Int64(data.count) > transfer.expected {
                records[i]["restartRequired"] = true
                failDirectDownload(id, message: "下载长度异常，请点击重新下载"); return
            }
            try output.write(contentsOf: data); transfer.received += Int64(data.count)
            records[i]["percent"] = transfer.expected > 0 ? min(99, Int(Double(transfer.received) / Double(transfer.expected) * 100)) : -1
            records[i]["message"] = "正在下载 · " + ByteCountFormatter.string(fromByteCount: transfer.received, countStyle: .file)
            onChange?()
        } catch { failDirectDownload(id, message: saveFailure(error, stage: "写入 App 内直接下载文件")) }
    }
    private func saveFailure(_ error: Error, stage: String) -> String {
        let value = error as NSError
        let underlying = value.userInfo[NSUnderlyingErrorKey] as? NSError
        let errors = [value] + (underlying.map { [$0] } ?? [])
        let detail: String
        if errors.contains(where: { ($0.domain == NSCocoaErrorDomain && $0.code == 640) || ($0.domain == NSPOSIXErrorDomain && $0.code == 28) }) {
            detail = "存储空间不足，请清理空间后重试"
        } else if errors.contains(where: { ($0.domain == NSCocoaErrorDomain && [257, 513].contains($0.code)) || ($0.domain == NSPOSIXErrorDomain && [1, 13].contains($0.code)) }) {
            detail = "系统拒绝读取临时文件或写入 App 目录，请反馈此错误码"
        } else if errors.contains(where: { ($0.domain == NSCocoaErrorDomain && [4, 260].contains($0.code)) || ($0.domain == NSPOSIXErrorDomain && $0.code == 2) }) {
            detail = "下载临时文件或目录已不可用，请重新下载"
        } else if errors.contains(where: { $0.domain == NSCocoaErrorDomain && $0.code == 516 }) {
            detail = "保存位置存在同名文件，请重新下载"
        } else {
            detail = "系统未能保存文件，请重试并反馈下方错误码"
        }
        let codes = errors.map { "\($0.domain):\($0.code)" }.joined(separator: " / ")
        return "\(stage)失败：\(detail)（\(codes)）"
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        guard let url = request.url, url.scheme == "https", let id = task.taskDescription,
              let row = records.first(where: { $0["id"] as? String == id }), origin(url) == row["origin"] as? String else {
            completionHandler(nil); return
        }
        completionHandler(request)
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        if task is URLSessionDataTask, let id = task.taskDescription {
            guard let transfer = directDownloads[id], transfer.task === task else { return }
            if let error {
                failDirectDownload(id, message: "下载已暂停，点击继续下载（\((error as NSError).domain):\((error as NSError).code)）"); return
            }
            guard let i = records.firstIndex(where: { $0["id"] as? String == id }), records[i]["state"] as? String == "running",
                  let output = transfer.output, let path = transfer.relativePath else {
                failDirectDownload(id, message: "未收到有效下载内容，请重新下载"); return
            }
            do {
                guard transfer.expected < 0 || transfer.received == transfer.expected else {
                    failDirectDownload(id, message: "下载未完成，点击继续下载"); return
                }
                try output.synchronize(); try output.close(); transfer.output = nil
                records[i]["name"] = transfer.name; records[i]["file"] = path; records[i]["storage"] = "internal"
                records[i]["state"] = "ready"; records[i]["percent"] = 100
                records[i]["message"] = "已下载到 App，可选择其他应用或“存储到文件”。"
                cleanDirectDownload(id, removeFile: false); persist()
            } catch { failDirectDownload(id, message: saveFailure(error, stage: "完成 App 内直接下载")) }
            return
        }
        guard let error, let i = records.firstIndex(where: { $0["id"] as? String == task.taskDescription }) else { return }
        guard records[i]["state"] as? String == "running", records[i]["transport"] as? String != "direct" else { return }
        records[i]["state"] = "failed"; records[i]["message"] = (error as NSError).code == NSURLErrorCancelled ? "已取消下载" : "下载中断，请检查网络后重新下载"; persist()
    }
    func urlSessionDidFinishEvents(forBackgroundURLSession session: URLSession) {
        persist(); let handler = completion; completion = nil; handler?()
    }
}

@MainActor
private final class ChatChromeHandler: NSObject, WKScriptMessageHandler {
    weak var model: ConnectionModel?
    init(model: ConnectionModel) { self.model = model; super.init() }
    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        model?.receiveChatChrome(message)
    }
}
