import Foundation
#if canImport(UIKit)
import UIKit
import AVKit
#endif

/// UU owns screen capture and input. Bridge keeps only the selected computer's binding.
@MainActor final class UURemote {
    private let defaults: UserDefaults
    private let pendingKey = "uu.pending"
    private let scheme: String
    private let pipSupported: () -> Bool
    private let installed: () -> Bool
    private let launch: (URL, @escaping (Bool) -> Void) -> Void
    var scope = ""

    init(_ defaults: UserDefaults, scheme: String, pipSupported: @escaping () -> Bool,
         installed: @escaping () -> Bool, launch: @escaping (URL, @escaping (Bool) -> Void) -> Void) {
        self.defaults = defaults; self.scheme = scheme
        self.pipSupported = pipSupported; self.installed = installed; self.launch = launch
    }
    #if canImport(UIKit)
    convenience init(_ defaults: UserDefaults) {
        self.init(defaults, scheme: (Bundle.main.bundleIdentifier ?? "") + ".uu", pipSupported: {
            if #available(iOS 18, *) { return AVPictureInPictureController.isPictureInPictureSupported() }
            return false
        }, installed: { UIApplication.shared.canOpenURL(URL(string:"uuremote://external")!) },
           launch: { UIApplication.shared.open($0, options:[:], completionHandler:$1) })
    }
    #endif

    static func device(_ cookie: String) -> String {
        guard cookie.range(of: "^codex_mobile_session_[a-f0-9]{24}$", options: .regularExpression) != nil else { return "" }
        return String(ConnectionDiscovery.hash(cookie).prefix(32))
    }

    var enabled: Bool { !scope.isEmpty && defaults.bool(forKey: "uu.enabled:" + scope) }
    private var pending: [String: Any] {
        guard let request = defaults.dictionary(forKey: pendingKey),
              let until = request["until"] as? Date, until > Date() else { return [:] }
        return request
    }
    private func unlink(_ device: String) {
        defaults.removeObject(forKey: "uu.binding:" + device)
        if pending["scope"] as? String == device { defaults.removeObject(forKey: pendingKey) }
    }
    func remove(_ device: String) {
        guard !device.isEmpty else { return }
        unlink(device); defaults.removeObject(forKey: "uu.enabled:" + device)
        if scope == device { scope = "" }
    }
    func settings(_ command: String) -> String {
        if !scope.isEmpty {
            if command == "enable" || command == "disable" {
                defaults.set(command == "enable", forKey: "uu.enabled:" + scope)
                if command == "disable", pending["scope"] as? String == scope { defaults.removeObject(forKey: pendingKey) }
            } else if command == "forget" { unlink(scope) }
        }
        let state = ["supported": !scope.isEmpty, "enabled": enabled, "pip": pipSupported(),
                     "bound": defaults.string(forKey: "uu.binding:" + scope) != nil]
        guard let bytes = try? JSONSerialization.data(withJSONObject: state) else { return "{}" }
        return String(decoding: bytes, as: UTF8.self)
    }
    private func url(_ path: String, _ params: [(String, String)]) -> URL? {
        let safe = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-._~"))
        let query = params.map { key, value in key + "=" + (value.addingPercentEncoding(withAllowedCharacters: safe) ?? "") }
        return URL(string: "uuremote://external/device/" + path + "?" + query.joined(separator: "&"))
    }
    func open(_ mode: String, notice: @escaping (String) -> Void) {
        guard enabled, ["pip", "full"].contains(mode), mode != "pip" || pipSupported() else { return }
        guard pending.isEmpty else { notice("UU 请求正在处理中"); return }
        guard installed() else {
            notice("请安装或更新 UU 远程至 4.42.0 及以上版本"); return
        }
        let nonce = UUID().uuidString, callback = scheme + "://result"
        let id = defaults.string(forKey: "uu.binding:" + scope)
        let kind = id == nil ? "bind" : mode
        let target: URL?
        if let id {
            var params = [("anonymous_device_id", id), ("window_type", mode == "pip" ? "1" : "0")]
            if mode == "pip" { params.append(("callback", callback + "?nonce=" + nonce)) }
            target = url("control", params)
        } else {
            target = url("authorize", [("callback", callback), ("nonce", nonce)])
        }
        guard let target else { notice("无法打开 UU 远程"); return }
        if kind != "full" {
            defaults.set(["kind":kind, "scope":scope, "nonce":nonce, "until":Date().addingTimeInterval(600)], forKey:pendingKey)
        }
        launch(target) { opened in
            if !opened {
                if self.pending["nonce"] as? String == nonce { self.defaults.removeObject(forKey:self.pendingKey) }
                notice("无法打开 UU 远程")
            }
        }
    }
    /// Nil delegates unrelated links to normal navigation. Stale callbacks are consumed silently.
    func callback(_ url: URL) -> String? {
        guard let parts = URLComponents(url:url, resolvingAgainstBaseURL:false), parts.scheme == scheme,
              parts.host == "result", parts.user == nil, parts.password == nil, parts.port == nil else { return nil }
        let request = pending
        func one(_ key: String) -> String? {
            let items = (parts.queryItems ?? []).filter { $0.name == key }
            return items.count == 1 ? items[0].value : nil
        }
        guard parts.path.isEmpty, parts.fragment == nil, url.absoluteString.utf8.count <= 8192, let nonce = one("nonce"), nonce == request["nonce"] as? String,
              let device = request["scope"] as? String, defaults.bool(forKey:"uu.enabled:" + device) else { return "" }
        defaults.removeObject(forKey:pendingKey)
        guard scope.isEmpty || scope == device else { return "" }
        if request["kind"] as? String == "bind" {
            if let id = one("anonymous_device_id"), !id.isEmpty, id.utf8.count <= 4096,
               !(parts.queryItems ?? []).contains(where:{ $0.name == "error" }) {
                defaults.set(id, forKey:"uu.binding:" + device)
                return "UU 设备已绑定，请再次选择小窗或远控"
            }
            return one("error") == "user_cancel" ? "" : "UU 绑定未完成，请在 UU 中确认账号和设备"
        }
        if one("status") == "success", !(parts.queryItems ?? []).contains(where:{ $0.name == "reason" }) { return "UU 小窗已开启" }
        guard one("status") == "failure" else { return "UU 小窗未能开启，请检查设备或重试" }
        switch one("reason") {
        case "device_not_found": unlink(device); return "UU 设备已失效，请重新绑定"
        case "device_offline": return "UU 设备离线"
        case "pip_unsupported": return "当前设备不支持 UU 小窗，请使用全屏远控"
        case "user_cancel": return ""
        default: return "UU 小窗未能开启，请检查设备或重试"
        }
    }
}
