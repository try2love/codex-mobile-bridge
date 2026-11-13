import Network
import UIKit
import PhotosUI
@preconcurrency import WebKit
import AVFoundation
import UserNotifications
import UniformTypeIdentifiers
import Security

private enum DeviceBinding {
    struct Credential: Codable { let name: String; let token: String; var cookie: String { name + "=" + token } }
    static func valid(_ name: String) -> Bool { name == "codex_mobile_session" || name.range(of: #"^codex_mobile_session_[a-f0-9]{24}$"#, options: .regularExpression) != nil }
    static func query(_ address: String) -> [String: Any] { [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: "codex-bridge-device-binding-v1", kSecAttrAccount as String: address] }
    static func load(_ address: String) -> Credential? {
        var request = query(address); request[kSecReturnData as String] = true; request[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?; guard SecItemCopyMatching(request as CFDictionary, &item) == errSecSuccess,
            let data = item as? Data, let value = try? JSONDecoder().decode(Credential.self, from: data), valid(value.name),
            value.token.range(of: #"^[A-Za-z0-9_-]{32,128}$"#, options: .regularExpression) != nil else { return nil }
        return value
    }
    @discardableResult static func save(_ address: String, _ credential: Credential) -> Bool {
        guard valid(credential.name), credential.token.range(of: #"^[A-Za-z0-9_-]{32,128}$"#, options: .regularExpression) != nil,
              let data = try? JSONEncoder().encode(credential) else { return false }
        var request = query(address); request[kSecValueData as String] = data
        request[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        let status = SecItemAdd(request as CFDictionary, nil)
        if status == errSecDuplicateItem { return SecItemUpdate(query(address) as CFDictionary, [kSecValueData as String: data] as CFDictionary) == errSecSuccess }
        return status == errSecSuccess
    }
    static func clear(_ address: String) { SecItemDelete(query(address) as CFDictionary) }
    /// nil is a transport error; false is a server-confirmed rejected binding.
    static func verify(_ address: String, _ credential: Credential) async -> Bool? {
        guard let url = URL(string: address + "/api/auth") else { return false }
        let config = URLSessionConfiguration.ephemeral; config.httpShouldSetCookies = false; config.httpCookieStorage = nil; config.urlCredentialStorage = nil
        config.timeoutIntervalForRequest = 4; config.timeoutIntervalForResource = 5
        let session = URLSession(configuration: config, delegate: NoRedirect(), delegateQueue: nil)
        defer { session.invalidateAndCancel() }
        var request = URLRequest(url: url); request.setValue(credential.cookie, forHTTPHeaderField: "Cookie")
        request.setValue(GatewayURL.httpOrigin(address), forHTTPHeaderField: "Origin"); request.setValue("BridgeMobile/0.1-iOS", forHTTPHeaderField: "User-Agent")
        do {
            let (bytes, response) = try await session.bytes(for: request)
            guard let http = response as? HTTPURLResponse, http.statusCode == 200, http.url.map({ GatewayURL.same($0, address) }) == true, response.expectedContentLength <= 65536 else { return nil }
            var body = Data(); for try await byte in bytes { guard body.count < 65536 else { return nil }; body.append(byte) }
            guard let auth = try JSONSerialization.jsonObject(with: body) as? [String: Any] else { return nil }
            guard auth["authenticated"] as? Bool == true else { return false }
            if let name = auth["cookieName"] as? String { return name == credential.name }
            return credential.name == "codex_mobile_session"
        } catch { return nil }
    }
}

final class BridgeNavigationController: UINavigationController {
    override var childForStatusBarStyle: UIViewController? { topViewController }
}

@main
final class AppDelegate: UIResponder, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    let controller = BridgeController()
    private var pendingNotification: UNNotificationResponse?
    private var lastNotificationRoute: (response: UNNotificationResponse, fromScene: Bool)?
    func application(_ application: UIApplication, didFinishLaunchingWithOptions options: [UIApplication.LaunchOptionsKey: Any]?) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        return true
    }
    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        UserDefaults.standard.set(deviceToken.map { String(format: "%02x", $0) }.joined(), forKey: "apnsToken")
        controller.registerNativePush()
    }
    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: Error) { controller.info(MobileStrings.text("系统推送注册失败，请检查推送签名与网络。")) }
    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse, withCompletionHandler completion: @escaping () -> Void) {
        DispatchQueue.main.async { self.receiveNotification(response, fromScene: false) }
        completion()
    }
    func receiveNotification(_ response: UNNotificationResponse, fromScene: Bool) {
        if let last = lastNotificationRoute,
           last.response === response, last.fromScene != fromScene {
            lastNotificationRoute = nil
            return
        }
        lastNotificationRoute = (response, fromScene)
        pendingNotification = response
        openPendingNotification()
    }
    func openPendingNotification() {
        guard controller.viewIfLoaded?.window != nil, let response = pendingNotification else { return }
        pendingNotification = nil
        let data = response.notification.request.content.userInfo
        if let origin = data["origin"] as? String { controller.openNotification(origin, thread: data["thread"] as? String, host: data["host"] as? String ?? "local") }
    }
    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification, withCompletionHandler completion: @escaping (UNNotificationPresentationOptions) -> Void) {
        if let address = notification.request.content.userInfo["origin"] as? String, !controller.notificationSourceEnabled(address) { completion([]); return }
        completion([.banner, .sound])
    }
}

final class SceneDelegate: UIResponder, UIWindowSceneDelegate {
    var window: UIWindow?
    func scene(_ scene: UIScene, willConnectTo session: UISceneSession, options connectionOptions: UIScene.ConnectionOptions) {
        guard let windowScene = scene as? UIWindowScene,
              let app = UIApplication.shared.delegate as? AppDelegate else { return }
        let window = UIWindow(windowScene: windowScene)
        window.rootViewController = BridgeNavigationController(rootViewController: app.controller)
        self.window = window
        window.overrideUserInterfaceStyle = app.controller.themeStyle
        window.makeKeyAndVisible()
        DispatchQueue.main.async {
            if let response = connectionOptions.notificationResponse { app.receiveNotification(response, fromScene: true) }
            app.openPendingNotification()
            if let url = connectionOptions.urlContexts.first?.url { app.controller.openLink(url) }
        }
    }
    // A cold-launch tap can arrive before the first layout attaches the
    // controller; activation is the next point where its window is ready.
    func sceneDidBecomeActive(_ scene: UIScene) {
        (UIApplication.shared.delegate as? AppDelegate)?.openPendingNotification()
    }
    func scene(_ scene: UIScene, openURLContexts URLContexts: Set<UIOpenURLContext>) {
        guard let url = URLContexts.first?.url,
              let app = UIApplication.shared.delegate as? AppDelegate else { return }
        app.controller.openLink(url)
    }
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
        let online = (value?["relay"] as? Bool != true || value?["online"] as? Bool == true) && error == nil && (task.response as? HTTPURLResponse)?.statusCode == 200 && value?["authenticated"] is Bool && value?["passwordless"] is Bool && !(value?["instanceId"] as? String ?? "").isEmpty
        session.finishTasksAndInvalidate()
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }; self.completion?(online); self.completion = nil; self.session = nil
        }
    }
}

final class BridgeController: UIViewController, WKNavigationDelegate, WKUIDelegate, UIDocumentPickerDelegate, UIDocumentInteractionControllerDelegate, PHPickerViewControllerDelegate, UIAdaptivePresentationControllerDelegate {
    private var web: WKWebView?
    private var nativeMainNavigation: (web: WKWebView, generation: Int, url: String)?
    private var origin = ""
    private var channelCatalogTimer:Timer?
    private var channelMonitor: NWPathMonitor?
    private var channelTicket = 0
    private var channelCookieOwners=[String:UUID]()
    private var channelSignature = ""
    private var channelWiFi = false
    private func watchChannels() {
        guard channelMonitor == nil else { return }
        channelCatalogTimer=Timer.scheduledTimer(withTimeInterval:30,repeats:true){[weak self] _ in
            guard let self,self.web != nil else{return};self.checkChannels(failure:false,reason:"tick")
        }
        let monitor = NWPathMonitor()
        channelMonitor = monitor
        monitor.pathUpdateHandler = { [weak self, weak monitor] path in
            guard let self, self.channelMonitor === monitor else { return }
            let signature=path.availableInterfaces.filter{path.usesInterfaceType($0.type)}.map{$0.name}.sorted().joined(separator:"|")
            guard signature != self.channelSignature else{return}
            self.channelSignature=signature
            self.channelWiFi = path.status == .satisfied && (path.usesInterfaceType(.wifi) || path.usesInterfaceType(.wiredEthernet))
            if self.web==nil{self.checkComputers()}else{self.checkChannels(failure:false,reason:"network")}
        }
        monitor.start(queue: .main)
    }
    private func channelRecord(_ address: String) -> [String: Any]? {
        defaults.dictionary(forKey: "channels:" + address)
    }
    private var lastChannelProbe: (key: String, time: TimeInterval)?
    private func clearChannels(_ address: String) {
        uu.remove(channelRecord(address)?["deviceId"] as? String ?? UURemote.device(cookieName(address)))
        defaults.removeObject(forKey:preferenceKey("disabled-channels",address));defaults.removeObject(forKey:preferenceKey("channel-mode",address))
        guard let id=channelRecord(address)?["deviceId"] as? String else { return }
        channelTicket += 1; lastChannelProbe=nil
        DeviceBinding.clear(GatewayChannels.vault(id));defaults.removeObject(forKey:"name:"+id)
        defaults.removeObject(forKey:"channel-anchor:"+id)
        let store=WKWebsiteDataStore.default().httpCookieStore
        for key in defaults.dictionaryRepresentation().keys where key.hasPrefix("channels:") {
            let alias=String(key.dropFirst(9))
            guard channelRecord(alias)?["deviceId"] as? String == id else { continue }
            let name=defaults.string(forKey:"cookieName:"+alias), host=URL(string:alias)?.host
            DeviceBinding.clear(alias); defaults.removeObject(forKey:key); defaults.removeObject(forKey:"connection:"+alias)
            store.getAllCookies { cookies in
                for cookie in cookies where cookie.domain == host && cookie.name == name { store.delete(cookie) }
            }
        }
    }
    private func activeChannel(_ address: String) -> String {
        guard let id = channelRecord(address)?["deviceId"] as? String else { return address }
        return saved.first { channelRecord($0)?["deviceId"] as? String == id } ?? address
    }
    private func gatewayIdentity(_ address:String)->String? {
        if let id=channelRecord(address)?["deviceId"] as? String{return id}
        guard let binding=DeviceBinding.load(address),binding.name.range(of:"^codex_mobile_session_[a-f0-9]{24}$",options:.regularExpression) != nil else{return nil}
        return String(ConnectionDiscovery.hash(binding.name).prefix(32))
    }
    private func enrollChannels(_ auth: [String: Any], address: String, cookie: HTTPCookie, ticket: Int) {
        guard auth["authenticated"] as? Bool == true,auth["trustedDevice"] as? Bool == true,var d=auth["mobileRoutes"] as? [String:Any],
              let id=d["deviceId"] as? String,id.range(of:"^[a-f0-9]{32}$",options:.regularExpression) != nil,
              GatewayChannels.endpoints(d).contains(address) else{return}
        let vault=GatewayChannels.vault(id),previous=DeviceBinding.load(vault),revision=bindingRevision
        var anchor=defaults.string(forKey:"channel-anchor:"+id) ?? address
        Task { @MainActor in
            var root=previous
            if root == nil || root?.name != cookie.name || GatewayChannels.token(root!.token,anchor:anchor,target:address) != cookie.value {
                // Reauthentication may rotate an existing route, never let a new origin claim an old identity.
                if previous != nil && address != anchor {
                    let known=defaults.dictionaryRepresentation().contains{key,value in
                        guard key.hasPrefix("channels:"),let prior=value as? [String:Any],prior["deviceId"] as? String==id else{return false}
                        return GatewayChannels.endpoints(prior).contains(address)
                    }
                    guard known else{return}
                }
                guard !cookie.value.hasPrefix("cmbroute1_"),await GatewayChannels.proves(id,target:address,token:cookie.value) else{return}
                root = .init(name:cookie.name,token:cookie.value);anchor=address
            }
            let current:HTTPCookie?=await withCheckedContinuation{continuation in self.binding(address){continuation.resume(returning:$0)}}
            guard ticket==generation,revision==bindingRevision,origin==address,saved.contains(address),current?.name==cookie.name,current?.value==cookie.value,
                  DeviceBinding.load(vault)?.token==previous?.token,let root else{return}
            let replaced=root.token != previous?.token || root.name != previous?.name
            if replaced && !DeviceBinding.save(vault,root){return}
            for other in saved.filter({$0 != address})+[address] where other==address || gatewayIdentity(other)==id {
                for key in ["name:","channel-mode:","disabled-channels:"] where defaults.object(forKey:key+id)==nil {
                    if let value=defaults.object(forKey:key+other){defaults.set(value,forKey:key+id)}
                }
            }
            d["anchor"]=anchor;defaults.set(anchor,forKey:"channel-anchor:"+id)
            defaults.set(d,forKey:"channels:"+address)
            defaults.set(saved.filter{$0==address || gatewayIdentity($0) != id},forKey:"origins")
            if replaced{manifestReads.removeValue(forKey:id);cancelSync()}
            web?.evaluateJavaScript("window.BridgeMobileChannels?.bound()",completionHandler:nil)
        }
    }
    private var routeTimes=[String:[String:Int]]()
    private var routeTraffic=[String:TimeInterval]()
    private var addressLists=[String:UIStackView]()
    private var computerTitles=[String:UIButton]()
    private var manifestReads=[String:TimeInterval]()
    private var arrivingVia:String?
    private var deferredRoute:(()->Void)?
    private func preferenceKey(_ key:String,_ address:String)->String {key+":"+((channelRecord(address)?["deviceId"] as? String) ?? address)}
    private func selectionId(_ url:String)->String {url.hasPrefix("https://") && url.hasSuffix(".trycloudflare.com") ? "quick-tunnel":url}
    private func channelEnabled(_ address:String)->Bool {!(defaults.stringArray(forKey:preferenceKey("disabled-channels",address)) ?? []).contains(selectionId(address))}
    private func addresses(_ address:String)->[String] {
        let disabled=Set(defaults.stringArray(forKey:preferenceKey("disabled-channels",address)) ?? [])
        return catalog(address).filter{!disabled.contains(selectionId($0))}
    }
    private func manageComputer(_ address:String){
        let key=preferenceKey("disabled-channels",address), (sheet,content)=sheet(MobileStrings.text("管理这台电脑"))
        var disabled=Set(defaults.stringArray(forKey:key) ?? [])
        for url in catalog(address){
            let id=selectionId(url)
            content.addArrangedSubview(sourceSwitch(label(url),name:url,on:!disabled.contains(id)){enabled in
                if enabled {disabled.remove(id)}else{disabled.insert(id)}
            })
        }
        guard let controller=sheet.topViewController else{return}
        controller.navigationItem.rightBarButtonItem=UIBarButtonItem(systemItem:.cancel,primaryAction:UIAction{[weak sheet] _ in sheet?.dismiss(animated:true)})
        let remove=button(MobileStrings.text("移除此电脑")){[weak self,weak sheet] in sheet?.dismiss(animated:true){self?.confirmRemove(address)}}
        remove.configuration?.baseForegroundColor = .systemRed
        let save=button(MobileStrings.text("保存"),primary:true){[weak self,weak sheet] in
            self?.cancelSync();self?.defaults.set(Array(disabled),forKey:key);self?.paintAddresses(address);self?.checkComputers();sheet?.dismiss(animated:true)
        }
        for control in [remove,save]{control.configuration?.contentInsets=NSDirectionalEdgeInsets(top:6,leading:14,bottom:6,trailing:14)}
        controller.toolbarItems=[UIBarButtonItem(customView:remove),UIBarButtonItem.flexibleSpace(),UIBarButtonItem(customView:save)];sheet.setToolbarHidden(false,animated:false)
    }
    private func catalog(_ address:String)->[String] {
        channelRecord(address).map(GatewayChannels.endpoints) ?? [address]
    }
    private func paintAddresses(_ address:String) {
        guard let list=addressLists[address] else{return}
        let id=channelRecord(address)?["deviceId"] as? String ?? "", urls=addresses(address)
        if list.arrangedSubviews.map({$0.accessibilityIdentifier ?? ""})==urls {
            for line in list.arrangedSubviews {
                guard let url=line.accessibilityIdentifier,let dot=(line as? UIStackView)?.arrangedSubviews.first as? UILabel else{continue}
                let ok=(routeTimes[id]?[url] ?? -1)>=0;dot.text=ok ? "●":"○";dot.textColor=ok ? .systemGreen:.secondaryLabel
            }
            return
        }
        list.arrangedSubviews.forEach{$0.removeFromSuperview()}
        for url in urls {
            let online=(routeTimes[id]?[url] ?? -1)>=0
            let dot=label(online ? "●" : "○",size:14);dot.textColor=online ? .systemGreen : .secondaryLabel
            dot.setContentHuggingPriority(.required,for:.horizontal)
            let line=UIStackView(arrangedSubviews:[dot,label(url,size:13,secondary:true)]);line.accessibilityIdentifier=url;line.axis = .horizontal;line.spacing=7;list.addArrangedSubview(line)
            if id.isEmpty {computerStates[address]=dot}
        }
    }
    private var inspectionGeneration:Int?
    private func channelMenu()->String {
        guard let id=channelRecord(origin)?["deviceId"] as? String else{return "{}"}
        let rows:[[String:Any]]=addresses(origin).map{["url":$0,"ms":routeTimes[id]?[$0] ?? -1]}
        let value:[String:Any]=["localNetwork":channelWiFi,"probing":inspectionGeneration==generation,"current":origin,"rows":rows,"mode":defaults.string(forKey:preferenceKey("channel-mode",origin)) ?? "auto"]
        return (try? JSONSerialization.data(withJSONObject:value)).flatMap{String(data:$0,encoding:.utf8)} ?? "{}"
    }
    private func routeNotice(_ text:String){
        let note=label(text,size:14);note.textColor = .white;note.backgroundColor=UIColor.black.withAlphaComponent(0.83)
        note.textAlignment = .center;note.numberOfLines=2;note.layer.cornerRadius=10;note.clipsToBounds=true
        view.addSubview(note);note.translatesAutoresizingMaskIntoConstraints=false
        NSLayoutConstraint.activate([note.centerXAnchor.constraint(equalTo:view.centerXAnchor),note.widthAnchor.constraint(lessThanOrEqualTo:view.widthAnchor,multiplier:0.94),note.bottomAnchor.constraint(equalTo:view.safeAreaLayoutGuide.bottomAnchor,constant:-18),note.heightAnchor.constraint(greaterThanOrEqualToConstant:40)])
        DispatchQueue.main.asyncAfter(deadline:.now()+2.5){note.removeFromSuperview()}
    }
    private func refreshRoutes(_ address:String,_ d:[String:Any],via:String,root:DeviceBinding.Credential){
        guard let id=d["deviceId"] as? String else{return}
        let now=ProcessInfo.processInfo.systemUptime
        guard now-(manifestReads[id] ?? -90)>=90 else{return}
        manifestReads[id]=now
        Task { @MainActor in
            guard let updated=try? await GatewayChannels.refresh(d,reached:via,name:root.name,root:root.token) else{return}
            let active=activeChannel(address)
            guard saved.contains(active),channelRecord(active)?["deviceId"] as? String==id,
                  DeviceBinding.load(GatewayChannels.vault(id))?.token==root.token else{return}
            guard !(NSDictionary(dictionary:updated).isEqual(to:channelRecord(active) ?? [:])) else{return}
            defaults.set(updated,forKey:"channels:"+active);paintAddresses(active)
            computerTitles[active]?.setTitle(computerName(active),for:.normal)
            if active==origin {web?.evaluateJavaScript("window.dispatchEvent(new Event('bridge-channel-sample'))",completionHandler:nil)}
        }
    }
    private var channelJobs=[UUID:Task<Void,Never>]()
    private func cancelChannelJobs(){inspectionGeneration=nil;channelJobs.values.forEach{$0.cancel()};channelJobs.removeAll()}
    private func sweep(_ address:String,_ d:[String:Any],targets:[String],current:@escaping ()->Bool,
                       sample:@escaping (String,Int?,Bool)->Void,exhausted:@escaping ()->Void,finished:@escaping ()->Void={}){
        guard let id=d["deviceId"] as? String,let root=DeviceBinding.load(GatewayChannels.vault(id)) else{exhausted();finished();return}
        let job=UUID()
        channelJobs[job]=Task { @MainActor in
            defer{channelJobs.removeValue(forKey:job);finished()}
            var any=false
            await GatewayChannels.probe(d,root:root.token,targets:targets){ url,ms in
                guard current(),DeviceBinding.load(GatewayChannels.vault(id))?.cookie==root.cookie,
                      self.channelRecord(address)?["anchor"] as? String==d["anchor"] as? String else{return}
                let result=ms ?? (self.pageReady && url==self.origin && ProcessInfo.processInfo.systemUptime-(self.routeTraffic[url] ?? -30)<30 ? self.routeTimes[id]?[url]:nil)
                self.routeTimes[id,default:[:]][url]=result ?? -1
                let first=ms != nil && !any
                if first {any=true;self.refreshRoutes(address,d,via:url,root:root)}
                let rule=(d["race"] as? [String:Any])?["select"] as? String ?? "firstVerified"
                sample(url,ms,first && rule=="firstVerified")
            }
            if current() && !any {exhausted()}
        }
    }
    @discardableResult private func checkChannels(failure:Bool,forced:String?=nil,inspect:Bool=false,reason:String="signal")->Bool{
        guard channelRecord(origin) != nil,let web else{return false}
        if forced != nil || inspect {return raceChannels(failure:failure,forced:forced,inspect:inspect)}
        if pageReady {web.evaluateJavaScript("window.BridgeChannelLink?.check('\(reason)')",completionHandler:nil)}
        else if failure,defaults.string(forKey:preferenceKey("channel-mode",origin)) != "manual" {
            let urls=addresses(origin).filter{$0 != origin && (origin.hasPrefix("http://") || (channelWiFi && $0.hasPrefix("http://")))}
            if !urls.isEmpty{_ = raceChannels(failure:true,allowed:urls)}
        }
        return true
    }
    private func channelCommand(_ cmd:String)->String? {
        guard let d=channelRecord(origin),let page=web else{return nil}
        let address=origin,key=preferenceKey("channel-mode",address)
        if ["mode:auto","mode:manual"].contains(cmd){channelTicket+=1;defaults.set(String(cmd.dropFirst(5)),forKey:key);return channelMenu()}
        if cmd.hasPrefix("verify:"),let id=Int(cmd.dropFirst(7)),id>0 {
            func valid()->Bool {web===page && origin==address && UIApplication.shared.applicationState == .active}
            func result(_ ok:Bool){page.evaluateJavaScript("window.BridgeChannelLink?.result(\(id),\(ok))",completionHandler:nil)}
            sweep(address,d,targets:[address],current:valid,sample:{_,ms,_ in if ms != nil{result(true)}},exhausted:{result(false)})
            return "started"
        }
        if cmd.hasPrefix("traffic:"),let ms=Int(cmd.dropFirst(8)),(0...120000).contains(ms),let id=d["deviceId"] as? String {
            routeTraffic[address]=ProcessInfo.processInfo.systemUptime;routeTimes[id,default:[:]][address]=ms;return "ok"
        }
        if cmd=="unreachable"{channelUnavailable();return "ok"}
        if cmd.hasPrefix("prefer:") || cmd.hasPrefix("outage:") || cmd.hasPrefix("retry:"){
            if defaults.string(forKey:key) != "manual",cmd.utf8.count<32768,let split=cmd.firstIndex(of:":"),
               let urls=(try? JSONSerialization.jsonObject(with:Data(cmd[cmd.index(after:split)...].utf8))) as? [String] {
                _ = raceChannels(failure:!cmd.hasPrefix("prefer:"),allowed:urls,notifyFailure:cmd.hasPrefix("outage:"))
            }
            return "ok"
        }
        if let peer=pendingPeer {
            if cmd=="failed:"+peer.nonce {rollbackPeer();routeNotice(MobileStrings.text("信道暂不可达"));return "ok"}
        }
        return nil
    }
    private func channelUnavailable(){routeNotice(MobileStrings.text("当前信道不可达，请手动切换连接信道"))}
    @discardableResult private func raceChannels(failure:Bool,forced:String?=nil,inspect:Bool=false,allowed:[String]?=nil,notifyFailure:Bool=false)->Bool{
        let address=origin
        guard let view=web, UIApplication.shared.applicationState == .active,let d=channelRecord(address) else{return false}
        let targets=addresses(address).filter{allowed?.contains($0) ?? true}
        guard !targets.isEmpty else{if notifyFailure{channelUnavailable()};return false}
        if let forced,!targets.contains(forced){return false}
        if inspect,inspectionGeneration==generation{return true}
        if inspect{inspectionGeneration=generation}
        let now=ProcessInfo.processInfo.systemUptime,key=address+"|"+String(channelWiFi)
        if !inspect,!notifyFailure,forced==nil,let last=lastChannelProbe,last.key==key,now-last.time<3 {return true}
        if !inspect {lastChannelProbe=(key,now);channelTicket+=1}
        let ticket=channelTicket,screen=generation
        func current()->Bool { channelTicket==ticket && generation==screen && web===view && saved.contains(address) && UIApplication.shared.applicationState == .active }
        sweep(address,d,targets:forced.map{[$0]} ?? targets,current:current,sample:{url,ms,select in
            view.evaluateJavaScript("window.dispatchEvent(new Event('bridge-channel-sample'))",completionHandler:nil)
            guard !inspect,select else{return}
            if url==address,self.pageReady {view.evaluateJavaScript("window.BridgeChannelLink?.check()",completionHandler:nil)}
            if url != address,let requested=view.url ?? self.requestedRoute {self.moveChannel(address,d,to:url,requested:requested,view:view,failure:failure,current:current)}
        },exhausted:{if notifyFailure{self.channelUnavailable()}else if forced != nil {self.routeNotice(MobileStrings.text("信道暂不可达"))}},finished:{
            if inspect,self.inspectionGeneration==screen {self.inspectionGeneration=nil;view.evaluateJavaScript("window.dispatchEvent(new Event('bridge-channel-sample'))",completionHandler:nil)}
        })
        return true
    }
    private func openComputer(_ address:String,_ url:URL){
        guard !addresses(address).isEmpty else{manageComputer(address);return}
        guard let d=channelRecord(address) else{connect(url,address:address);return}
        clear();navigation(home:false);origin=address;documentAddress=address;requestedRoute=url;subtitle.isHidden=false
        pendingEntry=(address,url,UIApplication.shared.applicationState != .active)
        let screen=generation,targets=addresses(address)
        var progress=[String:String]()
        subtitle.isHidden=true
        let container=UIView(),panel=column(),title=label(MobileStrings.text("正在连接 · ")+computerName(address),size:20,weight:.semibold)
        let rows=column(spacing:8);panel.addArrangedSubview(title);panel.addArrangedSubview(rows)
        entryProgress=container;page.addArrangedSubview(container);container.addSubview(panel);panel.translatesAutoresizingMaskIntoConstraints=false
        NSLayoutConstraint.activate([panel.centerYAnchor.constraint(equalTo:container.centerYAnchor),panel.leadingAnchor.constraint(equalTo:container.leadingAnchor,constant:24),panel.trailingAnchor.constraint(equalTo:container.trailingAnchor,constant:-24)])
        var labels=[String:UILabel]()
        for url in targets{
            let time=label("",size:14,secondary:true);time.setContentCompressionResistancePriority(.required,for:.horizontal)
            let row=UIStackView(arrangedSubviews:[label(url,size:14),time]);row.spacing=10;row.alignment = .center;rows.addArrangedSubview(row);labels[url]=time
        }
        func render(){for url in targets{labels[url]?.text=progress[url] ?? MobileStrings.text("连接中…")}}
        func current()->Bool{generation==screen && web==nil && connectionError==nil && UIApplication.shared.applicationState == .active}
        let deadline=DispatchWorkItem{[weak self] in
            guard let self,self.generation==screen,self.web==nil,self.connectionError==nil,
                  UIApplication.shared.applicationState == .active else{return}
            self.cancelChannelJobs();self.showConnectionError(status:nil)
        }
        connectionDeadline=deadline;DispatchQueue.main.asyncAfter(deadline:.now()+10,execute:deadline)
        render()
        sweep(address,d,targets:targets,current:current,sample:{next,ms,select in
            progress[next]=ms.map{"\($0) ms"} ?? "○";render()
            guard select else{return}
            if next==address{self.connect(url,address:address)}else{self.moveChannel(address,d,to:next,requested:url,view:nil,failure:false,current:current)}
        },exhausted:{if targets.contains(address){self.connect(url,address:address)}else{self.routeNotice(MobileStrings.text("信道暂不可达"))}})
    }
    @discardableResult
    private func moveChannel(_ address:String,_ d:[String:Any],to next:String,requested:URL,view:WKWebView?,failure:Bool,current:@escaping ()->Bool)->Task<Void,Never>?{
        let document=view == nil ? address:documentAddress
        guard current(),GatewayURL.same(requested,document),let url=URL(string:next+requested.absoluteString.dropFirst(document.count)),
              let id=d["deviceId"] as? String,let anchor=d["anchor"] as? String,let root=DeviceBinding.load(GatewayChannels.vault(id)),
              let host=URL(string:next)?.host,cookieName(address)==root.name else{return nil}
        let ticket=channelTicket,revision=bindingRevision
        func rooted()->Bool {
            let live=DeviceBinding.load(GatewayChannels.vault(id)),record=channelRecord(address)
            return bindingRevision==revision && live?.cookie==root.cookie && record?["deviceId"] as? String==id &&
                record?["anchor"] as? String==anchor && (defaults.string(forKey:"channel-anchor:"+id) ?? anchor)==anchor
        }
        func valid()->Bool {current() && channelTicket==ticket && rooted() && addresses(address).contains(next)}
        return Task { @MainActor in
            let token=GatewayChannels.token(root.token,anchor:anchor,target:next),store=WKWebsiteDataStore.default().httpCookieStore
            var properties:[HTTPCookiePropertyKey:Any]=[.name:root.name,.value:token,.domain:host,.path:"/",.expires:Date().addingTimeInterval(34560000),HTTPCookiePropertyKey("HttpOnly"):"TRUE",HTTPCookiePropertyKey("SameSite"):"Strict"]
            if next.hasPrefix("https://"){properties[.secure]="TRUE"}
            guard let cookie=HTTPCookie(properties:properties) else{return}
            func slot(_ value:HTTPCookie)->Bool {
                value.name==cookie.name && value.path==cookie.path &&
                    value.domain.trimmingCharacters(in:CharacterSet(charactersIn:"."))==host
            }
            func same(_ first:HTTPCookie?,_ second:HTTPCookie?)->Bool {
                first?.name==second?.name && first?.value==second?.value && first?.path==second?.path &&
                    first?.domain.trimmingCharacters(in:CharacterSet(charactersIn:"."))==second?.domain.trimmingCharacters(in:CharacterSet(charactersIn:".")) && first?.isSecure==second?.isSecure
            }
            let cookies=await store.allCookies(),source=cookies.first(where:{cookieMatches($0,address:address)})
            guard valid(),view==nil || source != nil else{return}
            let before=cookies.filter(slot),retained=DeviceBinding.load(next),owner=UUID()
            let targetKey=root.name+"\n"+host+"\n/",sourceKey=root.name+"\n"+(URL(string:address)?.host ?? "")+"\n"+GatewayURL.cookiePath(address)
            @MainActor func release(){for key in [targetKey,sourceKey] where channelCookieOwners[key]==owner{channelCookieOwners.removeValue(forKey:key)}}
            @MainActor func owned()->Bool{channelCookieOwners[targetKey]==owner && channelCookieOwners[sourceKey]==owner}
            let aliases=Set(GatewayChannels.endpoints(d)+[address,next,anchor])
            let known=Set(aliases.map{GatewayChannels.token(root.token,anchor:anchor,target:$0)} +
                aliases.compactMap{alias -> String? in
                    guard gatewayIdentity(alias)==id,let saved=DeviceBinding.load(alias),saved.name==root.name else{return nil}
                    return saved.token
                })
            if (gatewayIdentity(next) != nil && gatewayIdentity(next) != id) ||
                (retained != nil && retained?.name != root.name) || before.count>1 ||
                before.contains(where:{!known.contains($0.value)}) || source.map({!known.contains($0.value)})==true {
                if view==nil,addresses(address).contains(address) {connect(requested,address:address)}
                routeNotice(MobileStrings.text("此地址的登录绑定已变化，请重新验证"));return
            }
            let shared=URL(string:address)?.host==host && GatewayURL.cookiePath(address)==cookie.path
            let expectedSource=shared ? (cookieMatches(cookie,address:address) ? cookie:nil):source
            @MainActor func written(_ values:[HTTPCookie])->Bool {
                let target=values.filter(slot)
                return target.count==1 && same(target.first,cookie) &&
                    same(values.first(where:{cookieMatches($0,address:address)}),expectedSource)
            }
            @MainActor func rollback() async {
                let latest=await store.allCookies()
                await withCheckedContinuation{continuation in
                    guard rooted(),channelCookieOwners[targetKey]==owner,written(latest),DeviceBinding.load(next)?.cookie==retained?.cookie else{
                        release();continuation.resume();return
                    }
                    // Enqueue synchronously on MainActor before releasing ownership; a newer write follows it.
                    if let previous=before.first {store.setCookie(previous){continuation.resume()}}
                    else{store.delete(cookie){continuation.resume()}}
                    release()
                }
            }
            var dispatched=false
            await withCheckedContinuation{continuation in
                guard valid() else{continuation.resume();return}
                channelCookieOwners[targetKey]=owner;channelCookieOwners[sourceKey]=owner;dispatched=true
                store.setCookie(cookie){continuation.resume()}
            }
            guard dispatched else{return}
            let fresh=await store.allCookies()
            guard valid(),owned(),written(fresh) else{await rollback();return}
            @MainActor func commit(_ eligible:@escaping ()->Bool) async -> Bool {
                let latest=await store.allCookies()
                guard eligible(),valid(),owned(),written(latest),DeviceBinding.load(next)?.cookie==retained?.cookie,
                      DeviceBinding.save(next,.init(name:root.name,token:token)) else{await rollback();return false}
                for prefix in ["notificationsMuted:","computer-name:","cursor:","stream:"] {
                    if let value=defaults.object(forKey:prefix+address){defaults.set(value,forKey:prefix+next)}
                }
                if foregroundBaselines.contains(address){foregroundBaselines.insert(next)}
                defaults.set(channelRecord(address) ?? d,forKey:"channels:"+next);defaults.set(root.name,forKey:"cookieName:"+next)
                defaults.set(saved.filter{$0 != address && $0 != next}+[next],forKey:"origins")
                release()
                if view==nil || !pageReady {connect(url,address:next);arrivingVia=next}
                else{origin=next;defaults.set(next,forKey:"active");routeNotice(MobileStrings.text("已切换连接信道")+" · "+next)}
                return true
            }
            if view==nil || !pageReady {_ = await commit({true});return}
            let peer=RoutePeer(next,id,commit:commit,rollback:rollback)
            rollbackPeer();pendingPeer=peer
            view?.evaluateJavaScript("window.BridgeChannelLink?.prepare("+peer.arguments([next,peer.nonce,id])+")",completionHandler:nil)
        }
    }
    private final class RoutePeer {
        let next:String,device:String,nonce=UUID().uuidString,commit:@MainActor (@escaping ()->Bool) async -> Bool,rollback:@MainActor () async -> Void
        var document:String {next+"/mobile-channel#"+nonce}
        init(_ next:String,_ device:String,commit:@escaping @MainActor (@escaping ()->Bool) async -> Bool,rollback:@escaping @MainActor () async -> Void){self.next=next;self.device=device;self.commit=commit;self.rollback=rollback}
        func arguments(_ values:[String])->String {values.map{String(decoding:(try? JSONEncoder().encode($0)) ?? Data("null".utf8),as:UTF8.self)}.joined(separator:",")}
    }
    private func rollbackPeer(){
        guard let peer=pendingPeer else{return};pendingPeer=nil
        Task { @MainActor in await peer.rollback() }
    }
    private func commitPeer(_ peer:RoutePeer,eligible:@escaping ()->Bool,reply:@escaping (String)->Void){
        pendingPeer=nil
        var answered=false
        func answer(_ accepted:Bool){guard !answered else{return};answered=true;reply(accepted ? "accepted":"expired")}
        let deadline=DispatchWorkItem{answer(false)}
        DispatchQueue.main.asyncAfter(deadline:.now()+4,execute:deadline)
        Task { @MainActor in
            let accepted=await peer.commit{!answered && eligible()}
            deadline.cancel();answer(accepted)
        }
    }
    private var routeScript=""
    private var entryProgress:UIView?
    private var pendingEntry:(address:String,url:URL,interrupted:Bool)?
    private func hideEntryProgress(){entryProgress?.removeFromSuperview();entryProgress=nil}
    private var documentAddress="",pageReady=false
    private var pendingPeer:RoutePeer?
    private var peerViews=[WKWebView:RoutePeer]()
    func webView(_ parent:WKWebView,createWebViewWith configuration:WKWebViewConfiguration,for action:WKNavigationAction,windowFeatures:WKWindowFeatures)->WKWebView? {
        guard parent===web,action.sourceFrame.isMainFrame,let source=parent.url,GatewayURL.same(source,documentAddress),
              let peer=pendingPeer,action.request.url?.absoluteString==peer.document,!peerViews.values.contains(where:{$0===peer}) else{return nil}
        configuration.userContentController=WKUserContentController()
        let child=WKWebView(frame:CGRect(x:0,y:0,width:1,height:1),configuration:configuration)
        child.navigationDelegate=self;child.uiDelegate=self;child.isUserInteractionEnabled=false;child.accessibilityElementsHidden=true;child.alpha=0.01
        view.insertSubview(child,at:0);peerViews[child]=peer;return child
    }
    func webViewDidClose(_ child:WKWebView) {
        guard peerViews.removeValue(forKey:child) != nil else{return};child.navigationDelegate=nil;child.uiDelegate=nil;child.removeFromSuperview()
    }
    private var routeDiscovery: ConnectionDiscovery?
    private var routeAttempted = false
    private var requestedRoute: URL?
    private var connectionError: UIView?
    private var connectionDeadline: DispatchWorkItem?
    private var pendingStorage: String?
    private lazy var uu = UURemote(defaults)
    private var uuAvailability = "checking"
    private var clipboardToken = ""
    private var downloadState = DownloadManager.Snapshot()
    private let downloadPanel = DownloadPanel()
    private var exportPicker: UIDocumentPickerViewController?
    private var openDocument: UIDocumentInteractionController?
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
                    self.cookieMatches(cookie, address: address)
                }
                done(matching.isEmpty ? nil : HTTPCookie.requestHeaderFields(with: matching)["Cookie"])
            }
        }
        return manager
    }()
    private var uploadPicker: UIDocumentPickerViewController?
    private var uploadCompletion: (([URL]?) -> Void)?
    private var uploadGeneration = 0
    private var uploadSource: (name: String, at: Date)?
    private var mediaPicker: UIViewController?
    private var uploadTicket = UUID()
    private var mediaUploadDirectory: URL?
    private var timer: Timer?
    private var activityTimer: Timer?
    private var foregroundBaselines = Set<String>()
    private var polling = Set<String>()
    private var pollGeneration = 0
    private var pollTasks: [String: URLSessionDataTask] = [:]
    private var generation = 0
    private var authLossAttempted = false
    private var bindingRevision = 0
    private let defaults = UserDefaults.standard
    private func cookieName(_ address: String) -> String { defaults.string(forKey: "cookieName:" + address) ?? "codex_mobile_session" }
    private func cookieMatches(_ cookie: HTTPCookie, address: String, discover: Bool = false) -> Bool {
        guard let target = URL(string: address), cookie.path == GatewayURL.cookiePath(address),
              cookie.domain.trimmingCharacters(in: CharacterSet(charactersIn: ".")) == target.host,
              (!cookie.isSecure || target.scheme == "https"),
              cookie.expiresDate == nil || cookie.expiresDate! > Date() else { return false }
        return cookie.name == cookieName(address) || (discover && cookie.name.range(of: #"^codex_mobile_session_[a-f0-9]{24}$"#, options: .regularExpression) != nil)
    }
    private let page = UIStackView()
    private let topBackground = UIView()
    private var pageStatusBarStyle: UIStatusBarStyle = .default
    override var preferredStatusBarStyle: UIStatusBarStyle { pageStatusBarStyle }
    private let subtitle = UILabel()
    private let gatewayMenu = UIButton(type: .system)
    private var gatewayMenuTop: NSLayoutConstraint?
    private var pageBottom: NSLayoutConstraint?
    private var keyboardFrame: CGRect?
    private weak var keyboardScreen: UIScreen?
    private var computerStates: [String: UILabel] = [:]
    private var computerNotifications: [String: UIStackView] = [:]
    private var computerSnapshots: [String: [String: Any]] = [:]
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
        topBackground.translatesAutoresizingMaskIntoConstraints = false; topBackground.isUserInteractionEnabled = false
        topBackground.backgroundColor = .systemGroupedBackground; view.insertSubview(topBackground, belowSubview: page)
        NSLayoutConstraint.activate([topBackground.topAnchor.constraint(equalTo: view.topAnchor), topBackground.bottomAnchor.constraint(equalTo: page.topAnchor), topBackground.leadingAnchor.constraint(equalTo: view.leadingAnchor), topBackground.trailingAnchor.constraint(equalTo: view.trailingAnchor)])
        pageBottom = page.bottomAnchor.constraint(equalTo: view.bottomAnchor)
        NSLayoutConstraint.activate([page.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor), pageBottom!, page.leadingAnchor.constraint(equalTo: view.leadingAnchor), page.trailingAnchor.constraint(equalTo: view.trailingAnchor)])
        subtitle.font = .preferredFont(forTextStyle: .caption1); subtitle.textColor = .secondaryLabel; subtitle.textAlignment = .center; subtitle.numberOfLines = 2; page.addArrangedSubview(subtitle)
        var menuStyle = UIButton.Configuration.plain(); menuStyle.image = UIImage(systemName: "ellipsis")
        menuStyle.baseForegroundColor = .label; menuStyle.baseBackgroundColor = .secondarySystemBackground; menuStyle.cornerStyle = .capsule
        gatewayMenu.configuration = menuStyle; gatewayMenu.accessibilityLabel = MobileStrings.text("电脑与通知"); gatewayMenu.showsMenuAsPrimaryAction = true
        updateGatewayMenu()
        gatewayMenu.translatesAutoresizingMaskIntoConstraints = false; view.addSubview(gatewayMenu)
        NSLayoutConstraint.activate([gatewayMenu.trailingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.trailingAnchor, constant: -8), gatewayMenu.widthAnchor.constraint(equalToConstant: 48), gatewayMenu.heightAnchor.constraint(equalToConstant: 48)])
        let menuFallbackTop = gatewayMenu.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor, constant: 4)
        menuFallbackTop.priority = .defaultLow; menuFallbackTop.isActive = true
        downloadPanel.translatesAutoresizingMaskIntoConstraints = false; view.addSubview(downloadPanel)
        downloadPanel.onToggle = { [weak self] in
            guard let self else { return }
            if [.connecting, .downloading].contains(self.downloadState.phase) { self.downloads.pause() }
            else if self.downloadState.canResume { self.downloads.resume() } else { self.downloads.restart() }
        }
        downloadPanel.onCancel = { [weak self] in self?.downloads.cancel() }
        downloadPanel.onSave = { [weak self] in self?.saveDownload() }
        downloadPanel.onOpen = { [weak self] in self?.openDownload() }
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
    override func viewDidAppear(_ animated: Bool) {
        super.viewDidAppear(animated)
        applyTheme()
        requestStartupNotifications()
    }
    private var theme: String {
        let value = defaults.string(forKey: "settingsTheme") ?? "system"
        return ["system", "light", "dark"].contains(value) ? value : "system"
    }
    var themeStyle: UIUserInterfaceStyle { theme == "dark" ? .dark : theme == "light" ? .light : .unspecified }
    private func applyTheme() {
        view.window?.overrideUserInterfaceStyle = themeStyle
        syncWebTheme()
    }
    private func syncWebTheme() {
        // UIKit propagates the window override through its children after the
        // trait callback. Read the settled style, including when returning to system.
        DispatchQueue.main.async { [weak self] in
            guard let self, let web = self.web, let url = web.url, GatewayURL.same(url, self.documentAddress) else { return }
            let dark = self.view.traitCollection.userInterfaceStyle == .dark
            web.evaluateJavaScript("window.BridgeNativeTheme={theme:'" + self.theme + "',dark:" + String(dark) + "};document.dispatchEvent(new Event('bridge-native-theme'));")
        }
    }
    override func traitCollectionDidChange(_ previousTraitCollection: UITraitCollection?) {
        super.traitCollectionDidChange(previousTraitCollection)
        if previousTraitCollection?.userInterfaceStyle != traitCollection.userInterfaceStyle { syncWebTheme() }
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
        guard let web, let current = web.url, GatewayURL.same(current, documentAddress) else { return }
        web.evaluateJavaScript("(()=>{if(typeof window.BridgeNavigation?.accounts==='function')return window.BridgeNavigation.accounts();document.getElementById('accounts-button')?.click();return true;})()")
    }
    private func appIconsMenu() -> UIDeferredMenuElement {
        UIDeferredMenuElement.uncached { [weak self] completion in
            guard let self, let web = self.web, let url = web.url, GatewayURL.same(url, self.documentAddress) else { completion([]); return }
            let ticket = self.generation, address = self.origin
            web.evaluateJavaScript("(()=>{const button=document.getElementById('client-switch-toggle');return document.getElementById('app')?.hidden===false&&button&&!button.hidden?button.getAttribute('aria-expanded')==='false':null;})()") { [weak self, weak web] value, _ in
                guard let self, let web, self.web === web, self.generation == ticket, let url = web.url, GatewayURL.same(url, documentAddress), let collapsed = value as? Bool else { completion([]); return }
                let action = UIAction(title: MobileStrings.text(collapsed ? "显示底部 APP 图标" : "隐藏底部 APP 图标"), image: UIImage(systemName: "square.grid.3x1.below.line.grid.1x2")) { [weak self, weak web] _ in
                    guard let self, let web, self.web === web, self.generation == ticket, let url = web.url, GatewayURL.same(url, documentAddress) else { return }
                    web.evaluateJavaScript("document.getElementById('client-switch-toggle')?.click()")
                }
                completion([action])
            }
        }
    }
    private func uuContext() -> Bool {
        guard let current = web?.url, GatewayURL.same(current, documentAddress), saved.contains(origin),
              connectionError == nil, !uu.scope.isEmpty else { return false }
        return UURemote.device(cookieName(origin)) == uu.scope
    }
    private func uuMenu() -> UIDeferredMenuElement {
        UIDeferredMenuElement.uncached { [weak self] complete in
            guard let self, let web = self.web, self.uu.enabled, self.uuContext() else { complete([]); return }
            let ticket = self.generation, device = self.uu.scope
            web.evaluateJavaScript("window.BridgeUURemote?.menu(" + self.uu.settings("read") + ")||[]") { result, _ in
                guard self.generation == ticket, self.web === web, self.uu.scope == device, self.uuContext(),
                      let rows = result as? [[String:String]] else { complete([]); return }
                complete(rows.compactMap { row -> UIAction? in
                    guard let title = row["title"], let mode = row["mode"], ["pip","full"].contains(mode) else { return nil }
                    return UIAction(title:title,image:UIImage(systemName:mode == "pip" ? "pip" : "desktopcomputer")) { [weak self] _ in
                        guard let self, self.generation == ticket, self.web === web, self.uu.scope == device, self.uuContext() else { return }
                        self.uu.open(mode) { self.routeNotice(MobileStrings.text($0)) }
                    }
                })
            }
        }
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
            uuMenu(),
            appIconsMenu(),
            UIAction(title: MobileStrings.text("手机设置"), image: UIImage(systemName: "gearshape")) { [weak self] _ in self?.settings() }
        ])
    }
    private func navigation(home: Bool) {
        navigationController?.setNavigationBarHidden(!home, animated: false)
        gatewayMenu.isHidden = home
        gatewayMenu.isEnabled = true
        title = ""; navigationItem.largeTitleDisplayMode = .never
        let logo = UIImageView(image: UIImage(named: "AppIcon60x60@2x.png")); logo.contentMode = .scaleAspectFit
        logo.widthAnchor.constraint(equalToConstant: 30).isActive = true; logo.heightAnchor.constraint(equalToConstant: 30).isActive = true; logo.layer.cornerRadius = 7; logo.clipsToBounds = true
        let brand = UIStackView(arrangedSubviews: [logo, label("Codex Bridge", size: 18, weight: .semibold)]); brand.axis = .horizontal; brand.spacing = 8; brand.alignment = .center
        navigationItem.leftBarButtonItem = UIBarButtonItem(customView: brand)
        let settings = UIBarButtonItem(image: UIImage(systemName: "gearshape"), style: .plain, target: self, action: #selector(self.settings)); settings.accessibilityLabel = MobileStrings.text("设置")
        let inbox = UIBarButtonItem(image: UIImage(systemName: "bell"), style: .plain, target: self, action: #selector(self.inbox)); inbox.accessibilityLabel = MobileStrings.text("通知")
        navigationItem.rightBarButtonItems = [settings, inbox]
    }
    private func clear() { pendingEntry=nil;routeTraffic.removeAll();uu.scope = ""; uuAvailability = "checking"; hideEntryProgress();pageReady=false;rollbackPeer();for child in Array(peerViews.keys){child.stopLoading();webViewDidClose(child)};cancelChannelJobs();connectionDeadline?.cancel(); connectionDeadline = nil; updateSystemBars(reset: true); connectionError = nil; pendingStorage = nil; nativeMainNavigation = nil; routeDiscovery?.cancel(); routeDiscovery = nil; cancelSync(); finishUpload(nil); if let folder = mediaUploadDirectory { try? FileManager.default.removeItem(at: folder); mediaUploadDirectory = nil }; stopComputerChecks(); computerStates.removeAll();addressLists.removeAll();computerTitles.removeAll();arrivingVia=nil;deferredRoute=nil; computerNotifications.removeAll(); gatewayMenuTop?.isActive = false; gatewayMenuTop = nil; generation += 1; web?.stopLoading(); web?.navigationDelegate = nil; web?.uiDelegate = nil; web = nil; for v in page.arrangedSubviews where v !== subtitle { page.removeArrangedSubview(v); v.removeFromSuperview() } }
    @objc func home() {
        clear(); navigation(home: true); subtitle.isHidden = true
        let container = UIView(); page.addArrangedSubview(container); let content = scrollContent(in: container)
        let visual = UIImageView(image: UIImage(systemName: "desktopcomputer")); visual.tintColor = .label; visual.contentMode = .scaleAspectFit; visual.heightAnchor.constraint(equalToConstant: 54).isActive = true
        let hero = column(spacing: 14); hero.addArrangedSubview(visual)
        let heading = label(MobileStrings.text("电脑上的工作，\n带在身边。"), size: 30, weight: .semibold); heading.textAlignment = .center; hero.addArrangedSubview(heading)
        let hint = label(MobileStrings.text("继续聊天、查看结果，让电脑替你运行。"), secondary: true); hint.textAlignment = .center; hero.addArrangedSubview(hint)
        hero.setCustomSpacing(24, after: visual); content.addArrangedSubview(hero)
        content.addArrangedSubview(card([button(MobileStrings.text("扫码连接电脑"), symbol: "qrcode.viewfinder", primary: true) { [weak self] in self?.scan() }, button(MobileStrings.text("输入网关地址"), symbol: "link") { [weak self] in self?.manualAddress() }, button("DSH Mobile", symbol: "link") { [weak self] in self?.manualDsh() }]))
        let computersHeading = UIStackView(arrangedSubviews: [label(MobileStrings.text("我的电脑"), size: 19, weight: .semibold), button(MobileStrings.text("刷新状态"), symbol: "arrow.clockwise") { [weak self] in self?.checkComputers(); self?.sync() }])
        computersHeading.axis = .horizontal; computersHeading.alignment = .center; computersHeading.spacing = 8; content.addArrangedSubview(computersHeading)
        if saved.isEmpty && DshMobileStore.ids.isEmpty {
            content.addArrangedSubview(card([label(MobileStrings.text("还没有连接的电脑"), size: 17, weight: .medium), label(MobileStrings.text("在电脑网关中展开“扫码登录”，然后用上方按钮扫描。"), secondary: true)]))
        } else {
            for address in recentComputers {
                if address.hasPrefix("dsh:") { content.addArrangedSubview(dshComputer(String(address.dropFirst(4)))); continue }
                let row = button(computerName(address)) { [weak self] in self?.openSaved(address) }
                row.contentHorizontalAlignment = .leading
                row.titleLabel?.lineBreakMode = .byTruncatingTail; row.titleLabel?.numberOfLines = 1
                row.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
                row.configuration?.contentInsets = NSDirectionalEdgeInsets(top: 8, leading: 0, bottom: 8, trailing: 0)
                computerTitles[address]=row
                let list=UIStackView();list.axis = .vertical;list.spacing=4;addressLists[address]=list;paintAddresses(address)
                let apps = UIStackView(); apps.axis = .horizontal; apps.spacing = 0; apps.setContentHuggingPriority(.required, for: .horizontal); apps.setContentCompressionResistancePriority(.required, for: .horizontal); computerNotifications[address] = apps
                let heading = UIStackView(arrangedSubviews: [row, apps]); heading.axis = .horizontal; heading.alignment = .center; heading.spacing = 10
                let rename = button(MobileStrings.text("重命名"), symbol: "pencil") { [weak self] in self?.renameComputer(address) }
                let remove = button(MobileStrings.text("管理这台电脑"), symbol: "slider.horizontal.3") { [weak self] in self?.manageComputer(address) }
                remove.configuration?.contentInsets=NSDirectionalEdgeInsets(top:17,leading:8,bottom:17,trailing:8)
                let actions = UIStackView(arrangedSubviews: [rename, remove]); actions.axis = .horizontal; actions.distribution = .fillEqually; actions.spacing = 8
                content.addArrangedSubview(card([heading,list,actions]))
                updateComputerNotifications(computerSnapshots[address], address: address)
            }
        }
        let foot = label(MobileStrings.text("外出使用 HTTPS 地址；局域网地址需要连接同一网络。"), size: 13, secondary: true); foot.textAlignment = .center; content.addArrangedSubview(foot)
        checkComputers(); sync()
    }
    private var recentComputers: [String] {
        (Array(saved) + DshMobileStore.ids.map { "dsh:" + $0 }).sorted { a, b in
            let left = defaults.double(forKey: "last-opened:" + a), right = defaults.double(forKey: "last-opened:" + b)
            return left == right ? a < b : left > right
        }
    }
    private func dshComputer(_ id: String) -> UIView {
        let url = DshMobileStore.load(id)
        let open = button(MobileStrings.text("连接"), symbol: "link") { [weak self] in
            guard let url else { self?.info(MobileStrings.text("连接凭据不可用，请移除后重新添加")); return }; self?.openDsh(url, id: id)
        }
        let remove = button(MobileStrings.text("移除这台电脑"), symbol: "trash") { [weak self] in
            let alert = UIAlertController(title: MobileStrings.text("移除这台电脑") + "？", message: nil, preferredStyle: .alert)
            alert.addAction(UIAlertAction(title: MobileStrings.text("取消"), style: .cancel))
            alert.addAction(UIAlertAction(title: MobileStrings.text("移除"), style: .destructive) { _ in DshMobileStore.remove(id); self?.defaults.removeObject(forKey: "last-opened:dsh:" + id); self?.home() }); self?.present(alert, animated: true)
        }
        remove.configuration?.baseForegroundColor = .systemRed
        let actions = UIStackView(arrangedSubviews: [open, remove]); actions.axis = .horizontal; actions.distribution = .fillEqually; actions.spacing = 8
        return card([label("DSH Mobile", size: 17, weight: .semibold), label(url.map(DshMobileURL.display) ?? MobileStrings.text("连接凭据不可用"), size: 13, secondary: true), actions])
    }
    private func computerName(_ address: String) -> String {
        let id=channelRecord(address)?["deviceId"] as? String ?? address
        return defaults.string(forKey:"name:"+id) ?? defaults.string(forKey:"name:"+address) ?? channelRecord(address)?["name"] as? String ?? URL(string:address)?.host ?? address
    }
    private func renameComputer(_ address: String) {
        let alert = UIAlertController(title: MobileStrings.text("重命名电脑"), message: address + "\n" + MobileStrings.text("留空恢复默认名称"), preferredStyle: .alert)
        alert.addTextField { field in field.text = self.computerName(address) }
        alert.addAction(UIAlertAction(title: MobileStrings.text("取消"), style: .cancel))
        alert.addAction(UIAlertAction(title: MobileStrings.text("保存"), style: .default) { [weak self, weak alert] _ in
            guard let self else { return }
            let name = (alert?.textFields?.first?.text ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            guard name.count <= 80 else { self.info(MobileStrings.text("名称最多 80 个字符")); return }
            let id=self.channelRecord(address)?["deviceId"] as? String ?? address;self.defaults.removeObject(forKey:"name:"+address);if name.isEmpty {self.defaults.removeObject(forKey:"name:"+id)} else {self.defaults.set(name,forKey:"name:"+id)}
            self.home()
        })
        present(alert, animated: true)
    }
    private func updateComputerNotifications(_ data: [String: Any]?, address: String) {
        if let data { computerSnapshots[address] = ["clients": data["clients"] ?? []] }
        guard let apps = computerNotifications[address] else { return }
        guard let data else { apps.alpha = 0.5; return }
        apps.alpha = 1; apps.isHidden = false
        apps.arrangedSubviews.forEach { apps.removeArrangedSubview($0); $0.removeFromSuperview() }
        for row in ComputerNotificationSummary.clients(data) {
            guard let provider = row["id"] as? String else { continue }
            let name = ["codex": "Codex", "claude": "Claude", "deepseek": "DeepSeek Harness", "zcode": "ZCode", "antigravity": "Antigravity"][provider] ?? provider
            let count = max(0, row["unread"] as? Int ?? 0)
            let button = UIButton(type: .custom); button.translatesAutoresizingMaskIntoConstraints = false
            button.alpha = row["connected"] as? Bool == false ? 0.5 : 1
            button.accessibilityLabel = name + (row["connected"] as? Bool == false ? " · " + MobileStrings.text("尚未连接") : "") + " · " + MobileStrings.text("未读通知") + " " + String(count)
            button.addAction(UIAction { [weak self] _ in self?.openSaved(address, provider: provider) }, for: .touchUpInside)
            let image = UIImageView(image: UIImage(named: provider)); image.contentMode = .scaleAspectFit; image.translatesAutoresizingMaskIntoConstraints = false; button.addSubview(image)
            NSLayoutConstraint.activate([button.widthAnchor.constraint(equalToConstant: 44), button.heightAnchor.constraint(equalToConstant: 44), image.widthAnchor.constraint(equalToConstant: 24), image.heightAnchor.constraint(equalToConstant: 24), image.centerXAnchor.constraint(equalTo: button.centerXAnchor), image.centerYAnchor.constraint(equalTo: button.centerYAnchor)])
            if count > 0 {
                let badge = UILabel(); badge.text = count > 99 ? "99+" : String(count); badge.textAlignment = .center; badge.font = .systemFont(ofSize: 10, weight: .semibold); badge.textColor = .white; badge.backgroundColor = .systemRed; badge.layer.cornerRadius = 9; badge.clipsToBounds = true; badge.translatesAutoresizingMaskIntoConstraints = false; badge.isAccessibilityElement = false; button.addSubview(badge)
                NSLayoutConstraint.activate([badge.leadingAnchor.constraint(equalTo: button.leadingAnchor), badge.topAnchor.constraint(equalTo: button.topAnchor), badge.widthAnchor.constraint(greaterThanOrEqualToConstant: count > 99 ? 26 : 18), badge.heightAnchor.constraint(equalToConstant: 18)])
            }
            apps.addArrangedSubview(button)
        }
    }
    private func stopComputerChecks() {
        computerRevision += 1; computerTimer?.invalidate(); computerTimer = nil
        computerProbes.forEach { $0.cancel() }; computerProbes.removeAll()
    }
    private func checkComputers() {
        stopComputerChecks()
        guard web == nil, UIApplication.shared.applicationState == .active, !addressLists.isEmpty else { return }
        let revision = computerRevision
        for address in addressLists.keys {
            if let d=channelRecord(address){
                sweep(address,d,targets:addresses(address),current:{self.computerRevision==revision && self.web==nil},sample:{_,_,_ in self.paintAddresses(address)},exhausted:{})
            }
        }
        for (address, label) in computerStates {
            label.text = "○"; label.textColor = .secondaryLabel
            let probe = GatewayReachability(); computerProbes.append(probe)
            probe.start(address) { [weak self, weak label] online in
                guard let self, self.computerRevision == revision, self.web == nil else { return }
                label?.text = online ? "●" : "○"
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
    private func manualDsh() {
        let alert = UIAlertController(title: "DSH Mobile", message: MobileStrings.text("粘贴 DSH 客户端 Mobile 功能提供的完整链接"), preferredStyle: .alert)
        alert.addTextField { $0.placeholder = "https://…/?auth=…"; $0.keyboardType = .URL; $0.autocapitalizationType = .none; $0.autocorrectionType = .no }
        alert.addAction(UIAlertAction(title: MobileStrings.text("取消"), style: .cancel))
        alert.addAction(UIAlertAction(title: MobileStrings.text("继续"), style: .default) { [weak self, weak alert] _ in self?.chooseDsh(alert?.textFields?.first?.text ?? "") }); present(alert, animated: true)
    }
    private func chooseDsh(_ value: String) {
        do {
            let url = try DshMobileURL.connection(value)
            let alert = UIAlertController(title: MobileStrings.text("连接 DSH Mobile？"), message: DshMobileURL.display(url) + MobileStrings.text("\n请确认这是你自己的 DSH 客户端。将打开它自带的手机界面。"), preferredStyle: .alert)
            alert.addAction(UIAlertAction(title: MobileStrings.text("取消"), style: .cancel))
            alert.addAction(UIAlertAction(title: MobileStrings.text("连接"), style: .default) { [weak self] _ in
                do { let id = try DshMobileStore.save(url); self?.openDsh(url, id: id) } catch { self?.info(MobileStrings.text("无法保存 DSH Mobile 连接")) }
            }); present(alert, animated: true)
        } catch { info(error.localizedDescription) }
    }
    private func openDsh(_ url: URL, id: String) {
        defaults.set(Date().timeIntervalSince1970, forKey: "last-opened:dsh:" + id); home()
        let controller = UINavigationController(rootViewController: DshMobileController(url)); controller.modalPresentationStyle = .fullScreen
        controller.overrideUserInterfaceStyle = overrideUserInterfaceStyle; present(controller, animated: true)
    }
    func choose(_ value: String) {
        do { let url = try GatewayURL.connection(value); let address = try GatewayURL.origin(value)
            let alert = UIAlertController(title: MobileStrings.text("连接到这台电脑？"), message: address + MobileStrings.text("\n请确认这是你自己的网关。"), preferredStyle: .alert)
            alert.addAction(UIAlertAction(title: MobileStrings.text("取消"), style: .cancel)); alert.addAction(UIAlertAction(title: MobileStrings.text("连接"), style: .default) { _ in self.connect(url, address: address) })
            alert.addAction(UIAlertAction(title: "DSH Mobile", style: .default) { [weak self] _ in self?.chooseDsh(value) }); present(alert, animated: true)
        } catch { chooseDsh(value) }
    }
    func openNotification(_ address: String, thread: String?, host: String) {
        guard saved.contains(address) else { info(MobileStrings.text("请先连接通知对应的电脑。")); return }
        if let thread, let target = try? GatewayURL.chat(address, thread: thread, host: host) { connect(target, address: address) } else { openSaved(address) }
    }
    func openSaved(_ address: String, provider: String? = nil) { let suffix = provider.flatMap { ["codex", "claude", "deepseek", "zcode", "antigravity"].contains($0) ? "?client=" + $0 : nil } ?? ""; guard saved.contains(address), let url = URL(string: address + "/" + suffix) else { info(MobileStrings.text("请先扫码保存通知对应的电脑。")); return }; openComputer(address,url) }
    private func binding(_ address: String, done: @escaping (HTTPCookie?) -> Void) {
        WKWebsiteDataStore.default().httpCookieStore.getAllCookies { cookies in
            done(cookies.first { self.cookieMatches($0, address: address) })
        }
    }
    private func rememberConnection() {
        let address = origin, ticket = generation, revision = bindingRevision
        WKWebsiteDataStore.default().httpCookieStore.getAllCookies { sentCookies in
            let sent = sentCookies.filter { self.cookieMatches($0, address: address, discover: true) }
            guard !sent.isEmpty else { return }
        self.fetch(address: address, route: "/api/auth") { result in
            guard ticket == self.generation else { return }
            guard case .success(let auth) = result else {
                self.uuAvailability = "unavailable"
                self.web?.evaluateJavaScript("window.BridgeUURemote?.refresh()",completionHandler:nil)
                return
            }
            self.binding(address) { cookie in
                guard let cookie, ticket == self.generation else { return }
                self.binding(address) { current in
                    let previous = sent.first { $0.name == cookie.name } ?? (cookie.name == "codex_mobile_session" ? nil : sent.first { $0.name == "codex_mobile_session" })
                    guard ticket == self.generation, revision == self.bindingRevision, self.saved.contains(address), current?.name == cookie.name,
                          current?.value == cookie.value, previous?.value == cookie.value else { return }
                    if auth["authenticated"] as? Bool == true, auth["trustedDevice"] as? Bool == true,
                       (auth["cookieName"] as? String ?? "codex_mobile_session") == cookie.name {
                        DeviceBinding.save(address, .init(name: cookie.name, token: cookie.value))
                    } else if auth["authenticated"] as? Bool == false { DeviceBinding.clear(address) }
                    if auth["authenticated"] as? Bool==true && self.arrivingVia==address {self.arrivingVia=nil;self.routeNotice(MobileStrings.text("已切换连接信道")+" · "+address)}
                    let platform = (auth["computer"] as? [String:Any])?["platform"] as? String ?? ""
                    self.uu.scope = auth["authenticated"] as? Bool == true && ["Darwin","Windows"].contains(platform)
                        ? UURemote.device(cookie.name) : ""
                    self.uuAvailability = !self.uu.scope.isEmpty ? "ready"
                        : auth["authenticated"] as? Bool == true && !platform.isEmpty ? "unsupported" : "unavailable"
                    self.web?.evaluateJavaScript("window.BridgeUURemote?.refresh()",completionHandler:nil)
                    self.enrollChannels(auth,address:address,cookie:cookie,ticket:ticket)
                    self.defaults.set(ConnectionDiscovery.descriptor(auth, secret: cookie.value), forKey: "connection:" + address)
                }
            }
        }
        }
    }
    private func loadWithRecovery(_ web: WKWebView, url: URL, address: String, ticket: Int) {
        Task { @MainActor in
            guard let backup = DeviceBinding.load(address) else { if ticket == generation && self.web === web { loadNative(web, URLRequest(url: url)) }; return }
            let store = WKWebsiteDataStore.default().httpCookieStore
            let cookies = await withCheckedContinuation { continuation in store.getAllCookies { continuation.resume(returning: $0) } }
            let before = cookies.first { cookieMatches($0, address: address) }
            if before?.name == backup.name && before?.value == backup.token {
                if ticket == generation && self.web === web { loadNative(web, URLRequest(url: url)) }; return
            }
            if let before {
                let current = await DeviceBinding.verify(address, .init(name: before.name, token: before.value))
                if current == true || current == nil { if ticket == generation && self.web === web { loadNative(web, URLRequest(url: url)) }; return }
            }
            let valid = await DeviceBinding.verify(address, backup)
            if valid == false, DeviceBinding.load(address)?.cookie == backup.cookie { DeviceBinding.clear(address) }
            guard ticket == generation, self.web === web, saved.contains(address) else { return }
            guard valid == true, DeviceBinding.load(address)?.cookie == backup.cookie,
                  let host = URL(string: address)?.host else { loadNative(web, URLRequest(url: url)); return }
            var properties: [HTTPCookiePropertyKey: Any] = [.name: backup.name, .value: backup.token, .domain: host,
                .path: GatewayURL.cookiePath(address), .expires: Date().addingTimeInterval(400 * 86400),
                HTTPCookiePropertyKey("HttpOnly"): "TRUE", HTTPCookiePropertyKey("SameSite"): "Strict"]
            if address.hasPrefix("https://") { properties[.secure] = "TRUE" }
            guard let restored = HTTPCookie(properties: properties) else { loadNative(web, URLRequest(url: url)); return }
            let latest = await withCheckedContinuation { continuation in store.getAllCookies { continuation.resume(returning: $0) } }
            guard latest.first(where: { cookieMatches($0, address: address) })?.value == before?.value,
                  DeviceBinding.load(address)?.cookie == backup.cookie else { loadNative(web, URLRequest(url: url)); return }
            await withCheckedContinuation { continuation in store.setCookie(restored) { continuation.resume() } }
            guard ticket == generation, self.web === web, saved.contains(address),
                  DeviceBinding.load(address)?.cookie == backup.cookie else {
                let after = await withCheckedContinuation { continuation in store.getAllCookies { continuation.resume(returning: $0) } }
                if after.contains(where: { $0.name == restored.name && $0.value == restored.value && $0.domain == restored.domain && $0.path == restored.path }) { await store.deleteCookie(restored) }
                return
            }
            defaults.set(backup.name, forKey: "cookieName:" + address)
            loadNative(web, URLRequest(url: url))
        }
    }
    private func loadNative(_ web: WKWebView, _ request: URLRequest) {
        guard self.web === web, connectionError == nil else { return }
        nativeMainNavigation = request.url.map { (web, generation, $0.absoluteString) }
        web.load(request)
    }
    private func recoverConnection() { if !checkChannels(failure: true) { recoverLegacy() } }
    private func recoverLegacy() {
        guard !routeAttempted, UIApplication.shared.applicationState == .active, let requested = requestedRoute,
              let descriptor = defaults.dictionary(forKey: "connection:" + origin) as? [String: String] else { return }
        routeAttempted = true
        let address = origin, ticket = generation
        binding(address) { cookie in
            guard ticket == self.generation, UIApplication.shared.applicationState == .active, let cookie else { return }
            let resolver = ConnectionDiscovery(); self.routeDiscovery = resolver
            Task { @MainActor in
                let next = await resolver.resolve(address, descriptor: descriptor, secret: cookie.value)
                guard ticket == self.generation, self.routeDiscovery === resolver, self.saved.contains(address), next != address, UIApplication.shared.applicationState == .active else { return }
                self.binding(address) { current in
                    guard ticket == self.generation, self.routeDiscovery === resolver, UIApplication.shared.applicationState == .active, current?.value == cookie.value else { return }
                    self.binding(next) { existing in
                        guard ticket == self.generation, self.routeDiscovery === resolver, UIApplication.shared.applicationState == .active, existing == nil || existing?.value == cookie.value else { return }
                        self.relocate(address, to: next, requested: requested, cookie: cookie, descriptor: descriptor, resolver: resolver, ticket: ticket)
                    }
                }
            }
        }
    }
    private func relocate(_ address: String, to next: String, requested: URL, cookie: HTTPCookie, descriptor: [String: String], resolver: ConnectionDiscovery, ticket: Int) {
        guard let host = URL(string: next)?.host, let target = URL(string: next + requested.absoluteString.dropFirst(address.count)),
              let renewed = HTTPCookie(properties: [.name: cookie.name, .value: cookie.value, .domain: host, .path: "/", .secure: "TRUE", .expires: cookie.expiresDate ?? Date().addingTimeInterval(400 * 86400), HTTPCookiePropertyKey("HttpOnly"): "TRUE", HTTPCookiePropertyKey("SameSite"): "Strict"]) else { return }
        var snapshot: String?
        func current() -> Bool { ticket == self.generation && self.routeDiscovery === resolver && self.saved.contains(address) && UIApplication.shared.applicationState == .active }
        func finish() {
            guard current() else { return }
            let store = WKWebsiteDataStore.default().httpCookieStore
            store.setCookie(renewed) {
                guard current() else { return }
                store.delete(cookie) {
                guard current() else { return }
                if !self.saved.contains(next) { self.defaults.set(!self.notificationSourceEnabled(address), forKey: "notificationsMuted:" + next) }; self.defaults.removeObject(forKey: "notificationsMuted:" + address)
                var all = self.saved.filter { $0 != address }; if !all.contains(next) { all.append(next) }; self.defaults.set(all, forKey: "origins")
                self.defaults.set(descriptor, forKey: "connection:" + next); self.defaults.removeObject(forKey: "connection:" + address)
                if let name = self.defaults.string(forKey: "cookieName:" + address) { self.defaults.set(name, forKey: "cookieName:" + next) }; self.defaults.removeObject(forKey: "cookieName:" + address)
                if let backup = DeviceBinding.load(address), backup.name == cookie.name, backup.token == cookie.value,
                   DeviceBinding.load(next) == nil, DeviceBinding.save(next, backup) { DeviceBinding.clear(address) }
                for prefix in ["name:", "computer-name:", "cursor:", "stream:", "last-opened:"] {
                    if let value = self.defaults.object(forKey: prefix + address) { self.defaults.set(value, forKey: prefix + next) }; self.defaults.removeObject(forKey: prefix + address)
                }
                self.connect(target, address: next); self.pendingStorage = snapshot; self.routeAttempted = true; self.arrivingVia=next
                }
            }
        }
        if let web, let url = web.url, GatewayURL.same(url, documentAddress) {
            web.evaluateJavaScript("(()=>{if(typeof saveDraft==='function')saveDraft();return JSON.stringify(Object.fromEntries(Object.entries(localStorage)));})()") { value, _ in
                guard ticket == self.generation else { return }
                if let text = value as? String, text.utf8.count < 524288, let data = text.data(using: .utf8), (try? JSONSerialization.jsonObject(with: data)) is [String: String] { snapshot = text }
                finish()
            }
        } else { finish() }
    }
    private func connect(_ url: URL, address: String) {
        if saved.contains(address),!channelEnabled(address){openComputer(address,url);return}
        routeAttempted = false; requestedRoute = url
        let scripts: WebScripts
        do { scripts = try WebScripts() }
        catch { info(error.localizedDescription); return }
        if origin != address, #available(iOS 16.2, *) { Task { await LiveActivityController.shared.stop() } }
        clear(); navigation(home: false); subtitle.isHidden = false; origin = address; documentAddress=address; var all = saved; if !all.contains(address) { all.append(address) }; defaults.set(all, forKey: "origins"); defaults.set(address, forKey: "active"); defaults.set(Date().timeIntervalSince1970, forKey: "last-opened:" + address); subtitle.text = MobileStrings.text("正在连接 · ") + computerName(address)
        let configuration = WKWebViewConfiguration(); configuration.websiteDataStore = .default(); configuration.applicationNameForUserAgent = "BridgeMobile/0.1-iOS"
        configuration.preferences.javaScriptCanOpenWindowsAutomatically=true
        configuration.ignoresViewportScaleLimits = false
        configuration.userContentController.addUserScript(WKUserScript(source: WebScripts.fixedViewport, injectionTime: .atDocumentEnd, forMainFrameOnly: true))
        configuration.userContentController.addUserScript(WKUserScript(source: "window.BridgeMobileCapabilities = Object.freeze({ diagnosticsExport: true });", injectionTime: .atDocumentEnd, forMainFrameOnly: true))
        clipboardToken = UUID().uuidString
        let connectionName = String(decoding: (try? JSONEncoder().encode(computerName(address))) ?? Data("null".utf8), as: UTF8.self)
        let nativeLanguage = (defaults.string(forKey: "bridge-language") ?? Locale.preferredLanguages.first ?? "zh").hasPrefix("en") ? "en" : "zh"
        routeScript=scripts.route
        let appearanceScript = (scripts.route+"\n"+scripts.appearance).replacingOccurrences(of: "__BRIDGE_CLIPBOARD_TOKEN__", with: clipboardToken).replacingOccurrences(of:"__BRIDGE_ROUTE_BOUND__",with:channelRecord(address)==nil ? "no":"yes").replacingOccurrences(of: "'__BRIDGE_CONNECTION_NAME__'", with: connectionName) + "\ndocument.addEventListener('bridge-authenticated', () => window.prompt('codexbridge-session:" + clipboardToken + "', ''));document.addEventListener('bridge-logged-out', () => window.prompt('codexbridge-logout:" + clipboardToken + "', ''));document.addEventListener('bridge-auth-lost', () => window.prompt('codexbridge-auth-lost:" + clipboardToken + "', ''));"
        configuration.userContentController.addUserScript(WKUserScript(source: appearanceScript, injectionTime: .atDocumentEnd, forMainFrameOnly: true))
        #if compiler(>=6.1)
        if #available(iOS 18.4, *) {
            let uploadScript = "window.BridgeUploadSource = (source, accept) => navigator.userActivation?.isActive && window.prompt('codexbridge-upload-source:" + clipboardToken + "', source + (source !== 'files' && accept && !accept.includes('video') ? '-images' : '')) === 'selected';"
            configuration.userContentController.addUserScript(WKUserScript(source: uploadScript, injectionTime: .atDocumentEnd, forMainFrameOnly: true))
        }
        #endif
        let clipboardScript = scripts.clipboard.replacingOccurrences(of: "__BRIDGE_CLIPBOARD_TOKEN__", with: clipboardToken).replacingOccurrences(of: "'__BRIDGE_NATIVE_LANGUAGE__'", with: "\"" + nativeLanguage + "\"")
        configuration.userContentController.addUserScript(WKUserScript(source: clipboardScript, injectionTime: .atDocumentEnd, forMainFrameOnly: true))
        let systemBarsScript = scripts.systemBars.replacingOccurrences(of: "__BRIDGE_CLIPBOARD_TOKEN__", with: clipboardToken)
        configuration.userContentController.addUserScript(WKUserScript(source: systemBarsScript, injectionTime: .atDocumentEnd, forMainFrameOnly: true))
        let web = WKWebView(frame: .zero, configuration: configuration); self.web = web; web.scrollView.contentInsetAdjustmentBehavior = .never; web.navigationDelegate = self; web.uiDelegate = self; web.allowsBackForwardNavigationGestures = true; web.customUserAgent = nil; page.addArrangedSubview(web); gatewayMenuTop = gatewayMenu.topAnchor.constraint(equalTo: web.topAnchor, constant: 4); gatewayMenuTop?.isActive = true; loadWithRecovery(web, url: url, address: address, ticket: generation)
        let ticket = generation
        let deadline = DispatchWorkItem { [weak self, weak web] in
            guard let self, let web, ticket == self.generation, self.web === web, self.connectionError == nil else { return }
            self.connectionDeadline = nil; web.stopLoading(); self.showConnectionError(status: nil); self.recoverConnection()
        }
        connectionDeadline = deadline; DispatchQueue.main.asyncAfter(deadline: .now() + 30, execute: deadline)
        web.scrollView.pinchGestureRecognizer?.isEnabled = false
    }
    @objc private func refresh() { routeAttempted = false; if documentAddress != origin,let uri=web?.url,GatewayURL.same(uri,documentAddress),let next=URL(string:origin+uri.absoluteString.dropFirst(documentAddress.count)){connect(next,address:origin);return};if let web { nativeMainNavigation = web.url.map { (web, generation, $0.absoluteString) }; web.reload() } }
    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse, decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        let status = (response.response as? HTTPURLResponse)?.statusCode ?? 0
        if let peer=peerViews[webView] {decisionHandler((response.response.url?.absoluteString.components(separatedBy:"#").first==peer.next+"/mobile-channel" && status==200) ? .allow:.cancel);return}
        let recover = webView === web && response.isForMainFrame && response.response.url.map { GatewayURL.same($0, documentAddress) } == true && ConnectionDiscovery.retryableStatus(status)
        // Tunnel and proxy error pages (e.g. Cloudflare 1033/530) carry no gateway navigation; keep the way back native.
        decisionHandler(recover ? .cancel : .allow)
        if recover { showConnectionError(status: status); recoverConnection() }
    }
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { decisionHandler(.cancel); return }
        if let peer=peerViews[webView]{decisionHandler(action.request.url?.absoluteString==peer.document ? .allow:.cancel);return}
        if webView===web,action.sourceFrame.isMainFrame,action.targetFrame==nil,let peer=pendingPeer,url.absoluteString==peer.document{decisionHandler(.allow);return}
        if action.targetFrame?.isMainFrame == false {
            decisionHandler(isIsolatedShell(url) ? .allow : .cancel); return
        }
        if action.targetFrame?.isMainFrame == true, nativeMainNavigation?.web === webView {
            let expected = nativeMainNavigation
            nativeMainNavigation = nil
            if !action.sourceFrame.isMainFrame {
                guard action.navigationType == .other, expected?.generation == generation,
                      expected?.url == url.absoluteString, GatewayURL.same(url, documentAddress) else { decisionHandler(.cancel); return }
            }
        } else if !action.sourceFrame.isMainFrame { decisionHandler(.cancel); return }
        if isIsolatedShell(url) { decisionHandler(.cancel); return }
        if url.absoluteString == "codexbridge://home", action.sourceFrame.isMainFrame, action.navigationType == .linkActivated, let source = webView.url, GatewayURL.same(source, documentAddress) { decisionHandler(.cancel); home(); return }
        if action.navigationType == .linkActivated, action.sourceFrame.isMainFrame,
           let source = webView.url, GatewayURL.same(source, documentAddress), isArtifact(url) {
            decisionHandler(.cancel); downloadArtifact(action.request); return
        }
        if GatewayURL.same(url, documentAddress) {
            if action.targetFrame == nil { decisionHandler(.cancel); loadNative(webView, action.request) }
            else { decisionHandler(.allow) }
            return
        }
        decisionHandler(.cancel)
        if action.navigationType == .linkActivated, ["http", "https"].contains(url.scheme ?? "") {
            let a = UIAlertController(title: MobileStrings.text("在浏览器打开外部链接？"), message: url.host, preferredStyle: .alert); a.addAction(UIAlertAction(title: MobileStrings.text("取消"), style: .cancel)); a.addAction(UIAlertAction(title: MobileStrings.text("打开"), style: .default) { _ in UIApplication.shared.open(url) }); present(a, animated: true)
        }
    }
    func webView(_ webView: WKWebView, didCommit navigation: WKNavigation!) {
        guard webView === web, connectionError == nil, let url = webView.url, GatewayURL.same(url, documentAddress) else { return }
        channelTicket+=1;rollbackPeer()
        connectionDeadline?.cancel(); connectionDeadline = nil; hideEntryProgress();subtitle.isHidden = true
        syncWebTheme()
    }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        if let peer=peerViews[webView]{
            if pendingPeer===peer,webView.url?.absoluteString==peer.document{
                webView.evaluateJavaScript(routeScript+";window.BridgeChannelLink.worker("+peer.arguments([documentAddress,peer.nonce,peer.device])+")",completionHandler:nil)
            }
            return
        }
        if nativeMainNavigation?.web === webView { nativeMainNavigation = nil }; guard webView === web, connectionError == nil, let url = webView.url, GatewayURL.same(url, documentAddress) else { return }; hideEntryProgress();pageReady=true;subtitle.isHidden = true; registerNativePush(); rememberConnection()
        syncWebTheme()
        if let snapshot = pendingStorage, let encoded = try? JSONEncoder().encode(snapshot) { pendingStorage = nil; webView.evaluateJavaScript("(()=>{const data=JSON.parse(" + String(decoding:encoded,as:UTF8.self) + ");if(data.version===2){window.BridgeMobileHandoff?.restore(data);return;}for(const [k,v] of Object.entries(data))if(typeof v==='string')localStorage.setItem(k,v);location.reload();})()", completionHandler: nil) }
    }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) { if nativeMainNavigation?.web === webView { nativeMainNavigation = nil }; guard webView === web, connectionError == nil else { return }
        let failure = error as NSError
        guard failure.domain != NSURLErrorDomain || failure.code != NSURLErrorCancelled else { return }
        subtitle.isHidden = false; subtitle.text = MobileStrings.text("连接失败，请检查电脑和地址后刷新")
        showConnectionError(status: nil)
        recoverConnection()
    }
    private func showConnectionError(status: Int?) {
        guard connectionError == nil, let route = requestedRoute else { return }
        pendingEntry=nil;connectionDeadline?.cancel(); connectionDeadline = nil
        hideEntryProgress();let address = origin, ticket = generation
        updateSystemBars(reset: true); web?.isHidden = true; subtitle.isHidden = true; gatewayMenu.isHidden = true
        let container = UIView(); page.addArrangedSubview(container); connectionError = container; let content = scrollContent(in: container)
        let visual = UIImageView(image: UIImage(systemName: "wifi.exclamationmark")); visual.tintColor = .secondaryLabel; visual.contentMode = .scaleAspectFit; visual.heightAnchor.constraint(equalToConstant: 48).isActive = true
        let heading = label(MobileStrings.text("暂时无法连接电脑"), size: 22, weight: .semibold); heading.textAlignment = .center
        let hint = label(MobileStrings.text("网关暂时无法访问。请检查电脑和网络后重试，或返回电脑列表。"), secondary: true); hint.textAlignment = .center
        let detail = label(computerName(address) + (status.map { " · HTTP \($0)" } ?? ""), size: 13, secondary: true); detail.textAlignment = .center
        let hero = column(spacing: 12); [visual, heading, hint, detail].forEach(hero.addArrangedSubview); hero.setCustomSpacing(20, after: visual)
        content.addArrangedSubview(hero)
        content.addArrangedSubview(card([button(MobileStrings.text("重试"), symbol: "arrow.clockwise", primary: true) { [weak self] in
            guard let self, ticket == self.generation, self.saved.contains(address) else { return }; self.openComputer(address,route)
        }, button(MobileStrings.text("管理这台电脑"),symbol:"slider.horizontal.3"){[weak self] in self?.manageComputer(address)
        }, button(MobileStrings.text("返回电脑列表"), symbol: "desktopcomputer") { [weak self] in self?.home() }]))
    }
    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) { if nativeMainNavigation?.web === webView { nativeMainNavigation = nil }; guard webView === web else { return }; recoverConnection() }
    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) { guard frame.isMainFrame else { completionHandler(); return }; let a = UIAlertController(title: nil, message: message, preferredStyle: .alert); a.addAction(UIAlertAction(title: MobileStrings.text("好"), style: .default) { _ in completionHandler() }); present(a, animated: true) }
    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) { guard frame.isMainFrame else { completionHandler(false); return }; let a = UIAlertController(title: nil, message: message, preferredStyle: .alert); a.addAction(UIAlertAction(title: MobileStrings.text("取消"), style: .cancel) { _ in completionHandler(false) }); a.addAction(UIAlertAction(title: MobileStrings.text("确认"), style: .default) { _ in completionHandler(true) }); present(a, animated: true) }
    func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String, defaultText: String?, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (String?) -> Void) {
        guard webView===web else{completionHandler(nil);return}
        if prompt == "codexbridge-upload-source:" + clipboardToken {
            guard webView === web, connectionError == nil, UIApplication.shared.applicationState == .active,
                  frame.isMainFrame, let source = frame.request.url, GatewayURL.same(source, documentAddress),
                  let current = webView.url, GatewayURL.same(current, documentAddress), presentedViewController == nil,
                  let value = defaultText, ["photos", "files", "photos-images"].contains(value) else { completionHandler(nil); return }
            uploadSource = (value, Date()); completionHandler("selected"); return
        }
        if prompt == "codexbridge-uu:" + clipboardToken {
            if connectionError == nil, UIApplication.shared.applicationState == .active, frame.isMainFrame,
               let source = frame.request.url, GatewayURL.same(source, documentAddress),
               let current = webView.url, GatewayURL.same(current, documentAddress),
               let command = defaultText, ["read","retry"].contains(command) {
                if command == "retry", saved.contains(origin) { uuAvailability = "checking"; rememberConnection() }
                completionHandler(uuContext() ? uu.settings("read") : "{\"supported\":false,\"availability\":\"" + uuAvailability + "\"}")
                return
            }
            guard uuContext(), UIApplication.shared.applicationState == .active, frame.isMainFrame,
                  let source = frame.request.url, GatewayURL.same(source, documentAddress),
                  let command = defaultText, ["enable","disable","forget"].contains(command) else { completionHandler(nil); return }
            completionHandler(uu.settings(command)); return
        }
        if prompt == "codexbridge-theme:" + clipboardToken {
            guard webView === web, connectionError == nil, UIApplication.shared.applicationState == .active,
                  frame.isMainFrame, let source = frame.request.url, GatewayURL.same(source, documentAddress),
                  let current = webView.url, GatewayURL.same(current, documentAddress),
                  let value = defaultText, ["system", "light", "dark"].contains(value) else { completionHandler(nil); return }
            defaults.set(value, forKey: "settingsTheme"); completionHandler("saved"); applyTheme(); return
        }
        if prompt == "codexbridge-system-bars:" + clipboardToken {
            guard webView === web, UIApplication.shared.applicationState == .active, frame.isMainFrame,
                  let source = frame.request.url, GatewayURL.same(source, documentAddress),
                  let current = webView.url, GatewayURL.same(current, documentAddress),
                  let text = defaultText, text.utf16.count < 100, let data = text.data(using: .utf8),
                  let colors = try? JSONSerialization.jsonObject(with: data) as? [String: String],
                  let top = Self.barColor(colors["top"]), let bottom = Self.barColor(colors["bottom"]) else { completionHandler(nil); return }
            updateSystemBars(top: top, bottom: bottom); completionHandler("saved"); return
        }
        if prompt == "codexbridge-modal:" + clipboardToken {
            guard webView === web, connectionError == nil, UIApplication.shared.applicationState == .active,
                  frame.isMainFrame, let source = frame.request.url, GatewayURL.same(source, documentAddress),
                  let current = webView.url, GatewayURL.same(current, documentAddress),
                  let state = defaultText, ["open", "closed"].contains(state) else { completionHandler(nil); return }
            gatewayMenu.isHidden = state == "open"; gatewayMenu.isEnabled = state == "closed"
            completionHandler("saved"); return
        }
        if prompt == "codexbridge-channels:" + clipboardToken {
            guard web === webView,frame.isMainFrame,UIApplication.shared.applicationState == .active,
                  let src=frame.request.url,GatewayURL.same(src, documentAddress),let page=webView.url,GatewayURL.same(page, documentAddress) else{completionHandler(nil);return}
            if let peer=pendingPeer,defaultText=="ready:"+peer.nonce {
                let token=clipboardToken,screen=generation,address=documentAddress
                commitPeer(peer,eligible:{self.web===webView && self.generation==screen && self.clipboardToken==token &&
                    self.documentAddress==address && UIApplication.shared.applicationState == .active &&
                    webView.url==page && !webView.isLoading && webView.url.map{GatewayURL.same($0,address)}==true},reply:completionHandler)
                return
            }
            if let cmd=defaultText,let answer=channelCommand(cmd){completionHandler(answer);return}
            if defaultText=="settled"{let retry=deferredRoute;deferredRoute=nil;retry?()}
            else if defaultText=="probe"{checkChannels(failure:false,inspect:true)}
            else if let choice=defaultText,choice.hasPrefix("pick:"){checkChannels(failure:false,forced:String(choice.dropFirst(5)))}
            completionHandler(channelMenu());return
        }
        if prompt == "codexbridge-channel-failure:" + clipboardToken {
            if webView === web, frame.isMainFrame, let source = frame.request.url, GatewayURL.same(source, documentAddress) { checkChannels(failure:true) }
            completionHandler(""); return
        }
        if prompt == "codexbridge-logout:" + clipboardToken {
            guard webView === web, UIApplication.shared.applicationState == .active, frame.isMainFrame,
                  let source = frame.request.url, GatewayURL.same(source, documentAddress),
                  let current = webView.url, GatewayURL.same(current, documentAddress) else { completionHandler(nil); return }
            bindingRevision += 1
            clearChannels(origin);DeviceBinding.clear(origin)
            binding(origin) { cookie in if let cookie { WKWebsiteDataStore.default().httpCookieStore.delete(cookie) } }
            completionHandler("cleared"); return
        }
        if prompt == "codexbridge-auth-lost:" + clipboardToken {
            guard webView === web, UIApplication.shared.applicationState == .active, frame.isMainFrame,
                  let source = frame.request.url, GatewayURL.same(source, documentAddress),
                  let current = webView.url, GatewayURL.same(current, documentAddress), !authLossAttempted,
                  let target = URL(string: origin + "/") else { completionHandler(nil); return }
            authLossAttempted = true; loadWithRecovery(webView, url: target, address: origin, ticket: generation)
            completionHandler("recovering"); return
        }
        if prompt == "codexbridge-session:" + clipboardToken {
            guard webView === web, UIApplication.shared.applicationState == .active, frame.isMainFrame,
                  let source = frame.request.url, GatewayURL.same(source, documentAddress),
                  let current = webView.url, GatewayURL.same(current, documentAddress) else { completionHandler(nil); return }
            authLossAttempted = false; rememberConnection(); completionHandler("saved"); return
        }
        if prompt == "codexbridge-language:" + clipboardToken {
            guard webView === web, frame.isMainFrame, let source = frame.request.url, GatewayURL.same(source, documentAddress),
                  let current = webView.url, GatewayURL.same(current, documentAddress), let language = defaultText, ["zh", "en"].contains(language) else { completionHandler(nil); return }
            defaults.set(language, forKey: "bridge-language");updateGatewayMenu();completionHandler("saved");return
        }
        guard prompt == "codexbridge-copy:" + clipboardToken, webView === web,
              UIApplication.shared.applicationState == .active, frame.isMainFrame,
              let source = frame.request.url, GatewayURL.same(source, documentAddress),
              let current = webView.url, GatewayURL.same(current, documentAddress),
              let text = defaultText, text.utf16.count <= 262144 else { completionHandler(nil); return }
        UIPasteboard.general.string = text
        completionHandler("copied")
    }
    private static func barColor(_ value: String?) -> UIColor? {
        guard let value, value.range(of: "^#[0-9a-fA-F]{6}$", options: .regularExpression) != nil,
              let rgb = UInt32(value.dropFirst(), radix: 16) else { return nil }
        return UIColor(red: CGFloat((rgb >> 16) & 255) / 255, green: CGFloat((rgb >> 8) & 255) / 255, blue: CGFloat(rgb & 255) / 255, alpha: 1)
    }
    private func updateSystemBars(top: UIColor = .systemGroupedBackground, bottom: UIColor = .systemGroupedBackground, reset: Bool = false) {
        view.backgroundColor = bottom; topBackground.backgroundColor = top
        let appearance = UINavigationBarAppearance(); appearance.configureWithOpaqueBackground()
        appearance.backgroundColor = top; appearance.shadowColor = .clear
        navigationController?.navigationBar.standardAppearance = appearance
        navigationController?.navigationBar.scrollEdgeAppearance = appearance
        var red: CGFloat = 0, green: CGFloat = 0, blue: CGFloat = 0, alpha: CGFloat = 0
        top.resolvedColor(with: traitCollection).getRed(&red, green: &green, blue: &blue, alpha: &alpha)
        pageStatusBarStyle = reset ? .default : (red * 0.299 + green * 0.587 + blue * 0.114 >= 0.5 ? .darkContent : .lightContent)
        setNeedsStatusBarAppearanceUpdate(); navigationController?.setNeedsStatusBarAppearanceUpdate()
    }
    private func isArtifact(_ url: URL) -> Bool {
        DownloadManager.allows(url, origin: documentAddress)
    }
    private func isIsolatedShell(_ url: URL) -> Bool {
        url.absoluteString == documentAddress + "/html-preview-shell" || url.absoluteString == documentAddress + "/mermaid-render-shell" || url.absoluteString == documentAddress + "/visualize-shell"
    }
    private func downloadArtifact(_ request: URLRequest) {
        guard !downloadState.busy else { downloadPanel.expand(); info(MobileStrings.text("已有下载进行中，请稍候")); return }
        guard let url = request.url, isArtifact(url) else { return }
        guard let physical=URL(string:origin+url.absoluteString.dropFirst(documentAddress.count)) else{return};downloadPanel.expand(); downloads.start(physical, origin: origin)
    }
    private func openDownload() {
        guard downloadState.phase == .complete, let file = downloadState.file, presentedViewController == nil else { return }
        let document = UIDocumentInteractionController(url: file)
        document.delegate = self; openDocument = document
        // The menu transfers the document to the receiving app; the active task retains its source.
        if !document.presentOpenInMenu(from: downloadPanel.bounds, in: downloadPanel, animated: true),
           !document.presentPreview(animated: true) {
            openDocument = nil; info(MobileStrings.text("暂无可打开此文件的应用"))
        }
    }
    func documentInteractionControllerViewControllerForPreview(_ controller: UIDocumentInteractionController) -> UIViewController { self }
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
        guard webView === web, frame.isMainFrame, let source = frame.request.url, GatewayURL.same(source, documentAddress),
              let current = webView.url, GatewayURL.same(current, documentAddress), presentedViewController == nil else { completionHandler(nil); return }
        let uploadKind = uploadSource.flatMap { Date().timeIntervalSince($0.at) < 2 ? $0.name : nil }
        finishUpload(nil)
        uploadCompletion = completionHandler; uploadGeneration = generation
        if uploadKind == "photos" || uploadKind == "photos-images" {
            var config = PHPickerConfiguration(); config.preferredAssetRepresentationMode = .compatible; if uploadKind == "photos-images" { config.filter = .images }; config.selectionLimit = parameters.allowsMultipleSelection ? 10 : 1
            let picker = PHPickerViewController(configuration: config); picker.delegate = self
            mediaPicker = picker; present(picker, animated: true); picker.presentationController?.delegate = self; return
        }
        let picker = UIDocumentPickerViewController(forOpeningContentTypes: [.item], asCopy: true)
        picker.allowsMultipleSelection = parameters.allowsMultipleSelection; picker.delegate = self
        uploadPicker = picker; uploadCompletion = completionHandler; uploadGeneration = generation
        present(picker, animated: true); picker.presentationController?.delegate = self
    }
    #endif
    private func finishUpload(_ urls: [URL]?) {
        let completion = uploadCompletion; uploadCompletion = nil; uploadPicker = nil; mediaPicker = nil; uploadSource = nil; uploadTicket = UUID()
        completion?(urls)
    }
    func presentationControllerDidDismiss(_ presentationController: UIPresentationController) {
        let picker = presentationController.presentedViewController
        if picker === uploadPicker || picker === mediaPicker { finishUpload(nil) }
    }
    private func mediaUploadFolder() throws -> URL {
        if let folder = mediaUploadDirectory { return folder }
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("BridgeUpload-" + UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        mediaUploadDirectory = folder; return folder
    }
    func picker(_ picker: PHPickerViewController, didFinishPicking results: [PHPickerResult]) {
        guard picker === mediaPicker else { picker.dismiss(animated: true); return }
        let ticket = uploadTicket
        guard !results.isEmpty, let folder = try? mediaUploadFolder() else {
            picker.dismiss(animated: true) { if ticket == self.uploadTicket { self.finishUpload(nil) } }; return
        }
        picker.dismiss(animated: true)
        Task { @MainActor in
            var urls = [URL]()
            for result in results {
                let provider = result.itemProvider
                guard let type = provider.registeredTypeIdentifiers.first(where: { UTType($0)?.conforms(to: .image) == true || UTType($0)?.conforms(to: .movie) == true }) else { continue }
                let saved: URL? = await withCheckedContinuation { continuation in
                    provider.loadFileRepresentation(forTypeIdentifier: type) { url, _ in
                        guard let url else { continuation.resume(returning: nil); return }
                        let destination = folder.appendingPathComponent(UUID().uuidString).appendingPathExtension(url.pathExtension)
                        do { try FileManager.default.copyItem(at: url, to: destination); continuation.resume(returning: destination) }
                        catch { continuation.resume(returning: nil) }
                    }
                }
                if let saved { urls.append(saved) }
            }
            guard ticket == uploadTicket, uploadGeneration == generation, let current = web?.url, GatewayURL.same(current, documentAddress) else {
                for url in urls { try? FileManager.default.removeItem(at: url) }; return
            }
            if urls.count == results.count { finishUpload(urls) }
            else { for url in urls { try? FileManager.default.removeItem(at: url) }; finishUpload(nil); info(MobileStrings.text("无法读取所选文件，请从系统文件选择器重新选择。")) }
        }
    }
    func documentPicker(_ controller: UIDocumentPickerViewController, didPickDocumentsAt urls: [URL]) {
        if controller === exportPicker { exportPicker = nil; if !urls.isEmpty { downloads.markSaved() }; return }
        guard controller === uploadPicker else { return }
        guard uploadGeneration == generation, let current = web?.url, GatewayURL.same(current, documentAddress) else { finishUpload(nil); return }
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
        if let result = uu.callback(url) { if !result.isEmpty { routeNotice(MobileStrings.text(result)) }; return }
        guard let c = URLComponents(url: url, resolvingAgainstBaseURL: false), c.scheme == "codexbridge", c.host == "open" else { return }
        let q = c.queryItems ?? []; func get(_ key: String) -> String? { q.first(where: { $0.name == key })?.value }
        guard let original = get("origin"), let thread = get("thread"), saved.contains(activeChannel(original)), let target = try? GatewayURL.chat(activeChannel(original), thread: thread, host: get("host") ?? "local") else { info(MobileStrings.text("请先扫码保存通知对应的电脑，再打开聊天。")); return }
        let address = activeChannel(original)
        connect(target, address: address)
    }
    private func fetch(address requestedAddress: String? = nil, after: Int? = nil, poll: Int? = nil, route: String = "/api/mobile/events", body: [String: Any]? = nil, csrf: String? = nil, _ done: @escaping (Result<[String: Any], Error>) -> Void) {
        let address = requestedAddress ?? origin, ticket = generation
        func valid() -> Bool { ticket == self.generation && (poll.map { self.pollCurrent($0) } ?? true) }
        let query = after.map { "?after=\(max(0, $0))" } ?? ""
        guard let target = URL(string: address + route + query), !address.isEmpty else { done(.failure(GatewayURL.InvalidURL())); return }
        WKWebsiteDataStore.default().httpCookieStore.getAllCookies { cookies in
            guard valid() else { return }
            guard poll == nil || self.saved.contains(address) else { done(.failure(NSError(domain: "Bridge", code: 401))); return }
            let matching = cookies.filter { cookie in self.cookieMatches(cookie, address: address, discover: route == "/api/auth") }
            guard !matching.isEmpty else { done(.failure(NSError(domain: "Bridge", code: 401, userInfo: [NSLocalizedDescriptionKey: MobileStrings.text("请先连接并登录电脑网关")]))); return }
            var request = URLRequest(url: target); request.setValue(GatewayURL.httpOrigin(address), forHTTPHeaderField: "Origin"); request.setValue(HTTPCookie.requestHeaderFields(with: matching)["Cookie"], forHTTPHeaderField: "Cookie"); request.setValue("BridgeMobile/0.1-iOS", forHTTPHeaderField: "User-Agent")
            if let body { request.httpMethod = "POST"; request.httpBody = try? JSONSerialization.data(withJSONObject: body); request.setValue("application/json", forHTTPHeaderField: "Content-Type"); request.setValue(csrf, forHTTPHeaderField: "X-CSRF-Token") }
            let task = self.session.dataTask(with: request) { data, response, error in
                let code = (response as? HTTPURLResponse)?.statusCode
                let result: Result<[String: Any], Error>
                if let error { result = .failure(error) }
                else if code == 200, let data, data.count <= 512000, let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any] { result = .success(value) }
                else { result = .failure(NSError(domain: "Bridge", code: code ?? 0, userInfo: [NSLocalizedDescriptionKey: code == 401 || code == 403 ? MobileStrings.text("登录已失效，请重新连接电脑") : MobileStrings.text("请使用配套电脑 Preview，并检查连接")])) }
                DispatchQueue.main.async {
                    guard valid() else { return }
                    if route == "/api/auth", case .success(let auth) = result, auth["authenticated"] as? Bool == true,
                       let name = auth["cookieName"] as? String,
                       name.range(of: #"^codex_mobile_session_[a-f0-9]{24}$"#, options: .regularExpression) != nil {
                        self.defaults.set(name, forKey: "cookieName:" + address)
                    }
                    if poll != nil { self.pollTasks.removeValue(forKey: address) }
                    let response = response as? HTTPURLResponse
                    let headers = response?.allHeaderFields as? [String: String] ?? [:]
                    let renewed = code == 200 && response?.url.map({ GatewayURL.same($0, address) }) == true ? HTTPCookie.cookies(withResponseHeaderFields: headers, for: target).first(where: { cookie in
                        self.cookieMatches(cookie, address: address, discover: route == "/api/auth") && matching.contains(where: { $0.value == cookie.value })
                    }) : nil
                    let store = WKWebsiteDataStore.default().httpCookieStore
                    store.getAllCookies { current in
                        guard valid() else { return }
                        // A response from a removed/replaced login cannot renew its
                        // cookie or commit that account's notification cursor.
                        let selectedName = (try? result.get())?["cookieName"] as? String ?? self.cookieName(address)
                        let bindingCurrent = self.saved.contains(address) && matching.contains(where: { sent in
                            let migrates = sent.name == "codex_mobile_session" && renewed.map { $0.value == sent.value && $0.name == selectedName } == true
                            return (sent.name == selectedName || migrates) && current.contains(where: { $0.name == sent.name && $0.value == sent.value && $0.domain == sent.domain && $0.path == sent.path })
                        })
                        if !bindingCurrent { done(.failure(NSError(domain: "Bridge", code: 401))); return }
                        guard bindingCurrent, let renewed else { done(result); return }
                        store.setCookie(renewed) {
                            guard valid() else { return }
                            guard poll != nil else { done(result); return }
                            store.getAllCookies { latest in
                                guard valid() else { return }
                                guard self.saved.contains(address), latest.contains(where: { $0.name == renewed.name && $0.value == renewed.value && $0.domain == renewed.domain && $0.path == renewed.path }) else { done(.failure(NSError(domain: "Bridge", code: 401))); return }
                                done(result)
                            }
                        }
                    }
                }
            }
            if poll != nil { self.pollTasks[address] = task }
            task.resume()
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
    private func clearInbox(_ data: [String: Any], address: String, done: @escaping () -> Void) {
        fetch(address: address, route: "/api/auth") { result in
            guard case .success(let auth) = result, let csrf = auth["csrf"] as? String else { self.info(MobileStrings.text("登录已失效，请重新连接电脑")); return }
            self.fetch(address: address, route: "/api/mobile/events/read", body: ["provider": "all", "streamId": data["streamId"] ?? "", "through": data["cursor"] ?? 0], csrf: csrf) { result in
                switch result {
                case .success(let snapshot): self.updateComputerNotifications(snapshot, address: address); done()
                case .failure(let error): self.info(error.localizedDescription)
                }
            }
        }
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
                    guard let self else { return }
                    self.clearInbox(data, address: address) {
                        let cursor = data["cursor"] as? Int ?? 0
                        self.defaults.set(cursor, forKey: clearKey)
                        UNUserNotificationCenter.current().getDeliveredNotifications { notices in
                            let ids = notices.filter { ($0.request.content.userInfo["origin"] as? String) == address && (Int(String(describing: $0.request.content.userInfo["sequence"] ?? 0)) ?? 0) <= cursor }.map { $0.request.identifier }
                            UNUserNotificationCenter.current().removeDeliveredNotifications(withIdentifiers: ids)
                        }
                        sheet?.dismiss(animated: true) { self.inbox() }
                    }
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
    @objc private func active() { if let entry=pendingEntry,entry.interrupted,web==nil,UIApplication.shared.applicationState == .active {openComputer(entry.address,entry.url)};watchChannels(); computerNotifications.values.forEach { $0.alpha = 0.5 }; cancelSync(); checkComputers(); foregroundBaselines.removeAll();sync(); activityTimer?.invalidate(); activityTimer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in self?.syncLiveActivity() }; syncLiveActivity(); timer?.invalidate(); timer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in self?.sync() } }
    @objc private func inactive() { pendingEntry?.interrupted=true;rollbackPeer();cancelChannelJobs();lastChannelProbe=nil;channelSignature="";channelTicket += 1; channelMonitor?.cancel(); channelMonitor = nil;channelCatalogTimer?.invalidate();channelCatalogTimer=nil; routeDiscovery?.cancel(); routeDiscovery = nil; routeAttempted = false; computerNotifications.values.forEach { $0.alpha = 0.5 }; cancelSync(); downloads.pause(); stopComputerChecks(); timer?.invalidate(); timer = nil; activityTimer?.invalidate(); activityTimer = nil; if #available(iOS 16.2, *) { Task { await LiveActivityController.shared.pause() } } }
    private func cancelSync() {
        pollGeneration += 1; pollTasks.values.forEach { $0.cancel() }; pollTasks.removeAll(); polling.removeAll()
    }
    private func pollCurrent(_ ticket: Int) -> Bool {
        ticket == pollGeneration && UIApplication.shared.applicationState == .active
    }
    private func sync() {
        guard UIApplication.shared.applicationState == .active else { return }
        registerNativePush()
        let ticket = pollGeneration
        // Each saved computer has one request in flight. A slow/offline gateway
        // must not delay notifications from another computer.
        for address in saved where channelEnabled(address) && !polling.contains(address) {
            polling.insert(address)
            let after = self.defaults.integer(forKey: "cursor:" + address)
            self.fetch(address: address, after: after, poll: ticket) { result in
                guard self.pollCurrent(ticket) else { return }
                self.polling.remove(address)
                if case .success(let data) = result, self.saved.contains(address) { self.updateComputerNotifications(data, address: address) }
                else { if case .failure(let error) = result, [401, 403].contains((error as NSError).code) { self.computerSnapshots.removeValue(forKey: address); self.computerNotifications[address]?.arrangedSubviews.forEach { $0.removeFromSuperview() } }; self.updateComputerNotifications(nil, address: address) }
                if self.saved.contains(address), case .success(let data) = result, data["enabled"] as? Bool == true, let stream = data["streamId"] as? String {
                    let key = "cursor:" + address, previous = self.defaults.integer(forKey: key)
                    let ready = self.foregroundBaselines.contains(address) && self.defaults.string(forKey: "stream:" + address) == stream
                    let cleared = self.defaults.integer(forKey: "cleared:" + address + ":" + stream)
                    if ready, self.defaults.bool(forKey: "foregroundAlerts"), self.notificationSourceEnabled(address), UIApplication.shared.applicationState == .active {
                        for event in data["events"] as? [[String: Any]] ?? [] where event["unread"] as? Bool != false && (event["sequence"] as? Int ?? 0) > max(previous, cleared) {
                            let content = UNMutableNotificationContent()
                            content.title = event["title"] as? String ?? MobileStrings.text("任务提醒")
                            content.body = event["body"] as? String ?? ""
                            content.sound = .default
                            NotificationSource.apply(content, provider: event["provider"] as? String, host: event["host"] as? String)
                            content.userInfo = ["origin": address, "thread": event["threadId"] as? String ?? "", "host": event["host"] as? String ?? "local", "sequence": event["sequence"] as? Int ?? 0]
                            let identifier = (self.channelRecord(address)?["deviceId"] as? String ?? address) + ":" + stream + ":" + (event["id"] as? String ?? String(event["sequence"] as? Int ?? 0))
                            UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: identifier, content: content, trigger: nil))
                        }
                    }
                    self.defaults.set(data["cursor"] as? Int ?? 0, forKey: key)
                    self.defaults.set(stream, forKey: "stream:" + address)
                    self.foregroundBaselines.insert(address)
                }
            }
        }
    }
    func notificationSourceEnabled(_ address: String) -> Bool {
        !defaults.bool(forKey: "notificationsMuted:" + address)
    }
    private func setNotificationSource(_ address: String, enabled: Bool) {
        guard saved.contains(address), notificationSourceEnabled(address) != enabled else { return }
        defaults.set(!enabled, forKey: "notificationsMuted:" + address)
        // Retire pending polls before baselining this source; other inbox cursors stay intact.
        cancelSync(); foregroundBaselines.remove(address); sync()
    }
    private func sourceSwitch(_ text:UIView,name:String,on:Bool,changed:@escaping (Bool)->Void)->UIView {
        let toggle=UISwitch();toggle.isOn=on;toggle.accessibilityLabel=name
        toggle.addAction(UIAction { [weak toggle] _ in if let toggle {changed(toggle.isOn)} },for:.valueChanged)
        toggle.setContentCompressionResistancePriority(.required,for:.horizontal)
        let row=UIStackView(arrangedSubviews:[text,toggle]);row.axis = .horizontal;row.spacing=12;row.alignment = .center
        return card([row])
    }
    private func notificationSources() {
        let (_, content) = sheet(MobileStrings.text("通知来源"))
        content.addArrangedSubview(label(MobileStrings.text("仅控制这台手机的任务提醒，不影响聊天和通知收件箱。"), secondary: true))
        if saved.isEmpty { content.addArrangedSubview(label(MobileStrings.text("尚未保存电脑连接"), secondary: true)) }
        for address in saved {
            let text = column(spacing: 4)
            text.addArrangedSubview(label(computerName(address), size: 17, weight: .semibold))
            text.addArrangedSubview(label(address, size: 13, secondary: true))
            content.addArrangedSubview(sourceSwitch(text,name:computerName(address)+", "+address,on:notificationSourceEnabled(address)){[weak self] on in
                self?.setNotificationSource(address,enabled:on)
            })
        }
    }
    private func requestStartupNotifications() {
        // Preserve explicit choices and avoid prompting again after refusal.
        guard defaults.object(forKey: "foregroundAlerts") == nil else { return }
        defaults.set(false, forKey: "foregroundAlerts")
        requestForegroundAlerts(showDeniedMessage: false)
    }
    private func requestForegroundAlerts(showDeniedMessage: Bool) {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge]) { allowed, _ in
            DispatchQueue.main.async { self.defaults.set(allowed, forKey: "foregroundAlerts"); self.foregroundBaselines.removeAll(); if allowed { self.sync() } else if showDeniedMessage { self.info(MobileStrings.text("请在系统设置中允许通知。")) } }
        }
    }
    private func toggleForegroundAlerts() {
        if defaults.bool(forKey: "foregroundAlerts") { defaults.set(false, forKey: "foregroundAlerts"); return }
        requestForegroundAlerts(showDeniedMessage: true)
    }
    private func currentTask(_ completion: @escaping ([String: String]?) -> Void) {
        guard let web, let url = web.url, GatewayURL.same(url, documentAddress) else { completion(nil); return }
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
        content.addArrangedSubview(label(MobileStrings.text("外观与显示"), size: 19, weight: .semibold))
        let themes = ["system", "light", "dark"]
        let choice = UISegmentedControl(items: ["跟随系统", "浅色", "深色"].map(MobileStrings.text))
        choice.accessibilityLabel = MobileStrings.text("主题")
        choice.selectedSegmentIndex = themes.firstIndex(of: theme) ?? 0
        choice.addAction(UIAction { [weak self, weak choice] _ in
            guard let self, let index = choice?.selectedSegmentIndex, themes.indices.contains(index) else { return }
            self.defaults.set(themes[index], forKey: "settingsTheme"); self.applyTheme()
        }, for: .valueChanged)
        content.addArrangedSubview(card([label(MobileStrings.text("主题"), size: 17, weight: .semibold), choice]))
        content.addArrangedSubview(label(MobileStrings.text("通知"), size: 19, weight: .semibold))
        content.addArrangedSubview(card([
            label(MobileStrings.text("前台任务提醒"), size: 17, weight: .semibold),
            button(defaults.bool(forKey: "foregroundAlerts") ? MobileStrings.text("关闭任务通知") : MobileStrings.text("开启任务通知"), symbol: "bell") { [weak self, weak sheet] in sheet?.dismiss(animated: true) { self?.toggleForegroundAlerts() } },
            label(MobileStrings.text("App 打开时提醒已连接电脑的新任务消息。离开 App 或锁屏后不保证通知；可在电脑端配置 Bark 或 ntfy。"), secondary: true),
            button(MobileStrings.text("通知来源"), symbol: "bell") { [weak self, weak sheet] in sheet?.dismiss(animated: true) { self?.notificationSources() } },
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
        let current = Bundle.main.object(forInfoDictionaryKey: "BridgeReleaseVersion") as? String ?? "2.0.0"
        let updateStatus = label("", size: 13, secondary: true)
        let check = button(MobileStrings.text("检查更新"), symbol: "arrow.down.circle") {}
        check.addAction(UIAction { [weak self, weak sheet, weak check, weak updateStatus] _ in
            guard let self, let sheet, let check, let updateStatus else { return }
            self.checkUpdate(current: current, sheet: sheet, check: check, status: updateStatus)
        }, for: .touchUpInside)
        content.addArrangedSubview(card([label(MobileStrings.text("应用更新"), size: 19, weight: .semibold), label("Codex Mobile Bridge · " + current, size: 14), label(MobileStrings.text("手动检查更新，不会自动安装"), size: 13, secondary: true), check, updateStatus, label(MobileStrings.text("iOS App 需要使用自己的 Apple 账号重新签名安装，暂不支持 App 内直接覆盖更新。"), size: 13, secondary: true)]))
    }
    private func checkUpdate(current: String, sheet: UIViewController, check: UIButton, status: UILabel) {
        check.isEnabled = false; status.text = MobileStrings.text("正在检查更新…")
        var request = URLRequest(url: MobileRelease.api); request.timeoutInterval = 20
        request.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        request.setValue("Codex-Mobile-Bridge/" + current, forHTTPHeaderField: "User-Agent")
        let session = URLSession(configuration: .ephemeral)
        session.dataTask(with: request) { [weak self, weak sheet, weak check, weak status] data, response, error in
            defer { session.finishTasksAndInvalidate() }
            var candidate: MobileRelease?; var failed = false
            do {
                guard error == nil, (response as? HTTPURLResponse)?.statusCode == 200, let data, data.count <= 4 * 1024 * 1024 else { throw URLError(.badServerResponse) }
                candidate = try MobileRelease.select(data, current: current)
            } catch { failed = true }
            DispatchQueue.main.async {
                guard let self, let sheet, sheet.presentingViewController != nil, let check, let status else { return }
                check.isEnabled = true
                let url = candidate?.page ?? URL(string: MobileRelease.repository + "/releases")!
                if !failed && candidate == nil { status.text = MobileStrings.text("当前已是此通道最新版本。"); return }
                let skipKey = "skip-mobile-update:" + MobileRelease.channel(current)
                let skipped = candidate?.version == self.defaults.string(forKey: skipKey)
                status.text = MobileStrings.text(failed ? "检查失败，请检查网络后重试。也可以打开版本页面。" : skipped ? "已跳过此版本，仍可手动更新" : "发现新版本") + (candidate.map { " · " + $0.version } ?? "")
                let alert = UIAlertController(title: status.text, message: candidate.map { MobileStrings.text("iOS App 需要使用自己的 Apple 账号重新签名安装，暂不支持 App 内直接覆盖更新。") + "\n\n" + $0.notes }, preferredStyle: .alert)
                alert.addAction(UIAlertAction(title: MobileStrings.text("稍后"), style: .cancel))
                if let candidate { alert.addAction(UIAlertAction(title: MobileStrings.text("跳过此版本"), style: .default) { _ in
                    self.defaults.set(candidate.version, forKey: skipKey)
                    status.text = MobileStrings.text("已跳过此版本，仍可手动更新") + " · " + candidate.version
                }) }
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
        guard let web, address == origin, let url = web.url, GatewayURL.same(url, documentAddress) else { return }
        let device = defaults.string(forKey: "pushDeviceID") ?? UUID().uuidString; defaults.set(device, forKey: "pushDeviceID")
        var value = body; value["deviceId"] = device
        web.callAsyncJavaScript("const a = await (await fetch('/api/auth')).json(); if (!a.authenticated) return false; const r = await fetch('/api/mobile/push', {method:'POST', headers:{'Content-Type':'application/json', 'X-CSRF-Token':a.csrf}, body:JSON.stringify(body)}); return r.ok;", arguments: ["body": value], in: nil, in: .page) { _ in }
    }
    private func testNotification() {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert,.sound,.badge]) { allowed, _ in
            guard allowed else { DispatchQueue.main.async { self.info(MobileStrings.text("请在系统设置允许 Codex Bridge 通知。")) }; return }
            DispatchQueue.main.async {
                let content = UNMutableNotificationContent(); content.title = MobileStrings.text("手机通知测试"); content.body = MobileStrings.text("本机通知已开启"); content.sound = .default
                            NotificationSource.apply(content, provider: "codex", host: "local")
                if !self.origin.isEmpty { content.userInfo = ["origin": self.origin] }
                UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: "preview-test", content: content, trigger: UNTimeIntervalNotificationTrigger(timeInterval: 10, repeats: false))) { error in
                    DispatchQueue.main.async { self.info(error == nil ? MobileStrings.text("10 秒后显示本地测试通知，可以先锁屏。此测试不代表远程推送已经接通。") : MobileStrings.text("测试通知未能创建，请检查系统通知设置。")) }
                }
            }
        }
    }
    private func revokeConnection(_ address: String, _ done: @escaping () -> Void) {
        guard address == origin, let web, let url = web.url, GatewayURL.same(url, documentAddress) else { done(); return }
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
                self.clearChannels(target);DeviceBinding.clear(target)
                if target == self.origin, #available(iOS 16.2, *) { Task { await LiveActivityController.shared.stop() } }
                self.defaults.set(self.saved.filter { $0 != target }, forKey: "origins"); self.computerSnapshots.removeValue(forKey: target)
                if self.defaults.string(forKey: "active") == target { self.defaults.removeObject(forKey: "active") }
                self.defaults.removeObject(forKey: "notificationsMuted:" + target); self.defaults.removeObject(forKey: "connection:" + target); self.defaults.removeObject(forKey: "cursor:" + target); self.defaults.removeObject(forKey: "stream:" + target); self.defaults.removeObject(forKey: "name:" + target); self.defaults.removeObject(forKey: "last-opened:" + target)
                WKWebsiteDataStore.default().httpCookieStore.getAllCookies { cookies in
                    for cookie in cookies where self.cookieMatches(cookie, address: target) { WKWebsiteDataStore.default().httpCookieStore.delete(cookie) }
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
    var onOpen: (() -> Void)?
    private let title = UILabel()
    private let detail = UILabel()
    private let status = UILabel()
    private let progress = UIProgressView(progressViewStyle: .default)
    private let content = UIStackView()
    private let toggle = UIButton(type: .system)
    private let save = UIButton(type: .system)
    private let open = UIButton(type: .system)
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
        for button in [toggle, open, save, cancel] {
            var config = UIButton.Configuration.tinted(); config.cornerStyle = .medium
            config.baseForegroundColor = tintColor; config.titleTextAttributesTransformer = UIConfigurationTextAttributesTransformer { incoming in var output = incoming; output.font = .systemFont(ofSize: 14, weight: .medium); return output }
            button.configuration = config; button.heightAnchor.constraint(greaterThanOrEqualToConstant: 40).isActive = true; actions.addArrangedSubview(button)
        }
        toggle.addAction(UIAction { [weak self] _ in self?.onToggle?() }, for: .touchUpInside)
        cancel.addAction(UIAction { [weak self] _ in self?.onCancel?() }, for: .touchUpInside)
        save.addAction(UIAction { [weak self] _ in self?.onSave?() }, for: .touchUpInside)
        open.addAction(UIAction { [weak self] _ in self?.onOpen?() }, for: .touchUpInside)
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
    func expand() { collapsed = false; render(state) }
    func render(_ state: DownloadManager.Snapshot) {
        self.state = state; isHidden = !state.visible; content.isHidden = collapsed
        let percentage = state.total.map { $0 == 0 ? 100 : min(100, Int(Double(state.received) / Double($0) * 100)) }
        let phases: [DownloadManager.Phase: String] = [.connecting: "正在连接…", .downloading: "正在下载…", .paused: "已暂停", .failed: "下载未完成", .complete: "下载完成，可打开或保存", .saved: "已保存", .idle: "", .cancelled: "已取消"]
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
        open.isHidden = state.phase != .complete; open.configuration?.title = MobileStrings.text("打开")
        cancel.configuration?.title = MobileStrings.text([.complete, .saved].contains(state.phase) ? "关闭" : "取消下载")
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
// Only notification metadata is shown here; this does not assert live task state.
enum ComputerNotificationSummary {
    static func clients(_ data: [String: Any]) -> [[String: Any]] {
        let rows = data["clients"] as? [[String: Any]] ?? []
        return ["codex", "claude", "deepseek", "zcode", "antigravity"].compactMap { provider in rows.first { $0["id"] as? String == provider && $0["enabled"] as? Bool == true } }
    }
}

enum MobileStrings {
    static let values: [String: String] = [
        "UU 请求正在处理中": "A UU request is already in progress",
        "请安装或更新 UU 远程至 4.42.0 及以上版本": "Install or update UU Remote to version 4.42.0 or later",
        "无法打开 UU 远程": "Could not open UU Remote",
        "UU 设备已绑定，请再次选择小窗或远控": "Device linked. Select picture-in-picture or remote control again.",
        "UU 绑定未完成，请在 UU 中确认账号和设备": "Device linking did not finish. Check your account and device in UU Remote.",
        "UU 小窗已开启": "UU picture-in-picture is open",
        "UU 设备已失效，请重新绑定": "The UU device is no longer available. Link it again.",
        "UU 设备离线": "The UU device is offline",
        "当前设备不支持 UU 小窗，请使用全屏远控": "Picture-in-picture is unavailable. Use full-screen control.",
        "UU 小窗未能开启，请检查设备或重试": "Could not open picture-in-picture. Check the device and try again.",
        "此设备无法使用相机": "Camera is unavailable on this device",
        "粘贴 DSH 客户端 Mobile 功能提供的完整链接": "Paste the full link from your DSH client’s Mobile feature",
        "连接 DSH Mobile？": "Connect to DSH Mobile?",
        "\n请确认这是你自己的 DSH 客户端。将打开它自带的手机界面。": "\nConfirm this is your DSH client. Its own mobile interface will open.",
        "无法保存 DSH Mobile 连接": "Could not save the DSH Mobile connection",
        "连接凭据不可用，请移除后重新添加": "Connection credentials are unavailable. Remove and add this connection again.",
        "连接凭据不可用": "Connection credentials unavailable",
        "Android 8 请使用浏览器打开 DSH Mobile；App 内连接需要 Android 9 或更高版本。": "On Android 8, open DSH Mobile in your browser. In-app access requires Android 9 or later.",
        "连接失败，请刷新重试": "Connection failed. Refresh to retry",
        "DSH Mobile 地址无效": "Invalid DSH Mobile URL",
        "请使用完整的 DSH Mobile HTTPS 链接，或局域网 HTTP 链接。": "Use a full DSH Mobile HTTPS link, or an HTTP link on your local network.",
        "通知来源": "Notification sources",
        "连接中…": "Connecting…",
        "当前信道不可达，请手动切换连接信道": "Current channel is unreachable. Choose another channel.",
        "移除此电脑": "Remove this computer",
        "管理这台电脑": "Manage this computer",
        "仅控制这台手机的任务提醒，不影响聊天和通知收件箱。": "Choose task alerts for this phone. Chats and notification inboxes remain available.",
        "尚未保存电脑连接": "No saved computer connections",
        "主题": "Theme",
        "跟随系统": "System",
        "浅色": "Light",
        "深色": "Dark",
        "正在连接…": "Connecting…",
        "已暂停": "Paused",
        "下载未完成": "Download incomplete",
        "下载完成，可打开或保存": "Downloaded · open or save",
        "暂无可打开此文件的应用": "No app is available to open this file.",
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
        "未读通知": "Unread notifications", "刷新状态": "Refresh status", "最近通知": "Recent notifications", "需要你处理": "Action requested", "已运行完毕": "Completed",
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
"手动检查更新，不会自动安装": "Manual update checks, no automatic installation",
"检查更新": "Check for updates",
"正在检查更新…": "Checking for updates…",
"没有可用的浏览器，请在电脑上打开 GitHub Release。": "No browser is available. Open GitHub Releases on your computer.",
"检查失败，请检查网络后重试。也可以打开版本页面。": "Could not check for updates. Check your network and retry, or open the releases page.",
"版本页面": "Releases",
"当前已是此通道最新版本。": "You are up to date on this channel.",
"发现新版本": "Update available",
"跳过此版本": "Skip this version",
"已跳过此版本，仍可手动更新": "This version is skipped; you can still update manually",
"下载后按系统提示覆盖安装，不要先卸载。已保存的电脑与登录状态会保留。": "After downloading, follow the system prompts to update without uninstalling. Saved computers and sign-ins will be retained.",
"稍后": "Later",
"更新说明": "Release notes",
"下载 APK": "Download APK",
"查看新版与安装指引": "View update and installation guide",
"iOS App 需要使用自己的 Apple 账号重新签名安装，暂不支持 App 内直接覆盖更新。": "The iOS app requires signing the new build with your own Apple account. Direct in-app installation is not available yet.",

        "返回电脑列表": "Back to computers",
        "暂时无法连接电脑": "Can’t reach the computer",
        "网关暂时无法访问。请检查电脑和网络后重试，或返回电脑列表。": "The gateway is temporarily unreachable. Check your computer and network, then retry or go back to your computers.",
        "重试": "Try again",
        "外观与显示": "Appearance",
        "隐藏底部 APP 图标": "Hide bottom app icons",
        "显示底部 APP 图标": "Show bottom app icons",
        "刷新页面": "Reload page",
        "好": "OK",
        "电脑上的工作，\n带在身边。": "Your computer’s work,\nalways with you.",
        "继续聊天、查看结果，让电脑替你运行。": "Continue chats and view results while your computer does the work.",
        "扫码连接电脑": "Scan to connect",
        "输入网关地址": "Enter gateway address",
        "我的电脑": "My computers",
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
        "移除这台电脑": "Remove",
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
        "请在系统设置允许 Codex Bridge 通知。": "Allow Codex Bridge notifications in system settings.",
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
