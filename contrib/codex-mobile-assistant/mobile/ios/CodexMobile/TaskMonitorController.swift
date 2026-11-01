import Foundation
import CryptoKit
import WebKit

@MainActor
final class TaskMonitorController {
    static let shared = TaskMonitorController()
    var onStatus: ((String) -> Void)?
    private var configuration: [String: Any]?
    private var timer: Timer?
    private var lastSpawn = Date.distantPast
    private var blockedGeneration: String?
    private var lastMessage = ""

    var preferred: Bool {
        get { UserDefaults.standard.object(forKey: "backgroundTaskReminders") as? Bool ?? true }
        set { UserDefaults.standard.set(newValue, forKey: "backgroundTaskReminders") }
    }

    func configure(origin: URL, cookies: [HTTPCookie], sound: Bool) {
        guard preferred else { stop(); return }
        guard origin.scheme == "https", let host = origin.host else { stop(); return }
        guard let cookie = cookies.first(where: {
            let domain = $0.domain.hasPrefix(".") ? String($0.domain.dropFirst()) : $0.domain
            return $0.name == "codex_mobile_session"
                && (host.lowercased() == domain.lowercased() || host.lowercased().hasSuffix("." + domain.lowercased()))
                && $0.path == "/"
                && ($0.expiresDate == nil || $0.expiresDate! > Date())
        }) else { stop(message: "登录后开启后台提醒"); return }
        let header = HTTPCookie.requestHeaderFields(with: [cookie])["Cookie"] ?? ""
        let helper = URL(fileURLWithPath: Bundle.main.bundlePath).appendingPathComponent("Helpers/TaskMonitor")
            .resolvingSymlinksInPath().path
        let scope = SHA256.hash(data: Data((origin.absoluteString + "\n" + header).utf8))
            .map { String(format: "%02x", $0) }.joined()
        let old = TaskMonitorBridge.readFile("config.json") as? [String: Any]
        let reuse = old?["scope"] as? String == scope && old?["helper"] as? String == helper
            && old?["enabled"] as? Bool == true
        let generation = reuse ? (old?["generation"] as? String ?? UUID().uuidString) : UUID().uuidString
        let previousExpiry = reuse ? (old?["expiresAt"] as? Double ?? 0) : 0
        let lease = previousExpiry > Date().addingTimeInterval(3600).timeIntervalSince1970
            ? previousExpiry : Date().addingTimeInterval(24 * 3600).timeIntervalSince1970
        let expires = cookie.expiresDate?.timeIntervalSince1970 ?? lease
        guard expires > Date().timeIntervalSince1970 else { stop(message: "请重新登录后开启后台提醒"); return }
        let next: [String: Any] = [
            "enabled": true, "generation": generation, "origin": origin.absoluteString, "helper": helper,
            "cookie": header, "scope": scope, "sound": sound, "expiresAt": expires,
            "startedAt": reuse ? (old?["startedAt"] as? Double ?? Date().timeIntervalSince1970) : Date().timeIntervalSince1970
        ]
        if !NSDictionary(dictionary: old ?? [:]).isEqual(to: next) {
            guard TaskMonitorBridge.writeFile("config.json", value: next) else {
                stop(message: "无法保存后台提醒设置：" + TaskMonitorBridge.storageError()); return
            }
        }
        configuration = next
        if !reuse { blockedGeneration = nil; lastSpawn = .distantPast }
        if timer == nil {
            timer = Timer.scheduledTimer(withTimeInterval: 3, repeats: true) { [weak self] _ in
                Task { @MainActor in self?.refreshStatus() }
            }
        }
        refreshStatus()
    }

    func stop(message: String = "后台提醒已关闭") {
        configuration = nil
        timer?.invalidate()
        timer = nil
        // Dropping credentials also revokes in-flight helper responses before notification delivery.
        if (TaskMonitorBridge.readFile("config.json") as? [String: Any])?["enabled"] as? Bool == true {
            if !TaskMonitorBridge.writeFile("config.json", value: ["enabled": false]) {
                publish("无法停止后台提醒：" + TaskMonitorBridge.storageError()); return
            }
        }
        publish(message)
    }

    private func publish(_ message: String) {
        guard message != lastMessage else { return }
        lastMessage = message
        onStatus?(message)
    }

    func refreshStatus() {
        guard let configuration, let generation = configuration["generation"] as? String else { return }
        let status = TaskMonitorBridge.readFile("status.json") as? [String: Any]
        let heartbeat = status?["updatedAt"] as? Double ?? 0
        let matching = status?["generation"] as? String == generation
        let state = status?["state"] as? String ?? ""
        if matching && ["login", "unsupported"].contains(state) {
            blockedGeneration = generation
            publish(state == "login" ? "登录已失效，请重新登录" : "此系统暂不支持后台通知接口")
            return
        }
        if matching && Date().timeIntervalSince1970 - heartbeat < 15 && state != "stopped" {
            switch state {
            case "listening":
                let poll = Date(timeIntervalSince1970: status?["lastPollAt"] as? Double ?? 0)
                let formatter = DateFormatter()
                formatter.dateFormat = "HH:mm:ss"
                publish("后台监听中 · 最近读取 " + formatter.string(from: poll))
            case "notification-error": publish("任务已收到，系统通知提交失败")
            case "network": publish("后台连接暂时中断，正在重试")
            default: publish("正在连接后台提醒")
            }
            return
        }
        guard blockedGeneration != generation, Date().timeIntervalSince(lastSpawn) > 8 else { return }
        lastSpawn = Date()
        let code = TaskMonitorBridge.startHelper()
        publish(code == 0 ? "后台进程已启动，等待连接确认" : "无法启动后台提醒，请使用 TrollStore 安装本版本")
    }
}
