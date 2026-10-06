import Foundation
import ActivityKit

@available(iOS 16.2, *)
struct TaskActivity: ActivityAttributes {
    struct ContentState: Codable, Hashable {
        var phase: String
        var updatedAt: Date
        var label: String {
            switch phase {
            case "running": return "正在执行"
            case "waiting": return "等待回应"
            case "ended": return "本轮已结束"
            case "paused": return "打开 App 同步"
            case "offline": return "暂未连接"
            default: return "已连接"
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
