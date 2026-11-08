import Foundation

@main struct UURemoteTests {
    @MainActor static func main() {
        let name = "uu-test-" + UUID().uuidString
        let prefs = UserDefaults(suiteName:name)!
        defer { prefs.removePersistentDomain(forName:name) }
        var urls = [URL](), notices = [String](), pip = true
        let uu = UURemote(prefs, scheme:"fixture.uu", pipSupported:{ pip }, installed:{true}) {
            urls.append($0); $1(true)
        }
        func open(_ mode: String) { uu.open(mode){ notices.append($0) } }
        func value(_ url: URL, _ key: String) -> String? {
            URLComponents(url:url,resolvingAgainstBaseURL:false)?.queryItems?.first{ $0.name == key }?.value
        }
        func reply(_ query: String) -> String? { uu.callback(URL(string:"fixture.uu://result?" + query)!) }
        uu.scope = "computer-a"
        open("full"); precondition(urls.isEmpty)
        _ = uu.settings("enable")
        open("pip"); let bind = urls.last!
        precondition(bind.path == "/device/authorize" && value(bind,"callback") == "fixture.uu://result")
        open("pip"); precondition(urls.count == 1)
        let nonce = value(bind,"nonce")!
        precondition(reply("nonce=wrong&anonymous_device_id=no") == "")
        precondition(reply("nonce=" + nonce + "&anonymous_device_id=A%2BB%2F%3D")?.contains("已绑定") == true)
        precondition(reply("nonce=" + nonce + "&anonymous_device_id=replay") == "")
        open("pip"); let target = urls.last!
        precondition(value(target,"anonymous_device_id") == "A+B/=" && value(target,"window_type") == "1")
        let callback = URL(string:value(target,"callback")!)!
        precondition(uu.callback(URL(string:callback.absoluteString + "&status=success")!) == "UU 小窗已开启")
        open("full"); precondition(value(urls.last!,"window_type") == "0" && value(urls.last!,"callback") == nil)
        pip = false; let count = urls.count; open("pip"); precondition(urls.count == count)
        uu.scope = "computer-b"; _ = uu.settings("enable")
        open("full"); precondition(urls.last!.path == "/device/authorize")
        let late = value(urls.last!,"nonce")!; uu.scope = "computer-a"
        precondition(reply("nonce=" + late + "&anonymous_device_id=wrong-computer") == "")
        precondition(prefs.string(forKey:"uu.binding:computer-b") == nil)
        open("full"); precondition(value(urls.last!,"anonymous_device_id") == "A+B/=")
        pip = true; open("pip")
        let expired = URL(string:value(urls.last!,"callback")! + "&status=failure&reason=device_not_found")!
        precondition(uu.callback(expired)?.contains("失效") == true)
        open("full"); precondition(urls.last!.path == "/device/authorize")
        let removed = value(urls.last!,"nonce")!; uu.remove("computer-a")
        precondition(reply("nonce=" + removed + "&anonymous_device_id=stale") == "")
        precondition(prefs.string(forKey:"uu.binding:computer-a") == nil)
        print("UU bind, full screen, PiP, replay, scope change, unlink and stale callbacks passed")
    }
}
