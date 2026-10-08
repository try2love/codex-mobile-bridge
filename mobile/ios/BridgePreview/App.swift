import UIKit
@preconcurrency import WebKit
import AVFoundation
import UserNotifications
import UniformTypeIdentifiers

@main
final class AppDelegate: UIResponder, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    var window: UIWindow?
    let controller = BridgeController()
    func application(_ application: UIApplication, didFinishLaunchingWithOptions options: [UIApplication.LaunchOptionsKey: Any]?) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        let window = UIWindow(frame: UIScreen.main.bounds); window.rootViewController = UINavigationController(rootViewController: controller); window.makeKeyAndVisible(); self.window = window
        if let url = options?[.url] as? URL { DispatchQueue.main.async { self.controller.openLink(url) } }
        return true
    }
    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        UserDefaults.standard.set(deviceToken.map { String(format: "%02x", $0) }.joined(), forKey: "apnsToken")
        controller.registerNativePush()
    }
    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: Error) { controller.info(MobileStrings.text("系统推送注册失败，请检查推送签名与网络。")) }
    func application(_ app: UIApplication, open url: URL, options: [UIApplication.OpenURLOptionsKey: Any] = [:]) -> Bool { controller.openLink(url); return true }
    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse, withCompletionHandler completion: @escaping () -> Void) {
        let data = response.notification.request.content.userInfo
        if let origin = data["origin"] as? String { controller.openNotification(origin, thread: data["thread"] as? String, host: data["host"] as? String ?? "local") }; completion()
    }
    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification, withCompletionHandler completion: @escaping (UNNotificationPresentationOptions) -> Void) { completion([.banner, .sound]) }
}

final class NoRedirect: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
}

// Foreground-only, cookie-free reachability check with a bounded response.
final class GatewayReachability: NSObject, URLSessionDataDelegate {
    private var session: URLSession?
    private var data = Data()
    private var completion: ((Bool) -> Void)?
    func start(_ origin: String, completion: @escaping (Bool) -> Void) {
        guard let url = URL(string: origin + "/api/auth") else { completion(false); return }
        self.completion = completion
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 4; config.timeoutIntervalForResource = 5
        config.httpShouldSetCookies = false; config.httpCookieStorage = nil; config.urlCredentialStorage = nil; config.urlCache = nil
        let session = URLSession(configuration: config, delegate: self, delegateQueue: nil); self.session = session
        var request = URLRequest(url: url); request.cachePolicy = .reloadIgnoringLocalCacheData
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        session.dataTask(with: request).resume()
    }
    func cancel() { completion = nil; session?.invalidateAndCancel(); session = nil }
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive response: URLResponse, completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        completionHandler((response as? HTTPURLResponse)?.statusCode == 200 && response.expectedContentLength <= 16384 ? .allow : .cancel)
    }
    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive bytes: Data) {
        guard data.count + bytes.count <= 16384 else { dataTask.cancel(); return }; data.append(bytes)
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        let value = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        let online = error == nil && (task.response as? HTTPURLResponse)?.statusCode == 200 && value?["authenticated"] is Bool && value?["passwordless"] is Bool && !(value?["instanceId"] as? String ?? "").isEmpty
        session.finishTasksAndInvalidate()
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }; self.completion?(online); self.completion = nil; self.session = nil
        }
    }
}

final class BridgeController: UIViewController, WKNavigationDelegate, WKUIDelegate, UIDocumentPickerDelegate {
    private var web: WKWebView?
    private var origin = ""
    private var clipboardToken = ""
    private var downloadState = DownloadManager.Snapshot()
    private let downloadPanel = DownloadPanel()
    private var exportPicker: UIDocumentPickerViewController?
    private var downloadBottom: NSLayoutConstraint?
    private lazy var downloads: DownloadManager = {
        let manager = DownloadManager()
        manager.onChange = { [weak self] state in
            guard let self else { return }; self.downloadState = state; self.downloadPanel.render(state)
            self.view.bringSubviewToFront(self.downloadPanel)
        }
        manager.credentials = { [weak self] address, target, done in
            guard let self, self.saved.contains(address), DownloadManager.allows(target, origin: address) else { done(nil); return }
            WKWebsiteDataStore.default().httpCookieStore.getAllCookies { [weak self] cookies in
                guard let self, self.saved.contains(address) else { done(nil); return }
                let matching = cookies.filter { cookie in
                    cookie.name == "codex_mobile_session" && cookie.path == "/" &&
                    cookie.domain.trimmingCharacters(in: CharacterSet(charactersIn: ".")) == target.host &&
                    (!cookie.isSecure || target.scheme == "https") && (cookie.expiresDate == nil || cookie.expiresDate! > Date())
                }
                done(matching.isEmpty ? nil : HTTPCookie.requestHeaderFields(with: matching)["Cookie"])
            }
        }
        return manager
    }()
    private var uploadPicker: UIDocumentPickerViewController?
    private var uploadCompletion: (([URL]?) -> Void)?
    private var uploadGeneration = 0
    private var timer: Timer?
    private var activityTimer: Timer?
    private var foregroundBaselines = Set<String>()
    private var loading = false
    private var generation = 0
    private let defaults = UserDefaults.standard
    private let page = UIStackView()
    private let subtitle = UILabel()
    private let gatewayMenu = UIButton(type: .system)
    private var gatewayMenuTop: NSLayoutConstraint?
    private var pageBottom: NSLayoutConstraint?
    private var keyboardFrame: CGRect?
    private weak var keyboardScreen: UIScreen?
    // Mobile-only presentation; ordinary browser pages keep their own scaling.
    private static let fixedViewport = """
    (() => {
      const viewport = document.querySelector('meta[name="viewport"]');
      if (viewport) viewport.content = 'width=device-width,initial-scale=1,minimum-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover';
    })();
    """
    private static let webAppearance = """
    (() => {
      if (document.documentElement.classList.contains('bridge-mobile')) return;
      const sheet = Array.from(document.styleSheets).find(s => s.href && new URL(s.href).origin === location.origin);
      const app = document.getElementById('app');
      if (!sheet || !app) return;
      // Reuse a same-origin sheet: the gateway intentionally disallows inline styles.
      for (const rule of [
        '.bridge-mobile .phone-header { min-height: 56px; padding: 4px 64px 4px 16px; }',
        '.bridge-mobile.bridge-authenticated .phone-header { display: none !important; }',
        '.bridge-mobile aside { padding-top: 0; }',
        '.bridge-mobile .list-heading { min-height: 56px; padding-right: 48px; }',
        '.bridge-mobile .list-heading button { height: 48px; display: inline-flex; align-items: center; justify-content: center; margin: 0; }',
        '.bridge-mobile .chat-head { min-height: 56px; padding: 4px 64px 4px 8px; gap: 6px; }',
        '.bridge-mobile .chat-head .appearance-button, .bridge-mobile .list-actions .appearance-button, .bridge-mobile .list-more { display: none !important; }',
        '.bridge-mobile #back { width: 40px; height: 48px; padding: 8px; margin-left: 0; flex: none; }',
        '.bridge-mobile .sidebar-foot:not(.client-functions) { order: 99; flex: none; min-height: 44px; padding: 2px 16px max(4px, env(safe-area-inset-bottom)); background: var(--page); }',
        '.bridge-mobile:not(.bridge-authenticated) .sidebar-foot { display: none; }',
        '.bridge-mobile body.chat-detail > .sidebar-foot { display: none; }',
        '.bridge-mobile body.chat-detail .composer { padding-bottom: max(8px, env(safe-area-inset-bottom)); }',
        '.bridge-mobile .sidebar-foot #logout { display: none; }',
        '.bridge-mobile .sidebar-foot:not(.client-functions) button, .bridge-mobile .sidebar-foot:not(.client-functions) a { min-height: 40px; display: inline-flex; align-items: center; text-decoration: none; }',
        '.bridge-mobile .sidebar-foot > span { display: none; }',
        '.bridge-mobile .composer { padding-bottom: 8px; }',
        '.bridge-mobile input:not([type=checkbox]):not([type=radio]), .bridge-mobile textarea { font-size: max(16px, 1em); }'
      ]) sheet.insertRule(rule, sheet.cssRules.length);
      document.documentElement.classList.add('bridge-mobile');
      const sync = () => document.documentElement.classList.toggle('bridge-authenticated', !app.hidden);
      sync();
      new MutationObserver(sync).observe(app, { attributes: true, attributeFilter: ['hidden'] });
      const account = document.getElementById('accounts-button');
      if (account) {
        const labelAccount = () => {
          if (account.getAttribute('data-i18n') === '账号与额度') {
            const label = document.documentElement.lang.startsWith('en') ? 'Official account' : '官方账号';
            if (account.textContent !== label) account.textContent = label;
          }
        };
        new MutationObserver(labelAccount).observe(account, { attributes: true, attributeFilter: ['data-i18n'], childList: true });
        labelAccount();
      }
      const footer = document.querySelector('.sidebar-foot');
      if (footer && !document.querySelector('.client-navigation')) {
        document.body.appendChild(footer);
        const home = document.createElement('a'); home.href = 'codexbridge://home'; home.className = 'plain bridge-home'; home.dataset.i18n = '返回电脑列表'; home.textContent = typeof BridgeI18n !== 'undefined' ? BridgeI18n.t('返回电脑列表') : '返回电脑列表';
        footer.appendChild(home);
      }
      const computer = document.getElementById('connected-computer');
      const name = document.getElementById('computer-name');
      if (computer && name) {
        const connectionName = window.prompt('codexbridge-computer:__BRIDGE_CLIPBOARD_TOKEN__', '');
        if (connectionName) {
          let device = false;
          try { device = localStorage.getItem('bridge-computer-name-mode') === 'device'; } catch {}
          computer.setAttribute('role', 'button'); computer.tabIndex = 0;
          const render = () => {
            const actual = computer.dataset.deviceName || location.host;
            name.textContent = device ? actual : connectionName;
            const text = device ? '点击显示连接名称' : '点击显示电脑真实名称';
            const hint = typeof BridgeI18n !== 'undefined' ? BridgeI18n.t(text) : text;
            computer.title = hint + ' · ' + (device ? connectionName : actual);
            computer.setAttribute('aria-label', name.textContent + ' · ' + hint);
          };
          const toggle = () => {
            device = !device;
            try { localStorage.setItem('bridge-computer-name-mode', device ? 'device' : 'connection'); } catch {}
            render();
          };
          computer.addEventListener('click', toggle);
          computer.addEventListener('keydown', event => {
            if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); toggle(); }
          });
          document.addEventListener('bridge-computer', render);
          document.addEventListener('bridge-language', render);
          render();
        }
      }
    })();
    """
    private static let webClipboard = """
    (() => {
      if (window !== window.top) return;
      const nativePrompt = window.prompt.bind(window);
      const command = 'codexbridge-copy:__BRIDGE_CLIPBOARD_TOKEN__';
      const syncLanguage = () => { if (typeof BridgeI18n !== 'undefined') nativePrompt('codexbridge-language:__BRIDGE_CLIPBOARD_TOKEN__', BridgeI18n.language()); };
      document.addEventListener('bridge-language', syncLanguage); syncLanguage();
      let clicked = false;
      document.addEventListener('click', event => {
        clicked = event.isTrusted && !!event.target.closest?.('.message-copy, .code-copy');
      }, true);
      const install = () => {
        if (!window.BridgeClipboard) return;
        window.BridgeClipboard.copy = async (getText, button) => {
          if (button.disabled || !clicked) return;
          clicked = false;
          const translate = text => typeof BridgeI18n === 'undefined' ? text : BridgeI18n.t(text);
          const label = button.textContent;
          button.disabled = true;
          button.textContent = translate('正在读取…');
          try {
            const text = await getText();
            if (typeof text !== 'string') throw new Error('无法读取复制内容');
            if (text.length > 262144) throw new Error('内容过长，请分段复制');
            if (nativePrompt(command, text) !== 'copied') throw new Error('复制失败，请重试');
            button.textContent = translate('已复制');
            setTimeout(() => { button.textContent = label; }, 1500);
          } catch (error) {
            button.textContent = label;
            document.dispatchEvent(new CustomEvent('bridge-message-error', { detail: error.message }));
          } finally {
            button.disabled = false;
          }
        };
      };
      install();
      document.addEventListener('DOMContentLoaded', install, { once: true });
    })();
    """
    private var computerStates: [String: UILabel] = [:]
    private var computerProbes: [GatewayReachability] = []
    private var computerTimer: Timer?
    private var computerRevision = 0
    private let redirect = NoRedirect()
    private lazy var session: URLSession = { let c = URLSessionConfiguration.ephemeral; c.timeoutIntervalForRequest = 12; c.httpShouldSetCookies = false; return URLSession(configuration: c, delegate: redirect, delegateQueue: nil) }()
    private var saved: [String] { defaults.stringArray(forKey: "origins") ?? [] }
    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .systemGroupedBackground
        view.tintColor = .label
        let appearance = UINavigationBarAppearance(); appearance.configureWithOpaqueBackground()
        appearance.backgroundColor = .systemGroupedBackground; appearance.shadowColor = .clear
        navigationController?.navigationBar.standardAppearance = appearance
        navigationController?.navigationBar.scrollEdgeAppearance = appearance
        navigationController?.navigationBar.prefersLargeTitles = true
        page.axis = .vertical; page.spacing = 0; page.translatesAutoresizingMaskIntoConstraints = false; view.addSubview(page)
        pageBottom = page.bottomAnchor.constraint(equalTo: view.bottomAnchor)
        NSLayoutConstraint.activate([page.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor), pageBottom!, page.leadingAnchor.constraint(equalTo: view.leadingAnchor), page.trailingAnchor.constraint(equalTo: view.trailingAnchor)])
        subtitle.font = .preferredFont(forTextStyle: .caption1); subtitle.textColor = .secondaryLabel; subtitle.textAlignment = .center; subtitle.numberOfLines = 2; page.addArrangedSubview(subtitle)
        var menuStyle = UIButton.Configuration.plain(); menuStyle.image = UIImage(systemName: "ellipsis")
        menuStyle.baseForegroundColor = .label; menuStyle.baseBackgroundColor = .secondarySystemBackground; menuStyle.cornerStyle = .capsule
        gatewayMenu.configuration = menuStyle; gatewayMenu.accessibilityLabel = MobileStrings.text("电脑与通知"); gatewayMenu.showsMenuAsPrimaryAction = true
        updateGatewayMenu()
        gatewayMenu.translatesAutoresizingMaskIntoConstraints = false; view.addSubview(gatewayMenu)
        NSLayoutConstraint.activate([gatewayMenu.trailingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.trailingAnchor, constant: -8), gatewayMenu.widthAnchor.constraint(equalToConstant: 48), gatewayMenu.heightAnchor.constraint(equalToConstant: 48)])
        downloadPanel.translatesAutoresizingMaskIntoConstraints = false; view.addSubview(downloadPanel)
        downloadPanel.onToggle = { [weak self] in
            guard let self else { return }
            if [.connecting, .downloading].contains(self.downloadState.phase) { self.downloads.pause() }
            else if self.downloadState.canResume { self.downloads.resume() } else { self.downloads.restart() }
        }
        downloadPanel.onCancel = { [weak self] in self?.downloads.cancel() }
        downloadPanel.onSave = { [weak self] in self?.saveDownload() }
        downloadBottom = downloadPanel.bottomAnchor.constraint(equalTo: page.bottomAnchor, constant: -16)
        let downloadWidth = downloadPanel.widthAnchor.constraint(equalToConstant: 360); downloadWidth.priority = .defaultHigh
        NSLayoutConstraint.activate([downloadBottom!, downloadWidth,
            downloadPanel.trailingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.trailingAnchor, constant: -12),
            downloadPanel.leadingAnchor.constraint(greaterThanOrEqualTo: view.safeAreaLayoutGuide.leadingAnchor, constant: 12)])
        downloadPanel.render(downloadState)
        if let active = defaults.string(forKey: "active"), saved.contains(active) { openSaved(active) } else { home() }
        NotificationCenter.default.addObserver(self, selector: #selector(active), name: UIApplication.didBecomeActiveNotification, object: nil)
        NotificationCenter.default.addObserver(self, selector: #selector(inactive), name: UIApplication.willResignActiveNotification, object: nil)
        NotificationCenter.default.addObserver(self, selector: #selector(keyboardChanged), name: UIResponder.keyboardWillChangeFrameNotification, object: nil)
        NotificationCenter.default.addObserver(self, selector: #selector(keyboardChanged), name: UIResponder.keyboardWillHideNotification, object: nil)
        active()
    }
    override func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews(); updatePageBottom()
    }
    override func viewSafeAreaInsetsDidChange() {
        super.viewSafeAreaInsetsDidChange(); updatePageBottom()
    }
    private func updatePageBottom() {
        guard let pageBottom else { return }
        var overlap: CGFloat = 0
        if let frame = keyboardFrame, let screen = keyboardScreen {
            let local = screen.coordinateSpace.convert(frame, to: view)
            let intersection = view.bounds.intersection(local)
            if !intersection.isNull, intersection.maxY >= view.bounds.maxY - 1 { overlap = intersection.height }
        }
        // Use actual occlusion, not keyboardLayoutGuide's stale post-dismissal height.
        pageBottom.constant = -overlap
        downloadBottom?.constant = -(overlap > 0 ? 12 : max(12, view.safeAreaInsets.bottom + 8))
        gatewayMenuTop?.constant = 4
    }
    @objc private func keyboardChanged(_ notification: Notification) {
        guard let screen = (notification.object as? UIScreen) ?? view.window?.screen,
              screen === view.window?.screen else { return }
        if notification.name == UIResponder.keyboardWillHideNotification { keyboardFrame = nil }
        else { keyboardFrame = notification.userInfo?[UIResponder.keyboardFrameEndUserInfoKey] as? CGRect }
        keyboardScreen = screen; updatePageBottom()
        let duration = notification.userInfo?[UIResponder.keyboardAnimationDurationUserInfoKey] as? Double ?? 0
        let curve = notification.userInfo?[UIResponder.keyboardAnimationCurveUserInfoKey] as? UInt ?? 0
        UIView.animate(withDuration: duration, delay: 0, options: [UIView.AnimationOptions(rawValue: curve << 16), .beginFromCurrentState]) { self.view.layoutIfNeeded() }
    }
    func info(_ text: String) { let a = UIAlertController(title: nil, message: MobileStrings.text(text), preferredStyle: .alert); a.addAction(UIAlertAction(title: MobileStrings.text("好"), style: .default)); present(a, animated: true) }
    private func label(_ text: String, size: CGFloat = 15, weight: UIFont.Weight = .regular, secondary: Bool = false) -> UILabel {
        let l = UILabel(); l.text = text; l.numberOfLines = 0
        l.font = UIFontMetrics.default.scaledFont(for: .systemFont(ofSize: size, weight: weight)); l.adjustsFontForContentSizeCategory = true
        l.textColor = secondary ? .secondaryLabel : .label; return l
    }
    private func button(_ title: String, symbol: String? = nil, primary: Bool = false, action: @escaping () -> Void) -> UIButton {
        let b = UIButton(type: .system); var c = primary ? UIButton.Configuration.filled() : .tinted()
        c.title = title; c.baseForegroundColor = primary ? .systemBackground : .label; c.baseBackgroundColor = primary ? .label : .secondarySystemFill
        c.cornerStyle = .fixed; c.background.cornerRadius = 16
        c.contentInsets = NSDirectionalEdgeInsets(top: 17, leading: 18, bottom: 17, trailing: 18)
        if let symbol { c.image = UIImage(systemName: symbol); c.imagePadding = 10 }
        b.configuration = c; b.addAction(UIAction { _ in action() }, for: .touchUpInside); return b
    }
    private func column(spacing: CGFloat = 12) -> UIStackView { let v = UIStackView(); v.axis = .vertical; v.spacing = spacing; return v }
    private func card(_ items: [UIView]) -> UIStackView {
        let v = column(spacing: 12); v.backgroundColor = .secondarySystemGroupedBackground; v.layer.cornerRadius = 22
        v.isLayoutMarginsRelativeArrangement = true; v.directionalLayoutMargins = NSDirectionalEdgeInsets(top: 22, leading: 22, bottom: 22, trailing: 22)
        items.forEach(v.addArrangedSubview); return v
    }
    private func scrollContent(in parent: UIView) -> UIStackView {
        let scroll = UIScrollView(); scroll.alwaysBounceVertical = true; scroll.keyboardDismissMode = .interactive; scroll.translatesAutoresizingMaskIntoConstraints = false
        parent.addSubview(scroll); NSLayoutConstraint.activate([scroll.topAnchor.constraint(equalTo: parent.topAnchor), scroll.bottomAnchor.constraint(equalTo: parent.bottomAnchor), scroll.leadingAnchor.constraint(equalTo: parent.leadingAnchor), scroll.trailingAnchor.constraint(equalTo: parent.trailingAnchor)])
        let content = column(spacing: 22); content.translatesAutoresizingMaskIntoConstraints = false; scroll.addSubview(content)
        NSLayoutConstraint.activate([content.topAnchor.constraint(equalTo: scroll.contentLayoutGuide.topAnchor, constant: 20), content.bottomAnchor.constraint(equalTo: scroll.contentLayoutGuide.bottomAnchor, constant: -28), content.leadingAnchor.constraint(equalTo: scroll.contentLayoutGuide.leadingAnchor, constant: 20), content.trailingAnchor.constraint(equalTo: scroll.contentLayoutGuide.trailingAnchor, constant: -20), content.widthAnchor.constraint(equalTo: scroll.frameLayoutGuide.widthAnchor, constant: -40)])
        return content
    }
    private func openAccounts() {
        guard let web, let current = web.url, GatewayURL.same(current, origin) else { return }
        web.evaluateJavaScript("(()=>{if(typeof window.BridgeNavigation?.accounts==='function')return window.BridgeNavigation.accounts();document.getElementById('accounts-button')?.click();return true;})()")
    }
    private func updateGatewayMenu() {
        gatewayMenu.accessibilityLabel = MobileStrings.text("电脑与通知")
        gatewayMenu.menu = UIMenu(children: [
            UIAction(title: MobileStrings.text("返回电脑列表"), image: UIImage(systemName: "desktopcomputer")) { [weak self] _ in self?.home() },
            UIAction(title: MobileStrings.text("账号与接入"), image: UIImage(systemName: "person.crop.circle")) { [weak self] _ in self?.openAccounts() },
            UIAction(title: MobileStrings.text("在灵动岛显示此聊天"), image: UIImage(systemName: "capsule")) { [weak self] _ in self?.startLiveActivity() },
            UIAction(title: MobileStrings.text("结束灵动岛显示"), image: UIImage(systemName: "xmark.circle")) { _ in if #available(iOS 16.2, *) { Task { await LiveActivityController.shared.stop() } } },
            UIAction(title: MobileStrings.text("通知收件箱"), image: UIImage(systemName: "bell")) { [weak self] _ in self?.inbox() },
            UIAction(title: MobileStrings.text("刷新页面"), image: UIImage(systemName: "arrow.clockwise")) { [weak self] _ in self?.refresh() },
            UIAction(title: MobileStrings.text("外观与显示"), image: UIImage(systemName: "paintpalette")) { [weak self] _ in self?.web?.evaluateJavaScript("document.querySelector('[data-open-appearance]')?.click()") },
            UIAction(title: MobileStrings.text("手机设置"), image: UIImage(systemName: "gearshape")) { [weak self] _ in self?.settings() }
        ])
    }
    private func navigation(home: Bool) {
        navigationController?.setNavigationBarHidden(!home, animated: false)
        gatewayMenu.isHidden = home
        title = ""; navigationItem.largeTitleDisplayMode = .never
        let logo = UIImageView(image: UIImage(named: "AppIcon60x60@2x.png")); logo.contentMode = .scaleAspectFit
        logo.widthAnchor.constraint(equalToConstant: 30).isActive = true; logo.heightAnchor.constraint(equalToConstant: 30).isActive = true; logo.layer.cornerRadius = 7; logo.clipsToBounds = true
        let brand = UIStackView(arrangedSubviews: [logo, label("Codex Bridge", size: 18, weight: .semibold)]); brand.axis = .horizontal; brand.spacing = 8; brand.alignment = .center
        navigationItem.leftBarButtonItem = UIBarButtonItem(customView: brand)
        let settings = UIBarButtonItem(image: UIImage(systemName: "gearshape"), style: .plain, target: self, action: #selector(self.settings)); settings.accessibilityLabel = MobileStrings.text("设置")
        let inbox = UIBarButtonItem(image: UIImage(systemName: "bell"), style: .plain, target: self, action: #selector(self.inbox)); inbox.accessibilityLabel = MobileStrings.text("通知")
        navigationItem.rightBarButtonItems = [settings, inbox]
    }
    private func clear() { finishUpload(nil); stopComputerChecks(); computerStates.removeAll(); gatewayMenuTop?.isActive = false; gatewayMenuTop = nil; generation += 1; web?.stopLoading(); web?.navigationDelegate = nil; web?.uiDelegate = nil; web = nil; for v in page.arrangedSubviews where v !== subtitle { page.removeArrangedSubview(v); v.removeFromSuperview() } }
    @objc func home() {
        clear(); navigation(home: true); subtitle.isHidden = true
        let container = UIView(); page.addArrangedSubview(container); let content = scrollContent(in: container)
        let visual = UIImageView(image: UIImage(systemName: "desktopcomputer")); visual.tintColor = .label; visual.contentMode = .scaleAspectFit; visual.heightAnchor.constraint(equalToConstant: 54).isActive = true
        let hero = column(spacing: 14); hero.addArrangedSubview(visual)
        let heading = label(MobileStrings.text("电脑上的工作，\n带在身边。"), size: 30, weight: .semibold); heading.textAlignment = .center; hero.addArrangedSubview(heading)
        let hint = label(MobileStrings.text("继续聊天、查看结果，让电脑替你运行。"), secondary: true); hint.textAlignment = .center; hero.addArrangedSubview(hint)
        hero.setCustomSpacing(24, after: visual); content.addArrangedSubview(hero)
        content.addArrangedSubview(card([button(MobileStrings.text("扫码连接电脑"), symbol: "qrcode.viewfinder", primary: true) { [weak self] in self?.scan() }, button(MobileStrings.text("输入网关地址"), symbol: "link") { [weak self] in self?.manualAddress() }]))
        content.addArrangedSubview(label(MobileStrings.text("你的电脑"), size: 19, weight: .semibold))
        if saved.isEmpty {
            content.addArrangedSubview(card([label(MobileStrings.text("还没有连接的电脑"), size: 17, weight: .medium), label(MobileStrings.text("在电脑网关中展开“扫码登录”，然后用上方按钮扫描。"), secondary: true)]))
        } else {
            for address in saved {
                let row = button(computerName(address) + "   ›") { [weak self] in self?.openSaved(address) }
                row.contentHorizontalAlignment = .leading
                row.configuration?.contentInsets = NSDirectionalEdgeInsets(top: 8, leading: 0, bottom: 8, trailing: 0)
                let status = label(MobileStrings.text("检查中…"), size: 12, secondary: true)
                status.setContentHuggingPriority(.required, for: .horizontal)
                status.setContentCompressionResistancePriority(.required, for: .horizontal)
                computerStates[address] = status
                let heading = UIStackView(arrangedSubviews: [status, row]); heading.axis = .horizontal; heading.alignment = .center; heading.spacing = 10
                let rename = button(MobileStrings.text("重命名"), symbol: "pencil") { [weak self] in self?.renameComputer(address) }
                let remove = button(MobileStrings.text("移除这台电脑"), symbol: "trash") { [weak self] in self?.confirmRemove(address) }
                remove.configuration?.baseForegroundColor = .systemRed
                let actions = UIStackView(arrangedSubviews: [rename, remove]); actions.axis = .horizontal; actions.distribution = .fillEqually; actions.spacing = 8
                content.addArrangedSubview(card([heading, label(address, size: 13, secondary: true), actions]))
            }
        }
        let foot = label(MobileStrings.text("外出使用 HTTPS 地址；局域网地址需要连接同一网络。"), size: 13, secondary: true); foot.textAlignment = .center; content.addArrangedSubview(foot)
        checkComputers()
    }
    private func computerName(_ address: String) -> String {
        defaults.string(forKey: "name:" + address) ?? URL(string: address)?.host ?? address
    }
    private func renameComputer(_ address: String) {
        let alert = UIAlertController(title: MobileStrings.text("重命名电脑"), message: address + "\n" + MobileStrings.text("留空恢复默认名称"), preferredStyle: .alert)
        alert.addTextField { field in field.text = self.computerName(address) }
        alert.addAction(UIAlertAction(title: MobileStrings.text("取消"), style: .cancel))
        alert.addAction(UIAlertAction(title: MobileStrings.text("保存"), style: .default) { [weak self, weak alert] _ in
            guard let self else { return }
            let name = (alert?.textFields?.first?.text ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            guard name.count <= 80 else { self.info(MobileStrings.text("名称最多 80 个字符")); return }
            if name.isEmpty { self.defaults.removeObject(forKey: "name:" + address) } else { self.defaults.set(name, forKey: "name:" + address) }
            self.home()
        })
        present(alert, animated: true)
    }
    private func stopComputerChecks() {
        computerRevision += 1; computerTimer?.invalidate(); computerTimer = nil
        computerProbes.forEach { $0.cancel() }; computerProbes.removeAll()
    }
    private func checkComputers() {
        stopComputerChecks()
        guard web == nil, UIApplication.shared.applicationState == .active, !computerStates.isEmpty else { return }
        let revision = computerRevision
        for (address, label) in computerStates {
            let probe = GatewayReachability(); computerProbes.append(probe)
            probe.start(address) { [weak self, weak label] online in
                guard let self, self.computerRevision == revision, self.web == nil else { return }
                label?.text = (online ? "● " : "○ ") + MobileStrings.text(online ? "在线" : "暂不可达")
                label?.textColor = online ? .systemGreen : .secondaryLabel
            }
        }
        computerTimer = Timer.scheduledTimer(withTimeInterval: 30, repeats: false) { [weak self] _ in self?.checkComputers() }
    }
    private func manualAddress() {
        let a = UIAlertController(title: MobileStrings.text("连接电脑"), message: MobileStrings.text("粘贴电脑网关提供的访问地址"), preferredStyle: .alert)
        a.addTextField { field in field.text = "https://"; field.keyboardType = .URL; field.autocapitalizationType = .none; field.autocorrectionType = .no; field.placeholder = "https://codex.try2love.com" }
        a.addAction(UIAlertAction(title: MobileStrings.text("取消"), style: .cancel)); a.addAction(UIAlertAction(title: MobileStrings.text("继续"), style: .default) { [weak self, weak a] _ in self?.choose(a?.textFields?.first?.text ?? "") }); present(a, animated: true)
    }
    func choose(_ value: String) {
        do { let url = try GatewayURL.connection(value); let address = try GatewayURL.origin(value)
            let alert = UIAlertController(title: MobileStrings.text("连接到这台电脑？"), message: address + MobileStrings.text("\n请确认这是你自己的网关。"), preferredStyle: .alert)
            alert.addAction(UIAlertAction(title: MobileStrings.text("取消"), style: .cancel)); alert.addAction(UIAlertAction(title: MobileStrings.text("连接"), style: .default) { _ in self.connect(url, address: address) }); present(alert, animated: true)
        } catch { info(error.localizedDescription) }
    }
    func openNotification(_ address: String, thread: String?, host: String) {
        guard saved.contains(address) else { info(MobileStrings.text("请先连接通知对应的电脑。")); return }
        if let thread, let target = try? GatewayURL.chat(address, thread: thread, host: host) { connect(target, address: address) } else { openSaved(address) }
    }
    func openSaved(_ address: String) { guard saved.contains(address), let url = URL(string: address + "/") else { info(MobileStrings.text("请先扫码保存通知对应的电脑。")); return }; connect(url, address: address) }
    private func connect(_ url: URL, address: String) {
        if origin != address, #available(iOS 16.2, *) { Task { await LiveActivityController.shared.stop() } }
        clear(); navigation(home: false); subtitle.isHidden = false; origin = address; var all = saved; if !all.contains(address) { all.append(address) }; defaults.set(all, forKey: "origins"); defaults.set(address, forKey: "active"); subtitle.text = MobileStrings.text("正在连接 · ") + address
        let configuration = WKWebViewConfiguration(); configuration.websiteDataStore = .default(); configuration.applicationNameForUserAgent = "BridgeMobile/0.1-iOS"
        configuration.ignoresViewportScaleLimits = false
        configuration.userContentController.addUserScript(WKUserScript(source: Self.fixedViewport, injectionTime: .atDocumentEnd, forMainFrameOnly: true))
        clipboardToken = UUID().uuidString
        let appearanceScript = Self.webAppearance.replacingOccurrences(of: "__BRIDGE_CLIPBOARD_TOKEN__", with: clipboardToken)
        configuration.userContentController.addUserScript(WKUserScript(source: appearanceScript, injectionTime: .atDocumentEnd, forMainFrameOnly: true))
        let clipboardScript = Self.webClipboard.replacingOccurrences(of: "__BRIDGE_CLIPBOARD_TOKEN__", with: clipboardToken)
        configuration.userContentController.addUserScript(WKUserScript(source: clipboardScript, injectionTime: .atDocumentEnd, forMainFrameOnly: true))
        let web = WKWebView(frame: .zero, configuration: configuration); self.web = web; web.scrollView.contentInsetAdjustmentBehavior = .never; web.navigationDelegate = self; web.uiDelegate = self; web.allowsBackForwardNavigationGestures = true; web.customUserAgent = nil; page.addArrangedSubview(web); gatewayMenuTop = gatewayMenu.topAnchor.constraint(equalTo: web.topAnchor, constant: 4); gatewayMenuTop?.isActive = true; web.load(URLRequest(url: url))
        web.scrollView.pinchGestureRecognizer?.isEnabled = false
    }
    @objc private func refresh() { web?.reload() }
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { decisionHandler(.cancel); return }
        if url.absoluteString == "codexbridge://home", action.sourceFrame.isMainFrame, action.navigationType == .linkActivated, let source = webView.url, GatewayURL.same(source, origin) { decisionHandler(.cancel); home(); return }
        if action.navigationType == .linkActivated, action.sourceFrame.isMainFrame,
           let source = webView.url, GatewayURL.same(source, origin), isArtifact(url) {
            decisionHandler(.cancel); downloadArtifact(action.request); return
        }
        if GatewayURL.same(url, origin) {
            if action.targetFrame == nil { decisionHandler(.cancel); webView.load(action.request) }
            else { decisionHandler(.allow) }
            return
        }
        decisionHandler(.cancel)
        if action.navigationType == .linkActivated, ["http", "https"].contains(url.scheme ?? "") {
            let a = UIAlertController(title: MobileStrings.text("在浏览器打开外部链接？"), message: url.host, preferredStyle: .alert); a.addAction(UIAlertAction(title: MobileStrings.text("取消"), style: .cancel)); a.addAction(UIAlertAction(title: MobileStrings.text("打开"), style: .default) { _ in UIApplication.shared.open(url) }); present(a, animated: true)
        }
    }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) { subtitle.isHidden = true; registerNativePush() }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) { subtitle.isHidden = false; subtitle.text = MobileStrings.text("连接失败，请检查电脑和地址后刷新") }
    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) { let a = UIAlertController(title: nil, message: message, preferredStyle: .alert); a.addAction(UIAlertAction(title: MobileStrings.text("好"), style: .default) { _ in completionHandler() }); present(a, animated: true) }
    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) { let a = UIAlertController(title: nil, message: message, preferredStyle: .alert); a.addAction(UIAlertAction(title: MobileStrings.text("取消"), style: .cancel) { _ in completionHandler(false) }); a.addAction(UIAlertAction(title: MobileStrings.text("确认"), style: .default) { _ in completionHandler(true) }); present(a, animated: true) }
    func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String, defaultText: String?, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (String?) -> Void) {
        if prompt == "codexbridge-computer:" + clipboardToken {
            guard webView === web, frame.isMainFrame, let source = frame.request.url, GatewayURL.same(source, origin),
                  let current = webView.url, GatewayURL.same(current, origin) else { completionHandler(nil); return }
            completionHandler(computerName(origin)); return
        }
        if prompt == "codexbridge-language:" + clipboardToken {
            guard webView === web, frame.isMainFrame, let source = frame.request.url, GatewayURL.same(source, origin),
                  let current = webView.url, GatewayURL.same(current, origin), let language = defaultText, ["zh", "en"].contains(language) else { completionHandler(nil); return }
            defaults.set(language, forKey: "bridge-language");updateGatewayMenu();completionHandler("saved");return
        }
        guard prompt == "codexbridge-copy:" + clipboardToken, webView === web,
              UIApplication.shared.applicationState == .active, frame.isMainFrame,
              let source = frame.request.url, GatewayURL.same(source, origin),
              let current = webView.url, GatewayURL.same(current, origin),
              let text = defaultText, text.utf16.count <= 262144 else { completionHandler(nil); return }
        UIPasteboard.general.string = text
        completionHandler("copied")
    }
    private func isArtifact(_ url: URL) -> Bool {
        DownloadManager.allows(url, origin: origin)
    }
    private func downloadArtifact(_ request: URLRequest) {
        guard !downloadState.busy else { downloadPanel.expand(); info(MobileStrings.text("已有下载进行中，请稍候")); return }
        guard let url = request.url, isArtifact(url) else { return }
        downloadPanel.expand(); downloads.start(url, origin: origin)
    }
    private func saveDownload() {
        guard downloadState.phase == .complete, let file = downloadState.file, presentedViewController == nil else { return }
        let picker = UIDocumentPickerViewController(forExporting: [file], asCopy: true); picker.delegate = self
        exportPicker = picker
        present(picker, animated: true)
    }
    // Older iOS versions keep WebKit's built-in uploader. iOS 18.4+ supports
    // an explicit document picker with readable, imported copies and multi-selection.
    // Xcode 16.3 (Swift 6.1) is the first SDK that declares this iOS API.
    #if compiler(>=6.1)
    @available(iOS 18.4, *)
    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping @MainActor @Sendable ([URL]?) -> Void) {
        guard webView === web, frame.isMainFrame, let source = frame.request.url, GatewayURL.same(source, origin),
              let current = webView.url, GatewayURL.same(current, origin), presentedViewController == nil else { completionHandler(nil); return }
        finishUpload(nil)
        let picker = UIDocumentPickerViewController(forOpeningContentTypes: [.item], asCopy: true)
        picker.allowsMultipleSelection = parameters.allowsMultipleSelection; picker.delegate = self
        uploadPicker = picker; uploadCompletion = completionHandler; uploadGeneration = generation
        present(picker, animated: true)
    }
    #endif
    private func finishUpload(_ urls: [URL]?) {
        let completion = uploadCompletion; uploadCompletion = nil; uploadPicker = nil
        completion?(urls)
    }
    func documentPicker(_ controller: UIDocumentPickerViewController, didPickDocumentsAt urls: [URL]) {
        if controller === exportPicker { exportPicker = nil; if !urls.isEmpty { downloads.markSaved() }; return }
        guard controller === uploadPicker else { return }
        guard uploadGeneration == generation, let current = web?.url, GatewayURL.same(current, origin) else { finishUpload(nil); return }
        guard !urls.isEmpty, urls.allSatisfy({ $0.isFileURL && FileManager.default.isReadableFile(atPath: $0.path) }) else {
            finishUpload(nil)
            controller.dismiss(animated: true) { self.info(MobileStrings.text("无法读取所选文件，请从系统文件选择器重新选择。")) }
            return
        }
        finishUpload(urls)
    }
    func documentPickerWasCancelled(_ controller: UIDocumentPickerViewController) {
        if controller === uploadPicker { finishUpload(nil) }
        if controller === exportPicker { exportPicker = nil }
    }
    func openLink(_ url: URL) {
        guard let c = URLComponents(url: url, resolvingAgainstBaseURL: false), c.scheme == "codexbridge", c.host == "open" else { return }
        let q = c.queryItems ?? []; func get(_ key: String) -> String? { q.first(where: { $0.name == key })?.value }
        guard let address = get("origin"), saved.contains(address), let thread = get("thread"), let target = try? GatewayURL.chat(address, thread: thread, host: get("host") ?? "local") else { info(MobileStrings.text("请先扫码保存通知对应的电脑，再打开聊天。")); return }
        connect(target, address: address)
    }
    private func fetch(address requestedAddress: String? = nil, _ done: @escaping (Result<[String: Any], Error>) -> Void) {
        let address = requestedAddress ?? origin, ticket = generation
        guard let target = URL(string: address + "/api/mobile/events"), !address.isEmpty else { done(.failure(GatewayURL.InvalidURL())); return }
        WKWebsiteDataStore.default().httpCookieStore.getAllCookies { cookies in
            guard ticket == self.generation else { self.loading = false; return }
            let host = target.host ?? ""
            let matching = cookies.filter { cookie in cookie.name == "codex_mobile_session" && cookie.domain.trimmingCharacters(in: CharacterSet(charactersIn: ".")) == host && (!cookie.isSecure || target.scheme == "https") && (cookie.expiresDate == nil || cookie.expiresDate! > Date()) }
            guard !matching.isEmpty else { done(.failure(NSError(domain: "Bridge", code: 401, userInfo: [NSLocalizedDescriptionKey: MobileStrings.text("请先连接并登录电脑网关")]))); return }
            var request = URLRequest(url: target); request.setValue(address, forHTTPHeaderField: "Origin"); request.setValue(HTTPCookie.requestHeaderFields(with: matching)["Cookie"], forHTTPHeaderField: "Cookie"); request.setValue("BridgeMobile/0.1-iOS", forHTTPHeaderField: "User-Agent")
            self.session.dataTask(with: request) { data, response, error in
                let code = (response as? HTTPURLResponse)?.statusCode
                let result: Result<[String: Any], Error>
                if let error { result = .failure(error) }
                else if code == 200, let data, data.count <= 512000, let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any] { result = .success(value) }
                else { result = .failure(NSError(domain: "Bridge", code: code ?? 0, userInfo: [NSLocalizedDescriptionKey: code == 401 || code == 403 ? MobileStrings.text("登录已失效，请重新连接电脑") : MobileStrings.text("请使用配套电脑 Preview，并检查连接")])) }
                DispatchQueue.main.async {
                    guard ticket == self.generation else { self.loading = false; return }
                    guard code == 200, let response = response as? HTTPURLResponse,
                          let responseURL = response.url, GatewayURL.same(responseURL, address),
                          let headers = response.allHeaderFields as? [String: String],
                          let renewed = HTTPCookie.cookies(withResponseHeaderFields: headers, for: target).first(where: { cookie in
                              cookie.name == "codex_mobile_session" && cookie.domain.trimmingCharacters(in: CharacterSet(charactersIn: ".")) == host && cookie.path == "/" && matching.contains(where: { $0.name == cookie.name && $0.value == cookie.value })
                          }) else { done(result); return }
                    let store = WKWebsiteDataStore.default().httpCookieStore
                    store.getAllCookies { current in
                        guard ticket == self.generation else { self.loading = false; return }
                        // Do not resurrect a removed connection or replace a newer login.
                        guard self.saved.contains(address), current.contains(where: { $0.name == renewed.name && $0.value == renewed.value && $0.domain == renewed.domain && $0.path == renewed.path }) else { done(result); return }
                        store.setCookie(renewed) { if ticket == self.generation { done(result) } else { self.loading = false } }
                    }
                }
            }.resume()
        }
    }
    private func sheet(_ title: String) -> (UINavigationController, UIStackView) {
        let controller = UIViewController(); controller.title = title; controller.view.backgroundColor = .systemGroupedBackground
        let content = scrollContent(in: controller.view)
        let navigation = UINavigationController(rootViewController: controller)
        navigation.modalPresentationStyle = .pageSheet
        navigation.sheetPresentationController?.detents = [.large()]
        navigation.sheetPresentationController?.prefersGrabberVisible = true
        controller.navigationItem.rightBarButtonItem = UIBarButtonItem(systemItem: .done, primaryAction: UIAction { [weak navigation] _ in navigation?.dismiss(animated: true) })
        present(navigation, animated: true); return (navigation, content)
    }
    @objc private func inbox() { fetch { result in
        switch result {
        case .failure(let error): self.info(error.localizedDescription)
        case .success(let data):
            guard data["enabled"] as? Bool == true else { self.info(MobileStrings.text("请在电脑 Preview 的手机通知中开启“手机 App 通知收件箱”并保存。")); return }
            let stream = data["streamId"] as? String ?? ""
            let clearKey = "cleared:" + self.origin + ":" + stream
            let events = (data["events"] as? [[String: Any]] ?? []).filter { ($0["sequence"] as? Int ?? 0) > self.defaults.integer(forKey: clearKey) }
            let address = self.origin
            let (sheet, content) = self.sheet(MobileStrings.text("通知"))
            if !events.isEmpty {
                content.addArrangedSubview(self.button(MobileStrings.text("清空通知"), symbol: "trash") { [weak self, weak sheet] in
                    let cursor = data["cursor"] as? Int ?? 0
                    self?.defaults.set(cursor, forKey: clearKey)
                    UNUserNotificationCenter.current().getDeliveredNotifications { notices in
                        let ids = notices.filter { ($0.request.content.userInfo["origin"] as? String) == address && (Int(String(describing: $0.request.content.userInfo["sequence"] ?? 0)) ?? 0) <= cursor }.map { $0.request.identifier }
                        UNUserNotificationCenter.current().removeDeliveredNotifications(withIdentifiers: ids)
                    }
                    sheet?.dismiss(animated: true) { self?.inbox() }
                })
            }
            content.addArrangedSubview(self.label(MobileStrings.text("最近任务提醒"), size: 24, weight: .semibold))
            if events.isEmpty {
                content.addArrangedSubview(self.card([self.label(MobileStrings.text("暂无通知"), size: 18, weight: .medium), self.label(MobileStrings.text("在聊天中开启“提醒”，任务完成或需要你处理时，会记录在这里。"), secondary: true)]))
            }
            let date = DateFormatter(); date.dateStyle = .short; date.timeStyle = .short
            for event in events.reversed() {
                var items: [UIView] = [self.label(event["title"] as? String ?? MobileStrings.text("任务提醒"), size: 17, weight: .semibold)]
                if let body = event["body"] as? String, !body.isEmpty { items.append(self.label(body, secondary: true)) }
                if let timestamp = event["createdAt"] as? Double { items.append(self.label(date.string(from: Date(timeIntervalSince1970: timestamp)), size: 12, secondary: true)) }
                items.append(self.button(MobileStrings.text("查看聊天"), symbol: "arrow.up.right") { [weak self, weak sheet] in
                    sheet?.dismiss(animated: true) {
                        if let thread = event["threadId"] as? String, let host = event["host"] as? String, let url = try? GatewayURL.chat(address, thread: thread, host: host) { self?.connect(url, address: address) }
                    }
                })
                content.addArrangedSubview(self.card(items))
            }
        }
    } }
    @objc private func active() { checkComputers(); foregroundBaselines.removeAll();sync(); activityTimer?.invalidate(); activityTimer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in self?.syncLiveActivity() }; syncLiveActivity(); timer?.invalidate(); timer = Timer.scheduledTimer(withTimeInterval: 20, repeats: true) { [weak self] _ in self?.sync() } }
    @objc private func inactive() { downloads.pause(); stopComputerChecks(); timer?.invalidate(); timer = nil; activityTimer?.invalidate(); activityTimer = nil; if #available(iOS 16.2, *) { Task { await LiveActivityController.shared.pause() } } }
    private func sync() {
        registerNativePush()
        guard UIApplication.shared.applicationState == .active, !loading else { return }
        loading = true
        let addresses = saved
        func next(_ index: Int) {
            guard index < addresses.count else { self.loading = false; return }
            let address = addresses[index]
            self.fetch(address: address) { result in
                if case .success(let data) = result, data["enabled"] as? Bool == true, let stream = data["streamId"] as? String {
                    let key = "cursor:" + address, previous = self.defaults.integer(forKey: key)
                    let ready = self.foregroundBaselines.contains(address) && self.defaults.string(forKey: "stream:" + address) == stream
                    let cleared = self.defaults.integer(forKey: "cleared:" + address + ":" + stream)
                    if ready, self.defaults.bool(forKey: "foregroundAlerts"), UIApplication.shared.applicationState == .active {
                        for event in data["events"] as? [[String: Any]] ?? [] where (event["sequence"] as? Int ?? 0) > max(previous, cleared) {
                            let content = UNMutableNotificationContent()
                            content.title = event["title"] as? String ?? MobileStrings.text("任务提醒")
                            content.body = event["body"] as? String ?? ""
                            content.sound = .default
                            content.userInfo = ["origin": address, "thread": event["threadId"] as? String ?? "", "host": event["host"] as? String ?? "local", "sequence": event["sequence"] as? Int ?? 0]
                            let identifier = address + ":" + stream + ":" + (event["id"] as? String ?? String(event["sequence"] as? Int ?? 0))
                            UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: identifier, content: content, trigger: nil))
                        }
                    }
                    self.defaults.set(data["cursor"] as? Int ?? 0, forKey: key)
                    self.defaults.set(stream, forKey: "stream:" + address)
                    self.foregroundBaselines.insert(address)
                }
                next(index + 1)
            }
        }
        next(0)
    }
    private func toggleForegroundAlerts() {
        if defaults.bool(forKey: "foregroundAlerts") { defaults.set(false, forKey: "foregroundAlerts"); return }
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge]) { allowed, _ in
            DispatchQueue.main.async { self.defaults.set(allowed, forKey: "foregroundAlerts"); self.foregroundBaselines.removeAll(); if allowed { self.sync() } else { self.info(MobileStrings.text("请在系统设置中允许通知。")) } }
        }
    }
    private func currentTask(_ completion: @escaping ([String: String]?) -> Void) {
        guard let web, let url = web.url, GatewayURL.same(url, origin) else { completion(nil); return }
        let ticket = generation
        web.evaluateJavaScript("""
        (() => {
          if (document.getElementById('app')?.hidden || typeof currentId === 'undefined' || !currentId || typeof state === 'undefined' || !state) return null;
          const phase = !state.connected ? 'offline' : state.requests?.length ? 'waiting' : state.status === 'active' ? 'running' : 'ready';
          return {thread: currentId, host: currentHost, phase};
        })()
        """) { value, _ in
            guard ticket == self.generation, let task = value as? [String: String], let thread = task["thread"], UUID(uuidString: thread) != nil else { completion(nil); return }
            completion(task)
        }
    }
    private func startLiveActivity() {
        guard #available(iOS 16.2, *) else { info(MobileStrings.text("实时活动需要 iOS 16.2 或更新版本。")); return }
        currentTask { [weak self] task in
            guard let self else { return }
            guard let task else { self.info(MobileStrings.text("请先打开要跟踪的聊天，再选择灵动岛显示。")); return }
            Task { do { try await LiveActivityController.shared.start(origin: self.origin, thread: task["thread"]!, host: task["host"] ?? "local", phase: task["phase"] ?? "offline") }
                catch { self.info(error.localizedDescription) } }
        }
    }
    private func syncLiveActivity() {
        guard #available(iOS 16.2, *), LiveActivityController.shared.enabled else { return }
        currentTask { [weak self] task in
            guard let self else { return }
            Task {
                if let task { await LiveActivityController.shared.update(origin: self.origin, thread: task["thread"]!, host: task["host"] ?? "local", phase: task["phase"] ?? "offline") }
                else { await LiveActivityController.shared.pause() }
            }
        }
    }
    @objc private func settings() {
        let (sheet, content) = sheet(MobileStrings.text("设置"))
        content.addArrangedSubview(label(MobileStrings.text("通知"), size: 19, weight: .semibold))
        content.addArrangedSubview(card([
            label(MobileStrings.text("前台任务提醒"), size: 17, weight: .semibold),
            button(defaults.bool(forKey: "foregroundAlerts") ? MobileStrings.text("关闭任务通知") : MobileStrings.text("开启任务通知"), symbol: "bell") { [weak self, weak sheet] in sheet?.dismiss(animated: true) { self?.toggleForegroundAlerts() } },
            label(MobileStrings.text("App 打开时提醒已连接电脑的新任务消息。离开 App 或锁屏后不保证通知；可在电脑端配置 Bark 或 ntfy。"), secondary: true),
            button(MobileStrings.text("测试本机通知"), symbol: "bell.badge") { [weak self, weak sheet] in sheet?.dismiss(animated: true) { self?.testNotification() } },
            label(MobileStrings.text("10 秒后显示，用于检查手机的通知权限。"), size: 13, secondary: true)
        ]))
        if #available(iOS 16.2, *) {
            content.addArrangedSubview(label(MobileStrings.text("灵动岛与锁屏"), size: 19, weight: .semibold))
            content.addArrangedSubview(card([
                label(MobileStrings.text("跟踪当前聊天"), size: 17, weight: .semibold),
                label(MobileStrings.text("从电脑菜单选择聊天跟踪。后台显示可能不是最新状态，打开 App 可继续同步。"), secondary: true),
                button(MobileStrings.text("预览灵动岛"), symbol: "capsule") { [weak self, weak sheet] in
                    sheet?.dismiss(animated: true) {
                        Task { do { try await LiveActivityController.shared.start(origin: "", thread: "", host: "local", phase: "running", demo: true) }
                            catch { self?.info(error.localizedDescription) } }
                    }
                },
                button(MobileStrings.text("结束灵动岛显示"), symbol: "xmark.circle") { Task { await LiveActivityController.shared.stop() } }
            ]))
        }
        content.addArrangedSubview(label(MobileStrings.text("当前电脑"), size: 19, weight: .semibold))
        if origin.isEmpty {
            content.addArrangedSubview(card([label(MobileStrings.text("尚未选择电脑"), size: 17), label(MobileStrings.text("返回首页扫码或输入网关地址。"), secondary: true)]))
        } else {
            content.addArrangedSubview(card([
                label(computerName(origin), size: 17, weight: .semibold), label(origin, size: 13, secondary: true),
                button(MobileStrings.text("移除这台电脑"), symbol: "trash") { [weak self, weak sheet] in sheet?.dismiss(animated: true) { self?.confirmRemove() } }
            ]))
        }
        let current = Bundle.main.object(forInfoDictionaryKey: "BridgeReleaseVersion") as? String ?? "2.0.0-preview.3"
        let updateStatus = label("", size: 13, secondary: true)
        let check = button(MobileStrings.text("检查更新"), symbol: "arrow.down.circle") {}
        check.addAction(UIAction { [weak self, weak sheet, weak check, weak updateStatus] _ in
            guard let self, let sheet, let check, let updateStatus else { return }
            self.checkUpdate(current: current, sheet: sheet, check: check, status: updateStatus)
        }, for: .touchUpInside)
        content.addArrangedSubview(card([label(MobileStrings.text("应用更新"), size: 19, weight: .semibold), label("Bridge Preview · " + current, size: 14), label(MobileStrings.text("预览通道 · 手动检查，不自动安装"), size: 13, secondary: true), check, updateStatus, label(MobileStrings.text("iOS 预览需要使用自己的 Apple 账号重新签名安装，暂不支持 App 内直接覆盖更新。"), size: 13, secondary: true)]))
    }
    private func checkUpdate(current: String, sheet: UIViewController, check: UIButton, status: UILabel) {
        check.isEnabled = false; status.text = MobileStrings.text("正在检查更新…")
        var request = URLRequest(url: MobileRelease.api); request.timeoutInterval = 20
        request.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        request.setValue("Codex-Mobile-Bridge/" + current, forHTTPHeaderField: "User-Agent")
        let session = URLSession(configuration: .ephemeral)
        session.dataTask(with: request) { [weak sheet, weak check, weak status] data, response, error in
            defer { session.finishTasksAndInvalidate() }
            var candidate: MobileRelease?; var failed = false
            do {
                guard error == nil, (response as? HTTPURLResponse)?.statusCode == 200, let data, data.count <= 4 * 1024 * 1024 else { throw URLError(.badServerResponse) }
                candidate = try MobileRelease.select(data, current: current)
            } catch { failed = true }
            DispatchQueue.main.async {
                guard let sheet, sheet.presentingViewController != nil, let check, let status else { return }
                check.isEnabled = true
                let url = candidate?.page ?? URL(string: MobileRelease.repository + "/releases")!
                if !failed && candidate == nil { status.text = MobileStrings.text("当前已是此通道最新版本。"); return }
                status.text = MobileStrings.text(failed ? "检查失败，请检查网络后重试。也可以打开版本页面。" : "发现新版本") + (candidate.map { " · " + $0.version } ?? "")
                let alert = UIAlertController(title: status.text, message: candidate.map { MobileStrings.text("iOS 预览需要使用自己的 Apple 账号重新签名安装，暂不支持 App 内直接覆盖更新。") + "\n\n" + $0.notes }, preferredStyle: .alert)
                alert.addAction(UIAlertAction(title: MobileStrings.text("稍后"), style: .cancel))
                alert.addAction(UIAlertAction(title: MobileStrings.text(failed ? "版本页面" : "查看新版与安装指引"), style: .default) { _ in UIApplication.shared.open(url) })
                sheet.present(alert, animated: true)
            }
        }.resume()
    }
    private var pushSupported: Bool { Bundle.main.object(forInfoDictionaryKey: "BridgePushEnabled") as? Bool == true }
    private func enableNativePush() {
        guard pushSupported else { info(MobileStrings.text("此预览尚未配置 Apple 推送签名。需要开发者账号和电脑端推送服务后才能启用后台任务提醒。")); return }
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge]) { allowed, _ in DispatchQueue.main.async {
            guard allowed else { self.info(MobileStrings.text("请在系统设置中允许通知。")); return }
            self.defaults.set(true, forKey: "nativePushEnabled"); UIApplication.shared.registerForRemoteNotifications()
        } }
    }
    func registerNativePush() {
        guard pushSupported, defaults.bool(forKey: "nativePushEnabled"), let token = defaults.string(forKey: "apnsToken") else { return }
        sendPushRegistration(["kind": "apns", "token": token], address: origin)
        if #available(iOS 16.2, *) {
            LiveActivityController.shared.pushAvailable = true
            LiveActivityController.shared.onPushToken = { [weak self] attributes, token in
                self?.sendPushRegistration(["kind": "activity", "token": token, "thread": attributes.thread, "host": attributes.host], address: attributes.origin)
            }
            LiveActivityController.shared.recoverPushTokens()
        }
    }
    private func sendPushRegistration(_ body: [String: String], address: String) {
        guard let web, address == origin, let url = web.url, GatewayURL.same(url, origin) else { return }
        let device = defaults.string(forKey: "pushDeviceID") ?? UUID().uuidString; defaults.set(device, forKey: "pushDeviceID")
        var value = body; value["deviceId"] = device
        web.callAsyncJavaScript("const a = await (await fetch('/api/auth')).json(); if (!a.authenticated) return false; const r = await fetch('/api/mobile/push', {method:'POST', headers:{'Content-Type':'application/json', 'X-CSRF-Token':a.csrf}, body:JSON.stringify(body)}); return r.ok;", arguments: ["body": value], in: nil, in: .page) { _ in }
    }
    private func testNotification() {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert,.sound,.badge]) { allowed, _ in
            guard allowed else { DispatchQueue.main.async { self.info(MobileStrings.text("请在系统设置允许 Bridge Preview 通知。")) }; return }
            DispatchQueue.main.async {
                let content = UNMutableNotificationContent(); content.title = MobileStrings.text("手机通知测试"); content.body = MobileStrings.text("本机通知已开启"); content.sound = .default
                if !self.origin.isEmpty { content.userInfo = ["origin": self.origin] }
                UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: "preview-test", content: content, trigger: UNTimeIntervalNotificationTrigger(timeInterval: 10, repeats: false))) { error in
                    DispatchQueue.main.async { self.info(error == nil ? MobileStrings.text("10 秒后显示本地测试通知，可以先锁屏。此测试不代表远程推送已经接通。") : MobileStrings.text("测试通知未能创建，请检查系统通知设置。")) }
                }
            }
        }
    }
    private func revokeConnection(_ address: String, _ done: @escaping () -> Void) {
        guard address == origin, let web, let url = web.url, GatewayURL.same(url, address) else { done(); return }
        web.callAsyncJavaScript("const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), 5000); try { const a = await (await fetch('/api/auth', {signal:controller.signal})).json(); if (a.authenticated) await fetch('/api/logout', {method:'POST', signal:controller.signal, headers:{'Content-Type':'application/json', 'X-CSRF-Token':a.csrf}, body:'{}'}); } finally { clearTimeout(timer); }", arguments: [:], in: nil, in: .page) { _ in done() }
    }
    private func confirmRemove(_ address: String? = nil) {
        let target = address ?? origin
        let a = UIAlertController(title: MobileStrings.text("移除这台电脑？"), message: target + "\n\n" + MobileStrings.text("清除本机保存的连接和登录状态，电脑上的聊天不受影响。"), preferredStyle: .alert)
        a.addAction(UIAlertAction(title: MobileStrings.text("取消"), style: .cancel))
        a.addAction(UIAlertAction(title: MobileStrings.text("移除"), style: .destructive) { _ in
            let revision = self.generation
            self.downloads.cancel(origin: target)
            self.revokeConnection(target) {
                if target == self.origin, #available(iOS 16.2, *) { Task { await LiveActivityController.shared.stop() } }
                self.defaults.set(self.saved.filter { $0 != target }, forKey: "origins")
                if self.defaults.string(forKey: "active") == target { self.defaults.removeObject(forKey: "active") }
                self.defaults.removeObject(forKey: "cursor:" + target); self.defaults.removeObject(forKey: "stream:" + target); self.defaults.removeObject(forKey: "name:" + target)
                WKWebsiteDataStore.default().httpCookieStore.getAllCookies { cookies in
                    for cookie in cookies where cookie.domain.trimmingCharacters(in: CharacterSet(charactersIn: ".")) == URL(string: target)?.host && cookie.name == "codex_mobile_session" { WKWebsiteDataStore.default().httpCookieStore.delete(cookie) }
                }
                if self.origin == target { self.origin = "" }
                if self.generation == revision { self.home() }
            }
        }); present(a, animated: true)
    }
    private func scan() {
        AVCaptureDevice.requestAccess(for: .video) { allowed in DispatchQueue.main.async { guard allowed else { self.info(MobileStrings.text("未获得相机权限，请粘贴网关地址。")); return }; let scanner = Scanner(); scanner.result = { [weak self] value in self?.dismiss(animated: true) { self?.choose(value) } }; self.present(scanner, animated: true) } }
    }
}

final class DownloadPanel: UIView {
    var onToggle: (() -> Void)?
    var onCancel: (() -> Void)?
    var onSave: (() -> Void)?
    private let title = UILabel()
    private let detail = UILabel()
    private let status = UILabel()
    private let progress = UIProgressView(progressViewStyle: .default)
    private let content = UIStackView()
    private let toggle = UIButton(type: .system)
    private let save = UIButton(type: .system)
    private let cancel = UIButton(type: .system)
    private let collapse = UIButton(type: .system)
    private var collapsed = false
    private var state = DownloadManager.Snapshot()
    override init(frame: CGRect) {
        super.init(frame: frame)
        backgroundColor = .secondarySystemGroupedBackground
        layer.cornerRadius = 16; layer.borderWidth = 0.5; layer.borderColor = UIColor.separator.cgColor
        layer.shadowColor = UIColor.black.cgColor; layer.shadowOpacity = 0.12; layer.shadowRadius = 12; layer.shadowOffset = CGSize(width: 0, height: 4)
        tintColor = UIColor(red: 0.24, green: 0.42, blue: 0.68, alpha: 1)
        let stack = UIStackView(); stack.axis = .vertical; stack.spacing = 6; stack.translatesAutoresizingMaskIntoConstraints = false; addSubview(stack)
        NSLayoutConstraint.activate([stack.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 14), stack.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -10), stack.topAnchor.constraint(equalTo: topAnchor, constant: 8), stack.bottomAnchor.constraint(equalTo: bottomAnchor, constant: -10)])
        let header = UIStackView(); header.axis = .horizontal; header.spacing = 8; header.alignment = .center
        let icon = UIImageView(image: UIImage(systemName: "arrow.down.circle")); icon.tintColor = tintColor; icon.contentMode = .scaleAspectFit
        icon.widthAnchor.constraint(equalToConstant: 22).isActive = true
        title.font = .preferredFont(forTextStyle: .subheadline); title.lineBreakMode = .byTruncatingMiddle; title.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        collapse.widthAnchor.constraint(equalToConstant: 44).isActive = true; collapse.heightAnchor.constraint(equalToConstant: 36).isActive = true
        collapse.addAction(UIAction { [weak self] _ in guard let self else { return }; self.collapsed.toggle(); self.render(self.state) }, for: .touchUpInside)
        header.addArrangedSubview(icon); header.addArrangedSubview(title); header.addArrangedSubview(collapse); stack.addArrangedSubview(header)
        content.axis = .vertical; content.spacing = 7; stack.addArrangedSubview(content)
        detail.font = .monospacedDigitSystemFont(ofSize: 12, weight: .regular); detail.textColor = .secondaryLabel; detail.numberOfLines = 2
        status.font = .preferredFont(forTextStyle: .caption1); status.textColor = .secondaryLabel; status.numberOfLines = 2
        progress.progressTintColor = tintColor; progress.trackTintColor = .tertiarySystemFill
        content.addArrangedSubview(detail); content.addArrangedSubview(progress); content.addArrangedSubview(status)
        let actions = UIStackView(); actions.axis = .horizontal; actions.spacing = 8; actions.distribution = .fillEqually; content.addArrangedSubview(actions)
        for button in [toggle, save, cancel] {
            var config = UIButton.Configuration.tinted(); config.cornerStyle = .medium
            config.baseForegroundColor = tintColor; config.titleTextAttributesTransformer = UIConfigurationTextAttributesTransformer { incoming in var output = incoming; output.font = .systemFont(ofSize: 14, weight: .medium); return output }
            button.configuration = config; button.heightAnchor.constraint(greaterThanOrEqualToConstant: 40).isActive = true; actions.addArrangedSubview(button)
        }
        toggle.addAction(UIAction { [weak self] _ in self?.onToggle?() }, for: .touchUpInside)
        cancel.addAction(UIAction { [weak self] _ in self?.onCancel?() }, for: .touchUpInside)
        save.addAction(UIAction { [weak self] _ in self?.onSave?() }, for: .touchUpInside)
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
    func expand() { collapsed = false; render(state) }
    func render(_ state: DownloadManager.Snapshot) {
        self.state = state; isHidden = !state.visible; content.isHidden = collapsed
        let percentage = state.total.map { $0 == 0 ? 100 : min(100, Int(Double(state.received) / Double($0) * 100)) }
        let phases: [DownloadManager.Phase: String] = [.connecting: "正在连接…", .downloading: "正在下载…", .paused: "已暂停", .failed: "下载未完成", .complete: "下载完成，待保存", .saved: "已保存", .idle: "", .cancelled: "已取消"]
        let phase = MobileStrings.text(phases[state.phase] ?? "")
        title.text = state.name + (collapsed ? " · " + (state.phase == .downloading ? percentage.map { "\($0)%" } ?? phase : phase) : "")
        title.accessibilityLabel = state.name
        collapse.setImage(UIImage(systemName: collapsed ? "chevron.up" : "chevron.down"), for: .normal)
        collapse.accessibilityLabel = MobileStrings.text(collapsed ? "展开下载" : "收起下载")
        func bytes(_ value: Int64) -> String { ByteCountFormatter.string(fromByteCount: value, countStyle: .file) }
        detail.text = bytes(state.received) + (state.total.map { " / " + bytes($0) } ?? "") +
            (percentage.map { " · \($0)%" } ?? "") + (state.phase == .downloading ? " · " + bytes(Int64(max(0, state.speed))) + "/s" : "")
        status.text = state.message.isEmpty ? phase : MobileStrings.text(state.message)
        progress.isHidden = state.total == nil
        progress.progress = state.total.map { $0 == 0 ? 1 : Float(Double(state.received) / Double($0)) } ?? 0
        toggle.isHidden = ![.connecting, .downloading, .paused, .failed].contains(state.phase)
        toggle.configuration?.title = MobileStrings.text([.connecting, .downloading].contains(state.phase) ? "暂停" : state.canResume ? "继续下载" : "重新下载")
        save.isHidden = state.phase != .complete; save.configuration?.title = MobileStrings.text("保存到文件")
        cancel.configuration?.title = MobileStrings.text(state.phase == .saved ? "关闭" : "取消下载")
    }
}

final class Scanner: UIViewController, AVCaptureMetadataOutputObjectsDelegate {
    var result: ((String) -> Void)?
    private let capture = AVCaptureSession()
    private var layer: AVCaptureVideoPreviewLayer?
    private let queue = DispatchQueue(label: "bridge.scan")
    private var found = false
    override func viewDidLoad() {
        super.viewDidLoad(); view.backgroundColor = .black
        guard let device = AVCaptureDevice.default(for: .video), let input = try? AVCaptureDeviceInput(device: device), capture.canAddInput(input) else { dismiss(animated: true); return }
        capture.addInput(input); let output = AVCaptureMetadataOutput(); guard capture.canAddOutput(output) else { return }; capture.addOutput(output); output.setMetadataObjectsDelegate(self, queue: .main); output.metadataObjectTypes = [.qr]
        let preview = AVCaptureVideoPreviewLayer(session: capture); preview.videoGravity = .resizeAspectFill; view.layer.addSublayer(preview); layer = preview
        let close = UIButton(type: .system); close.setTitle(MobileStrings.text("取消扫码"), for: .normal); close.tintColor = .white; close.translatesAutoresizingMaskIntoConstraints = false; view.addSubview(close); close.addAction(UIAction { _ in self.dismiss(animated: true) }, for: .touchUpInside); NSLayoutConstraint.activate([close.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor, constant: 15), close.centerXAnchor.constraint(equalTo: view.centerXAnchor)])
        queue.async { self.capture.startRunning() }
    }
    override func viewDidLayoutSubviews() { super.viewDidLayoutSubviews(); layer?.frame = view.bounds }
    override func viewWillDisappear(_ animated: Bool) { queue.async { self.capture.stopRunning() }; super.viewWillDisappear(animated) }
    func metadataOutput(_ output: AVCaptureMetadataOutput, didOutput objects: [AVMetadataObject], from connection: AVCaptureConnection) { guard !found, let text = (objects.first as? AVMetadataMachineReadableCodeObject)?.stringValue else { return }; found = true; result?(text) }
}

// Product strings only. Chat content and server-provided names are not translated.
enum MobileStrings {
    static let values: [String: String] = [
        "正在连接…": "Connecting…",
        "已暂停": "Paused",
        "下载未完成": "Download incomplete",
        "下载完成，待保存": "Downloaded · ready to save",
        "已保存": "Saved",
        "已取消": "Cancelled",
        "展开下载": "Expand download",
        "收起下载": "Collapse download",
        "暂停": "Pause",
        "继续下载": "Resume",
        "重新下载": "Restart",
        "保存到文件": "Save to Files",
        "取消下载": "Cancel download",
        "关闭": "Close",
        "当前服务器不支持续传，请重新下载": "This server cannot resume downloads. Restart the download.",
        "下载响应格式不正确，请重新下载": "Invalid download response. Restart the download.",
        "文件已变化，请重新下载": "The file changed. Restart the download.",
        "网关不支持安全续传，请更新网关后重试": "Update the gateway to resume downloads safely.",
        "文件已变化或服务器不支持续传，请重新下载": "The file changed or the server cannot resume it. Restart the download.",
        "下载中断，可继续下载": "Download interrupted. You can resume it.",
        "电脑暂时不可用，可继续下载": "Computer temporarily unavailable. You can resume the download.",
        "无法读取所选文件，请从系统文件选择器重新选择。": "Cannot read the selected file. Select it again using the system file picker.",
        "重命名": "Rename",
        "重命名电脑": "Rename computer",
        "留空恢复默认名称": "Leave blank to restore the default name",
        "名称最多 80 个字符": "Use no more than 80 characters",
        "保存": "Save",

"Codex 未完成侧边聊天操作，请检查模型接入和运行时版本":"Codex could not complete the side chat operation. Check the model connection and runtime version.",
"临时侧边聊天数量已达上限，请关闭不用的聊天；必要时重启网关":"Too many temporary side chats. End unused chats or restart the gateway.",
"侧边聊天工作目录不一致，已取消创建":"Side chat creation cancelled because the working directory did not match.",
"侧边聊天已关闭或失效":"Side chat closed or expired",
"侧边聊天已失效，请关闭后新建":"Side chat expired. End it and create a new one.",
"侧边聊天执行失败，请检查模型接入后重试":"Side chat failed. Check the model connection and try again.",
"侧边聊天操作结果未确认，请刷新状态，不要重复发送":"Operation unconfirmed. Refresh the status before sending again.",
"侧边聊天的模型接入与主聊天不一致，已取消创建":"Side chat creation cancelled because its model connection did not match the parent.",
"侧边聊天运行时已断开":"Side chat runtime disconnected",
"同一消息标识不能用于不同内容":"A message ID cannot be reused for different content",
"无效侧边聊天操作":"Invalid side chat operation",
"无法确认临时分支，已取消创建":"Could not verify the temporary branch. Creation cancelled.",
"模型 ID 格式不正确":"Invalid model ID",
"此 Codex 运行时不支持临时侧边聊天，请更新 Codex":"This runtime does not support temporary side chats. Update Codex.",
"此侧边聊天已关闭或已失效，请刷新标签":"Side chat closed or expired. Refresh this tab.",
"此次创建的侧边聊天已关闭，请重新打开标签后新建":"This side chat was ended. Reopen the tab to create another.",
"此请求已处理或已失效":"This request was handled or expired",
"此请求暂不支持在侧边聊天处理，请停止本次回复。":"This request is not supported in side chats. Stop this response.",
"此请求暂不支持，请停止本次回复":"Unsupported request. Stop this response.",
"此预览暂支持网关电脑上的聊天，SSH 远程工作区尚未接入侧边聊天":"Side chats currently support the gateway computer. SSH remote workspaces are not supported yet.",
"确认回复格式不正确":"Invalid approval response",
"确认操作无效":"Invalid approval action",
"请先等待回复完成或处理确认请求":"Wait for the response or handle the pending request first",
"请输入 1–20000 字的消息":"Enter a message of 1–20,000 characters",
"请选择有效的推理强度":"Choose a valid reasoning effort",
"请选择本次允许、拒绝或取消":"Choose allow once, deny or cancel",
"请选择本次允许或拒绝":"Choose allow once or deny",
"这个模型不支持所选推理强度":"This model does not support the selected reasoning effort",
"公网地址必须使用 HTTPS；HTTP 仅限局域网":"Public addresses require HTTPS. HTTP is only allowed on a local network.",
"网关地址不应包含路径":"Gateway addresses must not include a path",
"请使用电脑网关提供的地址或二维码":"Use the address or QR code from your computer’s gateway",
"请填写完整网关地址，不含账号或参数":"Enter a complete gateway address without credentials or query parameters",
"请填写完整的 HTTPS 网关地址，或局域网 HTTP 地址，不含路径、账号和参数。":"Enter a complete HTTPS gateway address, or a local HTTP address, without a path, credentials or parameters.",
        "通知收件箱": "Notification inbox",
        "手机设置": "Mobile settings",
"应用更新": "App updates",
"预览通道 · 手动检查，不自动安装": "Preview channel · Manual checks, no automatic install",
"检查更新": "Check for updates",
"正在检查更新…": "Checking for updates…",
"没有可用的浏览器，请在电脑上打开 GitHub Release。": "No browser is available. Open GitHub Releases on your computer.",
"检查失败，请检查网络后重试。也可以打开版本页面。": "Could not check for updates. Check your network and retry, or open the releases page.",
"版本页面": "Releases",
"当前已是此通道最新版本。": "You are up to date on this channel.",
"发现新版本": "Update available",
"下载后按系统提示覆盖安装，不要先卸载。已保存的电脑与登录状态会保留。": "After downloading, follow the system prompts to update without uninstalling. Saved computers and sign-ins will be retained.",
"稍后": "Later",
"更新说明": "Release notes",
"下载 APK": "Download APK",
"查看新版与安装指引": "View update and installation guide",
"iOS 预览需要使用自己的 Apple 账号重新签名安装，暂不支持 App 内直接覆盖更新。": "The iOS preview requires signing the new build with your own Apple account. Direct in-app installation is not available yet.",

        "返回电脑列表": "Back to computers",
        "外观与显示": "Appearance",
        "刷新页面": "Reload page",
        "好": "OK",
        "电脑上的工作，\n带在身边。": "Your computer’s work,\nalways with you.",
        "继续聊天、查看结果，让电脑替你运行。": "Continue chats and view results while your computer does the work.",
        "扫码连接电脑": "Scan to connect",
        "输入网关地址": "Enter gateway address",
        "你的电脑": "Your computers",
        "账号与接入": "Accounts & connections",
        "还没有连接的电脑": "No computers connected yet",
        "在电脑网关中展开“扫码登录”，然后用上方按钮扫描。": "Open “Scan to sign in” on your computer’s gateway, then scan using the button above.",
        "连接 ": "Connect ",
        "外出使用 HTTPS 地址；局域网地址需要连接同一网络。": "Use HTTPS when away. Local addresses require the same network.",
        "连接电脑": "Connect to computer",
        "粘贴电脑网关提供的访问地址": "Paste the address shown by your computer’s gateway",
        "继续": "Continue",
        "取消": "Cancel",
        "连接到这台电脑？": "Connect to this computer?",
        "\n请确认这是你自己的网关。": "\nMake sure this is your own gateway.",
        "连接": "Connect",
        "正在连接 · ": "Connecting · ",
        "电脑与聊天选项": "Computer and chat options",
        "在浏览器打开外部链接？\n": "Open external link in browser?\n",
        "打开": "Open",
        "连接失败，请在更多菜单中刷新": "Connection failed. Reload from the More menu.",
        "没有可用的文件选择器": "No file picker available",
        "已有下载进行中，请稍候": "A download is in progress. Please wait.",
        "此下载不是当前电脑的附件，请在浏览器中打开": "This download is not an attachment from the current computer. Open it in your browser.",
        "正在下载…": "Downloading…",
        "没有可用的文件保存器": "No file saver available",
        "下载失败，请重试": "Download failed. Try again.",
        "文件已保存": "File saved",
        "保存失败，请重试": "Could not save. Try again.",
        "请先扫码保存通知对应的电脑，再打开聊天。": "Scan and save the notification’s computer before opening the chat.",
        "通知链接无效": "Invalid notification link",
        "完成": "Done",
        "请先连接并登录电脑": "Connect and sign in to a computer first",
        "正在读取通知…": "Loading notifications…",
        "请在电脑 Preview 的手机通知中开启“手机 App 通知收件箱”并保存。": "Enable and save “Mobile app notification inbox” in the desktop Preview’s notification settings.",
        "通知": "Notifications",
        "最近任务提醒": "Recent task notifications",
        "清空通知": "Clear notifications",
        "暂无通知": "No notifications yet",
        "在聊天中开启“提醒”，任务完成或需要你处理时，会记录在这里。": "Enable chat notifications to see completed tasks and requests that need your attention here.",
        "任务提醒": "Task notification",
        "查看聊天  ↗": "View chat  ↗",
        "聊天链接无效": "Invalid chat link",
        "设置": "Settings",
        "启用系统后台推送": "Enable system push",
        "关闭任务通知": "Turn off task notifications",
        "开启任务通知": "Turn on task notifications",
        "App 打开时提醒已连接电脑的新任务消息。离开 App 或锁屏后不保证通知；可在电脑端配置 Bark 或 ntfy。": "Receive new task alerts from saved computers while this app is open. Delivery after leaving the app or locking the screen is not guaranteed. Configure Bark or ntfy on your computer for external alerts.",
        "测试本机通知": "Test local notification",
        "10 秒后显示，用于检查手机的通知权限。": "Appears in 10 seconds to check notification permissions.",
        "当前电脑": "Current computer",
        "尚未选择电脑": "No computer selected",
        "返回首页扫码或输入网关地址。": "Return home to scan or enter a gateway address.",
        "移除这台电脑": "Remove this computer",
        "此预览尚未配置系统推送项目。当前仅同步收件箱；配置 Firebase 或厂商推送后才能启用后台任务提醒。": "System push is not configured. Background task alerts require Firebase or a device vendor’s push service.",
        "手机通知测试": "Mobile notification test",
        "本机通知已开启": "Local notifications are enabled",
        "10 秒后显示本地测试通知，可以先返回桌面。此测试不代表远程推送已经接通。": "A local test notification will appear in 10 seconds. You may leave the app. This does not verify remote push delivery.",
        "移除这台电脑？": "Remove this computer?",
        "清除本机保存的连接和登录状态，电脑上的聊天不受影响。": "Remove the saved connection and sign-in on this device. Chats on the computer are unaffected.",
        "移除": "Remove",
        "未获得权限，可继续粘贴地址使用。": "Permission denied. You can still paste an address to connect.",
        "系统推送注册失败，请检查推送签名与网络。": "Push registration failed. Check signing and network access.",
        "电脑与通知": "Computers and notifications",
        "在灵动岛显示此聊天": "Show this chat in Dynamic Island",
        "结束灵动岛显示": "End Live Activity",
        "请先连接通知对应的电脑。": "Connect to the computer for this notification first.",
        "请先扫码保存通知对应的电脑。": "Scan and save this notification’s computer first.",
        "在浏览器打开外部链接？": "Open external link in browser?",
        "连接失败，请检查电脑和地址后刷新": "Connection failed. Check the computer and address, then reload.",
        "确认": "确认",
        "下载失败，请检查登录状态后重试": "Download failed. Check sign-in and try again.",
        "附件超过 50 MB 下载限制": "Attachment exceeds the 50 MB download limit",
        "无法保存下载文件，请检查剩余空间": "Could not save the download. Check available storage.",
        "下载地址发生跳转，已取消。请重新打开电脑附件。": "Download redirect blocked. Reopen the attachment from your computer.",
        "下载失败，请检查网络后重试": "Download failed. Check your network and try again.",
        "请先连接并登录电脑网关": "Connect and sign in to your computer’s gateway first",
        "登录已失效，请重新连接电脑": "Sign-in expired. Reconnect to the computer.",
        "请使用配套电脑 Preview，并检查连接": "Use the matching desktop Preview and check the connection",
        "查看聊天": "View chat",
        "请在系统设置中允许通知。": "Allow notifications in system settings.",
        "实时活动需要 iOS 16.2 或更新版本。": "Live Activities require iOS 16.2 or later.",
        "请先打开要跟踪的聊天，再选择灵动岛显示。": "Open a chat first, then choose to show it in Dynamic Island.",
        "前台任务提醒": "Foreground task notifications",
        "灵动岛与锁屏": "Dynamic Island and Lock Screen",
        "跟踪当前聊天": "Track current chat",
        "从电脑菜单选择聊天跟踪。后台显示可能不是最新状态，打开 App 可继续同步。": "Choose a chat to track from the computer menu. Background status may be out of date; open the app to sync.",
        "预览灵动岛": "Preview Live Activity",
        "此预览尚未配置 Apple 推送签名。需要开发者账号和电脑端推送服务后才能启用后台任务提醒。": "Apple push signing is not configured. Background alerts require a developer account and a gateway push service.",
        "请在系统设置允许 Bridge Preview 通知。": "Allow Bridge Preview notifications in system settings.",
        "10 秒后显示本地测试通知，可以先锁屏。此测试不代表远程推送已经接通。": "A local test notification will appear in 10 seconds. You may lock the screen. This does not verify remote push delivery.",
        "测试通知未能创建，请检查系统通知设置。": "Could not schedule the test. Check system notification settings.",
        "未获得相机权限，请粘贴网关地址。": "Camera access denied. Paste the gateway address instead.",
        "检查中…": "Checking…", "在线": "Online", "暂不可达": "Unreachable",
        "取消扫码": "Cancel scan"
    ]
    static func text(_ value: String) -> String {
        let language = UserDefaults.standard.string(forKey: "bridge-language") ?? Locale.preferredLanguages.first ?? "zh"
        return language.hasPrefix("en") ? values[value] ?? value : value
    }
}
