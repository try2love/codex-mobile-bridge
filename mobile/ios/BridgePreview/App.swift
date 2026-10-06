import UIKit
@preconcurrency import WebKit
import AVFoundation
import UserNotifications

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
    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: Error) { controller.info("系统推送注册失败，请检查推送签名与网络。") }
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

final class BridgeController: UIViewController, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate, UIDocumentPickerDelegate {
    private var web: WKWebView?
    private var origin = ""
    private var clipboardToken = ""
    private var artifactDownload: WKDownload?
    private var downloadStarting = false
    private var downloadFile: URL?
    private var timer: Timer?
    private var activityTimer: Timer?
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
        '.bridge-mobile .chat-head .appearance-button { display: none; }',
        '.bridge-mobile #back { width: 40px; height: 48px; padding: 8px; margin-left: 0; flex: none; }',
        '.bridge-mobile .sidebar-foot { order: 99; flex: none; min-height: 44px; padding: 2px 16px max(4px, env(safe-area-inset-bottom)); background: var(--page); }',
        '.bridge-mobile:not(.bridge-authenticated) .sidebar-foot { display: none; }',
        '.bridge-mobile body.chat-detail > .sidebar-foot { display: none; }',
        '.bridge-mobile body.chat-detail .composer { padding-bottom: max(8px, env(safe-area-inset-bottom)); }',
        '.bridge-mobile .sidebar-foot #logout { display: none; }',
        '.bridge-mobile .sidebar-foot button, .bridge-mobile .sidebar-foot a { min-height: 40px; display: inline-flex; align-items: center; text-decoration: none; }',
        '.bridge-mobile .sidebar-foot > span { display: none; }',
        '.bridge-mobile #appearance-dialog #pushplus-settings { min-height: 44px; }',
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
      if (footer) {
        document.body.appendChild(footer);
        const home = document.createElement('a'); home.href = 'codexbridge://home'; home.className = 'plain'; home.textContent = '返回电脑列表';
        footer.appendChild(home);
      }
      const push = document.getElementById('pushplus-settings'), settings = document.getElementById('appearance-dialog');
      if (push && settings) {
        settings.insertBefore(push, settings.querySelector('.appearance-actions'));
        push.addEventListener('click', () => settings.close());
      }
    })();
    """
    private static let webClipboard = """
    (() => {
      if (window !== window.top) return;
      const nativePrompt = window.prompt.bind(window);
      const command = 'codexbridge-copy:__BRIDGE_CLIPBOARD_TOKEN__';
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
        gatewayMenu.configuration = menuStyle; gatewayMenu.accessibilityLabel = "电脑与通知"; gatewayMenu.showsMenuAsPrimaryAction = true
        gatewayMenu.menu = UIMenu(children: [
            UIAction(title: "返回电脑列表", image: UIImage(systemName: "desktopcomputer")) { [weak self] _ in self?.home() },
            UIAction(title: "在灵动岛显示此聊天", image: UIImage(systemName: "capsule")) { [weak self] _ in self?.startLiveActivity() },
            UIAction(title: "结束灵动岛显示", image: UIImage(systemName: "xmark.circle")) { _ in if #available(iOS 16.2, *) { Task { await LiveActivityController.shared.stop() } } },
            UIAction(title: "通知收件箱", image: UIImage(systemName: "bell")) { [weak self] _ in self?.inbox() },
            UIAction(title: "刷新页面", image: UIImage(systemName: "arrow.clockwise")) { [weak self] _ in self?.refresh() },
            UIAction(title: "外观与显示", image: UIImage(systemName: "paintpalette")) { [weak self] _ in self?.web?.evaluateJavaScript("document.querySelector('[data-open-appearance]')?.click()") },
            UIAction(title: "手机设置", image: UIImage(systemName: "gearshape")) { [weak self] _ in self?.settings() }
        ])
        gatewayMenu.translatesAutoresizingMaskIntoConstraints = false; view.addSubview(gatewayMenu)
        NSLayoutConstraint.activate([gatewayMenu.trailingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.trailingAnchor, constant: -8), gatewayMenu.widthAnchor.constraint(equalToConstant: 48), gatewayMenu.heightAnchor.constraint(equalToConstant: 48)])
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
    func info(_ text: String) { let a = UIAlertController(title: nil, message: text, preferredStyle: .alert); a.addAction(UIAlertAction(title: "好", style: .default)); present(a, animated: true) }
    private func label(_ text: String, size: CGFloat = 15, weight: UIFont.Weight = .regular, secondary: Bool = false) -> UILabel {
        let l = UILabel(); l.text = text; l.numberOfLines = 0
        l.font = UIFontMetrics.default.scaledFont(for: .systemFont(ofSize: size, weight: weight)); l.adjustsFontForContentSizeCategory = true
        l.textColor = secondary ? .secondaryLabel : .label; return l
    }
    private func button(_ title: String, symbol: String? = nil, primary: Bool = false, action: @escaping () -> Void) -> UIButton {
        let b = UIButton(type: .system); var c = primary ? UIButton.Configuration.filled() : .plain()
        c.title = title; c.baseForegroundColor = primary ? .systemBackground : .label; c.baseBackgroundColor = .label
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
    private func navigation(home: Bool) {
        navigationController?.setNavigationBarHidden(!home, animated: false)
        gatewayMenu.isHidden = home
        title = ""; navigationItem.largeTitleDisplayMode = .never
        let logo = UIImageView(image: UIImage(named: "AppIcon60x60@2x.png")); logo.contentMode = .scaleAspectFit
        logo.widthAnchor.constraint(equalToConstant: 30).isActive = true; logo.heightAnchor.constraint(equalToConstant: 30).isActive = true; logo.layer.cornerRadius = 7; logo.clipsToBounds = true
        let brand = UIStackView(arrangedSubviews: [logo, label("Codex Bridge", size: 18, weight: .semibold)]); brand.axis = .horizontal; brand.spacing = 8; brand.alignment = .center
        navigationItem.leftBarButtonItem = UIBarButtonItem(customView: brand)
        let settings = UIBarButtonItem(image: UIImage(systemName: "gearshape"), style: .plain, target: self, action: #selector(self.settings)); settings.accessibilityLabel = "设置"
        let inbox = UIBarButtonItem(image: UIImage(systemName: "bell"), style: .plain, target: self, action: #selector(self.inbox)); inbox.accessibilityLabel = "通知"
        navigationItem.rightBarButtonItems = [settings, inbox]
    }
    private func clear() { artifactDownload?.cancel { _ in }; artifactDownload = nil; downloadStarting = false; removeDownloadFile(); gatewayMenuTop?.isActive = false; gatewayMenuTop = nil; generation += 1; web?.stopLoading(); web?.navigationDelegate = nil; web?.uiDelegate = nil; web = nil; for v in page.arrangedSubviews where v !== subtitle { page.removeArrangedSubview(v); v.removeFromSuperview() } }
    @objc func home() {
        clear(); navigation(home: true); subtitle.isHidden = true
        let container = UIView(); page.addArrangedSubview(container); let content = scrollContent(in: container)
        let visual = UIImageView(image: UIImage(systemName: "desktopcomputer")); visual.tintColor = .label; visual.contentMode = .scaleAspectFit; visual.heightAnchor.constraint(equalToConstant: 54).isActive = true
        let hero = column(spacing: 14); hero.addArrangedSubview(visual)
        let heading = label("电脑上的工作，\n带在身边。", size: 30, weight: .semibold); heading.textAlignment = .center; hero.addArrangedSubview(heading)
        let hint = label("继续聊天、查看结果，让电脑替你运行。", secondary: true); hint.textAlignment = .center; hero.addArrangedSubview(hint)
        hero.setCustomSpacing(24, after: visual); content.addArrangedSubview(hero)
        content.addArrangedSubview(card([button("扫码连接电脑", symbol: "qrcode.viewfinder", primary: true) { [weak self] in self?.scan() }, button("输入网关地址", symbol: "link") { [weak self] in self?.manualAddress() }]))
        content.addArrangedSubview(label("你的电脑", size: 19, weight: .semibold))
        if saved.isEmpty {
            content.addArrangedSubview(card([label("还没有连接的电脑", size: 17, weight: .medium), label("在电脑网关中展开“扫码登录”，然后用上方按钮扫描。", secondary: true)]))
        } else {
            for address in saved {
                let row = button(URL(string: address)?.host ?? address, symbol: "desktopcomputer") { [weak self] in self?.openSaved(address) }
                row.contentHorizontalAlignment = .leading
                row.configuration?.subtitle = address; row.configuration?.titleAlignment = .leading
                row.configuration?.subtitleTextAttributesTransformer = UIConfigurationTextAttributesTransformer { a in var a = a; a.foregroundColor = .secondaryLabel; return a }
                content.addArrangedSubview(card([row]))
            }
        }
        let foot = label("外出使用 HTTPS 地址；局域网地址需要连接同一网络。", size: 13, secondary: true); foot.textAlignment = .center; content.addArrangedSubview(foot)
    }
    private func manualAddress() {
        let a = UIAlertController(title: "连接电脑", message: "粘贴电脑网关提供的访问地址", preferredStyle: .alert)
        a.addTextField { field in field.text = "https://"; field.keyboardType = .URL; field.autocapitalizationType = .none; field.autocorrectionType = .no; field.placeholder = "https://codex.try2love.com" }
        a.addAction(UIAlertAction(title: "取消", style: .cancel)); a.addAction(UIAlertAction(title: "继续", style: .default) { [weak self, weak a] _ in self?.choose(a?.textFields?.first?.text ?? "") }); present(a, animated: true)
    }
    func choose(_ value: String) {
        do { let url = try GatewayURL.connection(value); let address = try GatewayURL.origin(value)
            let alert = UIAlertController(title: "连接到这台电脑？", message: address + "\n请确认这是你自己的网关。", preferredStyle: .alert)
            alert.addAction(UIAlertAction(title: "取消", style: .cancel)); alert.addAction(UIAlertAction(title: "连接", style: .default) { _ in self.connect(url, address: address) }); present(alert, animated: true)
        } catch { info(error.localizedDescription) }
    }
    func openNotification(_ address: String, thread: String?, host: String) {
        guard saved.contains(address) else { info("请先连接通知对应的电脑。"); return }
        if let thread, let target = try? GatewayURL.chat(address, thread: thread, host: host) { connect(target, address: address) } else { openSaved(address) }
    }
    func openSaved(_ address: String) { guard saved.contains(address), let url = URL(string: address + "/") else { info("请先扫码保存通知对应的电脑。"); return }; connect(url, address: address) }
    private func connect(_ url: URL, address: String) {
        if origin != address, #available(iOS 16.2, *) { Task { await LiveActivityController.shared.stop() } }
        clear(); navigation(home: false); subtitle.isHidden = false; origin = address; var all = saved; if !all.contains(address) { all.append(address) }; defaults.set(all, forKey: "origins"); defaults.set(address, forKey: "active"); subtitle.text = "正在连接 · " + address
        let configuration = WKWebViewConfiguration(); configuration.websiteDataStore = .default(); configuration.applicationNameForUserAgent = "BridgeMobile/0.1-iOS"
        configuration.ignoresViewportScaleLimits = false
        configuration.userContentController.addUserScript(WKUserScript(source: Self.fixedViewport, injectionTime: .atDocumentEnd, forMainFrameOnly: true))
        configuration.userContentController.addUserScript(WKUserScript(source: Self.webAppearance, injectionTime: .atDocumentEnd, forMainFrameOnly: true))
        clipboardToken = UUID().uuidString
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
            let a = UIAlertController(title: "在浏览器打开外部链接？", message: url.host, preferredStyle: .alert); a.addAction(UIAlertAction(title: "取消", style: .cancel)); a.addAction(UIAlertAction(title: "打开", style: .default) { _ in UIApplication.shared.open(url) }); present(a, animated: true)
        }
    }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) { subtitle.isHidden = true; registerNativePush() }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) { subtitle.isHidden = false; subtitle.text = "连接失败，请检查电脑和地址后刷新" }
    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) { let a = UIAlertController(title: nil, message: message, preferredStyle: .alert); a.addAction(UIAlertAction(title: "好", style: .default) { _ in completionHandler() }); present(a, animated: true) }
    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) { let a = UIAlertController(title: nil, message: message, preferredStyle: .alert); a.addAction(UIAlertAction(title: "取消", style: .cancel) { _ in completionHandler(false) }); a.addAction(UIAlertAction(title: "确认", style: .default) { _ in completionHandler(true) }); present(a, animated: true) }
    func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String, defaultText: String?, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (String?) -> Void) {
        guard prompt == "codexbridge-copy:" + clipboardToken, webView === web,
              UIApplication.shared.applicationState == .active, frame.isMainFrame,
              let source = frame.request.url, GatewayURL.same(source, origin),
              let current = webView.url, GatewayURL.same(current, origin),
              let text = defaultText, text.utf16.count <= 262144 else { completionHandler(nil); return }
        UIPasteboard.general.string = text
        completionHandler("copied")
    }
    private func isArtifact(_ url: URL) -> Bool {
        GatewayURL.same(url, origin) && url.path.range(of: "^/api/sessions/[0-9a-f-]{36}/(?:files/[a-f0-9]{64}|workspace/download)$", options: .regularExpression) != nil
    }
    private func downloadArtifact(_ request: URLRequest) {
        guard !downloadStarting, artifactDownload == nil, downloadFile == nil else { info("已有下载进行中，请稍候"); return }
        guard let web, let url = request.url, isArtifact(url) else { return }
        let ticket = generation; downloadStarting = true; subtitle.text = "正在下载…"; subtitle.isHidden = false
        web.startDownload(using: request) { [weak self] download in
            guard let self, ticket == self.generation else { download.cancel { _ in }; return }
            self.downloadStarting = false; self.artifactDownload = download; download.delegate = self
        }
    }
    private func removeDownloadFile() {
        if let file = downloadFile { try? FileManager.default.removeItem(at: file.deletingLastPathComponent()) }
        downloadFile = nil
    }
    private func downloadError(_ message: String) {
        artifactDownload = nil; downloadStarting = false; removeDownloadFile(); subtitle.isHidden = true; info(message)
    }
    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        guard download === artifactDownload else { completionHandler(nil); return }
        guard let url = response.url, isArtifact(url),
              let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            completionHandler(nil); downloadError("下载失败，请检查登录状态后重试"); return
        }
        guard response.expectedContentLength <= 50 * 1024 * 1024 else { completionHandler(nil); downloadError("附件超过 50 MB 下载限制"); return }
        do {
            let folder = FileManager.default.temporaryDirectory.appendingPathComponent("bridge-download-" + UUID().uuidString, isDirectory: true)
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            let name = (suggestedFilename.replacingOccurrences(of: "\\", with: "/") as NSString).lastPathComponent
            let file = folder.appendingPathComponent(name.isEmpty || name == "." || name == ".." ? "download" : name)
            downloadFile = file; completionHandler(file)
        } catch { completionHandler(nil); downloadError("无法保存下载文件，请检查剩余空间") }
    }
    func download(_ download: WKDownload, willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest, decisionHandler: @escaping (WKDownload.RedirectPolicy) -> Void) {
        decisionHandler(.cancel)
        if download === artifactDownload { downloadError("下载地址发生跳转，已取消。请重新打开电脑附件。") }
    }
    func downloadDidFinish(_ download: WKDownload) {
        guard download === artifactDownload, let file = downloadFile else { return }
        artifactDownload = nil; subtitle.isHidden = true
        let picker = UIDocumentPickerViewController(forExporting: [file], asCopy: true); picker.delegate = self
        present(picker, animated: true)
    }
    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        guard download === artifactDownload else { return }; downloadError("下载失败，请检查网络后重试")
    }
    func documentPicker(_ controller: UIDocumentPickerViewController, didPickDocumentsAt urls: [URL]) { removeDownloadFile() }
    func documentPickerWasCancelled(_ controller: UIDocumentPickerViewController) { removeDownloadFile() }
    func openLink(_ url: URL) {
        guard let c = URLComponents(url: url, resolvingAgainstBaseURL: false), c.scheme == "codexbridge", c.host == "open" else { return }
        let q = c.queryItems ?? []; func get(_ key: String) -> String? { q.first(where: { $0.name == key })?.value }
        guard let address = get("origin"), saved.contains(address), let thread = get("thread"), let target = try? GatewayURL.chat(address, thread: thread, host: get("host") ?? "local") else { info("请先扫码保存通知对应的电脑，再打开聊天。"); return }
        connect(target, address: address)
    }
    private func fetch(_ done: @escaping (Result<[String: Any], Error>) -> Void) {
        let address = origin, ticket = generation
        guard let target = URL(string: address + "/api/mobile/events"), !address.isEmpty else { done(.failure(GatewayURL.InvalidURL())); return }
        WKWebsiteDataStore.default().httpCookieStore.getAllCookies { cookies in
            guard ticket == self.generation else { self.loading = false; return }
            let host = target.host ?? ""
            let matching = cookies.filter { cookie in cookie.name == "codex_mobile_session" && cookie.domain.trimmingCharacters(in: CharacterSet(charactersIn: ".")) == host && (!cookie.isSecure || target.scheme == "https") && (cookie.expiresDate == nil || cookie.expiresDate! > Date()) }
            guard !matching.isEmpty else { done(.failure(NSError(domain: "Bridge", code: 401, userInfo: [NSLocalizedDescriptionKey: "请先连接并登录电脑网关"]))); return }
            var request = URLRequest(url: target); request.setValue(address, forHTTPHeaderField: "Origin"); request.setValue(HTTPCookie.requestHeaderFields(with: matching)["Cookie"], forHTTPHeaderField: "Cookie")
            self.session.dataTask(with: request) { data, response, error in
                let code = (response as? HTTPURLResponse)?.statusCode
                let result: Result<[String: Any], Error>
                if let error { result = .failure(error) }
                else if code == 200, let data, data.count <= 512000, let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any] { result = .success(value) }
                else { result = .failure(NSError(domain: "Bridge", code: code ?? 0, userInfo: [NSLocalizedDescriptionKey: code == 401 || code == 403 ? "登录已失效，请重新连接电脑" : "请使用配套电脑 Preview，并检查连接"])) }
                DispatchQueue.main.async { if ticket == self.generation { done(result) } else { self.loading = false } }
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
            guard data["enabled"] as? Bool == true else { self.info("请在电脑 Preview 的手机通知中开启“手机 App 通知收件箱”并保存。"); return }
            let stream = data["streamId"] as? String ?? ""
            let clearKey = "cleared:" + self.origin + ":" + stream
            let events = (data["events"] as? [[String: Any]] ?? []).filter { ($0["sequence"] as? Int ?? 0) > self.defaults.integer(forKey: clearKey) }
            let address = self.origin
            let (sheet, content) = self.sheet("通知")
            if !events.isEmpty {
                content.addArrangedSubview(self.button("清空通知", symbol: "trash") { [weak self, weak sheet] in
                    let cursor = data["cursor"] as? Int ?? 0
                    self?.defaults.set(cursor, forKey: clearKey)
                    UNUserNotificationCenter.current().getDeliveredNotifications { notices in
                        let ids = notices.filter { ($0.request.content.userInfo["origin"] as? String) == address && (Int(String(describing: $0.request.content.userInfo["sequence"] ?? 0)) ?? 0) <= cursor }.map { $0.request.identifier }
                        UNUserNotificationCenter.current().removeDeliveredNotifications(withIdentifiers: ids)
                    }
                    sheet?.dismiss(animated: true) { self?.inbox() }
                })
            }
            content.addArrangedSubview(self.label("最近任务提醒", size: 24, weight: .semibold))
            if events.isEmpty {
                content.addArrangedSubview(self.card([self.label("暂无通知", size: 18, weight: .medium), self.label("在聊天中开启“提醒”，任务完成或需要你处理时，会记录在这里。", secondary: true)]))
            }
            let date = DateFormatter(); date.dateStyle = .short; date.timeStyle = .short
            for event in events.reversed() {
                var items: [UIView] = [self.label(event["title"] as? String ?? "任务提醒", size: 17, weight: .semibold)]
                if let body = event["body"] as? String, !body.isEmpty { items.append(self.label(body, secondary: true)) }
                if let timestamp = event["createdAt"] as? Double { items.append(self.label(date.string(from: Date(timeIntervalSince1970: timestamp)), size: 12, secondary: true)) }
                items.append(self.button("查看聊天", symbol: "arrow.up.right") { [weak self, weak sheet] in
                    sheet?.dismiss(animated: true) {
                        if let thread = event["threadId"] as? String, let host = event["host"] as? String, let url = try? GatewayURL.chat(address, thread: thread, host: host) { self?.connect(url, address: address) }
                    }
                })
                content.addArrangedSubview(self.card(items))
            }
        }
    } }
    @objc private func active() { activityTimer?.invalidate(); activityTimer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in self?.syncLiveActivity() }; syncLiveActivity(); timer?.invalidate(); timer = Timer.scheduledTimer(withTimeInterval: 20, repeats: true) { [weak self] _ in self?.sync() } }
    @objc private func inactive() { timer?.invalidate(); timer = nil; activityTimer?.invalidate(); activityTimer = nil; if #available(iOS 16.2, *) { Task { await LiveActivityController.shared.pause() } } }
    private func sync() { registerNativePush(); guard UIApplication.shared.applicationState == .active, !origin.isEmpty, web != nil, !loading else { return }; loading = true; fetch { result in self.loading = false; if case .success(let data) = result, data["enabled"] as? Bool == true { let cursor = data["cursor"] as? Int ?? 0, key = "cursor:" + self.origin; let previous = self.defaults.integer(forKey: key); if self.defaults.object(forKey: key) != nil, cursor > previous { self.gatewayMenu.accessibilityLabel = "更多，有新的任务提醒" }; self.defaults.set(cursor, forKey: key) } } }
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
        guard #available(iOS 16.2, *) else { info("实时活动需要 iOS 16.2 或更新版本。"); return }
        currentTask { [weak self] task in
            guard let self else { return }
            guard let task else { self.info("请先打开要跟踪的聊天，再选择灵动岛显示。"); return }
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
        let (sheet, content) = sheet("设置")
        content.addArrangedSubview(label("通知", size: 19, weight: .semibold))
        content.addArrangedSubview(card([
            label("锁屏任务提醒", size: 17, weight: .semibold),
            button("启用系统后台推送", symbol: "bell") { [weak self, weak sheet] in sheet?.dismiss(animated: true) { self?.enableNativePush() } },
            label("请在电脑端配置 Bark 等通知通道。当前 App 在打开时同步收件箱。", secondary: true),
            button("测试本机通知", symbol: "bell.badge") { [weak self, weak sheet] in sheet?.dismiss(animated: true) { self?.testNotification() } },
            label("10 秒后显示，用于检查手机的通知权限。", size: 13, secondary: true)
        ]))
        if #available(iOS 16.2, *) {
            content.addArrangedSubview(label("灵动岛与锁屏", size: 19, weight: .semibold))
            content.addArrangedSubview(card([
                label("跟踪当前聊天", size: 17, weight: .semibold),
                label("从电脑菜单选择聊天跟踪。后台显示可能不是最新状态，打开 App 可继续同步。", secondary: true),
                button("预览灵动岛", symbol: "capsule") { [weak self, weak sheet] in
                    sheet?.dismiss(animated: true) {
                        Task { do { try await LiveActivityController.shared.start(origin: "", thread: "", host: "local", phase: "running", demo: true) }
                            catch { self?.info(error.localizedDescription) } }
                    }
                },
                button("结束灵动岛显示", symbol: "xmark.circle") { Task { await LiveActivityController.shared.stop() } }
            ]))
        }
        content.addArrangedSubview(label("当前电脑", size: 19, weight: .semibold))
        if origin.isEmpty {
            content.addArrangedSubview(card([label("尚未选择电脑", size: 17), label("返回首页扫码或输入网关地址。", secondary: true)]))
        } else {
            content.addArrangedSubview(card([
                label(URL(string: origin)?.host ?? origin, size: 17, weight: .semibold), label(origin, size: 13, secondary: true),
                button("移除这台电脑", symbol: "trash") { [weak self, weak sheet] in sheet?.dismiss(animated: true) { self?.confirmRemove() } }
            ]))
        }
        let version = label("Bridge Preview · 0.1.0 (12)", size: 13, secondary: true); version.textAlignment = .center; content.addArrangedSubview(version)
    }
    private var pushSupported: Bool { Bundle.main.object(forInfoDictionaryKey: "BridgePushEnabled") as? Bool == true }
    private func enableNativePush() {
        guard pushSupported else { info("此预览尚未配置 Apple 推送签名。需要开发者账号和电脑端推送服务后才能启用后台任务提醒。"); return }
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge]) { allowed, _ in DispatchQueue.main.async {
            guard allowed else { self.info("请在系统设置中允许通知。"); return }
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
            guard allowed else { DispatchQueue.main.async { self.info("请在系统设置允许 Bridge Preview 通知。") }; return }
            DispatchQueue.main.async {
                let content = UNMutableNotificationContent(); content.title = "手机通知测试"; content.body = "本机通知已开启"; content.sound = .default
                if !self.origin.isEmpty { content.userInfo = ["origin": self.origin] }
                UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: "preview-test", content: content, trigger: UNTimeIntervalNotificationTrigger(timeInterval: 10, repeats: false))) { error in
                    DispatchQueue.main.async { self.info(error == nil ? "10 秒后显示本地测试通知，可以先锁屏。此测试不代表远程推送已经接通。" : "测试通知未能创建，请检查系统通知设置。") }
                }
            }
        }
    }
    private func revokeConnection(_ done: @escaping () -> Void) {
        guard let web, let url = web.url, GatewayURL.same(url, origin) else { done(); return }
        web.callAsyncJavaScript("const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), 5000); try { const a = await (await fetch('/api/auth', {signal:controller.signal})).json(); if (a.authenticated) await fetch('/api/logout', {method:'POST', signal:controller.signal, headers:{'Content-Type':'application/json', 'X-CSRF-Token':a.csrf}, body:'{}'}); } finally { clearTimeout(timer); }", arguments: [:], in: nil, in: .page) { _ in done() }
    }
    private func confirmRemove() {
        let a = UIAlertController(title: "移除这台电脑？", message: "清除本机保存的连接和登录状态，电脑上的聊天不受影响。", preferredStyle: .alert)
        a.addAction(UIAlertAction(title: "取消", style: .cancel))
        a.addAction(UIAlertAction(title: "移除", style: .destructive) { _ in
            self.revokeConnection {
            if #available(iOS 16.2, *) { Task { await LiveActivityController.shared.stop() } }
            let old = self.origin; self.defaults.set(self.saved.filter { $0 != old }, forKey: "origins"); self.defaults.removeObject(forKey: "active"); self.defaults.removeObject(forKey: "cursor:" + old)
            WKWebsiteDataStore.default().httpCookieStore.getAllCookies { cookies in for cookie in cookies where cookie.domain.trimmingCharacters(in: CharacterSet(charactersIn: ".")) == URL(string: old)?.host && cookie.name == "codex_mobile_session" { WKWebsiteDataStore.default().httpCookieStore.delete(cookie) } }; self.origin = ""; self.home()
            }
        }); present(a, animated: true)
    }
    private func scan() {
        AVCaptureDevice.requestAccess(for: .video) { allowed in DispatchQueue.main.async { guard allowed else { self.info("未获得相机权限，请粘贴网关地址。"); return }; let scanner = Scanner(); scanner.result = { [weak self] value in self?.dismiss(animated: true) { self?.choose(value) } }; self.present(scanner, animated: true) } }
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
        let close = UIButton(type: .system); close.setTitle("取消扫码", for: .normal); close.tintColor = .white; close.translatesAutoresizingMaskIntoConstraints = false; view.addSubview(close); close.addAction(UIAction { _ in self.dismiss(animated: true) }, for: .touchUpInside); NSLayoutConstraint.activate([close.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor, constant: 15), close.centerXAnchor.constraint(equalTo: view.centerXAnchor)])
        queue.async { self.capture.startRunning() }
    }
    override func viewDidLayoutSubviews() { super.viewDidLayoutSubviews(); layer?.frame = view.bounds }
    override func viewWillDisappear(_ animated: Bool) { queue.async { self.capture.stopRunning() }; super.viewWillDisappear(animated) }
    func metadataOutput(_ output: AVCaptureMetadataOutput, didOutput objects: [AVMetadataObject], from connection: AVCaptureConnection) { guard !found, let text = (objects.first as? AVMetadataMachineReadableCodeObject)?.stringValue else { return }; found = true; result?(text) }
}
