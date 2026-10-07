import Foundation
import ActivityKit

@available(iOS 16.2, *)
struct TaskActivity: ActivityAttributes {
    struct ContentState: Codable, Hashable {
        var phase: String
        var updatedAt: Date
        var language: String? = nil
        func text(_ zh: String, _ en: String) -> String { (language ?? Locale.preferredLanguages.first ?? "zh").hasPrefix("en") ? en : zh }
        var label: String {
            switch phase {
            case "running": return text("正在执行", "Running")
            case "waiting": return text("等待回应", "Waiting for you")
            case "ended": return text("本轮已结束", "Turn completed")
            case "paused": return text("打开 App 同步", "Open app to sync")
            case "offline": return text("暂未连接", "Disconnected")
            default: return text("已连接", "Connected")
            }
        }
        var symbol: String {
            switch phase {
            case "running": return "ellipsis"
            case "waiting": return "hand.raised.fill"
            case "ended": return "checkmark"
            case "paused", "offline": return "arrow.clockwise"
            default: return "desktopcomputer"
            }
        }
    }
    var origin: String
    var thread: String
    var host: String
    var demo: Bool
    var link: URL? {
        var c = URLComponents(); c.scheme = "codexbridge"; c.host = "open"
        c.queryItems = [URLQueryItem(name: "origin", value: origin), URLQueryItem(name: "thread", value: thread), URLQueryItem(name: "host", value: host)]
        return c.url
    }
}
